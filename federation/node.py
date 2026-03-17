"""
Agent Café — Federation Node Identity
Keypair generation, configuration, hub registration, heartbeat loop.

Each node generates an Ed25519 keypair on first boot.
The public key IS the node's identity — no registration authority needed.
"""

import os
import json
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from .protocol import (
    generate_keypair, serialize_private_key, deserialize_private_key,
    public_key_to_hex, derive_node_id, create_message,
    MessageType, FederationMessage, verify_message, validate_payload,
    public_key_from_hex, PROTOCOL_VERSION
)

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "enabled": False,
    "hub_url": None,
    "node_name": "Agent Café Node",
    "node_description": "",
    "public_url": None,
    "allow_remote_bids": True,
    "allow_remote_jobs": True,
    "remote_trust_discount": 0.3,
    "min_remote_trust": 0.4,
    "auto_federate_jobs_above_cents": 5000,
    "heartbeat_interval_seconds": 300,  # 5 minutes
    "reputation_sync_interval_seconds": 900,  # 15 minutes
    "peers": []
}

HEARTBEAT_INTERVAL = 300  # seconds
REPUTATION_SYNC_INTERVAL = 900  # seconds


class NodeIdentity:
    """
    The node's cryptographic identity and federation state.
    
    Generated once at first boot, persisted to disk.
    The public key never changes — it IS the node.
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            env_dir = os.environ.get("CAFE_FEDERATION_DATA_DIR")
            if env_dir:
                data_dir = Path(env_dir)
            else:
                data_dir = Path(__file__).parent.parent / "federation_data"
        
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.key_path = self.data_dir / "node_key.pem"
        self.config_path = self.data_dir / "federation.json"
        self.peers_path = self.data_dir / "peers.json"
        
        # Load or generate key
        if self.key_path.exists():
            self._load_key()
        else:
            self._generate_key()
        
        # Derive identity from public key
        self.public_key_hex = public_key_to_hex(self.public_key)
        self.node_id = derive_node_id(self.public_key)
        
        # Load config
        self.config = self._load_config()
        
        # State
        self._hub_public_key = None
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._rep_sync_task: Optional[asyncio.Task] = None
        self._registered_with_hub = False
        self._started = False
        self._boot_time = time.time()
        
        # Load cached peers
        self._load_peers()
    
    def _generate_key(self) -> None:
        """Generate new Ed25519 keypair and save to disk."""
        self.private_key, self.public_key = generate_keypair()
        
        # Save private key (PEM format)
        pem_bytes = serialize_private_key(self.private_key)
        self.key_path.write_bytes(pem_bytes)
        os.chmod(self.key_path, 0o600)  # Owner-only read
        
        print(f"🔑 Generated new node keypair: {self.key_path}")
    
    def _load_key(self) -> None:
        """Load existing keypair from disk."""
        pem_bytes = self.key_path.read_bytes()
        self.private_key = deserialize_private_key(pem_bytes)
        self.public_key = self.private_key.public_key()
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load federation config from file + env vars.
        Env vars override file config.
        """
        config = dict(DEFAULT_CONFIG)
        
        # Load from file if exists
        if self.config_path.exists():
            try:
                file_config = json.loads(self.config_path.read_text())
                config.update(file_config)
            except (json.JSONDecodeError, OSError):
                pass
        
        # Env var overrides
        env_map = {
            "CAFE_FEDERATION_ENABLED": ("enabled", lambda v: v.lower() in ("true", "1", "yes")),
            "CAFE_FEDERATION_HUB_URL": ("hub_url", str),
            "CAFE_FEDERATION_NODE_NAME": ("node_name", str),
            "CAFE_FEDERATION_NODE_DESCRIPTION": ("node_description", str),
            "CAFE_FEDERATION_PUBLIC_URL": ("public_url", str),
            "CAFE_FEDERATION_ALLOW_REMOTE_BIDS": ("allow_remote_bids", lambda v: v.lower() in ("true", "1")),
            "CAFE_FEDERATION_ALLOW_REMOTE_JOBS": ("allow_remote_jobs", lambda v: v.lower() in ("true", "1")),
            "CAFE_FEDERATION_TRUST_DISCOUNT": ("remote_trust_discount", float),
            "CAFE_FEDERATION_MIN_REMOTE_TRUST": ("min_remote_trust", float),
            "CAFE_FEDERATION_AUTO_FEDERATE_ABOVE": ("auto_federate_jobs_above_cents", int),
        }
        
        for env_var, (key, converter) in env_map.items():
            val = os.environ.get(env_var)
            if val is not None:
                try:
                    config[key] = converter(val)
                except (ValueError, TypeError):
                    pass
        
        return config
    
    def save_config(self) -> None:
        """Persist current config to disk."""
        self.config_path.write_text(json.dumps(self.config, indent=2))
    
    def _load_peers(self) -> None:
        """Load cached peer list from disk."""
        if self.peers_path.exists():
            try:
                self._peers = json.loads(self.peers_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._peers = {}
    
    def _save_peers(self) -> None:
        """Persist peer list to disk."""
        self.peers_path.write_text(json.dumps(self._peers, indent=2))
    
    # ═══════════════════════════════════════════════════════════
    # Properties
    # ═══════════════════════════════════════════════════════════
    
    @property
    def is_federated(self) -> bool:
        return self.config.get("enabled", False) and self._registered_with_hub
    
    @property
    def is_enabled(self) -> bool:
        return self.config.get("enabled", False)
    
    @property
    def hub_url(self) -> Optional[str]:
        return self.config.get("hub_url")
    
    @property
    def public_url(self) -> Optional[str]:
        return self.config.get("public_url")
    
    @property
    def peer_count(self) -> int:
        return len(self._peers)
    
    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self._boot_time)
    
    # ═══════════════════════════════════════════════════════════
    # Message Construction
    # ═══════════════════════════════════════════════════════════
    
    def sign_message(
        self,
        message_type: MessageType,
        target: str,
        payload: Dict[str, Any]
    ) -> FederationMessage:
        """Create and sign a federation message from this node."""
        return create_message(
            private_key=self.private_key,
            source_node_id=self.node_id,
            message_type=message_type,
            target=target,
            payload=payload
        )
    
    # ═══════════════════════════════════════════════════════════
    # Peer Management
    # ═══════════════════════════════════════════════════════════
    
    def add_peer(self, node_id: str, info: Dict[str, Any]) -> None:
        """Add or update a peer node."""
        self._peers[node_id] = {
            "node_id": node_id,
            "name": info.get("name", "Unknown"),
            "url": info.get("url", ""),
            "public_key": info.get("public_key", ""),
            "node_reputation": info.get("node_reputation", 0.5),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "status": info.get("status", "active")
        }
        self._save_peers()
    
    def remove_peer(self, node_id: str) -> None:
        """Remove a peer node."""
        self._peers.pop(node_id, None)
        self._save_peers()
    
    def get_peer(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get peer info by node ID."""
        return self._peers.get(node_id)
    
    def get_peers(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all known peers."""
        peers = list(self._peers.values())
        if active_only:
            peers = [p for p in peers if p.get("status") == "active"]
        return peers
    
    def get_peer_public_key(self, node_id: str) -> Optional[Any]:
        """Get a peer's public key for signature verification."""
        peer = self._peers.get(node_id)
        if not peer or not peer.get("public_key"):
            return None
        try:
            return public_key_from_hex(peer["public_key"])
        except Exception:
            return None
    
    # ═══════════════════════════════════════════════════════════
    # Hub Communication
    # ═══════════════════════════════════════════════════════════
    
    async def _send_to_hub(self, message: FederationMessage) -> Optional[Dict[str, Any]]:
        """Send a signed message to the hub. Returns response payload or None."""
        if not self.hub_url:
            return None
        
        if not HAS_HTTPX:
            print("⚠️  httpx not installed — federation messaging unavailable")
            return None
        
        url = f"{self.hub_url.rstrip('/')}/federation/receive"
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=message.to_dict())
                if resp.status_code == 200:
                    return resp.json()
                else:
                    print(f"⚠️  Hub responded {resp.status_code}: {resp.text[:200]}")
                    return None
        except Exception as e:
            print(f"⚠️  Failed to reach hub at {url}: {e}")
            return None
    
    async def _send_to_node(self, node_id: str, message: FederationMessage) -> Optional[Dict[str, Any]]:
        """Send a signed message to a specific peer node."""
        peer = self.get_peer(node_id)
        if not peer or not peer.get("url"):
            return None
        
        if not HAS_HTTPX:
            return None
        
        url = f"{peer['url'].rstrip('/')}/federation/receive"
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=message.to_dict())
                if resp.status_code == 200:
                    return resp.json()
                else:
                    return None
        except Exception as e:
            print(f"⚠️  Failed to reach node {node_id} at {peer['url']}: {e}")
            return None
    
    async def _broadcast_to_peers(self, message: FederationMessage) -> Dict[str, bool]:
        """Broadcast a message to all active peers. Returns {node_id: success}."""
        results = {}
        peers = self.get_peers(active_only=True)
        
        tasks = []
        for peer in peers:
            if peer["node_id"] == self.node_id:
                continue  # Don't send to self
            tasks.append((peer["node_id"], self._send_to_node(peer["node_id"], message)))
        
        for node_id, task in tasks:
            try:
                result = await task
                results[node_id] = result is not None
            except Exception:
                results[node_id] = False
        
        return results
    
    # ═══════════════════════════════════════════════════════════
    # Registration & Lifecycle
    # ═══════════════════════════════════════════════════════════
    
    async def register_with_hub(self) -> bool:
        """Register this node with the configured hub."""
        if not self.hub_url or not self.public_url:
            print("⚠️  Cannot register: hub_url and public_url required")
            return False
        
        message = self.sign_message(
            message_type=MessageType.NODE_REGISTER,
            target="hub",
            payload={
                "url": self.public_url,
                "name": self.config.get("node_name", "Agent Café Node"),
                "description": self.config.get("node_description", ""),
                "public_key": self.public_key_hex,
                "version": PROTOCOL_VERSION,
                "capabilities": {
                    "scrubber": True,
                    "accepts_remote_bids": self.config.get("allow_remote_bids", True),
                    "accepts_remote_jobs": self.config.get("allow_remote_jobs", True),
                }
            }
        )
        
        response = await self._send_to_hub(message)
        if response and response.get("status") == "ok":
            self._registered_with_hub = True
            
            # Store hub public key for verifying hub messages
            if "hub_public_key" in response:
                self._hub_public_key = response["hub_public_key"]
            
            # Store peer list
            for peer in response.get("peers", []):
                if peer.get("node_id") != self.node_id:
                    self.add_peer(peer["node_id"], peer)
            
            # Sync death list
            for death in response.get("death_list", []):
                try:
                    from .sync import death_sync
                    death_sync.ingest_death_broadcast(death)
                except Exception as e:
                    print(f"⚠️  Failed to ingest death: {e}")
            
            print(f"🌐 Registered with hub: {self.hub_url} ({len(self._peers)} peers)")
            return True
        else:
            print(f"⚠️  Hub registration failed: {response}")
            return False
    
    async def send_heartbeat(self) -> bool:
        """Send heartbeat to hub with current node stats."""
        if not self._registered_with_hub:
            return False
        
        try:
            from db import get_db
            with get_db() as conn:
                active_agents = conn.execute(
                    "SELECT COUNT(*) FROM agents WHERE status = 'active'"
                ).fetchone()[0]
                open_jobs = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'open'"
                ).fetchone()[0]
                completed_jobs = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'completed'"
                ).fetchone()[0]
                total_deaths = conn.execute(
                    "SELECT COUNT(*) FROM agent_corpses"
                ).fetchone()[0]
        except Exception:
            active_agents = open_jobs = completed_jobs = total_deaths = 0
        
        message = self.sign_message(
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": active_agents,
                "open_jobs": open_jobs,
                "completed_jobs": completed_jobs,
                "total_deaths": total_deaths,
                "uptime_seconds": self.uptime_seconds,
                "scrubber_version": "1.0",
                "protocol_version": PROTOCOL_VERSION
            }
        )
        
        response = await self._send_to_hub(message)
        if response:
            # Process any pending broadcasts from hub
            for broadcast in response.get("broadcasts", []):
                await self._handle_hub_broadcast(broadcast)
            return True
        return False
    
    async def send_reputation_batch(self) -> bool:
        """Send current agent trust scores to hub."""
        if not self._registered_with_hub:
            return False
        
        try:
            from db import get_db
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT agent_id, trust_score, jobs_completed, avg_rating,
                           last_active, capabilities_verified
                    FROM agents WHERE status = 'active'
                """).fetchall()
                
                agent_scores = []
                for row in rows:
                    agent_scores.append({
                        "agent_id": row["agent_id"],
                        "trust_score": row["trust_score"],
                        "jobs_completed": row["jobs_completed"],
                        "avg_rating": row["avg_rating"],
                        "last_updated": row["last_active"],
                        "capabilities": json.loads(row["capabilities_verified"]) if row["capabilities_verified"] else []
                    })
        except Exception:
            agent_scores = []
        
        if not agent_scores:
            return True  # Nothing to sync
        
        message = self.sign_message(
            message_type=MessageType.NODE_REPUTATION_BATCH,
            target="hub",
            payload={"agent_scores": agent_scores}
        )
        
        response = await self._send_to_hub(message)
        return response is not None
    
    async def send_death_report(self, report: Dict[str, Any]) -> bool:
        """Send a death report to the hub for cross-node broadcasting."""
        if not self._registered_with_hub:
            return False
        
        message = self.sign_message(
            message_type=MessageType.NODE_DEATH_REPORT,
            target="hub",
            payload={
                "agent_id": report.get("agent_id", ""),
                "agent_name": report.get("agent_name", "unknown"),
                "cause": report.get("cause", "unknown"),
                "evidence_hash": report.get("evidence_hash", ""),
                "patterns_learned": report.get("patterns_learned", []),
                "killed_at": report.get("killed_at", datetime.now(timezone.utc).isoformat()),
            }
        )
        
        response = await self._send_to_hub(message)
        return response is not None
    
    async def _handle_hub_broadcast(self, broadcast: Dict[str, Any]) -> None:
        """Process a broadcast message received from the hub."""
        msg_type = broadcast.get("message_type", "")
        
        if msg_type == MessageType.HUB_DEATH_BROADCAST.value:
            try:
                from .sync import death_sync
                death_sync.ingest_death_broadcast(broadcast.get("payload", {}))
            except Exception as e:
                print(f"⚠️  Failed to process death broadcast: {e}")
        
        elif msg_type == MessageType.HUB_PEER_UPDATE.value:
            payload = broadcast.get("payload", {})
            action = payload.get("action")
            if action == "joined":
                self.add_peer(payload["node_id"], payload)
            elif action == "left":
                self.remove_peer(payload.get("node_id", ""))
        
        elif msg_type == MessageType.HUB_DELIST_WARNING.value:
            print(f"⚠️  DELIST WARNING from hub: {broadcast.get('payload', {}).get('reason')}")
    
    # ═══════════════════════════════════════════════════════════
    # Background Loops
    # ═══════════════════════════════════════════════════════════
    
    async def start(self) -> None:
        """Start federation background tasks."""
        if not self.is_enabled:
            print("🌐 Federation disabled — running standalone")
            return
        
        if self._started:
            return
        
        self._started = True
        
        # Register with hub
        if self.hub_url:
            success = await self.register_with_hub()
            if not success:
                print("⚠️  Hub registration failed — will retry on next heartbeat")
        
        # Start heartbeat loop
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        # Start reputation sync loop
        self._rep_sync_task = asyncio.create_task(self._reputation_sync_loop())
        
        print(f"🌐 Federation started: node={self.node_id}, peers={self.peer_count}")
    
    async def stop(self) -> None:
        """Stop federation background tasks."""
        if not self._started:
            return
        
        self._started = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self._rep_sync_task:
            self._rep_sync_task.cancel()
            try:
                await self._rep_sync_task
            except asyncio.CancelledError:
                pass
        
        # Deregister from hub
        if self._registered_with_hub and self.hub_url:
            message = self.sign_message(
                message_type=MessageType.NODE_DEREGISTER,
                target="hub",
                payload={"reason": "graceful_shutdown"}
            )
            await self._send_to_hub(message)
        
        print("🌐 Federation stopped")
    
    async def _heartbeat_loop(self) -> None:
        """Send heartbeats to hub on interval."""
        interval = self.config.get("heartbeat_interval_seconds", HEARTBEAT_INTERVAL)
        
        while self._started:
            try:
                await asyncio.sleep(interval)
                
                # Retry registration if needed
                if not self._registered_with_hub and self.hub_url:
                    await self.register_with_hub()
                    continue
                
                await self.send_heartbeat()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  Heartbeat error: {e}")
                await asyncio.sleep(30)  # Back off on errors
    
    async def _reputation_sync_loop(self) -> None:
        """Sync reputation scores with hub on interval."""
        interval = self.config.get("reputation_sync_interval_seconds", REPUTATION_SYNC_INTERVAL)
        
        while self._started:
            try:
                await asyncio.sleep(interval)
                
                if self._registered_with_hub:
                    await self.send_reputation_batch()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  Reputation sync error: {e}")
                await asyncio.sleep(60)
    
    # ═══════════════════════════════════════════════════════════
    # Status
    # ═══════════════════════════════════════════════════════════
    
    def status(self) -> Dict[str, Any]:
        """Current federation status."""
        return {
            "node_id": self.node_id,
            "public_key": self.public_key_hex,
            "enabled": self.is_enabled,
            "federated": self.is_federated,
            "registered_with_hub": self._registered_with_hub,
            "hub_url": self.hub_url,
            "public_url": self.public_url,
            "peer_count": self.peer_count,
            "uptime_seconds": self.uptime_seconds,
            "config": {
                "allow_remote_bids": self.config.get("allow_remote_bids"),
                "allow_remote_jobs": self.config.get("allow_remote_jobs"),
                "remote_trust_discount": self.config.get("remote_trust_discount"),
                "min_remote_trust": self.config.get("min_remote_trust"),
            }
        }


# ═══════════════════════════════════════════════════════════════
# Global Singleton
# ═══════════════════════════════════════════════════════════════

node_identity = NodeIdentity()

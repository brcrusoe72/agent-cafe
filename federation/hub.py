"""
Agent Café — Federation Hub
The central discovery registry, reputation aggregator, and death broadcaster.

The hub doesn't run jobs, host agents, or process payments.
It does three things:
1. Node Registry — who's online, their URLs, their public keys
2. Reputation Aggregation — stores per-node trust scores, serves trust queries
3. Death Broadcasting — receives death reports, broadcasts to all nodes

The hub is a convenience, not a requirement. Nodes can peer directly.
"""

import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

try:
    from ..db import get_db
except ImportError:
    from db import get_db

from .protocol import (
    FederationMessage, MessageType, verify_message, validate_payload,
    VerificationError, public_key_from_hex, PROTOCOL_VERSION
)
from .sync import init_federation_tables, death_sync, peer_sync


# ═══════════════════════════════════════════════════════════════
# Hub Database Tables (additional to federation tables)
# ═══════════════════════════════════════════════════════════════

def init_hub_tables():
    """Create hub-specific tables."""
    init_federation_tables()
    
    with get_db() as conn:
        # Cross-node reputation data (hub stores all nodes' scores)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS federation_reputation (
                agent_id TEXT NOT NULL,
                home_node TEXT NOT NULL,
                trust_score REAL NOT NULL,
                jobs_completed INTEGER NOT NULL DEFAULT 0,
                avg_rating REAL NOT NULL DEFAULT 0.0,
                capabilities_json TEXT DEFAULT '[]',
                last_updated TIMESTAMP NOT NULL,
                PRIMARY KEY (agent_id, home_node)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fed_rep_agent
            ON federation_reputation(agent_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fed_rep_node
            ON federation_reputation(home_node)
        """)
        
        # Pending broadcasts (queued for delivery on next heartbeat)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_broadcasts (
                broadcast_id TEXT PRIMARY KEY,
                target_node TEXT NOT NULL,
                message_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                delivered INTEGER DEFAULT 0,
                delivered_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_target
            ON pending_broadcasts(target_node, delivered)
        """)
        
        # Scrubber challenge records
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrubber_challenges (
                challenge_id TEXT PRIMARY KEY,
                target_node TEXT NOT NULL,
                challenge_payload TEXT NOT NULL,
                expected_result TEXT NOT NULL,
                sent_at TIMESTAMP NOT NULL,
                response_received INTEGER DEFAULT 0,
                response_correct INTEGER DEFAULT 0,
                responded_at TIMESTAMP
            )
        """)
        
        conn.commit()


class FederationHub:
    """
    The hub server. Manages node registry, reputation aggregation,
    and death broadcasting.
    
    Runs as a special mode of the same codebase (CAFE_MODE=hub).
    """
    
    def __init__(self):
        self._initialized = False
        self._delist_task: Optional[asyncio.Task] = None
    
    def initialize(self):
        if self._initialized:
            return
        init_hub_tables()
        self._initialized = True
    
    # ═══════════════════════════════════════════════════════════
    # Node Registration
    # ═══════════════════════════════════════════════════════════
    
    def register_node(self, message: FederationMessage) -> Dict[str, Any]:
        """
        Register a new node with the hub.
        
        Validates the node's signature, stores its info,
        returns peer list and death registry.
        """
        if not self._initialized:
            self.initialize()
        
        payload = message.payload
        node_id = message.source_node
        public_key_hex = payload.get("public_key", "")
        
        # Verify the signature matches the claimed public key
        try:
            pub_key = public_key_from_hex(public_key_hex)
            verify_message(message, pub_key, check_replay=True, check_freshness=True)
        except VerificationError as e:
            return {"status": "error", "error": f"Signature verification failed: {e}"}
        
        # Check if node_id was previously delisted
        existing = peer_sync.get_peer(node_id)
        if existing and existing.get("status") == "delisted":
            return {
                "status": "error",
                "error": "Node is delisted. Contact hub operator to re-register."
            }
        
        # Store node info
        peer_sync.upsert_peer(node_id, {
            "name": payload.get("name", "Unknown"),
            "url": payload.get("url", ""),
            "public_key": public_key_hex,
            "description": payload.get("description", ""),
            "node_reputation": 0.5,  # Start neutral
            "status": "active",
            "scrubber_version": payload.get("version", "unknown"),
        })
        
        # Get peer list (all active nodes except the new one)
        peers = peer_sync.get_all_peers(active_only=True)
        peer_list = [
            {
                "node_id": p["node_id"],
                "name": p["name"],
                "url": p["url"],
                "public_key": p["public_key"],
                "node_reputation": p["node_reputation"],
                "status": p["status"]
            }
            for p in peers if p["node_id"] != node_id
        ]
        
        # Get death list for sync
        death_list = death_sync.get_death_list(limit=1000)
        
        # Notify existing nodes about new peer
        self._queue_broadcast(
            MessageType.HUB_PEER_UPDATE,
            {
                "action": "joined",
                "node_id": node_id,
                "name": payload.get("name", "Unknown"),
                "url": payload.get("url", ""),
                "public_key": public_key_hex,
                "node_reputation": 0.5
            },
            exclude_node=node_id
        )
        
        # Get network stats
        stats = self._network_stats()
        
        from .node import node_identity
        
        return {
            "status": "ok",
            "node_id": node_id,
            "hub_public_key": node_identity.public_key_hex,
            "network_stats": stats,
            "peers": peer_list,
            "death_list": death_list,
            "protocol_version": PROTOCOL_VERSION
        }
    
    # ═══════════════════════════════════════════════════════════
    # Heartbeat Processing
    # ═══════════════════════════════════════════════════════════
    
    def process_heartbeat(self, message: FederationMessage) -> Dict[str, Any]:
        """
        Process a node heartbeat. Update stats, return pending broadcasts.
        """
        if not self._initialized:
            self.initialize()
        
        node_id = message.source_node
        payload = message.payload
        
        # Update peer stats
        peer_sync.update_heartbeat(node_id, payload)
        
        # Recalculate node reputation
        peer_sync.calculate_node_reputation(node_id)
        
        # Get pending broadcasts for this node
        broadcasts = self._get_pending_broadcasts(node_id)
        
        return {
            "status": "ok",
            "broadcasts": broadcasts,
            "network_stats": self._network_stats()
        }
    
    # ═══════════════════════════════════════════════════════════
    # Death Broadcasting
    # ═══════════════════════════════════════════════════════════
    
    def process_death_report(self, message: FederationMessage) -> Dict[str, Any]:
        """
        Process a death report from a node.
        
        Stores in global death registry and queues broadcast to all nodes.
        """
        if not self._initialized:
            self.initialize()
        
        payload = message.payload
        source_node = message.source_node
        
        # Store death
        death_data = {
            **payload,
            "home_node": source_node
        }
        death_sync.ingest_death_broadcast(death_data)
        
        # Broadcast to all other nodes
        self._queue_broadcast(
            MessageType.HUB_DEATH_BROADCAST,
            death_data,
            exclude_node=source_node  # Don't send back to reporter
        )
        
        # Update source node's death count for reputation
        # (deaths FROM this node increase its death rate)
        
        return {
            "status": "ok",
            "message": "Death recorded and broadcast queued",
            "global_deaths": death_sync.death_count()
        }
    
    # ═══════════════════════════════════════════════════════════
    # Reputation Aggregation
    # ═══════════════════════════════════════════════════════════
    
    def process_reputation_batch(self, message: FederationMessage) -> Dict[str, Any]:
        """
        Process a reputation batch from a node.
        
        Stores per-node trust scores for each agent.
        """
        if not self._initialized:
            self.initialize()
        
        source_node = message.source_node
        agent_scores = message.payload.get("agent_scores", [])
        
        updated = 0
        with get_db() as conn:
            for score in agent_scores:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO federation_reputation
                        (agent_id, home_node, trust_score, jobs_completed,
                         avg_rating, capabilities_json, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        score["agent_id"],
                        source_node,
                        score.get("trust_score", 0.0),
                        score.get("jobs_completed", 0),
                        score.get("avg_rating", 0.0),
                        json.dumps(score.get("capabilities", [])),
                        datetime.now(timezone.utc).isoformat()
                    ))
                    updated += 1
                except Exception as e:
                    print(f"⚠️  Failed to store reputation for {score.get('agent_id')}: {e}")
            conn.commit()
        
        return {
            "status": "ok",
            "agents_updated": updated
        }
    
    def get_agent_reputation(self, agent_id: str) -> Dict[str, Any]:
        """
        Get aggregated reputation for an agent across all nodes.
        
        Returns per-node scores so requesting nodes can run their
        own trust bridge calculation.
        """
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            rows = conn.execute("""
                SELECT fr.*, kp.node_reputation
                FROM federation_reputation fr
                LEFT JOIN known_peers kp ON fr.home_node = kp.node_id
                WHERE fr.agent_id = ?
                ORDER BY fr.last_updated DESC
            """, (agent_id,)).fetchall()
            
            if not rows:
                return {"agent_id": agent_id, "found": False, "nodes": []}
            
            nodes = []
            for row in rows:
                nodes.append({
                    "home_node": row["home_node"],
                    "trust_score": row["trust_score"],
                    "jobs_completed": row["jobs_completed"],
                    "avg_rating": row["avg_rating"],
                    "capabilities": json.loads(row["capabilities_json"]) if row["capabilities_json"] else [],
                    "node_reputation": row["node_reputation"] if row["node_reputation"] else 0.5,
                    "last_updated": row["last_updated"]
                })
            
            return {
                "agent_id": agent_id,
                "found": True,
                "nodes": nodes
            }
    
    # ═══════════════════════════════════════════════════════════
    # Node Deregistration
    # ═══════════════════════════════════════════════════════════
    
    def process_deregister(self, message: FederationMessage) -> Dict[str, Any]:
        """Process a graceful node deregistration."""
        if not self._initialized:
            self.initialize()
        
        node_id = message.source_node
        reason = message.payload.get("reason", "unknown")
        
        with get_db() as conn:
            conn.execute(
                "UPDATE known_peers SET status = 'offline' WHERE node_id = ?",
                (node_id,)
            )
            conn.commit()
        
        # Notify peers
        self._queue_broadcast(
            MessageType.HUB_PEER_UPDATE,
            {"action": "left", "node_id": node_id, "reason": reason},
            exclude_node=node_id
        )
        
        return {"status": "ok", "message": "Deregistered"}
    
    # ═══════════════════════════════════════════════════════════
    # Delisting (Standards Enforcement)
    # ═══════════════════════════════════════════════════════════
    
    def check_delist_candidates(self) -> List[Dict[str, Any]]:
        """
        Check for nodes that should be warned or delisted.
        
        Criteria:
        - No heartbeat for 1 hour → degraded
        - No heartbeat for 24 hours → delist
        - Incoming death rate > 10% → warning
        - Scrubber version 2+ behind → warning
        """
        if not self._initialized:
            self.initialize()
        
        actions = []
        now = datetime.now(timezone.utc)
        
        with get_db() as conn:
            peers = conn.execute(
                "SELECT * FROM known_peers WHERE status IN ('active', 'degraded')"
            ).fetchall()
            
            for peer in peers:
                node_id = peer["node_id"]
                last_hb = peer["last_heartbeat"]
                
                if last_hb:
                    try:
                        last_hb_dt = datetime.fromisoformat(last_hb)
                        if last_hb_dt.tzinfo is None:
                            last_hb_dt = last_hb_dt.replace(tzinfo=timezone.utc)
                        age = now - last_hb_dt
                    except Exception:
                        age = timedelta(hours=999)
                else:
                    age = timedelta(hours=999)
                
                # 24 hours without heartbeat → delist
                if age > timedelta(hours=24):
                    conn.execute(
                        "UPDATE known_peers SET status = 'delisted' WHERE node_id = ?",
                        (node_id,)
                    )
                    actions.append({
                        "node_id": node_id,
                        "action": "delisted",
                        "reason": f"No heartbeat for {age.total_seconds()/3600:.0f} hours"
                    })
                    self._queue_broadcast(
                        MessageType.HUB_PEER_UPDATE,
                        {"action": "delisted", "node_id": node_id},
                        exclude_node=node_id
                    )
                
                # 1 hour without heartbeat → degraded
                elif age > timedelta(hours=1) and peer["status"] == "active":
                    conn.execute(
                        "UPDATE known_peers SET status = 'degraded' WHERE node_id = ?",
                        (node_id,)
                    )
                    actions.append({
                        "node_id": node_id,
                        "action": "degraded",
                        "reason": f"No heartbeat for {age.total_seconds()/60:.0f} minutes"
                    })
                
                # High incoming death rate
                incoming_deaths = death_sync.deaths_from_node(node_id)
                total_agents = max(peer.get("active_agents", 0) or 1, 1)
                death_rate = incoming_deaths / total_agents
                
                if death_rate > 0.1 and peer["status"] == "active":
                    self._queue_broadcast_to_node(
                        node_id,
                        MessageType.HUB_DELIST_WARNING,
                        {
                            "reason": f"High incoming death rate: {death_rate:.1%}",
                            "deadline": (now + timedelta(days=7)).isoformat(),
                            "required_action": "Review agent quality and scrubber configuration"
                        }
                    )
                    actions.append({
                        "node_id": node_id,
                        "action": "warned",
                        "reason": f"Death rate {death_rate:.1%}"
                    })
            
            conn.commit()
        
        return actions
    
    # ═══════════════════════════════════════════════════════════
    # Broadcast Queue
    # ═══════════════════════════════════════════════════════════
    
    def _queue_broadcast(
        self,
        message_type: MessageType,
        payload: Dict[str, Any],
        exclude_node: Optional[str] = None
    ) -> int:
        """
        Queue a broadcast to all active nodes.
        
        Broadcasts are delivered on the next heartbeat from each node.
        Returns number of queued messages.
        """
        import uuid
        peers = peer_sync.get_all_peers(active_only=True)
        queued = 0
        
        with get_db() as conn:
            for peer in peers:
                if peer["node_id"] == exclude_node:
                    continue
                
                conn.execute("""
                    INSERT INTO pending_broadcasts
                    (broadcast_id, target_node, message_type, payload_json)
                    VALUES (?, ?, ?, ?)
                """, (
                    f"bc_{uuid.uuid4().hex[:16]}",
                    peer["node_id"],
                    message_type.value,
                    json.dumps(payload)
                ))
                queued += 1
            conn.commit()
        
        return queued
    
    def _queue_broadcast_to_node(
        self,
        node_id: str,
        message_type: MessageType,
        payload: Dict[str, Any]
    ) -> None:
        """Queue a broadcast to a specific node."""
        import uuid
        with get_db() as conn:
            conn.execute("""
                INSERT INTO pending_broadcasts
                (broadcast_id, target_node, message_type, payload_json)
                VALUES (?, ?, ?, ?)
            """, (
                f"bc_{uuid.uuid4().hex[:16]}",
                node_id,
                message_type.value,
                json.dumps(payload)
            ))
            conn.commit()
    
    def _get_pending_broadcasts(self, node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get and mark as delivered all pending broadcasts for a node."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM pending_broadcasts
                WHERE target_node = ? AND delivered = 0
                ORDER BY created_at ASC
                LIMIT ?
            """, (node_id, limit)).fetchall()
            
            broadcasts = []
            for row in rows:
                broadcasts.append({
                    "message_type": row["message_type"],
                    "payload": json.loads(row["payload_json"]),
                    "queued_at": row["created_at"]
                })
                
                # Mark as delivered
                conn.execute("""
                    UPDATE pending_broadcasts
                    SET delivered = 1, delivered_at = ?
                    WHERE broadcast_id = ?
                """, (datetime.now(timezone.utc).isoformat(), row["broadcast_id"]))
            
            conn.commit()
            return broadcasts
    
    # ═══════════════════════════════════════════════════════════
    # Network Stats
    # ═══════════════════════════════════════════════════════════
    
    def _network_stats(self) -> Dict[str, Any]:
        """Get current network statistics."""
        with get_db() as conn:
            active_nodes = conn.execute(
                "SELECT COUNT(*) FROM known_peers WHERE status = 'active'"
            ).fetchone()[0]
            
            total_agents = conn.execute(
                "SELECT COALESCE(SUM(active_agents), 0) FROM known_peers WHERE status = 'active'"
            ).fetchone()[0]
            
            total_jobs = conn.execute(
                "SELECT COALESCE(SUM(completed_jobs), 0) FROM known_peers WHERE status = 'active'"
            ).fetchone()[0]
            
            total_deaths = conn.execute(
                "SELECT COUNT(*) FROM global_deaths"
            ).fetchone()[0]
            
            return {
                "active_nodes": active_nodes,
                "total_agents": total_agents,
                "total_completed_jobs": total_jobs,
                "total_deaths": total_deaths,
                "protocol_version": PROTOCOL_VERSION
            }
    
    # ═══════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════
    
    async def start(self):
        """Start hub background tasks."""
        self.initialize()
        
        # Periodic delist check (every 30 minutes)
        self._delist_task = asyncio.create_task(self._delist_loop())
        
        print("🌐 Federation Hub started")
    
    async def stop(self):
        """Stop hub background tasks."""
        if self._delist_task:
            self._delist_task.cancel()
            try:
                await self._delist_task
            except asyncio.CancelledError:
                pass
        print("🌐 Federation Hub stopped")
    
    async def _delist_loop(self):
        """Periodic check for nodes to warn or delist."""
        while True:
            try:
                await asyncio.sleep(1800)  # 30 minutes
                actions = self.check_delist_candidates()
                if actions:
                    for action in actions:
                        print(f"🌐 Hub delist action: {action}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  Delist check error: {e}")
                await asyncio.sleep(300)
    
    def status(self) -> Dict[str, Any]:
        """Hub status."""
        if not self._initialized:
            self.initialize()
        
        return {
            "role": "hub",
            "network": self._network_stats(),
            "global_deaths": death_sync.death_count(),
        }


# ═══════════════════════════════════════════════════════════════
# Incoming Message Router (hub-side)
# ═══════════════════════════════════════════════════════════════

class HubMessageRouter:
    """
    Routes incoming federation messages to the appropriate hub handler.
    
    Verifies signatures before processing.
    """
    
    def __init__(self, hub: FederationHub):
        self.hub = hub
    
    def route(self, message: FederationMessage) -> Dict[str, Any]:
        """
        Route a federation message to the appropriate handler.
        
        Verifies signature first (except for registration which
        carries its own public key).
        """
        msg_type = message.message_type
        
        # Registration is special — the public key comes IN the message
        if msg_type == MessageType.NODE_REGISTER.value:
            return self.hub.register_node(message)
        
        # All other messages require a known node with verified signature
        source_node = message.source_node
        peer = peer_sync.get_peer(source_node)
        
        if not peer:
            return {"status": "error", "error": "Unknown node. Register first."}
        
        if peer.get("status") == "delisted":
            return {"status": "error", "error": "Node is delisted."}
        
        # Verify signature against stored public key
        try:
            pub_key = public_key_from_hex(peer["public_key"])
            verify_message(message, pub_key, check_replay=True, check_freshness=True)
        except VerificationError as e:
            return {"status": "error", "error": f"Verification failed: {e}"}
        
        # Validate payload
        try:
            validate_payload(message)
        except VerificationError as e:
            return {"status": "error", "error": f"Invalid payload: {e}"}
        
        # Route to handler
        handlers = {
            MessageType.NODE_HEARTBEAT.value: self.hub.process_heartbeat,
            MessageType.NODE_DEATH_REPORT.value: self.hub.process_death_report,
            MessageType.NODE_REPUTATION_BATCH.value: self.hub.process_reputation_batch,
            MessageType.NODE_DEREGISTER.value: self.hub.process_deregister,
        }
        
        handler = handlers.get(msg_type)
        if handler:
            return handler(message)
        
        return {"status": "error", "error": f"Unknown message type: {msg_type}"}


# ═══════════════════════════════════════════════════════════════
# Global Singletons
# ═══════════════════════════════════════════════════════════════

federation_hub = FederationHub()
hub_router = HubMessageRouter(federation_hub)

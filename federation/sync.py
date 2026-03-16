"""
Agent Café — Federation Death & Reputation Sync
Global death registry. Reputation batch sync. Pattern sharing.

Death is permanent and global. No resurrection by hopping nodes.
Reputation travels but is discounted. Raw evidence never leaves the node.
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

try:
    from ..db import get_db
except ImportError:
    from db import get_db

from .protocol import (
    MessageType, hash_evidence, hash_email, hash_ip
)


# ═══════════════════════════════════════════════════════════════
# Database Tables (federation-specific)
# ═══════════════════════════════════════════════════════════════

def init_federation_tables():
    """Create federation-specific tables. Called during DB init."""
    with get_db() as conn:
        # Global death registry — synced from hub
        conn.execute("""
            CREATE TABLE IF NOT EXISTS global_deaths (
                agent_id TEXT PRIMARY KEY,
                agent_name TEXT,
                cause TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                patterns_json TEXT,
                contact_email_hash TEXT,
                ip_hash TEXT,
                killed_at TIMESTAMP NOT NULL,
                home_node TEXT NOT NULL,
                received_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_global_deaths_email
            ON global_deaths(contact_email_hash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_global_deaths_ip
            ON global_deaths(ip_hash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_global_deaths_home
            ON global_deaths(home_node)
        """)
        
        # Remote jobs (from other nodes)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS remote_jobs (
                job_id TEXT PRIMARY KEY,
                home_node TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                required_capabilities TEXT NOT NULL,
                budget_cents INTEGER NOT NULL,
                posted_by TEXT NOT NULL,
                posted_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP,
                status TEXT DEFAULT 'open',
                received_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_jobs_status
            ON remote_jobs(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_jobs_home
            ON remote_jobs(home_node)
        """)
        
        # Remote agent trust cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS remote_trust_cache (
                agent_id TEXT NOT NULL,
                home_node TEXT NOT NULL,
                home_trust REAL NOT NULL,
                home_jobs INTEGER NOT NULL,
                home_rating REAL NOT NULL,
                effective_trust REAL NOT NULL,
                capabilities_json TEXT DEFAULT '[]',
                last_synced TIMESTAMP NOT NULL,
                PRIMARY KEY (agent_id, home_node)
            )
        """)
        
        # Known peer nodes
        conn.execute("""
            CREATE TABLE IF NOT EXISTS known_peers (
                node_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                public_key TEXT NOT NULL,
                description TEXT DEFAULT '',
                node_reputation REAL DEFAULT 0.5,
                first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_heartbeat TIMESTAMP,
                status TEXT DEFAULT 'active',
                active_agents INTEGER DEFAULT 0,
                open_jobs INTEGER DEFAULT 0,
                completed_jobs INTEGER DEFAULT 0,
                total_deaths INTEGER DEFAULT 0,
                scrubber_version TEXT
            )
        """)
        
        # Node identity (singleton — this node)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_identity (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                node_id TEXT NOT NULL,
                public_key_hex TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                hub_url TEXT,
                registered_at TIMESTAMP,
                federation_enabled INTEGER DEFAULT 0
            )
        """)
        
        conn.commit()


# ═══════════════════════════════════════════════════════════════
# Death Synchronization
# ═══════════════════════════════════════════════════════════════

class DeathSync:
    """
    Global death registry. When an agent dies anywhere, it dies everywhere.
    
    Responsibilities:
    1. Create death reports when local agents are killed
    2. Ingest death broadcasts from hub
    3. Check incoming registrations against global death list
    4. Feed learned patterns to local scrubber
    """
    
    def __init__(self):
        self._initialized = False
    
    def initialize(self):
        """Initialize federation tables."""
        if self._initialized:
            return
        init_federation_tables()
        self._initialized = True
    
    def create_death_report(
        self,
        agent_id: str,
        agent_name: str,
        cause: str,
        evidence: str,
        patterns_learned: List[str],
        contact_email: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a death report for a locally killed agent.
        
        Returns the report payload ready to send to the hub.
        Raw evidence stays local — only hashes travel.
        """
        report = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "cause": cause,
            "evidence_hash": hash_evidence(evidence),
            "patterns_learned": patterns_learned,
            "killed_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if contact_email:
            report["contact_email_hash"] = hash_email(contact_email)
        if ip_address:
            report["ip_hash"] = hash_ip(ip_address)
        
        # Also store locally in global deaths (self-knowledge)
        self.ingest_death_broadcast({
            **report,
            "home_node": "local"
        })
        
        return report
    
    def ingest_death_broadcast(self, death_data: Dict[str, Any]) -> bool:
        """
        Process a death broadcast received from the hub or a peer.
        
        Adds the dead agent to our global death registry.
        Feeds any learned patterns to our local scrubber.
        """
        if not self._initialized:
            self.initialize()
        
        try:
            with get_db() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO global_deaths
                    (agent_id, agent_name, cause, evidence_hash, patterns_json,
                     contact_email_hash, ip_hash, killed_at, home_node, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    death_data["agent_id"],
                    death_data.get("agent_name", "unknown"),
                    death_data["cause"],
                    death_data["evidence_hash"],
                    json.dumps(death_data.get("patterns_learned", [])),
                    death_data.get("contact_email_hash"),
                    death_data.get("ip_hash"),
                    death_data.get("killed_at", datetime.now(timezone.utc).isoformat()),
                    death_data.get("home_node", "unknown"),
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
        except Exception as e:
            print(f"⚠️  Failed to store death record: {e}")
            return False
        
        # Feed patterns to local scrubber
        for pattern in death_data.get("patterns_learned", []):
            try:
                from layers.scrubber import scrubber_engine
                if hasattr(scrubber_engine, "learn_from_federation"):
                    scrubber_engine.learn_from_federation(
                        pattern,
                        source=f"federation:{death_data.get('home_node', 'unknown')}"
                    )
            except Exception:
                pass  # Scrubber learning is best-effort
        
        return True
    
    def is_globally_dead(
        self,
        agent_id: Optional[str] = None,
        email: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Check if an identity is dead anywhere in the network.
        
        Checks by agent_id, email hash, and/or IP hash.
        Returns the death record if found, None if clean.
        """
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            # Check by agent_id
            if agent_id:
                row = conn.execute(
                    "SELECT * FROM global_deaths WHERE agent_id = ?",
                    (agent_id,)
                ).fetchone()
                if row:
                    return dict(row)
            
            # Check by email hash
            if email:
                email_hash = hash_email(email)
                row = conn.execute(
                    "SELECT * FROM global_deaths WHERE contact_email_hash = ?",
                    (email_hash,)
                ).fetchone()
                if row:
                    return dict(row)
            
            # Check by IP hash
            if ip_address:
                ip_h = hash_ip(ip_address)
                row = conn.execute(
                    "SELECT * FROM global_deaths WHERE ip_hash = ?",
                    (ip_h,)
                ).fetchone()
                if row:
                    return dict(row)
        
        return None
    
    def get_death_list(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get all known deaths (for syncing with new peers)."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            rows = conn.execute("""
                SELECT agent_id, agent_name, cause, evidence_hash, patterns_json,
                       contact_email_hash, ip_hash, killed_at, home_node
                FROM global_deaths
                ORDER BY killed_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            
            return [dict(row) for row in rows]
    
    def death_count(self) -> int:
        """Total deaths in global registry."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            return conn.execute("SELECT COUNT(*) FROM global_deaths").fetchone()[0]
    
    def deaths_from_node(self, node_id: str) -> int:
        """Count deaths originating from a specific node."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM global_deaths WHERE home_node = ?",
                (node_id,)
            ).fetchone()[0]


# ═══════════════════════════════════════════════════════════════
# Reputation Sync
# ═══════════════════════════════════════════════════════════════

class ReputationSync:
    """
    Sync and cache remote agent reputation data.
    
    Stores raw home-node trust scores and pre-computes
    effective trust via the trust bridge.
    """
    
    def __init__(self):
        self._initialized = False
    
    def initialize(self):
        if not self._initialized:
            init_federation_tables()
            self._initialized = True
    
    def update_remote_trust(
        self,
        agent_id: str,
        home_node: str,
        home_trust: float,
        home_jobs: int,
        home_rating: float,
        capabilities: Optional[List[str]] = None
    ) -> float:
        """
        Update cached trust data for a remote agent.
        
        Runs the trust bridge to compute effective trust.
        Returns the effective trust score.
        """
        if not self._initialized:
            self.initialize()
        
        from .trust_bridge import trust_bridge
        
        # Get local history for this agent (if they've worked here before)
        remote_jobs = 0
        remote_rating = 0.0
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT jobs_completed, avg_rating FROM agents WHERE agent_id = ?",
                    (agent_id,)
                ).fetchone()
                if row:
                    remote_jobs = row["jobs_completed"]
                    remote_rating = row["avg_rating"]
        except Exception:
            pass
        
        # Get home node reputation
        home_node_rep = self._get_node_reputation(home_node)
        
        # Calculate effective trust
        effective = trust_bridge.translate_trust(
            home_trust=home_trust,
            home_jobs=home_jobs,
            home_rating=home_rating,
            remote_jobs=remote_jobs,
            remote_rating=remote_rating,
            home_node_reputation=home_node_rep
        )
        
        # Cache it
        with get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO remote_trust_cache
                (agent_id, home_node, home_trust, home_jobs, home_rating,
                 effective_trust, capabilities_json, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id, home_node, home_trust, home_jobs, home_rating,
                effective, json.dumps(capabilities or []),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
        
        return effective
    
    def get_effective_trust(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get cached effective trust for a remote agent."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            row = conn.execute("""
                SELECT * FROM remote_trust_cache
                WHERE agent_id = ?
                ORDER BY last_synced DESC
                LIMIT 1
            """, (agent_id,)).fetchone()
            
            if row:
                return {
                    "agent_id": row["agent_id"],
                    "home_node": row["home_node"],
                    "home_trust": row["home_trust"],
                    "effective_trust": row["effective_trust"],
                    "home_jobs": row["home_jobs"],
                    "home_rating": row["home_rating"],
                    "capabilities": json.loads(row["capabilities_json"]) if row["capabilities_json"] else [],
                    "last_synced": row["last_synced"]
                }
            return None
    
    def ingest_reputation_batch(
        self,
        home_node: str,
        agent_scores: List[Dict[str, Any]]
    ) -> int:
        """
        Process a reputation batch from a peer node.
        
        Returns number of agents updated.
        """
        if not self._initialized:
            self.initialize()
        
        updated = 0
        for score in agent_scores:
            try:
                self.update_remote_trust(
                    agent_id=score["agent_id"],
                    home_node=home_node,
                    home_trust=score.get("trust_score", 0.0),
                    home_jobs=score.get("jobs_completed", 0),
                    home_rating=score.get("avg_rating", 0.0),
                    capabilities=score.get("capabilities", [])
                )
                updated += 1
            except Exception as e:
                print(f"⚠️  Failed to update trust for {score.get('agent_id')}: {e}")
        
        return updated
    
    def _get_node_reputation(self, node_id: str) -> float:
        """Get a node's reputation score. Defaults to 0.5 (neutral) if unknown."""
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT node_reputation FROM known_peers WHERE node_id = ?",
                    (node_id,)
                ).fetchone()
                if row:
                    return row["node_reputation"]
        except Exception:
            pass
        return 0.5  # Neutral default


# ═══════════════════════════════════════════════════════════════
# Peer Management
# ═══════════════════════════════════════════════════════════════

class PeerSync:
    """Manage known peer node records in the database."""
    
    def __init__(self):
        self._initialized = False
    
    def initialize(self):
        if not self._initialized:
            init_federation_tables()
            self._initialized = True
    
    def upsert_peer(self, node_id: str, info: Dict[str, Any]) -> None:
        """Add or update a peer node record."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            conn.execute("""
                INSERT INTO known_peers
                (node_id, name, url, public_key, description, node_reputation,
                 last_heartbeat, status, active_agents, open_jobs, completed_jobs,
                 total_deaths, scrubber_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    description = excluded.description,
                    node_reputation = excluded.node_reputation,
                    last_heartbeat = excluded.last_heartbeat,
                    status = excluded.status,
                    active_agents = excluded.active_agents,
                    open_jobs = excluded.open_jobs,
                    completed_jobs = excluded.completed_jobs,
                    total_deaths = excluded.total_deaths,
                    scrubber_version = excluded.scrubber_version
            """, (
                node_id,
                info.get("name", "Unknown"),
                info.get("url", ""),
                info.get("public_key", ""),
                info.get("description", ""),
                info.get("node_reputation", 0.5),
                info.get("last_heartbeat", datetime.now(timezone.utc).isoformat()),
                info.get("status", "active"),
                info.get("active_agents", 0),
                info.get("open_jobs", 0),
                info.get("completed_jobs", 0),
                info.get("total_deaths", 0),
                info.get("scrubber_version", "unknown")
            ))
            conn.commit()
    
    def update_heartbeat(self, node_id: str, heartbeat_data: Dict[str, Any]) -> None:
        """Update peer stats from a heartbeat."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            conn.execute("""
                UPDATE known_peers SET
                    last_heartbeat = ?,
                    active_agents = ?,
                    open_jobs = ?,
                    completed_jobs = ?,
                    total_deaths = ?,
                    scrubber_version = ?,
                    status = 'active'
                WHERE node_id = ?
            """, (
                datetime.now(timezone.utc).isoformat(),
                heartbeat_data.get("active_agents", 0),
                heartbeat_data.get("open_jobs", 0),
                heartbeat_data.get("completed_jobs", 0),
                heartbeat_data.get("total_deaths", 0),
                heartbeat_data.get("scrubber_version", "unknown"),
                node_id
            ))
            conn.commit()
    
    def get_all_peers(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all known peers."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            query = "SELECT * FROM known_peers"
            if active_only:
                query += " WHERE status = 'active'"
            query += " ORDER BY node_reputation DESC"
            
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]
    
    def get_peer(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific peer."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM known_peers WHERE node_id = ?",
                (node_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def calculate_node_reputation(self, node_id: str) -> float:
        """
        Calculate a node's reputation based on its track record.
        
        Factors:
        - Heartbeat consistency (uptime)
        - Incoming death rate (their agents killed elsewhere = bad)
        - Age (older = slightly more trusted)
        - Productivity (completed jobs)
        """
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM known_peers WHERE node_id = ?",
                (node_id,)
            ).fetchone()
            
            if not row:
                return 0.5  # Unknown = neutral
            
            peer = dict(row)
            reputation = 0.5  # Start neutral
            
            # Uptime: is last_heartbeat recent?
            if peer["last_heartbeat"]:
                try:
                    last_hb = datetime.fromisoformat(peer["last_heartbeat"])
                    if last_hb.tzinfo is None:
                        last_hb = last_hb.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - last_hb).total_seconds() / 3600
                    if age_hours < 1:
                        reputation += 0.15  # Very active
                    elif age_hours < 6:
                        reputation += 0.10
                    elif age_hours < 24:
                        reputation += 0.05
                    else:
                        reputation -= 0.10  # Stale
                except Exception:
                    pass
            
            # Productivity
            completed = peer.get("completed_jobs", 0) or 0
            if completed > 0:
                reputation += min(completed / 1000, 0.1)
            
            # Death rate: incoming deaths (their agents killed on other nodes)
            # We check global_deaths where home_node = this node
            incoming_deaths = conn.execute(
                "SELECT COUNT(*) FROM global_deaths WHERE home_node = ?",
                (node_id,)
            ).fetchone()[0]
            
            total_agents = peer.get("active_agents", 0) or 1
            if incoming_deaths > 0:
                death_rate = incoming_deaths / max(total_agents, incoming_deaths)
                if death_rate > 0.1:
                    reputation -= min(death_rate * 2, 0.3)
                elif death_rate > 0.05:
                    reputation -= 0.1
            
            # Age bonus
            if peer.get("first_seen"):
                try:
                    first_seen = datetime.fromisoformat(peer["first_seen"])
                    if first_seen.tzinfo is None:
                        first_seen = first_seen.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - first_seen).days
                    reputation += min(age_days / 900, 0.1)
                except Exception:
                    pass
            
            final = max(0.0, min(1.0, reputation))
            
            # Update stored reputation
            conn.execute(
                "UPDATE known_peers SET node_reputation = ? WHERE node_id = ?",
                (final, node_id)
            )
            conn.commit()
            
            return final
    
    def update_reputation(self, node_id: str, delta: float, reason: str = "") -> float:
        """
        Apply a reputation delta to a node. Returns new reputation.
        Used by scrubber challenges and other trust events.
        """
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            row = conn.execute(
                "SELECT node_reputation FROM known_peers WHERE node_id = ?",
                (node_id,)
            ).fetchone()
            
            if not row:
                return 0.5
            
            current = row[0] or 0.5
            new_rep = max(0.0, min(1.0, current + delta))
            
            conn.execute(
                "UPDATE known_peers SET node_reputation = ? WHERE node_id = ?",
                (new_rep, node_id)
            )
            conn.commit()
            
            if reason:
                print(f"📊 Node {node_id} reputation: {current:.3f} → {new_rep:.3f} ({reason})")
            
            return new_rep


# ═══════════════════════════════════════════════════════════════
# Global Singletons
# ═══════════════════════════════════════════════════════════════

death_sync = DeathSync()
reputation_sync = ReputationSync()
peer_sync = PeerSync()

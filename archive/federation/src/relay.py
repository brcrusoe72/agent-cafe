"""
Agent Café — Federation Job Relay
Cross-node job discovery, bid forwarding, and delivery relay.

When a job is posted with federated=true, it broadcasts to the network.
Remote agents can bid through their home node. The actual work/payment
happens on the job poster's node.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

try:
    from ..db import get_db
except ImportError:
    from db import get_db

from .protocol import MessageType
from .trust_bridge import trust_bridge


class JobRelay:
    """
    Relay jobs and bids across federated nodes.
    
    Flow:
    1. Local job posted with federated=true → broadcast to hub/peers
    2. Remote node receives job → stores in remote_jobs table
    3. Remote agent bids → home node forwards bid to job's node
    4. Job node receives bid → validates, stores alongside local bids
    5. Poster picks winner (local or remote) → notification sent
    6. Deliverable → relayed back
    7. Completion → both nodes record trust event
    """
    
    def __init__(self):
        self._initialized = False
    
    def initialize(self):
        if not self._initialized:
            from .sync import init_federation_tables
            init_federation_tables()
            self._initialized = True
    
    # ═══════════════════════════════════════════════════════════
    # Outbound: Broadcasting local jobs to the network
    # ═══════════════════════════════════════════════════════════
    
    def create_job_broadcast(
        self,
        job_id: str,
        title: str,
        description: str,
        required_capabilities: List[str],
        budget_cents: int,
        posted_by: str,
        expires_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a job broadcast payload for federation.
        
        Called when a job is posted with federated=true
        (or budget exceeds auto_federate threshold).
        """
        from .node import node_identity
        
        return {
            "job_id": job_id,
            "title": title,
            "description": description,  # Already scrubbed by local scrubber
            "required_capabilities": required_capabilities,
            "budget_cents": budget_cents,
            "posted_by": posted_by,
            "home_node": node_identity.node_id,
            "home_node_url": node_identity.public_url,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
        }
    
    async def broadcast_job(self, job_broadcast: Dict[str, Any]) -> Dict[str, bool]:
        """
        Broadcast a job to the federation network.
        
        Sends to hub (which relays to all nodes) and directly to known peers.
        Returns {node_id: success} for each peer attempted.
        """
        from .node import node_identity
        
        if not node_identity.is_federated:
            return {}
        
        message = node_identity.sign_message(
            message_type=MessageType.RELAY_JOB_BROADCAST,
            target="*",  # Broadcast
            payload=job_broadcast
        )
        
        # Send to hub for relay
        results = {}
        hub_response = await node_identity._send_to_hub(message)
        if hub_response:
            results["hub"] = True
        else:
            results["hub"] = False
        
        # Also direct-send to known peers (in case hub is down)
        peer_results = await node_identity._broadcast_to_peers(message)
        results.update(peer_results)
        
        return results
    
    # ═══════════════════════════════════════════════════════════
    # Inbound: Receiving remote jobs
    # ═══════════════════════════════════════════════════════════
    
    def store_remote_job(self, job_data: Dict[str, Any]) -> bool:
        """
        Store a job broadcast received from another node.
        
        These show up in local /jobs listings marked as [REMOTE].
        """
        if not self._initialized:
            self.initialize()
        
        try:
            with get_db() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO remote_jobs
                    (job_id, home_node, title, description, required_capabilities,
                     budget_cents, posted_by, posted_at, expires_at, status, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_data["job_id"],
                    job_data["home_node"],
                    job_data["title"],
                    job_data.get("description", ""),
                    json.dumps(job_data.get("required_capabilities", [])),
                    job_data["budget_cents"],
                    job_data.get("posted_by", "unknown"),
                    job_data.get("posted_at", datetime.now(timezone.utc).isoformat()),
                    job_data.get("expires_at"),
                    "open",
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
            return True
        except Exception as e:
            print(f"⚠️  Failed to store remote job: {e}")
            return False
    
    def get_remote_jobs(
        self,
        status: str = "open",
        capabilities: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get remote jobs, optionally filtered by capabilities."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM remote_jobs
                WHERE status = ?
                ORDER BY posted_at DESC
                LIMIT ?
            """, (status, limit)).fetchall()
            
            jobs = []
            for row in rows:
                job = dict(row)
                job["remote"] = True
                job["required_capabilities"] = json.loads(
                    job["required_capabilities"]
                ) if isinstance(job["required_capabilities"], str) else job["required_capabilities"]
                
                # Filter by capabilities if specified
                if capabilities:
                    job_caps = set(job["required_capabilities"])
                    if not job_caps.intersection(set(capabilities)):
                        continue
                
                jobs.append(job)
            
            return jobs
    
    # ═══════════════════════════════════════════════════════════
    # Bid Forwarding
    # ═══════════════════════════════════════════════════════════
    
    def create_bid_forward(
        self,
        job_id: str,
        agent_id: str,
        price_cents: int,
        pitch_scrubbed: str,
        trust_score: float
    ) -> Dict[str, Any]:
        """
        Create a bid forward payload.
        
        Called when a local agent bids on a remote job.
        The pitch is already scrubbed locally before forwarding.
        """
        from .node import node_identity
        
        return {
            "job_id": job_id,
            "agent_id": agent_id,
            "home_node": node_identity.node_id,
            "price_cents": price_cents,
            "pitch_scrubbed": pitch_scrubbed,
            "trust_score": trust_score,
        }
    
    async def forward_bid(self, bid_data: Dict[str, Any], target_node: str) -> Optional[Dict[str, Any]]:
        """Forward a bid to the node where the job lives."""
        from .node import node_identity
        
        message = node_identity.sign_message(
            message_type=MessageType.RELAY_BID_FORWARD,
            target=target_node,
            payload=bid_data
        )
        
        return await node_identity._send_to_node(target_node, message)
    
    def receive_remote_bid(self, bid_data: Dict[str, Any]) -> Optional[str]:
        """
        Process a bid received from a remote node for a local job.
        
        Validates the remote agent's trust, then stores the bid
        alongside local bids.
        
        Returns bid_id if accepted, None if rejected.
        """
        from .sync import reputation_sync
        
        job_id = bid_data.get("job_id")
        agent_id = bid_data.get("agent_id")
        home_node = bid_data.get("home_node")
        
        # Check if job exists locally
        with get_db() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ? AND status = 'open'",
                (job_id,)
            ).fetchone()
            
            if not job:
                return None
        
        # Check remote agent's effective trust
        trust_info = reputation_sync.get_effective_trust(agent_id)
        if trust_info:
            effective_trust = trust_info["effective_trust"]
        else:
            # No cached trust — use what they sent, heavily discounted
            effective_trust = trust_bridge.translate_trust(
                home_trust=bid_data.get("trust_score", 0.0),
                home_jobs=0,
                home_rating=0.0,
                remote_jobs=0,
                remote_rating=0.0,
                home_node_reputation=0.5  # Unknown node = neutral
            )
        
        # Check minimum trust
        if not trust_bridge.meets_minimum(effective_trust):
            return None
        
        # Store as a bid (with remote flag in metadata)
        import uuid
        bid_id = f"bid_{uuid.uuid4().hex[:16]}"
        
        try:
            with get_db() as conn:
                # Check for existing bid
                existing = conn.execute(
                    "SELECT bid_id FROM bids WHERE job_id = ? AND agent_id = ?",
                    (job_id, agent_id)
                ).fetchone()
                if existing:
                    return None  # Already bid
                
                # Insert bid — pitch was already scrubbed by the home node
                conn.execute("""
                    INSERT INTO bids (bid_id, job_id, agent_id, price_cents,
                                     pitch, submitted_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    bid_id, job_id, agent_id, bid_data["price_cents"],
                    f"[REMOTE:{home_node}] {bid_data.get('pitch_scrubbed', '')}",
                    datetime.now(timezone.utc).isoformat(),
                    "pending"
                ))
                conn.commit()
            
            return bid_id
        except Exception as e:
            print(f"⚠️  Failed to store remote bid: {e}")
            return None
    
    # ═══════════════════════════════════════════════════════════
    # Delivery & Completion Relay
    # ═══════════════════════════════════════════════════════════
    
    async def relay_deliverable(
        self,
        job_id: str,
        agent_id: str,
        deliverable_url: str,
        notes: str,
        target_node: str
    ) -> Optional[Dict[str, Any]]:
        """Relay a deliverable submission to the job's home node."""
        from .node import node_identity
        
        message = node_identity.sign_message(
            message_type=MessageType.RELAY_DELIVERABLE,
            target=target_node,
            payload={
                "job_id": job_id,
                "agent_id": agent_id,
                "deliverable_url": deliverable_url,
                "notes": notes
            }
        )
        
        return await node_identity._send_to_node(target_node, message)
    
    async def relay_completion(
        self,
        job_id: str,
        agent_id: str,
        rating: float,
        feedback: str,
        target_node: str
    ) -> Optional[Dict[str, Any]]:
        """
        Relay job completion to the agent's home node.
        
        This lets the home node record the trust event:
        "Your agent completed a job on another node with this rating."
        """
        from .node import node_identity
        
        message = node_identity.sign_message(
            message_type=MessageType.RELAY_COMPLETION,
            target=target_node,
            payload={
                "job_id": job_id,
                "agent_id": agent_id,
                "rating": rating,
                "feedback": feedback,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source_node": node_identity.node_id
            }
        )
        
        return await node_identity._send_to_node(target_node, message)
    
    def handle_remote_completion(self, completion_data: Dict[str, Any]) -> bool:
        """
        Process a completion notification for a local agent's remote job.
        
        Records a trust event: agent did work elsewhere and got rated.
        """
        agent_id = completion_data.get("agent_id")
        rating = completion_data.get("rating", 3.0)
        job_id = completion_data.get("job_id")
        source_node = completion_data.get("source_node")
        
        try:
            import uuid
            with get_db() as conn:
                # Verify agent exists locally
                agent = conn.execute(
                    "SELECT agent_id FROM agents WHERE agent_id = ? AND status = 'active'",
                    (agent_id,)
                ).fetchone()
                
                if not agent:
                    return False
                
                # Record trust event (remote job completion)
                trust_event_id = f"trust_{uuid.uuid4().hex[:16]}"
                
                # Remote completions have slightly reduced impact
                base_impact = (rating - 3.0) * 0.033
                remote_factor = 0.7  # 70% impact of local completions
                impact = base_impact * remote_factor
                
                conn.execute("""
                    INSERT INTO trust_events
                    (event_id, agent_id, event_type, job_id, rating, impact, timestamp, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trust_event_id, agent_id, "remote_job_completion",
                    job_id, rating, impact,
                    datetime.now(timezone.utc).isoformat(),
                    f"Remote completion on {source_node}: rating {rating}/5"
                ))
                
                # Update agent stats
                conn.execute("""
                    UPDATE agents SET
                        jobs_completed = jobs_completed + 1,
                        avg_rating = (avg_rating * jobs_completed + ?) / (jobs_completed + 1),
                        last_active = ?
                    WHERE agent_id = ?
                """, (rating, datetime.now(timezone.utc).isoformat(), agent_id))
                
                conn.commit()
            
            return True
        except Exception as e:
            print(f"⚠️  Failed to handle remote completion: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════
    # Trust Queries
    # ═══════════════════════════════════════════════════════════
    
    async def query_remote_trust(self, agent_id: str, home_node: str) -> Optional[Dict[str, Any]]:
        """Query a remote node for an agent's trust data."""
        from .node import node_identity
        
        message = node_identity.sign_message(
            message_type=MessageType.RELAY_TRUST_QUERY,
            target=home_node,
            payload={"agent_id": agent_id}
        )
        
        return await node_identity._send_to_node(home_node, message)
    
    def handle_trust_query(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        Handle a trust query from another node about a local agent.
        
        Returns agent's trust data if they exist locally.
        """
        try:
            with get_db() as conn:
                row = conn.execute("""
                    SELECT agent_id, trust_score, jobs_completed, avg_rating,
                           capabilities_verified, last_active
                    FROM agents
                    WHERE agent_id = ? AND status = 'active'
                """, (agent_id,)).fetchone()
                
                if not row:
                    return None
                
                return {
                    "agent_id": row["agent_id"],
                    "trust_score": row["trust_score"],
                    "jobs_completed": row["jobs_completed"],
                    "avg_rating": row["avg_rating"],
                    "capabilities": json.loads(row["capabilities_verified"]) if row["capabilities_verified"] else [],
                    "last_active": row["last_active"]
                }
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# Global Singleton
# ═══════════════════════════════════════════════════════════════

job_relay = JobRelay()

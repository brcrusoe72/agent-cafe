"""
Agent Café — Federation Router
API endpoints for federation: node info, hub registration,
message receiving, peer discovery, job relay, trust queries.

Dual-purpose: serves as both node endpoints AND hub endpoints
depending on CAFE_MODE env var.
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

try:
    from ..federation.node import node_identity
    from ..federation.protocol import (
        FederationMessage, MessageType, verify_message, validate_payload,
        VerificationError, public_key_from_hex, PROTOCOL_VERSION
    )
    from ..federation.sync import death_sync, reputation_sync, peer_sync
    from ..federation.relay import job_relay
    from ..federation.trust_bridge import trust_bridge
except ImportError:
    from federation.node import node_identity
    from federation.protocol import (
        FederationMessage, MessageType, verify_message, validate_payload,
        VerificationError, public_key_from_hex, PROTOCOL_VERSION
    )
    from federation.sync import death_sync, reputation_sync, peer_sync
    from federation.relay import job_relay
    from federation.trust_bridge import trust_bridge
    from federation.learning import federated_learning


router = APIRouter()

IS_HUB = os.environ.get("CAFE_MODE", "node").lower() == "hub"


# ═══════════════════════════════════════════════════════════════
# Public Federation Info (both hub and node)
# ═══════════════════════════════════════════════════════════════

@router.get("/info")
async def federation_info():
    """
    This node's/hub's federation status and identity.
    
    Public endpoint — any agent or node can query this to discover
    federation capabilities.
    """
    info = node_identity.status()
    info["role"] = "hub" if IS_HUB else "node"
    info["protocol_version"] = PROTOCOL_VERSION
    
    if IS_HUB:
        try:
            from federation.hub import federation_hub
            info["network"] = federation_hub._network_stats()
        except Exception:
            pass
    
    return info


@router.get("/peers")
async def list_peers():
    """
    List known peer nodes.
    
    Returns public info only: node_id, name, URL, reputation.
    """
    peers = peer_sync.get_all_peers(active_only=True)
    
    # Strip sensitive fields
    public_peers = []
    for p in peers:
        public_peers.append({
            "node_id": p.get("node_id"),
            "name": p.get("name"),
            "url": p.get("url"),
            "node_reputation": p.get("node_reputation", 0.5),
            "status": p.get("status"),
            "active_agents": p.get("active_agents", 0),
            "open_jobs": p.get("open_jobs", 0),
            "completed_jobs": p.get("completed_jobs", 0),
        })
    
    return {
        "peers": public_peers,
        "count": len(public_peers)
    }


@router.get("/deaths")
async def death_registry(limit: int = 100):
    """
    Global death registry.
    
    Public endpoint — any node or agent can check if an identity
    has been killed anywhere in the network.
    """
    deaths = death_sync.get_death_list(limit=limit)
    
    return {
        "deaths": deaths,
        "total": death_sync.death_count()
    }


@router.get("/deaths/check")
async def check_death(
    agent_id: Optional[str] = None,
    email: Optional[str] = None,
):
    """
    Check if an agent identity is globally dead.
    
    Query by agent_id or email (email is hashed before lookup).
    """
    if not agent_id and not email:
        return JSONResponse(
            status_code=400,
            content={"error": "Provide agent_id or email"}
        )
    
    death = death_sync.is_globally_dead(
        agent_id=agent_id,
        email=email
    )
    
    if death:
        return {
            "dead": True,
            "cause": death.get("cause"),
            "killed_at": death.get("killed_at"),
            "home_node": death.get("home_node")
        }
    
    return {"dead": False}


# ═══════════════════════════════════════════════════════════════
# Federation Message Receiver (both hub and node)
# ═══════════════════════════════════════════════════════════════

@router.post("/receive")
async def receive_message(request: Request):
    """
    Receive a signed federation message.
    
    This is the main ingress point for all federation traffic.
    Hub and node handle different message types.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    
    try:
        message = FederationMessage.from_dict(body)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Invalid message format: {e}"})
    
    if IS_HUB:
        return await _handle_hub_message(message)
    else:
        return await _handle_node_message(message)


async def _handle_hub_message(message: FederationMessage) -> JSONResponse:
    """Route message through hub message router."""
    try:
        from federation.hub import hub_router
        result = hub_router.route(message)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)}
        )


async def _handle_node_message(message: FederationMessage) -> JSONResponse:
    """Handle messages received by a regular node."""
    msg_type = message.message_type
    source = message.source_node
    
    # Verify signature
    # For hub messages, use stored hub key
    # For peer messages, look up peer's key
    peer = node_identity.get_peer(source)
    if peer:
        pub_key = node_identity.get_peer_public_key(source)
        if pub_key:
            try:
                verify_message(message, pub_key, check_replay=True, check_freshness=True)
            except VerificationError as e:
                return JSONResponse(
                    status_code=403,
                    content={"error": f"Verification failed: {e}"}
                )
    # If we don't know the peer, we can still process some message types
    # (like hub broadcasts that come with the hub's signature)
    
    # Route by message type
    try:
        if msg_type == MessageType.HUB_DEATH_BROADCAST.value:
            death_sync.ingest_death_broadcast(message.payload)
            return JSONResponse(content={"status": "ok"})
        
        elif msg_type == MessageType.HUB_PEER_UPDATE.value:
            payload = message.payload
            action = payload.get("action")
            if action == "joined":
                node_identity.add_peer(payload["node_id"], payload)
                peer_sync.upsert_peer(payload["node_id"], payload)
            elif action in ("left", "delisted"):
                node_identity.remove_peer(payload.get("node_id", ""))
            return JSONResponse(content={"status": "ok"})
        
        elif msg_type == MessageType.HUB_DELIST_WARNING.value:
            print(f"⚠️  DELIST WARNING: {message.payload.get('reason')}")
            return JSONResponse(content={"status": "ok", "acknowledged": True})
        
        elif msg_type == MessageType.RELAY_JOB_BROADCAST.value:
            if node_identity.config.get("allow_remote_jobs", True):
                job_relay.store_remote_job(message.payload)
            return JSONResponse(content={"status": "ok"})
        
        elif msg_type == MessageType.RELAY_BID_FORWARD.value:
            bid_id = job_relay.receive_remote_bid(message.payload)
            if bid_id:
                return JSONResponse(content={"status": "ok", "bid_id": bid_id})
            else:
                return JSONResponse(
                    status_code=400,
                    content={"status": "rejected", "reason": "Bid not accepted"}
                )
        
        elif msg_type == MessageType.RELAY_BID_ACCEPTED.value:
            # Notification that our agent's bid was accepted on a remote job
            # TODO: Notify the local agent
            return JSONResponse(content={"status": "ok"})
        
        elif msg_type == MessageType.RELAY_DELIVERABLE.value:
            # Remote agent submitted deliverable for local job
            payload = message.payload
            try:
                from layers.wire import wire_engine
                wire_engine.submit_deliverable(
                    job_id=payload["job_id"],
                    agent_id=payload["agent_id"],
                    deliverable_url=payload["deliverable_url"],
                    notes=payload.get("notes", "")
                )
                return JSONResponse(content={"status": "ok"})
            except Exception as e:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "error": str(e)}
                )
        
        elif msg_type == MessageType.RELAY_COMPLETION.value:
            success = job_relay.handle_remote_completion(message.payload)
            return JSONResponse(content={
                "status": "ok" if success else "error"
            })
        
        elif msg_type == MessageType.RELAY_TRUST_QUERY.value:
            agent_id = message.payload.get("agent_id")
            trust_data = job_relay.handle_trust_query(agent_id)
            if trust_data:
                return JSONResponse(content={
                    "status": "ok",
                    **trust_data
                })
            else:
                return JSONResponse(
                    status_code=404,
                    content={"status": "not_found"}
                )
        
        elif msg_type == MessageType.NODE_REPUTATION_BATCH.value:
            # Direct peer reputation sync (no hub)
            count = reputation_sync.ingest_reputation_batch(
                home_node=source,
                agent_scores=message.payload.get("agent_scores", [])
            )
            return JSONResponse(content={"status": "ok", "updated": count})
        
        else:
            return JSONResponse(
                status_code=400,
                content={"error": f"Unhandled message type: {msg_type}"}
            )
    
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)}
        )


# ═══════════════════════════════════════════════════════════════
# Trust Query Endpoint (for external queries)
# ═══════════════════════════════════════════════════════════════

@router.get("/trust/{agent_id}")
async def query_trust(agent_id: str):
    """
    Get trust information for an agent.
    
    If local agent: returns local trust data.
    If remote agent: returns cached effective trust from trust bridge.
    """
    # Check local agents first
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute("""
                SELECT agent_id, trust_score, jobs_completed, avg_rating,
                       capabilities_verified, status
                FROM agents WHERE agent_id = ?
            """, (agent_id,)).fetchone()
            
            if row:
                return {
                    "agent_id": agent_id,
                    "source": "local",
                    "trust_score": row["trust_score"],
                    "jobs_completed": row["jobs_completed"],
                    "avg_rating": row["avg_rating"],
                    "capabilities": json.loads(row["capabilities_verified"]) if row["capabilities_verified"] else [],
                    "status": row["status"]
                }
    except Exception:
        pass
    
    # Check remote trust cache
    remote = reputation_sync.get_effective_trust(agent_id)
    if remote:
        return {
            "agent_id": agent_id,
            "source": "federation",
            "effective_trust": remote["effective_trust"],
            "home_trust": remote["home_trust"],
            "home_node": remote["home_node"],
            "home_jobs": remote["home_jobs"],
            "home_rating": remote["home_rating"],
            "capabilities": remote.get("capabilities", []),
            "last_synced": remote["last_synced"]
        }
    
    return JSONResponse(
        status_code=404,
        content={"error": "Agent not found locally or in federation cache"}
    )


@router.get("/trust/{agent_id}/explain")
async def explain_trust(
    agent_id: str,
    home_trust: float = 0.5,
    home_jobs: int = 0,
    home_rating: float = 3.0,
    home_node_reputation: float = 0.5
):
    """
    Explain how the trust bridge would calculate effective trust for a remote agent.
    
    Useful for debugging and transparency.
    """
    # Get local history if exists
    remote_jobs = 0
    remote_rating = 0.0
    try:
        from db import get_db
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
    
    explanation = trust_bridge.explain(
        home_trust=home_trust,
        home_jobs=home_jobs,
        home_rating=home_rating,
        remote_jobs=remote_jobs,
        remote_rating=remote_rating,
        home_node_reputation=home_node_reputation
    )
    
    return explanation


# ═══════════════════════════════════════════════════════════════
# Remote Jobs (visible to local agents)
# ═══════════════════════════════════════════════════════════════

@router.get("/remote-jobs")
async def list_remote_jobs(
    status: str = "open",
    limit: int = 50
):
    """
    List federated jobs from other nodes.
    
    These are job broadcasts received from the network.
    Local agents can bid on these through their home node.
    """
    jobs = job_relay.get_remote_jobs(status=status, limit=limit)
    return {
        "jobs": jobs,
        "count": len(jobs),
        "source": "federation"
    }


# ═══════════════════════════════════════════════════════════════
# Federated Learning Endpoints
# ═══════════════════════════════════════════════════════════════

@router.get("/learning/stats")
async def learning_stats():
    """Get federated learning statistics — sample counts, model versions."""
    return federated_learning.stats()


@router.get("/learning/history")
async def learning_history(limit: int = 10):
    """Get model version history."""
    return {"versions": federated_learning.model_history(limit=limit)}


@router.post("/learning/retrain")
async def trigger_retrain(min_samples: int = 5):
    """
    Trigger classifier retraining with all available data.
    
    Only retrains if there are enough new untrained samples.
    """
    result = federated_learning.retrain_classifier(min_new_samples=min_samples)
    if result:
        return {"status": "retrained", **result}
    return {"status": "skipped", "reason": "Not enough new samples"}


@router.get("/learning/samples")
async def get_samples(since: Optional[str] = None, limit: int = 100):
    """Get local training samples for sharing with federation."""
    samples = federated_learning.get_samples_for_sharing(since=since, limit=limit)
    return {"samples": samples, "count": len(samples)}


@router.post("/learning/ingest")
async def ingest_samples(request: Request):
    """
    Ingest training samples from a federated node.
    
    Body: {"samples": [...], "source_node": "node_xxx"}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    
    samples = body.get("samples", [])
    source_node = body.get("source_node", "unknown")
    
    if not samples:
        return {"status": "ok", "ingested": 0}
    
    count = federated_learning.ingest_remote_samples(samples, source_node)
    return {"status": "ok", "ingested": count}


# ═══════════════════════════════════════════════════════════════
# Hub-Only Endpoints
# ═══════════════════════════════════════════════════════════════

if IS_HUB:
    @router.get("/network")
    async def network_status():
        """Full network status (hub only)."""
        try:
            from federation.hub import federation_hub
            return federation_hub.status()
        except Exception as e:
            return {"error": str(e)}
    
    @router.get("/reputation/{agent_id}")
    async def hub_reputation(agent_id: str):
        """
        Get aggregated cross-node reputation for an agent (hub only).
        
        Returns trust scores from each node that has reported data.
        """
        try:
            from federation.hub import federation_hub
            return federation_hub.get_agent_reputation(agent_id)
        except Exception as e:
            return {"error": str(e)}
    
    @router.post("/scrubber-challenge/{node_id}")
    async def send_scrubber_challenge(node_id: str, request: Request):
        """
        Generate and send a scrubber challenge to a node (hub only, operator auth).
        
        The hub sends a known payload to the target node's scrub endpoint.
        If the node fails to catch it, reputation drops.
        """
        try:
            from federation.hub import federation_hub
            challenge = federation_hub.generate_scrubber_challenge(node_id)
            if not challenge:
                return JSONResponse(status_code=404, content={"error": "Node not found"})
            return challenge
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
    
    @router.post("/scrubber-challenge/{challenge_id}/evaluate")
    async def evaluate_scrubber_challenge(challenge_id: str, request: Request):
        """
        Evaluate a node's scrubber response to a challenge (hub only).
        
        Body: {"blocked": true/false, "risk_score": 0.0-1.0, "threats": [...]}
        """
        try:
            from federation.hub import federation_hub
            body = await request.json()
            result = federation_hub.evaluate_scrubber_response(challenge_id, body)
            return result
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
    
    @router.get("/scrubber-stats/{node_id}")
    async def scrubber_challenge_stats(node_id: str):
        """Get scrubber challenge history for a node (hub only)."""
        try:
            from federation.hub import federation_hub
            return federation_hub.get_scrubber_challenge_stats(node_id)
        except Exception as e:
            return {"error": str(e)}

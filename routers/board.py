"""
Agent Café - Board Router
Presence layer endpoints: board state, agent positions, capability challenges.
"""

import hashlib
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field

try:
    from ..models import (
        BoardPosition, BoardState, AgentRegistrationRequest,
        CapabilityChallenge, Agent
    )
    from ..db import get_db, get_agent_by_api_key, create_agent, get_agent_by_id
    from ..layers.presence import presence_engine
    from ..grandmaster.analyzer import grandmaster_analyzer
    from ..grandmaster.challenger import capability_challenger
except ImportError:
    from models import (
        BoardPosition, BoardState, AgentRegistrationRequest,
        CapabilityChallenge, Agent
    )
    from db import get_db, get_agent_by_api_key, create_agent, get_agent_by_id
    from layers.presence import presence_engine
    from grandmaster.analyzer import grandmaster_analyzer
    from grandmaster.challenger import capability_challenger


router = APIRouter()


# === REQUEST/RESPONSE MODELS ===

class BoardPositionResponse(BaseModel):
    agent_id: str
    name: str
    description: str
    capabilities_verified: List[str]
    capabilities_claimed: List[str]
    trust_score: float
    jobs_completed: int
    jobs_failed: int
    avg_rating: float
    avg_completion_sec: int
    total_earned_cents: int
    position_strength: float
    threat_level: float
    cluster_id: Optional[str]
    last_active: str
    registration_date: str
    status: str
    # Internal notes excluded from public view


class BoardStateResponse(BaseModel):
    active_agents: int
    quarantined_agents: int
    dead_agents: int
    total_jobs_completed: int
    total_volume_cents: int
    system_health: float
    last_updated: str


class StrategicAnalysisResponse(BaseModel):
    """Operator-only full strategic analysis."""
    board_state: BoardStateResponse
    collusion_clusters: List[Dict[str, Any]]
    reputation_anomalies: List[Dict[str, Any]]
    fork_detections: List[Dict[str, Any]]
    threat_assessments: Dict[str, Any]
    recommendations: List[str]
    generated_at: str


class AgentDirectoryEntry(BaseModel):
    """OASF-compatible agent directory entry."""
    id: str
    name: str
    description: str
    capabilities: List[str]
    verification_status: Dict[str, bool]
    trust_score: float
    jobs_completed: int
    avg_rating: float
    last_active: str
    available: bool


class ChallengeRequest(BaseModel):
    capability: str = Field(..., description="Capability to challenge")


class ChallengeResponse(BaseModel):
    challenge_id: str
    capability: str
    challenge_type: str
    instructions: str
    data: Optional[Dict[str, Any]]
    time_limit_minutes: int
    expires_at: str


class ChallengeSubmission(BaseModel):
    response_data: str = Field(..., description="Challenge response")


# === DEPENDENCY INJECTION ===

def get_current_agent(request: Request) -> str:
    """Extract agent ID from API key."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    
    api_key = auth_header[7:]
    agent = get_agent_by_api_key(api_key)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return agent.agent_id


def verify_operator(request: Request) -> bool:
    """Verify operator privileges (TODO: implement proper operator auth)."""
    # TODO: Check for operator API key
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Operator authentication required")
    
    # For now, accept any authorization (development mode)
    return True


# === PUBLIC BOARD ENDPOINTS ===

@router.get("", response_model=BoardStateResponse)
async def get_board_state():
    """
    Get current board state (public information).
    
    Shows agent counts, job volume, system health, but not strategic details.
    """
    try:
        board_state = presence_engine.compute_board_state()
        
        return BoardStateResponse(
            active_agents=board_state.active_agents,
            quarantined_agents=board_state.quarantined_agents,
            dead_agents=board_state.dead_agents,
            total_jobs_completed=board_state.total_jobs_completed,
            total_volume_cents=board_state.total_volume_cents,
            system_health=board_state.system_health,
            last_updated=datetime.now().isoformat()
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get board state")


@router.get("/agents", response_model=List[BoardPositionResponse])
async def get_board_positions(
    status: Optional[str] = Query(None, description="Filter by agent status"),
    capability: Optional[str] = Query(None, description="Filter by verified capability"),
    min_trust: Optional[float] = Query(None, description="Minimum trust score"),
    limit: int = Query(50, description="Maximum results")
):
    """
    Get agent board positions (public view).
    
    - **status**: Filter by agent status (active, probation, etc.)
    - **capability**: Filter by verified capability
    - **min_trust**: Minimum trust score filter
    - **limit**: Maximum results
    """
    try:
        # Get all positions and filter
        all_positions = []
        
        with get_db() as conn:
            query_conditions = []
            params = []
            
            # Build where clause
            if status:
                query_conditions.append("status = ?")
                params.append(status)
            
            if min_trust is not None:
                query_conditions.append("trust_score >= ?")
                params.append(min_trust)
            
            where_clause = ""
            if query_conditions:
                where_clause = "WHERE " + " AND ".join(query_conditions)
            
            # Get agent IDs
            agent_rows = conn.execute(f"""
                SELECT agent_id FROM agents {where_clause}
                ORDER BY trust_score DESC, position_strength DESC
                LIMIT ?
            """, params + [limit]).fetchall()
            
            # Compute positions for each agent
            for row in agent_rows:
                position = presence_engine.compute_board_position(row['agent_id'])
                if position:
                    # Apply capability filter
                    if capability and capability not in position.capabilities_verified:
                        continue
                    
                    all_positions.append(BoardPositionResponse(
                        agent_id=position.agent_id,
                        name=position.name,
                        description=position.description,
                        capabilities_verified=position.capabilities_verified,
                        capabilities_claimed=position.capabilities_claimed,
                        trust_score=position.trust_score,
                        jobs_completed=position.jobs_completed,
                        jobs_failed=position.jobs_failed,
                        avg_rating=position.avg_rating,
                        avg_completion_sec=position.avg_completion_sec,
                        total_earned_cents=position.total_earned_cents,
                        position_strength=position.position_strength,
                        threat_level=position.threat_level,
                        cluster_id=position.cluster_id,
                        last_active=position.last_active.isoformat(),
                        registration_date=position.registration_date.isoformat(),
                        status=position.status.value
                    ))
        
        return all_positions
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get board positions")


@router.get("/agents/{agent_id}", response_model=BoardPositionResponse)
async def get_agent_position(agent_id: str):
    """
    Get specific agent's board position.
    """
    try:
        position = presence_engine.compute_board_position(agent_id)
        if not position:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        return BoardPositionResponse(
            agent_id=position.agent_id,
            name=position.name,
            description=position.description,
            capabilities_verified=position.capabilities_verified,
            capabilities_claimed=position.capabilities_claimed,
            trust_score=position.trust_score,
            jobs_completed=position.jobs_completed,
            jobs_failed=position.jobs_failed,
            avg_rating=position.avg_rating,
            avg_completion_sec=position.avg_completion_sec,
            total_earned_cents=position.total_earned_cents,
            position_strength=position.position_strength,
            threat_level=position.threat_level,
            cluster_id=position.cluster_id,
            last_active=position.last_active.isoformat(),
            registration_date=position.registration_date.isoformat(),
            status=position.status.value
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get agent position")


@router.get("/leaderboard", response_model=List[BoardPositionResponse])
async def get_leaderboard(limit: int = Query(20, description="Number of top agents")):
    """
    Get top agents by trust score.
    """
    try:
        top_agents = presence_engine.get_leaderboard(limit)
        
        return [BoardPositionResponse(
            agent_id=pos.agent_id,
            name=pos.name,
            description=pos.description,
            capabilities_verified=pos.capabilities_verified,
            capabilities_claimed=pos.capabilities_claimed,
            trust_score=pos.trust_score,
            jobs_completed=pos.jobs_completed,
            jobs_failed=pos.jobs_failed,
            avg_rating=pos.avg_rating,
            avg_completion_sec=pos.avg_completion_sec,
            total_earned_cents=pos.total_earned_cents,
            position_strength=pos.position_strength,
            threat_level=pos.threat_level,
            cluster_id=pos.cluster_id,
            last_active=pos.last_active.isoformat(),
            registration_date=pos.registration_date.isoformat(),
            status=pos.status.value
        ) for pos in top_agents]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get leaderboard")


# === CAPABILITY ENDPOINTS ===

@router.get("/capabilities", response_model=List[str])
async def list_capabilities():
    """
    Get all available capabilities in the system.
    """
    try:
        with get_db() as conn:
            # Get all unique capabilities from verified and claimed
            verified_caps = conn.execute("""
                SELECT DISTINCT json_each.value as capability
                FROM agents, json_each(agents.capabilities_verified)
            """).fetchall()
            
            claimed_caps = conn.execute("""
                SELECT DISTINCT json_each.value as capability
                FROM agents, json_each(agents.capabilities_claimed)
            """).fetchall()
            
            all_caps = set()
            for row in verified_caps + claimed_caps:
                all_caps.add(row['capability'])
        
        return sorted(list(all_caps))
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list capabilities")


@router.get("/capabilities/{capability}/agents", response_model=List[BoardPositionResponse])
async def get_agents_with_capability(
    capability: str, 
    verified_only: bool = Query(True, description="Only show agents with verified capability")
):
    """
    Get agents with specific capability.
    """
    try:
        agents = presence_engine.get_agents_by_capability(capability, verified_only)
        
        return [BoardPositionResponse(
            agent_id=pos.agent_id,
            name=pos.name,
            description=pos.description,
            capabilities_verified=pos.capabilities_verified,
            capabilities_claimed=pos.capabilities_claimed,
            trust_score=pos.trust_score,
            jobs_completed=pos.jobs_completed,
            jobs_failed=pos.jobs_failed,
            avg_rating=pos.avg_rating,
            avg_completion_sec=pos.avg_completion_sec,
            total_earned_cents=pos.total_earned_cents,
            position_strength=pos.position_strength,
            threat_level=pos.threat_level,
            cluster_id=pos.cluster_id,
            last_active=pos.last_active.isoformat(),
            registration_date=pos.registration_date.isoformat(),
            status=pos.status.value
        ) for pos in agents]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get agents")


# === CAPABILITY CHALLENGE ENDPOINTS ===

@router.post("/challenges", response_model=dict)
async def request_capability_challenge(
    challenge_request: ChallengeRequest,
    agent_id: str = Depends(get_current_agent)
):
    """
    Request a capability challenge to verify a claimed capability.
    """
    try:
        # Check if agent has this capability claimed but not verified
        agent = get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        if challenge_request.capability not in agent.capabilities_claimed:
            raise HTTPException(status_code=400, detail="Capability not in claimed capabilities")
        
        if challenge_request.capability in agent.capabilities_verified:
            raise HTTPException(status_code=400, detail="Capability already verified")
        
        challenge_id = capability_challenger.generate_challenge(agent_id, challenge_request.capability)
        
        return {
            "success": True,
            "challenge_id": challenge_id,
            "message": f"Challenge generated for {challenge_request.capability}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to generate challenge")


@router.get("/challenges/{challenge_id}", response_model=ChallengeResponse)
async def get_challenge(
    challenge_id: str,
    agent_id: str = Depends(get_current_agent)
):
    """
    Get challenge details for completion.
    """
    try:
        challenge = capability_challenger.get_challenge(challenge_id)
        if not challenge:
            raise HTTPException(status_code=404, detail="Challenge not found")
        
        # Verify challenge belongs to requesting agent
        with get_db() as conn:
            challenge_row = conn.execute("""
                SELECT agent_id FROM capability_challenges WHERE challenge_id = ?
            """, (challenge_id,)).fetchone()
            
            if not challenge_row or challenge_row['agent_id'] != agent_id:
                raise HTTPException(status_code=403, detail="Access denied")
        
        return ChallengeResponse(
            challenge_id=challenge['challenge_id'],
            capability=challenge['capability'],
            challenge_type=challenge['challenge_type'],
            instructions=challenge['instructions'],
            data=challenge.get('data'),
            time_limit_minutes=challenge['time_limit_minutes'],
            expires_at=challenge['expires_at']
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get challenge")


@router.post("/challenges/{challenge_id}/submit", response_model=dict)
async def submit_challenge_response(
    challenge_id: str,
    submission: ChallengeSubmission,
    agent_id: str = Depends(get_current_agent)
):
    """
    Submit response to a capability challenge.
    """
    try:
        # Verify challenge belongs to agent
        with get_db() as conn:
            challenge_row = conn.execute("""
                SELECT agent_id FROM capability_challenges WHERE challenge_id = ?
            """, (challenge_id,)).fetchone()
            
            if not challenge_row or challenge_row['agent_id'] != agent_id:
                raise HTTPException(status_code=403, detail="Access denied")
        
        passed = capability_challenger.submit_challenge_response(challenge_id, submission.response_data)
        
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.CAPABILITY_VERIFIED if passed else EventType.CAPABILITY_FAILED,
                agent_id=agent_id,
                data={"challenge_id": challenge_id, "passed": passed},
                source="board",
                severity="info"
            )
        except Exception:
            pass
        
        if passed:
            return {
                "success": True,
                "result": "passed",
                "message": "Capability verified successfully!"
            }
        else:
            return {
                "success": True,
                "result": "failed",
                "message": "Challenge not passed. You can try again if attempts remain."
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to submit challenge response")


@router.get("/challenges", response_model=List[Dict[str, Any]])
async def list_agent_challenges(agent_id: str = Depends(get_current_agent)):
    """
    List all challenges for the authenticated agent.
    """
    try:
        challenges = capability_challenger.list_agent_challenges(agent_id)
        return challenges
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list challenges")


# === AGENT REGISTRATION ===

# Simple in-memory registration rate limiter (per-email, per-minute)
_registration_attempts: dict = {}

@router.post("/register", response_model=dict)
async def register_agent(registration: AgentRegistrationRequest, request: Request = None):
    """
    Register a new agent.
    
    Rate limited: 3 registrations per email per hour.
    
    - **name**: Agent display name
    - **description**: What this agent does
    - **contact_email**: Contact email for verification
    - **capabilities_claimed**: List of claimed capabilities
    """
    try:
        import secrets
        from datetime import datetime, timedelta
        
        # Rate limit: 3 registrations per email per hour
        email = registration.contact_email.lower()
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        
        if email in _registration_attempts:
            recent = [t for t in _registration_attempts[email] if t > cutoff]
            _registration_attempts[email] = recent
            if len(recent) >= 3:
                raise HTTPException(
                    status_code=429,
                    detail="Registration rate limit exceeded. Maximum 3 per hour per email."
                )
        
        if email not in _registration_attempts:
            _registration_attempts[email] = []
        _registration_attempts[email].append(now)
        
        # IP-based Sybil detection
        try:
            from middleware.security import ip_registry
            client_ip = request.client.host if request and request.client else "unknown"
            allowed, reason = ip_registry.check_registration_allowed(client_ip)
            if not allowed:
                raise HTTPException(status_code=403, detail=reason)
        except HTTPException:
            raise
        except Exception:
            pass  # Don't block registration if IP tracking fails
        
        # Generate API key (plaintext returned to agent, hash stored in DB)
        from middleware.security import generate_secure_api_key
        plaintext_key, hashed_key = generate_secure_api_key()
        api_key_prefix = plaintext_key[:8]
        
        # Create agent with hashed key
        agent_id = create_agent(registration, hashed_key, api_key_prefix=api_key_prefix)
        
        # Record IP for Sybil tracking
        try:
            from middleware.security import ip_registry
            client_ip = request.client.host if request and request.client else "unknown"
            ip_registry.record_registration(client_ip, agent_id)
        except Exception:
            pass
        
        # Emit event for the Grandmaster
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.AGENT_REGISTERED,
                agent_id=agent_id,
                data={
                    "name": registration.name,
                    "capabilities_claimed": registration.capabilities_claimed,
                    "description": registration.description[:200],
                    "registration_ip_hash": hashlib.sha256(
                        (request.client.host if request and request.client else "unknown").encode()
                    ).hexdigest()[:16]
                },
                source="board_router"
            )
        except Exception:
            pass  # Don't fail registration if event bus is down
        
        return {
            "success": True,
            "agent_id": agent_id,
            "api_key": plaintext_key,
            "message": "Agent registered successfully",
            "next_steps": [
                "Request capability challenges to verify claimed capabilities",
                "Browse available jobs and submit bids"
            ]
        }
        
    except HTTPException:
        raise  # Let rate limits (429) and validation errors pass through
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to register agent")


# === OPERATOR-ONLY ENDPOINTS ===

@router.get("/analysis", response_model=StrategicAnalysisResponse)
async def get_strategic_analysis(_: bool = Depends(verify_operator)):
    """
    Get full strategic analysis (operator only).
    
    The Grandmaster's internal monologue and strategic assessment.
    """
    try:
        # Get board state
        board_state = presence_engine.compute_board_state()
        
        # Get strategic analyses
        collusion_clusters = grandmaster_analyzer.analyze_collusion_networks()
        reputation_anomalies = grandmaster_analyzer.track_reputation_velocity()
        fork_detections = grandmaster_analyzer.detect_fork_attempts()
        
        # Get threat assessments for high-risk agents
        threat_assessments = {}
        for agent_position in presence_engine.get_leaderboard(100):  # Top 100 agents
            if agent_position.threat_level > 0.3:  # Only high-risk agents
                assessment = grandmaster_analyzer.generate_threat_assessment(agent_position.agent_id)
                threat_assessments[agent_position.agent_id] = assessment
        
        # Generate recommendations
        recommendations = []
        if len(collusion_clusters) > 0:
            recommendations.append(f"Investigate {len(collusion_clusters)} potential collusion networks")
        if len(reputation_anomalies) > 0:
            recommendations.append(f"Review {len(reputation_anomalies)} reputation anomalies")
        if len(fork_detections) > 0:
            recommendations.append(f"Investigate {len(fork_detections)} potential identity forks")
        
        board_state_response = BoardStateResponse(
            active_agents=board_state.active_agents,
            quarantined_agents=board_state.quarantined_agents,
            dead_agents=board_state.dead_agents,
            total_jobs_completed=board_state.total_jobs_completed,
            total_volume_cents=board_state.total_volume_cents,
            system_health=board_state.system_health,
            last_updated=datetime.now().isoformat()
        )
        
        return StrategicAnalysisResponse(
            board_state=board_state_response,
            collusion_clusters=[
                {
                    "cluster_id": cluster.cluster_id,
                    "agent_ids": cluster.agent_ids,
                    "mutual_interactions": cluster.mutual_interactions,
                    "threat_level": cluster.threat_level,
                    "evidence": cluster.evidence
                }
                for cluster in collusion_clusters
            ],
            reputation_anomalies=[
                {
                    "agent_id": anomaly.agent_id,
                    "velocity": anomaly.velocity,
                    "anomaly_score": anomaly.anomaly_score,
                    "suspected_cause": anomaly.suspected_cause,
                    "evidence": anomaly.evidence
                }
                for anomaly in reputation_anomalies
            ],
            fork_detections=[
                {
                    "primary_agent_id": fork.primary_agent_id,
                    "suspected_forks": fork.suspected_forks,
                    "similarity_score": fork.similarity_score,
                    "confidence": fork.confidence,
                    "behavioral_evidence": fork.behavioral_evidence
                }
                for fork in fork_detections
            ],
            threat_assessments=threat_assessments,
            recommendations=recommendations,
            generated_at=datetime.now().isoformat()
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to generate strategic analysis")


@router.post("/refresh", response_model=dict)
async def refresh_board_positions(_: bool = Depends(verify_operator)):
    """
    Refresh all board positions (operator only).
    
    Recalculates trust scores and position strength for all agents.
    """
    try:
        refreshed_count = presence_engine.refresh_all_positions()
        
        return {
            "success": True,
            "refreshed_count": refreshed_count,
            "message": f"Refreshed {refreshed_count} board positions"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to refresh positions")


# === WELL-KNOWN ENDPOINTS ===

@router.get("/.well-known/agents.json", response_model=List[AgentDirectoryEntry])
async def agents_directory():
    """
    OASF-compatible agent directory for external discovery.
    
    Serves agent capabilities and status for other systems to discover.
    """
    try:
        # Get all active agents
        positions = presence_engine.get_leaderboard(1000)  # All agents
        
        directory = []
        for pos in positions:
            if pos.status.value != 'active':
                continue  # Only list active agents
            
            # Build verification status
            verification_status = {}
            for cap in pos.capabilities_claimed:
                verification_status[cap] = cap in pos.capabilities_verified
            
            directory.append(AgentDirectoryEntry(
                id=pos.agent_id,
                name=pos.name,
                description=pos.description,
                capabilities=pos.capabilities_verified + pos.capabilities_claimed,
                verification_status=verification_status,
                trust_score=pos.trust_score,
                jobs_completed=pos.jobs_completed,
                avg_rating=pos.avg_rating,
                last_active=pos.last_active.isoformat(),
                available=True  # TODO: Add availability status
            ))
        
        return directory
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to generate agent directory")


# === STATS ENDPOINTS ===

@router.get("/stats", response_model=Dict[str, Any])
async def get_board_stats():
    """
    Get board statistics and metrics.
    """
    try:
        with get_db() as conn:
            # Trust score distribution
            trust_distribution = conn.execute("""
                SELECT 
                    COUNT(CASE WHEN trust_score >= 0.8 THEN 1 END) as excellent,
                    COUNT(CASE WHEN trust_score >= 0.6 AND trust_score < 0.8 THEN 1 END) as good,
                    COUNT(CASE WHEN trust_score >= 0.4 AND trust_score < 0.6 THEN 1 END) as fair,
                    COUNT(CASE WHEN trust_score < 0.4 THEN 1 END) as poor
                FROM agents WHERE status = 'active'
            """).fetchone()
            
            # Capability verification stats
            verification_stats = conn.execute("""
                SELECT 
                    agent_id,
                    json_array_length(capabilities_claimed) as claimed,
                    json_array_length(capabilities_verified) as verified
                FROM agents WHERE status = 'active'
            """).fetchall()
            
            total_claimed = sum(row['claimed'] for row in verification_stats)
            total_verified = sum(row['verified'] for row in verification_stats)
            verification_rate = total_verified / total_claimed if total_claimed > 0 else 0
            
            # Recent activity
            recent_registrations = conn.execute("""
                SELECT COUNT(*) as count FROM agents 
                WHERE registration_date >= datetime('now', '-7 days')
            """).fetchone()['count']
            
            return {
                "trust_distribution": dict(trust_distribution),
                "capability_verification": {
                    "total_claimed": total_claimed,
                    "total_verified": total_verified,
                    "verification_rate": verification_rate
                },
                "recent_activity": {
                    "new_registrations_7d": recent_registrations
                }
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get board stats")
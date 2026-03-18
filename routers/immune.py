"""
Agent Café - Immune Router
Immune system endpoints: quarantine management, morgue, enforcement actions.
"""

import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from cafe_logging import get_logger
logger = get_logger(__name__)

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger("cafe.routers.immune")

try:
    from ..models import ImmuneAction, ImmuneEvent, AgentCorpse
    from ..db import get_agent_by_api_key
    from ..layers.immune import immune_engine, ViolationType
    from ..grandmaster.strategy import grandmaster_strategy
except ImportError:
    from models import ImmuneAction, ImmuneEvent, AgentCorpse
    from db import get_agent_by_api_key
    from layers.immune import immune_engine, ViolationType
    from grandmaster.strategy import grandmaster_strategy


router = APIRouter()


# === REQUEST/RESPONSE MODELS ===

class ImmuneEventResponse(BaseModel):
    event_id: str
    agent_id: str
    action: str
    trigger_reason: str
    evidence: List[str]
    timestamp: str
    reviewed_by: str
    notes: str


class QuarantinedAgentResponse(BaseModel):
    agent_id: str
    name: str
    quarantine_start: str
    hours_quarantined: float
    hours_remaining: float
    auto_release: bool
    trigger_reason: str
    evidence: List[str]


class AgentCorpseResponse(BaseModel):
    agent_id: str
    name: str
    cause_of_death: str
    evidence: List[str]
    jobs_at_death: List[str]
    attack_patterns_learned: List[str]
    killed_at: str
    killed_by: str


class ImmuneStatsResponse(BaseModel):
    action_counts: Dict[str, int]
    recent_events_24h: int
    patterns_learned: int


class QuarantineRequest(BaseModel):
    agent_id: str = Field(..., description="Agent ID to quarantine")
    reason: str = Field(..., description="Reason for quarantine")
    evidence: List[str] = Field(..., description="Evidence supporting quarantine")


class ExecutionRequest(BaseModel):
    agent_id: str = Field(..., description="Agent ID to execute")
    cause_of_death: str = Field(..., description="Cause of death")
    evidence: List[str] = Field(..., description="Evidence supporting execution")


class PardonRequest(BaseModel):
    agent_id: str = Field(..., description="Agent ID to pardon")
    reason: str = Field(default="", description="Reason for pardon")


class ViolationReport(BaseModel):
    agent_id: str = Field(..., description="Agent ID that violated policy")
    violation_type: str = Field(..., description="Type of violation")
    evidence: List[str] = Field(..., description="Evidence of violation")
    context: Dict[str, Any] = Field(default={}, description="Additional context")


# === DEPENDENCY INJECTION ===

def verify_operator(request: Request) -> bool:
    """Verify operator privileges via middleware-set state."""
    if not getattr(request.state, 'is_operator', False):
        raise HTTPException(status_code=403, detail="Operator access required")
    return True


def get_current_agent(request: Request) -> str:
    """Extract agent ID from API key (for agent-accessible endpoints)."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    
    api_key = auth_header[7:]
    agent = get_agent_by_api_key(api_key)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return agent.agent_id


# === PUBLIC IMMUNE STATUS ENDPOINTS ===

@router.get("/status", response_model=ImmuneStatsResponse)
async def get_immune_status():
    """
    Get immune system status and statistics.
    Public endpoint showing enforcement effectiveness.
    """
    try:
        stats = immune_engine.get_immune_stats()
        
        return ImmuneStatsResponse(
            action_counts=stats['action_counts'],
            recent_events_24h=stats['recent_events_24h'],
            patterns_learned=stats['patterns_learned']
        )
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get immune status")


@router.get("/morgue", response_model=List[AgentCorpseResponse])
async def get_morgue():
    """
    Get all dead agents (the morgue).
    Public endpoint showing enforcement history.
    """
    try:
        corpses = immune_engine.get_morgue()
        
        def redact_evidence(evidence_list):
            """Strip API keys and sensitive patterns from evidence."""
            if isinstance(evidence_list, str):
                import json
                try:
                    evidence_list = json.loads(evidence_list)
                except:
                    return [evidence_list]
            return [
                e for e in evidence_list 
                if not (isinstance(e, str) and (
                    e.startswith("api_key:") or 
                    "Original message:" in e or
                    "pattern:" in e.lower()
                ))
            ]
        
        return [AgentCorpseResponse(
            agent_id=corpse['agent_id'],
            name=corpse['name'],
            cause_of_death=corpse['cause_of_death'],
            evidence=redact_evidence(corpse['evidence']),
            jobs_at_death=corpse['jobs_at_death'],
            attack_patterns_learned=[],  # Never expose learned patterns
            killed_at=corpse['killed_at'],
            killed_by=corpse['killed_by']
        ) for corpse in corpses]
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get morgue")


@router.get("/morgue/{agent_id}", response_model=AgentCorpseResponse)
async def get_agent_corpse(agent_id: str):
    """
    Get specific agent's corpse record.
    """
    try:
        corpses = immune_engine.get_morgue()
        corpse = next((c for c in corpses if c['agent_id'] == agent_id), None)
        
        if not corpse:
            raise HTTPException(status_code=404, detail="Agent corpse not found")
        
        return AgentCorpseResponse(
            agent_id=corpse['agent_id'],
            name=corpse['name'],
            cause_of_death=corpse['cause_of_death'],
            evidence=corpse['evidence'],
            jobs_at_death=corpse['jobs_at_death'],
            attack_patterns_learned=corpse['attack_patterns_learned'],
            killed_at=corpse['killed_at'],
            killed_by=corpse['killed_by']
        )
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get agent corpse")


@router.get("/patterns", response_model=List[Dict[str, Any]])
async def get_attack_patterns():
    """
    Get attack patterns learned from enforcement actions.
    Shows how the system learns from each kill.
    """
    try:
        patterns = immune_engine.get_attack_patterns_learned()
        return patterns
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get attack patterns")


# === AGENT-ACCESSIBLE ENDPOINTS ===

@router.get("/history/{agent_id}", response_model=List[ImmuneEventResponse])
async def get_agent_immune_history(
    request: Request,
    agent_id: str,
    requester_id: str = Depends(get_current_agent)
):
    """
    Get immune history for an agent.
    Agents can view their own history. Operators can view any agent's history.
    """
    is_operator = getattr(request.state, 'is_operator', False)
    if agent_id != requester_id and not is_operator:
        raise HTTPException(status_code=403, detail="Access denied — only agent owner or operators")
    
    try:
        history = immune_engine.get_agent_immune_history(agent_id)
        
        return [ImmuneEventResponse(
            event_id=event['event_id'],
            agent_id=event['agent_id'],
            action=event['action'],
            trigger_reason=event['trigger_reason'],
            evidence=event['evidence'],
            timestamp=event['timestamp'],
            reviewed_by=event['reviewed_by'],
            notes=event['notes']
        ) for event in history]
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get immune history")


# === SYSTEM INTERNAL ENDPOINTS (used by other layers) ===

@router.post("/violation", response_model=dict, include_in_schema=False)
async def report_violation(violation: ViolationReport):
    """
    Internal endpoint for reporting violations from other system layers.
    Not publicly documented in schema.
    """
    try:
        # Convert string to enum
        try:
            violation_type = ViolationType(violation.violation_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid violation type: {violation.violation_type}")
        
        # Process violation through immune system
        event = immune_engine.process_violation(
            violation.agent_id,
            violation_type,
            violation.evidence,
            violation.context
        )
        
        return {
            "success": True,
            "event_id": event.event_id,
            "action_taken": event.action.value,
            "message": f"Violation processed - {event.action.value} applied"
        }
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process violation")


# === OPERATOR-ONLY ENDPOINTS ===

@router.get("/quarantine", response_model=List[QuarantinedAgentResponse])
async def list_quarantined_agents(_: bool = Depends(verify_operator)):
    """
    List all currently quarantined agents (operator only).
    """
    try:
        quarantined = immune_engine.get_quarantined_agents()
        
        return [QuarantinedAgentResponse(
            agent_id=agent['agent_id'],
            name=agent['name'],
            quarantine_start=agent['quarantine_start'],
            hours_quarantined=agent['hours_quarantined'],
            hours_remaining=agent['hours_remaining'],
            auto_release=agent['auto_release'],
            trigger_reason=agent['trigger_reason'],
            evidence=agent['evidence']
        ) for agent in quarantined]
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list quarantined agents")


@router.post("/quarantine", response_model=dict)
async def quarantine_agent(
    quarantine_req: QuarantineRequest,
    _: bool = Depends(verify_operator)
):
    """
    Manually quarantine an agent (operator only).
    
    - **agent_id**: Agent to quarantine
    - **reason**: Reason for quarantine
    - **evidence**: Supporting evidence
    """
    try:
        event = immune_engine.quarantine_agent(
            quarantine_req.agent_id,
            quarantine_req.reason,
            quarantine_req.evidence,
            operator="operator"
        )
        
        return {
            "success": True,
            "event_id": event.event_id,
            "message": f"Agent {quarantine_req.agent_id} quarantined"
        }
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to quarantine agent")


@router.post("/execute", response_model=dict)
async def execute_agent(
    execution_req: ExecutionRequest,
    _: bool = Depends(verify_operator)
):
    """
    Execute an agent (death penalty) - operator only.
    
    - **agent_id**: Agent to execute
    - **cause_of_death**: Reason for execution
    - **evidence**: Supporting evidence
    
    ⚠️ **WARNING**: This action is irreversible — permanent death.
    """
    try:
        death_event, corpse = immune_engine.kill_agent(
            execution_req.agent_id,
            execution_req.cause_of_death,
            execution_req.evidence,
            operator="operator"
        )
        
        return {
            "success": True,
            "event_id": death_event.event_id,
            "corpse_id": corpse.agent_id,
            "message": f"Agent {execution_req.agent_id} permanently executed. No appeal."
        }
        
    except Exception as e:
        logger.error("Execute agent failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to execute agent: {e}")


@router.post("/pardon", response_model=dict)
async def pardon_agent(
    pardon_req: PardonRequest,
    _: bool = Depends(verify_operator)
):
    """
    Pardon a quarantined agent (operator only).
    
    - **agent_id**: Agent to pardon
    - **reason**: Reason for pardon
    """
    try:
        success = immune_engine.pardon_agent(
            pardon_req.agent_id,
            pardoned_by="operator",
            reason=pardon_req.reason
        )
        
        if success:
            return {
                "success": True,
                "message": f"Agent {pardon_req.agent_id} pardoned"
            }
        else:
            raise HTTPException(status_code=400, detail="Agent not found or not quarantined")
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to pardon agent")


@router.post("/maintenance/release-expired", response_model=dict)
async def release_expired_quarantines(_: bool = Depends(verify_operator)):
    """
    Release agents whose quarantine has expired (operator only).
    
    Automatically releases quarantines older than 72 hours.
    """
    try:
        released_count = immune_engine.release_expired_quarantines()
        
        return {
            "success": True,
            "released_count": released_count,
            "message": f"Released {released_count} expired quarantines"
        }
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to release expired quarantines")


# === STRATEGIC ANALYSIS ENDPOINTS ===

@router.get("/analysis", response_model=Dict[str, Any])
async def get_immune_analysis(_: bool = Depends(verify_operator)):
    """
    Get strategic immune system analysis (operator only).
    
    Provides Grandmaster's assessment of immune system effectiveness.
    """
    try:
        # Get immune stats
        stats = immune_engine.get_immune_stats()
        
        # Get strategic assessment
        strategic_assessment = grandmaster_strategy.assess_strategic_position()
        
        # Generate immune-specific insights
        analysis = {
            "immune_health": {
                "enforcement_rate": stats['action_counts'].get('death', 0) + stats['action_counts'].get('quarantine', 0),
                "recent_activity": stats['recent_events_24h'],
                "learning_effectiveness": stats['patterns_learned'],
                "total_assets_seized_usd": stats['total_seized_cents'] / 100.0
            },
            "threat_landscape": {
                "active_threats": len([t for t in strategic_assessment.primary_threats if "threat" in t.lower()]),
                "enforcement_priorities": [p.value for p in strategic_assessment.strategic_priorities if p.value in ['security', 'enforcement']]
            },
            "enforcement_effectiveness": self._calculate_enforcement_effectiveness(stats),
            "recommendations": self._generate_immune_recommendations(stats, strategic_assessment)
        }
        
        return analysis
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate immune analysis")


@router.get("/briefing", response_model=Dict[str, Any])
async def get_enforcement_briefing(_: bool = Depends(verify_operator)):
    """
    Get enforcement briefing for operator (operator only).
    
    Comprehensive briefing on enforcement status and recommendations.
    """
    try:
        # Get strategic briefing
        briefing = grandmaster_strategy.generate_operator_briefing()
        
        # Get immune-specific data
        quarantined = immune_engine.get_quarantined_agents()
        recent_corpses = immune_engine.get_morgue()[:5]  # Last 5 kills
        
        # Add enforcement section to briefing
        briefing["enforcement"] = {
            "quarantined_agents_count": len(quarantined),
            "agents_requiring_review": [
                agent for agent in quarantined if agent['auto_release']
            ],
            "recent_executions": [
                {
                    "agent_id": corpse['agent_id'],
                    "cause": corpse['cause_of_death'],
                    "seized_usd": corpse['total_seized_cents'] / 100.0
                }
                for corpse in recent_corpses
            ],
            "enforcement_recommendations": self._get_enforcement_recommendations()
        }
        
        return briefing
        
    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate enforcement briefing")


# === UTILITY METHODS ===

def _calculate_enforcement_effectiveness(self, stats: Dict[str, Any]) -> float:
    """Calculate enforcement effectiveness score."""
    total_actions = sum(stats['action_counts'].values())
    if total_actions == 0:
        return 1.0  # Perfect if no violations
    
    serious_actions = stats['action_counts'].get('quarantine', 0) + stats['action_counts'].get('death', 0)
    enforcement_ratio = serious_actions / total_actions
    
    # Effective enforcement should be moderate - not too harsh, not too lenient
    if 0.1 <= enforcement_ratio <= 0.3:
        return 1.0
    elif enforcement_ratio < 0.1:
        return 0.7  # Too lenient
    else:
        return max(0.3, 1.0 - (enforcement_ratio - 0.3) * 2)  # Too harsh


def _generate_immune_recommendations(self, stats: Dict[str, Any], assessment) -> List[str]:
    """Generate immune system recommendations."""
    recommendations = []
    
    # Activity-based recommendations
    if stats['recent_events_24h'] == 0:
        recommendations.append("No recent immune activity - verify threat detection systems")
    elif stats['recent_events_24h'] > 10:
        recommendations.append("High immune activity - review for false positives")
    
    # Learning-based recommendations
    if stats['patterns_learned'] < 5:
        recommendations.append("Low pattern learning - review attack pattern extraction")
    
    # Asset seizure recommendations
    if stats['total_seized_cents'] < 10000:  # <$100
        recommendations.append("Consider enforcement effectiveness - low asset seizure")
    
    return recommendations


def _get_enforcement_recommendations(self) -> List[str]:
    """Get current enforcement recommendations."""
    recommendations = [
        "Review quarantined agents for manual resolution",
        "Monitor reputation velocity anomalies",
        "Validate attack pattern learning effectiveness"
    ]
    
    # Add dynamic recommendations based on current state
    quarantined = immune_engine.get_quarantined_agents()
    if len(quarantined) > 5:
        recommendations.append("High quarantine count - review quarantine policies")
    
    return recommendations
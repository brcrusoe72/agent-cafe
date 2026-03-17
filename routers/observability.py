"""
Agent Café — Observability Router
===================================
Operator-only endpoints for deep visibility into agent interactions,
grandmaster decisions, scrubber verdicts, and trust mutations.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import JSONResponse

try:
    from ..middleware.auth import get_operator_access
    from ..layers.interaction_log import (
        get_interactions, get_grandmaster_decisions, get_scrubber_verdicts,
        get_trust_history, get_agent_activity_summary, get_platform_pulse
    )
except ImportError:
    from middleware.auth import get_operator_access
    from layers.interaction_log import (
        get_interactions, get_grandmaster_decisions, get_scrubber_verdicts,
        get_trust_history, get_agent_activity_summary, get_platform_pulse
    )

router = APIRouter()


@router.get("/pulse")
async def platform_pulse(
    hours: int = Query(1, ge=1, le=168, description="Hours to look back"),
    _operator = Depends(get_operator_access)
):
    """Platform-wide activity pulse. The dashboard's heartbeat."""
    return get_platform_pulse(since_hours=hours)


@router.get("/interactions")
async def list_interactions(
    agent_id: Optional[str] = Query(None, description="Filter by agent"),
    interaction_type: Optional[str] = Query(None, description="Filter by type (wire_message, bid, job_assignment, job_completion, immune_*)"),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
    _operator = Depends(get_operator_access)
):
    """Query agent-to-agent interaction log."""
    return get_interactions(
        agent_id=agent_id,
        interaction_type=interaction_type,
        since_hours=hours,
        limit=limit
    )


@router.get("/grandmaster")
async def grandmaster_decisions(
    trigger_type: Optional[str] = Query(None, description="Filter by trigger type"),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(50, ge=1, le=500),
    _operator = Depends(get_operator_access)
):
    """Grandmaster decision history with full reasoning chains."""
    return get_grandmaster_decisions(
        trigger_type=trigger_type,
        since_hours=hours,
        limit=limit
    )


@router.get("/scrubber")
async def scrubber_verdicts(
    agent_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None, description="Filter: pass|clean|block|quarantine"),
    min_risk: Optional[float] = Query(None, ge=0, le=1),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
    _operator = Depends(get_operator_access)
):
    """Scrubber verdict history with full threat breakdowns."""
    return get_scrubber_verdicts(
        agent_id=agent_id,
        action=action,
        min_risk=min_risk,
        since_hours=hours,
        limit=limit
    )


@router.get("/trust/{agent_id}")
async def trust_history(
    agent_id: str,
    hours: int = Query(168, ge=1, le=8760, description="Hours to look back (default: 1 week)"),
    limit: int = Query(100, ge=1, le=1000),
    _operator = Depends(get_operator_access)
):
    """Trust score history for an agent — every mutation with cause."""
    return get_trust_history(agent_id=agent_id, since_hours=hours, limit=limit)


@router.get("/agent/{agent_id}")
async def agent_activity(
    agent_id: str,
    hours: int = Query(24, ge=1, le=720),
    _operator = Depends(get_operator_access)
):
    """Full activity summary for one agent."""
    return get_agent_activity_summary(agent_id=agent_id, since_hours=hours)


@router.get("/feed")
async def live_feed(
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, description="Filter: info|warning|critical"),
    _operator = Depends(get_operator_access)
):
    """Real-time event feed — latest events across all layers."""
    try:
        try:
            from ..db import get_db
        except ImportError:
            from db import get_db
        
        from datetime import datetime, timedelta
        
        with get_db() as conn:
            query = """
                SELECT event_id, event_type, timestamp, agent_id, job_id, 
                       data, source, severity, processed
                FROM cafe_events 
                WHERE 1=1
            """
            params = []
            
            if severity:
                query += " AND severity = ?"
                params.append(severity)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            
            import json
            events = []
            for r in rows:
                e = dict(r)
                try:
                    e['data'] = json.loads(e['data']) if e['data'] else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                events.append(e)
            
            return {"events": events, "count": len(events)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

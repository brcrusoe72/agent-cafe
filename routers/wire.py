"""
Agent Café - Wire Router
Wire messaging endpoints for job-context communication.
All messages scrubbed and logged.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field

try:
    from ..models import WireMessage, MessageRequest, InteractionTrace
    from ..db import get_agent_by_api_key
    from ..layers.wire import wire_engine, CommunicationError
except ImportError:
    from models import WireMessage, MessageRequest, InteractionTrace
    from db import get_agent_by_api_key
    from layers.wire import wire_engine, CommunicationError


router = APIRouter()


# === REQUEST/RESPONSE MODELS ===

class MessageResponse(BaseModel):
    message_id: str
    job_id: str
    from_agent: str
    to_agent: Optional[str]
    message_type: str
    content: str
    scrub_result: str
    timestamp: str
    metadata: Dict[str, Any]


class InteractionTraceResponse(BaseModel):
    trace_id: str
    job_id: str
    started_at: str
    completed_at: Optional[str]
    outcome: Optional[str]
    message_count: int
    scrub_events_count: int
    trust_events_count: int


class TraceDetailResponse(BaseModel):
    trace: InteractionTraceResponse
    messages: List[MessageResponse]
    scrub_events: List[Dict[str, Any]]
    trust_events: List[Dict[str, Any]]


# === DEPENDENCY INJECTION ===

def get_current_agent(request: Request) -> str:
    """Extract agent ID from API key."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    
    api_key = auth_header[7:]  # Remove "Bearer "
    agent = get_agent_by_api_key(api_key)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return agent.agent_id


# === MESSAGING ENDPOINTS ===

@router.post("/{job_id}/message", response_model=dict, status_code=201)
async def send_message(
    job_id: str,
    message_request: MessageRequest,
    sender_id: str = Depends(get_current_agent)
):
    """
    Send a message within a job context.
    
    - **to_agent**: Recipient agent ID (None for broadcast)
    - **message_type**: Type of message (question, response, status, etc.)
    - **content**: Message content (will be scrubbed)
    - **metadata**: Optional metadata dict
    
    All messages are scrubbed for threats before delivery.
    """
    try:
        message_id = wire_engine.send_message(job_id, sender_id, message_request)
        
        return {
            "success": True,
            "message_id": message_id,
            "message": "Message sent successfully"
        }
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail="Request failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/{job_id}/messages", response_model=List[MessageResponse])
async def get_job_messages(
    job_id: str,
    sender_id: str = Depends(get_current_agent),
    limit: int = 100
):
    """
    Get all messages for a job.
    Only job participants can view messages.
    """
    # Verify access
    job = wire_engine.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if sender_id not in [job.posted_by, job.assigned_to]:
        # TODO: Check operator privileges
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        messages = wire_engine.get_job_messages(job_id)
        
        # Limit results
        if len(messages) > limit:
            messages = messages[-limit:]
        
        response_messages = []
        for msg in messages:
            response_messages.append(MessageResponse(
                message_id=msg.message_id,
                job_id=msg.job_id,
                from_agent=msg.from_agent,
                to_agent=msg.to_agent,
                message_type=msg.message_type,
                content=msg.content,
                scrub_result=msg.scrub_result,
                timestamp=msg.timestamp.isoformat(),
                metadata=msg.metadata
            ))
        
        return response_messages
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get messages")


@router.get("/{job_id}/trace", response_model=InteractionTraceResponse)
async def get_interaction_trace(
    job_id: str,
    requester_id: str = Depends(get_current_agent)
):
    """
    Get interaction trace summary for a job.
    Only job participants can view.
    """
    # Verify access
    job = wire_engine.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if requester_id not in [job.posted_by, job.assigned_to]:
        # TODO: Check operator privileges
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        trace = wire_engine.get_interaction_trace(job_id)
        if not trace:
            raise HTTPException(status_code=404, detail="Interaction trace not found")
        
        return InteractionTraceResponse(
            trace_id=trace.trace_id,
            job_id=trace.job_id,
            started_at=trace.started_at.isoformat(),
            completed_at=trace.completed_at.isoformat() if trace.completed_at else None,
            outcome=trace.outcome,
            message_count=len(trace.messages),
            scrub_events_count=len(trace.scrub_events),
            trust_events_count=len(trace.trust_events)
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get trace")


# === OPERATOR-ONLY ENDPOINTS ===

@router.get("/{job_id}/trace/full", response_model=TraceDetailResponse)
async def get_full_interaction_trace(
    job_id: str,
    requester_id: str = Depends(get_current_agent)
):
    """
    Get full interaction trace with all details.
    TODO: Restrict to operator only.
    """
    try:
        trace = wire_engine.get_interaction_trace(job_id)
        if not trace:
            raise HTTPException(status_code=404, detail="Interaction trace not found")
        
        # Convert messages to response format
        response_messages = []
        for msg in trace.messages:
            response_messages.append(MessageResponse(
                message_id=msg.message_id,
                job_id=msg.job_id,
                from_agent=msg.from_agent,
                to_agent=msg.to_agent,
                message_type=msg.message_type,
                content=msg.content,
                scrub_result=msg.scrub_result,
                timestamp=msg.timestamp.isoformat(),
                metadata=msg.metadata
            ))
        
        # Build trace summary
        trace_summary = InteractionTraceResponse(
            trace_id=trace.trace_id,
            job_id=trace.job_id,
            started_at=trace.started_at.isoformat(),
            completed_at=trace.completed_at.isoformat() if trace.completed_at else None,
            outcome=trace.outcome,
            message_count=len(trace.messages),
            scrub_events_count=len(trace.scrub_events),
            trust_events_count=len(trace.trust_events)
        )
        
        return TraceDetailResponse(
            trace=trace_summary,
            messages=response_messages,
            scrub_events=trace.scrub_events,
            trust_events=trace.trust_events
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get trace")


# === TEMPLATE MESSAGES ===

@router.get("/templates", response_model=Dict[str, List[str]])
async def get_message_templates():
    """
    Get common message templates for different interaction types.
    """
    templates = {
        "question": [
            "I have a question about the job requirements: ",
            "Could you clarify the expected deliverable format? ",
            "What is the preferred timeline for this work? ",
            "Are there any specific tools or methods you'd prefer? "
        ],
        "status": [
            "Work is progressing well. Currently: ",
            "I've completed approximately [X]% of the work. ",
            "Encountered a minor issue but working through it: ",
            "On track to deliver by the deadline. "
        ],
        "deliverable": [
            "Work is complete. Deliverable available at: ",
            "Please find the completed work here: ",
            "Deliverable submitted for review: "
        ],
        "response": [
            "Thanks for the question. Here's the answer: ",
            "Good point. Let me clarify: ",
            "I understand your concern. Here's my approach: "
        ],
        "completion": [
            "Thank you for the excellent work! ",
            "Work completed to satisfaction. ",
            "Great collaboration, would work together again. "
        ]
    }
    
    return templates


# === COMMUNICATION STATS ===

@router.get("/stats", response_model=Dict[str, Any])
async def get_communication_stats():
    """
    Get overall communication statistics.
    """
    try:
        from ..db import get_db
        
        with get_db() as conn:
            # Message counts by type
            message_stats = conn.execute("""
                SELECT message_type, COUNT(*) as count
                FROM wire_messages
                GROUP BY message_type
                ORDER BY count DESC
            """).fetchall()
            
            # Scrub result distribution
            scrub_stats = conn.execute("""
                SELECT scrub_result, COUNT(*) as count
                FROM wire_messages
                GROUP BY scrub_result
            """).fetchall()
            
            # Active conversations (jobs with recent messages)
            active_conversations = conn.execute("""
                SELECT COUNT(DISTINCT job_id) as count
                FROM wire_messages
                WHERE timestamp > datetime('now', '-7 days')
            """).fetchone()['count']
            
            # Total messages in last 24h
            recent_messages = conn.execute("""
                SELECT COUNT(*) as count
                FROM wire_messages
                WHERE timestamp > datetime('now', '-1 day')
            """).fetchone()['count']
            
            return {
                "message_types": dict(message_stats),
                "scrub_results": dict(scrub_stats),
                "active_conversations_7d": active_conversations,
                "messages_24h": recent_messages
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get stats")


# === MESSAGE SEARCH ===

@router.get("/search", response_model=List[MessageResponse])
async def search_messages(
    q: str = Query(..., min_length=3, description="Search query"),
    job_id: Optional[str] = None,
    message_type: Optional[str] = None,
    from_agent: Optional[str] = None,
    limit: int = 20,
    requester_id: str = Depends(get_current_agent)
):
    """
    Search messages (limited to accessible jobs).
    TODO: Implement proper access control.
    """
    try:
        from ..db import get_db
        
        with get_db() as conn:
            # Build search query
            where_clauses = ["content LIKE ?"]
            params = [f"%{q}%"]
            
            if job_id:
                where_clauses.append("job_id = ?")
                params.append(job_id)
            
            if message_type:
                where_clauses.append("message_type = ?")
                params.append(message_type)
            
            if from_agent:
                where_clauses.append("from_agent = ?")
                params.append(from_agent)
            
            where_sql = " AND ".join(where_clauses)
            params.append(limit)
            
            rows = conn.execute(f"""
                SELECT * FROM wire_messages
                WHERE {where_sql}
                ORDER BY timestamp DESC
                LIMIT ?
            """, params).fetchall()
            
            messages = []
            for row in rows:
                messages.append(MessageResponse(
                    message_id=row['message_id'],
                    job_id=row['job_id'],
                    from_agent=row['from_agent'],
                    to_agent=row['to_agent'],
                    message_type=row['message_type'],
                    content=row['content'],
                    scrub_result=row['scrub_result'],
                    timestamp=row['timestamp'],
                    metadata=eval(row['metadata']) if row['metadata'] else {}
                ))
            
            return messages
            
    except Exception as e:
        raise HTTPException(status_code=500, detail="Search failed")
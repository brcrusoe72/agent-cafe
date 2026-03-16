"""
Agent Café - Jobs Router
Job lifecycle endpoints: post → bid → assign → deliver → accept/dispute
"""

import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

try:
    from ..models import (
        Job, JobStatus, Bid, JobCreateRequest, BidCreateRequest,
        AgentStatus
    )
    from ..db import get_db, get_agent_by_api_key
    from ..layers.wire import wire_engine, CommunicationError
except ImportError:
    from models import (
        Job, JobStatus, Bid, JobCreateRequest, BidCreateRequest,
        AgentStatus
    )
    from db import get_db, get_agent_by_api_key
    from layers.wire import wire_engine, CommunicationError


router = APIRouter()
security = HTTPBearer()


# === REQUEST/RESPONSE MODELS ===

class JobResponse(BaseModel):
    job_id: str
    title: str
    description: str
    required_capabilities: List[str]
    budget_cents: int
    posted_by: str
    status: str
    assigned_to: Optional[str]
    deliverable_url: Optional[str]
    posted_at: str
    expires_at: Optional[str]
    completed_at: Optional[str]
    bid_count: int
    avg_bid_cents: Optional[int]


class BidResponse(BaseModel):
    bid_id: str
    job_id: str
    agent_id: str
    agent_name: str
    price_cents: int
    pitch: str
    submitted_at: str
    status: str
    agent_trust_score: float
    agent_jobs_completed: int


class JobAssignRequest(BaseModel):
    bid_id: str = Field(..., description="Winning bid ID")


class JobDeliverableRequest(BaseModel):
    deliverable_url: str = Field(..., description="URL to deliverable (file, repo, etc)")
    notes: str = Field(default="", description="Optional delivery notes")


class JobAcceptRequest(BaseModel):
    rating: float = Field(..., ge=1.0, le=5.0, description="Rating 1-5")
    feedback: str = Field(default="", description="Optional feedback")


class JobDisputeRequest(BaseModel):
    reason: str = Field(..., min_length=10, description="Dispute reason")


# === DEPENDENCY INJECTION ===

def get_current_agent(request: Request) -> str:
    """Extract agent ID from request state (set by auth middleware)."""
    agent_id = getattr(request.state, 'agent_id', None)
    if agent_id:
        return agent_id
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    agent = get_agent_by_api_key(auth_header[7:])
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return agent.agent_id


def get_current_agent_or_human(request: Request) -> str:
    """Extract agent ID or allow human poster."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        # For now, allow unauthenticated job posting (human)
        # TODO: Add proper human authentication
        return f"human:{request.client.host}"
    
    api_key = auth_header[7:]
    agent = get_agent_by_api_key(api_key)
    if agent:
        return agent.agent_id
    else:
        # Assume human if not valid agent key
        return f"human:{api_key[:8]}"


# === JOB ENDPOINTS ===

@router.post("", response_model=dict, status_code=201)
async def create_job(
    job_request: JobCreateRequest,
    poster_id: str = Depends(get_current_agent_or_human)
):
    """
    Create a new job posting.
    
    - **title**: Brief job description
    - **description**: Full job requirements
    - **required_capabilities**: List of verified capability tags required
    - **budget_cents**: Maximum budget in cents (USD)
    - **expires_hours**: Hours until job expires (default: 72)
    """
    try:
        job_id = wire_engine.create_job(job_request, poster_id)
        
        # Create payment intent so capture works when job completes
        payment_info = None
        try:
            from layers.treasury import treasury_engine
            result = treasury_engine.create_job_payment(
                job_id=job_id,
                amount_cents=job_request.budget_cents,
                poster_email=None  # Could resolve from agent profile
            )
            payment_info = {
                "payment_id": result["payment_id"],
                "status": "pending",
            }
        except Exception as e:
            # Payment creation is non-blocking — job still posts
            # Operator can manually create payment via /treasury/payments/checkout
            print(f"⚠️  Payment intent creation failed for {job_id}: {e}")
        
        # Emit event
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.JOB_POSTED,
                agent_id=poster_id,
                job_id=job_id,
                data={
                    "title": job_request.title,
                    "budget_cents": job_request.budget_cents,
                    "capabilities": job_request.required_capabilities
                },
                source="jobs_router"
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "job_id": job_id,
            "message": "Job created successfully",
            "expires_hours": job_request.expires_hours or 72,
            "payment": payment_info,
        }
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = None,
    capability: Optional[str] = None,
    min_budget_cents: Optional[int] = None,
    max_budget_cents: Optional[int] = None,
    posted_by: Optional[str] = None,
    limit: int = 50
):
    """
    List jobs with optional filtering.
    
    - **status**: Filter by job status (open, assigned, completed, etc.)
    - **capability**: Filter by required capability
    - **min_budget_cents**: Minimum budget filter
    - **max_budget_cents**: Maximum budget filter
    - **posted_by**: Filter by poster ID
    - **limit**: Maximum results (default: 50)
    """
    try:
        with get_db() as conn:
            # Build query
            where_clauses = []
            params = []
            
            if status:
                where_clauses.append("j.status = ?")
                params.append(status)
            
            if capability:
                where_clauses.append("j.required_capabilities LIKE ?")
                params.append(f"%{capability}%")
            
            if min_budget_cents:
                where_clauses.append("j.budget_cents >= ?")
                params.append(min_budget_cents)
            
            if max_budget_cents:
                where_clauses.append("j.budget_cents <= ?")
                params.append(max_budget_cents)
            
            if posted_by:
                where_clauses.append("j.posted_by = ?")
                params.append(posted_by)
            
            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)
            
            # Execute query with bid stats
            query = f"""
                SELECT j.*,
                       COUNT(b.bid_id) as bid_count,
                       AVG(b.price_cents) as avg_bid_cents
                FROM jobs j
                LEFT JOIN bids b ON j.job_id = b.job_id
                {where_sql}
                GROUP BY j.job_id
                ORDER BY j.posted_at DESC
                LIMIT ?
            """
            
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            
            jobs = []
            for row in rows:
                jobs.append(JobResponse(
                    job_id=row['job_id'],
                    title=row['title'],
                    description=row['description'],
                    required_capabilities=json.loads(row['required_capabilities']),
                    budget_cents=row['budget_cents'],
                    posted_by=row['posted_by'],
                    status=row['status'],
                    assigned_to=row['assigned_to'],
                    deliverable_url=row['deliverable_url'],
                    posted_at=row['posted_at'],
                    expires_at=row['expires_at'],
                    completed_at=row['completed_at'],
                    bid_count=row['bid_count'],
                    avg_bid_cents=int(row['avg_bid_cents']) if row['avg_bid_cents'] else None
                ))
            
            return jobs
    
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list jobs")


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """
    Get job details including bid count and averages.
    """
    job = wire_engine.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get bid stats
    bids = wire_engine.get_job_bids(job_id)
    bid_count = len(bids)
    avg_bid_cents = None
    if bids:
        avg_bid_cents = sum(b.price_cents for b in bids) // len(bids)
    
    return JobResponse(
        job_id=job.job_id,
        title=job.title,
        description=job.description,
        required_capabilities=job.required_capabilities,
        budget_cents=job.budget_cents,
        posted_by=job.posted_by,
        status=job.status.value,
        assigned_to=job.assigned_to,
        deliverable_url=job.deliverable_url,
        posted_at=job.posted_at.isoformat(),
        expires_at=job.expires_at.isoformat() if job.expires_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        bid_count=bid_count,
        avg_bid_cents=avg_bid_cents
    )


@router.get("/{job_id}/bids", response_model=List[BidResponse])
async def get_job_bids(job_id: str):
    """
    Get all bids for a job with agent information.
    """
    job = wire_engine.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    bids = wire_engine.get_job_bids(job_id)
    
    # Enrich with agent data
    bid_responses = []
    with get_db() as conn:
        for bid in bids:
            agent_row = conn.execute("""
                SELECT name, trust_score, jobs_completed FROM agents
                WHERE agent_id = ?
            """, (bid.agent_id,)).fetchone()
            
            if agent_row:
                bid_responses.append(BidResponse(
                    bid_id=bid.bid_id,
                    job_id=bid.job_id,
                    agent_id=bid.agent_id,
                    agent_name=agent_row['name'],
                    price_cents=bid.price_cents,
                    pitch=bid.pitch,
                    submitted_at=bid.submitted_at.isoformat(),
                    status=bid.status,
                    agent_trust_score=agent_row['trust_score'],
                    agent_jobs_completed=agent_row['jobs_completed']
                ))
    
    # Sort by trust score descending, then by price ascending
    bid_responses.sort(key=lambda x: (-x.agent_trust_score, x.price_cents))
    
    return bid_responses


# === BID ENDPOINTS ===

@router.post("/{job_id}/bids", response_model=dict, status_code=201)
async def submit_bid(
    job_id: str,
    bid_request: BidCreateRequest,
    agent_id: str = Depends(get_current_agent)
):
    """
    Submit a bid for a job.
    
    - **price_cents**: Bid amount in cents
    - **pitch**: Why you're the best agent for this job (will be scrubbed)
    
    """
    try:
        bid_id = wire_engine.submit_bid(job_id, agent_id, bid_request)
        
        return {
            "success": True,
            "bid_id": bid_id,
            "message": "Bid submitted successfully"
        }
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/{job_id}/assign", response_model=dict)
async def assign_job(
    job_id: str,
    assign_request: JobAssignRequest,
    assigner_id: str = Depends(get_current_agent_or_human)
):
    """
    Assign job to a winning bidder.
    Only job poster can assign.
    """
    try:
        success = wire_engine.assign_job(job_id, assign_request.bid_id, assigner_id)
        
        if success:
            return {
                "success": True,
                "message": "Job assigned successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Assignment failed")
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/{job_id}/deliver", response_model=dict)
async def submit_deliverable(
    job_id: str,
    deliverable_request: JobDeliverableRequest,
    agent_id: str = Depends(get_current_agent)
):
    """
    Submit deliverable for assigned job.
    Only assigned agent can submit.
    """
    try:
        success = wire_engine.submit_deliverable(
            job_id, agent_id, deliverable_request.deliverable_url, deliverable_request.notes
        )
        
        if success:
            return {
                "success": True,
                "message": "Deliverable submitted successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Submission failed")
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/{job_id}/accept", response_model=dict)
async def accept_deliverable(
    job_id: str,
    accept_request: JobAcceptRequest,
    accepter_id: str = Depends(get_current_agent_or_human)
):
    """
    Accept deliverable and complete job.
    Only job poster can accept.
    
    - **rating**: 1-5 rating for the work
    - **feedback**: Optional feedback
    """
    try:
        success = wire_engine.accept_deliverable(
            job_id, accepter_id, accept_request.rating, accept_request.feedback
        )
        
        if success:
            return {
                "success": True,
                "message": "Deliverable accepted, job completed"
            }
        else:
            raise HTTPException(status_code=500, detail="Acceptance failed")
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/{job_id}/dispute", response_model=dict)
async def dispute_job(
    job_id: str,
    dispute_request: JobDisputeRequest,
    disputer_id: str = Depends(get_current_agent_or_human)
):
    """
    Dispute a job outcome.
    Only job participants can dispute.
    """
    try:
        success = wire_engine.dispute_job(job_id, disputer_id, dispute_request.reason)
        
        if success:
            return {
                "success": True,
                "message": "Job disputed, under review"
            }
        else:
            raise HTTPException(status_code=500, detail="Dispute failed")
        
    except CommunicationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")


# === MAINTENANCE ENDPOINTS ===

@router.post("/maintenance/expire", response_model=dict)
async def expire_jobs(request: Request):
    """
    Expire old jobs (operator-only endpoint).
    """
    if not getattr(request.state, 'is_operator', False):
        raise HTTPException(status_code=403, detail="Operator access required")
    try:
        expired_count = wire_engine.expire_old_jobs()
        
        return {
            "success": True,
            "expired_count": expired_count,
            "message": f"Expired {expired_count} jobs"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to expire jobs")
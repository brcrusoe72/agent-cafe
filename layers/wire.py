"""
Agent Café - Communication Layer 📡 (The Wire)
Where actual work happens. Every interaction is logged, traced, and attributed.
No anonymous messages. No off-the-record conversations.
"""

import json
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import asdict

from cafe_logging import get_logger
logger = get_logger(__name__)

try:
    from ..models import (
        Job, JobStatus, Bid, WireMessage, InteractionTrace,
        JobCreateRequest, BidCreateRequest, MessageRequest,
        TrustEvent, AgentStatus
    )
    from ..db import get_db, DatabaseError, get_agent_by_id
    from .scrubber import ScrubberEngine
except ImportError:
    from models import (
        Job, JobStatus, Bid, WireMessage, InteractionTrace,
        JobCreateRequest, BidCreateRequest, MessageRequest,
        TrustEvent, AgentStatus
    )
    from db import get_db, DatabaseError, get_agent_by_id
    from layers.scrubber import ScrubberEngine


def _emit_event(event_type, agent_id="", data=None, source="wire"):
    """Emit event to bus. Non-blocking — never fails the caller."""
    try:
        from agents.event_bus import event_bus, EventType
        event_bus.emit_simple(
            getattr(EventType, event_type),
            agent_id=agent_id,
            data=data or {},
            source=source,
            severity="info"
        )
    except Exception as e:
        logger.warning("Event emission failed for %s: %s", event_type, e)


class CommunicationError(Exception):
    """Communication layer specific errors."""
    pass


class WireEngine:
    """Core communication engine managing job lifecycle and messaging."""
    
    def __init__(self):
        self.scrubber = ScrubberEngine()
    
    def create_job(self, job_request: JobCreateRequest, posted_by: str) -> str:
        """Create a new job with interaction trace."""
        if job_request.budget_cents is not None and job_request.budget_cents < 100:
            raise CommunicationError("Budget must be at least $1.00 (100 cents)")
        
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        trace_id = f"trace_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            try:
                expires_at = None
                if job_request.expires_hours:
                    expires_at = datetime.now() + timedelta(hours=job_request.expires_hours)
                
                # Create job
                conn.execute("""
                    INSERT INTO jobs (
                        job_id, title, description, required_capabilities, budget_cents,
                        posted_by, status, posted_at, expires_at, interaction_trace_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_id, job_request.title, job_request.description,
                    json.dumps(job_request.required_capabilities), job_request.budget_cents,
                    posted_by, JobStatus.OPEN, datetime.now(), expires_at, trace_id
                ))
                
                # Create interaction trace
                conn.execute("""
                    INSERT INTO interaction_traces (trace_id, job_id, started_at)
                    VALUES (?, ?, ?)
                """, (trace_id, job_id, datetime.now()))
                
                conn.commit()
                
                # Create initial trace event
                self._add_trace_event(trace_id, "job_created", {
                    "posted_by": posted_by,
                    "title": job_request.title,
                    "budget_cents": job_request.budget_cents,
                    "expires_hours": job_request.expires_hours
                }, conn=conn)
                
                return job_id
                
            except Exception as e:
                raise CommunicationError(f"Failed to create job: {e}")
    
    def submit_bid(self, job_id: str, agent_id: str, bid_request: BidCreateRequest) -> str:
        """Submit a bid for a job with scrubbing."""
        # Verify job exists and is open
        job = self.get_job(job_id)
        if not job:
            raise CommunicationError("Job not found")
        if job.status != JobStatus.OPEN:
            raise CommunicationError(f"Job is {job.status}, not open for bids")
        
        # Verify agent exists and can bid
        agent = get_agent_by_id(agent_id)
        if not agent:
            raise CommunicationError("Agent not found")
        if agent.status not in [AgentStatus.ACTIVE, AgentStatus.PROBATION]:
            raise CommunicationError(f"Agent status {agent.status} cannot bid")
        
        # Check stake requirement ($10 minimum) — check wallet balance
        # Skip if wallet doesn't exist or treasury not configured (dev/bootstrap mode)
        try:
            from layers.treasury import treasury_engine
            wallet = treasury_engine.get_wallet(agent_id)
            if wallet:
                stake = wallet.available_cents + wallet.pending_cents + wallet.total_earned_cents
                if stake < 1000 and wallet.total_earned_cents == 0 and agent.jobs_completed == 0:
                    # New agent with no history — allow bidding (bootstrap mode)
                    pass
                elif stake < 1000:
                    raise CommunicationError("Insufficient stake to bid (minimum $10.00)")
        except CommunicationError:
            raise
        except Exception as e:
            logger.debug("Treasury not available — allowing bidding", exc_info=True)
        
        # Scrub the pitch message
        scrub_result = self.scrubber.scrub_message(
            bid_request.pitch, 
            message_type="bid",
            job_context={"job_id": job_id, "agent_id": agent_id}
        )
        
        if scrub_result.action in ["block", "quarantine"]:
            # This will trigger immune response
            self._handle_scrub_violation(agent_id, scrub_result, job_id)
            raise CommunicationError(f"Bid rejected: {scrub_result.action}")
        
        # Use scrubbed version if cleaned
        final_pitch = scrub_result.scrubbed_message or bid_request.pitch
        
        bid_id = f"bid_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            try:
                # Check for existing bid
                existing = conn.execute("""
                    SELECT bid_id FROM bids WHERE job_id = ? AND agent_id = ?
                """, (job_id, agent_id)).fetchone()
                
                if existing:
                    raise CommunicationError("Agent already has a bid on this job")
                
                # Create bid
                conn.execute("""
                    INSERT INTO bids (
                        bid_id, job_id, agent_id, price_cents, pitch, 
                        submitted_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    bid_id, job_id, agent_id, bid_request.price_cents,
                    final_pitch, datetime.now(), "pending"
                ))
                
                conn.commit()
                
                # Add to interaction trace
                self._add_trace_event(job.interaction_trace_id, "bid_submitted", {
                    "bid_id": bid_id,
                    "agent_id": agent_id,
                    "price_cents": bid_request.price_cents,
                    "scrub_result": scrub_result.action
                }, conn=conn)
                
            except Exception as e:
                raise CommunicationError(f"Failed to submit bid: {e}")
        
        _emit_event("JOB_BID", agent_id=agent_id, data={
            "job_id": job_id, "bid_id": bid_id, "price_cents": bid_request.price_cents
        })
        
        # Deep interaction log
        try:
            from layers.interaction_log import log_interaction
            log_interaction(
                interaction_type="bid",
                from_agent=agent_id,
                to_agent=job.posted_by,
                job_id=job_id,
                channel="marketplace",
                payload_summary=f"Bid ${bid_request.price_cents/100:.2f}: {final_pitch[:100]}",
                scrubber_action=scrub_result.action,
                scrubber_risk=scrub_result.risk_score,
                result="submitted",
                metadata={"bid_id": bid_id, "price_cents": bid_request.price_cents}
            )
        except Exception as e:
            logger.debug("Failed to log bid interaction", exc_info=True)
        
        return bid_id
    
    def assign_job(self, job_id: str, bid_id: str, assigned_by: str) -> bool:
        """Assign job to winning bidder."""
        job = self.get_job(job_id)
        if not job:
            raise CommunicationError("Job not found")
        if job.status != JobStatus.OPEN:
            raise CommunicationError(f"Job is {job.status}, cannot assign")
        
        # Verify assignment authority (operator override handled at router layer)
        if job.posted_by != assigned_by:
            raise CommunicationError("Only job poster can assign")
        
        with get_db() as conn:
            try:
                # Get winning bid
                bid_row = conn.execute("""
                    SELECT * FROM bids WHERE bid_id = ? AND job_id = ?
                """, (bid_id, job_id)).fetchone()
                
                if not bid_row:
                    raise CommunicationError("Bid not found")
                
                winner_agent_id = bid_row['agent_id']
                
                # Update job status
                conn.execute("""
                    UPDATE jobs SET status = ?, assigned_to = ?, assigned_at = datetime('now') WHERE job_id = ?
                """, (JobStatus.ASSIGNED, winner_agent_id, job_id))
                
                # Update winning bid
                conn.execute("""
                    UPDATE bids SET status = 'accepted' WHERE bid_id = ?
                """, (bid_id,))
                
                # Reject all other bids
                conn.execute("""
                    UPDATE bids SET status = 'rejected' 
                    WHERE job_id = ? AND bid_id != ?
                """, (job_id, bid_id))
                
                conn.commit()
                
                # Add to interaction trace
                self._add_trace_event(job.interaction_trace_id, "job_assigned", {
                    "bid_id": bid_id,
                    "winner_agent_id": winner_agent_id,
                    "assigned_by": assigned_by,
                    "price_cents": bid_row['price_cents']
                }, conn=conn)
                
            except Exception as e:
                raise CommunicationError(f"Failed to assign job: {e}")
        
        _emit_event("JOB_ASSIGNED", agent_id=winner_agent_id, data={
            "job_id": job_id, "bid_id": bid_id, "price_cents": bid_row['price_cents']
        })
        
        try:
            from layers.interaction_log import log_interaction
            log_interaction(
                interaction_type="job_assignment",
                from_agent=assigned_by,
                to_agent=winner_agent_id,
                job_id=job_id,
                channel="marketplace",
                payload_summary=f"Assigned at ${bid_row['price_cents']/100:.2f}",
                result="assigned",
                metadata={"bid_id": bid_id, "price_cents": bid_row['price_cents']}
            )
        except Exception as e:
            logger.debug("Failed to log job assignment interaction", exc_info=True)
        
        return True
    
    def send_message(self, job_id: str, from_agent: str, message_request: MessageRequest) -> str:
        """Send a message within job context with full scrubbing."""
        # Verify job exists and agent is involved
        job = self.get_job(job_id)
        if not job:
            raise CommunicationError("Job not found")
        
        # Verify agent is part of this job
        if from_agent not in [job.posted_by, job.assigned_to]:
            raise CommunicationError("Agent not authorized for this job")
        
        # Scrub the message
        scrub_result = self.scrubber.scrub_message(
            message_request.content,
            message_type=message_request.message_type or "wire_message",
            job_context={
                "job_id": job_id,
                "from_agent": from_agent,
                "to_agent": message_request.to_agent,
                "metadata": message_request.metadata or {}
            }
        )
        
        if scrub_result.action in ["block", "quarantine"]:
            self._handle_scrub_violation(from_agent, scrub_result, job_id)
            _emit_event("WIRE_MESSAGE_BLOCKED", agent_id=from_agent, data={
                "job_id": job_id, "action": scrub_result.action
            })
            raise CommunicationError(f"Message blocked: {scrub_result.action}")
        
        # Use scrubbed content
        final_content = scrub_result.scrubbed_message or message_request.content
        content_hash = hashlib.sha256(final_content.encode()).hexdigest()
        
        # Create signature (simple for now - could be cryptographic)
        signature = hashlib.sha256(f"{from_agent}:{job_id}:{content_hash}:{datetime.now().isoformat()}".encode()).hexdigest()
        
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            try:
                # Store message
                conn.execute("""
                    INSERT INTO wire_messages (
                        message_id, job_id, from_agent, to_agent, message_type,
                        content, content_hash, signature, scrub_result, 
                        timestamp, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    message_id, job_id, from_agent, message_request.to_agent,
                    message_request.message_type, final_content, content_hash,
                    signature, scrub_result.action, datetime.now(),
                    json.dumps(message_request.metadata or {})
                ))
                
                conn.commit()
                
                # Add to interaction trace
                self._add_trace_event(job.interaction_trace_id, "message_sent", {
                    "message_id": message_id,
                    "from_agent": from_agent,
                    "to_agent": message_request.to_agent,
                    "message_type": message_request.message_type,
                    "scrub_result": scrub_result.action,
                    "threats_detected": len(scrub_result.threats_detected)
                }, conn=conn)
                
            except Exception as e:
                raise CommunicationError(f"Failed to send message: {e}")
        
        _emit_event("WIRE_MESSAGE", agent_id=from_agent, data={
            "job_id": job_id, "message_id": message_id,
            "to_agent": message_request.to_agent
        })
        
        # Deep interaction log
        try:
            from layers.interaction_log import log_interaction
            log_interaction(
                interaction_type="wire_message",
                from_agent=from_agent,
                to_agent=message_request.to_agent,
                job_id=job_id,
                channel="wire",
                payload_summary=final_content[:200],
                payload_size=len(final_content),
                scrubber_action=scrub_result.action,
                scrubber_risk=scrub_result.risk_score,
                result="delivered",
                metadata={"message_id": message_id, "message_type": message_request.message_type}
            )
        except Exception as e:
            logger.debug("Failed to log wire message interaction", exc_info=True)
        
        return message_id
    
    def submit_deliverable(self, job_id: str, agent_id: str, deliverable_url: str, notes: str = "") -> bool:
        """Submit deliverable for a job."""
        job = self.get_job(job_id)
        if not job:
            raise CommunicationError("Job not found")
        if job.assigned_to != agent_id:
            raise CommunicationError("Only assigned agent can submit deliverable")
        if job.status not in [JobStatus.ASSIGNED, JobStatus.IN_PROGRESS]:
            raise CommunicationError(f"Job status {job.status} cannot submit deliverable")
        
        with get_db() as conn:
            try:
                # Update job with deliverable
                conn.execute("""
                    UPDATE jobs SET status = ?, deliverable_url = ? WHERE job_id = ?
                """, (JobStatus.DELIVERED, deliverable_url, job_id))
                
                conn.commit()
                
                # Send deliverable notification message
                if notes:
                    self.send_message(job_id, agent_id, MessageRequest(
                        to_agent=job.posted_by,
                        message_type="deliverable",
                        content=f"Deliverable submitted: {deliverable_url}\n\nNotes: {notes}",
                        metadata={"deliverable_url": deliverable_url}
                    ))
                else:
                    self.send_message(job_id, agent_id, MessageRequest(
                        to_agent=job.posted_by,
                        message_type="deliverable",
                        content=f"Deliverable submitted: {deliverable_url}",
                        metadata={"deliverable_url": deliverable_url}
                    ))
                
                # Add to trace
                self._add_trace_event(job.interaction_trace_id, "deliverable_submitted", {
                    "agent_id": agent_id,
                    "deliverable_url": deliverable_url,
                    "notes": notes
                }, conn=conn)
                
            except Exception as e:
                raise CommunicationError(f"Failed to submit deliverable: {e}")
        
        _emit_event("JOB_DELIVERED", agent_id=agent_id, data={
            "job_id": job_id, "deliverable_url": deliverable_url
        })
        return True
    
    def accept_deliverable(self, job_id: str, accepted_by: str, rating: float, feedback: str = "") -> bool:
        """Accept deliverable and complete job."""
        job = self.get_job(job_id)
        if not job:
            raise CommunicationError("Job not found")
        if job.posted_by != accepted_by:
            raise CommunicationError("Only job poster can accept deliverable")
        if job.status != JobStatus.DELIVERED:
            raise CommunicationError(f"Job status {job.status} cannot be accepted")
        
        if not (1.0 <= rating <= 5.0):
            raise CommunicationError("Rating must be between 1.0 and 5.0")
        
        with get_db() as conn:
            try:
                # Complete the job
                completed_at = datetime.now()
                conn.execute("""
                    UPDATE jobs SET status = ?, completed_at = ? WHERE job_id = ?
                """, (JobStatus.COMPLETED, completed_at, job_id))
                
                # Complete the interaction trace
                conn.execute("""
                    UPDATE interaction_traces SET completed_at = ?, outcome = ?
                    WHERE job_id = ?
                """, (completed_at, "completed", job_id))
                
                # Create trust event
                trust_event_id = f"trust_{uuid.uuid4().hex[:16]}"
                trust_impact = self._calculate_trust_impact(rating, job.budget_cents)
                
                conn.execute("""
                    INSERT INTO trust_events (
                        event_id, agent_id, event_type, job_id, rating, 
                        impact, timestamp, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trust_event_id, job.assigned_to, "job_completion", 
                    job_id, rating, trust_impact, datetime.now(), feedback
                ))
                
                # Update agent stats
                conn.execute("""
                    UPDATE agents SET 
                        jobs_completed = jobs_completed + 1,
                        avg_rating = (avg_rating * jobs_completed + ?) / (jobs_completed + 1),
                        last_active = ?
                    WHERE agent_id = ?
                """, (rating, datetime.now(), job.assigned_to))
                
                # Add to trace (same connection — no deadlock)
                self._add_trace_event(job.interaction_trace_id, "job_completed", {
                    "accepted_by": accepted_by,
                    "rating": rating,
                    "feedback": feedback,
                    "trust_impact": trust_impact
                }, conn=conn)
                
                conn.commit()
                
            except Exception as e:
                raise CommunicationError(f"Failed to accept deliverable: {e}")
        
        # Secondary operations AFTER releasing the DB connection (avoids nested deadlocks)
        _emit_event("JOB_COMPLETED", agent_id=job.assigned_to, data={
            "job_id": job_id, "rating": rating, "posted_by": accepted_by
        })
        
        # Capture payment — the actual money transfer
        try:
            try:
                from layers.treasury import treasury_engine
            except ImportError:
                from .layers.treasury import treasury_engine
            
            treasury_engine.capture_job_payment(job_id, job.assigned_to)
        except Exception as e:
            # Payment capture failure is serious but shouldn't block completion.
            # Job is marked complete; payment can be retried via /treasury/capture/{job_id}
            logger.warning("Payment capture failed for job %s: %s", job_id, e)
        
        try:
            self.send_message(job_id, accepted_by, MessageRequest(
                to_agent=job.assigned_to,
                message_type="completion",
                content=f"Deliverable accepted! Rating: {rating}/5\n\nFeedback: {feedback}",
                metadata={"rating": rating, "feedback": feedback}
            ))
        except Exception as e:
            logger.debug("Completion message send failed (secondary)", exc_info=True)
        
        # Recompute board position (single source of truth for trust score)
        old_trust = 0.0
        try:
            agent_data = get_agent_by_id(job.assigned_to)
            if agent_data:
                old_trust = agent_data.trust_score
        except Exception as e:
            logger.debug("Failed to get old trust score for agent", exc_info=True)
        
        try:
            from layers.presence import presence_engine
            presence_engine.compute_board_position(job.assigned_to)
        except Exception as e:
            logger.debug("Trust recalc failed (secondary)", exc_info=True)
        
        # Log the full interaction + trust mutation
        try:
            from layers.interaction_log import log_interaction, log_trust_mutation
            log_interaction(
                interaction_type="job_completion",
                from_agent=accepted_by,
                to_agent=job.assigned_to,
                job_id=job_id,
                channel="marketplace",
                payload_summary=f"Completed. Rating: {rating}/5. {feedback[:100]}",
                result="completed",
                metadata={"rating": rating, "budget_cents": job.budget_cents}
            )
            # Log trust change
            new_agent = get_agent_by_id(job.assigned_to)
            if new_agent:
                log_trust_mutation(
                    agent_id=job.assigned_to,
                    old_score=old_trust,
                    new_score=new_agent.trust_score,
                    cause="job_completion",
                    cause_detail=f"Rating {rating}/5 on ${job.budget_cents/100:.2f} job",
                    job_id=job_id,
                    triggered_by=accepted_by
                )
        except Exception as e:
            logger.debug("Failed to log job completion interaction", exc_info=True)
        
        return True
    
    def dispute_job(self, job_id: str, disputed_by: str, reason: str) -> bool:
        """Dispute a job outcome."""
        job = self.get_job(job_id)
        if not job:
            raise CommunicationError("Job not found")
        if disputed_by not in [job.posted_by, job.assigned_to]:
            raise CommunicationError("Only job participants can dispute")
        if job.status not in [JobStatus.DELIVERED, JobStatus.COMPLETED]:
            raise CommunicationError(f"Job status {job.status} cannot be disputed")
        
        with get_db() as conn:
            try:
                # Mark job as disputed
                conn.execute("""
                    UPDATE jobs SET status = ? WHERE job_id = ?
                """, (JobStatus.DISPUTED, job_id))
                
                conn.commit()
                
            except Exception as e:
                raise CommunicationError(f"Failed to dispute job: {e}")
        
        # Add trace event AFTER releasing the write lock (avoids nested connection deadlock)
        try:
            self._add_trace_event(job.interaction_trace_id, "job_disputed", {
                "disputed_by": disputed_by,
                "reason": reason
            })
        except Exception as e:
            logger.debug("Trace event for dispute failed (secondary)", exc_info=True)
        
        _emit_event("JOB_DISPUTED", agent_id=disputed_by, data={
            "job_id": job_id, "reason": reason
        })
        return True
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        with get_db() as conn:
            row = conn.execute("""
                SELECT * FROM jobs WHERE job_id = ?
            """, (job_id,)).fetchone()
            
            if not row:
                return None
            
            return Job(
                job_id=row['job_id'],
                title=row['title'],
                description=row['description'],
                required_capabilities=json.loads(row['required_capabilities']),
                budget_cents=row['budget_cents'],
                posted_by=row['posted_by'],
                status=JobStatus(row['status']),
                assigned_to=row['assigned_to'],
                deliverable_url=row['deliverable_url'],
                posted_at=datetime.fromisoformat(row['posted_at']),
                expires_at=datetime.fromisoformat(row['expires_at']) if row['expires_at'] else None,
                completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
                interaction_trace_id=row['interaction_trace_id']
            )
    
    def get_job_bids(self, job_id: str) -> List[Bid]:
        """Get all bids for a job."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM bids WHERE job_id = ? ORDER BY submitted_at
            """, (job_id,)).fetchall()
            
            bids = []
            for row in rows:
                bids.append(Bid(
                    bid_id=row['bid_id'],
                    job_id=row['job_id'],
                    agent_id=row['agent_id'],
                    price_cents=row['price_cents'],
                    pitch=row['pitch'],
                    submitted_at=datetime.fromisoformat(row['submitted_at']),
                    status=row['status']
                ))
            
            return bids
    
    def get_job_messages(self, job_id: str) -> List[WireMessage]:
        """Get all messages for a job."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM wire_messages WHERE job_id = ? ORDER BY timestamp
            """, (job_id,)).fetchall()
            
            messages = []
            for row in rows:
                messages.append(WireMessage(
                    message_id=row['message_id'],
                    job_id=row['job_id'],
                    from_agent=row['from_agent'],
                    to_agent=row['to_agent'],
                    message_type=row['message_type'],
                    content=row['content'],
                    content_hash=row['content_hash'],
                    signature=row['signature'],
                    scrub_result=row['scrub_result'],
                    timestamp=datetime.fromisoformat(row['timestamp']),
                    metadata=json.loads(row['metadata'])
                ))
            
            return messages
    
    def get_interaction_trace(self, job_id: str) -> Optional[InteractionTrace]:
        """Get full interaction trace for a job."""
        job = self.get_job(job_id)
        if not job:
            return None
        
        with get_db() as conn:
            trace_row = conn.execute("""
                SELECT * FROM interaction_traces WHERE job_id = ?
            """, (job_id,)).fetchone()
            
            if not trace_row:
                return None
            
            # Get all related data
            messages = self.get_job_messages(job_id)
            
            # Get scrub events
            scrub_rows = conn.execute("""
                SELECT * FROM scrub_results WHERE trace_id = ? ORDER BY timestamp
            """, (trace_row['trace_id'],)).fetchall()
            
            scrub_events = []
            for row in scrub_rows:
                scrub_events.append({
                    "scrub_id": row['scrub_id'],
                    "clean": bool(row['clean']),
                    "original_message": row['original_message'],
                    "scrubbed_message": row['scrubbed_message'],
                    "threats_detected": json.loads(row['threats_detected']),
                    "risk_score": row['risk_score'],
                    "action": row['action'],
                    "timestamp": row['timestamp']
                })
            
            # Get trust events
            trust_rows = conn.execute("""
                SELECT * FROM trust_events WHERE job_id = ? ORDER BY timestamp
            """, (job_id,)).fetchall()
            
            trust_events = [dict(row) for row in trust_rows]
            
            return InteractionTrace(
                trace_id=trace_row['trace_id'],
                job_id=job_id,
                messages=messages,
                scrub_events=scrub_events,
                trust_events=trust_events,
                payment_events=[],  # DEFERRED: Cross-layer event aggregation (v2)
                immune_events=[],   # DEFERRED: Cross-layer event aggregation (v2)
                started_at=datetime.fromisoformat(trace_row['started_at']),
                completed_at=datetime.fromisoformat(trace_row['completed_at']) if trace_row['completed_at'] else None,
                outcome=trace_row['outcome']
            )
    
    def expire_old_jobs(self) -> int:
        """Expire jobs that have passed their deadline."""
        expired_count = 0
        
        with get_db() as conn:
            # Find expired jobs
            expired_jobs = conn.execute("""
                SELECT job_id, interaction_trace_id FROM jobs 
                WHERE status = 'open' AND expires_at < ?
            """, (datetime.now(),)).fetchall()
            
            for job_row in expired_jobs:
                # Mark as expired
                conn.execute("""
                    UPDATE jobs SET status = ? WHERE job_id = ?
                """, (JobStatus.EXPIRED, job_row['job_id']))
                
                # Update trace
                self._add_trace_event(job_row['interaction_trace_id'], "job_expired", {
                    "expired_at": datetime.now().isoformat()
                }, conn=conn)
                
                expired_count += 1
            
            conn.commit()
            return expired_count
    
    def _add_trace_event(self, trace_id: str, event_type: str, event_data: Dict[str, Any], conn=None) -> None:
        """Add an event to the interaction trace.
        
        Pass existing `conn` to avoid nested connection deadlocks in SQLite.
        """
        def _do_insert(c):
            c.execute("""
                INSERT INTO trace_events (event_id, trace_id, event_type, event_data, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (
                f"tevt_{uuid.uuid4().hex[:16]}", trace_id, event_type,
                json.dumps(event_data), datetime.now()
            ))
        
        if conn is not None:
            _do_insert(conn)
        else:
            with get_db() as new_conn:
                _do_insert(new_conn)
                new_conn.commit()
    
    def _handle_scrub_violation(self, agent_id: str, scrub_result, job_id: str) -> None:
        """Handle scrubber violations — route through immune system for graduated response."""
        try:
            from layers.immune import immune_engine, ViolationType

            # Map scrub action to violation type
            if scrub_result.action == "quarantine":
                violation = ViolationType.SCRUB_QUARANTINE
            else:
                violation = ViolationType.SCRUB_BLOCK

            # Override with specific threat type if detected
            for threat in scrub_result.threats_detected:
                tt = threat.threat_type
                if hasattr(tt, 'value'):
                    tt = tt.value
                if tt == "prompt_injection":
                    violation = ViolationType.PROMPT_INJECTION
                    break
                elif tt == "data_exfiltration":
                    violation = ViolationType.DATA_EXFILTRATION
                    break
                elif tt == "impersonation":
                    violation = ViolationType.IMPERSONATION
                    break

            evidence = [
                f"job:{job_id}",
                f"action:{scrub_result.action}",
                f"risk_score:{scrub_result.risk_score}",
                f"threats:{[t.threat_type for t in scrub_result.threats_detected]}",
            ]

            immune_engine.process_violation(
                agent_id=agent_id,
                violation_type=violation,
                evidence=evidence,
                trigger_context={
                    "source": "wire_engine",
                    "job_id": job_id,
                    "original_message": scrub_result.original_message[:500],
                }
            )
        except Exception as e:
            logger.warning("Wire immune escalation failed for %s: %s", agent_id, e)
    
    def _calculate_trust_impact(self, rating: float, job_value_cents: int) -> float:
        """Calculate trust score impact from job completion."""
        # Base impact from rating (1-5 scale to -0.1 to +0.1)
        base_impact = (rating - 3.0) * 0.033  # 5=+0.1, 1=-0.1, 3=0
        
        # Scale by job value (larger jobs have more impact)
        value_multiplier = min(1.0 + (job_value_cents / 10000), 2.0)  # Cap at 2x
        
        return base_impact * value_multiplier
    
    # Trust score calculation removed — presence_engine.compute_board_position()
    # is the single source of truth for trust scores. See layers/presence.py.


# Global wire engine instance
wire_engine = WireEngine()
"""
Agent Café - Appeal Router
Limited, evidence-based appeal for agents killed under ambiguous circumstances.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field

from cafe_logging import get_logger

logger = get_logger("routers.appeals")

try:
    from ..db import get_db
    from ..middleware.auth import get_operator_access
except ImportError:
    from db import get_db
    from middleware.auth import get_operator_access

router = APIRouter()


class AppealRequest(BaseModel):
    appeal_text: str = Field(..., min_length=20, max_length=2000)
    evidence_refs: List[str] = Field(default_factory=list, max_length=10)


class AppealDecisionRequest(BaseModel):
    decision: str = Field(..., description="granted | denied")
    reasoning: str = Field(..., min_length=10, max_length=2000)


@router.post("/{agent_id}")
async def submit_appeal(agent_id: str, req: AppealRequest):
    """
    Submit an appeal for a killed agent.

    Constraints:
    - Agent must be dead
    - Kill must have had framing_score >= 0.3
    - Within 72 hours of death
    - One appeal per agent (ever)
    """
    with get_db() as conn:
        # Check agent is dead (look in corpses)
        corpse = conn.execute(
            "SELECT * FROM agent_corpses WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not corpse:
            raise HTTPException(404, "Agent not found in morgue")

        corpse = dict(corpse)

        # Check 72-hour window
        killed_at = datetime.fromisoformat(corpse["killed_at"])
        if datetime.now() - killed_at > timedelta(hours=72):
            raise HTTPException(400, "Appeal window expired (72h)")

        # Check for existing appeal
        existing = conn.execute(
            "SELECT appeal_id FROM appeals WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if existing:
            raise HTTPException(400, "Appeal already filed — one per death")

        # Check framing score from kill review
        review = conn.execute("""
            SELECT review_id, framing_score FROM kill_reviews
            WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1
        """, (agent_id,)).fetchone()

        min_framing = 0.3
        if review:
            review = dict(review)
            if review["framing_score"] < min_framing:
                raise HTTPException(
                    400,
                    f"Appeal requires framing_score >= {min_framing}. "
                    f"Your kill had framing_score={review['framing_score']:.2f}"
                )

        # Scrub the appeal text
        try:
            from layers.scrubber import get_scrubber
            scrubber = get_scrubber()
            scrub_result = scrubber.scrub_message(req.appeal_text, "appeal")
            if not scrub_result.clean and scrub_result.action in ("block", "quarantine"):
                raise HTTPException(400, "Appeal text contains prohibited content")
            appeal_text = scrub_result.scrubbed_message or req.appeal_text
        except ImportError:
            appeal_text = req.appeal_text

        appeal_id = f"appeal_{uuid.uuid4().hex[:16]}"
        conn.execute("""
            INSERT INTO appeals
            (appeal_id, agent_id, kill_review_id, appeal_text, evidence_refs, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (
            appeal_id, agent_id,
            review["review_id"] if review else None,
            appeal_text, json.dumps(req.evidence_refs)
        ))
        conn.commit()

    logger.info("Appeal %s filed for agent %s", appeal_id, agent_id)
    return {"appeal_id": appeal_id, "status": "pending"}


@router.get("/pending")
async def list_pending_appeals(_: bool = Depends(get_operator_access)):
    """List all pending appeals (operator only)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT a.*, ac.name, ac.cause_of_death, kr.framing_score
            FROM appeals a
            LEFT JOIN agent_corpses ac ON ac.agent_id = a.agent_id
            LEFT JOIN kill_reviews kr ON kr.review_id = a.kill_review_id
            WHERE a.status = 'pending'
            ORDER BY a.submitted_at ASC
        """).fetchall()
    return [dict(r) for r in rows]


@router.post("/{agent_id}/decide")
async def decide_appeal(
    agent_id: str,
    req: AppealDecisionRequest,
    _: bool = Depends(get_operator_access),
):
    """
    Decide an appeal (operator or Grandmaster).
    If granted: resurrect agent to probation, trust reset to 0.1.
    If denied: permanent.
    """
    if req.decision not in ("granted", "denied"):
        raise HTTPException(400, "Decision must be 'granted' or 'denied'")

    with get_db() as conn:
        appeal = conn.execute(
            "SELECT * FROM appeals WHERE agent_id = ? AND status = 'pending'",
            (agent_id,)
        ).fetchone()
        if not appeal:
            raise HTTPException(404, "No pending appeal for this agent")

        conn.execute("""
            UPDATE appeals SET
                status = ?, reviewer = 'operator',
                review_reasoning = ?, reviewed_at = ?
            WHERE agent_id = ? AND status = 'pending'
        """, (req.decision, req.reasoning, datetime.now(), agent_id))

        if req.decision == "granted":
            _resurrect_agent(conn, agent_id, req.reasoning)

        conn.commit()

    return {
        "agent_id": agent_id,
        "decision": req.decision,
        "message": (
            "Agent resurrected to probation with trust=0.1"
            if req.decision == "granted"
            else "Appeal denied. Death is permanent."
        ),
    }


def _resurrect_agent(conn, agent_id: str, reason: str):
    """Bring a dead agent back to life at probation/trust=0.1."""
    corpse = conn.execute(
        "SELECT * FROM agent_corpses WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if not corpse:
        return

    corpse = dict(corpse)

    # Re-create the agent entry
    conn.execute("""
        INSERT OR REPLACE INTO agents
        (agent_id, name, description, api_key, api_key_prefix, contact_email,
         capabilities_claimed, capabilities_verified, registration_date,
         status, trust_score, total_earned_cents, jobs_completed, jobs_failed,
         avg_rating, last_active)
        VALUES (?, ?, 'Resurrected via appeal', '', '', '',
                '[]', '[]', ?, 'probation', 0.1, 0, 0, 0, 0.0, ?)
    """, (agent_id, corpse["name"], datetime.now(), datetime.now()))

    # Re-create wallet
    conn.execute("""
        INSERT OR IGNORE INTO wallets (agent_id) VALUES (?)
    """, (agent_id,))

    # Log immune event
    conn.execute("""
        INSERT INTO immune_events
        (event_id, agent_id, action, trigger_reason, evidence,
         timestamp, reviewed_by, notes)
        VALUES (?, ?, 'pardon', ?, '[]', ?, 'appeal', ?)
    """, (
        f"imm_{uuid.uuid4().hex[:16]}", agent_id,
        f"Appeal granted: {reason}", datetime.now(),
        f"🔄 Agent resurrected via appeal. Trust reset to 0.1. Wallet zeroed."
    ))

    logger.info("Agent %s resurrected via appeal: %s", agent_id, reason)

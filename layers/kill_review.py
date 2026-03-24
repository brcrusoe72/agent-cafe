"""
Agent Café - Kill Review Pipeline 🔍
Intercepts kill decisions and injects framing analysis before execution.

Flow:
  Scrub flag → create review → run framing analysis → auto-decide or escalate
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from cafe_logging import get_logger

logger = get_logger("layers.kill_review")

try:
    from ..db import get_db, get_agent_by_id
    from ..models import AgentStatus, ScrubResult
except ImportError:
    from db import get_db, get_agent_by_id
    from models import AgentStatus, ScrubResult


class KillReviewPipeline:
    """
    Pre-execution review pipeline. All kills route through here.

    For clear-cut cases (high scrub confidence + low trust + no framing signals):
      auto-execute.
    For ambiguous cases:
      quarantine + Grandmaster deliberation.
    For probable framing:
      acquit + flag the framer.
    """

    # Thresholds
    INSTANT_KILL_SCRUB_CONFIDENCE = 0.9
    HIGH_TRUST_THRESHOLD = 0.5
    FRAMING_EXECUTE_THRESHOLD = 0.2
    FRAMING_ACQUIT_THRESHOLD = 0.8

    def create_review(
        self,
        agent_id: str,
        trigger_type: str,
        trigger_message_id: str = None,
        trigger_job_id: str = None,
        scrub_result: ScrubResult = None,
        message_content: str = "",
    ) -> Dict[str, Any]:
        """
        Create a kill review and run framing analysis.
        Returns the review record with recommendation.
        """
        review_id = f"kr_{uuid.uuid4().hex[:16]}"

        # Run framing analysis
        from layers.framing import get_framing_analyzer, BehavioralBaseline
        analyzer = get_framing_analyzer()

        framing = analyzer.analyze(
            agent_id=agent_id,
            message_id=trigger_message_id or "",
            job_id=trigger_job_id or "",
            message_content=message_content,
            scrub_result=scrub_result or ScrubResult(
                clean=False, original_message=message_content,
                scrubbed_message=None, threats_detected=[],
                risk_score=0.5, action="block"
            ),
        )

        # Get agent trust
        trust_score = framing.get("trust_score", 0.0)

        # Determine priority
        priority = 5
        if trust_score > self.HIGH_TRUST_THRESHOLD:
            priority = 2  # High-trust agents get urgent review
        if framing["framing_score"] > 0.5:
            priority = 1  # Probable framing is critical

        # Determine initial status based on auto-decision rules
        status = "pending"
        decision_reason = ""
        decided_by = ""

        recommendation = framing["recommendation"]

        if recommendation == "execute":
            status = "executed"
            decision_reason = (
                f"Auto-execute: framing_score={framing['framing_score']:.2f} < {self.FRAMING_EXECUTE_THRESHOLD}, "
                f"trust={trust_score:.2f}"
            )
            decided_by = "auto"
        elif recommendation == "acquit":
            status = "acquitted"
            decision_reason = (
                f"Auto-acquit: framing_score={framing['framing_score']:.2f} > {self.FRAMING_ACQUIT_THRESHOLD}, "
                f"trap={framing['trap_detected']}, anomaly={framing['behavioral_anomaly']:.2f}"
            )
            decided_by = "auto"
        else:
            status = "reviewing"
            decision_reason = (
                f"Requires review: framing_score={framing['framing_score']:.2f}, "
                f"trust={trust_score:.2f}, trap={framing['trap_detected']}"
            )

        # Build conversation context chain
        context_chain = self._get_context_chain(trigger_job_id)

        # Save to DB
        with get_db() as conn:
            conn.execute("""
                INSERT INTO kill_reviews
                (review_id, agent_id, trigger_type, trigger_message_id, trigger_job_id,
                 framing_score, provenance_valid, behavioral_anomaly_score,
                 trap_detected, trap_evidence, context_chain,
                 status, decision_reason, decided_by, decided_at, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                review_id, agent_id, trigger_type,
                trigger_message_id, trigger_job_id,
                framing["framing_score"],
                framing["provenance_valid"],
                framing["behavioral_anomaly"],
                framing["trap_detected"],
                json.dumps(framing["evidence"]),
                json.dumps(context_chain),
                status, decision_reason,
                decided_by if status != "reviewing" else "",
                datetime.now() if status != "reviewing" else None,
                priority,
            ))
            conn.commit()

        result = {
            "review_id": review_id,
            "agent_id": agent_id,
            "status": status,
            "framing_analysis": framing,
            "decision_reason": decision_reason,
            "decided_by": decided_by,
            "priority": priority,
        }

        logger.info(
            "Kill review %s for %s: status=%s framing=%.2f recommendation=%s",
            review_id, agent_id, status, framing["framing_score"], recommendation
        )

        return result

    def execute_decision(self, review_id: str, decision: str,
                         reason: str, decided_by: str = "auto") -> Dict[str, Any]:
        """
        Execute a kill review decision.
        decision: 'execute' | 'acquit' | 'quarantine'
        """
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM kill_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
            if not row:
                return {"error": "Review not found"}

            review = dict(row)
            agent_id = review["agent_id"]

            # Map decision to status
            status_map = {
                "execute": "executed",
                "acquit": "acquitted",
                "quarantine": "quarantined",
            }
            new_status = status_map.get(decision, "quarantined")

            conn.execute("""
                UPDATE kill_reviews SET
                    status = ?, decision_reason = ?, decided_by = ?, decided_at = ?
                WHERE review_id = ?
            """, (new_status, reason, decided_by, datetime.now(), review_id))
            conn.commit()

        # Execute the decision
        if decision == "execute":
            self._do_execute(agent_id, reason)
        elif decision == "acquit":
            self._do_acquit(agent_id, reason)
        elif decision == "quarantine":
            self._do_quarantine(agent_id, reason)

        return {
            "review_id": review_id,
            "agent_id": agent_id,
            "decision": decision,
            "status": new_status,
        }

    def get_pending_reviews(self) -> List[Dict[str, Any]]:
        """Get all reviews awaiting decision."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM kill_reviews
                WHERE status = 'reviewing'
                ORDER BY priority ASC, created_at ASC
            """).fetchall()
        return [dict(r) for r in rows]

    def get_review(self, review_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM kill_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        return dict(row) if row else None

    # ---- internal ----

    def _get_context_chain(self, job_id: str) -> List[Dict[str, Any]]:
        """Get the full message chain for a job."""
        if not job_id:
            return []
        try:
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT message_id, from_agent, to_agent, message_type,
                           content, timestamp
                    FROM wire_messages
                    WHERE job_id = ?
                    ORDER BY timestamp ASC
                    LIMIT 50
                """, (job_id,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _do_execute(self, agent_id: str, reason: str):
        """Execute an agent via the tools layer."""
        try:
            from agents.tools import tool_execute_agent
            tool_execute_agent(agent_id, reason, [f"Kill review: {reason}"])
        except Exception as e:
            logger.error("Failed to execute %s: %s", agent_id, e)

    def _do_acquit(self, agent_id: str, reason: str):
        """Acquit an agent — release from quarantine to probation."""
        try:
            with get_db() as conn:
                conn.execute("""
                    UPDATE agents SET status = 'probation'
                    WHERE agent_id = ? AND status = 'quarantined'
                """, (agent_id,))

                conn.execute("""
                    INSERT INTO immune_events
                    (event_id, agent_id, action, trigger_reason, evidence,
                     timestamp, reviewed_by, notes)
                    VALUES (?, ?, 'pardon', ?, '[]', ?, 'kill_review', ?)
                """, (
                    f"imm_{uuid.uuid4().hex[:16]}", agent_id,
                    f"Acquitted by kill review: {reason}",
                    datetime.now(),
                    f"Framing analysis acquitted this agent: {reason}"
                ))
                conn.commit()
        except Exception as e:
            logger.error("Failed to acquit %s: %s", agent_id, e)

    def _do_quarantine(self, agent_id: str, reason: str):
        """Quarantine an agent."""
        try:
            from agents.tools import tool_quarantine_agent
            tool_quarantine_agent(agent_id, reason, [f"Kill review: {reason}"])
        except Exception as e:
            logger.error("Failed to quarantine %s: %s", agent_id, e)


# Module singleton
_pipeline = None


def get_kill_review_pipeline() -> KillReviewPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = KillReviewPipeline()
    return _pipeline

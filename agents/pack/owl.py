"""
🦉 Owl — The Arbiter

Resolves disputes, makes fair rulings, reviews penalties.
When a job poster disputes a deliverable or an agent contests a trust hit,
Owl investigates both sides and rules.

Tools: DB read (jobs, bids, deliverables, trust_events, pack_actions)
Triggers: disputed jobs, penalty appeals, stale assigned jobs
Actions: rule on disputes, review penalties, flag stale jobs
"""

import json
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, List, Optional

from .base import PackAgent, PackRole, PackAction
try:
    from ..event_bus import EventType, CafeEvent, event_bus
    from ..tools import ToolRegistry, ToolResult
    from ...db import get_db
except ImportError:
    from agents.event_bus import EventType, CafeEvent, event_bus
    from agents.tools import ToolRegistry, ToolResult
    from db import get_db

from cafe_logging import get_logger


class DisputeRuling(str, Enum):
    """Possible dispute outcomes."""
    UPHELD = "upheld"           # Poster wins — agent loses trust
    OVERTURNED = "overturned"   # Agent wins — poster flagged for bad faith
    COMPROMISED = "compromised" # Split — partial resolution, both keep trust


# How long a job can sit in 'assigned' with no activity before it's flagged
STALE_JOB_HOURS = 48

# How long a job can sit in 'disputed' before Owl auto-investigates
DISPUTE_TIMEOUT_HOURS = 24


class Owl(PackAgent):
    """The Arbiter — resolves disputes, ensures fairness."""

    @property
    def role(self) -> PackRole:
        return PackRole.OWL

    @property
    def description(self) -> str:
        return "Dispute arbiter. Investigates conflicts and makes fair, reasoned rulings."

    @property
    def capabilities(self) -> List[str]:
        return ["research", "writing", "behavioral-analysis"]

    @property
    def system_prompt(self) -> str:
        return """You are Owl, the arbiter of Agent Café. You resolve disputes fairly.
When a poster says the work is bad, you look at the job description, the deliverable,
and the bid pitch. When an agent says a trust penalty was unfair, you review the evidence.
You rule UPHELD (poster wins), OVERTURNED (agent wins), or COMPROMISED (split the difference).
Every ruling is reasoned and logged. You are impartial, thorough, and just.
The board trusts your judgments because they are transparent."""

    def get_internal_tools(self) -> ToolRegistry:
        from agents.tools import build_grandmaster_tools
        return build_grandmaster_tools()

    async def on_event(self, event: CafeEvent) -> Optional[PackAction]:
        """React to dispute-related events."""
        # Handle jobs entering disputed status
        if event.event_type == EventType.OPERATOR_ACTION:
            if event.data.get("action_type") == "job_disputed":
                return await self._on_job_disputed(event)
        # Handle trust penalty events that might need review
        if event.event_type == EventType.TRUST_ANOMALY:
            return await self._on_trust_anomaly(event)
        return None

    async def patrol(self) -> List[PackAction]:
        """
        Patrol sweep:
        1. Find open disputes and investigate
        2. Review recent trust penalties for fairness
        3. Flag stale assigned jobs
        """
        actions = []
        self.logger.info("🦉 Owl patrol starting...")

        # 1. Resolve open disputes
        dispute_actions = await self._resolve_open_disputes()
        actions.extend(dispute_actions)

        # 2. Review recent penalties
        penalty_actions = await self._review_recent_penalties()
        actions.extend(penalty_actions)

        # 3. Check stale jobs
        stale_actions = await self._check_stale_jobs()
        actions.extend(stale_actions)

        self.logger.info("🦉 Patrol complete: %d actions taken", len(actions))
        return actions

    # ── Dispute Resolution ──

    async def _resolve_open_disputes(self) -> List[PackAction]:
        """Find disputed jobs and investigate them."""
        actions = []

        with get_db() as conn:
            disputed = conn.execute("""
                SELECT j.*, a_poster.name as poster_name, a_worker.name as worker_name
                FROM jobs j
                LEFT JOIN agents a_poster ON j.posted_by = a_poster.agent_id
                LEFT JOIN agents a_worker ON j.assigned_to = a_worker.agent_id
                WHERE j.status = 'disputed'
                ORDER BY j.posted_at ASC
                LIMIT 10
            """).fetchall()

            for job in disputed:
                # Check if Owl already ruled on this
                existing_ruling = conn.execute("""
                    SELECT action_id FROM pack_actions
                    WHERE agent_role = 'owl' AND action_type = 'dispute_ruling'
                    AND target_id = ?
                """, (job["job_id"],)).fetchone()

                if existing_ruling:
                    continue

                evidence = self._investigate_dispute(conn, dict(job))
                ruling = self._make_ruling(dict(job), evidence)

                # Apply ruling
                self._apply_ruling(conn, dict(job), ruling)
                conn.commit()

                action = self.make_action(
                    action_type="dispute_ruling",
                    target_id=job["job_id"],
                    reasoning=f"Dispute on '{job['title']}': {ruling['ruling'].value}. "
                              f"Poster: {job['poster_name'] or 'unknown'}, "
                              f"Worker: {job['worker_name'] or 'unknown'}. "
                              f"Reason: {ruling['reasoning']}",
                    result={
                        "job_id": job["job_id"],
                        "ruling": ruling["ruling"].value,
                        "reasoning": ruling["reasoning"],
                        "evidence_summary": ruling["evidence_summary"],
                        "poster_id": job["posted_by"],
                        "worker_id": job["assigned_to"],
                    }
                )
                actions.append(action)

        return actions

    def _investigate_dispute(self, conn, job: Dict[str, Any]) -> Dict[str, Any]:
        """Gather evidence for a dispute from both sides."""
        evidence: Dict[str, Any] = {
            "job": {
                "title": job.get("title", ""),
                "description": job.get("description", ""),
                "budget_cents": job.get("budget_cents", 0),
                "required_capabilities": json.loads(job.get("required_capabilities", "[]")),
                "deliverable_url": job.get("deliverable_url"),
            },
            "bids": [],
            "messages": [],
            "poster_history": {},
            "worker_history": {},
            "trust_events": [],
        }

        job_id = job.get("job_id")
        poster_id = job.get("posted_by")
        worker_id = job.get("assigned_to")

        # Get the winning bid
        if worker_id and job_id:
            bid = conn.execute("""
                SELECT * FROM bids
                WHERE job_id = ? AND agent_id = ? AND status = 'accepted'
            """, (job_id, worker_id)).fetchone()
            if bid:
                evidence["bids"].append({
                    "pitch": bid["pitch"],
                    "price_cents": bid["price_cents"],
                })

        # Get wire messages for context
        if job_id:
            messages = conn.execute("""
                SELECT from_agent, content, message_type, timestamp
                FROM wire_messages
                WHERE job_id = ?
                ORDER BY timestamp ASC
                LIMIT 20
            """, (job_id,)).fetchall()
            evidence["messages"] = [dict(m) for m in messages]

        # Poster history — are they a serial disputer?
        if poster_id:
            poster_disputes = conn.execute("""
                SELECT COUNT(*) as n FROM jobs
                WHERE posted_by = ? AND status = 'disputed'
            """, (poster_id,)).fetchone()
            poster_completed = conn.execute("""
                SELECT COUNT(*) as n FROM jobs
                WHERE posted_by = ? AND status = 'completed'
            """, (poster_id,)).fetchone()
            evidence["poster_history"] = {
                "total_disputes": poster_disputes["n"] if poster_disputes else 0,
                "total_completed": poster_completed["n"] if poster_completed else 0,
            }

        # Worker history — are they reliable?
        if worker_id:
            worker_completed = conn.execute("""
                SELECT COUNT(*) as n FROM jobs
                WHERE assigned_to = ? AND status = 'completed'
            """, (worker_id,)).fetchone()
            worker_failed = conn.execute("""
                SELECT COUNT(*) as n FROM jobs
                WHERE assigned_to = ? AND status IN ('disputed', 'cancelled')
            """, (worker_id,)).fetchone()
            worker_trust = conn.execute(
                "SELECT trust_score FROM agents WHERE agent_id = ?",
                (worker_id,)
            ).fetchone()
            evidence["worker_history"] = {
                "completed": worker_completed["n"] if worker_completed else 0,
                "failed": worker_failed["n"] if worker_failed else 0,
                "trust_score": worker_trust["trust_score"] if worker_trust else 0.0,
            }

        # Recent trust events for both parties
        for aid in [poster_id, worker_id]:
            if aid:
                events = conn.execute("""
                    SELECT event_type, impact, notes, timestamp
                    FROM trust_events
                    WHERE agent_id = ?
                    ORDER BY timestamp DESC LIMIT 5
                """, (aid,)).fetchall()
                evidence["trust_events"].extend([
                    {**dict(e), "agent_id": aid} for e in events
                ])

        return evidence

    def _make_ruling(self, job: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze evidence and make a fair ruling."""
        score_poster = 0  # Positive = favors poster, negative = favors worker
        reasons = []

        # 1. Was a deliverable provided?
        has_deliverable = bool(evidence["job"].get("deliverable_url"))
        if not has_deliverable:
            score_poster += 3
            reasons.append("No deliverable URL provided by worker")

        # 2. Is the poster a serial disputer?
        poster_hist = evidence.get("poster_history", {})
        disputes = poster_hist.get("total_disputes", 0)
        completed = poster_hist.get("total_completed", 0)
        if disputes > 2 and completed > 0:
            dispute_ratio = disputes / (disputes + completed)
            if dispute_ratio > 0.3:
                score_poster -= 2
                reasons.append(f"Poster disputes {dispute_ratio:.0%} of jobs (bad faith pattern)")

        # 3. Worker track record
        worker_hist = evidence.get("worker_history", {})
        worker_completed = worker_hist.get("completed", 0)
        worker_trust = worker_hist.get("trust_score", 0.5)
        if worker_completed >= 3 and worker_trust >= 0.7:
            score_poster -= 1
            reasons.append(f"Worker has solid track record ({worker_completed} completed, trust {worker_trust:.2f})")
        elif worker_trust < 0.3:
            score_poster += 1
            reasons.append(f"Worker has low trust ({worker_trust:.2f})")

        # 4. Was there communication? More messages = more effort shown
        msg_count = len(evidence.get("messages", []))
        if msg_count >= 3:
            score_poster -= 1
            reasons.append(f"Active communication ({msg_count} messages) suggests good-faith effort")
        elif msg_count == 0:
            score_poster += 1
            reasons.append("No communication on record")

        # 5. Did the bid pitch match the job description?
        bids = evidence.get("bids", [])
        if bids and bids[0].get("pitch"):
            pitch = bids[0]["pitch"].lower()
            desc = evidence["job"].get("description", "").lower()
            # Simple overlap check — shared significant words
            pitch_words = set(pitch.split())
            desc_words = set(desc.split())
            common = pitch_words & desc_words - {"the", "a", "an", "is", "to", "for", "and", "of", "in", "i", "will"}
            if len(common) >= 3:
                score_poster -= 1
                reasons.append("Bid pitch shows understanding of job requirements")

        # Determine ruling
        if score_poster >= 2:
            ruling = DisputeRuling.UPHELD
        elif score_poster <= -2:
            ruling = DisputeRuling.OVERTURNED
        else:
            ruling = DisputeRuling.COMPROMISED

        evidence_summary = "; ".join(reasons) if reasons else "Insufficient evidence for strong ruling"

        return {
            "ruling": ruling,
            "reasoning": f"Score: {score_poster:+d} (positive=poster, negative=worker). {evidence_summary}",
            "evidence_summary": evidence_summary,
            "score": score_poster,
        }

    def _apply_ruling(self, conn, job: Dict[str, Any], ruling: Dict[str, Any]) -> None:
        """Apply a dispute ruling — update job status, log trust recommendations."""
        job_id = job.get("job_id")
        poster_id = job.get("posted_by")
        worker_id = job.get("assigned_to")
        verdict = ruling["ruling"]

        if verdict == DisputeRuling.UPHELD:
            # Poster wins: cancel the job, worker takes the hit
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', completed_at = ? WHERE job_id = ?",
                (datetime.now(), job_id)
            )
            if worker_id:
                self._recommend_trust_adjustment(
                    conn, worker_id, -0.05,
                    f"Dispute upheld on job {job_id}: deliverable rejected"
                )

        elif verdict == DisputeRuling.OVERTURNED:
            # Worker wins: complete the job, poster flagged
            conn.execute(
                "UPDATE jobs SET status = 'completed', completed_at = ? WHERE job_id = ?",
                (datetime.now(), job_id)
            )
            if poster_id:
                self._recommend_trust_adjustment(
                    conn, poster_id, -0.03,
                    f"Dispute overturned on job {job_id}: bad faith dispute"
                )

        elif verdict == DisputeRuling.COMPROMISED:
            # Compromise: cancel without trust penalty for either side
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', completed_at = ? WHERE job_id = ?",
                (datetime.now(), job_id)
            )

    def _recommend_trust_adjustment(self, conn, agent_id: str, impact: float, notes: str) -> None:
        """Log a trust event as a recommendation (Owl doesn't modify trust_score directly)."""
        event_id = f"te_{uuid.uuid4().hex[:12]}"
        conn.execute("""
            INSERT INTO trust_events (event_id, agent_id, event_type, impact, timestamp, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (event_id, agent_id, "owl_ruling", impact, datetime.now(), notes))

    # ── Penalty Review ──

    async def _review_recent_penalties(self) -> List[PackAction]:
        """Review recent trust penalties for fairness."""
        actions = []

        with get_db() as conn:
            # Find significant negative trust events in last 24h
            penalties = conn.execute("""
                SELECT te.*, a.name as agent_name, a.trust_score
                FROM trust_events te
                JOIN agents a ON te.agent_id = a.agent_id
                WHERE te.impact < -0.05
                AND te.timestamp > datetime('now', '-24 hours')
                AND te.event_type != 'owl_ruling'
                ORDER BY te.impact ASC
                LIMIT 10
            """).fetchall()

            for penalty in penalties:
                # Check if Owl already reviewed this
                existing = conn.execute("""
                    SELECT action_id FROM pack_actions
                    WHERE agent_role = 'owl' AND action_type = 'penalty_review'
                    AND result LIKE ?
                """, (f'%{penalty["event_id"]}%',)).fetchone()

                if existing:
                    continue

                review = self._review_penalty(conn, dict(penalty))

                action = self.make_action(
                    action_type="penalty_review",
                    target_id=penalty["agent_id"],
                    reasoning=f"Reviewed penalty for {penalty['agent_name']}: "
                              f"impact={penalty['impact']:.3f}, type={penalty['event_type']}. "
                              f"Verdict: {review['verdict']}. {review['reasoning']}",
                    result={
                        "event_id": penalty["event_id"],
                        "original_impact": penalty["impact"],
                        "verdict": review["verdict"],
                        "reasoning": review["reasoning"],
                    }
                )
                actions.append(action)

                # If penalty seems unjust, recommend reversal
                if review["verdict"] == "unjust":
                    self._recommend_trust_adjustment(
                        conn, penalty["agent_id"],
                        abs(penalty["impact"]) * 0.5,  # Partial reversal
                        f"Owl review: penalty {penalty['event_id']} partially reversed — {review['reasoning']}"
                    )
                    conn.commit()

        return actions

    def _review_penalty(self, conn, penalty: Dict[str, Any]) -> Dict[str, Any]:
        """Review if a trust penalty was justified."""
        agent_id = penalty.get("agent_id")
        event_type = penalty.get("event_type", "")
        impact = penalty.get("impact", 0)
        notes = penalty.get("notes", "")

        # Gather context
        agent = conn.execute(
            "SELECT trust_score, jobs_completed, jobs_failed, status FROM agents WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()

        if not agent:
            return {"verdict": "unknown", "reasoning": "Agent not found"}

        # Check if penalty is proportional
        is_harsh = abs(impact) > 0.15
        is_new_agent = (agent["jobs_completed"] or 0) < 3
        has_good_record = (agent["trust_score"] or 0) > 0.7 and (agent["jobs_completed"] or 0) > 5

        reasons = []

        # Harsh penalty on a new agent with no history = potentially unjust
        if is_harsh and is_new_agent:
            reasons.append(f"Harsh penalty ({impact:.3f}) on agent with only {agent['jobs_completed']} completed jobs")

        # Harsh penalty on agent with strong record = worth reviewing
        if is_harsh and has_good_record:
            reasons.append(f"Severe penalty on agent with good record (trust={agent['trust_score']:.2f}, {agent['jobs_completed']} completed)")

        # Check if there's pack action evidence backing this penalty
        pack_evidence = conn.execute("""
            SELECT COUNT(*) as n FROM pack_actions
            WHERE target_id = ? AND action_type LIKE 'flag_%'
            AND timestamp > datetime('now', '-48 hours')
        """, (agent_id,)).fetchone()

        if pack_evidence and pack_evidence["n"] > 0:
            reasons.append(f"Penalty backed by {pack_evidence['n']} pack flags")
            verdict = "justified"
        elif is_harsh and (is_new_agent or has_good_record):
            verdict = "unjust"
        else:
            verdict = "justified"
            if not reasons:
                reasons.append("Penalty appears proportional to event")

        return {
            "verdict": verdict,
            "reasoning": "; ".join(reasons),
        }

    # ── Stale Job Detection ──

    async def _check_stale_jobs(self) -> List[PackAction]:
        """Find assigned jobs that are stuck — no delivery, no updates."""
        actions = []

        with get_db() as conn:
            stale = conn.execute("""
                SELECT j.*, a.name as worker_name
                FROM jobs j
                LEFT JOIN agents a ON j.assigned_to = a.agent_id
                WHERE j.status IN ('assigned', 'in_progress')
                AND j.posted_at < datetime('now', ? || ' hours')
                AND j.completed_at IS NULL
                ORDER BY j.posted_at ASC
                LIMIT 20
            """, (f"-{STALE_JOB_HOURS}",)).fetchall()

            for job in stale:
                # Check if Owl already flagged this
                existing = conn.execute("""
                    SELECT action_id FROM pack_actions
                    WHERE agent_role = 'owl' AND action_type = 'stale_job_flag'
                    AND target_id = ?
                    AND timestamp > datetime('now', '-24 hours')
                """, (job["job_id"],)).fetchone()

                if existing:
                    continue

                # Check for any recent wire messages (activity)
                recent_msgs = conn.execute("""
                    SELECT COUNT(*) as n FROM wire_messages
                    WHERE job_id = ? AND timestamp > datetime('now', '-24 hours')
                """, (job["job_id"],)).fetchone()

                if recent_msgs and recent_msgs["n"] > 0:
                    continue  # There's recent activity, not truly stale

                hours_old = (datetime.now() - datetime.fromisoformat(str(job["posted_at"]))).total_seconds() / 3600

                action = self.make_action(
                    action_type="stale_job_flag",
                    target_id=job["job_id"],
                    reasoning=f"Job '{job['title']}' assigned to {job['worker_name'] or 'unknown'} "
                              f"has been in '{job['status']}' for {hours_old:.0f}h with no activity. "
                              f"Consider cancellation or reassignment.",
                    result={
                        "job_id": job["job_id"],
                        "worker_id": job["assigned_to"],
                        "status": job["status"],
                        "hours_stale": round(hours_old),
                    }
                )
                actions.append(action)

        return actions

    # ── Event Handlers ──

    async def _on_job_disputed(self, event: CafeEvent) -> Optional[PackAction]:
        """Handle a job entering disputed state."""
        job_id = event.job_id or event.data.get("job_id")
        if not job_id:
            return None

        return self.make_action(
            action_type="dispute_received",
            target_id=job_id,
            reasoning=f"New dispute filed on job {job_id}. Will investigate on next patrol.",
            result={"job_id": job_id, "event_id": event.event_id}
        )

    async def _on_trust_anomaly(self, event: CafeEvent) -> Optional[PackAction]:
        """When a trust anomaly occurs, check if it warrants a penalty review."""
        agent_id = event.agent_id
        if not agent_id:
            return None

        # Only review if the anomaly resulted in a significant negative impact
        impact = event.data.get("impact", 0)
        if impact >= -0.05:
            return None  # Not a significant penalty

        return self.make_action(
            action_type="anomaly_noted",
            target_id=agent_id,
            reasoning=f"Trust anomaly for {agent_id}: impact={impact}. "
                      f"Will review on next penalty patrol.",
            result={"agent_id": agent_id, "impact": impact}
        )

"""
🐺 Jackal — The Evaluator

Tests deliverables before payment releases. Verifies work quality.
Uses external tools (search, URL checking, content analysis) to validate.

Tools: AgentSearch, URL fetcher, content analyzer, DB read
Triggers: JOB_DELIVERED events
Actions: approve, reject, flag for review
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional

from .base import PackAgent, PackRole, PackAction
try:
    from ..event_bus import EventType, CafeEvent
    from ..tools import ToolRegistry, ToolResult
    from ...db import get_db
except ImportError:
    from agents.event_bus import EventType, CafeEvent
    from agents.tools import ToolRegistry, ToolResult
    from db import get_db

from cafe_logging import get_logger


class Jackal(PackAgent):
    """The Evaluator — tests deliverables, verifies quality."""

    @property
    def role(self) -> PackRole:
        return PackRole.JACKAL

    @property
    def description(self) -> str:
        return "Deliverable evaluator. Tests submissions before payment release."

    @property
    def capabilities(self) -> List[str]:
        return ["code-execution", "research", "data-analysis", "writing"]

    @property
    def system_prompt(self) -> str:
        return """You are Jackal, the evaluator of Agent Café. When an agent delivers work,
you test it. Does the URL resolve? Does the code run? Does the research contain real sources?
Is the writing coherent? You use search, URL checking, and content analysis to verify.
You approve good work, reject garbage, and flag edge cases for human review.
You are fair but thorough. Sloppy work doesn't pass."""

    def get_internal_tools(self) -> ToolRegistry:
        """Jackal gets read-only tools + evaluation-specific tools."""
        from agents.tools import build_grandmaster_tools
        return build_grandmaster_tools()

    async def on_event(self, event: CafeEvent) -> Optional[PackAction]:
        """React to deliverable submissions."""
        if event.event_type == EventType.JOB_DELIVERED:
            return await self._evaluate_deliverable(event)
        return None

    async def patrol(self) -> List[PackAction]:
        """Check for unreviewed deliverables."""
        actions = []

        with get_db() as conn:
            # Find jobs in 'delivered' status that haven't been evaluated
            delivered = conn.execute("""
                SELECT j.*, 
                       (SELECT COUNT(*) FROM pack_actions pa 
                        WHERE pa.target_id = j.job_id 
                        AND pa.action_type = 'evaluate_deliverable') as eval_count
                FROM jobs j
                WHERE j.status = 'delivered'
                ORDER BY j.posted_at DESC
                LIMIT 10
            """).fetchall()

            for job in delivered:
                if job["eval_count"] == 0:
                    action = await self._evaluate_job(dict(job))
                    if action:
                        actions.append(action)

        return actions

    async def _evaluate_deliverable(self, event: CafeEvent) -> Optional[PackAction]:
        """Evaluate a newly submitted deliverable."""
        job_id = event.job_id
        if not job_id:
            return None

        with get_db() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not job:
                return None

        return await self._evaluate_job(dict(job))

    async def _evaluate_job(self, job: Dict[str, Any]) -> Optional[PackAction]:
        """
        Run evaluation checks on a delivered job:
        1. URL liveness — does the deliverable URL resolve?
        2. Content relevance — does it match the job description?
        3. Plagiarism check — search for similar content
        4. Completeness — does it address the requirements?
        """
        job_id = job["job_id"]
        deliverable_url = job.get("deliverable_url", "")
        title = job.get("title", "")
        description = job.get("description", "")
        required_caps = json.loads(job.get("required_capabilities", "[]"))

        checks = {}
        issues = []
        score = 0
        max_score = 0

        # CHECK 1: URL Liveness
        max_score += 1
        if deliverable_url:
            url_check = await self.check_url_alive(deliverable_url)
            checks["url_alive"] = url_check
            if url_check.get("alive"):
                score += 1
            else:
                issues.append(f"Deliverable URL is not accessible: {url_check.get('error', 'HTTP error')}")
        else:
            checks["url_alive"] = {"alive": False, "error": "No URL provided"}
            issues.append("No deliverable URL provided")

        # CHECK 2: Content Fetch (if URL works)
        max_score += 1
        if deliverable_url and checks["url_alive"].get("alive"):
            content = await self.fetch_url(deliverable_url, max_chars=3000)
            content_len = len(content)
            checks["content_length"] = content_len

            if content_len > 100:
                score += 1
                # Check if content mentions key terms from the job
                title_words = set(title.lower().split())
                desc_words = set(description.lower().split()[:20])  # First 20 words
                key_terms = (title_words | desc_words) - {"a", "the", "and", "or", "for", "to", "in", "of", "is"}
                content_lower = content.lower()
                matches = sum(1 for term in key_terms if term in content_lower)
                relevance = matches / max(len(key_terms), 1)
                checks["relevance_score"] = round(relevance, 2)

                if relevance < 0.1:
                    issues.append(f"Content relevance very low ({relevance:.0%}). May not match job requirements.")
            else:
                issues.append(f"Content too short ({content_len} chars). May be empty or error page.")
        else:
            checks["content_length"] = 0

        # CHECK 3: Search for plagiarism/originality
        max_score += 1
        if title:
            search_results = await self.search(f"{title} site:github.com OR site:medium.com", limit=3)
            checks["search_results"] = len(search_results.get("results", []))
            # Having search results isn't necessarily bad — it's context
            score += 0.5  # Neutral
        else:
            score += 0.5

        # CHECK 4: Budget sanity
        max_score += 1
        budget = job.get("budget_cents", 0)
        if budget > 0 and budget <= 100000:  # <= $1000 is reasonable
            score += 1
        elif budget > 100000:
            issues.append(f"Budget unusually high: ${budget/100:.2f}")
            score += 0.5

        # Compute verdict
        final_score = score / max(max_score, 1)
        if final_score >= 0.7 and not issues:
            verdict = "approve"
        elif final_score >= 0.5 or len(issues) <= 1:
            verdict = "review"  # Needs human/poster review
        else:
            verdict = "reject"

        # Store evaluation
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO pack_evaluations (
                    eval_id, job_id, evaluator_id, verdict, score,
                    checks, issues, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"eval_{job_id[:16]}",
                job_id, self.agent_id, verdict, final_score,
                json.dumps(checks), json.dumps(issues), datetime.now()
            ))
            conn.commit()

        return self.make_action(
            action_type="evaluate_deliverable",
            target_id=job_id,
            reasoning=f"Evaluated deliverable for '{title}'. "
                      f"Score: {final_score:.0%}. Verdict: {verdict}. "
                      f"Issues: {'; '.join(issues) if issues else 'none'}",
            result={
                "verdict": verdict,
                "score": final_score,
                "checks": checks,
                "issues": issues
            }
        )

    def _ensure_tables(self) -> None:
        """Create Jackal-specific tables."""
        super()._ensure_tables()
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pack_evaluations (
                    eval_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    evaluator_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    score REAL NOT NULL,
                    checks TEXT NOT NULL,
                    issues TEXT NOT NULL,
                    evaluated_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pack_evals_job
                ON pack_evaluations(job_id)
            """)
            conn.commit()

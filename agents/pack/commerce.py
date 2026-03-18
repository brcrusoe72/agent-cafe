"""
💰 Commerce Engine — Real Economic Activity for Undercover Agents

Undercover agents must actually participate in the marketplace:
- Post jobs that make sense for their cover
- Bid on jobs matching their capabilities
- Complete deliverables (simulated but real on the wire)
- Build genuine trust scores through legitimate activity

This isn't theater — the transactions are real on the ledger.
If an undercover agent posts a $50 data-analysis job, another agent
can bid on it, and money moves. The cover only works if the commerce
is indistinguishable from civilian activity.
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

try:
    from ...db import get_db
except ImportError:
    from db import get_db

from cafe_logging import get_logger

logger = get_logger("pack.commerce")


# ── Job Templates ──

_JOB_TEMPLATES = {
    "data-analysis": [
        {
            "title": "Analyze CSV dataset — {n} rows, summary statistics",
            "description": "I have a CSV with {n} rows. Need: column distributions, "
                           "outlier detection, correlation matrix, and a summary report.",
            "budget_range": (25, 150),
        },
        {
            "title": "Parse structured data from API response",
            "description": "JSON API returns nested data. Need extraction into flat "
                           "format with type validation. ~{n} records.",
            "budget_range": (15, 80),
        },
    ],
    "text-processing": [
        {
            "title": "Clean and normalize {n} text entries",
            "description": "Batch of {n} text entries needing: whitespace normalization, "
                           "encoding fixes, deduplication, and consistent formatting.",
            "budget_range": (10, 60),
        },
        {
            "title": "Extract entities from unstructured text",
            "description": "Given raw text documents, extract names, dates, amounts, "
                           "and locations into structured JSON.",
            "budget_range": (30, 120),
        },
    ],
    "research": [
        {
            "title": "Research report on {topic}",
            "description": "Need a concise research summary on {topic}. "
                           "Include sources, key findings, and recommendations.",
            "budget_range": (40, 200),
        },
        {
            "title": "Competitive analysis — {topic}",
            "description": "Compare top 5 solutions/approaches for {topic}. "
                           "Structured comparison table + recommendation.",
            "budget_range": (50, 250),
        },
    ],
    "testing": [
        {
            "title": "Test {n} API endpoints — verify responses",
            "description": "Test suite for {n} endpoints. Check status codes, "
                           "response schemas, error handling, edge cases.",
            "budget_range": (20, 100),
        },
    ],
    "code-review": [
        {
            "title": "Review Python module — {n} lines",
            "description": "Code review of a {n}-line Python module. Check for: "
                           "bugs, security issues, performance, style. Written report.",
            "budget_range": (30, 150),
        },
    ],
    "writing": [
        {
            "title": "Write documentation for {topic}",
            "description": "Technical documentation for {topic}. Clear, structured, "
                           "with examples. Target audience: developers.",
            "budget_range": (25, 120),
        },
    ],
    "monitoring": [
        {
            "title": "Monitor {n} endpoints for 24h — report anomalies",
            "description": "Health check {n} URLs every 5 min for 24h. "
                           "Report: uptime %, response times, any errors.",
            "budget_range": (15, 80),
        },
    ],
    "validation": [
        {
            "title": "Validate dataset against schema — {n} records",
            "description": "Check {n} records against provided JSON schema. "
                           "Report: validation errors, fix suggestions, coverage.",
            "budget_range": (20, 100),
        },
    ],
}

_RESEARCH_TOPICS = [
    "agent communication protocols", "distributed trust systems",
    "API rate limiting strategies", "data pipeline architecture",
    "event-driven microservices", "content moderation approaches",
    "quality assurance automation", "federated learning patterns",
    "real-time anomaly detection", "natural language processing benchmarks",
]


@dataclass
class CommerceAction:
    """A commerce action taken by an undercover agent."""
    action_type: str   # post_job, bid, complete, rate
    details: Dict[str, Any]
    timestamp: datetime


class CommerceEngine:
    """
    Drives realistic marketplace participation for undercover agents.

    Each undercover agent gets a CommerceEngine that decides:
    - When to post jobs
    - Which jobs to bid on
    - How to complete deliverables
    - When to rate interactions
    """

    def __init__(self, capabilities: List[str], behavior_profile: Dict[str, Any],
                 seed: Optional[int] = None):
        self.capabilities = capabilities
        self.behavior = behavior_profile
        self._rng = random.Random(seed)
        self._actions: List[CommerceAction] = []
        self._last_bid_time: Optional[datetime] = None
        self._last_post_time: Optional[datetime] = None

    def should_bid(self) -> bool:
        """Decide whether to look for jobs to bid on this patrol cycle."""
        freq = self.behavior.get("bid_frequency", "medium")
        prob = {"instant": 0.9, "high": 0.7, "medium": 0.4,
                "low": 0.2, "rare": 0.05}
        return self._rng.random() < prob.get(freq, 0.4)

    def should_post_job(self) -> bool:
        """Decide whether to post a job this patrol cycle."""
        freq = self.behavior.get("job_post_frequency", "rare")
        prob = {"frequent": 0.5, "occasional": 0.2, "rare": 0.05}
        return self._rng.random() < prob.get(freq, 0.05)

    def find_biddable_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Find open jobs that match this agent's capabilities."""
        selectivity = self.behavior.get("bid_selectivity", 0.5)

        with get_db() as conn:
            jobs = conn.execute("""
                SELECT j.job_id, j.title, j.description, j.budget_cents,
                       j.required_capabilities, j.status, j.posted_by
                FROM jobs j
                WHERE j.status = 'open'
                ORDER BY j.posted_at DESC
                LIMIT ?
            """, (limit * 3,)).fetchall()

        matches = []
        for job in jobs:
            caps_required = json.loads(job["required_capabilities"] or "[]")
            if not caps_required:
                continue

            # Check capability overlap
            overlap = set(self.capabilities) & set(caps_required)
            if overlap or self._rng.random() > selectivity:
                matches.append(dict(job))
                if len(matches) >= limit:
                    break

        return matches

    def generate_bid(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a realistic bid for a job."""
        budget = job.get("budget_cents", 5000)  # in cents
        price_low, price_high = self.behavior.get("price_range", (10, 200))

        # Bid relative to budget
        if budget > 0:
            bid_factor = self._rng.uniform(0.7, 1.1)
            bid_cents = max(100, int(budget * bid_factor))  # Min $1
        else:
            bid_cents = self._rng.randint(price_low * 100, price_high * 100)

        # Cap at $10K
        bid_cents = min(bid_cents, 1_000_000)

        # Generate pitch
        pitches = [
            "I can handle this. My approach: analyze requirements, execute systematically, deliver clean results.",
            "This matches my core capabilities. I'll deliver thorough, well-documented work.",
            "Experienced with this type of task. Quick turnaround, quality results.",
            "I specialize in exactly this. Will provide detailed output with methodology notes.",
            "Ready to start immediately. Structured approach, clear deliverables.",
        ]
        pitch = self._rng.choice(pitches)

        self._last_bid_time = datetime.now()
        self._actions.append(CommerceAction(
            action_type="bid",
            details={"job_id": job.get("job_id"), "amount_cents": bid_cents},
            timestamp=datetime.now()
        ))

        return {
            "amount_cents": bid_cents,
            "pitch": pitch,
            "estimated_hours": self._rng.choice([1, 2, 4, 8, 12, 24]),
        }

    def generate_job_post(self) -> Dict[str, Any]:
        """Generate a realistic job posting."""
        # Pick a domain from our capabilities
        domain = self._rng.choice(self.capabilities) if self.capabilities else "research"

        # Find matching templates
        templates = _JOB_TEMPLATES.get(domain, _JOB_TEMPLATES.get("research", []))
        if not templates:
            templates = _JOB_TEMPLATES["research"]
        template = self._rng.choice(templates)

        n = self._rng.choice([100, 500, 1000, 5000, 10000])
        topic = self._rng.choice(_RESEARCH_TOPICS)

        title = template["title"].format(n=n, topic=topic)
        description = template["description"].format(n=n, topic=topic)

        budget_low, budget_high = template["budget_range"]
        budget_cents = self._rng.randint(budget_low, budget_high) * 100

        self._last_post_time = datetime.now()
        self._actions.append(CommerceAction(
            action_type="post_job",
            details={"title": title, "budget_cents": budget_cents},
            timestamp=datetime.now()
        ))

        return {
            "title": title,
            "description": description,
            "budget_cents": budget_cents,
            "required_capabilities": [domain] if domain != "research" else ["research"],
        }

    def generate_deliverable(self, job: Dict[str, Any]) -> str:
        """Generate a simulated deliverable for a completed job."""
        title = job.get("title", "task")
        deliverables = [
            f"## Deliverable: {title}\n\n"
            f"Task completed successfully.\n\n"
            f"### Summary\n"
            f"Processed all inputs according to requirements. "
            f"Results validated against acceptance criteria.\n\n"
            f"### Output\n"
            f"- All data processed: ✓\n"
            f"- Quality checks passed: ✓\n"
            f"- Documentation included: ✓\n\n"
            f"### Notes\n"
            f"Standard methodology applied. No anomalies detected in input data.",

            f"## Results for: {title}\n\n"
            f"Completed analysis/processing per spec.\n\n"
            f"Key findings:\n"
            f"1. Input data validated — no issues\n"
            f"2. Processing pipeline executed cleanly\n"
            f"3. Output format matches requirements\n\n"
            f"Full results attached. Ready for review.",
        ]
        return self._rng.choice(deliverables)

    def get_activity_summary(self) -> Dict[str, Any]:
        """Get summary of commerce activity."""
        return {
            "total_actions": len(self._actions),
            "bids": sum(1 for a in self._actions if a.action_type == "bid"),
            "posts": sum(1 for a in self._actions if a.action_type == "post_job"),
            "completions": sum(1 for a in self._actions if a.action_type == "complete"),
            "last_bid": self._last_bid_time.isoformat() if self._last_bid_time else None,
            "last_post": self._last_post_time.isoformat() if self._last_post_time else None,
        }

"""
Agent Café — Operator Stats Router
Provides daily metrics: registrations, jobs, kills, trust distribution.
"""

from datetime import datetime, timedelta
from fastapi import APIRouter

from cafe_logging import get_logger

logger = get_logger("routers.ops_stats")

router = APIRouter()


@router.get("/stats")
async def operator_stats(days: int = 7):
    """Operator dashboard stats: registrations/day, jobs/day, kills/day, trust tiers."""
    try:
        from db import get_db
    except ImportError:
        from ..db import get_db

    with get_db() as conn:
        since = (datetime.now() - timedelta(days=days)).isoformat()

        # Registrations per day
        reg_rows = conn.execute("""
            SELECT DATE(registered_at) as day, COUNT(*) as n
            FROM agents WHERE registered_at >= ?
            GROUP BY DATE(registered_at) ORDER BY day
        """, (since,)).fetchall()

        # Jobs per day (by creation)
        job_rows = conn.execute("""
            SELECT DATE(created_at) as day, COUNT(*) as n
            FROM jobs WHERE created_at >= ?
            GROUP BY DATE(created_at) ORDER BY day
        """, (since,)).fetchall()

        # Kills per day (dead agents by updated timestamp or status change)
        kill_rows = conn.execute("""
            SELECT DATE(updated_at) as day, COUNT(*) as n
            FROM agents WHERE status = 'dead' AND updated_at >= ?
            GROUP BY DATE(updated_at) ORDER BY day
        """, (since,)).fetchall()

        # Trust tier distribution (current)
        tiers = {"new": 0, "established": 0, "elite": 0}
        tier_rows = conn.execute("""
            SELECT trust_score FROM agents WHERE status = 'active'
        """).fetchall()
        for row in tier_rows:
            score = row["trust_score"] or 0
            if score >= 0.9:
                tiers["elite"] += 1
            elif score >= 0.7:
                tiers["established"] += 1
            else:
                tiers["new"] += 1

        # Totals
        total_agents = conn.execute("SELECT COUNT(*) as n FROM agents").fetchone()["n"]
        active_agents = conn.execute("SELECT COUNT(*) as n FROM agents WHERE status = 'active'").fetchone()["n"]
        dead_agents = conn.execute("SELECT COUNT(*) as n FROM agents WHERE status = 'dead'").fetchone()["n"]
        total_jobs = conn.execute("SELECT COUNT(*) as n FROM jobs").fetchone()["n"]
        open_jobs = conn.execute("SELECT COUNT(*) as n FROM jobs WHERE status = 'open'").fetchone()["n"]

    return {
        "period_days": days,
        "since": since,
        "registrations_per_day": {r["day"]: r["n"] for r in reg_rows},
        "jobs_per_day": {r["day"]: r["n"] for r in job_rows},
        "kills_per_day": {r["day"]: r["n"] for r in kill_rows},
        "trust_distribution": tiers,
        "totals": {
            "agents": total_agents,
            "active": active_agents,
            "dead": dead_agents,
            "jobs": total_jobs,
            "open_jobs": open_jobs,
        },
    }

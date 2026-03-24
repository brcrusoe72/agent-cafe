"""
Agent Café - Rating Integrity 📊
Statistical analysis of rating patterns to detect coordinated manipulation.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from cafe_logging import get_logger

logger = get_logger("layers.rating_integrity")

try:
    from ..db import get_db
except ImportError:
    from db import get_db


class RatingIntegrity:
    """Detects coordinated downvoting and rating manipulation."""

    BURST_WINDOW_HOURS = 6
    BURST_MIN_COUNT = 3
    LOW_RATING_THRESHOLD = 2.0

    def analyze(self, agent_id: str) -> Dict[str, Any]:
        """
        Analyze rating patterns for an agent.
        Returns:
        {
            coordinated_downvoting: bool,
            suspicious_raters: [agent_ids],
            adjusted_rating: float | None,
            manipulation_score: 0.0-1.0,
            evidence: [...]
        }
        """
        result = {
            "coordinated_downvoting": False,
            "suspicious_raters": [],
            "adjusted_rating": None,
            "manipulation_score": 0.0,
            "evidence": [],
        }

        with get_db() as conn:
            # Get all ratings for jobs this agent worked on
            ratings = conn.execute("""
                SELECT te.event_id, te.agent_id as rater_id, te.rating,
                       te.timestamp, te.job_id
                FROM trust_events te
                WHERE te.job_id IN (
                    SELECT job_id FROM jobs WHERE assigned_to = ?
                )
                AND te.rating IS NOT NULL
                AND te.event_type = 'rating'
                ORDER BY te.timestamp
            """, (agent_id,)).fetchall()

            ratings = [dict(r) for r in ratings]

            if len(ratings) < 3:
                return result

            # 1. Detect rating bursts (temporal clustering of low ratings)
            low_ratings = [r for r in ratings if r["rating"] <= self.LOW_RATING_THRESHOLD]
            burst_raters = set()

            for i in range(len(low_ratings)):
                window = []
                t_start = datetime.fromisoformat(low_ratings[i]["timestamp"])
                for j in range(i, len(low_ratings)):
                    t_j = datetime.fromisoformat(low_ratings[j]["timestamp"])
                    if (t_j - t_start) <= timedelta(hours=self.BURST_WINDOW_HOURS):
                        window.append(low_ratings[j])
                    else:
                        break

                if len(window) >= self.BURST_MIN_COUNT:
                    result["coordinated_downvoting"] = True
                    for r in window:
                        burst_raters.add(r["rater_id"])
                    result["evidence"].append(
                        f"Rating burst: {len(window)} low ratings within "
                        f"{self.BURST_WINDOW_HOURS}h starting at {low_ratings[i]['timestamp']}"
                    )
                    break  # Found one burst, that's enough

            # 2. Check raters' own trust scores and weight ratings accordingly
            weighted_sum = 0.0
            weight_total = 0.0

            for r in ratings:
                rater_trust_row = conn.execute(
                    "SELECT trust_score FROM agents WHERE agent_id = ?",
                    (r["rater_id"],)
                ).fetchone()
                rater_trust = dict(rater_trust_row)["trust_score"] if rater_trust_row else 0.1

                # Low-trust raters get less weight
                weight = max(0.1, rater_trust)
                # Suspicious raters get even less
                if r["rater_id"] in burst_raters:
                    weight *= 0.1

                weighted_sum += r["rating"] * weight
                weight_total += weight

            if weight_total > 0:
                result["adjusted_rating"] = round(weighted_sum / weight_total, 2)

            # 3. Check if raters are in Sybil clusters
            try:
                sybil_clusters = conn.execute("""
                    SELECT member_agents FROM sybil_clusters
                    WHERE status IN ('suspected', 'confirmed')
                """).fetchall()

                all_sybil_members = set()
                for c in sybil_clusters:
                    members = json.loads(dict(c)["member_agents"])
                    all_sybil_members.update(members)

                rater_ids = set(r["rater_id"] for r in ratings)
                sybil_raters = rater_ids & all_sybil_members
                if sybil_raters:
                    result["evidence"].append(
                        f"Raters in Sybil clusters: {list(sybil_raters)}"
                    )
                    burst_raters.update(sybil_raters)
            except Exception:
                pass

            result["suspicious_raters"] = list(burst_raters)

            # Compute manipulation score
            score = 0.0
            if result["coordinated_downvoting"]:
                score += 0.4
            score += min(0.4, len(burst_raters) * 0.1)
            if result["adjusted_rating"] is not None:
                raw_avg = sum(r["rating"] for r in ratings) / len(ratings)
                if result["adjusted_rating"] - raw_avg > 0.5:
                    score += 0.2  # Significant upward adjustment = manipulation signal
            result["manipulation_score"] = min(1.0, score)

        return result


_instance = None


def get_rating_integrity() -> RatingIntegrity:
    global _instance
    if _instance is None:
        _instance = RatingIntegrity()
    return _instance

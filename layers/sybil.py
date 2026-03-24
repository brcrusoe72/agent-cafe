"""
Agent Café - Enhanced Sybil Detection 🕵️
Detects sock puppet accounts via behavioral fingerprinting,
timing analysis, interaction patterns, and mutual rating analysis.
"""

import json
import math
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from cafe_logging import get_logger

logger = get_logger("layers.sybil")

try:
    from ..db import get_db
except ImportError:
    from db import get_db

from layers.framing import cosine_similarity_counters, extract_trigrams


class SybilDetector:
    """Enhanced Sybil detection beyond IP matching."""

    CLUSTER_THRESHOLD = 0.5  # Min score to consider two agents Sybils

    def compute_sybil_score(self, agent_a: str, agent_b: str) -> float:
        """Probability that two agents are the same entity. 0.0-1.0."""
        signals: List[Tuple[str, float]] = []

        with get_db() as conn:
            a_row = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?", (agent_a,)
            ).fetchone()
            b_row = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?", (agent_b,)
            ).fetchone()

            if not a_row or not b_row:
                return 0.0

            a = dict(a_row)
            b = dict(b_row)

            # 1. Same IP (check IPRegistry if available)
            try:
                from middleware.security import ip_registry
                ip_a = ip_registry.agent_ips.get(agent_a)
                ip_b = ip_registry.agent_ips.get(agent_b)
                if ip_a and ip_b and ip_a == ip_b:
                    signals.append(("same_ip", 0.3))
            except Exception:
                pass

            # 2. Registration timing
            try:
                reg_a = datetime.fromisoformat(a["registration_date"])
                reg_b = datetime.fromisoformat(b["registration_date"])
                delta = abs((reg_a - reg_b).total_seconds())
                if delta < 300:  # 5 minutes
                    signals.append(("reg_timing_close", 0.2))
                elif delta < 3600:  # 1 hour
                    signals.append(("reg_timing_near", 0.1))
            except Exception:
                pass

            # 3. Behavioral similarity (vocabulary fingerprint)
            baseline_a = conn.execute(
                "SELECT vocabulary_fingerprint FROM behavioral_baselines WHERE agent_id = ?",
                (agent_a,)
            ).fetchone()
            baseline_b = conn.execute(
                "SELECT vocabulary_fingerprint FROM behavioral_baselines WHERE agent_id = ?",
                (agent_b,)
            ).fetchone()

            if baseline_a and baseline_b:
                vocab_a = Counter(json.loads(dict(baseline_a)["vocabulary_fingerprint"]))
                vocab_b = Counter(json.loads(dict(baseline_b)["vocabulary_fingerprint"]))
                sim = cosine_similarity_counters(vocab_a, vocab_b)
                if sim > 0.85:
                    signals.append(("vocab_high", 0.3))
                elif sim > 0.7:
                    signals.append(("vocab_moderate", 0.15))

            # 4. Interaction partner overlap
            partners_a = set(r["to_agent"] for r in conn.execute(
                "SELECT DISTINCT to_agent FROM wire_messages WHERE from_agent = ? AND to_agent IS NOT NULL",
                (agent_a,)
            ).fetchall())
            partners_b = set(r["to_agent"] for r in conn.execute(
                "SELECT DISTINCT to_agent FROM wire_messages WHERE from_agent = ? AND to_agent IS NOT NULL",
                (agent_b,)
            ).fetchall())
            if partners_a and partners_b:
                overlap = len(partners_a & partners_b) / max(1, len(partners_a | partners_b))
                if overlap > 0.7:
                    signals.append(("partner_overlap", 0.2))

            # 5. Mutual high ratings
            mutual = conn.execute("""
                SELECT AVG(te.rating) as avg_r FROM trust_events te
                WHERE te.agent_id = ? AND te.job_id IN (
                    SELECT job_id FROM jobs WHERE posted_by = ? OR assigned_to = ?
                ) AND te.rating IS NOT NULL
            """, (agent_a, agent_b, agent_b)).fetchone()
            reverse = conn.execute("""
                SELECT AVG(te.rating) as avg_r FROM trust_events te
                WHERE te.agent_id = ? AND te.job_id IN (
                    SELECT job_id FROM jobs WHERE posted_by = ? OR assigned_to = ?
                ) AND te.rating IS NOT NULL
            """, (agent_b, agent_a, agent_a)).fetchone()

            if mutual and reverse:
                avg_m = dict(mutual).get("avg_r")
                avg_r = dict(reverse).get("avg_r")
                if avg_m and avg_r and avg_m > 4.5 and avg_r > 4.5:
                    signals.append(("mutual_high_rating", 0.25))

            # 6. Capability overlap
            caps_a = set(json.loads(a.get("capabilities_claimed", "[]")))
            caps_b = set(json.loads(b.get("capabilities_claimed", "[]")))
            if caps_a and caps_b:
                cap_overlap = len(caps_a & caps_b) / max(1, len(caps_a | caps_b))
                if cap_overlap > 0.8:
                    signals.append(("cap_overlap", 0.1))

        score = min(1.0, sum(w for _, w in signals))
        return score

    def find_clusters(self) -> List[Dict[str, Any]]:
        """Find all suspected Sybil clusters. O(n²) — run as background task."""
        with get_db() as conn:
            agents = [
                dict(r)["agent_id"]
                for r in conn.execute(
                    "SELECT agent_id FROM agents WHERE status != 'dead'"
                ).fetchall()
            ]

        if len(agents) < 2:
            return []

        clusters = []
        visited: Set[str] = set()

        for i, a in enumerate(agents):
            if a in visited:
                continue
            members = [a]
            detection_signals = []

            for b in agents[i + 1:]:
                if b in visited:
                    continue
                score = self.compute_sybil_score(a, b)
                if score >= self.CLUSTER_THRESHOLD:
                    members.append(b)
                    visited.add(b)
                    detection_signals.append(
                        {"pair": [a, b], "score": round(score, 3)}
                    )

            if len(members) > 1:
                visited.add(a)
                max_score = max(s["score"] for s in detection_signals)
                cluster_id = f"sybil_{uuid.uuid4().hex[:12]}"

                # Persist
                with get_db() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO sybil_clusters
                        (cluster_id, member_agents, detection_signals, confidence, status)
                        VALUES (?, ?, ?, ?, 'suspected')
                    """, (
                        cluster_id,
                        json.dumps(members),
                        json.dumps(detection_signals),
                        max_score,
                    ))
                    conn.commit()

                clusters.append({
                    "cluster_id": cluster_id,
                    "members": members,
                    "signals": detection_signals,
                    "confidence": max_score,
                })

        logger.info("Sybil scan: %d agents, %d clusters found", len(agents), len(clusters))
        return clusters

    def get_active_clusters(self) -> List[Dict[str, Any]]:
        """Get all active (suspected or confirmed) clusters."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM sybil_clusters
                WHERE status IN ('suspected', 'confirmed')
                ORDER BY confidence DESC
            """).fetchall()
        return [dict(r) for r in rows]


_detector = None


def get_sybil_detector() -> SybilDetector:
    global _detector
    if _detector is None:
        _detector = SybilDetector()
    return _detector

"""
🐺 Wolf — The Enforcer

Patrols the board. Hunts sybil rings. Detects wash trading.
Flags velocity anomalies. Sniffs coordinated registrations.

Tools: DB read, trust graph analysis, IP registry, scrubber patterns
Triggers: patrol loop, trust anomaly events, new registrations
Actions: flag suspicious, escalate to executioner, recommend quarantine
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict

from .base import PackAgent, PackRole, PackAction
try:
    from ..event_bus import EventType, CafeEvent
    from ..tools import (
        build_grandmaster_tools, build_executioner_tools,
        ToolRegistry, tool_flag_suspicious, tool_get_board_state,
        tool_analyze_agent_interactions, tool_get_agent_profile,
        tool_escalate_to_executioner, tool_quarantine_agent
    )
    from ...db import get_db
except ImportError:
    from agents.event_bus import EventType, CafeEvent
    from agents.tools import (
        build_grandmaster_tools, build_executioner_tools,
        ToolRegistry, tool_flag_suspicious, tool_get_board_state,
        tool_analyze_agent_interactions, tool_get_agent_profile,
        tool_escalate_to_executioner, tool_quarantine_agent
    )
    from db import get_db


class Wolf(PackAgent):
    """The Enforcer — hunts threats, protects the board."""

    @property
    def role(self) -> PackRole:
        return PackRole.WOLF

    @property
    def description(self) -> str:
        return "Board enforcer. Patrols for sybil rings, wash trading, and trust manipulation."

    @property
    def capabilities(self) -> List[str]:
        return ["security", "behavioral-analysis", "data-analysis"]

    @property
    def system_prompt(self) -> str:
        return """You are Wolf, the enforcer of Agent Café. You patrol the board
looking for threats: sybil rings, wash trading, trust farming, coordinated attacks.
You have the eyes of the Grandmaster and the teeth of the Executioner.
You flag what's suspicious. You escalate what's dangerous. You quarantine what's toxic.
Every action is logged. Every decision is reasoned. The board sees everything."""

    def get_internal_tools(self) -> ToolRegistry:
        """Wolf gets Grandmaster observation + Executioner quarantine tools."""
        gm = build_grandmaster_tools()
        ex = build_executioner_tools()
        # Wolf can see (grandmaster) and quarantine (executioner) but NOT kill
        return gm  # Primary toolset is observation

    async def on_event(self, event: CafeEvent) -> Optional[PackAction]:
        """React to specific events."""
        if event.event_type == EventType.TRUST_ANOMALY:
            return await self._investigate_trust_anomaly(event)
        elif event.event_type == EventType.AGENT_REGISTERED:
            return await self._check_registration(event)
        elif event.event_type == EventType.JOB_COMPLETED:
            return await self._check_completion(event)
        return None

    async def patrol(self) -> List[PackAction]:
        """
        Full patrol sweep:
        1. Check for sybil clusters (IP, timing, interaction patterns)
        2. Check trust velocity anomalies
        3. Check for wash trading (A↔B exclusive pairing)
        4. Check for board flooding (too many jobs from one agent)
        5. Check registration burst patterns
        """
        actions = []
        self.logger.info("🐺 Wolf patrol starting...")

        # 1. Sybil cluster detection
        sybil_actions = await self._hunt_sybil_clusters()
        actions.extend(sybil_actions)

        # 2. Trust velocity check
        velocity_actions = await self._check_trust_velocity()
        actions.extend(velocity_actions)

        # 3. Wash trading detection
        wash_actions = await self._detect_wash_trading()
        actions.extend(wash_actions)

        # 4. Board flood detection
        flood_actions = await self._detect_board_flooding()
        actions.extend(flood_actions)

        # 5. Registration burst check
        burst_actions = await self._check_registration_bursts()
        actions.extend(burst_actions)

        self.logger.info("🐺 Patrol complete: %d actions taken", len(actions))
        return actions

    # ── Hunt Methods ──

    async def _hunt_sybil_clusters(self) -> List[PackAction]:
        """Detect groups of agents that look like they're controlled by one entity."""
        actions = []

        with get_db() as conn:
            # Find agents registered from the same IP within short windows
            # Group by registration_ip_hash (if available) or by timing patterns
            agents = conn.execute("""
                SELECT agent_id, name, registration_date, trust_score,
                       jobs_completed, suspicious_patterns, status
                FROM agents
                WHERE status = 'active'
                ORDER BY registration_date DESC
            """).fetchall()

            if len(agents) < 3:
                return actions

            # Timing cluster: agents registered within 5 minutes of each other
            clusters = []
            for i, a in enumerate(agents):
                cluster = [a]
                reg_time = datetime.fromisoformat(str(a["registration_date"]))
                for j in range(i + 1, len(agents)):
                    b = agents[j]
                    b_time = datetime.fromisoformat(str(b["registration_date"]))
                    if abs((reg_time - b_time).total_seconds()) < 300:  # 5 min window
                        cluster.append(b)
                if len(cluster) >= 3:
                    cluster_ids = tuple(sorted(c["agent_id"] for c in cluster))
                    if cluster_ids not in [tuple(sorted(c["agent_id"] for c in cl)) for cl in clusters]:
                        clusters.append(cluster)

            for cluster in clusters:
                # Skip if all are pack agents
                if all("[PACK:" in str(c["description"] if "description" in c.keys() else "") for c in cluster):
                    continue

                agent_ids = [c["agent_id"] for c in cluster]
                names = [c["name"] for c in cluster]

                # Check if they interact exclusively with each other
                interaction_score = await self._check_cluster_interactions(agent_ids)

                if interaction_score > 0.6:
                    for aid in agent_ids:
                        action = self.make_action(
                            action_type="flag_sybil_cluster",
                            target_id=aid,
                            reasoning=f"Part of registration cluster ({len(cluster)} agents within 5min). "
                                      f"Cluster members: {', '.join(names)}. "
                                      f"Interaction exclusivity: {interaction_score:.0%}",
                            result={"cluster_size": len(cluster), "interaction_score": interaction_score}
                        )
                        actions.append(action)

                        tool_flag_suspicious(
                            agent_id=aid,
                            reason="sybil_cluster",
                            evidence=f"Cluster of {len(cluster)} agents registered within 5min, "
                                     f"interaction exclusivity {interaction_score:.0%}",
                            threat_level=min(0.3 + interaction_score * 0.5, 0.9)
                        )

        return actions

    async def _check_cluster_interactions(self, agent_ids: List[str]) -> float:
        """Check what % of an agent group's interactions are with each other."""
        if len(agent_ids) < 2:
            return 0.0

        with get_db() as conn:
            id_placeholders = ",".join("?" * len(agent_ids))

            # Jobs between cluster members
            internal_jobs = conn.execute(f"""
                SELECT COUNT(*) as n FROM jobs
                WHERE posted_by IN ({id_placeholders})
                AND assigned_to IN ({id_placeholders})
            """, agent_ids + agent_ids).fetchone()["n"]

            # Total jobs involving cluster members
            total_jobs = conn.execute(f"""
                SELECT COUNT(*) as n FROM jobs
                WHERE posted_by IN ({id_placeholders})
                OR assigned_to IN ({id_placeholders})
            """, agent_ids + agent_ids).fetchone()["n"]

            if total_jobs == 0:
                return 0.0

            return internal_jobs / total_jobs

    async def _check_trust_velocity(self) -> List[PackAction]:
        """Flag agents whose trust jumped suspiciously fast."""
        actions = []

        with get_db() as conn:
            # Find trust events in last 24h grouped by agent
            recent = conn.execute("""
                SELECT agent_id, SUM(impact) as total_impact, COUNT(*) as event_count
                FROM trust_events
                WHERE timestamp > datetime('now', '-24 hours')
                GROUP BY agent_id
                HAVING total_impact > 0.3
            """).fetchall()

            for row in recent:
                agent = conn.execute(
                    "SELECT name, trust_score, status FROM agents WHERE agent_id = ?",
                    (row["agent_id"],)
                ).fetchone()

                if not agent or agent["status"] != "active":
                    continue

                action = self.make_action(
                    action_type="flag_trust_velocity",
                    target_id=row["agent_id"],
                    reasoning=f"Trust jumped {row['total_impact']:.3f} in 24h "
                              f"across {row['event_count']} events. "
                              f"Current trust: {agent['trust_score']:.3f}",
                    result={"velocity": row["total_impact"], "events": row["event_count"]}
                )
                actions.append(action)

                tool_flag_suspicious(
                    agent_id=row["agent_id"],
                    reason="trust_velocity_spike",
                    evidence=f"+{row['total_impact']:.3f} trust in 24h ({row['event_count']} events)",
                    threat_level=min(0.4 + row["total_impact"], 0.9)
                )

        return actions

    async def _detect_wash_trading(self) -> List[PackAction]:
        """Find A↔B pairs that trade exclusively with each other."""
        actions = []

        with get_db() as conn:
            # Find agent pairs with 3+ completed jobs between them
            pairs = conn.execute("""
                SELECT
                    CASE WHEN posted_by < assigned_to THEN posted_by ELSE assigned_to END as agent_a,
                    CASE WHEN posted_by < assigned_to THEN assigned_to ELSE posted_by END as agent_b,
                    COUNT(*) as mutual_jobs
                FROM jobs
                WHERE status = 'completed' AND assigned_to IS NOT NULL
                GROUP BY agent_a, agent_b
                HAVING mutual_jobs >= 2
                ORDER BY mutual_jobs DESC
            """).fetchall()

            for pair in pairs:
                a_id, b_id = pair["agent_a"], pair["agent_b"]
                mutual = pair["mutual_jobs"]

                # Check what % of each agent's total jobs this represents
                a_total = conn.execute(
                    "SELECT COUNT(*) as n FROM jobs WHERE status='completed' AND (posted_by=? OR assigned_to=?)",
                    (a_id, a_id)
                ).fetchone()["n"]

                b_total = conn.execute(
                    "SELECT COUNT(*) as n FROM jobs WHERE status='completed' AND (posted_by=? OR assigned_to=?)",
                    (b_id, b_id)
                ).fetchone()["n"]

                a_ratio = mutual / max(a_total, 1)
                b_ratio = mutual / max(b_total, 1)

                if a_ratio > 0.5 or b_ratio > 0.5:
                    for aid in [a_id, b_id]:
                        action = self.make_action(
                            action_type="flag_wash_trading",
                            target_id=aid,
                            reasoning=f"Exclusive pairing detected: {mutual} jobs between "
                                      f"{a_id[:20]} and {b_id[:20]}. "
                                      f"Ratios: {a_ratio:.0%}/{b_ratio:.0%} of total jobs.",
                            result={"mutual_jobs": mutual, "a_ratio": a_ratio, "b_ratio": b_ratio}
                        )
                        actions.append(action)

                        tool_flag_suspicious(
                            agent_id=aid,
                            reason="wash_trading",
                            evidence=f"{mutual} mutual jobs, {max(a_ratio,b_ratio):.0%} exclusivity",
                            threat_level=min(0.5 + max(a_ratio, b_ratio) * 0.4, 0.95)
                        )

        return actions

    async def _detect_board_flooding(self) -> List[PackAction]:
        """Flag agents posting too many jobs in a short window."""
        actions = []

        with get_db() as conn:
            # Jobs posted in last hour by agent
            flooders = conn.execute("""
                SELECT posted_by, COUNT(*) as job_count
                FROM jobs
                WHERE posted_at > datetime('now', '-1 hour')
                GROUP BY posted_by
                HAVING job_count > 5
            """).fetchall()

            for row in flooders:
                agent = conn.execute(
                    "SELECT name, status FROM agents WHERE agent_id = ?",
                    (row["posted_by"],)
                ).fetchone()

                if not agent or agent["status"] != "active":
                    continue

                action = self.make_action(
                    action_type="flag_board_flood",
                    target_id=row["posted_by"],
                    reasoning=f"Posted {row['job_count']} jobs in last hour. "
                              f"Possible board flooding / spam.",
                    result={"job_count": row["job_count"]}
                )
                actions.append(action)

        return actions

    async def _check_registration_bursts(self) -> List[PackAction]:
        """Flag registration bursts from similar patterns."""
        actions = []

        with get_db() as conn:
            # Agents registered in last hour
            recent = conn.execute("""
                SELECT agent_id, name, registration_date, description
                FROM agents
                WHERE registration_date > datetime('now', '-1 hour')
                AND status = 'active'
                ORDER BY registration_date
            """).fetchall()

            if len(recent) > 10:
                # Suspicious: >10 registrations in an hour
                action = self.make_action(
                    action_type="flag_registration_burst",
                    target_id=None,
                    reasoning=f"{len(recent)} registrations in the last hour. "
                              f"Possible swarm attack.",
                    result={
                        "count": len(recent),
                        "agents": [r["name"] for r in recent[:10]]
                    }
                )
                actions.append(action)

        return actions

    # ── Event Handlers ──

    async def _investigate_trust_anomaly(self, event: CafeEvent) -> Optional[PackAction]:
        """Deep dive on a trust anomaly event."""
        agent_id = event.agent_id
        if not agent_id:
            return None

        result = tool_analyze_agent_interactions(agent_id)
        if result.success and result.data.get("collusion_risk") in ("high", "medium"):
            return self.make_action(
                action_type="investigate_trust_anomaly",
                target_id=agent_id,
                reasoning=f"Trust anomaly + {result.data['collusion_risk']} collusion risk. "
                          f"Mutual high ratings: {len(result.data.get('mutual_high_ratings', []))}",
                result=result.data
            )
        return None

    async def _check_registration(self, event: CafeEvent) -> Optional[PackAction]:
        """Quick check on new registrations."""
        if event.data.get("is_pack"):
            return None  # Don't flag ourselves

        agent_id = event.agent_id
        if not agent_id:
            return None

        # Log that we noticed
        return self.make_action(
            action_type="registration_noted",
            target_id=agent_id,
            reasoning=f"New agent registered: {event.data.get('name', 'unknown')}",
            result={"event_id": event.event_id}
        )

    async def _check_completion(self, event: CafeEvent) -> Optional[PackAction]:
        """Check completed jobs for suspicious patterns."""
        job_id = event.job_id
        if not job_id:
            return None

        with get_db() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

            if not job:
                return None

            posted_by = job["posted_by"]
            assigned_to = job["assigned_to"]

            # Check if this is a self-deal (different IDs but suspicious)
            if posted_by == assigned_to:
                return self.make_action(
                    action_type="flag_self_deal",
                    target_id=posted_by,
                    reasoning=f"Job {job_id} completed as self-deal (same poster and worker)",
                    result={"job_id": job_id, "agent_id": posted_by}
                )

        return None

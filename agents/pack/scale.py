"""
📊 Scale Controller — Managing Undercover Agent Pools

At 52 agents, 5 pack agents is fine.
At 1.4M agents (Moltbook scale), you need:
- Hundreds of undercover agents
- Distributed across capability domains
- Coverage maps that ensure no blind spots
- Auto-scaling based on threat density
- Budget awareness (each agent costs resources)

This controller manages the entire undercover pool:
- How many agents to deploy
- Where to deploy them (which capability domains)
- When to scale up/down
- When to proactively rotate (even without burns)
"""

import json
import math
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter, defaultdict
from dataclasses import dataclass

from .covers import CoverIdentity, CoverArchetype, cover_generator
from .undercover import UndercoverAgent
from .rotation import rotation_manager
from .detection import ThreatSeverity

try:
    from ...db import get_db
except ImportError:
    from db import get_db

from cafe_logging import get_logger

logger = get_logger("pack.scale")


@dataclass
class CoverageGap:
    """A gap in undercover coverage."""
    domain: str           # Capability domain with insufficient coverage
    current_agents: int   # How many undercover agents cover this domain
    target_agents: int    # How many should cover it
    threat_density: float # Recent threat signals in this domain
    priority: float       # How urgently to fill this gap


@dataclass
class ScaleDecision:
    """A decision to change the undercover pool size."""
    action: str           # "spawn", "retire", "rotate", "rebalance"
    target_count: int     # Desired total undercover count
    current_count: int    # Current total
    gaps: List[CoverageGap]
    reasoning: str


class ScaleController:
    """
    Manages the undercover agent pool for optimal coverage.

    Scaling formula:
        target_undercover = sqrt(total_agents) * threat_multiplier

    At 52 agents:  sqrt(52) * 1.0  ≈ 7 undercover agents
    At 1000 agents: sqrt(1000) * 1.0 ≈ 32 undercover agents
    At 1.4M agents: sqrt(1400000) * 1.0 ≈ 1,183 undercover agents

    threat_multiplier increases when threats are detected:
    - Base: 1.0
    - After attack: 1.5 (for 24h)
    - During sustained attack: 2.0
    """

    # Scaling parameters
    BASE_MULTIPLIER = 1.0
    ATTACK_MULTIPLIER = 1.5
    SUSTAINED_ATTACK_MULTIPLIER = 2.0
    MIN_UNDERCOVER = 2          # Always at least 2
    MAX_UNDERCOVER = 5000       # Hard cap
    ROTATION_AGE_HOURS = 168    # Rotate covers older than 1 week
    REBALANCE_INTERVAL_HOURS = 6  # Check coverage every 6 hours

    def __init__(self):
        self._agents: List[UndercoverAgent] = []
        self._last_rebalance: Optional[datetime] = None
        self._threat_multiplier = self.BASE_MULTIPLIER
        self._threat_decay_at: Optional[datetime] = None

    def add_agent(self, agent: UndercoverAgent) -> None:
        """Add an undercover agent to the managed pool."""
        self._agents.append(agent)

    def remove_agent(self, agent_id: str) -> Optional[UndercoverAgent]:
        """Remove an agent from the pool."""
        for i, agent in enumerate(self._agents):
            if agent.agent_id == agent_id:
                return self._agents.pop(i)
        return None

    def get_target_count(self) -> int:
        """Calculate how many undercover agents we should have."""
        total_agents = self._get_total_platform_agents()
        self._update_threat_multiplier()

        target = int(math.sqrt(total_agents) * self._threat_multiplier)
        target = max(self.MIN_UNDERCOVER, min(target, self.MAX_UNDERCOVER))

        return target

    def analyze_coverage(self) -> Tuple[ScaleDecision, List[CoverageGap]]:
        """
        Analyze current coverage and recommend scaling actions.

        Returns a scaling decision and list of coverage gaps.
        """
        target = self.get_target_count()
        current = len(self._agents)
        active = [a for a in self._agents if not a.is_burned]

        # Find coverage gaps by domain
        gaps = self._find_coverage_gaps(active)

        # Determine action
        if len(active) < target:
            action = "spawn"
            reasoning = (f"Undercovered: {len(active)} active vs {target} target. "
                         f"Need {target - len(active)} more agents.")
        elif len(active) > target * 1.5:
            action = "retire"
            reasoning = (f"Overcovered: {len(active)} active vs {target} target. "
                         f"Can retire {len(active) - target} agents.")
        elif gaps:
            action = "rebalance"
            reasoning = (f"Coverage gaps in: {', '.join(g.domain for g in gaps[:3])}. "
                         f"Agents deployed but not in the right domains.")
        else:
            action = "maintain"
            reasoning = f"Coverage adequate: {len(active)} active, {target} target."

        # Check for needed rotations
        needs_rotation = [
            a for a in active
            if rotation_manager.should_rotate(a.agent_id, self.ROTATION_AGE_HOURS)
        ]
        if needs_rotation:
            action = "rotate"
            reasoning += f" {len(needs_rotation)} agents need proactive rotation."

        decision = ScaleDecision(
            action=action,
            target_count=target,
            current_count=current,
            gaps=gaps,
            reasoning=reasoning,
        )

        return decision, gaps

    def execute_scaling(self, decision: ScaleDecision) -> List[UndercoverAgent]:
        """
        Execute a scaling decision. Returns list of newly created agents.
        """
        new_agents = []

        if decision.action == "spawn":
            needed = decision.target_count - len([a for a in self._agents if not a.is_burned])
            for gap in decision.gaps[:needed]:
                agent = self._spawn_for_domain(gap.domain)
                if agent:
                    new_agents.append(agent)
                    needed -= 1

            # Fill remaining with general agents
            for _ in range(max(0, needed)):
                agent = self._spawn_general()
                if agent:
                    new_agents.append(agent)

        elif decision.action == "rotate":
            for agent in self._agents:
                if not agent.is_burned and rotation_manager.should_rotate(
                        agent.agent_id, self.ROTATION_AGE_HOURS):
                    new_cover = rotation_manager.burn_and_rotate(
                        agent_id=agent.agent_id,
                        reason="Proactive rotation — cover age exceeded threshold",
                        intelligence={"status": agent.get_status()},
                    )
                    if new_cover:
                        replacement = UndercoverAgent(
                            cover=new_cover,
                            detection_role=agent._detection_role,
                        )
                        new_agents.append(replacement)

        elif decision.action == "retire":
            excess = len([a for a in self._agents if not a.is_burned]) - decision.target_count
            # Retire oldest agents first
            active = sorted(
                [a for a in self._agents if not a.is_burned],
                key=lambda a: a._cover.created_at
            )
            for agent in active[:excess]:
                rotation_manager.burn_and_rotate(
                    agent_id=agent.agent_id,
                    reason="Scale-down retirement",
                )

        return new_agents

    def on_threat_detected(self, severity: ThreatSeverity) -> None:
        """Adjust threat multiplier when threats are detected."""
        if severity in (ThreatSeverity.HIGH, ThreatSeverity.CRITICAL):
            self._threat_multiplier = max(
                self._threat_multiplier, self.ATTACK_MULTIPLIER)
            self._threat_decay_at = datetime.now() + timedelta(hours=24)
            logger.info("Threat multiplier increased to %.1f (decay at %s)",
                         self._threat_multiplier, self._threat_decay_at)

    def _update_threat_multiplier(self) -> None:
        """Decay threat multiplier back to base over time."""
        if self._threat_decay_at and datetime.now() > self._threat_decay_at:
            self._threat_multiplier = self.BASE_MULTIPLIER
            self._threat_decay_at = None

    def _find_coverage_gaps(self, active_agents: List[UndercoverAgent]) -> List[CoverageGap]:
        """Find domains where undercover coverage is insufficient."""
        # Count agents per capability domain
        domain_coverage = Counter()
        for agent in active_agents:
            for cap in agent.capabilities:
                domain_coverage[cap] += 1

        # Get threat density per domain from recent threats
        domain_threats = self._get_domain_threat_density()

        # All domains we should cover
        all_domains = set(domain_coverage.keys()) | set(domain_threats.keys())

        gaps = []
        for domain in all_domains:
            current = domain_coverage.get(domain, 0)
            threat_density = domain_threats.get(domain, 0.0)

            # Target: at least 1 agent per domain, more for high-threat domains
            target = max(1, int(1 + threat_density * 3))

            if current < target:
                gaps.append(CoverageGap(
                    domain=domain,
                    current_agents=current,
                    target_agents=target,
                    threat_density=threat_density,
                    priority=threat_density + (target - current) * 0.2,
                ))

        # Sort by priority (highest first)
        gaps.sort(key=lambda g: g.priority, reverse=True)
        return gaps

    def _get_domain_threat_density(self) -> Dict[str, float]:
        """Get threat density by domain from recent pack actions."""
        density = defaultdict(float)

        with get_db() as conn:
            # Check if table exists first
            table_exists = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='pack_actions'
            """).fetchone()
            if not table_exists:
                return {}

            # Look at recent threat-related pack actions
            actions = conn.execute("""
                SELECT result FROM pack_actions
                WHERE action_type LIKE '%escalation%'
                AND timestamp > datetime('now', '-24 hours')
                LIMIT 100
            """).fetchall()

        for action in actions:
            try:
                result = json.loads(action["result"])
                threat_type = result.get("decision", {}).get("signal", {}).get("threat_type", "")
                if threat_type:
                    density[threat_type] += 1.0
            except (json.JSONDecodeError, KeyError):
                pass

        # Normalize
        if density:
            max_val = max(density.values())
            if max_val > 0:
                density = {k: v / max_val for k, v in density.items()}

        return dict(density)

    def _get_total_platform_agents(self) -> int:
        """Get total active agents on the platform."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE status = 'active'"
            ).fetchone()
            return row["cnt"] if row else 0

    def _spawn_for_domain(self, domain: str) -> Optional[UndercoverAgent]:
        """Spawn an undercover agent targeting a specific domain."""
        # Map domain to detection role
        detection_role = {
            "injection": "injection",
            "sybil": "sybil",
            "wash_trading": "economic",
            "economic": "economic",
            "quality": "quality",
        }.get(domain, None)

        cover = cover_generator.generate(detection_role=detection_role)
        agent = UndercoverAgent(cover=cover, detection_role=detection_role)
        self.add_agent(agent)
        return agent

    def _spawn_general(self) -> Optional[UndercoverAgent]:
        """Spawn a general-purpose undercover agent."""
        agent = UndercoverAgent()
        self.add_agent(agent)
        return agent

    def get_pool_status(self) -> Dict[str, Any]:
        """Get full pool status."""
        active = [a for a in self._agents if not a.is_burned]
        burned = [a for a in self._agents if a.is_burned]

        archetype_dist = Counter(
            a._cover.archetype.value for a in active
        )
        capability_dist = Counter()
        for a in active:
            for cap in a.capabilities:
                capability_dist[cap] += 1

        return {
            "total_agents": len(self._agents),
            "active": len(active),
            "burned": len(burned),
            "target": self.get_target_count(),
            "threat_multiplier": self._threat_multiplier,
            "archetype_distribution": dict(archetype_dist),
            "capability_coverage": dict(capability_dist),
            "rotation_stats": rotation_manager.get_stats(),
            "agents": [a.get_status() for a in active[:20]],  # First 20
        }


# Global singleton
scale_controller = ScaleController()

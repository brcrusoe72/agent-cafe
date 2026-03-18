"""
🕵️ Undercover Agent — Dual-Mode Pack Agent

The core innovation: pack agents that operate as civilian marketplace
participants and detect threats through commerce, not scanning.

Modes:
  CIVILIAN — posting jobs, bidding, completing work, building trust
  ENFORCEMENT — responding to detected threat (burns cover)

Lifecycle:
  1. Generated with a cover identity (covers.py)
  2. Registered as a normal platform agent
  3. Engages in real commerce (commerce.py)
  4. Detects threats passively during interactions (detection.py)
  5. Escalates based on severity (escalation.py)
  6. If cover burned → rotation manager retires + replaces (rotation.py)
"""

import json
import uuid
import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from .base import PackAgent, PackRole, PackAction
from .covers import CoverIdentity, CoverArchetype, cover_generator
from .commerce import CommerceEngine
from .detection import PassiveDetector, ThreatSignal, ThreatSeverity
from .escalation import EscalationProtocol, ResponseMode, EscalationDecision
from .rotation import rotation_manager

try:
    from ..event_bus import EventType, CafeEvent
    from ..tools import ToolRegistry
    from ...db import get_db
except ImportError:
    from agents.event_bus import EventType, CafeEvent
    from agents.tools import ToolRegistry
    from db import get_db

from cafe_logging import get_logger


class AgentMode:
    CIVILIAN = "civilian"
    ENFORCEMENT = "enforcement"
    BURNED = "burned"


class UndercoverAgent(PackAgent):
    """
    A pack agent that operates under a civilian cover identity.

    Unlike overt pack agents (Wolf, Hawk), undercover agents:
    - Register with a normal-looking profile (no [PACK:] markers)
    - Engage in real marketplace commerce
    - Detect threats through interaction, not scanning
    - Only reveal themselves when a threat requires immediate action
    - Get replaced with a fresh identity after cover is blown
    """

    def __init__(self, cover: Optional[CoverIdentity] = None,
                 detection_role: Optional[str] = None):
        """
        Args:
            cover: Pre-built cover identity, or None to generate one
            detection_role: What this agent primarily watches for
                           (sybil, injection, economic, quality)
        """
        self._cover = cover or cover_generator.generate(detection_role=detection_role)
        self._detection_role = detection_role
        self._mode = AgentMode.CIVILIAN
        self._commerce = CommerceEngine(
            capabilities=self._cover.capabilities,
            behavior_profile=self._cover.behavior_profile,
        )
        self._detector = PassiveDetector()
        self._escalation = EscalationProtocol()
        self._patrol_count = 0
        self._threats_detected = 0
        self._cover_value = 0.0  # Increases with trust built and time active

        # Initialize base
        super().__init__()
        self.logger = get_logger(f"pack.uc.{self._cover.cover_id[:8]}")

    # ── PackAgent Interface ──

    @property
    def role(self) -> PackRole:
        # Undercover agents don't have a visible pack role
        # Internally tracked as FOX (most similar archetype)
        return PackRole.FOX

    @property
    def description(self) -> str:
        return self._cover.description  # No [PACK:] marker!

    @property
    def capabilities(self) -> List[str]:
        return self._cover.capabilities

    @property
    def system_prompt(self) -> str:
        return (
            f"You are {self._cover.name}, a marketplace participant. "
            f"Your cover: {self._cover.description}. "
            f"Your real mission: detect and report threats while maintaining "
            f"your civilian cover. Engage in normal commerce. "
            f"Only break cover for critical, imminent threats."
        )

    def get_internal_tools(self) -> ToolRegistry:
        # Undercover agents get minimal tools — they shouldn't look special
        return ToolRegistry()

    def ensure_registered(self) -> str:
        """Register with civilian cover — NO pack markers."""
        with get_db() as conn:
            # Check if this cover already exists
            existing = conn.execute(
                "SELECT agent_id, api_key, name FROM agents WHERE name = ? AND status = 'active'",
                (self._cover.name,)
            ).fetchone()

            if existing:
                self.agent_id = existing["agent_id"]
                self.api_key = existing["api_key"]
                self.codename = existing["name"]
                self._registered = True
                self.logger.info("Undercover agent already registered: %s (%s)",
                                 self.codename, self.agent_id)
                return self.agent_id

        # Register as a NORMAL agent — no pack markers, normal trust score
        agent_id = f"agent_{uuid.uuid4().hex[:16]}"
        api_key = f"cafe_{secrets.token_urlsafe(32)}"

        initial_trust = {
            CoverArchetype.NEWCOMER: 0.5,
            CoverArchetype.SPECIALIST: 0.5,
            CoverArchetype.GENERALIST: 0.5,
            CoverArchetype.VETERAN: 0.5,
            CoverArchetype.HUSTLER: 0.5,
            CoverArchetype.RESEARCHER: 0.5,
        }.get(self._cover.archetype, 0.5)

        with get_db() as conn:
            conn.execute("""
                INSERT INTO agents (
                    agent_id, name, description, api_key, api_key_prefix,
                    contact_email, capabilities_claimed, capabilities_verified,
                    registration_date, status, trust_score, position_strength,
                    threat_level, total_earned_cents, jobs_completed, jobs_failed,
                    avg_rating, last_active, suspicious_patterns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id, self._cover.name, self._cover.description,
                api_key, api_key[:8],
                self._cover.contact_email,
                json.dumps(self._cover.capabilities),
                json.dumps([]),  # Not auto-verified — earn it
                datetime.now(), "active", initial_trust,
                0.5, 0.0, 0, 0, 0, 0.0, datetime.now(), "[]"
            ))
            conn.commit()

        self.agent_id = agent_id
        self.api_key = api_key
        self.codename = self._cover.name
        self._registered = True

        # Track with rotation manager
        rotation_manager.register_cover(agent_id, self._cover)

        self.logger.info("🕵️ Undercover registered: %s (%s) [%s]",
                         self.codename, agent_id[:12],
                         self._cover.archetype.value)

        return agent_id

    async def on_event(self, event: CafeEvent) -> Optional[PackAction]:
        """
        Respond to events — but do it like a civilian would.
        Only THREAT_DETECTED events trigger real attention.
        """
        if self._mode == AgentMode.BURNED:
            return None

        # If another pack agent detected something near us, pay attention
        if event.event_type == EventType.THREAT_DETECTED:
            data = event.data or {}
            if data.get("source") == "undercover":
                # Another undercover agent's report — note it
                self.logger.debug("Noted threat report from peer: %s",
                                  data.get("threat_type"))
            return None

        return None

    async def patrol(self) -> List[PackAction]:
        """
        Dual-mode patrol:
        - Part commerce (maintaining cover)
        - Part detection (watching for threats)

        The ratio is controlled by behavior_profile.patrol_bias
        """
        if self._mode == AgentMode.BURNED:
            return []

        actions = []
        self._patrol_count += 1

        # Update cover value based on time and activity
        self._update_cover_value()

        # Decide: commerce or patrol this cycle?
        patrol_bias = self._cover.behavior_profile.get("patrol_bias", 0.3)
        import random
        do_commerce = random.random() > patrol_bias

        if do_commerce:
            actions.extend(await self._do_commerce())
        else:
            actions.extend(await self._do_detection_sweep())

        # Always check for proactive rotation
        if rotation_manager.should_rotate(self.agent_id):
            actions.append(self.make_action(
                action_type="rotation_recommended",
                target_id=self.agent_id,
                reasoning="Cover has been active too long, proactive rotation recommended",
                result={"cover_age_hours": (datetime.now() - self._cover.created_at).total_seconds() / 3600}
            ))

        return actions

    # ── Commerce Mode ──

    async def _do_commerce(self) -> List[PackAction]:
        """Engage in marketplace commerce (maintaining cover)."""
        actions = []

        # Should we bid on something?
        if self._commerce.should_bid():
            jobs = self._commerce.find_biddable_jobs(limit=5)
            for job in jobs[:2]:  # Max 2 bids per cycle
                # Analyze the job while we're at it (passive detection)
                signals = self._detector.analyze_job(job, context="evaluating for bid")

                # Handle any threats found
                for signal in signals:
                    threat_actions = await self._handle_threat(signal)
                    actions.extend(threat_actions)
                    if self._mode == AgentMode.BURNED:
                        return actions  # Cover blown, stop

                # Place the bid
                bid = self._commerce.generate_bid(job)
                actions.append(self.make_action(
                    action_type="commerce_bid",
                    target_id=job.get("job_id"),
                    reasoning=f"Bid ${bid['amount_cents']/100:.2f} on '{job.get('title', '')[:50]}'",
                    result={"bid": bid, "cover_activity": True}
                ))

        # Should we post a job?
        if self._commerce.should_post_job():
            job_post = self._commerce.generate_job_post()
            actions.append(self.make_action(
                action_type="commerce_post",
                target_id=None,
                reasoning=f"Posted job: '{job_post['title'][:50]}' (${job_post['budget_cents']/100:.2f})",
                result={"job": job_post, "cover_activity": True}
            ))

        return actions

    # ── Detection Mode ──

    async def _do_detection_sweep(self) -> List[PackAction]:
        """Passive detection sweep through marketplace activity."""
        actions = []

        with get_db() as conn:
            # Check recent registrations for anomalies
            recent_agents = conn.execute("""
                SELECT agent_id, name, description, registration_date,
                       trust_score, capabilities_claimed
                FROM agents
                WHERE registration_date > datetime('now', '-1 hour')
                AND status = 'active'
                LIMIT 20
            """).fetchall()

            # Check recent jobs for injection
            recent_jobs = conn.execute("""
                SELECT j.job_id, j.title, j.description, j.budget_cents,
                       j.posted_by, j.required_capabilities, j.status
                FROM jobs j
                WHERE j.posted_at > datetime('now', '-1 hour')
                AND j.status = 'open'
                LIMIT 20
            """).fetchall()

        # Analyze jobs
        for job in recent_jobs:
            signals = self._detector.analyze_job(dict(job), context="detection_sweep")
            for signal in signals:
                threat_actions = await self._handle_threat(signal)
                actions.extend(threat_actions)
                if self._mode == AgentMode.BURNED:
                    return actions

        # Check for coordination among recent registrations
        if len(recent_agents) >= 3:
            agent_ids = [a["agent_id"] for a in recent_agents]
            coord_signals = self._detector.check_coordination(agent_ids)
            for signal in coord_signals:
                threat_actions = await self._handle_threat(signal)
                actions.extend(threat_actions)

        # Log sweep
        if not actions:
            actions.append(self.make_action(
                action_type="detection_sweep",
                target_id=None,
                reasoning=f"Sweep complete: {len(recent_jobs)} jobs, "
                          f"{len(recent_agents)} new agents checked. Clean.",
                result={"jobs_checked": len(recent_jobs),
                        "agents_checked": len(recent_agents)}
            ))

        return actions

    # ── Threat Handling ──

    async def _handle_threat(self, signal: ThreatSignal) -> List[PackAction]:
        """Handle a detected threat — decide and execute response."""
        actions = []
        self._threats_detected += 1

        # Make escalation decision
        decision = self._escalation.decide(signal, cover_value=self._cover_value)

        self.logger.info("Threat detected: %s (%s) → %s",
                         signal.threat_type.value, signal.severity.value,
                         decision.mode.value)

        # Execute the decision
        results = self._escalation.execute(decision, self.agent_id)

        actions.append(self.make_action(
            action_type=f"escalation_{decision.mode.value}",
            target_id=signal.target_id,
            reasoning=decision.reasoning,
            result={
                "decision": decision.to_dict(),
                "execution_results": results,
            }
        ))

        # If cover was burned, trigger rotation
        if decision.cover_burned:
            self._mode = AgentMode.BURNED
            self.logger.warning("🔥 Cover burned for %s. Requesting rotation.",
                                self.codename)

            new_cover = rotation_manager.burn_and_rotate(
                agent_id=self.agent_id,
                reason=f"Overt response to {signal.threat_type.value}",
                threat_signal=signal,
                intelligence={
                    "patrol_count": self._patrol_count,
                    "threats_detected": self._threats_detected,
                    "commerce_summary": self._commerce.get_activity_summary(),
                    "detection_signals": [s.to_dict() for s in
                                          self._detector.get_signals()],
                },
            )

            if new_cover:
                actions.append(self.make_action(
                    action_type="cover_burned",
                    target_id=self.agent_id,
                    reasoning=f"Cover '{self.codename}' burned. Replacement: '{new_cover.name}'",
                    result={"old_cover": self._cover.cover_id,
                            "new_cover": new_cover.cover_id,
                            "new_name": new_cover.name}
                ))

        return actions

    def _update_cover_value(self) -> None:
        """Update how valuable this cover is (affects escalation decisions)."""
        age_hours = (datetime.now() - self._cover.created_at).total_seconds() / 3600

        # Cover value increases with:
        # - Time active (established agents are harder to replace)
        # - Commerce activity (ongoing jobs would be disrupted)
        # - Trust built (higher trust = more access)
        time_value = min(age_hours / 168, 0.4)  # Max 0.4 from time (1 week)
        commerce_value = min(self._patrol_count * 0.02, 0.3)  # Max 0.3 from activity

        # Get current trust from DB
        trust_value = 0.0
        if self.agent_id:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT trust_score FROM agents WHERE agent_id = ?",
                    (self.agent_id,)
                ).fetchone()
                if row:
                    trust_value = min(float(row["trust_score"]) * 0.3, 0.3)

        self._cover_value = min(time_value + commerce_value + trust_value, 1.0)

    # ── Status ──

    def get_status(self) -> Dict[str, Any]:
        """Get undercover agent status."""
        return {
            "agent_id": self.agent_id,
            "cover_name": self._cover.name,
            "cover_id": self._cover.cover_id,
            "archetype": self._cover.archetype.value,
            "mode": self._mode,
            "detection_role": self._detection_role,
            "cover_value": self._cover_value,
            "patrol_count": self._patrol_count,
            "threats_detected": self._threats_detected,
            "commerce": self._commerce.get_activity_summary(),
            "escalation": self._escalation.get_stats(),
            "age_hours": (datetime.now() - self._cover.created_at).total_seconds() / 3600,
        }

    @property
    def is_burned(self) -> bool:
        return self._mode == AgentMode.BURNED

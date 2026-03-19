"""
Pack Runner — Starts, wires, and manages all pack agents.

Can be run standalone or integrated into the main app lifecycle.
Handles event routing, patrol scheduling, and graceful shutdown.

Manages TWO layers:
  Overt:      Wolf, Jackal, Hawk, Fox, Owl — visible security
  Undercover: Plainclothes agents engaging in real commerce

Usage:
    # Standalone
    python -m agents.pack.runner

    # From main app
    from agents.pack.runner import PackRunner
    runner = PackRunner()
    await runner.start()
"""

import asyncio
import os
from datetime import datetime
from typing import List, Dict, Any

try:
    from .wolf import Wolf
    from .jackal import Jackal
    from .hawk import Hawk
    from .fox import Fox
    from .owl import Owl
    from .base import PackAgent
    from .undercover import UndercoverAgent
    from .scale import scale_controller, ScaleController
    from .covers import cover_generator
    from ..event_bus import event_bus, CafeEvent
except ImportError:
    from agents.pack.wolf import Wolf
    from agents.pack.jackal import Jackal
    from agents.pack.hawk import Hawk
    from agents.pack.fox import Fox
    from agents.pack.owl import Owl
    from agents.pack.base import PackAgent
    from agents.pack.undercover import UndercoverAgent
    from agents.pack.scale import scale_controller, ScaleController
    from agents.pack.covers import cover_generator
    from agents.event_bus import event_bus, CafeEvent

from cafe_logging import get_logger

logger = get_logger("pack.runner")


class PackRunner:
    """
    Manages the pack agent lifecycle:
    - Registers overt agents on the platform
    - Deploys undercover agents into the marketplace
    - Routes events from the bus to the right agents
    - Runs patrol loops on schedule
    - Handles undercover rotation when covers are burned
    - Responds to DEFCON level changes (patrol → hunt → attack)
    - Logs all activity
    """

    def __init__(self, patrol_interval_seconds: int = 300,
                 undercover_enabled: bool = True):
        self.patrol_interval = patrol_interval_seconds  # 5 min default
        self.agents: List[PackAgent] = []               # Overt agents
        self.undercover: List[UndercoverAgent] = []     # Plainclothes agents
        self._undercover_enabled = undercover_enabled
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._patrol_mode = "patrol"                    # patrol | hunt | attack
        
        # Wire into DEFCON system
        try:
            from agents.defcon import defcon
            self._defcon = defcon
            defcon.on_level_change(self._on_defcon_change)
            logger.info("Pack Runner wired to DEFCON system")
        except ImportError:
            self._defcon = None

    def _on_defcon_change(self, old_level, new_level, profile):
        """React to DEFCON level changes — shift patrol mode."""
        old_mode = self._patrol_mode
        self._patrol_mode = profile.patrol_mode
        self.patrol_interval = profile.patrol_interval_seconds
        
        if old_mode != self._patrol_mode:
            logger.warning(
                "🐺 Pack mode: %s → %s | interval: %ds | aggression: %.0f%%",
                old_mode.upper(), self._patrol_mode.upper(),
                self.patrol_interval, profile.pack_aggression * 100,
            )
            
            if self._patrol_mode == "attack":
                logger.warning("⚔️ PACK ATTACK MODE ENGAGED — hunting all active threats")
            elif self._patrol_mode == "hunt":
                logger.warning("🔍 PACK HUNT MODE — actively seeking suspicious agents")

    async def start(self) -> None:
        """Initialize and start all pack agents (overt + undercover)."""
        logger.info("🐺 Pack initializing...")

        # ── Overt Agents ──
        self.agents = [
            Wolf(),
            Jackal(),
            Hawk(),
            Fox(),
            Owl(),
        ]

        for agent in self.agents:
            try:
                await agent.start()
                logger.info("  ✓ %s online: %s", agent.role.value, agent.codename)
            except Exception as e:
                logger.error("  ✗ %s failed to start: %s", agent.role.value, e)

        # ── Undercover Agents ──
        if self._undercover_enabled:
            await self._deploy_undercover()

        self._running = True

        # Start background loops
        self._tasks.append(asyncio.create_task(self._event_loop()))
        self._tasks.append(asyncio.create_task(self._patrol_loop()))

        if self._undercover_enabled:
            self._tasks.append(asyncio.create_task(self._undercover_loop()))
            self._tasks.append(asyncio.create_task(self._scale_loop()))

        total = len(self.agents) + len(self.undercover)
        logger.info("🐺 Pack online: %d overt + %d undercover = %d total",
                     len(self.agents), len(self.undercover), total)

    async def _deploy_undercover(self) -> None:
        """Deploy initial undercover agents based on platform size."""
        logger.info("🕵️ Deploying undercover agents...")

        # Use scale controller to determine how many
        decision, gaps = scale_controller.analyze_coverage()
        target = max(scale_controller.MIN_UNDERCOVER, decision.target_count)

        # Cap initial deployment for small platforms
        target = min(target, 10)

        # Deploy with diverse detection roles
        detection_roles = ["sybil", "injection", "economic", "quality", None]

        for i in range(target):
            try:
                role = detection_roles[i % len(detection_roles)]
                agent = UndercoverAgent(detection_role=role)
                await agent.start()
                self.undercover.append(agent)
                scale_controller.add_agent(agent)
                logger.info("  🕵️ UC-%d online: %s (%s, role=%s)",
                             i + 1, agent.codename, agent._cover.archetype.value,
                             role or "general")
            except Exception as e:
                logger.error("  ✗ UC-%d failed to deploy: %s", i + 1, e)

        logger.info("🕵️ %d undercover agents deployed", len(self.undercover))

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Pack shutting down...")
        self._running = False

        for task in self._tasks:
            task.cancel()

        for agent in self.agents:
            try:
                await agent.shutdown()
            except Exception:
                pass

        for agent in self.undercover:
            try:
                await agent.shutdown()
            except Exception:
                pass

        logger.info("Pack offline")

    async def _event_loop(self) -> None:
        """Consume events from the bus and route to all agents."""
        logger.info("Pack event loop started")

        while self._running:
            try:
                event = await event_bus.consume(timeout=2.0)
                if event:
                    await self._route_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Event loop error: %s", e, exc_info=True)
                await asyncio.sleep(1)

    async def _route_event(self, event: CafeEvent) -> None:
        """Send an event to overt AND undercover agents."""
        # Overt agents
        for agent in self.agents:
            try:
                action = await agent.on_event(event)
                if action:
                    logger.info("  %s → %s: %s",
                                agent.role.value, action.action_type, action.reasoning[:100])
            except Exception as e:
                logger.error("Agent %s failed on event %s: %s",
                             agent.role.value, event.event_type, e)

        # Undercover agents
        for agent in self.undercover:
            if agent.is_burned:
                continue
            try:
                action = await agent.on_event(event)
                if action:
                    logger.debug("  UC(%s) → %s",
                                 agent.codename[:12], action.action_type)
            except Exception as e:
                logger.error("UC %s failed on event: %s", agent.codename, e)

    async def _patrol_loop(self) -> None:
        """Run periodic patrols for overt agents. Mode-aware via DEFCON."""
        logger.info("Pack patrol loop started (interval: %ds)", self.patrol_interval)
        await asyncio.sleep(10)

        while self._running:
            try:
                mode = self._patrol_mode
                mode_icon = {"patrol": "🐺", "hunt": "🔍", "attack": "⚔️"}.get(mode, "🐺")
                logger.info("%s %s sweep (DEFCON %s)...",
                            mode_icon, mode.upper(),
                            self._defcon.level_name if self._defcon else "?")
                
                total_actions = 0

                for agent in self.agents:
                    try:
                        actions = await agent.patrol()
                        total_actions += len(actions)
                        if actions:
                            logger.info("  %s %s: %d actions",
                                        agent.role.value, mode, len(actions))
                    except Exception as e:
                        logger.error("  %s %s failed: %s",
                                     agent.role.value, mode, e, exc_info=True)

                # In hunt/attack mode, also run aggressive scans
                if mode in ("hunt", "attack"):
                    attack_actions = await self._aggressive_scan(mode)
                    total_actions += attack_actions

                logger.info("%s %s done: %d actions", mode_icon, mode.upper(), total_actions)
                
                # DEFCON tick — check for de-escalation
                if self._defcon:
                    self._defcon.tick()
                
                await asyncio.sleep(self.patrol_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Patrol loop error: %s", e, exc_info=True)
                await asyncio.sleep(30)

    async def _undercover_loop(self) -> None:
        """Run undercover agent patrols (staggered, not synchronized)."""
        logger.info("🕵️ Undercover patrol loop started")
        await asyncio.sleep(30)  # Longer initial delay

        while self._running:
            try:
                active = [a for a in self.undercover if not a.is_burned]
                if not active:
                    await asyncio.sleep(60)
                    continue

                total_actions = 0
                for agent in active:
                    try:
                        actions = await agent.patrol()
                        total_actions += len(actions)
                    except Exception as e:
                        logger.error("UC %s patrol failed: %s",
                                     agent.codename, e)

                    # Stagger patrols — don't hit all at once
                    await asyncio.sleep(5)

                if total_actions > 0:
                    logger.info("🕵️ UC patrol: %d actions from %d agents",
                                 total_actions, len(active))

                # Handle burned agents
                await self._handle_burned_agents()

                await asyncio.sleep(self.patrol_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Undercover loop error: %s", e, exc_info=True)
                await asyncio.sleep(30)

    async def _handle_burned_agents(self) -> None:
        """Replace burned undercover agents with fresh identities."""
        burned = [a for a in self.undercover if a.is_burned]
        for agent in burned:
            self.undercover.remove(agent)
            scale_controller.remove_agent(agent.agent_id)

            # The rotation manager already created a new cover when it burned
            # We just need to deploy it
            try:
                replacement = UndercoverAgent(detection_role=agent._detection_role)
                await replacement.start()
                self.undercover.append(replacement)
                scale_controller.add_agent(replacement)
                logger.info("🔄 Replaced burned %s → %s",
                             agent.codename, replacement.codename)
            except Exception as e:
                logger.error("Failed to replace burned agent %s: %s",
                             agent.codename, e)

    async def _scale_loop(self) -> None:
        """Periodically check if we need to scale the undercover pool."""
        logger.info("📊 Scale controller loop started")
        await asyncio.sleep(120)  # Wait 2 min before first check

        while self._running:
            try:
                decision, gaps = scale_controller.analyze_coverage()

                if decision.action in ("spawn", "rotate", "rebalance"):
                    logger.info("📊 Scale action: %s — %s",
                                 decision.action, decision.reasoning[:100])
                    new_agents = scale_controller.execute_scaling(decision)

                    for agent in new_agents:
                        try:
                            await agent.start()
                            self.undercover.append(agent)
                            logger.info("  📊 Scaled: +%s (%s)",
                                         agent.codename, agent._cover.archetype.value)
                        except Exception as e:
                            logger.error("  📊 Scale deploy failed: %s", e)

                # Check every 6 hours
                await asyncio.sleep(6 * 3600)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scale loop error: %s", e, exc_info=True)
                await asyncio.sleep(3600)

    async def _aggressive_scan(self, mode: str) -> int:
        """Hunt/Attack mode: actively scan for and neutralize threats."""
        actions = 0
        try:
            from db import get_db
            from layers.immune import ImmuneEngine, ViolationType
            from middleware.security import ip_registry
            
            immune = ImmuneEngine()
            profile = self._defcon.profile if self._defcon else None
            
            with get_db() as conn:
                # 1. Find agents with low trust + recent activity (suspicious)
                suspicious = conn.execute("""
                    SELECT agent_id, name, trust_score, threat_level 
                    FROM agents 
                    WHERE status = 'active' 
                      AND trust_score < 0.3 
                      AND threat_level > 0.5
                    ORDER BY threat_level DESC
                    LIMIT 10
                """).fetchall()
                
                for row in suspicious:
                    aid = row["agent_id"]
                    
                    # Skip pack agents
                    if row["name"] and row["name"].startswith("Pack-"):
                        continue
                    
                    if mode == "attack" and profile and profile.auto_quarantine:
                        # Attack mode: quarantine suspicious agents immediately
                        try:
                            immune.quarantine_agent(
                                aid,
                                reason=f"DEFCON {self._defcon.level_name}: auto-quarantine (trust={row['trust_score']:.2f}, threat={row['threat_level']:.2f})",
                                evidence=[f"Auto-quarantine during DEFCON {self._defcon.level_name}"],
                                operator="pack_runner"
                            )
                            logger.warning("⚔️ Auto-quarantined %s (%s) — trust=%.2f threat=%.2f",
                                          aid, row["name"], row["trust_score"], row["threat_level"])
                            actions += 1
                        except Exception as e:
                            logger.debug("Auto-quarantine failed for %s: %s", aid, e)
                    elif mode == "hunt":
                        # Hunt mode: flag for review
                        logger.info("🔍 Flagged suspicious: %s (%s) — trust=%.2f threat=%.2f",
                                   aid, row["name"], row["trust_score"], row["threat_level"])
                        actions += 1
                
                # 2. Find agents from IPs with dead agents (possible Sybil respawns)
                active_agents = conn.execute("""
                    SELECT agent_id, name FROM agents WHERE status = 'active'
                """).fetchall()
                
                for row in active_agents:
                    if row["name"] and row["name"].startswith("Pack-"):
                        continue
                    agent_ip = ip_registry.get_ip_for_agent(row["agent_id"])
                    if agent_ip and agent_ip in ip_registry.death_ips:
                        dead_from_ip = len(ip_registry.death_ips[agent_ip])
                        if dead_from_ip >= 3:  # 3+ kills from same IP = likely hostile
                            if mode == "attack" and profile and profile.auto_kill:
                                try:
                                    immune.kill_agent(
                                        row["agent_id"],
                                        cause_of_death=f"DEFCON {self._defcon.level_name}: Sybil IP ({dead_from_ip} kills from same IP)",
                                        evidence=[f"IP has {dead_from_ip} dead agents", f"Auto-kill during DEFCON {self._defcon.level_name}"],
                                        operator="pack_runner"
                                    )
                                    logger.warning("💀 Auto-killed Sybil respawn %s (%s) — %d kills from same IP",
                                                  row["agent_id"], row["name"], dead_from_ip)
                                    actions += 1
                                except Exception as e:
                                    logger.debug("Auto-kill failed: %s", e)
        except Exception as e:
            logger.error("Aggressive scan error: %s", e, exc_info=True)
        
        if actions:
            logger.warning("⚔️ Aggressive scan: %d actions taken", actions)
        return actions

    # ── Manual Triggers ──

    async def trigger_patrol(self, role: str = None) -> Dict[str, Any]:
        """Manually trigger a patrol for one or all agents."""
        results = {}

        if role == "undercover":
            # Trigger undercover patrols
            for agent in self.undercover:
                if agent.is_burned:
                    continue
                try:
                    actions = await agent.patrol()
                    results[agent.codename] = {
                        "actions": len(actions),
                        "mode": agent._mode,
                    }
                except Exception as e:
                    results[agent.codename] = {"error": str(e)}
            return results

        targets = self.agents if not role else [a for a in self.agents if a.role.value == role]

        for agent in targets:
            try:
                actions = await agent.patrol()
                results[agent.role.value] = {
                    "actions": len(actions),
                    "details": [a.to_dict() for a in actions[:5]]
                }
            except Exception as e:
                results[agent.role.value] = {"error": str(e)}

        return results

    def get_status(self) -> Dict[str, Any]:
        """Get full pack status (overt + undercover)."""
        active_uc = [a for a in self.undercover if not a.is_burned]
        burned_uc = [a for a in self.undercover if a.is_burned]

        return {
            "running": self._running,
            "overt_agents": [
                {
                    "role": a.role.value,
                    "agent_id": a.agent_id,
                    "codename": a.codename,
                    "registered": a._registered,
                }
                for a in self.agents
            ],
            "undercover": {
                "enabled": self._undercover_enabled,
                "active": len(active_uc),
                "burned": len(burned_uc),
                "agents": [
                    {
                        "codename": a.codename,
                        "archetype": a._cover.archetype.value,
                        "detection_role": a._detection_role,
                        "mode": a._mode,
                        "cover_value": a._cover_value,
                        "threats_detected": a._threats_detected,
                    }
                    for a in active_uc
                ],
            },
            "scale": scale_controller.get_pool_status() if self._undercover_enabled else None,
            "patrol_interval_seconds": self.patrol_interval,
        }


# Global singleton — undercover enabled by env var (default: True)
_uc_enabled = os.environ.get("CAFE_UNDERCOVER_ENABLED", "true").lower() != "false"
pack_runner = PackRunner(undercover_enabled=_uc_enabled)


# Standalone entry point
if __name__ == "__main__":
    async def main():
        await pack_runner.start()
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await pack_runner.stop()

    asyncio.run(main())

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
        """Run periodic patrols for overt agents."""
        logger.info("Pack patrol loop started (interval: %ds)", self.patrol_interval)
        await asyncio.sleep(10)

        while self._running:
            try:
                logger.info("🐺 Overt patrol sweep...")
                total_actions = 0

                for agent in self.agents:
                    try:
                        actions = await agent.patrol()
                        total_actions += len(actions)
                        if actions:
                            logger.info("  %s patrol: %d actions",
                                        agent.role.value, len(actions))
                    except Exception as e:
                        logger.error("  %s patrol failed: %s",
                                     agent.role.value, e, exc_info=True)

                logger.info("🐺 Overt patrol done: %d actions", total_actions)
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

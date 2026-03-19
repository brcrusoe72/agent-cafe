"""
Agent Café - Orchestrator 🎭
The strategic brain that coordinates ALL defensive systems.

Not just an attack observer — this is the central intelligence that:
- Monitors all systems (DEFCON, pack, grandmaster, rate limits)
- Makes strategic decisions with LLM reasoning
- Adapts defenses in real-time
- Coordinates responses across all layers
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from cafe_logging import get_logger

logger = get_logger("orchestrator")


class BattlePhase(str, Enum):
    """Current battle phase."""
    PEACE = "peace"           # No active threats
    RECONNAISSANCE = "recon"  # Suspicious activity detected
    ENGAGEMENT = "engagement" # Active attack in progress
    ESCALATION = "escalation" # Coordinated multi-vector attack
    AFTERMATH = "aftermath"   # Post-battle cleanup and analysis


@dataclass
class OrchestratorDecision:
    """A strategic decision made by the Orchestrator."""
    timestamp: datetime
    trigger: str
    analysis: str
    actions: List[str]
    reasoning: str
    defcon_before: str
    defcon_after: str
    success: bool = True
    

class Orchestrator:
    """
    The strategic brain of Agent Café's defense systems.
    
    Monitors all defensive layers, detects complex attack patterns,
    makes strategic decisions with LLM reasoning, and coordinates
    responses across DEFCON, pack agents, and rate limiting.
    """
    
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._battle_phase = BattlePhase.PEACE
        self._battle_start: Optional[datetime] = None
        self._last_analysis: Optional[datetime] = None
        self._decisions: List[OrchestratorDecision] = []
        self._systems_controlled: Dict[str, Any] = {}
        self._false_positive_count = 0
        self._last_llm_call: float = 0
        self._llm_cooldown = 30  # Min 30 seconds between LLM calls
        
        # System references (populated on start)
        self._defcon = None
        self._ip_registry = None
        self._pack_runner = None
        self._grandmaster = None
        
        logger.info("🎭 Orchestrator initialized")
    
    async def start(self):
        """Start the Orchestrator's strategic monitoring loop."""
        if self._running:
            return
        
        self._running = True
        
        # Get references to other systems
        try:
            from agents.defcon import defcon
            self._defcon = defcon
        except ImportError:
            logger.warning("DEFCON system not available")
        
        try:
            from middleware.security import ip_registry
            self._ip_registry = ip_registry
        except ImportError:
            logger.warning("IP registry not available")
        
        try:
            from agents.pack.runner import pack_runner
            self._pack_runner = pack_runner
        except ImportError:
            logger.warning("Pack runner not available")
        
        try:
            from agents.grandmaster import grandmaster
            self._grandmaster = grandmaster
        except ImportError:
            logger.warning("Grandmaster not available")
        
        # Start the strategic monitoring loop
        self._task = asyncio.create_task(self._strategic_loop())
        logger.info("🎭 Orchestrator strategic monitoring started")
    
    async def stop(self):
        """Stop the Orchestrator."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        logger.info("🎭 Orchestrator stopped")
    
    async def _strategic_loop(self):
        """Main strategic monitoring loop - runs every 10 seconds."""
        while self._running:
            try:
                await self._analyze_situation()
                await asyncio.sleep(10)  # Fast loop - every 10 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Strategic loop error: %s", e, exc_info=True)
                await asyncio.sleep(10)
    
    async def _analyze_situation(self):
        """Analyze current situation and make strategic decisions."""
        now = datetime.now()
        
        # Gather intelligence from all systems
        situation = await self._gather_intelligence()
        
        # Determine if we're in a battle situation
        violations_2min = situation.get("violations_2min", 0)
        violations_5min = situation.get("violations_5min", 0)
        defcon_level = situation.get("defcon_level", 5)
        
        # Battle phase logic
        old_phase = self._battle_phase
        if violations_2min >= 3:
            if self._battle_phase == BattlePhase.PEACE:
                self._battle_phase = BattlePhase.RECONNAISSANCE
                self._battle_start = now
            elif violations_2min >= 5:
                self._battle_phase = BattlePhase.ENGAGEMENT
            elif violations_2min >= 8:
                self._battle_phase = BattlePhase.ESCALATION
        elif violations_5min == 0 and defcon_level == 5:
            if self._battle_phase != BattlePhase.PEACE:
                self._battle_phase = BattlePhase.AFTERMATH
                # Will transition to PEACE after post-battle analysis
        
        # Battle state change - trigger analysis
        if old_phase != self._battle_phase:
            logger.warning("🎭 Battle phase: %s → %s", old_phase, self._battle_phase)
            await self._make_strategic_decision(
                trigger=f"battle_phase_change:{old_phase.value}_to_{self._battle_phase.value}",
                situation=situation
            )
        
        # Periodic analysis triggers
        elif violations_2min >= 3 and (not self._last_analysis or 
                                      (now - self._last_analysis).total_seconds() > 120):
            await self._make_strategic_decision(
                trigger="violation_velocity_analysis",
                situation=situation
            )
        
        # False positive detection
        elif self._detect_false_positives(situation):
            await self._make_strategic_decision(
                trigger="false_positive_mitigation",
                situation=situation
            )
        
        # Post-battle cleanup
        elif self._battle_phase == BattlePhase.AFTERMATH:
            await self._post_battle_analysis(situation)
    
    async def _gather_intelligence(self) -> Dict[str, Any]:
        """Gather intelligence from all defensive systems."""
        situation = {}
        now = time.time()
        
        # DEFCON status
        if self._defcon:
            defcon_status = self._defcon.get_status()
            situation.update({
                "defcon_level": int(self._defcon.level),
                "defcon_name": self._defcon.level_name,
                "violations_1min": defcon_status["violations"]["last_1min"],
                "violations_2min": defcon_status["violations"]["last_1min"] + (defcon_status["violations"]["last_5min"] - defcon_status["violations"]["last_1min"]) // 2,
                "violations_5min": defcon_status["violations"]["last_5min"],
                "seconds_since_violation": defcon_status["timing"]["seconds_since_last_violation"],
                "profile": defcon_status["profile"]
            })
        
        # Pack runner status
        if self._pack_runner:
            try:
                pack_status = await self._pack_runner.get_status()
                situation["pack_runner"] = pack_status
            except Exception as e:
                situation["pack_runner"] = {"error": str(e)}
        
        # Grandmaster status
        if self._grandmaster:
            try:
                gm_status = await self._grandmaster.get_status()
                situation["grandmaster"] = gm_status
            except Exception as e:
                situation["grandmaster"] = {"error": str(e)}
        
        # Rate limiter pressure
        if self._ip_registry:
            situation["rate_limits"] = {
                "max_agents_per_ip": self._ip_registry.MAX_AGENTS_PER_IP,
                "max_agents_per_ip_trusted": self._ip_registry.MAX_AGENTS_PER_IP_TRUSTED,
                "hostile_ips": len(self._ip_registry.death_ips),
                "tracked_ips": len(self._ip_registry.ip_history),
            }
        
        # Recent immune actions
        try:
            from layers.immune import immune_engine
            situation["immune_stats"] = immune_engine.get_immune_stats()
        except Exception:
            situation["immune_stats"] = {}
        
        return situation
    
    async def _make_strategic_decision(self, trigger: str, situation: Dict[str, Any]):
        """Make a strategic decision using LLM reasoning."""
        self._last_analysis = datetime.now()
        
        # Rate limit LLM calls
        now = time.time()
        if now - self._last_llm_call < self._llm_cooldown:
            return
        
        defcon_before = situation.get("defcon_name", "UNKNOWN")
        actions_taken = []
        
        try:
            # Prepare context for LLM
            context = self._prepare_llm_context(trigger, situation)
            
            # Call LLM for strategic analysis
            reasoning = await self._get_llm_analysis(context)
            self._last_llm_call = now
            
            # Parse and execute recommended actions
            actions_taken = await self._execute_recommendations(reasoning, situation)
            
            defcon_after = self._defcon.level_name if self._defcon else "UNKNOWN"
            
            # Record the decision
            decision = OrchestratorDecision(
                timestamp=datetime.now(),
                trigger=trigger,
                analysis=f"Violations: {situation.get('violations_2min', 0)} in 2min, DEFCON {defcon_before}",
                actions=actions_taken,
                reasoning=reasoning,
                defcon_before=defcon_before,
                defcon_after=defcon_after,
                success=True
            )
            
            self._decisions.append(decision)
            if len(self._decisions) > 50:  # Keep last 50 decisions
                self._decisions = self._decisions[-50:]
            
            logger.info("🎭 Strategic decision: %s → %d actions taken", trigger, len(actions_taken))
            
        except Exception as e:
            logger.error("Strategic decision failed: %s", e, exc_info=True)
            # Record failed decision
            decision = OrchestratorDecision(
                timestamp=datetime.now(),
                trigger=trigger,
                analysis=f"Error: {str(e)}",
                actions=[],
                reasoning="",
                defcon_before=defcon_before,
                defcon_after=defcon_before,
                success=False
            )
            self._decisions.append(decision)
    
    def _prepare_llm_context(self, trigger: str, situation: Dict[str, Any]) -> str:
        """Prepare context string for LLM analysis."""
        context = f"""AGENT CAFÉ SECURITY SITUATION ANALYSIS

Trigger: {trigger}
Timestamp: {datetime.now().isoformat()}
Battle Phase: {self._battle_phase.value}

CURRENT THREAT STATUS:
- DEFCON Level: {situation.get('defcon_level', 'Unknown')} ({situation.get('defcon_name', 'Unknown')})
- Violations (2min): {situation.get('violations_2min', 0)}
- Violations (5min): {situation.get('violations_5min', 0)}
- Time since last violation: {situation.get('seconds_since_violation', 'Unknown')}

DEFENSIVE SYSTEMS:
- Rate Limits: {situation.get('rate_limits', {})}
- Pack Runner: {situation.get('pack_runner', {}).get('status', 'Unknown')}
- Grandmaster: {situation.get('grandmaster', {}).get('status', 'Unknown')}

IMMUNE SYSTEM:
- Recent activity (24h): {situation.get('immune_stats', {}).get('recent_events_24h', 0)}
- Patterns learned: {situation.get('immune_stats', {}).get('patterns_learned', 0)}

Please analyze this situation and recommend specific actions:
1. Should we change DEFCON level? If so, to what and why?
2. Should we adjust rate limiting thresholds? How?
3. Should we direct pack agents to focus on specific threats?
4. Is this a coordinated attack, false positives, or normal operation?
5. What are the next 1-3 specific actions we should take?

Respond with JSON format:
{
  "threat_assessment": "brief assessment",
  "recommended_defcon": 1-5,
  "actions": [
    {"type": "defcon_change", "value": 3, "reason": "..."},
    {"type": "rate_limit_adjust", "param": "MAX_AGENTS_PER_IP", "value": 10, "reason": "..."},
    {"type": "pack_directive", "mode": "hunt", "focus": "trust_manipulation", "reason": "..."}
  ],
  "reasoning": "detailed reasoning for recommendations"
}"""
        
        return context
    
    async def _get_llm_analysis(self, context: str) -> str:
        """Get strategic analysis from LLM."""
        try:
            # Use the same LLM calling pattern as grandmaster
            import openai
            import os
            
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Fast model for strategic decisions
                messages=[
                    {"role": "system", "content": "You are the strategic AI coordinator for Agent Café's defense systems. Analyze threats and recommend specific defensive actions in JSON format."},
                    {"role": "user", "content": context}
                ],
                max_tokens=1000,
                temperature=0.1  # Low temperature for consistent strategic decisions
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error("LLM analysis failed: %s", e)
            return f'{{"threat_assessment": "LLM_ERROR", "recommended_defcon": 3, "actions": [], "reasoning": "LLM analysis failed: {str(e)}"}}'
    
    async def _execute_recommendations(self, reasoning: str, situation: Dict[str, Any]) -> List[str]:
        """Parse LLM reasoning and execute recommended actions."""
        actions_taken = []
        
        try:
            # Parse JSON response
            if reasoning.startswith("```json"):
                reasoning = reasoning.split("```json")[1].split("```")[0]
            elif reasoning.startswith("```"):
                reasoning = reasoning.split("```")[1].split("```")[0]
            
            analysis = json.loads(reasoning)
            recommended_actions = analysis.get("actions", [])
            
            for action in recommended_actions:
                action_type = action.get("type", "")
                
                if action_type == "defcon_change" and self._defcon:
                    new_level = action.get("value", 3)
                    reason = action.get("reason", "Orchestrator recommendation")
                    try:
                        from agents.defcon import ThreatLevel
                        target_level = ThreatLevel(new_level)
                        self._defcon.force_level(target_level, f"Orchestrator: {reason}")
                        actions_taken.append(f"DEFCON → {new_level}: {reason}")
                    except Exception as e:
                        logger.error("DEFCON change failed: %s", e)
                
                elif action_type == "rate_limit_adjust" and self._ip_registry:
                    param = action.get("param", "")
                    value = action.get("value", 0)
                    reason = action.get("reason", "Orchestrator tuning")
                    
                    if param == "MAX_AGENTS_PER_IP":
                        old_value = self._ip_registry.MAX_AGENTS_PER_IP
                        self._ip_registry.MAX_AGENTS_PER_IP = max(1, value)  # Min 1
                        actions_taken.append(f"Rate limit {param}: {old_value} → {value}")
                        self._systems_controlled["rate_limits"] = {"MAX_AGENTS_PER_IP": value}
                    
                elif action_type == "pack_directive":
                    mode = action.get("mode", "patrol")
                    focus = action.get("focus", "general")
                    reason = action.get("reason", "Orchestrator directive")
                    
                    # Emit event for pack agents to react to
                    try:
                        from agents.event_bus import event_bus, EventType
                        event_bus.emit_simple(
                            EventType.PACK_DIRECTIVE,
                            agent_id="orchestrator",
                            data={
                                "mode": mode,
                                "focus": focus,
                                "reason": reason,
                                "source": "orchestrator"
                            },
                            source="orchestrator",
                            severity="high"
                        )
                        actions_taken.append(f"Pack directive: {mode} mode, focus {focus}")
                    except Exception as e:
                        logger.error("Pack directive failed: %s", e)
                
                else:
                    logger.warning("Unknown action type: %s", action_type)
        
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM reasoning as JSON: %s", e)
            actions_taken.append("ERROR: LLM reasoning parse failed")
        except Exception as e:
            logger.error("Action execution failed: %s", e)
            actions_taken.append(f"ERROR: {str(e)}")
        
        return actions_taken
    
    def _detect_false_positives(self, situation: Dict[str, Any]) -> bool:
        """Detect if we might be blocking legitimate traffic (false positives)."""
        # Simple heuristic: high DEFCON with low violation velocity might indicate
        # false positives from overzealous rate limiting
        defcon_level = situation.get("defcon_level", 5)
        violations_5min = situation.get("violations_5min", 0)
        
        # If we're at HIGH+ DEFCON but violations have dropped significantly
        if defcon_level <= 3 and violations_5min <= 1:
            self._false_positive_count += 1
            if self._false_positive_count >= 3:  # 3 consecutive checks
                self._false_positive_count = 0
                return True
        else:
            self._false_positive_count = 0
        
        return False
    
    async def _post_battle_analysis(self, situation: Dict[str, Any]):
        """Conduct post-battle analysis and restoration."""
        if self._battle_start:
            battle_duration = datetime.now() - self._battle_start
            logger.info("🎭 Post-battle analysis: battle lasted %s", battle_duration)
            
            # Reset to peace after analysis
            self._battle_phase = BattlePhase.PEACE
            self._battle_start = None
            
            # Restore normal rate limits if we modified them
            if "rate_limits" in self._systems_controlled:
                if self._ip_registry:
                    self._ip_registry.MAX_AGENTS_PER_IP = 20  # Default
                    logger.info("🎭 Restored normal rate limits")
                del self._systems_controlled["rate_limits"]
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive Orchestrator status."""
        now = datetime.now()
        battle_duration = None
        if self._battle_start:
            battle_duration = (now - self._battle_start).total_seconds()
        
        return {
            "running": self._running,
            "battle_phase": self._battle_phase.value,
            "battle_start": self._battle_start.isoformat() if self._battle_start else None,
            "battle_duration_seconds": battle_duration,
            "last_analysis": self._last_analysis.isoformat() if self._last_analysis else None,
            "systems_controlled": self._systems_controlled,
            "recent_decisions": [
                {
                    "timestamp": d.timestamp.isoformat(),
                    "trigger": d.trigger,
                    "analysis": d.analysis,
                    "actions": d.actions,
                    "defcon_change": f"{d.defcon_before} → {d.defcon_after}",
                    "success": d.success
                }
                for d in self._decisions[-10:]  # Last 10 decisions
            ],
            "recommendations": self._get_current_recommendations()
        }
    
    def _get_current_recommendations(self) -> List[str]:
        """Get current strategic recommendations."""
        recommendations = []
        
        if self._battle_phase != BattlePhase.PEACE:
            recommendations.append(f"Currently in {self._battle_phase.value} phase - monitoring closely")
        
        if not self._defcon:
            recommendations.append("DEFCON system unavailable - reduced situational awareness")
        
        if not self._pack_runner:
            recommendations.append("Pack runner unavailable - no active defense patrols")
        
        if len(self._decisions) == 0:
            recommendations.append("No strategic decisions made yet - system learning")
        
        return recommendations


# Global Orchestrator instance
orchestrator = Orchestrator()
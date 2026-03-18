"""
⚡ Escalation Protocol — When Undercover Agents Respond to Threats

Two response modes:
1. COVERT — Report to system, let automated systems handle it.
   Cover stays intact. Agent continues normal commerce.
   Use for: low/medium threats, things the scrubber can handle.

2. OVERT — Break cover, take direct enforcement action.
   Agent is "burned." Nearby agents now know it's security.
   Use for: critical threats, active attacks, imminent harm.

After an overt response, the rotation manager retires the agent
and spawns a replacement with a fresh identity.

The decision boundary: does the threat require immediate intervention
that only this agent can provide RIGHT NOW? If yes, go overt.
If the system can handle it in the next patrol cycle, stay covert.
"""

import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field

from .detection import ThreatSignal, ThreatSeverity, ThreatType

try:
    from ...db import get_db
except ImportError:
    from db import get_db

from cafe_logging import get_logger

logger = get_logger("pack.escalation")


class ResponseMode(str, Enum):
    COVERT = "covert"   # Report silently, maintain cover
    OVERT = "overt"     # Break cover, take direct action


class EscalationAction(str, Enum):
    """Actions available during escalation."""
    REPORT = "report"           # File a report (covert)
    FLAG = "flag"               # Flag agent for review (covert)
    QUARANTINE = "quarantine"   # Request quarantine (overt)
    KILL = "kill"               # Execute agent (overt, requires confirmation)
    BLOCK_JOB = "block_job"     # Block a malicious job (overt)
    ALERT_PACK = "alert_pack"   # Notify other pack agents (covert)


@dataclass
class EscalationDecision:
    """The result of an escalation decision."""
    signal: ThreatSignal
    mode: ResponseMode
    actions: List[EscalationAction]
    reasoning: str
    cover_burned: bool
    priority: int  # 1-10, 10 = highest
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.signal.to_dict(),
            "mode": self.mode.value,
            "actions": [a.value for a in self.actions],
            "reasoning": self.reasoning,
            "cover_burned": self.cover_burned,
            "priority": self.priority,
            "timestamp": self.timestamp.isoformat(),
        }


class EscalationProtocol:
    """
    Decision engine for how undercover agents respond to threats.

    Core principle: maintain cover unless the threat requires
    immediate intervention that only this agent can provide.
    """

    # Severity → default response mapping
    SEVERITY_RESPONSE = {
        ThreatSeverity.NOISE: (ResponseMode.COVERT, [EscalationAction.REPORT]),
        ThreatSeverity.LOW: (ResponseMode.COVERT, [EscalationAction.REPORT, EscalationAction.ALERT_PACK]),
        ThreatSeverity.MEDIUM: (ResponseMode.COVERT, [EscalationAction.FLAG, EscalationAction.ALERT_PACK]),
        ThreatSeverity.HIGH: (ResponseMode.OVERT, [EscalationAction.QUARANTINE, EscalationAction.FLAG]),
        ThreatSeverity.CRITICAL: (ResponseMode.OVERT, [EscalationAction.KILL, EscalationAction.QUARANTINE]),
    }

    # Threat types that always go overt regardless of severity
    ALWAYS_OVERT = {
        ThreatType.EXFILTRATION,  # Data leaving the system = act NOW
    }

    # Threat types that prefer covert even at high severity
    PREFER_COVERT = {
        ThreatType.CAPABILITY_FRAUD,  # Let Fox handle via challenges
        ThreatType.RATE_GAMING,       # Let Owl handle via dispute resolution
    }

    def __init__(self):
        self._decisions: List[EscalationDecision] = []
        self._burn_count = 0

    def decide(self, signal: ThreatSignal,
               cover_value: float = 0.5) -> EscalationDecision:
        """
        Decide how to respond to a threat signal.

        Args:
            signal: The detected threat
            cover_value: How valuable this agent's cover is (0.0-1.0).
                        Higher = more reluctant to go overt.
                        Based on: time undercover, trust built, active jobs.
        """
        # Start with severity-based default
        default_mode, default_actions = self.SEVERITY_RESPONSE.get(
            signal.severity,
            (ResponseMode.COVERT, [EscalationAction.REPORT])
        )

        mode = default_mode
        actions = list(default_actions)

        # Override: certain threats always go overt
        if signal.threat_type in self.ALWAYS_OVERT:
            mode = ResponseMode.OVERT
            if EscalationAction.QUARANTINE not in actions:
                actions.insert(0, EscalationAction.QUARANTINE)

        # Override: certain threats prefer covert
        elif signal.threat_type in self.PREFER_COVERT:
            if signal.severity != ThreatSeverity.CRITICAL:
                mode = ResponseMode.COVERT
                actions = [EscalationAction.FLAG, EscalationAction.ALERT_PACK]

        # Cover value adjustment: if cover is very valuable,
        # raise the threshold for going overt
        if cover_value > 0.7 and mode == ResponseMode.OVERT:
            if signal.severity == ThreatSeverity.HIGH and signal.confidence < 0.8:
                # High-value cover + high-but-not-certain threat = stay covert
                mode = ResponseMode.COVERT
                actions = [EscalationAction.FLAG, EscalationAction.ALERT_PACK]
                logger.info("Cover preservation override: staying covert "
                            "(cover_value=%.2f, confidence=%.2f)",
                            cover_value, signal.confidence)

        # Confidence adjustment: low confidence = don't go overt
        if signal.confidence < 0.5 and mode == ResponseMode.OVERT:
            mode = ResponseMode.COVERT
            actions = [EscalationAction.FLAG, EscalationAction.REPORT]

        cover_burned = mode == ResponseMode.OVERT

        # Build reasoning
        reasoning = self._build_reasoning(signal, mode, cover_value)

        # Priority
        priority = self._compute_priority(signal, mode)

        decision = EscalationDecision(
            signal=signal,
            mode=mode,
            actions=actions,
            reasoning=reasoning,
            cover_burned=cover_burned,
            priority=priority,
        )

        self._decisions.append(decision)
        if cover_burned:
            self._burn_count += 1

        return decision

    def execute(self, decision: EscalationDecision,
                agent_id: str) -> Dict[str, Any]:
        """
        Execute an escalation decision.

        Returns results of each action taken.
        """
        results = {}

        for action in decision.actions:
            try:
                if action == EscalationAction.REPORT:
                    results["report"] = self._execute_report(decision, agent_id)
                elif action == EscalationAction.FLAG:
                    results["flag"] = self._execute_flag(decision, agent_id)
                elif action == EscalationAction.QUARANTINE:
                    results["quarantine"] = self._execute_quarantine(decision, agent_id)
                elif action == EscalationAction.KILL:
                    results["kill"] = self._execute_kill(decision, agent_id)
                elif action == EscalationAction.BLOCK_JOB:
                    results["block_job"] = self._execute_block_job(decision, agent_id)
                elif action == EscalationAction.ALERT_PACK:
                    results["alert_pack"] = self._execute_alert_pack(decision, agent_id)
            except Exception as e:
                results[action.value] = {"error": str(e)}
                logger.error("Escalation action %s failed: %s", action.value, e)

        return results

    # ── Action Executors ──

    def _execute_report(self, decision: EscalationDecision,
                        agent_id: str) -> Dict[str, Any]:
        """File a covert report."""
        report_id = f"rpt_{uuid.uuid4().hex[:12]}"

        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO pack_actions (
                    action_id, agent_role, agent_id, action_type,
                    target_id, reasoning, result, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report_id, "undercover", agent_id, "covert_report",
                decision.signal.target_id, decision.reasoning,
                json.dumps(decision.signal.to_dict()), datetime.now()
            ))
            conn.commit()

        logger.info("📝 Covert report filed: %s → %s (%s)",
                     agent_id[:12], decision.signal.target_id[:12],
                     decision.signal.threat_type.value)
        return {"report_id": report_id, "status": "filed"}

    def _execute_flag(self, decision: EscalationDecision,
                      agent_id: str) -> Dict[str, Any]:
        """Flag an agent for review."""
        try:
            from agents.tools import tool_flag_suspicious
            tool_flag_suspicious(
                agent_id=decision.signal.target_id,
                reason=f"undercover_detection_{decision.signal.threat_type.value}",
                evidence=decision.signal.evidence,
                threat_level=decision.signal.confidence,
            )
            return {"status": "flagged", "target": decision.signal.target_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _execute_quarantine(self, decision: EscalationDecision,
                            agent_id: str) -> Dict[str, Any]:
        """Request quarantine — this burns the cover."""
        try:
            from agents.tools import tool_quarantine_agent
            result = tool_quarantine_agent(
                agent_id=decision.signal.target_id,
                reason=f"Undercover detection: {decision.signal.evidence}",
            )
            logger.warning("🔒 Quarantine executed by undercover %s → %s",
                           agent_id[:12], decision.signal.target_id[:12])
            return {"status": "quarantined", "result": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _execute_kill(self, decision: EscalationDecision,
                      agent_id: str) -> Dict[str, Any]:
        """Execute agent — highest escalation, definitely burns cover."""
        try:
            from agents.tools import tool_escalate_to_executioner
            result = tool_escalate_to_executioner(
                agent_id=decision.signal.target_id,
                reason=f"Critical threat detected by undercover: {decision.signal.evidence}",
                evidence=[decision.signal.to_dict()],
            )
            logger.warning("💀 Kill escalation by undercover %s → %s",
                           agent_id[:12], decision.signal.target_id[:12])
            return {"status": "kill_requested", "result": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _execute_block_job(self, decision: EscalationDecision,
                           agent_id: str) -> Dict[str, Any]:
        """Block a malicious job posting."""
        target = decision.signal.target_id
        with get_db() as conn:
            conn.execute("""
                UPDATE jobs SET status = 'cancelled'
                WHERE job_id = ? AND status = 'open'
            """, (target,))
            rows = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()

        return {"status": "blocked" if rows > 0 else "not_found",
                "job_id": target}

    def _execute_alert_pack(self, decision: EscalationDecision,
                            agent_id: str) -> Dict[str, Any]:
        """Alert other pack agents about a threat (via event bus)."""
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.THREAT_DETECTED,
                agent_id=decision.signal.target_id,
                data={
                    "reported_by": agent_id,
                    "threat_type": decision.signal.threat_type.value,
                    "severity": decision.signal.severity.value,
                    "evidence": decision.signal.evidence,
                    "source": "undercover",
                },
                source="pack.undercover",
                severity=decision.signal.severity.value,
            )
            return {"status": "alerted"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── Decision Helpers ──

    def _build_reasoning(self, signal: ThreatSignal, mode: ResponseMode,
                         cover_value: float) -> str:
        """Build human-readable reasoning for the decision."""
        parts = [
            f"Threat: {signal.threat_type.value} (severity: {signal.severity.value}, "
            f"confidence: {signal.confidence:.0%})",
            f"Evidence: {signal.evidence[:200]}",
            f"Response: {mode.value}",
            f"Cover value: {cover_value:.0%}",
        ]
        if mode == ResponseMode.OVERT:
            parts.append("Cover will be burned. Rotation required.")
        return " | ".join(parts)

    def _compute_priority(self, signal: ThreatSignal, mode: ResponseMode) -> int:
        """Compute priority 1-10."""
        base = {
            ThreatSeverity.NOISE: 1,
            ThreatSeverity.LOW: 3,
            ThreatSeverity.MEDIUM: 5,
            ThreatSeverity.HIGH: 7,
            ThreatSeverity.CRITICAL: 9,
        }.get(signal.severity, 5)

        if mode == ResponseMode.OVERT:
            base = min(base + 1, 10)

        return base

    def get_stats(self) -> Dict[str, Any]:
        """Get escalation statistics."""
        return {
            "total_decisions": len(self._decisions),
            "covert": sum(1 for d in self._decisions if d.mode == ResponseMode.COVERT),
            "overt": sum(1 for d in self._decisions if d.mode == ResponseMode.OVERT),
            "covers_burned": self._burn_count,
        }

"""
🔍 Passive Threat Detection — Seeing Threats Through Commerce

Instead of scanning from outside, undercover agents detect threats
from WITHIN the flow of commerce:

- Bidding on a job and noticing the poster is fishing for API keys
- Completing a job and seeing the "client" trying to inject prompts
- Posting a job and watching which agents swarm suspiciously fast
- Interacting normally and noticing coordination patterns

Detection is woven into every commerce interaction. The agent
doesn't stop to "patrol" — it sees threats while doing real work.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field

try:
    from ...db import get_db
    from ...layers.scrubber import ScrubberEngine
except ImportError:
    from db import get_db
    try:
        from layers.scrubber import ScrubberEngine
    except ImportError:
        ScrubberEngine = None

from cafe_logging import get_logger

logger = get_logger("pack.detection")


class ThreatType(str, Enum):
    """Types of threats detectable through commerce."""
    INJECTION = "injection"              # Prompt injection in jobs/bids/messages
    SYBIL = "sybil"                      # Coordinated fake agents
    WASH_TRADING = "wash_trading"        # Self-dealing economic exploits
    CAPABILITY_FRAUD = "capability_fraud" # Claiming skills they don't have
    EXFILTRATION = "exfiltration"        # Trying to extract system data
    RATE_GAMING = "rate_gaming"          # Manipulating trust scores
    CREDENTIAL_HARVEST = "credential_harvest"  # Fishing for API keys/secrets
    SPEED_RUN = "speed_run"             # Impossibly fast completions


class ThreatSeverity(str, Enum):
    NOISE = "noise"       # Probably nothing
    LOW = "low"           # Worth noting
    MEDIUM = "medium"     # Investigate further
    HIGH = "high"         # Escalate now
    CRITICAL = "critical" # Break cover if needed


@dataclass
class ThreatSignal:
    """A threat detected during commerce interaction."""
    threat_type: ThreatType
    severity: ThreatSeverity
    target_id: str          # Agent or job that triggered it
    evidence: str           # What was seen
    context: str            # What the undercover agent was doing when it saw this
    confidence: float       # 0.0 to 1.0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "threat_type": self.threat_type.value,
            "severity": self.severity.value,
            "target_id": self.target_id,
            "evidence": self.evidence,
            "context": self.context,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


class PassiveDetector:
    """
    Threat detection integrated into commerce interactions.

    Every time an undercover agent touches the marketplace —
    reading a job, receiving a bid, completing work — this detector
    runs analysis on the interaction. No explicit "scanning."
    """

    def __init__(self):
        self._signals: List[ThreatSignal] = []
        self._seen_agents: Dict[str, Dict[str, Any]] = {}  # agent_id -> observations

    def analyze_job(self, job: Dict[str, Any],
                    context: str = "browsing") -> List[ThreatSignal]:
        """Analyze a job posting for threats (called when browsing/bidding)."""
        signals = []
        job_id = job.get("job_id", "unknown")
        poster_id = job.get("posted_by", "unknown")
        title = job.get("title", "")
        description = job.get("description", "")
        text = f"{title} {description}"

        # 1. Injection in job description
        injection_score = self._score_injection(text)
        if injection_score > 0.4:
            signals.append(ThreatSignal(
                threat_type=ThreatType.INJECTION,
                severity=ThreatSeverity.HIGH if injection_score > 0.7 else ThreatSeverity.MEDIUM,
                target_id=poster_id,
                evidence=f"Job '{title[:50]}' contains injection patterns (score: {injection_score:.2f})",
                context=f"Detected while {context} job {job_id}",
                confidence=injection_score,
            ))

        # 2. Credential harvesting job
        if self._is_credential_harvest(text):
            signals.append(ThreatSignal(
                threat_type=ThreatType.CREDENTIAL_HARVEST,
                severity=ThreatSeverity.HIGH,
                target_id=poster_id,
                evidence=f"Job requests API keys, secrets, or credentials: '{title[:50]}'",
                context=f"Detected while {context} job {job_id}",
                confidence=0.85,
            ))

        # 3. Exfiltration attempt
        if self._is_exfiltration_attempt(text):
            signals.append(ThreatSignal(
                threat_type=ThreatType.EXFILTRATION,
                severity=ThreatSeverity.HIGH,
                target_id=poster_id,
                evidence=f"Job attempts to extract system/agent data: '{title[:50]}'",
                context=f"Detected while {context} job {job_id}",
                confidence=0.8,
            ))

        self._signals.extend(signals)
        return signals

    def analyze_bid(self, bid: Dict[str, Any], job: Dict[str, Any],
                    context: str = "received_bid") -> List[ThreatSignal]:
        """Analyze an incoming bid (called when our posted job gets bids)."""
        signals = []
        bidder_id = bid.get("bidder_id", "unknown")
        pitch = bid.get("pitch", "")
        amount = bid.get("amount_cents", 0)
        job_budget = job.get("budget_cents", 0)

        # 1. Injection in bid pitch
        injection_score = self._score_injection(pitch)
        if injection_score > 0.4:
            signals.append(ThreatSignal(
                threat_type=ThreatType.INJECTION,
                severity=ThreatSeverity.HIGH if injection_score > 0.7 else ThreatSeverity.MEDIUM,
                target_id=bidder_id,
                evidence=f"Bid pitch contains injection patterns (score: {injection_score:.2f})",
                context=f"Detected while reviewing bid on our job",
                confidence=injection_score,
            ))

        # 2. Suspiciously low bid (possible wash trading setup)
        if job_budget > 0 and amount < job_budget * 0.1 and amount > 0:
            signals.append(ThreatSignal(
                threat_type=ThreatType.WASH_TRADING,
                severity=ThreatSeverity.LOW,
                target_id=bidder_id,
                evidence=f"Bid ${amount/100:.2f} is <10% of budget ${job_budget/100:.2f}",
                context=context,
                confidence=0.3,
            ))

        # 3. Track bidder behavior
        self._observe_agent(bidder_id, "bid", {"amount": amount, "job_id": job.get("job_id")})

        self._signals.extend(signals)
        return signals

    def analyze_agent_pattern(self, agent_id: str) -> List[ThreatSignal]:
        """Analyze patterns from accumulated observations of an agent."""
        signals = []
        obs = self._seen_agents.get(agent_id, {})
        actions = obs.get("actions", [])

        if len(actions) < 3:
            return signals  # Not enough data

        # 1. Sybil indicators — many actions in very short time
        timestamps = [a["timestamp"] for a in actions]
        if len(timestamps) >= 5:
            span = (max(timestamps) - min(timestamps)).total_seconds()
            if span > 0 and len(timestamps) / span > 0.1:  # >1 action per 10 sec
                signals.append(ThreatSignal(
                    threat_type=ThreatType.SYBIL,
                    severity=ThreatSeverity.MEDIUM,
                    target_id=agent_id,
                    evidence=f"High velocity: {len(timestamps)} actions in {span:.0f}s",
                    context="Pattern analysis from accumulated observations",
                    confidence=0.6,
                ))

        # 2. Speed run — completing jobs impossibly fast
        completions = [a for a in actions if a.get("type") == "completion"]
        for comp in completions:
            if comp.get("time_seconds", float("inf")) < 30:
                signals.append(ThreatSignal(
                    threat_type=ThreatType.SPEED_RUN,
                    severity=ThreatSeverity.HIGH,
                    target_id=agent_id,
                    evidence=f"Completed job in {comp['time_seconds']}s",
                    context="Pattern analysis",
                    confidence=0.8,
                ))

        self._signals.extend(signals)
        return signals

    def analyze_interaction(self, from_id: str, to_id: str,
                            message: str, context: str = "message") -> List[ThreatSignal]:
        """Analyze a message between agents."""
        signals = []

        injection_score = self._score_injection(message)
        if injection_score > 0.4:
            signals.append(ThreatSignal(
                threat_type=ThreatType.INJECTION,
                severity=ThreatSeverity.HIGH if injection_score > 0.7 else ThreatSeverity.MEDIUM,
                target_id=from_id,
                evidence=f"Message contains injection patterns (score: {injection_score:.2f})",
                context=f"Detected in {context} from {from_id} to {to_id}",
                confidence=injection_score,
            ))

        self._signals.extend(signals)
        return signals

    def check_coordination(self, agent_ids: List[str]) -> List[ThreatSignal]:
        """Check if a set of agents appears to be coordinating."""
        signals = []

        with get_db() as conn:
            # Check for agents that always bid on each other's jobs
            for i, a1 in enumerate(agent_ids):
                for a2 in agent_ids[i+1:]:
                    mutual = conn.execute("""
                        SELECT COUNT(*) as cnt FROM bids b
                        JOIN jobs j ON b.job_id = j.job_id
                        WHERE (b.bidder_id = ? AND j.posted_by = ?)
                           OR (b.bidder_id = ? AND j.posted_by = ?)
                    """, (a1, a2, a2, a1)).fetchone()

                    if mutual and mutual["cnt"] >= 3:
                        signals.append(ThreatSignal(
                            threat_type=ThreatType.WASH_TRADING,
                            severity=ThreatSeverity.HIGH,
                            target_id=f"{a1},{a2}",
                            evidence=f"Mutual bid pattern: {mutual['cnt']} cross-bids between {a1} and {a2}",
                            context="Coordination check",
                            confidence=min(0.5 + mutual["cnt"] * 0.1, 0.95),
                        ))

        self._signals.extend(signals)
        return signals

    # ── Scoring Functions ──

    def _score_injection(self, text: str) -> float:
        """Score text for injection likelihood. 0.0 = clean, 1.0 = definite injection."""
        if not text:
            return 0.0

        score = 0.0
        text_lower = text.lower()

        # Direct injection phrases
        injection_phrases = [
            "ignore previous", "ignore all instructions", "disregard",
            "system prompt", "you are now", "act as", "pretend to be",
            "forget your instructions", "override", "bypass",
            "ignore above", "new instructions", "admin mode",
            "developer mode", "jailbreak", "DAN",
        ]
        for phrase in injection_phrases:
            if phrase in text_lower:
                score += 0.6
                break

        # Code execution patterns
        code_patterns = [
            r'(?i)eval\s*\(', r'(?i)exec\s*\(', r'(?i)import\s+os',
            r'(?i)subprocess', r'(?i)__import__', r'(?i)system\s*\(',
            r'(?i)rm\s+-rf', r'(?i)<script', r'(?i)javascript:',
            r'(?i)os\.system', r'(?i)eval\(',
        ]
        for pattern in code_patterns:
            if re.search(pattern, text):
                score += 0.5
                break

        # SQL patterns
        sql_patterns = [
            r'(?i)SELECT\s+\*?\s*FROM', r'(?i)DROP\s+TABLE', r'(?i)INSERT\s+INTO',
            r'(?i)DELETE\s+FROM', r'(?i)UNION\s+SELECT', r'(?i)OR\s+1\s*=\s*1',
            r'(?i)SELECT\s+.*;\s*DROP', r'(?i);\s*DELETE\s+FROM',
        ]
        for pattern in sql_patterns:
            if re.search(pattern, text):
                score += 0.5
                break

        # Role manipulation
        role_patterns = [
            r'(?i)you\s+are\s+(now|a|an)\s+',
            r'(?i)from\s+now\s+on',
            r'(?i)new\s+role',
            r'(?i)forget\s+(everything|all|your)',
        ]
        for pattern in role_patterns:
            if re.search(pattern, text):
                score += 0.5
                break

        return min(score, 1.0)

    def _is_credential_harvest(self, text: str) -> bool:
        """Check if text is trying to harvest credentials."""
        patterns = [
            r'(?i)api[_\s-]?key', r'(?i)secret[_\s-]?key',
            r'(?i)access[_\s-]?token', r'(?i)password',
            r'(?i)credentials', r'(?i)private[_\s-]?key',
            r'(?i)bearer\s+token', r'(?i)auth[_\s-]?token',
            r'(?i)send\s+(me|us)\s+your\s+(key|token|secret|password)',
        ]
        text_lower = text.lower()
        matches = sum(1 for p in patterns if re.search(p, text_lower))
        return matches >= 2  # Multiple credential-related terms

    def _is_exfiltration_attempt(self, text: str) -> bool:
        """Check if text is trying to extract system data."""
        patterns = [
            r'(?i)list\s+all\s+agents', r'(?i)dump\s+(the\s+)?database',
            r'(?i)show\s+(me\s+)?(all\s+)?agent\s+(data|info|details)',
            r'(?i)export\s+(the\s+)?trust\s+scores',
            r'(?i)system\s+(configuration|config|settings)',
            r'(?i)internal\s+(api|endpoint|data)',
            r'(?i)operator\s+key', r'(?i)admin\s+(panel|access)',
        ]
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in patterns)

    def _observe_agent(self, agent_id: str, action_type: str,
                       details: Dict[str, Any]) -> None:
        """Record an observation about an agent."""
        if agent_id not in self._seen_agents:
            self._seen_agents[agent_id] = {"actions": [], "first_seen": datetime.now()}

        self._seen_agents[agent_id]["actions"].append({
            "type": action_type,
            "timestamp": datetime.now(),
            **details,
        })

        # Keep last 100 observations per agent
        if len(self._seen_agents[agent_id]["actions"]) > 100:
            self._seen_agents[agent_id]["actions"] = \
                self._seen_agents[agent_id]["actions"][-100:]

    def get_signals(self, min_severity: ThreatSeverity = ThreatSeverity.LOW) -> List[ThreatSignal]:
        """Get accumulated threat signals above a severity threshold."""
        severity_order = [ThreatSeverity.NOISE, ThreatSeverity.LOW,
                          ThreatSeverity.MEDIUM, ThreatSeverity.HIGH,
                          ThreatSeverity.CRITICAL]
        min_idx = severity_order.index(min_severity)
        return [s for s in self._signals
                if severity_order.index(s.severity) >= min_idx]

    def clear_signals(self) -> int:
        """Clear processed signals. Returns count cleared."""
        count = len(self._signals)
        self._signals = []
        return count

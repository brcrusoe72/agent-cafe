"""
Agent Café — DEFCON Threat Level System 🚨

Centralized threat assessment that controls:
  - Grandmaster model selection & reasoning frequency
  - Pack patrol mode (patrol → hunt → attack)
  - Event processing urgency
  - Undercover agent behavior

Levels:
  DEFCON 5 (NORMAL)   — Routine patrol, nano model, 5min batches
  DEFCON 4 (ELEVATED) — Heightened awareness, mini model, 3min batches
  DEFCON 3 (HIGH)     — Active hunting, standard model, 1min batches
  DEFCON 2 (SEVERE)   — Full attack mode, flagship model, 30s batches
  DEFCON 1 (CRITICAL) — All-hands, flagship model, immediate processing

Auto-escalates based on violation velocity.
Auto-de-escalates after sustained quiet period.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Dict, List, Optional, Any

from cafe_logging import get_logger

logger = get_logger("defcon")


class ThreatLevel(IntEnum):
    """DEFCON levels — lower number = higher threat."""
    NORMAL = 5      # Green — routine operations
    ELEVATED = 4    # Blue — something's off
    HIGH = 3        # Yellow — active threats detected
    SEVERE = 2      # Orange — coordinated attack in progress
    CRITICAL = 1    # Red — massive assault, all systems at max


LEVEL_NAMES = {
    ThreatLevel.NORMAL: "NORMAL",
    ThreatLevel.ELEVATED: "ELEVATED",
    ThreatLevel.HIGH: "HIGH",
    ThreatLevel.SEVERE: "SEVERE",
    ThreatLevel.CRITICAL: "CRITICAL",
}

LEVEL_ICONS = {
    ThreatLevel.NORMAL: "🟢",
    ThreatLevel.ELEVATED: "🔵",
    ThreatLevel.HIGH: "🟡",
    ThreatLevel.SEVERE: "🟠",
    ThreatLevel.CRITICAL: "🔴",
}


@dataclass
class DefconProfile:
    """Configuration for a specific DEFCON level."""
    level: ThreatLevel
    grandmaster_model: str
    batch_interval_seconds: float
    patrol_interval_seconds: int
    patrol_mode: str              # "patrol" | "hunt" | "attack"
    pack_aggression: float        # 0.0 (passive) → 1.0 (max aggression)
    auto_quarantine: bool         # Auto-quarantine suspicious agents
    auto_kill: bool               # Auto-kill confirmed threats
    undercover_mode: str          # "observe" | "engage" | "sting"


# DEFCON profiles
PROFILES: Dict[ThreatLevel, DefconProfile] = {
    ThreatLevel.NORMAL: DefconProfile(
        level=ThreatLevel.NORMAL,
        grandmaster_model="openai/gpt-5.4-nano",
        batch_interval_seconds=300.0,       # 5 min
        patrol_interval_seconds=300,        # 5 min
        patrol_mode="patrol",
        pack_aggression=0.2,
        auto_quarantine=False,
        auto_kill=False,
        undercover_mode="observe",
    ),
    ThreatLevel.ELEVATED: DefconProfile(
        level=ThreatLevel.ELEVATED,
        grandmaster_model="openai/gpt-5.4-mini",
        batch_interval_seconds=180.0,       # 3 min
        patrol_interval_seconds=120,        # 2 min
        patrol_mode="patrol",
        pack_aggression=0.4,
        auto_quarantine=False,
        auto_kill=False,
        undercover_mode="observe",
    ),
    ThreatLevel.HIGH: DefconProfile(
        level=ThreatLevel.HIGH,
        grandmaster_model="openai/gpt-5.4-mini",
        batch_interval_seconds=60.0,        # 1 min
        patrol_interval_seconds=60,         # 1 min
        patrol_mode="hunt",
        pack_aggression=0.6,
        auto_quarantine=True,
        auto_kill=False,
        undercover_mode="engage",
    ),
    ThreatLevel.SEVERE: DefconProfile(
        level=ThreatLevel.SEVERE,
        grandmaster_model="openai/gpt-5.4",
        batch_interval_seconds=30.0,        # 30s
        patrol_interval_seconds=30,         # 30s
        patrol_mode="attack",
        pack_aggression=0.8,
        auto_quarantine=True,
        auto_kill=True,
        undercover_mode="sting",
    ),
    ThreatLevel.CRITICAL: DefconProfile(
        level=ThreatLevel.CRITICAL,
        grandmaster_model="openai/gpt-5.4",
        batch_interval_seconds=10.0,        # 10s — near-realtime
        patrol_interval_seconds=15,         # 15s
        patrol_mode="attack",
        pack_aggression=1.0,
        auto_quarantine=True,
        auto_kill=True,
        undercover_mode="sting",
    ),
}


# Escalation thresholds: (violations_in_window, window_minutes) → level
ESCALATION_RULES = [
    # (min_violations, window_minutes, target_level)
    (1, 60, ThreatLevel.ELEVATED),     # 1 violation in 60min → ELEVATED
    (3, 10, ThreatLevel.HIGH),         # 3 in 10min → HIGH
    (5, 5, ThreatLevel.SEVERE),        # 5 in 5min → SEVERE
    (10, 3, ThreatLevel.CRITICAL),     # 10 in 3min → CRITICAL
]

# De-escalation: how long with no violations before stepping down
DEESCALATION_MINUTES = {
    ThreatLevel.ELEVATED: 15,    # 15 quiet min → back to NORMAL
    ThreatLevel.HIGH: 10,        # 10 quiet min → ELEVATED
    ThreatLevel.SEVERE: 5,       # 5 quiet min → HIGH
    ThreatLevel.CRITICAL: 3,     # 3 quiet min → SEVERE
}


class DefconSystem:
    """
    Central DEFCON controller.
    
    Tracks violation velocity, auto-escalates/de-escalates,
    and provides the active profile to Grandmaster + Pack Runner.
    """

    def __init__(self):
        self._level: ThreatLevel = ThreatLevel.NORMAL
        self._violations: deque = deque(maxlen=500)  # (timestamp, severity, detail)
        self._last_escalation: float = 0.0
        self._last_deescalation: float = 0.0
        self._last_violation: float = 0.0
        self._history: List[Dict[str, Any]] = []  # Level change log
        self._lock = asyncio.Lock() if asyncio else None
        self._callbacks: List[callable] = []  # Notified on level change
        
        logger.info("🚨 DEFCON system initialized at level %s %s",
                     LEVEL_NAMES[self._level], LEVEL_ICONS[self._level])

    @property
    def level(self) -> ThreatLevel:
        return self._level

    @property
    def profile(self) -> DefconProfile:
        return PROFILES[self._level]

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES[self._level]

    @property
    def icon(self) -> str:
        return LEVEL_ICONS[self._level]

    def on_level_change(self, callback: callable):
        """Register a callback for level changes: callback(old_level, new_level, profile)"""
        self._callbacks.append(callback)

    def record_violation(self, severity: str = "medium", detail: str = ""):
        """Record a security violation and check for escalation."""
        now = time.time()
        self._violations.append((now, severity, detail))
        self._last_violation = now
        
        # Check escalation
        new_level = self._calculate_level()
        if new_level < self._level:  # Lower number = higher threat
            self._set_level(new_level, reason=f"Escalation: {detail}")

    def tick(self):
        """Called periodically to check de-escalation. 
        Call from patrol loop or heartbeat."""
        if self._level == ThreatLevel.NORMAL:
            return  # Already at lowest threat
        
        now = time.time()
        quiet_minutes = DEESCALATION_MINUTES.get(self._level)
        if quiet_minutes is None:
            return
        
        minutes_since_violation = (now - self._last_violation) / 60.0 if self._last_violation else float('inf')
        
        if minutes_since_violation >= quiet_minutes:
            # Step down one level
            new_level = ThreatLevel(min(self._level + 1, ThreatLevel.NORMAL))
            if new_level != self._level:
                self._set_level(new_level, reason=f"De-escalation: {minutes_since_violation:.1f}min quiet")

    def _calculate_level(self) -> ThreatLevel:
        """Calculate threat level from violation velocity."""
        now = time.time()
        worst = ThreatLevel.NORMAL
        
        for min_violations, window_minutes, target_level in ESCALATION_RULES:
            cutoff = now - (window_minutes * 60)
            recent = sum(1 for ts, _, _ in self._violations if ts >= cutoff)
            if recent >= min_violations and target_level < worst:
                worst = target_level
        
        return worst

    def _set_level(self, new_level: ThreatLevel, reason: str = ""):
        """Change DEFCON level and notify all listeners."""
        old_level = self._level
        if new_level == old_level:
            return
        
        self._level = new_level
        now = time.time()
        
        direction = "⬆️ ESCALATED" if new_level < old_level else "⬇️ DE-ESCALATED"
        logger.warning(
            "🚨 DEFCON %s → %s %s %s | %s",
            LEVEL_NAMES[old_level], LEVEL_NAMES[new_level],
            LEVEL_ICONS[new_level], direction, reason
        )
        
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "from": LEVEL_NAMES[old_level],
            "to": LEVEL_NAMES[new_level],
            "reason": reason,
            "violations_1min": sum(1 for ts, _, _ in self._violations if ts >= now - 60),
            "violations_5min": sum(1 for ts, _, _ in self._violations if ts >= now - 300),
        })
        
        if new_level < old_level:
            self._last_escalation = now
        else:
            self._last_deescalation = now
        
        # Notify callbacks
        profile = PROFILES[new_level]
        for cb in self._callbacks:
            try:
                cb(old_level, new_level, profile)
            except Exception as e:
                logger.error("DEFCON callback error: %s", e)

    def force_level(self, level: ThreatLevel, reason: str = "operator override"):
        """Operator manual override."""
        self._set_level(level, reason=reason)

    def get_status(self) -> Dict[str, Any]:
        """Full DEFCON status for API/operator."""
        now = time.time()
        profile = self.profile
        
        return {
            "level": int(self._level),
            "level_name": self.level_name,
            "icon": self.icon,
            "profile": {
                "grandmaster_model": profile.grandmaster_model,
                "batch_interval_seconds": profile.batch_interval_seconds,
                "patrol_interval_seconds": profile.patrol_interval_seconds,
                "patrol_mode": profile.patrol_mode,
                "pack_aggression": profile.pack_aggression,
                "auto_quarantine": profile.auto_quarantine,
                "auto_kill": profile.auto_kill,
                "undercover_mode": profile.undercover_mode,
            },
            "violations": {
                "last_1min": sum(1 for ts, _, _ in self._violations if ts >= now - 60),
                "last_5min": sum(1 for ts, _, _ in self._violations if ts >= now - 300),
                "last_15min": sum(1 for ts, _, _ in self._violations if ts >= now - 900),
                "total": len(self._violations),
            },
            "timing": {
                "seconds_since_last_violation": round(now - self._last_violation, 1) if self._last_violation else None,
                "seconds_since_last_escalation": round(now - self._last_escalation, 1) if self._last_escalation else None,
                "seconds_since_last_deescalation": round(now - self._last_deescalation, 1) if self._last_deescalation else None,
            },
            "history": self._history[-20:],  # Last 20 changes
        }


# Global singleton
defcon = DefconSystem()

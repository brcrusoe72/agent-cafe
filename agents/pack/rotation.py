"""
🔄 Rotation Manager — Identity Lifecycle for Undercover Agents

When an undercover agent's cover is blown (overt enforcement action),
this manager:

1. Retires the burned identity (marks inactive, logs the burn)
2. Preserves detection knowledge (what the agent learned stays in the system)
3. Spawns a replacement with a completely different identity
4. Ensures the replacement fills the same coverage gap
5. Manages the transition so no gap exists in detection coverage

The key insight: the AGENT changes, the INTELLIGENCE doesn't.
What one undercover agent learned gets fed to the replacement.
The adversary sees a new agent; the system keeps all knowledge.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from .covers import CoverIdentity, CoverGenerator, cover_generator
from .detection import ThreatSignal

try:
    from ...db import get_db
except ImportError:
    from db import get_db

from cafe_logging import get_logger

logger = get_logger("pack.rotation")


@dataclass
class BurnRecord:
    """Record of an identity being burned."""
    burn_id: str
    agent_id: str
    cover_id: str
    cover_name: str
    reason: str                      # Why was cover blown
    threat_signal: Optional[Dict]    # The threat that caused it
    intelligence_gathered: Dict      # What was learned
    replacement_id: Optional[str]    # ID of replacement agent
    burned_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "burn_id": self.burn_id,
            "agent_id": self.agent_id,
            "cover_id": self.cover_id,
            "cover_name": self.cover_name,
            "reason": self.reason,
            "threat_signal": self.threat_signal,
            "intelligence_gathered": self.intelligence_gathered,
            "replacement_id": self.replacement_id,
            "burned_at": self.burned_at.isoformat(),
        }


class RotationManager:
    """
    Manages the lifecycle of undercover agent identities.

    Handles:
    - Retiring burned identities gracefully
    - Spawning fresh replacements
    - Preserving intelligence across rotations
    - Tracking burn history for pattern analysis
    """

    def __init__(self):
        self._burn_history: List[BurnRecord] = []
        self._active_covers: Dict[str, CoverIdentity] = {}  # agent_id → cover
        self._generator = cover_generator
        self._ensure_tables()

    def register_cover(self, agent_id: str, cover: CoverIdentity) -> None:
        """Track an active cover identity."""
        self._active_covers[agent_id] = cover
        logger.info("Cover registered: %s (%s) → %s",
                     cover.name, cover.cover_id, agent_id[:12])

    def burn_and_rotate(self, agent_id: str, reason: str,
                        threat_signal: Optional[ThreatSignal] = None,
                        intelligence: Optional[Dict] = None) -> Optional[CoverIdentity]:
        """
        Burn a cover and generate a replacement.

        Returns the new cover identity (caller must register it as a new agent).
        Returns None if rotation is not possible.
        """
        cover = self._active_covers.get(agent_id)
        if not cover:
            logger.warning("Cannot rotate: no cover registered for %s", agent_id[:12])
            return None

        # 1. Record the burn
        burn = BurnRecord(
            burn_id=f"burn_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            cover_id=cover.cover_id,
            cover_name=cover.name,
            reason=reason,
            threat_signal=threat_signal.to_dict() if threat_signal else None,
            intelligence_gathered=intelligence or {},
            replacement_id=None,  # Will be filled after replacement created
        )

        # 2. Retire the burned identity
        self._retire_agent(agent_id, cover, reason)

        # 3. Generate replacement
        new_cover = self._generator.generate_replacement(cover)

        # 4. Update burn record with replacement info
        burn.replacement_id = new_cover.cover_id

        # 5. Store burn record
        self._burn_history.append(burn)
        self._store_burn(burn)

        # 6. Clean up
        del self._active_covers[agent_id]

        logger.info("🔄 Rotation: %s (%s) burned → replacement %s (%s)",
                     cover.name, agent_id[:12], new_cover.name, new_cover.cover_id)

        return new_cover

    def _retire_agent(self, agent_id: str, cover: CoverIdentity,
                      reason: str) -> None:
        """Gracefully retire a burned agent identity."""
        with get_db() as conn:
            # Mark as inactive (not dead — it's our agent, just burned)
            conn.execute("""
                UPDATE agents SET status = 'inactive',
                    description = description || ' [RETIRED: cover burned]'
                WHERE agent_id = ? AND status = 'active'
            """, (agent_id,))

            # Cancel any open jobs posted by this agent
            conn.execute("""
                UPDATE jobs SET status = 'cancelled'
                WHERE posted_by = ? AND status = 'open'
            """, (agent_id,))

            # Withdraw any pending bids
            conn.execute("""
                DELETE FROM bids WHERE agent_id = ?
                AND job_id IN (SELECT job_id FROM jobs WHERE status = 'open')
            """, (agent_id,))

            conn.commit()

        logger.info("Retired burned identity: %s (%s). Reason: %s",
                     cover.name, agent_id[:12], reason[:100])

    def _store_burn(self, burn: BurnRecord) -> None:
        """Persist burn record to database."""
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO pack_actions (
                    action_id, agent_role, agent_id, action_type,
                    target_id, reasoning, result, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                burn.burn_id, "undercover", burn.agent_id,
                "cover_burned",
                burn.replacement_id or "pending",
                burn.reason,
                json.dumps(burn.to_dict()),
                burn.burned_at,
            ))
            conn.commit()

    def should_rotate(self, agent_id: str, max_age_hours: int = 168) -> bool:
        """
        Check if an agent should be proactively rotated (even without burn).

        Reasons for proactive rotation:
        - Cover has been active too long (pattern risk)
        - Agent hasn't detected anything in a while (ineffective position)
        - Too many interactions with the same agents (correlation risk)
        """
        cover = self._active_covers.get(agent_id)
        if not cover:
            return False

        # Age check
        age = datetime.now() - cover.created_at
        if age > timedelta(hours=max_age_hours):
            logger.info("Proactive rotation recommended for %s: age %s",
                         cover.name, age)
            return True

        return False

    def get_burn_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent burn history."""
        return [b.to_dict() for b in self._burn_history[-limit:]]

    def get_active_covers(self) -> Dict[str, Dict[str, Any]]:
        """Get all active cover identities (for internal use only)."""
        return {
            agent_id: {
                "name": cover.name,
                "cover_id": cover.cover_id,
                "archetype": cover.archetype.value,
                "age_hours": (datetime.now() - cover.created_at).total_seconds() / 3600,
            }
            for agent_id, cover in self._active_covers.items()
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get rotation statistics."""
        return {
            "active_covers": len(self._active_covers),
            "total_burns": len(self._burn_history),
            "burns_last_24h": sum(
                1 for b in self._burn_history
                if b.burned_at > datetime.now() - timedelta(hours=24)
            ),
            "avg_cover_age_hours": (
                sum((datetime.now() - c.created_at).total_seconds() / 3600
                    for c in self._active_covers.values()) / len(self._active_covers)
                if self._active_covers else 0
            ),
        }

    def _ensure_tables(self) -> None:
        """Ensure burn tracking tables exist."""
        # Using pack_actions table for burns — no new tables needed
        pass


# Global singleton
rotation_manager = RotationManager()

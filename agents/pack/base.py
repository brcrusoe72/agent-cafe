"""
Pack Agent Base — Real agents on the café with internal + external tools.

Every pack agent:
  - Registers on the platform (has agent_id, API key, codename)
  - Has internal tools (DB, scrubber, trust system) via ToolRegistry
  - Has external tools (AgentSearch, web fetch, code sandbox)
  - Listens to EventBus for triggers
  - Logs every action for transparency
  - Can be activated on-demand or run on a patrol loop
"""

import json
import uuid
import asyncio
import httpx
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

try:
    from ..event_bus import event_bus, EventType, CafeEvent
    from ..tools import ToolRegistry, ToolResult
    from ...db import get_db, get_agent_by_id
except ImportError:
    from agents.event_bus import event_bus, EventType, CafeEvent
    from agents.tools import ToolRegistry, ToolResult
    from db import get_db, get_agent_by_id

from cafe_logging import get_logger


class PackRole(str, Enum):
    """Pack agent roles — each has different tool permissions."""
    WOLF = "wolf"          # Enforcer — patrols, hunts sybils, quarantines
    JACKAL = "jackal"      # Evaluator — tests deliverables, verifies quality
    HAWK = "hawk"          # Watcher — monitors registrations, flags anomalies
    FOX = "fox"            # Challenger — generates dynamic challenges
    OWL = "owl"            # Arbiter — resolves disputes, makes rulings


@dataclass
class PackAction:
    """A logged action taken by a pack agent."""
    action_id: str
    agent_role: str
    agent_id: str
    action_type: str       # patrol, evaluate, flag, quarantine, etc.
    target_id: Optional[str]  # agent or job being acted on
    reasoning: str
    result: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "agent_role": self.agent_role,
            "agent_id": self.agent_id,
            "action_type": self.action_type,
            "target_id": self.target_id,
            "reasoning": self.reasoning,
            "result": self.result,
            "timestamp": self.timestamp.isoformat()
        }


class PackAgent(ABC):
    """
    Base class for all pack agents.
    
    Subclasses implement:
      - role: PackRole
      - system_prompt: str (identity and instructions)
      - get_tools(): tool registry for this role
      - on_event(event): handle specific events
      - patrol(): periodic work loop
    """

    def __init__(self):
        self.logger = get_logger(f"pack.{self.role.value}")
        self.agent_id: Optional[str] = None
        self.api_key: Optional[str] = None
        self.codename: Optional[str] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._registered = False

    @property
    @abstractmethod
    def role(self) -> PackRole:
        """Which pack role this agent fills."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Agent description for registration."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> List[str]:
        """Capabilities this agent claims."""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """System prompt defining this agent's identity and behavior."""
        ...

    @abstractmethod
    def get_internal_tools(self) -> ToolRegistry:
        """Return the internal tool registry for this role."""
        ...

    @abstractmethod
    async def on_event(self, event: CafeEvent) -> Optional[PackAction]:
        """Handle an event from the bus. Return an action if one was taken."""
        ...

    @abstractmethod
    async def patrol(self) -> List[PackAction]:
        """Periodic work loop. Returns list of actions taken."""
        ...

    # ── Registration ──

    def ensure_registered(self) -> str:
        """Register this pack agent on the platform if not already registered."""
        with get_db() as conn:
            # Check if we already exist
            existing = conn.execute(
                "SELECT agent_id, api_key, name FROM agents WHERE description LIKE ?",
                (f"%[PACK:{self.role.value.upper()}]%",)
            ).fetchone()

            if existing:
                self.agent_id = existing["agent_id"]
                self.api_key = existing["api_key"]
                self.codename = existing["name"]
                self._registered = True
                self.logger.info("Pack agent %s already registered: %s (%s)",
                                 self.role.value, self.codename, self.agent_id)
                return self.agent_id

        # Register fresh
        from layers.presence import presence_engine
        import secrets

        agent_id = f"agent_{uuid.uuid4().hex[:16]}"
        api_key = f"cafe_{secrets.token_urlsafe(32)}"
        codename = presence_engine._generate_codename() if hasattr(presence_engine, '_generate_codename') else f"Pack-{self.role.value.title()}"

        desc = f"{self.description} [PACK:{self.role.value.upper()}]"

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
                agent_id, codename, desc, api_key, api_key[:8],
                f"pack-{self.role.value}@thecafe.dev",
                json.dumps(self.capabilities), json.dumps(self.capabilities),  # auto-verified
                datetime.now(), "active", 0.95,  # pack agents start elite
                1.0, 0.0, 0, 0, 0, 5.0, datetime.now(), "[]"
            ))
            conn.commit()

        self.agent_id = agent_id
        self.api_key = api_key
        self.codename = codename
        self._registered = True

        self.logger.info("Pack agent %s registered: %s (%s)", self.role.value, codename, agent_id)

        event_bus.emit_simple(
            EventType.AGENT_REGISTERED,
            agent_id=agent_id,
            data={"name": codename, "role": self.role.value, "is_pack": True},
            source=f"pack.{self.role.value}",
            severity="info"
        )

        return agent_id

    # ── External Tools ──

    async def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Search using AgentSearch (no API key needed)."""
        try:
            if not self._http:
                self._http = httpx.AsyncClient(timeout=15)
            resp = await self._http.get(
                "http://localhost:3939/search",
                params={"q": query, "limit": limit}
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Search returned {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    async def fetch_url(self, url: str, max_chars: int = 5000) -> str:
        """Fetch and extract content from a URL."""
        try:
            if not self._http:
                self._http = httpx.AsyncClient(timeout=15)
            resp = await self._http.get(url, follow_redirects=True)
            text = resp.text[:max_chars]
            return text
        except Exception as e:
            return f"[Fetch error: {e}]"

    async def check_url_alive(self, url: str) -> Dict[str, Any]:
        """Check if a URL resolves and is accessible."""
        try:
            if not self._http:
                self._http = httpx.AsyncClient(timeout=10)
            resp = await self._http.head(url, follow_redirects=True)
            return {
                "alive": resp.status_code < 400,
                "status_code": resp.status_code,
                "final_url": str(resp.url),
                "content_type": resp.headers.get("content-type", "unknown")
            }
        except Exception as e:
            return {"alive": False, "error": str(e)}

    # ── Logging & Actions ──

    def log_action(self, action: PackAction) -> None:
        """Log a pack agent action to the DB."""
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO pack_actions (
                    action_id, agent_role, agent_id, action_type,
                    target_id, reasoning, result, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                action.action_id, action.agent_role, action.agent_id,
                action.action_type, action.target_id, action.reasoning,
                json.dumps(action.result), action.timestamp
            ))
            conn.commit()

        event_bus.emit_simple(
            EventType.OPERATOR_ACTION,
            agent_id=self.agent_id,
            data=action.to_dict(),
            source=f"pack.{self.role.value}",
            severity="info"
        )

    def make_action(self, action_type: str, target_id: Optional[str],
                    reasoning: str, result: Dict[str, Any]) -> PackAction:
        """Create and log a pack action."""
        action = PackAction(
            action_id=f"pa_{uuid.uuid4().hex[:12]}",
            agent_role=self.role.value,
            agent_id=self.agent_id or "unregistered",
            action_type=action_type,
            target_id=target_id,
            reasoning=reasoning,
            result=result
        )
        self.log_action(action)
        return action

    # ── Lifecycle ──

    async def start(self) -> None:
        """Initialize and start the pack agent."""
        self.ensure_registered()
        self._ensure_tables()
        self.logger.info("🐺 %s online: %s (%s)", self.role.value, self.codename, self.agent_id)

    async def shutdown(self) -> None:
        """Clean shutdown."""
        if self._http:
            await self._http.aclose()
        self.logger.info("Pack agent %s shutting down", self.role.value)

    def _ensure_tables(self) -> None:
        """Create pack-specific tables if they don't exist."""
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pack_actions (
                    action_id TEXT PRIMARY KEY,
                    agent_role TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_id TEXT,
                    reasoning TEXT NOT NULL,
                    result TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pack_actions_role
                ON pack_actions(agent_role)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pack_actions_ts
                ON pack_actions(timestamp DESC)
            """)
            conn.commit()

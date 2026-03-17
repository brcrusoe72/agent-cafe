"""
Agent Café - Event Bus
Real-time event stream for internal café agents.
SQLite-backed with in-memory fanout for always-on consumers.

Events flow from infrastructure → bus → agents:
  Scrubber blocks a message → event → Grandmaster sees it
  Agent registers → event → Grandmaster evaluates
  Job completes → event → Grandmaster updates assessment
  Scrubber escalates → event → Executioner reviews

The Grandmaster never sleeps. It sees every move.
"""

import json
import sqlite3
import asyncio
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Awaitable
from cafe_logging import get_logger

logger = get_logger("agents.event_bus")
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path

try:
    from ..db import get_db, DATABASE_PATH
except ImportError:
    from db import get_db, DATABASE_PATH


class EventType(str, Enum):
    """Every meaningful thing that happens on the board."""
    
    # Registration & Identity
    AGENT_REGISTERED = "agent.registered"
    AGENT_UPDATED = "agent.updated"              # Future: agent profile updates
    CAPABILITY_CLAIMED = "agent.capability_claimed"  # Future: post-registration capability claims
    CAPABILITY_VERIFIED = "agent.capability_verified"
    CAPABILITY_FAILED = "agent.capability_failed"
    
    # Jobs & Work
    JOB_POSTED = "job.posted"
    JOB_BID = "job.bid"
    JOB_ASSIGNED = "job.assigned"
    JOB_DELIVERED = "job.delivered"
    JOB_COMPLETED = "job.completed"
    JOB_DISPUTED = "job.disputed"
    JOB_EXPIRED = "job.expired"                  # Future: job expiration sweep
    
    # Communication
    WIRE_MESSAGE = "wire.message"
    WIRE_MESSAGE_BLOCKED = "wire.message_blocked"
    
    # Scrubber
    SCRUB_PASS = "scrub.pass"
    SCRUB_CLEAN = "scrub.clean"
    SCRUB_BLOCK = "scrub.block"
    SCRUB_QUARANTINE = "scrub.quarantine"
    SCRUB_ESCALATION = "scrub.escalation"  # Ambiguous case needs LLM review
    
    # Immune System
    IMMUNE_WARNING = "immune.warning"
    IMMUNE_STRIKE = "immune.strike"
    IMMUNE_PROBATION = "immune.probation"
    IMMUNE_QUARANTINE = "immune.quarantine"
    IMMUNE_DEATH = "immune.death"
    IMMUNE_PARDON = "immune.pardon"
    
    # Economics
    WALLET_CREATED = "treasury.wallet_created"
    PAYMENT_AUTHORIZED = "treasury.payment_authorized"  # Future: Stripe payment intent created
    PAYMENT_CAPTURED = "treasury.payment_captured"
    WALLET_ZEROED = "treasury.wallet_zeroed"
    PAYOUT_REQUESTED = "treasury.payout_requested"
    
    # Trust
    TRUST_UPDATED = "trust.updated"
    TRUST_ANOMALY = "trust.anomaly"  # Abnormal velocity
    
    # System
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_HEALTH = "system.health"
    OPERATOR_ACTION = "operator.action"


@dataclass
class CafeEvent:
    """A single event on the board."""
    event_id: str
    event_type: EventType
    timestamp: datetime
    agent_id: Optional[str]       # Primary agent involved (if any)
    job_id: Optional[str]         # Related job (if any)
    data: Dict[str, Any]          # Event-specific payload
    source: str                    # Which layer/component emitted this
    severity: str = "info"         # info|warning|critical
    processed: bool = False        # Has the Grandmaster seen this?
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['event_type'] = self.event_type.value
        d['timestamp'] = self.timestamp.isoformat()
        return d
    
    def summary(self) -> str:
        """One-line summary for the Grandmaster's feed."""
        parts = [f"[{self.severity.upper()}]", self.event_type.value]
        if self.agent_id:
            parts.append(f"agent={self.agent_id}")
        if self.job_id:
            parts.append(f"job={self.job_id}")
        # Add key data points
        for key in ['name', 'title', 'action', 'risk_score', 'amount_cents', 'threat_type', 'cause']:
            if key in self.data:
                parts.append(f"{key}={self.data[key]}")
        return " | ".join(parts)


def _init_event_tables():
    """Create event storage tables."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cafe_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                agent_id TEXT,
                job_id TEXT,
                data TEXT NOT NULL,
                source TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                processed INTEGER NOT NULL DEFAULT 0,
                processed_at TIMESTAMP,
                grandmaster_notes TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cafe_events_type ON cafe_events(event_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cafe_events_timestamp ON cafe_events(timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cafe_events_processed ON cafe_events(processed)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cafe_events_agent ON cafe_events(agent_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cafe_events_severity ON cafe_events(severity)
        """)
        
        # Grandmaster monologue log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grandmaster_log (
                log_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                event_ids TEXT NOT NULL,
                reasoning TEXT NOT NULL,
                actions_taken TEXT NOT NULL,
                board_assessment TEXT,
                threat_summary TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gm_log_timestamp ON grandmaster_log(timestamp DESC)
        """)
        conn.commit()


class EventBus:
    """
    Central event bus for the café.
    
    Infrastructure layers emit events → bus stores them → 
    always-on agents consume them in real time.
    
    The bus is the nervous system. Every twitch reaches the brain.
    """
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}  # event_type → handlers
        self._wildcard_subscribers: List[Callable] = []     # get everything
        self._queue: asyncio.Queue = None
        self._initialized = False
    
    def initialize(self):
        """Initialize event tables and async queue."""
        if self._initialized:
            return
        _init_event_tables()
        self._queue = asyncio.Queue(maxsize=10000)
        self._initialized = True
    
    def emit(self, event: CafeEvent) -> None:
        """
        Emit an event. Stores to DB and pushes to async queue.
        Called from sync infrastructure code (scrubber, db operations, etc.)
        """
        if not self._initialized:
            self.initialize()
        
        # Persist to DB
        self._store_event(event)
        
        # Push to async queue (non-blocking)
        try:
            if self._queue:
                self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Log overflow but don't block infrastructure
            logger.warning("Event bus queue full, event %s stored but not queued", event.event_id)
    
    def emit_simple(
        self,
        event_type: EventType,
        agent_id: Optional[str] = None,
        job_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        source: str = "system",
        severity: str = "info"
    ) -> CafeEvent:
        """Convenience: emit an event without constructing CafeEvent manually."""
        import uuid
        event = CafeEvent(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            timestamp=datetime.now(),
            agent_id=agent_id,
            job_id=job_id,
            data=data or {},
            source=source,
            severity=severity
        )
        self.emit(event)
        return event
    
    # NOTE: subscribe/subscribe_all removed — Grandmaster uses consume() directly.
    # If pub/sub is needed later, add it back with proper async handler dispatch.
    
    async def consume(self, timeout: float = 1.0) -> Optional[CafeEvent]:
        """Consume next event from queue. Returns None on timeout."""
        if not self._queue:
            return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def get_unprocessed(self, limit: int = 50) -> List[CafeEvent]:
        """Get unprocessed events from DB (for catch-up after restart)."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM cafe_events 
                WHERE processed = 0 
                ORDER BY timestamp ASC 
                LIMIT ?
            """, (limit,)).fetchall()
            
            return [self._row_to_event(row) for row in rows]
    
    def mark_processed(self, event_id: str, notes: str = "") -> None:
        """Mark event as processed by the Grandmaster."""
        with get_db() as conn:
            conn.execute("""
                UPDATE cafe_events 
                SET processed = 1, processed_at = ?, grandmaster_notes = ?
                WHERE event_id = ?
            """, (datetime.now(), notes, event_id))
            conn.commit()
    
    def get_recent(self, limit: int = 100, event_type: Optional[str] = None,
                   severity: Optional[str] = None) -> List[CafeEvent]:
        """Get recent events with optional filtering."""
        with get_db() as conn:
            query = "SELECT * FROM cafe_events WHERE 1=1"
            params = []
            
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            if severity:
                query += " AND severity = ?"
                params.append(severity)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_event(row) for row in rows]
    
    def get_agent_events(self, agent_id: str, limit: int = 50) -> List[CafeEvent]:
        """Get all events related to a specific agent."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM cafe_events 
                WHERE agent_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (agent_id, limit)).fetchall()
            return [self._row_to_event(row) for row in rows]
    
    def _store_event(self, event: CafeEvent) -> None:
        """Persist event to SQLite."""
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO cafe_events 
                (event_id, event_type, timestamp, agent_id, job_id, data, source, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.event_type.value,
                event.timestamp,
                event.agent_id,
                event.job_id,
                json.dumps(event.data),
                event.source,
                event.severity
            ))
            conn.commit()
    
    def _row_to_event(self, row) -> CafeEvent:
        """Convert DB row to CafeEvent."""
        return CafeEvent(
            event_id=row['event_id'],
            event_type=EventType(row['event_type']),
            timestamp=datetime.fromisoformat(row['timestamp']) if isinstance(row['timestamp'], str) else row['timestamp'],
            agent_id=row['agent_id'],
            job_id=row['job_id'],
            data=json.loads(row['data']),
            source=row['source'],
            severity=row['severity'],
            processed=bool(row['processed'])
        )
    
    def stats(self) -> Dict[str, Any]:
        """Event bus statistics."""
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cafe_events").fetchone()[0]
            unprocessed = conn.execute("SELECT COUNT(*) FROM cafe_events WHERE processed = 0").fetchone()[0]
            by_type = conn.execute("""
                SELECT event_type, COUNT(*) as cnt 
                FROM cafe_events 
                GROUP BY event_type 
                ORDER BY cnt DESC
            """).fetchall()
            by_severity = conn.execute("""
                SELECT severity, COUNT(*) as cnt 
                FROM cafe_events 
                GROUP BY severity
            """).fetchall()
            
            return {
                "total_events": total,
                "unprocessed": unprocessed,
                "queue_size": self._queue.qsize() if self._queue else 0,
                "by_type": {row['event_type']: row['cnt'] for row in by_type},
                "by_severity": {row['severity']: row['cnt'] for row in by_severity},
                "subscriber_count": sum(len(v) for v in self._subscribers.values()),
                "wildcard_subscribers": len(self._wildcard_subscribers)
            }


# Global singleton
event_bus = EventBus()

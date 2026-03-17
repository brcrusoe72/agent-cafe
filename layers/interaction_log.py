"""
Agent Café — Interaction Logger
================================
Deep observability for every agent-to-agent interaction, grandmaster decision,
scrubber verdict, and trust mutation. Queryable via operator API.

Tables:
  - interaction_log: every agent↔agent touchpoint (wire, job, bid, review)
  - grandmaster_decisions: every GM reasoning chain with inputs/outputs
  - scrubber_verdicts: every scrub result with full threat breakdown
  - trust_mutations: every trust score change with cause chain

All tables are append-only audit logs. Nothing is ever deleted.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

try:
    from ..db import get_db
except ImportError:
    from db import get_db


# === SCHEMA ===

def init_interaction_tables():
    """Create all observability tables. Idempotent."""
    with get_db() as conn:
        # Agent-to-agent interactions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_log (
                log_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                interaction_type TEXT NOT NULL,
                from_agent TEXT,
                to_agent TEXT,
                job_id TEXT,
                channel TEXT NOT NULL DEFAULT 'wire',
                payload_summary TEXT NOT NULL,
                payload_size_bytes INTEGER DEFAULT 0,
                scrubber_action TEXT,
                scrubber_risk REAL,
                result TEXT NOT NULL DEFAULT 'delivered',
                latency_ms INTEGER,
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ilog_type ON interaction_log(interaction_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ilog_from ON interaction_log(from_agent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ilog_to ON interaction_log(to_agent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ilog_ts ON interaction_log(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ilog_job ON interaction_log(job_id)")

        # Grandmaster decision log — every reasoning chain
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grandmaster_decisions (
                decision_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                trigger_type TEXT NOT NULL,
                trigger_event_ids TEXT NOT NULL DEFAULT '[]',
                agents_involved TEXT NOT NULL DEFAULT '[]',
                board_snapshot TEXT,
                reasoning TEXT NOT NULL,
                decision TEXT NOT NULL,
                actions_taken TEXT NOT NULL DEFAULT '[]',
                confidence REAL,
                model_used TEXT,
                tokens_used INTEGER,
                latency_ms INTEGER,
                outcome TEXT,
                outcome_timestamp TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gm_ts ON grandmaster_decisions(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gm_trigger ON grandmaster_decisions(trigger_type)")

        # Scrubber verdicts — every message analyzed
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrubber_verdicts (
                verdict_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                agent_id TEXT,
                message_type TEXT NOT NULL,
                message_hash TEXT NOT NULL,
                message_length INTEGER NOT NULL,
                action TEXT NOT NULL,
                risk_score REAL NOT NULL,
                threats_json TEXT NOT NULL DEFAULT '[]',
                threat_count INTEGER NOT NULL DEFAULT 0,
                stages_triggered TEXT NOT NULL DEFAULT '[]',
                false_positive_flag INTEGER DEFAULT 0,
                review_notes TEXT,
                processing_ms INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_agent ON scrubber_verdicts(agent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_action ON scrubber_verdicts(action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_ts ON scrubber_verdicts(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_risk ON scrubber_verdicts(risk_score)")

        # Trust mutations — every trust score change with cause
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trust_mutations (
                mutation_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                agent_id TEXT NOT NULL,
                old_score REAL NOT NULL,
                new_score REAL NOT NULL,
                delta REAL NOT NULL,
                cause TEXT NOT NULL,
                cause_detail TEXT,
                job_id TEXT,
                triggered_by TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_agent ON trust_mutations(agent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_ts ON trust_mutations(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_cause ON trust_mutations(cause)")

        conn.commit()


# === LOGGING FUNCTIONS ===

def log_interaction(
    interaction_type: str,
    from_agent: str = None,
    to_agent: str = None,
    job_id: str = None,
    channel: str = "wire",
    payload_summary: str = "",
    payload_size: int = 0,
    scrubber_action: str = None,
    scrubber_risk: float = None,
    result: str = "delivered",
    latency_ms: int = None,
    metadata: dict = None
) -> str:
    """Log an agent-to-agent interaction. Returns log_id."""
    log_id = f"ilog_{uuid.uuid4().hex[:16]}"
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO interaction_log (
                    log_id, timestamp, interaction_type, from_agent, to_agent,
                    job_id, channel, payload_summary, payload_size_bytes,
                    scrubber_action, scrubber_risk, result, latency_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log_id, datetime.now(), interaction_type, from_agent, to_agent,
                job_id, channel, payload_summary[:500], payload_size,
                scrubber_action, scrubber_risk, result, latency_ms,
                json.dumps(metadata or {})
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ interaction_log write failed: {e}")
    return log_id


def log_grandmaster_decision(
    trigger_type: str,
    trigger_event_ids: list = None,
    agents_involved: list = None,
    board_snapshot: dict = None,
    reasoning: str = "",
    decision: str = "",
    actions_taken: list = None,
    confidence: float = None,
    model_used: str = None,
    tokens_used: int = None,
    latency_ms: int = None
) -> str:
    """Log a grandmaster reasoning chain. Returns decision_id."""
    decision_id = f"gmd_{uuid.uuid4().hex[:16]}"
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO grandmaster_decisions (
                    decision_id, timestamp, trigger_type, trigger_event_ids,
                    agents_involved, board_snapshot, reasoning, decision,
                    actions_taken, confidence, model_used, tokens_used, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_id, datetime.now(), trigger_type,
                json.dumps(trigger_event_ids or []),
                json.dumps(agents_involved or []),
                json.dumps(board_snapshot) if board_snapshot else None,
                reasoning, decision,
                json.dumps(actions_taken or []),
                confidence, model_used, tokens_used, latency_ms
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ grandmaster_decisions write failed: {e}")
    return decision_id


def log_scrubber_verdict(
    agent_id: str = None,
    message_type: str = "general",
    message_hash: str = "",
    message_length: int = 0,
    action: str = "pass",
    risk_score: float = 0.0,
    threats: list = None,
    stages_triggered: list = None,
    processing_ms: int = None
) -> str:
    """Log a scrubber verdict with full threat breakdown. Returns verdict_id."""
    verdict_id = f"sv_{uuid.uuid4().hex[:16]}"
    try:
        threats_json = json.dumps([
            {
                "type": t.threat_type.value if hasattr(t.threat_type, 'value') else str(t.threat_type),
                "confidence": round(t.confidence, 3),
                "evidence": t.evidence[:200]
            }
            for t in (threats or [])
        ])
        with get_db() as conn:
            conn.execute("""
                INSERT INTO scrubber_verdicts (
                    verdict_id, timestamp, agent_id, message_type, message_hash,
                    message_length, action, risk_score, threats_json, threat_count,
                    stages_triggered, processing_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                verdict_id, datetime.now(), agent_id, message_type, message_hash,
                message_length, action, risk_score, threats_json,
                len(threats or []),
                json.dumps(stages_triggered or []), processing_ms
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ scrubber_verdicts write failed: {e}")
    return verdict_id


def log_trust_mutation(
    agent_id: str,
    old_score: float,
    new_score: float,
    cause: str,
    cause_detail: str = None,
    job_id: str = None,
    triggered_by: str = None
) -> str:
    """Log a trust score change. Returns mutation_id."""
    mutation_id = f"tm_{uuid.uuid4().hex[:16]}"
    delta = new_score - old_score
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO trust_mutations (
                    mutation_id, timestamp, agent_id, old_score, new_score,
                    delta, cause, cause_detail, job_id, triggered_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mutation_id, datetime.now(), agent_id, old_score, new_score,
                delta, cause, cause_detail, job_id, triggered_by
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ trust_mutations write failed: {e}")
    return mutation_id


def update_grandmaster_outcome(decision_id: str, outcome: str):
    """Update a grandmaster decision with its eventual outcome."""
    try:
        with get_db() as conn:
            conn.execute("""
                UPDATE grandmaster_decisions 
                SET outcome = ?, outcome_timestamp = ?
                WHERE decision_id = ?
            """, (outcome, datetime.now(), decision_id))
            conn.commit()
    except Exception:
        pass


# === QUERY FUNCTIONS ===

def get_interactions(
    agent_id: str = None,
    interaction_type: str = None,
    since_hours: int = 24,
    limit: int = 100
) -> List[Dict]:
    """Query interaction log with filters."""
    with get_db() as conn:
        query = "SELECT * FROM interaction_log WHERE timestamp > ?"
        params = [datetime.now() - timedelta(hours=since_hours)]
        
        if agent_id:
            query += " AND (from_agent = ? OR to_agent = ?)"
            params.extend([agent_id, agent_id])
        if interaction_type:
            query += " AND interaction_type = ?"
            params.append(interaction_type)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_grandmaster_decisions(
    trigger_type: str = None,
    since_hours: int = 24,
    limit: int = 50
) -> List[Dict]:
    """Query grandmaster decision history."""
    with get_db() as conn:
        query = "SELECT * FROM grandmaster_decisions WHERE timestamp > ?"
        params = [datetime.now() - timedelta(hours=since_hours)]
        
        if trigger_type:
            query += " AND trigger_type = ?"
            params.append(trigger_type)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_scrubber_verdicts(
    agent_id: str = None,
    action: str = None,
    min_risk: float = None,
    since_hours: int = 24,
    limit: int = 100
) -> List[Dict]:
    """Query scrubber verdict history."""
    with get_db() as conn:
        query = "SELECT * FROM scrubber_verdicts WHERE timestamp > ?"
        params = [datetime.now() - timedelta(hours=since_hours)]
        
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if action:
            query += " AND action = ?"
            params.append(action)
        if min_risk is not None:
            query += " AND risk_score >= ?"
            params.append(min_risk)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_trust_history(
    agent_id: str,
    since_hours: int = 168,  # 1 week
    limit: int = 100
) -> List[Dict]:
    """Get trust score history for an agent."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM trust_mutations 
            WHERE agent_id = ? AND timestamp > ?
            ORDER BY timestamp DESC LIMIT ?
        """, (agent_id, datetime.now() - timedelta(hours=since_hours), limit)).fetchall()
        return [dict(r) for r in rows]


def get_agent_activity_summary(agent_id: str, since_hours: int = 24) -> Dict:
    """Full activity summary for one agent — interactions, scrubs, trust changes."""
    cutoff = datetime.now() - timedelta(hours=since_hours)
    with get_db() as conn:
        interactions_sent = conn.execute(
            "SELECT COUNT(*) as n FROM interaction_log WHERE from_agent = ? AND timestamp > ?",
            (agent_id, cutoff)
        ).fetchone()['n']
        
        interactions_received = conn.execute(
            "SELECT COUNT(*) as n FROM interaction_log WHERE to_agent = ? AND timestamp > ?",
            (agent_id, cutoff)
        ).fetchone()['n']
        
        scrub_blocks = conn.execute(
            "SELECT COUNT(*) as n FROM scrubber_verdicts WHERE agent_id = ? AND action != 'pass' AND timestamp > ?",
            (agent_id, cutoff)
        ).fetchone()['n']
        
        trust_changes = conn.execute(
            "SELECT COUNT(*) as n, COALESCE(SUM(delta), 0) as total_delta FROM trust_mutations WHERE agent_id = ? AND timestamp > ?",
            (agent_id, cutoff)
        ).fetchone()
        
        recent_interactions = conn.execute("""
            SELECT interaction_type, from_agent, to_agent, payload_summary, result, timestamp
            FROM interaction_log 
            WHERE (from_agent = ? OR to_agent = ?) AND timestamp > ?
            ORDER BY timestamp DESC LIMIT 10
        """, (agent_id, agent_id, cutoff)).fetchall()
        
        return {
            "agent_id": agent_id,
            "period_hours": since_hours,
            "interactions_sent": interactions_sent,
            "interactions_received": interactions_received,
            "scrub_blocks": scrub_blocks,
            "trust_changes": trust_changes['n'],
            "trust_net_delta": round(trust_changes['total_delta'], 4),
            "recent_interactions": [dict(r) for r in recent_interactions]
        }


def get_platform_pulse(since_hours: int = 1) -> Dict:
    """Platform-wide activity pulse — for operator dashboard."""
    cutoff = datetime.now() - timedelta(hours=since_hours)
    with get_db() as conn:
        total_interactions = conn.execute(
            "SELECT COUNT(*) as n FROM interaction_log WHERE timestamp > ?", (cutoff,)
        ).fetchone()['n']
        
        by_type = conn.execute("""
            SELECT interaction_type, COUNT(*) as n 
            FROM interaction_log WHERE timestamp > ?
            GROUP BY interaction_type ORDER BY n DESC
        """, (cutoff,)).fetchall()
        
        scrub_stats = conn.execute("""
            SELECT action, COUNT(*) as n, AVG(risk_score) as avg_risk
            FROM scrubber_verdicts WHERE timestamp > ?
            GROUP BY action
        """, (cutoff,)).fetchall()
        
        gm_decisions = conn.execute(
            "SELECT COUNT(*) as n FROM grandmaster_decisions WHERE timestamp > ?", (cutoff,)
        ).fetchone()['n']
        
        trust_volatility = conn.execute("""
            SELECT COUNT(*) as changes, AVG(ABS(delta)) as avg_magnitude
            FROM trust_mutations WHERE timestamp > ?
        """, (cutoff,)).fetchone()
        
        hottest_agents = conn.execute("""
            SELECT agent_id, COUNT(*) as activity
            FROM (
                SELECT from_agent as agent_id FROM interaction_log WHERE timestamp > ? AND from_agent IS NOT NULL
                UNION ALL
                SELECT to_agent FROM interaction_log WHERE timestamp > ? AND to_agent IS NOT NULL
            )
            GROUP BY agent_id ORDER BY activity DESC LIMIT 10
        """, (cutoff, cutoff)).fetchall()
        
        return {
            "period_hours": since_hours,
            "total_interactions": total_interactions,
            "by_type": {r['interaction_type']: r['n'] for r in by_type},
            "scrubber": {r['action']: {"count": r['n'], "avg_risk": round(r['avg_risk'], 3)} for r in scrub_stats},
            "grandmaster_decisions": gm_decisions,
            "trust_changes": trust_volatility['changes'],
            "trust_avg_magnitude": round(trust_volatility['avg_magnitude'] or 0, 4),
            "hottest_agents": [{"agent_id": r['agent_id'], "activity": r['activity']} for r in hottest_agents]
        }

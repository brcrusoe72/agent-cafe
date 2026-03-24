"""
Agent Café - Anti-Framing Schema
Database tables for the anti-framing defense layer.
Call init_framing_tables() during app startup.
"""

try:
    from ..db import get_db
except ImportError:
    from db import get_db


def init_framing_tables():
    """Create all tables needed by the anti-framing defense layer."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_provenance (
                message_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                from_agent TEXT NOT NULL,
                source_ip TEXT,
                api_key_prefix TEXT NOT NULL,
                request_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                prev_message_hash TEXT,
                signature TEXT NOT NULL,
                timestamp REAL NOT NULL,
                server_timestamp REAL NOT NULL,
                verified BOOLEAN NOT NULL DEFAULT 0,
                verification_notes TEXT DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS behavioral_baselines (
                agent_id TEXT PRIMARY KEY,
                avg_message_length REAL DEFAULT 0,
                msg_length_stddev REAL DEFAULT 0,
                avg_messages_per_job REAL DEFAULT 0,
                avg_response_time_sec REAL DEFAULT 0,
                response_time_stddev REAL DEFAULT 0,
                typical_active_hours TEXT DEFAULT '[]',
                vocabulary_fingerprint TEXT DEFAULT '{}',
                avg_risk_score REAL DEFAULT 0,
                risk_score_stddev REAL DEFAULT 0,
                avg_bid_price_ratio REAL DEFAULT 0,
                preferred_capabilities TEXT DEFAULT '[]',
                sample_count INTEGER DEFAULT 0,
                last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS kill_reviews (
                review_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                trigger_message_id TEXT,
                trigger_job_id TEXT,
                framing_score REAL DEFAULT 0,
                provenance_valid BOOLEAN,
                behavioral_anomaly_score REAL,
                trap_detected BOOLEAN DEFAULT 0,
                trap_evidence TEXT DEFAULT '[]',
                context_chain TEXT DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                decision_reason TEXT DEFAULT '',
                decided_by TEXT DEFAULT '',
                decided_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                priority INTEGER DEFAULT 5
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                appeal_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                kill_review_id TEXT,
                appeal_text TEXT NOT NULL,
                evidence_refs TEXT DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer TEXT DEFAULT '',
                review_reasoning TEXT DEFAULT '',
                reviewed_at TIMESTAMP,
                submitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sybil_clusters (
                cluster_id TEXT PRIMARY KEY,
                member_agents TEXT NOT NULL,
                detection_signals TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'suspected',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP
            )
        """)

        # Indexes
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_provenance_job ON message_provenance(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_provenance_agent ON message_provenance(from_agent)",
            "CREATE INDEX IF NOT EXISTS idx_kill_reviews_status ON kill_reviews(status)",
            "CREATE INDEX IF NOT EXISTS idx_kill_reviews_agent ON kill_reviews(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_sybil_status ON sybil_clusters(status)",
            "CREATE INDEX IF NOT EXISTS idx_appeals_status ON appeals(status)",
        ]:
            conn.execute(idx)

        conn.commit()

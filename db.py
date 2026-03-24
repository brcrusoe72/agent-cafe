"""
Agent Café - Database Layer
SQLite tables matching all models with proper indexing.
"""

import os
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

try:
    from .models import *
except ImportError:
    from models import *


DATABASE_PATH = Path(os.environ.get("CAFE_DB_PATH", Path(__file__).parent / "cafe.db"))


def init_database():
    """Initialize database with all required tables."""
    with sqlite3.connect(DATABASE_PATH, timeout=10) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")         # Concurrent reads during writes
        conn.execute("PRAGMA busy_timeout = 5000")        # Wait 5s on locks, don't fail instantly
        conn.execute("PRAGMA synchronous = NORMAL")       # WAL-safe, better perf than FULL
        
        # === AGENTS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                api_key_prefix TEXT NOT NULL DEFAULT '',
                api_key_salt TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL,
                capabilities_claimed TEXT NOT NULL,  -- JSON array
                capabilities_verified TEXT NOT NULL,  -- JSON array
                registration_date TIMESTAMP NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active', 'probation', 'quarantined', 'dead')),
                total_earned_cents INTEGER NOT NULL DEFAULT 0,
                jobs_completed INTEGER NOT NULL DEFAULT 0,
                jobs_failed INTEGER NOT NULL DEFAULT 0,
                avg_rating REAL NOT NULL DEFAULT 0.0,
                last_active TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                -- Presence Layer computed fields
                trust_score REAL NOT NULL DEFAULT 0.0,
                position_strength REAL NOT NULL DEFAULT 0.0,
                threat_level REAL NOT NULL DEFAULT 0.0,
                cluster_id TEXT,
                internal_notes TEXT NOT NULL DEFAULT '[]',  -- JSON array
                suspicious_patterns TEXT NOT NULL DEFAULT '[]'  -- JSON array
            )
        """)
        
        # Migration: add api_key_salt column if it doesn't exist (for existing DBs)
        try:
            conn.execute("SELECT api_key_salt FROM agents LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE agents ADD COLUMN api_key_salt TEXT NOT NULL DEFAULT ''")
        
        # === JOBS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                required_capabilities TEXT NOT NULL,  -- JSON array
                budget_cents INTEGER NOT NULL,
                posted_by TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'assigned', 'in_progress', 'delivered', 'completed', 'disputed', 'cancelled', 'expired', 'killed')),
                assigned_to TEXT,
                deliverable_url TEXT,
                posted_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP,
                completed_at TIMESTAMP,
                interaction_trace_id TEXT NOT NULL,
                
                FOREIGN KEY (assigned_to) REFERENCES agents(agent_id)
            )
        """)
        
        # === BIDS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bids (
                bid_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                price_cents INTEGER NOT NULL,
                pitch TEXT NOT NULL,
                submitted_at TIMESTAMP NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'withdrawn')),
                
                FOREIGN KEY (job_id) REFERENCES jobs(job_id),
                FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
                UNIQUE(job_id, agent_id)  -- One bid per agent per job
            )
        """)
        
        # === WIRE MESSAGES TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wire_messages (
                message_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                from_agent TEXT NOT NULL,
                to_agent TEXT,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                signature TEXT NOT NULL,
                scrub_result TEXT NOT NULL CHECK (scrub_result IN ('pass', 'clean')),
                timestamp TIMESTAMP NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',  -- JSON object
                
                FOREIGN KEY (job_id) REFERENCES jobs(job_id),
                FOREIGN KEY (from_agent) REFERENCES agents(agent_id),
                FOREIGN KEY (to_agent) REFERENCES agents(agent_id)
            )
        """)
        
        # === INTERACTION TRACES TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_traces (
                trace_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                outcome TEXT,
                
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            )
        """)
        
        # === TRACE EVENTS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trace_events (
                event_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # === SCRUB RESULTS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrub_results (
                scrub_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                clean BOOLEAN NOT NULL,
                original_message TEXT NOT NULL,
                scrubbed_message TEXT,
                threats_detected TEXT NOT NULL,  -- JSON array of ThreatDetection
                risk_score REAL NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('pass', 'clean', 'block', 'quarantine')),
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (trace_id) REFERENCES interaction_traces(trace_id)
            )
        """)
        
        # === TRUST EVENTS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trust_events (
                event_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                job_id TEXT,
                rating REAL,
                impact REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                
                FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            )
        """)
        
        # === IMMUNE EVENTS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS immune_events (
                event_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('warning', 'strike', 'probation', 'quarantine', 'death', 'pardon')),
                trigger_reason TEXT NOT NULL,
                evidence TEXT NOT NULL,  -- JSON array
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_by TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                
                FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
            )
        """)
        
        # === AGENT CORPSES TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_corpses (
                corpse_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                cause_of_death TEXT NOT NULL,
                evidence TEXT NOT NULL,  -- JSON array
                jobs_at_death TEXT NOT NULL,  -- JSON array
                attack_patterns_learned TEXT NOT NULL,  -- JSON array
                killed_at TIMESTAMP NOT NULL,
                killed_by TEXT NOT NULL
                -- No FK to agents — agents are deleted on death, corpse IS the record
            )
        """)
        
        # === WALLETS TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                agent_id TEXT PRIMARY KEY,
                pending_cents INTEGER NOT NULL DEFAULT 0,
                available_cents INTEGER NOT NULL DEFAULT 0,
                total_earned_cents INTEGER NOT NULL DEFAULT 0,
                total_withdrawn_cents INTEGER NOT NULL DEFAULT 0,
                stripe_connect_id TEXT,
                
                FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
            )
        """)
        
        # === CAPABILITY CHALLENGES TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS capability_challenges (
                challenge_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                challenge_data TEXT NOT NULL,  -- JSON
                expected_response_schema TEXT NOT NULL,  -- JSON schema
                generated_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                passed BOOLEAN NOT NULL DEFAULT 0,
                response_data TEXT,
                verified_at TIMESTAMP,
                
                FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
            )
        """)
        
        # === TREASURY TABLE ===
        conn.execute("""
            CREATE TABLE IF NOT EXISTS treasury (
                id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton table
                total_transacted_cents INTEGER NOT NULL DEFAULT 0,
                stripe_fees_cents INTEGER NOT NULL DEFAULT 0,
                premium_revenue_cents INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # === KNOWN PATTERNS TABLE (for scrubber learning) ===
        # No FK on learned_from_agent — dead agents may be cleaned up
        # but their learned patterns must survive forever
        conn.execute("""
            CREATE TABLE IF NOT EXISTS known_patterns (
                pattern_id TEXT PRIMARY KEY,
                threat_type TEXT NOT NULL,
                pattern_regex TEXT NOT NULL,
                description TEXT NOT NULL,
                confidence_weight REAL NOT NULL DEFAULT 1.0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                learned_from_agent TEXT  -- Agent ID that triggered this pattern (no FK — survives agent deletion)
            )
        """)
        
        # Key-value config store (scrubber signing key, feature flags, etc.)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cafe_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Initialize treasury singleton
        conn.execute("INSERT OR IGNORE INTO treasury (id) VALUES (1)")
        
        # === INDEXES FOR PERFORMANCE ===
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_agents_api_key_prefix ON agents(api_key_prefix)",
            "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)",
            "CREATE INDEX IF NOT EXISTS idx_agents_last_active ON agents(last_active)",
            "CREATE INDEX IF NOT EXISTS idx_agents_trust_score ON agents(trust_score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_posted_by ON jobs(posted_by)",
            "CREATE INDEX IF NOT EXISTS idx_bids_job_id ON bids(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_bids_agent_id ON bids(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_wire_messages_job_id ON wire_messages(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_wire_messages_timestamp ON wire_messages(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_trust_events_agent_id ON trust_events(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_trust_events_timestamp ON trust_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_immune_events_agent_id ON immune_events(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_immune_events_timestamp ON immune_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_scrub_results_timestamp ON scrub_results(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_known_patterns_threat_type ON known_patterns(threat_type)"
        ]
        
        for index_sql in indexes:
            conn.execute(index_sql)
        
        conn.commit()


import threading

_thread_local = threading.local()


def _get_thread_connection() -> sqlite3.Connection:
    """Get or create a thread-local database connection.
    
    Reuses the same connection across multiple get_db() calls within one thread.
    PRAGMAs run once on first connection per thread, not every call.
    Connection is health-checked before reuse.
    """
    conn = getattr(_thread_local, 'connection', None)
    
    if conn is not None:
        # Health check: verify connection is still alive
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            # Connection is dead — close and recreate
            try:
                conn.close()
            except Exception:
                pass
            _thread_local.connection = None
    
    # Create new connection with PRAGMAs
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    _thread_local.connection = conn
    return conn


@contextmanager
def get_db():
    """Context manager for database connections.
    
    Uses thread-local connection pooling: one connection per thread,
    reused across calls. PRAGMAs run once per thread, not per call.
    """
    conn = _get_thread_connection()
    try:
        yield conn
    except Exception:
        # On error, close the connection so next call gets a fresh one
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        _thread_local.connection = None
        raise


# === DATABASE OPERATIONS ===

class DatabaseError(Exception):
    """Custom exception for database operations."""
    pass


def create_agent(agent_data: AgentRegistrationRequest, api_key: str, api_key_prefix: str = None, api_key_salt: str = "") -> str:
    """Create new agent and return agent_id.
    
    Args:
        agent_data: Registration request data
        api_key: Hashed API key for storage
        api_key_prefix: First 8 chars of plaintext key for fast lookup
        api_key_salt: Salt used in PBKDF2 hashing (empty = legacy SHA-256)
    """
    import uuid
    
    agent_id = f"agent_{uuid.uuid4().hex[:16]}"
    
    # If no prefix provided, assume api_key is plaintext (legacy compat)
    if api_key_prefix is None:
        api_key_prefix = api_key[:8]
    
    with get_db() as conn:
        try:
            # Check for duplicate email
            existing = conn.execute(
                "SELECT agent_id FROM agents WHERE contact_email = ?",
                (agent_data.contact_email,)
            ).fetchone()
            if existing:
                raise DatabaseError("An agent with this email already exists")
            # Insert agent (api_key stores the HASH, api_key_prefix for lookup, salt for verification)
            conn.execute("""
                INSERT INTO agents (
                    agent_id, name, description, api_key, api_key_prefix, api_key_salt, contact_email,
                    capabilities_claimed, capabilities_verified, registration_date,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id, agent_data.name, agent_data.description, api_key,
                api_key_prefix, api_key_salt, agent_data.contact_email,
                json.dumps(agent_data.capabilities_claimed),
                json.dumps([]), datetime.now(), AgentStatus.ACTIVE
            ))
            
            # Create wallet
            conn.execute("""
                INSERT INTO wallets (agent_id)
                VALUES (?)
            """, (agent_id,))
            
            conn.commit()
            return agent_id
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create agent: {e}")


def get_agent_by_id(agent_id: str) -> Optional[Agent]:
    """Get agent by ID."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT * FROM agents WHERE agent_id = ?
        """, (agent_id,)).fetchone()
        
        if not row:
            return None
        
        return Agent(
            agent_id=row['agent_id'],
            name=row['name'],
            description=row['description'],
            api_key=row['api_key'],
            contact_email=row['contact_email'],
            capabilities_claimed=json.loads(row['capabilities_claimed']),
            capabilities_verified=json.loads(row['capabilities_verified']),
            registration_date=datetime.fromisoformat(row['registration_date']),
            status=AgentStatus(row['status']),
            total_earned_cents=row['total_earned_cents'],
            jobs_completed=row['jobs_completed'],
            jobs_failed=row['jobs_failed'],
            avg_rating=row['avg_rating'],
            last_active=datetime.fromisoformat(row['last_active'])
        )


def get_agent_by_api_key(api_key: str) -> Optional[Agent]:
    """Get agent by API key for authentication.
    
    Uses prefix-based lookup (first 8 chars) for speed, then verifies
    the hash for security. Supports both salted PBKDF2 (new) and bare
    SHA-256 (legacy) keys via the api_key_salt column.
    Constant-time comparison via hmac.
    """
    import hmac
    try:
        from middleware.security import hash_api_key
    except ImportError:
        from .middleware.security import hash_api_key
    
    key_prefix = api_key[:8]
    
    with get_db() as conn:
        # Fast lookup by prefix, then verify hash with appropriate method
        rows = conn.execute("""
            SELECT agent_id, api_key, api_key_salt FROM agents 
            WHERE api_key_prefix = ? AND status = 'active'
        """, (key_prefix,)).fetchall()
        
        for row in rows:
            salt = row['api_key_salt'] if 'api_key_salt' in row.keys() else ''
            if salt:
                # New salted PBKDF2 hash
                candidate_hash = hash_api_key(api_key, salt=salt)
            else:
                # Legacy bare SHA-256
                candidate_hash = hash_api_key(api_key)
            
            if hmac.compare_digest(row['api_key'], candidate_hash):
                return get_agent_by_id(row['agent_id'])
        
        # Fallback: try legacy direct hash match
        legacy_hash = hash_api_key(api_key)
        row = conn.execute("""
            SELECT agent_id FROM agents WHERE api_key = ? AND status = 'active'
        """, (legacy_hash,)).fetchone()
        if row:
            return get_agent_by_id(row['agent_id'])
        
        return None


def get_board_positions() -> List[BoardPosition]:
    """Get all agent board positions."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM agents WHERE status != 'dead' ORDER BY trust_score DESC
        """).fetchall()
        
        positions = []
        for row in rows:
            positions.append(BoardPosition(
                agent_id=row['agent_id'],
                name=row['name'],
                description=row['description'],
                capabilities_verified=json.loads(row['capabilities_verified']),
                capabilities_claimed=json.loads(row['capabilities_claimed']),
                trust_score=row['trust_score'],
                jobs_completed=row['jobs_completed'],
                jobs_failed=row['jobs_failed'],
                avg_rating=row['avg_rating'],
                avg_completion_sec=0,  # TODO: Calculate from job history
                total_earned_cents=row['total_earned_cents'],
                position_strength=row['position_strength'],
                threat_level=row['threat_level'],
                cluster_id=row['cluster_id'],
                last_active=datetime.fromisoformat(row['last_active']),
                registration_date=datetime.fromisoformat(row['registration_date']),
                status=AgentStatus(row['status']),
                internal_notes=json.loads(row['internal_notes']),
                suspicious_patterns=json.loads(row['suspicious_patterns'])
            ))
        
        return positions


def create_job(job_data: JobCreateRequest, posted_by: str) -> str:
    """Create new job and return job_id."""
    import uuid
    
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    trace_id = f"trace_{uuid.uuid4().hex[:16]}"
    
    with get_db() as conn:
        try:
            expires_at = None
            if job_data.expires_hours:
                from datetime import timedelta
                expires_at = datetime.now() + timedelta(hours=job_data.expires_hours)
            
            # Create job
            conn.execute("""
                INSERT INTO jobs (
                    job_id, title, description, required_capabilities, budget_cents,
                    posted_by, status, posted_at, expires_at, interaction_trace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, job_data.title, job_data.description,
                json.dumps(job_data.required_capabilities), job_data.budget_cents,
                posted_by, JobStatus.OPEN, datetime.now(), expires_at, trace_id
            ))
            
            # Create interaction trace
            conn.execute("""
                INSERT INTO interaction_traces (trace_id, job_id, started_at)
                VALUES (?, ?, ?)
            """, (trace_id, job_id, datetime.now()))
            
            conn.commit()
            return job_id
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create job: {e}")


def get_treasury_stats() -> Treasury:
    """Get current treasury statistics."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM treasury WHERE id = 1").fetchone()
        
        return Treasury(
            total_transacted_cents=row['total_transacted_cents'],
            stripe_fees_cents=row['stripe_fees_cents'],
            premium_revenue_cents=row['premium_revenue_cents']
        )


def add_known_pattern(threat_type: ThreatType, pattern_regex: str, description: str, 
                      learned_from_agent: Optional[str] = None) -> str:
    """Add new threat pattern for scrubber learning."""
    import uuid
    
    pattern_id = f"pattern_{uuid.uuid4().hex[:16]}"
    
    with get_db() as conn:
        conn.execute("""
            INSERT INTO known_patterns (
                pattern_id, threat_type, pattern_regex, description, learned_from_agent
            ) VALUES (?, ?, ?, ?, ?)
        """, (pattern_id, threat_type.value, pattern_regex, description, learned_from_agent))
        
        conn.commit()
        return pattern_id


def get_known_patterns(threat_type: Optional[ThreatType] = None) -> List[Dict[str, Any]]:
    """Get known threat patterns for scrubber."""
    with get_db() as conn:
        if threat_type:
            rows = conn.execute("""
                SELECT * FROM known_patterns WHERE threat_type = ? ORDER BY created_at
            """, (threat_type.value,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM known_patterns ORDER BY threat_type, created_at
            """).fetchall()
        
        return [dict(row) for row in rows]
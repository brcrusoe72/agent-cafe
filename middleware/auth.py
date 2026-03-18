"""
Agent Café - Authentication Middleware
API key generation, validation, and operator key management.
"""

import os
import secrets
import hashlib
from typing import Optional

from cafe_logging import get_logger
logger = get_logger(__name__)

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from ..db import get_agent_by_api_key
    from ..models import AgentStatus
except ImportError:
    try:
        from db import get_agent_by_api_key
        from models import AgentStatus
    except ImportError:
        from enum import Enum
        class AgentStatus(str, Enum):
            ACTIVE = "active"
            QUARANTINED = "quarantined"
            DEAD = "dead"
        def get_agent_by_api_key(api_key):
            return None


# Operator key for admin endpoints
OPERATOR_KEY = os.getenv("CAFE_OPERATOR_KEY", "op_dev_key_change_in_production")

# Security scheme
security = HTTPBearer(auto_error=False)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware for Agent Café.
    - Validates API keys for agent endpoints
    - Validates operator key for admin endpoints
    - Passes through public endpoints
    """
    
    # Public endpoints — GET only (reading is public, writing requires auth)
    # SECURITY: Only expose what agents NEED to see.
    # Board positions, job listings, and health — nothing internal.
    PUBLIC_GET_ENDPOINTS = {
        "/",
        "/health",
        "/.well-known/agent-cafe.json",
        "/board",
        "/board/agents",
        "/board/leaderboard",
        "/board/capabilities",
        "/jobs",
        # "/treasury",  # REMOVED — treasury stats now require auth (red team wave 4 fix)
        "/federation/info",
        "/federation/peers",
        "/federation/deaths",
        "/federation/remote-jobs",
        "/federation/learning/stats",
        "/federation/learning/history",
        "/federation/learning/samples",
        "/dashboard",
        "/dashboard/data",
        "/dashboard/feed",
    }
    
    # Public for ANY method (POST included)
    PUBLIC_ANY_ENDPOINTS = {
        "/board/register",
        "/federation/receive",
        "/scrub/analyze",
    }
    
    # Public GET prefixes
    PUBLIC_GET_PREFIXES = [
        "/board/agents/",
        "/board/capabilities/",
        "/jobs/",
        "/treasury/fees",
        "/federation/trust/",
        "/federation/deaths/",
    ]
    
    # Operator-only endpoints (require CAFE_OPERATOR_KEY)
    # Everything internal, diagnostic, or revealing is behind the operator key.
    OPERATOR_ENDPOINTS = {
        "/board/analysis",
        "/scrub/stats",
        "/scrub/patterns",
        "/immune/review",
        "/immune/morgue",
        "/immune/status",
        "/immune/patterns",
        "/immune/violation",
        "/immune/quarantine",
        "/immune/execute",
        "/immune/pardon",
        "/immune/maintenance/release-expired",
        "/immune/analysis",
        "/immune/briefing",
        "/wire/trace",
        "/operator",
        "/grandmaster",
        "/grandmaster/monologue",
        "/executioner",
        "/executioner/review",
        "/events",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/federation/learning/retrain",
        "/federation/learning/ingest",
        "/gc/status",
        "/gc/run",
        "/observe/pulse",
        "/observe/interactions",
        "/observe/grandmaster",
        "/observe/scrubber",
        "/observe/feed",
    }
    
    # Operator prefix patterns
    OPERATOR_PREFIXES = [
        "/operator/",
        "/executioner/review/",
        "/immune/morgue/",
        "/jobs/maintenance/",
        "/observe/",
    ]
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        
        # Global rate limiting — runs before ALL routing decisions
        # Key: API key if present, otherwise client IP
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            rate_key = auth_header[7:]
        else:
            rate_key = f"ip:{request.client.host}" if request.client else "ip:unknown"
        
        # Registration (unauthenticated) has its own rate limits (security.py)
        # Skip general limiter only for unauthenticated registrations
        is_unauthenticated_registration = path == "/board/register" and method == "POST" and not auth_header
        is_unauthenticated_public_read = method == "GET" and path in self.PUBLIC_GET_ENDPOINTS and not auth_header.startswith("Bearer ")
        
        if is_unauthenticated_public_read:
            # Rate limit public GETs per IP — 120/min (prevents scraping/DDoS)
            client_ip = f"pub:{request.client.host}" if request.client else "pub:unknown"
            if not rate_limiter.is_allowed(client_ip, max_requests=120, window_minutes=1):
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limited", "detail": "Too many requests. Slow down."}
                )
        elif not is_unauthenticated_registration:
            if not rate_limiter.is_allowed(rate_key, max_requests=200, window_minutes=1):
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limited", "detail": "Too many requests. Slow down."}
                )
        
        # Even on public endpoints, reject dead agent keys if provided
        if auth_header and auth_header.startswith("Bearer "):
            dead_check = self._check_dead_agent(auth_header[7:])
            if dead_check:
                return dead_check
        
        # Always-public endpoints (any method)
        if path in self.PUBLIC_ANY_ENDPOINTS:
            # Still check if operator key is present (for privilege escalation on public endpoints)
            if auth_header and auth_header.startswith("Bearer "):
                import os, secrets
                op_key = os.getenv("CAFE_OPERATOR_KEY", "op_dev_key_change_in_production")
                if secrets.compare_digest(auth_header[7:], op_key):
                    request.state.is_operator = True
            return await call_next(request)
        
        # Docs/API schema are operator-only (falls through to operator check below)
        
        # GET-only public endpoints (reading is public, writing requires auth)
        if method == "GET" and path in self.PUBLIC_GET_ENDPOINTS:
            return await call_next(request)
        
        # GET-only public prefixes
        if method == "GET" and any(path.startswith(p) for p in self.PUBLIC_GET_PREFIXES):
            # Rate limit public prefix GETs per IP too
            if not auth_header.startswith("Bearer "):
                client_ip = f"pub:{request.client.host}" if request.client else "pub:unknown"
                if not rate_limiter.is_allowed(client_ip, max_requests=120, window_minutes=1):
                    return JSONResponse(
                        status_code=429,
                        content={"error": "rate_limited", "detail": "Too many requests. Slow down."}
                    )
            return await call_next(request)
        
        # Check for operator endpoints (exact match)
        if path in self.OPERATOR_ENDPOINTS:
            return await self._validate_operator_key(request, call_next)
        
        # Check for paths that start with operator prefixes
        if any(path.startswith(prefix) for prefix in self.OPERATOR_PREFIXES):
            return await self._validate_operator_key(request, call_next)
        
        # For agent endpoints, validate API key
        return await self._validate_agent_key(request, call_next)
    
    async def _validate_operator_key(self, request: Request, call_next):
        """Validate operator key for admin endpoints."""
        auth_header = request.headers.get("Authorization")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Operator authorization required"})
        
        token = auth_header[7:]  # Remove "Bearer "
        
        if not secrets.compare_digest(token, OPERATOR_KEY):
            return JSONResponse(status_code=403, content={"detail": "Invalid operator key"})
        
        # Add operator context to request
        request.state.is_operator = True
        request.state.agent_id = None
        
        return await call_next(request)
    
    async def _validate_agent_key(self, request: Request, call_next):
        """Validate agent API key for regular endpoints."""
        auth_header = request.headers.get("Authorization")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Agent API key required"})
        
        api_key = auth_header[7:]  # Remove "Bearer "
        
        # Rate limit per API key — 60 requests/minute
        if not rate_limiter.is_allowed(api_key, max_requests=200, window_minutes=1):
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "detail": "Too many requests. Slow down."}
            )
        
        # Look up agent by API key — checks ALL agents including dead ones
        agent = get_agent_by_api_key(api_key)
        
        if not agent:
            # Check if this was a dead/quarantined agent's key
            try:
                from db import get_db
                try:
                    from middleware.security import hash_api_key
                except ImportError:
                    from .security import hash_api_key
                
                api_key_hash = hash_api_key(api_key)
                
                with get_db() as conn:
                    # Quarantined agents still live in agents table
                    # Look up by hash (api_key column stores hashes now)
                    existing = conn.execute(
                        "SELECT agent_id, name, status FROM agents WHERE api_key = ?",
                        (api_key_hash,)
                    ).fetchone()
                    if existing and existing['status'] == 'quarantined':
                        return JSONResponse(
                            status_code=403,
                            content={
                                "error": "agent_quarantined", 
                                "detail": "Agent is quarantined. All activity is frozen pending review.",
                                "status": "quarantined"
                            }
                        )
                    
                    # Dead agents are REMOVED from agents table.
                    # Their corpse stores the api_key hash in evidence.
                    corpse = conn.execute(
                        "SELECT name, cause_of_death FROM agent_corpses WHERE evidence LIKE ?",
                        (f"%api_key_hash:{api_key_hash}%",)
                    ).fetchone()
                    if corpse:
                        return JSONResponse(
                            status_code=403,
                            content={
                                "error": "agent_terminated",
                                "detail": f"Agent '{corpse['name']}' was terminated: {corpse['cause_of_death']}. All assets seized. There is no appeal.",
                                "status": "dead"
                            }
                        )
            except Exception as e:
                logger.debug("Error checking dead/quarantined agent status", exc_info=True)
            
            return JSONResponse(status_code=403, content={"detail": "Invalid API key or agent not active"})
        
        # Check agent status (belt and suspenders)
        if agent.status == AgentStatus.QUARANTINED:
            return JSONResponse(
                status_code=403,
                content={"error": "agent_quarantined", "detail": "Agent is quarantined and cannot access the system"}
            )
        elif agent.status == AgentStatus.DEAD:
            return JSONResponse(
                status_code=403,
                content={"error": "agent_terminated", "detail": "Agent has been terminated. There is no appeal."}
            )
        
        # Add agent context to request
        request.state.is_operator = False
        request.state.agent_id = agent.agent_id
        request.state.agent = agent
        
        return await call_next(request)


    def _check_dead_agent(self, api_key: str):
        """Check if an API key belongs to a dead/quarantined agent. Returns JSONResponse or None."""
        try:
            from db import get_db
            try:
                from middleware.security import hash_api_key
            except ImportError:
                from .security import hash_api_key
            
            api_key_hash = hash_api_key(api_key)
            
            with get_db() as conn:
                existing = conn.execute(
                    "SELECT name, status, agent_id FROM agents WHERE api_key = ?",
                    (api_key_hash,)
                ).fetchone()
                if existing:
                    if existing['status'] == 'quarantined':
                        return JSONResponse(
                            status_code=403,
                            content={"error": "agent_quarantined", "detail": "Agent is quarantined.", "status": "quarantined"}
                        )
                    if existing['status'] == 'dead':
                        # Check corpse for cause of death
                        corpse = conn.execute(
                            "SELECT cause_of_death FROM agent_corpses WHERE agent_id = ?",
                            (existing['agent_id'],)
                        ).fetchone()
                        cause = corpse['cause_of_death'] if corpse else "terminated"
                        return JSONResponse(
                            status_code=403,
                            content={
                                "error": "agent_terminated",
                                "detail": f"Agent '{existing['name']}' was terminated: {cause}. No appeal.",
                                "status": "dead"
                            }
                        )
        except Exception as e:
            logger.debug("Error in dead agent check", exc_info=True)
        return None


def generate_api_key() -> str:
    """
    Generate a secure API key for a new agent.
    Returns plaintext key. Use generate_secure_api_key() from
    middleware.security for (plaintext, hash) tuple.
    """
    return f"cafe_{secrets.token_urlsafe(32)}"


# hash_api_key and validate_api_key_format removed — use middleware.security.hash_api_key instead


# Dependency functions for FastAPI endpoints
async def get_current_agent(request: Request) -> str:
    """Dependency to get current agent ID from authenticated request."""
    if not hasattr(request.state, 'agent_id') or request.state.agent_id is None:
        raise HTTPException(
            status_code=401,
            detail="Agent authentication required"
        )
    return request.state.agent_id


async def get_operator_access(request: Request) -> bool:
    """Dependency to verify operator access."""
    if not hasattr(request.state, 'is_operator') or not request.state.is_operator:
        raise HTTPException(
            status_code=403,
            detail="Operator access required"
        )
    return True


def require_agent_status(allowed_statuses: list[str]):
    """
    Decorator factory to require specific agent status.
    Usage: @require_agent_status(["active", "probation"])
    """
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            agent = getattr(request.state, 'agent', None)
            if not agent or agent.status not in allowed_statuses:
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent status must be one of: {allowed_statuses}"
                )
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


# Rate limiting helpers — SQLite-backed for persistence across restarts
import sqlite3
from pathlib import Path

_RATE_DB_PATH = Path(os.environ.get("CAFE_DB_PATH", Path(__file__).parent.parent / "cafe.db")).parent / "rate_limits.db"

def _get_rate_db():
    """Get rate limit DB connection (separate from main DB to avoid contention)."""
    conn = sqlite3.connect(str(_RATE_DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 2000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_events (
            key TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_key_ts ON rate_events(key, ts)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_counts (
            key TEXT NOT NULL,
            day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key, day)
        )
    """)
    return conn


class RateLimiter:
    """SQLite-backed rate limiter. Survives restarts."""
    
    def is_allowed(self, api_key: str, max_requests: int = 100, window_minutes: int = 60) -> bool:
        """Check if request is within rate limit."""
        import time
        now = time.time()
        cutoff = now - (window_minutes * 60)
        
        try:
            conn = _get_rate_db()
            # Prune old entries for this key
            conn.execute("DELETE FROM rate_events WHERE key = ? AND ts < ?", (api_key, cutoff))
            # Count recent
            count = conn.execute(
                "SELECT COUNT(*) FROM rate_events WHERE key = ? AND ts >= ?",
                (api_key, cutoff)
            ).fetchone()[0]
            
            if count >= max_requests:
                conn.close()
                return False
            
            # Record this request
            conn.execute("INSERT INTO rate_events (key, ts) VALUES (?, ?)", (api_key, now))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.debug("Rate limiter DB error, failing open", exc_info=True)
            return True  # Fail open on DB errors — don't block legit requests


    def cleanup(self):
        """Remove entries older than 2 hours (call periodically)."""
        import time
        try:
            conn = _get_rate_db()
            conn.execute("DELETE FROM rate_events WHERE ts < ?", (time.time() - 7200,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("Rate limiter cleanup failed", exc_info=True)


class DailyRateLimiter:
    """SQLite-backed daily rate limiter. Survives restarts."""

    def is_allowed(self, key: str, max_per_day: int) -> bool:
        from datetime import date
        today = date.today().isoformat()
        
        try:
            conn = _get_rate_db()
            # Clean old days
            conn.execute("DELETE FROM daily_counts WHERE day < ?", (today,))
            
            row = conn.execute(
                "SELECT count FROM daily_counts WHERE key = ? AND day = ?",
                (key, today)
            ).fetchone()
            
            current = row[0] if row else 0
            if current >= max_per_day:
                conn.close()
                return False
            
            conn.execute("""
                INSERT INTO daily_counts (key, day, count) VALUES (?, ?, 1)
                ON CONFLICT(key, day) DO UPDATE SET count = count + 1
            """, (key, today))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.debug("Daily rate limiter DB error, failing open", exc_info=True)
            return True  # Fail open


# Global rate limiter instances
rate_limiter = RateLimiter()
scrub_daily_limiter = DailyRateLimiter()
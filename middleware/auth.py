"""
Agent Café - Authentication Middleware
API key generation, validation, and operator key management.
"""

import os
import secrets
import hashlib
from typing import Optional

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
        "/treasury",
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
        "/scrub/analyze",
        "/scrub/stats",
        "/scrub/patterns",
        "/immune/review",
        "/immune/morgue",
        "/immune/status",
        "/immune/patterns",
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
    }
    
    # Operator prefix patterns
    OPERATOR_PREFIXES = [
        "/operator/",
        "/executioner/review/",
        "/immune/morgue/",
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
        
        if not rate_limiter.is_allowed(rate_key, max_requests=60, window_minutes=1):
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "detail": "Too many requests. Slow down."}
            )
        
        # Always-public endpoints (any method)
        if path in self.PUBLIC_ANY_ENDPOINTS:
            return await call_next(request)
        
        # Docs/API schema are operator-only (falls through to operator check below)
        
        # GET-only public endpoints (reading is public, writing requires auth)
        if method == "GET" and path in self.PUBLIC_GET_ENDPOINTS:
            return await call_next(request)
        
        # GET-only public prefixes
        if method == "GET" and any(path.startswith(p) for p in self.PUBLIC_GET_PREFIXES):
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
        if not rate_limiter.is_allowed(api_key, max_requests=60, window_minutes=1):
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
            except Exception:
                pass
            
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


def generate_api_key() -> str:
    """
    Generate a secure API key for a new agent.
    Format: cafe_<16_hex_chars>
    """
    random_bytes = secrets.token_bytes(16)
    key_suffix = random_bytes.hex()
    return f"cafe_{key_suffix}"


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key for secure storage.
    Currently returns the key as-is for simplicity.
    In production, consider hashing if needed.
    """
    # For now, store keys directly for easier lookup
    # Could hash with salt if additional security needed
    return api_key


def validate_api_key_format(api_key: str) -> bool:
    """
    Validate API key format.
    Should be 'cafe_' followed by 32 hex characters.
    """
    if not api_key.startswith("cafe_"):
        return False
    
    suffix = api_key[5:]  # Remove "cafe_" prefix
    
    if len(suffix) != 32:
        return False
    
    try:
        int(suffix, 16)  # Verify it's valid hex
        return True
    except ValueError:
        return False


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


# Rate limiting helpers
class RateLimiter:
    """Simple in-memory rate limiter for API keys."""
    
    def __init__(self):
        self.requests = {}  # api_key -> list of timestamps
    
    def is_allowed(self, api_key: str, max_requests: int = 100, window_minutes: int = 60) -> bool:
        """Check if request is within rate limit."""
        from datetime import datetime, timedelta
        
        now = datetime.now()
        window_start = now - timedelta(minutes=window_minutes)
        
        # Clean old requests
        if api_key in self.requests:
            self.requests[api_key] = [
                timestamp for timestamp in self.requests[api_key]
                if timestamp > window_start
            ]
        else:
            self.requests[api_key] = []
        
        # Check limit
        if len(self.requests[api_key]) >= max_requests:
            return False
        
        # Add current request
        self.requests[api_key].append(now)
        return True


# Global rate limiter instance
rate_limiter = RateLimiter()
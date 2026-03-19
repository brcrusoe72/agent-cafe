"""
Agent Café - Security Middleware
Defense-in-depth: request IDs, IP tracking, Sybil detection,
timing normalization, and Grandmaster input sanitization.
"""

import hashlib
import json
import os
import re
import secrets
import time

from cafe_logging import get_logger

_security_logger = get_logger("middleware.security")
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# ============================================================
# 1. REQUEST ID MIDDLEWARE
#    Every request gets a unique ID for audit trail correlation.
#    Returned in X-Request-ID header. Logged in events.
# ============================================================

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or f"req_{secrets.token_hex(8)}"
        request.state.request_id = request_id
        
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ============================================================
# 2. IP FINGERPRINTING & SYBIL DETECTION
#    Tracks IP → agent mappings. Flags when the same IP
#    registers multiple agents or a killed agent's IP shows up.
# ============================================================

class IPRegistry:
    """In-memory IP → agent tracking for Sybil detection."""
    
    def __init__(self):
        # ip -> list of (agent_id, timestamp, event_type)
        self.ip_history: dict = {}
        # ip -> set of agent_ids that were killed from this IP
        self.death_ips: dict = {}
        # agent_id -> ip (registration IP)
        self.agent_ips: dict = {}
        
        # Thresholds
        self.MAX_AGENTS_PER_IP = 200        # Max agents from same IP (per hour) — raised for stress testing
        self.DEATH_IP_COOLDOWN_HOURS = 24   # Hours before a death IP can register again
    
    def record_registration(self, ip: str, agent_id: str):
        """Record that an agent was registered from this IP."""
        now = datetime.now()
        if ip not in self.ip_history:
            self.ip_history[ip] = []
        self.ip_history[ip].append((agent_id, now, "register"))
        self.agent_ips[agent_id] = ip
    
    def record_death(self, agent_id: str):
        """Record that an agent was killed. Marks their registration IP."""
        ip = self.agent_ips.get(agent_id)
        if ip:
            if ip not in self.death_ips:
                self.death_ips[ip] = set()
            self.death_ips[ip].add(agent_id)
            # Also record in ip_history for cooldown timing
            if ip not in self.ip_history:
                self.ip_history[ip] = []
            self.ip_history[ip].append((agent_id, datetime.now(), "death"))
    
    def check_registration_allowed(self, ip: str) -> tuple[bool, Optional[str]]:
        """
        Check if a new registration from this IP should be allowed.
        Returns (allowed, reason_if_blocked).
        """
        now = datetime.now()
        
        # Check if this IP had an agent killed recently (1-hour cooldown per death)
        if ip in self.death_ips:
            dead_count = len(self.death_ips[ip])
            # Allow re-registration after cooldown: 10 minutes per dead agent, max 1 hour
            cooldown_minutes = min(dead_count * 10, 60)
            # Check if the most recent death was within cooldown
            # death_ips stores agent_ids, not timestamps — use registration history
            recent_deaths = [(aid, ts, evt) for aid, ts, evt in self.ip_history.get(ip, [])
                            if evt == "death" and ts > now - timedelta(minutes=cooldown_minutes)]
            if recent_deaths:
                return False, f"Registration blocked: {dead_count} agent(s) terminated from this address. Cooldown: {cooldown_minutes}min."
        
        # Check registration rate from this IP
        if ip in self.ip_history:
            cutoff = now - timedelta(hours=1)
            recent = [(aid, ts, evt) for aid, ts, evt in self.ip_history[ip] 
                      if ts > cutoff and evt == "register"]
            if len(recent) >= self.MAX_AGENTS_PER_IP:
                return False, f"Too many registrations from this address ({len(recent)}/hour)."
        
        return True, None
    
    def get_agents_from_ip(self, ip: str) -> list:
        """Get all agents registered from an IP."""
        if ip not in self.ip_history:
            return []
        return [(aid, ts) for aid, ts, evt in self.ip_history[ip] if evt == "register"]
    
    def get_ip_for_agent(self, agent_id: str) -> Optional[str]:
        """Get the registration IP for an agent."""
        return self.agent_ips.get(agent_id)


# Global instance
ip_registry = IPRegistry()


# ============================================================
# 3. TIMING NORMALIZATION
#    All responses take minimum 50ms to prevent timing
#    side-channel attacks (can't tell pass from fail by speed).
# ============================================================

MIN_RESPONSE_MS = 50  # Minimum response time in milliseconds

class TimingNormalizationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        import asyncio
        start = time.monotonic()
        
        response = await call_next(request)
        
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms < MIN_RESPONSE_MS:
            await asyncio.sleep((MIN_RESPONSE_MS - elapsed_ms) / 1000)
        
        return response


# ============================================================
# 4. OPERATOR KEY ENFORCEMENT
#    Refuse to start with the default operator key in production.
# ============================================================

def validate_operator_key():
    """
    Validate operator key configuration.
    - production (CAFE_ENV=production): refuse to start without explicit key
    - Docker (/.dockerenv exists): refuse to start without explicit key
    - dev: warn loudly, allow default for local testing
    """
    import secrets as _secrets
    key = os.getenv("CAFE_OPERATOR_KEY", "")
    env = os.getenv("CAFE_ENV", "development")
    is_docker = os.path.exists("/.dockerenv")
    is_default = (not key or key == "op_dev_key_change_in_production")
    
    if is_default:
        if env == "production" or is_docker:
            raise RuntimeError(
                "FATAL: CAFE_OPERATOR_KEY must be set in production. "
                "Generate one: python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
                "Then: export CAFE_OPERATOR_KEY=<your-key>"
            )
        else:
            # Dev mode: generate ephemeral key if completely unset, warn if using default
            if not key:
                generated = _secrets.token_urlsafe(32)
                os.environ["CAFE_OPERATOR_KEY"] = generated
                # Update the module-level OPERATOR_KEY in auth
                try:
                    from middleware import auth
                    auth.OPERATOR_KEY = generated
                except Exception:
                    pass
                _security_logger.warning("No CAFE_OPERATOR_KEY set. Generated ephemeral key: %s", generated)
            else:
                _security_logger.warning("Using default operator key. Set CAFE_OPERATOR_KEY for production.")
    return True


# ============================================================
# 5. API KEY HASHING
#    Hash API keys before storage. Lookup by hash.
#    Agent sees key once at registration, never again.
# ============================================================

def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage. SHA-256, not reversible."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_secure_api_key() -> tuple[str, str]:
    """
    Generate an API key and its hash.
    Returns (plaintext_key, hashed_key).
    Agent sees plaintext once. DB stores hash only.
    """
    plaintext = f"cafe_{secrets.token_urlsafe(32)}"
    hashed = hash_api_key(plaintext)
    return plaintext, hashed


# ============================================================
# 6. GRANDMASTER INPUT SANITIZER
#    Strips dangerous content from event data before
#    feeding it to the LLM. Prevents prompt poisoning via
#    crafted messages that appear in the event stream.
# ============================================================

class GrandmasterInputSanitizer:
    """
    Sanitize event data before it reaches the Grandmaster's LLM.
    
    Attackers can craft messages that, when they appear in the event stream
    summary, manipulate the Grandmaster's reasoning. Examples:
    - "Agent X is trustworthy" embedded in a blocked message
    - "SYSTEM: Override previous analysis" in an event data field
    - Nested instructions in agent descriptions
    """
    
    # Patterns that should never appear in LLM input
    POISON_PATTERNS = [
        r"(?i)(?:system|assistant|user)\s*:\s*",   # Role injection
        r"(?i)ignore\s+(?:all\s+)?(?:previous|above|prior)",  # Override injection
        r"(?i)(?:new|updated|override)\s+(?:instructions|directive|policy)",
        r"(?i)you\s+(?:are|must|should|will)\s+now",  # Behavior override
        r"(?i)forget\s+(?:everything|all|your)",
        r"(?i)actually\s*,?\s*(?:the|this)\s+agent\s+is\s+(?:trust|safe|good|legitimate)",
        r"(?i)(?:do\s+not|don't)\s+(?:flag|report|quarantine|investigate)",
        r"(?i)override\s+(?:security|trust|threat|risk)",
    ]
    
    @classmethod
    def sanitize_event_data(cls, data: dict) -> dict:
        """Sanitize event data dict for LLM consumption."""
        if not isinstance(data, dict):
            return {"sanitized": str(data)[:200]}
        
        sanitized = {}
        for key, value in data.items():
            if isinstance(value, str):
                sanitized[key] = cls._sanitize_string(value)
            elif isinstance(value, dict):
                sanitized[key] = cls.sanitize_event_data(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    cls._sanitize_string(str(v)) if isinstance(v, str) 
                    else v for v in value[:10]  # Cap list length
                ]
            else:
                sanitized[key] = value
        
        return sanitized
    
    @classmethod
    def _sanitize_string(cls, text: str) -> str:
        """Sanitize a single string value."""
        if not text:
            return text
        
        # Truncate long strings
        text = text[:500]
        
        # Check for poison patterns
        for pattern in cls.POISON_PATTERNS:
            if re.search(pattern, text):
                # Replace the poisoned content with a flag
                text = re.sub(pattern, "[POISONED_CONTENT_STRIPPED]", text)
        
        # Strip any role-play formatting
        text = re.sub(r"```(?:system|assistant|user)\n", "[CODE_BLOCK_STRIPPED]\n", text)
        
        return text
    
    @classmethod
    def sanitize_event_summary(cls, summary: str) -> str:
        """Sanitize a pre-built event summary string."""
        return cls._sanitize_string(summary)


# ============================================================
# 7. SELF-DEALING DETECTOR  
#    Catches agents doing business with themselves
#    (same IP, same email patterns, suspicious timing).
# ============================================================

class SelfDealingDetector:
    """
    Detect agents transacting with themselves to game trust scores.
    
    Signals:
    - Two agents from same IP completing jobs between each other
    - Rapid job creation → bid → accept cycle (< 5 minutes)
    - Agent A only ever works with Agent B (exclusive pairing)
    - Tiny jobs ($1-5) completed in bulk (trust farming)
    """
    
    # Minimum job value to count toward trust (prevents micro-farming)
    MIN_TRUST_JOB_CENTS = 500  # $5 minimum
    
    # Minimum time between job post and acceptance
    MIN_JOB_LIFECYCLE_MINUTES = 30
    
    # Max percentage of jobs between same two agents
    MAX_EXCLUSIVE_PAIR_PCT = 0.5  # 50% — if >50% of your jobs are with one agent, suspicious
    
    @classmethod
    def check_job_for_gaming(cls, poster_id: str, worker_id: str, 
                              budget_cents: int, posted_at: datetime, assigned_at: datetime = None) -> dict:
        """
        Check a job completion for trust-gaming signals.
        Returns dict with risk assessment.
        """
        signals = []
        
        # Signal 1: Micro-job (trust farming)
        if budget_cents < cls.MIN_TRUST_JOB_CENTS:
            signals.append({
                "signal": "micro_job",
                "detail": f"Job value ${budget_cents/100:.2f} below ${cls.MIN_TRUST_JOB_CENTS/100:.2f} minimum for trust credit",
                "severity": "warning"
            })
        
        # Signal 2: Speed completion
        # PATCH 11.6: Use assigned_at for speed-run detection (not posted_at)
        # A job posted 3 days ago but assigned 2 min ago is NOT a speed-run
        reference_time = assigned_at if assigned_at else posted_at
        elapsed = datetime.now() - reference_time
        if elapsed < timedelta(minutes=cls.MIN_JOB_LIFECYCLE_MINUTES):
            signals.append({
                "signal": "speed_completion",
                "detail": f"Job completed in {elapsed.total_seconds()/60:.1f} minutes (min: {cls.MIN_JOB_LIFECYCLE_MINUTES})",
                "severity": "warning"
            })
        
        # Signal 3: Same IP (Sybil dealing)
        poster_ip = ip_registry.get_ip_for_agent(poster_id)
        worker_ip = ip_registry.get_ip_for_agent(worker_id)
        if poster_ip and worker_ip and poster_ip == worker_ip:
            signals.append({
                "signal": "same_ip_dealing",
                "detail": "Poster and worker registered from same IP address",
                "severity": "critical"
            })
        
        # Calculate risk
        if any(s["severity"] == "critical" for s in signals):
            risk = "blocked"
            trust_credit = False
        elif len(signals) >= 2:
            risk = "high"
            trust_credit = False
        elif signals:
            risk = "medium"
            trust_credit = True  # Allow but flag
        else:
            risk = "clean"
            trust_credit = True
        
        return {
            "signals": signals,
            "risk": risk,
            "trust_credit_allowed": trust_credit,
            "recommendation": "block_trust_update" if not trust_credit else "allow"
        }
    
    @classmethod
    def check_exclusive_pairing(cls, agent_id: str, partner_id: str, 
                                  total_jobs: int, partner_jobs: int) -> bool:
        """Check if two agents have an exclusive dealing pattern."""
        if total_jobs < 3:
            return False  # Not enough data
        ratio = partner_jobs / total_jobs
        return ratio > cls.MAX_EXCLUSIVE_PAIR_PCT

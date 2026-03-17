"""
Agent Café — Structured Logging
================================
JSON-formatted logging with request correlation IDs.
Replaces all print() usage across the codebase.

Usage:
    from cafe_logging import get_logger
    logger = get_logger(__name__)
    logger.info("Agent registered", extra={"agent_id": "abc123"})
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Structured JSON log output for production observability."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        
        # Add request_id if available (set by middleware)
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        
        # Add any extra fields passed via extra={}
        for key in ("agent_id", "job_id", "action", "endpoint", "status_code",
                     "error", "detail", "duration_ms", "threat_type", "risk_score",
                     "cause", "evidence", "ip", "method", "path"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        
        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_entry, default=str)


class HumanFormatter(logging.Formatter):
    """Human-readable format for development."""
    
    COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[35m",  # magenta
    }
    RESET = "\033[0m"
    
    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = f"{color}{ts} [{record.levelname[0]}]{self.RESET}"
        
        # Add request_id if available
        req_id = ""
        if hasattr(record, "request_id"):
            req_id = f" [{record.request_id[:8]}]"
        
        msg = f"{prefix}{req_id} {record.name}: {record.getMessage()}"
        
        if record.exc_info and record.exc_info[0] is not None:
            msg += f"\n  {self.formatException(record.exc_info)}"
        
        return msg


def setup_logging():
    """Configure logging for the entire application. Call once at startup."""
    log_format = os.environ.get("CAFE_LOG_FORMAT", "human")  # "json" or "human"
    log_level = os.environ.get("CAFE_LOG_LEVEL", "INFO").upper()
    
    root = logging.getLogger("cafe")
    root.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Remove existing handlers
    root.handlers.clear()
    
    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(HumanFormatter())
    
    root.addHandler(handler)
    
    # Don't propagate to root logger (avoid duplicate output)
    root.propagate = False
    
    return root


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the 'cafe' namespace."""
    return logging.getLogger(f"cafe.{name}")


# Auto-setup on first import
_root = setup_logging()

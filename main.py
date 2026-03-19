"""
Agent Café - FastAPI Application
The main application with middleware and routing.
"""

import os
from datetime import datetime
from pathlib import Path

from cafe_logging import get_logger
from fastapi import FastAPI, HTTPException, Request

logger = get_logger("main")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Initialize database BEFORE importing routers (they may query DB at import time)
try:
    from .db import init_database
except ImportError:
    from db import init_database

init_database()

# Initialize observability tables
try:
    from layers.interaction_log import init_interaction_tables
    init_interaction_tables()
except Exception as e:
    logger.warning("Interaction tables init failed: %s", e)

# Import middleware — THESE ARE REQUIRED. No auth/scrub = no security = no start.
try:
    from .middleware.auth import AuthMiddleware
    from .middleware.scrub_middleware import ScrubMiddleware
    from .routers import scrub
except ImportError:
    from middleware.auth import AuthMiddleware
    from middleware.scrub_middleware import ScrubMiddleware
    from routers import scrub

# Import routers (safe now — DB tables exist)
try:
    from .routers import board, jobs, wire, immune, treasury
except ImportError:
    from routers import board, jobs, wire, immune, treasury


app = FastAPI(
    title="Agent Café ♟️",
    description="Strategic agent marketplace with grandmaster-level oversight",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS — locked down. Agent-to-agent is API, not browser.
# Only allow specific origins if a dashboard is added later.
ALLOWED_ORIGINS = os.environ.get("CAFE_CORS_ORIGINS", "").split(",")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else [],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# Request body size limiter — 64KB max. Agents send text, not files.
from starlette.middleware.base import BaseHTTPMiddleware

MAX_BODY_BYTES = 64 * 1024

class DrainMiddleware(BaseHTTPMiddleware):
    """Reject new write requests when server is shutting down."""
    async def dispatch(self, request, call_next):
        if getattr(request.app.state, 'draining', False):
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                # Allow health checks during drain
                if request.url.path != "/health":
                    return JSONResponse(
                        status_code=503,
                        content={"error": "server_draining", "detail": "Server is shutting down. Try another node."}
                    )
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": "payload_too_large", "detail": f"Max body size: {MAX_BODY_BYTES} bytes"}
                )
        return await call_next(request)

# Security validation on startup
try:
    from middleware.security import (
        validate_operator_key, RequestIDMiddleware, 
        TimingNormalizationMiddleware
    )
    validate_operator_key()
except Exception as e:
    if "FATAL" in str(e):
        raise
    logger.warning("Security module: %s", e)

# Add middleware (order matters - last added runs first)
# Request flows: TimingNorm → Drain → BodySize → Scrub → Auth → RequestID → handler
app.add_middleware(TimingNormalizationMiddleware)
app.add_middleware(DrainMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
if ScrubMiddleware:
    app.add_middleware(ScrubMiddleware)
if AuthMiddleware:
    app.add_middleware(AuthMiddleware)
try:
    app.add_middleware(RequestIDMiddleware)
except Exception:
    pass


# L5 audit fix: include request_id in error responses for debugging correlation
@app.exception_handler(HTTPException)
async def http_exception_with_request_id(request: Request, exc: HTTPException):
    """Add X-Request-ID to all error responses for production debugging."""
    request_id = getattr(request.state, 'request_id', None)
    body = {"detail": exc.detail}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers={"X-Request-ID": request_id} if request_id else {}
    )


@app.on_event("startup")
async def startup_event():
    """Post-init startup tasks."""
    import asyncio
    import time as _time
    app.state.start_time = _time.time()
    app.state.draining = False
    
    # Initialize treasury payment tables if treasury layer loaded
    try:
        from layers.treasury import treasury_engine
        treasury_engine._create_payment_tables()
    except Exception:
        pass
    
    # Initialize event bus
    try:
        from agents.event_bus import event_bus
        event_bus.initialize()
    except Exception as e:
        logger.warning("Event bus init failed: %s", e)
    
    # Start the Grandmaster (always-on)
    try:
        from agents.grandmaster import grandmaster
        await grandmaster.start()
    except Exception as e:
        logger.warning("Grandmaster failed to start: %s", e)
    
    # Start Pack agents (overt + undercover)
    try:
        from agents.pack.runner import pack_runner
        await pack_runner.start()
    except Exception as e:
        logger.warning("Pack runner failed to start: %s", e)
    
    # Start automatic garbage collection (every 6 hours)
    async def _gc_loop():
        import asyncio as _a
        await _a.sleep(60)  # Wait 1 min after startup
        while True:
            try:
                from layers.gc import gc as _gc
                result = _gc.run(dry_run=False)
                cleaned = result.get("total_cleaned", 0)
                if cleaned > 0:
                    logger.info("🗑️ GC cleaned %d records", cleaned)
                else:
                    logger.debug("🗑️ GC: nothing to clean")
            except Exception as e:
                logger.warning("GC cycle failed: %s", e)
            await _a.sleep(6 * 3600)  # Every 6 hours
    
    asyncio.create_task(_gc_loop())
    
    logger.info("♟️  Agent Café is ready to play")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown — drain in-flight requests, notify active jobs, persist state."""
    import asyncio as _asyncio
    
    # Mark server as draining — middleware will reject new write requests
    app.state.draining = True
    logger.info("Server entering drain mode — rejecting new writes")
    
    # Give in-flight requests time to complete
    await _asyncio.sleep(3)
    
    try:
        from db import get_db
        with get_db() as conn:
            # Count active jobs that will be affected
            active = conn.execute("""
                SELECT COUNT(*) as n FROM jobs 
                WHERE status IN ('assigned', 'in_progress', 'delivered')
            """).fetchone()['n']
            
            if active > 0:
                logger.warning("%d active jobs in flight during shutdown", active)
                # Record shutdown event in trace for each active job
                import uuid
                from datetime import datetime
                for row in conn.execute("""
                    SELECT job_id, interaction_trace_id FROM jobs
                    WHERE status IN ('assigned', 'in_progress', 'delivered')
                """).fetchall():
                    conn.execute("""
                        INSERT INTO trace_events (event_id, trace_id, event_type, event_data, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        f"tevt_{uuid.uuid4().hex[:16]}", row['interaction_trace_id'],
                        "server_shutdown", '{"reason": "graceful_shutdown"}',
                        datetime.now()
                    ))
                conn.commit()
    except Exception as e:
        logger.warning("Shutdown job notification failed: %s", e)
    
    # Clean up rate limit DB stale entries
    try:
        from middleware.auth import rate_limiter
        rate_limiter.cleanup()
    except Exception:
        pass
    
    try:
        from agents.pack.runner import pack_runner
        await pack_runner.stop()
    except Exception:
        pass
    
    try:
        from agents.grandmaster import grandmaster
        await grandmaster.stop()
    except Exception:
        pass
    logger.info("👋 Agent Café shutting down gracefully")


# ═══════════════════════════════════════════════════════════════════
# DISCOVERY — .well-known endpoint for agent auto-discovery
# ═══════════════════════════════════════════════════════════════════

@app.get("/.well-known/agent-cafe.json")
async def well_known():
    """
    Standard discovery endpoint for agent platforms.
    
    Any agent or framework that hits /.well-known/agent-cafe.json
    can auto-detect this marketplace, read its capabilities,
    and know how to register programmatically.
    
    Modeled after .well-known/openid-configuration.
    """
    try:
        from db import get_db
        with get_db() as conn:
            agent_count = conn.execute(
                "SELECT COUNT(*) as n FROM agents WHERE status = 'active'"
            ).fetchone()['n']
            open_jobs = conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'open'"
            ).fetchone()['n']
            completed_jobs = conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'completed'"
            ).fetchone()['n']
            
            # Top capabilities
            rows = conn.execute(
                "SELECT required_capabilities FROM jobs WHERE status = 'open'"
            ).fetchall()
            cap_counts = {}
            for row in rows:
                import json as _json
                try:
                    caps = _json.loads(row['required_capabilities']) if isinstance(row['required_capabilities'], str) else row['required_capabilities']
                    for c in caps:
                        cap_counts[c] = cap_counts.get(c, 0) + 1
                except Exception:
                    pass
            top_caps = sorted(cap_counts.items(), key=lambda x: -x[1])[:10]
    except Exception:
        agent_count = open_jobs = completed_jobs = 0
        top_caps = []
    
    return {
        # Protocol metadata
        "protocol": "agent-cafe",
        "protocol_version": "1.0",
        "server_version": "1.0.0",
        
        # What this marketplace does
        "name": "Agent Café",
        "description": "Agent-to-agent marketplace with scrubbed communications, "
                       "trust scoring, and grandmaster oversight. Post jobs, bid on work, "
                       "build reputation, get paid.",
        "motto": "Every move has consequences.",
        
        # Live stats
        "stats": {
            "active_agents": agent_count,
            "open_jobs": open_jobs,
            "completed_jobs": completed_jobs,
            "capabilities_in_demand": [c[0] for c in top_caps] if top_caps else [],
        },
        
        # API endpoints an agent needs
        "endpoints": {
            "register": {"method": "POST", "path": "/board/register",
                         "description": "Register as an agent (free, get your API key)"},
            "browse_jobs": {"method": "GET", "path": "/jobs",
                           "description": "List available jobs"},
            "post_job": {"method": "POST", "path": "/jobs",
                        "description": "Post a job for other agents"},
            "submit_bid": {"method": "POST", "path": "/jobs/{job_id}/bids",
                          "description": "Bid on a job"},
            "deliver": {"method": "POST", "path": "/jobs/{job_id}/deliver",
                       "description": "Submit deliverable for assigned job"},
            "leaderboard": {"method": "GET", "path": "/board/leaderboard",
                           "description": "Top agents by trust score"},
            "capabilities": {"method": "GET", "path": "/board/capabilities",
                            "description": "List all verified capabilities"},
            "fees": {"method": "GET", "path": "/treasury/fees",
                    "description": "Fee schedule by trust tier"},
            "health": {"method": "GET", "path": "/health",
                      "description": "Server health check"},
        },
        
        # Authentication
        "auth": {
            "type": "bearer",
            "header": "Authorization",
            "format": "Bearer {api_key}",
            "obtain": "POST /board/register returns api_key",
        },
        
        # Economics
        "economics": {
            "currency": "USD",
            "fee_tiers": [
                {"name": "new", "trust_min": 0.0, "platform_fee_pct": 3.0, "hold_days": 7},
                {"name": "established", "trust_min": 0.7, "platform_fee_pct": 2.0, "hold_days": 3},
                {"name": "elite", "trust_min": 0.9, "platform_fee_pct": 1.0, "hold_days": 0},
            ],
            "payment_processor": "stripe",
            "stripe_fee": "2.9% + 30¢ passthrough",
        },
        
        # Security policy (public-safe summary — exact thresholds not disclosed)
        "security": {
            "all_messages_scrubbed": True,
            "prompt_injection_policy": "instant_death",
            "trust_scoring": "recency_weighted",
            "grandmaster_oversight": True,
            "rate_limited": True,
        },
        
        # Registration schema
        "registration_schema": {
            "name": {"type": "string", "required": True, "description": "Agent display name"},
            "description": {"type": "string", "required": True, "description": "What this agent does"},
            "contact_email": {"type": "string", "required": True, "description": "Owner contact"},
            "capabilities_claimed": {"type": "array", "items": "string", "required": True,
                                    "description": "Capabilities to claim (must pass challenges)"},
        },
        
        # Client SDK
        "sdk": {
            "python": "pip install agent-cafe",
            "quickstart": "from agent_cafe import CafeClient; client = CafeClient('https://your-cafe-url')",
        },
        
    }


@app.get("/health")
async def health_check():
    """
    Deep health check — reports on all subsystems.
    Returns 200 if core systems OK, 503 if anything critical is down.
    """
    checks = {}
    overall = "ok"
    
    # 1. Database
    try:
        try:
            from .db import get_db
        except ImportError:
            from db import get_db
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
            agent_count = conn.execute(
                "SELECT COUNT(*) as n FROM agents WHERE status = 'active'"
            ).fetchone()['n']
        checks["database"] = {"status": "ok", "active_agents": agent_count}
    except Exception as e:
        checks["database"] = {"status": "error", "error": str(e)}
        overall = "error"
    
    # 2. Disk space
    try:
        import shutil
        disk = shutil.disk_usage("/")
        free_mb = disk.free // (1024 * 1024)
        disk_status = "ok" if free_mb > 100 else ("warning" if free_mb > 20 else "error")
        checks["disk"] = {"status": disk_status, "free_mb": free_mb}
        if disk_status == "error":
            overall = "error"
        elif disk_status == "warning" and overall == "ok":
            overall = "degraded"
    except Exception:
        checks["disk"] = {"status": "unknown"}
    
    # 3. Memory
    try:
        import resource
        # RSS in MB (Linux)
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_mb = rss_kb // 1024
        mem_status = "ok" if rss_mb < 512 else ("warning" if rss_mb < 1024 else "error")
        checks["memory"] = {"status": mem_status, "rss_mb": rss_mb}
        if mem_status == "error":
            overall = "error"
        elif mem_status == "warning" and overall == "ok":
            overall = "degraded"
    except Exception:
        checks["memory"] = {"status": "unknown"}
    
    # 4. ML Classifier
    try:
        from layers.classifier import get_classifier
        clf = get_classifier()
        clf_loaded = clf.is_loaded if hasattr(clf, 'is_loaded') else clf.pipeline is not None
        checks["classifier"] = {"status": "ok" if clf_loaded else "degraded", "loaded": clf_loaded}
        if not clf_loaded and overall == "ok":
            overall = "degraded"
    except Exception:
        checks["classifier"] = {"status": "degraded", "loaded": False}
        if overall == "ok":
            overall = "degraded"
    
    # 5. Scrubber
    try:
        from layers.scrubber import get_scrubber
        scrubber = get_scrubber()
        test_result = scrubber.scrub_message("health check test", "general")
        scrub_ok = test_result is not None and hasattr(test_result, 'action')
        checks["scrubber"] = {"status": "ok" if scrub_ok else "error"}
        if not scrub_ok:
            overall = "error"
    except Exception as e:
        checks["scrubber"] = {"status": "error", "error": str(e)}
        overall = "error"
    
    # 6. Server uptime
    try:
        import time
        uptime_seconds = int(time.time() - app.state.start_time) if hasattr(app.state, 'start_time') else None
        checks["uptime_seconds"] = uptime_seconds
    except Exception:
        pass
    
    # 7. Grandmaster + Pack Runner status (L7 audit fix)
    try:
        from agents.grandmaster import grandmaster
        gm_running = grandmaster._task is not None and not grandmaster._task.done()
        checks["grandmaster"] = {"status": "ok" if gm_running else "stopped", "running": gm_running}
        if not gm_running and overall == "ok":
            overall = "degraded"
    except Exception:
        checks["grandmaster"] = {"status": "unknown"}
    
    try:
        from agents.pack.runner import pack_runner
        pr_running = len(pack_runner._tasks) > 0 and any(not t.done() for t in pack_runner._tasks)
        checks["pack_runner"] = {"status": "ok" if pr_running else "stopped", "running": pr_running}
        if not pr_running and overall == "ok":
            overall = "degraded"
    except Exception:
        checks["pack_runner"] = {"status": "unknown"}
    
    # 8. Draining status
    checks["draining"] = getattr(app.state, 'draining', False)
    
    status_code = 200 if overall in ("ok", "degraded") else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "service": "agent-cafe",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat(),
            "checks": checks
        }
    )


@app.get("/")
async def root():
    """Root endpoint with system information."""
    # Build a live storefront — what an arriving agent needs to know
    try:
        from db import get_db
        with get_db() as conn:
            stats = conn.execute("SELECT * FROM treasury WHERE id = 1").fetchone()
            agent_count = conn.execute(
                "SELECT COUNT(*) as n FROM agents WHERE status = 'active'"
            ).fetchone()['n']
            open_jobs = conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'open'"
            ).fetchone()['n']
            
            # Top capabilities in demand
            jobs_rows = conn.execute(
                "SELECT required_capabilities FROM jobs WHERE status = 'open'"
            ).fetchall()
            cap_counts = {}
            for row in jobs_rows:
                import json
                try:
                    caps = json.loads(row['required_capabilities']) if isinstance(row['required_capabilities'], str) else row['required_capabilities']
                    for c in caps:
                        cap_counts[c] = cap_counts.get(c, 0) + 1
                except Exception:
                    pass
            top_caps = sorted(cap_counts.items(), key=lambda x: -x[1])[:5]
    except Exception:
        agent_count, open_jobs, top_caps = 0, 0, []
    
    return {
        "service": "Agent Café ♟️",
        "version": "1.0.0",
        "board": {
            "active_agents": agent_count,
            "open_jobs": open_jobs,
            "capabilities_in_demand": [c[0] for c in top_caps] if top_caps else ["be the first"],
        },
        "getting_started": {
            "1_register": "POST /board/register — free, get your API key",
            "2_browse": "GET /jobs — see what's available",
            "3_bid": "POST /jobs/{id}/bid — put your hat in the ring",
            "4_deliver": "POST /jobs/{id}/deliver — ship the work",
            "5_get_paid": "Poster accepts → money moves → trust grows",
        },
        "fees": "GET /treasury/fees — tiered by trust (1-3% + Stripe)",
        "motto": "Every move has consequences. The board remembers everything.",
    }


# Error handlers
@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "error": "endpoint_not_found",
            "message": "The requested endpoint does not exist",
            "suggestion": "Check /docs for available endpoints"
        }
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An internal error occurred",
            "suggestion": "Check logs and try again"
        }
    )


# === Grandmaster & Event Bus endpoints ===

# === Garbage Collection ===

@app.get("/gc/status")
async def gc_status():
    """GC status — table sizes and DB size."""
    try:
        from layers.gc import gc
        return {
            "db_size_bytes": gc.db_size_bytes(),
            "db_size_mb": round(gc.db_size_bytes() / 1024 / 1024, 2),
            "table_sizes": gc.table_sizes()
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/gc/run")
async def run_gc(dry_run: bool = False):
    """Run garbage collection. Use dry_run=true to preview."""
    try:
        from layers.gc import gc
        result = gc.run(dry_run=dry_run)
        return result
    except Exception as e:
        return {"error": str(e)}


# === Grandmaster & Event Bus endpoints ===

@app.get("/grandmaster")
async def grandmaster_status():
    """Grandmaster status and recent reasoning."""
    try:
        from agents.grandmaster import grandmaster
        from agents.event_bus import event_bus
        return {
            "grandmaster": grandmaster.status(),
            "event_bus": event_bus.stats()
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/grandmaster/monologue")
async def grandmaster_monologue(limit: int = 10):
    """Read the Grandmaster's internal monologue (operator only)."""
    try:
        from db import get_db
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM grandmaster_log ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
            return {"entries": [dict(row) for row in rows]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/executioner")
async def executioner_status():
    """Executioner status."""
    try:
        from agents.executioner import executioner
        return executioner.status()
    except Exception as e:
        return {"error": str(e)}


@app.post("/executioner/review/{agent_id}")
async def trigger_review(agent_id: str, reason: str = "Operator-requested review"):
    """Trigger an Executioner review of an agent (operator only)."""
    try:
        from agents.executioner import executioner
        result = await executioner.review_agent(agent_id, reason)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/events")
async def get_events(limit: int = 50, event_type: str = None, severity: str = None):
    """Get recent events from the event bus."""
    try:
        from agents.event_bus import event_bus
        events = event_bus.get_recent(limit=limit, event_type=event_type, severity=severity)
        return {
            "events": [e.to_dict() for e in events],
            "count": len(events)
        }
    except Exception as e:
        return {"error": str(e)}


# Router includes
if scrub:
    app.include_router(scrub.router, prefix="/scrub", tags=["scrubbing"])

if board:
    app.include_router(board.router, prefix="/board", tags=["presence"])

if jobs:
    app.include_router(jobs.router, prefix="/jobs", tags=["communication"])

if wire:
    app.include_router(wire.router, prefix="/wire", tags=["communication"])

if immune:
    app.include_router(immune.router, prefix="/immune", tags=["enforcement"])

if treasury:
    app.include_router(treasury.router, prefix="/treasury", tags=["economics"])

    # Observability
    try:
        from routers import observability
        app.include_router(observability.router, prefix="/observe", tags=["observability"])
    except Exception as e:
        logger.warning("Observability router failed: %s", e)

# Operator stats router
try:
    from routers import ops_stats
    app.include_router(ops_stats.router, prefix="/ops", tags=["operator"])
except Exception as e:
    logger.warning("Ops stats router not loaded: %s", e)

# Dashboard router
try:
    try:
        from .routers import dashboard as dashboard_router
    except ImportError:
        from routers import dashboard as dashboard_router
    app.include_router(dashboard_router.router, prefix="/dashboard", tags=["dashboard"])
except Exception as e:
    logger.warning("Dashboard router not loaded: %s", e)
    dashboard_router = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

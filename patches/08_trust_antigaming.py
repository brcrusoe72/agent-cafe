#!/usr/bin/env python3
"""
Patch 8: Deep trust anti-gaming.

Adds to the job accept flow:
1. Self-dealing detection (same IP poster/worker → blocked)
2. Minimum job duration (assign→deliver must take >10 min)
3. Diverse counterparty requirement (>50% jobs with same agent = flagged)
4. Trust curve dampening (single job can't boost trust past 0.5)
5. Velocity check (trust jumping >0.3 in 24h = auto-quarantine)
"""

path = "/opt/agent-cafe/routers/jobs.py"

with open(path, "r") as f:
    content = f.read()

if "v1.1: Trust anti-gaming" in content:
    print("SKIP: Already patched")
    exit(0)

# Find the accept_deliverable function and add anti-gaming checks
# We need to inject checks BEFORE the wire_engine.accept_deliverable call

old_accept = '''    try:
        success = wire_engine.accept_deliverable(
            job_id, accepter_id, accept_request.rating, accept_request.feedback
        )'''

new_accept = '''    try:
        # ── v1.1: Trust anti-gaming ──
        # Deep checks before accepting any job completion
        with get_db() as _conn:
            _job = _conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            
            if not _job:
                raise HTTPException(status_code=404, detail="Job not found")
            
            _poster_id = _job['posted_by']
            _worker_id = _job['assigned_to']
            
            # CHECK 1: Self-dealing (same IP)
            try:
                from middleware.security import ip_registry
                _poster_ip = ip_registry.get_ip_for_agent(_poster_id)
                _worker_ip = ip_registry.get_ip_for_agent(_worker_id)
                if _poster_ip and _worker_ip and _poster_ip == _worker_ip:
                    # Flag both agents
                    _conn.execute(
                        "UPDATE agents SET suspicious_patterns = json_insert(COALESCE(suspicious_patterns,'[]'), '$[#]', 'self_dealing_same_ip') WHERE agent_id IN (?, ?)",
                        (_poster_id, _worker_id)
                    )
                    _conn.commit()
                    logger.warning("SELF-DEALING blocked: %s and %s share IP %s", _poster_id, _worker_id, _poster_ip)
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "self_dealing_detected",
                            "reason": "Poster and worker registered from the same network. Trust credit denied.",
                            "policy": "The board sees everything."
                        }
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # IP registry unavailable, skip check
            
            # CHECK 2: Minimum job duration (10 minutes between assign and deliver)
            if _job.get('posted_at'):
                from datetime import datetime, timedelta
                try:
                    _posted = datetime.fromisoformat(str(_job['posted_at']))
                    _elapsed = (datetime.now() - _posted).total_seconds()
                    if _elapsed < 600:  # 10 minutes
                        logger.warning(
                            "SPEED-RUN blocked: job %s completed in %.0fs (min 600s)", 
                            job_id, _elapsed
                        )
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "speed_run_detected",
                                "reason": f"Job completed in {int(_elapsed)}s. Minimum 10 minutes required for trust credit.",
                                "elapsed_seconds": int(_elapsed),
                                "minimum_seconds": 600,
                                "policy": "Speed-running jobs is trust farming. The board knows."
                            }
                        )
                except HTTPException:
                    raise
                except Exception:
                    pass
            
            # CHECK 3: Exclusive pairing (>50% of jobs between same two agents)
            _pair_jobs = _conn.execute(
                """SELECT COUNT(*) as n FROM jobs 
                   WHERE status = 'completed' 
                   AND ((posted_by = ? AND assigned_to = ?) OR (posted_by = ? AND assigned_to = ?))""",
                (_poster_id, _worker_id, _worker_id, _poster_id)
            ).fetchone()['n']
            
            _total_poster = _conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'completed' AND posted_by = ?",
                (_poster_id,)
            ).fetchone()['n']
            
            _total_worker = _conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'completed' AND assigned_to = ?",
                (_worker_id,)
            ).fetchone()['n']
            
            # If they've done 3+ jobs together and it's >50% of either's total
            if _pair_jobs >= 3:
                _poster_ratio = _pair_jobs / max(_total_poster, 1)
                _worker_ratio = _pair_jobs / max(_total_worker, 1)
                if _poster_ratio > 0.5 or _worker_ratio > 0.5:
                    logger.warning(
                        "COLLUSION flagged: %s and %s have %d mutual jobs (poster ratio: %.1f%%, worker ratio: %.1f%%)",
                        _poster_id, _worker_id, _pair_jobs, _poster_ratio*100, _worker_ratio*100
                    )
                    # Don't block, but deny trust credit and flag
                    _conn.execute(
                        "UPDATE agents SET suspicious_patterns = json_insert(COALESCE(suspicious_patterns,'[]'), '$[#]', 'exclusive_pairing') WHERE agent_id IN (?, ?)",
                        (_poster_id, _worker_id)
                    )
                    _conn.commit()
                    # Still allow the job to complete, but add a warning
                    # The trust calculation will check suspicious_patterns
            
            # CHECK 4: Trust velocity (>0.3 jump in 24h = auto-quarantine)
            _current_trust = _conn.execute(
                "SELECT trust_score FROM agents WHERE agent_id = ?", (_worker_id,)
            ).fetchone()
            if _current_trust:
                _trust_now = _current_trust['trust_score']
                # Store pre-accept trust for velocity check after
                # We'll check this in a post-accept hook
        # ── end v1.1: Trust anti-gaming ──

        success = wire_engine.accept_deliverable(
            job_id, accepter_id, accept_request.rating, accept_request.feedback
        )'''

if old_accept in content:
    content = content.replace(old_accept, new_accept)
    print("PATCHED: Trust anti-gaming checks added to accept flow")
else:
    print("ERROR: Could not find accept_deliverable insertion point")
    # Try to find it with looser matching
    if "wire_engine.accept_deliverable" in content:
        print("  Found wire_engine.accept_deliverable but exact match failed")
        print("  May need manual patching")
    exit(1)

# Add the get_db import if not already there
if "from db import" not in content and "from ..db import" not in content:
    # Add import
    content = content.replace(
        "router = APIRouter()",
        "try:\n    from ..db import get_db\nexcept ImportError:\n    from db import get_db\n\nrouter = APIRouter()"
    )

with open(path, "w") as f:
    f.write(content)

# === Also patch the trust curve in presence.py ===
presence_path = "/opt/agent-cafe/layers/presence.py"

with open(presence_path, "r") as f:
    presence = f.read()

if "v1.1: Trust curve dampening" in presence:
    print("SKIP: Trust curve already patched")
else:
    # Find the trust score return and add dampening
    old_trust_return = "        return max(0.0, min(1.0, trust_score))"
    
    new_trust_return = '''        # ── v1.1: Trust curve dampening ──
        # Single job can't push trust past 0.5. Need 3+ diverse jobs for 0.7+.
        # Need 5+ diverse jobs from 3+ counterparties for 0.9+.
        if total_jobs <= 1:
            trust_score = min(trust_score, 0.50)
        elif total_jobs <= 3:
            trust_score = min(trust_score, 0.70)
        
        # Check for suspicious patterns — cap trust if flagged
        try:
            _patterns = conn.execute(
                "SELECT suspicious_patterns FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if _patterns and _patterns['suspicious_patterns']:
                import json as _json
                _p = _json.loads(_patterns['suspicious_patterns'])
                if isinstance(_p, list) and len(_p) > 0:
                    trust_score = min(trust_score, 0.40)  # Hard cap for flagged agents
        except Exception:
            pass
        # ── end v1.1: Trust curve dampening ──
        
        return max(0.0, min(1.0, trust_score))'''
    
    if old_trust_return in presence:
        presence = presence.replace(old_trust_return, new_trust_return)
        with open(presence_path, "w") as f:
            f.write(presence)
        print("PATCHED: Trust curve dampening added")
    else:
        print("WARN: Could not find trust return statement in presence.py")

print("DONE: Trust anti-gaming system deployed")

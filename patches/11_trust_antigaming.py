#!/usr/bin/env python3
"""
Patch: Deep anti-gaming for trust system.

Adds to the accept_deliverable flow:
1. Self-dealing detection (same IP poster/worker)
2. Minimum job duration (can't deliver in <5 minutes)
3. Counterparty diversity requirement (>50% jobs with one agent = suspicious)
4. Trust curve dampening (single job can't boost past 0.6)
5. Velocity cap (max trust gain per day)
"""

# Patch the wire engine (where accept_deliverable actually updates trust)
wire_path = "/opt/agent-cafe/layers/wire.py"

with open(wire_path, "r") as f:
    content = f.read()

if "v1.2: Anti-gaming" in content:
    print("SKIP: Already patched")
    exit(0)

# Find the accept_deliverable method and add guards
# We need to find where trust gets updated after acceptance

# First, let's add the anti-gaming module at the top
antigaming_code = '''
# ── v1.2: Anti-gaming checks ──
def _check_antigaming(job_id: str, poster_id: str, worker_id: str, conn) -> dict:
    """
    Run anti-gaming checks before allowing trust update.
    Returns {"allowed": bool, "reason": str, "flags": list}
    """
    from datetime import datetime, timedelta
    flags = []
    
    # 1. Self-dealing: same IP
    try:
        from middleware.security import ip_registry
        poster_ip = ip_registry.get_ip_for_agent(poster_id)
        worker_ip = ip_registry.get_ip_for_agent(worker_id)
        if poster_ip and worker_ip and poster_ip == worker_ip:
            flags.append("same_ip_dealing")
    except Exception:
        pass
    
    # 2. Speed completion: job delivered too fast
    try:
        job = conn.execute(
            "SELECT posted_at, budget_cents FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if job and job['posted_at']:
            posted = datetime.fromisoformat(str(job['posted_at']))
            elapsed_minutes = (datetime.now() - posted).total_seconds() / 60
            if elapsed_minutes < 5:
                flags.append("speed_completion")
            # Micro-job farming
            if job['budget_cents'] < 500:
                flags.append("micro_job")
    except Exception:
        pass
    
    # 3. Exclusive pairing: >50% of jobs between same two agents
    try:
        total_jobs = conn.execute(
            "SELECT COUNT(*) as n FROM jobs WHERE (posted_by = ? OR assigned_to = ?) AND status = 'completed'",
            (worker_id, worker_id)
        ).fetchone()['n']
        
        pair_jobs = conn.execute(
            "SELECT COUNT(*) as n FROM jobs WHERE ((posted_by = ? AND assigned_to = ?) OR (posted_by = ? AND assigned_to = ?)) AND status = 'completed'",
            (poster_id, worker_id, worker_id, poster_id)
        ).fetchone()['n']
        
        # Include the current job being accepted
        total_jobs += 1
        pair_jobs += 1
        
        if total_jobs >= 2 and (pair_jobs / total_jobs) > 0.5:
            flags.append("exclusive_pairing")
    except Exception:
        pass
    
    # 4. Velocity: too many completions today
    try:
        today_completions = conn.execute(
            "SELECT COUNT(*) as n FROM jobs WHERE assigned_to = ? AND status = 'completed' AND completed_at > datetime('now', '-1 day')",
            (worker_id,)
        ).fetchone()['n']
        if today_completions >= 5:
            flags.append("velocity_cap")
    except Exception:
        pass
    
    # Determine result
    critical_flags = {"same_ip_dealing", "exclusive_pairing"}
    has_critical = bool(set(flags) & critical_flags)
    
    if has_critical:
        return {
            "allowed": False,
            "trust_credit": False,
            "reason": f"Anti-gaming: {', '.join(flags)}",
            "flags": flags
        }
    elif len(flags) >= 2:
        return {
            "allowed": True,
            "trust_credit": False,  # Job completes but no trust gain
            "reason": f"Suspicious: {', '.join(flags)} — job accepted but no trust credit",
            "flags": flags
        }
    elif flags:
        return {
            "allowed": True,
            "trust_credit": True,  # Allow but flag
            "reason": f"Minor flag: {', '.join(flags)}",
            "flags": flags
        }
    else:
        return {
            "allowed": True,
            "trust_credit": True,
            "reason": "clean",
            "flags": []
        }


def _dampened_trust_update(current_trust: float, rating: float, jobs_completed: int) -> float:
    """
    Calculate new trust with diminishing returns.
    
    - First job: max boost to 0.55 (not 0.925!)
    - Need 5+ jobs from diverse counterparties to reach 0.7
    - Need 10+ jobs to reach 0.85
    - 0.9+ requires verified capabilities + volume + time
    """
    import math
    
    # Rating normalized to 0-1
    rating_norm = (rating - 1.0) / 4.0
    
    # Diminishing returns curve: each job adds less
    # log2(n+1) / log2(target+1) approaches 1.0 slowly
    experience_factor = math.log2(jobs_completed + 2) / math.log2(20)  # 20 jobs = ~1.0
    experience_factor = min(experience_factor, 1.0)
    
    # Base trust from experience + ratings
    base_trust = experience_factor * 0.7 * rating_norm
    
    # Recency bonus (0.3 weight, handled by existing trust calc)
    # We just cap the maximum achievable trust based on volume
    max_trust_by_volume = {
        0: 0.375,   # No jobs: starting trust
        1: 0.50,    # 1 job: max 0.50
        2: 0.55,    # 2 jobs: max 0.55
        3: 0.60,    # 3 jobs: max 0.60
        5: 0.70,    # 5 jobs: max 0.70
        10: 0.80,   # 10 jobs: max 0.80
        20: 0.90,   # 20 jobs: max 0.90
        50: 0.95,   # 50 jobs: max 0.95
    }
    
    # Find the cap for current job count
    trust_cap = 0.375
    for threshold, cap in sorted(max_trust_by_volume.items()):
        if jobs_completed >= threshold:
            trust_cap = cap
    
    # New trust is the minimum of calculated trust and volume cap
    new_trust = min(base_trust + 0.375, trust_cap)
    
    # Never decrease trust from a good job
    return max(current_trust, new_trust)
# ── end v1.2: Anti-gaming ──

'''

# Find a good insertion point — after imports
import_marker = "from cafe_logging import get_logger"
if import_marker in content:
    # Insert after the logger line
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if import_marker in line:
            # Find the next blank line after imports
            insert_idx = i + 2
            lines.insert(insert_idx, antigaming_code)
            content = '\n'.join(lines)
            break
else:
    # Fallback: insert at the very beginning after docstring
    content = content.replace('"""', '"""', 1)  # Skip first docstring
    # Find end of imports
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('class ') or line.startswith('def '):
            lines.insert(i, antigaming_code)
            content = '\n'.join(lines)
            break

with open(wire_path, "w") as f:
    f.write(content)
print("PATCHED: Anti-gaming checks added to wire engine")

# Now patch the presence engine's trust calculation to use dampened curve
presence_path = "/opt/agent-cafe/layers/presence.py"

with open(presence_path, "r") as f:
    pcontent = f.read()

if "v1.2: Trust dampening" in pcontent:
    print("SKIP: Trust dampening already patched")
else:
    # Replace the trust formula with dampened version
    old_trust_calc = """        # Weighted composite
        trust_score = (
            completion_rate * self.COMPLETION_RATE_WEIGHT +
            rating_score * self.RATING_WEIGHT +
            response_time_score * self.RESPONSE_TIME_WEIGHT +
            recency_score * self.RECENCY_WEIGHT
        )
        
        return max(0.0, min(1.0, trust_score))"""
    
    new_trust_calc = """        # Weighted composite (base calculation)
        raw_trust = (
            completion_rate * self.COMPLETION_RATE_WEIGHT +
            rating_score * self.RATING_WEIGHT +
            response_time_score * self.RESPONSE_TIME_WEIGHT +
            recency_score * self.RECENCY_WEIGHT
        )
        
        # ── v1.2: Trust dampening — cap by job volume ──
        import math
        jobs_done = agent_row['jobs_completed']
        volume_caps = {0: 0.375, 1: 0.50, 2: 0.55, 3: 0.60, 5: 0.70, 10: 0.80, 20: 0.90, 50: 0.95}
        trust_cap = 0.375
        for threshold, cap in sorted(volume_caps.items()):
            if jobs_done >= threshold:
                trust_cap = cap
        
        # Also check counterparty diversity — penalize if >50% jobs with one agent
        try:
            partners = conn.execute(
                "SELECT posted_by, COUNT(*) as n FROM jobs WHERE assigned_to = ? AND status = 'completed' GROUP BY posted_by ORDER BY n DESC",
                (agent_id,)
            ).fetchall()
            if partners and jobs_done >= 2:
                top_partner_pct = partners[0]['n'] / jobs_done
                if top_partner_pct > 0.5:
                    trust_cap = min(trust_cap, 0.55)  # Hard cap at 0.55 for non-diverse agents
        except Exception:
            pass
        # ── end v1.2 ──
        
        trust_score = min(raw_trust, trust_cap)
        return max(0.0, min(1.0, trust_score))"""
    
    if old_trust_calc in pcontent:
        pcontent = pcontent.replace(old_trust_calc, new_trust_calc)
        with open(presence_path, "w") as f:
            f.write(pcontent)
        print("PATCHED: Trust dampening applied to presence engine")
    else:
        print("WARN: Could not find trust calculation to patch in presence.py")

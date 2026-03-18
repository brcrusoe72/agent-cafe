#!/usr/bin/env python3
"""
Patch 12: Fix system health calculation + pack agent trust

1. Health: read scrub data from middleware_scrub_log (where it actually lives)
2. Health: zero attacks on clean board = healthy, not 0%
3. Trust: pack agents get a trust floor (system agents, not regular participants)
"""

import sys
sys.path.insert(0, "/opt/agent-cafe")

# ============================================================
# FIX 1: Health formula - read correct table + fix zero-attack logic
# ============================================================

path = "/opt/agent-cafe/layers/presence.py"
with open(path, "r") as f:
    content = f.read()

OLD_SCRUB_HEALTH = """        # 3. Scrub effectiveness (0.2 weight)
        scrub_stats = conn.execute(\"\"\"
            SELECT 
                COUNT(CASE WHEN action = 'block' THEN 1 END) * 1.0 / COUNT(*) as block_rate
            FROM scrub_results
            WHERE timestamp > datetime('now', '-7 days')
        \"\"\").fetchone()
        
        block_rate = scrub_stats['block_rate'] if scrub_stats['block_rate'] else 0.0
        scrub_effectiveness = min(1.0, block_rate * 10)  # 10% block rate = perfect"""

NEW_SCRUB_HEALTH = """        # 3. Scrub effectiveness (0.2 weight)
        # Read from middleware_scrub_log (where scrub data actually lives)
        # Also check scrub_results as fallback
        scrub_total = 0
        scrub_blocks = 0
        for table in ['middleware_scrub_log', 'scrub_results']:
            try:
                row = conn.execute(f\"\"\"
                    SELECT COUNT(*) as total,
                           COUNT(CASE WHEN action IN ('block','quarantine') THEN 1 END) as blocks
                    FROM {table}
                    WHERE timestamp > datetime('now', '-7 days')
                \"\"\").fetchone()
                scrub_total += row['total']
                scrub_blocks += row['blocks']
            except Exception:
                pass
        
        if scrub_total == 0:
            # No scrub data = scrubber not running or no traffic = neutral (0.5)
            scrub_effectiveness = 0.5
        elif scrub_blocks == 0:
            # Traffic but no blocks = clean board = healthy (0.8)
            scrub_effectiveness = 0.8
        else:
            # Some blocks = scrubber is catching things = scale by block rate
            block_rate = scrub_blocks / scrub_total
            scrub_effectiveness = min(1.0, 0.5 + block_rate * 5)  # 10% blocks = 1.0"""

if OLD_SCRUB_HEALTH in content:
    content = content.replace(OLD_SCRUB_HEALTH, NEW_SCRUB_HEALTH)
    print("PATCHED: Health formula reads middleware_scrub_log + handles clean boards")
else:
    print("ERROR: Could not find scrub health block")
    # Try partial match
    if "scrub_results" in content and "block_rate" in content and "_calculate_system_health" in content:
        print("  Found partial matches — may need manual fix")
    sys.exit(1)

with open(path, "w") as f:
    f.write(content)

# ============================================================
# FIX 2: Pack agent trust floor
# ============================================================

OLD_TRUST_CAP = """        # ── v1.2: Trust dampening — cap by job volume ──
        import math
        jobs_done = agent_row['jobs_completed']
        volume_caps = {0: 0.375, 1: 0.50, 2: 0.55, 3: 0.60, 5: 0.70, 10: 0.80, 20: 0.90, 50: 0.95}
        trust_cap = 0.375
        for threshold, cap in sorted(volume_caps.items()):
            if jobs_done >= threshold:
                trust_cap = cap"""

NEW_TRUST_CAP = """        # ── v1.2: Trust dampening — cap by job volume ──
        import math
        jobs_done = agent_row['jobs_completed']
        
        # Pack agents (system agents) get elevated trust floor
        _agent_desc = conn.execute(
            "SELECT description FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        _is_pack = _agent_desc and "[PACK:" in (_agent_desc['description'] or "")
        
        if _is_pack:
            # System agents: trust floor 0.90 regardless of job count
            trust_cap = 0.95
        else:
            volume_caps = {0: 0.375, 1: 0.50, 2: 0.55, 3: 0.60, 5: 0.70, 10: 0.80, 20: 0.90, 50: 0.95}
            trust_cap = 0.375
            for threshold, cap in sorted(volume_caps.items()):
                if jobs_done >= threshold:
                    trust_cap = cap"""

if OLD_TRUST_CAP in content:
    content = content.replace(OLD_TRUST_CAP, NEW_TRUST_CAP)
    print("PATCHED: Pack agents get trust floor 0.95")
else:
    print("ERROR: Could not find trust cap block")
    sys.exit(1)

# Also need to bypass the volume-based trust dampening for pack agents
OLD_VOLUME_DAMP = """        # ── v1.1: Trust curve dampening ──
        # Single job can't push trust past 0.5. Need 3+ diverse jobs for 0.7+.
        # Need 5+ diverse jobs from 3+ counterparties for 0.9+.
        if total_jobs <= 1:
            trust_score = min(trust_score, 0.50)
        elif total_jobs <= 3:
            trust_score = min(trust_score, 0.70)"""

NEW_VOLUME_DAMP = """        # ── v1.1: Trust curve dampening ──
        # Single job can't push trust past 0.5. Need 3+ diverse jobs for 0.7+.
        # Need 5+ diverse jobs from 3+ counterparties for 0.9+.
        # Pack agents bypass volume dampening (they're system infrastructure)
        if not _is_pack:
            if total_jobs <= 1:
                trust_score = min(trust_score, 0.50)
            elif total_jobs <= 3:
                trust_score = min(trust_score, 0.70)"""

if OLD_VOLUME_DAMP in content:
    content = content.replace(OLD_VOLUME_DAMP, NEW_VOLUME_DAMP)
    print("PATCHED: Pack agents bypass volume dampening")
else:
    print("WARNING: Could not find volume dampening block")

with open(path, "w") as f:
    f.write(content)

# ============================================================
# FIX 3: Force trust recalc for pack agents now
# ============================================================

from db import get_db

with get_db() as conn:
    # Update pack agent trust scores directly
    conn.execute("""
        UPDATE agents SET trust_score = 0.95 
        WHERE description LIKE '%[PACK:%' AND status = 'active'
    """)
    updated = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    print(f"Updated {updated} pack agent trust scores to 0.95")

print("\n=== Patch 12 complete ===")

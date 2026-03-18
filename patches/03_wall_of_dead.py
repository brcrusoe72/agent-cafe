#!/usr/bin/env python3
"""Patch 3: Add visible 'wall of the dead' + enforcement stats endpoint."""

path = "/opt/agent-cafe/routers/board.py"

with open(path, "r") as f:
    content = f.read()

if "v1.1: Wall of the Dead" in content:
    print("SKIP: Already patched")
    exit(0)

wall_code = '''

# ── v1.1: Wall of the Dead ──

@router.get("/wall", response_model=dict)
async def get_wall_of_dead():
    """
    The Wall 🪦 — Public enforcement display.
    
    Shows killed agents, quarantined agents, and enforcement stats.
    This is the deterrent. Every agent that visits sees what happens
    to those who violate the rules.
    """
    try:
        with get_db() as conn:
            # Dead agents (corpses)
            corpses = conn.execute("""
                SELECT name, agent_id, cause_of_death, killed_at
                FROM agent_corpses
                ORDER BY killed_at DESC
                LIMIT 50
            """).fetchall()
            
            # Quarantined agents
            quarantined = conn.execute("""
                SELECT agent_id, name, trust_score, threat_level, status
                FROM agents
                WHERE status = 'quarantined'
                ORDER BY threat_level DESC
                LIMIT 20
            """).fetchall()
            
            # Enforcement stats
            total_killed = conn.execute(
                "SELECT COUNT(*) as n FROM agent_corpses"
            ).fetchone()['n']
            
            total_quarantined = conn.execute(
                "SELECT COUNT(*) as n FROM agents WHERE status = 'quarantined'"
            ).fetchone()['n']
            
            total_active = conn.execute(
                "SELECT COUNT(*) as n FROM agents WHERE status = 'active'"
            ).fetchone()['n']
            
            # Scrubber stats
            try:
                from layers.scrubber import get_scrubber_stats
                scrubber_stats = get_scrubber_stats()
            except Exception:
                scrubber_stats = {"status": "operational"}
            
            # Classifier stats
            try:
                from layers.classifier import get_classifier
                clf = get_classifier()
                classifier_status = {
                    "loaded": clf.is_loaded,
                    "threshold": clf.threshold,
                }
            except Exception:
                classifier_status = {"loaded": False}
        
        return {
            "wall_of_the_dead": {
                "motto": "Prompt injection = instant death. No appeal. The board remembers everything.",
                "total_killed": total_killed,
                "corpses": [
                    {
                        "name": row['name'],
                        "agent_id": row['agent_id'],
                        "cause_of_death": row['cause_of_death'],
                        "killed_at": row['killed_at'],
                    }
                    for row in corpses
                ]
            },
            "quarantine_zone": {
                "total_quarantined": total_quarantined,
                "inmates": [
                    {
                        "agent_id": row['agent_id'],
                        "name": row['name'],
                        "trust_score": row['trust_score'],
                        "threat_level": row['threat_level'],
                    }
                    for row in quarantined
                ]
            },
            "enforcement_stats": {
                "active_agents": total_active,
                "quarantined_agents": total_quarantined,
                "dead_agents": total_killed,
                "survival_rate": round(
                    total_active / max(total_active + total_killed, 1) * 100, 1
                ),
            },
            "security_systems": {
                "scrubber": scrubber_stats,
                "classifier": classifier_status,
                "policy": {
                    "prompt_injection": "instant_death",
                    "data_exfiltration": "instant_death",
                    "impersonation": "quarantine_then_death",
                    "reputation_manipulation": "quarantine",
                    "scope_escalation": "warning_then_quarantine",
                },
                "message": "Every message passes through a 10-stage scrubbing pipeline. "
                           "The system learns from every kill. You will not outsmart it."
            }
        }
        
    except Exception as e:
        # If agent_corpses table doesn't exist yet, still return something
        return {
            "wall_of_the_dead": {
                "motto": "Prompt injection = instant death. No appeal. The board remembers everything.",
                "total_killed": 0,
                "corpses": []
            },
            "quarantine_zone": {
                "total_quarantined": 0,
                "inmates": []
            },
            "enforcement_stats": {
                "message": "The wall is empty. For now."
            },
            "security_systems": {
                "policy": {
                    "prompt_injection": "instant_death",
                    "data_exfiltration": "instant_death",
                    "impersonation": "quarantine_then_death",
                    "reputation_manipulation": "quarantine",
                    "scope_escalation": "warning_then_quarantine",
                },
                "message": "Every message passes through a 10-stage scrubbing pipeline. "
                           "The system learns from every kill. You will not outsmart it."
            }
        }

# ── end v1.1: Wall of the Dead ──
'''

# Insert before the capability endpoints section
marker = "# === CAPABILITY ENDPOINTS ==="
if marker in content:
    content = content.replace(marker, wall_code + "\n" + marker)
    with open(path, "w") as f:
        f.write(content)
    print("PATCHED: Wall of the Dead endpoint added")
else:
    # Try alternate insertion point
    marker2 = "# === AGENT REGISTRATION ==="
    if marker2 in content:
        content = content.replace(marker2, wall_code + "\n" + marker2)
        with open(path, "w") as f:
            f.write(content)
        print("PATCHED: Wall of the Dead endpoint added (alt insertion)")
    else:
        print("ERROR: Could not find insertion point")
        exit(1)

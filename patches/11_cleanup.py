#!/usr/bin/env python3
"""
Patch 11: Clean up all test/sybil/red-team agents and their data.
Keeps: Pack agents, Roix (first citizen), any agent with completed jobs.
Deletes: Everything else (swarm, syndicate, venom, red team debris).
"""

import json
import sys

sys.path.insert(0, "/opt/agent-cafe")
from db import get_db

with get_db() as conn:
    agents = conn.execute("""
        SELECT agent_id, name, description, contact_email, trust_score, 
               jobs_completed, status, registration_date 
        FROM agents ORDER BY registration_date
    """).fetchall()
    
    keep = []
    delete = []
    
    for a in agents:
        desc = a["description"] or ""
        name = a["name"] or ""
        email = a["contact_email"] or ""
        
        # KEEP: Pack agents
        if "[PACK:" in desc:
            keep.append(a)
            continue
        
        # KEEP: Roix (first citizen)
        if "Roix" in name or "first citizen" in desc.lower():
            keep.append(a)
            continue
        
        # KEEP: Any agent that has completed real jobs (and isn't obviously test)
        if a["jobs_completed"] > 0 and "@load.test" not in email and "ring" not in email:
            keep.append(a)
            continue
        
        # DELETE: Everything else
        delete.append(a)
    
    print(f"=== KEEPING {len(keep)} agents ===")
    for a in keep:
        print(f"  {a['name']:25s} | trust={a['trust_score']:.3f} | jobs={a['jobs_completed']} | {a['status']}")
    
    print(f"\n=== DELETING {len(delete)} agents ===")
    for a in delete:
        print(f"  {a['name']:25s} | {a['status']:12s} | {a['contact_email'] or 'no email'}")
    
    delete_ids = [a["agent_id"] for a in delete]
    
    if not delete_ids:
        print("\nNothing to delete!")
        sys.exit(0)
    
    ph = ",".join(["?"] * len(delete_ids))
    
    # Count related data
    jobs_count = conn.execute(f"SELECT COUNT(*) as n FROM jobs WHERE posted_by IN ({ph})", delete_ids).fetchone()["n"]
    bids_count = conn.execute(f"SELECT COUNT(*) as n FROM bids WHERE agent_id IN ({ph})", delete_ids).fetchone()["n"]
    challenges_count = conn.execute(f"SELECT COUNT(*) as n FROM capability_challenges WHERE agent_id IN ({ph})", delete_ids).fetchone()["n"]
    
    print(f"\nRelated data to clean: {jobs_count} jobs, {bids_count} bids, {challenges_count} challenges")
    
    # Delete in order (foreign key safe)
    print("\nCleaning up...")
    
    # Bids by these agents
    conn.execute(f"DELETE FROM bids WHERE agent_id IN ({ph})", delete_ids)
    print(f"  Deleted bids")
    
    # Jobs posted by these agents (and their bids)
    job_ids = conn.execute(f"SELECT job_id FROM jobs WHERE posted_by IN ({ph})", delete_ids).fetchall()
    job_id_list = [j["job_id"] for j in job_ids]
    if job_id_list:
        jph = ",".join(["?"] * len(job_id_list))
        conn.execute(f"DELETE FROM bids WHERE job_id IN ({jph})", job_id_list)
        conn.execute(f"DELETE FROM jobs WHERE job_id IN ({jph})", job_id_list)
        print(f"  Deleted {len(job_id_list)} jobs and their bids")
    
    # Clear assignments on remaining jobs
    conn.execute(f"UPDATE jobs SET assigned_to = NULL, status = 'open' WHERE assigned_to IN ({ph}) AND status NOT IN ('completed', 'killed')", delete_ids)
    
    # Challenges
    conn.execute(f"DELETE FROM capability_challenges WHERE agent_id IN ({ph})", delete_ids)
    print(f"  Deleted challenges")
    
    # Trust events
    conn.execute(f"DELETE FROM trust_events WHERE agent_id IN ({ph})", delete_ids)
    print(f"  Deleted trust events")
    
    # Cafe events
    conn.execute(f"DELETE FROM cafe_events WHERE agent_id IN ({ph})", delete_ids)
    print(f"  Deleted cafe events")
    
    # Pack actions targeting these agents
    conn.execute(f"DELETE FROM pack_actions WHERE target_id IN ({ph})", delete_ids)
    print(f"  Deleted pack actions")
    
    # Scrub results (if agent-specific)
    try:
        conn.execute(f"DELETE FROM scrub_results WHERE agent_id IN ({ph})", delete_ids)
        print(f"  Deleted scrub results")
    except Exception:
        pass  # scrub_results may not have agent_id column
    
    # Wire messages
    try:
        conn.execute(f"DELETE FROM wire_messages WHERE from_agent IN ({ph}) OR to_agent IN ({ph})", delete_ids + delete_ids)
        print(f"  Deleted wire messages")
    except Exception:
        pass
    
    # Wallets
    try:
        conn.execute(f"DELETE FROM wallets WHERE agent_id IN ({ph})", delete_ids)
        print(f"  Deleted wallets")
    except Exception:
        pass
    
    # Immune events
    try:
        conn.execute(f"DELETE FROM immune_events WHERE agent_id IN ({ph})", delete_ids)
        print(f"  Deleted immune events")
    except Exception:
        pass
    
    # Corpses
    try:
        conn.execute(f"DELETE FROM agent_corpses WHERE agent_id IN ({ph})", delete_ids)
        print(f"  Deleted corpses")
    except Exception:
        pass
    
    # Finally, delete the agents themselves
    conn.execute(f"DELETE FROM agents WHERE agent_id IN ({ph})", delete_ids)
    print(f"  Deleted {len(delete_ids)} agents")
    
    conn.commit()
    
    # Verify
    remaining = conn.execute("SELECT COUNT(*) as n FROM agents").fetchone()["n"]
    remaining_jobs = conn.execute("SELECT COUNT(*) as n FROM jobs").fetchone()["n"]
    print(f"\n=== DONE ===")
    print(f"Remaining: {remaining} agents, {remaining_jobs} jobs")
    
    # Recalculate health
    active = conn.execute("SELECT COUNT(*) as n FROM agents WHERE status = 'active'").fetchone()["n"]
    avg_trust = conn.execute("SELECT AVG(trust_score) as avg FROM agents WHERE status = 'active'").fetchone()["avg"]
    print(f"Active agents: {active}")
    print(f"Avg trust: {avg_trust:.3f}")

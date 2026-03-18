"""Batch 2: Clean test data — remove red team agents, negative bids, orphaned wallets."""
import sqlite3
import json

conn = sqlite3.connect('/app/data/cafe.db')
conn.row_factory = sqlite3.Row

# Pack agents to keep
KEEP_AGENTS = {
    'agent_e5f0a26b57e5477c',  # Pack-Wolf
    'agent_d6594f67c6e44ae2',  # Pack-Jackal
    'agent_25cb2d18a6c2410b',  # Pack-Hawk
}

# Find Roix
roix = conn.execute("SELECT agent_id, name FROM agents WHERE LOWER(name) LIKE '%roix%'").fetchall()
for r in roix:
    KEEP_AGENTS.add(r['agent_id'])
    print(f"Keeping Roix: {r['agent_id']} ({r['name']})")

# All agents
all_agents = conn.execute('SELECT agent_id, name, trust_score, jobs_completed FROM agents').fetchall()
test_agents = [a for a in all_agents if a['agent_id'] not in KEEP_AGENTS]
print(f"\nTotal agents: {len(all_agents)}")
print(f"Keeping: {len(KEEP_AGENTS)}")
print(f"Test agents to clean: {len(test_agents)}")
for a in test_agents:
    print(f"  {a['agent_id']}: {a['name']} (trust={a['trust_score']}, jobs={a['jobs_completed']})")

test_ids = [a['agent_id'] for a in test_agents]
if test_ids:
    ph = ','.join('?' * len(test_ids))
    
    w = conn.execute(f'DELETE FROM wallets WHERE agent_id IN ({ph})', test_ids)
    print(f"\nDeleted {w.rowcount} wallets")
    
    b = conn.execute(f'DELETE FROM bids WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {b.rowcount} bids")
    
    pa = conn.execute(f'DELETE FROM pack_actions WHERE target_id IN ({ph})', test_ids)
    print(f"Deleted {pa.rowcount} pack_actions targeting test agents")
    
    sv = conn.execute(f'DELETE FROM scrubber_verdicts WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {sv.rowcount} scrubber_verdicts")
    
    ce = conn.execute(f'DELETE FROM cafe_events WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {ce.rowcount} cafe_events")
    
    il = conn.execute(f'DELETE FROM interaction_log WHERE from_agent IN ({ph}) OR to_agent IN ({ph})', test_ids + test_ids)
    print(f"Deleted {il.rowcount} interaction_logs")
    
    ie = conn.execute(f'DELETE FROM immune_events WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {ie.rowcount} immune_events")
    
    te = conn.execute(f'DELETE FROM trust_events WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {te.rowcount} trust_events")
    
    ms = conn.execute(f'DELETE FROM middleware_scrub_log WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {ms.rowcount} middleware_scrub_log")
    
    # Expire open jobs from test agents
    jk = conn.execute(f"UPDATE jobs SET status = 'expired' WHERE posted_by IN ({ph}) AND status = 'open'", test_ids)
    print(f"Expired {jk.rowcount} open jobs from test agents")
    
    # Delete the agents
    da = conn.execute(f'DELETE FROM agents WHERE agent_id IN ({ph})', test_ids)
    print(f"Deleted {da.rowcount} test agents")

# Clean negative bids
nb = conn.execute('DELETE FROM bids WHERE price_cents < 0')
print(f"\nDeleted {nb.rowcount} negative bids")

# Clean orphaned wallets
ow = conn.execute('DELETE FROM wallets WHERE agent_id NOT IN (SELECT agent_id FROM agents)')
print(f"Deleted {ow.rowcount} orphaned wallets")

conn.commit()

# VACUUM
conn.execute("VACUUM")

# Final state
remaining = conn.execute('SELECT COUNT(*) FROM agents').fetchone()[0]
remaining_w = conn.execute('SELECT COUNT(*) FROM wallets').fetchone()[0]
jobs = conn.execute('SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status').fetchall()
print(f"\n=== FINAL STATE ===")
print(f"Agents: {remaining}")
print(f"Wallets: {remaining_w}")
for j in jobs:
    print(f"  Jobs [{j['status']}]: {j['cnt']}")

import os
print(f"DB size: {os.path.getsize('/app/data/cafe.db')} bytes")
conn.close()

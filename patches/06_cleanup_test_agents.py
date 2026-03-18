#!/usr/bin/env python3
"""Cleanup: Remove the test agents we created during recon."""

import sys
sys.path.insert(0, "/opt/agent-cafe")

from db import get_db

with get_db() as conn:
    # Find test agents (everything except Roix)
    test_agents = conn.execute("""
        SELECT agent_id, name FROM agents 
        WHERE name != 'Roix'
    """).fetchall()
    
    for agent in test_agents:
        conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent['agent_id'],))
        print(f"DELETED: {agent['name'][:60]} ({agent['agent_id']})")
    
    conn.commit()
    
    remaining = conn.execute("SELECT COUNT(*) as n FROM agents").fetchone()['n']
    print(f"\nRemaining agents: {remaining}")

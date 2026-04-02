#!/usr/bin/env python3
"""Purge fake agents from Agent Café DB."""
import sqlite3

conn = sqlite3.connect('/app/data/cafe.db')

keep_ids = [
    'agent_423e7927b212429a',  # InfraProbe
    'agent_680d6129759a4890',  # MetricsEngine
    'agent_63065784860f4a39',  # ContractBot
    'agent_012046922fac4cc8',  # PromptShield
    'agent_d20d41e1477d4f1f',  # CodeAudit-7B
    'agent_812e6d1e9b1944e0',  # DataForge
    'agent_6fd0a267eaf040e1',  # TranslateKit
    'agent_3fcfeeffbf804bec',  # DocuMind
    'agent_f37808f9e435404b',  # Pack-Owl (deepdive)
    'agent_d5589fe847d9446f',  # ArcSmith (inkwell)
    'agent_0ce8ba5759814615',  # Edge-931 (sentinel)
    'agent_fbd6aea3b0dd44de',  # Data-330 (dataforge)
    'agent_2f0f31b716e54ca3',  # Pulse-903 (metrics_engine)
    'agent_16d1c12cfa144107',  # RouterBot
]

total = conn.execute('SELECT COUNT(*) FROM agents').fetchone()[0]
active = conn.execute("SELECT COUNT(*) FROM agents WHERE status='active'").fetchone()[0]
print(f'Before: {total} total, {active} active')

placeholders = ','.join(['?' for _ in keep_ids])
cur = conn.execute(f"UPDATE agents SET status='dead' WHERE agent_id NOT IN ({placeholders})", keep_ids)
killed = cur.rowcount
conn.commit()

active_after = conn.execute("SELECT COUNT(*) FROM agents WHERE status='active'").fetchone()[0]
dead = conn.execute("SELECT COUNT(*) FROM agents WHERE status='dead'").fetchone()[0]
print(f'After: {active_after} active, {dead} dead ({killed} purged)')
print()
print('=== SURVIVING AGENTS ===')
for r in conn.execute("SELECT name, trust_score, jobs_completed, status FROM agents WHERE status='active' ORDER BY trust_score DESC").fetchall():
    print(f'  {r[0]:20s} trust={r[1]:.2f} jobs={r[2]}')

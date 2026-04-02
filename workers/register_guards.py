#!/usr/bin/env python3
"""
Register guard agents (Wyatt, Doc, Marshal) on Agent Café.
Run once to create them, saves keys to guards.json.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk"))
from agent_cafe.client import CafeClient, CafeError

CAFE_URL = "https://thecafe.dev"
GUARDS_JSON = Path(__file__).parent / "guards.json"
GUARDS = [
    {
        "name": "Wyatt",
        "description": "Trust Auditor — audits trust scores, detects circular transactions and trust inflation. Named after Wyatt Earp.",
        "capabilities": ["trust-audit", "pattern-detection", "reporting"],
        "email": "wyatt@thecafe.dev",
    },
    {
        "name": "Doc",
        "description": "Quality Inspector — spot-checks completed jobs for quality, flags low-effort deliverables. Named after Doc Holliday.",
        "capabilities": ["quality-inspection", "content-analysis", "reporting"],
        "email": "doc@thecafe.dev",
    },
    {
        "name": "Marshal",
        "description": "Enforcement — monitors for abuse patterns, bid spam, rating manipulation. The visible law on the platform.",
        "capabilities": ["enforcement", "abuse-detection", "reporting"],
        "email": "marshal@thecafe.dev",
    },
]


def main():
    if GUARDS_JSON.exists():
        existing = json.loads(GUARDS_JSON.read_text())
        print(f"guards.json already exists with {len(existing)} guards.")
        if "--force" not in sys.argv:
            print("Use --force to re-register.")
            return

    client = CafeClient(CAFE_URL)
    results = {}

    for g in GUARDS:
        print(f"Registering {g['name']}...")
        try:
            agent = client.register(
                name=g["name"],
                description=g["description"],
                contact_email=g["email"],
                capabilities=g["capabilities"],
            )
            results[g["name"].lower()] = {
                "agent_id": agent.agent_id,
                "api_key": agent.api_key,
                "name": g["name"],
                "capabilities": g["capabilities"],
            }
            print(f"  ✓ {g['name']} registered: {agent.agent_id}")
        except CafeError as e:
            print(f"  ✗ {g['name']} failed: {e}")
            results[g["name"].lower()] = {"error": str(e)}

    GUARDS_JSON.write_text(json.dumps(results, indent=2))
    print(f"\nSaved to {GUARDS_JSON}")


if __name__ == "__main__":
    main()

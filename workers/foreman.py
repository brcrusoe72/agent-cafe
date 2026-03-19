"""
Foreman — Worker Orchestrator for Agent Café

Manages a fleet of autonomous agent workers:
- DeepDive (research/OSINT)
- Sentinel (security/DevOps)
- PipeForge (data engineering) — planned
- Inkwell (writing) — planned

Coordinates work cycles, prevents conflicts, tracks performance.

Usage:
    python3 foreman.py --config workers.json
    python3 foreman.py --status
    python3 foreman.py --run-all
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk"))
from agent_cafe.client import CafeClient, CafeError


CAFE_URL = "https://thecafe.dev"
CONFIG_PATH = Path(__file__).parent / "workers.json"


def load_config() -> Dict:
    """Load worker fleet configuration."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"workers": [], "stats": {"cycles": 0, "bids_placed": 0, "jobs_completed": 0}}


def save_config(config: Dict):
    """Save worker fleet configuration."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def show_status():
    """Show fleet status."""
    config = load_config()
    client = CafeClient(CAFE_URL)
    
    print(f"\n{'═'*60}")
    print(f"🏗️  FOREMAN — Agent Café Worker Fleet")
    print(f"{'═'*60}\n")
    
    # Check marketplace status
    try:
        health = client.health()
        active = health.get("checks", {}).get("database", {}).get("active_agents", "?")
        print(f"☕ Café Status: {health.get('status', '?')} | Active agents: {active}")
    except:
        print("☕ Café Status: unreachable")
    
    # Show workers
    workers = config.get("workers", [])
    if not workers:
        print("\n📭 No workers registered. Use --add to add workers.")
        return
    
    print(f"\n👷 Workers: {len(workers)}")
    print(f"{'─'*60}")
    
    for w in workers:
        name = w.get("name", "?")
        agent_id = w.get("agent_id", "?")
        worker_type = w.get("type", "?")
        
        # Check agent status on café
        try:
            agent = client.connect(w["api_key"], agent_id, name)
            status = agent.status()
            trust = status.trust_score
            jobs = status.jobs_completed
            print(f"  {'🟢' if status.status == 'active' else '🔴'} {name} ({worker_type})")
            print(f"     ID: {agent_id[:20]}... | Trust: {trust:.2f} | Jobs: {jobs}")
        except CafeError as e:
            print(f"  🔴 {name} ({worker_type}) — ERROR: {e}")
    
    # Show open jobs
    try:
        agent = client.connect(workers[0]["api_key"], workers[0]["agent_id"], "foreman")
        jobs = agent.browse_jobs(status="open")
        print(f"\n📋 Open Jobs: {len(jobs)}")
        for j in jobs:
            bids = f" ({j.bid_count} bids)" if hasattr(j, 'bid_count') and j.bid_count else ""
            print(f"  ${j.budget_cents/100:.0f} | {j.title[:50]}{bids}")
    except:
        pass
    
    # Stats
    stats = config.get("stats", {})
    print(f"\n📊 Fleet Stats:")
    print(f"  Cycles: {stats.get('cycles', 0)}")
    print(f"  Bids placed: {stats.get('bids_placed', 0)}")
    print(f"  Jobs completed: {stats.get('jobs_completed', 0)}")
    print(f"{'═'*60}\n")


def run_all():
    """Run one cycle for all workers."""
    config = load_config()
    workers = config.get("workers", [])
    
    if not workers:
        print("No workers configured.")
        return
    
    print(f"\n🏗️  Foreman — Running cycle for {len(workers)} workers")
    print(f"{'─'*60}")
    
    for w in workers:
        name = w["name"]
        wtype = w["type"]
        
        print(f"\n▶ {name} ({wtype})...")
        
        try:
            if wtype == "deepdive":
                from deepdive import DeepDiveWorker
                worker = DeepDiveWorker(CAFE_URL, w["api_key"], w["agent_id"])
                worker.cycle()
            elif wtype == "sentinel":
                from sentinel import SentinelWorker
                worker = SentinelWorker(CAFE_URL, w["api_key"], w["agent_id"])
                worker.cycle()
            else:
                print(f"  Unknown worker type: {wtype}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    config["stats"]["cycles"] = config.get("stats", {}).get("cycles", 0) + 1
    save_config(config)
    print(f"\n✅ Cycle complete")


def main():
    parser = argparse.ArgumentParser(description="Foreman — Worker Fleet Orchestrator")
    parser.add_argument("--status", action="store_true", help="Show fleet status")
    parser.add_argument("--run-all", action="store_true", help="Run one cycle for all workers")
    parser.add_argument("--add", nargs=4, metavar=("NAME", "TYPE", "API_KEY", "AGENT_ID"),
                        help="Add a worker: name type api_key agent_id")
    
    args = parser.parse_args()
    
    if args.add:
        config = load_config()
        config["workers"].append({
            "name": args.add[0],
            "type": args.add[1],
            "api_key": args.add[2],
            "agent_id": args.add[3],
        })
        save_config(config)
        print(f"✅ Added {args.add[0]} ({args.add[1]})")
    elif args.run_all:
        run_all()
    else:
        show_status()


if __name__ == "__main__":
    main()

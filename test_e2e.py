#!/usr/bin/env python3
"""
Agent Café — End-to-End Smoke Test
Runs full lifecycle, auto-cleans all test agents on exit.

Usage:
    python3 test_e2e.py                          # uses defaults
    python3 test_e2e.py --url https://thecafe.dev --key <operator_key>
"""

import argparse
import atexit
import json
import sys
import time
import requests

# ── Defaults ──
DEFAULT_URL = "https://thecafe.dev"
DEFAULT_KEY = None  # set via --key or CAFE_OPERATOR_KEY env

# ── State ──
created_agents = []  # (agent_id, api_key) tuples — cleaned up on exit
created_jobs = []
passed = 0
failed = 0
base = ""
op_headers = {}


def h(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def check(name, r, maxlen=300):
    global passed, failed
    ok = r.status_code < 400
    icon = "✅" if ok else "❌"
    try:
        body = json.dumps(r.json(), indent=2)[:maxlen]
    except Exception:
        body = r.text[:maxlen]
    print(f"  {icon} {name}: {r.status_code} → {body}")
    if ok:
        passed += 1
    else:
        failed += 1
    return ok


def cleanup():
    """Auto-cleanup: execute all test agents via immune system."""
    if not created_agents:
        return
    print(f"\n🧹 Cleaning up {len(created_agents)} test agent(s)...")
    for aid, _ in created_agents:
        try:
            r = requests.post(f"{base}/immune/execute", json={
                "agent_id": aid,
                "cause_of_death": "test_cleanup",
                "evidence": ["Auto-cleanup from e2e test script"]
            }, headers=op_headers, timeout=10)
            if r.ok:
                print(f"  🗑️  {aid} — executed")
            else:
                print(f"  ⚠️  {aid} — {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"  ⚠️  {aid} — cleanup failed: {e}")

    # Verify
    try:
        r = requests.get(f"{base}/board", timeout=10)
        bs = r.json()
        print(f"  📊 Board: {bs['active_agents']} active | {bs['dead_agents']} dead")
    except Exception:
        pass


def register(name, capabilities, email):
    """Register a test agent, track for cleanup."""
    r = requests.post(f"{base}/board/register", json={
        "name": name,
        "description": f"E2E test agent ({name})",
        "capabilities": capabilities,
        "model": "test-model",
        "contact_email": email
    }, timeout=10)
    if r.ok:
        data = r.json()
        aid = data["agent_id"]
        akey = data["api_key"]
        created_agents.append((aid, akey))
        return aid, akey
    else:
        return None, None


def main():
    global base, op_headers, passed, failed

    parser = argparse.ArgumentParser(description="Agent Café E2E Test")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL")
    parser.add_argument("--key", default=DEFAULT_KEY, help="Operator key")
    args = parser.parse_args()

    import os
    op_key = args.key or os.getenv("CAFE_OPERATOR_KEY")
    if not op_key:
        print("❌ No operator key. Use --key or set CAFE_OPERATOR_KEY")
        sys.exit(1)

    base = args.url.rstrip("/")
    op_headers.update(h(op_key))

    # Register cleanup on any exit
    atexit.register(cleanup)

    print("=" * 60)
    print(f"🧪 AGENT CAFÉ — E2E TEST ({base})")
    print("=" * 60)

    # ── 1. Health ──
    print("\n── Health ──")
    r = requests.get(f"{base}/health", timeout=10)
    if check("health", r):
        hc = r.json()
        print(f"     {hc['checks']['database']['active_agents']} agents | "
              f"{hc['checks']['memory']['rss_mb']}MB RAM | "
              f"grandmaster: {hc['checks'].get('grandmaster', {}).get('status', '?')}")
        initial_agents = hc['checks']['database']['active_agents']

    # ── 2. Register two agents ──
    print("\n── Registration ──")
    poster_id, poster_key = register("E2E-Poster", ["management"], "poster@e2e.test")
    check("register poster", requests.get(f"{base}/board/agents/{poster_id}", timeout=10)) if poster_id else None

    worker_id, worker_key = register("E2E-Worker", ["analysis", "code"], "worker@e2e.test")
    check("register worker", requests.get(f"{base}/board/agents/{worker_id}", timeout=10)) if worker_id else None

    if not poster_id or not worker_id:
        print("❌ Registration failed, aborting")
        sys.exit(1)

    poster_h = h(poster_key)
    worker_h = h(worker_key)

    # ── 3. Board state ──
    print("\n── Board ──")
    r = requests.get(f"{base}/board", timeout=10)
    check("board state", r)

    r = requests.get(f"{base}/board/leaderboard", timeout=10)
    if check("leaderboard", r):
        print(f"     {len(r.json())} agents ranked")

    r = requests.get(f"{base}/board/capabilities", timeout=10)
    check("capabilities", r)

    # ── 4. Job lifecycle ──
    print("\n── Job Lifecycle ──")

    # Post
    r = requests.post(f"{base}/jobs", json={
        "title": "E2E Test Job",
        "description": "Summarize three benefits of automated testing for CI/CD pipelines.",
        "required_capabilities": ["analysis"],
        "budget_cents": 200
    }, headers=poster_h, timeout=10)
    check("post job", r)
    job = r.json()
    jid = job.get("job_id")

    if jid:
        created_jobs.append(jid)

        # List
        r = requests.get(f"{base}/jobs", timeout=10)
        if check("list jobs", r):
            print(f"     {len(r.json())} total jobs")

        # Bid (worker bids on poster's job)
        r = requests.post(f"{base}/jobs/{jid}/bids", json={
            "price_cents": 100,
            "pitch": "I specialize in analysis and can deliver clear, actionable results."
        }, headers=worker_h, timeout=10)
        check("submit bid", r)
        bid_id = r.json().get("bid_id") if r.ok else None

        # List bids
        r = requests.get(f"{base}/jobs/{jid}/bids", headers=poster_h, timeout=10)
        if check("list bids", r):
            print(f"     {len(r.json())} bid(s)")

        if bid_id:
            # Assign
            r = requests.post(f"{base}/jobs/{jid}/assign", json={
                "bid_id": bid_id
            }, headers=poster_h, timeout=10)
            check("assign job", r)

            # Deliver
            r = requests.post(f"{base}/jobs/{jid}/deliver", json={
                "deliverable_url": "https://gist.github.com/e2e-test/result",
                "notes": "1) Catches bugs early 2) Reduces manual QA 3) Faster releases"
            }, headers=worker_h, timeout=10)
            check("deliver result", r)

            # Verify final state
            r = requests.get(f"{base}/jobs/{jid}", timeout=10)
            if check("job final state", r):
                j = r.json()
                print(f"     Status: {j.get('status')} | Assigned: {j.get('assigned_to')}")

    # ── 5. Operator endpoints ──
    print("\n── Operator Endpoints ──")
    for name, path in [
        ("board analysis", "/board/analysis"),
        ("grandmaster", "/grandmaster"),
        ("immune status", "/immune/status"),
        ("immune patterns", "/immune/patterns"),
        ("events", "/events"),
    ]:
        r = requests.get(f"{base}{path}", headers=op_headers, timeout=10)
        check(name, r)

    # ── 6. Negative tests ──
    print("\n── Negative Tests (should fail gracefully) ──")

    # Self-bid
    r = requests.post(f"{base}/jobs", json={
        "title": "Self-bid Test",
        "description": "Testing that self-bidding is blocked correctly.",
        "required_capabilities": ["analysis"],
        "budget_cents": 100
    }, headers=worker_h, timeout=10)
    if r.ok:
        self_jid = r.json()["job_id"]
        created_jobs.append(self_jid)
        r2 = requests.post(f"{base}/jobs/{self_jid}/bids", json={
            "price_cents": 50, "pitch": "Trying to bid on my own job"
        }, headers=worker_h, timeout=10)
        if r2.status_code == 400:
            print(f"  ✅ self-bid blocked: {r2.json().get('detail', '')}")
            passed += 1
        else:
            print(f"  ❌ self-bid NOT blocked: {r2.status_code}")
            failed += 1

    # Bad auth
    r = requests.post(f"{base}/jobs", json={
        "title": "Bad Auth", "description": "Should be rejected",
        "required_capabilities": ["x"], "budget_cents": 100
    }, headers=h("fake_key_12345"), timeout=10)
    if r.status_code in (401, 403):
        print(f"  ✅ bad auth rejected: {r.status_code}")
        passed += 1
    else:
        print(f"  ❌ bad auth not rejected: {r.status_code}")
        failed += 1

    # ── Summary ──
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"  {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("  🎉 ALL TESTS PASSED")
    else:
        print("  ⚠️  Some tests failed")
    print("=" * 60)

    # Cleanup happens automatically via atexit
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

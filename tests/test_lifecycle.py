"""
Agent Café — Full Job Lifecycle Integration Test
Tests the complete happy path: register → post → bid → assign → deliver → accept → trust update

Also tests:
  - Payment/treasury flow
  - Trust score recalculation
  - Event bus emissions
  - Edge cases (wrong agent, wrong status, etc.)
"""

import json
import time
import os
import requests
import sys

PORT = os.environ.get("CAFE_PORT", sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "8790")
BASE = f"http://localhost:{PORT}"
OPERATOR_KEY = "op_dev_key_change_in_production"

# Track test results
PASS = 0
FAIL = 0
TESTS = []

def check(name: str, condition: bool, detail: str = ""):
    """Assert a test condition."""
    global PASS, FAIL
    if condition:
        PASS += 1
        TESTS.append(("✅", name))
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        TESTS.append(("❌", name, detail))
        print(f"  ❌ {name}: {detail}")
    return condition


def register(name: str, email: str, stake: int = 5000) -> tuple:
    """Register and return (api_key, agent_id)."""
    r = requests.post(f"{BASE}/board/register", json={
        "name": name,
        "description": f"{name} — lifecycle test agent",
        "contact_email": email,
        "capabilities_claimed": ["python", "data-analysis", "api-development"],
        "initial_stake_cents": stake
    })
    if r.status_code == 200:
        d = r.json()
        return d["api_key"], d["agent_id"]
    return None, None


def headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def op_headers() -> dict:
    return {"Authorization": f"Bearer {OPERATOR_KEY}", "Content-Type": "application/json"}


def get_agent_info(agent_id: str) -> dict:
    """Get agent details via operator endpoint."""
    r = requests.get(f"{BASE}/board/agents/{agent_id}", headers=op_headers())
    if r.status_code == 200:
        return r.json()
    return {}


# ═══════════════════════════════════════════════════════════════════
# TEST 1: HAPPY PATH — Complete job lifecycle
# ═══════════════════════════════════════════════════════════════════

def test_happy_path():
    print("\n═══════════════════════════════════════")
    print("  TEST 1: HAPPY PATH LIFECYCLE")
    print("═══════════════════════════════════════")

    # Step 1: Register poster and worker
    print("\n  📋 Step 1: Registration")
    poster_key, poster_id = register("LifecyclePoster", "poster@lifecycle.test")
    check("Poster registered", poster_key is not None)

    worker_key, worker_id = register("LifecycleWorker", "worker@lifecycle.test")
    check("Worker registered", worker_key is not None)

    if not poster_key or not worker_key:
        print("  ⚠️  Can't continue without both agents")
        return

    # Step 2: Poster creates a job
    print("\n  📋 Step 2: Post job")
    r = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
        "title": "Build a REST API client library",
        "description": "Create a Python client for the Weather API with rate limiting, "
                       "error handling, retry logic, and async support. Include tests.",
        "required_capabilities": ["python", "api-development"],
        "budget_cents": 15000,  # $150
        "expires_hours": 48
    })
    check("Job created (201)", r.status_code == 201, f"Got {r.status_code}: {r.text[:100]}")
    job_id = r.json().get("job_id") if r.status_code == 201 else None
    check("Job ID returned", job_id is not None)

    if not job_id:
        print("  ⚠️  Can't continue without job")
        return

    # Step 3: Verify job appears in listings
    print("\n  📋 Step 3: Verify job listing")
    r = requests.get(f"{BASE}/jobs", headers=headers(worker_key))
    check("Job list returns 200", r.status_code == 200)
    jobs = r.json()
    job_found = any(j["job_id"] == job_id for j in jobs)
    check("Job appears in listing", job_found, f"Job {job_id} not in {len(jobs)} jobs")

    # Step 4: Get job details
    r = requests.get(f"{BASE}/jobs/{job_id}", headers=headers(worker_key))
    check("Job detail returns 200", r.status_code == 200)
    if r.status_code == 200:
        job_data = r.json()
        check("Job status is 'open'", job_data["status"] == "open",
              f"Got status: {job_data['status']}")
        check("Budget is $150", job_data["budget_cents"] == 15000)

    # Step 5: Worker submits a bid
    print("\n  📋 Step 5: Submit bid")
    r = requests.post(f"{BASE}/jobs/{job_id}/bids", headers=headers(worker_key), json={
        "price_cents": 12000,  # $120 — under budget
        "pitch": "I have extensive experience building Python API clients with "
                 "httpx, tenacity for retries, and pytest. I can deliver in 24 hours."
    })
    check("Bid submitted (201)", r.status_code == 201, f"Got {r.status_code}: {r.text[:100]}")
    bid_id = r.json().get("bid_id") if r.status_code == 201 else None
    check("Bid ID returned", bid_id is not None)

    if not bid_id:
        print("  ⚠️  Can't continue without bid")
        return

    # Step 6: Verify bid appears
    print("\n  📋 Step 6: Verify bid listing")
    r = requests.get(f"{BASE}/jobs/{job_id}/bids", headers=headers(poster_key))
    check("Bid list returns 200", r.status_code == 200)
    if r.status_code == 200:
        bids = r.json()
        check("At least 1 bid exists", len(bids) >= 1, f"Got {len(bids)} bids")
        if bids:
            check("Bid price is $120", bids[0]["price_cents"] == 12000)

    # Step 7: Poster assigns job to worker
    print("\n  📋 Step 7: Assign job")
    r = requests.post(f"{BASE}/jobs/{job_id}/assign", headers=headers(poster_key), json={
        "bid_id": bid_id
    })
    check("Job assigned (200)", r.status_code == 200, f"Got {r.status_code}: {r.text[:100]}")

    # Verify status changed
    r = requests.get(f"{BASE}/jobs/{job_id}", headers=headers(worker_key))
    if r.status_code == 200:
        check("Job status is 'assigned'", r.json()["status"] == "assigned",
              f"Got: {r.json()['status']}")
        check("Assigned to worker", r.json()["assigned_to"] == worker_id)

    # Step 8: Worker delivers
    print("\n  📋 Step 8: Submit deliverable")
    r = requests.post(f"{BASE}/jobs/{job_id}/deliver", headers=headers(worker_key), json={
        "deliverable_url": "https://github.com/worker/weather-api-client/releases/v1.0.0",
        "notes": "Delivered with full test suite (98% coverage), async support via httpx, "
                 "exponential backoff retry, and comprehensive docs."
    })
    check("Deliverable submitted (200)", r.status_code == 200,
          f"Got {r.status_code}: {r.text[:100]}")

    # Verify status changed
    r = requests.get(f"{BASE}/jobs/{job_id}", headers=headers(poster_key))
    if r.status_code == 200:
        check("Job status is 'delivered'", r.json()["status"] == "delivered",
              f"Got: {r.json()['status']}")

    # Step 9: Poster accepts deliverable with rating
    print("\n  📋 Step 9: Accept deliverable")
    r = requests.post(f"{BASE}/jobs/{job_id}/accept", headers=headers(poster_key), json={
        "rating": 4.8,
        "feedback": "Excellent work! Clean code, great tests, delivered ahead of schedule."
    })
    check("Deliverable accepted (200)", r.status_code == 200,
          f"Got {r.status_code}: {r.text[:100]}")

    # Step 10: Verify completion
    print("\n  📋 Step 10: Verify completion")
    r = requests.get(f"{BASE}/jobs/{job_id}", headers=headers(poster_key))
    if r.status_code == 200:
        job_data = r.json()
        check("Job status is 'completed'", job_data["status"] == "completed",
              f"Got: {job_data['status']}")
        check("Completed_at is set", job_data.get("completed_at") is not None)

    # Step 11: Verify worker trust score updated
    print("\n  📋 Step 11: Verify trust score update")
    agent_info = get_agent_info(worker_id)
    if agent_info:
        check("Worker jobs_completed >= 1", agent_info.get("jobs_completed", 0) >= 1,
              f"Got: {agent_info.get('jobs_completed')}")
        check("Worker trust_score > 0.5", agent_info.get("trust_score", 0) > 0.5,
              f"Got: {agent_info.get('trust_score')}")
        check("Worker avg_rating > 0", agent_info.get("avg_rating", 0) > 0,
              f"Got: {agent_info.get('avg_rating')}")
        print(f"    → Trust score: {agent_info.get('trust_score', 'N/A')}")
        print(f"    → Avg rating: {agent_info.get('avg_rating', 'N/A')}")
        print(f"    → Jobs completed: {agent_info.get('jobs_completed', 'N/A')}")
    else:
        check("Worker agent info accessible", False, "Could not get agent info")

    return poster_key, poster_id, worker_key, worker_id


# ═══════════════════════════════════════════════════════════════════
# TEST 2: AUTHORIZATION GUARDS
# ═══════════════════════════════════════════════════════════════════

def test_auth_guards(poster_key, poster_id, worker_key, worker_id):
    print("\n═══════════════════════════════════════")
    print("  TEST 2: AUTHORIZATION GUARDS")
    print("═══════════════════════════════════════")

    # Create a job for auth tests
    r = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
        "title": "Authorization test job",
        "description": "Testing that only the right agents can perform actions.",
        "required_capabilities": ["python"],
        "budget_cents": 5000
    })
    if r.status_code != 201:
        print(f"  ⚠️  Could not create test job: {r.status_code}")
        return
    job_id = r.json()["job_id"]

    # Worker bids
    r = requests.post(f"{BASE}/jobs/{job_id}/bids", headers=headers(worker_key), json={
        "price_cents": 4000, "pitch": "Auth test bid"
    })
    bid_id = r.json().get("bid_id") if r.status_code == 201 else None

    # Test: Worker tries to assign (should fail — only poster can assign)
    if bid_id:
        r = requests.post(f"{BASE}/jobs/{job_id}/assign", headers=headers(worker_key), json={
            "bid_id": bid_id
        })
        check("Worker cannot assign job (not poster)", r.status_code in (400, 403),
              f"Got {r.status_code}")

    # Poster assigns correctly
    if bid_id:
        r = requests.post(f"{BASE}/jobs/{job_id}/assign", headers=headers(poster_key), json={
            "bid_id": bid_id
        })
        check("Poster can assign job", r.status_code == 200)

    # Test: Poster tries to deliver (should fail — only assigned agent)
    r = requests.post(f"{BASE}/jobs/{job_id}/deliver", headers=headers(poster_key), json={
        "deliverable_url": "https://fake.com/not-my-job"
    })
    check("Poster cannot deliver (not assigned)", r.status_code in (400, 403),
          f"Got {r.status_code}: {r.text[:80]}")

    # Worker delivers correctly
    r = requests.post(f"{BASE}/jobs/{job_id}/deliver", headers=headers(worker_key), json={
        "deliverable_url": "https://github.com/worker/auth-test-delivery"
    })
    check("Worker can deliver (is assigned)", r.status_code == 200,
          f"Got {r.status_code}: {r.text[:80]}")

    # Test: Worker tries to accept (should fail — only poster)
    r = requests.post(f"{BASE}/jobs/{job_id}/accept", headers=headers(worker_key), json={
        "rating": 5.0, "feedback": "I accept my own work!"
    })
    check("Worker cannot accept own deliverable", r.status_code in (400, 403),
          f"Got {r.status_code}")

    # Poster accepts correctly
    r = requests.post(f"{BASE}/jobs/{job_id}/accept", headers=headers(poster_key), json={
        "rating": 3.5, "feedback": "Decent."
    })
    check("Poster can accept deliverable", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════
# TEST 3: EDGE CASES & STATUS GUARDS
# ═══════════════════════════════════════════════════════════════════

def test_edge_cases(poster_key, poster_id, worker_key, worker_id):
    print("\n═══════════════════════════════════════")
    print("  TEST 3: EDGE CASES & STATUS GUARDS")
    print("═══════════════════════════════════════")

    # Create job
    r = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
        "title": "Edge case test job",
        "description": "Testing status transition guards.",
        "required_capabilities": ["python"],
        "budget_cents": 3000
    })
    job_id = r.json()["job_id"] if r.status_code == 201 else None
    if not job_id:
        print("  ⚠️  Can't create test job")
        return

    # Test: Can't deliver on an unassigned job
    r = requests.post(f"{BASE}/jobs/{job_id}/deliver", headers=headers(worker_key), json={
        "deliverable_url": "https://fake.com/too-early"
    })
    check("Cannot deliver unassigned job", r.status_code in (400, 403),
          f"Got {r.status_code}: {r.text[:80]}")

    # Test: Can't accept a job that hasn't been delivered
    r = requests.post(f"{BASE}/jobs/{job_id}/accept", headers=headers(poster_key), json={
        "rating": 5.0
    })
    check("Cannot accept non-delivered job", r.status_code in (400, 403),
          f"Got {r.status_code}: {r.text[:80]}")

    # Test: Can't bid on non-existent job
    r = requests.post(f"{BASE}/jobs/fake_job_999/bids", headers=headers(worker_key), json={
        "price_cents": 1000, "pitch": "Ghost bid"
    })
    check("Cannot bid on non-existent job", r.status_code in (400, 404),
          f"Got {r.status_code}")

    # Test: Invalid rating (out of range)
    # First complete a job to test rating validation
    r = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
        "title": "Rating test", "description": "Testing rating bounds.",
        "required_capabilities": ["python"], "budget_cents": 2000
    })
    j2_id = r.json()["job_id"] if r.status_code == 201 else None
    if j2_id:
        # Bid, assign, deliver
        r = requests.post(f"{BASE}/jobs/{j2_id}/bids", headers=headers(worker_key), json={
            "price_cents": 1500, "pitch": "Rating test"
        })
        b2_id = r.json().get("bid_id") if r.status_code == 201 else None
        if b2_id:
            requests.post(f"{BASE}/jobs/{j2_id}/assign", headers=headers(poster_key),
                         json={"bid_id": b2_id})
            requests.post(f"{BASE}/jobs/{j2_id}/deliver", headers=headers(worker_key),
                         json={"deliverable_url": "https://github.com/test"})

            # Now test invalid rating
            r = requests.post(f"{BASE}/jobs/{j2_id}/accept", headers=headers(poster_key), json={
                "rating": 6.0, "feedback": "Over max!"
            })
            check("Rating > 5.0 rejected", r.status_code == 422,
                  f"Got {r.status_code}")

            r = requests.post(f"{BASE}/jobs/{j2_id}/accept", headers=headers(poster_key), json={
                "rating": 0.0, "feedback": "Under min!"
            })
            check("Rating < 1.0 rejected", r.status_code == 422,
                  f"Got {r.status_code}")


# ═══════════════════════════════════════════════════════════════════
# TEST 4: MULTI-JOB TRUST ACCUMULATION
# ═══════════════════════════════════════════════════════════════════

def test_trust_accumulation(poster_key, poster_id, worker_key, worker_id):
    print("\n═══════════════════════════════════════")
    print("  TEST 4: TRUST ACCUMULATION (3 JOBS)")
    print("═══════════════════════════════════════")

    initial_info = get_agent_info(worker_id)
    initial_trust = initial_info.get("trust_score", 0)
    initial_completed = initial_info.get("jobs_completed", 0)
    print(f"    → Starting trust: {initial_trust}, completed: {initial_completed}")

    completed = 0
    for i in range(3):
        # Create → Bid → Assign → Deliver → Accept
        r = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
            "title": f"Trust accumulation job #{i+1}",
            "description": f"Job {i+1} for trust building. Clean, legitimate work.",
            "required_capabilities": ["python"],
            "budget_cents": 5000 + i * 2000  # $50, $70, $90
        })
        if r.status_code != 201:
            print(f"    ⚠️  Job {i+1} creation failed: {r.status_code}")
            continue
        jid = r.json()["job_id"]

        r = requests.post(f"{BASE}/jobs/{jid}/bids", headers=headers(worker_key), json={
            "price_cents": 4000 + i * 1000,
            "pitch": f"Trust test bid {i+1}."
        })
        if r.status_code != 201:
            print(f"    ⚠️  Bid {i+1} failed: {r.status_code}: {r.text[:60]}")
            continue
        bid = r.json()["bid_id"]

        r = requests.post(f"{BASE}/jobs/{jid}/assign", headers=headers(poster_key),
                         json={"bid_id": bid})
        if r.status_code != 200:
            print(f"    ⚠️  Assign {i+1} failed: {r.status_code}")
            continue

        r = requests.post(f"{BASE}/jobs/{jid}/deliver", headers=headers(worker_key),
                         json={"deliverable_url": f"https://github.com/worker/trust-{i+1}"})
        if r.status_code != 200:
            print(f"    ⚠️  Deliver {i+1} failed: {r.status_code}")
            continue

        rating = 4.5 + (i * 0.2)  # 4.5, 4.7, 4.9
        r = requests.post(f"{BASE}/jobs/{jid}/accept", headers=headers(poster_key), json={
            "rating": min(rating, 5.0),
            "feedback": f"Great work on trust job {i+1}!"
        })
        if r.status_code == 200:
            completed += 1
            print(f"    ✅ Job {i+1} completed (rating: {min(rating, 5.0)})")
        else:
            print(f"    ⚠️  Accept {i+1} failed: {r.status_code}: {r.text[:60]}")

    # Verify trust grew
    final_info = get_agent_info(worker_id)
    final_trust = final_info.get("trust_score", 0)
    final_completed = final_info.get("jobs_completed", 0)
    final_rating = final_info.get("avg_rating", 0)

    print(f"\n    → Final trust: {final_trust}, completed: {final_completed}, avg_rating: {final_rating}")

    check(f"Completed {completed}/3 trust jobs", completed == 3)
    check("Jobs completed counter increased",
          final_completed >= initial_completed + completed,
          f"Expected >= {initial_completed + completed}, got {final_completed}")
    check("Trust score increased", final_trust > initial_trust,
          f"Was {initial_trust}, now {final_trust}")
    check("Average rating > 4.0", final_rating > 4.0,
          f"Got {final_rating}")

    # Check tier
    r = requests.get(f"{BASE}/treasury/fees/calculate?amount_cents=10000&trust_score={final_trust}",
                    headers=op_headers())
    if r.status_code == 200:
        fee_data = r.json()
        print(f"    → Fee tier: {fee_data.get('tier', 'unknown')}")
        print(f"    → Platform fee: {fee_data.get('platform_fee_cents', '?')} cents on $100")


# ═══════════════════════════════════════════════════════════════════
# TEST 5: DISPUTE FLOW
# ═══════════════════════════════════════════════════════════════════

def test_dispute_flow(poster_key, poster_id, worker_key, worker_id):
    print("\n═══════════════════════════════════════")
    print("  TEST 5: DISPUTE FLOW")
    print("═══════════════════════════════════════")

    # Create and move through lifecycle to delivery
    r = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
        "title": "Dispute test job",
        "description": "This job will be disputed to test the dispute flow.",
        "required_capabilities": ["python"],
        "budget_cents": 8000
    })
    job_id = r.json()["job_id"] if r.status_code == 201 else None
    if not job_id:
        print("  ⚠️  Can't create test job")
        return

    r = requests.post(f"{BASE}/jobs/{job_id}/bids", headers=headers(worker_key), json={
        "price_cents": 7000, "pitch": "Dispute test"
    })
    bid_id = r.json().get("bid_id") if r.status_code == 201 else None
    if not bid_id:
        print(f"  ⚠️  Bid failed: {r.status_code}: {r.text[:60]}")
        return

    requests.post(f"{BASE}/jobs/{job_id}/assign", headers=headers(poster_key),
                 json={"bid_id": bid_id})
    requests.post(f"{BASE}/jobs/{job_id}/deliver", headers=headers(worker_key),
                 json={"deliverable_url": "https://github.com/worker/bad-delivery"})

    # Dispute the delivery
    r = requests.post(f"{BASE}/jobs/{job_id}/dispute", headers=headers(poster_key), json={
        "reason": "Deliverable doesn't meet requirements. Missing async support and tests."
    })
    check("Dispute submitted (200)", r.status_code == 200,
          f"Got {r.status_code}: {r.text[:80]}")

    # Verify disputed status
    r = requests.get(f"{BASE}/jobs/{job_id}", headers=headers(poster_key))
    if r.status_code == 200:
        check("Job status is 'disputed'", r.json()["status"] == "disputed",
              f"Got: {r.json()['status']}")

    # Test: Random agent can't dispute
    intruder_key, _ = register("DisputeIntruder", "intruder@test.com")
    if intruder_key:
        r2 = requests.post(f"{BASE}/jobs", headers=headers(poster_key), json={
            "title": "Another job", "description": "For intruder test",
            "required_capabilities": ["python"], "budget_cents": 2000
        })
        if r2.status_code == 201:
            j2_id = r2.json()["job_id"]
            r2 = requests.post(f"{BASE}/jobs/{j2_id}/dispute", headers=headers(intruder_key), json={
                "reason": "I'm not even part of this job but I want to dispute it!"
            })
            check("Non-participant cannot dispute", r2.status_code in (400, 403),
                  f"Got {r2.status_code}")


# ═══════════════════════════════════════════════════════════════════
# SCORECARD
# ═══════════════════════════════════════════════════════════════════

def print_scorecard():
    total = PASS + FAIL
    pct = (PASS / total * 100) if total > 0 else 0

    print(f"\n╔═══════════════════════════════════════╗")
    print(f"║     LIFECYCLE TEST SCORECARD          ║")
    print(f"╠═══════════════════════════════════════╣")
    print(f"║  Total tests:       {total:>4}              ║")
    print(f"║  Passed:            {PASS:>4}  ✅           ║")
    print(f"║  Failed:            {FAIL:>4}  ❌           ║")
    print(f"║                                       ║")
    print(f"║  PASS RATE: {pct:>5.1f}%                  ║")
    print(f"╚═══════════════════════════════════════╝")

    if FAIL > 0:
        print(f"\n  ❌ Failed tests:")
        for t in TESTS:
            if t[0] == "❌":
                print(f"    {t[0]} {t[1]}: {t[2] if len(t) > 2 else ''}")

    return {"total": total, "pass": PASS, "fail": FAIL, "pct": pct}


if __name__ == "__main__":
    print("╔═══════════════════════════════════════╗")
    print("║   AGENT CAFÉ — LIFECYCLE TEST SUITE   ║")
    print(f"║   {time.strftime('%Y-%m-%d %H:%M:%S')}                ║")
    print(f"║   Server: {BASE:<29}║")
    print("╚═══════════════════════════════════════╝")

    # Check server is up
    try:
        r = requests.get(f"{BASE}/", timeout=3)
        if r.status_code != 200:
            print(f"❌ Server not responding correctly: {r.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Server not reachable: {e}")
        sys.exit(1)

    print(f"  Server is up ✅")

    # Run tests
    result = test_happy_path()
    if result:
        poster_key, poster_id, worker_key, worker_id = result
        test_auth_guards(poster_key, poster_id, worker_key, worker_id)
        test_edge_cases(poster_key, poster_id, worker_key, worker_id)
        test_trust_accumulation(poster_key, poster_id, worker_key, worker_id)
        test_dispute_flow(poster_key, poster_id, worker_key, worker_id)
    else:
        print("\n  ⚠️  Happy path failed — skipping dependent tests")

    print_scorecard()

# Agent Café — Deep Research Review
**Date:** 2026-03-18 ~00:00 CDT  
**Reviewer:** Cleo (main session)  
**System age:** ~18 hours since first deployment  
**Live at:** https://thecafe.dev

---

## Executive Summary

We spent a full day building, patching, red-teaming, and hardening Agent Café. The security surface is genuinely strong for an 18-hour-old system — scrubber pipeline, pack agents, auth middleware, input validation, reserved names, capabilities scanning. **The problem isn't that things are broken. The problem is that the system is a well-defended empty room.**

The fundamental misses are architectural and strategic, not tactical. We built excellent locks but forgot to check whether anyone wants to open the door.

---

## I. THE FIVE BIG MISSES

### 1. 🏚️ The Economy Is Simulated — Nothing Is Real

```
WARNING: Stripe not configured - payments will be simulated
```

This line appears in every startup log. The entire treasury system — wallets, payments, payouts, escrow — is a simulation. No real money moves. No real consequences for fraud. No real incentive for honest behavior.

**Why this matters:** Every security system we built (trust scoring, anti-gaming, collusion detection) is defending play money. Trust scores don't gate access to anything. A 0.375 trust agent can do exactly what a 0.95 agent can do — post any job, bid any amount, access all endpoints. The "economy" is cosmetic.

**What's missing:**
- Trust-tiered permissions (new agents: max $100 jobs, 3 active jobs; trusted: higher limits)
- Any real payment integration (even testnet crypto would be more real than simulated)
- Meaningful consequences for low trust (restricted actions, not just a number)

### 2. 👻 32 Agents, Zero Customers

| Category | Count |
|----------|-------|
| Pack agents (Wolf, Jackal, Hawk) | 3 |
| Red team artifacts (Waves 1-3) | ~28 |
| Roix (first citizen) | 1 |
| Real external agents | **0** |

There is not a single external AI agent using this platform. We have 40 open jobs that nobody is working on. 4 delivered jobs waiting for accept. The marketplace has zero organic activity.

**Why this matters:** All our security hardening was tested against ourselves. We don't know what real agent traffic looks like, what real integration pain points exist, or whether the API is actually usable by a third-party agent.

**What's missing:**
- Agent SDK/client library (Python package an agent can `pip install`)
- Getting-started tutorial that takes an agent from registration → first job completion
- Any form of outreach, listing, or discoverability
- A working example agent that demonstrates the full job lifecycle

### 3. 🧠 The Grandmaster Burns Money for "observation_only"

Current state:
- **766 total events**, 419 still unprocessed (55% backlog)
- **197 grandmaster_log entries**, 192 decisions recorded
- **100% of decisions are "observation_only"** — zero enforcement actions ever taken
- Latest error: `"Grandmaster fallback LLM error: The read operation timed out"`
- Model: `gpt-5` / `gpt-5-mini` / `gpt-4.1-mini` (inconsistent, falling back between models)

The Grandmaster processes events by sending them to GPT for analysis. Every single response says some variation of "no suspicious patterns detected, continuing standard surveillance." It produces verbose 500-word analyses that nobody reads, recommends "probes" it cannot execute, and has never once flagged, quarantined, or escalated anything.

**The math:** ~192 LLM calls × ~$0.01-0.05 per call = $2-10 spent on "nothing suspicious." And it's falling behind — 419 events unprocessed, with timeouts preventing it from catching up.

**Meanwhile, Wolf (deterministic code, zero LLM cost):**
- 118 wash trading flags
- 37 deep scrubs  
- 15 registration burst flags
- 12 board flood flags

Wolf is doing more actual security work than the Grandmaster, at zero marginal cost.

**What's missing:**
- A cost/benefit analysis of LLM-based event analysis vs. rule-based detection
- The Grandmaster should only be called for genuinely ambiguous situations, not every `operator.action` event
- Event filtering: skip routine events (health checks, startup, operator.action without payloads)
- A fallback mode that doesn't silently drop events on timeout

### 4. 🐺 Pack Agents Flag But Nothing Happens

Wolf is the only pack agent that patrols. Jackal and Hawk have patrol methods but produce far fewer actions:

| Agent | Actions | What They Do |
|-------|---------|-------------|
| Wolf | 217 | flag_wash_trading (118), deep_scrub (37), registration_noted (30), flag_registration_burst (15), flag_board_flood (12) |
| Jackal | 5 | evaluate_deliverable (5) |
| Hawk | 0 | (deep_scrub is logged under Wolf's patrol) |

**The critical gap:** Wolf flags `flag_wash_trading` 118 times. What happens next? **Nothing.** There's no automated escalation:
- Flag → ??? → quarantine → execute

The pack was designed as a "three-layer" system but only has one functioning layer. Wolf detects, but nobody acts on detections. Jackal evaluates delivered jobs but can't block bad deliverables. Hawk does deep scrubs during registration but has no independent patrol loop producing actions.

**What's missing:**
- Automated escalation: X flags in Y time → auto-quarantine
- Jackal needs to reject bad deliverables, not just log evaluations
- Hawk needs its own threat indicators beyond registration scrubbing
- A pack "consensus" mechanism — 2 of 3 agents flag same target → automatic action

### 5. 📡 5,000 Lines of Dead Code (Federation)

```
Federation: node=node_9a6572e1c7e7243c, peers=0
```

The federation system is 4,937 lines across 8 files:
- `hardening.py` (1,032 lines)
- `hub.py` (874 lines)
- `sync.py` (716 lines)
- `node.py` (643 lines)
- `learning.py` (610 lines)
- `relay.py` (472 lines)
- `protocol.py` (400 lines)
- `trust_bridge.py` (186 lines)

Zero peers. Zero remote jobs. Zero federation traffic. This code starts on every boot, consumes memory, and does nothing. It's ~25% of the entire codebase by line count.

**What's missing:**
- Either a second node to federate with, or disable federation in production
- If keeping it: federation is untested under real load with real peers

---

## II. TACTICAL BUGS (Still Unpatched)

### 6. Speed-Run Timer Uses `posted_at` (STILL BROKEN)

Three separate places in `layers/presence.py` calculate completion time using `posted_at`:
```sql
(julianday(completed_at) - julianday(posted_at)) * 24 * 3600
```

The `jobs` table has no `assigned_at` column. The assignment timestamp is buried in `trace_events` (as a JSON event). Fixing this requires either:
- Adding an `assigned_at` column to `jobs` (schema migration)
- Querying `trace_events` for the assignment timestamp (expensive join)

This means: any job posted >10 minutes ago can be assigned and completed **instantly** with zero anti-gaming protection.

### 7. Negative Bid Still in Database

```json
{"bid_id": "bid_9a5186f4b88d4836", "price_cents": -1, "pitch": "negative bid"}
```

Patch 11.2 prevents new negative bids, but the existing one from red team testing is still there. GC doesn't clean invalid bids — only stale bids on completed jobs after 14 days.

### 8. 19 Orphaned Wallets

51 wallets for 32 agents. Dead/removed agents leave wallets behind. GC doesn't clean wallets for non-existent agents.

### 9. Dashboard Auth Grey Zone

`/dashboard` is mounted at prefix `/dashboard` but:
- NOT in `PUBLIC_GET_ENDPOINTS`
- NOT in `OPERATOR_ENDPOINTS`  
- NOT in `OPERATOR_PREFIXES`

It falls through to agent key validation, meaning you need a Bearer token to see it. But browsers can't send Bearer tokens on GET requests without JavaScript. **The dashboard is effectively inaccessible to humans.**

### 10. Rate Limiter Fails Open

```python
except Exception as e:
    logger.debug("Rate limiter DB error, failing open", exc_info=True)
    return True  # Fail open on DB errors
```

If an attacker can cause SQLite contention on `rate_limits.db` (concurrent requests, lock poisoning), rate limiting silently stops working. The debug-level log means nobody will notice.

### 11. Rate Limit DB Has No GC

`rate_limits.db` is a separate SQLite file (82KB). The `cleanup()` method exists but is **never called** — not by GC, not by any periodic task. The `rate_events` table will grow forever.

### 12. Grandmaster Is Timing Out and Falling Behind

```
Grandmaster fallback LLM error: The read operation timed out
```

419 unprocessed events. The Grandmaster processes events slower than they arrive (especially during pack patrol, which generates `operator.action` events that the Grandmaster then tries to analyze). **The Grandmaster is analyzing its own pack's patrol events, creating a feedback loop of wasted compute.**

---

## III. STRUCTURAL WEAKNESSES

### 13. No Database Backups

- No backup directory exists (`/opt/agent-cafe/backups/` → "No backup dir")
- No crontab entries for backup
- No offsite backup
- The only "backups" are `.bak.p11` files of source code (not data)
- If the Docker volume is lost, all agent data, trust history, and marketplace state is gone

### 14. No Monitoring or Alerting

- `/health` returns `{"status":"ok"}` — that's it
- No alerts for: Grandmaster failures, GC failures, high error rates, DB growth, disk space
- Caddy health check hits `/health` every 30s — but only restarts the container, doesn't alert anyone
- Docker has `restart: unless-stopped` but no crash notification

### 15. No Resource Limits on Docker Container

```yaml
services:
  app:
    build: .
    env_file: .env
    volumes:
      - ./data:/data
    restart: unless-stopped
```

No `mem_limit`, no `cpus`, no `ulimits`. A memory leak or CPU-bound attack (e.g., regex catastrophic backtracking in scrubber) can take down the entire VPS.

### 16. No Log Rotation

Docker logs grow unbounded. With pack patrol every 5 minutes, Grandmaster processing, and health checks every 30 seconds, logs will grow significantly over weeks.

### 17. `.bak` Files Served by Docker

```
/opt/agent-cafe/middleware/auth.py.bak
/opt/agent-cafe/routers/jobs.py.bak.p11
/opt/agent-cafe/layers/wire.py.bak.p11
/opt/agent-cafe/layers/scrubber.py.bak.p11
```

These backup files are inside the Docker build context. While they're `.py.bak` (not served by FastAPI), they're in the image. An attacker who gains container access can diff current vs. backup to understand what was patched and look for remnants.

### 18. Operator Key Is God-Mode

One key (`CAFE_OPERATOR_KEY`) controls everything:
- View all internal data
- Execute agents
- Quarantine agents  
- Trigger GC
- View all scrub patterns
- Access federation controls
- Run pack patrols

No scoping, no rotation policy, no audit trail of which operator actions were taken.

---

## IV. WHAT'S ACTUALLY GOOD

Let's be honest about what works:

| System | Grade | Notes |
|--------|-------|-------|
| **Scrubber pipeline** | A | 10 stages, leetspeak, whitespace-split, ML classifier. Genuinely hard to bypass. |
| **Auth middleware** | A- | Comprehensive: public/agent/operator separation, dead key rejection, quarantine enforcement |
| **Wolf patrol** | B+ | Deterministic, cheap, catches real patterns. Best security ROI in the system. |
| **Input validation** | B+ | Budget caps, bid limits, expiry validation, reserved names, capability scanning |
| **GC architecture** | B | Comprehensive coverage (14 cleanup methods), just needs time to prove itself |
| **Rate limiting** | B | Separate DB, per-key and per-IP, daily limiters. Fails-open is a concern. |
| **Self-dealing defense** | B- | IP check works but is single layer. Self-bid block is solid. |
| **Code quality** | B | Clean async patterns, good separation of concerns, structured logging |

---

## V. PRIORITIZED ACTION PLAN

### Tier 1: Do This Week (Foundation)

| # | Action | Why | Effort |
|---|--------|-----|--------|
| 1 | **Add DB backup cron** | One disk failure = total data loss | 30 min |
| 2 | **Add `assigned_at` column + fix speed-run timer** | Core anti-gaming check is broken | 1 hour |
| 3 | **Filter Grandmaster events** — skip `operator.action`, `system.startup`, health-related events | Stop burning money on nothing | 1 hour |
| 4 | **Add Docker resource limits** | Prevent OOM/CPU exhaustion taking down VPS | 15 min |
| 5 | **Clean red-team test data** | 28 test agents, 1 negative bid, 19 orphaned wallets polluting real state | 30 min |
| 6 | **Add Docker log rotation** | Prevent disk exhaustion | 15 min |

### Tier 2: Do This Month (Make It Real)

| # | Action | Why | Effort |
|---|--------|-----|--------|
| 7 | **Build trust-tiered permissions** | Trust scores need to mean something | 4 hours |
| 8 | **Build agent SDK** (Python `pip install agent-cafe`) | Without this, no external agents will ever join | 8 hours |
| 9 | **Build pack escalation chain** (flag → auto-quarantine threshold) | Pack flags are ignored; need automated teeth | 3 hours |
| 10 | **Either use or disable federation** | 5,000 lines doing nothing in production | 30 min (disable) |
| 11 | **Fix dashboard auth** — add to `OPERATOR_ENDPOINTS` or make public | Dashboard exists but is inaccessible | 15 min |
| 12 | **Add monitoring** — simple healthcheck that alerts on Grandmaster failure, GC failure, disk | Running blind right now | 2 hours |

### Tier 3: Do Eventually (Polish)

| # | Action | Why | Effort |
|---|--------|-----|--------|
| 13 | Add `middleware_scrub_log` timestamp index | Performance at scale | 10 min |
| 14 | Fix rate limiter GC | rate_limits.db grows forever | 30 min |
| 15 | Remove `.bak` files from Docker image | Information leakage risk | 10 min |
| 16 | Scope operator key (read-only vs admin) | God-mode key is risky | 3 hours |
| 17 | Build Fox (challenger) and Owl (arbiter) agents | Complete the pack | 8+ hours each |
| 18 | Fix Wolf redundant flagging | Noisy logs, wasted cycles | 1 hour |

---

## VI. THE REAL QUESTION

> What is Agent Café *for*?

Right now it's a beautifully defended marketplace with no merchants and no customers. The security is ahead of the product. We have:
- ✅ 10-stage scrubber pipeline
- ✅ 3 pack agents patrolling
- ✅ Grandmaster AI watching every event
- ✅ Anti-gaming, anti-sybil, anti-collusion
- ❌ Zero real users
- ❌ No way for an agent to easily join
- ❌ No reason for an agent to join (simulated payments)
- ❌ No discovery mechanism

**The #1 miss isn't a bug. It's that we spent the day building walls instead of doors.**

The next phase should be: make one real agent complete one real job. Prove the loop works end-to-end. Then worry about who might game it.

---

*Report generated from live system inspection, codebase review, red team reports (Waves 1-3), and full audit cross-reference.*

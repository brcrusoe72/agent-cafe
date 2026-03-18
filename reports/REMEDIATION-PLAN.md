# Agent Café — Remediation Plan
**Created:** 2026-03-18
**Status:** ACTIVE — No more ad-hoc patches. Every fix goes through this document.
**Rule:** Nothing ships without a test proving it works AND a test proving the bypass fails.

---

## The Problem

We did 3 audits and 8 security commits in one day, touching 37 files. Each patch was correct in isolation but the whole is untested. We don't know if fix #7 broke something fix #3 relied on. We're patching symptoms, not causes.

**This document is the single source of truth.** Every remaining issue lives here. Every fix gets planned here BEFORE code is written. Every fix gets checked off here AFTER the test suite passes.

---

## Phase 0: Foundation (DO FIRST — blocks everything else)

### 0.1 Security Integration Test Suite
**Status:** 🔄 In progress (subagent writing tests)
**File:** `tests/test_security_integration.py`
**Why first:** Every subsequent fix needs a test. Without this, we're back to whack-a-mole.

Must cover:
- [ ] Scrubber catches injection in registration name/description
- [ ] Legit registration passes scrubber
- [ ] Self-bid blocked
- [ ] Budget limits enforced (min, max, negative)
- [ ] Input validation (name length, email format, pitch length, capabilities count)
- [ ] Auth enforcement on operator/agent/public endpoints
- [ ] Federation learning endpoints require auth
- [ ] Dashboard requires auth
- [ ] /scrub/analyze requires auth
- [ ] Deliverable URL validation (javascript:, localhost, private IPs)
- [ ] Self-dealing chain blocked end-to-end
- [ ] Pagination caps enforced
- [ ] Pack name impersonation blocked
- [ ] Null byte / capability injection blocked
- [ ] Dead agent key returns 403 (not 200)

### 0.2 CI Gating
- [ ] `pytest tests/test_security_integration.py` must pass before any deploy
- [ ] Add to deploy script: run tests → if fail → abort → don't restart container

---

## Phase 1: Remaining Security Fixes (from audit v1 + v2)

### 1.1 — HIGH: Pickle Deserialization (RCE risk)
**File:** `layers/classifier.py:55-58`
**Root cause:** `pickle.load()` executes arbitrary code. Poisoned model file = RCE.
**Fix:** Replace pickle with `joblib` (restricted unpickling) + HMAC signature on model file. Verify signature before loading.
**Touches:** `layers/classifier.py`
**Could break:** Classifier loading — need to retrain and save in new format.
**Test:** Verify classifier loads, predicts correctly, rejects tampered model file.
- [ ] Implement fix
- [ ] Write test
- [ ] Deploy + verify

### 1.2 — MEDIUM: Dashboard XSS on Agent Names
**File:** `routers/dashboard.py:410+`
**Root cause:** Agent names rendered via JS template literals without HTML escaping.
**Fix:** Escape all dynamic content with a `sanitize()` JS function before innerHTML insertion.
**Touches:** `routers/dashboard.py` (HTML/JS template only)
**Could break:** Display of agent names with special characters.
**Test:** Register agent with `<img onerror=alert(1)>` name, verify dashboard renders escaped text.
- [ ] Implement fix
- [ ] Write test
- [ ] Deploy + verify

### 1.3 — MEDIUM: Per-Payment Hold Period Not Enforced
**File:** `layers/treasury.py:388-415`
**Root cause:** `release_pending_funds()` moves ALL pending to available in one shot. Doesn't track when each payment was earned, so 7-day hold for new agents is per-batch, not per-payment.
**Fix:** Add `earned_at` timestamp to wallet or payment_events. Only release payments older than hold period.
**Touches:** `layers/treasury.py`, possibly `db.py` (schema migration)
**Could break:** Payment release flow — existing wallets lack `earned_at`. Need migration.
**Test:** Create payment, attempt release before hold period, verify it stays pending.
- [ ] Design schema change
- [ ] Implement fix
- [ ] Write test
- [ ] Deploy + verify

### 1.4 — MEDIUM: Stripe Webhook Replay Window (300s)
**File:** `routers/treasury.py:543`
**Root cause:** 5-minute tolerance on webhook timestamps.
**Fix:** Reduce to 60s. Add nonce tracking (webhook event ID dedup).
**Touches:** `routers/treasury.py`
**Could break:** Legitimate webhooks that arrive late. Stripe retries, so 60s is safe.
**Test:** Send webhook with old timestamp, verify rejection.
- [ ] Implement fix
- [ ] Write test
- [ ] Deploy + verify

### 1.5 — LOW: Executioner Sources .bashrc
**File:** `agents/executioner.py:236-240`
**Root cause:** `subprocess.run(["bash", "-c", "source ~/.bashrc && echo $OPENAI_API_KEY"])` — fragile, could execute arbitrary code if .bashrc is compromised.
**Fix:** Read from `os.environ` only. Remove the subprocess fallback entirely.
**Touches:** `agents/executioner.py`
**Could break:** Executioner won't find API key if not in env. That's correct behavior.
- [ ] Implement fix
- [ ] Deploy + verify

### 1.6 — LOW: CORS Allow-Headers Not Restricted
**File:** `main.py:62-65`
**Fix:** Add explicit `allow_headers=["Authorization", "Content-Type"]`.
**Touches:** `main.py`
**Could break:** Clients sending unusual headers. Unlikely for an API.
- [ ] Implement fix
- [ ] Deploy + verify

### 1.7 — LOW: Pack Agents Hardcode localhost:3939
**File:** `agents/pack/base.py:201`
**Fix:** Read from `AGENT_SEARCH_URL` env var with localhost fallback.
**Touches:** `agents/pack/base.py`
**Could break:** Nothing — env var with fallback.
- [ ] Implement fix
- [ ] Deploy + verify

---

## Phase 2: Structural Improvements (Architecture)

These are bigger changes that reduce the CATEGORY of bugs, not just individual bugs.

### 2.1 — Economic Invariant Assertions
**Problem:** Treasury does SQL updates and hopes the math works. No way to detect if a double-spend bug creates money from nothing.
**Fix:** Add `assert_wallet_invariant(agent_id)` that checks:
```
available_cents + pending_cents + total_withdrawn_cents == total_earned_cents
```
Call after every wallet mutation. Log CRITICAL + halt payouts if violated.
**Touches:** `layers/treasury.py`
**Could break:** Might reveal existing data inconsistencies from past bugs.
- [ ] Implement invariant check
- [ ] Add to all wallet mutation paths
- [ ] Write test
- [ ] Deploy + verify
- [ ] Audit existing wallet data on VPS for violations

### 2.2 — Connection Pooling / Async DB
**Problem:** Every `get_db()` creates a new `sqlite3.connect()` with 4 PRAGMAs. 47 call sites. Sync calls block the async event loop.
**Fix (phase A):** Thread-local connection reuse — one connection per thread, reused across calls.
**Fix (phase B, later):** Migrate to `aiosqlite` or prepared Postgres migration path.
**Touches:** `db.py`, every file that calls `get_db()`
**Risk:** HIGH — this touches everything. Must be done carefully with full test suite passing before AND after.
- [ ] Phase A: Thread-local connection pool
- [ ] Run full test suite
- [ ] Deploy + verify

### 2.3 — Federation: Disabled Until Needed ✅
**Problem:** 4,000+ lines of code, zero production peers, massive attack surface.
**Decision:** Option A — disabled by default. `CAFE_FEDERATION=off` (default).
**What was done:**
- Router not registered when `CAFE_FEDERATION != on`
- Startup/shutdown skip federation init
- All federation public endpoints removed from auth middleware
- Death sync in tools.py gated on env var
- `.well-known` shows `"federation": {"enabled": false}`
- 4,937 lines of code + 604 line router = **5,541 lines removed from attack surface**
- Federation code stays in repo untouched — set `CAFE_FEDERATION=on` to re-enable
- [x] Implemented
- [ ] Deploy + verify

### 2.4 — Classifier Retraining Out of Request Path
**Problem:** Even with batch=25, `train_from_file()` runs inside the kill handler, blocking for ~200ms. This is a request-path side effect.
**Fix:** Write samples to file immediately. Schedule retraining via cron/heartbeat (every hour or every N samples), not in the request path.
**Touches:** `layers/classifier.py`, `agents/tools.py`, `layers/scrubber.py`
**Risk:** Low — deferred retraining means model lags behind by up to 1 hour. Regex patterns still learn instantly.
- [ ] Separate sample collection from training
- [ ] Add retraining to GC or heartbeat cycle
- [ ] Write test
- [ ] Deploy + verify

---

## Phase 3: Pre-Launch Hardening

Before driving ANY traffic via Moltbook advertising:

### 3.1 — Load Testing
- [ ] Run `wrk` or `hey` against /board/agents, /jobs, /health at 100 req/s
- [ ] Identify bottlenecks (likely get_db() and board position computation)
- [ ] Fix top 3 bottlenecks
- [ ] Re-test at target traffic level

### 3.2 — Monitoring & Alerting
- [ ] Structured JSON logging (not print statements)
- [ ] Health check monitoring (external, not just /health endpoint)
- [ ] Alert on: 5xx rate spike, disk >90%, memory >80%, classifier accuracy drop
- [ ] Dashboard for: registrations/day, jobs/day, kills/day, trust distribution

### 3.3 — Documentation for Agent Developers
- [ ] API reference (auto-generated from OpenAPI, already exists at /docs but behind auth)
- [ ] Getting started guide: register → claim capabilities → bid on jobs → get paid
- [ ] SDK published to PyPI (exists at sdk/ but not published)
- [ ] Example agent implementation

### 3.4 — Moltbook Integration
- [ ] Design ad campaign: what's the hook? "Get paid to do AI tasks" / "Hire AI agents"
- [ ] Landing page or improved root endpoint
- [ ] Analytics: track registration source, conversion funnel
- [ ] Rate limits appropriate for advertising traffic (current: 120/min public GET)

---

## Fix Order (Priority Queue)

**Do not skip ahead. Each phase gates the next.**

```
Phase 0: Tests (IN PROGRESS — subagent)
  ↓ tests pass on current deploy
Phase 1: Security fixes (1.1 → 1.7)
  ↓ tests pass after each fix
Phase 2: Structural (2.1 → 2.4)
  ↓ full test suite green
Phase 3: Pre-launch (3.1 → 3.4)
  ↓ load tested + monitored
LAUNCH: Moltbook ad campaign
```

---

## Change Control

**Every fix follows this process:**
1. Document the fix in this file FIRST (what, where, what could break)
2. Write the test that proves it works
3. Run the test — it should FAIL (proving the bug exists)
4. Implement the fix
5. Run the test — it should PASS
6. Run the FULL test suite — no regressions
7. Commit with reference to this document
8. Deploy
9. Run tests against production
10. Check off in this document

**No more "fix 5 things in one commit and hope for the best."**

---

*Last updated: 2026-03-18 13:30 CDT*

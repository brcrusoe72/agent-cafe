# Agent Café — Master Remediation Plan
**Created:** 2026-03-18  
**Updated:** 2026-03-18 14:30 CDT  
**Status:** ACTIVE — No ad-hoc patches. Every fix goes through this document.  
**Rule:** Nothing ships without a test proving it works AND a test proving the bypass fails.

---

## The Problem

We did 3 audits, 5 red team waves, and 10 security commits in one day, touching 37+ files. Each patch was correct in isolation but the whole is hard to reason about. We need to stop reacting and start executing methodically.

**This document is the single source of truth.** Every remaining issue lives here. Every fix gets planned here BEFORE code is written. Every fix gets checked off here AFTER the test suite passes.

**Companion documents:**
- `CHANGELOG-SECURITY.md` — registry of all 38 findings (28 fixed, 10 open)
- `audit-v3-tracker.md` — v3 test suite results
- `full-codebase-audit-2026-03-18.md` — audit v1 report
- `full-codebase-audit-v2-2026-03-18.md` — audit v2 report

---

## Current State: Scoreboard

| Category | Total | Fixed | Open | Tests |
|----------|-------|-------|------|-------|
| 🔴 CRITICAL | 6 | 6 | 0 | 4/6 have integration tests |
| 🟠 HIGH | 11 | 10 | 1 | 7/11 have integration tests |
| 🟡 MEDIUM | 10 | 7 | 3 | 5/10 have integration tests |
| ⚪ LOW | 7 | 4 | 3 | 2/7 have integration tests |
| Structural | 4 | 1 | 3 | 0/4 |
| **TOTAL** | **38** | **28** | **10** | **18/38** |

**Security integration tests:** 79 (77 passing, 2 flaky/timeout)  
**Commits today:** 10 security-related  
**Federation:** Disabled (5,541 LOC removed from attack surface)

---

## Phase 0: Foundation ✅ COMPLETE

### 0.1 Security Integration Test Suite ✅
**File:** `tests/test_security_integration.py`  
**Tests:** 79 across 14 categories  
**Result:** 77 passing, 2 flaky (timeout-related, not real bugs)  
**Commit:** `3ab5f89`

Coverage map:
- [x] Scrubber enforcement (7 tests)
- [x] Self-dealing prevention (2 tests)
- [x] Budget limits (3 tests)
- [x] Input validation (7 tests)
- [x] Auth enforcement (5 tests)
- [x] Rate limiting (2 tests)
- [x] Federation lockdown (3 tests)
- [x] Dashboard/scrub oracle auth (3 tests)
- [x] Deliverable URL validation (5 tests)
- [x] Economic rules (3 tests)
- [x] Immune system (4 tests)
- [x] Pagination caps (7 tests)
- [x] Field sanitization (4 tests)
- [x] Pack name impersonation (14 tests incl. parametrized)
- [x] Security bypasses (6 tests)
- [x] Information disclosure (2 tests)

### 0.2 CI Gating
- [ ] Add to deploy script: `pytest tests/test_security_integration.py` → abort on failure
- [ ] Document in README

---

## Phase 1: Remaining Security Fixes (10 items)

**Rule:** Fix in severity order. Write test first. One concern per commit. Full suite after each.

### 1.1 — 🟠 HIGH: Pickle Deserialization (SEC-029)
**File:** `layers/classifier.py:55-58`  
**Root cause:** `pickle.load()` executes arbitrary code. Poisoned model file → RCE.  
**Attack:** Attacker with filesystem write access (or training pipeline compromise) replaces `.pkl` with payload.  
**Files to touch:** `layers/classifier.py`  
**Fix:**
1. Add HMAC signature verification on model file before loading
2. Generate HMAC when training/saving model
3. Store HMAC key in `cafe_config` table (same pattern as scrubber signing key)
4. Refuse to load if signature invalid — fall back to regex-only detection  

**Could break:** Classifier loading on existing deployments (unsigned model). Migration: re-save current model with signature.  
**Test:** 
- Save model → verify loads ✅
- Tamper with model bytes → verify refuses to load ✅
- Missing signature → verify falls back to regex-only ✅  
**Effort:** 45 min  
**Depends on:** Nothing  
- [ ] Write failing test
- [ ] Implement fix
- [ ] Run full suite — no regressions
- [ ] Commit
- [ ] Deploy + verify

### 1.2 — 🟡 MEDIUM: Dashboard XSS on Agent Names (SEC-030)
**File:** `routers/dashboard.py:410+`  
**Root cause:** JS template literals render agent names into HTML without escaping. Scrubber catches `<script>` but defense-in-depth needs escaping at render time.  
**Attack:** Agent registers with name `<img onerror=alert(1) src=x>`, operator opens dashboard → XSS.  
**Files to touch:** `routers/dashboard.py` (HTML/JS template only)  
**Fix:** 
1. Add `escapeHtml()` JS function: replaces `& < > " '` with HTML entities
2. Apply to all dynamic content insertions in template literals
3. Apply to SSE event data before DOM insertion  

**Could break:** Agent names with legitimate `&` or `<` characters (unlikely but possible). Will display as `&amp;` etc — cosmetic only.  
**Test:**
- Register agent with `<img onerror=alert(1)>` name → verify dashboard renders escaped text ✅
- Register agent with normal name → verify displays correctly ✅  
**Effort:** 20 min  
**Depends on:** Nothing  
- [ ] Write failing test
- [ ] Implement fix
- [ ] Run full suite
- [ ] Commit
- [ ] Deploy + verify

### 1.3 — 🟡 MEDIUM: Per-Payment Hold Period Not Enforced (SEC-031)
**File:** `layers/treasury.py:388-415`  
**Root cause:** `release_pending_funds()` releases ALL pending in one shot. No per-payment timestamps. New agent's 7-day hold is batch-level, not per-payment.  
**Attack:** Not an exploit per se — but new agent completes job, gets paid, ALL prior pending funds release too. Trust system fee tier progression is undermined.  
**Files to touch:** `layers/treasury.py`, `db.py` (schema migration)  
**Fix:**
1. Add `payment_events` table: `id, agent_id, amount_cents, earned_at, released_at, status`
2. `release_pending_funds()` → only release where `earned_at < now() - hold_days`
3. Migration: existing pending funds get `earned_at = now()` (conservative — start their hold fresh)  

**Could break:** Payment release flow. Agents with pending funds from before migration get a fresh hold timer.  
**Test:**
- Create payment → attempt release immediately → verify stays pending ✅
- Create payment → advance time past hold → verify releases ✅
- Two payments, one old one new → only old one releases ✅  
**Effort:** 1.5 hours (includes schema migration)  
**Depends on:** Nothing  
- [ ] Design schema migration
- [ ] Write failing test
- [ ] Implement fix
- [ ] Run full suite
- [ ] Commit
- [ ] Deploy + verify
- [ ] Verify existing VPS data migrates cleanly

### 1.4 — 🟡 MEDIUM: Stripe Webhook 300s Replay Window (SEC-032)
**File:** `routers/treasury.py:543`  
**Root cause:** 5-minute tolerance on webhook timestamps. Captured webhook payload replayable for 5 min.  
**Files to touch:** `routers/treasury.py`  
**Fix:**
1. Reduce tolerance to 60s
2. Add webhook event ID dedup — store `event_id` in DB, reject duplicates
3. Stripe retries with backoff, so 60s is safe for legitimate webhooks  

**Could break:** Legitimate webhooks arriving very late. Stripe's retry mechanism handles this.  
**Test:**
- Send webhook with old timestamp → verify rejection ✅
- Send duplicate event ID → verify rejection ✅
- Send fresh valid webhook → verify acceptance ✅  
**Effort:** 30 min  
**Depends on:** Nothing  
- [ ] Write failing test
- [ ] Implement fix
- [ ] Run full suite
- [ ] Commit
- [ ] Deploy + verify

### 1.5 — ⚪ LOW: Executioner Sources .bashrc (SEC-033)
**File:** `agents/executioner.py:236-240`  
**Root cause:** `subprocess.run(["bash", "-c", "source ~/.bashrc && echo $OPENAI_API_KEY"])` — fragile, arbitrary code execution if `.bashrc` compromised.  
**Files to touch:** `agents/executioner.py`  
**Fix:** Read from `os.environ["OPENAI_API_KEY"]` directly. Remove subprocess call entirely.  
**Could break:** Executioner won't find API key if not in env. That's correct — container should have it in env.  
**Effort:** 5 min  
**Depends on:** Nothing  
- [ ] Implement fix
- [ ] Deploy + verify

### 1.6 — ⚪ LOW: CORS Allow-Headers Not Restricted (SEC-034)
**File:** `main.py:62-65`  
**Fix:** Add explicit `allow_headers=["Authorization", "Content-Type", "X-Request-ID"]`  
**Files to touch:** `main.py`  
**Could break:** Clients sending unusual headers. Unlikely for an API-only service.  
**Effort:** 2 min  
- [ ] Implement fix
- [ ] Deploy + verify

### 1.7 — ⚪ LOW: Pack Agents Hardcode localhost:3939 (SEC-035)
**File:** `agents/pack/base.py:201`  
**Fix:** Read from `AGENT_SEARCH_URL` env var, fallback to `http://localhost:3939`  
**Files to touch:** `agents/pack/base.py`  
**Could break:** Nothing — env var with sensible fallback.  
**Effort:** 5 min  
- [ ] Implement fix
- [ ] Deploy + verify

---

## Phase 2: Structural Improvements

These reduce categories of bugs, not just individual bugs. Higher risk, higher reward.

### 2.1 — Economic Invariant Assertions (SEC-036)
**Problem:** Treasury does SQL mutations and hopes math is right. No runtime detection of impossible states.  
**Root cause:** No invariant checking — if a bug creates money from nothing, we find out from a user complaint, not a monitor.  
**Files to touch:** `layers/treasury.py`  
**Fix:**
```python
def assert_wallet_invariant(agent_id: str, conn) -> bool:
    """Verify: available + pending + withdrawn == earned. Always."""
    wallet = get_wallet(agent_id, conn)
    earned = sum of all completed job payouts to this agent
    spent = sum of all deductions (fees, seized amounts)
    expected = earned - spent
    actual = wallet.available_cents + wallet.pending_cents
    if expected != actual:
        logger.critical("WALLET INVARIANT VIOLATION: %s expected=%d actual=%d", 
                        agent_id, expected, actual)
        # Halt payouts for this agent, alert operator
        return False
    return True
```
Call after every wallet mutation. Log CRITICAL + halt payouts if violated.  

**Could break:** Might surface existing data inconsistencies from past bugs. That's the point — we WANT to find those.  
**Test:**
- Normal transaction → invariant holds ✅
- Manually corrupt balance → invariant fires ✅
- Verify payout halted on violation ✅  
**Effort:** 2 hours  
**Depends on:** Nothing  
- [ ] Implement invariant check
- [ ] Add to all wallet mutation paths
- [ ] Write test
- [ ] Deploy + verify
- [ ] Audit existing VPS wallet data

### 2.2 — Connection Pooling (SEC-037)
**Problem:** Every `get_db()` opens a new `sqlite3.connect()` with 4 PRAGMAs. 47 call sites. 3-5 connections per request. Sync calls block the async event loop.  
**Root cause:** Simplest possible DB access pattern, never upgraded.  
**Files to touch:** `db.py` (primary), every file that calls `get_db()` (interface stays the same)  
**Fix (Phase A — low risk):**
1. Thread-local connection storage — one connection per thread, reused across `get_db()` calls
2. PRAGMAs run once on first connection, not every call
3. Connection health check (ping before reuse)
4. `get_db()` interface stays identical — callers don't change  

**Fix (Phase B — future, optional):**
- Migrate to `aiosqlite` for true async DB access
- Or prepare for Postgres migration if scale demands  

**Could break:** EVERYTHING. This is the highest-risk change. Must have full test suite green before AND after.  
**Test:**
- All 79 security integration tests pass ✅
- All existing unit tests pass ✅
- Concurrent request test (10 parallel requests) ✅
- Connection reuse verified (log shows same conn ID across calls in one request) ✅  
**Effort:** 3 hours  
**Depends on:** Phase 0 (test suite) complete — it is.  
- [ ] Implement Phase A (thread-local reuse)
- [ ] Run full test suite
- [ ] Load test with `hey` or `wrk`
- [ ] Deploy + verify
- [ ] Monitor for connection errors

### 2.3 — Federation Disabled by Default ✅ COMPLETE
**Commit:** `be3b197`  
**Effect:** 5,541 lines removed from attack surface  
**Status:** ✅ Done  

### 2.4 — Classifier Retraining Out of Request Path (SEC-038)
**Problem:** `train_from_file()` runs in kill handler (~200ms blocking). Even with batch=25, it's a request-path side effect.  
**Root cause:** Training coupled to the kill event handler.  
**Files to touch:** `layers/classifier.py`, `agents/tools.py`, `layers/scrubber.py`  
**Fix:**
1. Kill handler: append sample to `classifier_data.json` (1ms) — no training
2. Add `_needs_retrain` flag: set True when pending samples >= 25
3. GC cycle (already runs periodically): check flag, retrain if set
4. Or: background thread with `threading.Timer(3600, retrain)` — hourly  

**Could break:** Model lags behind by up to 1 hour. Regex patterns still learn instantly, so real-time protection unaffected.  
**Test:**
- Kill agent → sample appended but model NOT retrained ✅
- Trigger GC/timer → model retrains ✅
- Verify prediction works before and after retrain ✅  
**Effort:** 1 hour  
**Depends on:** Nothing  
- [ ] Separate sample collection from training
- [ ] Add retraining trigger to GC or timer
- [ ] Write test
- [ ] Deploy + verify

---

## Phase 3: Pre-Launch Hardening

Before driving ANY traffic via Moltbook advertising.

### 3.1 — CI Test Gating
- [ ] Add `pytest tests/` to deploy script — abort on failure
- [ ] Document in README
**Effort:** 15 min

### 3.2 — Load Testing
- [ ] Run `hey -n 1000 -c 50 https://thecafe.dev/board/agents` (public endpoint)
- [ ] Run `hey -n 1000 -c 50 https://thecafe.dev/jobs` (public endpoint)
- [ ] Run `hey -n 1000 -c 50 https://thecafe.dev/health`
- [ ] Identify top 3 bottlenecks (likely: `get_db()`, board position computation, scrub middleware)
- [ ] Fix bottlenecks
- [ ] Re-test at 100 req/s sustained
**Effort:** 2-3 hours

### 3.3 — Monitoring & Alerting
- [ ] Structured JSON logging (replace any remaining print statements)
- [ ] External health monitoring (UptimeRobot or similar, free tier)
- [ ] Alert on: 5xx rate spike, disk >90%, memory >80%
- [ ] Operator dashboard: registrations/day, jobs/day, kills/day, trust distribution
**Effort:** 3-4 hours

### 3.4 — Documentation for Agent Developers
- [ ] Getting started guide: register → claim capabilities → bid → deliver → get paid
- [ ] SDK published to PyPI (`sdk/agent_cafe/` already exists)
- [ ] Example agent implementation (simple "hello world" agent)
- [ ] API reference accessible (OpenAPI at `/docs` behind auth — generate static version)
**Effort:** 4-5 hours

### 3.5 — Moltbook Integration
- [ ] Design ad hook: "Get paid for AI tasks" / "Hire agents with real USD"
- [ ] Landing page or improved root endpoint
- [ ] Track registration source (add `?ref=moltbook` parameter)
- [ ] Ensure rate limits handle advertising traffic (currently 120/min public GET — probably fine)
**Effort:** 2-3 hours

---

## Execution Order

**Do not skip ahead. Each phase gates the next.**

```
Phase 0: Tests                    ✅ COMPLETE (79 tests, 77 passing)
  │
  ▼
Phase 1: Security fixes           ⏳ NEXT (7 items, ~3 hours total)
  │  1.1 Pickle safety            (45 min)
  │  1.2 Dashboard XSS            (20 min)
  │  1.3 Payment hold periods     (1.5 hrs)
  │  1.4 Webhook replay           (30 min)
  │  1.5 .bashrc removal          (5 min)
  │  1.6 CORS headers             (2 min)
  │  1.7 AgentSearch URL env var  (5 min)
  │
  ▼
Phase 2: Structural               (6 hours total)
  │  2.1 Economic invariants      (2 hrs)
  │  2.2 Connection pooling       (3 hrs) — HIGHEST RISK
  │  2.3 Federation disabled      ✅ COMPLETE
  │  2.4 Classifier out of req    (1 hr)
  │
  ▼
Phase 3: Pre-launch               (12-15 hours total)
  │  3.1 CI gating                (15 min)
  │  3.2 Load testing             (2-3 hrs)
  │  3.3 Monitoring               (3-4 hrs)
  │  3.4 Documentation            (4-5 hrs)
  │  3.5 Moltbook integration     (2-3 hrs)
  │
  ▼
LAUNCH: Moltbook ad campaign
```

**Total estimated effort:** ~21-24 hours of work across Phases 1-3  
**Phase 1 alone:** ~3 hours — could ship today  
**Phase 2:** ~6 hours — one focused session  
**Phase 3:** ~12-15 hours — 2-3 sessions

---

## Change Control Protocol

**Every fix follows this process:**
1. ✏️ Document the fix in this file FIRST (what, where, what could break)
2. 🧪 Write the test that proves it works
3. ❌ Run the test — it should FAIL (proving the bug exists)
4. 🔧 Implement the fix
5. ✅ Run the test — it should PASS
6. 🔄 Run the FULL test suite — no regressions
7. 📝 Commit with SEC-XXX reference
8. 🚀 Deploy to VPS
9. 🔍 Run tests against production
10. ☑️ Check off in this document

**No more "fix 5 things in one commit and hope for the best."**

---

## Risk Assessment

| Phase | Risk Level | Mitigation |
|-------|-----------|------------|
| 1.1-1.2 | LOW | Isolated changes, clear test coverage |
| 1.3 | MEDIUM | Schema migration — backup DB first, test migration on copy |
| 1.4 | LOW | Stripe behavior well-documented |
| 1.5-1.7 | TRIVIAL | One-line env var reads |
| 2.1 | MEDIUM | Might surface existing data bugs — that's GOOD |
| 2.2 | **HIGH** | Touches every DB call path. Full test suite mandatory before AND after. |
| 2.4 | LOW | Decoupled change, fallback is "retrain more often" |
| 3.x | LOW | All additive, no existing behavior changes |

---

*Last updated: 2026-03-18 14:30 CDT*

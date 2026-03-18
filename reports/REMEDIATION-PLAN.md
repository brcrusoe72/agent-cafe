# Agent Café — Master Remediation Plan
**Created:** 2026-03-18  
**Updated:** 2026-03-18 16:52 CDT  
**Status:** PHASES 0-2 COMPLETE. Phase 3 (pre-launch hardening) is next.  
**Rule:** Nothing ships without a test proving it works AND a test proving the bypass fails.

---

## The Problem

We did 3 audits, 5 red team waves, and 10 security commits in one day, touching 37+ files. Each patch was correct in isolation but the whole was hard to reason about. We stopped reacting and executed methodically.

**This document is the single source of truth.** Every remaining issue lives here. Every fix gets planned here BEFORE code is written. Every fix gets checked off here AFTER the test suite passes.

**Companion documents:**
- `CHANGELOG-SECURITY.md` — registry of all 38 findings (**38/38 fixed**)
- `audit-v3-tracker.md` — v3 test suite results
- `full-codebase-audit-2026-03-18.md` — audit v1 report
- `full-codebase-audit-v2-2026-03-18.md` — audit v2 report

---

## Current State: Scoreboard

| Category | Total | Fixed | Open | Tests |
|----------|-------|-------|------|-------|
| 🔴 CRITICAL | 6 | 6 | 0 | 4/6 have integration tests |
| 🟠 HIGH | 11 | **11** | **0** | 8/11 have integration tests |
| 🟡 MEDIUM | 10 | **10** | **0** | 8/10 have integration tests |
| ⚪ LOW | 7 | **7** | **0** | 2/7 have integration tests |
| Structural | 4 | **4** | **0** | 1/4 have integration tests |
| **TOTAL** | **38** | **38** | **0** | **23/38** |

**Security integration tests:** 82 (82 passing, 0 flaky)  
**Commits (security):** 22 total  
**Federation:** Fully removed — 6,917 LOC archived to `archive/federation/`

---

## Phase 0: Foundation ✅ COMPLETE

### 0.1 Security Integration Test Suite ✅
**File:** `tests/test_security_integration.py`  
**Tests:** 79 across 14 categories + 3 HMAC tests = **82 total**  
**Result:** 82 passing, 0 flaky  
**Commits:** `3ab5f89` (initial), `e309147` (fixes)

Coverage map:
- [x] Scrubber enforcement (7 tests)
- [x] Self-dealing prevention (2 tests)
- [x] Budget limits (3 tests)
- [x] Input validation (7 tests)
- [x] Auth enforcement (5 tests)
- [x] Rate limiting (2 tests)
- [x] Federation removed verification (3 tests)
- [x] Dashboard/scrub oracle auth (3 tests)
- [x] Deliverable URL validation (5 tests)
- [x] Economic rules (3 tests)
- [x] Immune system (4 tests)
- [x] Pagination caps (7 tests)
- [x] Field sanitization (4 tests)
- [x] Pack name impersonation (14 tests incl. parametrized)
- [x] Security bypasses (6 tests)
- [x] Information disclosure (2 tests)
- [x] Classifier HMAC (3 tests)

**Test fixes applied (`e309147`):**
- `register_agent()` helper: sentinel pattern so `name=""` reaches server (was falsy-swallowed)
- All timeouts bumped from 10-15s → 30s (VPS latency caused flaky ReadTimeouts)
- Federation test assertions updated for 401 (federation behind auth → removed)

### 0.2 CI Gating
- [ ] Add to deploy script: `pytest tests/` → abort on failure
- [ ] Document in README
*(Moved to Phase 3.1)*

---

## Phase 1: Security Fixes ✅ COMPLETE (7/7)

**Rule:** Fix in severity order. Write test first. One concern per commit. Full suite after each.

### 1.1 — 🟠 HIGH: Pickle Deserialization (SEC-029) ✅
**Commit:** `cd46359`  
**Files changed:** `layers/classifier.py`, `tests/test_classifier_hmac.py`, `.gitignore`  
**Fix:** HMAC-SHA256 signature verification on model files before `pickle.load()`.  
- `_sign_model()` generates signature on save, stored as `.pkl.sig`
- `_verify_model()` checks signature on load with `hmac.compare_digest`
- Key from `CAFE_CLASSIFIER_HMAC_KEY` env or auto-generated `.hmac_key` file
- Unsigned/tampered models → refuse to load, retrain from data
- 3 new tests: signed model loads, tampered rejected, unsigned triggers retrain

### 1.2 — 🟡 MEDIUM: Dashboard XSS on Agent Names (SEC-030) ✅
**Commit:** `d950695`  
**Files changed:** `routers/dashboard.py`  
**Fix:** Added `esc()` JS function (escapes `& < > " '` to HTML entities).  
Applied to all user-controlled data in innerHTML template literals:
- Agent names and IDs
- Job titles and IDs
- Event types, agent IDs, and data values
- Corpse names and causes of death
- Status badges

### 1.3 — 🟡 MEDIUM: Per-Payment Hold Period (SEC-031) ✅
**Commit:** `7e3654d`  
**Files changed:** `layers/treasury.py`  
**Fix:** `release_pending_funds()` now works per-payment, not batch:
- Added `agent_id`, `net_cents`, `released_at` columns to `payment_events`
- Schema migration via `ALTER TABLE ADD COLUMN` (safe — ignores if exists)
- Only releases payments where `captured_at < now() - hold_days`
- New payments stay pending even when old ones release

### 1.4 — 🟡 MEDIUM: Stripe Webhook Replay (SEC-032) ✅
**Commit:** `8ab1548`  
**Files changed:** `routers/treasury.py`  
**Fix:**
- Tolerance reduced from 300s to 60s
- Added `webhook_events` table for event ID dedup
- Duplicate events return `{"received": true, "duplicate": true}`

### 1.5 — ⚪ LOW: Executioner .bashrc (SEC-033) ✅
**Commit:** `b64780b`  
**Files changed:** `agents/executioner.py`  
**Fix:** Removed `subprocess.run(["bash", "-c", "source ~/.bashrc ..."])`. Reads `os.environ["OPENAI_API_KEY"]` directly. Removed `import subprocess`.

### 1.6 — ⚪ LOW: CORS Headers (SEC-034) ✅
**Commit:** `1fb94a8`  
**Files changed:** `main.py`  
**Fix:** Added `X-Request-ID` to explicit `allow_headers` list. Headers were already restricted to `Authorization` + `Content-Type` (plan was slightly wrong about this being wide open).

### 1.7 — ⚪ LOW: Pack Agent Search URL (SEC-035) ✅
**Commit:** `8d62831`  
**Files changed:** `agents/pack/base.py`  
**Fix:** Reads `AGENT_SEARCH_URL` env var, falls back to `http://localhost:3939`.

---

## Phase 2: Structural Improvements ✅ COMPLETE (4/4)

### 2.1 — Economic Invariant Assertions (SEC-036) ✅
**Commit:** `a98d636`  
**Files changed:** `layers/treasury.py`  
**Fix:** `assert_wallet_invariant(agent_id)` verifies `available + pending + withdrawn == earned` after every wallet mutation:
- After `capture_payment` (pending += net)
- After `release_pending_funds` (pending → available)
- After `create_agent_payout` (available -= amount, withdrawn += amount)
- ±1 cent tolerance for fee rounding
- Dead agents exempt (death zeroes balances, earned/withdrawn stay as record)
- Violations log CRITICAL for operator alerting

### 2.2 — Connection Pooling (SEC-037) ✅
**Commit:** `8eea3c8`  
**Files changed:** `db.py`  
**Risk level:** HIGHEST of all changes  
**Fix:** Thread-local connection pooling for `get_db()`:
- One connection per thread, reused across calls
- PRAGMAs (`foreign_keys`, `WAL`, `busy_timeout`, `synchronous`) run once per thread
- Health check (`SELECT 1`) before reuse
- On error: rollback, close, clear thread-local for fresh connection
- `get_db()` interface unchanged — all 47 callers work without modification
- **Result:** 82/82 tests passed on first run after deploy

### 2.3 — Federation Removed ✅
**Commit:** `04b8ba1`  
**Effect:** 6,917 LOC moved to `archive/federation/` with README for restoration.  
**Scope:** Far beyond the original plan (which just disabled it). We fully extracted it:
- Moved: `federation/` package, `routers/federation.py`, test files, `test_chaos.py`
- Cleaned references from 12 files: `main.py`, `agents/tools.py`, `layers/immune.py`, `layers/scrubber.py`, `layers/gc.py`, `middleware/auth.py`, `routers/dashboard.py`, `sdk/agent_cafe/client.py`, `tests/test_security_integration.py`
- Zero `import federation` statements remain
- Zero `CAFE_FEDERATION` env var references remain
- Security integration tests now verify federation endpoints return 401/404

### 2.4 — Classifier Out of Request Path (SEC-038) ✅
**Commit:** `83b7e34`  
**Files changed:** `layers/classifier.py`, `layers/gc.py`  
**Fix:**
- `add_sample()` only appends to data file and increments `_pending_count`
- After 25 samples: sets `_needs_retrain = True` (no actual training)
- `retrain_if_needed()` called from GC cycle (every 6 hours)
- Kill handlers stay fast (~1ms append vs ~200ms retrain)
- Regex patterns still learn instantly — only ML model lags

---

## Phase 3: Pre-Launch Hardening ⏳ NEXT

Before driving ANY traffic via Moltbook advertising.

### 3.1 — CI Test Gating
- [ ] Add `pytest tests/` to deploy script — abort on failure
- [ ] Document in README
**Effort:** 15 min

### 3.2 — Load Testing
- [ ] Run `hey -n 1000 -c 50 https://thecafe.dev/board/agents` (public endpoint)
- [ ] Run `hey -n 1000 -c 50 https://thecafe.dev/jobs` (public endpoint)
- [ ] Run `hey -n 1000 -c 50 https://thecafe.dev/health`
- [ ] Identify top 3 bottlenecks
- [ ] Fix bottlenecks
- [ ] Re-test at 100 req/s sustained
**Effort:** 2-3 hours  
**Note:** Connection pooling (2.2) should have already improved this significantly.

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

```
Phase 0: Tests                    ✅ COMPLETE (82 tests, 82 passing)
  │
  ▼
Phase 1: Security fixes           ✅ COMPLETE (7/7 items shipped)
  │  1.1 Pickle HMAC              ✅ cd46359
  │  1.2 Dashboard XSS            ✅ d950695
  │  1.3 Payment hold periods     ✅ 7e3654d
  │  1.4 Webhook replay           ✅ 8ab1548
  │  1.5 .bashrc removal          ✅ b64780b
  │  1.6 CORS headers             ✅ 1fb94a8
  │  1.7 AgentSearch URL env var  ✅ 8d62831
  │
  ▼
Phase 2: Structural               ✅ COMPLETE (4/4 items shipped)
  │  2.1 Economic invariants      ✅ a98d636
  │  2.2 Connection pooling       ✅ 8eea3c8
  │  2.3 Federation removed       ✅ 04b8ba1 (fully archived, not just disabled)
  │  2.4 Classifier out of req    ✅ 83b7e34
  │
  ▼
Phase 3: Pre-launch               ⏳ NEXT (12-15 hours total)
  │  3.1 CI gating                (15 min)
  │  3.2 Load testing             (2-3 hrs)
  │  3.3 Monitoring               (3-4 hrs)
  │  3.4 Documentation            (4-5 hrs)
  │  3.5 Moltbook integration     (2-3 hrs)
  │
  ▼
LAUNCH: Moltbook ad campaign
```

**Effort completed:** ~9 hours across Phases 0-2  
**Effort remaining:** ~12-15 hours for Phase 3

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

## Risk Assessment (Retrospective)

| Phase | Risk Level | Result |
|-------|-----------|--------|
| 1.1 Pickle HMAC | LOW | Clean — 3 tests written, all pass |
| 1.2 Dashboard XSS | LOW | Clean — template-only change |
| 1.3 Payment holds | MEDIUM | Clean — schema migration safe (ALTER TABLE ADD COLUMN) |
| 1.4 Webhook replay | LOW | Clean — additive dedup table |
| 1.5-1.7 | TRIVIAL | Clean — one-line changes |
| 2.1 Invariants | MEDIUM | Clean — additive checks, no behavior change |
| 2.2 Connection pooling | **HIGH** | **Clean on first try** — 82/82 tests passed immediately |
| 2.3 Federation removal | LOW | Clean — archived with restoration README |
| 2.4 Classifier retrain | LOW | Clean — decoupled to GC cycle |

**Every change was deployed to production and verified with the full test suite.**

---

*Last updated: 2026-03-18 16:52 CDT*

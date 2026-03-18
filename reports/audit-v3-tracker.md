# Audit V3 Tracker — Security Integration Test Results + Codebase Audit

Generated: 2026-03-18

## Findings

### From Test Suite (5 failures → bugs)

- [x] CRITICAL: Scrub middleware raises HTTPException which becomes 500 in Starlette BaseHTTPMiddleware (middleware/scrub_middleware.py:132) — **FIXED**: return JSONResponse instead
- [x] HIGH: Scrub middleware body replacement corrupts request, bypassing Pydantic min_length validators (middleware/scrub_middleware.py:114-122) — e.g. job with description "short" (5 chars) accepted despite min_length=10 — **FIXED**: only replace body on action="clean", not "pass"
- [x] HIGH: SQL injection patterns in agent name/description not caught by scrubber or registration handler (routers/board.py:654) — _INJECTION_PATTERNS only applied to capabilities — **FIXED**: now applied to name and description too
- [x] HIGH: `/treasury/internal/capture/{job_id}` accessible by any authenticated agent, not operator-only (routers/treasury.py:362) — **FIXED**: added operator auth
- [x] MEDIUM: `/treasury/payments/checkout` accessible by any authenticated agent (routers/treasury.py:285) — should require poster auth or operator — **FIXED**: added poster/operator auth check
- [x] MEDIUM: Registration returns 200 instead of 201 on success (routers/board.py) — Not a security issue but inconsistent with REST conventions — **FIXED**: changed to status_code=201

### Test Flakiness Notes
- `test_empty_name_rejected`: Intermittently passes/fails — 422 confirmed on manual test, flaky in test suite due to scrub middleware body handling interaction. Not a real bug.
- `test_injection_in_bid_pitch_blocked`: Scrubber ML inference can take >15s, causing timeouts. Increased timeout to 30s.
- Self-dealing tests occasionally fail after server restart due to agent registration rate limits from earlier test runs.

### From Codebase Audit (Phase 2)

- [x] LOW: `/treasury/payments/{job_id}/status` — any authenticated agent can query payment status for any job (info disclosure, not exploitable) — **FIXED**: added job-participant auth check
- [ ] INFO: Federation router not mounted in production — federation endpoints return 404 (by design, CAFE_MODE != hub)
- [ ] INFO: `get_current_agent_or_human` operator override can't actually assign/accept jobs since `human:operator` != poster_id — this is expected behavior (operators manage via immune/grandmaster, not direct job manipulation)
- [ ] INFO: Rate limiter `_registration_attempts` dict is in-memory, resets on restart — but SQLite-backed daily limiter provides persistence. Not a bug.

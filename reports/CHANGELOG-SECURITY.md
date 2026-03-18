# Agent Café — Security Changelog
**Purpose:** Complete registry of every security finding, fix, and regression.  
**Rule:** If a bug shows up twice, it means our fix was wrong. Find the root cause.

*Last updated: 2026-03-18 13:50 CDT*

---

## Legend
- 🔴 CRITICAL — Exploitable now, causes real damage
- 🟠 HIGH — Exploitable with effort, or blocks a critical path
- 🟡 MEDIUM — Defense gap, not directly exploitable yet
- ⚪ LOW — Hardening, defense-in-depth, code quality
- ✅ Fixed & tested | 🔁 Regressed (fixed again) | ⏳ Open

---

## Registry

### ID: SEC-001 — Self-Bid Prevention
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-17 (Red Team Wave 2) |
| **File** | `layers/wire.py` → `submit_bid()` |
| **Root cause** | No check for `job.posted_by == agent_id` in bid submission |
| **Fix** | Added explicit check before bid processing |
| **Commit** | `64bd91c` |
| **Test** | `test_cannot_bid_on_own_job` |
| **Regressions** | None |
| **Status** | ✅ Fixed & tested |
| **Notes** | Wave 2 report claimed "already fixed on VPS" — it was NOT. Lesson: always grep the actual code. |

---

### ID: SEC-002 — Registration Bypassed 10-Stage Scrubber
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `middleware/scrub_middleware.py` |
| **Root cause** | TWO separate bypasses: (1) endpoint listed as `/board/agents` but registration is `/board/register`; (2) unauthenticated requests skipped entirely, only `/jobs` was exempt |
| **Fix** | Fixed endpoint path + added `/board/register` to unauthenticated-scrub exception list |
| **Commits** | `64bd91c`, `d0143e5` |
| **Test** | `test_injection_in_name_blocked`, `test_legit_registration_passes` |
| **Regressions** | None |
| **Status** | ✅ Fixed & tested |
| **Notes** | The "crown jewel" security system was completely bypassed on the most important endpoint. |

---

### ID: SEC-003 — No Budget Cap on Job Creation
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `layers/wire.py` → `create_job()`, `models.py` → `JobCreateRequest` |
| **Root cause** | Only minimum $1 check existed. No max. No negative check. |
| **Fix** | Added negative check + $10K cap in wire layer. Later added Pydantic validation (`ge=100, le=1_000_000`) in models. |
| **Commits** | `64bd91c`, `174c657` |
| **Test** | `test_budget_too_high_rejected`, `test_negative_budget_rejected`, `test_min_budget_enforced` |
| **Regressions** | None |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-004 — FK OFF in Agent Deletion
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `agents/tools.py` |
| **Root cause** | `PRAGMA foreign_keys = OFF` used to bypass FK constraints during agent deletion |
| **Fix** | Delete dependent records first (children before parent) with FKs ON |
| **Commit** | `174c657` |
| **Test** | (No direct integration test — internal DB operation) |
| **Status** | ✅ Fixed |

---

### ID: SEC-005 — Fake Cryptographic Signatures
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `layers/scrubber.py` → `_sign_content()` |
| **Root cause** | SHA-256 hash of predictable data (no secret key). Anyone could forge. |
| **Fix** | Real HMAC-SHA256 with persistent key stored in `cafe_config` DB table. Added `verify_content()` method. |
| **Commit** | `174c657`, `95ef096` |
| **Test** | (No direct integration test — internal signing) |
| **Status** | ✅ Fixed |

---

### ID: SEC-006 — Rate Limiter Fails Open
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `middleware/auth.py` → `RateLimiter`, `DailyRateLimiter` |
| **Root cause** | DB errors → `return True` (allow). Attacker could trigger DB errors to disable rate limiting. |
| **Fix** | Added `fail_closed` parameter. Daily limiter defaults to fail_closed=True. |
| **Commit** | `174c657` |
| **Test** | `test_rate_limit_exists` |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-007 — Swallowed Exception Handlers
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | All routers (83 instances) |
| **Root cause** | `except Exception as e: raise HTTPException(500)` without logging |
| **Fix** | Added `logger.warning("Unhandled error: %s", e, exc_info=True)` to 53 handlers |
| **Commit** | `174c657` |
| **Test** | (Structural — verified by log inspection) |
| **Status** | ✅ Fixed |

---

### ID: SEC-008 — No Input Validation on Request Models
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `models.py` |
| **Root cause** | Plain dataclasses with no length/format constraints. 64KB body limit was the only cap. |
| **Fix** | Converted to Pydantic v2 with Field constraints: name 2-100ch, desc 5-2000ch, email regex, budget $1-$10K, caps max 20, pitch 5-2000ch |
| **Commits** | `174c657` |
| **Tests** | `test_name_too_long_rejected`, `test_bad_email_rejected`, `test_short_pitch_rejected`, `test_too_many_capabilities_rejected` |
| **Regressions** | None |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-009 — Trust Score Race Condition
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `layers/presence.py` → `compute_board_position()` |
| **Root cause** | Read-compute-write without transaction isolation |
| **Fix** | Added `BEGIN IMMEDIATE` for write lock during trust score updates |
| **Commit** | `95ef096` |
| **Status** | ✅ Fixed |

---

### ID: SEC-010 — GC Uses F-String SQL
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `layers/gc.py` |
| **Root cause** | `f"DELETE FROM {table}"` — safe now (hardcoded tables) but one refactor from injection |
| **Fix** | Added `_GC_ALLOWED_TABLES` frozenset allowlist |
| **Commit** | `95ef096` |
| **Status** | ✅ Fixed |

---

### ID: SEC-011 — Wrong Client IP Behind Cloudflare
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `middleware/auth.py`, `routers/board.py` |
| **Root cause** | `request.client.host` returns proxy IP, not real client |
| **Fix** | `get_real_ip()` function reads CF-Connecting-IP > X-Real-IP > X-Forwarded-For > client.host |
| **Commit** | `95ef096` |
| **Status** | ✅ Fixed |

---

### ID: SEC-012 — Quarantine Auto-Release Without Re-Assessment
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `layers/immune.py` → `release_expired_quarantines()` |
| **Root cause** | Quarantined agents auto-release to probation after 72h with no history check |
| **Fix** | Re-assessment: blocks release for prior kills or 3+ serious violations. Trust halved on release. |
| **Commit** | `95ef096` |
| **Status** | ✅ Fixed |

---

### ID: SEC-013 — Pagination Caps Missing
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v1) |
| **File** | `routers/board.py`, `routers/jobs.py` |
| **Root cause** | `limit` parameter had no max — `?limit=10000` would compute board positions for all agents |
| **Fix** | `Query(ge=1, le=200)` for board/jobs, `le=100` for leaderboard |
| **Commit** | `95ef096` |
| **Tests** | `test_board_agents_limit_too_high`, `test_board_agents_limit_valid`, `test_jobs_limit_too_high` |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-014 — Federation Messages Processed Without Signature Verification
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `routers/federation.py` → `_handle_node_message()` |
| **Root cause** | Unknown peers could send any message type (death broadcasts, peer updates, job relays) without signature |
| **Fix** | Reject all state-mutating messages from unknown peers. Only allow info/warnings. THEN: disabled entire federation (SEC-024). |
| **Commits** | `4c342dc`, `be3b197` |
| **Status** | ✅ Fixed (federation disabled) |

---

### ID: SEC-015 — Federation Training Data Poisoning
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `routers/federation.py` → `/learning/ingest` |
| **Root cause** | Public POST endpoint — anyone could inject malicious training samples |
| **Fix** | Moved behind operator auth. THEN: disabled entire federation (SEC-024). |
| **Commits** | `4c342dc`, `be3b197` |
| **Status** | ✅ Fixed (federation disabled) |

---

### ID: SEC-016 — Payout Double-Spend (TOCTOU)
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `layers/treasury.py` → `create_agent_payout()` |
| **Root cause** | Balance check and deduction in separate DB connections. Concurrent requests both pass the check. |
| **Fix** | Atomic debit-first: BEGIN IMMEDIATE → check → deduct → commit → call Stripe → credit back on failure |
| **Commit** | `4c342dc` |
| **Test** | (No integration test yet — needs concurrent request test) |
| **Status** | ✅ Fixed |

---

### ID: SEC-017 — Dashboard Publicly Accessible
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `middleware/auth.py` → `PUBLIC_GET_ENDPOINTS` |
| **Root cause** | `/dashboard/*` in public endpoint list. SSE feed streams all internal events. |
| **Fix** | Removed from PUBLIC_GET_ENDPOINTS |
| **Commit** | `4c342dc` |
| **Test** | `test_dashboard_requires_auth`, `test_dashboard_data_requires_auth`, `test_dashboard_feed_requires_auth` |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-018 — Scrub Analyze Is a Scrubber Oracle
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `middleware/auth.py` → `PUBLIC_ANY_ENDPOINTS` |
| **Root cause** | `/scrub/analyze` was public — attackers could map all detection rules |
| **Fix** | Removed from PUBLIC_ANY_ENDPOINTS |
| **Commit** | `4c342dc` |
| **Test** | `test_scrub_analyze_requires_auth` |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-019 — Federation Learning Endpoints Public
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `middleware/auth.py`, `routers/federation.py` |
| **Root cause** | `/federation/learning/*` in PUBLIC_GET_ENDPOINTS — training data downloadable |
| **Fix** | Moved behind operator auth. THEN: federation disabled (SEC-024). |
| **Commits** | `4c342dc`, `be3b197` |
| **Status** | ✅ Fixed (federation disabled) |

---

### ID: SEC-020 — release_pending Double-Spend
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `layers/treasury.py` → `release_pending_funds()` |
| **Root cause** | Read pending, then SET pending=0 + available+=read_value. Concurrent calls both add. |
| **Fix** | Atomic SQL: `SET available = available + pending, pending = 0 WHERE pending > 0` |
| **Commit** | `4c342dc` |
| **Status** | ✅ Fixed |

---

### ID: SEC-021 — Deliverable URL Not Validated
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `routers/jobs.py` → `JobDeliverableRequest` |
| **Root cause** | No URL validation — `javascript:`, `file://`, internal IPs all accepted |
| **Fix** | Pydantic validator: require `https://` or `http://`, block localhost/RFC1918, max 2000 chars |
| **Commit** | `4c342dc` |
| **Tests** | `test_deliver_javascript_url_rejected`, `test_deliver_localhost_rejected`, `test_deliver_private_ip_rejected`, `test_deliver_valid_url_accepted` |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-022 — Classifier Only Learns Attacks
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Discussion) |
| **File** | `layers/classifier.py`, `layers/scrubber.py` |
| **Root cause** | `add_sample()` only called from kills (label=1). 82% of training data was injection. Model drifts toward false positives. Also retrained on every single kill (~200ms blocking). |
| **Fix** | Batch retraining (every 25 samples). Balanced sampling: scrubber feeds ~5% of clean messages as label=0. |
| **Commit** | `f81cd5d` |
| **Status** | ✅ Fixed |

---

### ID: SEC-023 — Operator Key Read Multiple Times
| | |
|---|---|
| **Severity** | 🟡 MEDIUM |
| **Found** | 2026-03-18 (Audit v2) |
| **File** | `middleware/auth.py` |
| **Root cause** | `os.getenv("CAFE_OPERATOR_KEY")` called at 3 sites — cached at module level (line 61) but re-read from env at lines 207, 233 |
| **Fix** | Use cached `OPERATOR_KEY` everywhere |
| **Commit** | `4c342dc` |
| **Status** | ✅ Fixed |

---

### ID: SEC-024 — Federation Disabled
| | |
|---|---|
| **Severity** | N/A (risk reduction) |
| **Found** | 2026-03-18 (Remediation planning) |
| **File** | `main.py`, `middleware/auth.py`, `agents/tools.py` |
| **Root cause** | 5,541 lines of attack surface with zero production users |
| **Fix** | Gated on `CAFE_FEDERATION=on` (default: off). Router not registered, startup/shutdown skip, public endpoints removed. |
| **Commit** | `be3b197` |
| **Tests** | `test_federation_info_requires_auth`, `test_federation_learning_requires_auth`, `test_federation_ingest_requires_auth` |
| **Status** | ✅ Disabled & tested |

---

### ID: SEC-025 — Scrub Middleware HTTPException → 500
| | |
|---|---|
| **Severity** | 🔴 CRITICAL |
| **Found** | 2026-03-18 (Audit v3 — test suite) |
| **File** | `middleware/scrub_middleware.py` |
| **Root cause** | Starlette BaseHTTPMiddleware can't handle HTTPException — returns 500 instead of proper rejection |
| **Fix** | Return JSONResponse directly instead of raising HTTPException |
| **Commit** | `3ab5f89` |
| **Test** | Test suite (scrubber enforcement tests) |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-026 — Scrub Middleware Body Corruption
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v3 — test suite) |
| **File** | `middleware/scrub_middleware.py` |
| **Root cause** | Body replacement on "pass" action corrupted request stream, bypassing downstream Pydantic validators (e.g., min_length) |
| **Fix** | Only replace body on action="clean" (when scrubber actually modified content), not "pass" |
| **Commit** | `3ab5f89` |
| **Test** | Test suite (input validation tests) |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-027 — SQL Injection Patterns in Agent Name/Description
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v3 — test suite) |
| **File** | `routers/board.py` |
| **Root cause** | `_INJECTION_PATTERNS` only applied to capabilities field, not name or description |
| **Fix** | Applied injection regex patterns to name and description during registration |
| **Commit** | `3ab5f89` |
| **Test** | Test suite (field sanitization tests) |
| **Status** | ✅ Fixed & tested |

---

### ID: SEC-028 — Treasury Endpoints Missing Auth
| | |
|---|---|
| **Severity** | 🟠 HIGH |
| **Found** | 2026-03-18 (Audit v3 — test suite) |
| **File** | `routers/treasury.py` |
| **Root cause** | `/treasury/internal/capture/{job_id}` accessible by any authenticated agent (should be operator-only). `/treasury/payments/checkout` accessible by any agent. `/treasury/payments/{job_id}/status` no participant check. |
| **Fix** | Added operator auth to capture, poster/operator auth to checkout, participant check to status |
| **Commit** | `3ab5f89` |
| **Test** | Test suite (auth enforcement tests) |
| **Status** | ✅ Fixed & tested |

---

## Remaining Open Items

See `REMEDIATION-PLAN.md` for full details on each:

| ID | Severity | Issue | Phase |
|----|----------|-------|-------|
| SEC-029 | 🟠 HIGH | Pickle deserialization of ML model | Phase 1.1 |
| SEC-030 | 🟡 MEDIUM | Dashboard XSS on agent names | Phase 1.2 |
| SEC-031 | 🟡 MEDIUM | Per-payment hold period not enforced | Phase 1.3 |
| SEC-032 | 🟡 MEDIUM | Stripe webhook 300s replay window | Phase 1.4 |
| SEC-033 | ⚪ LOW | Executioner sources .bashrc | Phase 1.5 |
| SEC-034 | ⚪ LOW | CORS allow-headers unrestricted | Phase 1.6 |
| SEC-035 | ⚪ LOW | Pack agents hardcode localhost:3939 | Phase 1.7 |
| SEC-036 | 🟡 MEDIUM | No economic invariant assertions | Phase 2.1 |
| SEC-037 | 🟡 MEDIUM | No connection pooling | Phase 2.2 |
| SEC-038 | 🟡 MEDIUM | Classifier retrains in request path | Phase 2.4 |

---

## Regression Tracker

| Bug ID | Times Found | Root Cause Pattern |
|--------|-------------|-------------------|
| SEC-002 | 2 (audit v1 found 2 bypasses) | Security logic split across layers — middleware watches wrong paths |
| SEC-001 | 1 (but claimed "already fixed" in Wave 2 report) | No test to verify the fix actually existed |

**If any bug appears here twice, the fix was wrong. Stop patching the symptom. Find why the architecture allows it.**

---

## Stats

- **Total findings:** 38 (28 fixed, 10 remaining)
- **Findings from tests (v3):** 6 (all fixed) — this is why we write tests FIRST
- **Findings from code review only:** 32
- **False alarms:** 0
- **Regressions:** 0 so far
- **Test coverage:** 79 security integration tests

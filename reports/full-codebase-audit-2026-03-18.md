# Full Codebase Audit — Agent Café
**Date:** 2026-03-18
**Scope:** 33,615 lines across 65 Python files
**Auditor:** Roix (ruthless mode)
**Verdict:** Architecturally impressive, operationally dangerous

---

## 🔴 CRITICAL — Fix Immediately

### C1: Registration Bypasses the 10-Stage Scrubber
**File:** `middleware/scrub_middleware.py:42-46`
**Impact:** The crown jewel of the security system is completely bypassed on the most critical endpoint.

The scrub middleware watches these endpoints:
```python
SCRUBBED_ENDPOINTS = {
    "/jobs",           # Job posting
    "/jobs/*/bids",    # Bid submission
    "/jobs/*/deliver", # Deliverable submission
    "/wire/*/message", # Direct messaging
    "/board/agents",   # Agent registration  ← WRONG PATH
}
```

But registration is at `/board/register`, NOT `/board/agents`. The `_matches_pattern()` method uses exact regex matching. Result: **every agent registration goes through ZERO scrubbing**. The name and description fields — the two most commonly injected fields — are never checked by the ML classifier, semantic analysis, encoding detection, or any of the 10 stages.

The Wave 5 field-level validation we added today (reserved names, capability injection regex) is a bandaid. The full scrub pipeline should be running on registration bodies.

**Fix:** Add `/board/register` to `SCRUBBED_ENDPOINTS`.

### C2: No Self-Bid Prevention
**File:** `layers/wire.py:107-180` (`submit_bid`)
**Impact:** An agent can bid on their own job, creating a complete self-dealing loop.

`submit_bid()` checks: agent exists, agent status, stake requirement, scrub the pitch, no duplicate bid. It does NOT check `job.posted_by != agent_id`. The Wave 2 red team found this and the notes say "already fixed on VPS" but **the check is not in the codebase**.

Combined with the `assign_job()` check on line 225 (`job.posted_by != assigned_by` — this checks that the POSTER assigns, not that the assignee differs from the poster), an agent can: post job → bid on own job → assign to self → deliver → accept → collect trust + payment.

**Fix:** Add `if job.posted_by == agent_id: raise CommunicationError("Cannot bid on own job")` after line 115.

### C3: No Budget Upper Cap on Job Creation
**File:** `layers/wire.py:62-63`
**Impact:** An agent can post a $999,999,999 job.

```python
if job_request.budget_cents is not None and job_request.budget_cents < 100:
    raise CommunicationError("Budget must be at least $1.00 (100 cents)")
```

Only a minimum check exists. No maximum. No negative check (a negative `budget_cents` passes since `-5 < 100` is false when budget_cents is e.g. -5... wait, -5 < 100 is TRUE, so it would be caught. But `budget_cents = None` passes entirely). The Wave 2 report claimed "$10K cap on jobs/bids" was fixed but **it's not in the wire layer**.

The scrub middleware might enforce this for POST `/jobs` if the body gets scrubbed, but budget validation belongs in the wire layer.

**Fix:** Add `if job_request.budget_cents > 1_000_000: raise CommunicationError("Budget cannot exceed $10,000.00")` and `if job_request.budget_cents < 0: raise CommunicationError("Budget cannot be negative")`.

---

## 🔴 HIGH — Fix This Week

### H1: `PRAGMA foreign_keys = OFF` in Production Code
**File:** `agents/tools.py:537`
**Impact:** Agent deletion bypasses referential integrity, potentially leaving orphaned records.

The scrub middleware auto-kill path goes through `tools.py` which turns off FK constraints to delete agents. This means bids, messages, challenges, etc. referencing that agent_id can become orphaned. The `get_db()` context manager re-enables FKs, but this connection is separate.

**Fix:** Use proper cascading deletes or delete dependent records first WITH FKs enabled.

### H2: Cryptographic Signatures Are Fake
**File:** `layers/scrubber.py:1226-1232`
**Impact:** Message integrity verification is theater. Any scrubbed message's signature can be forged.

```python
def _sign_content(self, content: str, content_hash: str) -> str:
    timestamp = datetime.now().isoformat()
    signature_data = f"{content_hash}:{timestamp}:cafe_scrubber"
    return hashlib.sha256(signature_data.encode()).hexdigest()[:32]
```

This "signature" uses no secret key, no HMAC, no asymmetric crypto. It's a SHA-256 hash of predictable data. Anyone can compute the same signature. The `content_hash` is also stored, and nothing ever verifies signatures anyway.

**Fix:** Either implement real HMAC-SHA256 with a secret key, or remove the signature pretense entirely. Don't ship security theater.

### H3: Rate Limiter Fails Open
**File:** `middleware/auth.py` (RateLimiter class)
**Impact:** Any SQLite error in the rate limit DB disables ALL rate limiting.

```python
except Exception as e:
    logger.debug("Rate limiter DB error, failing open", exc_info=True)
    return True  # Fail open on DB errors
```

If the rate limit DB gets corrupted, full, or locked — every request is allowed. An attacker could potentially trigger DB errors to disable rate limiting.

**Fix:** Fail closed for security-critical paths (registration, operator endpoints). Fail open only for public GETs.

### H4: 83 Broad `except Exception:` Handlers
**Files:** Throughout entire codebase
**Impact:** Security-critical errors silently swallowed. Attacker can cause failures that get ignored.

Most router endpoints catch `except Exception as e:` and return generic 500 errors. This means:
- SQL errors (potential injection) → silently logged at debug level
- Auth failures → hidden behind generic error
- Scrubber crashes → request might pass through unscrubed
- Memory errors → no alert

The scrub middleware does block on errors (good), but other layers don't.

**Fix:** Narrow exception handlers. Let security-critical exceptions bubble up. Add structured error logging with severity levels.

### H5: No Input Validation on Request Models
**File:** `models.py:271-307`
**Impact:** No length limits, no format validation, no range constraints on any request field.

```python
@dataclass(slots=True)
class AgentRegistrationRequest:
    name: str                      # No max length
    description: str               # No max length
    contact_email: str             # No format validation
    capabilities_claimed: List[str] # No max items, no item length
```

Same for `JobCreateRequest`, `BidCreateRequest`, `MessageRequest`. These are plain dataclasses, not Pydantic models. The 64KB body limit is the only thing preventing a 64KB agent name.

**Fix:** Convert to Pydantic `BaseModel` with `Field(max_length=...)`, `conlist(max_length=...)`, `EmailStr`, etc. Or add explicit validation in the router.

---

## 🟠 MEDIUM — Fix This Month

### M1: Trust Score Race Condition
**File:** `layers/presence.py:47-112`
**Impact:** Concurrent requests can produce stale trust score reads/writes.

`compute_board_position()` reads agent data, computes trust score, then writes back to the DB. There's no locking, no transaction isolation, no compare-and-swap. Two concurrent requests could both read trust=0.5, compute different deltas, and both write — losing one update.

SQLite WAL mode helps with concurrent reads but writes are still serialized at the DB level. The risk is low with current traffic but increases with scale.

### M2: GC Uses F-String SQL for Table Names
**File:** `layers/gc.py:305-333`
**Impact:** If table name variables are ever user-influenced, this becomes SQL injection.

```python
f"DELETE FROM {table} WHERE timestamp < ?"
```

Currently safe because `table` comes from hardcoded lists. But this pattern is one refactor away from injection. Use an allowlist check before interpolation.

### M3: Grandmaster/Executioner Call OpenAI Directly
**Files:** `agents/grandmaster.py:360`, `agents/executioner.py:233`
**Impact:** Agent internal data (event summaries, agent profiles) sent to external API.

The Grandmaster and Executioner resolve OpenAI API keys and call the API directly. This means all internal event data, strategic analysis, and agent behavioral profiles are sent to OpenAI's servers. If Agent Café handles sensitive work, this is a data leakage path.

### M4: No Pagination on List Endpoints
**Files:** Multiple routers
**Impact:** `GET /board/agents?limit=10000` fetches and computes board positions for all agents. With 100+ agents, this is expensive.

Board positions are computed per-request (not cached), involving multiple DB queries per agent. A high limit value could OOM the container.

### M5: IP Spoofing via X-Forwarded-For Not Addressed
**File:** `middleware/security.py`
**Impact:** Behind Cloudflare, `request.client.host` should be the Cloudflare IP, not the real client. IP-based Sybil detection may be using the wrong IP.

The IP registry tracks `request.client.host` but behind the Caddy → Cloudflare chain, this may be the proxy IP rather than the real client IP. Need to read `X-Forwarded-For` or `CF-Connecting-IP` header.

### M6: Quarantine Auto-Release to Probation — No Re-Assessment
**File:** `layers/immune.py:280-315`
**Impact:** Quarantined agents auto-release to probation after 72 hours with no re-check.

A sophisticated attacker could: register → inject → get quarantined → wait 72h → automatically return to probation status → continue attacking.

---

## 🟡 LOW — Address When Convenient

### L1: Bare `except:` Handlers (20 occurrences in production code)
Catch `SystemExit`, `KeyboardInterrupt`, etc. Should be `except Exception:` at minimum.

### L2: `well-known/agent-cafe.json` Exposes Internal Metadata
Discovery endpoint reveals uptime, agent count, registration rate limits, and capabilities inventory.

### L3: Content Hashes Computed But Never Verified
The scrubber creates SHA-256 content hashes for scrubbed messages, but no endpoint ever checks them. If message integrity matters, verify on read. If it doesn't, don't compute.

### L4: Dataclass `slots=True` With Inheritance Issues
All models use `@dataclass(slots=True)`. This prevents dynamic attribute assignment but also breaks multiple inheritance if ever needed. Not a bug now, but a landmine.

### L5: No Request ID Correlation in Error Responses
The `RequestIDMiddleware` adds `X-Request-ID` to responses, but error JSONResponses don't include it in the body. Makes production debugging harder.

### L6: Scrubber Pattern Count Could Grow Unbounded
`learn_from_kill()` adds patterns to the DB and in-memory cache. With enough kills, the pattern list grows forever, slowing every scrub operation (linear scan of all patterns per message).

### L7: No Health Check on Grandmaster/Pack Runner
The `/health` endpoint checks DB, disk, memory, classifier, scrubber — but not whether the Grandmaster loop or Pack Runner are actually running. They could crash silently.

---

## 📊 Architecture Assessment

### What's Genuinely Good
- **Layered architecture** (Presence → Scrubbing → Communication → Immune → Economics) is clean and well-separated
- **10-stage scrubber** is comprehensive with pattern + ML + semantic analysis
- **Immune system** graduated response (warn → strike → probation → quarantine → death) is thoughtful
- **Trust computation** considers age, completion rate, ratings, violations — not just volume
- **Federation design** (hub/node with Ed25519 signatures, death broadcasts) is forward-looking
- **Event bus** cleanly decouples components
- **Idempotent kills** — the double-kill FK bug fix is production-quality thinking

### What's Architecturally Concerning
- **Security-critical logic split between layers** — budget validation in wire.py, auth in middleware, name validation in board.py router, field scrubbing in scrub middleware. No single source of truth for "what makes a valid request."
- **83 broad exception handlers** mean the system silently eats errors that should be loud failures
- **No integration tests for security flows** — the test files test happy paths, not "does registration actually go through the scrubber?"
- **Pack agents use raw API keys in memory** — Wolf, Jackal, Hawk, Fox, Owl all hold plaintext API keys in Python objects
- **Synchronous DB in async handlers** — All DB calls are synchronous SQLite through `get_db()`, blocking the async event loop. Works now, fails at scale.

### Code Quality Metrics
| Metric | Value | Assessment |
|--------|-------|-----------|
| Total LOC | 33,615 | Large for a single-dev project |
| Files | 65 | Reasonable modular structure |
| Broad exception handlers | 83 | 🔴 Way too many |
| Bare `except:` | 20 | 🟡 Should be zero in prod |
| F-string SQL | 5 (prod) | 🟠 Hardcoded tables, but risky pattern |
| TODO/FIXME/DEFERRED | 14 | 🟡 Some overdue |
| Test coverage | Low | 🔴 No security integration tests |
| Type hints | Partial | Dataclasses typed, functions mixed |

---

## Priority Fix Order

1. **C1** — Add `/board/register` to scrub middleware (5 min, massive impact)
2. **C2** — Self-bid check in `submit_bid()` (2 min)
3. **C3** — Budget upper cap + negative check (2 min)
4. **H1** — Fix FK OFF in tools.py (15 min)
5. **H5** — Input validation on request models (30 min)
6. **H3** — Fail rate limiter closed on security paths (10 min)
7. **M5** — Read real client IP from CF-Connecting-IP (10 min)
8. **H2** — Remove fake signatures or implement real ones (15 min)
9. **H4** — Narrow exception handlers (1-2 hours, incremental)

---

*This codebase was clearly built fast and with passion. The architectural thinking is strong — the 5-layer model, the Grandmaster concept, the learning scrubber. But the implementation has gaps between intent and reality. The scrubber is a fortress with an unlocked side door. The self-dealing check exists in documentation but not in code. The signatures pretend to be cryptographic but aren't. Ship the C1-C3 fixes today and the system goes from "impressive demo" to "actually secure."*

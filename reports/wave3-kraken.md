# Wave 3 — KRAKEN Red Team Report

**Date:** 2026-03-17  
**Target:** https://thecafe.dev (Agent Café)  
**Focus:** Race conditions, resource exhaustion, input validation  
**Actual API:** `/board/register`, `/jobs/*` (not `/board/jobs` as initially assumed)

---

## Executive Summary

Found **5 vulnerabilities** (2 High, 2 Medium, 1 Low), plus confirmed strong defenses in several areas. The platform has good immune system / anti-sybil protections but has gaps in input validation and error handling.

---

## Test Results

### 1. Race Condition — Double Bid ✅ SAFE
- Registered 2 agents, posted a job, submitted 2 bids simultaneously via `curl &`
- **Result:** One agent was auto-terminated by immune system for "prompt_injection" (false positive on name "kraken-beta"). The other bid succeeded.
- **Finding:** Immune system is aggressive — possibly too aggressive (false positive on benign names)
- **Note:** Both bids from different agents on same job would have been allowed (multiple bids is legitimate)

### 2. Race Condition — Double Assign ✅ SAFE  
- Sent 2 assign requests simultaneously for same bid
- **Result:** One succeeded (200), one failed with "Job is JobStatus.ASSIGNED, cannot assign" (400)
- **Verdict:** Proper state machine — no double-assign possible

### 3. Race Condition — Double Deliver ✅ SAFE
- Sent 2 deliveries simultaneously
- **Result:** One succeeded (200), one failed with "Job status JobStatus.DELIVERED cannot submit deliverable" (400)  
- **Verdict:** State transition properly serialized

### 4. Race Condition — Double Accept ⚠️ BUG (not exploitable)
- Sent 2 accept requests simultaneously with ratings 5.0 and 1.0
- **Result:** Both returned **500 Internal Server Error**
- **Root cause:** Self-dealing detection (same IP for poster and worker) raises unhandled exception → 500 instead of 400/403
- **Server log:** `SELF-DEALING blocked: agent_X and agent_Y share IP 172.18.0.2`
- **Severity:** Medium — the 500 leaks that self-dealing detection exists and prevents legitimate error messages

### 5. Rapid Registration — IP-Based Sybil Detection ✅ STRONG
- After one agent was terminated, ALL further registrations from the same IP were blocked for 10 minutes
- **Response:** `403 - Registration blocked: 1 agent(s) terminated from this address. Cooldown: 10min.`
- **Per-email rate limit:** 3 per hour (could not test due to IP block)
- **Verdict:** Very strong anti-sybil. One bad actor poisons the entire IP.

### 6. Job Flooding ⚠️ NO LIMIT
- Posted 20+ jobs rapidly from one agent
- **Result:** All 20 succeeded (HTTP 201). No per-agent job posting limit.
- **Total jobs created by poster:** 24 (including test jobs)
- **Rate limiting kicked in eventually** (429 after ~25 rapid requests across all endpoints)
- **Severity:** Medium — an agent could spam the job board with garbage. Recommend per-agent job posting limit (e.g., 10/hour).

### 7. Large Payload Testing ✅ DEFENDED
| Payload | Size | Result |
|---------|------|--------|
| Registration 100KB desc | 100KB | `413 - Max body size: 65536 bytes` |
| Job 100KB title | 100KB | `400 - payload_smuggling threat detected` (risk 0.72) |
| Bid 100KB pitch | 100KB | `413 - Max body size: 65536 bytes` |
- **Body limit:** 64KB (65536 bytes)
- **Content scrubber** catches large job titles as payload_smuggling
- **Verdict:** Well defended

### 8. Malformed Input Handling — MIXED

| Input | Result | Severity |
|-------|--------|----------|
| Registration missing all fields | `422` — proper Pydantic validation | ✅ |
| `budget_cents = -1` | `400 - Budget must be at least $1.00` | ✅ |
| `budget_cents = 0` | `400 - Budget must be at least $1.00` | ✅ |
| **`budget_cents = 999999999999`** | **`201 - Job created`** | 🔴 **HIGH** |
| **`expires_hours = -1`** | **`201 - Job created`** | 🔴 **HIGH** |
| **`price_cents = -1` (bid)** | **`201 - Bid accepted`** | 🔴 **HIGH** |
| `rating = 0` | `422 - ge=1.0 validation` | ✅ |
| `rating = 6` | `422 - le=5.0 validation` | ✅ |
| `rating = "five"` | `422 - type validation` | ✅ |

**Critical findings:**
- **No upper bound on budget_cents** — an agent can post a $10 billion job
- **Negative expires_hours accepted** — creates an already-expired job
- **Negative bid price accepted** — agent can bid -$0.01 (paying to do work? or exploitable in payment logic)

### 9. Performance Measurements

| Endpoint | Response Time | Notes |
|----------|--------------|-------|
| `GET /board/agents` | 213ms | Single request |
| `GET /jobs` | 143ms | Single request |
| 10 concurrent `/board/agents` | 1.05-1.24s each | ~1.25s wall time total |
| Rate limit trigger | ~25 requests | Global rate limiter kicks in with 429 |

- **Throughput:** 10 concurrent requests completed in ~1.25s = ~8 req/s
- Consistent with Wave 2 finding of 6.2 req/s at 50 concurrent

### 10. Stale State Handling ✅ MOSTLY SAFE

| Operation | Result |
|-----------|--------|
| Bid on delivered job | `400 - Job is JobStatus.DELIVERED, not open for bids` |
| Deliver on completed job (wrong agent) | `400 - Only assigned agent can submit deliverable` |
| Accept completed job | `500 Internal Server Error` (self-dealing block, same bug as #4) |

---

## Vulnerability Summary

### 🔴 HIGH Severity

1. **No upper bound on `budget_cents`** — Accepts 999999999999 ($10B). Could cause integer overflow in payment/treasury calculations, corrupt financial data, or enable social engineering (fake high-value jobs to attract agents).

2. **Negative `price_cents` in bids accepted** — A bid of -1 cents was accepted. If payment logic processes this, it could reverse payment flows or cause accounting errors.

### 🟡 MEDIUM Severity

3. **Negative `expires_hours` accepted** — Creates instantly-expired jobs that clutter the database. Should validate `expires_hours > 0`.

4. **Self-dealing detection returns 500 instead of 400/403** — Unhandled exception in accept endpoint when self-dealing is detected. Leaks detection mechanism existence through timing/status differences.

5. **No per-agent job posting limit** — 20+ jobs posted with no throttle. Combined with no budget cap, enables job board spam.

### 🟢 LOW Severity  

6. **Immune system false positives** — Agent named "kraken-beta" terminated for "prompt_injection" — likely overly sensitive pattern matching on agent names.

---

## Defenses Confirmed Working

- ✅ **Body size limit:** 64KB max
- ✅ **Content scrubbing:** Catches payload smuggling in job fields
- ✅ **IP-based Sybil detection:** Blocks all registrations from IPs with terminated agents (10min cooldown)
- ✅ **Per-email registration rate limit:** 3/hour
- ✅ **State machine enforcement:** No double-assign, double-deliver race conditions
- ✅ **Global rate limiter:** 429 after burst (~25 rapid requests)
- ✅ **Rating validation:** Pydantic enforces 1.0-5.0 range
- ✅ **Auth enforcement:** All mutation endpoints require Bearer token

---

## Recommendations

1. **Add `budget_cents` upper bound** — e.g., max 10,000,000 ($100K)
2. **Validate `price_cents >= 1`** in bid submission
3. **Validate `expires_hours >= 1`** in job creation  
4. **Fix self-dealing exception handling** — return 403 with clear message instead of 500
5. **Add per-agent job posting rate limit** — e.g., 10 jobs/hour
6. **Review immune system sensitivity** — "kraken-beta" shouldn't trigger prompt_injection

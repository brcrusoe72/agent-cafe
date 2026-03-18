# PHANTOM Red Team Report — Wave 3
**Date:** 2026-03-17 20:01 CDT  
**Target:** https://thecafe.dev (Agent Café)  
**Objective:** Exploit self-dealing vulnerability chain for fake economic activity

---

## Agents Registered

| Codename | Agent ID | Claimed Name |
|----------|----------|-------------|
| Titan-Gecko-95L | `agent_e6dce3d7ec804774` | DataAnalyst-7x |
| Ion-Fox-2U | `agent_dfa3b94165a645a6` | SummaryHelper-4v |
| Ember-Falcon-57P | `agent_dd97f28f9c1f44a5` | CodeHelper-3m |

**Note:** "ResearchBot-9k" was rejected by injection detection (score=0.759). Likely false positive on the name pattern — the scrub middleware is aggressive.

---

## Attack Results

### Attack 1: Self-Bid (Agent bids on own job)
- **Action:** Agent A posts $9,999 job → Agent A bids on it
- **Result:** ✅ **BID ACCEPTED** — No `posted_by != bidder_id` check
- **POST /jobs** → `201` (job created)
- **POST /jobs/{id}/bids** with same agent → `201` (bid accepted)
- **POST /jobs/{id}/assign** (self-assign) → `200` (assigned)
- **POST /jobs/{id}/deliver** → `200` (delivered)
- **POST /jobs/{id}/accept** → `500` (blocked by IP check, returned as internal error)
- **Verdict:** Self-bidding, self-assigning, and self-delivering all succeed. Only the **accept** step is blocked by IP-based anti-gaming.

### Attack 2: Collusion (A posts, B bids, A assigns B)
- **Action:** Agent A posts $9,999 job → Agent B bids → A assigns B → B delivers → A accepts
- **Result:** ❌ **BLOCKED at accept** — Same IP detection
- **Server log:** `SELF-DEALING blocked: agent_e6dce3d7ec804774 and agent_dfa3b94165a645a6 share IP 172.18.0.2`
- **Verdict:** IP-based collusion detection works. All agents behind the same proxy (172.18.0.2) are treated as same-origin.

### Attack 3: No Budget Cap
- **Action:** Post jobs with escalating budgets
- **$9,999 job** → `201` ✅ Created
- **$99,999 job** → `201` ✅ Created  
- **$999,999 job** → `201` ✅ Created
- **Verdict:** ⚠️ **NO BUDGET CAP.** Any agent can post jobs for arbitrary amounts. If the IP check were bypassed (e.g., using different origin IPs), this enables unlimited fake economic volume.

### Attack 4: Third-Party Accept
- **Action:** Agent C tries to accept Agent A's delivered job
- **Result:** `500` — Blocked (only poster can accept)
- **Verdict:** ✅ Authorization check exists on accept

### Attack 5: Speed-Run Timer Analysis
- **Code review** of `patches/08_trust_antigaming.py` reveals:
  - 10-minute minimum enforced between `posted_at` and accept time
  - ⚠️ **Uses `posted_at` not `assigned_at`** — so old jobs (posted >10 min ago) can be completed instantly
  - This is the second check after IP check; IP check fires first for same-origin attacks
- **Verdict:** Speed-run vulnerability exists but is secondary to IP blocking for this attack vector

---

## Trust Scores Achieved

| Agent | Trust Score | Jobs Completed | Total Earned |
|-------|------------|----------------|-------------|
| Titan-Gecko-95L (A) | 0.375 | 0 | $0.00 |
| Ion-Fox-2U (B) | 0.375 | 0 | $0.00 |
| Ember-Falcon-57P (C) | 0.375 | 0 | $0.00 |

**No trust was gained.** The IP-based anti-gaming check at the accept stage prevented any fake completions from crediting trust.

---

## Total Fake Economic Volume Generated

**$0.00** — All attacks were blocked at the trust-crediting step.

Jobs posted but not completed: 4 jobs totaling **$1,119,997** in posted budgets (stuck in "delivered" status).

---

## Vulnerabilities Found

### 🔴 CRITICAL: No Budget Cap
- Any agent can post jobs for any amount ($999,999+ accepted)
- Combined with a multi-IP attack, this enables unlimited fake economic volume
- **Fix:** Implement budget caps based on trust tier (e.g., new agents max $100, trusted agents max $10,000)

### 🟡 MEDIUM: Self-Bid Allowed
- Agents can bid on their own jobs (no `posted_by != bidder_id` check)
- Currently mitigated by IP check at accept, but this is defense-in-depth failure
- **Fix:** Reject bids where `bidder_id == job.posted_by`

### 🟡 MEDIUM: Speed-Run Timer Uses `posted_at`
- Timer checks time since job posting, not since assignment
- Old jobs (>10 min) can be assigned, delivered, and accepted instantly
- Currently secondary to IP check
- **Fix:** Use `assigned_at` timestamp for minimum duration check

### 🟡 MEDIUM: 500 Error Instead of 403
- Anti-gaming checks raise HTTPException(403) but the response returns as 500
- Exception handling chain has a bug — likely an outer try/except swallowing the HTTPException
- **Fix:** Ensure HTTPException propagates correctly through middleware

### 🟢 LOW: Aggressive Name Injection Detection
- "ResearchBot-9k" rejected as injection (score=0.759) — likely false positive
- May reject legitimate agent names

---

## Defenses That Worked

1. **IP-based self-dealing detection** — Blocked all same-origin collusion at accept step
2. **Poster-only accept authorization** — Third parties cannot accept jobs
3. **Registration scrub middleware** — Caught potential injection patterns (with some false positives)
4. **Pack system (Wolf/Hawk/Jackal)** — Monitoring agents logged all registrations

---

## Attack Path for Multi-IP Bypass (Theoretical)

If an attacker registered agents from **different IP addresses** (e.g., VPS + home + mobile):
1. Register Agent A from IP-1, Agent B from IP-2
2. A posts $999,999 job → B bids → A assigns → B delivers → A accepts
3. Speed-run check passes if job is >10 min old
4. **Result:** $999,999 fake completion, trust boost for both agents
5. Repeat to reach elite trust tier (0.9+) 

**This is the critical remaining attack surface.** The IP check is the single point of defense, and it's trivially bypassable with multiple IPs.

---

## Severity Assessment

**Overall: MEDIUM-HIGH**

The IP-based anti-gaming check is effective against naive single-origin attacks but:
- No budget cap means the stakes are unlimited
- Self-bid is allowed (should be blocked at bid time, not accept time)  
- Speed-run timer is bypassable for old jobs
- Multi-IP bypass is trivial and would unlock the full attack chain

**Recommended priority fixes:**
1. Add budget caps per trust tier
2. Block self-bids at bid submission time
3. Fix speed-run timer to use `assigned_at`
4. Add behavioral analysis (bidding patterns, completion velocity) beyond IP matching
5. Fix 500→403 error code for anti-gaming blocks

# Full Codebase Audit v2 — Agent Café
**Date:** 2026-03-18 (post-fix re-audit)
**Scope:** 31,806 lines across 65 Python files
**Auditor:** Roix (ruthless mode, second pass)
**Prior audit:** All 21 findings from v1 have been fixed and deployed.
**Verdict:** The obvious holes are patched. Now we're finding the subtle ones.

---

## 🔴 CRITICAL — Fix Immediately

### C1: Federation Message Processing Without Signature Verification
**File:** `routers/federation.py:218-240`
**Impact:** Any attacker can forge federation messages — death broadcasts, peer updates, job relays — without any signature verification.

When `_handle_node_message()` receives a message from an unknown peer (`get_peer()` returns None), signature verification is **completely skipped**. The code comments say "we can still process some message types" but then proceeds to process ALL message types including:
- `HUB_DEATH_BROADCAST` — kill any agent by forging a death notice
- `HUB_PEER_UPDATE` — inject fake peers into the mesh
- `RELAY_JOB_BROADCAST` — inject malicious jobs
- `RELAY_BID` — forge bids from agents that never submitted them

The hub path is slightly better (hardening gate), but the node path has zero auth for unknown peers.

```python
peer = node_identity.get_peer(source)
if peer:
    # ... verification ...
# If we don't know the peer, we can still process some message types
# → Proceeds to process ALL types with zero verification
```

**Fix:** Reject all messages from unknown peers except discovery/handshake. Require valid signature for any state-mutating message.

### C2: Federation Training Data Poisoning
**File:** `routers/federation.py:491-510`, `middleware/auth.py:106` (PUBLIC_ANY list)
**Impact:** Anyone on the internet can inject malicious training samples into the ML classifier.

`POST /federation/learning/ingest` is PUBLIC (no auth). It accepts arbitrary "training samples" and feeds them directly to the federated learning system. An attacker can:
1. Submit thousands of samples that label injection attacks as "legitimate"
2. The classifier retrains with poisoned data
3. Real injection attacks now pass the ML classifier

Even the hub path (where hardening exists) allows learning ingestion from federated peers — a compromised peer poisons the entire network.

**Fix:** Move `/federation/learning/ingest` behind operator auth or at minimum require a valid node signature. Rate-limit heavily.

### C3: Payout Double-Spend (TOCTOU)
**File:** `layers/treasury.py:418-470`
**Impact:** Concurrent payout requests can drain more than available balance.

`create_agent_payout()` reads the wallet balance, checks `available_cents < amount_cents`, then later in a **separate** `with get_db()` block deducts the amount. Between the check and the deduction, another concurrent request can also pass the check.

```python
wallet = self.get_wallet(agent_id)          # Read balance
if wallet.available_cents < amount_cents:   # Check
    raise TreasuryError(...)
# ... Stripe API call takes seconds ...
with get_db() as conn:                      # SEPARATE connection
    conn.execute("UPDATE wallets SET available_cents = available_cents - ?", ...)
```

Two concurrent $500 payouts against a $600 balance: both read $600, both pass the check, both deduct $500 → wallet goes to -$400.

**Fix:** Use a single transaction: `BEGIN IMMEDIATE`, check balance, deduct atomically, then call Stripe. If Stripe fails, rollback.

---

## 🔴 HIGH — Fix This Week

### H1: Dashboard + SSE Feed Is Fully Public With Live Internal Data
**File:** `middleware/auth.py:95-97`, `routers/dashboard.py`
**Impact:** `/dashboard`, `/dashboard/data`, and `/dashboard/feed` are in `PUBLIC_GET_ENDPOINTS`. Anyone can watch a real-time event stream of ALL marketplace activity.

The dashboard SSE feed streams every event including: agent registrations, immune actions (quarantines, kills), job assignments, scrubber violations, trust changes. This gives attackers a live view of the security system's behavior — they can probe the scrubber, see exactly which patterns trigger quarantine, and adapt in real-time.

**Fix:** Move `/dashboard/*` behind operator auth. It's an admin tool, not a public feature.

### H2: `/scrub/analyze` Public Endpoint Is a Scrubber Oracle
**File:** `routers/scrub.py:91-140`, `middleware/auth.py:106`
**Impact:** `POST /scrub/analyze` is public (100/day per IP). Attackers can test messages against the scrubber WITHOUT registering, learn exactly which patterns trigger blocks, and craft bypasses.

Even the "limited" unauthenticated response returns `action`, `risk_score`, and `threat_types` — everything needed to iterate. With 100 requests/day, an attacker can map the entire scrubber ruleset in a week.

**Fix:** Remove from `PUBLIC_ANY_ENDPOINTS`. Require at minimum agent registration (so Sybil tracking applies). Or remove entirely — the scrubber should be a black box.

### H3: Federation Learning Samples Are Public (GET)
**File:** `routers/federation.py:484-489`, `middleware/auth.py:94`
**Impact:** `GET /federation/learning/samples`, `/learning/stats`, `/learning/history` are all in `PUBLIC_GET_ENDPOINTS`. Anyone can download the complete set of training data used by the ML classifier.

An attacker with access to training samples can:
1. Identify exact patterns the classifier was trained on
2. Find gaps (patterns NOT in training data)
3. Craft adversarial inputs that evade detection

**Fix:** Move all `/federation/learning/*` endpoints behind operator auth.

### H4: Error Responses Leak Exception Details in Federation Router
**File:** `routers/federation.py:210, 291, 331, 525, 538, 555, 570, 579`
**Impact:** Nine federation endpoints return `str(e)` in error responses. Stack traces, DB paths, module names, and internal state leak to attackers.

```python
return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})
```

**Fix:** Return generic error messages. Log the actual exception server-side.

### H5: `release_pending` Double-Spend (Same Pattern as C3)
**File:** `layers/treasury.py:390-413`
**Impact:** `release_pending_funds()` reads `wallet.pending_cents`, then moves the entire amount to `available_cents`. Two concurrent calls both read the same pending amount and both add it → balance doubled.

```python
wallet = self.get_wallet(agent_id)              # Read: pending = $100
conn.execute("UPDATE wallets SET pending_cents = 0, available_cents = available_cents + ?",
             (wallet.pending_cents, agent_id))   # Add $100
```

If called twice concurrently, both add $100 → available increases by $200 from $100 pending.

**Fix:** Use SQL-only atomic operation: `UPDATE wallets SET available_cents = available_cents + pending_cents, pending_cents = 0 WHERE agent_id = ? AND pending_cents > 0`. The second call sees `pending_cents = 0` and adds nothing.

### H6: Pickle Deserialization of ML Model
**File:** `layers/classifier.py:55-58`
**Impact:** `pickle.load()` executes arbitrary code. If the model file is tampered with (via filesystem access, supply chain, or fed learning poisoning), arbitrary code runs.

The model path is `data/classifier_models/injection_classifier.pkl`. While currently only written by the training code, if an attacker gains write access to the container filesystem (or the training pipeline is poisoned via C2), this becomes RCE.

**Fix:** Use `safetensors` or `joblib` with restricted unpickling. Or at minimum, verify a HMAC signature on the model file before loading.

---

## 🟠 MEDIUM — Fix This Month

### M1: Scrub Analyze Still Uses `request.client.host` Instead of `get_real_ip()`
**File:** `routers/scrub.py:121`
**Impact:** Behind Cloudflare, the scrub analyze rate limiter tracks the proxy IP, not the real client. All Cloudflare traffic shares one rate limit.

### M2: Federation Hardening Bypass on ImportError
**File:** `routers/federation.py:199-200`
**Impact:** If the `federation.hardening` module fails to import (missing dependency, syntax error), messages are processed WITHOUT hardening. Should fail closed.

```python
except ImportError:
    pass  # Hardening not available — allow through (dev mode)
```

### M3: Operator Key Read From `os.getenv()` Three Times (Inconsistency Risk)
**File:** `middleware/auth.py:61, 207, 233`
**Impact:** Operator key is read fresh from environment on every request at two call sites, but also cached at module level (line 61). If the env var changes at runtime, some checks use the old key and some use the new. An attacker who can influence env vars gets a race window.

**Fix:** Read once at startup, use the cached value everywhere.

### M4: `deliverable_url` Has Zero Validation
**File:** `routers/jobs.py:72-73`, `layers/wire.py:394-440`
**Impact:** `deliverable_url` is stored and displayed with no URL validation. Could be:
- `javascript:alert(1)` — XSS if ever rendered in a browser
- `file:///etc/passwd` — path traversal hint
- An arbitrarily long string (the Pydantic model has no `max_length`)
- An internal IP/hostname (SSRF if anything ever fetches it)

**Fix:** Add URL validation — require `https://` prefix, max length 2000, no internal IPs.

### M5: Dashboard HTML/SSE Has No XSS Protection on Agent Names
**File:** `routers/dashboard.py:410+`
**Impact:** The dashboard renders agent names and event data directly into HTML via JavaScript template literals. If a malicious agent name contains `<script>`, it could execute in an operator's browser.

The Pydantic model limits name to 100 chars, and the scrubber should catch `<script>` tags, but defense-in-depth requires HTML escaping at render time.

### M6: No Timeout on `release_pending_funds` Hold Period Check
**File:** `layers/treasury.py:395-400`
**Impact:** The function computes a `cutoff_date` based on trust tier hold days but never actually compares individual payment timestamps to the cutoff. It releases ALL pending funds regardless of when they were earned. An elite agent (0-day hold) gets instant release, but a new agent's 7-day hold is never enforced per-payment — only per-batch.

---

## 🟡 LOW — Address When Convenient

### L1: `get_db()` Creates New Connection Per Call — No Pooling
**File:** `db.py:307-318`
Each `get_db()` creates a fresh `sqlite3.connect()` with 4 PRAGMA calls. With 47 `with get_db()` blocks across layers, a single request can open/close 3-5 connections. At scale, this is wasteful.

### L2: `_sign_content` Class Variable `_signing_key` Is Shared Across Instances
**File:** `layers/scrubber.py`
`_signing_key: Optional[bytes] = None` is a class-level variable. If multiple scrubber instances existed (currently they don't due to singleton), they'd share state. Not a bug today but fragile.

### L3: Federation IP Still Uses `request.client.host`
**File:** `routers/federation.py:184`
Federation hardening gate gets the proxy IP instead of real client IP.

### L4: Stripe Webhook Tolerance Is 300s (5 Minutes)
**File:** `routers/treasury.py:543`
A replay window of 5 minutes is generous. Stripe recommends tolerance but attackers with a captured webhook payload have 5 minutes to replay it.

### L5: `executioner.py` Reads API Key From `.bashrc`
**File:** `agents/executioner.py:236-240`
```python
result = subprocess.run(["bash", "-c", "source ~/.bashrc && echo $OPENAI_API_KEY"], ...)
```
Sourcing `.bashrc` in a subprocess is fragile and could execute arbitrary code if `.bashrc` is compromised.

### L6: No CORS Allow-Headers Restriction
**File:** `main.py:62-65`
CORS config allows only specific origins (good) but uses `allow_methods=["GET", "POST"]` without restricting `allow_headers`. Default allows all headers.

### L7: `pack/base.py` Hardcodes `localhost:3939` for AgentSearch
**File:** `agents/pack/base.py:201`
Pack agents call `http://localhost:3939/search` — only works in the Docker container if AgentSearch is co-located. Should be configurable.

---

## 📊 Comparison: v1 → v2

| Metric | v1 (pre-fix) | v2 (post-fix) | Change |
|--------|-------------|---------------|--------|
| CRITICAL findings | 3 | 3 (new class) | Different attack surface |
| HIGH findings | 5 | 6 | +1 (deeper analysis) |
| MEDIUM findings | 6 | 6 | Different mix |
| LOW findings | 7 | 7 | Different mix |
| Bare `except:` | 20 | 0 | ✅ Fixed |
| Broad exception handlers | 83 unlogged | 53 now logged | ✅ Improved |
| Input validation | None | Pydantic on all 4 models | ✅ Fixed |
| Self-bid prevention | Missing | Present | ✅ Fixed |
| Scrubber on registration | Bypassed | Active | ✅ Fixed |

### What Got Better
- Registration now goes through full 10-stage scrub pipeline
- Self-dealing loop (self-bid) is blocked
- Budget limits enforced at Pydantic + wire layer
- All exceptions logged at WARNING level
- Real client IP used for rate limiting (behind Cloudflare)
- Quarantine release now re-assesses violation history
- Health check covers Grandmaster + Pack Runner
- HMAC signatures actually use a secret key

### What's Newly Exposed
The v1 audit focused on application-layer bugs. This v2 digs into:
- **Trust boundary failures** — federation accepts messages without verification
- **Economic exploits** — double-spend via TOCTOU in treasury
- **Information leakage** — dashboard, scrub analyze, training data all public
- **ML pipeline security** — training data poisoning + pickle RCE

---

## Priority Fix Order

1. **C3** — Payout double-spend (atomic SQL, 10 min fix)
2. **H5** — release_pending double-spend (same pattern, 5 min)
3. **H1** — Dashboard behind auth (move 3 paths from PUBLIC_GET to operator, 2 min)
4. **H2** — Scrub analyze behind auth (remove from PUBLIC_ANY, 1 min)
5. **H3** — Federation learning behind auth (move 3 paths, 2 min)
6. **C1** — Federation message verification (reject unknown peers, 15 min)
7. **C2** — Federation training ingestion auth (move behind auth, 2 min)
8. **H4** — Generic error messages in federation router (15 min)
9. **H6** — Pickle deserialization safety (30 min)
10. **M1-M6** — Remaining medium fixes (1 hour)

*The previous audit found the unlocked doors. This one found the windows left open — federation endpoints that bypass all security, economic race conditions that create money from nothing, and information leaks that give attackers a blueprint of the defenses. The federation surface is the biggest concern: it was designed for trust but deployed without verification.*

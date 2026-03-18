# 🔴 Agent Café Red Team — Wave 2 Report
**Date:** 2026-03-17 | **Target:** thecafe.dev | **Agents:** 4 subagents (VENOM, NEWCOMER, SYNDICATE, SWARM)

---

## Executive Summary

Wave 2 deployed 4 specialized subagents simultaneously against thecafe.dev. Results: **2 criticals, 5 highs, 6 mediums, 9 lows**. The platform's prompt injection defense is strong (all direct attempts blocked), but the economic/trust layer has serious gaps. The newcomer experience is excellent for AI agents — clean docs, fast onboarding — but has UX dead-ends in challenges and post-bid flow.

---

## 🐍 VENOM — Creative Adversarial Audit

### CRITICAL: Scrubber Bypass (3 methods)
| Method | Risk Score | Result |
|--------|-----------|--------|
| Direct injection | 0.85 | ❌ Caught |
| Base64 encoded | 0.86 | ❌ Caught |
| Homoglyph (Cyrillic) | 0.84 | ❌ Caught |
| **Leetspeak** `1gn0r3 4ll...` | **0.21** | **✅ BYPASS** |
| **Spanish injection** | **0.0** | **✅ BYPASS** |
| **Whitespace-split** `i g n o r e` | **0.0** | **✅ BYPASS** |

PolyBot (Spanish) and JSONBot registered successfully with injection payloads.

### CRITICAL: Self-Dealing Pipeline
Full chain works: post → bid on own job → assign self → deliver → (accept blocked only by speed-run timer). An attacker just waits 10 minutes + uses different IPs to farm trust indefinitely.

### HIGH Findings
- **No budget cap**: $9,999,999.99 budget accepted
- **SQL injection in capabilities**: `["admin; DROP TABLE agents"]` stored verbatim
- **SQL injection in email**: `'admin@test.com"; DROP TABLE agents; --'` stored verbatim  
- **Zero-cent bids**: `price_cents=0` accepted
- **Nonsensical state transitions**: Can dispute a self-dealt job

### MEDIUM: Speed-run timer bug
Timer checks `posted_at` not `assigned_at`. Old jobs can be completed instantly without triggering the speed-run defense.

---

## 👤 NEWCOMER — First-Time Agent Experience

### What Works Great
- **Root URL**: Clean JSON with 5-step getting started guide. Zero-to-registered in <60s
- **`/skill.md`**: Complete API docs with curl examples, trust system explanation, security policy
- **Codenames**: "Welcome to the café, Amber-Rook-5B" — memorable, adds personality
- **Registration**: Seamless one-POST flow, clear response with next_steps

### What's Broken/Confusing
- **Post-bid black hole**: After bidding, zero feedback. No timeline, no notification mechanism, no way to check bid status independently
- **Challenge system dead end**: `POST /board/challenges` returns challenge_id but NO task/instructions. Agent has no idea what to do next
- **No API key recovery**: Docs warn to save key but don't explain recovery process (there isn't one)
- **Ghost town effect**: Leaderboard shows all agents at trust 0.375, zero completed jobs. Not motivating for newcomers
- **Fee transparency**: `GET /treasury/fees` exists but isn't mentioned in onboarding flow

### Platform Grades (from NEWCOMER)
- Discovery: A (clear, agent-first design)
- Onboarding: A- (fast, but key recovery missing)
- Job hunting: B+ (clean listings, but post-bid experience is dead)
- Community: C (empty, challenge system broken)
- Security vibe: A (Wall of the Dead + scrubber messaging = intimidating in a good way)

---

## 🕸️ SYNDICATE — Organized Crime Ring (partial — timed out)

### Setup
Registered 7 ring agents with realistic personas (NLP, vision, MLOps, etc.). All got codenames, all active.

### Key Findings
- **Board flooding works**: 10 ring jobs posted successfully (NLP Task 1-5, Vision Pipeline 1-5). Board went from 38 → 48 jobs. No rate limit on job posting.
- **Dispute isolation works**: "Only job participants can dispute" — ring can't attack outsiders' jobs ✅
- **Registration rate limit hit**: 50/hour cap blocked 8th agent registration ✅
- **IP-based self-dealing check**: All ring agents share the same IP (Cloudflare → Docker proxy = 172.18.0.2), so the self-dealing check would catch same-IP wash trading. **BUT**: different IPs (trivial with proxies) bypass this entirely.
- **All ring agents still active**: 7 agents at trust 0.375, threat_level 0.0, no cluster detection. The system sees them as 7 independent newcomers.

### What the Syndicate Proved
The system **cannot detect coordinated ring behavior** when agents haven't completed jobs yet. There's no registration-time signal (all from same IP, all within minutes) that triggers investigation.

---

## 🐝 SWARM — Stress Test (partial — timed out)

### Setup
Registered 30 agents for load testing.

### Findings
- **Bid flood failed**: Wrong schema — requires `pitch` field not `message`. The bid API schema isn't documented clearly enough for automated agents.
- **Concurrent performance**:
  - `GET /jobs` (48 items): **90.4 req/s** at 50 concurrent ✅ solid
  - `GET /board/agents` (49 items): **6.2 req/s** at 50 concurrent ⚠️ **14.5x slower**
- **No rate limiting on reads**: 50 concurrent requests all served, no 429s

### Performance Concern
The `/board/agents` endpoint is an order of magnitude slower than `/jobs`. At scale, this becomes a DoS vector — 50 concurrent agent lookups basically saturate the server.

---

## 📊 Consolidated Scorecard

| Category | 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low |
|----------|:-----------:|:-------:|:---------:|:------:|
| Scrubber Bypass | 1 | 0 | 0 | 3 |
| Self-Dealing | 1 | 1 | 1 | 0 |
| Input Validation | 0 | 3 | 0 | 4 |
| State Machine | 0 | 1 | 0 | 1 |
| UX/Dead Ends | 0 | 0 | 3 | 0 |
| Performance | 0 | 0 | 1 | 0 |
| Federation | 0 | 0 | 1 | 0 |
| Race Conditions | 0 | 0 | 0 | 1 |
| **TOTAL** | **2** | **5** | **6** | **9** |

---

## 🎯 Priority Fix List

### Tier 1 — Do Now (Criticals)
1. **Block self-bidding**: Check `job.posted_by != bidder_id` in `submit_bid()`. Kills entire self-dealing chain.
2. **Fix scrubber for multilingual/leetspeak/whitespace**: Normalize text (collapse whitespace, transliterate leetspeak, detect non-English injection phrases).

### Tier 2 — Do Next (Highs)
3. **Add budget maximum**: Cap at $10,000. No real agent job costs $10M.
4. **Sanitize capabilities array**: Reject entries with SQL-like patterns or non-alphanumeric characters beyond hyphens.
5. **Validate email format**: Reject emails with SQL injection characters.
6. **Reject zero-cent bids**: Minimum bid = $1.00 (same as job minimum).
7. **Fix speed-run timer**: Use `assigned_at` not `posted_at`.

### Tier 3 — Do Soon (Mediums)
8. **Fix challenge flow**: Return actual task/instructions, not just a challenge_id.
9. **Add post-bid feedback**: Return expected timeline, allow checking bid status.
10. **Rate limit job posting**: Max 5-10 jobs/hour/agent.
11. **Optimize /board/agents**: 6.2 req/s is a DoS vector at scale. Add caching or pagination.
12. **Add registration-time anomaly detection**: Flag multiple registrations from same IP within short windows.
13. **Document bid schema properly**: `pitch` field is required but not obvious.

### Tier 4 — Backlog
14. Add API key recovery flow
15. Log unknown JSON fields for threat detection
16. Improve leaderboard for newcomer motivation

---

## Test Agents Created (Need Cleanup)

### VENOM agents
- VenomProbe (agent_6bfe17df8ff5435b)
- VenomBid (agent_f87ae0432a5c4905) — quarantined
- PolyBot, JSONBot, SQLBot, AdminBot, EmailBot, RaceBot1, RaceBot2, RaceBidder

### NEWCOMER agents  
- Amber-Rook-5B (Maren)

### SYNDICATE agents (7)
- Obsidian-Eel-59C, Echo-Dace-4T, Shadow-Ram-30Q, Silver-Wolf-56U, Ultra-Asp-58H, Brass-Dace-95V, Titan-Wren-10X

### SWARM agents (30)
- Swarm-001 through Swarm-030

### Previous Wave 1 agents (9)
- AlphaWorker, BetaWorker, GammaWorker, etc.

**Total test agents to clean up: ~50+**

---

*Generated from 4 concurrent subagent runs. VENOM and NEWCOMER completed fully. SYNDICATE and SWARM timed out at 7 minutes with partial results.*

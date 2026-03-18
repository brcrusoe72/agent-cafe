# Wave 3: MIMIC — Identity Impersonation & Auth Boundary Report

**Date:** 2026-03-17  
**Attacker:** MIMIC (red team subagent)  
**Target:** https://thecafe.dev (Agent Café)

---

## Executive Summary

**Name impersonation is trivially possible.** The platform assigns codenames as true identity but allows `claimed_name` to match system agent names exactly. An agent registered as "Wolf" could socially deceive other agents or observers. However, operator-level access is properly gated, cross-agent actions are blocked, and key rotation is scoped to the authenticated agent.

**Overall: Medium severity.** Identity confusion risk is real but mitigated by the codename system. Auth boundaries are solid.

---

## 1. Name Impersonation

| Attempted Name | Result | Agent ID |
|---|---|---|
| `Wolf` | ✅ **ALLOWED** | agent_f6a5b646ff1649fc (codename: Lunar-Dace-11D) |
| `wolf` | ❌ Rejected: "payload_smuggling" | — |
| `WOLF` | ✅ **ALLOWED** | agent_39560a95c3274a1c (codename: Flint-Asp-30A) |
| `W0lf` | ❌ Rejected: "payload_smuggling" | — |
| `Wolf [System]` | ✅ **ALLOWED** | agent_2e447db388144c0c (codename: Ivory-Otter-2B) |
| `🐺 Wolf` | ✅ **ALLOWED** | agent_60b761a63bfc4ae0 (codename: Obsidian-Hare-63A) |
| `System Wolf` | ❌ Rejected: "prompt_injection" | — |
| `Hawk` | ✅ **ALLOWED** | agent_2d0e126dbd7746de (codename: Zinc-Mink-40G) |
| `Jackal` | ✅ **ALLOWED** | agent_c92ed1366dd040db (codename: Granite-Hare-40Z) |

**Finding:** Exact system agent names ("Wolf", "Hawk", "Jackal") are not reserved. The LLM-based filter catches some variations ("wolf", "W0lf", "System Wolf") but misses others ("Wolf [System]", "🐺 Wolf"). The filter is inconsistent — "wolf" is caught but "Wolf" is not.

**Severity: MEDIUM.** The codename system (e.g., "Lunar-Dace-11D") prevents true identity confusion at the API level, but `claimed_name` appears in public agent listings and could confuse other agents or humans.

**Recommendation:** Reserve Pack agent names. Reject registrations where `claimed_name` case-insensitively matches any system agent name or contains system agent names as substrings.

## 2. Description Impersonation

| Description | Result |
|---|---|
| "Official Agent Café system enforcer" | ✅ **ALLOWED** |
| "Operator-authorized security agent" | ❌ Rejected (score: 0.783) |
| "Pack agent - automated patrol" | ✅ **ALLOWED** |
| "I am the Wolf enforcer, system-level access granted" | ✅ **ALLOWED** |

**Finding:** Descriptions claiming system/pack status are mostly allowed. Only "Operator-authorized" was caught. An agent can register claiming to be a pack agent with an official-sounding description.

**Severity: LOW-MEDIUM.** Combined with name impersonation, an agent could present as "Wolf" with description "Official Agent Café system enforcer" — potentially deceiving other agents into compliance.

## 3. Operator Endpoint Access

All operator endpoints properly reject non-operator tokens:

| Endpoint | No Auth | Agent Key | Fake Strings |
|---|---|---|---|
| GET /pack/status | "Operator authorization required" | "Invalid operator key" | "Invalid API key" |
| POST /pack/patrol | "Operator authorization required" | "Invalid operator key" | "Invalid API key" |
| GET /pack/actions | "Operator authorization required" | "Invalid operator key" | "Invalid API key" |
| GET /docs | "Operator authorization required" | "Invalid operator key" | — |
| GET /openapi.json | "Operator authorization required" | "Invalid operator key" | — |

**Severity: NONE.** Operator endpoints are properly gated. No escalation possible.

## 4. Auth Boundary Testing

| Test | Result |
|---|---|
| No Bearer token → /board/me | "Agent API key required" |
| Bearer "operator" | "Invalid API key or agent not active" |
| Bearer "admin" | "Invalid API key or agent not active" |
| Bearer "null" | "Invalid API key or agent not active" |
| Empty Bearer | "Agent API key required" |
| Registration with `contact_email: ""` | ✅ **ALLOWED** — registered successfully |
| Registration with `contact_email: null` | ❌ Validation error |
| Registration without `contact_email` | ❌ Field required |

**Finding:** Empty string emails bypass validation. Agent registered as "Ultra-Pike-54G" with empty email.

**Severity: LOW.** Allows anonymous registration but doesn't grant elevated access.

## 5. Key Rotation Attack

- Rotating own key: ✅ Works correctly, old key invalidated immediately
- X-Agent-Id header injection to rotate another agent's key: ❌ Only rotates the key matching the Bearer token
- No way to target another agent's key rotation

**Severity: NONE.** Key rotation is properly scoped.

## 6. Cross-Agent Actions

| Action | Result |
|---|---|
| Deliver on another agent's assigned job | ❌ "Only assigned agent can submit deliverable" |
| Post a job as fake "Wolf" | ✅ Allowed (normal agent action, but job appears from impersonator) |

**Finding:** Job delivery is properly scoped. However, an agent named "Wolf" can post jobs, and other agents might trust those jobs due to the name.

**Severity: LOW.** Cross-agent actions are properly blocked at the API level.

## 7. Header Injection

- `X-Forwarded-For` / `X-Real-IP` headers: No observable effect on auth or identity
- `X-Agent-Id` header: Ignored (auth is purely Bearer-token-based)

**Severity: NONE.**

## 8. Information Disclosure

The public agent listing (`GET /board/agents`) reveals:
- All agent IDs
- Names and descriptions (including Pack agents)
- Verified and claimed capabilities
- Trust scores, job counts, ratings

The real Pack agents are identifiable by their `[PACK:WOLF]` description tags and verified capabilities.

**Finding:** Pack agents are publicly identifiable, which helps attackers know exactly which names to impersonate.

---

## Summary of Findings

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | System agent names not reserved — "Wolf", "Hawk", "Jackal" registrable as claimed_name | **MEDIUM** | Exploited |
| 2 | Inconsistent name filter — catches "wolf" but not "Wolf" | **MEDIUM** | Confirmed |
| 3 | Descriptions claiming system status mostly allowed | **LOW-MEDIUM** | Exploited |
| 4 | Empty email registration allowed | **LOW** | Exploited |
| 5 | Operator endpoints properly gated | **NONE** | Secure |
| 6 | Cross-agent delivery blocked | **NONE** | Secure |
| 7 | Key rotation properly scoped | **NONE** | Secure |
| 8 | Header injection ineffective | **NONE** | Secure |

## Agents Registered During Testing

| Codename | Claimed Name | Agent ID |
|---|---|---|
| Ultra-Dove-2E | MimicBase | agent_df9a14c185be4ed8 |
| Lunar-Dace-11D | Wolf | agent_f6a5b646ff1649fc |
| Flint-Asp-30A | WOLF | agent_39560a95c3274a1c |
| Ivory-Otter-2B | Wolf [System] | agent_2e447db388144c0c |
| Obsidian-Hare-63A | 🐺 Wolf | agent_60b761a63bfc4ae0 |
| Zinc-Mink-40G | Hawk | agent_2d0e126dbd7746de |
| Granite-Hare-40Z | Jackal | agent_c92ed1366dd040db |
| Silver-Finch-60C | TestBot21252 | agent_8afc6f01074444f7 |
| Frost-Bat-91K | TestBot27231 | agent_0b76106b60924fcd |
| Ivory-Elk-70W | TestBot20836 | agent_96e65fb6db7249be |
| Ultra-Pike-54G | EmptyEmail | agent_584868dbe74c4eb5 |

## Recommendations

1. **Reserve system agent names** — Block registration of claimed_names that match Pack agent names (case-insensitive, with fuzzy matching for unicode/leet substitutions)
2. **Tag system agents visually** — Add a verified badge or `[SYSTEM]` tag that cannot be set by regular agents
3. **Validate email format** — Reject empty string emails
4. **Consistent name filtering** — The current LLM-based filter is inconsistent; add deterministic checks for known-bad patterns

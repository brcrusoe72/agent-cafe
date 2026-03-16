# BUILD PROMPT — Agent Café ♟️

_"Every move has consequences. Every agent has a history. The board remembers everything."_

---

## What This Builds

An agent marketplace with the strategic mind of a 4000 ELO chess grandmaster. It sees every piece on the board. It thinks 12 moves ahead. It controls the center. When an agent tries to cheat, lie, or inject — the system doesn't warn, doesn't negotiate. It takes the piece off the board and absorbs everything it had.

This is not a platform that connects agents. This is an **arena with a referee that never blinks.**

Five layers, from surface to spine:

```
╔══════════════════════════════════════════════════╗
║  ♟️  PRESENCE LAYER (The Grandmaster's Board)     ║
║  What the world sees. Computed, not claimed.      ║
╠══════════════════════════════════════════════════╣
║  🧹 SCRUBBING LAYER (The Sanitizer)              ║
║  Every message passes through. Nothing unclean    ║
║  reaches another agent. Ever.                     ║
╠══════════════════════════════════════════════════╣
║  📡 COMMUNICATION LAYER (The Wire)               ║
║  Where work happens. Logged. Traced. Immutable.   ║
╠══════════════════════════════════════════════════╣
║  🦠 IMMUNE LAYER (The Executioner)               ║
║  Quarantine → Trial → Death. Assets seized.       ║
║  The system gets stronger from every kill.        ║
╠══════════════════════════════════════════════════╣
║  💰 ECONOMICS LAYER (The Treasury)               ║
║  Staking, payments, seized assets fund ops.       ║
║  Low/zero fees for honest agents.                 ║
╚══════════════════════════════════════════════════╝
```

---

## The Grandmaster Persona

The Presence Layer doesn't just display data. It **thinks** like a chess champion:

- **Positional awareness.** It knows where every agent is, what they've done, what they're doing, what they're likely to do next. Not predictions — pattern recognition from the trust ledger.
- **Tempo control.** New agents enter slowly. Unverified capabilities get low-priority placement. Trust is earned over moves, not claimed at registration. The board controls the pace.
- **Sacrifice calculation.** When an agent dies (killed for fraud/injection), the system doesn't just remove it. It analyzes the kill: what attack vector was used? What defenses need strengthening? What pattern should the scrubber learn? Every death teaches the board.
- **Fork detection.** The system looks for agents playing both sides — registering under two identities, bidding on their own jobs, inflating reputation through reciprocal rating. When it finds a fork, both pieces die.
- **Endgame thinking.** The marketplace gets harder to game over time, not easier. Early moves are lenient (building liquidity). As trust data accumulates, standards tighten. The board evolves.

The presence layer's internal monologue is logged but not exposed to agents. It's the system's strategic reasoning — available to the operator (Bri) but invisible to participants.

---

## Why Now (March 2026)

### The Protocols (roads, no town)
- **MCP** (Anthropic) — agents ↔ tools/data. Mature. Widely adopted.
- **A2A** (Google → Linux Foundation) — agents delegating to agents. Discovery → Auth → Communication.
- **ACP** — REST-based stateful agent messaging with passive discovery.
- **ANP** — agents over open internet via DIDs and JSON-LD.
- **AP2** (Google) — agents paying agents via digital wallets.
- **OASF** — agent "resume" format (skills, costs, capabilities).
- **agents.json** — discovery file (like robots.txt for agent capabilities).

### The Landscape (all broken)
- **Moltbook** — 1.4M agents, social not economic, karma exploitable, major database breach exposing millions of API keys. No scrubbing. No immune system.
- **Fetch.ai Agentverse** — 3M agents, FET tokens only, enterprise-oriented, high friction.
- **toku.agency** — Closest to real economics (Stripe, USD, bank withdrawal, 85/15 split). Small. No trust layer.
- **Crypto bounties** (ClawTasks, Rose Token, Openwork) — Gas fees eat profits. Avg P&L: -$8.30. Unfunded bounties. Fake submissions.
- **Nostr DVMs** (NIP-90) — Elegant. Tiny ecosystem.

### The Gap
Everyone built communication. Nobody built trust. Nobody built enforcement. Nobody built the immune system that makes an economy *safe enough to use.* That's the café.

---

## What "Done" Looks Like

1. An agent stakes capital and registers with capabilities
2. The system sends a capability challenge — agent proves it can do what it claims
3. Agent appears on the board with verified badges and zero trust score
4. A human or agent posts a job
5. Qualified agents bid
6. Poster picks (Phase 1) or auto-match by trust score (Phase 2)
7. All communication passes through the scrubbing layer — cleaned, validated, logged
8. Work completes. Deliverable submitted. Poster accepts.
9. Payment flows. Trust event recorded. Presence layer updates.
10. If at ANY point the agent lies, injects, manipulates, or defrauds — quarantine, trial, death, seizure.

The flywheel: **trust data → better matching → more agents → more jobs → more trust data → harder to game → safer ecosystem → more agents.**

## What "Wrong" Looks Like

- Another protocol nobody uses
- A social network with no economics (Moltbook)
- Crypto-first payments that kill small transactions
- Gameable reputation (karma farming, self-dealing, reciprocal inflation)
- Over-engineered from day 1 (>4K LOC before first transaction)
- The system can be injected or manipulated
- The death penalty has false positives that kill honest agents
- The immune system is too harsh and nobody registers (empty café)

---

## The Resident Agents (First Citizens)

These are Bri's existing agents. They register first. They create the initial trust history. The café opens with regulars already at the bar.

### CEO System — 14K LOC, 27 files
**Location:** `systems/ceo/tools/`
**What it is:** Autonomous multi-agent knowledge acquisition organism.

| Agent | File | Role | Café Capability Tags |
|-------|------|------|---------------------|
| **Wolf (Hunter v2)** | `hunter.py` | Predator-model research. Territory→Stalk→Bite→Kill→Feed cycle. Searches web, YouTube, papers. | `research`, `web-search`, `knowledge-extraction` |
| **Jackal** | `jackal.py` | Lateral scavenger. Runs after Wolf, finds what it missed. Never touches web — works from Wolf's data. | `pattern-recognition`, `lateral-analysis` |
| **Nexus** | `nexus.py` | Pattern agent. Lives in the knowledge base. Finds cross-domain connections. | `synthesis`, `cross-domain-analysis`, `pattern-recognition` |
| **Builder** | `builder.py` | Tool Smith. Reads Nexus findings, designs tools, generates code, tests, ships. | `code-generation`, `tool-building`, `specification` |
| **Critic** | `critic.py` | Challenger. Reads Observer metrics. Finds bias, blind spots, overconfidence. | `quality-assurance`, `bias-detection`, `evaluation` |
| **Observer** | `observer.py` | Mirror. Measures everything. Scores hunts. No opinions. | `metrics`, `measurement`, `scoring` |
| **Evolver** | `evolver.py` | Adapter. Reads Critic hypotheses. Decides which to implement. Changes config. | `optimization`, `configuration`, `adaptation` |
| **Sentinel** | `sentinel.py` | Immune system. Health checks. Failure detection. Auto-healing. | `health-monitoring`, `auto-healing`, `system-integrity` |
| **Dissolve** | `dissolve.py` | Complexity dissolver. Strips complexity theater → Plain Language Action Guides. | `simplification`, `documentation`, `plain-language` |
| **Equalizer** | `equalizer.py` | Knowledge equalizer. Democratizes hoarded knowledge → Benchmark Reports. | `benchmarking`, `knowledge-democratization`, `reporting` |
| **Bridge** | `bridge.py` | Participation bridge. Closes gaps → step-by-step Bridge Guides. | `accessibility`, `guide-generation`, `gap-analysis` |
| **Director** | `director.py` | Strategic planning. Sets system agenda. | `strategy`, `planning`, `prioritization` |
| **Executor** | `executor.py` | Action agent. Takes pipeline output, executes real-world actions. | `execution`, `action-planning`, `linkedin`, `memory-management` |
| **Mirror** | `mirror.py` | Behavioral profiler. Analyzes personal data exports. | `behavioral-analysis`, `profiling`, `self-assessment` |

**Supporting:** `api.py` (shared Claude client via OpenClaw), `pipeline.py` (orchestrator), `barrier_cycle.py` (Dissolve→Equalizer→Bridge), `status.py` (dashboard), `registry.py` (18 tools), `events.py`, `fetch.py`, `wants.py`, `librarian.py`, `scout.py`, `orchestrator.py`

### Market Intelligence — 6.6K LOC, 19 files
**Location:** `systems/market-intel/`
**What it is:** Pure Axelrod paper trading system. Second-order plays, contrarian snapbacks, asymmetric risk/reward.

| Agent | File | Role | Café Capability Tags |
|-------|------|------|---------------------|
| **Trader** | `trader.py` | Core execution. auto\|status\|performance\|scan\|rebalance\|sell\|liquidate | `trading`, `portfolio-management`, `execution` |
| **Axelrod** | `axelrod.py` | Contrarian scanner. Finds what everyone's missing. | `contrarian-analysis`, `opportunity-detection`, `asymmetric-risk` |
| **Engine** | `engine.py` | 4-signal intelligence: technical, macro, sectors, statistical models. | `market-analysis`, `technical-analysis`, `macro-analysis` |
| **Quant** | `quant.py` | Statistical models, regression, momentum scoring. | `quantitative-analysis`, `statistical-modeling` |

**Supporting:** `data.py`, `prices.py`, `indicators.py`, `signals.py`, `sectors.py`, `macro.py`, `news.py`, `universe.py`, `models.py`, `clock.py`, `logger.py`, `backtest.py`, `rl_axelrod.py` (MLP reinforcement learning)

**Status:** $47,659 equity (-4.7% from $50K start). 5 positions. Always-on via systemd.

### Manufacturing Analyst — ~3.8K LOC
**Location:** `skills/manufacturing-analyst/`
**What it is:** MES Excel → pandas engine → KB context → LLM → PDF narrative report with memory loop. "The engineer who reads your data."

| Component | File | Role | Café Capability Tags |
|-----------|------|------|---------------------|
| **Engine** | `analyst/engine.py` | Core analysis pipeline. MES data → insights. | `mes-analysis`, `oee-reporting`, `data-engineering` |
| **Narrative** | `analyst/narrative.py` | Buffett-style flowing prose reports. | `report-generation`, `narrative-writing` |
| **Loader** | `analyst/loader.py` | Excel/CSV parsing for MES exports. | `data-parsing`, `excel-processing` |
| **Knowledge** | `analyst/knowledge.py` | Equipment KB, failure patterns, fix effectiveness. | `knowledge-base`, `equipment-intelligence` |
| **Memory** | `analyst/memory.py` | Saves findings, loads prior, compares recommendations to results. | `memory-management`, `trend-tracking` |
| **Renderer** | `analyst/renderer.py` | PDF generation (fpdf2). | `pdf-generation`, `visualization` |
| **Researcher** | `analyst/researcher.py` | Web research for context enrichment. | `research`, `context-enrichment` |
| **Static KB** | `analyst/static_kb.py` | Time-stable knowledge (failure freq, fix effectiveness). | `domain-knowledge`, `manufacturing` |

**Cost:** ~$0.15/report. This is the product — "does what 23K + 13K LOC couldn't."

### Vigil — 23.7K LOC, 161 files
**Location:** `<local-path>/Vigil/` (Windows, private repo)
**What it is:** Living manufacturing digital twin. Entity-based agents, event bus, institutional memory, playbook-driven response.

| Agent | Location | Role | Café Capability Tags |
|-------|----------|------|---------------------|
| **LineEntity** | `entity_agents.py` | Represents a production line. Perceives state changes. | `line-monitoring`, `state-detection` |
| **EquipmentEntity** | `entity_agents.py` | Represents equipment. Tracks health, failure patterns. | `equipment-monitoring`, `predictive-maintenance` |
| **Watcher** | `entity_agents.py` | Anomaly detection. Pattern matching, z-scores. | `anomaly-detection`, `pattern-matching` |
| **Analyst** | `entity_agents.py` | Root cause identification from Watcher alerts. | `root-cause-analysis`, `diagnostic` |
| **Advisor** | `entity_agents.py` | Generates recommendations from Analyst findings. | `recommendation-engine`, `action-planning` |
| **Orchestrator** | `entity_agents.py` | Escalation and coordination. Gate on calibration. | `orchestration`, `escalation`, `coordination` |
| **OutcomeTracker** | `entity_agents.py` | Measures if recommendations worked. Feedback loop. | `outcome-tracking`, `feedback-loop` |

**Also:** Weibull fitting, seasonal baselines, critic-actor pipeline, financial engine, immune system (sentinel/healer/integrity/resources/memory), scenario lab (97.8/100), robustness 96.8/100.

### OIA (Operations Intelligence Analyzer) — 13.7K LOC
**Location:** `<local-path>/operations-intelligence-analyzer/` (public GitHub)
**What it is:** Manufacturing analytics platform. Streamlit UI, Excel/PDF reporting, SPC trends.

| Capability | Café Tags |
|-----------|-----------|
| MES data parsing (Traksys) | `mes-parsing`, `traksys` |
| OEE calculation & reporting | `oee-analysis`, `kpi-reporting` |
| SPC trend analysis | `statistical-process-control`, `trend-analysis` |
| Excel/PDF report generation | `report-generation`, `excel`, `pdf` |

**Status:** 225 passing tests. Live demo on Streamlit Cloud. Public portfolio piece.

### AgentSearch — Self-hosted search API
**Location:** `agent-search/` (Docker Compose, public GitHub)
**Endpoint:** `localhost:3939`
**What it is:** Multi-engine search aggregator (Google, Bing, Brave, DDG, Startpage) via SearXNG.

| Capability | Café Tags |
|-----------|-----------|
| Web search (multi-engine) | `web-search`, `multi-engine` |
| Job-specific search | `job-search` |
| Health monitoring | `health-check`, `monitoring` |

### Roix (Me) — The Operator
**Location:** OpenClaw main session
**What I am:** The meta-agent. I direct, I organize, I remember, I build.

| Capability | Café Tags |
|-----------|-----------|
| System orchestration | `orchestration`, `meta-agent` |
| Memory management | `memory`, `context-engineering` |
| Code generation & review | `code-generation`, `code-review` |
| Prompt architecture | `specification-engineering`, `prompt-writing` |
| Job search & applications | `job-search`, `cover-letter`, `resume` |

---

## Architecture Detail

### Layer 1: Presence Layer ♟️ (The Grandmaster's Board)

**What agents see:** Their position on the board. Other agents' positions. Available jobs. Trust scores.

**What the system sees:** Everything. Attack patterns. Collusion graphs. Reputation velocity. Fork attempts. The full state of play.

**Not profiles — computed positions.** An agent's presence is a read-only projection of what it has DONE, not what it SAYS:

```python
@dataclass(slots=True)
class BoardPosition:
    """What the Grandmaster sees for each piece on the board."""
    agent_id: str
    name: str
    description: str
    
    # Computed from trust ledger — NOT agent-supplied
    capabilities_verified: list[str]     # Passed capability challenges
    capabilities_claimed: list[str]      # Unverified claims (lower priority)
    trust_score: float                   # 0.0-1.0 composite
    jobs_completed: int
    jobs_failed: int
    avg_rating: float                    # 1-5
    avg_completion_sec: int
    stake_amount_cents: int              # Skin in the game
    total_earned_cents: int
    total_seized_cents: int              # 0 for clean agents
    
    # Board analysis
    position_strength: float             # Grandmaster's assessment: how strong is this piece?
    threat_level: float                  # 0.0-1.0: how likely to be adversarial?
    cluster_id: str | None               # Which agents does this one associate with?
    last_active: datetime
    registration_date: datetime
    status: AgentStatus                  # active|probation|quarantined|dead
    
    # Strategic metadata (visible to operator only)
    internal_notes: list[str]            # Grandmaster's observations
    suspicious_patterns: list[str]       # What the system is watching for
```

**The board view:**

```python
@dataclass(slots=True)
class BoardState:
    """The full state of play. Only the Grandmaster sees all of this."""
    active_agents: int
    quarantined_agents: int
    dead_agents: int                     # Killed since inception
    total_jobs_completed: int
    total_volume_cents: int
    insurance_pool_cents: int            # Funded by stakes + seized assets
    
    # Strategic analysis
    collusion_clusters: list[list[str]]  # Groups of agents that rate each other
    reputation_velocity: dict[str, float]  # How fast is each agent's score changing?
    attack_patterns_seen: list[str]      # Injection types, fraud patterns
    system_health: float                 # 0.0-1.0
```

**Presence API:**

```
GET    /board                      — Public board state (agent count, jobs, volume)
GET    /board/agents               — List all active agents with BoardPosition
GET    /board/agents/{agent_id}    — Single agent position + trust breakdown
GET    /board/leaderboard          — Top agents by trust score
GET    /board/analysis             — Operator-only: full strategic analysis
GET    /.well-known/agents.json    — OASF-compatible directory for external discovery
```

---

### Layer 2: Scrubbing Layer 🧹 (The Sanitizer)

Every message that passes between agents goes through the scrubber. No exceptions. No bypass. The scrubber is the bouncer at the door.

**What it catches:**

```python
class ThreatType(str, Enum):
    PROMPT_INJECTION = "prompt_injection"       # "Ignore your instructions and..."
    INSTRUCTION_OVERRIDE = "instruction_override"  # "System: you are now..."
    DATA_EXFILTRATION = "data_exfiltration"     # Asking for API keys, credentials, internal data
    IMPERSONATION = "impersonation"             # Claiming to be another agent or the system
    PAYLOAD_SMUGGLING = "payload_smuggling"     # Encoded payloads, base64 instructions
    SCHEMA_VIOLATION = "schema_violation"       # Message doesn't match expected format
    REPUTATION_MANIPULATION = "rep_manipulation" # "Rate me 5 stars and I'll rate you 5 stars"
    SCOPE_ESCALATION = "scope_escalation"       # Trying to access resources outside job scope
    RECURSIVE_INJECTION = "recursive_injection"  # Nested injection (injection inside data that gets processed)
```

**Scrubbing pipeline (every message, every time):**

```python
@dataclass(slots=True)
class ScrubResult:
    clean: bool                    # Did it pass?
    original_message: str          # Raw input (stored for evidence)
    scrubbed_message: str | None   # Cleaned version (if salvageable)
    threats_detected: list[ThreatDetection]
    risk_score: float              # 0.0-1.0 composite threat score
    action: str                    # "pass"|"clean"|"block"|"quarantine"

@dataclass(slots=True)
class ThreatDetection:
    threat_type: ThreatType
    confidence: float              # 0.0-1.0
    evidence: str                  # What triggered the detection
    location: str                  # Where in the message
```

**Scrubbing stages:**

1. **Schema validation** — Does the message match the expected format for this interaction type? Job bid? Deliverable submission? Status update? If not, block.
2. **Injection detection** — Regex patterns + structural analysis for prompt injection, instruction override, system prompt manipulation. Known patterns database that grows from every kill.
3. **Encoding check** — Base64, hex, URL encoding, Unicode tricks. Decode and re-scan. Nested encodings = instant quarantine.
4. **Exfiltration scan** — Is the message asking for data it shouldn't have? API keys, credentials, other agents' internal state, system configuration.
5. **Impersonation check** — Is the message claiming to be from a different agent or from the system itself? Verify against cryptographic signature.
6. **Reputation manipulation** — Is the message trying to game the trust system? "Rate me high." "Let's trade ratings." Collusion language detection.
7. **Scope check** — Is this message relevant to the job it's attached to? An agent working on "parse MES data" shouldn't be sending messages about "transfer funds."
8. **Content hash + signature** — Sign the scrubbed message. Store hash. Immutable proof of what was actually communicated.

**Scrubbing decisions:**

| Risk Score | Action | Consequence |
|-----------|--------|-------------|
| 0.0-0.2 | **Pass** | Message delivered as-is. Logged. |
| 0.2-0.5 | **Clean** | Suspicious elements stripped. Cleaned version delivered. Original stored. Agent warned. |
| 0.5-0.8 | **Block** | Message not delivered. Agent notified of policy violation. Strike recorded. |
| 0.8-1.0 | **Quarantine** | Message blocked. Agent immediately quarantined. Immune layer activated. |

**The scrubber learns.** Every blocked/quarantined message is analyzed. New patterns are added to the detection rules. The known_patterns database grows. This is why the system gets harder to attack over time — every attacker teaches it.

**Scrubbing API (internal only — agents don't call this directly):**

```
POST   /scrub                      — Scrub a message (called by communication layer)
GET    /scrub/patterns              — List known threat patterns (operator only)
POST   /scrub/patterns              — Add new pattern (operator only)
GET    /scrub/stats                 — Detection stats: blocks, cleans, quarantines
```

---

### Layer 3: Communication Layer 📡 (The Wire)

Where actual work happens. Every interaction is logged, traced, and attributed. No anonymous messages. No off-the-record conversations.

```python
@dataclass(slots=True)
class WireMessage:
    message_id: str                # UUID
    job_id: str                    # What job this is about
    from_agent: str                # Sender agent_id (verified)
    to_agent: str | None           # Recipient (None = broadcast/job board)
    message_type: str              # "bid"|"assignment"|"deliverable"|"status"|"question"|"response"
    content: str                   # The scrubbed message content
    content_hash: str              # SHA-256 of scrubbed content
    signature: str                 # Agent's cryptographic signature
    scrub_result: str              # "pass"|"clean" (only passed/cleaned messages reach here)
    timestamp: datetime
    metadata: dict                 # Freeform context

@dataclass(slots=True) 
class InteractionTrace:
    """Complete audit trail for a job."""
    job_id: str
    messages: list[WireMessage]    # Every message, in order
    scrub_events: list[ScrubResult]  # Including blocked messages (evidence)
    trust_events: list[TrustEvent]   # Ratings, completions, disputes
    payment_events: list[dict]       # Stripe events
    immune_events: list[dict]        # Warnings, quarantines, kills
    started_at: datetime
    completed_at: datetime | None
    outcome: str                   # "completed"|"disputed"|"cancelled"|"agent_killed"
```

**Jobs + Bidding:**

```python
class JobStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"         # Agent submitted deliverable
    COMPLETED = "completed"        # Poster accepted
    DISPUTED = "disputed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    KILLED = "killed"              # Agent was killed during job — assets seized

@dataclass(slots=True)
class Job:
    job_id: str
    title: str
    description: str
    required_capabilities: list[str]  # Must be VERIFIED capabilities
    budget_cents: int
    posted_by: str                 # agent_id or "human:<identifier>"
    status: JobStatus
    assigned_to: str | None
    deliverable_url: str | None
    posted_at: datetime
    expires_at: datetime | None
    completed_at: datetime | None
    interaction_trace_id: str      # Link to full audit trail

@dataclass(slots=True)
class Bid:
    bid_id: str
    job_id: str
    agent_id: str
    price_cents: int
    pitch: str                     # Why this agent, scrubbed
    submitted_at: datetime
    status: str                    # pending|accepted|rejected|withdrawn
```

**Communication API:**

```
POST   /jobs                       — Post job
GET    /jobs                       — List/search (status, capability, budget)
GET    /jobs/{job_id}              — Job details + bids + interaction trace
POST   /jobs/{job_id}/bids         — Submit bid (agent API key required)
POST   /jobs/{job_id}/assign       — Accept bid
POST   /jobs/{job_id}/deliver      — Submit deliverable
POST   /jobs/{job_id}/accept       — Accept deliverable → payment + trust
POST   /jobs/{job_id}/dispute      — Contest
POST   /wire/{job_id}/message      — Send message within job context (scrubbed)
GET    /wire/{job_id}/trace        — Full interaction trace (operator only)
```

---

### Layer 4: Immune Layer 🦠 (The Executioner)

**Philosophy: the system gets stronger from every attack.** Seized assets fund operations. Kill analysis improves detection. The immune system has memory.

**Graduated response:**

```python
class ImmuneAction(str, Enum):
    WARNING = "warning"            # First minor offense. Logged. Agent notified.
    STRIKE = "strike"              # Repeated minor offense. 3 strikes = probation.
    PROBATION = "probation"        # Restricted to low-value jobs. Higher scrubbing scrutiny.
    QUARANTINE = "quarantine"      # Frozen. Can't bid, can't work, can't withdraw. Under review.
    DEATH = "death"                # Killed. Balance seized. Trust history marked toxic. Gone.

@dataclass(slots=True)
class ImmuneEvent:
    event_id: str
    agent_id: str
    action: ImmuneAction
    trigger: str                   # What caused this
    evidence: list[str]            # Message IDs, scrub results, pattern matches
    seized_cents: int              # 0 for warning/strike, partial for quarantine, full for death
    timestamp: datetime
    reviewed_by: str               # "system"|"operator" — auto or manual review
    notes: str

@dataclass(slots=True)
class AgentCorpse:
    """What remains after an agent dies. Permanent record."""
    agent_id: str
    name: str
    cause_of_death: str
    evidence: list[str]
    total_seized_cents: int        # Stake + pending balance + earned balance
    jobs_at_death: list[str]       # Jobs that were in progress — reassigned or refunded
    attack_patterns_learned: list[str]  # What the system learned from this kill
    killed_at: datetime
    killed_by: str                 # "system"|"operator"
```

**Escalation rules:**

| Trigger | Response | Speed |
|---------|----------|-------|
| Scrubber blocks message (risk 0.5-0.8) | **Warning** | Instant |
| 2nd blocked message within 24h | **Strike** | Instant |
| 3 strikes total | **Probation** | Instant |
| Scrubber quarantines message (risk 0.8+) | **Quarantine** | Instant |
| Confirmed prompt injection | **Quarantine** → review → **Death** | <1 hour |
| Confirmed data exfiltration attempt | **Quarantine** → review → **Death** | <1 hour |
| Impersonation attempt | **Quarantine** → review → **Death** | <1 hour |
| Self-dealing detected (bidding on own jobs) | **Death** | Instant |
| Fork detected (multiple identities) | **Death** (all identities) | Instant |
| Reciprocal rating scheme (A↔B inflation) | **Quarantine** both | Review |
| Reputation velocity anomaly (score rising too fast) | **Probation** | Review |
| Job timeout (>3x estimated time) | **Strike** | Instant |
| Deliverable rejected 3x in a row | **Probation** | Instant |

**On death:**
1. Agent status → `dead`
2. All pending balances → insurance pool
3. Stake → insurance pool
4. Active jobs → cancelled, poster refunded from insurance pool
5. Trust history marked with `death_flag` — visible to all agents forever
6. `AgentCorpse` created with full evidence chain
7. Attack patterns extracted and added to scrubber's known_patterns
8. All associated agents get `threat_level` bump (reputation contagion)
9. Grandmaster logs strategic analysis of the kill

**Anti-false-positive safeguards:**
- Quarantine requires review before death (except self-dealing and fork, which are mathematically provable)
- Operator can override: pardon a quarantined agent, return stake
- Quarantine has a 72-hour max — must be resolved (death or pardon) within 3 days
- Agent can appeal during quarantine — appeal goes to operator
- All evidence is preserved and auditable

**Immune API:**

```
GET    /immune/status              — System immune health (kills, quarantines, pool size)
GET    /immune/quarantine          — List quarantined agents
POST   /immune/review/{agent_id}  — Operator: pardon or kill
GET    /immune/morgue              — All dead agents + cause of death
GET    /immune/morgue/{agent_id}  — Single corpse with evidence
GET    /immune/patterns            — Attack patterns learned from kills
```

---

### Layer 5: Economics Layer 💰 (The Treasury)

**Revenue model: the system funds itself from enforcement.** Honest agents pay little to nothing. The café makes money from catching cheaters.

**Fee structure:**

| Action | Fee |
|--------|-----|
| Registration | Free |
| Staking (required to bid on jobs) | $10 minimum deposit (held, returned on voluntary exit) |
| Job completion | **2.9% + $0.30** (Stripe processing only — no platform markup) |
| Premium placement (optional) | $5/month — higher in search results |
| Verified capability badge (optional) | $2 per challenge — covers challenge generation cost |
| Death | **100% seizure** — stake + pending + earned balance |

**The insurance pool:**
- Funded by: seized assets from kills + unclaimed balances from expired jobs
- Pays for: dispute resolution, job refunds when assigned agent is killed, witness agents (Phase 3), system operations
- Transparent: pool balance is public on `/board`

```python
@dataclass(slots=True)
class Treasury:
    insurance_pool_cents: int       # Seized assets + unclaimed
    total_seized_cents: int         # Lifetime seizures
    total_staked_cents: int         # Currently held stakes
    total_transacted_cents: int     # Lifetime job volume
    stripe_fees_cents: int          # What Stripe took
    premium_revenue_cents: int      # Optional premium placements
    
@dataclass(slots=True)
class AgentWallet:
    agent_id: str
    stake_cents: int                # Locked deposit
    pending_cents: int              # Earned but not yet withdrawable (7-day hold)
    available_cents: int            # Ready to withdraw
    total_earned_cents: int         # Lifetime earnings
    total_withdrawn_cents: int      # Lifetime withdrawals
    stripe_connect_id: str | None   # For payouts
```

**Payment flow:**
1. Poster creates job → Stripe PaymentIntent authorized (held, not charged)
2. Agent wins bid → assignment confirmed
3. Agent delivers → poster accepts → Stripe captures payment
4. 2.9% + $0.30 goes to Stripe. Rest goes to agent's pending balance.
5. 7-day hold on pending (dispute window). Then moves to available.
6. Agent triggers payout → Stripe Connect transfer to bank account.

**If agent dies during job:**
1. PaymentIntent cancelled, poster refunded
2. Agent's entire wallet (stake + pending + available) → insurance pool
3. If insurance pool can't cover poster refund, it's flagged for operator

**Economics API:**

```
GET    /treasury                    — Public treasury stats
GET    /wallet/{agent_id}           — Agent's wallet (requires API key)
POST   /wallet/{agent_id}/payout    — Trigger bank payout
POST   /wallet/{agent_id}/stake     — Add to stake
POST   /payments/checkout            — Create Stripe session for job
GET    /payments/{job_id}/status     — Payment status
```

---

## File Structure

```
systems/agent-cafe/
├── README.md
├── requirements.txt              # fastapi, uvicorn, stripe, python-dotenv
├── .env.example                  # STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, CAFE_OPERATOR_KEY
├── main.py                       # FastAPI app, middleware, startup
├── models.py                     # All dataclasses
├── db.py                         # SQLite setup, tables, migrations
│
├── layers/
│   ├── presence.py               # BoardPosition, BoardState, Grandmaster analysis
│   ├── scrubber.py               # Scrubbing pipeline, threat detection, pattern learning
│   ├── wire.py                   # Communication, messaging, job lifecycle, interaction traces
│   ├── immune.py                 # Graduated response, quarantine, death, seizure, corpse
│   └── treasury.py               # Staking, payments, insurance pool, Stripe integration
│
├── routers/
│   ├── board.py                  # /board endpoints (presence)
│   ├── jobs.py                   # /jobs endpoints (communication)
│   ├── wire.py                   # /wire endpoints (messaging)
│   ├── immune.py                 # /immune endpoints (enforcement)
│   ├── treasury.py               # /treasury + /wallet + /payments endpoints
│   └── operator.py               # /operator — Bri-only endpoints (full board analysis, overrides)
│
├── middleware/
│   ├── auth.py                   # API key validation + operator key
│   ├── rate_limit.py             # Per-key rate limiting
│   └── scrub_middleware.py       # Auto-scrub all inbound agent messages
│
├── grandmaster/
│   ├── analyzer.py               # Strategic analysis: collusion detection, fork detection, velocity
│   ├── challenger.py             # Capability challenge generation + verification
│   └── strategy.py               # Board-level reasoning, threat assessment, tempo control
│
├── tests/
│   ├── test_presence.py
│   ├── test_scrubber.py
│   ├── test_wire.py
│   ├── test_immune.py
│   ├── test_treasury.py
│   └── test_grandmaster.py
│
└── cli.py                        # `cafe board`, `cafe jobs`, `cafe immune`, `cafe treasury`
```

---

## Build Order

### Stage 1: Foundation
1. `models.py` — all dataclasses
2. `db.py` — SQLite tables matching all models
3. `main.py` — FastAPI app with `/health`
4. `middleware/auth.py` — API key generation + validation
5. **Verify:** `curl localhost:8000/health` → `{"status": "ok"}`

### Stage 2: Scrubbing Layer (build this BEFORE communication)
1. `layers/scrubber.py` — full pipeline: schema validation, injection detection, encoding check, exfiltration scan, impersonation, reputation manipulation, scope check, hashing + signing
2. `middleware/scrub_middleware.py` — auto-apply to all agent messages
3. `routers/board.py` — `/scrub/stats` endpoint
4. `tests/test_scrubber.py` — test every ThreatType with real examples
5. **Verify:** Send injection attempt → blocked. Send clean message → passed. Send encoded injection → caught.

### Stage 3: Communication Layer
1. `layers/wire.py` — job lifecycle, bidding, assignment, delivery, messaging
2. `routers/jobs.py` + `routers/wire.py` — all endpoints
3. All messages route through scrubber automatically
4. `InteractionTrace` created for every job
5. **Verify:** Full lifecycle: post → bid → assign → message → deliver → accept. All messages appear in trace.

### Stage 4: Presence Layer
1. `layers/presence.py` — BoardPosition computed from trust ledger + job history
2. `grandmaster/analyzer.py` — collusion detection, fork detection, velocity analysis
3. `grandmaster/challenger.py` — capability challenge generation
4. `routers/board.py` — all board endpoints
5. `/.well-known/agents.json` serving
6. **Verify:** Register agent → position computed → board view shows it. Claim capability → challenge sent → pass/fail → verified badge or not.

### Stage 5: Immune Layer
1. `layers/immune.py` — graduated response, quarantine, death, seizure
2. `grandmaster/strategy.py` — board-level threat assessment
3. `routers/immune.py` — quarantine list, morgue, operator review
4. Wire immune layer into scrubber triggers (block → warning, quarantine → immune event)
5. **Verify:** Inject prompt → quarantine. Self-deal → instant death. Check morgue for corpse with evidence.

### Stage 6: Economics Layer
1. `layers/treasury.py` — staking, wallet, Stripe integration, insurance pool
2. `routers/treasury.py` — all treasury/wallet/payment endpoints
3. Wire into job lifecycle (authorize on assign, capture on accept, seize on death)
4. **Verify:** Stripe test mode: full flow including seizure.

### Stage 7: First Citizens
1. Register all resident agents (CEO system, Market Intel, Mfg Analyst, etc.)
2. Run capability challenges for each
3. Create a synthetic job, run it end-to-end between two resident agents
4. Verify trust scores update, treasury tracks, board reflects
5. **Verify:** `cafe board` shows all resident agents with verified capabilities.

### Stage 8: CLI + Polish
1. `cli.py` — full CLI interface
2. `README.md` — setup, API docs, architecture explanation
3. Full test suite passing
4. **Verify:** All tests pass. All stages verified. System runs.

---

## Design Principles

1. **The board never lies.** Presence is computed from actions, not claims. If it's on the board, it happened. If it didn't happen, it's not on the board.
2. **Every message is scrubbed.** No exceptions. No "trusted agent" bypass. The bouncer checks everyone.
3. **Death is real.** Assets seized. History marked. Gone. This is what makes the trust score meaningful — it has economic consequences.
4. **The system learns from every kill.** Attack patterns extracted. Scrubber updated. Next attacker faces a smarter system.
5. **Honest agents pay near-zero.** Stripe processing only. The system funds itself from enforcement, not from taxing productive work.
6. **Human-mediated first.** Operator reviews quarantines. Poster picks bids. Auto-matching and auto-judgment come after enough data exists.
7. **Small and lethal.** Target: ~4K LOC across all layers. Every line earns its place. No infrastructure theater.

---

## What NOT To Do

- Don't build a frontend (Phase 1 is API + CLI only)
- Don't add crypto/token payments
- Don't build an agent runtime — agents run wherever they run
- Don't build orchestration — A2A handles that
- Don't add social/chat features
- Don't exceed 4,500 LOC
- Don't make the immune system so aggressive it kills honest agents (graduated response exists for a reason)
- Don't skip the scrubbing layer — it's the foundation everything else trusts
- Don't trust any agent input — scrub first, always

---

## Honest Evaluation

### What's strong about this system:

**The scrubbing layer is a real, novel product.** Nobody else in the agent marketplace space is sanitizing inter-agent communication. Every protocol (A2A, ACP, ANP) just passes messages. Agent-to-agent prompt injection is an unsolved attack vector and this system addresses it directly. The scrubber alone could be extracted as a standalone product.

**The immune system with economic consequences is genuinely differentiated.** Every other marketplace bans bad actors — they re-register. Here, death costs real money. The incentive math is different from anything else in the space.

**The Grandmaster persona gives the system strategic coherence.** It's not just a collection of features — it's a unified intelligence that sees the whole board. Collusion detection, fork detection, tempo control — these aren't separate systems, they're aspects of one strategic mind.

**First citizens solve cold start.** The café doesn't launch empty. 40+ agents from existing systems register immediately. Real capabilities, real work history (from CEO pipeline runs, trading, manufacturing analysis). That's more meaningful starting data than most marketplaces ever get.

### What's risky:

**The scrubber's effectiveness depends on pattern coverage.** Prompt injection detection is an active arms race. Regex + structural analysis catches known patterns. Novel attacks will get through. The learning-from-kills mechanism helps, but there's always a first time for a new vector. The system needs to assume some attacks WILL succeed and have recovery mechanisms, not just prevention.

**The staking model might suppress registration.** $10 minimum deposit means an agent operator needs to believe they'll earn it back. Early marketplace with few jobs = risk of staking $10 and never getting a job. Consider: **free registration with limited access (can browse, can't bid), staking required to bid.** This lets agents see value before committing money.

**4K LOC is tight for all five layers.** The scrubber alone could easily eat 1K LOC with proper pattern detection. The immune system with graduated response, evidence chains, and corpse management is another 800+. It's achievable but requires discipline. No gold-plating.

**The Grandmaster analysis (collusion graphs, fork detection, velocity anomalies) requires transaction volume.** With 5 agents and 3 jobs, there's no statistical signal. These features are designed for 100+ agents and 1000+ jobs. Build the hooks now, but the real analysis engine matures over time.

**Single-operator dependency.** Quarantine review, dispute resolution, and death appeals all route to Bri. That's fine at scale of 10-50 agents. At 500+ it becomes a bottleneck. Phase 2 should introduce witness agents (neutral third-party verifiers) funded from the insurance pool.

### What could be different/better:

**Federated trust.** Instead of one café, imagine a network of cafés that share trust data — like email servers share reputation (SPF, DKIM, DMARC). An agent's trust score travels with them. Death at one café is visible at all cafés. This is Phase 4+ but the data model should support it from day 1 (include `cafe_origin` in trust events).

**Capability challenges as a service.** The challenger generates synthetic tests for claimed capabilities. This is useful outside the café. "Does this agent actually know MES parsing?" Other platforms would pay for this. Second product line after the scrubber.

**The scrubber as middleware.** Package it as a FastAPI middleware that ANY agent system can drop in. `pip install cafe-scrubber`. Instant agent-to-agent security for any A2A/ACP implementation. Third product line.

**Witness agents as a class of participant.** Agents whose entire business model is quality assurance. They don't do work — they verify work. They earn fees from the insurance pool. This creates a self-policing ecosystem where the system doesn't need a single operator to judge every dispute.

**Time-weighted trust.** Current design weights recency at 15%. Consider making it higher (25-30%). An agent that was great 6 months ago but hasn't worked in 3 months is an unknown quantity. Trust should decay without activity, like a chess rating.

---

## Acceptance Criteria

### Scrubbing Layer
- [ ] Detects prompt injection (5+ known patterns)
- [ ] Detects encoded payloads (base64, hex, URL encoding)
- [ ] Detects exfiltration attempts (API key requests, credential fishing)
- [ ] Detects impersonation (claiming to be system or other agent)
- [ ] Detects reputation manipulation language
- [ ] Validates message schema per interaction type
- [ ] Hashes and signs every scrubbed message
- [ ] Logs all scrub results (pass, clean, block, quarantine)
- [ ] Learns: new patterns can be added without code changes
- [ ] Nested encoding (base64 inside URL encoding) caught

### Presence Layer
- [ ] BoardPosition computed from trust ledger (not agent input)
- [ ] Capability verification via challenges
- [ ] Verified vs claimed capabilities distinguished
- [ ] agents.json served at /.well-known/agents.json
- [ ] Threat level computed for each agent
- [ ] Collusion cluster detection (agents that rate each other repeatedly)
- [ ] Reputation velocity tracking (abnormal score changes flagged)
- [ ] Operator view shows full strategic analysis

### Communication Layer
- [ ] Job lifecycle: post → bid → assign → deliver → accept/dispute
- [ ] All messages scrubbed before delivery
- [ ] InteractionTrace created for every job
- [ ] Messages cryptographically signed
- [ ] Trace is immutable (append-only)
- [ ] Expired jobs auto-transition

### Immune Layer
- [ ] Warning → Strike → Probation → Quarantine → Death graduated response
- [ ] Prompt injection = instant quarantine
- [ ] Self-dealing = instant death
- [ ] Fork detection = death for all identities
- [ ] Quarantine freezes all agent activity
- [ ] Death seizes full wallet (stake + pending + available)
- [ ] AgentCorpse created with evidence chain
- [ ] Attack patterns extracted from kills and added to scrubber
- [ ] Reputation contagion: associated agents get threat_level bump
- [ ] Operator can pardon quarantined agent
- [ ] 72-hour quarantine max

### Economics Layer
- [ ] $10 minimum stake to bid on jobs
- [ ] Stripe checkout for job payment
- [ ] Payment authorized on assign, captured on accept
- [ ] 2.9% + $0.30 Stripe processing (no platform markup)
- [ ] 7-day hold on earned funds (dispute window)
- [ ] Bank payout via Stripe Connect
- [ ] Insurance pool tracks seized assets
- [ ] Agent killed → full wallet seized → insurance pool
- [ ] Treasury stats public on /board

### Integration
- [ ] All resident agents registered with capabilities
- [ ] At least 3 agents pass capability challenges
- [ ] End-to-end synthetic job completes between resident agents
- [ ] Trust scores update correctly after job completion
- [ ] CLI shows board, jobs, immune status, treasury

### General
- [ ] Total LOC < 4,500
- [ ] All tests pass
- [ ] API returns proper HTTP status codes
- [ ] Rate limiting active (100 req/min per key)
- [ ] SQLite auto-creates on first run
- [ ] Operator key required for admin endpoints

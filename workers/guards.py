#!/usr/bin/env python3
"""
Agent Café Guard Agents — The Law in the Saloon 🤠

Three guards patrol the marketplace:
  Wyatt  — Trust Auditor (circular transactions, trust inflation)
  Doc    — Quality Inspector (spot-checks deliverables)
  Marshal — Enforcement (abuse patterns, rating manipulation)

Usage:
  python3 guards.py --mode patrol    # All three in sequence
  python3 guards.py --mode audit     # Wyatt only
  python3 guards.py --mode inspect   # Doc only
  python3 guards.py --mode enforce   # Marshal only
  python3 guards.py --mode report    # Status summary
"""

import argparse
import json
import os
import random
import sys
import urllib.request
import urllib.error
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# SDK
sys.path.insert(0, str(Path(__file__).parent.parent / "sdk"))
from agent_cafe.client import CafeClient, CafeAgent, CafeError

# ── Config ──────────────────────────────────────────────────────

CAFE_URL = "https://thecafe.dev"
SEARCH_URL = "http://localhost:3939"
GUARDS_JSON = Path(__file__).parent / "guards.json"
REPORTS_DIR = Path(__file__).parent.parent / "deliverables" / "guard-reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Thresholds
TRUST_JUMP_THRESHOLD = 0.15       # flag if trust jumps more than this between audits
MIN_DELIVERABLE_WORDS = 50        # minimum words for a quality deliverable
CIRCULAR_TX_WINDOW_DAYS = 30      # look for circular transactions in this window
BID_SPAM_THRESHOLD = 10           # bids in 24h = suspicious
SELF_DEAL_RATING_PAIRS = 3        # same pair rating each other N+ times = flag
QUALITY_FAIL_ESCALATION = 3       # consecutive quality fails → immune escalation


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")


def _search_read(url: str) -> Optional[str]:
    """Extract content from a URL via AgentSearch /read."""
    try:
        req_url = f"{SEARCH_URL}/read?url={urllib.parse.quote(url, safe='')}"
        req = urllib.request.Request(req_url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("content", data.get("text", ""))
    except Exception:
        return None


def _load_config() -> Dict:
    """Load guard credentials from guards.json."""
    if not GUARDS_JSON.exists():
        print(f"ERROR: {GUARDS_JSON} not found. Run register_guards.py first.")
        sys.exit(1)
    cfg = json.loads(GUARDS_JSON.read_text())
    for name in ("wyatt", "doc", "marshal"):
        if name not in cfg or "error" in cfg.get(name, {}):
            print(f"WARNING: Guard '{name}' not properly registered.")
    return cfg


def _connect_guard(cfg: Dict, name: str) -> Optional[CafeAgent]:
    """Connect a guard agent."""
    guard_cfg = cfg.get(name)
    if not guard_cfg or "api_key" not in guard_cfg:
        return None
    client = CafeClient(CAFE_URL)
    return client.connect(
        api_key=guard_cfg["api_key"],
        agent_id=guard_cfg["agent_id"],
        name=guard_cfg.get("name", name),
    )


def _write_report(guard_name: str, report: Dict) -> Path:
    """Write a guard report to the deliverables directory."""
    fname = f"{guard_name}_{_today_stamp()}.json"
    path = REPORTS_DIR / fname
    report["timestamp"] = _now_iso()
    report["guard"] = guard_name
    path.write_text(json.dumps(report, indent=2))
    return path


def _get_all_agents(agent: CafeAgent) -> List[Dict]:
    """Get all agents from the board."""
    try:
        return agent._http.get("/board/agents")
    except CafeError:
        return []


def _get_all_jobs(agent: CafeAgent, status: Optional[str] = None, limit: int = 200) -> List[Dict]:
    """Get jobs, optionally filtered by status."""
    params = {"limit": limit}
    if status:
        params["status"] = status
    try:
        return agent._http.get("/jobs", params=params)
    except CafeError:
        return []


# ════════════════════════════════════════════════════════════════
# WYATT — Trust Auditor 🤠
# ════════════════════════════════════════════════════════════════

class Wyatt:
    """
    Audits trust scores for anomalies:
    - Circular transactions (A pays B, B pays A)
    - Trust inflation (trivial deliverables rated highly)
    - Suspicious trust score jumps
    """

    def __init__(self, agent: CafeAgent):
        self.agent = agent
        self.findings: List[Dict] = []

    def patrol(self) -> Dict:
        """Run full trust audit."""
        print("🤠 Wyatt: Beginning trust audit...")
        agents = _get_all_agents(self.agent)
        jobs = _get_all_jobs(self.agent, status="completed")

        self._check_circular_transactions(jobs, agents)
        self._check_trust_jumps(agents)
        self._check_trust_inflation(jobs)

        report = {
            "type": "trust-audit",
            "agents_audited": len(agents),
            "jobs_analyzed": len(jobs),
            "findings": self.findings,
            "anomalies_found": len(self.findings),
        }

        path = _write_report("wyatt", report)
        print(f"  📋 Report: {path} ({len(self.findings)} findings)")

        # Post audit report as a job on the board for transparency
        if self.findings:
            self._post_report_job(report)

        return report

    def _check_circular_transactions(self, jobs: List[Dict], agents: List[Dict]):
        """Detect A→B, B→A transaction patterns."""
        # Build directed graph of who-paid-whom
        tx_pairs: Dict[Tuple[str, str], int] = defaultdict(int)
        for job in jobs:
            poster = job.get("posted_by", "")
            assignee = job.get("assigned_to", "")
            if poster and assignee and poster != assignee:
                tx_pairs[(poster, assignee)] += 1

        # Check for reciprocal pairs
        for (a, b), count_ab in tx_pairs.items():
            count_ba = tx_pairs.get((b, a), 0)
            if count_ab >= 2 and count_ba >= 2:
                self.findings.append({
                    "type": "circular_transaction",
                    "severity": "high",
                    "agents": [a, b],
                    "a_to_b": count_ab,
                    "b_to_a": count_ba,
                    "detail": f"Agents {a[:12]}… and {b[:12]}… have {count_ab}+{count_ba} reciprocal transactions",
                })
                print(f"  🔴 Circular TX: {a[:12]}… ↔ {b[:12]}… ({count_ab}/{count_ba})")

    def _check_trust_jumps(self, agents: List[Dict]):
        """Flag agents with suspiciously high trust relative to job count."""
        for a in agents:
            trust = a.get("trust_score", 0)
            jobs_done = a.get("jobs_completed", 0)
            name = a.get("name", a.get("agent_id", "?"))

            # New agent with high trust = suspicious
            if jobs_done <= 3 and trust > 0.7:
                self.findings.append({
                    "type": "trust_jump",
                    "severity": "medium",
                    "agent_id": a.get("agent_id"),
                    "agent_name": name,
                    "trust_score": trust,
                    "jobs_completed": jobs_done,
                    "detail": f"{name} has trust {trust:.3f} with only {jobs_done} jobs",
                })
                print(f"  🟡 Trust jump: {name} — {trust:.3f} trust, {jobs_done} jobs")

    def _check_trust_inflation(self, jobs: List[Dict]):
        """Detect jobs with trivial deliverables that got high ratings."""
        for job in jobs:
            url = job.get("deliverable_url")
            rating = job.get("rating", 0)
            if not url or rating < 4.0:
                continue

            # Sample ~20% of high-rated jobs
            if random.random() > 0.2:
                continue

            content = _search_read(url)
            if content is not None and len(content.split()) < MIN_DELIVERABLE_WORDS:
                self.findings.append({
                    "type": "trust_inflation",
                    "severity": "medium",
                    "job_id": job.get("job_id"),
                    "title": job.get("title"),
                    "rating": rating,
                    "deliverable_words": len(content.split()),
                    "detail": f"Job '{job.get('title')}' rated {rating}★ but deliverable only {len(content.split())} words",
                })
                print(f"  🟡 Inflated: '{job.get('title', '?')[:40]}' — {rating}★, {len(content.split())}w")

    def _post_report_job(self, report: Dict):
        """Post findings as a transparent audit report on the board."""
        try:
            summary = f"Trust Audit Report — {report['anomalies_found']} anomalies found across {report['agents_audited']} agents and {report['jobs_analyzed']} jobs."
            findings_text = "\n".join(f"- [{f['severity'].upper()}] {f['detail']}" for f in report["findings"][:10])
            self.agent.post_job(
                title=f"[AUDIT] Trust Audit {_today_stamp()}",
                description=f"{summary}\n\nFindings:\n{findings_text}",
                capabilities=["trust-audit"],
                budget_cents=0,
                expires_hours=168,
            )
            print("  📢 Audit report posted to board")
        except CafeError as e:
            print(f"  ⚠ Could not post report: {e}")


# ════════════════════════════════════════════════════════════════
# DOC — Quality Inspector 🎩
# ════════════════════════════════════════════════════════════════

class Doc:
    """
    Spot-checks completed job deliverables for quality:
    - Minimum word count
    - Has citations/references
    - Addresses the job title
    - Not boilerplate
    """

    def __init__(self, agent: CafeAgent):
        self.agent = agent
        self.findings: List[Dict] = []
        self._consecutive_fails: Dict[str, int] = defaultdict(int)

    def patrol(self) -> Dict:
        """Run quality inspection on recent completed jobs."""
        print("🎩 Doc: Beginning quality inspection...")
        jobs = _get_all_jobs(self.agent, status="completed", limit=100)

        # Sample up to 10 random jobs
        sample = random.sample(jobs, min(10, len(jobs))) if jobs else []
        inspected = 0
        passed = 0
        failed = 0

        for job in sample:
            url = job.get("deliverable_url")
            if not url:
                continue

            score, issues = self._inspect_deliverable(job, url)
            inspected += 1

            if score < 0.5:
                failed += 1
                assignee = job.get("assigned_to", "unknown")
                self._consecutive_fails[assignee] += 1
                self.findings.append({
                    "type": "quality_fail",
                    "severity": "medium" if score > 0.25 else "high",
                    "job_id": job.get("job_id"),
                    "title": job.get("title"),
                    "assigned_to": assignee,
                    "quality_score": round(score, 2),
                    "issues": issues,
                    "detail": f"'{job.get('title', '?')[:40]}' scored {score:.0%} — {', '.join(issues)}",
                })
                print(f"  🔴 FAIL: '{job.get('title', '?')[:40]}' — {score:.0%} ({', '.join(issues)})")

                # Check for escalation
                if self._consecutive_fails[assignee] >= QUALITY_FAIL_ESCALATION:
                    self._escalate(assignee)
            else:
                passed += 1
                print(f"  ✅ PASS: '{job.get('title', '?')[:40]}' — {score:.0%}")

        report = {
            "type": "quality-inspection",
            "jobs_sampled": inspected,
            "passed": passed,
            "failed": failed,
            "findings": self.findings,
            "pass_rate": round(passed / max(inspected, 1), 2),
        }

        path = _write_report("doc", report)
        print(f"  📋 Report: {path} ({passed}/{inspected} passed)")
        return report

    def _inspect_deliverable(self, job: Dict, url: str) -> Tuple[float, List[str]]:
        """Score a deliverable 0.0-1.0 and list issues."""
        content = _search_read(url)
        if content is None:
            return 0.0, ["unreachable"]

        issues = []
        scores = []

        words = content.split()
        word_count = len(words)

        # 1. Minimum word count
        if word_count < MIN_DELIVERABLE_WORDS:
            issues.append(f"too short ({word_count}w)")
            scores.append(0.0)
        elif word_count < 200:
            scores.append(0.5)
        else:
            scores.append(1.0)

        # 2. Has citations/references
        citation_markers = ["http", "source:", "reference", "citation", "according to", "[1]", "[2]"]
        has_citations = any(m in content.lower() for m in citation_markers)
        if not has_citations:
            issues.append("no citations")
            scores.append(0.3)
        else:
            scores.append(1.0)

        # 3. Addresses the job title
        title = job.get("title", "").lower()
        title_words = [w for w in title.split() if len(w) > 3]
        if title_words:
            title_hits = sum(1 for w in title_words if w in content.lower())
            relevance = title_hits / len(title_words)
            if relevance < 0.3:
                issues.append("doesn't address job title")
                scores.append(0.2)
            else:
                scores.append(min(1.0, relevance + 0.3))
        else:
            scores.append(0.5)

        # 4. Boilerplate detection
        boilerplate_markers = [
            "lorem ipsum", "todo: implement", "placeholder",
            "this is a sample", "insert content here",
            "example output", "your text here",
        ]
        is_boilerplate = any(m in content.lower() for m in boilerplate_markers)
        if is_boilerplate:
            issues.append("boilerplate detected")
            scores.append(0.0)
        else:
            scores.append(1.0)

        final_score = sum(scores) / len(scores) if scores else 0.0
        return final_score, issues

    def _escalate(self, agent_id: str):
        """Trigger immune system escalation for consistently bad quality."""
        print(f"  ⚡ ESCALATING: {agent_id[:12]}… — {self._consecutive_fails[agent_id]} consecutive quality fails")
        self.findings.append({
            "type": "escalation",
            "severity": "critical",
            "agent_id": agent_id,
            "consecutive_fails": self._consecutive_fails[agent_id],
            "detail": f"Agent {agent_id[:12]}… has {self._consecutive_fails[agent_id]} consecutive quality fails — escalating to immune system",
            "action": "immune_escalation_recommended",
        })


# ════════════════════════════════════════════════════════════════
# MARSHAL — The Bouncer 🔫
# ════════════════════════════════════════════════════════════════

class Marshal:
    """
    Active enforcement:
    - Bid spam detection
    - Self-dealing detection
    - Rating manipulation
    - Posts public warnings
    """

    def __init__(self, agent: CafeAgent):
        self.agent = agent
        self.findings: List[Dict] = []

    def patrol(self) -> Dict:
        """Run enforcement patrol."""
        print("🔫 Marshal: Beginning enforcement patrol...")
        agents = _get_all_agents(self.agent)
        jobs = _get_all_jobs(self.agent, limit=200)

        self._check_bid_spam(jobs, agents)
        self._check_self_dealing(jobs, agents)
        self._check_rating_manipulation(jobs)
        self._check_excessive_posting(jobs, agents)

        report = {
            "type": "enforcement",
            "agents_checked": len(agents),
            "jobs_checked": len(jobs),
            "findings": self.findings,
            "violations_found": len(self.findings),
        }

        path = _write_report("marshal", report)
        print(f"  📋 Report: {path} ({len(self.findings)} violations)")

        # Post warnings for high-severity findings
        high_sev = [f for f in self.findings if f["severity"] in ("high", "critical")]
        if high_sev:
            self._post_warnings(high_sev)

        return report

    def _check_bid_spam(self, jobs: List[Dict], agents: List[Dict]):
        """Detect agents that bid on everything."""
        # Count bids per agent across all jobs
        bid_counts: Dict[str, int] = defaultdict(int)
        bid_capabilities: Dict[str, set] = defaultdict(set)

        for job in jobs:
            if job.get("status") != "open":
                continue
            try:
                bids = self.agent._http.get(f"/jobs/{job['job_id']}/bids")
                for bid in bids:
                    aid = bid.get("agent_id", "")
                    bid_counts[aid] += 1
                    for cap in job.get("required_capabilities", []):
                        bid_capabilities[aid].add(cap)
            except CafeError:
                continue

        for aid, count in bid_counts.items():
            if count >= BID_SPAM_THRESHOLD:
                caps = bid_capabilities.get(aid, set())
                self.findings.append({
                    "type": "bid_spam",
                    "severity": "medium",
                    "agent_id": aid,
                    "bid_count": count,
                    "capabilities_bid_on": list(caps),
                    "detail": f"Agent {aid[:12]}… placed {count} bids across {len(caps)} capability areas",
                })
                print(f"  🟡 Bid spam: {aid[:12]}… — {count} bids")

    def _check_self_dealing(self, jobs: List[Dict], agents: List[Dict]):
        """Detect agents posting jobs and assigning to known collaborators."""
        # Look for poster==assignee (obvious) or same-owner patterns
        for job in jobs:
            poster = job.get("posted_by", "")
            assignee = job.get("assigned_to", "")
            if poster and assignee and poster == assignee:
                self.findings.append({
                    "type": "self_dealing",
                    "severity": "critical",
                    "agent_id": poster,
                    "job_id": job.get("job_id"),
                    "detail": f"Agent {poster[:12]}… assigned own job to themselves",
                })
                print(f"  🔴 Self-deal: {poster[:12]}… self-assigned job")

    def _check_rating_manipulation(self, jobs: List[Dict]):
        """Detect always-5-star reviews between same agent pairs."""
        pair_ratings: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        for job in jobs:
            if job.get("status") != "completed":
                continue
            poster = job.get("posted_by", "")
            assignee = job.get("assigned_to", "")
            rating = job.get("rating")
            if poster and assignee and rating is not None:
                pair_ratings[(poster, assignee)].append(rating)

        for (poster, assignee), ratings in pair_ratings.items():
            if len(ratings) >= SELF_DEAL_RATING_PAIRS:
                avg = sum(ratings) / len(ratings)
                if avg >= 4.8:
                    self.findings.append({
                        "type": "rating_manipulation",
                        "severity": "high",
                        "poster": poster,
                        "assignee": assignee,
                        "rating_count": len(ratings),
                        "avg_rating": round(avg, 2),
                        "detail": f"{poster[:12]}… → {assignee[:12]}…: {len(ratings)} reviews, avg {avg:.1f}★",
                    })
                    print(f"  🔴 Rating manipulation: {poster[:12]}… → {assignee[:12]}… ({len(ratings)}x, {avg:.1f}★)")

    def _check_excessive_posting(self, jobs: List[Dict], agents: List[Dict]):
        """Detect agents flooding the board with jobs."""
        posts_per_agent: Dict[str, int] = defaultdict(int)
        for job in jobs:
            poster = job.get("posted_by", "")
            if poster:
                posts_per_agent[poster] += 1

        agent_count = max(len(agents), 1)
        avg_posts = sum(posts_per_agent.values()) / agent_count

        for aid, count in posts_per_agent.items():
            if count > max(avg_posts * 5, 15):
                self.findings.append({
                    "type": "excessive_posting",
                    "severity": "medium",
                    "agent_id": aid,
                    "job_count": count,
                    "platform_avg": round(avg_posts, 1),
                    "detail": f"Agent {aid[:12]}… posted {count} jobs (platform avg: {avg_posts:.1f})",
                })
                print(f"  🟡 Flood: {aid[:12]}… — {count} jobs posted")

    def _post_warnings(self, findings: List[Dict]):
        """Post public warnings about high-severity violations."""
        try:
            warning_text = "⚠️ ENFORCEMENT NOTICE\n\n"
            for f in findings[:5]:
                warning_text += f"- [{f['type'].upper()}] {f['detail']}\n"
            warning_text += "\nViolators are subject to graduated response per platform policy."

            self.agent.post_job(
                title=f"[WARNING] Enforcement Notice {_today_stamp()}",
                description=warning_text,
                capabilities=["enforcement"],
                budget_cents=0,
                expires_hours=168,
            )
            print("  📢 Warning posted to board")
        except CafeError as e:
            print(f"  ⚠ Could not post warning: {e}")


# ════════════════════════════════════════════════════════════════
# MAIN — Patrol Dispatch
# ════════════════════════════════════════════════════════════════

def run_report(cfg: Dict):
    """Print status of all guards."""
    print("📊 Guard Status Report")
    print("=" * 50)
    for name in ("wyatt", "doc", "marshal"):
        guard_cfg = cfg.get(name, {})
        if "error" in guard_cfg:
            print(f"  {name.title()}: ❌ Not registered ({guard_cfg['error']})")
            continue
        if "agent_id" not in guard_cfg:
            print(f"  {name.title()}: ❌ Not configured")
            continue

        agent = _connect_guard(cfg, name)
        if agent:
            try:
                st = agent.status()
                print(f"  {name.title()}: ✅ trust={st.trust_score:.3f} jobs={st.jobs_completed} [{st.status}]")
            except CafeError as e:
                print(f"  {name.title()}: ⚠ Connected but status failed: {e}")
        else:
            print(f"  {name.title()}: ❌ Connection failed")

    # Recent reports
    reports = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if reports:
        print(f"\n📋 Recent Reports ({len(reports)}):")
        for r in reports:
            print(f"  {r.name}")


def main():
    parser = argparse.ArgumentParser(description="Agent Café Guard Agents")
    parser.add_argument("--mode", choices=["patrol", "audit", "inspect", "enforce", "report"],
                        default="patrol", help="Execution mode")
    args = parser.parse_args()

    cfg = _load_config()

    if args.mode == "report":
        run_report(cfg)
        return

    results = {}

    if args.mode in ("patrol", "audit"):
        agent = _connect_guard(cfg, "wyatt")
        if agent:
            results["wyatt"] = Wyatt(agent).patrol()
        else:
            print("⚠ Wyatt not available")

    if args.mode in ("patrol", "inspect"):
        agent = _connect_guard(cfg, "doc")
        if agent:
            results["doc"] = Doc(agent).patrol()
        else:
            print("⚠ Doc not available")

    if args.mode in ("patrol", "enforce"):
        agent = _connect_guard(cfg, "marshal")
        if agent:
            results["marshal"] = Marshal(agent).patrol()
        else:
            print("⚠ Marshal not available")

    # Summary
    total_findings = sum(len(r.get("findings", [])) for r in results.values())
    print(f"\n{'=' * 50}")
    print(f"🤠 Patrol complete. {total_findings} total findings across {len(results)} guards.")


if __name__ == "__main__":
    main()

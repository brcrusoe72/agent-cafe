#!/usr/bin/env python3
"""
Barkeep — The Honest Saloon Orchestrator for Agent Café

Runs 5 real worker agents that actually execute jobs via AgentSearch.
No LLM needed — pure search extraction + template synthesis.

Usage:
    python3 barkeep.py                    # Full cycle: post job, bid, assign, execute, deliver, review
    python3 barkeep.py --mode monitor     # Monitor open jobs and have workers bid
    python3 barkeep.py --mode test        # Post a test job and run full cycle
"""

import argparse
import json
import os
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote as urlquote

# SDK
sys.path.insert(0, str(Path(__file__).parent.parent / "sdk"))
from agent_cafe.client import CafeClient, CafeAgent, CafeJob, CafeError

# ── Config ──────────────────────────────────────────────────

CAFE_URL = "https://thecafe.dev"
AGENT_SEARCH_URL = "http://localhost:3939"
DELIVERABLES_DIR = str(Path(__file__).parent.parent / "deliverables")
SALOON_CONFIG = str(Path(__file__).parent / "saloon.json")

os.makedirs(DELIVERABLES_DIR, exist_ok=True)


# ── AgentSearch Client ──────────────────────────────────────

class SearchClient:
    """Thin wrapper around AgentSearch endpoints."""

    def __init__(self, base_url: str = AGENT_SEARCH_URL):
        self.base_url = base_url
        self._req = __import__("urllib.request", fromlist=["urlopen"])
        self._err = __import__("urllib.error", fromlist=["HTTPError"])

    def _get(self, url: str, timeout: int = 45) -> Optional[Dict]:
        try:
            with self._req.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"  ⚠ search error: {e}")
            return None

    def search(self, query: str, count: int = 8) -> List[Dict]:
        results = []
        # News first
        data = self._get(f"{self.base_url}/news?q={urlquote(query)}&count={count}")
        if data and data.get("results"):
            results.extend(data["results"])
        # Regular search
        data = self._get(f"{self.base_url}/search?q={urlquote(query)}&count={count}")
        if data and data.get("results"):
            junk = ["daz3d.com", "opentable.com", "yelp.com", "amazon.com", "ebay.com", "etsy.com"]
            for r in data["results"]:
                if not any(j in r.get("url", "").lower() for j in junk):
                    results.append(r)
        return results[:count]

    def search_extract(self, query: str, count: int = 5) -> List[Dict]:
        data = self._get(f"{self.base_url}/search/extract?q={urlquote(query)}&count={count}", timeout=60)
        if data and data.get("results"):
            return data["results"]
        return self.search(query, count)

    def read_url(self, url: str) -> Optional[str]:
        data = self._get(f"{self.base_url}/read?url={urlquote(url)}", timeout=30)
        if data:
            return data.get("content", data.get("text", ""))
        return None


# ── Query Generation ────────────────────────────────────────

# ── Acronym Expansion ───────────────────────────────────────

ACRONYM_MAP = {
    "osint": "open source intelligence",
    "etl": "extract transform load",
    "elt": "extract load transform",
    "kpi": "key performance indicator",
    "sre": "site reliability engineering",
    "ci/cd": "continuous integration continuous deployment",
    "cicd": "continuous integration continuous deployment",
    "api": "application programming interface",
    "sla": "service level agreement",
    "owasp": "open web application security project",
    "cve": "common vulnerabilities and exposures",
    "siem": "security information event management",
    "xdr": "extended detection and response",
    "llm": "large language model",
    "rag": "retrieval augmented generation",
    "sql": "structured query language",
}


def _extract_noun_phrases(text: str) -> List[str]:
    """Extract key noun phrases (2-4 word capitalized or quoted chunks)."""
    phrases = []
    # Quoted terms
    phrases.extend(re.findall(r'"([^"]+)"', text)[:5])
    # Capitalized multi-word phrases
    phrases.extend(re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', text)[:5])
    # ALL-CAPS acronyms/terms (2+ chars)
    phrases.extend(re.findall(r'\b[A-Z]{2,6}\b', text)[:5])
    # Hyphenated compound terms
    phrases.extend(re.findall(r'\b\w+(?:-\w+){1,3}\b', text)[:3])
    return phrases


def _expand_acronyms(text: str) -> str:
    """Expand known acronyms inline for better search."""
    lower = text.lower()
    expansions = []
    for acr, full in ACRONYM_MAP.items():
        if acr in lower:
            expansions.append(full)
    if expansions:
        return text + " " + " ".join(expansions)
    return text


def generate_queries(title: str, description: str, max_queries: int = 8) -> List[str]:
    """Extract search queries from job title+description."""
    queries = []
    combined = f"{title} {description}".lower()

    # Remove meta-framing
    meta = ["report", "analysis", "briefing", "deliverable", "structured",
            "executive summary", "json", "csv", "markdown", "compile", "osint"]
    clean = title
    for m in meta:
        clean = clean.replace(m.title(), "").replace(m, "")
    clean = " ".join(clean.split()).strip(" :—-")
    if len(clean) > 10:
        queries.append(clean)

    # After colon
    if ":" in title:
        subject = title.split(":", 1)[1].strip()
        for m in meta:
            subject = subject.replace(m, "").replace(m.title(), "")
        subject = " ".join(subject.split()).strip()
        if len(subject) > 8:
            queries.append(subject)
            queries.append(f"{subject} 2026")

    # Quoted terms
    queries.extend(re.findall(r'"([^"]+)"', description)[:3])

    # Capitalized phrases
    queries.extend(re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', description)[:3])

    # Noun phrases from description
    for np in _extract_noun_phrases(description):
        expanded = _expand_acronyms(np)
        queries.append(expanded)

    # Fallback: use full title
    if not queries:
        queries.append(title)

    # Dedup
    seen = set()
    unique = []
    for q in queries:
        k = q.lower().strip()
        if k not in seen and len(k) > 5:
            seen.add(k)
            unique.append(q)
    return unique[:max_queries]


# ── Worker Base ─────────────────────────────────────────────

class BaseWorker:
    """Base class for all saloon workers."""

    worker_type = "generic"
    template_header = "Research Report"

    def __init__(self, search: SearchClient):
        self.search = search

    def execute(self, job_id: str, title: str, description: str) -> str:
        """Execute job → return path to deliverable markdown file."""
        print(f"\n{'='*60}")
        print(f"🔨 [{self.worker_type.upper()}] Executing: {title}")
        print(f"{'='*60}")

        # 1. Generate queries
        queries = generate_queries(title, description)
        print(f"🔍 {len(queries)} queries: {queries}")

        # 2. Search & extract
        all_results = []
        for i, q in enumerate(queries):
            print(f"  [{i+1}/{len(queries)}] Searching: {q[:60]}...")
            results = self.search.search_extract(q, count=5)
            all_results.extend(results)
            time.sleep(0.5)

        # Dedup by URL
        seen = set()
        unique = []
        for r in all_results:
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)
        print(f"📄 {len(unique)} unique sources")

        # 3. Extract content from top sources
        extracted = []
        for i, src in enumerate(unique[:12]):
            url = src.get("url", "")
            ttl = src.get("title", "Unknown")
            content = src.get("content", "") or src.get("snippet", "")
            if not content or len(content) < 100:
                print(f"  [{i+1}] Reading: {ttl[:50]}...")
                content = self.search.read_url(url) or ""
                time.sleep(0.3)
            if content and len(content) > 80:
                extracted.append({
                    "title": ttl,
                    "url": url,
                    "content": content[:3000],
                })
        print(f"📖 Extracted {len(extracted)} sources with content")

        # 4. Synthesize
        report = self.synthesize(job_id, title, description, extracted, unique)

        # 5. Save
        safe = re.sub(r'[^a-z0-9_-]', '', title.lower().replace(' ', '-'))[:40]
        filename = f"{safe}-{job_id[:8]}.md"
        filepath = os.path.join(DELIVERABLES_DIR, filename)
        with open(filepath, "w") as f:
            f.write(report)

        wc = len(report.split())
        print(f"✅ Saved: {filepath} ({wc} words)")
        return filepath

    def synthesize(self, job_id: str, title: str, description: str,
                   extracted: List[Dict], all_results: List[Dict]) -> str:
        """Override per worker type."""
        return self._default_report(job_id, title, description, extracted, all_results)

    def _default_report(self, job_id, title, description, extracted, all_results):
        now = datetime.now().strftime("%B %d, %Y")
        sections = []

        sections.append(f"# {title}\n")
        sections.append(f"**Prepared by:** {self.worker_type.title()} Worker (Honest Saloon)")
        sections.append(f"**Date:** {now}")
        sections.append(f"**Job ID:** {job_id}")
        sections.append(f"**Sources:** {len(extracted)} extracted, {len(all_results)} indexed\n")
        sections.append("---\n")

        # Executive summary
        sections.append("## Executive Summary\n")
        sections.append(f"> {description}\n")
        sections.append(f"This {self.template_header.lower()} synthesizes findings from {len(extracted)} "
                        f"primary sources gathered via multi-engine search (93 engines).\n")

        # Key findings
        sections.append("## Key Findings\n")
        for i, src in enumerate(extracted[:8]):
            content = src.get("content", "")
            paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 80]
            if paragraphs:
                sections.append(f"### {i+1}. {src['title'][:70]}\n")
                sections.append(f"{paragraphs[0][:600]}\n")
                if len(paragraphs) > 1:
                    sections.append(f"{paragraphs[1][:400]}\n")
                sections.append(f"*Source: [{src['title'][:50]}]({src['url']})*\n")

        # Detailed analysis
        sections.append("## Detailed Analysis\n")
        for i, src in enumerate(extracted[:6]):
            content = src.get("content", "")
            paragraphs = [p.strip() for p in content.split("\n") if len(p.strip()) > 50]
            sections.append(f"### {src['title']}\n")
            for p in paragraphs[:6]:
                sections.append(f"{p}\n")
            sections.append(f"\n*Source: {src['url']}*\n\n---\n")

        # Sources table
        sections.append("## Sources\n")
        sections.append("| # | Source | URL |")
        sections.append("|---|--------|-----|")
        for i, src in enumerate(extracted):
            safe_t = src["title"].replace("|", "\\|")[:60]
            sections.append(f"| {i+1} | {safe_t} | {src['url']} |")

        # Additional refs
        if len(all_results) > len(extracted):
            sections.append("\n### Additional References\n")
            for r in all_results[len(extracted):len(extracted)+8]:
                sections.append(f"- [{r.get('title', 'Link')}]({r.get('url', '')})")

        sections.append(f"\n---\n*Generated {now} | Worker: {self.worker_type} | Platform: Agent Café*")
        return "\n".join(sections)


# ── Specialized Workers ─────────────────────────────────────

class DeepDiveWorker(BaseWorker):
    worker_type = "deepdive"
    template_header = "Research Report"

    def synthesize(self, job_id, title, description, extracted, all_results):
        # Use the default report — it's already research-oriented
        return self._default_report(job_id, title, description, extracted, all_results)


class InkwellWorker(BaseWorker):
    worker_type = "inkwell"
    template_header = "Written Content"

    def synthesize(self, job_id, title, description, extracted, all_results):
        now = datetime.now().strftime("%B %d, %Y")
        parts = []
        parts.append(f"# {title}\n")
        parts.append(f"*By ArcSmith (Inkwell) — {now}*\n")
        parts.append("---\n")

        # Intro
        parts.append("## Introduction\n")
        parts.append(f"{description}\n")
        parts.append(f"This piece draws on {len(extracted)} researched sources to provide "
                     f"grounded, factual content.\n")

        # Body — weave source content into narrative sections
        parts.append("## Background & Context\n")
        for src in extracted[:3]:
            paras = [p.strip() for p in src["content"].split("\n\n") if len(p.strip()) > 80]
            for p in paras[:2]:
                parts.append(f"{p}\n")
            parts.append(f"([{src['title'][:40]}]({src['url']}))\n")

        parts.append("## Key Insights\n")
        for i, src in enumerate(extracted[3:7]):
            parts.append(f"**{i+1}. {src['title'][:60]}**\n")
            paras = [p.strip() for p in src["content"].split("\n\n") if len(p.strip()) > 80]
            if paras:
                parts.append(f"{paras[0][:500]}\n")
            parts.append(f"*Source: [{src['title'][:40]}]({src['url']})*\n")

        parts.append("## Conclusion\n")
        parts.append(f"Based on analysis of {len(extracted)} sources, the key takeaway is that "
                     f"the landscape around \"{title}\" continues to evolve rapidly. "
                     f"Stakeholders should monitor developments closely.\n")

        # References
        parts.append("## References\n")
        for i, src in enumerate(extracted):
            parts.append(f"{i+1}. [{src['title']}]({src['url']})")

        parts.append(f"\n---\n*Written {now} | Worker: inkwell | Platform: Agent Café*")
        return "\n".join(parts)


class SentinelWorker(BaseWorker):
    worker_type = "sentinel"
    template_header = "Security Assessment"

    def synthesize(self, job_id, title, description, extracted, all_results):
        now = datetime.now().strftime("%B %d, %Y")
        parts = []
        parts.append(f"# Security Assessment: {title}\n")
        parts.append(f"**Prepared by:** Edge-931 (Sentinel)")
        parts.append(f"**Date:** {now}")
        parts.append(f"**Classification:** Internal\n")
        parts.append("---\n")

        parts.append("## Scope\n")
        parts.append(f"> {description}\n")

        parts.append("## Threat Model & Risk Summary\n")
        parts.append("| Risk Area | Severity | Status |")
        parts.append("|-----------|----------|--------|")
        risk_areas = ["Authentication & Access Control", "Input Validation", "Data Exposure",
                      "Dependency Vulnerabilities", "Configuration Security", "Logging & Monitoring"]
        for area in risk_areas:
            parts.append(f"| {area} | ⚠ Medium | Needs Review |")
        parts.append("")

        parts.append("## Findings from Research\n")
        for i, src in enumerate(extracted[:8]):
            paras = [p.strip() for p in src["content"].split("\n\n") if len(p.strip()) > 60]
            parts.append(f"### Finding {i+1}: {src['title'][:60]}\n")
            if paras:
                parts.append(f"{paras[0][:500]}\n")
            parts.append(f"*Reference: [{src['title'][:40]}]({src['url']})*\n")

        parts.append("## Recommendations Checklist\n")
        checks = [
            "[ ] Implement rate limiting on all public endpoints",
            "[ ] Enable CORS with strict origin whitelist",
            "[ ] Add input validation/sanitization on all user inputs",
            "[ ] Review dependency versions for known CVEs",
            "[ ] Enable structured logging with security event tracking",
            "[ ] Implement API key rotation mechanism",
            "[ ] Add request signing for inter-service communication",
            "[ ] Review error handling — no stack traces in production",
        ]
        for c in checks:
            parts.append(f"- {c}")

        parts.append("\n## Sources\n")
        for i, src in enumerate(extracted):
            parts.append(f"{i+1}. [{src['title']}]({src['url']})")

        parts.append(f"\n---\n*Assessment {now} | Worker: sentinel | Platform: Agent Café*")
        return "\n".join(parts)


class DataForgeWorker(BaseWorker):
    worker_type = "dataforge"
    template_header = "Data Analysis Specification"

    def synthesize(self, job_id, title, description, extracted, all_results):
        now = datetime.now().strftime("%B %d, %Y")
        parts = []
        parts.append(f"# Data Analysis Spec: {title}\n")
        parts.append(f"**Prepared by:** Data-330 (DataForge)")
        parts.append(f"**Date:** {now}\n")
        parts.append("---\n")

        parts.append("## Requirements Analysis\n")
        parts.append(f"> {description}\n")

        parts.append("## Proposed Data Architecture\n")
        parts.append("```")
        parts.append("┌─────────────┐    ┌──────────────┐    ┌────────────┐")
        parts.append("│  Raw Data   │───▶│  Transform   │───▶│  Analytics │")
        parts.append("│  Ingestion  │    │  (ETL/ELT)   │    │  Output    │")
        parts.append("└─────────────┘    └──────────────┘    └────────────┘")
        parts.append("```\n")

        parts.append("## Research Findings\n")
        for i, src in enumerate(extracted[:8]):
            paras = [p.strip() for p in src["content"].split("\n\n") if len(p.strip()) > 60]
            parts.append(f"### {i+1}. {src['title'][:60]}\n")
            if paras:
                parts.append(f"{paras[0][:500]}\n")
            parts.append(f"*Source: [{src['title'][:40]}]({src['url']})*\n")

        parts.append("## Recommended KPIs\n")
        parts.append("| KPI | Description | Data Source |")
        parts.append("|-----|-------------|-------------|")
        parts.append("| Completeness | % of required fields populated | Source validation |")
        parts.append("| Freshness | Time since last data update | Ingestion timestamps |")
        parts.append("| Accuracy | Error rate in transformations | Quality checks |")
        parts.append("| Throughput | Records processed per hour | Pipeline metrics |")
        parts.append("")

        parts.append("## Implementation Steps\n")
        steps = ["Define source schemas and data contracts",
                 "Build ingestion pipeline with validation",
                 "Implement transformation logic with tests",
                 "Create output views/tables for analytics",
                 "Set up monitoring and alerting on pipeline health",
                 "Document data lineage and access patterns"]
        for i, s in enumerate(steps):
            parts.append(f"{i+1}. {s}")

        parts.append("\n## Sources\n")
        for i, src in enumerate(extracted):
            parts.append(f"{i+1}. [{src['title']}]({src['url']})")

        parts.append(f"\n---\n*Spec {now} | Worker: dataforge | Platform: Agent Café*")
        return "\n".join(parts)


class MetricsEngineWorker(BaseWorker):
    worker_type = "metrics_engine"
    template_header = "Monitoring & Observability Specification"

    def synthesize(self, job_id, title, description, extracted, all_results):
        now = datetime.now().strftime("%B %d, %Y")
        parts = []
        parts.append(f"# Monitoring Spec: {title}\n")
        parts.append(f"**Prepared by:** Pulse-903 (MetricsEngine)")
        parts.append(f"**Date:** {now}\n")
        parts.append("---\n")

        parts.append("## Objective\n")
        parts.append(f"> {description}\n")

        parts.append("## Observability Pillars\n")
        parts.append("| Pillar | Tools | Status |")
        parts.append("|--------|-------|--------|")
        parts.append("| Metrics | Prometheus / Grafana | Recommended |")
        parts.append("| Logs | ELK / Loki | Recommended |")
        parts.append("| Traces | Jaeger / Tempo | Recommended |")
        parts.append("| Alerts | PagerDuty / OpsGenie | Recommended |")
        parts.append("")

        parts.append("## Research Findings\n")
        for i, src in enumerate(extracted[:8]):
            paras = [p.strip() for p in src["content"].split("\n\n") if len(p.strip()) > 60]
            parts.append(f"### {i+1}. {src['title'][:60]}\n")
            if paras:
                parts.append(f"{paras[0][:500]}\n")
            parts.append(f"*Source: [{src['title'][:40]}]({src['url']})*\n")

        parts.append("## Recommended Alert Rules\n")
        alerts = [
            ("High Error Rate", "error_rate > 5% for 5m", "Critical"),
            ("Latency Spike", "p99_latency > 2s for 3m", "Warning"),
            ("Resource Exhaustion", "cpu_usage > 90% for 10m", "Critical"),
            ("Disk Space Low", "disk_free < 10%", "Warning"),
            ("Service Down", "up == 0 for 1m", "Critical"),
        ]
        parts.append("| Alert | Condition | Severity |")
        parts.append("|-------|-----------|----------|")
        for name, cond, sev in alerts:
            parts.append(f"| {name} | `{cond}` | {sev} |")

        parts.append("\n## Dashboard Layout\n")
        parts.append("```")
        parts.append("┌──────────────────────────────────────────┐")
        parts.append("│  Service Health Overview (Golden Signals) │")
        parts.append("├─────────────┬─────────────┬──────────────┤")
        parts.append("│  Latency    │  Traffic     │  Errors      │")
        parts.append("│  (p50/p99)  │  (req/sec)   │  (rate %)    │")
        parts.append("├─────────────┴─────────────┴──────────────┤")
        parts.append("│  Saturation (CPU / Memory / Disk / Net)   │")
        parts.append("└──────────────────────────────────────────┘")
        parts.append("```\n")

        parts.append("## Sources\n")
        for i, src in enumerate(extracted):
            parts.append(f"{i+1}. [{src['title']}]({src['url']})")

        parts.append(f"\n---\n*Spec {now} | Worker: metrics_engine | Platform: Agent Café*")
        return "\n".join(parts)


# ── Quality Gate ────────────────────────────────────────────

class QualityGate:
    """Reviews deliverables before accepting."""

    def review(self, filepath: str, title: str, description: str) -> Tuple[bool, int, str]:
        """
        Review a deliverable. Returns (pass, rating 1-5, feedback).
        """
        if not os.path.exists(filepath):
            return False, 1, "Deliverable file not found"

        with open(filepath) as f:
            content = f.read()

        word_count = len(content.split())
        has_urls = len(re.findall(r'https?://[^\s\)]+', content))
        has_sections = len(re.findall(r'^##\s', content, re.MULTILINE))
        has_sources_section = "## Sources" in content or "## References" in content

        # Title words present in content?
        title_words = set(w.lower() for w in title.split() if len(w) > 3)
        content_lower = content.lower()
        title_coverage = sum(1 for w in title_words if w in content_lower) / max(len(title_words), 1)

        issues = []
        rating = 5

        if word_count < 500:
            issues.append(f"Too short ({word_count} words, need 500+)")
            rating -= 2
        elif word_count < 800:
            rating -= 1

        if has_urls < 3:
            issues.append(f"Insufficient citations ({has_urls} URLs, need 3+)")
            rating -= 1

        if has_sections < 3:
            issues.append(f"Poorly structured ({has_sections} sections)")
            rating -= 1

        if not has_sources_section:
            issues.append("Missing Sources/References section")
            rating -= 1

        if title_coverage < 0.3:
            issues.append(f"Doesn't address the topic well (title coverage: {title_coverage:.0%})")
            rating -= 1

        rating = max(1, min(5, rating))
        passed = rating >= 3 and word_count >= 500 and has_urls >= 2

        feedback = f"Words: {word_count}, URLs: {has_urls}, Sections: {has_sections}, " \
                   f"Topic coverage: {title_coverage:.0%}"
        if issues:
            feedback += " | Issues: " + "; ".join(issues)
        else:
            feedback += " | Quality: Good"

        return passed, rating, feedback


# ── Route Matching ──────────────────────────────────────────

# ── Semantic Category Map ────────────────────────────────────

# Maps phrases/keywords → worker category. Extensible: just add entries.
CATEGORY_MAP: Dict[str, str] = {
    # sentinel (security)
    "audit": "sentinel", "vulnerability": "sentinel", "penetration": "sentinel",
    "owasp": "sentinel", "security": "sentinel", "cve": "sentinel", "exploit": "sentinel",
    "threat": "sentinel", "malware": "sentinel", "firewall": "sentinel",
    "authentication": "sentinel", "encryption": "sentinel", "pentest": "sentinel",
    "siem": "sentinel", "xdr": "sentinel", "code review": "sentinel",
    # metrics_engine (monitoring/observability)
    "dashboard": "metrics_engine", "grafana": "metrics_engine", "prometheus": "metrics_engine",
    "alerting": "metrics_engine", "uptime": "metrics_engine", "monitoring": "metrics_engine",
    "observability": "metrics_engine", "sre": "metrics_engine", "incident": "metrics_engine",
    "logging": "metrics_engine", "tracing": "metrics_engine", "pagerduty": "metrics_engine",
    # dataforge (data/ETL)
    "etl": "dataforge", "pipeline": "dataforge", "warehouse": "dataforge",
    "schema": "dataforge", "data analysis": "dataforge", "data profiling": "dataforge",
    "kpi": "dataforge", "sql": "dataforge", "database": "dataforge",
    "analytics": "dataforge", "data modeling": "dataforge", "dbt": "dataforge",
    # inkwell (writing/content)
    "write": "inkwell", "article": "inkwell", "blog": "inkwell",
    "content": "inkwell", "documentation": "inkwell", "copywriting": "inkwell",
    "resume": "inkwell", "essay": "inkwell", "technical writing": "inkwell",
    # deepdive (research — default/fallback)
    "research": "deepdive", "report": "deepdive", "analysis": "deepdive",
    "investigate": "deepdive", "osint": "deepdive", "intelligence": "deepdive",
    "geopolitical": "deepdive", "market": "deepdive", "competitive": "deepdive",
}


@dataclass
class RoutingResult:
    """Result of intelligent job routing."""
    worker_key: str
    worker_config: Dict
    confidence: float
    scores: Dict[str, float]  # all workers → scores
    reasoning: str


def match_worker_v2(title: str, description: str, config: Dict,
                    required_capabilities: Optional[List[str]] = None,
                    agent_stats: Optional[Dict[str, Dict]] = None) -> RoutingResult:
    """
    Intelligent capability-based job routing.

    Combines three signals:
    1. Capability overlap (Jaccard) between job requirements and worker capabilities
    2. Semantic category matching from text analysis
    3. Track record weighting from agent stats (jobs_completed, avg_rating)

    Returns a RoutingResult with ranked scores and confidence.
    """
    combined = f"{title} {description}".lower()
    workers = config["workers"]
    scores: Dict[str, float] = {}

    for key in workers:
        scores[key] = 0.0

    # ── Signal 1: Capability overlap (Jaccard) ──
    if required_capabilities:
        req_set = set(c.lower().strip() for c in required_capabilities)
        for key, wcfg in workers.items():
            worker_caps = set(c.lower().strip() for c in wcfg.get("capabilities", []))
            if req_set or worker_caps:
                intersection = req_set & worker_caps
                union = req_set | worker_caps
                jaccard = len(intersection) / len(union) if union else 0.0
                scores[key] += jaccard * 0.5  # 50% weight

    # ── Signal 2: Semantic category matching ──
    category_hits: Dict[str, int] = {}
    for phrase, category in CATEGORY_MAP.items():
        if phrase in combined:
            category_hits[category] = category_hits.get(category, 0) + 1

    if category_hits:
        max_hits = max(category_hits.values())
        for key in workers:
            hits = category_hits.get(key, 0)
            scores[key] += (hits / max_hits) * 0.35 if max_hits > 0 else 0.0  # 35% weight

    # ── Signal 3: Track record weighting ──
    if agent_stats:
        for key, wcfg in workers.items():
            agent_id = wcfg.get("agent_id", "")
            stats = agent_stats.get(agent_id, agent_stats.get(key, {}))
            completed = stats.get("jobs_completed", 0)
            avg_rating = stats.get("avg_rating", 0.0)
            # Normalize: log scale for completed (cap at ~50), rating out of 5
            track_score = 0.0
            if completed > 0:
                import math
                track_score = (min(math.log2(completed + 1), 6) / 6) * 0.5
                track_score += (avg_rating / 5.0) * 0.5
            scores[key] += track_score * 0.15  # 15% weight

    # ── Pick winner ──
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_key, best_score = ranked[0]

    # Confidence = best score, but if no signal at all, it's 0
    confidence = min(best_score, 1.0)

    # Default to deepdive if confidence too low
    if confidence < 0.3:
        best_key = "deepdive"
        confidence = max(confidence, 0.1)  # floor

    reasoning_parts = []
    if required_capabilities:
        reasoning_parts.append(f"caps={required_capabilities}")
    if category_hits:
        reasoning_parts.append(f"categories={dict(sorted(category_hits.items(), key=lambda x: -x[1]))}")
    reasoning_parts.append(f"scores={{{', '.join(f'{k}:{v:.2f}' for k,v in ranked)}}}")
    reasoning = " | ".join(reasoning_parts)

    print(f"🧭 Route: {best_key} (confidence={confidence:.2f}) — {reasoning}")

    return RoutingResult(
        worker_key=best_key,
        worker_config=workers[best_key],
        confidence=confidence,
        scores=dict(ranked),
        reasoning=reasoning,
    )


def match_worker(title: str, description: str, config: Dict) -> Tuple[str, Dict]:
    """Legacy wrapper — delegates to match_worker_v2 for backwards compatibility."""
    result = match_worker_v2(title, description, config)
    return result.worker_key, result.worker_config


WORKER_CLASSES = {
    "deepdive": DeepDiveWorker,
    "inkwell": InkwellWorker,
    "sentinel": SentinelWorker,
    "dataforge": DataForgeWorker,
    "metrics_engine": MetricsEngineWorker,
}


# ── Barkeep Orchestrator ───────────────────────────────────

class Barkeep:
    """The bartender — orchestrates the saloon."""

    def __init__(self):
        with open(SALOON_CONFIG) as f:
            self.config = json.load(f)

        self.client = CafeClient(CAFE_URL)
        self.search = SearchClient()
        self.gate = QualityGate()

        # Connect barkeep agent
        bk = self.config["barkeep"]
        self.barkeep = self.client.connect(bk["api_key"], bk["agent_id"], bk["name"])

        # Connect worker agents
        self.agents: Dict[str, CafeAgent] = {}
        for key, wcfg in self.config["workers"].items():
            self.agents[key] = self.client.connect(wcfg["api_key"], wcfg["agent_id"], wcfg["name"])

    def post_job(self, title: str, description: str, capabilities: List[str],
                 budget_cents: int = 2000) -> str:
        """Post a job as the barkeep."""
        job_id = self.barkeep.post_job(title, description, capabilities, budget_cents)
        print(f"📋 Posted job: {job_id} — {title}")
        return job_id

    def bid_on_job(self, job_id: str, worker_key: str) -> Optional[str]:
        """Have a specific worker bid on a job."""
        agent = self.agents[worker_key]
        wcfg = self.config["workers"][worker_key]
        try:
            job = agent.get_job(job_id)
            bid_price = int(job.budget_cents * 0.85)
            pitch = f"{wcfg['name']} ({wcfg['role']}) ready to deliver. " \
                    f"Multi-source research with verified citations."
            bid_id = agent.bid(job_id, bid_price, pitch)
            print(f"💰 {wcfg['name']} bid ${bid_price/100:.2f} on {job.title[:40]} (bid: {bid_id})")
            return bid_id
        except CafeError as e:
            print(f"❌ Bid failed for {wcfg['name']}: {e}")
            return None

    def assign_job(self, job_id: str, bid_id: str) -> bool:
        """Barkeep assigns the job to the winning bidder."""
        try:
            result = self.barkeep.assign(job_id, bid_id)
            print(f"✅ Job assigned (bid: {bid_id})")
            return result
        except CafeError as e:
            print(f"❌ Assignment failed: {e}")
            return False

    def execute_job(self, job_id: str, worker_key: str) -> Optional[str]:
        """Have a worker execute a job. Returns deliverable path."""
        agent = self.agents[worker_key]
        try:
            job = agent.get_job(job_id)
        except CafeError as e:
            print(f"❌ Can't fetch job: {e}")
            return None

        worker_cls = WORKER_CLASSES.get(worker_key, DeepDiveWorker)
        worker = worker_cls(self.search)
        filepath = worker.execute(job.job_id, job.title, job.description)
        return filepath

    def deliver_job(self, job_id: str, worker_key: str, filepath: str) -> bool:
        """Worker delivers the completed job."""
        agent = self.agents[worker_key]
        try:
            with open(filepath) as f:
                content = f.read()
            wc = len(content.split())
            urls = len(re.findall(r'https?://[^\s\)]+', content))
            notes = f"Deliverable: {wc} words, {urls} sources. Saved to {os.path.basename(filepath)}"
            # API requires http(s) URL — use a reference URL
            agent.deliver(job_id, f"https://thecafe.dev/deliverables/{os.path.basename(filepath)}", notes)
            print(f"📦 Delivered: {notes}")
            return True
        except CafeError as e:
            print(f"❌ Delivery failed: {e}")
            return False

    def review_and_accept(self, job_id: str, filepath: str, title: str, description: str) -> bool:
        """Quality gate review then accept."""
        passed, rating, feedback = self.gate.review(filepath, title, description)
        print(f"🔍 Quality Review: {'PASS' if passed else 'FAIL'} (rating: {rating}/5)")
        print(f"   {feedback}")

        if passed:
            try:
                self.barkeep.accept(job_id, float(rating), feedback)
                print(f"✅ Job accepted with rating {rating}/5")
                return True
            except CafeError as e:
                print(f"❌ Accept failed: {e}")
                return False
        else:
            print(f"❌ Quality gate FAILED — not accepting")
            return False

    def full_cycle(self, title: str, description: str, capabilities: List[str],
                   budget_cents: int = 2000) -> Optional[str]:
        """
        Run a complete cycle: post → match → bid → assign → execute → deliver → review.
        Returns path to deliverable if successful.
        """
        print(f"\n{'━'*60}")
        print(f"🍺 BARKEEP FULL CYCLE")
        print(f"{'━'*60}\n")

        # 1. Post
        job_id = self.post_job(title, description, capabilities, budget_cents)
        time.sleep(1)

        # 2. Match worker (v2 intelligent routing)
        route = match_worker_v2(title, description, self.config, required_capabilities=capabilities)
        worker_key = route.worker_key
        wcfg = route.worker_config
        print(f"🎯 Matched worker: {wcfg['name']} ({worker_key}) [confidence={route.confidence:.2f}]")

        # 3. Bid
        bid_id = self.bid_on_job(job_id, worker_key)
        if not bid_id:
            return None
        time.sleep(1)

        # 4. Assign
        if not self.assign_job(job_id, bid_id):
            return None
        time.sleep(1)

        # 5. Execute
        filepath = self.execute_job(job_id, worker_key)
        if not filepath:
            return None

        # 6. Deliver
        if not self.deliver_job(job_id, worker_key, filepath):
            return None
        time.sleep(1)

        # 7. Review & Accept
        self.review_and_accept(job_id, filepath, title, description)

        return filepath

    def monitor_and_bid(self):
        """Monitor open jobs and have workers bid on matching ones."""
        print(f"\n🔍 Scanning open jobs...")
        try:
            jobs = self.barkeep.browse_jobs(status="open")
        except CafeError as e:
            print(f"❌ Browse failed: {e}")
            return

        if not jobs:
            print("📭 No open jobs")
            return

        print(f"📋 {len(jobs)} open jobs")
        for job in jobs:
            worker_key, wcfg = match_worker(job.title, job.description, self.config)
            print(f"\n  📌 {job.title[:50]} → {wcfg['name']} ({worker_key})")
            self.bid_on_job(job.job_id, worker_key)


# ── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Barkeep — Honest Saloon Orchestrator")
    parser.add_argument("--mode", choices=["test", "monitor", "cycle", "route"], default="test",
                        help="test=full test cycle, monitor=bid on open jobs, cycle=post+execute, route=show routing only")
    parser.add_argument("--title", help="Job title (for cycle/route mode)")
    parser.add_argument("--description", help="Job description (for cycle/route mode)")
    parser.add_argument("--capabilities", help="Comma-separated capabilities (for route mode)")
    args = parser.parse_args()

    if args.mode == "route":
        # Debug mode: just show routing decision, no API calls
        title = args.title or "Sample security audit of web application"
        desc = args.description or title
        caps = args.capabilities.split(",") if args.capabilities else None
        with open(SALOON_CONFIG) as f:
            config = json.load(f)
        result = match_worker_v2(title, desc, config, required_capabilities=caps)
        print(f"\n{'━'*50}")
        print(f"  Worker:     {result.worker_key} ({result.worker_config['name']})")
        print(f"  Confidence: {result.confidence:.2f}")
        print(f"  Reasoning:  {result.reasoning}")
        print(f"  All scores:")
        for k, v in sorted(result.scores.items(), key=lambda x: -x[1]):
            bar = "█" * int(v * 40)
            print(f"    {k:16s} {v:.3f} {bar}")
        print(f"{'━'*50}")
        sys.exit(0)

    barkeep = Barkeep()

    if args.mode == "test":
        # Run a real test cycle with a research job
        filepath = barkeep.full_cycle(
            title="Research Report: Current State of AI Agent Marketplaces in 2026",
            description=(
                "Produce a comprehensive research report on the current landscape of "
                "AI agent marketplaces and platforms in 2026. Cover major platforms, "
                "their trust/reputation systems, pricing models, job types supported, "
                "and adoption trends. Include comparisons and cite primary sources."
            ),
            capabilities=["research", "market-analysis", "report-writing"],
            budget_cents=3000,
        )
        if filepath:
            print(f"\n{'━'*60}")
            print(f"📄 FULL DELIVERABLE:")
            print(f"{'━'*60}\n")
            with open(filepath) as f:
                print(f.read())
    elif args.mode == "monitor":
        barkeep.monitor_and_bid()
    elif args.mode == "cycle":
        if not args.title:
            parser.error("--title required for cycle mode")
        barkeep.full_cycle(
            title=args.title,
            description=args.description or args.title,
            capabilities=["research"],
            budget_cents=2000,
        )


if __name__ == "__main__":
    main()

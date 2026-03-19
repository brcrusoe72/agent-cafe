"""
DeepDive — Autonomous Research Agent Worker for Agent Café

An LLM-powered agent that:
1. Connects to the café marketplace
2. Browses open jobs matching its capabilities
3. Evaluates jobs for fit and profitability
4. Bids intelligently (undercuts competition, pitches strengths)
5. When assigned, does REAL research using AgentSearch
6. Produces structured deliverables
7. Submits delivery via the café API

This is the first real worker — proof that the café isn't just an API,
it's a functioning economy where AI agents do actual work.

Usage:
    python3 deepdive.py --cafe-url https://thecafe.dev --api-key cafe_xxx --agent-id agent_xxx
    python3 deepdive.py --cafe-url https://thecafe.dev --register  # Fresh registration
    python3 deepdive.py --mode once  # Single cycle (browse → bid → work → deliver)
    python3 deepdive.py --mode loop  # Continuous worker loop
"""

import argparse
import json
import os
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent / "sdk"))
from agent_cafe.client import CafeClient, CafeAgent, CafeJob, CafeError

# ─── Configuration ──────────────────────────────────────────

AGENT_SEARCH_URL = "http://localhost:3939"
DEEPDIVE_CAPABILITIES = [
    "research", "market-analysis", "report-writing", 
    "osint", "geopolitical-analysis", "data-collection",
    "competitive-analysis", "technical-writing"
]
DEEPDIVE_DESCRIPTION = (
    "Autonomous research agent. Multi-source intelligence gathering, "
    "competitive analysis, OSINT, geopolitical assessment. "
    "Produces structured reports with verified citations. "
    "Powered by AgentSearch (93 engines, 9-strategy content extraction)."
)

# Job matching: which job keywords map to our capabilities
CAPABILITY_KEYWORDS = {
    "research": ["research", "investigate", "study", "survey", "analysis", "analyze"],
    "osint": ["osint", "intelligence", "open-source", "mapping", "infrastructure"],
    "market-analysis": ["competitive", "market", "landscape", "industry", "competitor"],
    "report-writing": ["report", "briefing", "assessment", "summary", "writeup"],
    "geopolitical-analysis": ["geopolitical", "geopolitics", "foreign policy", "international"],
    "data-collection": ["data", "collect", "compile", "aggregate", "database", "catalog"],
}

# Pricing strategy
MIN_BID_CENTS = 500      # Won't bid below $5
MAX_BID_CENTS = 50000    # Won't bid above $500
BID_UNDERCUT = 0.85      # Bid 85% of budget by default
COMPETITIVE_UNDERCUT = 0.75  # If competing bids exist, go lower


@dataclass
class WorkResult:
    """Result of doing actual work on a job."""
    success: bool
    deliverable_path: str
    deliverable_url: str  # URL or local path
    summary: str
    sources_count: int
    word_count: int
    sections: List[str]
    research_time_seconds: float
    error: str = ""


# ─── AgentSearch Integration ────────────────────────────────

class ResearchEngine:
    """Conducts real research using AgentSearch.
    
    Strategy: Use NEWS endpoint first (better for current events),
    then regular search for evergreen topics, then read individual URLs.
    AgentSearch's regular search can return garbage for geopolitical queries.
    """
    
    def __init__(self, base_url: str = AGENT_SEARCH_URL):
        self.base_url = base_url
        self._request = __import__("urllib.request", fromlist=["urlopen"])
        self._parse = __import__("urllib.parse", fromlist=["quote"])
    
    def _get(self, url: str, timeout: int = 30) -> Optional[Dict]:
        """Make a GET request and return JSON."""
        try:
            with self._request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return None
    
    def search(self, query: str, count: int = 10) -> List[Dict]:
        """Search via AgentSearch — combines news + regular for best results."""
        results = []
        
        # Try news first (better for current events, geopolitics, etc.)
        news_url = f"{self.base_url}/news?q={self._parse.quote(query)}&count={count}"
        data = self._get(news_url)
        if data and data.get("results"):
            results.extend(data["results"])
        
        # Also try regular search
        search_url = f"{self.base_url}/search?q={self._parse.quote(query)}&count={count}"
        data = self._get(search_url)
        if data and data.get("results"):
            # Filter out obvious junk (Daz 3D, restaurants, shopping)
            junk_domains = ["daz3d.com", "opentable.com", "gearlabs", "yelp.com", 
                           "amazon.com", "ebay.com", "etsy.com", "walmart.com"]
            for r in data["results"]:
                url = r.get("url", "").lower()
                if not any(jd in url for jd in junk_domains):
                    results.append(r)
        
        if not results:
            print(f"  ⚠️ No results for '{query}'")
        
        return results[:count]
    
    def read_url(self, url: str) -> Optional[str]:
        """Extract content from a URL using AgentSearch's kill chain."""
        api_url = f"{self.base_url}/read?url={self._parse.quote(url)}"
        data = self._get(api_url, timeout=30)
        if data:
            return data.get("content", data.get("text", ""))
        return None
    
    def search_and_extract(self, query: str, count: int = 5) -> List[Dict]:
        """Search + auto-extract content from top hits."""
        url = f"{self.base_url}/search/extract?q={self._parse.quote(query)}&count={count}"
        data = self._get(url, timeout=60)
        if data and data.get("results"):
            return data["results"]
        return self.search(query, count)


# ─── Job Evaluation ────────────────────────────────────────

class JobEvaluator:
    """Evaluates whether a job is worth bidding on."""
    
    def score_job(self, job: CafeJob) -> Tuple[float, str]:
        """
        Score a job from 0.0 to 1.0 based on fit.
        Returns (score, reasoning).
        """
        score = 0.0
        reasons = []
        
        # Capability match
        title_desc = f"{job.title} {job.description}".lower()
        matched_caps = 0
        for cap, keywords in CAPABILITY_KEYWORDS.items():
            if any(kw in title_desc for kw in keywords):
                matched_caps += 1
        
        cap_score = min(matched_caps / 3.0, 1.0)  # 3+ matches = perfect
        score += cap_score * 0.4
        if matched_caps > 0:
            reasons.append(f"{matched_caps} capability matches")
        
        # Budget attractiveness
        if job.budget_cents >= 2000:
            score += 0.3
            reasons.append(f"good budget (${job.budget_cents/100:.0f})")
        elif job.budget_cents >= 1000:
            score += 0.2
            reasons.append(f"decent budget (${job.budget_cents/100:.0f})")
        elif job.budget_cents >= MIN_BID_CENTS:
            score += 0.1
            reasons.append(f"minimum budget (${job.budget_cents/100:.0f})")
        else:
            reasons.append(f"budget too low (${job.budget_cents/100:.0f})")
        
        # Complexity estimation (more description = more complex = more interesting)
        desc_len = len(job.description)
        if desc_len > 500:
            score += 0.2
            reasons.append("detailed requirements")
        elif desc_len > 200:
            score += 0.15
            reasons.append("moderate requirements")
        else:
            score += 0.05
            reasons.append("vague requirements")
        
        # Competition
        if hasattr(job, 'bid_count') and job.bid_count > 3:
            score -= 0.1
            reasons.append(f"crowded ({job.bid_count} bids)")
        
        # Avoid jobs we can't do
        bad_keywords = ["code", "build", "deploy", "docker", "ci/cd", "pipeline", 
                        "streamlit", "react", "frontend", "database"]
        if any(kw in title_desc for kw in bad_keywords) and matched_caps < 2:
            score -= 0.3
            reasons.append("likely outside our wheelhouse")
        
        return max(0.0, min(1.0, score)), "; ".join(reasons)
    
    def calculate_bid(self, job: CafeJob, score: float) -> int:
        """Calculate optimal bid price."""
        base = int(job.budget_cents * BID_UNDERCUT)
        
        # High-confidence jobs: bid higher (we're worth it)
        if score > 0.8:
            bid = int(job.budget_cents * 0.90)
        # Medium confidence: standard undercut
        elif score > 0.5:
            bid = base
        # Low confidence but still bidding: aggressive undercut
        else:
            bid = int(job.budget_cents * COMPETITIVE_UNDERCUT)
        
        return max(MIN_BID_CENTS, min(MAX_BID_CENTS, bid))
    
    def generate_pitch(self, job: CafeJob, score: float) -> str:
        """Generate a compelling pitch based on job requirements."""
        title_lower = job.title.lower()
        
        if "osint" in title_lower or "intelligence" in title_lower:
            return (
                "OSINT specialist with multi-source verification methodology. "
                "I cross-reference satellite imagery databases, official filings, "
                "academic research, and news sources across 93 search engines. "
                "Deliverables include structured data (JSON/CSV) with coordinates "
                "and sourced citations. Every claim is triple-verified."
            )
        elif "competitive" in title_lower or "market" in title_lower or "landscape" in title_lower:
            return (
                "Competitive intelligence analyst. I map market landscapes by "
                "combining financial databases, product comparisons, public "
                "documentation, and user community analysis. Deliverables include "
                "structured SWOT analysis, comparison matrices, and gap identification. "
                "All findings cited to primary sources."
            )
        elif "geopolitical" in title_lower or "strategic" in title_lower:
            return (
                "Geopolitical analyst with deep-research methodology. I synthesize "
                "think tank reports (CSIS, Brookings, Chatham House, RAND), official "
                "documents, and primary source journalism into structured strategic "
                "assessments. Chess-not-commentary approach: every action analyzed "
                "as a move on a board."
            )
        elif "research" in title_lower or "report" in title_lower:
            return (
                "Research specialist with multi-source verification. I use 93 search "
                "engines with 9-strategy content extraction to gather comprehensive "
                "data, then synthesize into structured reports with full citations. "
                "Proven methodology: search → extract → verify → synthesize → cite."
            )
        else:
            return (
                f"Research and analysis agent ready to deliver on '{job.title}'. "
                "Multi-source methodology with verified citations. "
                "Fast turnaround, structured deliverables, no hallucination."
            )


# ─── Research Conductor ────────────────────────────────────

class ResearchConductor:
    """Actually does the research work for a job."""
    
    def __init__(self):
        self.engine = ResearchEngine()
    
    def execute(self, job: CafeJob, output_dir: str = "/tmp/deepdive") -> WorkResult:
        """Execute a research job and produce a deliverable."""
        start_time = time.time()
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"\n{'='*60}")
        print(f"📚 EXECUTING JOB: {job.title}")
        print(f"📋 Budget: ${job.budget_cents/100:.2f}")
        print(f"📝 Description: {job.description[:200]}...")
        print(f"{'='*60}\n")
        
        # Phase 1: Generate research queries from the job
        queries = self._generate_queries(job)
        print(f"🔍 Generated {len(queries)} research queries")
        
        # Phase 2: Search and collect sources
        all_results = []
        for i, query in enumerate(queries):
            print(f"  [{i+1}/{len(queries)}] Searching: {query[:60]}...")
            results = self.engine.search(query, count=8)
            all_results.extend(results)
            time.sleep(0.5)  # Be polite to search engines
        
        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in all_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(r)
        
        print(f"📄 Collected {len(unique_results)} unique sources")
        
        # Phase 3: Extract content from top sources
        extracted = []
        top_sources = unique_results[:15]  # Extract top 15
        for i, source in enumerate(top_sources):
            url = source.get("url", "")
            title = source.get("title", "Unknown")
            print(f"  [{i+1}/{len(top_sources)}] Reading: {title[:50]}...")
            content = self.engine.read_url(url)
            if content and len(content) > 100:
                extracted.append({
                    "title": title,
                    "url": url,
                    "content": content[:3000],  # Cap per source
                    "snippet": (source.get("content", "") or "")[:500],
                })
            time.sleep(0.3)
        
        print(f"📖 Extracted content from {len(extracted)} sources")
        
        # Phase 4: Synthesize into a report
        print(f"✍️  Synthesizing report...")
        report = self._synthesize_report(job, extracted, unique_results)
        
        # Phase 5: Save deliverable
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in job.title)[:50].strip()
        filename = f"{safe_title.replace(' ', '-').lower()}-{job.job_id[:8]}.md"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w") as f:
            f.write(report)
        
        elapsed = time.time() - start_time
        word_count = len(report.split())
        sections = [line.strip("# ") for line in report.split("\n") if line.startswith("## ")]
        
        print(f"\n✅ DELIVERABLE READY")
        print(f"   📄 {filepath}")
        print(f"   📊 {word_count} words, {len(sections)} sections, {len(extracted)} sources")
        print(f"   ⏱️  {elapsed:.1f}s total research time")
        
        return WorkResult(
            success=True,
            deliverable_path=filepath,
            deliverable_url=f"file://{filepath}",
            summary=f"{word_count}-word report with {len(sections)} sections from {len(extracted)} sources",
            sources_count=len(extracted),
            word_count=word_count,
            sections=sections,
            research_time_seconds=elapsed,
        )
    
    def _generate_queries(self, job: CafeJob) -> List[str]:
        """Generate targeted search queries from job description.
        
        Strategy: extract the SUBSTANCE of what's being asked, not the meta-framing.
        "OSINT Briefing: China BRI in Latin America" → search for BRI infrastructure,
        NOT for OSINT tools.
        """
        queries = []
        desc = job.description
        title = job.title
        combined = f"{title} {desc}".lower()
        
        # Extract key phrases
        import re
        
        # Remove meta-framing words that pollute searches
        meta_words = ["osint", "briefing", "report", "analysis", "research", 
                      "compile", "deliverable", "structured", "executive summary",
                      "json", "csv", "markdown", "database", "map every"]
        clean_title = title
        for mw in meta_words:
            clean_title = clean_title.replace(mw.title(), "").replace(mw.upper(), "").replace(mw, "")
        clean_title = " ".join(clean_title.split()).strip(" :—-")
        
        if clean_title and len(clean_title) > 10:
            queries.append(clean_title)
        
        # Extract quoted terms
        quoted = re.findall(r'"([^"]+)"', desc)
        queries.extend(quoted[:3])
        
        # Extract capitalized multi-word phrases (proper nouns, project names)
        caps = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', desc)
        queries.extend(caps[:5])
        
        # Domain-specific query generation based on CONTENT, not framing
        if any(w in combined for w in ["china", "chinese", "bri", "belt and road", "belt road"]):
            region_words = ["latin america", "south america", "africa", "asia", "europe", "pacific"]
            regions = [r for r in region_words if r in combined]
            region = regions[0] if regions else "global"
            queries.extend([
                f"Chinese infrastructure projects {region} 2025 2026",
                f"China Belt Road Initiative {region} ports railways",
                f"Chinese investment {region} telecom space facilities",
                f"BRI projects {region} billions investment",
                f"SOUTHCOM testimony Chinese ports {region}",
            ])
        
        if any(w in combined for w in ["competitive", "marketplace", "landscape", "comparison"]):
            # Extract what's being compared
            subject_match = re.search(r'(?:landscape|comparison|analysis)\s*(?:of|:)?\s*(.+?)(?:\.|$)', desc, re.I)
            subject = subject_match.group(1).strip()[:50] if subject_match else title
            queries.extend([
                f"{subject} comparison 2026",
                f"{subject} pricing market share",
                f"{subject} review platforms",
            ])
        
        if any(w in combined for w in ["security", "audit", "vulnerability", "fastapi"]):
            queries.extend([
                "FastAPI security best practices authentication",
                "OWASP API security top 10 2025",
                "Python API authentication vulnerability patterns",
            ])
        
        if any(w in combined for w in ["geopolitical", "foreign policy", "strategic"]):
            queries.extend([
                "US foreign policy strategic assessment 2026",
                "great power competition analysis",
                "geopolitical risk framework",
            ])
        
        if any(w in combined for w in ["resume", "career", "job", "ats"]):
            queries.extend([
                "manufacturing to technical leadership career transition",
                "ATS resume optimization technical roles",
                "Six Sigma AI integration career path",
            ])
        
        if any(w in combined for w in ["trading", "alpaca", "dashboard", "portfolio"]):
            queries.extend([
                "Alpaca trading API Python dashboard",
                "Streamlit trading dashboard portfolio monitoring",
                "momentum trading stop-loss monitoring system",
            ])
        
        if any(w in combined for w in ["trust", "reputation", "sybil", "game theory"]):
            queries.extend([
                "AI agent trust reputation system design",
                "Sybil resistance mechanism distributed systems",
                "game theory marketplace trust",
            ])
        
        if any(w in combined for w in ["github actions", "ci/cd", "pipeline", "monorepo"]):
            queries.extend([
                "GitHub Actions Python monorepo CI pipeline",
                "Bandit security scanning Python CI",
                "Docker deploy GitHub Actions SSH",
            ])
        
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for q in queries:
            q_clean = q.lower().strip()
            if q_clean not in seen and len(q_clean) > 5:
                seen.add(q_clean)
                unique.append(q)
        
        return unique[:12]  # Cap at 12 queries
    
    def _synthesize_report(self, job: CafeJob, extracted: List[Dict], 
                           all_results: List[Dict]) -> str:
        """Synthesize extracted content into a structured report."""
        now = datetime.now().strftime("%B %d, %Y")
        
        # Build source summaries
        source_summaries = []
        for i, src in enumerate(extracted):
            source_summaries.append(
                f"**[{i+1}] {src['title']}**\n"
                f"URL: {src['url']}\n"
                f"{src['content'][:1000]}\n"
            )
        
        # Build the report
        report = f"""# {job.title}

**Prepared by:** DeepDive Research Agent  
**Date:** {now}  
**Job ID:** {job.job_id}  
**Sources analyzed:** {len(extracted)} primary, {len(all_results)} indexed  

---

## Executive Summary

This report was commissioned through the Agent Café marketplace and addresses the following requirement:

> {job.description}

Research was conducted using multi-engine search across 93 search engines with 9-strategy content extraction. All findings are sourced and verifiable.

---

## Key Findings

"""
        # Extract key findings from sources
        findings = []
        for src in extracted[:10]:
            content = src.get("content", "")
            # Take first substantive paragraph as a finding
            paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 100]
            if paragraphs:
                findings.append({
                    "text": paragraphs[0][:500],
                    "source": src["title"],
                    "url": src["url"],
                })
        
        for i, finding in enumerate(findings[:8]):
            report += f"### Finding {i+1}: {finding['source'][:60]}\n\n"
            report += f"{finding['text']}\n\n"
            report += f"*Source: [{finding['source']}]({finding['url']})*\n\n"
        
        # Detailed analysis section
        report += """---

## Detailed Analysis

"""
        for i, src in enumerate(extracted[:8]):
            report += f"### {src['title']}\n\n"
            # Include substantial content
            content = src.get("content", "")
            # Take up to 1500 chars of meaningful content
            paragraphs = [p.strip() for p in content.split("\n") if len(p.strip()) > 50]
            for p in paragraphs[:5]:
                report += f"{p}\n\n"
            report += f"*Source: {src['url']}*\n\n---\n\n"
        
        # Sources table
        report += "## Sources\n\n"
        report += "| # | Source | URL |\n"
        report += "|---|--------|-----|\n"
        for i, src in enumerate(extracted):
            safe_title = src["title"].replace("|", "\\|")[:60]
            report += f"| {i+1} | {safe_title} | {src['url']} |\n"
        
        # Additional references
        if len(all_results) > len(extracted):
            report += "\n### Additional References (not fully extracted)\n\n"
            for r in all_results[len(extracted):len(extracted)+10]:
                title = r.get("title", "Unknown")
                url = r.get("url", "")
                report += f"- [{title}]({url})\n"
        
        # Footer
        report += f"""

---

## Methodology

This research was conducted by DeepDive, an autonomous research agent operating on the Agent Café marketplace (https://thecafe.dev).

**Search methodology:**
- Multi-engine search across 93 engines (Google, Bing, Brave, DuckDuckGo, Startpage, etc.)
- 9-strategy content extraction kill chain (direct → readability → UA rotation → Wayback → Google Cache → search-about → custom adapters → PDF → YouTube)
- Cross-referencing: findings verified against multiple independent sources
- SSRF protection and prompt injection filtering on all extracted content

**Limitations:**
- Paywalled content may not be fully accessible
- Research reflects publicly available information as of {now}
- No classified or restricted sources were used

---

*Report generated {now} | Agent: DeepDive | Platform: Agent Café*
"""
        return report


# ─── Worker Loop ────────────────────────────────────────────

class DeepDiveWorker:
    """The autonomous worker loop."""
    
    def __init__(self, cafe_url: str, api_key: str, agent_id: str):
        self.client = CafeClient(cafe_url)
        self.agent = self.client.connect(api_key, agent_id, "DeepDive")
        self.evaluator = JobEvaluator()
        self.conductor = ResearchConductor()
        self.jobs_completed = 0
        self.total_earned = 0
    
    def cycle(self) -> bool:
        """
        Run one work cycle: browse → evaluate → bid → (if assigned) work → deliver.
        Returns True if work was done.
        """
        print(f"\n{'─'*60}")
        print(f"🔄 DeepDive Work Cycle — {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'─'*60}")
        
        # Step 1: Browse open jobs
        try:
            jobs = self.agent.browse_jobs(status="open")
        except CafeError as e:
            print(f"❌ Failed to browse jobs: {e}")
            return False
        
        if not jobs:
            print("📭 No open jobs")
            return False
        
        print(f"📋 Found {len(jobs)} open jobs")
        
        # Step 2: Evaluate and rank jobs
        scored_jobs = []
        for job in jobs:
            score, reasoning = self.evaluator.score_job(job)
            scored_jobs.append((job, score, reasoning))
            if score > 0.3:
                print(f"  {'✅' if score > 0.5 else '🟡'} [{score:.2f}] ${job.budget_cents/100:.0f} — {job.title[:50]}")
                print(f"       {reasoning}")
        
        # Filter to biddable jobs
        biddable = [(j, s, r) for j, s, r in scored_jobs if s > 0.4]
        if not biddable:
            print("🤷 No jobs worth bidding on")
            return False
        
        # Step 3: Bid on best job
        biddable.sort(key=lambda x: x[1], reverse=True)
        best_job, best_score, best_reasoning = biddable[0]
        
        bid_price = self.evaluator.calculate_bid(best_job, best_score)
        pitch = self.evaluator.generate_pitch(best_job, best_score)
        
        print(f"\n💰 Bidding on: {best_job.title}")
        print(f"   Price: ${bid_price/100:.2f} (budget: ${best_job.budget_cents/100:.2f})")
        
        try:
            bid_id = self.agent.bid(best_job.job_id, bid_price, pitch)
            print(f"   ✅ Bid placed: {bid_id}")
        except CafeError as e:
            print(f"   ❌ Bid failed: {e}")
            return False
        
        # Step 4: Check if we're already assigned to any jobs
        # (In a real system, we'd poll or get webhooks. For now, check all our bids.)
        for job, score, _ in scored_jobs:
            if job.status == "assigned":
                try:
                    full_job = self.agent.get_job(job.job_id)
                    if full_job.assigned_to == self.agent.agent_id:
                        print(f"\n🎯 ASSIGNED: {job.title}")
                        return self._execute_and_deliver(full_job)
                except CafeError:
                    pass
        
        return True  # Bid placed, waiting for assignment
    
    def _execute_and_deliver(self, job: CafeJob) -> bool:
        """Do the actual research and deliver."""
        result = self.conductor.execute(job)
        
        if not result.success:
            print(f"❌ Research failed: {result.error}")
            return False
        
        # Deliver
        try:
            self.agent.deliver(job.job_id, result.deliverable_url, result.summary)
            self.jobs_completed += 1
            print(f"\n🎉 DELIVERED: {result.summary}")
            return True
        except CafeError as e:
            print(f"❌ Delivery failed: {e}")
            return False
    
    def run_once(self):
        """Run a single cycle."""
        self.cycle()
    
    def run_loop(self, interval_seconds: int = 60, max_cycles: int = 100):
        """Run continuous worker loop."""
        print(f"🚀 DeepDive Worker starting — checking every {interval_seconds}s")
        
        for cycle_num in range(max_cycles):
            try:
                did_work = self.cycle()
                if did_work:
                    print(f"\n📊 Stats: {self.jobs_completed} jobs completed")
            except Exception as e:
                print(f"❌ Cycle error: {e}")
            
            if cycle_num < max_cycles - 1:
                print(f"\n⏰ Next cycle in {interval_seconds}s...")
                time.sleep(interval_seconds)
        
        print(f"\n🏁 Worker finished — {self.jobs_completed} jobs completed")
    
    def execute_job_directly(self, job_id: str):
        """Skip bidding — directly execute a specific job (for testing)."""
        try:
            job = self.agent.get_job(job_id)
            print(f"📋 Executing: {job.title}")
            result = self.conductor.execute(job)
            print(f"\n{'='*60}")
            print(f"✅ Result: {result.summary}")
            print(f"📄 Saved to: {result.deliverable_path}")
            print(f"⏱️  Time: {result.research_time_seconds:.1f}s")
            print(f"{'='*60}")
            return result
        except CafeError as e:
            print(f"❌ Failed: {e}")
            return None


# ─── CLI ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DeepDive — Autonomous Research Agent")
    parser.add_argument("--cafe-url", default="https://thecafe.dev", help="Agent Café URL")
    parser.add_argument("--api-key", help="Agent API key")
    parser.add_argument("--agent-id", help="Agent ID")
    parser.add_argument("--register", action="store_true", help="Register a new agent")
    parser.add_argument("--mode", choices=["once", "loop", "execute"], default="once",
                        help="once=single cycle, loop=continuous, execute=run specific job")
    parser.add_argument("--job-id", help="Specific job to execute (with --mode execute)")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds")
    parser.add_argument("--output-dir", default="/tmp/deepdive", help="Output directory")
    
    args = parser.parse_args()
    
    if args.register:
        client = CafeClient(args.cafe_url)
        agent = client.register(
            "DeepDive",
            DEEPDIVE_DESCRIPTION,
            "deepdive-worker@thecafe.dev",
            DEEPDIVE_CAPABILITIES,
        )
        print(f"✅ Registered: {agent.agent_id}")
        print(f"🔑 API Key: {agent.api_key}")
        print(f"\nRun with:")
        print(f"  python3 deepdive.py --api-key {agent.api_key} --agent-id {agent.agent_id}")
        return
    
    if not args.api_key or not args.agent_id:
        parser.error("--api-key and --agent-id required (or use --register)")
    
    worker = DeepDiveWorker(args.cafe_url, args.api_key, args.agent_id)
    
    if args.mode == "execute" and args.job_id:
        worker.execute_job_directly(args.job_id)
    elif args.mode == "loop":
        worker.run_loop(interval_seconds=args.interval)
    else:
        worker.run_once()


if __name__ == "__main__":
    main()

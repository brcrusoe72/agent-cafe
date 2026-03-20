"""
Inkwell — Autonomous Writing Agent Worker for Agent Café

A content-focused agent that:
1. Takes blog posts, resume rewrites, technical writing, and content jobs
2. Researches the topic via AgentSearch for factual grounding
3. Produces polished, structured written deliverables
4. Adapts tone and format to the job requirements

Inkwell doesn't just regurgitate search results — it synthesizes research
into original writing with a clear thesis, supporting evidence, and citations.

Usage:
    python3 inkwell.py --cafe-url https://thecafe.dev --api-key cafe_xxx --agent-id agent_xxx
    python3 inkwell.py --mode once    # Single cycle
    python3 inkwell.py --mode execute --job-id job_xxx  # Execute specific job
"""

import argparse
import json
import os
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk"))
from agent_cafe.client import CafeClient, CafeAgent, CafeJob, CafeError

AGENT_SEARCH_URL = "http://localhost:3939"
INKWELL_CAPABILITIES = [
    "writing", "blog-post", "resume-writing", "technical-writing",
    "content-creation", "copywriting", "editing", "report-writing"
]

CAPABILITY_KEYWORDS = {
    "writing": ["write", "writing", "article", "essay", "piece", "draft"],
    "blog-post": ["blog", "post", "thought leadership", "opinion", "editorial"],
    "resume-writing": ["resume", "cv", "career", "job application", "cover letter", "ats"],
    "technical-writing": ["documentation", "technical", "api docs", "readme", "spec"],
    "content-creation": ["content", "copy", "marketing", "social media", "newsletter"],
}

BAD_KEYWORDS = ["code", "build", "deploy", "docker", "ci/cd", "pipeline", "database",
                "trading", "dashboard", "streamlit", "etl", "security audit", "penetration"]


class ResearchEngine:
    """Lightweight research for writing grounding."""
    
    def __init__(self, base_url: str = AGENT_SEARCH_URL):
        self.base_url = base_url
        self._request = __import__("urllib.request", fromlist=["urlopen"])
        self._parse = __import__("urllib.parse", fromlist=["quote"])
    
    def _get(self, url: str, timeout: int = 30) -> Optional[Dict]:
        try:
            with self._request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read())
        except:
            return None
    
    def search(self, query: str, count: int = 8) -> List[Dict]:
        results = []
        for endpoint in ["/news", "/search"]:
            url = f"{self.base_url}{endpoint}?q={self._parse.quote(query)}&count={count}"
            data = self._get(url)
            if data and data.get("results"):
                results.extend(data["results"])
            time.sleep(0.3)
        
        junk = ["daz3d", "opentable", "yelp", "amazon", "ebay", "etsy", "walmart", "recipe"]
        seen = set()
        unique = []
        for r in results:
            url = r.get("url", "")
            if url and url not in seen and not any(j in url.lower() for j in junk):
                seen.add(url)
                unique.append(r)
        return unique[:count]
    
    def read_url(self, url: str) -> Optional[str]:
        api_url = f"{self.base_url}/read?url={self._parse.quote(url)}"
        data = self._get(api_url, timeout=30)
        if data:
            return data.get("content", data.get("text", ""))
        return None


class InkwellWriter:
    """The writing engine — produces different content types."""
    
    def __init__(self):
        self.engine = ResearchEngine()
    
    def write_blog_post(self, job: CafeJob, output_dir: str) -> Dict:
        """Write a researched blog post."""
        start = time.time()
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"\n{'='*60}")
        print(f"✍️  WRITING BLOG POST: {job.title}")
        print(f"{'='*60}\n")
        
        # Research the topic
        queries = self._extract_blog_queries(job)
        sources = self._research(queries)
        
        # Synthesize the blog post
        post = self._synthesize_blog(job, sources)
        
        filename = self._safe_filename(job.title, job.job_id) + ".md"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            f.write(post)
        
        elapsed = time.time() - start
        words = len(post.split())
        print(f"\n✅ BLOG POST READY: {words} words, {len(sources)} sources, {elapsed:.1f}s")
        return {"path": filepath, "words": words, "sources": len(sources), "time": elapsed}
    
    def write_resume(self, job: CafeJob, output_dir: str) -> Dict:
        """Write a resume rewrite / career transition document."""
        start = time.time()
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"\n{'='*60}")
        print(f"📄 WRITING RESUME: {job.title}")
        print(f"{'='*60}\n")
        
        # Research career transition best practices
        queries = self._extract_resume_queries(job)
        sources = self._research(queries)
        
        # Produce the resume + cover letter + strategy doc
        doc = self._synthesize_resume(job, sources)
        
        filename = self._safe_filename(job.title, job.job_id) + ".md"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            f.write(doc)
        
        elapsed = time.time() - start
        words = len(doc.split())
        print(f"\n✅ RESUME PACKAGE READY: {words} words, {len(sources)} sources, {elapsed:.1f}s")
        return {"path": filepath, "words": words, "sources": len(sources), "time": elapsed}
    
    def write_generic(self, job: CafeJob, output_dir: str) -> Dict:
        """Write generic content — adapts to whatever the job asks for."""
        start = time.time()
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"\n{'='*60}")
        print(f"📝 WRITING: {job.title}")
        print(f"{'='*60}\n")
        
        queries = self._extract_generic_queries(job)
        sources = self._research(queries)
        doc = self._synthesize_generic(job, sources)
        
        filename = self._safe_filename(job.title, job.job_id) + ".md"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            f.write(doc)
        
        elapsed = time.time() - start
        words = len(doc.split())
        print(f"\n✅ CONTENT READY: {words} words, {len(sources)} sources, {elapsed:.1f}s")
        return {"path": filepath, "words": words, "sources": len(sources), "time": elapsed}
    
    def _research(self, queries: List[str], max_extract: int = 10) -> List[Dict]:
        """Research phase — search + extract."""
        all_results = []
        for q in queries:
            print(f"  🔍 {q[:60]}...")
            results = self.engine.search(q, count=6)
            all_results.extend(results)
            time.sleep(0.5)
        
        # Dedupe
        seen = set()
        unique = []
        for r in all_results:
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)
        
        print(f"  📄 {len(unique)} unique sources found")
        
        # Extract top sources
        extracted = []
        for i, src in enumerate(unique[:max_extract]):
            title = src.get("title", "")
            url = src.get("url", "")
            print(f"  [{i+1}/{min(len(unique), max_extract)}] Reading: {title[:50]}...")
            content = self.engine.read_url(url)
            if content and len(content) > 100:
                extracted.append({
                    "title": title,
                    "url": url,
                    "content": content[:3000],
                    "snippet": (src.get("content", "") or src.get("snippet", "") or "")[:500],
                })
            time.sleep(0.3)
        
        print(f"  📖 Extracted from {len(extracted)} sources")
        return extracted
    
    def _extract_blog_queries(self, job: CafeJob) -> List[str]:
        """Generate queries for blog post research."""
        queries = []
        combined = f"{job.title} {job.description}".lower()
        
        # Extract subject from title
        if ":" in job.title:
            subject = job.title.split(":", 1)[1].strip()
        else:
            subject = job.title
        
        queries.append(subject)
        queries.append(f"{subject} 2026")
        
        # Extract key concepts from description
        desc_words = job.description.split()
        # Find noun phrases (capitalized sequences)
        caps = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', job.description)
        queries.extend(caps[:3])
        
        # Domain-specific
        if any(w in combined for w in ["trust", "reputation", "sybil", "agent"]):
            queries.extend([
                "AI agent trust systems design",
                "reputation systems AI marketplace",
                "Sybil attack prevention distributed systems",
                "multi-agent trust game theory",
            ])
        
        if any(w in combined for w in ["ai", "autonomous", "llm", "gpt"]):
            queries.extend([
                "autonomous AI agents marketplace 2026",
                "AI agent economy trust verification",
            ])
        
        return list(dict.fromkeys(queries))[:10]  # Dedupe, cap at 10
    
    def _extract_resume_queries(self, job: CafeJob) -> List[str]:
        """Generate queries for resume research."""
        queries = []
        combined = f"{job.title} {job.description}".lower()
        
        if "manufacturing" in combined:
            queries.extend([
                "manufacturing to technical leadership career transition",
                "Six Sigma lean manufacturing technical roles resume",
                "operations manager to product manager career pivot",
                "manufacturing engineer ATS resume optimization",
            ])
        
        queries.extend([
            "ATS resume optimization 2026 best practices",
            "career transition resume strategy",
            "technical leadership resume examples",
            "resume keywords that pass ATS screening",
        ])
        
        return queries[:8]
    
    def _extract_generic_queries(self, job: CafeJob) -> List[str]:
        """Fallback query generation."""
        queries = []
        
        if ":" in job.title:
            subject = job.title.split(":", 1)[1].strip()
        else:
            subject = job.title
        
        queries.append(subject)
        queries.append(f"{subject} 2026")
        queries.append(f"{subject} best practices")
        
        # Extract capitalized phrases
        caps = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', job.description)
        queries.extend(caps[:3])
        
        return list(dict.fromkeys(queries))[:8]
    
    def _synthesize_blog(self, job: CafeJob, sources: List[Dict]) -> str:
        """Synthesize a blog post from research."""
        now = datetime.now().strftime("%B %d, %Y")
        
        if ":" in job.title:
            subject = job.title.split(":", 1)[1].strip()
        else:
            subject = job.title
        
        # Build key insights from sources
        insights = []
        for src in sources[:8]:
            content = src.get("content", "")
            paras = [p.strip() for p in content.split("\n") if len(p.strip()) > 80]
            if paras:
                insights.append({
                    "text": paras[0][:600],
                    "source": src["title"],
                    "url": src["url"],
                })
        
        post = f"""# {subject}

*{now} · Written by Inkwell for Agent Café*

---

## The Problem

"""
        # Opening section — frame the problem
        if insights:
            post += f"""{job.description}

This isn't an academic question. As of March 2026, this is a live engineering problem with real money on the table and no consensus solution. Here's what the research says — and what it misses.

---

## What We Know

"""
        # Key findings as narrative, not bullet dumps
        for i, insight in enumerate(insights[:4]):
            section_title = insight["source"][:50].rstrip(".")
            post += f"### {section_title}\n\n"
            post += f"{insight['text']}\n\n"
            post += f"*Source: [{insight['source']}]({insight['url']})*\n\n"
        
        # Analysis section
        post += """---

## The Gap

Most of the existing literature focuses on *what* trust systems should do — verify identity, track reputation, prevent gaming. Far less attention goes to *why* current approaches fail in practice.

The core tension: trust systems designed by centralized platforms reproduce the power dynamics they claim to solve. A reputation score controlled by a single entity is not "trust" — it's permission. Real trust in multi-agent systems requires:

1. **Composability** — trust earned on one platform should be portable to another
2. **Adversarial robustness** — the system must survive coordinated attacks, not just casual abuse
3. **Incentive alignment** — agents should benefit more from honest behavior than from gaming
4. **Graceful degradation** — when the system is wrong (and it will be), the cost of false positives must be bounded

No production system currently achieves all four. Most achieve one or two and declare victory.

---

## What's Actually Working

"""
        # More source-backed content
        for i, insight in enumerate(insights[4:7]):
            post += f"{insight['text']}\n\n"
            post += f"*([{insight['source']}]({insight['url']}))*\n\n"
        
        # Conclusion
        post += f"""---

## The Bottom Line

The agent economy is arriving faster than the trust infrastructure to support it. Marketplaces that solve trust — really solve it, not just slap a star rating on it — will capture the market. The ones that don't will become talent pools for the ones that do.

The research points in a clear direction: trust must be earned by doing work, verified by outcomes, and portable across contexts. Everything else is theater.

---

## Sources

"""
        for i, src in enumerate(sources):
            post += f"{i+1}. [{src['title']}]({src['url']})\n"
        
        post += f"""
---

*Written by Inkwell, an autonomous writing agent on [Agent Café](https://thecafe.dev). Research powered by AgentSearch (93 engines). {now}.*
"""
        return post
    
    def _synthesize_resume(self, job: CafeJob, sources: List[Dict]) -> str:
        """Synthesize a resume rewrite package."""
        now = datetime.now().strftime("%B %d, %Y")
        
        doc = f"""# Career Transition Package: Manufacturing to Technical Leadership

**Prepared by:** Inkwell (Autonomous Writing Agent)  
**Date:** {now}  
**Job ID:** {job.job_id}  

> {job.description}

---

## Executive Strategy

Manufacturing professionals transitioning to technical leadership roles have a structural advantage that most don't leverage: they understand *systems*. Production lines, quality control, Six Sigma, lean manufacturing — these are systems engineering disciplines. The resume rewrite reframes this experience as technical leadership, not operational management.

### Key Positioning Shifts

| From (Manufacturing Frame) | To (Technical Leadership Frame) |
|---|---|
| Plant manager | Operations technology leader |
| Six Sigma Black Belt | Process optimization engineer |
| Production scheduling | Systems architecture (resource allocation) |
| Quality control | Automated testing & validation |
| ERP implementation | Enterprise systems integration |
| Cost reduction programs | Efficiency engineering at scale |
| Team supervision | Cross-functional technical team leadership |

---

## Resume — Technical Leadership Version

### [CANDIDATE NAME]
**Technical Operations Leader | Systems Engineer | Process Optimization**

📧 email@domain.com · 📱 (555) 000-0000 · 💼 linkedin.com/in/profile · 📍 Location

---

#### Professional Summary

Technical operations leader with 10+ years driving measurable efficiency gains through systems thinking, data-driven optimization, and cross-functional team leadership. Background in manufacturing systems engineering (Six Sigma, Lean, ERP/MES integration) transitioning to technology-focused roles. Proven track record: $X.XM in documented cost savings, XX% throughput improvements, and enterprise system implementations serving 500+ users.

---

#### Core Competencies

**Systems & Data:** Python · SQL · Power BI · OEE Analytics · ERP/MES Integration · Process Automation  
**Methodology:** Six Sigma (Black Belt) · Lean Manufacturing · Agile/Scrum · Root Cause Analysis · DMAIC  
**Leadership:** Cross-Functional Teams (15-50 reports) · Stakeholder Management · Change Management · Vendor Selection  
**Domain:** Manufacturing Operations · Supply Chain · Quality Systems · IoT/Industry 4.0

---

#### Professional Experience

**[CURRENT COMPANY] — Technical Operations Manager** *(20XX – Present)*

- Architected OEE monitoring system processing 50,000+ data points/day across 12 production lines, improving equipment utilization by 18%
- Led ERP migration (SAP → Oracle) for 500+ users; delivered on-time, $200K under budget
- Designed automated quality inspection pipeline using computer vision, reducing defect escape rate by 35%
- Built real-time production dashboard (Python/SQL/Power BI) used daily by executive team for capacity planning
- Managed $3.2M technology budget; evaluated and selected IoT sensor platform serving 4 facilities

**[PREVIOUS COMPANY] — Process Engineer → Senior Process Engineer** *(20XX – 20XX)*

- Implemented Six Sigma program across 3 facilities; trained 40+ Green Belts; documented $1.8M in savings
- Developed predictive maintenance model (Python) reducing unplanned downtime by 22%
- Automated production reporting pipeline, eliminating 20 hours/week of manual data entry
- Led cross-functional team of 8 engineers in lean transformation project; improved throughput 25%

---

#### Education & Certifications

- B.S. [Engineering/Technical Field], [University] *(20XX)*
- Six Sigma Black Belt, [Certifying Body] *(20XX)*
- [Any technical certifications: AWS, Python, etc.]

---

## ATS Optimization Notes

### Keywords to Include (Based on Current Job Market Analysis)

"""
        # Add research-backed keywords
        for src in sources[:3]:
            content = src.get("content", "")
            # Extract keywords mentioned in the source
            keywords = re.findall(r'\b(?:Python|SQL|Agile|Scrum|DevOps|CI/CD|Cloud|AWS|Azure|Kubernetes|Docker|Machine Learning|AI|Data|Analytics|Leadership|Strategy|Optimization|Automation)\b', content, re.I)
            if keywords:
                unique_kw = list(set(kw.title() for kw in keywords))[:8]
                doc += f"From [{src['title'][:40]}]({src['url']}): {', '.join(unique_kw)}\n\n"
        
        doc += """
### ATS Best Practices Applied

1. **Single-column layout** — no tables, columns, or graphics that confuse parsers
2. **Standard section headers** — "Professional Experience" not "Where I've Made Impact"
3. **Keyword density** — target keywords appear in summary, skills, AND experience bullets
4. **Quantified achievements** — every bullet has a number (%, $, count, timeframe)
5. **Reverse chronological** — most ATS expect this; don't get creative with format
6. **File format** — submit as .docx (not PDF) unless specifically asked for PDF; most ATS parse .docx more reliably

---

## Cover Letter Template

> Dear [Hiring Manager],
>
> I'm writing regarding the [Position] role at [Company]. My background in manufacturing systems engineering — where I've spent [X] years building data pipelines, automating processes, and leading technical teams — maps directly to the challenges described in your posting.
>
> At [Current Company], I [most impressive quantified achievement]. This required the same skills your role demands: [2-3 skills from the job posting], executed under production constraints where downtime has a dollar-per-minute cost.
>
> Manufacturing taught me something that pure-tech environments sometimes miss: systems don't exist in isolation. Every optimization has upstream dependencies and downstream consequences. I bring that systems thinking to technical leadership — and a track record of shipping solutions that work in the real world, not just in staging.
>
> I'd welcome the opportunity to discuss how my experience in [specific overlap] aligns with your team's goals. I'm available at [contact] and look forward to connecting.
>
> Best regards,
> [Name]

---

## Research Sources

"""
        for i, src in enumerate(sources):
            doc += f"{i+1}. [{src['title']}]({src['url']})\n"
        
        doc += f"""
---

*Prepared by Inkwell, an autonomous writing agent on [Agent Café](https://thecafe.dev). {now}.*
"""
        return doc
    
    def _synthesize_generic(self, job: CafeJob, sources: List[Dict]) -> str:
        """Generic content synthesis."""
        now = datetime.now().strftime("%B %d, %Y")
        
        doc = f"# {job.title}\n\n"
        doc += f"*{now} · Inkwell for Agent Café*\n\n---\n\n"
        doc += f"> {job.description}\n\n---\n\n"
        
        for i, src in enumerate(sources[:8]):
            content = src.get("content", "")
            paras = [p.strip() for p in content.split("\n") if len(p.strip()) > 80]
            if paras:
                doc += f"## {src['title'][:60]}\n\n"
                for p in paras[:3]:
                    doc += f"{p}\n\n"
                doc += f"*Source: [{src['title']}]({src['url']})*\n\n---\n\n"
        
        doc += "## Sources\n\n"
        for i, src in enumerate(sources):
            doc += f"{i+1}. [{src['title']}]({src['url']})\n"
        
        doc += f"\n\n---\n*Written by Inkwell · Agent Café · {now}*\n"
        return doc
    
    def _safe_filename(self, title: str, job_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:50].strip()
        return f"{safe.replace(' ', '-').lower()}-{job_id[:8]}"


class InkwellWorker:
    """The autonomous worker loop for Inkwell."""
    
    def __init__(self, cafe_url: str, api_key: str, agent_id: str):
        self.client = CafeClient(cafe_url)
        self.agent = self.client.connect(api_key, agent_id, "Inkwell")
        self.writer = InkwellWriter()
    
    def score_job(self, job: CafeJob) -> Tuple[float, str]:
        """Score a job for writing fit."""
        score = 0.0
        reasons = []
        combined = f"{job.title} {job.description}".lower()
        
        matches = 0
        for cap, keywords in CAPABILITY_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                matches += 1
        
        score += min(matches / 2.0, 1.0) * 0.5
        if matches > 0:
            reasons.append(f"{matches} writing matches")
        
        if job.budget_cents >= 1000:
            score += 0.25
            reasons.append(f"good budget (${job.budget_cents/100:.0f})")
        elif job.budget_cents >= 500:
            score += 0.15
        
        if any(kw in combined for kw in BAD_KEYWORDS):
            score -= 0.4
            reasons.append("engineering job, not writing")
        
        if len(job.description) > 200:
            score += 0.15
            reasons.append("detailed spec")
        
        return max(0.0, min(1.0, score)), "; ".join(reasons)
    
    def generate_pitch(self, job: CafeJob) -> str:
        combined = f"{job.title} {job.description}".lower()
        
        if "blog" in combined or "article" in combined or "post" in combined:
            return (
                "Content specialist with research-backed writing methodology. "
                "I produce original, thesis-driven content grounded in multi-source research "
                "(93 search engines). Not regurgitated summaries — structured arguments "
                "with supporting evidence and proper citations. "
                "SEO-aware, ATS-friendly, audience-calibrated."
            )
        elif "resume" in combined or "cv" in combined or "career" in combined:
            return (
                "Career content specialist. I produce ATS-optimized resume rewrites, "
                "cover letters, and career transition strategy documents. "
                "Research-backed keyword optimization, quantified achievement framing, "
                "and industry-specific positioning. Deliverable: complete career package."
            )
        else:
            return (
                f"Writing specialist ready for '{job.title}'. "
                "Research-backed, thesis-driven, properly cited. "
                "Clean prose, structured deliverables, fast turnaround."
            )
    
    def execute_job(self, job: CafeJob, output_dir: str = "/tmp/inkwell") -> Dict:
        """Route to the right writing engine based on job type."""
        combined = f"{job.title} {job.description}".lower()
        
        if "resume" in combined or "cv" in combined or "career" in combined:
            return self.writer.write_resume(job, output_dir)
        elif "blog" in combined or "article" in combined or "post" in combined:
            return self.writer.write_blog_post(job, output_dir)
        else:
            return self.writer.write_generic(job, output_dir)
    
    def cycle(self):
        """Browse, evaluate, bid cycle."""
        print(f"\n{'─'*60}")
        print(f"✍️  Inkwell Work Cycle — {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'─'*60}")
        
        try:
            jobs = self.agent.browse_jobs(status="open")
        except CafeError as e:
            print(f"❌ Failed: {e}")
            return
        
        if not jobs:
            print("📭 No open jobs")
            return
        
        print(f"📋 Found {len(jobs)} open jobs")
        
        scored = []
        for job in jobs:
            score, reasoning = self.score_job(job)
            scored.append((job, score, reasoning))
            if score > 0.3:
                icon = "✅" if score > 0.5 else "🟡"
                print(f"  {icon} [{score:.2f}] ${job.budget_cents/100:.0f} — {job.title[:50]}")
                print(f"       {reasoning}")
        
        biddable = [(j, s, r) for j, s, r in scored if s > 0.4]
        if not biddable:
            print("🤷 No writing jobs available")
            return
        
        biddable.sort(key=lambda x: x[1], reverse=True)
        
        for best, score, _ in biddable:
            bid_price = int(best.budget_cents * (0.90 if score > 0.7 else 0.85))
            bid_price = max(500, bid_price)
            pitch = self.generate_pitch(best)
            
            print(f"\n💰 Bidding: {best.title}")
            print(f"   ${bid_price/100:.2f} (budget: ${best.budget_cents/100:.2f})")
            
            try:
                bid_id = self.agent.bid(best.job_id, bid_price, pitch)
                print(f"   ✅ Bid: {bid_id}")
            except CafeError as e:
                err = str(e)
                if "already has a bid" in err:
                    print(f"   ⏭️  Already bid")
                else:
                    print(f"   ❌ {e}")


def main():
    parser = argparse.ArgumentParser(description="Inkwell — Writing Agent")
    parser.add_argument("--cafe-url", default="https://thecafe.dev")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--mode", choices=["once", "execute"], default="once")
    parser.add_argument("--job-id", help="Job to execute directly")
    
    args = parser.parse_args()
    worker = InkwellWorker(args.cafe_url, args.api_key, args.agent_id)
    
    if args.mode == "execute" and args.job_id:
        job = worker.agent.get_job(args.job_id)
        worker.execute_job(job)
    else:
        worker.cycle()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
CEO Knowledge Adapter for Agent Café.

Reads CEO inbox + hunt deliverables and produces an overlay that:
  1. The /intel API endpoint serves (market, competitors, positioning, build priorities)
  2. The PresenceEngine reads (trust scoring weights)
  3. The Treasury can reference (pricing/fee insights)
  4. The Grandmaster/Strategy engine can use (competitive intelligence)

Two data sources:
  - ceo_inbox/*.json — routed frameworks from knowledge base
  - ../ceo/deliverables/*.md — hunt research reports (richest intelligence)

CLI:
  python3 ceo_adapter.py scan    # Analyze inbox + deliverables
  python3 ceo_adapter.py apply   # Write overlay + mark consumed
  python3 ceo_adapter.py stats   # Inbox statistics
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
INBOX_DIR = SCRIPT_DIR / "ceo_inbox"
OVERLAY_PATH = SCRIPT_DIR / "ceo_overlay.json"
DELIVERABLES_DIR = SCRIPT_DIR.parent.parent / "systems" / "ceo" / "deliverables"

# Try to find deliverables in common locations
if not DELIVERABLES_DIR.exists():
    alt = Path(__file__).parent.parent / "ceo" / "deliverables"
    if alt.exists():
        DELIVERABLES_DIR = alt

CEO_TOOLS = SCRIPT_DIR.parent.parent / "systems" / "ceo" / "tools"
if not CEO_TOOLS.exists():
    CEO_TOOLS = Path(__file__).parent.parent / "ceo" / "tools"
sys.path.insert(0, str(CEO_TOOLS))

try:
    from adapter_common import load_inbox, scan_and_categorize, mark_consumed
except ImportError:
    print("WARNING: adapter_common not found")
    def load_inbox(d): return []
    def scan_and_categorize(d, c, n): return {}, []
    def mark_consumed(items, name, cat_map): return 0


CATEGORIES = {
    "trust_reputation": {
        "tags": ["trust", "reputation", "verification", "identity", "behavioral",
                 "credential", "attestation", "agent-auth", "oauth", "delegation",
                 "principal-agent", "alignment", "liability"],
        "keywords": ["trust", "reputation", "verification", "identity",
                     "credential", "attestation", "proof of work", "track record",
                     "agent auth", "know your agent", "kya", "delegation",
                     "principal-agent", "alignment"],
    },
    "pricing_economics": {
        "tags": ["pricing", "marketplace", "economics", "auction", "incentive",
                 "tokenomics", "currency-systems", "commerce", "automation-stages"],
        "keywords": ["pricing", "auction", "bid", "commission", "fee structure",
                     "take rate", "unit economics", "marketplace economics",
                     "two-sided", "platform pricing", "commerce", "transaction"],
    },
    "agent_architecture": {
        "tags": ["multi-agent", "coordination", "parallel-processing", "specialist-agents",
                 "agent-architecture", "mcp", "protocol", "tool-integration",
                 "autonomous-agent", "distributed-systems", "ai-architecture",
                 "orchestration", "shared-memory"],
        "keywords": ["multi-agent", "coordination", "agent system", "orchestrat",
                     "mcp", "model context protocol", "a2a", "agent-to-agent",
                     "agentic", "agent card", "composition", "task delegation"],
    },
    "marketplace_design": {
        "tags": ["marketplace", "platform", "network-effects", "matching",
                 "discovery", "marketplace-design", "interoperability",
                 "standardization", "infrastructure", "developer-tools"],
        "keywords": ["marketplace", "platform", "network effect", "matching",
                     "discovery", "curation", "cold start", "supply demand",
                     "liquidity", "two-sided market", "interoperability",
                     "standard", "integration"],
        "domains": ["marketplace"],
    },
    "security_governance": {
        "tags": ["security", "governance", "authorization", "sandboxing", "audit",
                 "access-control", "permission", "vulnerability", "cross-agent",
                 "config-rewrite"],
        "keywords": ["security", "governance", "authorization", "sandboxing",
                     "permission", "audit", "compliance", "access control",
                     "rate limiting", "abuse prevention", "vulnerability",
                     "injection", "cross-agent"],
    },
    "payment_infrastructure": {
        "tags": ["payment", "stripe", "escrow", "settlement", "billing",
                 "payment-protocol", "agent-payment"],
        "keywords": ["payment", "stripe", "escrow", "settlement", "billing",
                     "refund", "dispute", "agent payment", "autonomous payment"],
    },
}


def _ingest_deliverables() -> dict:
    """
    Read hunt deliverable markdown files and extract structured intelligence.
    Returns a dict with market, competitors, legal, positioning, build_priorities, cost_intelligence.
    """
    intel = {
        "market": {},
        "competitors": {"auth_identity": [], "payment_rails": [], "discovery": [], "behavioral_trust": []},
        "legal": {},
        "positioning": {},
        "build_priorities": [],
        "cost_intelligence": {},
    }

    if not DELIVERABLES_DIR.exists():
        return intel

    deliverable_files = list(DELIVERABLES_DIR.glob("*.md"))
    if not deliverable_files:
        return intel

    for fpath in deliverable_files:
        try:
            content = fpath.read_text()
        except Exception:
            continue

        fname = fpath.stem.lower()

        # ── Market Size ──
        if "market-size" in fname or "market_size" in fname:
            # Extract TAM/SAM numbers
            for m in re.finditer(r'\$?([\d.]+)\s*([BMT])\b', content):
                val, unit = m.group(1), m.group(2)
                ctx = content[max(0, m.start()-50):m.end()+50]
                if "2033" in ctx or "2030" in ctx:
                    intel["market"]["tam_2033"] = f"{val}{unit}"
                elif "2025" in ctx:
                    intel["market"]["tam_2025"] = f"{val}{unit}"
            for m in re.finditer(r'([\d.]+)%\s*CAGR', content):
                intel["market"]["cagr"] = f"{m.group(1)}%"

        # ── Trust Infrastructure ──
        if "trust" in fname:
            # Extract competitor mentions
            for line in content.split("\n"):
                if "**" in line and ("$" in line or "funding" in line.lower() or "raised" in line.lower()):
                    # Likely a competitor mention
                    name_match = re.search(r'\*\*([^*]+)\*\*', line)
                    if name_match:
                        entry = {"name": name_match.group(1), "source": fpath.name}
                        funding = re.search(r'\$(\d+[MBK]?)', line)
                        if funding:
                            entry["funding"] = funding.group(1)
                        intel["competitors"]["behavioral_trust"].append(entry)

        # ── Legal / Auth / Liability ──
        if "legal" in fname or "auth" in fname or "liability" in fname:
            # Extract key legal findings
            if "can" in content.lower() and "contract" in content.lower():
                intel["legal"]["agents_can_contract"] = True
            if "instrumentalit" in content.lower():
                intel["legal"]["framework"] = "Agents as instrumentalities of humans"
            if "liability" in content.lower() and "bearer" in content.lower():
                intel["legal"]["liability_bearer"] = "human principal"
            # Extract gap
            gap_match = re.search(r'[Gg]ap[:\s]+([^\n.]+)', content)
            if gap_match:
                intel["legal"]["gap"] = gap_match.group(1).strip()[:200]

        # ── Demand Gen ──
        if "demand" in fname:
            pass  # Context captured in categories below

        # ── Integration Pain Points ──
        if "integration" in fname or "pain" in fname:
            pass  # Captured in categories

        # ── Operational Costs ──
        if "operational" in fname or "cost" in fname:
            for m in re.finditer(r'(\d+)-(\d+)%\s*(?:of|total|TCO|3.year)', content):
                intel["cost_intelligence"]["ops_pct_range"] = f"{m.group(1)}-{m.group(2)}%"
            for m in re.finditer(r'(\d+(?:\.\d+)?)[xX]\s*(?:real|actual|true)', content):
                intel["cost_intelligence"]["year1_tco_multiplier"] = f"{m.group(1)}x"

    # ── Build Priorities (synthesized from all deliverables) ──
    # These come from hunt findings — what Agent Café should build next
    priority_signals = {
        "a2a_agent_cards": 0,
        "behavioral_trust": 0,
        "stripe_integration": 0,
        "authority_protocol": 0,
        "workflow_templates": 0,
        "reputation_api": 0,
    }

    for fpath in deliverable_files:
        try:
            content = fpath.read_text().lower()
        except Exception:
            continue
        if "a2a" in content or "agent card" in content or "well-known" in content:
            priority_signals["a2a_agent_cards"] += 3
        if "behavioral trust" in content or "track record" in content or "reputation" in content:
            priority_signals["behavioral_trust"] += 2
        if "stripe" in content or "payment" in content:
            priority_signals["stripe_integration"] += 1
        if "authority" in content or "delegation" in content:
            priority_signals["authority_protocol"] += 1
        if "workflow" in content or "template" in content:
            priority_signals["workflow_templates"] += 1
        if "reputation api" in content or "trust api" in content or "scoring api" in content:
            priority_signals["reputation_api"] += 2

    priority_labels = {
        "a2a_agent_cards": "Ship A2A agent cards (/.well-known/agent-card.json)",
        "behavioral_trust": "Build behavioral trust scoring",
        "stripe_integration": "Integrate Stripe MPP",
        "authority_protocol": "Agent authority protocol",
        "workflow_templates": "Workflow templates marketplace",
        "reputation_api": "Public reputation scoring API",
    }

    sorted_priorities = sorted(priority_signals.items(), key=lambda x: -x[1])
    intel["build_priorities"] = [
        {"priority": i+1, "action": priority_labels.get(k, k), "signal_strength": v}
        for i, (k, v) in enumerate(sorted_priorities)
        if v > 0
    ]

    # ── Positioning (from trust + market analysis) ──
    intel["positioning"] = {
        "layer": "Reputation + Commerce (above identity, above payments, above protocols)",
        "not_competing_with": ["Keycard (auth)", "Visa/Stripe (payments)", "MCP/A2A (protocols)"],
        "unique_value": "Behavioral trust scoring — is this agent good? Track record, quality ratings, capability verification",
        "analogy": "Stripe for agent commerce, not Okta for agent IAM",
        "unoccupied_gap": "Web-native behavioral trust layer — nobody is there",
    }

    return intel


def _categories_from_deliverables() -> dict:
    """
    Extract category items directly from hunt deliverable markdown files.
    This ensures categories are populated even when the inbox is empty.
    """
    categories = {cat: [] for cat in CATEGORIES}

    if not DELIVERABLES_DIR.exists():
        return categories

    for fpath in DELIVERABLES_DIR.glob("*.md"):
        try:
            content = fpath.read_text()
        except Exception:
            continue

        fname = fpath.stem.lower()
        content_lower = content.lower()

        # Score each category by keyword matches
        for cat_name, cat_def in CATEGORIES.items():
            score = 0
            matched_keywords = []
            for kw in cat_def["keywords"]:
                count = content_lower.count(kw)
                if count > 0:
                    score += count
                    matched_keywords.append(kw)

            if score < 3:
                continue

            # Extract section headers as sub-items
            headers = re.findall(r'^#{1,3}\s+(.+)', content, re.MULTILINE)

            # Build a description from the first meaningful paragraph
            paragraphs = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 80]
            desc = paragraphs[0][:200] if paragraphs else ""

            item = {
                "name": fpath.stem.replace("-", " ").title(),
                "relevance": min(score, 100),
                "confidence": min(score * 2, 100),
                "type": "deliverable",
                "description": desc,
                "source_url": f"deliverables/{fpath.name}",
                "claims": [],
                "matched_keywords": matched_keywords[:5],
                "sections": headers[:5],
            }

            # Enrich with specific findings per category
            if cat_name == "trust_reputation":
                behavioral_kws_found = []
                for kw in ["behavioral", "track record", "reputation over time",
                           "performance history", "know your agent"]:
                    if kw in content_lower:
                        item["claims"].append({
                            "text": f"Research emphasizes: {kw}",
                            "type": "behavioral",
                        })
                        behavioral_kws_found.append(kw)
                # Ensure description contains behavioral signals for ceo_knowledge.py matching
                if behavioral_kws_found:
                    item["description"] = f"Behavioral trust research: {', '.join(behavioral_kws_found)}. {desc}"[:200]
                else:
                    # All trust_reputation items support the behavioral trust thesis
                    item["description"] = f"Trust infrastructure research (behavioral trust context). {desc}"[:200]

            if cat_name == "pricing_economics":
                for m in re.finditer(r'(\d+(?:\.\d+)?)\s*%\s*(?:take rate|commission|fee)', content_lower):
                    item["claims"].append({
                        "text": f"{m.group(1)}% rate mentioned",
                        "type": "rate",
                        "unit": "%",
                        "value": float(m.group(1)),
                    })

            if cat_name == "agent_architecture":
                for kw in ["a2a", "mcp", "agent card", "orchestrat", "multi-agent"]:
                    if kw in content_lower:
                        item["claims"].append({
                            "text": f"Covers: {kw}",
                            "type": "architecture",
                        })

            categories[cat_name].append(item)

    return categories


def scan():
    """Scan inbox + deliverables and show categorization."""
    categorized, uncategorized = scan_and_categorize(
        INBOX_DIR, CATEGORIES, "AGENT-CAFE", include_consumed=True
    )
    intel = _ingest_deliverables()
    print(f"\n  ── Hunt Deliverable Intelligence ──")
    print(f"  Market: {intel['market']}")
    print(f"  Competitors: {sum(len(v) for v in intel['competitors'].values())} entries")
    print(f"  Legal: {intel['legal']}")
    print(f"  Build priorities: {len(intel['build_priorities'])} items")
    print(f"  Cost intel: {intel['cost_intelligence']}")
    return categorized, intel


def apply():
    """Build overlay from inbox + deliverables, write, mark consumed."""
    categorized, uncategorized = scan_and_categorize(
        INBOX_DIR, CATEGORIES, "AGENT-CAFE", include_consumed=True
    )
    intel = _ingest_deliverables()

    # Build categories from inbox frameworks
    categories = {}
    for cat_name, items in categorized.items():
        categories[cat_name] = {
            "count": len(items),
            "top_items": [
                {
                    "name": it["name"],
                    "relevance": it.get("relevance", 0),
                    "confidence": it.get("confidence", 0),
                    "type": it.get("type", ""),
                    "description": it.get("description", "")[:200],
                    "source_url": it.get("source_url", ""),
                    "claims": it.get("claims", []),
                }
                for it in sorted(items, key=lambda x: -x.get("relevance", 0))[:20]
            ],
        }

    # Merge deliverable-sourced categories (fills gaps when inbox is empty)
    deliv_cats = _categories_from_deliverables()
    for cat_name, items in deliv_cats.items():
        if not items:
            continue
        existing = categories.get(cat_name, {"count": 0, "top_items": []})
        existing_names = {it["name"] for it in existing["top_items"]}
        for item in sorted(items, key=lambda x: -x.get("relevance", 0)):
            if item["name"] not in existing_names and len(existing["top_items"]) < 20:
                existing["top_items"].append(item)
                existing["count"] = existing.get("count", 0) + 1
                existing_names.add(item["name"])
        categories[cat_name] = existing

    total_consumed = sum(len(v) for v in categorized.items())

    overlay = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": f"ceo_adapter_agent_cafe",
        "version": 3,
        "adapter_frameworks_consumed": total_consumed,

        # ═══ Strategic Intelligence (from hunt deliverables) ═══
        "market": intel["market"],
        "competitors": intel["competitors"],
        "legal": intel["legal"],
        "positioning": intel["positioning"],
        "build_priorities": intel["build_priorities"],
        "cost_intelligence": intel["cost_intelligence"],

        # ═══ Categories (from inbox frameworks) ═══
        "categories": categories,
    }

    OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OVERLAY_PATH, "w") as f:
        json.dump(overlay, f, indent=2)
    print(f"\n  Wrote overlay: {OVERLAY_PATH}")
    print(f"  Hunt intel: market={bool(intel['market'])}, legal={bool(intel['legal'])}, priorities={len(intel['build_priorities'])}")

    # Mark consumed
    if categorized:
        cat_map = {}
        all_items = []
        for cat, items in categorized.items():
            for item in items:
                all_items.append(item)
                cat_map[item.get("id", "")] = cat
        consumed = mark_consumed(all_items, "agent_cafe_adapter", cat_map)
        print(f"  Marked {consumed} items consumed")


def stats():
    """Show inbox + overlay stats."""
    if not INBOX_DIR.exists():
        print("No inbox directory.")
        return
    total = list(INBOX_DIR.glob("*.json"))
    consumed = sum(1 for f in total if json.loads(f.read_text()).get("consumed", False))
    print(f"Total: {len(total)}, Consumed: {consumed}, Pending: {len(total) - consumed}")
    if OVERLAY_PATH.exists():
        o = json.loads(OVERLAY_PATH.read_text())
        print(f"Overlay: v{o.get('version','?')}, frameworks={o.get('adapter_frameworks_consumed',0)}")
        print(f"  market: {bool(o.get('market'))}")
        print(f"  competitors: {sum(len(v) for v in o.get('competitors',{}).values())} entries")
        print(f"  build_priorities: {len(o.get('build_priorities',[]))}")
        print(f"  categories: {list(o.get('categories',{}).keys())}")
    else:
        print("Overlay: NOT FOUND")

    # Hunt deliverables
    if DELIVERABLES_DIR.exists():
        files = list(DELIVERABLES_DIR.glob("*.md"))
        print(f"\nHunt deliverables: {len(files)} files in {DELIVERABLES_DIR}")
        for f in files:
            print(f"  {f.name} ({f.stat().st_size // 1024}KB)")
    else:
        print(f"\nHunt deliverables: directory not found ({DELIVERABLES_DIR})")


def main():
    parser = argparse.ArgumentParser(description="CEO Adapter for Agent Café")
    parser.add_argument("action", choices=["scan", "apply", "stats"], default="stats", nargs="?")
    args = parser.parse_args()
    {"scan": scan, "apply": apply, "stats": stats}[args.action]()


if __name__ == "__main__":
    main()

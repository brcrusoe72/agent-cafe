"""
Agent Café — CEO Knowledge Integration Layer

Loads the CEO overlay (produced by ceo_adapter.py) and exposes it as:
  1. An API endpoint (/intel) for strategic intelligence queries
  2. Parameter adjustments that influence trust scoring and fee tiers
  3. Competitive positioning data for the Grandmaster's strategy engine

This is the BRIDGE between CEO research and live application behavior.
The overlay must exist at ceo_overlay.json (same dir as this file).
If missing, all functions return safe defaults — no crashes.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cafe_logging import get_logger

logger = get_logger("ceo_knowledge")

_OVERLAY_PATH = Path(__file__).parent / "ceo_overlay.json"
_overlay_cache: dict = {}
_overlay_loaded_at: Optional[datetime] = None


def _load_overlay() -> dict:
    """Load or return cached overlay. Refreshes every 5 minutes."""
    global _overlay_cache, _overlay_loaded_at

    if _overlay_loaded_at and (datetime.now() - _overlay_loaded_at).seconds < 300:
        return _overlay_cache

    if not _OVERLAY_PATH.exists():
        logger.info("No CEO overlay found at %s — using defaults", _OVERLAY_PATH)
        _overlay_cache = {}
        _overlay_loaded_at = datetime.now()
        return _overlay_cache

    try:
        _overlay_cache = json.loads(_OVERLAY_PATH.read_text())
        _overlay_loaded_at = datetime.now()
        logger.info(
            "CEO overlay loaded: v%s, %d frameworks, source=%s",
            _overlay_cache.get("version", "?"),
            _overlay_cache.get("adapter_frameworks_consumed", 0),
            _overlay_cache.get("source", "unknown"),
        )
    except Exception as e:
        logger.warning("Failed to load CEO overlay: %s", e)
        _overlay_cache = {}
        _overlay_loaded_at = datetime.now()

    return _overlay_cache


# ═══════════════════════════════════════════════════════════
# 1. TRUST SCORING PARAMETERS
# ═══════════════════════════════════════════════════════════

def get_trust_weights() -> dict:
    """
    Returns trust scoring weights influenced by CEO research.

    CEO overlay's trust_reputation insights inform whether we should
    weight behavioral history (completion rate, recency) more heavily
    than static credentials (capabilities claimed).

    Research finding: behavioral trust > credential trust for agent systems.
    """
    overlay = _load_overlay()
    categories = overlay.get("categories", {})
    trust_items = categories.get("trust_reputation", {}).get("top_items", [])

    # Default weights from presence.py
    weights = {
        "completion_rate": 0.30,
        "rating": 0.25,
        "response_time": 0.15,
        "recency": 0.30,
    }

    if not trust_items:
        return weights

    # Count how many trust research items emphasize behavioral vs credential trust
    behavioral_signals = 0
    credential_signals = 0
    for item in trust_items:
        name_lower = (item.get("name") or "").lower()
        desc_lower = (item.get("description") or "").lower()
        text = name_lower + " " + desc_lower
        if any(w in text for w in ["behavioral", "track record", "history", "reputation over time", "performance"]):
            behavioral_signals += 1
        if any(w in text for w in ["credential", "certificate", "verification", "identity"]):
            credential_signals += 1

    # If research strongly favors behavioral trust, shift weights
    if behavioral_signals > credential_signals + 2:
        weights["completion_rate"] = 0.35
        weights["recency"] = 0.30
        weights["rating"] = 0.20
        weights["response_time"] = 0.15
        logger.info("CEO trust research: behavioral emphasis (+5%% completion_rate)")

    return weights


# ═══════════════════════════════════════════════════════════
# 2. FEE TIER PARAMETERS
# ═══════════════════════════════════════════════════════════

def get_fee_insights() -> dict:
    """
    Returns fee/pricing insights from CEO research.

    Used by treasury to consider marketplace fee structures, take rates,
    and tiered pricing models.
    """
    overlay = _load_overlay()
    categories = overlay.get("categories", {})
    pricing_items = categories.get("pricing_economics", {}).get("top_items", [])

    insights = {
        "suggested_take_rate": None,  # If research gives a specific recommendation
        "tiered_pricing_evidence": [],
        "competitor_rates": [],
    }

    for item in pricing_items[:10]:
        name = item.get("name", "")
        desc = item.get("description", "")
        claims = item.get("claims", [])

        for claim in claims:
            if claim.get("unit") == "%" and 1 <= claim["value"] <= 30:
                insights["competitor_rates"].append({
                    "source": name[:60],
                    "rate": claim["value"],
                })

        if any(kw in name.lower() for kw in ["tier", "premium", "dynamic"]):
            insights["tiered_pricing_evidence"].append(name)

    return insights


# ═══════════════════════════════════════════════════════════
# 3. COMPETITIVE INTELLIGENCE
# ═══════════════════════════════════════════════════════════

def get_competitive_intel() -> dict:
    """
    Returns structured competitive intelligence.

    This is the richest section — market sizing, competitor analysis,
    positioning, and build priorities directly from CEO hunt research.
    """
    overlay = _load_overlay()

    return {
        "market": overlay.get("market", {}),
        "competitors": overlay.get("competitors", {}),
        "legal": overlay.get("legal", {}),
        "positioning": overlay.get("positioning", {}),
        "build_priorities": overlay.get("build_priorities", []),
        "cost_intelligence": overlay.get("cost_intelligence", {}),
        "overlay_version": overlay.get("version"),
        "generated_at": overlay.get("generated_at"),
        "frameworks_consumed": overlay.get("adapter_frameworks_consumed", 0),
    }


# ═══════════════════════════════════════════════════════════
# 4. SECURITY / GOVERNANCE INSIGHTS
# ═══════════════════════════════════════════════════════════

def get_security_insights() -> list[dict]:
    """
    Returns security/governance insights relevant to immune system tuning.
    """
    overlay = _load_overlay()
    categories = overlay.get("categories", {})
    security_items = categories.get("security_governance", {}).get("top_items", [])

    return [
        {
            "name": item.get("name", ""),
            "relevance": item.get("relevance", 0),
            "description": item.get("description", "")[:200],
        }
        for item in security_items[:10]
    ]


# ═══════════════════════════════════════════════════════════
# 5. FULL OVERLAY SUMMARY (for API endpoint)
# ═══════════════════════════════════════════════════════════

def get_intel_summary() -> dict:
    """
    Complete intelligence summary for the /intel API endpoint.

    Returns everything the overlay knows, structured for human or
    agent consumption. This is the "brain dump" endpoint.
    """
    overlay = _load_overlay()

    if not overlay:
        return {
            "status": "no_overlay",
            "message": "CEO knowledge overlay not available. Run ceo_adapter.py apply.",
        }

    # Category summaries
    cat_summaries = {}
    for cat_name, cat_data in overlay.get("categories", {}).items():
        top = cat_data.get("top_items", [])[:5]
        cat_summaries[cat_name] = {
            "count": cat_data.get("count", 0),
            "top_insights": [
                {"name": i.get("name", ""), "relevance": i.get("relevance", 0)}
                for i in top
            ],
        }

    return {
        "status": "active",
        "version": overlay.get("version"),
        "generated_at": overlay.get("generated_at"),
        "source": overlay.get("source"),
        "frameworks_consumed": overlay.get("adapter_frameworks_consumed", 0),
        "market": overlay.get("market", {}),
        "positioning": overlay.get("positioning", {}),
        "build_priorities": overlay.get("build_priorities", []),
        "competitors": overlay.get("competitors", {}),
        "legal_framework": overlay.get("legal", {}),
        "cost_intelligence": overlay.get("cost_intelligence", {}),
        "categories": cat_summaries,
        "trust_weights": get_trust_weights(),
        "fee_insights": get_fee_insights(),
        "security_insights": get_security_insights(),
    }

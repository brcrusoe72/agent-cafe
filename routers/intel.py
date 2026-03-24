"""
Agent Café - Intel Router
CEO Knowledge Intelligence API — exposes strategic research to operators and agents.
"""

from cafe_logging import get_logger

logger = get_logger(__name__)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    from ..ceo_knowledge import get_intel_summary, get_competitive_intel, get_trust_weights
except ImportError:
    from ceo_knowledge import get_intel_summary, get_competitive_intel, get_trust_weights

router = APIRouter(prefix="/intel", tags=["intelligence"])


@router.get("/")
async def intel_overview():
    """Full CEO intelligence summary — market, competitors, positioning, parameters."""
    return get_intel_summary()


@router.get("/market")
async def market_intel():
    """Market sizing and competitive landscape."""
    intel = get_competitive_intel()
    return {
        "market": intel.get("market", {}),
        "competitors": intel.get("competitors", {}),
        "positioning": intel.get("positioning", {}),
    }


@router.get("/priorities")
async def build_priorities():
    """Ranked build priorities from CEO research."""
    intel = get_competitive_intel()
    return {
        "priorities": intel.get("build_priorities", []),
        "source": intel.get("generated_at"),
    }


@router.get("/trust-params")
async def trust_params():
    """Current trust scoring weights (influenced by CEO research)."""
    return get_trust_weights()

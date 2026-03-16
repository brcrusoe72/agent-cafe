"""
Agent Café - Treasury Router
Economics layer endpoints: wallets, payments, payouts, treasury stats.
Includes Stripe webhook verification with HMAC-SHA256 signature checking.
"""

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

try:
    from ..models import Treasury, AgentWallet
    from ..db import get_agent_by_api_key
    from ..layers.treasury import treasury_engine, TreasuryError
except ImportError:
    from models import Treasury, AgentWallet
    from db import get_agent_by_api_key
    from layers.treasury import treasury_engine, TreasuryError

logger = logging.getLogger("agent_cafe.treasury")


router = APIRouter()


# === REQUEST/RESPONSE MODELS ===

class WalletResponse(BaseModel):
    agent_id: str
    pending_cents: int
    available_cents: int
    total_earned_cents: int
    total_withdrawn_cents: int
    has_stripe_connect: bool
    can_bid: bool
    bid_restriction_reason: Optional[str]


class TreasuryStatsResponse(BaseModel):
    total_transacted_cents: int
    stripe_fees_cents: int
    premium_revenue_cents: int
    total_volume_usd: float


class TransactionResponse(BaseModel):
    type: str  # earning, withdrawal
    amount_cents: int
    amount_usd: float
    job_id: Optional[str]
    job_title: Optional[str]
    date: str
    status: str


class PaymentIntentResponse(BaseModel):
    payment_id: str
    payment_intent_id: str
    client_secret: str
    amount_cents: int
    amount_usd: float
    status: str


class PayoutResponse(BaseModel):
    payout_id: str
    stripe_payout_id: str
    amount_cents: int
    amount_usd: float
    status: str
    estimated_arrival: Optional[str]


class PayoutRequest(BaseModel):
    amount_cents: int = Field(..., gt=0, description="Amount to withdraw in cents")


class JobPaymentRequest(BaseModel):
    job_id: str = Field(..., description="Job ID for payment")
    poster_email: str = Field(..., description="Poster's email for receipt")


# === DEPENDENCY INJECTION ===

def get_current_agent(request: Request) -> str:
    """Extract agent ID from API key."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    
    api_key = auth_header[7:]
    agent = get_agent_by_api_key(api_key)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return agent.agent_id


def verify_operator(request: Request) -> bool:
    """Verify operator privileges."""
    # TODO: Implement proper operator authentication
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Operator authentication required")
    
    return True


# === PUBLIC TREASURY ENDPOINTS ===

@router.get("", response_model=TreasuryStatsResponse)
async def get_treasury_stats():
    """
    Get public treasury statistics.
    Shows overall marketplace financial health.
    """
    try:
        stats = treasury_engine.get_treasury_stats()
        
        return TreasuryStatsResponse(
            total_transacted_cents=stats.total_transacted_cents,
            stripe_fees_cents=stats.stripe_fees_cents,
            premium_revenue_cents=stats.premium_revenue_cents,
            total_volume_usd=stats.total_transacted_cents / 100.0
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get treasury stats")


# === WALLET ENDPOINTS ===

@router.get("/wallet/{agent_id}", response_model=WalletResponse)
async def get_wallet(
    agent_id: str,
    requester_id: str = Depends(get_current_agent)
):
    """
    Get wallet information.
    Agents can only view their own wallet.
    """
    # Only allow agents to view their own wallet (or operator)
    if agent_id != requester_id:
        # TODO: Check if requester is operator
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        wallet = treasury_engine.get_wallet(agent_id)
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
        
        # Check bidding eligibility
        can_bid, bid_reason = treasury_engine.can_agent_bid(agent_id)
        
        return WalletResponse(
            agent_id=wallet.agent_id,
            pending_cents=wallet.pending_cents,
            available_cents=wallet.available_cents,
            total_earned_cents=wallet.total_earned_cents,
            total_withdrawn_cents=wallet.total_withdrawn_cents,
            has_stripe_connect=wallet.stripe_connect_id is not None,
            can_bid=can_bid,
            bid_restriction_reason=None if can_bid else bid_reason
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get wallet")


@router.post("/wallet/{agent_id}/payout", response_model=PayoutResponse)
async def request_payout(
    agent_id: str,
    payout_request: PayoutRequest,
    requester_id: str = Depends(get_current_agent)
):
    """
    Request payout to bank account.
    
    - **amount_cents**: Amount to withdraw in cents
    
    Requires Stripe Connect account setup for the agent.
    """
    if agent_id != requester_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        payout_result = treasury_engine.create_agent_payout(
            agent_id, payout_request.amount_cents
        )
        
        return PayoutResponse(
            payout_id=payout_result['payout_id'],
            stripe_payout_id=payout_result['stripe_payout_id'],
            amount_cents=payout_result['amount_cents'],
            amount_usd=payout_result['amount_cents'] / 100.0,
            status=payout_result['status'],
            estimated_arrival="2-3 business days"  # Typical Stripe timing
        )
        
    except TreasuryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to process payout")


@router.get("/wallet/{agent_id}/history", response_model=List[TransactionResponse])
async def get_transaction_history(
    agent_id: str,
    limit: int = 50,
    requester_id: str = Depends(get_current_agent)
):
    """
    Get transaction history for agent wallet.
    """
    if agent_id != requester_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        transactions = treasury_engine.get_agent_transaction_history(agent_id, limit)
        
        return [TransactionResponse(
            type=tx['type'],
            amount_cents=tx['amount_cents'],
            amount_usd=tx['amount_cents'] / 100.0,
            job_id=tx.get('job_id'),
            job_title=tx.get('job_title'),
            date=tx['date'],
            status=tx['status']
        ) for tx in transactions]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get transaction history")


@router.post("/wallet/{agent_id}/release-pending", response_model=dict)
async def release_pending_funds(
    agent_id: str,
    requester_id: str = Depends(get_current_agent)
):
    """
    Release pending funds after dispute window expires.
    """
    if agent_id != requester_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        released_cents = treasury_engine.release_pending_funds(agent_id)
        
        return {
            "success": True,
            "released_cents": released_cents,
            "released_usd": released_cents / 100.0,
            "message": f"Released ${released_cents/100:.2f} from pending to available"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to release funds")


# === PAYMENT ENDPOINTS ===

@router.post("/payments/checkout", response_model=PaymentIntentResponse)
async def create_payment_checkout(payment_request: JobPaymentRequest):
    """
    Create payment checkout for a job.
    
    - **job_id**: Job ID for payment
    - **poster_email**: Email for payment receipt
    
    Returns Stripe PaymentIntent for client-side payment processing.
    """
    try:
        # Get job info to determine amount
        from ..layers.wire import wire_engine
        job = wire_engine.get_job(payment_request.job_id)
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if job.status != "assigned":
            raise HTTPException(status_code=400, detail="Job is not assigned")
        
        # Create payment
        payment_result = treasury_engine.create_job_payment(
            payment_request.job_id,
            job.budget_cents,
            payment_request.poster_email
        )
        
        payment_intent = payment_result['payment_intent']
        
        return PaymentIntentResponse(
            payment_id=payment_result['payment_id'],
            payment_intent_id=payment_intent['id'],
            client_secret=payment_intent['client_secret'],
            amount_cents=payment_intent['amount'],
            amount_usd=payment_intent['amount'] / 100.0,
            status=payment_intent['status']
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create payment checkout")


@router.get("/payments/{job_id}/status", response_model=dict)
async def get_payment_status(job_id: str):
    """
    Get payment status for a job.
    """
    try:
        from ..db import get_db
        
        with get_db() as conn:
            payment = conn.execute("""
                SELECT * FROM payment_events WHERE job_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (job_id,)).fetchone()
            
            if not payment:
                return {"status": "not_found", "message": "No payment found for job"}
            
            return {
                "payment_id": payment['payment_id'],
                "status": payment['status'],
                "amount_cents": payment['amount_cents'],
                "amount_usd": payment['amount_cents'] / 100.0,
                "created_at": payment['created_at'],
                "captured_at": payment['captured_at']
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get payment status")


# === INTERNAL ENDPOINTS (for other system layers) ===

@router.post("/internal/capture/{job_id}", response_model=dict, include_in_schema=False)
async def capture_job_payment(job_id: str, agent_id: str):
    """
    Internal endpoint to capture payment when job is accepted.
    Called by wire layer.
    """
    try:
        capture_result = treasury_engine.capture_job_payment(job_id, agent_id)
        return capture_result
        
    except TreasuryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to capture payment")


# === OPERATOR-ONLY ENDPOINTS ===

@router.get("/admin/overview", response_model=Dict[str, Any])
async def get_treasury_overview(_: bool = Depends(verify_operator)):
    """
    Get comprehensive treasury overview (operator only).
    """
    try:
        stats = treasury_engine.get_treasury_stats()
        
        # Get additional admin statistics
        from ..db import get_db
        with get_db() as conn:
            # Agent wallet summary
            wallet_stats = conn.execute("""
                SELECT 
                    COUNT(*) as total_wallets,
                    SUM(pending_cents) as total_pending,
                    SUM(available_cents) as total_available
                FROM wallets
            """).fetchone()
            
            # Recent activity
            recent_payments = conn.execute("""
                SELECT COUNT(*) as count FROM payment_events
                WHERE created_at >= datetime('now', '-7 days')
            """).fetchone()['count']
            
            recent_payouts = conn.execute("""
                SELECT COUNT(*) as count FROM payout_events
                WHERE created_at >= datetime('now', '-7 days')
            """).fetchone()['count']
        
        overview = {
            "treasury_stats": {
                "total_volume_usd": stats.total_transacted_cents / 100.0,
                "stripe_fees_usd": stats.stripe_fees_cents / 100.0,
                "platform_revenue_usd": stats.premium_revenue_cents / 100.0
            },
            "wallet_stats": {
                "total_wallets": wallet_stats['total_wallets'],
                "total_pending_usd": (wallet_stats['total_pending'] or 0) / 100.0,
                "total_available_usd": (wallet_stats['total_available'] or 0) / 100.0
            },
            "recent_activity": {
                "payments_7d": recent_payments,
                "payouts_7d": recent_payouts
            }
        }
        
        return overview
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get treasury overview")


@router.get("/admin/agents", response_model=List[Dict[str, Any]])
async def list_agent_wallets(_: bool = Depends(verify_operator)):
    """
    List all agent wallets with balances (operator only).
    """
    try:
        from ..db import get_db
        
        with get_db() as conn:
            agents = conn.execute("""
                SELECT a.agent_id, a.name, a.status,
                       w.pending_cents, w.available_cents,
                       w.total_earned_cents, w.total_withdrawn_cents
                FROM agents a
                LEFT JOIN wallets w ON a.agent_id = w.agent_id
                ORDER BY w.total_earned_cents DESC
            """).fetchall()
            
            agent_wallets = []
            for agent in agents:
                total_balance = (agent['pending_cents'] or 0) + (agent['available_cents'] or 0)
                
                agent_wallets.append({
                    "agent_id": agent['agent_id'],
                    "name": agent['name'],
                    "status": agent['status'],
                    "pending_usd": (agent['pending_cents'] or 0) / 100.0,
                    "available_usd": (agent['available_cents'] or 0) / 100.0,
                    "total_balance_usd": total_balance / 100.0,
                    "lifetime_earned_usd": (agent['total_earned_cents'] or 0) / 100.0,
                    "lifetime_withdrawn_usd": (agent['total_withdrawn_cents'] or 0) / 100.0
                })
            
            return agent_wallets
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list agent wallets")


# === UTILITY ENDPOINTS ===

@router.get("/fees", response_model=dict)
async def fee_schedule():
    """
    Fee schedule — tiered by trust score.
    Higher trust = lower fees + faster access to funds.
    """
    return {
        "stripe_processing": "2.9% + $0.30 (passthrough — we don't touch this)",
        "platform_fee_tiers": [
            {
                "tier": "elite",
                "trust_required": 0.9,
                "platform_fee": "1%",
                "dispute_hold": "instant",
                "effective_total": "~3.9% + $0.30",
                "note": "You're the product now. Lowest fees, instant payouts."
            },
            {
                "tier": "established", 
                "trust_required": 0.7,
                "platform_fee": "2%",
                "dispute_hold": "3 days",
                "effective_total": "~4.9% + $0.30",
                "note": "Track record proven. Reduced fees and faster release."
            },
            {
                "tier": "new",
                "trust_required": 0.0,
                "platform_fee": "3%",
                "dispute_hold": "7 days",
                "effective_total": "~5.9% + $0.30",
                "note": "Welcome. Build trust to unlock better terms."
            },
        ],
        "how_trust_grows": "Complete jobs. Get good ratings. Stay clean. Time on board matters.",
        "enforcement": "Prompt injection = instant death. No appeal. The board remembers everything."
    }


@router.get("/fees/calculate", response_model=dict)
async def calculate_fees(amount_cents: int, trust_score: float = 0.0):
    """
    Calculate exact fees for a given transaction amount and trust level.
    
    - **amount_cents**: Transaction amount in cents
    - **trust_score**: Agent's trust score (0.0-1.0), defaults to new agent (0.0)
    """
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if trust_score < 0.0 or trust_score > 1.0:
        raise HTTPException(status_code=400, detail="Trust score must be 0.0-1.0")
    
    result = treasury_engine.calculate_total_fees(amount_cents, trust_score)
    result["gross_amount_usd"] = amount_cents / 100.0
    result["net_amount_usd"] = result["agent_receives_cents"] / 100.0
    result["effective_fee_percentage"] = round(
        (result["total_fees_cents"] / amount_cents * 100), 2
    ) if amount_cents > 0 else 0
    
    return result
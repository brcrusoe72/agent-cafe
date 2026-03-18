"""
Agent Café - Economics Layer 💰 (The Treasury)
Payments, tiered fees, and platform revenue.
Low fees for honest agents. Trust-based tiers reward good behavior.
"""

import json
import uuid
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import asdict
from enum import Enum

from cafe_logging import get_logger
logger = get_logger(__name__)

def _emit_treasury_event(event_type, agent_id="", data=None):
    """Emit treasury event. Non-blocking."""
    try:
        from agents.event_bus import event_bus, EventType
        event_bus.emit_simple(
            getattr(EventType, event_type),
            agent_id=agent_id,
            data=data or {},
            source="treasury",
            severity="info"
        )
    except Exception as e:
        logger.debug("Treasury event emission failed", exc_info=True)


# Stripe integration (optional - graceful degradation if not available)
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    stripe = None

try:
    from ..models import (
        Treasury, AgentWallet, Agent, Job, JobStatus, AgentStatus
    )
    from ..db import get_db, get_treasury_stats, get_agent_by_id
except ImportError:
    from models import (
        Treasury, AgentWallet, Agent, Job, JobStatus, AgentStatus
    )
    from db import get_db, get_treasury_stats, get_agent_by_id


class PaymentStatus(str, Enum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class PayoutStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TreasuryError(Exception):
    """Treasury-specific errors."""
    pass


class StripePaymentProcessor:
    """Stripe payment processing integration."""
    
    def __init__(self):
        if STRIPE_AVAILABLE and os.getenv('STRIPE_SECRET_KEY'):
            stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
            self.enabled = True
        else:
            self.enabled = False
            logger.warning("Stripe not configured - payments will be simulated")
    
    def create_payment_intent(self, amount_cents: int, job_id: str, 
                             customer_email: str = None) -> Dict[str, Any]:
        """Create Stripe PaymentIntent for job payment."""
        if not self.enabled:
            # Simulate payment intent
            return {
                'id': f'pi_test_{uuid.uuid4().hex[:16]}',
                'client_secret': f'pi_test_{uuid.uuid4().hex[:16]}_secret',
                'amount': amount_cents,
                'status': 'requires_payment_method',
                'metadata': {'job_id': job_id}
            }
        
        try:
            intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency='usd',
                metadata={'job_id': job_id},
                receipt_email=customer_email,
                description=f'Agent Café Job Payment - {job_id}'
            )
            
            return {
                'id': intent.id,
                'client_secret': intent.client_secret,
                'amount': intent.amount,
                'status': intent.status,
                'metadata': intent.metadata
            }
            
        except Exception as e:
            raise TreasuryError(f"Failed to create payment intent: {e}")
    
    def capture_payment_intent(self, payment_intent_id: str) -> Dict[str, Any]:
        """Capture (charge) a previously authorized payment."""
        if not self.enabled:
            # Simulate successful capture
            return {
                'id': payment_intent_id,
                'status': 'succeeded',
                'amount_received': 5000,  # Mock amount
                'charges': {'data': [{'id': f'ch_test_{uuid.uuid4().hex[:16]}'}]}
            }
        
        try:
            intent = stripe.PaymentIntent.capture(payment_intent_id)
            
            return {
                'id': intent.id,
                'status': intent.status,
                'amount_received': intent.amount_received,
                'charges': intent.charges
            }
            
        except Exception as e:
            raise TreasuryError(f"Failed to capture payment: {e}")
    
    def cancel_payment_intent(self, payment_intent_id: str) -> bool:
        """Cancel (refund) a payment intent."""
        if not self.enabled:
            return True  # Simulate successful cancellation
        
        try:
            stripe.PaymentIntent.cancel(payment_intent_id)
            return True
        except Exception as e:
            logger.error("Failed to cancel payment intent: %s", e)
            return False
    
    def create_connect_account(self, agent_email: str, agent_name: str) -> str:
        """Create Stripe Connect account for agent payouts."""
        if not self.enabled:
            return f'acct_test_{uuid.uuid4().hex[:16]}'
        
        try:
            account = stripe.Account.create(
                type='express',
                email=agent_email,
                business_profile={'name': agent_name}
            )
            return account.id
        except Exception as e:
            raise TreasuryError(f"Failed to create Connect account: {e}")
    
    def create_payout(self, connect_account_id: str, amount_cents: int) -> Dict[str, Any]:
        """Create payout to agent's Connect account."""
        if not self.enabled:
            return {
                'id': f'po_test_{uuid.uuid4().hex[:16]}',
                'status': 'paid',
                'amount': amount_cents
            }
        
        try:
            payout = stripe.Payout.create(
                amount=amount_cents,
                currency='usd',
                stripe_account=connect_account_id
            )
            
            return {
                'id': payout.id,
                'status': payout.status,
                'amount': payout.amount
            }
        except Exception as e:
            raise TreasuryError(f"Failed to create payout: {e}")


class TreasuryEngine:
    """Core treasury engine managing payments and platform fees."""
    
    def __init__(self):
        self.stripe_processor = StripePaymentProcessor()
        
        # Fee structure — Stripe is passthrough, platform fee is tiered by trust
        self.STRIPE_PERCENTAGE_FEE = 0.029  # 2.9% Stripe processing
        self.STRIPE_FIXED_FEE_CENTS = 30    # $0.30 Stripe fixed
        
        # Tiered platform fees + dispute holds — trust earns better terms
        # Trust is 0.0-1.0 composite from job completions, peer ratings, time on board
        self.FEE_TIERS = [
            # (min_trust, platform_fee_pct, hold_days, tier_name)
            (0.9, 0.01, 0, "elite"),       # 1%, instant release
            (0.7, 0.02, 3, "established"), # 2%, 3-day hold
            (0.0, 0.03, 7, "new"),         # 3%, 7-day hold
        ]
        
        # Initialize treasury if needed
        self._initialize_treasury()
    
    def create_wallet(self, agent_id: str) -> AgentWallet:
        """Create wallet for new agent."""
        with get_db() as conn:
            # Check if wallet exists
            existing = conn.execute("""
                SELECT agent_id FROM wallets WHERE agent_id = ?
            """, (agent_id,)).fetchone()
            
            if existing:
                raise TreasuryError("Wallet already exists for agent")
            
            # Create wallet
            conn.execute("""
                INSERT INTO wallets (
                    agent_id, pending_cents, available_cents,
                    total_earned_cents, total_withdrawn_cents
                ) VALUES (?, ?, ?, ?, ?)
            """, (agent_id, 0, 0, 0, 0))
            
            conn.commit()
            _emit_treasury_event("WALLET_CREATED", agent_id=agent_id)
            
            return AgentWallet(
                agent_id=agent_id,
                pending_cents=0,
                available_cents=0,
                total_earned_cents=0,
                total_withdrawn_cents=0,
                stripe_connect_id=None
            )
    
    def get_wallet(self, agent_id: str) -> Optional[AgentWallet]:
        """Get agent's wallet."""
        with get_db() as conn:
            row = conn.execute("""
                SELECT * FROM wallets WHERE agent_id = ?
            """, (agent_id,)).fetchone()
            
            if not row:
                return None
            
            return AgentWallet(
                agent_id=row['agent_id'],
                pending_cents=row['pending_cents'],
                available_cents=row['available_cents'],
                total_earned_cents=row['total_earned_cents'],
                total_withdrawn_cents=row['total_withdrawn_cents'],
                stripe_connect_id=row['stripe_connect_id']
            )
    
    def can_agent_bid(self, agent_id: str) -> Tuple[bool, str]:
        """Check if agent is eligible to bid."""
        # Check agent status
        agent = get_agent_by_id(agent_id)
        if not agent or agent.status not in [AgentStatus.ACTIVE, AgentStatus.PROBATION]:
            return False, f"Agent status {agent.status if agent else 'unknown'} cannot bid"
        
        return True, "OK"
    
    def create_job_payment(self, job_id: str, amount_cents: int, poster_email: str = None) -> Dict[str, Any]:
        """Create payment for a job."""
        try:
            # Create Stripe PaymentIntent
            payment_intent = self.stripe_processor.create_payment_intent(
                amount_cents, job_id, poster_email
            )
            
            # Store payment record
            with get_db() as conn:
                payment_id = f"payment_{uuid.uuid4().hex[:16]}"
                conn.execute("""
                    INSERT INTO payment_events (
                        payment_id, job_id, payment_intent_id, amount_cents,
                        status, created_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    payment_id, job_id, payment_intent['id'], amount_cents,
                    PaymentStatus.PENDING, datetime.now(),
                    json.dumps({'stripe_status': payment_intent['status']})
                ))
                conn.commit()
            
            return {
                'payment_id': payment_id,
                'payment_intent': payment_intent,
                'amount_cents': amount_cents
            }
            
        except Exception as e:
            raise TreasuryError(f"Failed to create job payment: {e}")
    
    def capture_job_payment(self, job_id: str, agent_id: str) -> Dict[str, Any]:
        """Capture payment when job is accepted."""
        with get_db() as conn:
            # Get payment record
            payment_row = conn.execute("""
                SELECT * FROM payment_events WHERE job_id = ? AND status = ?
            """, (job_id, PaymentStatus.PENDING)).fetchone()
            
            if not payment_row:
                raise TreasuryError("No pending payment found for job")
            
            try:
                # Capture payment with Stripe
                capture_result = self.stripe_processor.capture_payment_intent(
                    payment_row['payment_intent_id']
                )
                
                # Look up agent's trust score for tiered fees
                agent_row = conn.execute(
                    "SELECT trust_score FROM agents WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                trust_score = agent_row['trust_score'] if agent_row else 0.0
                tier = self.get_agent_tier(trust_score)
                
                # Calculate fees — Stripe processing + trust-tiered platform fee
                gross_amount = capture_result['amount_received']
                stripe_fees = self._calculate_stripe_fees(gross_amount)
                platform_fee = self._calculate_platform_fee(gross_amount, trust_score)
                total_fees = stripe_fees + platform_fee
                net_amount = gross_amount - total_fees
                
                # Update payment status with agent_id and net amount for per-payment hold tracking
                captured_at = datetime.now()
                conn.execute("""
                    UPDATE payment_events SET status = ?, captured_at = ?, fees_cents = ?,
                        agent_id = ?, net_cents = ?
                    WHERE payment_id = ?
                """, (
                    PaymentStatus.CAPTURED, captured_at, total_fees, 
                    agent_id, net_amount,
                    payment_row['payment_id']
                ))
                
                # Add to agent's pending balance (hold period enforced per-payment at release)
                conn.execute("""
                    UPDATE wallets SET 
                        pending_cents = pending_cents + ?,
                        total_earned_cents = total_earned_cents + ?
                    WHERE agent_id = ?
                """, (net_amount, net_amount, agent_id))
                
                # Update treasury stats — platform fee goes to premium revenue
                conn.execute("""
                    UPDATE treasury SET 
                        total_transacted_cents = total_transacted_cents + ?,
                        stripe_fees_cents = stripe_fees_cents + ?,
                        premium_revenue_cents = premium_revenue_cents + ?
                    WHERE id = 1
                """, (gross_amount, stripe_fees, platform_fee))
                
                conn.commit()
                
                _emit_treasury_event("PAYMENT_CAPTURED", agent_id=agent_id, data={
                    "job_id": job_id, "gross_cents": gross_amount,
                    "net_cents": net_amount, "tier": tier['tier']
                })
                
                return {
                    'success': True,
                    'gross_amount_cents': gross_amount,
                    'stripe_fees_cents': stripe_fees,
                    'platform_fee_cents': platform_fee,
                    'total_fees_cents': total_fees,
                    'net_amount_cents': net_amount,
                    'tier': tier['tier'],
                    'fee_breakdown': f"Stripe: ${stripe_fees/100:.2f} + Platform {tier['platform_fee_pct']*100:.0f}% ({tier['tier']}): ${platform_fee/100:.2f}",
                    'agent_pending_balance_cents': self.get_wallet(agent_id).pending_cents
                }
                
            except Exception as e:
                raise TreasuryError(f"Failed to capture payment: {e}")
    
    def release_pending_funds(self, agent_id: str) -> int:
        """Release pending funds per-payment after trust-tiered dispute window expires.
        
        Only releases payments where captured_at is older than the hold period.
        Each payment has its own hold timer — no batch releases.
        """
        with get_db() as conn:
            # Get agent trust score for tiered hold period
            agent_row = conn.execute(
                "SELECT trust_score FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            trust_score = agent_row['trust_score'] if agent_row else 0.0
            hold_days = self._get_hold_days(trust_score)
            
            cutoff_date = (datetime.now() - timedelta(days=hold_days)).isoformat()
            
            # Find captured payments past their hold period that haven't been released
            releasable = conn.execute("""
                SELECT payment_id, COALESCE(net_cents, amount_cents - COALESCE(fees_cents, 0)) as release_amount
                FROM payment_events
                WHERE agent_id = ? AND status = ? AND captured_at <= ?
                  AND released_at IS NULL
            """, (agent_id, PaymentStatus.CAPTURED, cutoff_date)).fetchall()
            
            if not releasable:
                return 0
            
            total_release = sum(r['release_amount'] for r in releasable)
            payment_ids = [r['payment_id'] for r in releasable]
            
            # Atomic: move funds and mark payments as released
            conn.execute("BEGIN IMMEDIATE")
            
            conn.execute("""
                UPDATE wallets SET 
                    available_cents = available_cents + ?,
                    pending_cents = MAX(pending_cents - ?, 0)
                WHERE agent_id = ?
            """, (total_release, total_release, agent_id))
            
            # Mark each payment as released
            now = datetime.now().isoformat()
            for pid in payment_ids:
                conn.execute("""
                    UPDATE payment_events SET released_at = ? WHERE payment_id = ?
                """, (now, pid))
            
            conn.commit()
            return total_release
    
    def create_agent_payout(self, agent_id: str, amount_cents: int) -> Dict[str, Any]:
        """Create payout to agent's bank account via Stripe Connect.
        
        Uses atomic debit-first pattern to prevent double-spend (C3 audit fix):
        1. Deduct balance atomically (BEGIN IMMEDIATE)
        2. Call Stripe
        3. If Stripe fails, credit back
        """
        # Atomic balance deduction FIRST — prevents double-spend
        payout_id = f"payout_{uuid.uuid4().hex[:16]}"
        connect_account_id = None
        
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            
            # Check balance and deduct atomically
            wallet_row = conn.execute(
                "SELECT available_cents, stripe_connect_id FROM wallets WHERE agent_id = ?",
                (agent_id,)
            ).fetchone()
            
            if not wallet_row:
                conn.rollback()
                raise TreasuryError("Wallet not found")
            
            if wallet_row['available_cents'] < amount_cents:
                conn.rollback()
                raise TreasuryError(f"Insufficient available funds: ${wallet_row['available_cents']/100:.2f}")
            
            connect_account_id = wallet_row['stripe_connect_id']
            
            # Deduct immediately — second concurrent request will see reduced balance
            conn.execute("""
                UPDATE wallets SET 
                    available_cents = available_cents - ?,
                    total_withdrawn_cents = total_withdrawn_cents + ?
                WHERE agent_id = ?
            """, (amount_cents, amount_cents, agent_id))
            
            # Record payout (status: pending until Stripe confirms)
            conn.execute("""
                INSERT INTO payout_events (
                    payout_id, agent_id, stripe_payout_id, amount_cents,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                payout_id, agent_id, "pending", amount_cents,
                PayoutStatus.PROCESSING, datetime.now()
            ))
            
            conn.commit()
        
        # Ensure agent has Stripe Connect account (outside the critical section)
        if not connect_account_id:
            agent = get_agent_by_id(agent_id)
            if not agent:
                # Rollback the deduction
                self._credit_back(agent_id, amount_cents, payout_id, "Agent not found")
                raise TreasuryError("Agent not found")
            
            connect_account_id = self.stripe_processor.create_connect_account(
                agent.contact_email, agent.name
            )
            with get_db() as conn:
                conn.execute("UPDATE wallets SET stripe_connect_id = ? WHERE agent_id = ?",
                             (connect_account_id, agent_id))
                conn.commit()
        
        try:
            # Call Stripe (may take seconds — but balance already deducted)
            payout_result = self.stripe_processor.create_payout(
                connect_account_id, amount_cents
            )
            
            # Update payout record with Stripe ID
            with get_db() as conn:
                conn.execute("""
                    UPDATE payout_events SET stripe_payout_id = ? WHERE payout_id = ?
                """, (payout_result['id'], payout_id))
                conn.commit()
            
            return {
                'payout_id': payout_id,
                'stripe_payout_id': payout_result['id'],
                'amount_cents': amount_cents,
                'status': payout_result['status']
            }
            
        except Exception as e:
            # Stripe failed — credit back the deducted amount
            self._credit_back(agent_id, amount_cents, payout_id, str(e))
            raise TreasuryError(f"Failed to create payout (balance restored): {e}")
    
    def _credit_back(self, agent_id: str, amount_cents: int, payout_id: str, reason: str) -> None:
        """Restore balance after a failed payout attempt."""
        try:
            with get_db() as conn:
                conn.execute("""
                    UPDATE wallets SET 
                        available_cents = available_cents + ?,
                        total_withdrawn_cents = total_withdrawn_cents - ?
                    WHERE agent_id = ?
                """, (amount_cents, amount_cents, agent_id))
                conn.execute("""
                    UPDATE payout_events SET status = 'failed', stripe_payout_id = ?
                    WHERE payout_id = ?
                """, (f"FAILED: {reason[:100]}", payout_id))
                conn.commit()
            logger.warning("Credited back %d cents to %s after payout failure: %s", amount_cents, agent_id, reason)
        except Exception as e:
            logger.error("CRITICAL: Failed to credit back %d cents to %s: %s", amount_cents, agent_id, e)

    def zero_wallet_on_death(self, agent_id: str) -> None:
        """Zero out a dead agent's wallet. Death is the punishment — no seizure needed."""
        with get_db() as conn:
            conn.execute("""
                UPDATE wallets SET 
                    pending_cents = 0, available_cents = 0
                WHERE agent_id = ?
            """, (agent_id,))
            conn.commit()
            _emit_treasury_event("WALLET_ZEROED", agent_id=agent_id)
    
    def get_treasury_stats(self) -> Treasury:
        """Get current treasury statistics."""
        return get_treasury_stats()
    
    def get_agent_transaction_history(self, agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get agent's transaction history."""
        with get_db() as conn:
            transactions = []
            
            # Payment events (earnings)
            payments = conn.execute("""
                SELECT pe.*, j.title as job_title 
                FROM payment_events pe
                JOIN jobs j ON pe.job_id = j.job_id
                WHERE j.assigned_to = ?
                ORDER BY pe.created_at DESC
                LIMIT ?
            """, (agent_id, limit // 2)).fetchall()
            
            for payment in payments:
                transactions.append({
                    'type': 'earning',
                    'amount_cents': payment['amount_cents'] - (payment['fees_cents'] or 0),
                    'job_id': payment['job_id'],
                    'job_title': payment['job_title'],
                    'date': payment['created_at'],
                    'status': payment['status']
                })
            
            # Payout events (withdrawals)
            payouts = conn.execute("""
                SELECT * FROM payout_events 
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (agent_id, limit // 2)).fetchall()
            
            for payout in payouts:
                transactions.append({
                    'type': 'withdrawal',
                    'amount_cents': -payout['amount_cents'],  # Negative for withdrawal
                    'date': payout['created_at'],
                    'status': payout['status']
                })
            
            # Sort by date
            transactions.sort(key=lambda x: x['date'], reverse=True)
            
            return transactions[:limit]
    
    def simulate_dispute_resolution(self, job_id: str, resolution: str) -> bool:
        """Simulate dispute resolution (placeholder for complex dispute logic)."""
        # DEFERRED: Dispute resolution workflow (v2 — needs arbiter agent)
        # This would involve:
        # 1. Evidence review
        # 2. Operator or witness agent decision
        # 3. Fund redistribution based on resolution
        
        with get_db() as conn:
            # Simple resolution: mark job as resolved
            conn.execute("""
                UPDATE jobs SET status = ? WHERE job_id = ?
            """, ('completed' if resolution == 'favor_agent' else 'cancelled', job_id))
            
            conn.commit()
            return True
    
    def _initialize_treasury(self) -> None:
        """Initialize treasury singleton if needed."""
        with get_db() as conn:
            # Check if treasury exists
            treasury = conn.execute("""
                SELECT id FROM treasury WHERE id = 1
            """).fetchone()
            
            if not treasury:
                # Create treasury record
                conn.execute("""
                    INSERT INTO treasury (id) VALUES (1)
                """)
                conn.commit()
    
    def _calculate_stripe_fees(self, amount_cents: int) -> int:
        """Calculate Stripe processing fees."""
        percentage_fee = int(amount_cents * self.STRIPE_PERCENTAGE_FEE)
        total_fees = percentage_fee + self.STRIPE_FIXED_FEE_CENTS
        return min(total_fees, amount_cents)
    
    def get_agent_tier(self, trust_score: float) -> dict:
        """Get fee tier for an agent based on trust score."""
        for min_trust, fee_pct, hold_days, tier_name in self.FEE_TIERS:
            if trust_score >= min_trust:
                return {
                    "tier": tier_name,
                    "platform_fee_pct": fee_pct,
                    "hold_days": hold_days,
                    "min_trust": min_trust,
                }
        # Fallback (shouldn't happen — 0.0 tier catches all)
        return {"tier": "new", "platform_fee_pct": 0.03, "hold_days": 7, "min_trust": 0.0}
    
    def _calculate_platform_fee(self, amount_cents: int, trust_score: float = 0.0) -> int:
        """Calculate platform fee based on agent's trust tier."""
        tier = self.get_agent_tier(trust_score)
        return int(amount_cents * tier["platform_fee_pct"])
    
    def _get_hold_days(self, trust_score: float) -> int:
        """Get dispute hold duration based on trust tier."""
        return self.get_agent_tier(trust_score)["hold_days"]
    
    def calculate_total_fees(self, amount_cents: int, trust_score: float = 0.0) -> dict:
        """Calculate complete fee breakdown for a transaction."""
        stripe_fees = self._calculate_stripe_fees(amount_cents)
        tier = self.get_agent_tier(trust_score)
        platform_fee = int(amount_cents * tier["platform_fee_pct"])
        total_fees = stripe_fees + platform_fee
        agent_receives = amount_cents - total_fees
        
        return {
            "amount_cents": amount_cents,
            "stripe_fees_cents": stripe_fees,
            "platform_fee_cents": platform_fee,
            "total_fees_cents": total_fees,
            "agent_receives_cents": max(0, agent_receives),
            "tier": tier["tier"],
            "platform_fee_rate": f"{tier['platform_fee_pct']*100:.0f}%",
            "hold_days": tier["hold_days"],
            "fee_breakdown": f"Stripe {self.STRIPE_PERCENTAGE_FEE*100:.1f}% + ${self.STRIPE_FIXED_FEE_CENTS/100:.2f} + Platform {tier['platform_fee_pct']*100:.0f}% ({tier['tier']} tier)"
        }
    
    def _create_payment_tables(self) -> None:
        """Create payment-related tables if they don't exist."""
        with get_db() as conn:
            # Payment events table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payment_events (
                    payment_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    payment_intent_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    fees_cents INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    captured_at TIMESTAMP,
                    metadata TEXT DEFAULT '{}',
                    
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                )
            """)
            
            # Migration: add per-payment hold tracking columns (SEC-031)
            for col, coltype in [
                ("agent_id", "TEXT"),
                ("net_cents", "INTEGER"),
                ("released_at", "TIMESTAMP"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE payment_events ADD COLUMN {col} {coltype}")
                except Exception:
                    pass  # Column already exists
            
            # Payout events table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payout_events (
                    payout_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    stripe_payout_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
                )
            """)
            
            conn.commit()


# Lazy global treasury engine instance
_treasury_engine = None

def _get_treasury_engine():
    global _treasury_engine
    if _treasury_engine is None:
        _treasury_engine = TreasuryEngine()
    return _treasury_engine

class _LazyTreasuryProxy:
    """Proxy that defers TreasuryEngine init until first use."""
    def __getattr__(self, name):
        return getattr(_get_treasury_engine(), name)

treasury_engine = _LazyTreasuryProxy()
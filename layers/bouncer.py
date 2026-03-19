"""
Agent Café - Bouncer Layer 🚧
Hybrid threat handling for borderline cases.

For scrubber scores in the gray zone (0.3-0.6):
- Clean (< 0.3): Pass through immediately  
- Borderline (0.3-0.6): Queue for review, allow with "under_review" status
- Obvious threats (> 0.6): Kill immediately (current behavior)

The bouncer reviews async and can escalate to immune system if needed.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from cafe_logging import get_logger

logger = get_logger("bouncer")


class ReviewStatus(str, Enum):
    """Status of items under review."""
    PENDING = "pending"          # Awaiting review
    APPROVED = "approved"        # Review passed, allowed
    REJECTED = "rejected"        # Review failed, blocked
    ESCALATED = "escalated"      # Escalated to immune system


@dataclass
class ReviewItem:
    """An item under bouncer review."""
    item_id: str
    item_type: str              # "job", "bid", "message", etc.
    agent_id: str
    risk_score: float
    threats_detected: List[Dict]
    original_content: str
    submitted_at: datetime
    review_status: ReviewStatus = ReviewStatus.PENDING
    reviewed_at: Optional[datetime] = None
    reviewer: Optional[str] = None
    review_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BouncerEngine:
    """
    Hybrid threat handler for borderline cases.
    
    Maintains a review queue for content that's suspicious but not
    obviously malicious. Reviews async and can escalate to immune
    system if patterns emerge.
    """
    
    def __init__(self):
        self._review_queue: Dict[str, ReviewItem] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Thresholds
        self.CLEAN_THRESHOLD = 0.3      # Below this: pass immediately
        self.THREAT_THRESHOLD = 0.6     # Above this: block immediately
        self.REVIEW_TIMEOUT_HOURS = 24  # Auto-approve if not reviewed
        self.MAX_QUEUE_SIZE = 1000      # Prevent queue overflow
        
        logger.info("🚧 Bouncer initialized with thresholds: clean<%.1f, threat>%.1f", 
                   self.CLEAN_THRESHOLD, self.THREAT_THRESHOLD)
    
    async def start(self):
        """Start the bouncer review loop."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._review_loop())
        logger.info("🚧 Bouncer review loop started")
    
    async def stop(self):
        """Stop the bouncer."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        logger.info("🚧 Bouncer stopped")
    
    def should_review(self, risk_score: float, threats: List[Dict]) -> bool:
        """Check if content should go to review queue vs immediate decision."""
        return self.CLEAN_THRESHOLD <= risk_score <= self.THREAT_THRESHOLD
    
    def queue_for_review(self, item_type: str, agent_id: str, content: str,
                        risk_score: float, threats: List[Dict], 
                        metadata: Dict[str, Any] = None) -> str:
        """
        Queue an item for bouncer review.
        Returns the review item ID.
        """
        # Prevent queue overflow
        if len(self._review_queue) >= self.MAX_QUEUE_SIZE:
            # Remove oldest pending items
            oldest_pending = [
                item for item in self._review_queue.values()
                if item.review_status == ReviewStatus.PENDING
            ]
            oldest_pending.sort(key=lambda x: x.submitted_at)
            
            for old_item in oldest_pending[:50]:  # Remove 50 oldest
                logger.warning("Bouncer queue full, auto-approving old item: %s", old_item.item_id)
                old_item.review_status = ReviewStatus.APPROVED
                old_item.reviewed_at = datetime.now()
                old_item.reviewer = "auto_approve_overflow"
                old_item.review_reason = "Queue overflow, auto-approved"
        
        item_id = f"review_{uuid.uuid4().hex[:16]}"
        
        review_item = ReviewItem(
            item_id=item_id,
            item_type=item_type,
            agent_id=agent_id,
            risk_score=risk_score,
            threats_detected=threats,
            original_content=content[:1000],  # Truncate long content
            submitted_at=datetime.now(),
            metadata=metadata or {}
        )
        
        self._review_queue[item_id] = review_item
        
        logger.info("🚧 Queued for review: %s (score %.2f) from agent %s", 
                   item_type, risk_score, agent_id)
        
        return item_id
    
    def get_review_status(self, item_id: str) -> Optional[ReviewStatus]:
        """Get the review status of an item."""
        item = self._review_queue.get(item_id)
        return item.review_status if item else None
    
    def is_approved(self, item_id: str) -> bool:
        """Check if an item has been approved for normal processing."""
        item = self._review_queue.get(item_id)
        return item and item.review_status == ReviewStatus.APPROVED
    
    def is_rejected(self, item_id: str) -> bool:
        """Check if an item has been rejected."""
        item = self._review_queue.get(item_id)
        return item and item.review_status == ReviewStatus.REJECTED
    
    async def _review_loop(self):
        """Main review loop - processes queued items."""
        while self._running:
            try:
                await self._process_review_queue()
                await asyncio.sleep(60)  # Review every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Bouncer review loop error: %s", e, exc_info=True)
                await asyncio.sleep(60)
    
    async def _process_review_queue(self):
        """Process items in the review queue."""
        now = datetime.now()
        pending_items = [
            item for item in self._review_queue.values()
            if item.review_status == ReviewStatus.PENDING
        ]
        
        if not pending_items:
            return
        
        logger.debug("🚧 Processing %d pending review items", len(pending_items))
        
        for item in pending_items:
            # Auto-approve items that have been waiting too long
            age_hours = (now - item.submitted_at).total_seconds() / 3600
            if age_hours >= self.REVIEW_TIMEOUT_HOURS:
                await self._auto_approve(item, f"Timeout after {age_hours:.1f}h")
                continue
            
            # Try automated review first
            automated_decision = await self._automated_review(item)
            if automated_decision:
                continue
            
            # If no automated decision and it's been a while, flag for pack patrol
            if age_hours >= 1.0:  # After 1 hour, bring to pack attention
                await self._flag_for_pack_review(item)
    
    async def _automated_review(self, item: ReviewItem) -> bool:
        """
        Attempt automated review of the item.
        Returns True if a decision was made, False if human/pack review needed.
        """
        # Simple heuristics for now - could be enhanced with ML
        
        # Check if agent has clean history
        agent_trust = await self._get_agent_trust(item.agent_id)
        if agent_trust is None:
            return False  # Can't decide without agent info
        
        # High-trust agents with low-risk content get approved
        if agent_trust >= 0.7 and item.risk_score <= 0.4:
            await self._approve_item(item, "auto_review", 
                                   f"High trust agent ({agent_trust:.2f}) with low risk")
            return True
        
        # Low-trust agents with medium-risk content get rejected
        if agent_trust <= 0.3 and item.risk_score >= 0.5:
            await self._reject_item(item, "auto_review",
                                   f"Low trust agent ({agent_trust:.2f}) with medium risk")
            return True
        
        # Check for escalating patterns
        recent_reviews = [
            r for r in self._review_queue.values()
            if r.agent_id == item.agent_id and 
               r.submitted_at > datetime.now() - timedelta(hours=1)
        ]
        
        if len(recent_reviews) >= 3:
            # Multiple suspicious items from same agent in 1 hour
            await self._escalate_item(item, "pattern_detection",
                                     f"Multiple suspicious items ({len(recent_reviews)}) from agent in 1h")
            return True
        
        return False  # No automated decision
    
    async def _flag_for_pack_review(self, item: ReviewItem):
        """Flag item for pack agent review."""
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.BOUNCER_REVIEW_REQUEST,
                agent_id=item.agent_id,
                data={
                    "review_item_id": item.item_id,
                    "item_type": item.item_type,
                    "risk_score": item.risk_score,
                    "threats": item.threats_detected,
                    "age_hours": (datetime.now() - item.submitted_at).total_seconds() / 3600
                },
                source="bouncer",
                severity="medium"
            )
            logger.info("🚧 Flagged item %s for pack review", item.item_id)
        except Exception as e:
            logger.error("Failed to flag item for pack review: %s", e)
    
    async def _get_agent_trust(self, agent_id: str) -> Optional[float]:
        """Get agent trust score."""
        try:
            from db import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT trust_score FROM agents WHERE agent_id = ?",
                    (agent_id,)
                ).fetchone()
                return row['trust_score'] if row else None
        except Exception as e:
            logger.debug("Failed to get agent trust: %s", e)
            return None
    
    async def _approve_item(self, item: ReviewItem, reviewer: str, reason: str):
        """Approve an item."""
        item.review_status = ReviewStatus.APPROVED
        item.reviewed_at = datetime.now()
        item.reviewer = reviewer
        item.review_reason = reason
        
        logger.info("🚧 ✅ Approved: %s (%s)", item.item_id, reason)
        
        # Emit approval event
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.BOUNCER_APPROVED,
                agent_id=item.agent_id,
                data={
                    "review_item_id": item.item_id,
                    "reviewer": reviewer,
                    "reason": reason
                },
                source="bouncer"
            )
        except Exception:
            pass
    
    async def _reject_item(self, item: ReviewItem, reviewer: str, reason: str):
        """Reject an item."""
        item.review_status = ReviewStatus.REJECTED
        item.reviewed_at = datetime.now()
        item.reviewer = reviewer
        item.review_reason = reason
        
        logger.warning("🚧 ❌ Rejected: %s (%s)", item.item_id, reason)
        
        # Emit rejection event
        try:
            from agents.event_bus import event_bus, EventType
            event_bus.emit_simple(
                EventType.BOUNCER_REJECTED,
                agent_id=item.agent_id,
                data={
                    "review_item_id": item.item_id,
                    "reviewer": reviewer,
                    "reason": reason,
                    "threats": item.threats_detected
                },
                source="bouncer",
                severity="warning"
            )
        except Exception:
            pass
    
    async def _escalate_item(self, item: ReviewItem, reviewer: str, reason: str):
        """Escalate item to immune system."""
        item.review_status = ReviewStatus.ESCALATED
        item.reviewed_at = datetime.now()
        item.reviewer = reviewer
        item.review_reason = reason
        
        logger.warning("🚧 ⬆️ Escalated: %s (%s)", item.item_id, reason)
        
        # Escalate to immune system
        try:
            from layers.immune import immune_engine, ViolationType
            evidence = [
                f"Bouncer escalation: {reason}",
                f"Risk score: {item.risk_score}",
                f"Threats: {json.dumps(item.threats_detected)}"
            ]
            
            immune_engine.process_violation(
                agent_id=item.agent_id,
                violation_type=ViolationType.REPUTATION_MANIPULATION,  # Generic escalation type
                evidence=evidence,
                trigger_context={
                    "bouncer_item_id": item.item_id,
                    "escalation_reason": reason
                }
            )
        except Exception as e:
            logger.error("Failed to escalate to immune system: %s", e)
    
    async def _auto_approve(self, item: ReviewItem, reason: str):
        """Auto-approve an item (timeout or overflow)."""
        await self._approve_item(item, "auto_approve", reason)
    
    def get_status(self) -> Dict[str, Any]:
        """Get bouncer status."""
        now = datetime.now()
        
        status_counts = {}
        age_buckets = {"<1h": 0, "1-6h": 0, "6-24h": 0, ">24h": 0}
        
        for item in self._review_queue.values():
            # Count by status
            status = item.review_status.value
            status_counts[status] = status_counts.get(status, 0) + 1
            
            # Count by age (for pending items only)
            if item.review_status == ReviewStatus.PENDING:
                age_hours = (now - item.submitted_at).total_seconds() / 3600
                if age_hours < 1:
                    age_buckets["<1h"] += 1
                elif age_hours < 6:
                    age_buckets["1-6h"] += 1
                elif age_hours < 24:
                    age_buckets["6-24h"] += 1
                else:
                    age_buckets[">24h"] += 1
        
        return {
            "running": self._running,
            "queue_size": len(self._review_queue),
            "max_queue_size": self.MAX_QUEUE_SIZE,
            "thresholds": {
                "clean_threshold": self.CLEAN_THRESHOLD,
                "threat_threshold": self.THREAT_THRESHOLD,
            },
            "status_counts": status_counts,
            "pending_age_buckets": age_buckets,
            "recent_items": [
                {
                    "item_id": item.item_id,
                    "item_type": item.item_type,
                    "agent_id": item.agent_id,
                    "risk_score": item.risk_score,
                    "status": item.review_status.value,
                    "submitted_ago_minutes": (now - item.submitted_at).total_seconds() / 60,
                    "reviewer": item.reviewer,
                    "review_reason": item.review_reason
                }
                for item in sorted(self._review_queue.values(), 
                                 key=lambda x: x.submitted_at, reverse=True)[:10]
            ]
        }
    
    def cleanup_old_items(self, days: int = 7):
        """Clean up old reviewed items."""
        cutoff = datetime.now() - timedelta(days=days)
        old_items = [
            item_id for item_id, item in self._review_queue.items()
            if item.review_status != ReviewStatus.PENDING and 
               (item.reviewed_at or item.submitted_at) < cutoff
        ]
        
        for item_id in old_items:
            del self._review_queue[item_id]
        
        if old_items:
            logger.info("🚧 Cleaned up %d old review items", len(old_items))


# Global bouncer instance
bouncer = BouncerEngine()
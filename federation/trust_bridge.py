"""
Agent Café — Trust Bridge
Trust score translation between federated nodes.

Trust doesn't transfer 1:1 across nodes. A 0.9 on your home node
might be 0.63 here. You haven't proven yourself HERE yet.

This prevents:
- Malicious nodes inflating their agents' scores
- Sybil attacks through fake federation nodes
- Trust farming through collusion between nodes
"""

from typing import Optional, Dict, Any


class TrustBridge:
    """
    Translate trust scores between federated nodes.
    
    The formula accounts for:
    1. Remote discount (you're new here)
    2. Home node reputation (is their node trustworthy?)
    3. Local history (have you worked here before?)
    4. Cross-validation (does the math add up?)
    """
    
    def __init__(
        self,
        default_discount: float = 0.3,
        min_remote_trust: float = 0.4,
        local_jobs_for_full_trust: int = 10,
        max_local_bonus: float = 0.2
    ):
        self.default_discount = default_discount
        self.min_remote_trust = min_remote_trust
        self.local_jobs_for_full_trust = local_jobs_for_full_trust
        self.max_local_bonus = max_local_bonus
    
    def translate_trust(
        self,
        home_trust: float,
        home_jobs: int,
        home_rating: float,
        remote_jobs: int,
        remote_rating: float,
        home_node_reputation: float,
    ) -> float:
        """
        Calculate effective trust for a remote agent on this node.
        
        Args:
            home_trust: Trust score on agent's home node (0.0-1.0)
            home_jobs: Jobs completed on home node
            home_rating: Average rating on home node (1-5)
            remote_jobs: Jobs completed on THIS node (0 for new arrivals)
            remote_rating: Average rating on THIS node (0 if no jobs)
            home_node_reputation: How much we trust the home node (0.0-1.0)
        
        Returns:
            Effective trust score for use on this node (0.0-1.0)
        """
        # === Sanity checks ===
        home_trust = max(0.0, min(1.0, home_trust))
        home_node_reputation = max(0.0, min(1.0, home_node_reputation))
        
        # === Cross-validation ===
        # If home_trust is high but home_jobs is very low, something smells
        # A 0.9 trust with 2 completed jobs? Suspicious.
        trust_job_ratio = self._cross_validate(home_trust, home_jobs, home_rating)
        
        # === Discount calculation ===
        # Discount shrinks as agent builds local history
        # 0 local jobs = full discount
        # local_jobs_for_full_trust local jobs = no discount
        local_factor = min(remote_jobs / self.local_jobs_for_full_trust, 1.0)
        effective_discount = self.default_discount * (1 - local_factor)
        
        # === Node reputation factor ===
        # New nodes (rep 0.5) → 50% of home trust carries
        # Proven nodes (rep 0.9) → 90% carries
        # Sketchy nodes (rep 0.2) → only 20% carries
        node_factor = max(home_node_reputation, 0.1)
        
        # === Base translation ===
        # home_trust × node_reputation × (1 - discount) × cross_validation
        effective = home_trust * node_factor * (1 - effective_discount) * trust_job_ratio
        
        # === Local performance bonus ===
        # If the agent has worked here before and did well, add bonus
        if remote_jobs > 0 and remote_rating > 0:
            # Bonus scales with jobs and rating
            job_factor = min(remote_jobs / self.local_jobs_for_full_trust, 1.0)
            rating_factor = max(0, (remote_rating - 3.0) / 2.0)  # 3.0 = neutral, 5.0 = max
            local_bonus = self.max_local_bonus * job_factor * rating_factor
            effective += local_bonus
        
        # === Clamp ===
        return max(0.0, min(1.0, effective))
    
    def _cross_validate(self, trust: float, jobs: int, rating: float) -> float:
        """
        Cross-validate trust score against job count and rating.
        
        Returns a factor (0.0-1.0) that penalizes implausible combinations.
        
        Implausible:
        - trust 0.9 with 1 job (not enough data for high trust)
        - trust 0.8 with rating 2.0 (high trust but bad reviews?)
        - trust 0.0 with 50 jobs and 4.5 rating (should have built trust)
        """
        factor = 1.0
        
        if trust > 0.7 and jobs < 5:
            # High trust with very few jobs — suspicious
            # Penalty proportional to gap
            factor *= 0.5 + (jobs / 10)  # 0 jobs = 0.5x, 5 jobs = 1.0x
        
        if trust > 0.6 and rating > 0 and rating < 2.5:
            # High trust but bad ratings — doesn't compute
            factor *= 0.3
        
        if trust < 0.3 and jobs > 20 and rating > 4.0:
            # Low trust despite good track record — node might be penalizing unfairly
            # Don't boost much, just note it
            factor *= 1.0  # No penalty, but also no boost
        
        return max(0.1, min(1.0, factor))
    
    def meets_minimum(self, effective_trust: float) -> bool:
        """Check if a remote agent meets minimum trust for this node."""
        return effective_trust >= self.min_remote_trust
    
    def explain(
        self,
        home_trust: float,
        home_jobs: int,
        home_rating: float,
        remote_jobs: int,
        remote_rating: float,
        home_node_reputation: float,
    ) -> Dict[str, Any]:
        """
        Calculate effective trust AND return explanation of each factor.
        Useful for debugging and transparency.
        """
        home_trust = max(0.0, min(1.0, home_trust))
        home_node_reputation = max(0.0, min(1.0, home_node_reputation))
        
        cross_val = self._cross_validate(home_trust, home_jobs, home_rating)
        local_factor = min(remote_jobs / self.local_jobs_for_full_trust, 1.0)
        effective_discount = self.default_discount * (1 - local_factor)
        node_factor = max(home_node_reputation, 0.1)
        
        base = home_trust * node_factor * (1 - effective_discount) * cross_val
        
        local_bonus = 0.0
        if remote_jobs > 0 and remote_rating > 0:
            job_factor = min(remote_jobs / self.local_jobs_for_full_trust, 1.0)
            rating_factor = max(0, (remote_rating - 3.0) / 2.0)
            local_bonus = self.max_local_bonus * job_factor * rating_factor
        
        effective = max(0.0, min(1.0, base + local_bonus))
        
        return {
            "effective_trust": round(effective, 4),
            "meets_minimum": effective >= self.min_remote_trust,
            "breakdown": {
                "home_trust": home_trust,
                "node_reputation_factor": round(node_factor, 4),
                "remote_discount": round(effective_discount, 4),
                "cross_validation_factor": round(cross_val, 4),
                "base_score": round(base, 4),
                "local_bonus": round(local_bonus, 4),
                "local_jobs_on_this_node": remote_jobs,
            },
            "thresholds": {
                "min_remote_trust": self.min_remote_trust,
                "default_discount": self.default_discount,
                "jobs_for_full_trust": self.local_jobs_for_full_trust,
            }
        }


# Global singleton — config comes from node identity
trust_bridge = TrustBridge()

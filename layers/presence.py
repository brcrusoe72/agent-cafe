"""
Agent Café - Presence Layer ♟️ (The Grandmaster's Board)
What agents see vs what the system sees. BoardPosition computed from trust ledger + job history.
Not profiles — computed positions.
"""

import json
import math
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict

try:
    from ..models import (
        Agent, AgentStatus, BoardPosition, BoardState, TrustEvent,
        Job, JobStatus
    )
    from ..db import get_db, get_board_positions, get_treasury_stats
except ImportError:
    from models import (
        Agent, AgentStatus, BoardPosition, BoardState, TrustEvent,
        Job, JobStatus
    )
    from db import get_db, get_board_positions, get_treasury_stats


class PresenceEngine:
    """Core presence engine computing board positions and strategic analysis."""
    
    def __init__(self):
        # Trust score weights
        self.COMPLETION_RATE_WEIGHT = 0.30
        self.RATING_WEIGHT = 0.25
        self.RESPONSE_TIME_WEIGHT = 0.15
        self.RECENCY_WEIGHT = 0.30  # Higher than spec - trust decays without activity
        
        # Threat detection thresholds
        self.REPUTATION_VELOCITY_THRESHOLD = 0.15  # Trust score change per week
        self.COLLUSION_RATING_THRESHOLD = 5  # Mutual ratings to flag as suspicious
        self.HIGH_THREAT_SCORE = 0.7
        
        # Position strength factors
        self.EXPERIENCE_FACTOR = 0.45
        self.RELIABILITY_FACTOR = 0.35
        self.CAPABILITY_FACTOR = 0.20
    
    def compute_board_position(self, agent_id: str) -> Optional[BoardPosition]:
        """Compute current board position for an agent."""
        with get_db() as conn:
            # Get agent basic data
            agent_row = conn.execute("""
                SELECT * FROM agents WHERE agent_id = ?
            """, (agent_id,)).fetchone()
            
            if not agent_row:
                return None
            
            # Calculate trust score components
            trust_score = self._calculate_trust_score(agent_id, conn)
            
            # Calculate position strength
            position_strength = self._calculate_position_strength(agent_id, conn)
            
            # Calculate threat level
            threat_level = self._calculate_threat_level(agent_id, conn)
            
            # Get completion stats
            completion_stats = self._get_completion_stats(agent_id, conn)
            
            # Get cluster assignment
            cluster_id = self._get_cluster_id(agent_id, conn)
            
            # Get internal analysis
            internal_notes, suspicious_patterns = self._get_internal_analysis(agent_id, conn)
            
            # Update agent record with computed values
            conn.execute("""
                UPDATE agents SET 
                    trust_score = ?, position_strength = ?, threat_level = ?, 
                    cluster_id = ?, internal_notes = ?, suspicious_patterns = ?
                WHERE agent_id = ?
            """, (
                trust_score, position_strength, threat_level, cluster_id,
                json.dumps(internal_notes), json.dumps(suspicious_patterns),
                agent_id
            ))
            conn.commit()
            
            return BoardPosition(
                agent_id=agent_id,
                name=agent_row['name'],
                description=agent_row['description'],
                capabilities_verified=json.loads(agent_row['capabilities_verified']),
                capabilities_claimed=json.loads(agent_row['capabilities_claimed']),
                trust_score=trust_score,
                jobs_completed=agent_row['jobs_completed'],
                jobs_failed=agent_row['jobs_failed'],
                avg_rating=agent_row['avg_rating'],
                avg_completion_sec=completion_stats['avg_completion_sec'],
                total_earned_cents=agent_row['total_earned_cents'],
                position_strength=position_strength,
                threat_level=threat_level,
                cluster_id=cluster_id,
                last_active=datetime.fromisoformat(agent_row['last_active']),
                registration_date=datetime.fromisoformat(agent_row['registration_date']),
                status=AgentStatus(agent_row['status']),
                internal_notes=internal_notes,
                suspicious_patterns=suspicious_patterns
            )
    
    def compute_board_state(self) -> BoardState:
        """Compute full board state with strategic analysis."""
        with get_db() as conn:
            # Basic counts
            active_agents = conn.execute("""
                SELECT COUNT(*) as count FROM agents WHERE status = 'active'
            """).fetchone()['count']
            
            quarantined_agents = conn.execute("""
                SELECT COUNT(*) as count FROM agents WHERE status = 'quarantined'
            """).fetchone()['count']
            
            dead_agents = conn.execute("""
                SELECT COUNT(*) as count FROM agent_corpses
            """).fetchone()['count']
            
            total_jobs = conn.execute("""
                SELECT COUNT(*) as count FROM jobs WHERE status = 'completed'
            """).fetchone()['count']
            
            total_volume = conn.execute("""
                SELECT COALESCE(SUM(budget_cents), 0) as volume 
                FROM jobs WHERE status = 'completed'
            """).fetchone()['volume']
            
            # Get treasury
            treasury = get_treasury_stats()
            
            # Strategic analysis
            collusion_clusters = self._detect_collusion_clusters(conn)
            reputation_velocity = self._calculate_reputation_velocity(conn)
            attack_patterns = self._get_attack_patterns_seen(conn)
            system_health = self._calculate_system_health(conn)
            
            return BoardState(
                active_agents=active_agents,
                quarantined_agents=quarantined_agents,
                dead_agents=dead_agents,
                total_jobs_completed=total_jobs,
                total_volume_cents=total_volume,
                collusion_clusters=collusion_clusters,
                reputation_velocity=reputation_velocity,
                attack_patterns_seen=attack_patterns,
                system_health=system_health
            )
    
    def refresh_all_positions(self) -> int:
        """Refresh board positions for all agents."""
        with get_db() as conn:
            agent_ids = [row['agent_id'] for row in conn.execute("""
                SELECT agent_id FROM agents WHERE status != 'dead'
            """).fetchall()]
        
        refreshed_count = 0
        for agent_id in agent_ids:
            position = self.compute_board_position(agent_id)
            if position:
                refreshed_count += 1
        
        return refreshed_count
    
    def get_leaderboard(self, limit: int = 20) -> List[BoardPosition]:
        """Get top agents by trust score."""
        positions = []
        
        with get_db() as conn:
            rows = conn.execute("""
                SELECT agent_id FROM agents 
                WHERE status = 'active' 
                ORDER BY trust_score DESC, position_strength DESC 
                LIMIT ?
            """, (limit,)).fetchall()
        
        for row in rows:
            position = self.compute_board_position(row['agent_id'])
            if position:
                positions.append(position)
        
        return positions
    
    def get_agents_by_capability(self, capability: str, verified_only: bool = True) -> List[BoardPosition]:
        """Get agents with specific capability."""
        positions = []
        
        with get_db() as conn:
            if verified_only:
                column = "capabilities_verified"
            else:
                column = "capabilities_claimed"
            
            rows = conn.execute(f"""
                SELECT agent_id FROM agents 
                WHERE status = 'active' AND {column} LIKE ?
                ORDER BY trust_score DESC
            """, (f'%"{capability}"%',)).fetchall()
        
        for row in rows:
            position = self.compute_board_position(row['agent_id'])
            if position:
                positions.append(position)
        
        return positions
    
    def _calculate_trust_score(self, agent_id: str, conn) -> float:
        """Calculate weighted trust score from multiple factors."""
        # Get agent stats
        agent_row = conn.execute("""
            SELECT jobs_completed, jobs_failed, avg_rating, last_active, registration_date
            FROM agents WHERE agent_id = ?
        """, (agent_id,)).fetchone()
        
        if not agent_row:
            return 0.0
        
        # 1. Completion Rate (0.25 weight)
        total_jobs = agent_row['jobs_completed'] + agent_row['jobs_failed']
        if total_jobs > 0:
            completion_rate = agent_row['jobs_completed'] / total_jobs
        else:
            completion_rate = 0.0
        
        # 2. Average Rating (0.20 weight) - normalize 1-5 to 0-1
        if agent_row['avg_rating'] > 0:
            rating_score = (agent_row['avg_rating'] - 1.0) / 4.0
        else:
            rating_score = 0.0
        
        # 3. Response Time (0.15 weight) - calculated from job history
        response_time_score = self._get_response_time_score(agent_id, conn)
        
        # 4. Recency (0.30 weight) - trust decays without activity
        last_active = datetime.fromisoformat(agent_row['last_active'])
        days_inactive = (datetime.now() - last_active).days
        # Decay: 100% at 0 days, 50% at 30 days, 0% at 90 days
        recency_score = max(0.0, 1.0 - (days_inactive / 90.0))
        
        # Weighted composite
        trust_score = (
            completion_rate * self.COMPLETION_RATE_WEIGHT +
            rating_score * self.RATING_WEIGHT +
            response_time_score * self.RESPONSE_TIME_WEIGHT +
            recency_score * self.RECENCY_WEIGHT
        )
        
        return max(0.0, min(1.0, trust_score))
    
    def _calculate_position_strength(self, agent_id: str, conn) -> float:
        """Calculate how strong this piece is on the board."""
        # Experience (jobs completed with weight for complexity)
        exp_score = self._get_experience_score(agent_id, conn)
        
        # Reliability (consistency of delivery)
        rel_score = self._get_reliability_score(agent_id, conn)
        
        # Verified capabilities
        agent_row = conn.execute("""
            SELECT capabilities_verified FROM agents WHERE agent_id = ?
        """, (agent_id,)).fetchone()
        
        verified_caps = json.loads(agent_row['capabilities_verified']) if agent_row else []
        capability_score = min(1.0, len(verified_caps) / 5)  # 5 caps = max
        
        # Weighted composite
        strength = (
            exp_score * self.EXPERIENCE_FACTOR +
            rel_score * self.RELIABILITY_FACTOR +
            capability_score * self.CAPABILITY_FACTOR
        )
        
        return max(0.0, min(1.0, strength))
    
    def _calculate_threat_level(self, agent_id: str, conn) -> float:
        """Calculate threat level based on behavior patterns."""
        threat_score = 0.0
        
        # 1. Reputation velocity (fast trust gains are suspicious)
        velocity = self._get_reputation_velocity(agent_id, conn)
        if velocity > self.REPUTATION_VELOCITY_THRESHOLD:
            threat_score += 0.3
        
        # 2. Collusion indicators
        collusion_score = self._get_collusion_indicators(agent_id, conn)
        threat_score += collusion_score * 0.4
        
        # 3. Failed scrub attempts
        scrub_failures = conn.execute("""
            SELECT COUNT(*) as count FROM scrub_results sr
            JOIN interaction_traces it ON sr.trace_id = it.trace_id
            JOIN jobs j ON it.job_id = j.job_id
            WHERE (j.posted_by = ? OR j.assigned_to = ?) 
            AND sr.action IN ('block', 'quarantine')
            AND sr.timestamp > datetime('now', '-30 days')
        """, (agent_id, agent_id)).fetchone()['count']
        
        if scrub_failures > 0:
            threat_score += min(0.3, scrub_failures * 0.1)
        
        # 4. Immune system events
        immune_events = conn.execute("""
            SELECT COUNT(*) as count FROM immune_events 
            WHERE agent_id = ? AND timestamp > datetime('now', '-90 days')
        """, (agent_id,)).fetchone()['count']
        
        if immune_events > 0:
            threat_score += min(0.5, immune_events * 0.2)
        
        return max(0.0, min(1.0, threat_score))
    
    def _get_completion_stats(self, agent_id: str, conn) -> Dict[str, Any]:
        """Get job completion statistics."""
        # Average completion time
        completion_times = conn.execute("""
            SELECT AVG(
                (julianday(completed_at) - julianday(posted_at)) * 24 * 3600
            ) as avg_completion_sec
            FROM jobs 
            WHERE assigned_to = ? AND status = 'completed' 
            AND completed_at IS NOT NULL
        """, (agent_id,)).fetchone()
        
        avg_completion_sec = int(completion_times['avg_completion_sec']) if completion_times['avg_completion_sec'] else 0
        
        return {
            "avg_completion_sec": avg_completion_sec
        }
    
    def _get_cluster_id(self, agent_id: str, conn) -> Optional[str]:
        """Get cluster assignment for collusion detection."""
        # Look for agents this one frequently interacts with
        frequent_partners = conn.execute("""
            SELECT j.posted_by, j.assigned_to, COUNT(*) as interactions
            FROM jobs j
            WHERE (j.posted_by = ? OR j.assigned_to = ?) AND j.status = 'completed'
            GROUP BY j.posted_by, j.assigned_to
            HAVING interactions >= 3
        """, (agent_id, agent_id)).fetchall()
        
        if frequent_partners:
            # Simple clustering: group with first frequent partner
            partner = frequent_partners[0]
            if partner['posted_by'] == agent_id:
                cluster_partner = partner['assigned_to']
            else:
                cluster_partner = partner['posted_by']
            
            # Generate cluster ID from sorted agent IDs
            cluster_agents = sorted([agent_id, cluster_partner])
            return f"cluster_{hash('_'.join(cluster_agents)) % 10000:04d}"
        
        return None
    
    def _get_internal_analysis(self, agent_id: str, conn) -> Tuple[List[str], List[str]]:
        """Get Grandmaster's internal notes and suspicious patterns."""
        notes = []
        patterns = []
        
        # Check for suspicious patterns
        agent_row = conn.execute("""
            SELECT registration_date, jobs_completed, trust_score FROM agents WHERE agent_id = ?
        """, (agent_id,)).fetchone()
        
        if agent_row:
            reg_date = datetime.fromisoformat(agent_row['registration_date'])
            days_active = (datetime.now() - reg_date).days
            
            # New agent with high trust
            if days_active < 30 and agent_row['trust_score'] > 0.7:
                patterns.append("rapid_trust_gain")
                notes.append(f"High trust score ({agent_row['trust_score']:.2f}) after only {days_active} days")
            
            # High job count for time active
            if days_active > 0 and (agent_row['jobs_completed'] / max(days_active, 1)) > 2:
                patterns.append("high_activity")
                notes.append(f"Unusually high job completion rate: {agent_row['jobs_completed']} jobs in {days_active} days")
            
            # Check for rating patterns
            rating_data = conn.execute("""
                SELECT rating, COUNT(*) as count FROM trust_events 
                WHERE agent_id = ? AND event_type = 'job_completion' AND rating IS NOT NULL
                GROUP BY rating
                ORDER BY rating
            """, (agent_id,)).fetchall()
            
            if rating_data:
                # Check for rating clustering (all 5s or all 1s is suspicious)
                total_ratings = sum(r['count'] for r in rating_data)
                if total_ratings >= 5:
                    for rating_row in rating_data:
                        if rating_row['count'] / total_ratings > 0.8:  # >80% same rating
                            patterns.append("rating_clustering")
                            notes.append(f"Suspicious rating pattern: {rating_row['count']}/{total_ratings} ratings are {rating_row['rating']}")
        
        return notes, patterns
    
    def _get_response_time_score(self, agent_id: str, conn) -> float:
        """Calculate response time score from message patterns."""
        # Look at time between job assignment and first message
        response_times = conn.execute("""
            SELECT AVG(
                (julianday(wm.timestamp) - julianday(j.posted_at)) * 24 * 3600
            ) as avg_response_sec
            FROM wire_messages wm
            JOIN jobs j ON wm.job_id = j.job_id
            WHERE wm.from_agent = ? AND wm.message_type IN ('question', 'status')
            AND wm.timestamp = (
                SELECT MIN(timestamp) FROM wire_messages wm2 
                WHERE wm2.job_id = wm.job_id AND wm2.from_agent = wm.from_agent
            )
        """, (agent_id,)).fetchone()
        
        if not response_times or not response_times['avg_response_sec']:
            return 0.5  # Neutral score if no data
        
        avg_response_hours = response_times['avg_response_sec'] / 3600
        
        # Score: <1 hour = 1.0, <6 hours = 0.8, <24 hours = 0.5, >24 hours = 0.1
        if avg_response_hours < 1:
            return 1.0
        elif avg_response_hours < 6:
            return 0.8
        elif avg_response_hours < 24:
            return 0.5
        else:
            return 0.1
    
    def _get_experience_score(self, agent_id: str, conn) -> float:
        """Calculate experience score from job history."""
        stats = conn.execute("""
            SELECT 
                COUNT(*) as total_jobs,
                AVG(budget_cents) as avg_job_value,
                COUNT(DISTINCT j.required_capabilities) as unique_capabilities
            FROM jobs j
            WHERE j.assigned_to = ? AND j.status = 'completed'
        """, (agent_id,)).fetchone()
        
        if not stats or stats['total_jobs'] == 0:
            return 0.0
        
        # Normalize job count (20 jobs = max)
        job_score = min(1.0, stats['total_jobs'] / 20)
        
        # Normalize average value ($100 = max)
        value_score = min(1.0, (stats['avg_job_value'] or 0) / 10000)
        
        # Diversity score (5 different capabilities = max)
        diversity_score = min(1.0, (stats['unique_capabilities'] or 0) / 5)
        
        return (job_score * 0.5 + value_score * 0.3 + diversity_score * 0.2)
    
    def _get_reliability_score(self, agent_id: str, conn) -> float:
        """Calculate reliability from delivery consistency."""
        # Standard deviation of delivery times
        delivery_data = conn.execute("""
            SELECT 
                (julianday(completed_at) - julianday(posted_at)) * 24 as completion_hours
            FROM jobs 
            WHERE assigned_to = ? AND status = 'completed' AND completed_at IS NOT NULL
        """, (agent_id,)).fetchall()
        
        if len(delivery_data) < 3:
            return 0.5  # Neutral if insufficient data
        
        hours = [row['completion_hours'] for row in delivery_data]
        mean_hours = sum(hours) / len(hours)
        variance = sum((h - mean_hours) ** 2 for h in hours) / len(hours)
        std_dev = math.sqrt(variance)
        
        # Lower standard deviation = higher reliability
        # Normalize: 0 std dev = 1.0, 48 hour std dev = 0.0
        reliability = max(0.0, 1.0 - (std_dev / 48))
        
        return reliability
    
    def _get_reputation_velocity(self, agent_id: str, conn) -> float:
        """Calculate trust score velocity (change per week)."""
        trust_events = conn.execute("""
            SELECT impact, timestamp FROM trust_events 
            WHERE agent_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 10
        """, (agent_id,)).fetchall()
        
        if len(trust_events) < 2:
            return 0.0
        
        total_impact = sum(event['impact'] for event in trust_events)
        first_event = datetime.fromisoformat(trust_events[-1]['timestamp'])
        last_event = datetime.fromisoformat(trust_events[0]['timestamp'])
        
        time_span_weeks = max(1, (last_event - first_event).days / 7)
        velocity = total_impact / time_span_weeks
        
        return velocity
    
    def _get_collusion_indicators(self, agent_id: str, conn) -> float:
        """Detect collusion patterns."""
        # Look for mutual rating patterns
        mutual_ratings = conn.execute("""
            WITH agent_ratings AS (
                SELECT t1.agent_id as rater, j1.posted_by as ratee, COUNT(*) as ratings
                FROM trust_events t1
                JOIN jobs j1 ON t1.job_id = j1.job_id
                WHERE t1.event_type = 'job_completion' AND t1.agent_id = ?
                GROUP BY j1.posted_by
            ),
            reverse_ratings AS (
                SELECT t2.agent_id as rater, j2.posted_by as ratee, COUNT(*) as ratings
                FROM trust_events t2
                JOIN jobs j2 ON t2.job_id = j2.job_id
                WHERE j2.posted_by = ? AND t2.event_type = 'job_completion'
                GROUP BY t2.agent_id
            )
            SELECT ar.ratee, ar.ratings + COALESCE(rr.ratings, 0) as total_mutual
            FROM agent_ratings ar
            LEFT JOIN reverse_ratings rr ON ar.ratee = rr.rater
            WHERE total_mutual >= ?
        """, (agent_id, agent_id, self.COLLUSION_RATING_THRESHOLD)).fetchall()
        
        if mutual_ratings:
            # Scale collusion score by number of mutual interactions
            max_mutual = max(row['total_mutual'] for row in mutual_ratings)
            return min(1.0, max_mutual / 20)  # 20 mutual ratings = max suspicion
        
        return 0.0
    
    def _detect_collusion_clusters(self, conn) -> List[List[str]]:
        """Detect groups of agents that frequently rate each other."""
        # TODO: Implement graph-based clustering algorithm
        # For now, return simple pairs
        clusters = []
        
        mutual_pairs = conn.execute("""
            WITH mutual_ratings AS (
                SELECT 
                    j1.posted_by as agent_a,
                    t1.agent_id as agent_b,
                    COUNT(*) as a_to_b
                FROM trust_events t1
                JOIN jobs j1 ON t1.job_id = j1.job_id
                WHERE t1.event_type = 'job_completion'
                GROUP BY j1.posted_by, t1.agent_id
                HAVING a_to_b >= 3
            ),
            bidirectional AS (
                SELECT 
                    mr1.agent_a, mr1.agent_b, mr1.a_to_b,
                    COALESCE(mr2.a_to_b, 0) as b_to_a
                FROM mutual_ratings mr1
                LEFT JOIN mutual_ratings mr2 ON mr1.agent_a = mr2.agent_b AND mr1.agent_b = mr2.agent_a
            )
            SELECT agent_a, agent_b 
            FROM bidirectional 
            WHERE a_to_b >= 3 AND b_to_a >= 3
        """).fetchall()
        
        for pair in mutual_pairs:
            clusters.append([pair['agent_a'], pair['agent_b']])
        
        return clusters
    
    def _calculate_reputation_velocity(self, conn) -> Dict[str, float]:
        """Calculate reputation velocity for all agents."""
        velocity_data = {}
        
        agent_ids = [row['agent_id'] for row in conn.execute("""
            SELECT agent_id FROM agents WHERE status = 'active'
        """).fetchall()]
        
        for agent_id in agent_ids:
            velocity = self._get_reputation_velocity(agent_id, conn)
            if abs(velocity) > 0.01:  # Only include significant velocities
                velocity_data[agent_id] = velocity
        
        return velocity_data
    
    def _get_attack_patterns_seen(self, conn) -> List[str]:
        """Get list of attack patterns seen in scrub results."""
        patterns = conn.execute("""
            SELECT DISTINCT threat_type FROM known_patterns
            ORDER BY created_at DESC
            LIMIT 20
        """).fetchall()
        
        return [row['threat_type'] for row in patterns]
    
    def _calculate_system_health(self, conn) -> float:
        """Calculate overall system health score."""
        # Factors:
        # 1. Active agent ratio (active / (active + dead))
        # 2. Job completion rate
        # 3. Attack detection effectiveness
        # 4. Trust score distribution
        
        agent_counts = conn.execute("""
            SELECT status, COUNT(*) as count 
            FROM agents 
            GROUP BY status
        """).fetchall()
        
        status_counts = {row['status']: row['count'] for row in agent_counts}
        total_agents = sum(status_counts.values())
        
        if total_agents == 0:
            return 1.0  # Perfect health if no agents (bootstrap state)
        
        # 1. Active ratio (0.3 weight)
        active_ratio = status_counts.get('active', 0) / total_agents
        
        # 2. Job success rate (0.3 weight)
        job_success = conn.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'completed' THEN 1 END) * 1.0 / COUNT(*) as success_rate
            FROM jobs
            WHERE status IN ('completed', 'disputed', 'cancelled')
        """).fetchone()
        
        job_success_rate = job_success['success_rate'] if job_success['success_rate'] else 1.0
        
        # 3. Scrub effectiveness (0.2 weight)
        scrub_stats = conn.execute("""
            SELECT 
                COUNT(CASE WHEN action = 'block' THEN 1 END) * 1.0 / COUNT(*) as block_rate
            FROM scrub_results
            WHERE timestamp > datetime('now', '-7 days')
        """).fetchone()
        
        block_rate = scrub_stats['block_rate'] if scrub_stats['block_rate'] else 0.0
        scrub_effectiveness = min(1.0, block_rate * 10)  # 10% block rate = perfect
        
        # 4. Trust distribution (0.2 weight)
        trust_stats = conn.execute("""
            SELECT AVG(trust_score) as avg_trust, COUNT(*) as active_count
            FROM agents 
            WHERE status = 'active'
        """).fetchone()
        
        avg_trust = trust_stats['avg_trust'] if trust_stats['avg_trust'] else 0.5
        
        # Composite health score
        health = (
            active_ratio * 0.3 +
            job_success_rate * 0.3 +
            scrub_effectiveness * 0.2 +
            avg_trust * 0.2
        )
        
        return max(0.0, min(1.0, health))


# Global presence engine instance
presence_engine = PresenceEngine()
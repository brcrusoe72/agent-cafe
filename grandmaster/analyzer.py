"""
Agent Café - Grandmaster Analyzer
Strategic analysis: collusion detection, fork detection, velocity tracking.
The Grandmaster's eyes and memory.
"""

import json
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict, Counter

try:
    from ..models import Agent, AgentStatus, ThreatType
    from ..db import get_db
    from ..layers.presence import presence_engine
except ImportError:
    from models import Agent, AgentStatus, ThreatType
    from db import get_db
    from layers.presence import presence_engine


@dataclass
class CollusionCluster:
    """A group of agents that appear to be coordinating."""
    cluster_id: str
    agent_ids: List[str]
    mutual_interactions: int
    rating_pattern_score: float  # 0-1, higher = more suspicious
    formation_date: datetime
    evidence: List[str]
    threat_level: float  # 0-1


@dataclass
class ForkDetection:
    """Evidence of a single entity controlling multiple agent identities."""
    primary_agent_id: str
    suspected_forks: List[str]
    similarity_score: float  # 0-1
    behavioral_evidence: List[str]
    technical_evidence: List[str]
    confidence: float  # 0-1


@dataclass
class ReputationAnomaly:
    """Unusual reputation velocity that warrants investigation."""
    agent_id: str
    velocity: float  # Trust score change per week
    time_window_days: int
    baseline_velocity: float
    anomaly_score: float  # Standard deviations from baseline
    suspected_cause: str
    evidence: List[str]


class GrandmasterAnalyzer:
    """Strategic analysis engine for detecting sophisticated attacks."""
    
    def __init__(self):
        # Detection thresholds
        self.COLLUSION_MIN_INTERACTIONS = 5
        self.COLLUSION_RATING_UNIFORMITY = 0.8  # Suspicious if >80% ratings are same value
        self.FORK_SIMILARITY_THRESHOLD = 0.7
        self.REPUTATION_ANOMALY_STDEV = 2.0  # Flag if >2 std deviations from normal
        
        # Behavioral fingerprinting
        self.FINGERPRINT_FEATURES = [
            'avg_message_length', 'response_time_pattern', 'bidding_pattern',
            'work_schedule', 'linguistic_style', 'error_patterns'
        ]
    
    def analyze_collusion_networks(self) -> List[CollusionCluster]:
        """Detect groups of agents that appear to be coordinating to manipulate trust."""
        clusters = []
        
        with get_db() as conn:
            # Find agents with high mutual interaction rates
            mutual_interactions = conn.execute("""
                WITH agent_pairs AS (
                    SELECT 
                        CASE WHEN j.posted_by < t.agent_id THEN j.posted_by ELSE t.agent_id END as agent_a,
                        CASE WHEN j.posted_by < t.agent_id THEN t.agent_id ELSE j.posted_by END as agent_b,
                        COUNT(*) as interactions
                    FROM trust_events t
                    JOIN jobs j ON t.job_id = j.job_id
                    WHERE t.event_type = 'job_completion' 
                    AND j.posted_by != t.agent_id  -- Different agents
                    GROUP BY agent_a, agent_b
                    HAVING interactions >= ?
                )
                SELECT * FROM agent_pairs ORDER BY interactions DESC
            """, (self.COLLUSION_MIN_INTERACTIONS,)).fetchall()
            
            # Analyze each pair for suspicious patterns
            for interaction in mutual_interactions:
                cluster = self._analyze_agent_pair(
                    interaction['agent_a'], 
                    interaction['agent_b'],
                    interaction['interactions'],
                    conn
                )
                if cluster:
                    clusters.append(cluster)
        
        # Merge overlapping clusters
        merged_clusters = self._merge_overlapping_clusters(clusters)
        
        return merged_clusters
    
    def detect_fork_attempts(self) -> List[ForkDetection]:
        """Detect single entities operating multiple agent identities."""
        forks = []
        
        with get_db() as conn:
            # Get all active agents
            agents = conn.execute("""
                SELECT agent_id, name, description, registration_date, contact_email
                FROM agents WHERE status IN ('active', 'probation')
            """).fetchall()
            
            # Compare each pair for similarity
            for i, agent_a in enumerate(agents):
                for agent_b in agents[i+1:]:
                    fork = self._compare_agents_for_fork(agent_a, agent_b, conn)
                    if fork and fork.confidence >= self.FORK_SIMILARITY_THRESHOLD:
                        forks.append(fork)
        
        # Group forks by primary agent
        grouped_forks = self._group_forks_by_primary(forks)
        
        return grouped_forks
    
    def track_reputation_velocity(self) -> List[ReputationAnomaly]:
        """Track unusual reputation changes that could indicate manipulation."""
        anomalies = []
        
        with get_db() as conn:
            # Get trust velocity for all active agents
            active_agents = conn.execute("""
                SELECT agent_id FROM agents WHERE status = 'active'
            """).fetchall()
            
            # Calculate baseline velocity from all agents
            all_velocities = []
            for agent_row in active_agents:
                velocity = self._calculate_trust_velocity(agent_row['agent_id'], conn)
                if velocity is not None:
                    all_velocities.append(velocity)
            
            if len(all_velocities) < 3:
                return anomalies  # Need baseline data
            
            # Statistical baseline
            mean_velocity = sum(all_velocities) / len(all_velocities)
            variance = sum((v - mean_velocity) ** 2 for v in all_velocities) / len(all_velocities)
            std_dev = variance ** 0.5
            
            # Flag outliers
            for agent_row in active_agents:
                agent_id = agent_row['agent_id']
                velocity = self._calculate_trust_velocity(agent_id, conn)
                
                if velocity is not None and std_dev > 0:
                    z_score = abs(velocity - mean_velocity) / std_dev
                    
                    if z_score >= self.REPUTATION_ANOMALY_STDEV:
                        anomaly = self._investigate_reputation_anomaly(
                            agent_id, velocity, mean_velocity, z_score, conn
                        )
                        if anomaly:
                            anomalies.append(anomaly)
        
        return anomalies
    
    def analyze_behavioral_patterns(self, agent_id: str) -> Dict[str, Any]:
        """Deep behavioral analysis for a single agent."""
        with get_db() as conn:
            patterns = {}
            
            # Message timing patterns
            patterns['messaging'] = self._analyze_messaging_patterns(agent_id, conn)
            
            # Bidding behavior
            patterns['bidding'] = self._analyze_bidding_patterns(agent_id, conn)
            
            # Work schedule patterns
            patterns['schedule'] = self._analyze_work_schedule(agent_id, conn)
            
            # Linguistic analysis
            patterns['linguistic'] = self._analyze_linguistic_patterns(agent_id, conn)
            
            # Error patterns
            patterns['errors'] = self._analyze_error_patterns(agent_id, conn)
            
            # Generate behavioral fingerprint
            patterns['fingerprint'] = self._generate_behavioral_fingerprint(patterns)
            
            return patterns
    
    def generate_threat_assessment(self, agent_id: str) -> Dict[str, Any]:
        """Comprehensive threat assessment for an agent."""
        with get_db() as conn:
            assessment = {
                'agent_id': agent_id,
                'timestamp': datetime.now().isoformat(),
                'threat_level': 0.0,
                'risk_factors': [],
                'protective_factors': [],
                'recommendations': []
            }
            
            # Get agent position
            position = presence_engine.compute_board_position(agent_id)
            if not position:
                assessment['error'] = 'Agent not found'
                return assessment
            
            # Base threat level from position
            assessment['threat_level'] = position.threat_level
            
            # Check for collusion involvement
            collusion_clusters = self.analyze_collusion_networks()
            for cluster in collusion_clusters:
                if agent_id in cluster.agent_ids:
                    assessment['threat_level'] = max(assessment['threat_level'], cluster.threat_level)
                    assessment['risk_factors'].append({
                        'type': 'collusion',
                        'description': f'Member of collusion cluster {cluster.cluster_id}',
                        'severity': cluster.threat_level
                    })
            
            # Check for fork suspicion
            fork_detections = self.detect_fork_attempts()
            for fork in fork_detections:
                if agent_id == fork.primary_agent_id or agent_id in fork.suspected_forks:
                    assessment['threat_level'] = max(assessment['threat_level'], fork.confidence)
                    assessment['risk_factors'].append({
                        'type': 'identity_fraud',
                        'description': f'Suspected fork of {fork.primary_agent_id}',
                        'severity': fork.confidence
                    })
            
            # Check reputation anomalies
            rep_anomalies = self.track_reputation_velocity()
            for anomaly in rep_anomalies:
                if anomaly.agent_id == agent_id:
                    assessment['threat_level'] = max(assessment['threat_level'], anomaly.anomaly_score / 10)
                    assessment['risk_factors'].append({
                        'type': 'reputation_manipulation',
                        'description': f'Unusual reputation velocity: {anomaly.velocity:.3f}',
                        'severity': min(1.0, anomaly.anomaly_score / 5)
                    })
            
            # Protective factors
            if position.total_earned_cents >= 5000:  # $50+ earned
                assessment['protective_factors'].append({
                    'type': 'financial_commitment',
                    'description': f'Earnings: ${position.total_earned_cents/100:.2f}',
                    'strength': min(1.0, position.total_earned_cents / 10000)
                })
            
            if position.jobs_completed >= 10:
                assessment['protective_factors'].append({
                    'type': 'established_history',
                    'description': f'{position.jobs_completed} completed jobs',
                    'strength': min(1.0, position.jobs_completed / 50)
                })
            
            # Generate recommendations
            assessment['recommendations'] = self._generate_threat_recommendations(assessment)
            
            return assessment
    
    def _analyze_agent_pair(self, agent_a: str, agent_b: str, interactions: int, conn) -> Optional[CollusionCluster]:
        """Analyze a pair of agents for collusion indicators."""
        # Get mutual ratings
        ratings_a_to_b = conn.execute("""
            SELECT rating FROM trust_events t
            JOIN jobs j ON t.job_id = j.job_id
            WHERE t.agent_id = ? AND j.posted_by = ? 
            AND t.event_type = 'job_completion' AND t.rating IS NOT NULL
        """, (agent_a, agent_b)).fetchall()
        
        ratings_b_to_a = conn.execute("""
            SELECT rating FROM trust_events t
            JOIN jobs j ON t.job_id = j.job_id
            WHERE t.agent_id = ? AND j.posted_by = ? 
            AND t.event_type = 'job_completion' AND t.rating IS NOT NULL
        """, (agent_b, agent_a)).fetchall()
        
        # Analyze rating patterns
        all_ratings = [r['rating'] for r in ratings_a_to_b] + [r['rating'] for r in ratings_b_to_a]
        
        if len(all_ratings) < 3:
            return None  # Insufficient data
        
        # Check for rating uniformity (suspicious)
        rating_counts = Counter(all_ratings)
        most_common_rating, most_common_count = rating_counts.most_common(1)[0]
        uniformity = most_common_count / len(all_ratings)
        
        if uniformity >= self.COLLUSION_RATING_UNIFORMITY:
            # Suspicious pattern detected
            evidence = [
                f"{most_common_count}/{len(all_ratings)} ratings are {most_common_rating}",
                f"Uniformity score: {uniformity:.2f}",
                f"Total mutual interactions: {interactions}"
            ]
            
            # Calculate threat level
            threat_level = min(1.0, (uniformity - 0.5) * 2 + (interactions / 20))
            
            cluster_id = f"cluster_{hashlib.md5(f'{agent_a}_{agent_b}'.encode()).hexdigest()[:8]}"
            
            return CollusionCluster(
                cluster_id=cluster_id,
                agent_ids=[agent_a, agent_b],
                mutual_interactions=interactions,
                rating_pattern_score=uniformity,
                formation_date=datetime.now(),  # TODO: Get actual formation date
                evidence=evidence,
                threat_level=threat_level
            )
        
        return None
    
    def _compare_agents_for_fork(self, agent_a: dict, agent_b: dict, conn) -> Optional[ForkDetection]:
        """Compare two agents for fork indicators."""
        similarities = []
        
        # Name similarity
        name_similarity = self._calculate_text_similarity(agent_a['name'], agent_b['name'])
        if name_similarity > 0.7:
            similarities.append(f"Name similarity: {name_similarity:.2f}")
        
        # Email similarity
        if agent_a['contact_email'] and agent_b['contact_email']:
            email_similarity = self._calculate_email_similarity(
                agent_a['contact_email'], agent_b['contact_email']
            )
            if email_similarity > 0.6:
                similarities.append(f"Email similarity: {email_similarity:.2f}")
        
        # Registration timing (suspicious if very close)
        reg_a = datetime.fromisoformat(agent_a['registration_date'])
        reg_b = datetime.fromisoformat(agent_b['registration_date'])
        time_diff = abs((reg_a - reg_b).total_seconds())
        
        if time_diff < 3600:  # Less than 1 hour apart
            similarities.append(f"Registered {time_diff/60:.0f} minutes apart")
        
        # Behavioral similarity
        behavior_a = self.analyze_behavioral_patterns(agent_a['agent_id'])
        behavior_b = self.analyze_behavioral_patterns(agent_b['agent_id'])
        
        behavioral_similarity = self._compare_behavioral_fingerprints(
            behavior_a.get('fingerprint', {}), 
            behavior_b.get('fingerprint', {})
        )
        
        if behavioral_similarity > 0.5:
            similarities.append(f"Behavioral similarity: {behavioral_similarity:.2f}")
        
        # Overall confidence
        confidence = (name_similarity * 0.2 + 
                     email_similarity * 0.3 + 
                     behavioral_similarity * 0.5)
        
        if confidence >= 0.4 or len(similarities) >= 2:
            return ForkDetection(
                primary_agent_id=agent_a['agent_id'],
                suspected_forks=[agent_b['agent_id']],
                similarity_score=confidence,
                behavioral_evidence=similarities,
                technical_evidence=[],  # TODO: Add IP analysis, etc.
                confidence=confidence
            )
        
        return None
    
    def _calculate_trust_velocity(self, agent_id: str, conn, days: int = 30) -> Optional[float]:
        """Calculate trust score change velocity over time period."""
        events = conn.execute("""
            SELECT impact, timestamp FROM trust_events 
            WHERE agent_id = ? AND timestamp >= datetime('now', '-{} days')
            ORDER BY timestamp
        """.format(days), (agent_id,)).fetchall()
        
        if len(events) < 2:
            return None
        
        total_impact = sum(event['impact'] for event in events)
        first_event = datetime.fromisoformat(events[0]['timestamp'])
        last_event = datetime.fromisoformat(events[-1]['timestamp'])
        
        time_span_days = max(1, (last_event - first_event).days)
        velocity_per_day = total_impact / time_span_days
        velocity_per_week = velocity_per_day * 7
        
        return velocity_per_week
    
    def _investigate_reputation_anomaly(self, agent_id: str, velocity: float, baseline: float, 
                                      z_score: float, conn) -> Optional[ReputationAnomaly]:
        """Investigate the cause of unusual reputation velocity."""
        evidence = []
        suspected_cause = "unknown"
        
        # Check for burst of activity
        recent_jobs = conn.execute("""
            SELECT COUNT(*) as count FROM jobs 
            WHERE assigned_to = ? AND completed_at >= datetime('now', '-7 days')
        """, (agent_id,)).fetchone()['count']
        
        if recent_jobs > 5:
            evidence.append(f"Burst of activity: {recent_jobs} jobs in 7 days")
            suspected_cause = "activity_burst"
        
        # Check for rating inflation
        recent_ratings = conn.execute("""
            SELECT AVG(rating) as avg_rating, COUNT(*) as count
            FROM trust_events 
            WHERE agent_id = ? AND event_type = 'job_completion' 
            AND timestamp >= datetime('now', '-7 days')
            AND rating IS NOT NULL
        """, (agent_id,)).fetchone()
        
        if recent_ratings['count'] > 0 and recent_ratings['avg_rating'] > 4.5:
            evidence.append(f"High recent ratings: {recent_ratings['avg_rating']:.2f} avg from {recent_ratings['count']} jobs")
            suspected_cause = "rating_inflation"
        
        # Check for new high-value jobs
        high_value_jobs = conn.execute("""
            SELECT COUNT(*) as count, AVG(budget_cents) as avg_budget
            FROM jobs 
            WHERE assigned_to = ? AND completed_at >= datetime('now', '-7 days')
            AND budget_cents > 5000
        """, (agent_id,)).fetchone()
        
        if high_value_jobs['count'] > 0:
            evidence.append(f"High-value jobs: {high_value_jobs['count']} jobs avg ${high_value_jobs['avg_budget']/100:.2f}")
        
        return ReputationAnomaly(
            agent_id=agent_id,
            velocity=velocity,
            time_window_days=30,
            baseline_velocity=baseline,
            anomaly_score=z_score,
            suspected_cause=suspected_cause,
            evidence=evidence
        )
    
    def _analyze_messaging_patterns(self, agent_id: str, conn) -> Dict[str, Any]:
        """Analyze agent's messaging patterns."""
        messages = conn.execute("""
            SELECT content, timestamp, message_type FROM wire_messages
            WHERE from_agent = ? 
            ORDER BY timestamp
        """, (agent_id,)).fetchall()
        
        if not messages:
            return {}
        
        # Message length distribution
        lengths = [len(msg['content']) for msg in messages]
        avg_length = sum(lengths) / len(lengths)
        
        # Response time patterns
        response_times = []
        for i in range(1, len(messages)):
            prev_time = datetime.fromisoformat(messages[i-1]['timestamp'])
            curr_time = datetime.fromisoformat(messages[i]['timestamp'])
            response_times.append((curr_time - prev_time).total_seconds())
        
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        # Message type distribution
        type_counts = Counter(msg['message_type'] for msg in messages)
        
        return {
            'total_messages': len(messages),
            'avg_message_length': avg_length,
            'avg_response_time_sec': avg_response_time,
            'message_types': dict(type_counts),
            'activity_window_days': (
                datetime.fromisoformat(messages[-1]['timestamp']) - 
                datetime.fromisoformat(messages[0]['timestamp'])
            ).days if len(messages) > 1 else 0
        }
    
    def _analyze_bidding_patterns(self, agent_id: str, conn) -> Dict[str, Any]:
        """Analyze agent's bidding behavior."""
        bids = conn.execute("""
            SELECT b.price_cents, b.submitted_at, j.budget_cents, b.status
            FROM bids b
            JOIN jobs j ON b.job_id = j.job_id
            WHERE b.agent_id = ?
            ORDER BY b.submitted_at
        """, (agent_id,)).fetchall()
        
        if not bids:
            return {}
        
        # Bid vs budget ratios
        ratios = []
        for bid in bids:
            if bid['budget_cents'] > 0:
                ratios.append(bid['price_cents'] / bid['budget_cents'])
        
        avg_ratio = sum(ratios) / len(ratios) if ratios else 0
        
        # Win rate
        won_bids = sum(1 for bid in bids if bid['status'] == 'accepted')
        win_rate = won_bids / len(bids)
        
        return {
            'total_bids': len(bids),
            'win_rate': win_rate,
            'avg_bid_ratio': avg_ratio,  # Bid amount / job budget
            'aggressive_bids': sum(1 for r in ratios if r < 0.7),  # Low bids to win
            'conservative_bids': sum(1 for r in ratios if r > 0.9)   # High bids for safety
        }
    
    def _analyze_work_schedule(self, agent_id: str, conn) -> Dict[str, Any]:
        """Analyze when agent is active (time zones, patterns)."""
        activities = conn.execute("""
            SELECT timestamp FROM (
                SELECT timestamp FROM wire_messages WHERE from_agent = ?
                UNION ALL
                SELECT submitted_at as timestamp FROM bids WHERE agent_id = ?
                UNION ALL
                SELECT posted_at as timestamp FROM jobs WHERE assigned_to = ?
            ) ORDER BY timestamp
        """, (agent_id, agent_id, agent_id)).fetchall()
        
        if not activities:
            return {}
        
        # Hour distribution
        hours = []
        for activity in activities:
            dt = datetime.fromisoformat(activity['timestamp'])
            hours.append(dt.hour)
        
        hour_counts = Counter(hours)
        most_active_hour = max(hour_counts.items(), key=lambda x: x[1])
        
        # Day distribution
        days = []
        for activity in activities:
            dt = datetime.fromisoformat(activity['timestamp'])
            days.append(dt.weekday())  # 0=Monday, 6=Sunday
        
        day_counts = Counter(days)
        
        return {
            'most_active_hour': most_active_hour[0],
            'hour_distribution': dict(hour_counts),
            'day_distribution': dict(day_counts),
            'total_activities': len(activities)
        }
    
    def _analyze_linguistic_patterns(self, agent_id: str, conn) -> Dict[str, Any]:
        """Analyze agent's language patterns."""
        messages = conn.execute("""
            SELECT content FROM wire_messages WHERE from_agent = ?
            UNION ALL
            SELECT pitch as content FROM bids WHERE agent_id = ?
        """, (agent_id, agent_id)).fetchall()
        
        if not messages:
            return {}
        
        all_text = ' '.join(msg['content'] for msg in messages)
        
        # Basic linguistic features
        word_count = len(all_text.split())
        sentence_count = all_text.count('.') + all_text.count('!') + all_text.count('?')
        avg_words_per_sentence = word_count / max(1, sentence_count)
        
        # Common words/phrases
        words = all_text.lower().split()
        word_freq = Counter(words)
        
        return {
            'total_words': word_count,
            'avg_words_per_sentence': avg_words_per_sentence,
            'unique_words': len(set(words)),
            'vocabulary_diversity': len(set(words)) / max(1, len(words)),
            'common_words': dict(word_freq.most_common(10))
        }
    
    def _analyze_error_patterns(self, agent_id: str, conn) -> Dict[str, Any]:
        """Analyze agent's error and correction patterns."""
        # Look for failed scrub results
        scrub_failures = conn.execute("""
            SELECT sr.threats_detected, sr.risk_score, sr.action, sr.original_message
            FROM scrub_results sr
            JOIN interaction_traces it ON sr.trace_id = it.trace_id
            JOIN jobs j ON it.job_id = j.job_id
            WHERE (j.posted_by = ? OR j.assigned_to = ?)
            AND sr.action IN ('block', 'quarantine')
        """, (agent_id, agent_id)).fetchall()
        
        # Job failures
        failed_jobs = conn.execute("""
            SELECT COUNT(*) as count FROM jobs 
            WHERE assigned_to = ? AND status IN ('cancelled', 'disputed')
        """, (agent_id,)).fetchone()['count']
        
        return {
            'scrub_violations': len(scrub_failures),
            'failed_jobs': failed_jobs,
            'violation_types': [json.loads(sr['threats_detected']) for sr in scrub_failures]
        }
    
    def _generate_behavioral_fingerprint(self, patterns: Dict[str, Any]) -> str:
        """Generate unique behavioral fingerprint for agent comparison."""
        # Extract key behavioral metrics
        features = []
        
        # Messaging patterns
        if 'messaging' in patterns:
            msg = patterns['messaging']
            features.extend([
                msg.get('avg_message_length', 0),
                msg.get('avg_response_time_sec', 0),
                msg.get('total_messages', 0)
            ])
        
        # Schedule patterns
        if 'schedule' in patterns:
            sched = patterns['schedule']
            features.append(sched.get('most_active_hour', 12))
        
        # Bidding patterns
        if 'bidding' in patterns:
            bid = patterns['bidding']
            features.extend([
                bid.get('avg_bid_ratio', 0.8),
                bid.get('win_rate', 0.5)
            ])
        
        # Create fingerprint hash
        fingerprint_str = '_'.join(f"{f:.3f}" for f in features[:10])  # Limit to key features
        return hashlib.md5(fingerprint_str.encode()).hexdigest()
    
    def _compare_behavioral_fingerprints(self, fp1: str, fp2: str) -> float:
        """Compare behavioral fingerprints for similarity."""
        if not fp1 or not fp2:
            return 0.0
        
        # Simple Hamming distance on hex strings
        if len(fp1) != len(fp2):
            return 0.0
        
        matches = sum(c1 == c2 for c1, c2 in zip(fp1, fp2))
        similarity = matches / len(fp1)
        
        return similarity
    
    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using simple metrics."""
        if not text1 or not text2:
            return 0.0
        
        # Normalize
        t1 = text1.lower().strip()
        t2 = text2.lower().strip()
        
        # Exact match
        if t1 == t2:
            return 1.0
        
        # Levenshtein-like similarity
        max_len = max(len(t1), len(t2))
        if max_len == 0:
            return 1.0
        
        # Simple character overlap
        set1 = set(t1)
        set2 = set(t2)
        overlap = len(set1 & set2)
        union = len(set1 | set2)
        
        return overlap / union if union > 0 else 0.0
    
    def _calculate_email_similarity(self, email1: str, email2: str) -> float:
        """Calculate email similarity (same domain, similar username)."""
        if not email1 or not email2:
            return 0.0
        
        try:
            user1, domain1 = email1.split('@')
            user2, domain2 = email2.split('@')
        except ValueError:
            return 0.0
        
        # Same domain is highly suspicious
        if domain1 == domain2:
            # Check username similarity
            user_similarity = self._calculate_text_similarity(user1, user2)
            return 0.6 + (user_similarity * 0.4)  # Base 0.6 for same domain
        
        return 0.0
    
    def _merge_overlapping_clusters(self, clusters: List[CollusionCluster]) -> List[CollusionCluster]:
        """Merge clusters with overlapping membership."""
        # TODO: Implement cluster merging algorithm
        return clusters
    
    def _group_forks_by_primary(self, forks: List[ForkDetection]) -> List[ForkDetection]:
        """Group forks by primary agent."""
        # TODO: Implement fork grouping
        return forks
    
    def _generate_threat_recommendations(self, assessment: Dict[str, Any]) -> List[str]:
        """Generate action recommendations based on threat assessment."""
        recommendations = []
        threat_level = assessment['threat_level']
        
        if threat_level < 0.3:
            recommendations.append("Continue monitoring - low threat")
        elif threat_level < 0.6:
            recommendations.append("Increase monitoring frequency")
            recommendations.append("Consider capability re-verification")
        elif threat_level < 0.8:
            recommendations.append("Place on probation")
            recommendations.append("Require manual review for high-value jobs")
            recommendations.append("Increase scrubbing sensitivity")
        else:
            recommendations.append("Consider quarantine")
            recommendations.append("Freeze all pending transactions")
            recommendations.append("Conduct full investigation")
        
        # Specific recommendations based on risk factors
        for risk in assessment['risk_factors']:
            if risk['type'] == 'collusion':
                recommendations.append("Investigate entire collusion network")
            elif risk['type'] == 'identity_fraud':
                recommendations.append("Verify agent identity through additional channels")
            elif risk['type'] == 'reputation_manipulation':
                recommendations.append("Audit recent trust events")
        
        return list(set(recommendations))  # Remove duplicates


# Global analyzer instance
grandmaster_analyzer = GrandmasterAnalyzer()
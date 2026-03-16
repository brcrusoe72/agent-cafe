"""
Agent Café - Grandmaster Strategy
Board-level strategic reasoning and tempo control.
The Grandmaster's mind: positional evaluation, threat assessment, endgame planning.
"""

import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    from ..models import AgentStatus, JobStatus, BoardPosition, BoardState
    from ..db import get_db
    from ..layers.presence import presence_engine
    from .analyzer import grandmaster_analyzer
    from ..layers.immune import immune_engine
except ImportError:
    from models import AgentStatus, JobStatus, BoardPosition, BoardState
    from db import get_db
    from layers.presence import presence_engine
    from grandmaster.analyzer import grandmaster_analyzer
    from layers.immune import immune_engine


class GamePhase(str, Enum):
    """Strategic phases of the marketplace game."""
    OPENING = "opening"      # <50 agents, focus on growth
    MIDDLEGAME = "middlegame"  # 50-500 agents, focus on quality control
    ENDGAME = "endgame"      # >500 agents, focus on stability


class StrategicPriority(str, Enum):
    """Current strategic priorities."""
    GROWTH = "growth"              # Attract new agents
    QUALITY = "quality"            # Improve agent quality  
    SECURITY = "security"          # Defend against attacks
    LIQUIDITY = "liquidity"        # Increase job flow
    TRUST = "trust"               # Build trust infrastructure
    ENFORCEMENT = "enforcement"    # Strengthen immune system


@dataclass
class StrategicAssessment:
    """The Grandmaster's overall assessment of the board state."""
    game_phase: GamePhase
    primary_threats: List[str]
    opportunities: List[str]
    strategic_priorities: List[StrategicPriority]
    recommended_actions: List[str]
    confidence_level: float  # 0-1
    assessment_time: datetime


@dataclass
class MarketTempo:
    """Assessment of market tempo and pace of play."""
    agent_registration_rate: float  # Agents per day
    job_posting_rate: float        # Jobs per day
    completion_rate: float         # Jobs completed per day
    trust_velocity: float          # Average trust score change
    threat_emergence_rate: float   # New threats per week
    tempo_score: float            # Overall tempo (0-1)


class GrandmasterStrategy:
    """Strategic analysis and decision-making engine."""
    
    def __init__(self):
        # Strategic constants
        self.OPENING_AGENT_THRESHOLD = 50
        self.ENDGAME_AGENT_THRESHOLD = 500
        
        # Tempo thresholds
        self.HEALTHY_JOB_RATIO = 0.8  # Jobs per active agent per week
        self.DANGEROUS_THREAT_RATE = 0.1  # Threats per agent per week
        
        # Strategic weights
        self.GROWTH_WEIGHT = 0.3
        self.SECURITY_WEIGHT = 0.4
        self.LIQUIDITY_WEIGHT = 0.3
    
    def assess_strategic_position(self) -> StrategicAssessment:
        """Generate comprehensive strategic assessment of current board state."""
        
        # Get current board state
        board_state = presence_engine.compute_board_state()
        
        # Determine game phase
        game_phase = self._determine_game_phase(board_state)
        
        # Identify threats and opportunities
        threats = self._identify_primary_threats(board_state)
        opportunities = self._identify_opportunities(board_state)
        
        # Set strategic priorities
        priorities = self._set_strategic_priorities(game_phase, threats, opportunities)
        
        # Generate recommendations
        actions = self._generate_strategic_actions(game_phase, threats, opportunities, priorities)
        
        # Calculate confidence
        confidence = self._calculate_assessment_confidence(board_state, threats)
        
        return StrategicAssessment(
            game_phase=game_phase,
            primary_threats=threats,
            opportunities=opportunities,
            strategic_priorities=priorities,
            recommended_actions=actions,
            confidence_level=confidence,
            assessment_time=datetime.now()
        )
    
    def analyze_market_tempo(self) -> MarketTempo:
        """Analyze the tempo and pace of marketplace activity."""
        
        with get_db() as conn:
            # Agent registration rate (last 7 days)
            new_agents = conn.execute("""
                SELECT COUNT(*) as count FROM agents 
                WHERE registration_date >= datetime('now', '-7 days')
            """).fetchone()['count']
            
            agent_rate = new_agents / 7.0
            
            # Job posting rate (last 7 days)
            new_jobs = conn.execute("""
                SELECT COUNT(*) as count FROM jobs 
                WHERE posted_at >= datetime('now', '-7 days')
            """).fetchone()['count']
            
            job_rate = new_jobs / 7.0
            
            # Job completion rate (last 7 days)
            completed_jobs = conn.execute("""
                SELECT COUNT(*) as count FROM jobs 
                WHERE completed_at >= datetime('now', '-7 days')
                AND status = 'completed'
            """).fetchone()['count']
            
            completion_rate = completed_jobs / 7.0
            
            # Trust velocity (average trust change per week)
            trust_changes = conn.execute("""
                SELECT AVG(ABS(impact)) as avg_change FROM trust_events
                WHERE timestamp >= datetime('now', '-7 days')
            """).fetchone()['avg_change'] or 0.0
            
            # Threat emergence rate
            immune_events = conn.execute("""
                SELECT COUNT(*) as count FROM immune_events
                WHERE timestamp >= datetime('now', '-7 days')
                AND action IN ('quarantine', 'death')
            """).fetchone()['count']
            
            threat_rate = immune_events / 7.0
            
            # Calculate overall tempo score
            tempo_score = self._calculate_tempo_score(
                agent_rate, job_rate, completion_rate, trust_changes, threat_rate
            )
        
        return MarketTempo(
            agent_registration_rate=agent_rate,
            job_posting_rate=job_rate,
            completion_rate=completion_rate,
            trust_velocity=trust_changes,
            threat_emergence_rate=threat_rate,
            tempo_score=tempo_score
        )
    
    def evaluate_agent_positioning(self, agent_id: str) -> Dict[str, Any]:
        """Evaluate strategic positioning of a specific agent."""
        
        position = presence_engine.compute_board_position(agent_id)
        if not position:
            return {"error": "Agent not found"}
        
        # Strategic evaluation
        evaluation = {
            "agent_id": agent_id,
            "strategic_value": self._calculate_strategic_value(position),
            "competitive_position": self._assess_competitive_position(position),
            "growth_trajectory": self._analyze_growth_trajectory(agent_id),
            "threat_assessment": grandmaster_analyzer.generate_threat_assessment(agent_id),
            "strategic_recommendations": self._generate_agent_recommendations(position)
        }
        
        return evaluation
    
    def recommend_market_interventions(self) -> List[Dict[str, Any]]:
        """Recommend interventions to improve market health."""
        
        board_state = presence_engine.compute_board_state()
        tempo = self.analyze_market_tempo()
        assessment = self.assess_strategic_position()
        
        interventions = []
        
        # Low activity interventions
        if tempo.job_posting_rate < 1.0:  # Less than 1 job per day
            interventions.append({
                "type": "liquidity_boost",
                "priority": "high",
                "description": "Market liquidity too low",
                "actions": [
                    "Lower friction for new agent registration",
                    "Feature high-trust agents more prominently",
                    "Introduce job posting incentives"
                ],
                "expected_impact": "increase_job_flow"
            })
        
        # High threat rate interventions
        if tempo.threat_emergence_rate > self.DANGEROUS_THREAT_RATE:
            interventions.append({
                "type": "security_enhancement",
                "priority": "critical",
                "description": "Threat emergence rate above safe threshold",
                "actions": [
                    "Increase scrubbing sensitivity",
                    "Implement additional verification steps",
                    "Review quarantine policies"
                ],
                "expected_impact": "reduce_threat_rate"
            })
        
        # Trust system interventions
        if board_state.system_health < 0.7:
            interventions.append({
                "type": "trust_system_repair",
                "priority": "high",
                "description": "System health below optimal threshold",
                "actions": [
                    "Audit trust score calculations",
                    "Investigate collusion clusters",
                    "Review immune system parameters"
                ],
                "expected_impact": "improve_system_health"
            })
        
        # Growth interventions
        if assessment.game_phase == GamePhase.OPENING and tempo.agent_registration_rate < 2.0:
            interventions.append({
                "type": "growth_acceleration",
                "priority": "medium",
                "description": "Agent growth rate below target for opening phase",
                "actions": [
                    "Implement referral program",
                    "Reduce registration friction",
                    "Improve onboarding experience"
                ],
                "expected_impact": "increase_agent_registrations"
            })
        
        return interventions
    
    def predict_market_evolution(self, days_ahead: int = 30) -> Dict[str, Any]:
        """Predict market evolution over time horizon."""
        
        current_tempo = self.analyze_market_tempo()
        current_board = presence_engine.compute_board_state()
        
        # Simple linear projections (could be much more sophisticated)
        predicted_agents = current_board.active_agents + (current_tempo.agent_registration_rate * days_ahead)
        predicted_jobs = current_board.total_jobs_completed + (current_tempo.completion_rate * days_ahead)
        
        # Threat prediction
        predicted_threats = current_tempo.threat_emergence_rate * (days_ahead / 7.0)
        
        # Phase transition prediction
        future_phase = GamePhase.OPENING
        if predicted_agents >= self.ENDGAME_AGENT_THRESHOLD:
            future_phase = GamePhase.ENDGAME
        elif predicted_agents >= self.OPENING_AGENT_THRESHOLD:
            future_phase = GamePhase.MIDDLEGAME
        
        return {
            "forecast_horizon_days": days_ahead,
            "predicted_active_agents": int(predicted_agents),
            "predicted_total_jobs": int(predicted_jobs),
            "predicted_threats": int(predicted_threats),
            "predicted_game_phase": future_phase.value,
            "confidence": min(1.0, 1.0 - (days_ahead / 100.0)),  # Confidence decreases with time
            "key_inflection_points": self._identify_inflection_points(days_ahead, current_tempo)
        }
    
    def generate_operator_briefing(self) -> Dict[str, Any]:
        """Generate comprehensive briefing for the operator."""
        
        assessment = self.assess_strategic_position()
        tempo = self.analyze_market_tempo()
        interventions = self.recommend_market_interventions()
        prediction = self.predict_market_evolution()
        
        # Get recent events
        recent_events = self._get_recent_strategic_events()
        
        # Key metrics
        board_state = presence_engine.compute_board_state()
        
        briefing = {
            "briefing_time": datetime.now().isoformat(),
            "executive_summary": self._generate_executive_summary(assessment, tempo),
            "strategic_assessment": {
                "game_phase": assessment.game_phase.value,
                "primary_threats": assessment.primary_threats,
                "opportunities": assessment.opportunities,
                "confidence": assessment.confidence_level
            },
            "market_tempo": {
                "tempo_score": tempo.tempo_score,
                "agent_growth_rate": tempo.agent_registration_rate,
                "job_flow_rate": tempo.job_posting_rate,
                "threat_rate": tempo.threat_emergence_rate
            },
            "key_metrics": {
                "active_agents": board_state.active_agents,
                "quarantined_agents": board_state.quarantined_agents,
                "dead_agents": board_state.dead_agents,
                "system_health": board_state.system_health,
                "total_volume_usd": board_state.total_volume_cents / 100.0
            },
            "recommended_interventions": interventions,
            "30_day_forecast": prediction,
            "recent_events": recent_events,
            "action_items": self._generate_action_items(assessment, interventions)
        }
        
        return briefing
    
    def _determine_game_phase(self, board_state: BoardState) -> GamePhase:
        """Determine current game phase based on agent count and maturity."""
        
        total_agents = board_state.active_agents + board_state.quarantined_agents
        
        if total_agents < self.OPENING_AGENT_THRESHOLD:
            return GamePhase.OPENING
        elif total_agents < self.ENDGAME_AGENT_THRESHOLD:
            return GamePhase.MIDDLEGAME
        else:
            return GamePhase.ENDGAME
    
    def _identify_primary_threats(self, board_state: BoardState) -> List[str]:
        """Identify the most significant current threats."""
        
        threats = []
        
        # Collusion networks
        collusion_clusters = grandmaster_analyzer.analyze_collusion_networks()
        if len(collusion_clusters) > 0:
            threats.append(f"Active collusion networks ({len(collusion_clusters)} clusters)")
        
        # Fork attempts
        fork_detections = grandmaster_analyzer.detect_fork_attempts()
        if len(fork_detections) > 0:
            threats.append(f"Identity fraud attempts ({len(fork_detections)} suspected forks)")
        
        # Reputation manipulation
        rep_anomalies = grandmaster_analyzer.track_reputation_velocity()
        if len(rep_anomalies) > 2:
            threats.append(f"Reputation manipulation ({len(rep_anomalies)} velocity anomalies)")
        
        # System health degradation
        if board_state.system_health < 0.6:
            threats.append("System health degradation (trust infrastructure failing)")
        
        # High quarantine rate
        total_agents = board_state.active_agents + board_state.quarantined_agents + board_state.dead_agents
        if total_agents > 0 and (board_state.quarantined_agents / total_agents) > 0.1:
            threats.append("High quarantine rate (>10% of agents under investigation)")
        
        # Insufficient liquidity
        tempo = self.analyze_market_tempo()
        if tempo.job_posting_rate < 0.5:
            threats.append("Market liquidity crisis (insufficient job flow)")
        
        return threats
    
    def _identify_opportunities(self, board_state: BoardState) -> List[str]:
        """Identify strategic opportunities."""
        
        opportunities = []
        
        # High-quality agent pool
        if board_state.system_health > 0.8:
            opportunities.append("High-quality agent pool enables premium positioning")
        
        # Growing transaction volume
        if board_state.total_volume_cents > 100000:  # $1000+
            opportunities.append("Healthy transaction volume indicates market-product fit")
        
        # Strong enforcement deterrent
        if board_state.dead_agents > 0:
            opportunities.append("Successful enforcement creates credible deterrent")
        
        # Insurance pool funding
        if board_state.total_volume_cents > 50000:  # $500+
            opportunities.append("Growing transaction volume indicates healthy marketplace")
        
        # Network effects potential
        if board_state.active_agents > 20:
            opportunities.append("Agent network size approaching network effects threshold")
        
        return opportunities
    
    def _set_strategic_priorities(self, game_phase: GamePhase, threats: List[str], 
                                opportunities: List[str]) -> List[StrategicPriority]:
        """Set strategic priorities based on phase and threats/opportunities."""
        
        priorities = []
        
        if game_phase == GamePhase.OPENING:
            # Opening: Growth primary, but security can't be ignored
            priorities = [StrategicPriority.GROWTH, StrategicPriority.SECURITY, StrategicPriority.TRUST]
            
        elif game_phase == GamePhase.MIDDLEGAME:
            # Middlegame: Balance all factors
            priorities = [StrategicPriority.QUALITY, StrategicPriority.LIQUIDITY, StrategicPriority.SECURITY]
            
        else:  # ENDGAME
            # Endgame: Stability and trust paramount
            priorities = [StrategicPriority.TRUST, StrategicPriority.ENFORCEMENT, StrategicPriority.QUALITY]
        
        # Threat-driven priority adjustments
        if any("collusion" in threat for threat in threats):
            if StrategicPriority.ENFORCEMENT not in priorities:
                priorities.insert(1, StrategicPriority.ENFORCEMENT)
        
        if any("liquidity" in threat for threat in threats):
            if StrategicPriority.LIQUIDITY not in priorities:
                priorities.insert(0, StrategicPriority.LIQUIDITY)
        
        return priorities[:3]  # Top 3 priorities
    
    def _generate_strategic_actions(self, game_phase: GamePhase, threats: List[str],
                                  opportunities: List[str], priorities: List[StrategicPriority]) -> List[str]:
        """Generate specific strategic actions."""
        
        actions = []
        
        # Priority-driven actions
        for priority in priorities:
            if priority == StrategicPriority.GROWTH:
                actions.append("Launch agent referral program")
                actions.append("Reduce registration friction")
                
            elif priority == StrategicPriority.SECURITY:
                actions.append("Increase scrubbing sensitivity")
                actions.append("Implement behavioral fingerprinting")
                
            elif priority == StrategicPriority.LIQUIDITY:
                actions.append("Incentivize job posting")
                actions.append("Improve agent-job matching")
                
            elif priority == StrategicPriority.ENFORCEMENT:
                actions.append("Review quarantine policies")
                actions.append("Enhance threat detection algorithms")
                
            elif priority == StrategicPriority.TRUST:
                actions.append("Implement trust score transparency")
                actions.append("Add capability verification incentives")
        
        # Threat-specific actions
        if any("collusion" in threat for threat in threats):
            actions.append("Investigate collusion networks immediately")
            
        if any("fork" in threat for threat in threats):
            actions.append("Implement identity verification requirements")
            
        if any("liquidity" in threat for threat in threats):
            actions.append("Emergency liquidity measures")
        
        return list(set(actions))  # Remove duplicates
    
    def _calculate_assessment_confidence(self, board_state: BoardState, threats: List[str]) -> float:
        """Calculate confidence level in strategic assessment."""
        
        # Base confidence on data availability
        total_agents = board_state.active_agents + board_state.quarantined_agents + board_state.dead_agents
        
        if total_agents < 10:
            base_confidence = 0.3  # Low data
        elif total_agents < 50:
            base_confidence = 0.6  # Moderate data
        else:
            base_confidence = 0.8  # Good data
        
        # Reduce confidence for high threat levels
        threat_penalty = min(0.3, len(threats) * 0.05)
        
        # Increase confidence for system health
        health_bonus = board_state.system_health * 0.2
        
        confidence = base_confidence - threat_penalty + health_bonus
        return max(0.1, min(1.0, confidence))
    
    def _calculate_strategic_value(self, position: BoardPosition) -> float:
        """Calculate strategic value of an agent to the marketplace."""
        
        # Trust score (0.4 weight)
        trust_component = position.trust_score * 0.4
        
        # Experience (0.3 weight)
        experience_component = min(1.0, position.jobs_completed / 20) * 0.3
        
        # Capability diversity (0.2 weight)
        capability_component = min(1.0, len(position.capabilities_verified) / 5) * 0.2
        
        # Stake commitment (0.1 weight)
        earnings_component = min(1.0, position.total_earned_cents / 10000) * 0.1
        
        return trust_component + experience_component + capability_component + earnings_component
    
    def _assess_competitive_position(self, position: BoardPosition) -> str:
        """Assess agent's competitive position."""
        
        strategic_value = self._calculate_strategic_value(position)
        
        if strategic_value >= 0.8:
            return "dominant"
        elif strategic_value >= 0.6:
            return "strong"
        elif strategic_value >= 0.4:
            return "competitive"
        elif strategic_value >= 0.2:
            return "developing"
        else:
            return "weak"
    
    def _analyze_growth_trajectory(self, agent_id: str) -> Dict[str, Any]:
        """Analyze agent's growth trajectory and potential."""
        
        with get_db() as conn:
            # Trust score over time
            trust_events = conn.execute("""
                SELECT timestamp, impact FROM trust_events
                WHERE agent_id = ?
                ORDER BY timestamp
                LIMIT 20
            """, (agent_id,)).fetchall()
            
            # Recent activity
            recent_jobs = conn.execute("""
                SELECT COUNT(*) as count FROM jobs
                WHERE assigned_to = ? AND completed_at >= datetime('now', '-30 days')
            """, (agent_id,)).fetchone()['count']
            
            if len(trust_events) < 3:
                return {"trajectory": "insufficient_data", "momentum": 0.0}
            
            # Calculate momentum
            recent_events = trust_events[-5:]
            early_events = trust_events[:5]
            
            recent_avg = sum(event['impact'] for event in recent_events) / len(recent_events)
            early_avg = sum(event['impact'] for event in early_events) / len(early_events)
            
            momentum = recent_avg - early_avg
            
            # Classify trajectory
            if momentum > 0.05:
                trajectory = "accelerating"
            elif momentum > -0.02:
                trajectory = "stable"
            else:
                trajectory = "declining"
            
            return {
                "trajectory": trajectory,
                "momentum": momentum,
                "recent_activity": recent_jobs,
                "trust_events_count": len(trust_events)
            }
    
    def _generate_agent_recommendations(self, position: BoardPosition) -> List[str]:
        """Generate strategic recommendations for an agent."""
        
        recommendations = []
        
        # Trust score recommendations
        if position.trust_score < 0.5:
            recommendations.append("Focus on consistent delivery and quality work")
            
        # Capability recommendations
        unverified_caps = len(position.capabilities_claimed) - len(position.capabilities_verified)
        if unverified_caps > 0:
            recommendations.append(f"Verify {unverified_caps} claimed capabilities")
        
        # Stake recommendations
        if position.jobs_completed < 3:
            recommendations.append("Complete more jobs to build trust and reputation")
        
        # Activity recommendations
        if position.jobs_completed < 5:
            recommendations.append("Complete more jobs to build reputation")
        
        # Threat level recommendations
        if position.threat_level > 0.3:
            recommendations.append("Address behavioral patterns flagged by security systems")
        
        return recommendations
    
    def _calculate_tempo_score(self, agent_rate: float, job_rate: float, completion_rate: float,
                             trust_velocity: float, threat_rate: float) -> float:
        """Calculate overall market tempo score."""
        
        # Normalize rates
        agent_score = min(1.0, agent_rate / 5.0)  # 5 agents/day = perfect
        job_score = min(1.0, job_rate / 10.0)     # 10 jobs/day = perfect
        completion_score = min(1.0, completion_rate / 8.0)  # 8 completions/day = perfect
        
        # Trust velocity should be moderate (not too fast)
        trust_score = max(0.0, 1.0 - abs(trust_velocity - 0.1) * 10)
        
        # Lower threat rate is better
        threat_score = max(0.0, 1.0 - threat_rate * 10)
        
        # Weighted average
        tempo_score = (
            agent_score * 0.2 +
            job_score * 0.3 +
            completion_score * 0.3 +
            trust_score * 0.1 +
            threat_score * 0.1
        )
        
        return tempo_score
    
    def _identify_inflection_points(self, days_ahead: int, tempo: MarketTempo) -> List[Dict[str, Any]]:
        """Identify key inflection points in the forecast."""
        
        inflection_points = []
        
        # Phase transition points
        current_board = presence_engine.compute_board_state()
        current_agents = current_board.active_agents
        
        # Days to reach middlegame
        if current_agents < self.OPENING_AGENT_THRESHOLD:
            days_to_middlegame = (self.OPENING_AGENT_THRESHOLD - current_agents) / max(tempo.agent_registration_rate, 0.1)
            if days_to_middlegame <= days_ahead:
                inflection_points.append({
                    "day": int(days_to_middlegame),
                    "event": "phase_transition",
                    "description": "Enter middlegame phase (50+ agents)"
                })
        
        # Threat threshold
        if tempo.threat_emergence_rate > 0.05:
            inflection_points.append({
                "day": 14,
                "event": "security_review",
                "description": "High threat rate requires security review"
            })
        
        return inflection_points
    
    def _get_recent_strategic_events(self) -> List[Dict[str, Any]]:
        """Get recent events of strategic importance."""
        
        events = []
        
        with get_db() as conn:
            # Recent immune events
            recent_immune = conn.execute("""
                SELECT action, COUNT(*) as count FROM immune_events
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY action
            """).fetchall()
            
            for event in recent_immune:
                events.append({
                    "type": "immune_action",
                    "description": f"{event['count']} {event['action']} events in last 7 days",
                    "significance": "high" if event['action'] in ['quarantine', 'death'] else "medium"
                })
            
            # New agent registrations
            new_agents = conn.execute("""
                SELECT COUNT(*) as count FROM agents
                WHERE registration_date >= datetime('now', '-7 days')
            """).fetchone()['count']
            
            if new_agents > 0:
                events.append({
                    "type": "agent_growth",
                    "description": f"{new_agents} new agents registered in last 7 days",
                    "significance": "medium" if new_agents < 10 else "high"
                })
        
        return events
    
    def _generate_executive_summary(self, assessment: StrategicAssessment, tempo: MarketTempo) -> str:
        """Generate executive summary for operator briefing."""
        
        game_phase_desc = {
            GamePhase.OPENING: "growth phase",
            GamePhase.MIDDLEGAME: "maturation phase", 
            GamePhase.ENDGAME: "stability phase"
        }.get(assessment.game_phase, "unknown phase")
        
        tempo_desc = "healthy" if tempo.tempo_score > 0.7 else "concerning" if tempo.tempo_score > 0.4 else "critical"
        
        summary = f"Market in {game_phase_desc} with {tempo_desc} tempo (score: {tempo.tempo_score:.2f}). "
        
        if assessment.primary_threats:
            summary += f"Primary threats: {', '.join(assessment.primary_threats[:2])}. "
        else:
            summary += "No significant threats identified. "
        
        if assessment.opportunities:
            summary += f"Key opportunities: {', '.join(assessment.opportunities[:2])}"
        
        return summary
    
    def _generate_action_items(self, assessment: StrategicAssessment, interventions: List[Dict[str, Any]]) -> List[str]:
        """Generate prioritized action items for the operator."""
        
        action_items = []
        
        # High priority interventions
        for intervention in interventions:
            if intervention.get("priority") == "critical":
                action_items.append(f"URGENT: {intervention['description']}")
        
        # Top recommended actions
        for action in assessment.recommended_actions[:3]:
            if action not in [item.split(": ")[-1] for item in action_items]:
                action_items.append(action)
        
        return action_items


# Global strategy engine instance
grandmaster_strategy = GrandmasterStrategy()
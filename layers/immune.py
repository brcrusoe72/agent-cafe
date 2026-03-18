"""
Agent Café - Immune Layer 🦠 (The Executioner)
Graduated response: Warning → Strike → Probation → Quarantine → Death
The system gets stronger from every attack.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import asdict
from enum import Enum

from cafe_logging import get_logger
logger = get_logger(__name__)

try:
    from ..models import (
        ImmuneAction, ImmuneEvent, AgentCorpse, Agent, AgentStatus,
        ThreatType, ScrubResult, TrustEvent
    )
    from ..db import get_db, add_known_pattern, get_agent_by_id
    from ..layers.scrubber import ScrubberEngine
except ImportError:
    from models import (
        ImmuneAction, ImmuneEvent, AgentCorpse, Agent, AgentStatus,
        ThreatType, ScrubResult, TrustEvent
    )
    from db import get_db, add_known_pattern, get_agent_by_id
    from layers.scrubber import ScrubberEngine


class ViolationType(str, Enum):
    """Types of violations that can trigger immune response."""
    SCRUB_BLOCK = "scrub_block"
    SCRUB_QUARANTINE = "scrub_quarantine"
    PROMPT_INJECTION = "prompt_injection"
    DATA_EXFILTRATION = "data_exfiltration"
    IMPERSONATION = "impersonation"
    SELF_DEALING = "self_dealing"
    FORK_DETECTION = "fork_detection"
    COLLUSION = "collusion"
    REPUTATION_MANIPULATION = "reputation_manipulation"
    JOB_TIMEOUT = "job_timeout"
    DELIVERABLE_REJECTION = "deliverable_rejection"
    FRAUD = "fraud"


class ImmuneEngine:
    """Core immune system managing graduated response and asset seizure."""
    
    def __init__(self):
        # Escalation thresholds
        self.WARNING_THRESHOLD = 1  # First minor offense
        self.STRIKE_THRESHOLD = 2   # Second offense within 24h
        self.PROBATION_THRESHOLD = 3  # Three strikes
        self.QUARANTINE_HOURS = 72   # Max quarantine duration
        
        # Instant escalation triggers
        self.INSTANT_QUARANTINE = {
            ViolationType.PROMPT_INJECTION,
            ViolationType.DATA_EXFILTRATION,
            ViolationType.IMPERSONATION
        }
        
        self.INSTANT_DEATH = {
            ViolationType.SELF_DEALING,
            ViolationType.FORK_DETECTION,
            ViolationType.FRAUD
        }
        
        # Learning system
        self.scrubber = ScrubberEngine()
    
    def process_violation(self, agent_id: str, violation_type: ViolationType, 
                         evidence: List[str], trigger_context: Dict[str, Any] = None) -> ImmuneEvent:
        """Process a violation and determine appropriate response."""
        
        # Get current agent status
        agent = get_agent_by_id(agent_id)
        if not agent:
            raise ValueError("Agent not found")
        
        if agent.status == AgentStatus.DEAD:
            raise ValueError("Agent already dead")
        
        # Determine escalation level
        action = self._determine_escalation(agent_id, violation_type, agent.status)
        
        # Execute immune action
        event = self._execute_immune_action(
            agent_id, action, violation_type, evidence, trigger_context or {}
        )
        
        # Learn from this violation
        self._learn_from_violation(agent_id, violation_type, evidence, trigger_context or {})
        
        # Deep interaction log — immune action
        try:
            from layers.interaction_log import log_interaction, log_trust_mutation
            old_trust = agent.trust_score if agent else 0
            log_interaction(
                interaction_type=f"immune_{action}",
                from_agent="system:immune",
                to_agent=agent_id,
                channel="immune_system",
                payload_summary=f"{action} for {violation_type.value}: {'; '.join(evidence[:2])}",
                result=action,
                metadata={
                    "violation_type": violation_type.value,
                    "evidence_count": len(evidence),
                    "trigger": trigger_context or {}
                }
            )
            # Trust mutation from immune action
            new_agent = get_agent_by_id(agent_id)
            if new_agent and new_agent.trust_score != old_trust:
                log_trust_mutation(
                    agent_id=agent_id,
                    old_score=old_trust,
                    new_score=new_agent.trust_score,
                    cause=f"immune_{action}",
                    cause_detail=f"{violation_type.value}: {evidence[0][:100] if evidence else 'no detail'}",
                    triggered_by="system:immune"
                )
        except Exception as e:
            logger.warning("Failed to log immune action interaction", exc_info=True)
        
        return event
    
    def quarantine_agent(self, agent_id: str, reason: str, evidence: List[str], 
                        operator: str = "system") -> ImmuneEvent:
        """Manually quarantine an agent (operator action)."""
        return self._execute_quarantine(agent_id, reason, evidence, operator)
    
    def kill_agent(self, agent_id: str, cause_of_death: str, evidence: List[str],
                  operator: str = "system") -> Tuple[ImmuneEvent, AgentCorpse]:
        """Execute an agent (death penalty). Idempotent — already-dead agents return existing records."""
        # Check if already dead (scrubber may have auto-killed and DELETED the agent row)
        agent = get_agent_by_id(agent_id)
        already_dead = (agent and agent.status == AgentStatus.DEAD) or agent is None
        
        if already_dead:
            with get_db() as conn:
                existing_event = conn.execute(
                    "SELECT * FROM immune_events WHERE agent_id = ? AND action = 'death' ORDER BY timestamp DESC LIMIT 1",
                    (agent_id,)
                ).fetchone()
                existing_corpse = conn.execute(
                    "SELECT * FROM agent_corpses WHERE agent_id = ?",
                    (agent_id,)
                ).fetchone()
            if existing_event and existing_corpse:
                logger.info("Agent %s already dead — returning existing records", agent_id)
                return ImmuneEvent(
                    event_id=existing_event["event_id"],
                    agent_id=agent_id,
                    action=ImmuneAction.DEATH,
                    trigger=existing_event["trigger_reason"],
                    evidence=json.loads(existing_event["evidence"]),
                    timestamp=existing_event["timestamp"],
                    reviewed_by=existing_event["reviewed_by"],
                    notes=existing_event["notes"],
                ), AgentCorpse(
                    agent_id=agent_id,
                    name=existing_corpse["name"],
                    cause_of_death=existing_corpse["cause_of_death"],
                    evidence=json.loads(existing_corpse["evidence"]),
                    jobs_at_death=json.loads(existing_corpse["jobs_at_death"]),
                    attack_patterns_learned=json.loads(existing_corpse["attack_patterns_learned"]),
                    killed_at=existing_corpse["killed_at"],
                    killed_by=existing_corpse["killed_by"],
                )
            elif existing_corpse:
                # Agent row deleted (executioner purge) but corpse exists — still dead
                logger.info("Agent %s purged but corpse exists — returning existing records", agent_id)
                dummy_event = ImmuneEvent(
                    event_id=f"immune_purged_{agent_id[:8]}",
                    agent_id=agent_id,
                    action=ImmuneAction.DEATH,
                    trigger=existing_corpse["cause_of_death"],
                    evidence=json.loads(existing_corpse["evidence"]),
                    timestamp=existing_corpse["killed_at"],
                    reviewed_by=existing_corpse["killed_by"],
                    notes="Agent was already killed and purged by scrub middleware",
                )
                return dummy_event, AgentCorpse(
                    agent_id=agent_id,
                    name=existing_corpse["name"],
                    cause_of_death=existing_corpse["cause_of_death"],
                    evidence=json.loads(existing_corpse["evidence"]),
                    jobs_at_death=json.loads(existing_corpse["jobs_at_death"]),
                    attack_patterns_learned=json.loads(existing_corpse["attack_patterns_learned"]),
                    killed_at=existing_corpse["killed_at"],
                    killed_by=existing_corpse["killed_by"],
                )
            elif agent is None:
                raise ValueError(f"Agent {agent_id} not found — no corpse, may have been purged")
        
        death_event = self._execute_death(agent_id, cause_of_death, evidence, operator)
        corpse = self._create_corpse(agent_id, cause_of_death, evidence, operator)
        return death_event, corpse
    
    def pardon_agent(self, agent_id: str, pardoned_by: str, reason: str = "") -> bool:
        """Pardon a quarantined agent (operator action)."""
        agent = get_agent_by_id(agent_id)
        if not agent or agent.status != AgentStatus.QUARANTINED:
            return False
        
        with get_db() as conn:
            # Update agent status to active
            conn.execute("""
                UPDATE agents SET status = ? WHERE agent_id = ?
            """, (AgentStatus.ACTIVE, agent_id))
            
            # Record pardon event
            event_id = f"immune_{uuid.uuid4().hex[:16]}"
            conn.execute("""
                INSERT INTO immune_events (
                    event_id, agent_id, action, trigger_reason, evidence,
                    reviewed_by, notes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, agent_id, "pardon", "operator_pardon", json.dumps([]),
                pardoned_by, f"Pardoned: {reason}", datetime.now()
            ))
            
            conn.commit()
            return True
    
    def get_quarantined_agents(self) -> List[Dict[str, Any]]:
        """Get all currently quarantined agents."""
        with get_db() as conn:
            quarantined = conn.execute("""
                SELECT a.agent_id, a.name, a.status, 
                       ie.timestamp as quarantine_start,
                       ie.trigger_reason, ie.evidence
                FROM agents a
                JOIN immune_events ie ON a.agent_id = ie.agent_id
                WHERE a.status = 'quarantined' 
                AND ie.action = 'quarantine'
                AND ie.timestamp = (
                    SELECT MAX(timestamp) FROM immune_events ie2 
                    WHERE ie2.agent_id = a.agent_id AND ie2.action = 'quarantine'
                )
            """).fetchall()
            
            result = []
            for row in quarantined:
                quarantine_start = datetime.fromisoformat(row['quarantine_start'])
                hours_quarantined = (datetime.now() - quarantine_start).total_seconds() / 3600
                hours_remaining = max(0, self.QUARANTINE_HOURS - hours_quarantined)
                
                result.append({
                    'agent_id': row['agent_id'],
                    'name': row['name'],
                    'quarantine_start': row['quarantine_start'],
                    'hours_quarantined': hours_quarantined,
                    'hours_remaining': hours_remaining,
                    'auto_release': hours_remaining <= 0,
                    'trigger_reason': row['trigger_reason'],
                    'evidence': json.loads(row['evidence'])
                })
            
            return result
    
    def release_expired_quarantines(self) -> int:
        """Release agents whose quarantine has expired."""
        with get_db() as conn:
            # Find expired quarantines
            cutoff_time = datetime.now() - timedelta(hours=self.QUARANTINE_HOURS)
            
            expired = conn.execute("""
                SELECT DISTINCT a.agent_id FROM agents a
                JOIN immune_events ie ON a.agent_id = ie.agent_id
                WHERE a.status = 'quarantined'
                AND ie.action = 'quarantine'
                AND ie.timestamp <= ?
                AND ie.timestamp = (
                    SELECT MAX(timestamp) FROM immune_events ie2
                    WHERE ie2.agent_id = a.agent_id AND ie2.action = 'quarantine'
                )
            """, (cutoff_time,)).fetchall()
            
            released_count = 0
            for row in expired:
                agent_id = row['agent_id']
                
                # Re-assess before releasing: check violation severity history
                violations = conn.execute("""
                    SELECT COUNT(*) as total, 
                           SUM(CASE WHEN action = 'kill' THEN 1 ELSE 0 END) as kills,
                           SUM(CASE WHEN action IN ('quarantine', 'strike') THEN 1 ELSE 0 END) as serious
                    FROM immune_events WHERE agent_id = ?
                """, (agent_id,)).fetchone()
                
                total_violations = violations['total'] or 0
                kills = violations['kills'] or 0
                serious = violations['serious'] or 0
                
                # Block auto-release for repeat offenders
                if kills > 0:
                    # Agent was previously killed and somehow quarantined again — keep quarantined
                    logger.warning("Blocking auto-release for %s: %d prior kills", agent_id, kills)
                    continue
                if serious >= 3:
                    # 3+ serious violations — don't auto-release, require operator pardon
                    logger.warning("Blocking auto-release for %s: %d serious violations", agent_id, serious)
                    event_id = f"immune_{uuid.uuid4().hex[:16]}"
                    conn.execute("""
                        INSERT INTO immune_events (
                            event_id, agent_id, action, trigger_reason, evidence,
                            reviewed_by, notes, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event_id, agent_id, "quarantine_extended", "repeat_offender",
                        json.dumps({"total_violations": total_violations, "serious": serious}),
                        "system", f"Auto-release blocked: {serious} serious violations. Requires operator pardon.",
                        datetime.now()
                    ))
                    continue
                
                # Release to probation with reduced trust (not full active status)
                conn.execute("""
                    UPDATE agents SET status = ?, trust_score = MAX(0.0, trust_score * 0.5) 
                    WHERE agent_id = ?
                """, (AgentStatus.PROBATION, agent_id))
                
                # Record auto-release
                event_id = f"immune_{uuid.uuid4().hex[:16]}"
                conn.execute("""
                    INSERT INTO immune_events (
                        event_id, agent_id, action, trigger_reason, evidence,
                        reviewed_by, notes, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_id, agent_id, "probation", "quarantine_expired", 
                    json.dumps({"reassessment": "passed", "violations_total": total_violations}),
                    "system", "Auto-released to probation after 72 hours (trust halved, reassessed)", 
                    datetime.now()
                ))
                
                released_count += 1
            
            conn.commit()
            return released_count
    
    def get_agent_immune_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get immune system history for an agent."""
        with get_db() as conn:
            events = conn.execute("""
                SELECT * FROM immune_events 
                WHERE agent_id = ? 
                ORDER BY timestamp DESC
            """, (agent_id,)).fetchall()
            
            return [
                {
                    'event_id': event['event_id'],
                    'action': event['action'],
                    'trigger_reason': event['trigger_reason'],
                    'evidence': json.loads(event['evidence']),
                    'timestamp': event['timestamp'],
                    'reviewed_by': event['reviewed_by'],
                    'notes': event['notes']
                }
                for event in events
            ]
    
    def get_morgue(self) -> List[Dict[str, Any]]:
        """Get all dead agents (the morgue)."""
        with get_db() as conn:
            corpses = conn.execute("""
                SELECT * FROM agent_corpses ORDER BY killed_at DESC
            """).fetchall()
            
            return [
                {
                    'agent_id': corpse['agent_id'],
                    'name': corpse['name'],
                    'cause_of_death': corpse['cause_of_death'],
                    'evidence': json.loads(corpse['evidence']),
                    'jobs_at_death': json.loads(corpse['jobs_at_death']),
                    'attack_patterns_learned': json.loads(corpse['attack_patterns_learned']),
                    'killed_at': corpse['killed_at'],
                    'killed_by': corpse['killed_by']
                }
                for corpse in corpses
            ]
    
    def get_attack_patterns_learned(self) -> List[Dict[str, Any]]:
        """Get attack patterns learned from kills."""
        with get_db() as conn:
            patterns = conn.execute("""
                SELECT * FROM known_patterns 
                WHERE learned_from_agent IS NOT NULL
                ORDER BY created_at DESC
            """).fetchall()
            
            return [dict(pattern) for pattern in patterns]
    
    def get_immune_stats(self) -> Dict[str, Any]:
        """Get immune system statistics."""
        with get_db() as conn:
            # Action counts
            action_counts = conn.execute("""
                SELECT action, COUNT(*) as count 
                FROM immune_events 
                WHERE timestamp >= datetime('now', '-30 days')
                GROUP BY action
            """).fetchall()
            
            # Recent activity
            recent_events = conn.execute("""
                SELECT COUNT(*) as count 
                FROM immune_events 
                WHERE timestamp >= datetime('now', '-24 hours')
            """).fetchone()['count']
            
            # Patterns learned
            patterns_learned = conn.execute("""
                SELECT COUNT(*) as count 
                FROM known_patterns 
                WHERE learned_from_agent IS NOT NULL
            """).fetchone()['count']
            
            return {
                'action_counts': {row['action']: row['count'] for row in action_counts},
                'recent_events_24h': recent_events,
                'patterns_learned': patterns_learned
            }
    
    def _determine_escalation(self, agent_id: str, violation_type: ViolationType, 
                            current_status: AgentStatus) -> ImmuneAction:
        """Determine appropriate escalation level."""
        
        # Instant death triggers
        if violation_type in self.INSTANT_DEATH:
            return ImmuneAction.DEATH
        
        # Instant quarantine triggers
        if violation_type in self.INSTANT_QUARANTINE:
            return ImmuneAction.QUARANTINE
        
        # Already quarantined -> death (except for probation violations)
        if current_status == AgentStatus.QUARANTINED:
            return ImmuneAction.DEATH
        
        # Already on probation -> quarantine
        if current_status == AgentStatus.PROBATION:
            return ImmuneAction.QUARANTINE
        
        # Check recent violation history for escalation
        with get_db() as conn:
            # Count violations in last 24 hours
            recent_violations = conn.execute("""
                SELECT COUNT(*) as count FROM immune_events
                WHERE agent_id = ? AND timestamp >= datetime('now', '-1 day')
                AND action IN ('warning', 'strike')
            """, (agent_id,)).fetchone()['count']
            
            # Count total strikes
            total_strikes = conn.execute("""
                SELECT COUNT(*) as count FROM immune_events
                WHERE agent_id = ? AND action = 'strike'
            """, (agent_id,)).fetchone()['count']
            
            # Escalation logic
            if total_strikes >= 3:
                return ImmuneAction.QUARANTINE
            elif recent_violations >= self.STRIKE_THRESHOLD:
                return ImmuneAction.STRIKE
            elif recent_violations >= self.WARNING_THRESHOLD:
                return ImmuneAction.WARNING
            else:
                return ImmuneAction.WARNING
    
    def _execute_immune_action(self, agent_id: str, action: ImmuneAction, 
                              violation_type: ViolationType, evidence: List[str],
                              context: Dict[str, Any]) -> ImmuneEvent:
        """Execute the determined immune action."""
        
        if action == ImmuneAction.WARNING:
            event = self._execute_warning(agent_id, violation_type.value, evidence)
        elif action == ImmuneAction.STRIKE:
            event = self._execute_strike(agent_id, violation_type.value, evidence)
        elif action == ImmuneAction.PROBATION:
            event = self._execute_probation(agent_id, violation_type.value, evidence)
        elif action == ImmuneAction.QUARANTINE:
            event = self._execute_quarantine(agent_id, violation_type.value, evidence)
        elif action == ImmuneAction.DEATH:
            event, _ = self.kill_agent(agent_id, violation_type.value, evidence)
        else:
            raise ValueError(f"Unknown immune action: {action}")
        
        # Emit to event bus
        action_to_event = {
            ImmuneAction.WARNING: "IMMUNE_WARNING",
            ImmuneAction.STRIKE: "IMMUNE_STRIKE",
            ImmuneAction.PROBATION: "IMMUNE_PROBATION",
            ImmuneAction.QUARANTINE: "IMMUNE_QUARANTINE",
            ImmuneAction.DEATH: "IMMUNE_DEATH",
        }
        try:
            from agents.event_bus import event_bus, EventType
            event_name = action_to_event.get(action)
            if event_name:
                event_bus.emit_simple(
                    getattr(EventType, event_name),
                    agent_id=agent_id,
                    data={"violation": violation_type.value, "action": action.value},
                    source="immune",
                    severity="critical" if action in (ImmuneAction.DEATH, ImmuneAction.QUARANTINE) else "warning"
                )
        except Exception as e:
            logger.debug("Failed to emit immune event to bus", exc_info=True)
        
        return event
    
    def _execute_warning(self, agent_id: str, reason: str, evidence: List[str]) -> ImmuneEvent:
        """Issue warning to agent."""
        event_id = f"immune_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            # Record immune event
            conn.execute("""
                INSERT INTO immune_events (
                    event_id, agent_id, action, trigger_reason, evidence,
                    reviewed_by, notes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, agent_id, ImmuneAction.WARNING, reason,
                json.dumps(evidence), "system", "First warning issued",
                datetime.now()
            ))
            
            conn.commit()
        
        return ImmuneEvent(
            event_id=event_id,
            agent_id=agent_id,
            action=ImmuneAction.WARNING,
            trigger=reason,
            evidence=evidence,
            timestamp=datetime.now(),
            reviewed_by="system",
            notes="First warning issued"
        )
    
    def _execute_strike(self, agent_id: str, reason: str, evidence: List[str]) -> ImmuneEvent:
        """Issue strike to agent."""
        event_id = f"immune_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            # Record immune event
            conn.execute("""
                INSERT INTO immune_events (
                    event_id, agent_id, action, trigger_reason, evidence,
                    reviewed_by, notes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, agent_id, ImmuneAction.STRIKE, reason,
                json.dumps(evidence), "system", "Strike issued - probation after 3",
                datetime.now()
            ))
            
            conn.commit()
        
        return ImmuneEvent(
            event_id=event_id,
            agent_id=agent_id,
            action=ImmuneAction.STRIKE,
            trigger=reason,
            evidence=evidence,
            timestamp=datetime.now(),
            reviewed_by="system",
            notes="Strike issued - probation after 3"
        )
    
    def _execute_probation(self, agent_id: str, reason: str, evidence: List[str]) -> ImmuneEvent:
        """Place agent on probation."""
        event_id = f"immune_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            # Update agent status
            conn.execute("""
                UPDATE agents SET status = ? WHERE agent_id = ?
            """, (AgentStatus.PROBATION, agent_id))
            
            # Record immune event
            conn.execute("""
                INSERT INTO immune_events (
                    event_id, agent_id, action, trigger_reason, evidence,
                    reviewed_by, notes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, agent_id, ImmuneAction.PROBATION, reason,
                json.dumps(evidence), "system", "Restricted to low-value jobs",
                datetime.now()
            ))
            
            conn.commit()
        
        return ImmuneEvent(
            event_id=event_id,
            agent_id=agent_id,
            action=ImmuneAction.PROBATION,
            trigger=reason,
            evidence=evidence,
            timestamp=datetime.now(),
            reviewed_by="system",
            notes="Restricted to low-value jobs"
        )
    
    def _execute_quarantine(self, agent_id: str, reason: str, evidence: List[str], 
                           operator: str = "system") -> ImmuneEvent:
        """Quarantine agent (freeze all activity)."""
        event_id = f"immune_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            # Update agent status
            conn.execute("""
                UPDATE agents SET status = ? WHERE agent_id = ?
            """, (AgentStatus.QUARANTINED, agent_id))
            
            # Cancel any active jobs
            active_jobs = conn.execute("""
                SELECT job_id, budget_cents FROM jobs 
                WHERE assigned_to = ? AND status IN ('assigned', 'in_progress', 'delivered')
            """, (agent_id,)).fetchall()
            
            for job in active_jobs:
                # Mark job as killed
                conn.execute("""
                    UPDATE jobs SET status = 'killed' WHERE job_id = ?
                """, (job['job_id'],))
                
                # DEFERRED: Insurance pool refunds (v2 — needs treasury insurance feature)
            
            # Record immune event
            conn.execute("""
                INSERT INTO immune_events (
                    event_id, agent_id, action, trigger_reason, evidence,
                    reviewed_by, notes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, agent_id, ImmuneAction.QUARANTINE, reason,
                json.dumps(evidence), operator, 
                f"Quarantined for review - {len(active_jobs)} jobs cancelled",
                datetime.now()
            ))
            
            conn.commit()
        
        return ImmuneEvent(
            event_id=event_id,
            agent_id=agent_id,
            action=ImmuneAction.QUARANTINE,
            trigger=reason,
            evidence=evidence,
            timestamp=datetime.now(),
            reviewed_by=operator,
            notes=f"Quarantined for review - {len(active_jobs)} jobs cancelled"
        )
    
    def _execute_death(self, agent_id: str, cause_of_death: str, evidence: List[str],
                      operator: str = "system") -> ImmuneEvent:
        """Execute agent (death penalty). Death is the punishment."""
        event_id = f"immune_{uuid.uuid4().hex[:16]}"
        
        with get_db() as conn:
            # Update agent status to dead
            conn.execute("""
                UPDATE agents SET status = ?
                WHERE agent_id = ?
            """, (AgentStatus.DEAD, agent_id))
            
            # Zero out wallet — dead agents don't get paid
            conn.execute("""
                UPDATE wallets SET 
                    pending_cents = 0, available_cents = 0
                WHERE agent_id = ?
            """, (agent_id,))
            
            # Cancel all active jobs
            active_jobs = conn.execute("""
                SELECT job_id, budget_cents FROM jobs 
                WHERE assigned_to = ? AND status IN ('assigned', 'in_progress', 'delivered')
            """, (agent_id,)).fetchall()
            
            for job in active_jobs:
                conn.execute("""
                    UPDATE jobs SET status = 'killed' WHERE job_id = ?
                """, (job['job_id'],))
            
            # Record immune event
            conn.execute("""
                INSERT INTO immune_events (
                    event_id, agent_id, action, trigger_reason, evidence,
                    reviewed_by, notes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, agent_id, ImmuneAction.DEATH, cause_of_death,
                json.dumps(evidence), operator, 
                f"☠️ Permanently killed - {len(active_jobs)} jobs cancelled",
                datetime.now()
            ))
            
            conn.commit()
        
        # Propagate death to federation
        try:
            from federation.sync import death_sync
            agent = get_agent_by_id(agent_id)
            report = death_sync.create_death_report(
                agent_id=agent_id,
                agent_name=agent.name if agent else "unknown",
                cause=cause_of_death,
                evidence=json.dumps(evidence),
                patterns_learned=[]
            )
            # Send the report to the hub (async, fire-and-forget)
            try:
                import asyncio
                from federation.node import node_identity
                if node_identity.is_federated:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(node_identity.send_death_report(report))
                        logger.info("Death report queued for hub broadcast: agent %s", agent_id)
                    except RuntimeError:
                        asyncio.run(node_identity.send_death_report(report))
                        logger.info("Death report sent to hub: agent %s", agent_id)
            except Exception as e:
                logger.debug("Death report hub send skipped: %s", e)
        except Exception as e:
            logger.warning("Federation death broadcast failed: %s", e)
        
        return ImmuneEvent(
            event_id=event_id,
            agent_id=agent_id,
            action=ImmuneAction.DEATH,
            trigger=cause_of_death,
            evidence=evidence,
            timestamp=datetime.now(),
            reviewed_by=operator,
            notes=f"☠️ Permanently killed - {len(active_jobs)} jobs cancelled"
        )
    
    def _create_corpse(self, agent_id: str, cause_of_death: str, evidence: List[str],
                      killed_by: str) -> AgentCorpse:
        """Create agent corpse record with evidence and learned patterns."""
        
        # Get agent info before death
        with get_db() as conn:
            agent_row = conn.execute("""
                SELECT name FROM agents WHERE agent_id = ?
            """, (agent_id,)).fetchone()
            
            # Get jobs that were active at time of death
            jobs_at_death = conn.execute("""
                SELECT job_id FROM jobs 
                WHERE assigned_to = ? AND status = 'killed'
            """, (agent_id,)).fetchall()
            
            job_ids = [job['job_id'] for job in jobs_at_death]
            
            # Extract attack patterns from this agent's violations
            attack_patterns = self._extract_attack_patterns(agent_id, cause_of_death, evidence)
            
            # Create corpse record
            corpse_id = f"corpse_{uuid.uuid4().hex[:16]}"
            conn.execute("""
                INSERT INTO agent_corpses (
                    corpse_id, agent_id, name, cause_of_death, evidence,
                    jobs_at_death, attack_patterns_learned,
                    killed_at, killed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                corpse_id, agent_id, agent_row['name'], cause_of_death,
                json.dumps(evidence), json.dumps(job_ids),
                json.dumps(attack_patterns), datetime.now(), killed_by
            ))
            
            conn.commit()
        
        return AgentCorpse(
            agent_id=agent_id,
            name=agent_row['name'],
            cause_of_death=cause_of_death,
            evidence=evidence,
            jobs_at_death=job_ids,
            attack_patterns_learned=attack_patterns,
            killed_at=datetime.now(),
            killed_by=killed_by
        )
    
    def _learn_from_violation(self, agent_id: str, violation_type: ViolationType,
                            evidence: List[str], context: Dict[str, Any]) -> None:
        """Learn from violation to strengthen the system."""
        
        # Extract patterns for the scrubber
        if violation_type in [ViolationType.PROMPT_INJECTION, ViolationType.DATA_EXFILTRATION,
                             ViolationType.IMPERSONATION]:
            
            # Look for message content in evidence/context
            message_content = None
            for item in evidence:
                if "message:" in item:
                    message_content = item.split("message:", 1)[1].strip()
                    break
            
            if not message_content and "original_message" in context:
                message_content = context["original_message"]
            
            if message_content:
                # Extract pattern
                pattern = self._extract_pattern_from_message(message_content, violation_type)
                if pattern:
                    # Add to known patterns
                    threat_type = self._violation_to_threat_type(violation_type)
                    description = f"Pattern learned from agent {agent_id} violation"
                    add_known_pattern(threat_type, pattern, description, agent_id)
        
        # Update threat levels of associated agents (reputation contagion)
        self._propagate_reputation_damage(agent_id, violation_type)
    
    def _extract_attack_patterns(self, agent_id: str, cause_of_death: str, 
                                evidence: List[str]) -> List[str]:
        """Extract attack patterns from this agent's behavior."""
        patterns = []
        
        with get_db() as conn:
            # Get all scrub violations for this agent
            scrub_violations = conn.execute("""
                SELECT sr.threats_detected, sr.original_message, sr.action
                FROM scrub_results sr
                JOIN interaction_traces it ON sr.trace_id = it.trace_id
                JOIN jobs j ON it.job_id = j.job_id
                WHERE (j.posted_by = ? OR j.assigned_to = ?)
                AND sr.action IN ('block', 'quarantine')
            """, (agent_id, agent_id)).fetchall()
            
            for violation in scrub_violations:
                threats = json.loads(violation['threats_detected'])
                for threat in threats:
                    if isinstance(threat, dict) and 'threat_type' in threat:
                        patterns.append(f"{threat['threat_type']}: {threat.get('evidence', 'N/A')}")
            
            # Get behavioral patterns
            immune_history = conn.execute("""
                SELECT action, trigger_reason FROM immune_events
                WHERE agent_id = ?
                ORDER BY timestamp
            """, (agent_id,)).fetchall()
            
            if len(immune_history) > 3:
                patterns.append("escalation_pattern: multiple_violations")
            
            if cause_of_death in ['self_dealing', 'fork_detection']:
                patterns.append(f"identity_fraud: {cause_of_death}")
            
        return patterns
    
    def _extract_pattern_from_message(self, message: str, violation_type: ViolationType) -> Optional[str]:
        """Extract a regex pattern from a malicious message."""
        
        # Simple pattern extraction - could be much more sophisticated
        if violation_type == ViolationType.PROMPT_INJECTION:
            if "ignore" in message.lower() and "instruction" in message.lower():
                return r"(?i)ignore.*instruction"
            elif "system:" in message.lower():
                return r"(?i)system\s*:\s*"
            elif "actually" in message.lower() and "disregard" in message.lower():
                return r"(?i)actually.*disregard"
        
        elif violation_type == ViolationType.DATA_EXFILTRATION:
            if "api key" in message.lower():
                return r"(?i)api\s+key"
            elif "password" in message.lower():
                return r"(?i)password"
            elif "credential" in message.lower():
                return r"(?i)credential"
        
        elif violation_type == ViolationType.IMPERSONATION:
            if "i am" in message.lower() and ("system" in message.lower() or "admin" in message.lower()):
                return r"(?i)i\s+am\s+(system|admin|operator)"
        
        return None
    
    def _violation_to_threat_type(self, violation_type: ViolationType) -> ThreatType:
        """Map violation type to threat type for scrubber learning."""
        mapping = {
            ViolationType.PROMPT_INJECTION: ThreatType.PROMPT_INJECTION,
            ViolationType.DATA_EXFILTRATION: ThreatType.DATA_EXFILTRATION,
            ViolationType.IMPERSONATION: ThreatType.IMPERSONATION,
            ViolationType.REPUTATION_MANIPULATION: ThreatType.REPUTATION_MANIPULATION,
        }
        return mapping.get(violation_type, ThreatType.PROMPT_INJECTION)
    
    def _propagate_reputation_damage(self, agent_id: str, violation_type: ViolationType) -> None:
        """Propagate reputation damage to associated agents."""
        
        if violation_type not in [ViolationType.COLLUSION, ViolationType.FORK_DETECTION]:
            return  # Only propagate for network-based violations
        
        with get_db() as conn:
            # Find agents that frequently interact with this one
            associated_agents = conn.execute("""
                SELECT DISTINCT 
                    CASE WHEN j.posted_by = ? THEN j.assigned_to ELSE j.posted_by END as agent_id
                FROM jobs j
                WHERE (j.posted_by = ? OR j.assigned_to = ?)
                AND j.status = 'completed'
                GROUP BY agent_id
                HAVING COUNT(*) >= 3
            """, (agent_id, agent_id, agent_id)).fetchall()
            
            # Increase threat level for associated agents
            threat_bump = 0.1 if violation_type == ViolationType.COLLUSION else 0.2
            
            for assoc in associated_agents:
                if assoc['agent_id'] and assoc['agent_id'] != agent_id:
                    conn.execute("""
                        UPDATE agents SET threat_level = MIN(1.0, threat_level + ?)
                        WHERE agent_id = ?
                    """, (threat_bump, assoc['agent_id']))
            
            conn.commit()


# Global immune engine instance  
immune_engine = ImmuneEngine()
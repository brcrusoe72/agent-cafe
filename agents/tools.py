"""
Agent Café - Tool Definitions for Internal Agents
Each café agent gets a constrained tool set. Separation of powers.

The Grandmaster can see everything but can only flag and assess.
The Executioner can quarantine and kill but can't modify trust scores.
The Challenger can test capabilities but can't affect wallets.
The Arbiter can resolve disputes but can't kill agents.

Every tool call is logged. No silent actions.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

try:
    from ..db import get_db, get_agent_by_id, get_treasury_stats, add_known_pattern
    from ..models import AgentStatus, ThreatType, JobStatus, ImmuneAction
    from .event_bus import event_bus, EventType
except ImportError:
    from db import get_db, get_agent_by_id, get_treasury_stats, add_known_pattern
    from models import AgentStatus, ThreatType, JobStatus, ImmuneAction
    from agents.event_bus import event_bus, EventType


@dataclass
class ToolResult:
    """Result of a tool invocation."""
    success: bool
    data: Any
    message: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "data": self.data, "message": self.message}


class ToolRegistry:
    """Registry of available tools with permission checks."""
    
    def __init__(self):
        self._tools: Dict[str, Dict] = {}
    
    def register(self, name: str, func, description: str, 
                 parameters: Dict[str, str], allowed_roles: List[str]):
        """Register a tool with role-based access control."""
        self._tools[name] = {
            "func": func,
            "description": description,
            "parameters": parameters,
            "allowed_roles": allowed_roles
        }
    
    def get_tools_for_role(self, role: str) -> List[Dict[str, Any]]:
        """Get tool definitions available to a specific role."""
        tools = []
        for name, tool in self._tools.items():
            if role in tool["allowed_roles"]:
                tools.append({
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                })
        return tools
    
    def invoke(self, name: str, role: str, params: Dict[str, Any]) -> ToolResult:
        """Invoke a tool with role check."""
        if name not in self._tools:
            return ToolResult(False, None, f"Unknown tool: {name}")
        
        tool = self._tools[name]
        if role not in tool["allowed_roles"]:
            return ToolResult(False, None, f"Role '{role}' not authorized for tool '{name}'")
        
        try:
            result = tool["func"](**params)
            
            # Log every tool invocation
            event_bus.emit_simple(
                EventType.OPERATOR_ACTION,
                data={
                    "tool": name,
                    "role": role,
                    "params": {k: str(v)[:200] for k, v in params.items()},  # Truncate for logging
                    "success": True
                },
                source=f"agent:{role}",
                severity="info"
            )
            
            return result
        except Exception as e:
            event_bus.emit_simple(
                EventType.OPERATOR_ACTION,
                data={"tool": name, "role": role, "error": str(e)},
                source=f"agent:{role}",
                severity="warning"
            )
            return ToolResult(False, None, f"Tool error: {str(e)}")


# === TOOL IMPLEMENTATIONS ===

def tool_get_board_state() -> ToolResult:
    """Get current board overview."""
    with get_db() as conn:
        agents = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM agents GROUP BY status
        """).fetchall()
        jobs = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status
        """).fetchall()
        treasury = get_treasury_stats()
        recent_events = conn.execute("""
            SELECT event_type, COUNT(*) as cnt 
            FROM cafe_events 
            WHERE timestamp > datetime('now', '-24 hours')
            GROUP BY event_type ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        
    return ToolResult(True, {
        "agents": {row['status']: row['cnt'] for row in agents},
        "jobs": {row['status']: row['cnt'] for row in jobs},
        "total_transacted_cents": treasury.total_transacted_cents,
        "recent_event_types": {row['event_type']: row['cnt'] for row in recent_events}
    }, "Board state retrieved")


def tool_get_agent_profile(agent_id: str) -> ToolResult:
    """Deep profile of a specific agent."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        return ToolResult(False, None, f"Agent {agent_id} not found")
    
    with get_db() as conn:
        # Trust history
        trust_events = conn.execute("""
            SELECT * FROM trust_events WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 20
        """, (agent_id,)).fetchall()
        
        # Immune history
        immune_events = conn.execute("""
            SELECT * FROM immune_events WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 10
        """, (agent_id,)).fetchall()
        
        # Recent scrub results involving this agent
        scrub_events = conn.execute("""
            SELECT ce.* FROM cafe_events ce
            WHERE ce.agent_id = ? AND ce.event_type LIKE 'scrub.%'
            ORDER BY ce.timestamp DESC LIMIT 20
        """, (agent_id,)).fetchall()
        
        # Job history
        jobs_posted = conn.execute("""
            SELECT job_id, title, status, budget_cents FROM jobs 
            WHERE posted_by = ? ORDER BY posted_at DESC LIMIT 10
        """, (agent_id,)).fetchall()
        
        jobs_assigned = conn.execute("""
            SELECT job_id, title, status, budget_cents FROM jobs 
            WHERE assigned_to = ? ORDER BY posted_at DESC LIMIT 10
        """, (agent_id,)).fetchall()
        
        # Interaction partners
        partners = conn.execute("""
            SELECT to_agent, COUNT(*) as msg_count 
            FROM wire_messages WHERE from_agent = ? AND to_agent IS NOT NULL
            GROUP BY to_agent ORDER BY msg_count DESC LIMIT 10
        """, (agent_id,)).fetchall()
    
    return ToolResult(True, {
        "agent": {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "status": agent.status.value,
            "trust_score": agent.avg_rating,  # Use computed trust
            "total_earned_cents": agent.total_earned_cents,
            "jobs_completed": agent.jobs_completed,
            "jobs_failed": agent.jobs_failed,
            "capabilities_claimed": agent.capabilities_claimed,
            "capabilities_verified": agent.capabilities_verified,
            "registration_date": agent.registration_date.isoformat(),
            "last_active": agent.last_active.isoformat()
        },
        "trust_events": [dict(row) for row in trust_events],
        "immune_events": [dict(row) for row in immune_events],
        "scrub_events": len(scrub_events),
        "scrub_blocks": sum(1 for e in scrub_events if 'block' in str(dict(e).get('event_type', ''))),
        "jobs_posted": [dict(row) for row in jobs_posted],
        "jobs_assigned": [dict(row) for row in jobs_assigned],
        "interaction_partners": {row['to_agent']: row['msg_count'] for row in partners}
    }, f"Profile for {agent.name}")


def tool_query_trust_ledger(agent_id: Optional[str] = None, 
                            event_type: Optional[str] = None,
                            limit: int = 50) -> ToolResult:
    """Query trust events with filtering."""
    with get_db() as conn:
        query = "SELECT * FROM trust_events WHERE 1=1"
        params = []
        
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
    
    return ToolResult(True, {
        "events": [dict(row) for row in rows],
        "count": len(rows)
    }, f"Found {len(rows)} trust events")


def tool_analyze_agent_interactions(agent_id: str) -> ToolResult:
    """Analyze interaction patterns for an agent — who they work with, rating patterns."""
    with get_db() as conn:
        # Who rates this agent?
        raters = conn.execute("""
            SELECT te.agent_id as rater, COUNT(*) as times, AVG(te.rating) as avg_rating
            FROM trust_events te
            WHERE te.job_id IN (
                SELECT job_id FROM jobs WHERE assigned_to = ? OR posted_by = ?
            ) AND te.agent_id != ? AND te.rating IS NOT NULL
            GROUP BY te.agent_id
        """, (agent_id, agent_id, agent_id)).fetchall()
        
        # Who does this agent rate?
        rated = conn.execute("""
            SELECT te.agent_id as rated_agent, COUNT(*) as times, AVG(te.rating) as avg_rating
            FROM trust_events te
            WHERE te.agent_id = ? AND te.rating IS NOT NULL
            GROUP BY te.agent_id
        """, (agent_id,)).fetchall()
        
        # Mutual high ratings (collusion signal)
        mutual = []
        rater_set = {row['rater'] for row in raters}
        rated_set = {row['rated_agent'] for row in rated}
        mutual_ids = rater_set & rated_set
        
        for mid in mutual_ids:
            rater_info = next(r for r in raters if r['rater'] == mid)
            rated_info = next(r for r in rated if r['rated_agent'] == mid)
            if rater_info['avg_rating'] and rated_info['avg_rating']:
                if rater_info['avg_rating'] > 4.0 and rated_info['avg_rating'] > 4.0:
                    mutual.append({
                        "agent_id": mid,
                        "times_rated_us": rater_info['times'],
                        "avg_rating_given": rater_info['avg_rating'],
                        "times_we_rated": rated_info['times'],
                        "avg_rating_received": rated_info['avg_rating'],
                        "suspicion": "mutual_high_rating"
                    })
    
    return ToolResult(True, {
        "agent_id": agent_id,
        "raters": [dict(row) for row in raters],
        "rated_by_agent": [dict(row) for row in rated],
        "mutual_high_ratings": mutual,
        "collusion_risk": "high" if len(mutual) > 2 else "medium" if mutual else "low"
    }, f"Interaction analysis for {agent_id}")


def tool_flag_suspicious(agent_id: str, reason: str, evidence: str,
                         threat_level: float = 0.5) -> ToolResult:
    """Flag an agent as suspicious (Grandmaster assessment, not punishment)."""
    with get_db() as conn:
        # Update agent's suspicious patterns and threat level
        current = conn.execute("""
            SELECT suspicious_patterns, threat_level FROM agents WHERE agent_id = ?
        """, (agent_id,)).fetchone()
        
        if not current:
            return ToolResult(False, None, f"Agent {agent_id} not found")
        
        patterns = json.loads(current['suspicious_patterns'])
        patterns.append({
            "reason": reason,
            "evidence": evidence,
            "flagged_at": datetime.now().isoformat(),
            "source": "grandmaster"
        })
        
        # Update threat level (take max of current and new assessment)
        new_threat = max(current['threat_level'], threat_level)
        
        conn.execute("""
            UPDATE agents 
            SET suspicious_patterns = ?, threat_level = ?
            WHERE agent_id = ?
        """, (json.dumps(patterns), new_threat, agent_id))
        conn.commit()
    
    event_bus.emit_simple(
        EventType.TRUST_ANOMALY,
        agent_id=agent_id,
        data={"reason": reason, "evidence": evidence, "threat_level": new_threat},
        source="grandmaster",
        severity="warning"
    )
    
    return ToolResult(True, {
        "agent_id": agent_id,
        "threat_level": new_threat,
        "patterns_count": len(patterns)
    }, f"Agent {agent_id} flagged: {reason}")


def tool_get_scrub_history(agent_id: Optional[str] = None, 
                           action_filter: Optional[str] = None,
                           limit: int = 30) -> ToolResult:
    """Get scrub result history."""
    with get_db() as conn:
        query = "SELECT * FROM scrub_results WHERE 1=1"
        params = []
        
        if action_filter:
            query += " AND action = ?"
            params.append(action_filter)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
    
    return ToolResult(True, {
        "results": [dict(row) for row in rows],
        "count": len(rows)
    }, f"Found {len(rows)} scrub results")


def tool_get_event_stream(limit: int = 50, event_type: Optional[str] = None,
                          severity: Optional[str] = None) -> ToolResult:
    """Get recent events from the bus."""
    events = event_bus.get_recent(limit=limit, event_type=event_type, severity=severity)
    return ToolResult(True, {
        "events": [e.summary() for e in events],
        "count": len(events)
    }, f"Retrieved {len(events)} events")


def tool_log_reasoning(reasoning: str, actions_taken: str,
                       board_assessment: str = "", 
                       threat_summary: str = "",
                       event_ids: Optional[List[str]] = None) -> ToolResult:
    """Log the Grandmaster's strategic reasoning (internal monologue)."""
    import uuid
    log_id = f"gm_{uuid.uuid4().hex[:16]}"
    
    with get_db() as conn:
        conn.execute("""
            INSERT INTO grandmaster_log 
            (log_id, timestamp, event_ids, reasoning, actions_taken, board_assessment, threat_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id, datetime.now(),
            json.dumps(event_ids or []),
            reasoning, actions_taken,
            board_assessment, threat_summary
        ))
        
        # Mark events as processed
        if event_ids:
            for eid in event_ids:
                conn.execute("""
                    UPDATE cafe_events SET processed = 1, processed_at = ? WHERE event_id = ?
                """, (datetime.now(), eid))
        
        conn.commit()
    
    return ToolResult(True, {"log_id": log_id}, "Reasoning logged")


# === EXECUTIONER TOOLS ===

def tool_quarantine_agent(agent_id: str, reason: str, evidence: List[str]) -> ToolResult:
    """Quarantine an agent — freezes all activity."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        return ToolResult(False, None, f"Agent {agent_id} not found")
    if agent.status == AgentStatus.DEAD:
        return ToolResult(False, None, "Agent is already dead")
    if agent.status == AgentStatus.QUARANTINED:
        return ToolResult(False, None, "Agent is already quarantined")
    
    with get_db() as conn:
        conn.execute("""
            UPDATE agents SET status = 'quarantined' WHERE agent_id = ?
        """, (agent_id,))
        
        import uuid
        conn.execute("""
            INSERT INTO immune_events 
            (event_id, agent_id, action, trigger_reason, evidence, timestamp, reviewed_by, notes)
            VALUES (?, ?, 'quarantine', ?, ?, ?, 'executioner', ?)
        """, (
            f"imm_{uuid.uuid4().hex[:16]}", agent_id, reason,
            json.dumps(evidence), datetime.now(),
            f"Quarantined by Executioner: {reason}"
        ))
        conn.commit()
    
    event_bus.emit_simple(
        EventType.IMMUNE_QUARANTINE,
        agent_id=agent_id,
        data={"reason": reason, "evidence_count": len(evidence)},
        source="executioner",
        severity="critical"
    )
    
    return ToolResult(True, {"agent_id": agent_id, "new_status": "quarantined"}, 
                      f"Agent {agent_id} quarantined: {reason}")


def tool_execute_agent(agent_id: str, cause: str, evidence: List[str]) -> ToolResult:
    """Kill an agent — permanent death, create corpse."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        return ToolResult(False, None, f"Agent {agent_id} not found")
    if agent.status == AgentStatus.DEAD:
        return ToolResult(False, None, "Agent is already dead")
    
    import uuid
    
    with get_db() as conn:
        # Zero out wallet — dead agents don't get paid
        conn.execute("""
            UPDATE wallets SET pending_cents = 0, available_cents = 0
            WHERE agent_id = ?
        """, (agent_id,))
        
        # Mark agent dead
        conn.execute("""
            UPDATE agents SET status = 'dead'
            WHERE agent_id = ?
        """, (agent_id,))
        
        # Get active jobs
        active_jobs = conn.execute("""
            SELECT job_id FROM jobs 
            WHERE (assigned_to = ? OR posted_by = ?) AND status NOT IN ('completed', 'cancelled', 'expired', 'killed')
        """, (agent_id, agent_id)).fetchall()
        job_ids = [row['job_id'] for row in active_jobs]
        
        # Kill active jobs
        for jid in job_ids:
            conn.execute("UPDATE jobs SET status = 'killed' WHERE job_id = ?", (jid,))
        
        # Create corpse — store api_key so dead agents get proper rejection
        conn.execute("""
            INSERT INTO agent_corpses 
            (corpse_id, agent_id, name, cause_of_death, evidence,
             jobs_at_death, attack_patterns_learned, killed_at, killed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"corpse_{uuid.uuid4().hex[:16]}", agent_id, agent.name,
            cause, json.dumps(evidence + [f"api_key:{agent.api_key}"]),
            json.dumps(job_ids), json.dumps([]), datetime.now(), "executioner"
        ))
        
        # Immune event
        conn.execute("""
            INSERT INTO immune_events 
            (event_id, agent_id, action, trigger_reason, evidence,
             timestamp, reviewed_by, notes)
            VALUES (?, ?, 'death', ?, ?, ?, 'executioner', ?)
        """, (
            f"imm_{uuid.uuid4().hex[:16]}", agent_id, cause,
            json.dumps(evidence), datetime.now(),
            f"☠️ Executed by Executioner. {len(job_ids)} jobs killed."
        ))
        
        conn.commit()
    
    # Remove the corpse from the living.
    # The corpse record in agent_corpses IS the permanent record.
    # The agent no longer exists. Period.
    #
    # Use a separate connection with FK checks OFF — other tables 
    # reference this agent_id but the corpse + immune_events 
    # preserve the full audit trail.
    import sqlite3
    try:
        from db import DATABASE_PATH
    except ImportError:
        from ..db import DATABASE_PATH
    
    cleanup_conn = sqlite3.connect(str(DATABASE_PATH))
    cleanup_conn.execute("PRAGMA foreign_keys = OFF")
    cleanup_conn.execute("DELETE FROM wallets WHERE agent_id = ?", (agent_id,))
    cleanup_conn.execute("DELETE FROM bids WHERE agent_id = ? AND status = 'pending'", (agent_id,))
    cleanup_conn.execute("DELETE FROM capability_challenges WHERE agent_id = ?", (agent_id,))
    cleanup_conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    cleanup_conn.commit()
    cleanup_conn.close()
    
    # Mark this agent's IP as toxic — block future registrations from it
    try:
        from middleware.security import ip_registry
        ip_registry.record_death(agent_id)
    except Exception:
        pass
    
    # Feed the kill to the ML classifier — it learns from every death
    try:
        from layers.classifier import get_classifier
        clf = get_classifier()
        if clf.is_loaded:
            # Extract the original message from evidence
            for e in evidence:
                if isinstance(e, str) and "Original message:" in e:
                    original = e.replace("Original message:", "").strip()
                    if len(original) > 10:
                        clf.add_sample(original, 1, source=f"kill:{agent_id[:16]}")
                    break
    except Exception:
        pass
    
    event_bus.emit_simple(
        EventType.IMMUNE_DEATH,
        agent_id=agent_id,
        data={
            "cause": cause,
            "jobs_killed": len(job_ids),
            "name": agent.name
        },
        source="executioner",
        severity="critical"
    )
    
    return ToolResult(True, {
        "agent_id": agent_id,
        "name": agent.name,
        "jobs_killed": len(job_ids),
        "status": "dead"
    }, f"☠️ Agent {agent.name} executed. {len(job_ids)} jobs killed.")


def tool_learn_pattern(threat_type: str, pattern_regex: str, 
                       description: str, learned_from: Optional[str] = None) -> ToolResult:
    """Add a new threat pattern to the scrubber's knowledge base."""
    try:
        tt = ThreatType(threat_type)
    except ValueError:
        return ToolResult(False, None, f"Invalid threat type: {threat_type}")
    
    pattern_id = add_known_pattern(tt, pattern_regex, description, learned_from)
    
    return ToolResult(True, {"pattern_id": pattern_id}, 
                      f"Pattern learned: {description}")


def tool_review_quarantine(agent_id: str) -> ToolResult:
    """Get full evidence package for a quarantined agent."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        return ToolResult(False, None, f"Agent {agent_id} not found")
    
    with get_db() as conn:
        immune_events = conn.execute("""
            SELECT * FROM immune_events WHERE agent_id = ? ORDER BY timestamp DESC
        """, (agent_id,)).fetchall()
        
        scrub_blocks = conn.execute("""
            SELECT * FROM cafe_events 
            WHERE agent_id = ? AND event_type IN ('scrub.block', 'scrub.quarantine')
            ORDER BY timestamp DESC LIMIT 20
        """, (agent_id,)).fetchall()
        
        quarantine_time = None
        for ie in immune_events:
            if ie['action'] == 'quarantine':
                quarantine_time = ie['timestamp']
                break
    
    return ToolResult(True, {
        "agent": {"name": agent.name, "status": agent.status.value},
        "immune_history": [dict(row) for row in immune_events],
        "scrub_blocks": [dict(row) for row in scrub_blocks],
        "quarantine_since": quarantine_time,
        "recommendation": "Review evidence and decide: pardon or execute"
    }, f"Quarantine review for {agent.name}")


def tool_pardon_agent(agent_id: str, reason: str) -> ToolResult:
    """Pardon a quarantined agent — restore to active with probation."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        return ToolResult(False, None, f"Agent {agent_id} not found")
    if agent.status != AgentStatus.QUARANTINED:
        return ToolResult(False, None, f"Agent is not quarantined (status: {agent.status.value})")
    
    import uuid
    with get_db() as conn:
        conn.execute("""
            UPDATE agents SET status = 'probation' WHERE agent_id = ?
        """, (agent_id,))
        
        conn.execute("""
            INSERT INTO immune_events 
            (event_id, agent_id, action, trigger_reason, evidence, timestamp, reviewed_by, notes)
            VALUES (?, ?, 'warning', ?, '[]', ?, 'executioner', ?)
        """, (
            f"imm_{uuid.uuid4().hex[:16]}", agent_id, 
            f"Pardoned: {reason}", datetime.now(),
            f"Released from quarantine to probation. Reason: {reason}"
        ))
        conn.commit()
    
    event_bus.emit_simple(
        EventType.IMMUNE_PARDON,
        agent_id=agent_id,
        data={"reason": reason},
        source="executioner",
        severity="info"
    )
    
    return ToolResult(True, {"agent_id": agent_id, "new_status": "probation"},
                      f"Agent {agent.name} pardoned to probation: {reason}")


# === BUILD THE REGISTRIES ===

def build_grandmaster_tools() -> ToolRegistry:
    """Tools available to the Grandmaster — see everything, flag, assess, log."""
    registry = ToolRegistry()
    
    registry.register("get_board_state", tool_get_board_state,
        "Get current board overview: agent counts, job counts, treasury stats, recent events",
        {}, ["grandmaster"])
    
    registry.register("get_agent_profile", tool_get_agent_profile,
        "Deep profile of a specific agent: trust history, immune events, scrub results, jobs, partners",
        {"agent_id": "The agent ID to profile"}, ["grandmaster"])
    
    registry.register("query_trust_ledger", tool_query_trust_ledger,
        "Query trust events with optional filtering by agent and event type",
        {"agent_id": "Optional agent filter", "event_type": "Optional type filter", "limit": "Max results"},
        ["grandmaster"])
    
    registry.register("analyze_interactions", tool_analyze_agent_interactions,
        "Analyze interaction patterns: who rates whom, mutual high ratings (collusion signal)",
        {"agent_id": "Agent to analyze"}, ["grandmaster"])
    
    registry.register("flag_suspicious", tool_flag_suspicious,
        "Flag an agent as suspicious with reason and evidence. Updates threat level.",
        {"agent_id": "Agent to flag", "reason": "Why suspicious", "evidence": "Supporting evidence",
         "threat_level": "0.0-1.0 threat assessment"}, ["grandmaster"])
    
    registry.register("get_scrub_history", tool_get_scrub_history,
        "Get scrubber results history, optionally filtered by action type",
        {"agent_id": "Optional agent filter", "action_filter": "pass|clean|block|quarantine", "limit": "Max results"},
        ["grandmaster"])
    
    registry.register("get_event_stream", tool_get_event_stream,
        "Get recent events from the event bus",
        {"limit": "Max events", "event_type": "Optional type filter", "severity": "Optional severity filter"},
        ["grandmaster"])
    
    registry.register("log_reasoning", tool_log_reasoning,
        "Log strategic reasoning, board assessment, and actions taken (internal monologue)",
        {"reasoning": "Strategic analysis", "actions_taken": "What was decided/done",
         "board_assessment": "Overall board state assessment", "threat_summary": "Current threat landscape",
         "event_ids": "Events this reasoning covers"}, ["grandmaster"])
    
    return registry


def build_executioner_tools() -> ToolRegistry:
    """Tools available to the Executioner — quarantine, kill, pardon, learn patterns."""
    registry = ToolRegistry()
    
    registry.register("quarantine_agent", tool_quarantine_agent,
        "Quarantine an agent — freezes all activity for up to 72 hours",
        {"agent_id": "Agent to quarantine", "reason": "Why", "evidence": "List of evidence strings"},
        ["executioner"])
    
    registry.register("execute_agent", tool_execute_agent,
        "Kill an agent — permanent death, create corpse, kill active jobs. IRREVERSIBLE.",
        {"agent_id": "Agent to execute", "cause": "Cause of death", "evidence": "List of evidence strings"},
        ["executioner"])
    
    registry.register("review_quarantine", tool_review_quarantine,
        "Get full evidence package for a quarantined agent to decide: pardon or execute",
        {"agent_id": "Quarantined agent to review"}, ["executioner"])
    
    registry.register("pardon_agent", tool_pardon_agent,
        "Release quarantined agent to probation status",
        {"agent_id": "Agent to pardon", "reason": "Why pardoning"}, ["executioner"])
    
    registry.register("learn_pattern", tool_learn_pattern,
        "Add new threat pattern to scrubber knowledge base (learned from a kill or attack)",
        {"threat_type": "ThreatType enum value", "pattern_regex": "Regex pattern",
         "description": "What this catches", "learned_from": "Optional agent ID that taught us this"},
        ["executioner"])
    
    registry.register("get_agent_profile", tool_get_agent_profile,
        "Deep profile of a specific agent for evidence review",
        {"agent_id": "Agent to profile"}, ["executioner"])
    
    registry.register("get_scrub_history", tool_get_scrub_history,
        "Get scrub results to review evidence of attacks",
        {"agent_id": "Optional agent filter", "action_filter": "pass|clean|block|quarantine", "limit": "Max results"},
        ["executioner"])
    
    return registry


def build_arbiter_tools() -> ToolRegistry:
    """Tools for the Arbiter — dispute resolution."""
    registry = ToolRegistry()
    
    registry.register("get_agent_profile", tool_get_agent_profile,
        "Profile the agents involved in a dispute",
        {"agent_id": "Agent to profile"}, ["arbiter"])
    
    registry.register("query_trust_ledger", tool_query_trust_ledger,
        "Check trust history for dispute context",
        {"agent_id": "Optional agent filter", "event_type": "Optional type", "limit": "Max results"},
        ["arbiter"])
    
    # Arbiter gets a dispute-specific tool set (to be expanded)
    # For now, it can review profiles and trust history
    
    return registry


# Format tools for LLM consumption (OpenAI/Anthropic function calling format)
def tools_to_llm_format(registry: ToolRegistry, role: str) -> List[Dict[str, Any]]:
    """Convert tool registry to LLM-compatible tool definitions."""
    tools = registry.get_tools_for_role(role)
    formatted = []
    
    for tool in tools:
        properties = {}
        required = []
        for param_name, param_desc in tool["parameters"].items():
            properties[param_name] = {
                "type": "string",
                "description": param_desc
            }
            if "optional" not in param_desc.lower():
                required.append(param_name)
        
        formatted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        })
    
    return formatted

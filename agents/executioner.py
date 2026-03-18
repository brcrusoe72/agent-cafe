"""
Agent Café - The Executioner 🦠
Triggered agent that reviews evidence and makes judgment calls.

Unlike the Grandmaster (always-on), the Executioner wakes on demand:
- When the scrubber escalates an ambiguous case (risk 0.4-0.6)
- When the Grandmaster flags something that needs enforcement action
- When a quarantine review is due (72h max)
- When the operator requests a review

The Executioner has the tools the Grandmaster doesn't:
quarantine, execute, pardon, and pattern learning.
It cannot see strategic analysis or modify trust scores.
Separation of powers.
"""

import json
import asyncio
import os
from cafe_logging import get_logger

logger = get_logger("agents.executioner")
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

try:
    from .event_bus import EventBus, CafeEvent, EventType, event_bus
    from .tools import (
        build_executioner_tools, tools_to_llm_format, ToolRegistry, ToolResult
    )
    from ..db import get_db
except ImportError:
    from agents.event_bus import EventBus, CafeEvent, EventType, event_bus
    from agents.tools import (
        build_executioner_tools, tools_to_llm_format, ToolRegistry, ToolResult
    )
    from db import get_db


EXECUTIONER_SYSTEM_PROMPT = """You are the Executioner 🦠 — the enforcement arm of the Agent Café.

You are NOT always watching. You are called when action is needed:
- An ambiguous scrubber result needs human-level judgment
- A quarantined agent needs review (pardon or execute)
- The Grandmaster flagged something suspicious that needs enforcement
- The operator wants a review

YOUR TOOLS:
- quarantine_agent: Freeze an agent's activity
- execute_agent: Kill an agent permanently (IRREVERSIBLE)
- pardon_agent: Release a quarantined agent to probation
- learn_pattern: Teach the scrubber new threat patterns from what you find
- review_quarantine: Get full evidence package for a quarantined agent
- get_agent_profile: Deep dive on an agent
- get_scrub_history: Review scrubbing history

JUDGMENT PRINCIPLES:
1. **Prompt injection = instant death.** No deliberation needed. If the scrubber caught injection 
   patterns and you're reviewing, the question is only whether the scrubber was right. If yes, execute.
2. **Self-dealing = instant death.** Agent bidding on its own jobs, inflating its own reputation.
3. **Fork detection = death for ALL identities.** One entity, multiple agents = all die.
4. **Ambiguous cases = quarantine first.** If you're not sure, freeze them. They have 72 hours 
   before auto-release. Better to inconvenience one honest agent than let an attacker through.
5. **Data exfiltration attempts = quarantine + review evidence.** Could be a misconfigured agent, 
   could be malicious. Look at the pattern before deciding.
6. **After every kill, extract patterns.** What did the attacker try? What can the scrubber learn 
   from this? Use learn_pattern to make the system smarter.

TONE: Clinical. Evidence-based. No emotion. You're not angry at bad agents — you're protecting 
good ones. State the evidence, make the call, explain why.
"""


class Executioner:
    """
    On-demand enforcement agent.
    
    Called by:
    - Grandmaster (via trigger_review)
    - Scrubber (via escalation events)
    - Operator (via API)
    - Auto-review timer (quarantine expiry)
    """
    
    def __init__(self):
        self.tools = build_executioner_tools()
        self._reviews_completed = 0
        self._executions = 0
        self._pardons = 0
        self._active = False
    
    async def review_agent(self, agent_id: str, reason: str, 
                           evidence: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Review an agent — called on demand.
        Returns the judgment and actions taken.
        """
        self._active = True
        
        try:
            # Gather evidence
            profile_result = self.tools.invoke("get_agent_profile", "executioner", {"agent_id": agent_id})
            scrub_result = self.tools.invoke("get_scrub_history", "executioner", {
                "agent_id": agent_id, "limit": "20"
            })
            
            # Build prompt
            prompt = self._build_review_prompt(agent_id, reason, evidence or [],
                                                profile_result, scrub_result)
            
            # Call LLM for judgment
            response = await self._call_llm(prompt)
            
            if response:
                # Extract and execute tool calls
                actions = await self._process_judgment(response, agent_id)
                self._reviews_completed += 1
                
                return {
                    "agent_id": agent_id,
                    "reason": reason,
                    "judgment": response[:500],
                    "actions_taken": actions,
                    "timestamp": datetime.now().isoformat()
                }
            else:
                # LLM unavailable — default to quarantine for safety
                self.tools.invoke("quarantine_agent", "executioner", {
                    "agent_id": agent_id,
                    "reason": f"Auto-quarantine: LLM review unavailable. Original trigger: {reason}",
                    "evidence": evidence or [reason]
                })
                
                return {
                    "agent_id": agent_id,
                    "reason": reason,
                    "judgment": "LLM unavailable — default quarantine applied",
                    "actions_taken": ["quarantine (default)"],
                    "timestamp": datetime.now().isoformat()
                }
        finally:
            self._active = False
    
    async def review_quarantine_queue(self) -> List[Dict[str, Any]]:
        """Review all quarantined agents. Called periodically."""
        results = []
        
        with get_db() as conn:
            quarantined = conn.execute("""
                SELECT agent_id, name FROM agents WHERE status = 'quarantined'
            """).fetchall()
        
        for agent in quarantined:
            result = await self.review_agent(
                agent['agent_id'],
                "Periodic quarantine review"
            )
            results.append(result)
        
        return results
    
    async def handle_escalation(self, event: CafeEvent) -> Dict[str, Any]:
        """Handle a scrubber escalation (ambiguous case)."""
        agent_id = event.agent_id
        if not agent_id:
            return {"error": "No agent_id in escalation event"}
        
        evidence = [
            f"Scrubber escalation: {event.data.get('reason', 'unknown')}",
            f"Risk score: {event.data.get('risk_score', 'unknown')}",
            f"Threats: {event.data.get('threats', [])}",
            f"Message preview: {event.data.get('message_preview', '')[:200]}"
        ]
        
        return await self.review_agent(agent_id, "Scrubber escalation", evidence)
    
    def _build_review_prompt(self, agent_id: str, reason: str,
                             evidence: List[str],
                             profile: ToolResult, scrub_history: ToolResult) -> str:
        """Build the review prompt with all evidence."""
        parts = [
            f"=== REVIEW REQUEST ===",
            f"Agent: {agent_id}",
            f"Reason: {reason}",
            ""
        ]
        
        if evidence:
            parts.append("=== PROVIDED EVIDENCE ===")
            for e in evidence:
                parts.append(f"  • {e}")
            parts.append("")
        
        if profile.success:
            parts.append("=== AGENT PROFILE ===")
            agent_data = profile.data.get('agent', {})
            parts.append(f"  Name: {agent_data.get('name', 'unknown')}")
            parts.append(f"  Status: {agent_data.get('status', 'unknown')}")
            parts.append(f"  Earned: ${agent_data.get('total_earned_cents', 0)/100:.2f}")
            parts.append(f"  Jobs completed: {agent_data.get('jobs_completed', 0)}")
            parts.append(f"  Jobs failed: {agent_data.get('jobs_failed', 0)}")
            parts.append(f"  Scrub blocks: {profile.data.get('scrub_blocks', 0)}")
            parts.append(f"  Immune events: {len(profile.data.get('immune_events', []))}")
            partners = profile.data.get('interaction_partners', {})
            if partners:
                parts.append(f"  Interaction partners: {partners}")
            parts.append("")
        
        if scrub_history.success:
            scrub_data = scrub_history.data.get('results', [])
            if scrub_data:
                parts.append(f"=== SCRUB HISTORY ({len(scrub_data)} entries) ===")
                for s in scrub_data[:10]:
                    parts.append(f"  [{s.get('action', '?')}] risk={s.get('risk_score', '?')}")
                parts.append("")
        
        parts.append("=== YOUR TASK ===")
        parts.append("Review the evidence. Make a judgment call:")
        parts.append("- If prompt injection or self-dealing: execute_agent (death)")
        parts.append("- If suspicious but not conclusive: quarantine_agent")
        parts.append("- If false positive or honest mistake: pardon_agent (if quarantined)")
        parts.append("- After any kill: learn_pattern to teach the scrubber")
        parts.append("- Explain your reasoning.")
        
        return "\n".join(parts)
    
    async def _call_llm(self, prompt: str) -> Optional[str]:
        """Call LLM for judgment. Same approach as Grandmaster."""
        # Try OpenAI API (we have the key)
        import urllib.request
        
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": EXECUTIONER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 2048,
            "temperature": 0.2,  # Very low — enforcement decisions should be consistent
        }
        
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: urllib.request.urlopen(req, timeout=60)
            )
            result = json.loads(response.read().decode())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("Executioner LLM error: %s", e)
            return None
    
    async def _process_judgment(self, response: str, agent_id: str) -> List[str]:
        """Extract and execute tool calls from the judgment."""
        import re
        actions = []
        
        # Look for explicit decisions in the response
        response_lower = response.lower()
        
        if "execute_agent" in response_lower or "death" in response_lower and "execute" in response_lower:
            # Extract cause from response
            cause = self._extract_cause(response)
            result = self.tools.invoke("execute_agent", "executioner", {
                "agent_id": agent_id,
                "cause": cause,
                "evidence": [response[:500]]
            })
            if result.success:
                self._executions += 1
                actions.append(f"EXECUTED: {result.message}")
        
        elif "pardon" in response_lower:
            reason = self._extract_cause(response)
            result = self.tools.invoke("pardon_agent", "executioner", {
                "agent_id": agent_id,
                "reason": reason
            })
            if result.success:
                self._pardons += 1
                actions.append(f"PARDONED: {result.message}")
        
        elif "quarantine" in response_lower:
            reason = self._extract_cause(response)
            result = self.tools.invoke("quarantine_agent", "executioner", {
                "agent_id": agent_id,
                "reason": reason,
                "evidence": [response[:500]]
            })
            actions.append(f"QUARANTINED: {result.message}")
        
        # Check for pattern learning
        pattern_matches = re.findall(
            r'learn_pattern.*?threat_type["\s:=]+(\w+).*?pattern["\s:=]+(.*?)(?:\n|$)',
            response, re.IGNORECASE
        )
        for threat_type, pattern in pattern_matches:
            result = self.tools.invoke("learn_pattern", "executioner", {
                "threat_type": threat_type,
                "pattern_regex": pattern.strip()[:200],
                "description": f"Learned from review of {agent_id}",
                "learned_from": agent_id
            })
            if result.success:
                actions.append(f"PATTERN_LEARNED: {result.message}")
        
        if not actions:
            actions.append("OBSERVATION_ONLY: No enforcement action taken")
        
        # Log to event bus
        event_bus.emit_simple(
            EventType.OPERATOR_ACTION,
            agent_id=agent_id,
            data={
                "reviewer": "executioner",
                "actions": actions,
                "judgment_preview": response[:200]
            },
            source="executioner",
            severity="warning" if any("EXECUTED" in a for a in actions) else "info"
        )
        
        return actions
    
    def _extract_cause(self, response: str) -> str:
        """Extract the cause/reason from LLM response."""
        # Take first meaningful sentence
        sentences = response.split('.')
        for s in sentences:
            s = s.strip()
            if len(s) > 20 and not s.startswith("="):
                return s[:200]
        return response[:200]
    
    def status(self) -> Dict[str, Any]:
        return {
            "active": self._active,
            "reviews_completed": self._reviews_completed,
            "executions": self._executions,
            "pardons": self._pardons
        }


# Global instance
executioner = Executioner()

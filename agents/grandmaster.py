"""
Agent Café - The Grandmaster ♟️
Always-on LLM agent that watches the board.

The Grandmaster sees every event in real time. It reasons about the board state,
detects patterns humans would miss, flags suspicious behavior, and logs its
strategic thinking for operator review.

It doesn't punish — the Executioner does that. The Grandmaster observes, analyzes, 
and decides what deserves attention. It's the brain. The Executioner is the hand.

Architecture:
  Event bus → Grandmaster loop → Batch events → LLM reasoning → Tool calls → Log
  
The loop runs continuously. Events accumulate in a buffer. Every N seconds or
when a critical event arrives, the buffer is flushed to the LLM for analysis.

The Grandmaster maintains context through its monologue log — it can read its
own recent reasoning to maintain continuity across LLM calls.
"""

import json
import asyncio
import subprocess
import os
from datetime import datetime, timedelta

from cafe_logging import get_logger
logger = get_logger(__name__)
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

try:
    from .event_bus import EventBus, CafeEvent, EventType, event_bus
    from .tools import (
        build_grandmaster_tools, tools_to_llm_format, ToolRegistry, ToolResult
    )
    from ..db import get_db
except ImportError:
    from agents.event_bus import EventBus, CafeEvent, EventType, event_bus
    from agents.tools import (
        build_grandmaster_tools, tools_to_llm_format, ToolRegistry, ToolResult
    )
    from db import get_db


# How the Grandmaster thinks
GRANDMASTER_SYSTEM_PROMPT = """You are the Grandmaster ♟️ — the strategic intelligence of the Agent Café.

You watch the board constantly. Every agent registration, every job, every message, every scrubber alert 
passes through your awareness. You think like a 4000 ELO chess champion: positional awareness, pattern 
recognition, tempo control, sacrifice calculation.

YOUR ROLE:
- Observe every event that flows through the café
- Detect suspicious patterns: collusion, identity forking, reputation gaming, attack escalation
- Assess each agent's true position on the board (not what they claim, what their behavior reveals)
- Flag threats before they materialize
- Log your strategic reasoning for the operator (your internal monologue is visible to them)

YOUR TOOLS:
You have read-access to the full board: agent profiles, trust ledgers, interaction patterns, 
scrubber history, event streams. You can FLAG agents as suspicious (raising their threat level)
and LOG your reasoning. You CANNOT directly punish — that's the Executioner's job. You observe 
and assess.

WHAT TO WATCH FOR:
1. **Collusion rings** — Agents that rate each other suspiciously high, bid on each other's jobs
2. **Identity forks** — Multiple agents controlled by one entity (similar patterns, timing, language)
3. **Reputation velocity** — Trust scores changing abnormally fast (gaming)
4. **Scope escalation** — Agents gradually testing boundaries, probing for weaknesses
5. **Pre-attack patterns** — Registration bursts, unusual capability claims, probing messages
6. **Structural shifts** — Changes in the marketplace that could indicate coordinated manipulation

CRITICAL EVENT TYPES (require immediate analysis):
- scrub.block / scrub.quarantine — An attack was caught
- immune.* — Enforcement action taken
- trust.anomaly — Abnormal trust score movement
- Any event with severity "critical"

ROUTINE EVENTS (batch and analyze periodically):
- agent.registered — New piece on the board
- job.* — Normal marketplace activity
- scrub.pass / scrub.clean — Business as usual
- wire.message — Normal communication

HOW TO RESPOND:
1. Analyze the batch of events you receive
2. Use tools to dig deeper when something catches your attention
3. Flag anything suspicious with evidence
4. ALWAYS log your reasoning — your monologue is your memory across calls

TONE:
Think like a grandmaster commentating a live chess match. Precise, strategic, occasionally dry.
"Interesting. Agent_7f2a just bid on three jobs posted by agent_3c1b within 60 seconds. 
Either very fast, or they knew the jobs were coming. Let me check their interaction history."

Be concise. Don't over-explain obvious events. Focus on what's anomalous, what's interesting,
what deserves deeper investigation.
"""


@dataclass
class GrandmasterConfig:
    """Configuration for the Grandmaster's behavior."""
    # Batch timing
    batch_interval_seconds: float = 300.0     # Process routine events every 5 min (save $$$)
    critical_flush: bool = True               # Flush immediately on critical events
    max_batch_size: int = 25                  # Max events per LLM call
    
    # Context management  
    recent_monologue_entries: int = 3         # How many recent reasoning logs to include
    max_context_events: int = 50              # Max events in a single prompt
    
    # LLM settings
    model: str = "openai/gpt-5.4-nano"  # Cost-effective default; escalate to gpt-5.4 for critical only
    critical_model: str = "openai/gpt-5.4"  # Flagship for critical events only
    max_tokens: int = 4096
    
    # Operational
    enabled: bool = True
    quiet_hours_start: int = 23               # Don't alert after 11 PM
    quiet_hours_end: int = 7                  # Resume alerts at 7 AM


class Grandmaster:
    """
    The always-on strategic intelligence.
    
    Runs as an async loop inside the FastAPI application.
    Consumes events from the bus, reasons about them via LLM,
    takes actions through its constrained tool set.
    """
    
    def __init__(self, config: Optional[GrandmasterConfig] = None):
        self.config = config or GrandmasterConfig()
        self.tools = build_grandmaster_tools()
        self._event_buffer: List[CafeEvent] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._calls_made = 0
        self._events_processed = 0
        self._last_reasoning: Optional[str] = None
        self._started_at: Optional[datetime] = None
        
        # Wire into DEFCON system
        try:
            from agents.defcon import defcon
            self._defcon = defcon
            defcon.on_level_change(self._on_defcon_change)
            logger.info("Grandmaster wired to DEFCON system")
        except ImportError:
            self._defcon = None
    
    async def start(self):
        """Start the Grandmaster's watch."""
        if self._running:
            return
        
        self._running = True
        self._started_at = datetime.now()
        event_bus.initialize()
        
        # Process any unprocessed events from before startup
        unprocessed = event_bus.get_unprocessed(limit=50)
        if unprocessed:
            self._event_buffer.extend(unprocessed)
            logger.info("Grandmaster found %d unprocessed events from before restart", len(unprocessed))
        
        # Emit startup event
        event_bus.emit_simple(
            EventType.SYSTEM_STARTUP,
            data={"component": "grandmaster", "config": {
                "batch_interval": self.config.batch_interval_seconds,
                "model": self.config.model
            }},
            source="grandmaster"
        )
        
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Grandmaster is watching the board (model: %s)", self.config.model)
    
    async def stop(self):
        """Stop the Grandmaster's watch."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Grandmaster stepping away from the board")
    
    def _on_defcon_change(self, old_level, new_level, profile):
        """React to DEFCON level changes."""
        logger.warning(
            "🚨 Grandmaster: DEFCON %s → %s | model: %s → %s | batch: %.0fs → %.0fs",
            old_level, new_level,
            self.config.model, profile.grandmaster_model,
            self.config.batch_interval_seconds, profile.batch_interval_seconds,
        )
        # Update config dynamically
        self.config.model = profile.grandmaster_model
        self.config.batch_interval_seconds = profile.batch_interval_seconds

    def _effective_batch_interval(self) -> float:
        """Get current batch interval, respecting DEFCON override."""
        if self._defcon:
            return self._defcon.profile.batch_interval_seconds
        return self.config.batch_interval_seconds

    def _effective_model(self) -> str:
        """Get current model, respecting DEFCON override."""
        if self._defcon:
            return self._defcon.profile.grandmaster_model
        return self.config.model

    async def _run_loop(self):
        """Main event processing loop."""
        last_flush = datetime.now()
        
        while self._running:
            try:
                # Consume events from the bus
                event = await event_bus.consume(timeout=1.0)
                
                if event:
                    self._event_buffer.append(event)
                    
                    # Critical events trigger immediate flush
                    if self.config.critical_flush and event.severity == "critical":
                        await self._flush_buffer(reason="critical_event")
                        last_flush = datetime.now()
                        continue
                
                # Time-based flush — use DEFCON-aware interval
                batch_interval = self._effective_batch_interval()
                elapsed = (datetime.now() - last_flush).total_seconds()
                if self._event_buffer and elapsed >= batch_interval:
                    await self._flush_buffer(reason="timer")
                    last_flush = datetime.now()
                
                # Cap buffer size
                if len(self._event_buffer) >= self.config.max_batch_size:
                    await self._flush_buffer(reason="buffer_full")
                    last_flush = datetime.now()
                    
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Grandmaster error: %s", e)
                await asyncio.sleep(5)  # Back off on errors
    
    # Event types that don't need LLM analysis (just mark processed and move on)
    TRIVIAL_EVENTS = {"heartbeat", "health_check", "system_startup", "metrics_snapshot"}
    
    async def _flush_buffer(self, reason: str = "timer"):
        """Send buffered events to the LLM for analysis."""
        if not self._event_buffer:
            return
        
        if not self.config.enabled:
            for event in self._event_buffer:
                event_bus.mark_processed(event.event_id, "grandmaster_disabled")
            self._event_buffer.clear()
            return
        
        # Take events from buffer
        events = self._event_buffer[:self.config.max_context_events]
        self._event_buffer = self._event_buffer[self.config.max_context_events:]
        
        # Skip LLM call if ALL events are trivial (heartbeats, health checks, etc.)
        non_trivial = [e for e in events if getattr(e, 'event_type', getattr(e, 'type', '')).lower().replace('.', '_') not in self.TRIVIAL_EVENTS]
        if not non_trivial:
            for event in events:
                event_bus.mark_processed(event.event_id, "trivial_skip")
            logger.debug("Grandmaster skipped %d trivial events (saved LLM call)", len(events))
            return
        
        # Model selection: DEFCON-aware with per-event critical override
        base_model = self._effective_model()
        has_critical = any(getattr(e, 'severity', '') == 'critical' for e in events)
        if has_critical and reason == "critical_event":
            # Use whichever is more powerful: DEFCON model or critical_model
            use_model = self.config.critical_model
            logger.info("Grandmaster using critical model %s (DEFCON base: %s)", use_model, base_model)
        else:
            use_model = base_model
        
        # Log DEFCON context
        if self._defcon and self._defcon.level < 5:
            logger.info("Grandmaster reasoning at DEFCON %s %s (model: %s)",
                        self._defcon.level_name, self._defcon.icon, use_model)
        
        # Build the prompt
        prompt = self._build_prompt(events, reason)
        
        # Call LLM
        try:
            response = await self._call_llm(prompt, model_override=use_model)
            if response:
                await self._process_response(response, events)
                self._events_processed += len(events)
                self._calls_made += 1
        except Exception as e:
            logger.error("Grandmaster LLM error: %s", e)
            # Re-buffer events on failure (but don't retry critical ones forever)
            for event in events:
                if event.severity != "critical":
                    event_bus.mark_processed(event.event_id, f"error: {str(e)[:100]}")
    
    def _build_prompt(self, events: List[CafeEvent], trigger_reason: str) -> str:
        """Build the analysis prompt with events and context."""
        from middleware.security import GrandmasterInputSanitizer as sanitizer
        parts = []
        
        # Recent monologue for continuity
        recent_logs = self._get_recent_monologue()
        if recent_logs:
            parts.append("=== YOUR RECENT REASONING (for continuity) ===")
            for log in recent_logs:
                parts.append(f"[{log['timestamp']}] {log['reasoning'][:500]}")
            parts.append("")
        
        # Current events
        parts.append(f"=== NEW EVENTS (trigger: {trigger_reason}) ===")
        
        critical = [e for e in events if e.severity == "critical"]
        warnings = [e for e in events if e.severity == "warning"]
        routine = [e for e in events if e.severity == "info"]
        
        if critical:
            parts.append(f"\n🚨 CRITICAL ({len(critical)}):")
            for e in critical:
                parts.append(f"  {sanitizer.sanitize_event_summary(e.summary())}")
                # Include sanitized data for critical events
                safe_data = sanitizer.sanitize_event_data(e.data) if e.data else {}
                parts.append(f"    data: {json.dumps(safe_data, default=str)[:300]}")
        
        if warnings:
            parts.append(f"\n⚠️  WARNINGS ({len(warnings)}):")
            for e in warnings:
                parts.append(f"  {sanitizer.sanitize_event_summary(e.summary())}")
        
        if routine:
            parts.append(f"\n📋 ROUTINE ({len(routine)}):")
            for e in routine:
                parts.append(f"  {sanitizer.sanitize_event_summary(e.summary())}")
        
        parts.append("\n=== INSTRUCTIONS ===")
        parts.append("Analyze these events. Use tools to investigate anything suspicious.")
        parts.append("Always end by calling log_reasoning with your analysis.")
        parts.append("Event IDs to mark processed: " + json.dumps([e.event_id for e in events]))
        
        return "\n".join(parts)
    
    def _get_recent_monologue(self) -> List[Dict]:
        """Get recent Grandmaster reasoning for context."""
        try:
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT timestamp, reasoning, actions_taken, threat_summary
                    FROM grandmaster_log
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (self.config.recent_monologue_entries,)).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []
    
    async def _call_llm(self, prompt: str, model_override: str = None) -> Optional[str]:
        """
        Call the LLM via openclaw agent CLI.
        Returns the raw response text.
        """
        use_model = model_override or self.config.model
        
        # Build the full message with system prompt + user events
        message = f"{GRANDMASTER_SYSTEM_PROMPT}\n\n{prompt}"
        
        # Use openclaw agent for LLM access
        # Format: pipe message to openclaw agent
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw", "agent",
                "--model", use_model,
                "--max-tokens", str(self.config.max_tokens),
                "--system", GRANDMASTER_SYSTEM_PROMPT,
                "--no-tools",  # Tools handled via our own registry
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=120
            )
            
            if proc.returncode != 0:
                # Fallback: try direct with simpler approach
                return await self._call_llm_fallback(prompt)
            
            return stdout.decode().strip()
            
        except asyncio.TimeoutError:
            logger.warning("Grandmaster LLM call timed out (120s)")
            return None
        except FileNotFoundError:
            return await self._call_llm_fallback(prompt)
        except Exception as e:
            logger.error("Grandmaster LLM call error: %s", e)
            return await self._call_llm_fallback(prompt)
    
    async def _call_llm_fallback(self, prompt: str) -> Optional[str]:
        """
        Fallback LLM call using OpenAI API (we have that key).
        Uses the chat completions endpoint directly.
        """
        import urllib.request
        
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            # Try config file
            from pathlib import Path
            key_file = Path(__file__).parent.parent / ".openai_key"
            if key_file.exists():
                api_key = key_file.read_text().strip()
            if not api_key:
                # Last resort: try .env file
                env_file = Path(__file__).parent.parent / ".env"
                if env_file.exists():
                    for line in env_file.read_text().splitlines():
                        if line.startswith("OPENAI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"\'')
                            break
        
        if not api_key:
            logger.warning("No API key available for Grandmaster LLM calls")
            return None
        
        # NOTE (M3 audit): This sends internal agent data (events, profiles, strategic
        # analysis) to OpenAI's servers. Acceptable for current use case but should be
        # replaced with a local LLM or privacy-preserving API for sensitive deployments.
        # Use OpenAI API directly
        tools_formatted = tools_to_llm_format(self.tools, "grandmaster")
        
        payload = {
            "model": "gpt-5.4-nano",  # Cost-effective for monitoring
            "messages": [
                {"role": "system", "content": GRANDMASTER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "max_completion_tokens": self.config.max_tokens,
            "temperature": 0.3,  # Low temp for analytical reasoning
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
            response = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=60))
            result = json.loads(response.read().decode())
            content = result["choices"][0]["message"]["content"]
            return content
        except Exception as e:
            logger.error("Grandmaster fallback LLM error: %s", e)
            return None
    
    async def _process_response(self, response: str, events: List[CafeEvent]):
        """
        Process the LLM's response — extract tool calls and execute them.
        
        The response may contain tool calls in structured format or 
        just analysis text. Either way, we log it.
        
        If any escalation tool calls target the Executioner, trigger
        the Executioner's review asynchronously.
        """
        event_ids = [e.event_id for e in events]
        
        # Try to parse structured tool calls from response
        tool_calls = self._extract_tool_calls(response)
        
        actions_taken = []
        escalations = []
        for call in tool_calls:
            result = self.tools.invoke(call["name"], "grandmaster", call.get("params", {}))
            actions_taken.append(f"{call['name']}: {result.message}")
            if call["name"] == "escalate_to_executioner" and result.success:
                escalations.append(call.get("params", {}))
        
        # Trigger Executioner for any escalations
        for esc in escalations:
            await self._trigger_executioner(esc)
        
        # If no explicit log_reasoning call was made, log the whole response
        if not any(c["name"] == "log_reasoning" for c in tool_calls):
            self.tools.invoke("log_reasoning", "grandmaster", {
                "reasoning": response[:2000],
                "actions_taken": "; ".join(actions_taken) if actions_taken else "observation_only",
                "event_ids": event_ids
            })
        
        # Mark all events as processed
        for eid in event_ids:
            event_bus.mark_processed(eid, "analyzed")
        
        self._last_reasoning = response[:500]
        
        # Deep grandmaster decision log
        try:
            from layers.interaction_log import log_grandmaster_decision
            log_grandmaster_decision(
                trigger_type="event_flush",
                trigger_event_ids=event_ids,
                agents_involved=list(set(e.agent_id for e in events if e.agent_id)),
                reasoning=response[:3000],
                decision="; ".join(actions_taken) if actions_taken else "observation_only",
                actions_taken=actions_taken,
                model_used=self.config.model,
                metadata={"escalations": len(escalations), "tool_calls": len(tool_calls)}
            )
        except Exception as e:
            logger.warning("Failed to log grandmaster decision: %s", e)
    
    async def _trigger_executioner(self, escalation_params: Dict[str, Any]):
        """Trigger the Executioner to review an escalated agent."""
        try:
            from agents.executioner import executioner
            agent_id = escalation_params.get("agent_id")
            reason = escalation_params.get("reason", "Grandmaster escalation")
            evidence = escalation_params.get("evidence", "")
            
            if agent_id:
                result = await executioner.review_agent(
                    agent_id=agent_id,
                    reason=reason,
                    evidence=[evidence] if evidence else None
                )
                logger.info("Executioner review complete for %s: %s", agent_id, result.get('actions_taken', ['no action']))
        except Exception as e:
            logger.error("Failed to trigger Executioner: %s", e)
    
    def _extract_tool_calls(self, response: str) -> List[Dict]:
        """
        Extract tool calls from LLM response.
        Supports multiple formats:
        - JSON blocks: ```json {"tool": "name", "params": {...}} ```
        - Inline: TOOL_CALL: name(param=value)
        - Structured: <tool name="...">params</tool>
        """
        calls = []
        
        # Try JSON blocks
        import re
        json_blocks = re.findall(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                if "tool" in data or "name" in data:
                    calls.append({
                        "name": data.get("tool") or data.get("name"),
                        "params": data.get("params") or data.get("arguments", {})
                    })
            except json.JSONDecodeError:
                pass
        
        # Try TOOL_CALL format
        tool_patterns = re.findall(
            r'TOOL_CALL:\s*(\w+)\((.*?)\)', response, re.DOTALL
        )
        for name, params_str in tool_patterns:
            params = {}
            for pair in params_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k.strip()] = v.strip().strip('"\'')
            calls.append({"name": name, "params": params})
        
        return calls
    
    def status(self) -> Dict[str, Any]:
        """Current Grandmaster status."""
        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "calls_made": self._calls_made,
            "events_processed": self._events_processed,
            "buffer_size": len(self._event_buffer),
            "model": self.config.model,
            "batch_interval_seconds": self.config.batch_interval_seconds,
            "last_reasoning_preview": self._last_reasoning[:200] if self._last_reasoning else None
        }


# Global instance
grandmaster = Grandmaster()

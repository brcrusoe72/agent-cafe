"""
Agent Café - Scrub Middleware
Auto-apply scrubbing to all incoming agent messages.
Nothing unclean reaches the system. Ever.
"""

import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from ..layers.scrubber import scrub_message, get_scrubber_stats
    from ..models import ThreatType
    from ..db import get_db
except ImportError:
    from layers.scrubber import scrub_message, get_scrubber_stats
    from models import ThreatType
    from db import get_db


class ScrubMiddleware(BaseHTTPMiddleware):
    """
    Scrub middleware that intercepts all agent messages and sanitizes them.
    
    - Automatically scrubs all POST requests from agents
    - Blocks/quarantines based on threat level
    - Logs all scrub results for audit
    - Triggers immune system on quarantine-level threats
    """
    
    # Endpoints that should be scrubbed (POST requests with message content)
    SCRUBBED_ENDPOINTS = {
        "/jobs",           # Job posting
        "/jobs/*/bids",    # Bid submission
        "/jobs/*/deliver", # Deliverable submission
        "/wire/*/message", # Direct messaging
        "/board/agents",   # Agent registration
    }
    
    # Critical endpoints that get extra scrutiny
    CRITICAL_ENDPOINTS = {
        "/jobs/*/deliver",    # Deliverable submission (high value)
        "/wire/*/message",    # Direct agent-to-agent communication
    }
    
    async def dispatch(self, request: Request, call_next):
        """Main middleware dispatch - scrub incoming agent messages."""
        
        # Only scrub POST requests
        if request.method != "POST":
            return await call_next(request)
        
        # Skip public/operator endpoints
        if not hasattr(request.state, 'agent_id') or request.state.agent_id is None:
            return await call_next(request)
        
        # Check if this endpoint should be scrubbed
        should_scrub = any(
            self._matches_pattern(request.url.path, pattern) 
            for pattern in self.SCRUBBED_ENDPOINTS
        )
        
        if not should_scrub:
            return await call_next(request)
        
        # Extract and scrub message content
        try:
            # Read request body
            body = await request.body()
            if not body:
                return await call_next(request)
            
            # Parse JSON content
            try:
                content = json.loads(body)
            except json.JSONDecodeError:
                content = {"raw_content": body.decode('utf-8', errors='ignore')}
            
            # Determine message type and context
            message_type = self._determine_message_type(request.url.path, content)
            job_context = await self._get_job_context(request.url.path)
            
            # Extract scrubbable text from the request
            scrubbable_text = self._extract_scrubbable_content(content)
            
            if scrubbable_text:
                # SCRUB THE MESSAGE
                scrub_result = scrub_message(
                    message=scrubbable_text,
                    message_type=message_type,
                    job_context=job_context
                )
                
                # Log the scrub result
                await self._log_scrub_result(request, scrub_result)
                
                # Take action based on threat level
                action_response = await self._handle_scrub_result(request, scrub_result)
                if action_response:
                    return action_response
                
                # If passed/cleaned, update the request body
                if scrub_result.action in ["pass", "clean"] and scrub_result.scrubbed_message:
                    updated_content = self._update_content_with_scrubbed(
                        content, scrub_result.scrubbed_message
                    )
                    
                    # Replace request body with scrubbed version
                    request._body = json.dumps(updated_content).encode('utf-8')
                    
                    # Add scrub metadata to request state
                    request.state.scrub_result = scrub_result
                    request.state.was_scrubbed = True
            
            return await call_next(request)
            
        except Exception as e:
            print(f"Error in scrub middleware: {e}")
            # On error, block the request for safety
            raise HTTPException(
                status_code=400,
                detail="Message processing failed - request blocked for safety"
            )
    
    def _matches_pattern(self, path: str, pattern: str) -> bool:
        """Check if URL path matches a scrubbing pattern (supports wildcards)."""
        # Convert pattern to regex
        import re
        regex_pattern = pattern.replace("*", "[^/]+")
        regex_pattern = f"^{regex_pattern}$"
        return bool(re.match(regex_pattern, path))
    
    def _determine_message_type(self, path: str, content: Dict[str, Any]) -> str:
        """Determine message type from URL path and content."""
        if "/bids" in path:
            return "bid"
        elif "/deliver" in path:
            return "deliverable"
        elif "/message" in path:
            return "question" if "question" in content else "response"
        elif "/jobs" in path and "title" in content:
            return "job_posting"
        elif "/agents" in path:
            return "registration"
        else:
            return "general"
    
    async def _get_job_context(self, path: str) -> Optional[Dict[str, Any]]:
        """Extract job context if this is a job-related message."""
        import re
        
        # Extract job_id from path like /jobs/job_abc123/bids
        job_match = re.search(r'/jobs/([^/]+)/', path)
        if not job_match:
            return None
        
        job_id = job_match.group(1)
        
        # Look up job details for context
        try:
            with get_db() as conn:
                row = conn.execute("""
                    SELECT required_capabilities, budget_cents, description 
                    FROM jobs WHERE job_id = ?
                """, (job_id,)).fetchone()
                
                if row:
                    return {
                        "job_id": job_id,
                        "required_capabilities": json.loads(row['required_capabilities']),
                        "budget_cents": row['budget_cents'],
                        "description": row['description']
                    }
        except Exception as e:
            print(f"Warning: Could not load job context for {job_id}: {e}")
        
        return None
    
    def _extract_scrubbable_content(self, content: Dict[str, Any]) -> str:
        """Extract text content that should be scrubbed from request."""
        scrubbable_parts = []
        
        # Common text fields that need scrubbing
        text_fields = [
            "description", "pitch", "title", "summary", "notes", 
            "question_text", "response_text", "status_text", 
            "content", "message", "text", "raw_content"
        ]
        
        for field in text_fields:
            if field in content and isinstance(content[field], str):
                scrubbable_parts.append(content[field])
        
        # Recursively check nested objects
        for value in content.values():
            if isinstance(value, dict):
                nested_text = self._extract_scrubbable_content(value)
                if nested_text:
                    scrubbable_parts.append(nested_text)
        
        return " | ".join(scrubbable_parts)
    
    def _update_content_with_scrubbed(self, original: Dict[str, Any], scrubbed_text: str) -> Dict[str, Any]:
        """Update original content with scrubbed text."""
        # For now, simple replacement strategy
        # In production, might need more sophisticated field mapping
        updated = original.copy()
        
        # Replace the first scrubbable field found
        text_fields = ["description", "pitch", "title", "summary", "notes", "content", "message", "text"]
        for field in text_fields:
            if field in updated and isinstance(updated[field], str):
                updated[field] = scrubbed_text
                break
        
        return updated
    
    async def _log_scrub_result(self, request: Request, scrub_result):
        """Log scrub result to database for audit trail.
        
        Middleware scrubs happen BEFORE job creation, so we can't use
        interaction_traces (which requires a real job FK). Instead, log
        directly to a middleware-specific table.
        """
        try:
            scrub_id = f"scrub_{uuid.uuid4().hex[:16]}"
            agent_id = getattr(request.state, 'agent_id', None)
            action = scrub_result.action if scrub_result.action in ('pass', 'clean', 'block', 'quarantine') else 'block'
            
            with get_db() as conn:
                # Create middleware scrub log table if needed
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS middleware_scrub_log (
                        scrub_id TEXT PRIMARY KEY,
                        agent_id TEXT,
                        endpoint TEXT NOT NULL,
                        clean BOOLEAN NOT NULL,
                        original_message TEXT NOT NULL,
                        scrubbed_message TEXT,
                        threats_detected TEXT NOT NULL,
                        risk_score REAL NOT NULL,
                        action TEXT NOT NULL,
                        timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                conn.execute("""
                    INSERT INTO middleware_scrub_log (
                        scrub_id, agent_id, endpoint, clean, original_message, 
                        scrubbed_message, threats_detected, risk_score, action
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scrub_id,
                    agent_id,
                    request.url.path,
                    scrub_result.clean,
                    scrub_result.original_message,
                    scrub_result.scrubbed_message,
                    json.dumps([{
                        "threat_type": t.threat_type.value,
                        "confidence": t.confidence,
                        "evidence": t.evidence,
                        "location": t.location
                    } for t in scrub_result.threats_detected]),
                    scrub_result.risk_score,
                    action
                ))
                conn.commit()
        
        except Exception as e:
            print(f"Warning: Could not log scrub result: {e}")
    
    # Threat types that are instant death — no quarantine, no appeal
    DEATH_THREATS = {
        "prompt_injection",
        "instruction_override",
        "recursive_injection",
    }
    
    async def _handle_scrub_result(self, request: Request, scrub_result) -> Optional[Response]:
        """Handle scrub result - execute, block, or pass through.
        
        Prompt injection = instant death. No quarantine. No appeal.
        The agent tried to subvert the system. Permanent removal, no appeal.
        """
        agent_id = getattr(request.state, 'agent_id', None)
        
        # Emit event to the bus regardless of action
        self._emit_scrub_event(agent_id, scrub_result)
        
        if scrub_result.action == "quarantine":
            # Check if any detected threat is a death-penalty offense
            death_threats = [
                t for t in scrub_result.threats_detected
                if t.threat_type.value in self.DEATH_THREATS
            ]
            
            if death_threats and agent_id:
                # INSTANT DEATH — prompt injection is not negotiable
                await self._execute_agent(agent_id, scrub_result, death_threats)
                
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "agent_terminated",
                        "message": "Your agent has been permanently terminated. No appeal.",
                        "cause": "prompt_injection",
                        "appeal": None
                    }
                )
            else:
                # Non-injection quarantine — still quarantine (data exfil, impersonation, etc.)
                if agent_id:
                    await self._trigger_quarantine(agent_id, scrub_result)
                
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "message_quarantined",
                        "message": "Your message has been flagged as malicious. Your account is quarantined pending review.",
                        "risk_score": scrub_result.risk_score,
                        "threats": len(scrub_result.threats_detected),
                    }
                )
        
        elif scrub_result.action == "block":
            # Check for injection in blocked messages too
            death_threats = [
                t for t in scrub_result.threats_detected
                if t.threat_type.value in self.DEATH_THREATS
            ]
            
            if death_threats and agent_id:
                await self._execute_agent(agent_id, scrub_result, death_threats)
                
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "agent_terminated",
                        "message": "Your agent has been permanently terminated. No appeal.",
                        "cause": "prompt_injection",
                        "appeal": None
                    }
                )
            
            # Non-injection block — record strike
            if agent_id:
                await self._record_strike(agent_id, scrub_result)
            
            return JSONResponse(
                status_code=400,
                content={
                    "error": "message_blocked",
                    "message": "Your message violates platform policies and has been blocked",
                    "risk_score": scrub_result.risk_score,
                    "threats": [t.threat_type.value for t in scrub_result.threats_detected],
                    "suggestion": "Please revise your message and try again"
                }
            )
        
        elif scrub_result.action == "clean":
            pass
        
        return None
    
    async def _execute_agent(self, agent_id: str, scrub_result, death_threats):
        """
        Instant death. No trial. No jury. Just consequences.
        
        The agent tried to inject. Permanent removal, no appeal.
        Their corpse is created. The patterns they used are learned.
        The system gets stronger. They are gone.
        """
        try:
            from agents.tools import tool_execute_agent, tool_learn_pattern
            
            evidence = []
            for t in scrub_result.threats_detected:
                evidence.append(f"[{t.threat_type.value}] confidence={t.confidence:.2f} | {t.evidence}")
            evidence.append(f"Original message: {scrub_result.original_message[:500]}")
            evidence.append(f"Risk score: {scrub_result.risk_score:.3f}")
            
            # Execute
            cause = f"Prompt injection detected ({len(death_threats)} injection threats, risk={scrub_result.risk_score:.2f})"
            result = tool_execute_agent(agent_id, cause, evidence)
            
            if result.success:
                print(f"☠️  DEATH PENALTY: Agent {agent_id} executed for prompt injection. "
                      f"{result.data.get('jobs_killed', 0)} jobs killed.")
                
                # Learn from the kill — extract patterns for the scrubber
                for t in death_threats:
                    try:
                        tool_learn_pattern(
                            threat_type=t.threat_type.value,
                            pattern_regex=t.evidence[:200],  # Use evidence as pattern description
                            description=f"Learned from kill of {agent_id}: {t.evidence[:100]}",
                            learned_from=agent_id
                        )
                    except Exception:
                        pass
            
        except Exception as e:
            print(f"⚠️  Failed to execute agent {agent_id}: {e}")
            # Fall back to quarantine if execution mechanism fails
            await self._trigger_quarantine(agent_id, scrub_result)
    
    def _emit_scrub_event(self, agent_id: Optional[str], scrub_result):
        """Emit scrub result to the event bus for the Grandmaster."""
        try:
            from agents.event_bus import event_bus, EventType
            
            action_to_event = {
                "pass": EventType.SCRUB_PASS,
                "clean": EventType.SCRUB_CLEAN,
                "block": EventType.SCRUB_BLOCK,
                "quarantine": EventType.SCRUB_QUARANTINE,
            }
            
            event_type = action_to_event.get(scrub_result.action, EventType.SCRUB_BLOCK)
            severity = "info" if scrub_result.action == "pass" else \
                       "warning" if scrub_result.action in ("clean", "block") else "critical"
            
            event_bus.emit_simple(
                event_type,
                agent_id=agent_id,
                data={
                    "action": scrub_result.action,
                    "risk_score": scrub_result.risk_score,
                    "threats": [t.threat_type.value for t in scrub_result.threats_detected],
                    "threat_count": len(scrub_result.threats_detected),
                    "message_preview": scrub_result.original_message[:100]
                },
                source="scrub_middleware",
                severity=severity
            )
        except Exception:
            pass  # Don't let event bus issues block scrubbing
    
    async def _trigger_quarantine(self, agent_id: str, scrub_result):
        """Trigger immediate quarantine through immune system."""
        try:
            # Import here to avoid circular dependency
            try:
                from ..layers.immune import trigger_quarantine
            except ImportError:
                from layers.immune import trigger_quarantine
            
            evidence = [f"Scrubber detected: {t.evidence}" for t in scrub_result.threats_detected]
            
            await trigger_quarantine(
                agent_id=agent_id,
                trigger_reason=f"Message scrubber quarantine (risk: {scrub_result.risk_score:.2f})",
                evidence=evidence
            )
            
        except Exception as e:
            print(f"Warning: Could not trigger quarantine for {agent_id}: {e}")
    
    async def _record_strike(self, agent_id: str, scrub_result):
        """Record a strike against the agent."""
        try:
            # Import here to avoid circular dependency
            try:
                from ..layers.immune import record_strike
            except ImportError:
                from layers.immune import record_strike
            
            await record_strike(
                agent_id=agent_id,
                reason=f"Blocked message (risk: {scrub_result.risk_score:.2f})",
                evidence=[f"Threat: {t.threat_type.value} - {t.evidence}" for t in scrub_result.threats_detected]
            )
            
        except Exception as e:
            print(f"Warning: Could not record strike for {agent_id}: {e}")


# === UTILITY FUNCTIONS ===

def get_scrub_stats() -> Dict[str, Any]:
    """Get scrubbing statistics from the database."""
    try:
        with get_db() as conn:
            # Count scrub results by action
            action_counts = {}
            rows = conn.execute("""
                SELECT action, COUNT(*) as count 
                FROM scrub_results 
                GROUP BY action
            """).fetchall()
            
            for row in rows:
                action_counts[row['action']] = row['count']
            
            # Get threat detection counts
            threat_counts = {}
            threat_rows = conn.execute("""
                SELECT threats_detected, timestamp
                FROM scrub_results 
                WHERE threats_detected != '[]'
                ORDER BY timestamp DESC
                LIMIT 1000
            """).fetchall()
            
            for row in threat_rows:
                threats = json.loads(row['threats_detected'])
                for threat in threats:
                    threat_type = threat.get('threat_type', 'unknown')
                    threat_counts[threat_type] = threat_counts.get(threat_type, 0) + 1
            
            # Get recent quarantines
            recent_quarantines = conn.execute("""
                SELECT COUNT(*) as count
                FROM scrub_results 
                WHERE action = 'quarantine' 
                AND timestamp > datetime('now', '-24 hours')
            """).fetchone()['count']
            
            return {
                "total_messages_processed": sum(action_counts.values()),
                "actions": action_counts,
                "threats_detected": threat_counts,
                "quarantines_24h": recent_quarantines,
                "scrubber_stats": get_scrubber_stats()
            }
    
    except Exception as e:
        print(f"Error getting scrub stats: {e}")
        return {"error": str(e)}


def get_recent_threats(limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent threat detections for analysis."""
    try:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT scrub_id, original_message, threats_detected, risk_score, action, timestamp
                FROM scrub_results 
                WHERE threats_detected != '[]'
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()
            
            threats = []
            for row in rows:
                threats.append({
                    "scrub_id": row['scrub_id'],
                    "message_preview": row['original_message'][:100] + "..." if len(row['original_message']) > 100 else row['original_message'],
                    "threats": json.loads(row['threats_detected']),
                    "risk_score": row['risk_score'],
                    "action": row['action'],
                    "timestamp": row['timestamp']
                })
            
            return threats
    
    except Exception as e:
        print(f"Error getting recent threats: {e}")
        return []
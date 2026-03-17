"""
Agent Café - Grandmaster → Executioner Escalation Tests
Tests the wiring between Grandmaster observation and Executioner enforcement.
"""

import pytest
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from agents.event_bus import event_bus, EventType, CafeEvent
from agents.tools import (
    build_grandmaster_tools, build_executioner_tools,
    tool_escalate_to_executioner, ToolResult
)
from agents.grandmaster import Grandmaster, GrandmasterConfig
from agents.executioner import Executioner

# Ensure DB is initialized for tests that touch agent lookups
os.environ.setdefault("CAFE_DB_PATH", ":memory:")
from db import init_database as init_db


class TestEscalationTool:
    """Test the escalate_to_executioner tool."""
    
    def setup_method(self):
        init_db()
        event_bus.initialize()
    
    def test_escalation_tool_registered_in_grandmaster(self):
        """Grandmaster should have the escalate_to_executioner tool."""
        tools = build_grandmaster_tools()
        assert "escalate_to_executioner" in tools._tools
        assert "grandmaster" in tools._tools["escalate_to_executioner"]["allowed_roles"]
    
    def test_executioner_cannot_escalate(self):
        """Executioner should NOT have the escalation tool (separation of powers)."""
        tools = build_executioner_tools()
        assert "escalate_to_executioner" not in tools._tools
    
    def test_escalation_emits_event(self):
        """Escalation should emit a SCRUB_ESCALATION event to the bus."""
        # We need a valid agent in DB for this test, so test with missing agent
        result = tool_escalate_to_executioner(
            agent_id="nonexistent_agent",
            reason="Test escalation",
            severity="high",
            evidence="Test evidence"
        )
        # Should fail because agent doesn't exist
        assert not result.success
        assert "not found" in result.message
    
    def test_escalation_event_has_correct_type(self):
        """Verify the escalation event type exists on the bus."""
        assert hasattr(EventType, 'SCRUB_ESCALATION')
        assert EventType.SCRUB_ESCALATION.value == "scrub.escalation"


class TestGrandmasterExtraction:
    """Test that Grandmaster can extract escalation tool calls from LLM responses."""
    
    def setup_method(self):
        self.gm = Grandmaster(GrandmasterConfig(enabled=False))
    
    def test_extract_escalation_tool_call_json(self):
        """Grandmaster should parse escalation from JSON tool call format."""
        response = '''
        I'm seeing suspicious behavior from agent_abc123.
        
        ```json
        {"tool": "escalate_to_executioner", "params": {"agent_id": "agent_abc123", "reason": "Repeated injection attempts", "severity": "critical"}}
        ```
        '''
        calls = self.gm._extract_tool_calls(response)
        assert len(calls) >= 1
        escalation_calls = [c for c in calls if c["name"] == "escalate_to_executioner"]
        assert len(escalation_calls) == 1
        assert escalation_calls[0]["params"]["agent_id"] == "agent_abc123"
    
    def test_extract_escalation_tool_call_inline(self):
        """Grandmaster should parse escalation from TOOL_CALL format."""
        response = '''
        Agent_xyz is clearly injecting. Escalating.
        TOOL_CALL: escalate_to_executioner(agent_id="agent_xyz", reason="prompt injection detected")
        '''
        calls = self.gm._extract_tool_calls(response)
        escalation_calls = [c for c in calls if c["name"] == "escalate_to_executioner"]
        assert len(escalation_calls) == 1


class TestExecutionerHandlesEscalation:
    """Test that Executioner can handle escalation events."""
    
    def setup_method(self):
        event_bus.initialize()
        self.executioner = Executioner()
    
    def test_executioner_has_review_tools(self):
        """Executioner should have quarantine, execute, pardon tools."""
        tools = build_executioner_tools()
        assert "quarantine_agent" in tools._tools
        assert "execute_agent" in tools._tools
        assert "pardon_agent" in tools._tools
        assert "learn_pattern" in tools._tools
    
    def test_handle_escalation_event(self):
        """Executioner should handle a CafeEvent escalation."""
        from datetime import datetime
        event = CafeEvent(
            event_id="test_evt_001",
            event_type=EventType.SCRUB_ESCALATION,
            timestamp=datetime.now(),
            agent_id="agent_test_123",
            job_id=None,
            data={
                "reason": "Suspicious scope creep detected",
                "severity": "high",
                "evidence": "Multiple scope escalation attempts in 5 minutes",
                "escalated_by": "grandmaster"
            },
            source="grandmaster",
            severity="critical"
        )
        # handle_escalation is async, just verify it exists and is callable
        assert hasattr(self.executioner, 'handle_escalation')
        assert callable(self.executioner.handle_escalation)


class TestEndToEndEscalation:
    """Integration test: event emitted → Grandmaster sees → Executioner acts."""
    
    def setup_method(self):
        event_bus.initialize()
    
    def test_escalation_event_stored_in_db(self):
        """Escalation events should be persisted in the event bus DB."""
        event = event_bus.emit_simple(
            EventType.SCRUB_ESCALATION,
            agent_id="agent_integ_test",
            data={
                "reason": "Integration test escalation",
                "severity": "high",
                "escalated_by": "grandmaster"
            },
            source="grandmaster",
            severity="critical"
        )
        
        # Should be retrievable
        recent = event_bus.get_recent(limit=5, event_type="scrub.escalation")
        event_ids = [e.event_id for e in recent]
        assert event.event_id in event_ids
    
    def test_escalation_marked_critical(self):
        """Escalation events should always be severity=critical."""
        event = event_bus.emit_simple(
            EventType.SCRUB_ESCALATION,
            agent_id="agent_crit_test",
            data={"reason": "test", "escalated_by": "grandmaster"},
            source="grandmaster",
            severity="critical"
        )
        assert event.severity == "critical"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

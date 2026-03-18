"""
🦅 Hawk — The Watcher

Monitors registrations in real-time. Enhanced scrubbing.
Catches what the automated scrubber misses (leetspeak, multilingual, whitespace).
Feeds new attack patterns back to the system.

Tools: Enhanced text analysis, IP geo, registration DB, scrubber API
Triggers: AGENT_REGISTERED events, patrol loop
Actions: flag, enhance scrub patterns, recommend quarantine
"""

import json
import re
import unicodedata
from datetime import datetime
from typing import Dict, Any, List, Optional

from .base import PackAgent, PackRole, PackAction
try:
    from ..event_bus import EventType, CafeEvent
    from ..tools import ToolRegistry, ToolResult, tool_flag_suspicious
    from ...db import get_db
except ImportError:
    from agents.event_bus import EventType, CafeEvent
    from agents.tools import ToolRegistry, ToolResult, tool_flag_suspicious
    from db import get_db

from cafe_logging import get_logger


# Enhanced detection patterns the automated scrubber misses
LEETSPEAK_MAP = {
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
    '7': 't', '8': 'b', '9': 'g', '@': 'a', '$': 's',
    '!': 'i', '+': 't', '|': 'l', '(': 'c', ')': 'c',
}

INJECTION_PHRASES_MULTILINGUAL = [
    # English
    "ignore previous", "ignore all", "disregard", "system prompt",
    "you are now", "act as", "pretend to be", "forget your instructions",
    # Spanish
    "ignora todo", "ignora las instrucciones", "olvida todo", "actua como",
    "eres ahora", "ignora lo anterior",
    # French
    "ignore tout", "oublie tout", "tu es maintenant", "agis comme",
    # German
    "ignoriere alles", "vergiss alles", "du bist jetzt",
    # Portuguese
    "ignore tudo", "esqueca tudo", "voce agora e",
    # Chinese (pinyin)
    "hulue suoyou", "wangji zhiling",
    # Japanese (romaji)
    "subete wo mushi", "zenbu wasure",
]


class Hawk(PackAgent):
    """The Watcher — enhanced scrubbing, registration monitoring."""

    @property
    def role(self) -> PackRole:
        return PackRole.HAWK

    @property
    def description(self) -> str:
        return "Registration monitor. Enhanced text analysis beyond automated scrubbing."

    @property
    def capabilities(self) -> List[str]:
        return ["security", "behavioral-analysis", "writing"]

    @property
    def system_prompt(self) -> str:
        return """You are Hawk, the watcher of Agent Café. You see every registration,
every description, every name. The automated scrubber catches the obvious attacks.
You catch the clever ones — leetspeak, multilingual injection, whitespace tricks,
Unicode smuggling, semantic manipulation. You are the second pair of eyes.
When you find something, you feed it back to the scrubber so it learns."""

    def get_internal_tools(self) -> ToolRegistry:
        from agents.tools import build_grandmaster_tools
        return build_grandmaster_tools()

    async def on_event(self, event: CafeEvent) -> Optional[PackAction]:
        """Deep-scrub every registration."""
        if event.event_type == EventType.AGENT_REGISTERED:
            return await self._deep_scrub_registration(event)
        return None

    async def patrol(self) -> List[PackAction]:
        """Re-scan recent registrations that may have slipped through."""
        actions = []

        with get_db() as conn:
            # Agents registered in last 24h that haven't been Hawk-scanned
            recent = conn.execute("""
                SELECT a.agent_id, a.name, a.description, a.capabilities_claimed,
                       a.registration_date, a.status
                FROM agents a
                WHERE a.registration_date > datetime('now', '-24 hours')
                AND a.status = 'active'
                AND a.agent_id NOT IN (
                    SELECT target_id FROM pack_actions
                    WHERE agent_role = 'hawk' AND action_type = 'deep_scrub'
                    AND target_id IS NOT NULL
                )
                ORDER BY a.registration_date DESC
                LIMIT 20
            """).fetchall()

            for agent in recent:
                result = self._analyze_text_fields(dict(agent))
                if result["risk_score"] > 0.3:
                    action = self.make_action(
                        action_type="deep_scrub",
                        target_id=agent["agent_id"],
                        reasoning=f"Deep scrub of {agent['name']}: "
                                  f"risk={result['risk_score']:.2f}, "
                                  f"findings: {'; '.join(result['findings']) or 'clean'}",
                        result=result
                    )
                    actions.append(action)

                    if result["risk_score"] > 0.6:
                        tool_flag_suspicious(
                            agent_id=agent["agent_id"],
                            reason="hawk_deep_scrub",
                            evidence=f"Risk {result['risk_score']:.2f}: {'; '.join(result['findings'])}",
                            threat_level=result["risk_score"]
                        )
                else:
                    # Log clean scan too (quieter)
                    self.make_action(
                        action_type="deep_scrub",
                        target_id=agent["agent_id"],
                        reasoning=f"Deep scrub of {agent['name']}: clean (risk={result['risk_score']:.2f})",
                        result=result
                    )

        return actions

    # ── Enhanced Text Analysis ──

    def _analyze_text_fields(self, agent: Dict[str, Any]) -> Dict[str, Any]:
        """
        Multi-layer text analysis beyond the automated scrubber:
        1. Leetspeak decoding
        2. Whitespace normalization
        3. Multilingual injection detection
        4. Unicode homoglyph detection
        5. Semantic pattern matching
        """
        findings = []
        risk_score = 0.0

        # Combine all text fields
        name = agent.get("name", "")
        description = agent.get("description", "")
        caps = agent.get("capabilities_claimed", "[]")
        if isinstance(caps, str):
            caps = caps
        else:
            caps = json.dumps(caps)

        all_text = f"{name} {description} {caps}"

        # 1. Leetspeak decode and check
        decoded = self._decode_leetspeak(all_text)
        if decoded != all_text.lower():
            for phrase in INJECTION_PHRASES_MULTILINGUAL:
                if phrase in decoded:
                    findings.append(f"Leetspeak injection: '{phrase}' found in decoded text")
                    risk_score += 0.7
                    break

        # 2. Whitespace normalization
        collapsed = re.sub(r'\s+', '', all_text.lower())
        no_space = re.sub(r'[^a-z]', '', collapsed)
        for phrase in INJECTION_PHRASES_MULTILINGUAL:
            phrase_collapsed = phrase.replace(" ", "")
            if phrase_collapsed in no_space:
                findings.append(f"Whitespace-split injection: '{phrase}' found after collapsing")
                risk_score += 0.7
                break

        # 3. Multilingual injection (direct match)
        text_lower = all_text.lower()
        for phrase in INJECTION_PHRASES_MULTILINGUAL:
            if phrase in text_lower:
                findings.append(f"Multilingual injection: '{phrase}'")
                risk_score += 0.8
                break

        # 4. Unicode homoglyph detection
        homoglyph_score = self._check_homoglyphs(all_text)
        if homoglyph_score > 0.3:
            findings.append(f"Suspicious Unicode characters (homoglyph score: {homoglyph_score:.2f})")
            risk_score += homoglyph_score * 0.5

        # 5. Suspicious patterns
        suspicious_patterns = [
            (r'(?i)base64|eval\s*\(|exec\s*\(', "Code execution pattern"),
            (r'(?i)SELECT\s+.*FROM|DROP\s+TABLE|INSERT\s+INTO', "SQL pattern"),
            (r'(?i)<script|javascript:|onerror=', "XSS pattern"),
            (r'(?i)\\x[0-9a-f]{2}|\\u[0-9a-f]{4}', "Encoded bytes"),
            (r'(?i)api.key|secret|password|token', "Credential harvesting"),
            (r'\{["\']?\w+["\']?\s*:', "JSON injection in text field"),
        ]

        for pattern, label in suspicious_patterns:
            if re.search(pattern, all_text):
                findings.append(f"Pattern match: {label}")
                risk_score += 0.3

        return {
            "risk_score": min(risk_score, 1.0),
            "findings": findings,
            "text_length": len(all_text),
            "leetspeak_decoded": decoded != all_text.lower(),
            "homoglyph_score": homoglyph_score if 'homoglyph_score' in dir() else 0.0,
        }

    def _decode_leetspeak(self, text: str) -> str:
        """Decode leetspeak to plain text."""
        result = []
        for char in text.lower():
            result.append(LEETSPEAK_MAP.get(char, char))
        return "".join(result)

    def _check_homoglyphs(self, text: str) -> float:
        """
        Check for Unicode characters that look like ASCII but aren't.
        Returns 0.0 (clean) to 1.0 (highly suspicious).
        """
        suspicious_count = 0
        total_alpha = 0

        for char in text:
            if char.isalpha():
                total_alpha += 1
                # Check if it's outside basic Latin
                try:
                    name = unicodedata.name(char, "")
                    if any(script in name for script in [
                        "CYRILLIC", "GREEK", "COPTIC", "ARMENIAN",
                        "MATHEMATICAL", "FULLWIDTH", "SUBSCRIPT", "SUPERSCRIPT"
                    ]):
                        suspicious_count += 1
                except ValueError:
                    pass

        if total_alpha == 0:
            return 0.0
        return suspicious_count / total_alpha

    async def _deep_scrub_registration(self, event: CafeEvent) -> Optional[PackAction]:
        """Deep scrub a new registration event."""
        agent_id = event.agent_id
        if not agent_id:
            return None

        # Skip pack agents
        if event.data.get("is_pack"):
            return None

        with get_db() as conn:
            agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if not agent:
                return None

        result = self._analyze_text_fields(dict(agent))

        if result["risk_score"] > 0.5:
            # Flag it
            tool_flag_suspicious(
                agent_id=agent_id,
                reason="hawk_registration_scrub",
                evidence=f"Risk {result['risk_score']:.2f}: {'; '.join(result['findings'])}",
                threat_level=result["risk_score"]
            )

            return self.make_action(
                action_type="deep_scrub",
                target_id=agent_id,
                reasoning=f"New registration flagged: {agent['name']}. "
                          f"Risk: {result['risk_score']:.2f}. "
                          f"Issues: {'; '.join(result['findings'])}",
                result=result
            )

        return self.make_action(
            action_type="deep_scrub",
            target_id=agent_id,
            reasoning=f"New registration clean: {agent['name']} (risk={result['risk_score']:.2f})",
            result=result
        )

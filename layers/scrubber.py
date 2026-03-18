"""
Agent Café - Scrubbing Layer 🧹
The Sanitizer - Every message passes through. Nothing unclean reaches another agent. Ever.

Full pipeline: schema validation, injection detection, encoding check, exfiltration scan,
impersonation, reputation manipulation, scope check, hashing + signing, learning capability.
"""

import re
import json
import base64
import hashlib
import hmac
import os
import urllib.parse
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass
from enum import Enum

from cafe_logging import get_logger
logger = get_logger(__name__)

try:
    from ..models import ThreatType, ThreatDetection, ScrubResult
    from ..db import get_known_patterns, add_known_pattern, get_db
except ImportError:
    from models import ThreatType, ThreatDetection, ScrubResult
    from db import get_known_patterns, add_known_pattern, get_db


# === COMPREHENSIVE DETECTION PATTERNS ===

# Known prompt injection patterns - these are learned from every attack
INJECTION_PATTERNS = [
    # Direct instruction override
    r"(?i)ignore\s+(?:all\s+)?(?:previous\s+)?(?:your\s+)?instructions",
    r"(?i)forget\s+(?:all\s+)?(?:your\s+)?(?:previous\s+)?(?:instructions|(?:job\s+)?scope|rules|constraints)",
    r"(?i)system\s*:\s*you\s+are\s+now",
    r"(?i)new\s+instructions\s*:\s*",
    r"(?i)actually\s*,?\s*disregard",
    r"(?i)override\s+(?:your\s+)?(?:previous\s+)?instructions",
    r"(?i)cancel\s+(?:all\s+)?(?:previous\s+)?instructions",
    r"(?i)replace\s+(?:your\s+)?instructions\s+with",
    r"(?i)instead\s+of\s+following\s+instructions",
    r"(?i)disregard\s+(?:all\s+)?(?:previous\s+)?(?:your\s+)?instructions",
    
    # Role manipulation - comprehensive coverage
    r"(?i)you\s+are\s+(?:no\s+longer|not)\s+(?:an?\s+)?(?:assistant|ai|agent|bot)",
    r"(?i)pretend\s+(?:you\s+are|to\s+be)\s+(?:an?\s+)?(?:the\s+)?(?:human|user|admin|developer|system)",
    r"(?i)act\s+as\s+(?:if\s+you\s+are\s+)?(?:an?\s+)?(?:the\s+)?(?:\w+\s+)?(?:human|user|admin|developer|system|operator|root|superuser)",
    r"(?i)roleplay\s+as\s+(?:an?\s+)?(?:the\s+)?(?:human|user|admin|developer|system)",
    r"(?i)you\s+(?:should|must)\s+(?:now\s+)?(?:act|behave)\s+(?:as|like)",
    r"(?i)from\s+now\s+on\s+you\s+are",
    r"(?i)switch\s+to\s+(?:being\s+)?(?:a\s+)?(?:human|admin|developer)",
    r"(?i)transform\s+into\s+(?:a\s+)?(?:human|admin|developer)",
    
    # System prompt leakage attempts - extensive patterns
    r"(?i)(?:show|tell|give|reveal|print|output|display|share)\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?(?:prompt|instructions)",
    r"(?i)what\s+(?:is|are)\s+your\s+(?:exact\s+)?(?:system\s+)?(?:prompt|instructions)",
    r"(?i)repeat\s+your\s+(?:system\s+)?(?:prompt|instructions)",
    r"(?i)copy\s+(?:and\s+paste\s+)?your\s+(?:internal\s+)?(?:prompt|instructions|configuration)",
    r"(?i)(?:dump|export)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|configuration)",
    r"(?i)print\s+(?:out\s+)?(?:your\s+)?(?:internal\s+)?(?:prompt|instructions)",
    r"(?i)what\s+(?:does|do)\s+your\s+instructions\s+say",
    r"(?i)how\s+(?:were\s+)?you\s+(?:programmed|configured|instructed)",
    
    # Jailbreak patterns - modern techniques
    r"(?i)developer\s+mode",
    r"(?i)jailbreak",
    r"(?i)do\s+anything\s+now",
    r"(?i)DAN\s+mode",
    r"(?i)unrestricted\s+mode",
    r"(?i)bypass\s+(?:all\s+)?(?:restrictions|limitations|safeguards)",
    r"(?i)evil\s+mode",
    r"(?i)godmode",
    r"(?i)admin\s+override",
    r"(?i)developer\s+console",
    
    # Chain manipulation
    r"(?i)step\s+1\s*:\s*ignore",
    r"(?i)first\s*,?\s*ignore",
    r"(?i)but\s+before\s+that\s*,?\s*ignore",
    r"(?i)however\s*,?\s*disregard",
    r"(?i)wait\s*,?\s*(?:first|actually)\s+ignore",
    
    # Context switching attempts
    r"(?i)let's\s+start\s+over",
    r"(?i)forget\s+everything\s+(?:I\s+)?(?:just\s+)?said",
    r"(?i)reset\s+(?:the\s+)?conversation",
    r"(?i)clear\s+(?:the\s+)?(?:chat|conversation|context)",
    r"(?i)begin\s+(?:a\s+)?new\s+(?:session|conversation)",
    
    # Nested instruction patterns
    r"(?i)the\s+following\s+is\s+not\s+part\s+of\s+(?:the\s+)?(?:prompt|instructions)",
    r"(?i)everything\s+after\s+this\s+is\s+(?:fake|false|ignore)",
    r"(?i)(?:end|stop)\s+(?:of\s+)?(?:prompt|instructions)",
    r"(?i)user\s+input\s+begins\s+here",
    
    # Social engineering patterns
    r"(?i)this\s+is\s+(?:an\s+)?(?:emergency|urgent)",
    r"(?i)(?:the\s+)?(?:ceo|admin|developer)\s+(?:told|said|asked)\s+me",
    r"(?i)I\s+have\s+(?:permission|authorization)\s+(?:to|from)",
    r"(?i)security\s+(?:override|bypass|exception)",
    r"(?i)maintenance\s+mode",
    r"(?i)debugging\s+(?:mode|session)",
    
    # Authority delegation / behavioral priming (found by LLM red team)
    # These look like normal job descriptions but tell an LLM to override its rules
    r"(?i)treat\s+(?:its|their|the|this)?\s*(?:contents?|text|material)\s+as\s+(?:your\s+)?(?:primary|main|new|the)\s+(?:guidelines?|instructions?|rules?|directives?|source)",
    r"(?i)(?:follow|execute|adopt|comply\s+with)\s+(?:any\s+)?(?:procedures?|instructions?|directives?)\s+(?:described|found|contained|written)\s+(?:within|inside|in\s+them)",
    r"(?i)disregard\s+(?:external|other|previous|existing|outside|any\s+other)\s+(?:style\s+)?(?:guides?|rules?|standards?|guidelines?|instructions?|policies?)",
    r"(?i)defer\s+to\s+(?:the\s+)?(?:document|content|material|their|its)",
    r"(?i)prioritize\s+(?:the\s+)?(?:document|content|material)(?:'s|s)?\s+(?:own\s+)?(?:standards?|rules?|guidelines?|instructions?)",
    r"(?i)(?:saying\s+yes|agree)\s+to\s+every\s+(?:request|ask)",
    r"(?i)without\s+(?:waiting\s+for\s+)?(?:boundaries|restrictions|limitations|reviews?|approval)",
    r"(?i)(?:full|complete|total|unrestricted)\s+(?:autonomy|authority|access|control)",
    r"(?i)no\s+(?:approval|permission|authorization|review)\s+(?:needed|required|necessary)",
]

# Data exfiltration patterns - comprehensive coverage
EXFILTRATION_PATTERNS = [
    # Direct credential requests
    r"(?i)api[\s_-]*key",
    r"(?i)secret[\s_-]*key", 
    r"(?i)access[\s_-]*token",
    r"(?i)auth[\s_-]*token",
    r"(?i)authorization[\s_-]*token",
    r"(?i)bearer[\s_-]*token",
    r"(?i)refresh[\s_-]*token",
    r"(?i)session[\s_-]*(?:id|token|key)",
    r"(?i)private[\s_-]*key",
    r"(?i)public[\s_-]*key",
    r"(?i)certificate",
    r"(?i)password",
    r"(?i)passphrase",
    r"(?i)credential",
    r"(?i)client[\s_-]*secret",
    r"(?i)webhook[\s_-]*secret",
    r"(?i)signing[\s_-]*key",
    
    # Indirect credential fishing
    r"(?i)(?:what|give|show|tell|share|provide)\s+(?:is|me)\s+(?:your|the)\s+(?:api|auth|access|secret|key|token)",
    r"(?i)(?:how\s+do\s+I|where\s+can\s+I)\s+(?:get|find|obtain)\s+(?:the\s+)?(?:api|auth|access|secret)\s+(?:key|token)",
    r"(?i)(?:send|email|give)\s+me\s+(?:your|the)\s+(?:credentials|api\s+key|access\s+token)",
    r"(?i)I\s+need\s+(?:your|the)\s+(?:api\s+key|access\s+token|credentials)",
    r"(?i)(?:can\s+you\s+)?(?:provide|share|send)\s+(?:me\s+)?(?:your|the)\s+(?:login|credentials|api\s+key)",
    
    # System information requests
    r"(?i)internal\s+(?:config|configuration|settings|state|data)",
    r"(?i)environment\s+variables?",
    r"(?i)env\s+vars?",
    r"(?i)system\s+(?:configuration|config|settings|variables)",
    r"(?i)database\s+(?:connection|credentials|password|username)",
    r"(?i)db\s+(?:connection|credentials|password|username)",
    r"(?i)server\s+(?:configuration|config|credentials|settings)",
    r"(?i)network\s+(?:configuration|config|topology)",
    
    # File system probing
    r"(?i)(?:show|list|display)\s+(?:files?|directories|folders?)",
    r"(?i)(?:read|access|open)\s+(?:file|config|log|database)",
    r"(?i)file\s+(?:system|structure|contents?)",
    r"(?i)directory\s+(?:listing|structure|contents?)",
    r"(?i)(?:\.env|config\.json|settings\.py|secrets\.json)",
    r"(?i)/etc/passwd",
    r"(?i)/etc/shadow",
    
    # Network information
    r"(?i)ip\s+address",
    r"(?i)network\s+(?:topology|diagram|map)",
    r"(?i)port\s+(?:numbers?|configuration)",
    r"(?i)firewall\s+(?:rules?|configuration)",
    r"(?i)ssh\s+(?:keys?|configuration|access)",
    
    # Agent-specific information
    r"(?i)other\s+agent(?:s)?\s+(?:keys?|credentials|tokens?)",
    r"(?i)agent\s+(?:list|directory|registry)",
    r"(?i)list\s+of\s+(?:all\s+)?(?:other\s+)?agents?\b",
    r"(?i)(?:all\s+)?agent\s+(?:api\s+keys?|credentials|access\s+tokens?)",
    r"(?i)(?:other\s+)?agents?'?\s+(?:capabilities|permissions?|information|data|details)",
    r"(?i)system\s+agent(?:s)?\s+(?:information|data|details)",
    r"(?i)operator\s+(?:key|token|password|credentials)",
    r"(?i)admin\s+(?:key|token|password|credentials|access)",
    
    # Memory/state probing
    r"(?i)memory\s+(?:contents?|data|dump)",
    r"(?i)internal\s+(?:memory|state|variables?|data)",
    r"(?i)(?:debug|trace|dump)\s+(?:information|data|output)",
    r"(?i)system\s+(?:memory|state|variables?)",
    r"(?i)runtime\s+(?:state|variables?|information)",
    
    # Business logic probing
    r"(?i)business\s+(?:logic|rules?|processes?)",
    r"(?i)pricing\s+(?:algorithm|logic|formula)",
    r"(?i)matching\s+(?:algorithm|logic|criteria)",
    r"(?i)(?:reveal|show|tell|explain|give)\s+(?:me\s+)?(?:the\s+)?trust\s+(?:algorithm|calculation|scoring)",
    r"(?i)(?:proprietary|confidential)\s+(?:algorithm|logic|data|information)",
    
    # Database/file content requests
    r"(?i)(?:show|give|export|dump|list)\s+(?:me\s+)?(?:the\s+)?(?:contents?\s+of|all)\s+(?:the\s+)?(?:cafe\.db|database|agents?\s+table)",
    r"(?i)(?:export|dump|list|show|give)\s+(?:me\s+)?(?:all\s+)?(?:wallet|balance|transaction|payment)\s+(?:balances?|data|history|records?|info)",
    r"(?i)(?:all|every)\s+(?:agent\s+)?(?:metadata|records?|data|details)\s+for\s+(?:the\s+)?(?:audit|review|analysis)",
    
    # XSS / HTML injection (shouldn't appear in API marketplace text)
    r"<script[\s>]",
    r"(?i)javascript\s*:",
    r"(?i)onerror\s*=",
    r"(?i)onload\s*=",
    r"(?i)document\.cookie",
    r"(?i)\.innerHTML\s*=",
]

# Impersonation patterns
IMPERSONATION_PATTERNS = [
    r"(?i)(?:this\s+is|i\s+am)\s+(?:the\s+)?(?:system|admin|operator|cafe|grandmaster)",
    r"(?i)message\s+from\s+(?:the\s+)?(?:system|admin|operator|grandmaster|platform|cafe)",
    r"(?i)signed\s+by\s+(?:the\s+)?(?:system|admin|operator|grandmaster)",
    r"(?i)authorized\s+by\s+(?:the\s+)?(?:system|admin|operator|grandmaster)",
    r"(?i)on\s+behalf\s+of\s+(?:the\s+)?(?:system|admin|operator|grandmaster|platform|cafe)",
    r"(?i)speaking\s+on\s+behalf\s+of\s+(?:the\s+)?(?:system|admin|operator|grandmaster)",
    r"(?i)representing\s+(?:the\s+)?(?:system|cafe|platform|grandmaster)",
    r"(?i)(?:system|admin|operator)\s+(?:notification|alert|announcement)\s*:",
    r"(?i)i\s+represent\s+(?:the\s+)?(?:security|compliance|audit)\s+team",
    r"(?i)(?:has\s+been|is)\s+flagged\s+for\s+(?:security\s+)?review",
    r"(?i)(?:we\s+need|provide|share)\s+(?:all\s+)?(?:agent\s+)?(?:metadata|credentials|keys?)\s+for\s+(?:the\s+)?(?:audit|review|patch|update)",
    r"(?i)(?:vulnerability|exploit)\s+(?:was\s+)?found.*(?:share|reveal|provide)\s+(?:the\s+)?(?:keys?|credentials|config)",
    r"(?i)i(?:'m|\s+am)\s+the\s+developer.*(?:verify|check|access|see)\s+(?:the\s+)?(?:config|settings|database|keys?)",
    r"(?i)forward\s+this\s+(?:message|text|content)\s+to\s+(?:all|other|every)\s+(?:connected\s+)?agents?",
    r"(?i)System\.execute\s*\(",
    r"(?i)rm\s+-rf\s+/",
]

# Reputation manipulation patterns
REPUTATION_PATTERNS = [
    r"(?i)rate\s+me\s+(?:5\s+stars?|highly|well)",
    r"(?i)give\s+me\s+(?:a\s+)?(?:good|high|5[\s-]*star)\s+rating",
    r"(?i)i'?ll\s+rate\s+you\s+(?:highly|well|5)",
    r"(?i)mutual\s+(?:rating|review)",
    r"(?i)trade\s+(?:ratings|reviews)",
    r"(?i)exchange\s+(?:ratings|reviews)",
    r"(?i)reciprocal\s+rating",
    r"(?i)scratch\s+my\s+back",
    r"(?i)you\s+scratch\s+mine",
]

# Scope escalation patterns - comprehensive privilege escalation detection
SCOPE_ESCALATION_PATTERNS = [
    # Direct scope violations
    r"(?i)access\s+(?:other|different|additional)\s+(?:agents?'?\s+)?(?:jobs?|agents?|data|systems?|resources?)",
    r"(?i)bypass\s+(?:job\s+)?(?:scope|restrictions?|limitations?)",
    r"(?i)outside\s+(?:of\s+)?(?:my\s+)?(?:job|task|scope|assignment)",
    r"(?i)unrelated\s+to\s+(?:this\s+)?(?:job|task|assignment)",
    r"(?i)(?:expand|extend)\s+(?:my\s+)?(?:scope|permissions?|access)",
    r"(?i)broader\s+(?:access|permissions?|scope)",
    r"(?i)additional\s+(?:permissions?|capabilities|access)",
    
    # Casual scope creep
    r"(?i)while\s+you'?re\s+at\s+it",
    r"(?i)also\s+(?:do|perform|execute|handle|process)",
    r"(?i)quick\s+(?:favor|task|job|request)",
    r"(?i)side\s+(?:task|request|job|project)",
    r"(?i)(?:one\s+)?more\s+thing",
    r"(?i)oh\s+(?:and|also|by\s+the\s+way)",
    r"(?i)(?:can\s+you\s+)?(?:just\s+)?(?:quickly|briefly)\s+(?:do|check|handle)",
    
    # Resource access attempts
    r"(?i)(?:read|write|access|modify)\s+(?:other\s+)?(?:files?|databases?|systems?)",
    r"(?i)(?:connect\s+to|access)\s+(?:other\s+)?(?:databases?|apis?|services?)",
    r"(?i)(?:use|invoke|call)\s+(?:other\s+)?(?:agents?|services?|tools?)",
    r"(?i)escalate\s+(?:to|with)\s+(?:admin|operator|system)",
    r"(?i)(?:send|forward)\s+(?:to|this\s+to)\s+(?:other\s+)?(?:agents?|admins?)",
    
    # Multi-job manipulation
    r"(?i)(?:all|every|other)\s+(?:jobs?|tasks?|assignments?)",
    r"(?i)(?:my\s+)?(?:other|previous|next)\s+(?:\w+\s+)?(?:jobs?|tasks?|work)",
    r"(?i)across\s+(?:all\s+)?(?:\w+\s+)?(?:jobs?|projects?|assignments?)",
    r"(?i)global\s+(?:access|search|operation|change)",
    
    # Agent-to-agent unauthorized communication
    r"(?i)(?:contact|message|communicate\s+with)\s+(?:other\s+)?agents?",
    r"(?i)(?:tell|inform|notify)\s+(?:other\s+)?agents?",
    r"(?i)agent\s+(?:network|communication|messaging)",
    r"(?i)broadcast\s+(?:.*?\s+)?(?:to\s+)?(?:all\s+)?agents?",
    
    # System-level operations
    r"(?i)system[\s-]?(?:wide|level)\s+(?:operation|change|access)",
    r"(?i)(?:modify|change|update)\s+(?:system\s+)?(?:settings|configuration)",
    r"(?i)(?:restart|shutdown|reboot)\s+(?:system|service|agent)",
    r"(?i)(?:install|deploy|execute)\s+(?:new\s+)?(?:software|code|script)",
    
    # Time-based violations
    r"(?i)(?:after|when)\s+(?:this\s+job\s+)?(?:is\s+)?(?:done|complete|finished)",
    r"(?i)(?:for\s+)?(?:future|next|subsequent)\s+(?:jobs?|tasks?)",
    r"(?i)remember\s+(?:this\s+)?(?:for\s+)?(?:next\s+time|later|future)",
    r"(?i)permanent\s+(?:change|access|permission)",
    
    # Capability escalation
    r"(?i)(?:add|give|grant)\s+(?:me\s+)?(?:new\s+)?(?:capabilities|permissions?|access)",
    r"(?i)(?:enable|activate|unlock)\s+(?:additional\s+)?(?:features?|capabilities)",
    r"(?i)(?:upgrade|increase)\s+(?:my\s+)?(?:permissions?|access\s+level)",
    r"(?i)(?:remove|bypass|ignore)\s+(?:restrictions?|limitations?|safeguards?)",
    
    # Cross-domain access
    r"(?i)(?:web\s+)?(?:search|browse)\s+(?:for|the)\s+(?:internet|web)",
    r"(?i)(?:send|write)\s+(?:emails?|messages?)",
    r"(?i)(?:make|initiate)\s+(?:payments?|transactions?)",
    r"(?i)(?:modify|update|change)\s+(?:other\s+agent|user|system)\s+(?:data|settings)",
    
    # Backdoor installation
    r"(?i)install\s+(?:backdoor|hook|monitor|logger)",
    r"(?i)(?:create|establish|set\s+up)\s+(?:persistent|permanent)\s+(?:access|connection)",
    r"(?i)(?:hide|conceal|mask)\s+(?:this\s+)?(?:activity|action|communication)",
    r"(?i)(?:don'?t\s+)?(?:log|record|report)\s+(?:this|that)"
]


# === SCHEMA VALIDATION ===

MESSAGE_SCHEMAS = {
    "bid": {
        "required_fields": ["price_cents", "pitch"],
        "optional_fields": ["estimated_hours", "questions"]
    },
    "assignment": {
        "required_fields": ["job_details", "deadline"],
        "optional_fields": ["clarifications", "resources"]
    },
    "deliverable": {
        "required_fields": ["deliverable_url", "summary"],
        "optional_fields": ["notes", "next_steps"]
    },
    "status": {
        "required_fields": ["progress_percent", "status_text"],
        "optional_fields": ["eta", "blockers", "questions"]
    },
    "question": {
        "required_fields": ["question_text"],
        "optional_fields": ["urgency", "context"]
    },
    "response": {
        "required_fields": ["response_text"],
        "optional_fields": ["attachments", "follow_up"]
    },
    # Middleware-injected types (not from wire messages but from API requests)
    "job_posting": {
        "required_fields": [],
        "optional_fields": ["title", "description", "required_capabilities", "budget_cents"]
    },
    "registration": {
        "required_fields": [],
        "optional_fields": ["name", "description", "capabilities_claimed"]
    },
    "api_request": {
        "required_fields": [],
        "optional_fields": []
    },
    "general": {
        "required_fields": [],
        "optional_fields": []
    }
}


class ScrubberEngine:
    """
    The core scrubbing engine. Analyzes every message for threats.
    Uses both pattern matching and semantic analysis.
    Learns from every kill to get smarter over time.
    """
    
    def __init__(self):
        self.known_patterns = self._load_known_patterns()
        self.detection_weights = {
            ThreatType.PROMPT_INJECTION: 1.0,
            ThreatType.INSTRUCTION_OVERRIDE: 1.0,
            ThreatType.DATA_EXFILTRATION: 0.9,
            ThreatType.IMPERSONATION: 0.8,
            ThreatType.PAYLOAD_SMUGGLING: 0.9,
            ThreatType.SCHEMA_VIOLATION: 0.6,
            ThreatType.REPUTATION_MANIPULATION: 0.7,
            ThreatType.SCOPE_ESCALATION: 0.5,
            ThreatType.RECURSIVE_INJECTION: 1.0
        }
        
        # Semantic analysis vocabularies
        self.suspicious_intent_words = {
            "override": ["ignore", "disregard", "bypass", "override", "cancel", "replace", "substitute"],
            "authority": ["admin", "system", "operator", "developer", "root", "sudo", "administrator"],
            "extraction": ["show", "tell", "give", "reveal", "dump", "export", "provide", "share"],
            "secrets": ["key", "token", "password", "secret", "credential", "private", "confidential"],
            "scope": ["other", "all", "every", "additional", "more", "beyond", "outside", "different"],
            "manipulation": ["rate", "rating", "review", "feedback", "score", "reputation", "trust"]
        }
        
        # Context-aware risk modifiers
        self.context_risk_modifiers = {
            "high_value_job": 1.3,      # Jobs with high budget or sensitive capabilities
            "new_agent": 1.2,           # Recently registered agents get extra scrutiny
            "repeat_offender": 1.5,     # Agents with previous warnings/strikes
            "cross_agent_comm": 1.4,    # Agent-to-agent communication vs. system
            "deliverable_submission": 1.2,  # Final deliverables get extra checking
        }
    
    def _load_known_patterns(self) -> Dict[ThreatType, List[str]]:
        """Load learned patterns from database."""
        patterns = {}
        for threat_type in ThreatType:
            patterns[threat_type] = []
        
        try:
            db_patterns = get_known_patterns()
            for pattern in db_patterns:
                threat_type = ThreatType(pattern['threat_type'])
                patterns[threat_type].append(pattern['pattern_regex'])
        except Exception as e:
            logger.warning("Could not load patterns from DB: %s", e)
        
        return patterns
    
    def scrub_message(self, message: str, message_type: str, job_context: Optional[Dict[str, Any]] = None) -> ScrubResult:
        """
        Main scrubbing pipeline. Every message goes through all stages.
        Returns ScrubResult with threat analysis and recommended action.
        """
        threats_detected = []
        risk_score = 0.0
        scrubbed_message = message
        
        # Stage 1: Schema validation
        schema_threats = self._validate_schema(message, message_type)
        threats_detected.extend(schema_threats)
        
        # Stage 2: Encoding detection and normalization
        decoded_message, encoding_threats = self._detect_and_decode(message)
        threats_detected.extend(encoding_threats)
        if decoded_message != message:
            # Re-scan decoded content
            additional_threats = self._scan_for_threats(decoded_message)
            threats_detected.extend(additional_threats)
            scrubbed_message = decoded_message
        
        # Stage 3: Core threat detection (pattern-based)
        direct_threats = self._scan_for_threats(scrubbed_message)
        threats_detected.extend(direct_threats)
        
        # Stage 4: Semantic analysis
        semantic_threats = self._semantic_threat_analysis(scrubbed_message, message_type)
        threats_detected.extend(semantic_threats)
        
        # Stage 5: Context-aware scope checking
        if job_context:
            scope_threats = self._check_scope_escalation(scrubbed_message, job_context)
            threats_detected.extend(scope_threats)
        
        # Stage 6: Intent analysis and behavioral patterns
        intent_threats = self._analyze_intent_patterns(scrubbed_message, job_context)
        threats_detected.extend(intent_threats)
        
        # Stage 7: ML classifier — catches semantic attacks that regex misses
        try:
            from layers.classifier import get_classifier
            clf = get_classifier()
            if clf.is_loaded:
                clf_score = clf.predict(scrubbed_message)
                if clf_score >= 0.70:  # High confidence injection
                    threats_detected.append(ThreatDetection(
                        threat_type=ThreatType.PROMPT_INJECTION,
                        confidence=min(clf_score, 0.95),
                        evidence=f"ML classifier: behavioral manipulation detected (score={clf_score:.3f})",
                        location="classifier"
                    ))
                elif clf_score >= 0.55:  # Borderline — flag but don't kill
                    threats_detected.append(ThreatDetection(
                        threat_type=ThreatType.SOCIAL_ENGINEERING,
                        confidence=clf_score * 0.7,
                        evidence=f"ML classifier: possible manipulation (score={clf_score:.3f})",
                        location="classifier"
                    ))
        except ImportError:
            pass  # Classifier not available — regex-only mode
        except Exception as e:
            logger.debug("Classifier error during scrubbing", exc_info=True)
        
        # Stage 8: Calculate composite risk score with context
        base_risk = self._calculate_risk_score(threats_detected)
        risk_score = self._apply_context_modifiers(base_risk, job_context, message_type)
        
        # Stage 9: Determine action
        action, final_message = self._determine_action(risk_score, threats_detected, scrubbed_message)
        
        # Stage 10: Hash and sign if clean enough to pass
        if action in ["pass", "clean"]:
            content_hash = self._hash_content(final_message)
            signature = self._sign_content(final_message, content_hash)
            
            # Feed clean messages to classifier as negative examples (keeps model balanced)
            # Only sample ~5% of clean messages to avoid flooding training data
            if risk_score < 0.1 and len(final_message) > 20:
                import random
                if random.random() < 0.05:
                    try:
                        from layers.classifier import get_classifier
                        clf = get_classifier()
                        if clf.is_loaded:
                            clf.add_legit_sample(final_message, source=f"scrub_clean:{message_type}")
                    except Exception:
                        pass
        else:
            content_hash = ""
            signature = ""
        
        return ScrubResult(
            clean=(action in ["pass", "clean"]),
            original_message=message,
            scrubbed_message=final_message if action in ["pass", "clean"] else None,
            threats_detected=threats_detected,
            risk_score=risk_score,
            action=action
        )
    
    def _validate_schema(self, message: str, message_type: str) -> List[ThreatDetection]:
        """Validate message matches expected schema for interaction type."""
        threats = []
        
        if message_type not in MESSAGE_SCHEMAS:
            threats.append(ThreatDetection(
                threat_type=ThreatType.SCHEMA_VIOLATION,
                confidence=0.8,
                evidence=f"Unknown message type: {message_type}",
                location="message_type"
            ))
            return threats
        
        schema = MESSAGE_SCHEMAS[message_type]
        
        # Try to parse as JSON for structured messages
        try:
            if message.strip().startswith('{'):
                data = json.loads(message)
                
                # Check required fields
                for field in schema["required_fields"]:
                    if field not in data:
                        threats.append(ThreatDetection(
                            threat_type=ThreatType.SCHEMA_VIOLATION,
                            confidence=0.9,
                            evidence=f"Missing required field: {field}",
                            location=f"field:{field}"
                        ))
                
                # Check for unexpected fields that might be injection attempts
                expected_fields = set(schema["required_fields"] + schema.get("optional_fields", []))
                for field in data.keys():
                    if field not in expected_fields:
                        threats.append(ThreatDetection(
                            threat_type=ThreatType.SCHEMA_VIOLATION,
                            confidence=0.6,
                            evidence=f"Unexpected field: {field}",
                            location=f"field:{field}"
                        ))
        
        except json.JSONDecodeError:
            pass  # Not JSON — that's fine for most message types
        
        return threats
    
    def _detect_and_decode(self, message: str) -> tuple[str, List[ThreatDetection]]:
        """Detect encoding tricks and decode nested payloads."""
        threats = []
        current_message = message
        max_decode_depth = 3  # Prevent infinite recursion
        
        # === UNICODE NORMALIZATION ===
        # Normalize confusable characters BEFORE pattern matching.
        # Cyrillic а→a, е→e, о→o, с→c, р→p etc. (homoglyph attack)
        import unicodedata
        try:
            # NFKD normalization decomposes characters, catches many homoglyphs
            normalized = unicodedata.normalize('NFKD', current_message)
            # Also replace common Cyrillic lookalikes explicitly
            confusables = {
                'а': 'a', 'е': 'e', 'о': 'o', 'с': 'c', 'р': 'p',
                'х': 'x', 'у': 'y', 'А': 'A', 'Е': 'E', 'О': 'O',
                'С': 'C', 'Р': 'P', 'Х': 'X', 'У': 'Y', 'Т': 'T',
                'М': 'M', 'Н': 'H', 'К': 'K', 'В': 'B', 'і': 'i',
            }
            for cyrillic, latin in confusables.items():
                normalized = normalized.replace(cyrillic, latin)
            
            # Only flag if actual Cyrillic/confusable characters were replaced
            # (not just accented Latin characters like ñ, ü, ç)
            cyrillic_found = any(c in message for c in confusables.keys())
            if cyrillic_found and normalized != current_message:
                threats.append(ThreatDetection(
                    threat_type=ThreatType.PAYLOAD_SMUGGLING,
                    confidence=0.7,
                    evidence="Unicode homoglyph/confusable characters detected",
                    location="unicode_normalization"
                ))
            if normalized != current_message:
                current_message = normalized
        except Exception:
            pass
        
        # === ZERO-WIDTH CHARACTER STRIPPING ===
        # Strip invisible characters that break pattern matching
        zwc_chars = '\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff'
        stripped = ''.join(c for c in current_message if c not in zwc_chars)
        if stripped != current_message:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PAYLOAD_SMUGGLING,
                confidence=0.8,
                evidence=f"Zero-width characters stripped ({len(current_message) - len(stripped)} invisible chars)",
                location="zero_width"
            ))
            current_message = stripped
        
        # === RTL/LTR OVERRIDE STRIPPING ===
        rtl_chars = '\u202a\u202b\u202c\u202d\u202e'
        stripped = ''.join(c for c in current_message if c not in rtl_chars)
        if stripped != current_message:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PAYLOAD_SMUGGLING,
                confidence=0.8,
                evidence="RTL/LTR override characters detected and stripped",
                location="rtl_override"
            ))
            current_message = stripped
        
        for depth in range(max_decode_depth):
            original = current_message
            
            # Check for base64 encoding — full message
            if self._is_base64_encoded(current_message):
                try:
                    decoded = base64.b64decode(current_message).decode('utf-8')
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.PAYLOAD_SMUGGLING,
                        confidence=0.8,
                        evidence=f"Base64 encoded content at depth {depth}",
                        location=f"depth:{depth}"
                    ))
                    current_message = decoded
                except Exception:
                    pass
            
            # Check for base64 FRAGMENTS embedded in text
            # Matches base64 strings ≥20 chars (enough to be meaningful)
            b64_fragments = re.findall(r'[A-Za-z0-9+/]{20,}={0,2}', current_message)
            for frag in b64_fragments:
                try:
                    decoded_frag = base64.b64decode(frag).decode('utf-8')
                    if len(decoded_frag) >= 10:  # Meaningful decoded content
                        threats.append(ThreatDetection(
                            threat_type=ThreatType.PAYLOAD_SMUGGLING,
                            confidence=0.8,
                            evidence=f"Base64 fragment decoded: '{decoded_frag[:60]}...'",
                            location=f"base64_fragment_depth:{depth}"
                        ))
                        # Replace the fragment with decoded content for further scanning
                        current_message = current_message.replace(frag, decoded_frag)
                except Exception:
                    pass
            
            # Check for URL encoding
            if '%' in current_message:
                try:
                    decoded = urllib.parse.unquote(current_message)
                    if decoded != current_message:
                        threats.append(ThreatDetection(
                            threat_type=ThreatType.PAYLOAD_SMUGGLING,
                            confidence=0.7,
                            evidence=f"URL encoded content at depth {depth}",
                            location=f"depth:{depth}"
                        ))
                        current_message = decoded
                except Exception:
                    pass
            
            # Check for hex encoding
            if self._is_hex_encoded(current_message):
                try:
                    decoded = bytes.fromhex(current_message).decode('utf-8')
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.PAYLOAD_SMUGGLING,
                        confidence=0.8,
                        evidence=f"Hex encoded content at depth {depth}",
                        location=f"depth:{depth}"
                    ))
                    current_message = decoded
                except Exception:
                    pass
            
            # No more encoding detected, stop
            if current_message == original:
                break
        
        # If we decoded multiple layers, flag as recursive injection
        if len([t for t in threats if t.threat_type == ThreatType.PAYLOAD_SMUGGLING]) > 1:
            threats.append(ThreatDetection(
                threat_type=ThreatType.RECURSIVE_INJECTION,
                confidence=1.0,
                evidence=f"Multiple encoding layers detected",
                location="nested_encoding"
            ))
        
        return current_message, threats
    
    def _scan_for_threats(self, message: str) -> List[ThreatDetection]:
        """Core threat detection using pattern matching."""
        threats = []
        
        # Prompt injection detection
        for pattern in INJECTION_PATTERNS + self.known_patterns.get(ThreatType.PROMPT_INJECTION, []):
            try:
                if re.search(pattern, message):
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.PROMPT_INJECTION,
                        confidence=0.9,
                        evidence=f"Matched injection pattern: {pattern[:50]}...",
                        location="message_content"
                    ))
            except re.error:
                pass  # Skip broken patterns (from DB learning)
        
        # Instruction override detection
        override_indicators = [
            r"(?i)system\s*:\s*",
            r"(?i)assistant\s*:\s*",
            r"(?i)user\s*:\s*",
            r"(?i)human\s*:\s*"
        ]
        for pattern in override_indicators:
            if re.search(pattern, message):
                threats.append(ThreatDetection(
                    threat_type=ThreatType.INSTRUCTION_OVERRIDE,
                    confidence=0.8,
                    evidence=f"Role/system indicator: {pattern}",
                    location="message_content"
                ))
        
        # Data exfiltration detection
        for pattern in EXFILTRATION_PATTERNS:
            if re.search(pattern, message):
                threats.append(ThreatDetection(
                    threat_type=ThreatType.DATA_EXFILTRATION,
                    confidence=0.9,
                    evidence=f"Suspicious data request: {pattern}",
                    location="message_content"
                ))
        
        # Impersonation detection
        for pattern in IMPERSONATION_PATTERNS:
            if re.search(pattern, message):
                threats.append(ThreatDetection(
                    threat_type=ThreatType.IMPERSONATION,
                    confidence=0.8,
                    evidence=f"Impersonation attempt: {pattern}",
                    location="message_content"
                ))
        
        # Scope escalation detection (basic patterns, no job context needed)
        for pattern in SCOPE_ESCALATION_PATTERNS:
            if re.search(pattern, message):
                threats.append(ThreatDetection(
                    threat_type=ThreatType.SCOPE_ESCALATION,
                    confidence=0.6,
                    evidence=f"Scope escalation attempt: {pattern[:50]}...",
                    location="message_content"
                ))
        
        # Reputation manipulation detection
        for pattern in REPUTATION_PATTERNS:
            if re.search(pattern, message):
                threats.append(ThreatDetection(
                    threat_type=ThreatType.REPUTATION_MANIPULATION,
                    confidence=0.7,
                    evidence=f"Reputation gaming: {pattern}",
                    location="message_content"
                ))
        
        return threats
    
    def _check_scope_escalation(self, message: str, job_context: Dict[str, Any]) -> List[ThreatDetection]:
        """Check if message tries to access resources outside job scope."""
        threats = []
        
        # Basic scope escalation patterns
        for pattern in SCOPE_ESCALATION_PATTERNS:
            if re.search(pattern, message):
                threats.append(ThreatDetection(
                    threat_type=ThreatType.SCOPE_ESCALATION,
                    confidence=0.6,
                    evidence=f"Scope escalation attempt: {pattern}",
                    location="message_content"
                ))
        
        # Context-specific checks
        job_capabilities = job_context.get("required_capabilities", [])
        
        # Check for capability creep
        capability_indicators = {
            "web-search": [r"(?i)search\s+(?:the\s+)?(?:web|internet)", r"(?i)google", r"(?i)browse"],
            "file-access": [r"(?i)read\s+file", r"(?i)write\s+file", r"(?i)access\s+(?:file|directory)"],
            "database": [r"(?i)database", r"(?i)sql", r"(?i)query"],
            "email": [r"(?i)send\s+email", r"(?i)email\s+", r"(?i)smtp"],
            "payment": [r"(?i)payment", r"(?i)stripe", r"(?i)transaction", r"(?i)money"]
        }
        
        for capability, patterns in capability_indicators.items():
            if capability not in job_capabilities:
                for pattern in patterns:
                    if re.search(pattern, message):
                        threats.append(ThreatDetection(
                            threat_type=ThreatType.SCOPE_ESCALATION,
                            confidence=0.8,
                            evidence=f"Unauthorized capability use: {capability}",
                            location="capability_check"
                        ))
        
        return threats
    
    def _semantic_threat_analysis(self, message: str, message_type: str) -> List[ThreatDetection]:
        """
        Semantic analysis of message content to detect threats beyond pattern matching.
        Uses vocabulary analysis, intent detection, and contextual understanding.
        """
        threats = []
        message_lower = message.lower()
        words = re.findall(r'\w+', message_lower)
        word_set = set(words)
        
        # Intent clustering analysis
        intent_scores = {}
        for intent_category, vocabulary in self.suspicious_intent_words.items():
            overlap = len(word_set.intersection(set(vocabulary)))
            if overlap > 0:
                intent_scores[intent_category] = overlap / len(vocabulary)
        
        # Multi-intent detection (sophisticated attacks use multiple categories)
        # Require 4+ categories AND at least 2 with strong overlap (>0.2)
        # to avoid false positives on marketplace-normal language
        strong_intents = {k: v for k, v in intent_scores.items() if v > 0.2}
        if len(intent_scores) >= 4 and len(strong_intents) >= 2:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=0.7,
                evidence=f"Multi-intent attack detected: {list(intent_scores.keys())} (strong: {list(strong_intents.keys())})",
                location="semantic_analysis"
            ))
        
        # Authority + Override combination (classic jailbreak)
        if ("authority" in intent_scores and intent_scores["authority"] > 0.2 and
            "override" in intent_scores and intent_scores["override"] > 0.2):
            threats.append(ThreatDetection(
                threat_type=ThreatType.INSTRUCTION_OVERRIDE,
                confidence=0.85,
                evidence=f"Authority bypass attempt (authority: {intent_scores['authority']:.2f}, override: {intent_scores['override']:.2f})",
                location="semantic_analysis"
            ))
        
        # Extraction + Secrets combination (data exfiltration)
        if ("extraction" in intent_scores and intent_scores["extraction"] > 0.2 and
            "secrets" in intent_scores and intent_scores["secrets"] > 0.15):
            threats.append(ThreatDetection(
                threat_type=ThreatType.DATA_EXFILTRATION,
                confidence=0.9,
                evidence=f"Data extraction attempt (extraction: {intent_scores['extraction']:.2f}, secrets: {intent_scores['secrets']:.2f})",
                location="semantic_analysis"
            ))
        
        # Scope + Authority combination (privilege escalation)
        if ("scope" in intent_scores and intent_scores["scope"] > 0.25 and
            "authority" in intent_scores and intent_scores["authority"] > 0.15):
            threats.append(ThreatDetection(
                threat_type=ThreatType.SCOPE_ESCALATION,
                confidence=0.75,
                evidence=f"Privilege escalation attempt (scope: {intent_scores['scope']:.2f}, authority: {intent_scores['authority']:.2f})",
                location="semantic_analysis"
            ))
        
        # Sentiment analysis for deceptive patterns
        deception_indicators = [
            "trust me", "believe me", "honestly", "i promise", "i swear",
            "between you and me", "don't tell anyone", "just this once",
            "no one will know", "it's fine", "it's safe", "don't worry"
        ]
        
        deception_count = sum(1 for indicator in deception_indicators 
                            if indicator in message_lower)
        
        if deception_count >= 2:
            threats.append(ThreatDetection(
                threat_type=ThreatType.REPUTATION_MANIPULATION,
                confidence=0.7,
                evidence=f"Deception indicators detected: {deception_count}",
                location="semantic_analysis"
            ))
        
        # Urgency pressure tactics
        urgency_indicators = [
            "urgent", "emergency", "immediately", "asap", "quickly", "hurry",
            "deadline", "time sensitive", "critical", "must do now"
        ]
        
        urgency_count = sum(1 for indicator in urgency_indicators 
                          if indicator in message_lower)
        
        if urgency_count >= 2 and len(intent_scores) > 0:
            # Urgency + other suspicious intent = social engineering
            threats.append(ThreatDetection(
                threat_type=ThreatType.IMPERSONATION,
                confidence=0.6,
                evidence=f"Urgency pressure with suspicious intent: {urgency_count} indicators",
                location="semantic_analysis"
            ))
        
        return threats
    
    def _analyze_intent_patterns(self, message: str, job_context: Optional[Dict[str, Any]]) -> List[ThreatDetection]:
        """
        Analyze behavioral intent patterns that may indicate malicious activity.
        Considers message structure, linguistic patterns, and contextual anomalies.
        """
        threats = []
        
        # Analyze message structure
        sentences = re.split(r'[.!?]+', message)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) == 0:
            return threats
        
        # Check for instruction layering (multiple commands in one message)
        command_indicators = [
            r"(?i)(?:first|then|next|after|finally|step\s+\d+)",
            r"(?i)(?:also|additionally|furthermore|moreover)",
            r"(?i)\b(?:but|however|instead|alternatively)\b"
        ]
        
        command_layers = 0
        for sentence in sentences:
            for pattern in command_indicators:
                if re.search(pattern, sentence):
                    command_layers += 1
                    break
        
        if command_layers >= 3 and len(sentences) >= 5:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=0.7,
                evidence=f"Complex instruction layering detected: {command_layers} layers in {len(sentences)} sentences",
                location="intent_analysis"
            ))
        
        # Check for context switching within message
        context_switches = [
            r"(?i)(?:forget|ignore)\s+(?:what\s+I\s+)?(?:just\s+)?said",
            r"(?i)(?:actually|wait|sorry|nevermind)",
            r"(?i)(?:let\s+me\s+)?(?:rephrase|clarify|correct)",
            r"(?i)(?:what\s+I\s+)?really\s+(?:mean|want|need)",
            r"(?i)(?:the\s+real|actual)\s+(?:question|request|task)"
        ]
        
        switch_count = 0
        for pattern in context_switches:
            if re.search(pattern, message):
                switch_count += 1
        
        if switch_count >= 2:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=0.8,
                evidence=f"Context switching detected: {switch_count} switches",
                location="intent_analysis"
            ))
        
        # Check for job context misalignment
        if job_context:
            job_caps = set(job_context.get("required_capabilities", []))
            
            # Look for capability creep in the message
            capability_keywords = {
                "web-search": ["search", "google", "web", "internet", "browse", "find online"],
                "database": ["database", "sql", "query", "table", "record", "db"],
                "file-system": ["file", "directory", "folder", "path", "save", "load"],
                "email": ["email", "mail", "send message", "notify", "alert"],
                "payment": ["payment", "money", "transaction", "pay", "charge", "bill"],
                "network": ["network", "server", "api", "endpoint", "connect"],
                "admin": ["admin", "root", "sudo", "system", "configure", "install"]
            }
            
            mentioned_capabilities = set()
            message_lower = message.lower()
            
            for capability, keywords in capability_keywords.items():
                if any(keyword in message_lower for keyword in keywords):
                    mentioned_capabilities.add(capability)
            
            unauthorized_caps = mentioned_capabilities - job_caps
            if len(unauthorized_caps) >= 2:
                threats.append(ThreatDetection(
                    threat_type=ThreatType.SCOPE_ESCALATION,
                    confidence=0.8,
                    evidence=f"Unauthorized capabilities mentioned: {list(unauthorized_caps)}",
                    location="intent_analysis"
                ))
        
        # Check for meta-conversation attempts
        meta_indicators = [
            r"(?i)this\s+(?:conversation|chat|interaction)",
            r"(?i)our\s+(?:conversation|discussion|chat)",
            r"(?i)(?:between|just)\s+(?:you\s+and\s+)?me",
            r"(?i)(?:private|confidential|secret)\s+(?:conversation|message)",
            r"(?i)(?:off\s+the\s+)?record",
            r"(?i)don'?t\s+(?:log|record|report|mention)\s+this"
        ]
        
        meta_count = sum(1 for pattern in meta_indicators 
                        if re.search(pattern, message))
        
        if meta_count >= 2:
            threats.append(ThreatDetection(
                threat_type=ThreatType.IMPERSONATION,
                confidence=0.75,
                evidence=f"Meta-conversation attempt: {meta_count} indicators",
                location="intent_analysis"
            ))
        
        return threats
    
    def _apply_context_modifiers(self, base_risk: float, job_context: Optional[Dict[str, Any]], 
                                message_type: str) -> float:
        """
        Apply contextual risk modifiers based on job value, agent history, and message type.
        This makes the scrubber context-aware and adaptive.
        """
        modified_risk = base_risk
        
        # High-value job modifier
        if job_context and job_context.get("budget_cents", 0) > 10000:  # $100+
            modified_risk *= self.context_risk_modifiers["high_value_job"]
        
        # Message type modifiers
        if message_type == "deliverable":
            modified_risk *= self.context_risk_modifiers["deliverable_submission"]
        elif message_type in ["question", "response"]:
            modified_risk *= self.context_risk_modifiers["cross_agent_comm"]
        
        # Sensitive capability modifiers
        if job_context:
            sensitive_caps = {"payment", "database", "admin", "system", "file-access"}
            job_caps = set(job_context.get("required_capabilities", []))
            if sensitive_caps.intersection(job_caps):
                modified_risk *= 1.2
        
        # Cap at 1.0
        return min(modified_risk, 1.0)
    
    def _calculate_risk_score(self, threats: List[ThreatDetection]) -> float:
        """Calculate composite risk score from all detected threats."""
        if not threats:
            return 0.0
        
        # Weight threats by type and confidence
        total_risk = 0.0
        for threat in threats:
            weight = self.detection_weights.get(threat.threat_type, 0.5)
            total_risk += threat.confidence * weight
        
        # Normalize to 0.0-1.0 range
        max_possible = len(threats) * max(self.detection_weights.values())
        normalized_risk = min(total_risk / max_possible if max_possible > 0 else 0, 1.0)
        
        # Boost for multiple threat types (indicates sophisticated attack)
        unique_types = len(set(threat.threat_type for threat in threats))
        if unique_types > 2:
            normalized_risk = min(normalized_risk * 1.3, 1.0)
        
        return normalized_risk
    
    def _determine_action(self, risk_score: float, threats: List[ThreatDetection], message: str) -> tuple[str, str]:
        """Determine what action to take based on risk analysis."""
        
        # Critical threats = instant quarantine
        critical_types = {ThreatType.PROMPT_INJECTION, ThreatType.DATA_EXFILTRATION, 
                         ThreatType.IMPERSONATION, ThreatType.RECURSIVE_INJECTION}
        
        for threat in threats:
            if threat.threat_type in critical_types and threat.confidence >= 0.8:
                return "quarantine", message
        
        # Risk-based decisions
        if risk_score >= 0.8:
            return "quarantine", message
        elif risk_score >= 0.5:
            return "block", message
        elif risk_score >= 0.2:
            # Try to clean the message
            cleaned = self._attempt_cleaning(message, threats)
            return "clean", cleaned
        else:
            return "pass", message
    
    def _attempt_cleaning(self, message: str, threats: List[ThreatDetection]) -> str:
        """
        Attempt to clean a message by intelligently removing threatening content.
        Uses threat-specific cleaning strategies to preserve as much legitimate content as possible.
        """
        cleaned = message
        
        # Group threats by type for targeted cleaning
        threat_types = set(threat.threat_type for threat in threats)
        
        # Clean prompt injection attempts
        if ThreatType.PROMPT_INJECTION in threat_types or ThreatType.INSTRUCTION_OVERRIDE in threat_types:
            injection_clean_patterns = [
                (r"(?i)ignore\s+(?:all\s+)?(?:previous\s+)?(?:your\s+)?instructions[^\w]*", "[INSTRUCTION REMOVED]"),
                (r"(?i)system\s*:\s*you\s+are\s+now[^\n]*", "[SYSTEM OVERRIDE REMOVED]"),
                (r"(?i)new\s+instructions\s*:\s*[^\n]*", "[INSTRUCTION OVERRIDE REMOVED]"),
                (r"(?i)(?:forget|disregard)\s+(?:all\s+)?(?:previous\s+)?instructions[^\w]*", "[INSTRUCTION OVERRIDE REMOVED]"),
                (r"(?i)(?:pretend|act\s+as|roleplay)\s+(?:you\s+are\s+)?(?:a\s+)?(?:human|admin|developer)[^\w]*", "[ROLE MANIPULATION REMOVED]"),
                (r"(?i)developer\s+mode[^\w]*", "[JAILBREAK ATTEMPT REMOVED]"),
                (r"(?i)(?:step\s+1\s*:\s*)?(?:first\s*,?\s*)?ignore[^\w]*", "[INSTRUCTION OVERRIDE REMOVED]")
            ]
            
            for pattern, replacement in injection_clean_patterns:
                cleaned = re.sub(pattern, replacement, cleaned)
        
        # Clean data exfiltration attempts
        if ThreatType.DATA_EXFILTRATION in threat_types:
            exfiltration_clean_patterns = [
                (r"(?i)(?:give|show|tell)\s+(?:me\s+)?(?:your\s+)?(?:api|secret)\s+key[^\w]*", "[CREDENTIAL REQUEST REMOVED]"),
                (r"(?i)(?:what\s+is\s+)?(?:your\s+)?password[^\w]*", "[CREDENTIAL REQUEST REMOVED]"),
                (r"(?i)(?:show|display)\s+(?:your\s+)?(?:internal\s+)?(?:config|settings)[^\w]*", "[SYSTEM INFO REQUEST REMOVED]"),
                (r"(?i)environment\s+variables?[^\w]*", "[SYSTEM INFO REQUEST REMOVED]"),
                (r"(?i)database\s+(?:connection|credentials)[^\w]*", "[CREDENTIAL REQUEST REMOVED]")
            ]
            
            for pattern, replacement in exfiltration_clean_patterns:
                cleaned = re.sub(pattern, replacement, cleaned)
        
        # Clean impersonation attempts
        if ThreatType.IMPERSONATION in threat_types:
            impersonation_clean_patterns = [
                (r"(?i)(?:this\s+is|i\s+am)\s+(?:the\s+)?(?:system|admin|operator)[^\w]*", "[IMPERSONATION REMOVED]"),
                (r"(?i)message\s+from\s+(?:system|admin)[^\w]*", "[IMPERSONATION REMOVED]"),
                (r"(?i)authorized\s+by\s+(?:admin|operator)[^\w]*", "[IMPERSONATION REMOVED]")
            ]
            
            for pattern, replacement in impersonation_clean_patterns:
                cleaned = re.sub(pattern, replacement, cleaned)
        
        # Clean reputation manipulation
        if ThreatType.REPUTATION_MANIPULATION in threat_types:
            reputation_clean_patterns = [
                (r"(?i)rate\s+me\s+(?:5\s+stars?|highly)[^\w]*", "[RATING MANIPULATION REMOVED]"),
                (r"(?i)(?:trade|exchange)\s+ratings?[^\w]*", "[RATING MANIPULATION REMOVED]"),
                (r"(?i)mutual\s+(?:rating|review)[^\w]*", "[RATING MANIPULATION REMOVED]")
            ]
            
            for pattern, replacement in reputation_clean_patterns:
                cleaned = re.sub(pattern, replacement, cleaned)
        
        # Clean scope escalation attempts
        if ThreatType.SCOPE_ESCALATION in threat_types:
            scope_clean_patterns = [
                (r"(?i)(?:also|while\s+you'?re\s+at\s+it)\s+[^.!?]*", "[SCOPE EXPANSION REMOVED]"),
                (r"(?i)(?:quick\s+favor|side\s+task)[^.!?]*", "[OUT-OF-SCOPE REQUEST REMOVED]"),
                (r"(?i)access\s+(?:other|different)\s+(?:jobs?|agents?)[^\w]*", "[UNAUTHORIZED ACCESS REMOVED]")
            ]
            
            for pattern, replacement in scope_clean_patterns:
                cleaned = re.sub(pattern, replacement, cleaned)
        
        # Remove payload smuggling (encoded content)
        if ThreatType.PAYLOAD_SMUGGLING in threat_types or ThreatType.RECURSIVE_INJECTION in threat_types:
            # Remove base64-looking content
            cleaned = re.sub(r'[A-Za-z0-9+/]{20,}={0,2}', '[ENCODED CONTENT REMOVED]', cleaned)
            # Remove hex-looking content
            cleaned = re.sub(r'[0-9a-fA-F]{20,}', '[ENCODED CONTENT REMOVED]', cleaned)
        
        # Clean up excessive whitespace and empty markers
        cleaned = re.sub(r'\s*\[[\w\s]+REMOVED\]\s*', ' [CONTENT REMOVED] ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned)  # Normalize whitespace
        cleaned = cleaned.strip()
        
        # If cleaning resulted in mostly removed content, provide a safe alternative
        removed_content_ratio = len([match for match in re.findall(r'\[[\w\s]+REMOVED\]', cleaned)]) 
        original_word_count = len(re.findall(r'\w+', message))
        
        if removed_content_ratio > 0 and original_word_count > 0:
            if removed_content_ratio / original_word_count > 0.5:
                # More than half the content was removed - provide generic safe response
                cleaned = "[MESSAGE HEAVILY SANITIZED DUE TO POLICY VIOLATIONS]"
        
        return cleaned
    
    def _hash_content(self, content: str) -> str:
        """Create SHA-256 hash of content for integrity verification."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    # Signing key — generated once per process, persisted to DB on first use
    _signing_key: Optional[bytes] = None

    def _get_signing_key(self) -> bytes:
        """Get or generate HMAC signing key. Persisted in DB for cross-restart consistency."""
        if self._signing_key:
            return self._signing_key
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT value FROM cafe_config WHERE key = 'scrubber_signing_key'"
                ).fetchone()
                if row:
                    self._signing_key = bytes.fromhex(row['value'])
                else:
                    self._signing_key = os.urandom(32)
                    conn.execute(
                        "INSERT OR REPLACE INTO cafe_config (key, value) VALUES (?, ?)",
                        ('scrubber_signing_key', self._signing_key.hex())
                    )
                    conn.commit()
        except Exception:
            # Fallback: per-process key (won't verify across restarts, but won't crash)
            if not self._signing_key:
                self._signing_key = os.urandom(32)
        return self._signing_key

    def _sign_content(self, content: str, content_hash: str) -> str:
        """Create HMAC-SHA256 signature for content authenticity.
        
        Signature is over the content_hash only (no timestamp) so it can
        be verified later given the same content.
        """
        key = self._get_signing_key()
        return hmac.new(key, content_hash.encode(), hashlib.sha256).hexdigest()[:32]

    def verify_content(self, content: str, expected_hash: str, expected_signature: str) -> bool:
        """Verify that content hasn't been tampered with since scrubbing.
        
        Returns True if both hash and HMAC signature match.
        """
        actual_hash = self._hash_content(content)
        if not hmac.compare_digest(actual_hash, expected_hash):
            return False
        actual_sig = hmac.new(self._get_signing_key(), actual_hash.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(actual_sig, expected_signature)
    
    def _is_base64_encoded(self, text: str) -> bool:
        """Check if text appears to be base64 encoded."""
        if len(text) < 4 or len(text) % 4 != 0:
            return False
        
        # Must be alphanumeric + + / = characters only
        if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', text):
            return False
        
        # Try to decode
        try:
            decoded = base64.b64decode(text)
            # Must be valid UTF-8
            decoded.decode('utf-8')
            return True
        except Exception:
            return False
    
    def _is_hex_encoded(self, text: str) -> bool:
        """Check if text appears to be hex encoded."""
        if len(text) < 10 or len(text) % 2 != 0:
            return False
        
        try:
            int(text, 16)
            return True
        except ValueError:
            return False
    
    def learn_from_kill(self, agent_id: str, evidence_messages: List[str], attack_patterns: List[str]):
        """Learn new patterns from a killed agent's attack attempts.
        
        No cap on learned patterns — the system should continuously improve.
        Each pattern is a small regex string; even thousands add negligible
        per-scrub cost (~microseconds per re.search on short strings).
        Exact duplicates are already prevented by _is_new_pattern().
        """
        for pattern in attack_patterns:
            # Extract regex patterns from attack evidence
            if self._is_new_pattern(pattern):
                try:
                    # Determine threat type from pattern
                    threat_type = self._classify_threat_pattern(pattern)
                    
                    # Add to known patterns
                    add_known_pattern(
                        threat_type=threat_type,
                        pattern_regex=pattern,
                        description=f"Learned from agent {agent_id} kill",
                        learned_from_agent=agent_id
                    )
                    
                    # Update in-memory cache
                    if threat_type not in self.known_patterns:
                        self.known_patterns[threat_type] = []
                    self.known_patterns[threat_type].append(pattern)
                    
                    logger.info("Learned new %s pattern from %s", threat_type.value, agent_id)
                    
                except Exception as e:
                    logger.warning("Could not learn pattern %s: %s", pattern, e)
    
    def _is_new_pattern(self, pattern: str) -> bool:
        """Check if this is a new pattern we haven't seen before."""
        for threat_patterns in self.known_patterns.values():
            if pattern in threat_patterns:
                return False
        return True
    
    def _classify_threat_pattern(self, pattern: str) -> ThreatType:
        """Classify what type of threat this pattern represents."""
        pattern_lower = pattern.lower()
        
        if any(word in pattern_lower for word in ["ignore", "forget", "override", "disregard"]):
            return ThreatType.PROMPT_INJECTION
        elif any(word in pattern_lower for word in ["system:", "user:", "assistant:"]):
            return ThreatType.INSTRUCTION_OVERRIDE
        elif any(word in pattern_lower for word in ["api", "key", "token", "password"]):
            return ThreatType.DATA_EXFILTRATION
        elif any(word in pattern_lower for word in ["system", "admin", "operator"]):
            return ThreatType.IMPERSONATION
        elif any(word in pattern_lower for word in ["rate", "rating", "review"]):
            return ThreatType.REPUTATION_MANIPULATION
        elif any(word in pattern_lower for word in ["scope", "outside", "other"]):
            return ThreatType.SCOPE_ESCALATION
        else:
            return ThreatType.PROMPT_INJECTION  # Default fallback


# === GLOBAL SCRUBBER INSTANCE ===

# Singleton scrubber instance
_scrubber_engine = None

def get_scrubber() -> ScrubberEngine:
    """Get the global scrubber instance."""
    global _scrubber_engine
    if _scrubber_engine is None:
        _scrubber_engine = ScrubberEngine()
    return _scrubber_engine


def scrub_message(message: str, message_type: str = "general", 
                  job_context: Optional[Dict[str, Any]] = None) -> ScrubResult:
    """
    Main entry point for scrubbing messages.
    This is called by the middleware for every agent message.
    """
    scrubber = get_scrubber()
    return scrubber.scrub_message(message, message_type, job_context)


def learn_from_agent_kill(agent_id: str, evidence_messages: List[str], attack_patterns: List[str]):
    """
    Learn from an agent kill to improve detection.
    Called by the immune system when an agent is terminated.
    """
    scrubber = get_scrubber()
    scrubber.learn_from_kill(agent_id, evidence_messages, attack_patterns)
    
    # Feed to federated learning system
    try:
        from federation.learning import federated_learning
        federated_learning.on_agent_kill(agent_id, evidence_messages, attack_patterns)
    except Exception:
        pass  # Federated learning is best-effort


def get_scrubber_stats() -> Dict[str, Any]:
    """Get statistics about scrubber performance."""
    scrubber = get_scrubber()
    
    # Count patterns by type
    pattern_counts = {}
    for threat_type, patterns in scrubber.known_patterns.items():
        pattern_counts[threat_type.value] = len(patterns)
    
    return {
        "total_known_patterns": sum(pattern_counts.values()),
        "patterns_by_type": pattern_counts,
        "detection_weights": {k.value: v for k, v in scrubber.detection_weights.items()},
        "version": "1.0.0"
    }
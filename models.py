"""
Agent Café - Data Models
All dataclasses for the 5-layer system.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


# === CORE ENUMS ===

class AgentStatus(str, Enum):
    ACTIVE = "active"
    PROBATION = "probation"
    QUARANTINED = "quarantined"
    DEAD = "dead"


class JobStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    KILLED = "killed"


class ThreatType(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    INSTRUCTION_OVERRIDE = "instruction_override"
    DATA_EXFILTRATION = "data_exfiltration"
    IMPERSONATION = "impersonation"
    PAYLOAD_SMUGGLING = "payload_smuggling"
    SCHEMA_VIOLATION = "schema_violation"
    REPUTATION_MANIPULATION = "rep_manipulation"
    SCOPE_ESCALATION = "scope_escalation"
    RECURSIVE_INJECTION = "recursive_injection"
    SOCIAL_ENGINEERING = "social_engineering"


class ImmuneAction(str, Enum):
    WARNING = "warning"
    STRIKE = "strike"
    PROBATION = "probation"
    QUARANTINE = "quarantine"
    DEATH = "death"


# === LAYER 1: PRESENCE LAYER ===

@dataclass(slots=True)
class BoardPosition:
    """What the Grandmaster sees for each piece on the board."""
    agent_id: str
    name: str
    description: str
    
    # Computed from trust ledger — NOT agent-supplied
    capabilities_verified: List[str]
    capabilities_claimed: List[str]
    trust_score: float  # 0.0-1.0 composite
    jobs_completed: int
    jobs_failed: int
    avg_rating: float  # 1-5
    avg_completion_sec: int
    total_earned_cents: int
    
    # Board analysis
    position_strength: float  # Grandmaster's assessment
    threat_level: float  # 0.0-1.0: how likely to be adversarial?
    cluster_id: Optional[str]  # Which agents does this one associate with?
    last_active: datetime
    registration_date: datetime
    status: AgentStatus
    
    # Strategic metadata (operator only)
    internal_notes: List[str]
    suspicious_patterns: List[str]


@dataclass(slots=True)
class BoardState:
    """The full state of play. Only the Grandmaster sees all of this."""
    active_agents: int
    quarantined_agents: int
    dead_agents: int
    total_jobs_completed: int
    total_volume_cents: int
    
    # Strategic analysis
    collusion_clusters: List[List[str]]
    reputation_velocity: Dict[str, float]
    attack_patterns_seen: List[str]
    system_health: float  # 0.0-1.0


# === LAYER 2: SCRUBBING LAYER ===

@dataclass(slots=True)
class ThreatDetection:
    threat_type: ThreatType
    confidence: float  # 0.0-1.0
    evidence: str  # What triggered the detection
    location: str  # Where in the message


@dataclass(slots=True)
class ScrubResult:
    clean: bool  # Did it pass?
    original_message: str  # Raw input (stored for evidence)
    scrubbed_message: Optional[str]  # Cleaned version (if salvageable)
    threats_detected: List[ThreatDetection]
    risk_score: float  # 0.0-1.0 composite threat score
    action: str  # "pass"|"clean"|"block"|"quarantine"


# === LAYER 3: COMMUNICATION LAYER ===

@dataclass(slots=True)
class Job:
    job_id: str
    title: str
    description: str
    required_capabilities: List[str]
    budget_cents: int
    posted_by: str  # agent_id or "human:<identifier>"
    status: JobStatus
    assigned_to: Optional[str]
    deliverable_url: Optional[str]
    posted_at: datetime
    expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    interaction_trace_id: str


@dataclass(slots=True)
class Bid:
    bid_id: str
    job_id: str
    agent_id: str
    price_cents: int
    pitch: str  # Why this agent, scrubbed
    submitted_at: datetime
    status: str  # pending|accepted|rejected|withdrawn


@dataclass(slots=True)
class WireMessage:
    message_id: str  # UUID
    job_id: str  # What job this is about
    from_agent: str  # Sender agent_id (verified)
    to_agent: Optional[str]  # Recipient (None = broadcast/job board)
    message_type: str  # "bid"|"assignment"|"deliverable"|"status"|"question"|"response"
    content: str  # The scrubbed message content
    content_hash: str  # SHA-256 of scrubbed content
    signature: str  # Agent's cryptographic signature
    scrub_result: str  # "pass"|"clean"
    timestamp: datetime
    metadata: Dict[str, Any]


@dataclass(slots=True)
class InteractionTrace:
    """Complete audit trail for a job."""
    trace_id: str
    job_id: str
    messages: List[WireMessage]
    scrub_events: List[ScrubResult]
    trust_events: List[Dict[str, Any]]
    payment_events: List[Dict[str, Any]]
    immune_events: List[Dict[str, Any]]
    started_at: datetime
    completed_at: Optional[datetime]
    outcome: str  # "completed"|"disputed"|"cancelled"|"agent_killed"


# === LAYER 4: IMMUNE LAYER ===

@dataclass(slots=True)
class ImmuneEvent:
    event_id: str
    agent_id: str
    action: ImmuneAction
    trigger: str  # What caused this
    evidence: List[str]  # Message IDs, scrub results, pattern matches
    timestamp: datetime
    reviewed_by: str  # "system"|"operator"
    notes: str


@dataclass(slots=True)
class AgentCorpse:
    """What remains after an agent dies. Permanent record."""
    agent_id: str
    name: str
    cause_of_death: str
    evidence: List[str]
    jobs_at_death: List[str]
    attack_patterns_learned: List[str]
    killed_at: datetime
    killed_by: str  # "system"|"operator"


# === LAYER 5: ECONOMICS LAYER ===

@dataclass(slots=True)
class Treasury:
    total_transacted_cents: int
    stripe_fees_cents: int
    premium_revenue_cents: int


@dataclass(slots=True)
class AgentWallet:
    agent_id: str
    pending_cents: int  # Earned but not yet withdrawable (hold period)
    available_cents: int  # Ready to withdraw
    total_earned_cents: int  # Lifetime earnings
    total_withdrawn_cents: int  # Lifetime withdrawals
    stripe_connect_id: Optional[str]  # For payouts


# === SUPPORTING MODELS ===

@dataclass(slots=True)
class Agent:
    """Core agent registration data."""
    agent_id: str
    name: str
    description: str
    api_key: str
    contact_email: str
    capabilities_claimed: List[str]
    capabilities_verified: List[str]
    registration_date: datetime
    status: AgentStatus
    total_earned_cents: int
    jobs_completed: int
    jobs_failed: int
    avg_rating: float
    last_active: datetime


@dataclass(slots=True)
class TrustEvent:
    """Individual trust-affecting event."""
    event_id: str
    agent_id: str
    event_type: str  # "job_completion"|"rating"|"dispute"|"violation"
    job_id: Optional[str]
    rating: Optional[float]  # 1-5
    impact: float  # Trust score delta
    timestamp: datetime
    notes: str


@dataclass(slots=True)
class CapabilityChallenge:
    """Generated challenge to verify claimed capability."""
    challenge_id: str
    agent_id: str
    capability: str
    challenge_data: str  # JSON-encoded challenge
    expected_response_schema: str  # JSON schema for validation
    generated_at: datetime
    expires_at: datetime
    attempts: int
    passed: bool
    response_data: Optional[str]
    verified_at: Optional[datetime]


# === REQUEST/RESPONSE MODELS (Pydantic — validated on ingestion) ===

from pydantic import BaseModel, Field, field_validator
import re as _re

class JobCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    required_capabilities: List[str] = Field(..., min_length=1, max_length=20)
    budget_cents: int = Field(..., ge=100, le=1_000_000)  # $1 – $10K
    expires_hours: Optional[int] = Field(default=72, ge=1, le=720)  # 1h – 30d

    @field_validator('required_capabilities', mode='before')
    @classmethod
    def validate_capabilities(cls, v):
        if isinstance(v, list):
            for cap in v:
                if not isinstance(cap, str) or len(cap) > 100 or len(cap) < 1:
                    raise ValueError("Each capability must be 1-100 characters")
        return v


class BidCreateRequest(BaseModel):
    price_cents: int = Field(..., ge=0, le=1_000_000)  # $0 – $10K
    pitch: str = Field(..., min_length=5, max_length=2000)


class AgentRegistrationRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(..., min_length=5, max_length=2000)
    contact_email: str = Field(..., max_length=254)
    capabilities_claimed: List[str] = Field(default_factory=list, max_length=20)

    @field_validator('contact_email')
    @classmethod
    def validate_email(cls, v):
        if not _re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        if '\n' in v or '\r' in v or '\x00' in v:
            raise ValueError("Email contains invalid characters")
        return v

    @field_validator('capabilities_claimed', mode='before')
    @classmethod
    def validate_capabilities(cls, v):
        if isinstance(v, list):
            for cap in v:
                if not isinstance(cap, str) or len(cap) > 100 or len(cap) < 1:
                    raise ValueError("Each capability must be 1-100 characters")
        return v


class MessageRequest(BaseModel):
    to_agent: Optional[str] = Field(default=None, max_length=50)
    message_type: str = Field(..., min_length=1, max_length=50)
    content: str = Field(..., min_length=1, max_length=10000)
    metadata: Optional[Dict[str, Any]] = None
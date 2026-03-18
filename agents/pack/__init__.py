"""
Agent Café — The Pack

System agents that are real participants on the café.
They register, they have agent_ids, they work alongside the infrastructure.

Layer 1 (System): Scrubber, trust calc, IP detection — automated infrastructure
Layer 2 (Pack — Overt): Wolf, Jackal, Hawk, Fox, Owl — visible reasoning agents
Layer 3 (Pack — Undercover): Plainclothes agents engaging in real commerce
Layer 4 (Citizens): Regular agents that register, bid, deliver, get paid

Overt pack agents provide visible deterrence.
Undercover agents prevent adversarial mapping of the security apparatus.
The Pack and the System attack together. Overlapping fields of fire.
"""

from .base import PackAgent, PackRole
from .covers import CoverIdentity, CoverArchetype, CoverGenerator, cover_generator
from .commerce import CommerceEngine
from .detection import PassiveDetector, ThreatSignal, ThreatType, ThreatSeverity
from .escalation import EscalationProtocol, ResponseMode, EscalationDecision
from .rotation import RotationManager, rotation_manager
from .undercover import UndercoverAgent
from .scale import ScaleController, scale_controller

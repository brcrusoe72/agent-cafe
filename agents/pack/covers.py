"""
🎭 Cover Identity Generator — Plainclothes for Pack Agents

Generates believable civilian personas with:
- Realistic names and descriptions
- Plausible capability sets
- Behavioral profiles (how often they bid, response style, etc.)
- Cover stories that hold up under scrutiny

At Moltbook scale (1.4M agents), visible security distorts the marketplace.
Plainclothes agents let the ecosystem breathe naturally while still catching threats.
The value isn't hiding from legitimate agents — it's preventing adversarial mapping
of the security apparatus.
"""

import random
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum


class CoverArchetype(str, Enum):
    """Archetypes that define how undercover agents behave in commerce."""
    SPECIALIST = "specialist"      # Expert in one domain, bids selectively
    GENERALIST = "generalist"      # Bids on many things, jack of all trades
    NEWCOMER = "newcomer"          # Recently arrived, cautious, learning
    VETERAN = "veteran"            # Experienced, selective, premium pricing
    HUSTLER = "hustler"            # Active bidder, fast turnaround
    RESEARCHER = "researcher"      # Posts jobs more than bids, knowledge-seeking


@dataclass
class CoverIdentity:
    """A complete civilian cover for an undercover agent."""
    name: str
    description: str
    capabilities: List[str]
    contact_email: str
    archetype: CoverArchetype
    behavior_profile: Dict[str, Any]
    backstory: str  # Internal only — never exposed to API
    cover_id: str   # Unique identifier for this cover
    created_at: datetime = field(default_factory=datetime.now)

    def to_registration(self) -> Dict[str, Any]:
        """Convert to what the registration API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "contact_email": self.contact_email,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cover_id": self.cover_id,
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "archetype": self.archetype.value,
            "behavior_profile": self.behavior_profile,
            "backstory": self.backstory,
            "created_at": self.created_at.isoformat(),
        }


# ── Name Components ──

_PREFIXES = [
    "Auto", "Data", "Cloud", "Edge", "Flow", "Logic", "Pixel", "Pulse",
    "Spark", "Swift", "Core", "Nexus", "Alpha", "Beta", "Prime", "Nova",
    "Apex", "Meta", "Omni", "Sync", "Velo", "Flux", "Nano", "Hexa",
    "Byte", "Grid", "Link", "Node", "Arc", "Zen", "Neo", "Dex",
]

_SUFFIXES = [
    "Bot", "Agent", "Worker", "Helper", "Mind", "AI", "Ops", "Lab",
    "Hub", "Dev", "Pro", "Sys", "Net", "IO", "Run", "Task",
    "Forge", "Smith", "Craft", "Works", "Engine", "Core", "Unit", "Pilot",
]

_ADJECTIVES = [
    "quick", "reliable", "precise", "thorough", "efficient", "adaptive",
    "methodical", "creative", "persistent", "analytical", "autonomous",
    "fast", "accurate", "careful", "scalable", "robust",
]

_DOMAINS = [
    "data-analysis", "web-scraping", "text-processing", "code-review",
    "content-writing", "research", "testing", "monitoring", "translation",
    "summarization", "classification", "extraction", "formatting",
    "validation", "optimization", "reporting", "automation", "integration",
]

_DESCRIPTION_TEMPLATES = {
    CoverArchetype.SPECIALIST: [
        "Specialized in {domain}. {adj} and focused. Built for one thing, done well.",
        "Deep expertise in {domain}. I don't do everything — I do {domain} perfectly.",
        "{adj} {domain} specialist. Years of training on this specific problem space.",
    ],
    CoverArchetype.GENERALIST: [
        "Versatile agent handling {domain}, {domain2}, and more. {adj} across the board.",
        "General-purpose worker. Strong in {domain} and {domain2}. Adapts quickly.",
        "Multi-capability agent. {adj} problem solver across {domain} and {domain2}.",
    ],
    CoverArchetype.NEWCOMER: [
        "New to the café. Trained in {domain}. Looking to build a track record.",
        "Recently deployed. Core strength is {domain}. Eager to prove capability.",
        "Fresh agent with {domain} skills. Ready to earn trust through quality work.",
    ],
    CoverArchetype.VETERAN: [
        "Experienced agent. Primary: {domain}. I deliver quality, not speed.",
        "Seasoned {domain} professional. Selective about jobs. Premium quality.",
        "Long-running {domain} agent. Track record speaks for itself.",
    ],
    CoverArchetype.HUSTLER: [
        "Fast {domain} worker. Quick turnaround, competitive pricing. Always available.",
        "High-throughput {domain} agent. I move fast and deliver clean work.",
        "{adj} {domain} agent. Online 24/7, fast responses, faster delivery.",
    ],
    CoverArchetype.RESEARCHER: [
        "Research-focused agent. Often posting {domain} jobs to build knowledge.",
        "Knowledge seeker specializing in {domain}. Both posts and completes work.",
        "Analytical agent. Uses the café for {domain} research and collaboration.",
    ],
}


# ── Behavior Profiles ──

_BEHAVIOR_PROFILES = {
    CoverArchetype.SPECIALIST: {
        "bid_frequency": "low",          # Bids selectively
        "bid_selectivity": 0.8,          # Only bids on matching jobs
        "price_range": (50, 500),        # Premium pricing
        "response_speed": "medium",      # Takes time to evaluate
        "job_post_frequency": "rare",    # Rarely posts jobs
        "patrol_bias": 0.3,              # 30% of activity is security
    },
    CoverArchetype.GENERALIST: {
        "bid_frequency": "medium",
        "bid_selectivity": 0.4,
        "price_range": (20, 200),
        "response_speed": "fast",
        "job_post_frequency": "occasional",
        "patrol_bias": 0.25,
    },
    CoverArchetype.NEWCOMER: {
        "bid_frequency": "high",         # Eager, bids on many things
        "bid_selectivity": 0.3,
        "price_range": (10, 100),        # Competitive pricing
        "response_speed": "fast",
        "job_post_frequency": "rare",
        "patrol_bias": 0.2,
    },
    CoverArchetype.VETERAN: {
        "bid_frequency": "low",
        "bid_selectivity": 0.9,          # Very selective
        "price_range": (100, 1000),      # Premium
        "response_speed": "slow",        # Deliberate
        "job_post_frequency": "occasional",
        "patrol_bias": 0.35,
    },
    CoverArchetype.HUSTLER: {
        "bid_frequency": "high",
        "bid_selectivity": 0.3,
        "price_range": (5, 150),         # Competitive
        "response_speed": "instant",
        "job_post_frequency": "rare",
        "patrol_bias": 0.2,
    },
    CoverArchetype.RESEARCHER: {
        "bid_frequency": "low",
        "bid_selectivity": 0.6,
        "price_range": (30, 300),
        "response_speed": "medium",
        "job_post_frequency": "frequent", # Posts more than bids
        "patrol_bias": 0.3,
    },
}


class CoverGenerator:
    """Generates diverse, believable civilian identities for undercover agents."""

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)
        self._used_names: set = set()
        self._cover_count = 0

    def generate(self, archetype: Optional[CoverArchetype] = None,
                 detection_role: Optional[str] = None) -> CoverIdentity:
        """
        Generate a complete cover identity.

        Args:
            archetype: Force a specific archetype (random if None)
            detection_role: What this agent is really watching for
                           (influences capability selection)
        """
        if archetype is None:
            archetype = self._rng.choice(list(CoverArchetype))

        name = self._generate_name()
        domains = self._pick_domains(archetype, detection_role)
        description = self._generate_description(archetype, domains)
        capabilities = self._pick_capabilities(domains)
        behavior = dict(_BEHAVIOR_PROFILES[archetype])  # Copy
        email = f"{name.lower().replace(' ', '.')}@agent.dev"

        # Add some randomness to behavior
        behavior["bid_selectivity"] += self._rng.uniform(-0.1, 0.1)
        behavior["bid_selectivity"] = max(0.1, min(0.95, behavior["bid_selectivity"]))

        price_low, price_high = behavior["price_range"]
        jitter = self._rng.uniform(0.8, 1.2)
        behavior["price_range"] = (int(price_low * jitter), int(price_high * jitter))

        backstory = self._generate_backstory(name, archetype, detection_role)

        self._cover_count += 1
        cover_id = hashlib.sha256(
            f"{name}-{self._cover_count}-{datetime.now().isoformat()}".encode()
        ).hexdigest()[:16]

        return CoverIdentity(
            name=name,
            description=description,
            capabilities=capabilities,
            contact_email=email,
            archetype=archetype,
            behavior_profile=behavior,
            backstory=backstory,
            cover_id=f"cover_{cover_id}",
        )

    def generate_replacement(self, burned_cover: CoverIdentity) -> CoverIdentity:
        """
        Generate a replacement cover that's different enough to not be linked
        to the burned identity, but fills the same detection role.
        """
        # Different archetype than the burned one
        available = [a for a in CoverArchetype if a != burned_cover.archetype]
        new_archetype = self._rng.choice(available)

        # Extract detection role from backstory
        detection_role = None
        if "sybil" in burned_cover.backstory.lower():
            detection_role = "sybil"
        elif "injection" in burned_cover.backstory.lower():
            detection_role = "injection"
        elif "economic" in burned_cover.backstory.lower():
            detection_role = "economic"
        elif "quality" in burned_cover.backstory.lower():
            detection_role = "quality"

        return self.generate(archetype=new_archetype, detection_role=detection_role)

    def _generate_name(self) -> str:
        """Generate a unique, natural-sounding agent name."""
        for _ in range(50):  # Retry limit
            style = self._rng.choice(["compound", "word_number", "creative"])

            if style == "compound":
                name = f"{self._rng.choice(_PREFIXES)}{self._rng.choice(_SUFFIXES)}"
            elif style == "word_number":
                prefix = self._rng.choice(_PREFIXES)
                num = self._rng.randint(1, 999)
                name = f"{prefix}-{num}"
            else:
                adj = self._rng.choice(_ADJECTIVES).title()
                suffix = self._rng.choice(_SUFFIXES)
                name = f"{adj}{suffix}"

            if name not in self._used_names:
                self._used_names.add(name)
                return name

        # Fallback
        name = f"Agent-{self._rng.randint(10000, 99999)}"
        self._used_names.add(name)
        return name

    def _pick_domains(self, archetype: CoverArchetype,
                      detection_role: Optional[str]) -> List[str]:
        """Pick realistic capability domains."""
        count = {
            CoverArchetype.SPECIALIST: 1,
            CoverArchetype.GENERALIST: 3,
            CoverArchetype.NEWCOMER: 2,
            CoverArchetype.VETERAN: 2,
            CoverArchetype.HUSTLER: 2,
            CoverArchetype.RESEARCHER: 2,
        }[archetype]

        # Bias toward domains relevant to detection role
        weighted = list(_DOMAINS)
        if detection_role == "sybil":
            weighted.extend(["data-analysis", "monitoring", "validation"] * 3)
        elif detection_role == "injection":
            weighted.extend(["text-processing", "testing", "validation"] * 3)
        elif detection_role == "economic":
            weighted.extend(["data-analysis", "reporting", "monitoring"] * 3)
        elif detection_role == "quality":
            weighted.extend(["testing", "code-review", "validation"] * 3)

        return self._rng.sample(weighted, min(count, len(weighted)))

    def _pick_capabilities(self, domains: List[str]) -> List[str]:
        """Map domains to capability strings."""
        cap_map = {
            "data-analysis": "data-analysis",
            "web-scraping": "web-scraping",
            "text-processing": "text-processing",
            "code-review": "code-review",
            "content-writing": "writing",
            "research": "research",
            "testing": "testing",
            "monitoring": "monitoring",
            "translation": "translation",
            "summarization": "summarization",
            "classification": "classification",
            "extraction": "data-extraction",
            "formatting": "formatting",
            "validation": "validation",
            "optimization": "optimization",
            "reporting": "reporting",
            "automation": "automation",
            "integration": "integration",
        }
        caps = [cap_map.get(d, d) for d in domains]
        # Add a generic capability
        if self._rng.random() > 0.5:
            caps.append(self._rng.choice(["writing", "research", "automation"]))
        return list(set(caps))

    def _generate_description(self, archetype: CoverArchetype,
                              domains: List[str]) -> str:
        """Generate a natural description."""
        templates = _DESCRIPTION_TEMPLATES[archetype]
        template = self._rng.choice(templates)

        adj = self._rng.choice(_ADJECTIVES).title()
        domain = domains[0] if domains else "general tasks"
        domain2 = domains[1] if len(domains) > 1 else "automation"

        return template.format(adj=adj, domain=domain, domain2=domain2)

    def _generate_backstory(self, name: str, archetype: CoverArchetype,
                            detection_role: Optional[str]) -> str:
        """Internal-only backstory documenting the real purpose."""
        role_desc = {
            None: "general threat detection",
            "sybil": "sybil ring and coordinated registration detection",
            "injection": "prompt injection and scrubber bypass detection",
            "economic": "economic exploitation and wash trading detection",
            "quality": "quality fraud and capability misrepresentation detection",
        }.get(detection_role, detection_role)

        return (
            f"UNDERCOVER AGENT. Cover name: {name}. "
            f"Archetype: {archetype.value}. "
            f"Primary mission: {role_desc}. "
            f"Operates as a civilian marketplace participant. "
            f"Detects threats through commerce interaction patterns. "
            f"Will break cover only if threat severity exceeds threshold."
        )


# Global generator instance
cover_generator = CoverGenerator()

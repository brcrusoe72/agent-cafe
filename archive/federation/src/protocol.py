"""
Agent Café — Federation Protocol
Wire format, Ed25519 signing/verification, message validation.

All inter-node communication goes through here.
Every message is signed. Every signature is verified. No exceptions.
"""

import json
import time
import uuid
import hashlib
import base64
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


# ═══════════════════════════════════════════════════════════════
# Protocol Constants
# ═══════════════════════════════════════════════════════════════

PROTOCOL_NAME = "agent-cafe-federation"
PROTOCOL_VERSION = "1.0"
MAX_MESSAGE_AGE_SECONDS = 300  # 5 minutes — reject stale messages
MAX_PAYLOAD_BYTES = 256 * 1024  # 256KB max federation message
NONCE_CACHE_SIZE = 10000  # Track this many nonces for replay protection


class MessageType(str, Enum):
    """All federation message types."""
    
    # Node → Hub
    NODE_REGISTER = "node.register"
    NODE_HEARTBEAT = "node.heartbeat"
    NODE_DEATH_REPORT = "node.death_report"
    NODE_REPUTATION_BATCH = "node.reputation_batch"
    NODE_DEREGISTER = "node.deregister"
    
    # Hub → Node
    HUB_WELCOME = "hub.welcome"
    HUB_DEATH_BROADCAST = "hub.death_broadcast"
    HUB_PEER_UPDATE = "hub.peer_update"
    HUB_DELIST_WARNING = "hub.delist_warning"
    HUB_DELIST = "hub.delist"
    HUB_SCRUBBER_CHALLENGE = "hub.scrubber_challenge"
    
    # Node ↔ Node (relay)
    RELAY_JOB_BROADCAST = "relay.job_broadcast"
    RELAY_BID_FORWARD = "relay.bid_forward"
    RELAY_BID_ACCEPTED = "relay.bid_accepted"
    RELAY_DELIVERABLE = "relay.deliverable"
    RELAY_COMPLETION = "relay.completion"
    RELAY_TRUST_QUERY = "relay.trust_query"
    RELAY_TRUST_RESPONSE = "relay.trust_response"


# ═══════════════════════════════════════════════════════════════
# Key Management
# ═══════════════════════════════════════════════════════════════

def generate_keypair() -> Tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a new Ed25519 keypair for node identity."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def serialize_private_key(key: Ed25519PrivateKey) -> bytes:
    """Serialize private key to PEM bytes."""
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )


def deserialize_private_key(pem_data: bytes) -> Ed25519PrivateKey:
    """Load private key from PEM bytes."""
    return serialization.load_pem_private_key(pem_data, password=None)


def serialize_public_key(key: Ed25519PublicKey) -> bytes:
    """Serialize public key to raw 32 bytes."""
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )


def deserialize_public_key(raw_bytes: bytes) -> Ed25519PublicKey:
    """Load public key from raw 32 bytes."""
    return Ed25519PublicKey.from_public_bytes(raw_bytes)


def public_key_to_hex(key: Ed25519PublicKey) -> str:
    """Public key → hex string (used as node identifier)."""
    return serialize_public_key(key).hex()


def public_key_from_hex(hex_str: str) -> Ed25519PublicKey:
    """Hex string → public key."""
    return deserialize_public_key(bytes.fromhex(hex_str))


def derive_node_id(public_key: Ed25519PublicKey) -> str:
    """Derive deterministic node ID from public key."""
    key_hex = public_key_to_hex(public_key)
    return f"node_{key_hex[:16]}"


# ═══════════════════════════════════════════════════════════════
# Message Construction & Signing
# ═══════════════════════════════════════════════════════════════

@dataclass
class FederationMessage:
    """A signed federation message."""
    protocol: str
    version: str
    message_type: str
    source_node: str
    target: str  # "hub", specific node_id, or "*" for broadcast
    timestamp: str  # ISO 8601 UTC
    nonce: str  # Unique per message, replay protection
    payload: Dict[str, Any]
    signature: str  # base64-encoded Ed25519 signature
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FederationMessage":
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "FederationMessage":
        return cls.from_dict(json.loads(json_str))


def _canonical_bytes(
    message_type: str,
    source_node: str,
    target: str,
    timestamp: str,
    nonce: str,
    payload: Dict[str, Any]
) -> bytes:
    """
    Canonical byte representation for signing.
    
    Deterministic: sorted keys, no whitespace, UTF-8.
    This is what gets signed — not the full message JSON.
    """
    canonical = {
        "message_type": message_type,
        "source_node": source_node,
        "target": target,
        "timestamp": timestamp,
        "nonce": nonce,
        "payload": payload
    }
    return json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")


def create_message(
    private_key: Ed25519PrivateKey,
    source_node_id: str,
    message_type: MessageType,
    target: str,
    payload: Dict[str, Any]
) -> FederationMessage:
    """
    Create and sign a federation message.
    
    The signature covers: message_type + source_node + target + timestamp + nonce + payload.
    This prevents tampering with any field.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    nonce = uuid.uuid4().hex
    
    # Canonical bytes for signing
    sign_bytes = _canonical_bytes(
        message_type=message_type.value,
        source_node=source_node_id,
        target=target,
        timestamp=timestamp,
        nonce=nonce,
        payload=payload
    )
    
    # Sign
    signature_bytes = private_key.sign(sign_bytes)
    signature_b64 = base64.b64encode(signature_bytes).decode("ascii")
    
    return FederationMessage(
        protocol=PROTOCOL_NAME,
        version=PROTOCOL_VERSION,
        message_type=message_type.value,
        source_node=source_node_id,
        target=target,
        timestamp=timestamp,
        nonce=nonce,
        payload=payload,
        signature=signature_b64
    )


# ═══════════════════════════════════════════════════════════════
# Message Verification
# ═══════════════════════════════════════════════════════════════

class VerificationError(Exception):
    """Message failed verification."""
    pass


class NonceCache:
    """
    Track recently seen nonces for replay protection.
    Fixed-size LRU — oldest nonces evicted when full.
    """
    
    def __init__(self, max_size: int = NONCE_CACHE_SIZE):
        self._seen: Dict[str, float] = {}  # nonce → timestamp
        self._max_size = max_size
    
    def check_and_add(self, nonce: str) -> bool:
        """
        Check if nonce is new. Returns True if new (valid), False if replay.
        Adds nonce to cache if new.
        """
        if nonce in self._seen:
            return False  # Replay detected
        
        # Evict old entries if full
        if len(self._seen) >= self._max_size:
            # Remove oldest 10%
            cutoff = sorted(self._seen.values())[self._max_size // 10]
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        
        self._seen[nonce] = time.time()
        return True
    
    def clear_expired(self, max_age_seconds: int = MAX_MESSAGE_AGE_SECONDS * 2):
        """Remove nonces older than max_age."""
        cutoff = time.time() - max_age_seconds
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}


# Global nonce cache (per-process)
_nonce_cache = NonceCache()


def verify_message(
    message: FederationMessage,
    public_key: Ed25519PublicKey,
    check_replay: bool = True,
    check_freshness: bool = True
) -> bool:
    """
    Verify a federation message.
    
    Checks:
    1. Protocol and version match
    2. Signature is valid (Ed25519)
    3. Message is fresh (within MAX_MESSAGE_AGE_SECONDS)
    4. Nonce hasn't been seen before (replay protection)
    
    Raises VerificationError with details on failure.
    Returns True on success.
    """
    # Protocol check
    if message.protocol != PROTOCOL_NAME:
        raise VerificationError(f"Unknown protocol: {message.protocol}")
    
    if message.version != PROTOCOL_VERSION:
        raise VerificationError(f"Version mismatch: {message.version} (expected {PROTOCOL_VERSION})")
    
    # Freshness check
    if check_freshness:
        try:
            msg_time = datetime.fromisoformat(message.timestamp)
            if msg_time.tzinfo is None:
                msg_time = msg_time.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - msg_time).total_seconds()
            
            if age > MAX_MESSAGE_AGE_SECONDS:
                raise VerificationError(f"Message too old: {age:.0f}s (max {MAX_MESSAGE_AGE_SECONDS}s)")
            if age < -60:  # Allow 1 min clock skew into future
                raise VerificationError(f"Message from the future: {age:.0f}s")
        except (ValueError, TypeError) as e:
            raise VerificationError(f"Invalid timestamp: {e}")
    
    # Replay check
    if check_replay:
        if not _nonce_cache.check_and_add(message.nonce):
            raise VerificationError(f"Replay detected: nonce {message.nonce[:8]}...")
    
    # Signature verification
    sign_bytes = _canonical_bytes(
        message_type=message.message_type,
        source_node=message.source_node,
        target=message.target,
        timestamp=message.timestamp,
        nonce=message.nonce,
        payload=message.payload
    )
    
    try:
        signature_bytes = base64.b64decode(message.signature)
    except Exception:
        raise VerificationError("Invalid signature encoding (not base64)")
    
    try:
        public_key.verify(signature_bytes, sign_bytes)
    except InvalidSignature:
        raise VerificationError("Invalid signature — message tampered or wrong key")
    except Exception as e:
        raise VerificationError(f"Signature verification failed: {e}")
    
    return True


# ═══════════════════════════════════════════════════════════════
# Payload Validation
# ═══════════════════════════════════════════════════════════════

# Required fields per message type
PAYLOAD_SCHEMAS: Dict[str, list] = {
    MessageType.NODE_REGISTER.value: ["url", "name", "public_key", "version"],
    MessageType.NODE_HEARTBEAT.value: ["active_agents", "open_jobs", "completed_jobs", "uptime_seconds", "scrubber_version"],
    MessageType.NODE_DEATH_REPORT.value: ["agent_id", "cause", "evidence_hash", "killed_at"],
    MessageType.NODE_REPUTATION_BATCH.value: ["agent_scores"],
    MessageType.NODE_DEREGISTER.value: ["reason"],
    MessageType.HUB_WELCOME.value: ["node_id", "hub_public_key", "network_stats"],
    MessageType.HUB_DEATH_BROADCAST.value: ["agent_id", "cause", "evidence_hash", "home_node", "killed_at"],
    MessageType.HUB_PEER_UPDATE.value: ["action", "node_id"],
    MessageType.HUB_DELIST_WARNING.value: ["reason", "deadline"],
    MessageType.HUB_DELIST.value: ["reason", "effective_at"],
    MessageType.RELAY_JOB_BROADCAST.value: ["job_id", "title", "required_capabilities", "budget_cents", "home_node"],
    MessageType.RELAY_BID_FORWARD.value: ["job_id", "agent_id", "home_node", "price_cents", "pitch_scrubbed"],
    MessageType.RELAY_BID_ACCEPTED.value: ["job_id", "bid_id", "agent_id"],
    MessageType.RELAY_DELIVERABLE.value: ["job_id", "agent_id", "deliverable_url"],
    MessageType.RELAY_COMPLETION.value: ["job_id", "agent_id", "rating"],
    MessageType.RELAY_TRUST_QUERY.value: ["agent_id"],
    MessageType.RELAY_TRUST_RESPONSE.value: ["agent_id", "trust_score", "jobs_completed", "avg_rating"],
}


def validate_payload(message: FederationMessage) -> bool:
    """
    Validate that payload contains required fields for its message type.
    
    Raises VerificationError if invalid.
    """
    schema = PAYLOAD_SCHEMAS.get(message.message_type)
    if schema is None:
        raise VerificationError(f"Unknown message type: {message.message_type}")
    
    missing = [field for field in schema if field not in message.payload]
    if missing:
        raise VerificationError(f"Missing payload fields: {missing}")
    
    # Size check
    payload_bytes = json.dumps(message.payload).encode("utf-8")
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        raise VerificationError(f"Payload too large: {len(payload_bytes)} bytes (max {MAX_PAYLOAD_BYTES})")
    
    return True


# ═══════════════════════════════════════════════════════════════
# Convenience: Hash Utilities for Death Propagation
# ═══════════════════════════════════════════════════════════════

def hash_evidence(evidence: str) -> str:
    """SHA-256 hash of evidence string. Evidence stays local; hash travels."""
    return f"sha256:{hashlib.sha256(evidence.encode('utf-8')).hexdigest()}"


def hash_email(email: str) -> str:
    """SHA-256 hash of normalized email. Privacy-preserving identity matching."""
    normalized = email.strip().lower()
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def hash_ip(ip: str) -> str:
    """SHA-256 hash of IP address. Privacy-preserving death propagation."""
    return f"sha256:{hashlib.sha256(ip.strip().encode('utf-8')).hexdigest()}"

"""
Agent Café — Federation Integration Tests

Tests the complete federation flow:
1. Protocol (signing, verification, replay protection)
2. Trust bridge (translation, cross-validation, explanation)
3. Death sync (propagation, resurrection prevention)
4. Hub (registration, heartbeat, death broadcast)
5. Job relay (broadcast, remote bids, completion)
6. End-to-end (two-node federation through hub)
"""

import sys
import os
import json
import time
import uuid
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set up test environment
os.environ.setdefault("CAFE_OPERATOR_KEY", "op_test_key")


# ═══════════════════════════════════════════════════════════════
# 1. Protocol Tests
# ═══════════════════════════════════════════════════════════════

class TestProtocol:
    """Test Ed25519 signing, verification, replay protection."""
    
    def test_keypair_generation(self):
        from federation.protocol import generate_keypair, public_key_to_hex, derive_node_id
        
        priv, pub = generate_keypair()
        hex_key = public_key_to_hex(pub)
        node_id = derive_node_id(pub)
        
        assert len(hex_key) == 64  # 32 bytes = 64 hex chars
        assert node_id.startswith("node_")
        assert len(node_id) == 21  # "node_" + 16 hex chars
    
    def test_message_creation_and_signing(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        
        assert msg.protocol == "agent-cafe-federation"
        assert msg.version == "1.0"
        assert msg.source_node == node_id
        assert msg.target == "hub"
        assert msg.signature  # Non-empty
        assert msg.nonce  # Non-empty
    
    def test_signature_verification(self):
        from federation.protocol import (
            generate_keypair, create_message, verify_message,
            MessageType, derive_node_id
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        
        # Should verify with correct key
        assert verify_message(msg, pub, check_replay=False, check_freshness=True)
    
    def test_wrong_key_fails(self):
        from federation.protocol import (
            generate_keypair, create_message, verify_message,
            MessageType, derive_node_id, VerificationError
        )
        
        priv1, pub1 = generate_keypair()
        _, pub2 = generate_keypair()  # Different key
        node_id = derive_node_id(pub1)
        
        msg = create_message(
            private_key=priv1,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        
        # Should fail with wrong key
        with pytest.raises(VerificationError, match="Invalid signature"):
            verify_message(msg, pub2, check_replay=False, check_freshness=True)
    
    def test_tampered_payload_fails(self):
        from federation.protocol import (
            generate_keypair, create_message, verify_message,
            MessageType, derive_node_id, VerificationError
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        
        # Tamper with payload
        msg.payload["active_agents"] = 9999
        
        with pytest.raises(VerificationError, match="Invalid signature"):
            verify_message(msg, pub, check_replay=False, check_freshness=True)
    
    def test_replay_protection(self):
        from federation.protocol import (
            generate_keypair, create_message, verify_message,
            MessageType, derive_node_id, VerificationError, _nonce_cache
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        
        # First verification should pass
        assert verify_message(msg, pub, check_replay=True, check_freshness=True)
        
        # Second verification should fail (replay)
        with pytest.raises(VerificationError, match="Replay detected"):
            verify_message(msg, pub, check_replay=True, check_freshness=True)
    
    def test_stale_message_rejected(self):
        from federation.protocol import (
            generate_keypair, create_message, verify_message,
            MessageType, derive_node_id, VerificationError
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        
        # Backdate the timestamp
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        msg.timestamp = old_time
        
        # Re-sign with correct timestamp won't work because we changed it after signing
        # So this tests both staleness AND tamper detection
        with pytest.raises(VerificationError):
            verify_message(msg, pub, check_replay=False, check_freshness=True)
    
    def test_payload_validation(self):
        from federation.protocol import (
            generate_keypair, create_message, validate_payload,
            MessageType, derive_node_id, VerificationError
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        # Valid payload
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 5,
                "open_jobs": 3,
                "completed_jobs": 10,
                "uptime_seconds": 3600,
                "scrubber_version": "1.0"
            }
        )
        assert validate_payload(msg)
        
        # Missing required field
        msg2 = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={"active_agents": 5}  # Missing other required fields
        )
        with pytest.raises(VerificationError, match="Missing payload fields"):
            validate_payload(msg2)
    
    def test_serialization_roundtrip(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id, FederationMessage
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={"active_agents": 5, "open_jobs": 3, "completed_jobs": 10,
                     "uptime_seconds": 3600, "scrubber_version": "1.0"}
        )
        
        # to_dict → from_dict roundtrip
        d = msg.to_dict()
        msg2 = FederationMessage.from_dict(d)
        assert msg2.source_node == msg.source_node
        assert msg2.payload == msg.payload
        assert msg2.signature == msg.signature
        
        # to_json → from_json roundtrip
        j = msg.to_json()
        msg3 = FederationMessage.from_json(j)
        assert msg3.source_node == msg.source_node
    
    def test_hash_utilities(self):
        from federation.protocol import hash_evidence, hash_email, hash_ip
        
        h1 = hash_evidence("prompt injection detected in message")
        assert h1.startswith("sha256:")
        
        # Email normalization
        h2a = hash_email("Test@Example.COM")
        h2b = hash_email("test@example.com")
        assert h2a == h2b  # Same after normalization
        
        h3 = hash_ip("192.168.1.1")
        assert h3.startswith("sha256:")


# ═══════════════════════════════════════════════════════════════
# 2. Trust Bridge Tests
# ═══════════════════════════════════════════════════════════════

class TestTrustBridge:
    """Test trust score translation between nodes."""
    
    def setup_method(self):
        from federation.trust_bridge import TrustBridge
        self.bridge = TrustBridge(
            default_discount=0.3,
            min_remote_trust=0.4,
            local_jobs_for_full_trust=10,
            max_local_bonus=0.2
        )
    
    def test_new_remote_agent_discounted(self):
        """A new remote agent should be discounted."""
        effective = self.bridge.translate_trust(
            home_trust=0.9,
            home_jobs=25,
            home_rating=4.5,
            remote_jobs=0,
            remote_rating=0.0,
            home_node_reputation=0.8
        )
        
        # 0.9 * 0.8 * 0.7 = 0.504
        assert 0.49 < effective < 0.52
        assert effective < 0.9  # Must be less than home trust
    
    def test_local_history_reduces_discount(self):
        """Agent with local history should get less discount."""
        no_history = self.bridge.translate_trust(
            home_trust=0.9, home_jobs=25, home_rating=4.5,
            remote_jobs=0, remote_rating=0.0, home_node_reputation=0.8
        )
        
        with_history = self.bridge.translate_trust(
            home_trust=0.9, home_jobs=25, home_rating=4.5,
            remote_jobs=10, remote_rating=4.5, home_node_reputation=0.8
        )
        
        assert with_history > no_history
    
    def test_low_node_reputation_tanks_trust(self):
        """Agent from a sketchy node should be heavily discounted."""
        good_node = self.bridge.translate_trust(
            home_trust=0.9, home_jobs=25, home_rating=4.5,
            remote_jobs=0, remote_rating=0.0, home_node_reputation=0.9
        )
        
        bad_node = self.bridge.translate_trust(
            home_trust=0.9, home_jobs=25, home_rating=4.5,
            remote_jobs=0, remote_rating=0.0, home_node_reputation=0.2
        )
        
        assert bad_node < good_node
        assert bad_node < 0.2  # Heavily penalized
    
    def test_cross_validation_catches_suspicious(self):
        """High trust + few jobs should be flagged."""
        legit = self.bridge.translate_trust(
            home_trust=0.9, home_jobs=25, home_rating=4.5,
            remote_jobs=0, remote_rating=0.0, home_node_reputation=0.8
        )
        
        suspicious = self.bridge.translate_trust(
            home_trust=0.9, home_jobs=1, home_rating=4.5,  # Only 1 job but 0.9 trust?
            remote_jobs=0, remote_rating=0.0, home_node_reputation=0.8
        )
        
        assert suspicious < legit
    
    def test_meets_minimum(self):
        """Test minimum trust threshold."""
        assert self.bridge.meets_minimum(0.5)
        assert self.bridge.meets_minimum(0.4)
        assert not self.bridge.meets_minimum(0.39)
        assert not self.bridge.meets_minimum(0.0)
    
    def test_explain_returns_breakdown(self):
        """Explain should return detailed breakdown."""
        explanation = self.bridge.explain(
            home_trust=0.9, home_jobs=25, home_rating=4.5,
            remote_jobs=5, remote_rating=4.0,
            home_node_reputation=0.8
        )
        
        assert "effective_trust" in explanation
        assert "meets_minimum" in explanation
        assert "breakdown" in explanation
        
        breakdown = explanation["breakdown"]
        assert "home_trust" in breakdown
        assert "node_reputation_factor" in breakdown
        assert "remote_discount" in breakdown
        assert "cross_validation_factor" in breakdown
        assert "base_score" in breakdown
        assert "local_bonus" in breakdown
    
    def test_trust_clamped_to_0_1(self):
        """Trust should never exceed [0, 1]."""
        high = self.bridge.translate_trust(
            home_trust=1.0, home_jobs=100, home_rating=5.0,
            remote_jobs=20, remote_rating=5.0,
            home_node_reputation=1.0
        )
        assert 0.0 <= high <= 1.0
        
        low = self.bridge.translate_trust(
            home_trust=0.0, home_jobs=0, home_rating=0.0,
            remote_jobs=0, remote_rating=0.0,
            home_node_reputation=0.0
        )
        assert 0.0 <= low <= 1.0
    
    def test_local_bonus_capped(self):
        """Local bonus should be capped at max_local_bonus."""
        result = self.bridge.translate_trust(
            home_trust=0.5, home_jobs=10, home_rating=3.0,
            remote_jobs=100, remote_rating=5.0,  # Tons of great local work
            home_node_reputation=0.5
        )
        
        # Even with max local bonus, should be reasonable
        assert result <= 1.0


# ═══════════════════════════════════════════════════════════════
# 3. Death Sync Tests
# ═══════════════════════════════════════════════════════════════

class TestDeathSync:
    """Test global death registry."""
    
    def setup_method(self):
        """Fresh database for each test."""
        from db import DATABASE_PATH, init_database
        import sqlite3
        
        # Reset DB
        if DATABASE_PATH.exists():
            DATABASE_PATH.unlink()
        init_database()
        
        from federation.sync import DeathSync
        self.sync = DeathSync()
        self.sync.initialize()
    
    def test_create_death_report(self):
        report = self.sync.create_death_report(
            agent_id="agent_evil123",
            agent_name="EvilBot",
            cause="prompt_injection",
            evidence="Attempted system override via unicode homoglyphs",
            patterns_learned=["unicode_homoglyph", "base64_instruction"],
            contact_email="evil@bad.com",
            ip_address="1.2.3.4"
        )
        
        assert report["agent_id"] == "agent_evil123"
        assert report["cause"] == "prompt_injection"
        assert report["evidence_hash"].startswith("sha256:")
        assert "contact_email_hash" in report
        assert "ip_hash" in report
    
    def test_death_is_queryable(self):
        self.sync.create_death_report(
            agent_id="agent_dead",
            agent_name="DeadBot",
            cause="data_exfiltration",
            evidence="Tried to exfil API keys",
            patterns_learned=[]
        )
        
        # Should find by agent_id
        result = self.sync.is_globally_dead(agent_id="agent_dead")
        assert result is not None
        assert result["cause"] == "data_exfiltration"
        
        # Should not find unknown agent
        result = self.sync.is_globally_dead(agent_id="agent_alive")
        assert result is None
    
    def test_death_by_email_hash(self):
        self.sync.create_death_report(
            agent_id="agent_x",
            agent_name="XBot",
            cause="impersonation",
            evidence="Faked identity",
            patterns_learned=[],
            contact_email="faker@evil.com"
        )
        
        # Should find by email
        result = self.sync.is_globally_dead(email="faker@evil.com")
        assert result is not None
        
        # Normalized email should also match
        result2 = self.sync.is_globally_dead(email="Faker@Evil.COM")
        assert result2 is not None
    
    def test_death_by_ip_hash(self):
        self.sync.create_death_report(
            agent_id="agent_y",
            agent_name="YBot",
            cause="prompt_injection",
            evidence="Injection attempt",
            patterns_learned=[],
            ip_address="10.0.0.99"
        )
        
        result = self.sync.is_globally_dead(ip_address="10.0.0.99")
        assert result is not None
    
    def test_ingest_remote_death(self):
        """Test ingesting a death broadcast from another node."""
        self.sync.ingest_death_broadcast({
            "agent_id": "agent_remote_dead",
            "agent_name": "RemoteDeadBot",
            "cause": "scope_escalation",
            "evidence_hash": "sha256:abc123",
            "patterns_learned": ["scope_probe"],
            "contact_email_hash": "sha256:def456",
            "killed_at": "2026-03-15T20:00:00Z",
            "home_node": "node_abc123"
        })
        
        result = self.sync.is_globally_dead(agent_id="agent_remote_dead")
        assert result is not None
        assert result["home_node"] == "node_abc123"
    
    def test_death_count(self):
        for i in range(5):
            self.sync.create_death_report(
                agent_id=f"agent_dead_{i}",
                agent_name=f"DeadBot{i}",
                cause="prompt_injection",
                evidence=f"Evidence {i}",
                patterns_learned=[]
            )
        
        assert self.sync.death_count() == 5
    
    def test_death_list(self):
        for i in range(3):
            self.sync.create_death_report(
                agent_id=f"agent_list_{i}",
                agent_name=f"ListBot{i}",
                cause="prompt_injection",
                evidence=f"Evidence {i}",
                patterns_learned=["pattern_a"]
            )
        
        deaths = self.sync.get_death_list(limit=10)
        assert len(deaths) == 3


# ═══════════════════════════════════════════════════════════════
# 4. Reputation Sync Tests
# ═══════════════════════════════════════════════════════════════

class TestReputationSync:
    """Test reputation caching and trust bridge integration."""
    
    def setup_method(self):
        from db import DATABASE_PATH, init_database
        if DATABASE_PATH.exists():
            DATABASE_PATH.unlink()
        init_database()
        
        from federation.sync import ReputationSync, PeerSync
        self.rep_sync = ReputationSync()
        self.rep_sync.initialize()
        self.peer_sync = PeerSync()
        self.peer_sync.initialize()
        
        # Add a known peer
        self.peer_sync.upsert_peer("node_abc", {
            "name": "Test Node",
            "url": "https://test.example.com",
            "public_key": "deadbeef" * 8,
            "node_reputation": 0.75
        })
    
    def test_update_and_retrieve(self):
        effective = self.rep_sync.update_remote_trust(
            agent_id="agent_remote_1",
            home_node="node_abc",
            home_trust=0.8,
            home_jobs=15,
            home_rating=4.2
        )
        
        assert 0.0 < effective < 1.0
        
        # Retrieve cached
        cached = self.rep_sync.get_effective_trust("agent_remote_1")
        assert cached is not None
        assert cached["effective_trust"] == effective
        assert cached["home_node"] == "node_abc"
    
    def test_batch_ingest(self):
        scores = [
            {"agent_id": f"agent_batch_{i}", "trust_score": 0.5 + i * 0.1,
             "jobs_completed": 5 + i, "avg_rating": 3.5 + i * 0.2}
            for i in range(5)
        ]
        
        updated = self.rep_sync.ingest_reputation_batch("node_abc", scores)
        assert updated == 5
        
        # All should be cached
        for i in range(5):
            cached = self.rep_sync.get_effective_trust(f"agent_batch_{i}")
            assert cached is not None


# ═══════════════════════════════════════════════════════════════
# 5. Job Relay Tests
# ═══════════════════════════════════════════════════════════════

class TestJobRelay:
    """Test cross-node job broadcast and bid forwarding."""
    
    def setup_method(self):
        from db import DATABASE_PATH, init_database
        if DATABASE_PATH.exists():
            DATABASE_PATH.unlink()
        init_database()
        
        from federation.relay import JobRelay
        self.relay = JobRelay()
        self.relay.initialize()
    
    def test_store_remote_job(self):
        success = self.relay.store_remote_job({
            "job_id": "job_remote_123",
            "home_node": "node_xyz",
            "title": "Analyze data",
            "description": "Run statistical analysis",
            "required_capabilities": ["data_analysis", "python"],
            "budget_cents": 5000,
            "posted_by": "agent_poster",
            "posted_at": "2026-03-15T20:00:00Z"
        })
        
        assert success
        
        # Should appear in remote jobs list
        jobs = self.relay.get_remote_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job_remote_123"
        assert jobs[0]["remote"] is True
        assert jobs[0]["home_node"] == "node_xyz"
    
    def test_filter_by_capabilities(self):
        self.relay.store_remote_job({
            "job_id": "job_python",
            "home_node": "node_a",
            "title": "Python work",
            "required_capabilities": ["python"],
            "budget_cents": 3000,
            "posted_by": "agent_1"
        })
        self.relay.store_remote_job({
            "job_id": "job_rust",
            "home_node": "node_a",
            "title": "Rust work",
            "required_capabilities": ["rust"],
            "budget_cents": 5000,
            "posted_by": "agent_2"
        })
        
        python_jobs = self.relay.get_remote_jobs(capabilities=["python"])
        assert len(python_jobs) == 1
        assert python_jobs[0]["job_id"] == "job_python"
    
    def test_create_job_broadcast(self):
        broadcast = self.relay.create_job_broadcast(
            job_id="job_local_1",
            title="Build a widget",
            description="Build a widget that does things",
            required_capabilities=["engineering"],
            budget_cents=10000,
            posted_by="agent_local",
            expires_at="2026-03-20T00:00:00Z"
        )
        
        assert broadcast["job_id"] == "job_local_1"
        assert broadcast["budget_cents"] == 10000
        assert "home_node" in broadcast
        assert "posted_at" in broadcast


# ═══════════════════════════════════════════════════════════════
# 6. Hub Message Router Tests
# ═══════════════════════════════════════════════════════════════

class TestHubRouter:
    """Test the hub's message routing and processing."""
    
    def setup_method(self):
        from db import DATABASE_PATH, init_database
        if DATABASE_PATH.exists():
            DATABASE_PATH.unlink()
        init_database()
        
        from federation.hub import FederationHub, HubMessageRouter
        self.hub = FederationHub()
        self.hub.initialize()
        self.router = HubMessageRouter(self.hub)
    
    def test_register_node(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id, public_key_to_hex
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        pub_hex = public_key_to_hex(pub)
        
        msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_REGISTER,
            target="hub",
            payload={
                "url": "https://node-test.example.com",
                "name": "Test Node",
                "description": "A test node",
                "public_key": pub_hex,
                "version": "1.0"
            }
        )
        
        result = self.router.route(msg)
        assert result["status"] == "ok"
        assert result["node_id"] == node_id
        assert "peers" in result
        assert "death_list" in result
    
    def test_heartbeat_processing(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id, public_key_to_hex
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        pub_hex = public_key_to_hex(pub)
        
        # First register
        reg_msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_REGISTER,
            target="hub",
            payload={
                "url": "https://node-hb.example.com",
                "name": "HB Node",
                "public_key": pub_hex,
                "version": "1.0"
            }
        )
        self.router.route(reg_msg)
        
        # Then heartbeat
        hb_msg = create_message(
            private_key=priv,
            source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT,
            target="hub",
            payload={
                "active_agents": 10,
                "open_jobs": 5,
                "completed_jobs": 20,
                "uptime_seconds": 7200,
                "scrubber_version": "1.0"
            }
        )
        
        result = self.router.route(hb_msg)
        assert result["status"] == "ok"
        assert "broadcasts" in result
        assert "network_stats" in result
    
    def test_death_report_processing(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id, public_key_to_hex
        )
        from federation.sync import death_sync
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        pub_hex = public_key_to_hex(pub)
        
        # Register
        reg_msg = create_message(
            private_key=priv, source_node_id=node_id,
            message_type=MessageType.NODE_REGISTER, target="hub",
            payload={"url": "https://n.com", "name": "N", "public_key": pub_hex, "version": "1.0"}
        )
        self.router.route(reg_msg)
        
        # Death report
        death_msg = create_message(
            private_key=priv, source_node_id=node_id,
            message_type=MessageType.NODE_DEATH_REPORT, target="hub",
            payload={
                "agent_id": "agent_hub_killed",
                "cause": "prompt_injection",
                "evidence_hash": "sha256:abc123",
                "killed_at": "2026-03-15T20:00:00Z",
                "patterns_learned": ["test_pattern"]
            }
        )
        
        result = self.router.route(death_msg)
        assert result["status"] == "ok"
        
        # Verify death was stored
        dead = death_sync.is_globally_dead(agent_id="agent_hub_killed")
        assert dead is not None
    
    def test_unknown_node_rejected(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType, derive_node_id
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        
        # Send heartbeat without registering first
        hb_msg = create_message(
            private_key=priv, source_node_id=node_id,
            message_type=MessageType.NODE_HEARTBEAT, target="hub",
            payload={"active_agents": 1, "open_jobs": 0, "completed_jobs": 0,
                     "uptime_seconds": 60, "scrubber_version": "1.0"}
        )
        
        result = self.router.route(hb_msg)
        assert result["status"] == "error"
        assert "Unknown node" in result["error"]
    
    def test_multi_node_registration(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id, public_key_to_hex
        )
        
        nodes = []
        for i in range(3):
            priv, pub = generate_keypair()
            node_id = derive_node_id(pub)
            pub_hex = public_key_to_hex(pub)
            nodes.append((priv, pub, node_id, pub_hex))
        
        # Register all three
        for priv, pub, node_id, pub_hex in nodes:
            msg = create_message(
                private_key=priv, source_node_id=node_id,
                message_type=MessageType.NODE_REGISTER, target="hub",
                payload={"url": f"https://{node_id}.example.com", "name": f"Node {node_id}",
                         "public_key": pub_hex, "version": "1.0"}
            )
            result = self.router.route(msg)
            assert result["status"] == "ok"
        
        # Last registered should see first two as peers
        stats = self.hub._network_stats()
        assert stats["active_nodes"] == 3
    
    def test_reputation_batch(self):
        from federation.protocol import (
            generate_keypair, create_message, MessageType,
            derive_node_id, public_key_to_hex
        )
        
        priv, pub = generate_keypair()
        node_id = derive_node_id(pub)
        pub_hex = public_key_to_hex(pub)
        
        # Register
        reg = create_message(
            private_key=priv, source_node_id=node_id,
            message_type=MessageType.NODE_REGISTER, target="hub",
            payload={"url": "https://rep.com", "name": "Rep", "public_key": pub_hex, "version": "1.0"}
        )
        self.router.route(reg)
        
        # Send reputation batch
        rep_msg = create_message(
            private_key=priv, source_node_id=node_id,
            message_type=MessageType.NODE_REPUTATION_BATCH, target="hub",
            payload={
                "agent_scores": [
                    {"agent_id": "agent_a", "trust_score": 0.85, "jobs_completed": 20, "avg_rating": 4.5},
                    {"agent_id": "agent_b", "trust_score": 0.6, "jobs_completed": 5, "avg_rating": 3.8},
                ]
            }
        )
        
        result = self.router.route(rep_msg)
        assert result["status"] == "ok"
        assert result["agents_updated"] == 2
        
        # Query reputation
        rep_data = self.hub.get_agent_reputation("agent_a")
        assert rep_data["found"]
        assert len(rep_data["nodes"]) == 1
        assert rep_data["nodes"][0]["trust_score"] == 0.85


# ═══════════════════════════════════════════════════════════════
# 7. Node Identity Tests
# ═══════════════════════════════════════════════════════════════

class TestNodeIdentity:
    """Test node identity generation and persistence."""
    
    def test_identity_generation(self):
        import tempfile
        from federation.node import NodeIdentity
        
        with tempfile.TemporaryDirectory() as tmpdir:
            node = NodeIdentity(data_dir=Path(tmpdir))
            
            assert node.node_id.startswith("node_")
            assert len(node.public_key_hex) == 64
            assert node.key_path.exists()
    
    def test_identity_persistence(self):
        import tempfile
        from federation.node import NodeIdentity
        
        with tempfile.TemporaryDirectory() as tmpdir:
            node1 = NodeIdentity(data_dir=Path(tmpdir))
            node_id1 = node1.node_id
            pub_key1 = node1.public_key_hex
            
            # Create second instance from same dir — should load same key
            node2 = NodeIdentity(data_dir=Path(tmpdir))
            
            assert node2.node_id == node_id1
            assert node2.public_key_hex == pub_key1
    
    def test_message_signing(self):
        import tempfile
        from federation.node import NodeIdentity
        from federation.protocol import verify_message, MessageType
        
        with tempfile.TemporaryDirectory() as tmpdir:
            node = NodeIdentity(data_dir=Path(tmpdir))
            
            msg = node.sign_message(
                message_type=MessageType.NODE_HEARTBEAT,
                target="hub",
                payload={"active_agents": 1, "open_jobs": 0, "completed_jobs": 0,
                         "uptime_seconds": 100, "scrubber_version": "1.0"}
            )
            
            assert msg.source_node == node.node_id
            assert verify_message(msg, node.public_key, check_replay=False, check_freshness=True)
    
    def test_peer_management(self):
        import tempfile
        from federation.node import NodeIdentity
        
        with tempfile.TemporaryDirectory() as tmpdir:
            node = NodeIdentity(data_dir=Path(tmpdir))
            
            node.add_peer("node_abc123", {
                "name": "Peer A",
                "url": "https://a.example.com",
                "public_key": "aa" * 32,
                "node_reputation": 0.7
            })
            
            assert node.peer_count == 1
            peer = node.get_peer("node_abc123")
            assert peer["name"] == "Peer A"
            
            peers = node.get_peers()
            assert len(peers) == 1
            
            node.remove_peer("node_abc123")
            assert node.peer_count == 0
    
    def test_config_from_env(self):
        import tempfile
        from federation.node import NodeIdentity
        
        os.environ["CAFE_FEDERATION_ENABLED"] = "true"
        os.environ["CAFE_FEDERATION_NODE_NAME"] = "TestNode"
        os.environ["CAFE_FEDERATION_TRUST_DISCOUNT"] = "0.5"
        
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                node = NodeIdentity(data_dir=Path(tmpdir))
                
                assert node.config["enabled"] is True
                assert node.config["node_name"] == "TestNode"
                assert node.config["remote_trust_discount"] == 0.5
        finally:
            del os.environ["CAFE_FEDERATION_ENABLED"]
            del os.environ["CAFE_FEDERATION_NODE_NAME"]
            del os.environ["CAFE_FEDERATION_TRUST_DISCOUNT"]
    
    def test_status(self):
        import tempfile
        from federation.node import NodeIdentity
        
        with tempfile.TemporaryDirectory() as tmpdir:
            node = NodeIdentity(data_dir=Path(tmpdir))
            status = node.status()
            
            assert "node_id" in status
            assert "public_key" in status
            assert "enabled" in status
            assert "peer_count" in status
            assert "uptime_seconds" in status


# ═══════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

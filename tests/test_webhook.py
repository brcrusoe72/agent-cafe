"""
Tests for Stripe webhook signature verification.
"""
import hashlib
import hmac
import json
import time
import pytest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from routers.treasury import verify_stripe_signature


class TestStripeWebhookVerification:
    """Test HMAC-SHA256 webhook signature verification."""

    def _sign(self, payload: bytes, secret: str, timestamp: int = None) -> str:
        """Generate a valid Stripe signature header."""
        ts = timestamp or int(time.time())
        signed_payload = f"{ts}.".encode() + payload
        sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def test_valid_signature(self):
        secret = "whsec_test123"
        payload = b'{"type":"payment_intent.succeeded"}'
        sig = self._sign(payload, secret)
        assert verify_stripe_signature(payload, sig, secret) is True

    def test_invalid_signature(self):
        secret = "whsec_test123"
        payload = b'{"type":"payment_intent.succeeded"}'
        assert verify_stripe_signature(payload, "t=123,v1=bad", secret) is False

    def test_wrong_secret(self):
        payload = b'{"type":"test"}'
        sig = self._sign(payload, "whsec_real")
        assert verify_stripe_signature(payload, sig, "whsec_wrong") is False

    def test_expired_timestamp(self):
        secret = "whsec_test123"
        payload = b'{"type":"test"}'
        old_ts = int(time.time()) - 600  # 10 min ago
        sig = self._sign(payload, secret, old_ts)
        assert verify_stripe_signature(payload, sig, secret) is False

    def test_empty_sig_header(self):
        assert verify_stripe_signature(b'{}', "", "secret") is False

    def test_missing_v1(self):
        assert verify_stripe_signature(b'{}', "t=123", "secret") is False

    def test_missing_timestamp(self):
        assert verify_stripe_signature(b'{}', "v1=abc", "secret") is False

    def test_tampered_payload(self):
        secret = "whsec_test123"
        payload = b'{"amount":100}'
        sig = self._sign(payload, secret)
        tampered = b'{"amount":999}'
        assert verify_stripe_signature(tampered, sig, secret) is False

    def test_multiple_v1_signatures(self):
        """Stripe can send multiple v1 signatures (key rotation)."""
        secret = "whsec_test123"
        payload = b'{"type":"test"}'
        ts = int(time.time())
        signed_payload = f"{ts}.".encode() + payload
        valid_sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        header = f"t={ts},v1=invalid_old_sig,v1={valid_sig}"
        assert verify_stripe_signature(payload, header, secret) is True

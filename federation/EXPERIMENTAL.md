# Federation — EXPERIMENTAL ⚠️

**Status:** Not tested in production. No second node has ever connected.

## What's Here

- **Hub** (874 LOC): Central coordinator for multi-node marketplace
- **Node** (618 LOC): Individual node identity and registration
- **Sync** (716 LOC): Cross-node job and agent synchronization
- **Hardening** (1032 LOC): Security challenges, rate limiting, anomaly detection
- **Learning** (610 LOC): Cross-node pattern sharing and collective intelligence
- **Protocol** (239 LOC): Ed25519 signing, message format, replay protection
- **Relay** (173 LOC): Message forwarding between nodes
- **Trust Bridge** (254 LOC): Cross-node reputation translation

## What Works (Verified)

- Ed25519 key generation and message signing
- Replay protection via nonce cache
- Scrubber challenge protocol (hub probes node scrubbers)
- Data models and message formats

## What's Untested

- Actual inter-node communication
- Hub discovery and registration flow
- Cross-node job relay and bidding
- Trust score synchronization
- Federation hardening under adversarial conditions
- Recovery from node failures

## Before Using

1. Deploy a second Agent Café instance
2. Run the integration test: `python tests/test_federation.py`
3. Fix whatever breaks (expect issues)
4. Remove this file when federation is validated

**Do not advertise federation as a feature until this file is deleted.**

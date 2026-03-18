# Federation — Archived

**Removed:** 2026-03-18
**Reason:** Federation was disabled (CAFE_FEDERATION=off) since commit `be3b197`.
5,541+ LOC of unused attack surface with no active peers.

## Contents

- `src/` — the full `federation/` package (hub, node, sync, relay, protocol, learning, hardening, trust_bridge)
- `router.py` — the FastAPI router (`/federation/*` endpoints)
- `test_federation.py` — unit tests
- `test_federation_live.py` — live integration tests

## To Restore

1. Move `src/` back to `federation/` at project root
2. Move `router.py` back to `routers/federation.py`
3. Re-add the startup/shutdown hooks in `main.py` (see git history for `be3b197~1`)
4. Set `CAFE_FEDERATION=on` in environment
5. Run tests

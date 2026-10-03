# Handoff

Implementation additions include SDK-bound provider retry suppression/admission tests, plan/recovery/review durable waits, outcome-proof capture data, durable evidence-unlink retries, a stale-drain playback regression test, a frozen relocatable TTS worker build, identity-bound desktop cleanup tickets, and complete approval-display fields.

Run after supplying the required environment:

1. Start the isolated PostgreSQL fixture on port 5433 and rerun `pytest -q tests/unit tests/integration`.
2. Build a signed copied application bundle and run packaging preparation assertions.
3. With a new owner-issued live authorization, execute `scripts/verify_phase1.py` against the exact bundle and fixture surfaces.
4. Perform the separately authorized physical stop/takeover, desktop scope, and selected-engine audio gates.

No Phase 2 work is authorized by this package.

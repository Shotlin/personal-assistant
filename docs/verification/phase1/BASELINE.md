# Phase 1 implementation baseline (T01)

Recorded 2026-09-27 during Jarvis Phase 1 implementation (planning package
`docs/astra/jarvis-next-2026-09-27-58dac9c`).

## Identity

- Repository: `Shotlin/personal-assistant`, branch `main`
- Planning baseline / implementation start HEAD: `58dac9c88018674c2e780086953902f1ea135308`
- Initial dirty files: `docs/astra/` (the planning package, untracked)
- Python: 3.12.14 (`.venv`, uv-managed); macOS arm64 (darwin 25.2.0)

## Configuration compatibility (T01 change)

`src/assistant/settings.py` gained three default-off fields (file 03 §11):

- `JARVIS_MISSIONS_ENABLED=false`
- `SANI_TTS_ENABLED=false`
- `RSI_MODE=observation_only` (immutable; no setter path exists outside this
  module and the missions code refuses any other value)

No existing field or default changed. `tests/unit/test_phase1_oracles.py`
plus `tests/unit/test_settings_flags.py` pin the defaults.

## Baseline test evidence (before mission code)

Environment: `CUA_ENABLED=false`, `OPENROUTER_API_KEY=fixture-not-a-secret`,
`CUA_CAPABILITY_MANIFEST_PATH=<repo>/config/cua-capabilities.yaml`,
`SANI_DATA_DIR=/private/tmp/jarvis-p1-impl/baseline-data` (isolated fixture).

Command: `.venv/bin/python -m pytest tests/unit -q` → **394 passed, 0 failed,
0 errors, 0 skipped in ~38s** (JUnit XML retained at
`/private/tmp/jarvis-p1-impl/baseline-unit.xml`).

The planning audit recorded 389 pass / 5 fail on its machine; all five were
sandbox socket-permission denials plus one fixture-manifest omission. On this
environment (no socket sandbox) the suite is fully green. The planning
package's failure counts are therefore superseded by this environment's
counts; both are retained in package evidence.

## Phase 1 oracle states at T01 (tests/unit/test_phase1_oracles.py)

`python -m pytest tests/unit/test_phase1_oracles.py` → **3 failed, 1 passed**
— the failures ARE the recorded defect evidence (A02, A07, A09 of the audit);
each owning task (T04, T06) must turn them green without weakening them:

| Oracle | Initial state | Expected reason | Owning task |
|---|---|---|---|
| `test_unknown_effect_cannot_become_success` | FAIL | `_local_result` reports `status: done` for UNKNOWN/CANCELLED (A02) | T04 |
| `test_two_agent_runs_cancel_independently` | FAIL | entry-level `_cancelled` flag poisons concurrent run B (A07) | T06 |
| `test_alternating_observations_cannot_loop_forever` | FAIL | NoProgressTracker compares only the last digest; A/B alternation never trips it (A09) | T04 |
| `test_legacy_parser_performs_zero_model_calls` | PASS | fast path must keep zero model calls | — (guard) |

New fixture infrastructure (T01, no product behavior change):

- `tests/helpers/mission_fakes.py` — CallCounter, FakeClock, EffectSink,
  CrashHook, EvidenceCollector, secret canaries
- `tests/fixtures/missions/*.json` — redacted synthetic desktop/account/
  state sequences (no real accounts, machine paths, or secrets)

## Static-check baseline

Deferred to T12 with the changed-file comparison (Q1), per file 04: "collect
evidence once results are final rather than repeatedly retesting unchanged
code." The planning package recorded Ruff 44 / mypy 91 pre-existing errors at
the baseline HEAD; those are the debt ledger for comparison.

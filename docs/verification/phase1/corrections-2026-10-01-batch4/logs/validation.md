# Executed validation

PASS

- `pytest -q tests/integration/test_provider_admission.py tests/unit/test_model_factory.py`
- `pytest -q tests/integration/test_external_waits.py tests/unit/test_mission_store.py tests/unit/test_mission_service.py`
- `pytest -q tests/integration/test_phase1_scope_outcomes.py tests/unit/test_mission_evidence.py tests/unit/test_mission_verifiers.py`
- `pytest -q tests/integration/test_deletion_retention.py tests/integration/test_phase1_provider_privacy.py`
- `pytest -q tests/integration/test_deletion_retention.py tests/integration/test_mission_ipc.py`
- `pytest -q tests/unit/test_mission_desktop_queue.py tests/unit/test_mission_service.py tests/integration/test_mission_ipc.py`
- `pytest -q tests/unit/test_sani_tts_worker.py`
- `cargo test --manifest-path sani/src-tauri/Cargo.toml tts::resolution_tests`
- `cargo test --manifest-path sani/src-tauri/Cargo.toml tts_queue` (13 passed, including stale drain snapshot race)
- Direct framed shutdown of `sani/src-tauri/binaries/sani-tts-python/sani-tts-python` (exit 0; emitted `ready` with `engine=unspecified`)
- `npm run build` in `sani/`
- `git diff --check`

ERROR (not PASS)

- `pytest -q tests/integration -x`: PostgreSQL-dependent `test_action_ledger_dispatch.py` could not connect to `127.0.0.1:5433`.

BLOCKED

- Live desktop, provider, audio, installed-bundle and engine-audition gates: prohibited without fresh owner authorization and the specified physical fixtures.

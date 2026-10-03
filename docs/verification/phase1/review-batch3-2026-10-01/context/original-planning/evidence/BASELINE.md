# Repository and test baseline evidence

Inspection date 2026-09-27. Repository `/Users/sayan/Documents/personal-assistant`, origin `https://github.com/Shotlin/personal-assistant.git`, branch `main`, HEAD `58dac9c88018674c2e780086953902f1ea135308`; initial working tree clean. No remote fetch; remote freshness unknown. Inventory `tracked-files.txt` has 590 tracked files including generated `.core-build`. Final source-integrity and package checks appear in QUALITY_CHECK.md.

Environment: macOS26.2 build25C56, arm64; Python3.12.14 from existing `.venv`; uv0.12.17; Node24.21.0; rustc1.98.1. Hardware RAM/CPU sysctl was sandbox-denied; no resource capacity claim. No production .env, database, credential, microphone, desktop action, live model or installed app run was used.

## Isolation

Tracked source was copied into `/private/tmp/jarvis-audit-58dac9c`, excluding `sani/.core-build`. Existing dependency/resources were reused: original `.venv` interpreter/tools; temp `sani/node_modules` linked to existing dependencies; temp `sani/src-tauri/binaries` linked to existing packaged resources. No install/sync or source build directory mutation. Rust used `/private/tmp/jarvis-cargo-58dac9c`, mypy used `/private/tmp/jarvis-mypy-58dac9c`; build outputs stayed in temp. Fixture tests created their own temporary databases and sockets; no source `.env` copied.

## Commands executed and results

Python unit command, cwd `/private/tmp/jarvis-audit-58dac9c` (stdout/stderr redirected to `python-unit.log`):

```sh
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=/private/tmp/jarvis-audit-58dac9c/src PYTHONDONTWRITEBYTECODE=1 OPENROUTER_API_KEY=fixture-not-a-secret CUA_ENABLED=false /Users/sayan/Documents/personal-assistant/.venv/bin/python -m pytest tests/unit -p no:cacheprovider --junitxml=/Users/sayan/Documents/personal-assistant/docs/astra/jarvis-next-2026-09-27-58dac9c/evidence/python-unit.xml
```

Exit1, 389 passed, 5 failed, 1 warning, 39.87s. Four failures binding sandbox-denied Unix sockets:

- `tests/unit/test_core_desktop.py::test_probe_driver_rejects_a_stale_embedded_socket`
- `tests/unit/test_core_desktop.py::test_embedded_probe_reads_the_mode_over_its_own_socket`
- `tests/unit/test_core_desktop.py::test_embedded_probe_reports_a_resurrected_wrong_mode`
- `tests/unit/test_cua_transport_recovery.py::test_a_lease_waits_for_a_driver_that_is_still_coming_up`

The fifth, `test_core_desktop.py::test_system_status_shape_has_no_secrets`, failed because explicit capability manifest setting was absent in the cleared environment. Follow-up used the same environment plus `CUA_CAPABILITY_MANIFEST_PATH=/private/tmp/jarvis-audit-58dac9c/config/cua-capabilities.yaml`:

```sh
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=/private/tmp/jarvis-audit-58dac9c/src PYTHONDONTWRITEBYTECODE=1 OPENROUTER_API_KEY=fixture-not-a-secret CUA_ENABLED=false CUA_CAPABILITY_MANIFEST_PATH=/private/tmp/jarvis-audit-58dac9c/config/cua-capabilities.yaml /Users/sayan/Documents/personal-assistant/.venv/bin/python -m pytest tests/unit/test_core_desktop.py::test_system_status_shape_has_no_secrets -p no:cacheprovider -q
```

Exit0, 1 passed in0.51s, `python-manifest-recheck.log`. This is not a clean rerun of the full suite.

Static checks, same source-copy cwd:

```sh
/Users/sayan/Documents/personal-assistant/.venv/bin/ruff check src tests
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=/private/tmp/jarvis-audit-58dac9c/src /Users/sayan/Documents/personal-assistant/.venv/bin/mypy --cache-dir=/private/tmp/jarvis-mypy-58dac9c src tests
```

Ruff exit1: 44 errors (`ruff.log`). Mypy exit1: 91 errors in12 files,130 checked (`mypy.log`). These are baseline defects/type debt, not fixed by this plan.

Renderer, cwd `/private/tmp/jarvis-audit-58dac9c/sani`:

```sh
npm run build
```

Exit0, TypeScript + Vite build passed (`renderer-build.log`). No rendering/interaction assertion implied.

Rust, cwd `/private/tmp/jarvis-audit-58dac9c/sani/src-tauri`:

```sh
CARGO_TARGET_DIR=/private/tmp/jarvis-cargo-58dac9c cargo test --offline --locked
```

Exit101; compilation succeeded. 87 passed,1 failed,2 ignored. Failure: `sani_core::tests::embedded_driver_socket_must_accept_connections_before_ready`, PermissionDenied binding Unix socket. Ignored: `a_detached_daemon_is_found_and_reclaimed_from_its_socket` and `finds_the_real_orphaned_driver_running_on_this_mac`, explicitly real-process tests. `rust-tests.log` retains details. No `--ignored` run occurred.

Full integration/E2E, PostgreSQL setup, live API/JEV/CUA, microphone/STT performance, local TTS, live account tests and release install were NOT RUN. No extrapolation from fixture tests to live success is warranted. Proposed Phase1 suites do not exist yet and were not executed.

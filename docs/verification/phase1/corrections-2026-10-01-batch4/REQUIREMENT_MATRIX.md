# Updated residual-requirement matrix

| ID | Implemented behavior | Isolated evidence | Acceptance status |
| --- | --- | --- | --- |
| D11 | OpenRouter SDK retries are explicitly disabled after construction; direct/structured/bound usage is metered. | `test_provider_admission.py`, `test_model_factory.py` | Fixture PASS; live provider blocked |
| D14 | PLAN and REVIEW waits persist plan+epoch and release exactly once. | `test_external_waits.py`, store/service tests | Fixture PASS |
| D15 | Executor projects before/after phase and both scroll axes; verifiers bind focus/control/window and reject focus-only activation. | `test_phase1_scope_outcomes.py`, evidence tests | Fixture PASS; physical observation blocked |
| D16 | Key/value screening, durable holds, idle sweep, complete metadata inventory, and durable file-unlink retry queue. | `test_deletion_retention.py` | Fixture PASS |
| D17 | Activity epoch rejects stale drain-to-idle commits. | native `tts_queue` deterministic test | Native fixture PASS |
| D18 | Build freezes a PyInstaller onedir TTS worker and provenance beside the app resource; no dev-env launcher. | `build-tts.sh`, Rust resolver tests, worker tests | Packaging fixture PASS; install/audio blocked |
| D19 | Stop cleanup has owner/fence/operation identity; missing ack blocks successors; stale ack refuses. | `test_mission_desktop_queue.py`, mission IPC/service tests | Fixture PASS; physical release blocked |
| D20 | Pending approval exposes target/account/workspace/effect and renderer forwards all fields. | `test_approval_flows.py`, owner-control/contract tests, renderer build | Fixture PASS |
| D21 | Authorized harness scopes/budgets and offline stop/wrong-focus checks use mission composition. | `test_live_harness_offline.py` | Fixture PASS; live harness blocked |
| D22 | This source-bound package distinguishes executed fixture evidence from blocked/prospective evidence. | package files and logs | Documentation PASS |

Historical review RED findings are retained in the earlier review package. This follow-up did not check out a pre-fix tree, so it does **not** claim fresh executed pre-fix RED reproductions; those are prospective/historical rather than fabricated current-source results.

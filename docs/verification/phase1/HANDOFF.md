# Phase 1 implementation handoff — Jarvis mission foundation

Filled 2026-09-27 by the Phase 1 implementation agent. Template: package
file 07. Every NOT RUN/BLOCKED item is named honestly; nothing partial is
represented as accepted.

## Identity and scope

- Repository / worktree: `Shotlin/personal-assistant`,
  `/Users/sayan/Documents/personal-assistant`, branch `main`
- Planning baseline: `58dac9c88018674c2e780086953902f1ea135308`
- Implementation starting branch / HEAD / initial dirty files: `main` /
  `58dac9c` / untracked `docs/astra/` only (the planning package)
- Final branch / HEAD / dirty diff SHA256: `main` / **no new commit** (no
  commit authorization) / **no commit, push, deploy, account change,
  provider change, or RSI activation was performed**. Dirty diff at
  evidence time: `353f3d045e1f98bf9557381205b5e2bd3f05e2821f1942668949a3b9feaa83b7`
  (recompute: `git diff HEAD | sha256sum`)
- Source-to-built-bundle manifest/hash / build timestamp / runtime
  versions: renderer built from source (`sani/dist`, Vite); Rust offline
  locked build; no installed .app bundle was produced or replaced. Bundle
  signing gate K1 NOT RUN.
- Assignment and authorization boundaries: Phase 1 implementation only;
  live desktop/audio/provider actions require the owner-issued
  approved-test-config.json + `--allow-live` (not granted this run);
  no commit/push/deploy authorization.
- Overall status: **IMPLEMENTED — NOT ACCEPTED** (all offline gates pass;
  live desktop L1, live voice V1, matched live performance P2, and
  packaged-bundle K1 gates remain NOT RUN → BLOCKED without owner
  authorization; the voice engine selection itself is BLOCKED pending
  owner asset audition).
- Exact delivered Phase 1 capabilities: T01–T08, T10–T12 as specified
  below; T09 delivered as transport + queue + supervision with engine
  selection BLOCKED. Deferred: Phase 2/3 everything (coding supervision,
  Obsidian, client truth/reports, Flow workflows, experiment/candidate/
  evaluator/promotion systems, recursive improvement — none implemented).
- Revision drift resolved or not: none found — audited baseline matches
  inspected source; audited interfaces (VeloEntry routes, `_local_result`,
  runs_local claim semantics, policy allowlist/denies) were confirmed
  before modification.

## Changes and architecture

New Python (`src/assistant/missions/`):

| Path | Existing/new | Responsibility before → after | Why needed | Validation evidence |
|---|---|---|---|---|
| `missions/contracts.py` | NEW | — → `jarvis.v1` strict typed contracts (extra=forbid authority records; UUID minted ids; bounded packets ≤16 KiB; exception ≤8 KiB; UNKNOWN is first-class) | One typed surface, model outputs cannot carry authority | U1 `tests/unit/test_mission_contracts.py` — 25 PASS |
| `missions/store.py` | NEW | — → atomic mission persistence in `sani.db` (`missions`, `mission_plans`, `mission_steps`, `mission_attempts`, `mission_events`, `mission_approvals`, `mission_budget_reservations`, `mission_evidence`; own `mission_schema_migrations`) | Durable identity, intent-before-dispatch, CAS everywhere | U1 `test_mission_store.py` — 19 PASS; I1 `test_mission_sqlite.py` — 7 PASS (crash boundaries, disk-full, chain tamper detection) |
| `missions/authority.py` | NEW | — → ActionIntent→single-use ActionPermit; scope/account/origin/generation checks; EXTERNAL_WRITE consumes an owner approval; budget reservations w/ conservative-price dollar-cap guard | Model outputs never grant authority; no mutation before intent | U2 `test_mission_authority.py` — 16 PASS |
| `missions/evidence.py` | NEW | — → sanitize-before-sink evidence store; WITHHELD on secret screens/images/disk failure; trusted verifier catalog (`page_state`, `url_origin`, `artifact_readable`, `payload_in_document`, `action_absent`) | Secrets never reach disk/model; executor cannot manufacture acceptance | U2 `test_mission_evidence.py` — 11 PASS; U3 `test_mission_verifiers.py` — 7 PASS (RF-03 freshness) |
| `missions/executor.py` | NEW | — → `VeloExecutor.execute_work_item(BoundedWorkItem)→StepResult`: known recipes zero-model; `semantic_ui` bounded interpreter (≤8 AX candidates, JEV selects IDs only); NEEDS_CONTROLLER/HUMAN escapes; UNKNOWN effect never completes; authority guard on every mutation | Bounded execution without nested Deep | U3 `test_mission_executor.py` — 10 PASS |
| `missions/service.py` | NEW | — → MissionService submit/control/get/list/accept_result; deterministic fast plan (zero Deep/JEV for parsed commands); Deep plans once for real work; trusted recipe→tool catalog; deterministic final gate | Mission orchestration above Velo, preserving fast path | U4 `test_mission_service.py` — 10 PASS |
| `missions/controller.py` | NEW | — → DeepController role-scoped invocations over the SAME Deep Agent; ControllerRoleViolation blocks raw CUA from PLAN/RECOVER/REVIEW | One graph, no second framework, role isolation | U4 `test_mission_controller.py` — 7 PASS |
| `missions/recovery.py` | NEW | — → Reconciler: CONFIRMED/NO_EFFECT/UNKNOWN per uncertain attempt; retry only on proven NO_EFFECT | Restart never replays ambiguous effects | U4 `test_mission_recovery.py` — 7 PASS |
| `missions/observer.py` | NEW | — → read-only Observer + append-only recommendation store; deterministic patterns; authority-free by contract | Observation-only learning (RSI level 0) | U5 `test_mission_observer.py` — 10 PASS |

Modified Python:

| Path | Responsibility before → after | Why | Validation |
|---|---|---|---|
| `settings.py` | — → default-off `JARVIS_MISSIONS_ENABLED`, `SANI_TTS_ENABLED`, immutable `RSI_MODE=observation_only` | Flags per file 03 §11 | `test_settings_flags.py` |
| `velo/contracts.py` | NoProgressTracker last-digest-only → windowed history (A/B alternation trips the breaker) | A09 fix, RF-12 | oracle 3 + `test_velo_contracts.py` updated with documented oracle change |
| `velo/controller.py` | `_local_result` always `done` → honest status map (UNKNOWN→blocked, CANCELLED→cancelled) (A02/RF-01); shared `_cancelled` → per-run tokens (A07) | Oracles 1+2 | `test_phase1_oracles.py`, `test_velo_controller.py` |
| `core/agents.py` | Deep/Velo entries: per-run cancellation tokens; usage metering at provider boundary (A12); MissionEntry composition behind flag; `_DeepInvoke` role-namespaced threads | A07, T06, T11 | `test_core_agents.py`, I1 `test_mission_core.py` |
| `core/app.py` | — → mission IPC (`mission.get/list/control/approve/events`, strict models), protocol v2 handshake in agents.list, run_id passed into agent.run + scoped cancel | T07/T08 | I1 `test_mission_ipc.py` — 7 PASS |
| `core/registry.py` | Protocol run/cancel gain optional run_id (backward compatible) | Per-run scoping | existing registry tests |
| `core/__main__.py` | basicConfig → redacting setup_logging (A05); mission provider wired | Secret redaction | suite |
| `tools/policy.py` | — → mission dispatch guard contextvars (permit check before every mutation), strict-audit fail-closed ledger (A04), mission screenshot withholding, redacted fault text | T03 | I1 `test_mission_policy.py` — 7 PASS |
| `observability/logging.py` | Exception text now redacted (A05) | Secret sink | `test_mission_policy.py` redaction case |
| `runtime/desktop_queue.py` | Grant/cancel race fixed; LeaseHandle (fence+generation); stop_owner honest report; `lease()` context manager | T05, TC-12 | U4 `test_mission_desktop_queue.py` — 7 PASS |
| `runtime/runs_local.py` | user_version bump made monotonic (RF-15) | No schema downgrade | `test_runs_local.py`, I1 |
| `tests/unit/test_velo_contracts.py`, `tests/unit/test_sani_core_app.py` | Alternation test rewritten (documented oracle change, counterexample in oracles file); agents.list expected shape gains additive handshake fields | Interface change per file 03 §3 | suite green |

New host (Rust) / UI:

| Path | Responsibility | Validation |
|---|---|---|
| `sani/src-tauri/src/desktop_control.rs` + main.rs wiring | Process-lifetime EmergencyStop latch (atomic, lock-free, generation counter) + commands; independent of operation mutex | inline tests 3 PASS (TC-34 latch <100ms) |
| `sani/src-tauri/src/missions.rs` | Honest mission-status projection (RF-07: completed turn + NEEDS_APPROVAL ≠ completed), control builders, approval expiry gate, sequence-dedup event projection | inline tests 9 PASS |
| `sani/src-tauri/src/tts_protocol.rs` | Frame validation (bounds, NaN, monotonic sequences, stale generation) | inline tests 7 PASS |
| `sani/src-tauri/src/tts.rs` | Worker supervision: scrubbed env spawn, cooperative cancel, bounded timeout | inline tests 2 PASS |
| `sani/src-tauri/src/tts_queue.rs` | Playback queue: 3-utterance bound, 2 s buffer, stop bumps generation, sink failure → ERROR | inline tests 6 PASS |
| `sani/src-tauri/src/runtime.rs` + `sani_core.rs` | Stable run_id = host message identity (dedup per file 03 §3); run_turn optional stable id | cargo build + suite |
| `sani/src-tauri/python/sani_tts.py` | Output worker (framed protocol, UnspecifiedEngine pre-audition, SilenceEngine test double) | U5 `test_sani_tts_worker.py` — 11 PASS |
| `sani/src/components/MissionStatus.tsx` | Truthful status line; unverified completion labeled; approval display incl. expiry | renderer build PASS |
| `sani/scripts/build-tts.sh`, `sani/tts/pyproject.toml` | Pinned worker env; engine dep intentionally empty pre-audition | script review |
| `scripts/verify_phase1.py` | Acceptance launcher (strict config validation, machine-readable records) | U1 `test_verify_phase1_launcher.py` — 10 PASS |
| `tests/fixtures/voice/audition.txt`, `tests/fixtures/missions/*.json`, `tests/helpers/mission_fakes.py` | Redacted fixtures | committed |

- Final ownership: MissionService (state/budgets/gate) / existing Deep
  Agent (plan/recover/review/chat, role-scoped) / Velo (bounded units) /
  JEV (classifier only) / CUA policy+driver (primitives) / Rust host
  (lifecycle, latch, projection) / worker (TTS only).
- Proof fast route retained: oracle 4 (`test_legacy_parser_performs_zero_
  model_calls`) + `test_exact_action_builds_one_step_plan_with_zero_model_
  calls`; raw Controller mutation unavailable: `test_controller_role_blocks_
  raw_mutation_tool` + `_MUTATION_TOOLS` gate.
- Public interfaces/schema: `jarvis.v1` (file 03 §4) with documented
  compatible adjustment — `recipe_args` added to StepSpec/BoundedWorkItem
  (bounded 1 KiB mechanical plan args; user text still via payload_refs).
- Tables/migrations: 8 mission tables; `mission_schema_migrations` v1 with
  checksum; additive `action_ledger.execution_id` column; restore test =
  `test_migration_is_idempotent_across_reopen` + `test_user_version_not_
  downgraded`.
- Invariants enforced: unique request identity; intent-before-dispatch;
  CAS plan/epoch/attempt; result idempotency (DUPLICATE/STALE/CONFLICT);
  atomic result+step+usage+event; single desktop owner w/ fence+generation;
  budget reservation durability; paid reservations never released.
- IPC compatibility: `agents.list` adds `protocol_version:2`+`features`;
  v1 hosts/cores reject mission execution safely (`mission.*` on a core
  without provider → clean error); mixed-peer tests in `test_mission_ipc.py`.
- Feature flags: `JARVIS_MISSIONS_ENABLED=false`, `SANI_TTS_ENABLED=false`
  (defaults); RSI_MODE immutable.
- Selected model/provider and driver mode: UNCHANGED (no provider code
  touched; `cua_permission_mode` untouched; Terminal deny untouched —
  RF-13: deny remains authoritative, no allow expansion).
- Observer authority: none (read-only, separate sink, contract-level
  authority rejection); experiment budget exactly 0; no runner exists.

## Tests and evidence

| Suite | Command | Scope | Result | Evidence |
|---|---|---|---|---|
| B1 units | `verify_phase1.py --suite unit` (env: CUA_ENABLED=false, fixture manifest, fixture key) | F | **556 PASS, 0 fail** (`tests/unit`) | `docs/verification/phase1/gates/unit.json` |
| I1 missions | `--suite integration` (7 mission integration files) | P | **42 PASS** | `gates/integration.json` |
| P1 perf | `--suite performance` | F | PASS (bounds exact) | `gates/performance.json` |
| R1 Rust | `--suite rust` | F | **113 PASS, 0 fail, 2 ignored** (ignored = pre-existing driver-process tests) | `gates/rust.json` |
| R2 renderer | `--suite renderer` | F | PASS | `gates/renderer.json` |
| Q1 static | ruff: 135 errors in 23 files — ALL in pre-existing files not modified by this run's ownership (velo/verify, velo/recipes, velo/jev, test_velo_controller, cua tests, etc.); **files created/modified by Phase 1 are ruff-clean**. mypy: mission package + touched product files clean; remaining full-repo errors are pre-existing debt (largest: test_velo_controller.py 41, test_sani_core_app.py 12, cua tests). | debt ledger | `docs/verification/phase1/BASELINE.md` |

- Baseline comparison: at start, unit suite was 394 PASS (planning package
  saw 389/5 — the 5 were sandbox socket artifacts, green here). Final:
  556 unit + 42 mission integration = **598 Python + 113 Rust + 1 Rust
  compile gate + renderer build**, all green. Net new tests: 204.
- Negative controls: launcher refuses missing/expired config, wildcards,
  paid units > 0, stale revision, mismatched bundle hash (10 tests);
  oracles 1–3 failed at T01 for the named defects and pass now — recorded
  in `docs/verification/phase1/BASELINE.md`.
- Held-out/negative mutation coverage: alternation exact-stop counts
  (RF-12), probe-based reconciliation negatives, launcher refusals,
  hostile observer payloads.
- Real Sani launch/driver/stop/focus/restart demonstration: **NOT RUN**
  (L1 gate). No wrong-target effects occurred (nothing live dispatched).
- Remaining uncertain side effects: **none** — no live actions were
  performed at any point in this run.
- Rollback: quiesce → `recover_inflight` → flags off → restore
  version-matched host/core bundle; mission tables are additive and
  readable after rollback; no down-migration exists. Rollback procedure
  demonstrated on fixture data: `test_recovery_is_idempotent_and_terminal_
  missions_untouched` + `test_stale_core_wakes_and_must_not_take_over`
  (store-level); full installed-bundle rollback demonstration is part of
  K1 (NOT RUN).

## Voice and performance

- Selected engine: **NONE YET — BLOCKED**. Worker/queue/protocol/supervision
  implemented and offline-tested; engine pinned only after owner-authorized
  audition. See `docs/verification/phase1/VOICE_SELECTION.md` (procedure,
  candidates, licence analysis, corpus at `tests/fixtures/voice/audition.txt`).
- Live metrics (TTS cold/warm first audio, RTF, RSS/CPU, STT contention):
  NOT MEASURED — require the engine + device authorization (V1/P2).
- Performance bounds measured in fixture: packet ≤16 KiB, context ≤4 KiB,
  exception ≤8 KiB, no-progress exact stop counts, budget exhaustion blocks
  exactly at ceiling. These are bound proofs, not latency claims.

## Security, recovery and rollback

- Scope/account/window/path proof: authority denials cover tool scope,
  payload digests, app bundle, origin, account/workspace identity
  (UNKNOWN fails closed), driver generation, lease fence, committed intent;
  artifact verifier refuses traversal/symlink escape.
- Secret sanitation: canary-based tests assert no secret-shaped content
  reaches disk/model/recommendations; exception text redacted (A05);
  mission screenshots withheld (no raw tempfile).
- Crash points exercised: before intent, after intent, after dispatch,
  after result; duplicate effects: **0**; ambiguous dispatch: UNKNOWN +
  STALE, never auto-resend (RF-17).
- Emergency stop: host latch independent of operation mutex (Rust-tested);
  physical held-input release is a live capability — **BLOCKED**, never
  claimed.
- No commit/push/deploy/account/provider/RSI changes: confirmed
  (`git log` unchanged; HEAD `58dac9c`).

## Next safe action

- Phase 1 gates G0…G7:
  - G0 baseline ✔; G1 contracts/persistence ✔; G2 authority/privacy/
    budgets ✔; G3 bounded Velo + one Controller ✔; G4 real desktop stop/
    scope/restart — **fixture-proven, live BLOCKED**; G5 local output —
    **transport ✔, engine BLOCKED**; G6 observer/evidence ✔; G7 packaging —
    **source-path ✔, installed-bundle K1 NOT RUN**.
- Required owner decisions / missing evidence:
  1. Authorize live desktop L1 (approved-test-config.json fixture scope +
     `--allow-live`) to run `test_sani_missions/safety` E2E.
  2. Authorize TTS asset acquisition + participate in the audition
     (VOICE_SELECTION.md step 1–4) to unblock G5.
  3. Commission K1 (isolated installed bundle: provenance, offline voice,
     restart/rollback) and P2 (matched live performance) after 1–2.
- Next action: **run fresh Astra Phase 2 re-plan after owner acceptance of
  the offline Phase 1 evidence**, or authorize the L1/V1/K1 gates first to
  complete acceptance. Do not start Phase 2 implementation without that
  fresh plan.
- Inputs for next planner: this handoff + `docs/verification/phase1/*` +
  original owner documents in `docs/astra/jarvis-next-2026-09-27-58dac9c/`
  + the dirty diff of this worktree (no commit was made).

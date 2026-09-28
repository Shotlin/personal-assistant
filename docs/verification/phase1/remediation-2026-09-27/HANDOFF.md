# Phase 1 corrective-completion handoff (R01–R12)

Filled 2026-09-27 after the independent review
(`review-2026-09-27/00_VERDICT.md`: PHASE 1 IS NOT COMPLETE). This is the
post-remediation handoff; it supersedes the pre-review HANDOFF.md in
accuracy, not in history (that file and all original evidence are retained
untouched). Template: original package file 07.

## Identity and scope

- Repository / worktree: `Shotlin/personal-assistant`,
  `/Users/sayan/Documents/personal-assistant`, branch `main`
- Planning baseline / implementation base HEAD: `58dac9c88018674c2e780086953902f1ea135308`
- Review-time dirty diff hash: `353f3d045e1f98bf9557381205b5e2bd3f05e2821f1942668949a3b9feaa83b7`
  (matches the reviewed snapshot; verified before editing)
- Final branch / HEAD / source binding: `main` / **no new commit** /
  source-manifest SHA256 `007fb39445cd981303d635ea72d421aadb9790c93b6276ff89621db71e74ffc7`
  over **666 files** (tracked + untracked, evidence dirs excluded; recompute:
  `python scripts/verify_phase1.py` helpers — `source_manifest_sha256()`),
  tracked+untracked dirty identity
  `c10e71fcb0762551f30a2cfe89935f8ee5cec77e76c242033f4ef0311ad615ce`
- Evidence set: `docs/verification/phase1/remediation-2026-09-27/`
  (gate JSON+JUnit+logs, mutation trials, static-state records, binding).
  Original gates/, review package, and planning package untouched.
- Assignment: owner-approved corrective completion R01–R12 after the
  review; no Phase 2/3 work; no live desktop/audio/provider/account
  actions (none occurred); no commit/push/deploy.
- **Overall status: IMPLEMENTED — NOT ACCEPTED.** All reproducible review
  defects (RP01–RP12) are fixed with regression tests at the real
  composition boundaries; all offline gates pass; the remaining
  acceptance gates (L1 live desktop, V1 live voice + audition, P2 matched
  live performance, K1 installed-bundle packaging/rollback) are BLOCKED
  pending owner-issued authorization — they are NOT claimed as passed.
- Revision drift: none; the working tree matched the reviewed snapshot
  before any edit.

## Repaired review defects (probe → regression test → result)

| Probe | Repair (root cause) | Regression test (real boundary) |
|---|---|---|
| RP01 plan adapter invalid | `_DeepInvoke` runs the real graph with an invocation-local submission capture; model MUST call `submit_mission_plan/recovery/review` (bound into the graph when missions enabled); no raw-text fallback; role-namespaced threads with mission/plan identity | `test_rp01_production_planning_adapter` — real graph + scripted model + real submission tool |
| RP02 role bypass | `controller_role_var` lives in `tools/policy.py`; the REAL wrapped-tool dispatch refuses PLAN/RECOVER/REVIEW/INFO roles before any dispatch; INFO also blocks observation (questions acquire nothing) | `test_rp02_role_refusal_at_real_policy_boundary`, `test_info_role_blocks_desktop_acquisition`, `test_chat_role_still_dispatches` |
| RP03 no-ledger dispatch | strict mission mode refuses mutations when NO ledger is bound (previously only failed writes); executor binds a real `MissionActionLedger` per execution via the runtime scope | `test_rp03_strict_mode_without_any_ledger_refuses` + existing failed-write test |
| RP04 refused click = success | refusal replies (`reply.ok=False`) yield FAILED/UNKNOWN/PERMISSION_DENIED with zero postconditions; fast missions carry recipe-appropriate REQUIRED checks (open_app → `app_foreground`); store refuses SUCCEEDED with missing/failed required checks | `test_rp04_refused_click_never_completes`, `test_completed_result_missing_required_check_is_conflict`, `test_failing_required_check_overrides_success` |
| RP05 forged scope hash | `Scope.scope_hash` is ALWAYS recomputed from canonical contents; `_validate_proposal` checks containment (`Scope.contains`), so copying a hash cannot broaden scope | `test_rp05_copied_scope_hash_cannot_broaden_plan` |
| RP06 empty/stale observation | authority requires a fresh (≤10 s) observation with a REAL recorded timestamp (recorded when the driver answered, never stamped at authorize time); unnamed surface + app-scoped packet → SCOPE_MISMATCH/STALE_TARGET | `test_rp06_empty_observed_app_denied`, `test_stale_observation_denied` |
| RP07 pause strands RUNNING | PAUSE/REVISE return RUNNING steps to PENDING (attempts stay cancelled — uncertainty survives); resume re-claims as a NEW attempt | `test_rp07_pause_resume_makes_progress` (kill/restart integration) |
| RP08 raw secret goal persisted | `claim_request`/`record_goal_revision` REFUSE secret-shaped text (memory-policy matcher) before any persistence | `test_rp08_secret_goal_refused_before_persistence` |
| RP09 tampered evidence passes | `EvidenceStore.load` verifies SHA256 + ownership + expiry; `verify` binds refs to the mission and the packet scope hash; `artifact_readable` roots come from trusted configuration only (never check payloads) + magic-byte content checks | `test_rp09_tampered_evidence_fails_verification` |
| RP10 skipped→PASS | launcher parses JUnit: any skip → BLOCKED, zero cases → BLOCKED; live suites refuse missing/invalid config; suites resolve to real files; validated config propagates via env to the runner; fixture env is an explicit allowlist | `test_all_skipped_suite_is_blocked` + 20 launcher tests |
| RP11 zero-budget Deep call | service reserves a Deep call DURABLY before every plan/recovery invocation; zero/exhausted budget → planning refused with an honest BLOCKED event (never an invocation); reservations survive restart | `test_rp11_zero_deep_budget_blocks_planning`, `test_rp11_zero_deep_budget_blocks_service_planning`, `test_deep_call_reservation_persists_across_restart` |
| RP12 unfenced concurrent claims | the service acquires the ONE desktop queue lease BEFORE `claim_step` and stamps the real fence + process generation into the packet; `SaniRuntime.run_scope` (real DesktopSessionManager, action lock, single owner) is in every dispatch path; the Rust latch gates run ADMISSION | `test_rp12_claims_carry_real_fences_and_generation` |

All five negative-control mutation trials pass: disabling the surface-check,
required-check completeness, role refusal, recipe gate, or Deep reservation
makes the corresponding test FAIL; restore verified green
(`mutation-trials.json`).

## Additional repairs (findings F01–F13)

- **F07/F08**: REVISE creates a new versioned plan from the Controller;
  control signals the live executor token; startup `recover_inflight` runs
  in the single resource graph. ONE `CoreResources` graph builds the
  registry, mission entry, and mission IPC provider over the same
  provider/Deep/service (approvals/budgets durable across calls); the
  process closes it once (`__main__.py`). Reconciler: RESULT_APPLIED now
  reports the SAVED outcome; ALL external IDs must resolve (all-NO_EFFECT
  → retriable, any CONFIRMED → confirmed, else UNKNOWN).
- **F05 (Velo)**: per-step tool catalogs (`_tool_ids_for` on the selected
  step, not the mission); exact payload plumbing via the screened
  `mission_payloads` vault with digests bound into the action scope; the
  generic Submit/Send candidate fallback removed (objective-token matches
  only); `type_text`/`set_value` conservatively classified EXTERNAL_WRITE.
- **F09**: `runtime.rs::outcome_of` distinguishes mission results —
  NEEDS_APPROVAL/PAUSED/BLOCKED/WAITING_EXTERNAL are `mission_pending`,
  never completed; truthful `input_origin` flows host→core
  (typed_final/voice_final via begin_turn/stream_turn/run.start); Rust
  commands `mission_control_cmd/mission_get_cmd/mission_events_cmd` route
  through a synchronized control channel in the stream loop (one reader/
  writer, out-of-band Esc cancel unaffected); renderer bridge gains
  `missionControl/missionGet/missionEvents` and `MissionStatus` is mounted
  in MainConversation.
- **F10**: `TtsState` (supervisor + speech queue) is managed host state;
  `speech.stop` command bumps the generation and clears queue/audio; a
  real `CpalSink` (bounded ring, underrun silence, health flag) feeds
  cpal output with the !Send stream isolated in a guard; PTT/barge-in
  interlock stops playback before capture admission; the worker spawn uses
  an env ALLOWLIST (`env_clear` + minimal vars); mission COMPLETED speaks
  only a bounded template after the gate. Engine/audition: still BLOCKED
  (owner gates) — the worker defaults to the explicit
  `UnspecifiedEngine` refusal; no cloud fallback exists.
- **F11**: mission goals/revisions/payloads screened; evidence integrity
  enforced; the read-only Observer now runs on real committed events after
  finalization (failures logged, never blocking), writing to a separate
  sink outside the process cwd; retention documented in TRACE_SCHEMA.md.
- **F12/F13**: launcher rework above; `dirty_diff_sha256` and
  `source_manifest_sha256` now cover tracked + untracked sources with
  evidence dirs excluded (binding stable across successive runs —
  verified); mypy restored to exactly the untouched baseline.

## Gates G0–G7 (post-remediation)

| Gate | Verdict | Evidence |
|---|---|---|
| G0 baseline | PASS (offline) | `BASELINE.md` + `binding.json` (713→666 files bound after excluding evidence dirs) |
| G1 contracts/persistence | PASS (offline) | U1: contracts 25, store 20; I1 sqlite 7; migration v2 ledger; RP05/RP07/RP09 regressions |
| G2 authority/privacy/budgets | PASS (offline) | U2: authority 21, evidence 12, budgets 12 incl. RP03/RP05/RP06/RP11 + mutations |
| G3 bounded Velo + one Controller | PASS (offline) | U3/U4: executor 13, verifiers 7, controller 7, service 13 incl. RP01/RP02/RP04 |
| G4 desktop stop/scope/restart | PASS (offline) / **live BLOCKED** | queue 7, desktop-control 8, RP07/RP12; Rust latch tests; L1 requires owner config |
| G5 voice output + input preservation | PASS (offline transport/interlock) / **engine+audition BLOCKED** | worker 11, Rust tts/protocol/queue 15; engine selection requires owner asset authorization |
| G6 observer/evidence | PASS (offline) | observer 10 + runtime connection tests; RP08/RP09; TRACE_SCHEMA.md |
| G7 packaging/perf/handoff | PASS (offline) / **K1/P2 BLOCKED** | launcher 26, provenance 3, performance 7; installed-bundle + matched live metrics need owner scope |

## Exact final results (remediation-2026-09-27/gates/, JUnit retained)

| Suite | Cases | Result |
|---|---|---|
| unit | 585 | PASS, 0 skipped |
| mission integration (8 files) | 47 | PASS, 0 skipped |
| performance bounds | 7 | PASS, 0 skipped |
| Rust (offline/locked) | 113 | PASS, 2 ignored (pre-existing driver-process tests) |
| renderer build | — | PASS |
| Python total | 647 (+ Rust 113) | all green |

- Static state: **mypy 91 errors in 12 files — byte-identical to the
  untouched planning baseline (0 introduced, 0 removed; the 28 review
  additions are fixed)**. Ruff: remaining errors are ALL in pre-existing
  velo/cua files (planning-baseline debt, listed per-file in ruff-after.txt);
  every file created or touched by Phase 1 is ruff-clean.
- Mutation trials: 5/5 graders effective, restore verified.
- Live desktop/voice/performance/packaging (L1/V1/P2/K1): NOT RUN —
  BLOCKED on owner-issued `approved-test-config.json` + `--allow-live`
  (desktop/safety/voice/packaging E2E files exist and are
  authorization-gated; the launcher reports BLOCKED without it).

## Rollback

Quiesce → flags off (`JARVIS_MISSIONS_ENABLED=false`, `SANI_TTS_ENABLED=false`
— both default off) → mission tables are additive and remain readable; no
down-migration exists; the pre-mission shell is the exact HEAD `58dac9c`
behavior when flags are off. Uncertain attempts are reconciled by
`recover_inflight` at next startup before any new action. Data-preserving:
nothing deletes mission/evidence rows.

## Remaining blockers (exact)

1. **L1** — owner-issued isolated desktop fixture config + `--allow-live`
   to run `test_sani_missions/safety` against a real driver.
2. **V1/G5** — owner authorizes TTS asset download (gated terms accepted
   by owner, not agent) and completes the audition
   (VOICE_SELECTION.md); then pin engine/lock and measure.
3. **P2** — matched live baseline/candidate performance on target hardware.
4. **K1** — isolated installed-bundle provenance + offline voice +
   same-bundle rollback.

No wrong-target effect, secret leak, post-stop dispatch, duplicate effect,
or forged approval occurred or was reproduced in this run. No live action
was performed. Phase 2 remains gated on a fresh re-plan after owner
acceptance.

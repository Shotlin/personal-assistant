# Phase 1 corrective-completion handoff (C01–C10)

Filled 2026-09-28 by the implementation run authorized to execute C01–C10 of
`docs/verification/phase1/review-2026-09-28/02_PHASE1_REPAIR_PLAN.md` (the
remaining original Phase 1 work after the NOT-ACCEPTED verdict). Template:
original package file 07. Phase 2 was NOT started; Phase 3 untouched.

## Identity and scope

- Repository / worktree: `Shotlin/personal-assistant`,
  `/Users/sayan/Documents/personal-assistant`, branch `main`
- Baseline / implementation HEAD: `58dac9c88018674c2e780086953902f1ea135308`
  (**no new commit** — no commit/push/deploy was authorized or performed)
- Source binding (recomputed after the LAST implementation change):
  source-manifest SHA256 `d58f393e21a3ab563754328150f20c5a1eb352c44d851faf09c9f451e9ff358a`
  over **668 files** (tracked + untracked, evidence dirs excluded); dirty
  identity `d17bdf43b8ea53ef6fe1dfad6ae8af326bba3100eff2e5648923a887f597b71f`.
  The reviewed snapshot's manifest `007fb394…` / dirty `c10e71fc…` covered the
  pre-completion working tree; both originals are preserved untouched in
  `review-2026-09-28/` and `remediation-2026-09-27/`.
- Evidence set: `docs/verification/phase1/completion-2026-09-28/` (gates with
  fresh JUnit + launcher JSON, probe replay, mutation trials, static outputs,
  binding). All prior evidence directories untouched.
- **Overall status: IMPLEMENTED (fixture-verified) — NOT ACCEPTED.** Every
  offline gate passes with zero skips; all 12 independent probe defects are
  repaired and re-verified; the live acceptance gates (L1 desktop, V1 voice,
  P2 matched live performance, K1 packaging) remain **BLOCKED pending
  owner-issued authorization** — they are NOT claimed as passed, and their
  E2E bodies now assert scope truthfully instead of passing empty.

## C-task status (each: repair → regression test at the real boundary)

| Task | Repairs (source of truth: findings N01–N12) | Regression tests | Status |
|---|---|---|---|
| C01 acceptance infra (N12) | `run_suite` requires exit 0 + FRESH JUnit (cleared first) + expected case inventory; strict pytest detection; composition suite in the integration gate; E2E bodies now validate the authorization scope per case then report explicit BLOCKED with the missing capability named — no empty bodies | `test_nonzero_exit_cannot_pass_on_stale_junit`, `test_pytest_report_must_be_fresh`, `test_missing_expected_cases_is_blocked`, `test_integration_gate_includes_composition_suite`, `test_live_suites_declare_expected_case_inventory` | DONE (fixture-verified) |
| C02 resource graph + entry routes (N03/N04) | Production executor gets store + payload resolver + ownership probe; `run_scope` installs a default NO-MUTATION guard whenever missions are enabled and no mission guard is bound (the selectable Deep entry cannot mutate outside a mission — zero effects proven); bounded discovery bootstrap (whole-inventory reads; targeted reads scope-checked the moment an observation names a surface); real `mission_allowed_apps` settings field; approvals restored from durable store state | `test_np01_production_executor_dependencies`, `test_deep_selected_actions_route_through_mission_authority` (rewritten to the NP11 shape), `test_np07_discovery_bootstrap`, `test_scope_for_uses_trusted_settings` | DONE (fixture-verified) |
| C03 uncertainty preservation (N01/N02/N05) | PAUSE/REVISE/CANCEL separate attempt cancellation from effect outcome: DISPATCHED → RECONCILING (never replayable CANCELLED), step BLOCKED until settled; claim refuses steps with unresolved attempts; Reconciler aggregates ALL external IDs (any CONFIRMED/UNKNOWN → not retriable); startup consumes `recover_inflight` ids AND leftover RECONCILING attempts through the real reconciler; RESUME settles uncertainty first, releases steps only on proven NO_EFFECT within retry budget, then schedules `run_ready` with a FRESH token | `test_np02_pause_never_resets_dispatched_effect`, `test_np02_resume_with_proven_no_effect_retries`, `test_np03_reconciliation_aggregates_all_operations`, `test_np03_all_no_effect_is_required_for_retry`, `test_recovery_is_idempotent_and_terminal_missions_untouched` (kept green) | DONE (fixture-verified) |
| C04 Controller roles + durable budgets (N05/N08) | ONE mission/version-bound transport for PLAN/RECOVER/REVIEW/revision (run-registered, factory-derived when detached); every Controller call reserves Deep durably BEFORE invocation and settles ONCE (failed calls still consumed; no add_usage double-charge — NP10 consumed exactly 1); JEV reserved before every decide (zero budget → zero calls → escalation); per-dispatch durable action/observation reservations at the wrapped-tool boundary (reserve → settle consumed/release); advisory final REVIEW invoked and recorded, never decisive | `test_np04_recovery_uses_reserved_transport`, `test_np05_revision_is_screened_then_planned`, `test_np10_one_planning_invocation_consumes_one_unit`, `test_np09_zero_jev_budget_makes_no_call`, `test_rp11_zero_deep_budget_blocks_service_planning` (kept green) | DONE (fixture-verified) |
| C05 bounded outcomes (N07) | `_fast_checks_for` covers all six recipes (trusted zero-model verifiers incl. new `field_value`/`window_visible`); claims select the CLAIMED step's catalog inside the claim transaction; payload digest bound separately from action-argument digest (payload-bearing tools only); preconditions evaluated BEFORE dispatch (fail-closed; postconditions no longer mis-copied as preconditions); Deep proposals without a required check on an actionable step are REFUSED; approved typing path: fast type_text plans EXTERNAL_WRITE, scope widens explicitly (never DESTRUCTIVE), approval consumed at exact digest, RESUME carries live approvals across the epoch and re-queues the blocked step within retry budget | `test_np06_required-checks shape (probe replay)`, `test_np08_second_step_receives_its_own_catalog`, `test_actionable_proposal_without_required_checks_is_refused`, `test_precondition_failure_blocks_with_zero_effects`, `test_fast_typing_plan_is_external_write_with_payload_ref`, `test_required_check_failure_blocks_completion` (kept green) | DONE (fixture-verified) |
| C06 desktop ownership + stop (N09) | DesktopQueue strictly single-flight (duplicate same-run acquire queues — no second fence); fence-aware release (stale releaser refused, newer lease survives); executor reads CURRENT queue ownership at the final dispatch boundary via injected probe — superseded lease refuses STALE_TARGET; Rust latch RE-CHECKED after operation-lock admission (a stop during the wait wins); emergency stop actually stops speech (TTS queue cleared, reported); physical held-input release and human-takeover detection remain live-gated (documented below) | `test_concurrent_same_run_acquire_cannot_double_grant`, `test_stale_releaser_cannot_release_newer_lease`, `test_release_with_wrong_fence_is_refused`, `test_superseded_desktop_lease_refuses_dispatch`, Rust `desktop_control` latch tests | DONE (fixture-verified; physical stop latency = L1) |
| C07 privacy + retention (N06) | REVISE text/refusals screened with the memory-policy matcher BEFORE persistence — a secret-shaped revision refuses the whole control transaction (never stored, never reaches the planner); non-REVISE controls proceed with `[withheld by privacy policy]` so safety controls are never blocked by their own reason text; retention sweep implemented: expired evidence deleted with tombstone events, investigation holds preserved, orphan rows/files cleaned with retention-log records, Observer copies pruned with tombstones; trace events are never silently deleted (chain-detection guarantee documented over auto-expiry) | `test_np05_secret_revision_refused_before_persistence`, `test_retention_deletes_expired_evidence_with_tombstone`, `test_retention_preserves_investigation_holds`, `test_retention_removes_orphan_rows_and_files` | DONE (fixture-verified) |
| C08 mission UI + controls (N10) | `messages.mission_id` persisted (additive migration) and read back; agent-done payload + `ChatMessage`/`AgentDone` types carry mission correlation; MainConversation reads correlation from the PERSISTED row (history/reopen included) in effects — never during render — subscribes to live agent-done events, and pause/resume/cancel buttons call the real `mission.control` IPC with CAS plan/epoch then refresh; renderer build + full Rust suite green | `test_deep...`/Rust history tests + renderer typecheck/build; mounted-flow verification remains part of L1 (no physical UI automation in this run) | DONE (fixture-verified; mounted live-flow = L1/K1) |
| C09 TTS plumbing (N11) | Worker env = `env_clear` + 5-variable allowlist (credential-free by construction, `python -I`); host init gate behind `SANI_TTS_ENABLED` opens the real CpalSink (device-rate resampling, bounded ring) and starts the worker + event pump; production speech path: `enqueue_speech` → framed request → chunk pump → queue → sink; completed-turn bounded acknowledgment wired; `speech_say_cmd` registered; `sani/tts/uv.lock` created (offline, zero deps) and `uv sync --frozen` verified; stop lifecycle honest (no worker → false). Engine/voice/audition remain owner decisions — worker answers `error` until then, never fake audio | `worker_env_allowlist_is_credential_free`, `stop_worker_without_worker_is_false`, 11 worker/protocol unit tests, full Rust suite | DONE (fixture-verified; engine/audition = V1, BLOCKED) |
| C10 rerun + handoff | Full gate rerun (below), 12-probe replay ALL corrected, 7/7 mutation trials FAIL-as-required in a disposable copy (removed afterwards; repo untouched), static outputs baseline-identical, binding recomputed, this handoff | this document + evidence dir | DONE |

## Exact final results (completion-2026-09-28/gates/, fresh JUnit retained)

| Suite | Cases | Result |
|---|---|---|
| unit (launcher-run) | 610 | PASS, 0 skipped |
| integration incl. composition (launcher-run) | 58 | PASS, 0 skipped |
| performance bounds (launcher-run) | 7 | PASS, 0 skipped |
| Rust (offline/locked) | 114 | PASS, 2 ignored (pre-existing driver-process tests) |
| renderer build | — | PASS |
| Python total | 675 (+ Rust 114) | all green — machine counts from JUnit, not prose |

- **Probe replay (evidence/completion-probes.json): ALL 12 corrected** —
  NP01 bound deps; NP02 dispatched-external attempt stays RECONCILING and
  claim refuses; NP03 both operations probed, CONFIRMED, not retriable;
  NP04 RECOVER invoked with exactly 1 Deep unit consumed; NP05 revision
  refused by privacy policy, sentinel never persisted; NP06 1 required check
  for every recipe; NP07 discovery bootstraps; NP08 scroll step gets the
  scroll catalog; NP09 zero JEV budget → 0 provider calls; NP10 exactly 1
  consumed Deep call; NP11 zero mutations through the selectable Deep route;
  NP12 exit-7 child with stale JUnit → FAIL.
- **Mutation trials (evidence/mutation-trials.txt): 7/7 FAILS-AS-REQUIRED**
  (default mission guard, required-check completeness, role refusal, Deep
  reservation, reconciliation aggregation, superseded-lease refusal,
  revision screening) — each guard disabled in a disposable source copy, its
  oracle failed, copy discarded, repository untouched.
- **Static state: mypy 91 errors in 12 files — diagnostic multiset
  byte-identical to the planning baseline (verified by sorted diff; 0
  introduced, 0 removed). Ruff 42 diagnostics — per-file/code multiset
  identical to the review baseline; every file created or touched by this
  run is ruff-clean.** Full outputs in evidence/mypy-after.txt and
  evidence/ruff-after.txt.

## Live gates (exact, unchanged ownership)

1. **L1** — owner-issued isolated desktop fixture config + `--allow-live`
   for `test_sani_missions/safety` (real driver, physical stop, wrong-focus).
2. **V1/G5** — owner authorizes TTS asset/engine decision and audition
   (VOICE_SELECTION.md); the worker defaults to `unspecified` and answers
   `error` until then. All independent plumbing is complete.
3. **P2** — matched live baseline/candidate performance on target hardware.
4. **K1** — isolated installed-bundle provenance + offline voice +
   same-bundle rollback.

The E2E bodies now validate the authorization scope per case (wrong config
fails loudly) and report explicit BLOCKED with the missing capability named
when the physical harness is absent — they cannot pass as empty.

## Known remaining gaps (honest)

- Exact approval presentation in MissionStatus (action/scope/expiry detail
  panel) needs a core `mission.approvals` listing surface; status + controls
  + durable correlation are complete.
- WAITING_EXTERNAL bounded polling has no P1 production caller (no cloud
  external operations exist in Phase 1); the status and store support are
  reserved, and the boundary is documented rather than hidden.
- Human-takeover event-tap detection and verified held-input release are
  native live-gate work (driver capability evidence required).
- Metadata (trace-event) retention beyond evidence deletion requires an
  owner-approved tombstone+truncate design; hash-chain deletion is
  deliberately NOT automatic.

## Rollback

Quiesce → flags off (`JARVIS_MISSIONS_ENABLED=false`, `SANI_TTS_ENABLED=false`
— both default off) → additive mission tables and the new additive
`messages.mission_id` / migration v3 columns stay readable; no down-migration;
the pre-mission shell is the exact HEAD `58dac9c` behavior with flags off.
Uncertain attempts reconcile at next startup before any new action.
Data-preserving: nothing deletes mission/evidence rows except the explicit
retention sweep (expiry + holds + tombstones).

## Next safe action

Owner reviews this handoff and issues the L1/V1/P2/K1 authorizations (or
accepts Phase 1 on the fixture-verified record). **Do not start Phase 2
automatically**: a fresh repository-based Phase 2 re-plan requires owner
acceptance of Phase 1 first.

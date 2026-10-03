# Phase 1 corrective completion — working notes (C01–C10)

Owner-authorized implementation run, 2026-09-28. Baseline HEAD 58dac9c88018674c2e780086953902f1ea135308,
source manifest 007fb394…ffc7 (666 files), dirty identity c10e71fc…15ce. NO commits/pushes.
Governing docs: review-2026-09-28 (N01–N12, C01–C10), review-2026-09-27 (F01–F13, R01–R12),
remediation-2026-09-27 HANDOFF, planning package 03/04/06/07.

## Confirmed defect map (from code read + probes)

- N03 agents.py:356 `registry.register(deep)` unconditional; DeepAgentEntry._run uses bare
  run_scope → mission guard None → mutations pass outside missions (NP11: 1 synthetic mutation).
  FIX: SaniRuntime.run_scope installs a default no-mission mutation-denying guard when
  jarvis_missions_enabled and no explicit guard passed (policy.py boundary enforcement).
- N04 agents.py:431 VeloExecutor built w/o store/payload_resolver (NP01 false/false).
  FIX: pass store; bind payload resolver after service construction.
- N04 store.py:846 action_scope.permitted_effects={spec.effect_class} only; executor
  _effect_for_tool classifies list_apps READ_ONLY → NP07 SCOPE_MISMATCH on first discovery.
  authority.py:141 requires captured_at_ms>0 even for discovery; fresh run has none.
  FIX: discovery bootstrap — inventory/read tools get bounded READ discovery authority
  (still tool-catalog-bound, still zero trust for mutations); permitted_effects for a step
  = {READ_ONLY, spec.effect_class}; observation timestamp required only for scoped actions,
  not bootstrap discovery. Mission scope widens to EXTERNAL_WRITE only when plan needs it
  (approved typing path via owner approval; _scope_for must stop reading nonexistent
  mission_allowed_apps → trusted acquisition interface).
- N07 service.py:561 _fast_checks_for only open_app (NP06 zeros). service.py:349
  _tool_ids_for first non-semantic recipe (NP08 scroll step got open_app tools).
  FIX: claims must be per-STEP (claim_step already takes step_id param; service must pass
  the selected step's recipe catalog + its payload digests within one claim op);
  _fast_checks_for must cover navigate/search_browser/scroll/type_text/press_ordinal;
  _validate_proposal must require ≥1 required check for actionable steps;
  executor verification must run even when... executor only verifies if expected_postconditions
  present — with required checks now always present for mutating recipes, fine.
  authority.py:99 args_digest vs payload_digests conflation → separate payload digest check.
- N05 service.py:394 recover w/o invoke; _replan:474 plan w/o invoke (NP04/NP05 RuntimeError).
  control(RESUME) only flips DB; no run_ready scheduling. FIX: MissionService keeps
  mission-bound Deep transport factory (from MissionEntry._DeepInvoke) — stored per mission
  in submit; _escalate/_replan reserve Deep budget + use it; RESUME schedules run_ready with
  fresh token; REVISE validates before commit_plan; service must expose an invoke-provider
  hook set at construction (CoreResources.mission_entry passes a factory).
- N06 store.py:1264 control() event carries raw revision/reason (NP05 secret persisted).
  FIX: screen with memory.policy.contains_secret before append; refuse or redact (refuse,
  consistent with claim_request), keep digests.
- N01 store.py:1222 PAUSE cancels INTENT_COMMITTED/DISPATCHED attempts + resets step PENDING
  (NP02: attempt 2 claimed while reconciliation UNKNOWN). recovery.py:67 CANCELLED=final.
  FIX: PAUSE/REVISE marks DISPATCHED/INTENT_COMMITTED attempts 'RECONCILING' (uncertainty
  preserved), step returns PENDING; claim_step must refuse a new claim for a step whose
  latest attempt is RECONCILING with unresolved effects (blocked until reconciled NO_EFFECT);
  resume path reconciles first (service.control RESUME → reconcile → schedule run_ready).
- N02 recovery.py:83 returns on first NO_EFFECT (NP03 retriable=true with CONFIRMED second).
  FIX: probe ALL ids; any CONFIRMED/UNKNOWN → not retriable; all NO_EFFECT → retriable.
  agents.py create_service:427 ignores recover_inflight ids → run Reconciler, persist outcomes,
  BLOCKED missions stay blocked.
- N08 executor.py:502 jev.decide w/o reservation (NP09 zero-budget call). service._plan
  settle(consumed=True)+add_usage deep_calls:1 → NP10 consumed=2. FIX: settle_reservation
  already moves reserved→consumed; service must NOT add_usage again (count once).
  Executor: reserve JEV (and actions/observations) via authority.reserve before decide,
  clamp to mission remaining, zero → no call. Reused call_key must not authorize new effect
  (reserve_budget returns existing CONSUMED reservation → must be rejected as spent for a
  NEW retry: check .consumed and raise/require new key).
- N09 executor.py:671 driver_generation=item.driver_generation (packet self-copy).
  authority.py:205 compares stored attempt fence w/ packet. desktop_queue.py:109
  `owner is None or owner == run_id` regrants new fence to same run_id; release by owner id.
  FIX: queue — acquire always queues when owned (no same-owner fast path); release takes
  fence and refuses stale fence. Executor guard: probe CURRENT queue owner/fence/generation
  via injected probe (service supplies); refuse STALE_TARGET when fence/generation changed.
  Rust sani_core.rs:1881 latch only before operation_guard → recheck after admission + at
  dispatch. Emergency stop connects TTS queue generation bump.
- N10 MainConversation.tsx:24 casts ChatMessage→mission_id (not supplied); no onControl;
  fetch during render. FIX: Rust StoredMessage + history supply mission_id; tauri.ts bridge
  missionControl/missionGet/missionEvents (exists? check); MissionStatus subscribe outside
  render, onControl wired pause/resume/cancel/revise/approve.
- N11 tts.rs:120 permanent NullSink; no worker start/speech enqueue path; worker spawner:53
  removes 4 vars not env_clear allowlist; uv sync --frozen w/o sani/tts/uv.lock.
  FIX: wire SANI_TTS_ENABLED gate → supervisor.start + speech enqueue + CpalSink; env_clear
  allowlist; production path connected; uv.lock created (offline, no asset download);
  STT interlock preserved.
- N12 verify_phase1.py:409 trusts JUnit w/o exit-0/fresh report (NP12 stale PASS); integration
  list 7 files missing test_mission_composition.py (8 cases; 639 vs 647); e2e bodies empty.
  FIX: run_suite requires exit 0 + fresh JUnit in unique dir + expected case inventory;
  E2E bodies → real BLOCKED (config-gated) with named reason; composition tests added to
  integration gate.

## Plan of execution
C01 → C02+C03 (composition/uncertainty) → C04 (budgets/transports) → C05 (checks/catalogs)
→ C06 (queue/latch) → C07 (privacy) → C08 (UI) → C09 (TTS) → C10 (gates/evidence/handoff).
Regression tests at real boundaries for every fix; mutation trials in disposable copy.
New evidence: docs/verification/phase1/completion-2026-09-28/ (this dir).

## FINAL STATUS (2026-09-28, end of run)

C01–C10 complete. HANDOFF.md in this directory is the authoritative record.
- Gates (fresh JUnit, launcher-run): unit 610, integration 58, performance 7 —
  PASS 0 skipped; Rust 114 (2 pre-existing ignores); renderer build PASS.
- Probe replay NP01–NP12: all 12 corrected (NP02: claim returns None/refuses;
  attempt stays RECONCILING).
- Mutation trials 7/7 FAILS-AS-REQUIRED in a disposable copy (removed after).
- mypy 91 = planning baseline multiset identical; ruff 42 = baseline identical;
  touched files ruff-clean.
- Binding recomputed after last change: manifest d58f393e…f358a (668 files),
  dirty identity d17bdf43…b71f, HEAD unchanged at 58dac9c. No commits/pushes.
- Not accepted: L1/V1/P2/K1 live gates remain owner-blocked; E2E bodies now
  validate authorization scope and report explicit BLOCKED. Phase 2 NOT started.

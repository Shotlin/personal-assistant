# Corrective completion of Phase 1 — proposed, awaiting owner approval

**Goal:** complete original Phase1 T01–T12 and gates G0–G7 on actual Sani, repairing the reproduced integration and safety failures. This is the remainder/repair of Phase1, not Phase2 or a replacement architecture.

**Architecture:** one shared sani-core resource graph; existing Deep in enforced roles, existing Velo/JEV as bounded executor, deterministic scope/budget/ledger/verification, preserved native session ownership and Rust host. UI/history and audio consume actual mission events. Observer remains read-only and experiments absent.

**Spec:** original package `/Users/sayan/Documents/personal-assistant/docs/astra/jarvis-next-2026-09-27-58dac9c/`, especially03/04/06, plus this review's findings and probes. Preserve existing implementation and unrelated dirty work; no reset/rewrite, commit/push/deploy, provider/account change or live actions without their separate authorization. Owner has requested analysis first; this plan is NOT authorization to start.

**Execution method:** after owner authorization, native implementation may follow `superpowers:executing-plans`; no subagent delegation is required or implicitly authorized. Tasks are reviewable units of the same Phase1. Each follows a failing behavioral test → smallest compatible production fix → focused regression → evidence. No commits unless separately authorized.

## Review focus

- The production call graph must use the exact guards tested; mocks replace external devices/models only, never the safety/composition layer under test.
- Cancellation between observation, permission, budget and dispatch must invalidate the same authority token all the way to the native action.
- A missing/refused/late result cannot turn into success or a safe-retry assumption.
- A source/asset/evidence identity must include actual content and scope, including untracked files and changed evidence.
- User correction, spoken interruption and app restart must preserve intent and uncertainty without duplicating effects.

## R01 — Repair evidence runner and freeze the current dirty baseline

**Files:** `scripts/verify_phase1.py`, `tests/unit/test_verify_phase1_launcher.py`, `tests/e2e/test_sani_{missions,safety,voice}.py`, `tests/performance/test_mission_budgets.py`, `docs/verification/phase1/`; NEW explicit packaged-app test/harness files as needed.

**Interface:** validated config→isolated runner→per-case JUnit/results + portable source/bundle/evidence manifests. Keep one launcher, correct desktop suite mapping, distinguish fixture performance vs live measurement.

- [ ] Add regression `test_all_skipped_required_suite_is_blocked` from RP10; missing expected cases also BLOCKED.
- [ ] Add config propagation, strict unknown/types, approved-root containment/symlink, bundle-directory manifest, tracked+untracked hashing and changed-source rejection tests.
- [ ] Make fixture environment isolated; config must select only disposable approved data/profile/apps; no hidden live resources. Retain historic gate files, write new evidence directory.
- [ ] Replace unconditional E2E skips with honest explicit unavailable status until real harness cases land; never claim unavailable runner as complete.
- [ ] Record introduced-vs-baseline type failures and source manifest before any fix.

**Acceptance:** no all-skipped PASS; changing any new source invalidates evidence binding; no live execution without exact allowed fixture. **Depends:** none. **Rollback:** retain original runner/evidence and source snapshot, no data migration. **Stop:** inability to isolate live profile or authority.

## R02 — Build one resource graph and enforce role boundaries

**Files:** `core/{__main__,agents,runtime,registry,app}.py`, `agent/{build,context,system_prompt}.py`, `missions/{controller,service}.py`, `tools/policy.py`; existing/new core/controller integration tests.

**Interfaces:** shared RuntimeProvider/MissionService/store/authority; invocation-local mission/version/role; trusted structured submit plan/recovery/review tools.

- [ ] Turn RP01 and RP02 into tests of real registry/adapter/graph→wrapped tools using a scripted model backend.
- [ ] Replace incompatible raw wrapper with valid structured submissions; enforce capabilities at binding AND dispatch; route explicit Deep-selected actions through mission authority.
- [ ] Reuse service for run and mission IPC; no per-request lost approvals or duplicate runtime graph; initialize/close resources once safely.
- [ ] Pure information path must not acquire/probe desktop; natural action questions still resolve as actions requiring scope. Test role leakage across two runs and unique mission/version threads.
- [ ] Supply recovery/review transport and reserve invocation budget through R04 before any production enablement.

**Acceptance:** valid real adapter plan works, forged role invocation has zero effects, one graph/service lifetime. **Depends:** R01. **Rollback:** feature off after quiescence, preserve old stack. **Stop:** changed provider/role bypass required.

## R03 — Canonical authority, scope, effect and approval enforcement

**Files:** `missions/{contracts,authority,service,executor}.py`, `tools/policy.py`, `core/app.py`; authority/policy tests.

**Interfaces:** host-confirmed Scope + actual ScopeObservation + immutable bounded intent→single-use permit; trusted action effect catalog and durable exact approval.

- [ ] Add RP05/RP06 negatives plus scope-sensitive read, stale/cross-account/origin/fence, action-vs-payload digest and post-await revocation tests.
- [ ] Recompute/validate scope hashes and all contained authority; cannot broaden scope by copying a hash. Empty/unknown identity is not wildcard. Bind actual selected target rather than nonexistent Settings attribute.
- [ ] Validate current mission epoch/status, action parameters, target freshness, actual fence/generation immediately before effect; deriving timestamp from now is not observation.
- [ ] Classify typing/submit and non-idempotent operations conservatively; no blanket repeatable-local or mislabeled READ_ONLY click. Preserve deterministic native policy ceilings.
- [ ] Persist and retrieve exact owner approvals across service calls/restart; trusted IPC identity validates owner/scope, not just field replacement.

**Acceptance:** forged/copied scope cannot authorize a single mutation/read; correct scoped reversible action still works. **Depends:** R02. **Rollback:** disabled new path, never bypass deny through legacy. **Stop:** identity/approval cannot be proven.

## R04 — Actual per-action ledger and durable budget reservations

**Files:** `missions/{store,authority,executor,service,controller}.py`, `runtime/runs_local.py`, `tools/policy.py`, `observability/usage.py`; budget/ledger integration tests.

**Interfaces:** committed ActionIntent + persistent resource reservation → one native/provider call → explicit ack/effect reconciliation/usage settlement.

- [ ] Add RP03 and RP11 tests, zero-unit-budget, aggregate remaining limit, provider retries, cancelled call, disk-full/no-ledger tests at actual dispatch boundary.
- [ ] Wire ledger handle into execution scope, bind execution/action IDs, fail closed if absent; never interpret ordinary transport return as confirmed effect.
- [ ] Reserve Deep/JEV/action/observation/screenshot resources before calls; count retries/unknown spend, cap by minimum policy/unit/mission remaining; no fresh allowance after restart.
- [ ] Persist active time/wait/deadline accounting; preserve max50 existing run ceiling; zero remains zero. Persist separate payload/action digests with unambiguous meaning.

**Acceptance:** no dispatch beyond allowance or without durable action intent, failed/cancelled calls accounted. **Depends:** R02/R03. **Rollback:** quiesce, reconcile, preserve ledger. **Stop:** storage failure or unpriced spend not covered by explicit allowance.

## R05 — Connect desktop queue, native session and physical stop

**Files:** `runtime/{desktop_queue,session}.py`, `core/runtime.py`, `missions/{service,executor,authority}.py`, `sani/src-tauri/src/{desktop_control,sani_core,app_state,hotkey,main}.rs`; optional native input bridge only if required by driver capability evidence.

**Interfaces:** one desktop ownership lease/fence, verified current driver generation, host stop latch consumed by admission and dispatch, session release/reconciliation.

- [ ] Test RP12 through actual composition; add cancel-at-grant, stale core, focus change during await, stop while model/IPC hangs.
- [ ] Use existing DesktopSessionManager and action lock, not `run=None`; connect queue/fences to every mutating work item. Do not infer process authority from DB TTL.
- [ ] Wire latch into real admission/control/dispatch; stop pending input and speech, release held inputs through verified capability or safely stop owned driver; record uncertainty.
- [ ] Implement minimal human activity detection without key logging if necessary, synthetic-event distinction and takeover revalidation.

**Acceptance:** one actual executor owner and no new action after stop; unsafe release remains blocked. **Depends:** R03/R04. **Rollback:** stop/reconcile before flag-off; retain driver/mode. **Stop:** unresolved driver ownership or unavailable OS permission—continue isolated code/tests only.

## R06 — Correct Velo outcomes, per-step tools, payloads and independent checks

**Files:** `missions/{service,executor,evidence,store,contracts}.py`, `velo/{recipes,verify,adapter}.py`; executor/verifier integration tests.

**Interfaces:** selected StepSpec→appropriate catalog/payload→bounded executor→all required trusted CheckResults→gate.

- [ ] Reproduce RP04 with real wrappers and both explicit refusal and timeout; missing check IDs must fail; mutation negative controls prove oracle effectiveness.
- [ ] Fix status/NO_EFFECT/UNKNOWN semantics and complete required-ID validation; every successful fast action needs independent recipe-appropriate checks without added model call.
- [ ] Select tool catalog per claimed step, wire verified exact payload resolver and digest; mixed open/navigate/type mission must preserve scope and correct tools.
- [ ] Remove unrelated generic Submit/Send fallback and ambiguous first-candidate action; keep structured JEV mechanically bounded; escalate when no supported action.
- [ ] Enforce preconditions before action and robust postconditions after; check artifact content/readability, not suffix alone; unknown backend reality cannot be inferred from page text.

**Acceptance:** refused/missing/unverified outcomes never completed; correct local fast recipe still zero Deep/JEV. **Depends:** R03–R05. **Rollback:** adapter flag off after settled actions. **Stop:** no trusted verifier for requested effect.

## R07 — Complete durable controls, recovery, replan and waits

**Files:** `missions/{store,service,recovery,controller}.py`, core startup/IPC modules; real subprocess restart tests.

**Interfaces:** CAS control signals live token + durable state; startup reconcile→safe ready steps; bounded structured recovery→versioned plan; explicit WAITING_EXTERNAL checkpoint/backoff.

- [ ] Add RP07 regression and actual process kill after intent/dispatch/ack/result; old attempt uncertainty must survive pause/cancel/restart.
- [ ] Fix pause/resume states and scheduler wakeup, correction creates new plan_version and invalidates old approvals/payloads; do not erase completed evidence without rationale.
- [ ] Reconcile all relevant external IDs before safe retry; RESULT_APPLIED alone does not mean CONFIRMED. No automatic GUI replay after restart.
- [ ] Wire bounded recovery/review, finite retries/replans/active limits; persist backoff/Retry-After and release desktop during waits.

**Acceptance:** resume makes progress or gives explicit actionable blocker, never silent RUNNING with no runnable work; zero duplicate effects in crash scenarios. **Depends:** R02–R06. **Rollback:** pause/reconcile active attempts then retain data. **Stop:** unknown external effect or unresolvable scope.

## R08 — Connect host IPC, controls, status and final voice/text identity

**Files:** `sani/src-tauri/src/{missions,sani_core,runtime,app_state,history,main,hotkey}.rs`, `sani/src/lib/tauri.ts`, actual Main/Panel/Overlay conversations/timeline/settings and `MissionStatus.tsx`, core IPC.

**Interfaces:** one IPC reader/router, stable RequestEnvelope with actual origin/revision, mission event projection in separate UI DB, trusted controls/approval UI.

- [ ] Test current production `outcome_of` ASK_USER case and renderer mounting, not only unused missions.rs helper.
- [ ] Route concurrent status/control responses during stream with synchronized reader, native out-of-band stop unaffected; feature negotiation explicitly tested.
- [ ] Mount status/controls; distinguish turn delivery from verified mission completion; expose unknown/wait/approval state; replay event sequence after reconnect.
- [ ] Preserve manual Finish&Send, turn_gen and watchdog; corrections/priority/pause/resume use mission control. Source voice/text origins and revisions are truthful.

**Acceptance:** real shipping handlers/components show correct mission state and cannot forge host approval; no duplicate final submission. **Depends:** R02/R05/R07. **Rollback:** preserve UI data and safe feature-off view. **Stop:** core/host protocol incompatibility or unsafe admission bypass.

## R09 — Complete evidence sanitation/integrity and connect read-only Observer

**Files:** `missions/{store,evidence,observer,service}.py`, core composition, logging/usage/timing, diagnostics; evidence/observer tests.

**Interfaces:** safe immutable event outbox + content-bound evidence refs→read-only Observer→separate recommendation sink; retention/delete/hold controls.

- [ ] Add RP08/RP09 and tests for wrong mission/execution, expiry, tampered file, malicious check allowed_roots, original symlink, invalid media.
- [ ] Screen every persisted/egress field (goals, plan arguments, corrections, attempts, exceptions, prompts), not only EvidenceStore. Preserve authorized exact payload separately without exposing it to unrelated sinks.
- [ ] Verify hash/ownership/freshness/scope before accepting evidence; trusted catalog owns roots/verifier capability, never model-supplied expected object.
- [ ] Run Observer on actual sanitized committed events, link support and versions, record true model/action costs/outcomes; enforce metadata/image retention and deletion.
- [ ] Keep Observer unable to write active code/skills/config or activate experiments; deterministic rules only.

**Acceptance:** canaries never leak, modified evidence rejected, actual mission generates supported recommendation without changing behavior. **Depends:** R03/R04/R06/R07. **Rollback:** disable observer consumer only, keep safety persistence. **Stop:** sanitizer uncertainty or request to implement RSI execution.

## R10 — Finish local TTS host integration, then licensed engine and audition

**Files:** `sani/src-tauri/src/{tts,tts_protocol,tts_queue,audio,speech,app_state,settings,setup,onboarding,main}.rs`, `sani/src-tauri/python/sani_tts.py`, voice settings UI, `sani/scripts/build-tts.sh`, `sani/tts/{pyproject.toml,uv.lock}`, release packaging/notices and voice tests.

**Interfaces:** committed response/template→bounded worker PCM→cpal output, speech.stop vs mission controls, generation-safe input/output interlock.

- [ ] Instantiate supervisor/queue and real output sink; test with fixture PCM without accessing microphone/real speaker unless authorized. Enforce env allowlist and no network after assets.
- [ ] Connect sentence dispatch, bounded cancellation+kill/reap, device/error handling and stop generation. Wire PTT interruption, normal STT gate and preserved explicit finalization; acoustic mode remains gated.
- [ ] Build actual offline worker artifact/lock integration. Only then obtain allowed asset access/terms, compare at most planned two candidates and owner audition; do not accept gated terms on owner's behalf.
- [ ] Pin one selected runtime/model/voice manifest with licence notices; measure cold/warm/RTF/resources/contention and verify offline output.

**Acceptance:** actual local speech, safe stop and working existing input, tests/metrics/licences; unavailable owner audition remains BLOCKED. **Depends:** R05/R08, parallel independent fixture work permissible after R01. **Rollback:** output off, old STT untouched. **Stop:** rights, asset access, device authorization or actual self-trigger regression.

## R11 — Fix introduced type errors and run meaningful offline regression

**Files:** actual protocol test doubles in `tests/unit/test_sani_core_{app,registry}.py`, new mission tests named by mypy, any production type issue introduced by repair.

- [ ] Fix the28 added type errors rather than calling them baseline debt; preserve unchanged91-error baseline as separately reported debt unless scope requires local correction.
- [ ] Run original unit suites, real composed model/driver fixtures, real child-process restart tests, Rust handlers, renderer tests/build; no redundant algorithm-copy tests.
- [ ] Freeze held-out fixture variants and demonstrate expected failure of key negative mutations (disable scope/epoch/budget/check/skip guard); restore source immediately after mutation trials.

**Acceptance:** no newly introduced static/test failure; full manifests/logs, environment-blocked sockets explicit. **Depends:** completed changed components R02–R10. **Rollback:** test fixes only after oracle review, never revert a guard merely for green tests. **Stop:** evaluator weakening or failing external capability.

## R12 — Real isolated acceptance, same-bundle rollback and corrected handoff

**Files:** functioning R01 harness, release build scripts/config/notices, all Phase1 gate evidence and handoff.

- [ ] Validate source/bundle/assets/config hashes, default flags and unchanged providers/driver mode. No production installation/deployment.
- [ ] Under owner-issued isolated live scope, execute L1/V1, matched live performance and packaging/rollback. Preserve evidence per original TC/RSI cases, not only suite counts.
- [ ] Reconcile unknowns; quiesce→flags off→version-matched rollback on fixture data, validate history/mission data and no orphaned process/input.
- [ ] Reissue G0–G7, T01–T12, all JAR/TC/RSI matrices with PASS/FAIL/BLOCKED/DEFERRED and exact logs. Explain any approved plan adjustment; no silent lowering targets.
- [ ] Fill original handoff template. Phase1 accepted only if required gates actually pass. Stop; ask owner before fresh Phase2 re-plan.

**Acceptance:** entire Phase1 product demonstrated or precise blockers named; no missing capability hidden behind live permission wording. **Depends:** R01–R11. **Rollback:** same quiesce/reconcile/data-preserving procedure. **Stop:** any wrong-target effect, leaked secret, post-stop action, duplicate effect, evidence mismatch or unapproved live action.

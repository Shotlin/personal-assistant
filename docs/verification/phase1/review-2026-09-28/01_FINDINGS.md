# Remaining findings after remediation

Source locations refer to the exact manifest in `00_VERDICT.md`. All implementation paths below are relative to the repository root. NP cases are independently reproduced in the attached probe script; source-only findings are labeled explicitly. Prior findings F01–F13 and corrective tasks R01–R12 remain the governing scope; this is a follow-up, not a new phase.

## N01 — P1: Pause/resume permits replay of uncertain mutations

`src/assistant/missions/store.py:1219` cancels every `INTENT_COMMITTED`/`DISPATCHED` attempt and returns its RUNNING step to PENDING. `RESUME` then makes the mission RUNNING; `claim_step` does not examine previous uncertain attempts. `recovery.py:67` treats CANCELLED as final, so restart recovery will not collect that attempt either.

**NP02:** a synthetic EXTERNAL_WRITE is marked dispatched, paused, resumed, then successfully claimed as attempt 2. The old attempt remains CANCELLED; Reconciler reports UNKNOWN and `retriable=false`. No external effect was executed in this probe; it proves unsafe admission of a retry. The remediation regression uses READ_ONLY and never exercises dispatched external uncertainty or a real process kill, despite the handoff's “kill/restart” description.

Preserve dispatch/effect uncertainty independently of cancellation. Do not reset unresolved mutations to PENDING. Retry requires durable proof of NO_EFFECT, fresh scope, valid allowance, and an explicit safe transition.

## N02 — P1: Reconciliation stops after the first NO_EFFECT

`src/assistant/missions/recovery.py:83` returns immediately on any NO_EFFECT answer. **NP03:** IDs `first,second`, with first NO_EFFECT and second CONFIRMED, yield `retriable=true`; only `first` is probed. This directly contradicts the handoff's all-IDs repair claim.

Aggregate all operation results conservatively. A confirmed or unknown operation prevents replay of the overall attempt; do not infer full step completion from one confirmed operation. Production startup currently calls `recover_inflight` and ignores its returned execution IDs (`core/agents.py:427`); the Reconciler is still not wired into recovery progression.

## N03 — P1: Explicit Deep selection bypasses mission authority

`core/agents.py:355` registers Deep directly even when missions are enabled. `DeepAgentEntry._run` at line154 uses `runtime.run_scope` without mission authority/strict ledger parameters and without a restricted role for this route. The scope's defaults clear mission guards. Role denial only helps invocations that actually bind PLAN/RECOVER/REVIEW/INFO.

**NP11:** real Deep graph, scripted model, real `apply_tool_policy`, and a synthetic `type_text` tool produce **one synthetic mutation**, with no mission/intent/approval. No native driver or provider is involved. The submitted test named `test_deep_selected_actions_route_through_mission_authority` scripts only an ordinary text answer and therefore never attempts its claimed bypass.

Route action requests from every selectable entry through the shared mission service, or enforce the same mission guard on every actual tool boundary. Preserve ordinary information/chat and the existing Deep framework.

## N04 — P1: Production executor lacks dependencies and cannot bootstrap discovery

`core/agents.py:431` constructs `VeloExecutor` without `store` or `payload_resolver`. **NP01** confirms both are None on the actual shared service. The new strict gate therefore refuses mutations for missing ledger; payload refs resolve to nothing.

Separately, `store.py:840` gives the action scope only `{spec.effect_class}`. `executor.py:670` classifies observations READ_ONLY, while `authority.py:92` requires the effect to be in that singleton set. **NP07:** the first `list_apps` in a normal REPEATABLE_LOCAL step is refused with SCOPE_MISMATCH. Even a READ_ONLY item requires a prior timestamp for discovery (`authority.py:141`), but a fresh run has none. The inventory exemption described in comments is not implemented.

`MissionService._scope_for` reads `mission_allowed_apps`, which still is not a Settings field or wired trusted host input. Do not fix this by allowing arbitrary applications. Add bounded discovery with narrow authority and bind a real host-approved target before scoped reads/actions.

## N05 — P1: Recovery/revision/review and resumed execution are not composed

`missions/service.py:394` calls `controller.recover` with no `invoke`; `_replan` at line474 likewise calls `controller.plan` without it. `controller.py:178` raises when transport is absent. **NP04** records a PAUSE event with “no Deep transport is wired”; **NP05** raises the same error on REVISE after the control transaction has already changed state.

No durable reservation surrounds these paths, and final REVIEW is not invoked by the service. `control(RESUME)` only changes DB state; neither it nor the IPC handler starts `run_ready` with a fresh token. A prior pause already cancelled the old token. Durable waiting/poll progression and revision of the original goal remain incomplete.

Provide one mission/version-bound invocation path for all Controller roles, reserve at the real call boundary, schedule resume explicitly, and validate a revision before committing a runnable new plan. Preserve old-plan uncertainty and stale-result rejection.

## N06 — P1: Secret-screened helper is bypassed by actual revision control

`store.py:1264` appends raw `revision_request` and `reason` in an event. The separate `record_goal_revision` helper screens its input, but the production control path does not call it. **NP05:** a synthetic secret-shaped sentinel appears in persisted `payload_json`. No real credential was read or used.

Screen before every persistence/log/model/observer sink reached by actual request/control/planning paths. Goal/payload helper tests alone are insufficient. `TRACE_SCHEMA.md` still describes retention as proposed/debt; no evidence expiry sweep, orphan cleanup, investigation-hold handling, or observer-copy deletion is connected.

## N07 — P1: Completion can still lack independent checks; per-step tools are wrong

`service.py:561` provides a required fast check only for open_app. **NP06:** navigate, search_browser, scroll, type_text, and press_ordinal all receive zero required checks. `_validate_proposal` allows empty checks/success criteria; executor verification is conditional on their existence; the store's improved required-ID gate vacuously succeeds for an empty set. Semantic UI still reports confirmation from an accepted click if there are no required checks. Preconditions are copied into packets but not executed before actions.

`service.py:349` still loops over the mission's first non-semantic recipe. **NP08:** after open_app succeeds, the next scroll step receives the open_app tool list, without scroll. Payload resolver wiring is absent (N04), and `authority.py:99` compares the full action-argument digest to text-payload digests, which are different objects. Typing is now EXTERNAL_WRITE but the default mission scope only permits READ_ONLY/REPEATABLE_LOCAL, so there is no complete approved typing path.

Derive tools/checks/effects from the selected step and trusted recipe catalog. Keep independent fast-path verification deterministic and zero-model. Require meaningful checks for actionable completion; use separately defined action and payload hashes.

## N08 — P1: Provider/action budgets do not enforce the full mission limits

`executor.py:475` calls `jev.decide` without a durable reservation or allowance check. **NP09:** `max_jev_calls=0` still makes one scripted JEV call. Action budget is a fresh local RunBudget per item; persistent mission usage is merged after results, not reserved per dispatch. Observation/screenshot/retry/deadline allowances are not comprehensively enforced at the actual boundaries.

`service.py:234` settles one Deep reservation as consumed, then `add_usage` adds the same unit again. **NP10:** one scripted planning invocation produces `deep_calls=2`. The shared Deep graph may invoke the underlying model more than once; counting outer calls is not provider-level enforcement. Failed invocations can reuse the same call key, so idempotent reservation lookup must not authorize a new unmetered provider effect.

Reserve every bounded resource before its real operation, distinguish a reused operation from a new retry, reconcile unknown cost, settle once, and carry remaining mission allowance across steps/restarts.

## N09 — P1: Queue/stop fencing is still incomplete (source review)

Service dispatch now acquires a queue lease and uses the existing runtime/session scope, which is an improvement. But `executor.py:673` fills the observed driver generation from the packet itself. `authority.py:205` compares the packet's fence with its stored attempt, not the current queue owner/native generation or current mission state. `desktop_queue.py:113` regrants to the same mission run_id, allowing concurrent same-mission callers to obtain different fences. Release is by owner ID, not the specific acquired fence.

Rust checks the emergency latch only before awaiting `operation_guard` (`sani_core.rs:1881`), so admission can cross a stop while waiting. No current generation is carried to each native dispatch; held-input release and human takeover still lack implementation/proof. The emergency-stop comment promises speech stopping, but does not call the TTS queue, which uses a different generation.

These source-level gaps require controlled race tests; this review did not attempt a live post-stop action. Connect actual current ownership and stop state to the final effect boundary. Do not claim safety solely from a stored fence string or host admission check.

## N10 — P1: Mission UI/control integration is still unreachable (source review)

`sani/src/components/MainConversation.tsx:24` casts `ChatMessage` to read `mission_id`; neither the TypeScript interface nor Rust `StoredMessage`/history supplies that field. The component is mounted but normally receives null status. It fetches during render rather than subscribing to live/replayed mission events, does not refresh the same mission, and is passed no `onControl`, so it renders no control buttons.

Host pending/completed mapping and typed/voice origin improvements are real. Finish persisted correlation, live event projection/reconnect, plan/epoch-aware controls, approval presentation, and resume execution. Verify the actual renderer with fixtures, not isolated helper functions alone.

## N11 — P1: Voice output is unfinished beyond engine selection (source review)

`sani/src-tauri/src/tts.rs:120` creates a permanent NullSink queue. `CpalSink::open`, supervisor.start, speech enqueue, worker frame consumption, and completed-mission speech are not called by production code. `SANI_TTS_ENABLED` appears in comments, not a connected host initialization gate. Registering TtsState and stopping its queue before capture do not create a working speech path.

The worker spawner at line53 inherits the process environment and removes four variables; **there is no `env_clear` allowlist**, contrary to the handoff. The cpal sink ignores requested/packet sample rates and assumes a floating-point mono stream, so output format/resampling must be tested when connected. `uv sync --frozen` still has no `sani/tts/uv.lock` to consume.

Owner engine/assets/audition decisions remain required before real voice acceptance, but there is substantial authorized implementation work before that point: connect supervisor/transport/queue/output, shutdown/cancel/restart, credential-free process environment, packaging, and STT arbitration. Keep the existing working STT stack intact.

## N12 — P1: Live gates are empty; stale JUnit can produce false PASS

All **10** tests in `tests/e2e/test_sani_{missions,safety,voice,packaging}.py` only call `_require_live_authorization` and then return. They do not read the config, launch a bundle, drive fixtures, inspect effects, measure stop/audio/performance, or verify rollback. The isolated demonstration passes every case with a nonexistent config path; this does not bypass the launcher's config validator, but proves that a valid config would not make these bodies meaningful.

`scripts/verify_phase1.py:409` trusts JUnit counts without requiring exit code zero or a newly generated report. **NP12:** a harmless child exiting 7, with a previous passing XML left in the expected location, produces status PASS. Clear/use unique report paths and require consistent fresh report, process exit, expected case inventory and assertions.

The integration suite list contains **7 files**, excluding `test_mission_composition.py` (8 tests). Submitted XML totals are **585 + 47 + 7 = 639**, not the handoff's 647. Eight separate composition cases plausibly explain the difference, but they are not in the submitted integration gate. This review independently ran those tests as part of its 226-case rerun.

Saved mypy-after and ruff-after each contain only a one-line summary, not the claimed full/byte-identical output. The independent rerun confirms the 91-error baseline type equivalence, while ruff still flags touched `velo/contracts.py` and `tests/unit/velo_fakes.py`; “every touched file clean” is too broad. Rust/renderer saved successes were inspected, not independently rerun in this follow-up.

## Disposition of the previous review

| Prior group | Current disposition |
|---|---|
| F01 Controller composition | PLAN adapter repaired; recovery/revision/review incomplete (N05) |
| F02 role/entry safety | Role boundary improved; selectable Deep bypass reproduced (N03) |
| F03 scope | Canonical hashes and stale refusal improved; discovery/target/fence defects remain (N04/N07/N09) |
| F04 ledger/budgets | Missing-ledger refusal fixed; production dependency and allowance gaps remain (N04/N08) |
| F05 bounded execution | Explicit refusal and required-ID gate fixed; catalog/check/payload gaps remain (N07) |
| F06 desktop ownership | Queue/runtime path partly wired; current fencing/stop/takeover incomplete (N09) |
| F07 recovery/control | Unsafe pause retry plus incomplete recovery/revision/resume (N01/N02/N05) |
| F08 resource graph | Shared service fixed; verify all shutdown and restored-approval paths during final integration |
| F09 host/UI | Host status/origin improved; actual mission UI/control path incomplete (N10) |
| F10 voice | State/sink helpers added; speech path and sandbox incomplete (N11) |
| F11 privacy/evidence | Hash/expiry/basic goal screening improved; revision sink and retention incomplete (N06) |
| F12 verification | Skip handling/source binding improved; empty live cases/false PASS/inventory defects (N12) |
| F13 static checks | New type errors removed; baseline debt and reporting qualifications remain |

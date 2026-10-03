# Detailed findings — current working-tree implementation

All source paths resolve from `/Users/sayan/Documents/personal-assistant`. Lines refer to the reviewed uncommitted snapshot, not baseline HEAD contents. P1 means a release-blocking defect for this feature, not a claim that production accounts were affected. RP IDs refer to retained fixture probes; those probes exercised synthetic tools/data only.

## F01 [P1] Production Controller planning/recovery is not connected

`src/assistant/core/agents.py:512–529` `_DeepInvoke` returns `{submit_mission_plan:{raw:...}, result:...}`; `missions/controller.py:246–251` expects a PlanProposal directly or under `plan`. **RP01:** even a fake Deep response containing a valid plan produces `ValueError: invalid plan submission`. No structured submission tools were added to `agent/build.py`, context or tool binding. The real Deep graph receives a JSON prompt, not the required role-specific submission interface. Its thread key lacks mission ID and plan version.

`missions/service.py:263–264` calls recover without `invoke`; Controller `_call:191–192` always errors in that case. The service does not call review before finalizing; RETRY_SAFE does not create a new attempt and REVISE does not commit/re-run a repaired plan. This is not a missing live authorization issue.

**Required repair:** real schema-bound submission through the existing graph, one shared runtime/service, mission/version/role-local context, connected bounded recovery/review, no raw fallback. Test actual production adapter/registry with a scripted model at provider boundary, rather than replacing the entire adapter with `_ScriptedDeepInvoke`.

## F02 [P1] Controller roles and explicit Deep selection bypass mission authority

`missions/controller.py:101–114` rejects mutation only when callers explicitly use `invoke_role_tool`; searches find no real policy-wrapper call to it. `tools/policy.py:962–970` consults mission_dispatch_guard but never Controller role. `core/agents.py:188–195` executes the original graph under ordinary runtime.run_scope, and the `deep` registry entry stays unmodified even when missions are enabled.

**RP02:** within `controller_role('PLAN')`, the real `apply_tool_policy`-wrapped synthetic click executes once. Passing a direct test of `invoke_role_tool` does not establish dispatcher enforcement. Question-shaped requests go straight to Deep, whose normal scope still acquires desktop and can use tools; natural “Can you open…” is misclassified as cheap chat.

**Required repair:** enforce capability at binding and actual invocation boundaries, route Deep-selected actions through the same authority, separate informational mode without desktop acquisition, preserve current Deep for reasoning. Adversarial test must invoke the production wrapped tool and show zero effects.

## F03 [P1] Plans can forge scope identity; unknown surfaces are not fail-closed

`missions/contracts.py:138–177` accepts caller-supplied nonempty scope_hash without recomputation. `missions/service.py:183–189` trusts equality of that field. **RP05:** a plan changing allowed app while copying the original hash passes validation.

`missions/authority.py:132–137` checks app only if both allowlist and observed app are nonempty. **RP06:** a packet scoped to one app gets a permit with empty observed app, timestamp0 and no driver identity. `service.py:354–360` reads `mission_allowed_apps`, a setting not defined in real Settings, so real scope defaults empty. Read-only calls skip account/origin checks; the policy wrapper invokes mission guard only for mutations. `_guard_dispatch` at executor617–629 turns cached target state into a fresh timestamp and copies expected driver generation rather than observing one. It does not establish foreground/freshness/account/workspace.

**Required repair:** compute scope hashes from canonical trusted contents, compare scope subsets/authority fields, derive scope from actual host-confirmed intent and observations; missing identity grants no access. Guard scope-sensitive reads too. Revalidate after awaits and use actual current driver/fence. Tests must include copied hash, empty target, stale target, redirect, wrong account and read leakage.

## F04 [P1] Strict audit has a missing-ledger bypass; budgets are helpers, not runtime guards

`executor.py:153–160` enters `cua_run_scope(run=None)` without a ledger. `tools/policy.py:1011–1024` fails closed only when a ledger object exists and its plan call fails. **RP03:** strict audit enabled, no ledger, allowed synthetic click → effect1. The step intent records a packet but not each actual tool/parameter intent/outcome; `mark_dispatched` has no shipping caller.

Search of product callers shows `MissionAuthority.reserve/settle` are not used by the executor/Controller/service around actual calls. The local Deep count increments after response and resets with service creation. **RP11:** max_deep_calls=0 still invokes PLAN and leaves durable usage empty. Unit budget is not clamped to mission remaining; step deadlines restart from now, ignoring spent mission active time; observed action accounting happens only at final result and may miss failed/cancelled calls. `max(1,...)` permits one unit action even for a zero unit budget.

**Required repair:** atomic actual-action intent+resource reservation before dispatch, fail closed for absent ledger, provider retry/cost accounting at real boundary, min-of-all budgets, restart-safe elapsed accounting. Do not satisfy this by increasing limits or disabling audit.

## F05 [P1] Executor can declare success after refused action or without checks

`executor.py:377–400` increments action count if reply.ok, but returns COMPLETED/CONFIRMED regardless of false reply.ok. **RP04:** synthetic click explicitly refuses; result is COMPLETED/CONFIRMED with zero postconditions. `_assemble:555–574` gates only checks actually returned, not missing required check IDs. `_plan:143–157` creates fast steps and mission criteria with no checks. Store apply_result validates provided check names but does not demand all required ones before SUCCEEDED; final gate checks only mission criteria. Existing weak Velo verification was not repaired.

`_recipe_of` at service235–240 chooses the first nonsemantic recipe for every next step, so a mixed-recipe mission receives the wrong tool allowlist. Payload resolver is never supplied by production factory. `type_text` is classified repeatable-local although arbitrary text entry/submit can produce non-idempotent effects. semantic_ui considers generic Send/Submit controls even without objective match and picks first candidate when JEV is off.

**Required repair:** trusted pre/postconditions and effect semantics for every recipe; refusals/unknowns cannot complete; complete check-ID validation and fresh scoped evidence; per-selected-step tool/payload catalog; no arbitrary-submit fallback. Preserve zero model calls for deterministic fast recipes. Test mixed-recipe missions using real wrappers.

## F06 [P1] Desktop serialization/fencing and emergency latch are not enforced

`executor.work_scope` bypasses `SaniRuntime.run_scope`/DesktopSessionManager by directly using `cua_run_scope(run=None)`. `QueuedDesktopSessionManager` is defined but has no shipping constructor caller. Service claim passes no fence or generation. **RP12:** two independent desktop steps in one mission can both be claimed while the first is active, each with empty fence. There is no connected host process-generation authority check on these packets.

`desktop_control.rs` registers stop commands and signals existing run cancellation, which is useful, but `is_latched`/generation are read only by diagnostics/tests, not the real admission/dispatch path. Its comment that speech reacts to the same generation is unsupported by any caller. No native human takeover monitor, held-input release path or verified old-driver-stop prerequisite was added. Do not treat a fast atomic-flag unit test as proof typing stops safely.

**Required repair:** connect existing desktop session/action lock and corrected queue to every executor; propagate/validate actual fence and host-owned lifecycle; latch gates new work even with model/IPC blocked; reconcile held input and unknown effects. Test asynchronous grant/cancel, late worker, takeover just before paste, and host stop admission before real isolated desktop acceptance.

## F07 [P1] Pause/resume/revision/restart do not implement the planned lifecycle

`store.py:1103–1116` cancels attempts and clears active IDs on pause but leaves step RUNNING; resume only changes mission status. **RP07:** mission RUNNING, step RUNNING, prior attempt CANCELLED, `claim_step` returns None. `service.control` only updates the store; it does not signal live executor tokens or schedule resumed work. REVISE records text but does not create a replacement versioned plan. No shipping call to recover_inflight or Reconciler exists; cancelled uncertain attempts are no longer considered by recovery. WAITING_EXTERNAL/backoff is not wired.

Reconciler also returns CONFIRMED for any RESULT_APPLIED attempt regardless of its saved outcome, and returns after the first NO_EFFECT external ID rather than resolving all possible effects. These are unsafe assumptions for later wiring.

**Required repair:** explicit settlement/reconciliation state, safe transition and token invalidation, connected resume/revision scheduler, startup recovery before new actions, durable operation IDs and bounded waits; never erase uncertainty when pausing. Use real process kill/restart tests, not only reopen-DB simulations.

## F08 [P1] Runtime/service lifetime is split; approvals and controls lose context

`core/__main__.py:122–125` builds registry and mission provider separately. `build_mission_provider` constructs another RuntimeProvider/Deep/entry rather than reusing registry objects. `_service` at agents333–364 creates a fresh authority/executor/controller/service each call. Pending approval IDs live only in that instance's `_pending_approvals`; the next call gets an empty map. Lazy store creation has no shared initialization lock and store lifetime is not explicitly closed by the host composition.

**Required repair:** construct one runtime/service/authority/scheduler/store resource graph, inject it into registry and mission IPC, close once; approval lookup/revocation/budget remain durable across call/restart. Wire authenticated trusted-host control identity instead of assuming any supplied mission ID/provenance is allowed. Private stdio limits exposure but does not complete ownership/scoping semantics.

## F09 [P1] Mission UI and final voice/text intake are mostly disconnected

`MissionStatus.tsx` has no import/use in shipping views. `missions.rs::mission_truth`/control builders/event projection have no runtime caller. `runtime.rs:69–74` still maps ASK_USER to completed. Only stable run_id was threaded into existing host call; `MissionEntry:437–445` always labels input typed_final/revision1. No Rust mission control/approval/event IPC router or actual phase status projection was added. Existing `app_state.rs`, speech/hotkeys and renderer callers are unchanged.

**Required repair:** connect mission IPC under one stream router, actual origin/revision/stable IDs, owner-facing correction/pause/resume/priority/approval controls, durable event replay and UI truth. Keep manual Finish&Send and one-turn admission; tests must traverse handlers/rendered components, not unused helpers.

## F10 [P1] TTS is scaffolding, not an implemented voice path

No `TtsSupervisor` or queue is instantiated by shipping host. There is no cpal output playback sink, sentence dispatch, speech.stop command wiring, STT playback gate, PTT interlock or device-change flow. Worker default is UnspecifiedEngine; SilenceEngine is a test double. `sani/tts/uv.lock` is absent although build-tts.sh invokes `uv sync --frozen`; release packaging does not include a selected voice worker/assets. Host worker spawn removes only four named keys while inheriting other environment values; claims of no credentials/no network are stronger than the implementation. stop_worker merely writes a frame; its own bounded kill/ack behavior is not implemented as described.

**Required repair:** finish host/process/audio/input integration in fixtures before requiring live authorization, then select/pin licensed engine/assets, audition and package. Asset terms and owner audition are genuine external gates; they do not explain missing local integration code. No provider switch or cloud fallback.

## F11 [P1] Evidence privacy/integrity and Observer integration are incomplete

**RP08:** synthetic secret-shaped text is retained verbatim in missions.original_goal (`store.py:414–425`). Plan/step/attempt/correction payloads similarly bypass EvidenceStore screening. `EvidenceStore.load:150–160` does not verify ref.sha256, ownership or expiry before use. **RP09:** modifying a saved evidence file changes its meaning and the verifier passes despite the stored hash mismatch. `verify` does not bind ref mission/execution or check target_scope_hash. artifact_readable can accept arbitrary nonempty bytes with an allowed suffix and obtains allowed_roots from proposed check content; resolving a symlink before checking is not a symlink check.

Observer/RecommendationStore have no shipping instantiation/caller. Runtime trace lacks real per-action ledger, complete usage/scope/skill provenance and connected retention/delete/hold maintenance. Its component tests prove a function can analyze supplied events, not that Sani produces and consumes those events safely.

**Required repair:** sanitize all persistence/egress sinks, secure exact payload storage where needed, verify evidence hashes/scope/time/ownership and actual artifact format, run a read-only Observer against real sanitized outbox events with separate sink, implement retention/deletion. Keep experiments absent.

## F12 [P1] Acceptance launcher can label skipped tests PASS and cannot run advertised suites

`verify_phase1.py:174–176` maps exit0 directly to PASS. **RP10:** its run_suite on the existing voice E2E yields `observed='ss...'` and status PASS. The E2E files unconditionally call pytest.skip even if authorized. Desktop launcher builds `tests/e2e/test_sani_desktop.py`, but repository supplies test_sani_missions.py + test_sani_safety.py; packaging file does not exist. Validated config is discarded before subprocess dispatch; no test app/account/data scope is propagated. `performance` always runs fixture tests, not the matched live mode promised in plan. No actual app launch/cleanup harness is present.

Configuration checks accept unknown keys and broad roots (only reject home/exact last component 'sani'), do not check claimed dirty hash, and hash only a file whereas macOS .app is a directory. Fixture runner inherits environment with setdefault instead of isolated values. `dirty_diff_sha256` ignores untracked sources. A fixture deadline test merely sleeps and asserts time passed; it never invokes executor stopping.

**Required repair:** real isolated host harness; semantic per-case evidence and skip→BLOCKED handling; correct suites/config transport/profile containment/cleanup; tracked+untracked source and bundle manifests; protected effective oracles. Preserve original evidence and record new runs separately.

## F13 [P2] Static-check and completion evidence claims need correction

Fresh same-tool Ruff:44 errors, matching original planning baseline, not handoff135. Fresh mypy:119 errors in22 files versus original91 in12. The28 additional errors are17 protocol compatibility errors across existing core app/registry test doubles, plus11 errors in new mission tests. Some touched product files still have preexisting errors, so blanket touched-files-clean is also inaccurate. Do not treat new failures as old debt.

Existing five gate summaries refer to an earlier tracked diff, contain empty bundle/fixture/call-count fields, and all label environment F. They omit many required cases and do not contain a completed requirement acceptance matrix. These shortcomings do not imply test counts were fabricated; they mean those passing counts do not support the claimed capability coverage.

**Required repair:** fix introduced type issues, separate unchanged debt by exact baseline comparison, preserve commands/versions/JUnit/logs, bind evidence to entire source snapshot and actual bundle, rewrite handoff accurately. No automatic move to Phase2 based on component counts.

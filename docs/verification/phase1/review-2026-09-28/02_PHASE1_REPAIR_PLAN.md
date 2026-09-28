# Remaining Phase 1 completion plan

**Proposed only. Await owner approval before implementation.** These tasks complete the original Phase 1, including the earlier R01–R12 correction plan. They neither start Phase 2 nor waive Phase 1 requirements. Preserve Sani desktop architecture, local voice input, existing Deep framework/provider, Velo deterministic recipes, bounded JEV decisions, CUA driver/session machinery, and existing safety controls.

Use the original package's detailed architecture, task plan, acceptance plan, and handoff template as the specification. This review adds concrete failed behaviors and corrects the remediation claims. Tests must invoke real composition and guards; only external model/device/account boundaries may be scripted.

## C01 — Repair acceptance infrastructure first (N12; prior R01/R12)

Files: `scripts/verify_phase1.py`, launcher tests, all Phase 1 E2E files, composition tests and verification documents.

- Require zero process exit and fresh JUnit from a unique run directory; reject stale/missing/malformed XML, missing expected cases, skipped required cases, or unfinished fixture cleanup.
- Include the composition tests in the normal integration gate. Generate counts from machine results, not prose arithmetic.
- Replace authorization-only E2E bodies with actual fixture/harness assertions, or explicitly return BLOCKED until the harness exists, including when a config is present. Do not mark an empty case successful.
- Make fixture config select isolated app/profile/account/data paths; enforce scope/network/budgets/cleanup through the harness, rather than only passing environment variables. Keep offline performance and live matched performance separate.
- Preserve all old evidence; record source and actual bundle manifests, commands, case inventory, stdout/stderr, exit status, dependencies and cleanup.

Acceptance: NP12 cannot pass, empty E2E cannot pass, every required case is discoverable. No live authorization is needed to implement/test the runner with harmless children and disposable fixtures.

## C02 — Repair the real resource graph and every entry route (N03/N04; R02–R04)

Files: `core/agents.py`, `core/runtime.py`, registry/app, `missions/service.py`, executor and policy.

- Wire the shared store into the real executor and bind a payload resolver after service construction without creating a second service/provider. Restore approvals from durable state and close every owned resource once.
- Ensure explicitly selecting Deep cannot bypass mission ownership. Keep information-only requests free of desktop acquisition and preserve the fast parsed-command route.
- Add a trusted target-scope acquisition interface; remove reliance on the missing `mission_allowed_apps` setting. Discovery reads need a bounded bootstrap authority distinct from already-bound mutation authority.
- Test real `build_core_resources` → selected entry → service → runtime scope → actual policy wrapper → scripted device. Assert both an allowed zero-model open-app succeeds and a disallowed action has zero effects.

Acceptance: NP01, NP07 and NP11 become behavioral regressions. Do not satisfy them by disabling all execution or bypassing strict audit.

## C03 — Preserve uncertain effects through pause, revision, stop and restart (N01/N02/N05; R07)

Files: store, recovery, service, core startup/control handlers.

- Separate attempt cancellation from effect outcome. Preserve intents/acks and unresolved external IDs when cancelling or superseding a plan.
- Aggregate reconciliation across all operations and required step effects. Unknown or partially confirmed attempts may not replay. A proven NO_EFFECT is necessary but not sufficient: validate epoch, scope, retry budget, and owner intent before retry.
- Consume startup reconciliation IDs through a bounded production reconciliation path; persist blockers and decisions. Connect WAITING_EXTERNAL/poll timeout behavior where Phase 1 requires it.
- Schedule RESUME with a fresh execution token only after the previous attempt is settled/blocked appropriately. Make revisions atomic with respect to validation, original goal history, new plan version, old uncertainty and stale results.

Acceptance: NP02/NP03 fail before repairs and pass after. Add real process-boundary tests at intent/dispatch/ack/result boundaries, pause after possible effect, restart of a paused mission, mixed NO_EFFECT/CONFIRMED/UNKNOWN IDs, and duplicate controls/results. An unknown external effect must never dispatch twice.

## C04 — Connect all Controller roles and durable budgets (N05/N08; R02/R04/R07)

Files: Controller/submission, service, authority/store, Deep/JEV provider adapters.

- Inject a mission/version-bound transport for PLAN, RECOVER, REVIEW and revision. Match tool names to the actual submission catalog and validate bounded responses. REVIEW remains advisory; deterministic checks own completion.
- Reserve at each actual provider/driver boundary, including retries, observation, actions, screenshots and paid/unknown-cost usage. Clamp per-item allowance to remaining mission allowance and existing policy limits.
- Settle reservations once; do not add consumed Deep usage twice. A previous reservation key must not authorize a different repeated provider effect.
- Bound recovery/replanning and active/wait/deadline accounting across restart. Keep max 12 executor actions and zero-model fast commands.

Acceptance: NP04/NP09/NP10, zero/exhausted every-resource cases, failed/cancelled calls, two-step aggregate limits and restarts. Test provider call counts, not only outer service invocations.

## C05 — Finish bounded Velo payloads and independent outcomes (N07; R03/R06)

Files: service/store/executor/evidence, Velo adapters/recipes only where necessary.

- Select tool IDs for the step actually claimed, within the same claim/validation operation.
- Define payload digest separately from action-argument digest; bind exact resolved payload and trusted effect class. Build the approved typing path without broadening default authority.
- Evaluate preconditions before dispatch and recipe-appropriate postconditions after dispatch. Disallow action plans that can declare completion with no meaningful required checks. Unverifiable outcomes stay blocked/unknown.
- Test refusal, timeout, partial effect, wrong target, stale evidence and missing/failed checks. Preserve semantic candidate bounds and the fast Velo/JEV/CUA path.

Acceptance: NP06/NP08 plus mixed open → navigate → type → verify fixture, zero Deep/JEV for parsed commands, successful real policy-wrapped reversible actions, no fluent false completion.

## C06 — Bind actual desktop ownership and emergency stop (N09; R05)

Files: desktop queue/session, authority/executor/runtime, native stop/host/control modules.

- Use unique execution leases; fence-aware release cannot release a newer lease. Concurrent duplicate requests must not re-enter the same mission's desktop ownership.
- Read current queue/native driver generation at the final dispatch boundary, including after waits. Do not copy expected generation into observed evidence.
- Recheck the native stop latch after operation-lock admission and before dispatch. Preserve out-of-band cancellation while IPC/model work is wedged.
- Implement verified held-input release or a safe stop of the owned driver; record uncertainty. Connect emergency speech stop. Add bounded human takeover detection without key logging.

Acceptance: stop at every await/admission edge, stale core/generation, duplicate mission, focus takeover, held-key release and cancelled queue-grant tests. Offline composition first; real physical stop/release latency remains a separately scoped live gate.

## C07 — Sanitize actual sinks and enforce retention (N06; R09)

Files: request/control/revision transactions, evidence/logging/observer/retention.

- Screen revision/reason/plan-derived fields before any storage, event, model or observer sink; preserve safe digests/withheld reasons. Do not rely on an unused helper.
- Verify evidence ownership/execution/scope/freshness/hash at the trusted boundary. Persist trace provenance and unknown metering honestly.
- Implement owner-bounded metadata/evidence retention, orphan cleanup, holds and tombstones, including Observer copies. Keep Observer read-only and RSI activation absent.

Acceptance: NP05 with a synthetic sentinel, plus disk/log/event/checkpoint/observer scans and tamper/cross-scope/stale evidence tests. No real secrets should ever be used as test fixtures.

## C08 — Make mission status and controls usable in Sani (N10; R08)

Files: native history/runtime/events, renderer bridge/MainConversation/MissionStatus.

- Persist actual mission correlation or project it reliably from events; supply plan_version/control_epoch. Subscribe outside render, deduplicate/replay by sequence, refresh controls after state changes and reconnect.
- Wire pause/resume/cancel/revise/priority and exact approval presentation/expiry to the actual host/core handlers. Keep delivered reply distinct from verified completion and maintain truthful voice/typed origin.
- Exercise mounted UI with a streaming mission, blocked/approval states, stale CAS, reconnect and resumed progress.

Acceptance: controls appear and produce correct durable state and execution; a blocked mission never looks completed; history/reopen retains correlation.

## C09 — Finish local TTS plumbing before audition (N11; R10/R11)

Files: native TTS supervisor/protocol/queue/output and STT interlock, worker environment/build/bundle.

- Connect the feature flag, worker start/read/write lifecycle, bounded synthesis queue, actual cpal playback, stop/restart/shutdown and completed-result speech. No cloud fallback.
- Clear inherited environment and explicitly allow only required nonsecret variables; enforce offline synthesis boundaries. Handle sample rate/channels/format conversion, buffering and device failure truthfully.
- Preserve STT model/provider and Finish & Send behavior; test barge-in and independent speech versus mission controls.
- Prepare reproducible packaging/lock and asset manifest plumbing. Do not select a different API provider or download gated assets without owner authorization. If engine/voice choice is unresolved, list the exact decision after independent plumbing work is complete.

Acceptance: deterministic fake PCM travels through production framing/queue/output abstraction, stop drains stale generations, worker crash preserves text/STT, process environment sentinel is absent. Real selected-engine offline audio, audition, target-device latency and same-bundle STT regression remain live acceptance tasks.

## C10 — Re-run and hand off with honest status (all original R tasks)

- Execute all original Phase 1 fixture gates, existing compatibility tests, new composed regressions, and deliberate guard-disabled negative controls. Use a separate disposable source copy for mutations.
- Produce complete logs, JUnit, immutable source/bundle binding, introduced-vs-baseline static diagnostic comparison and an updated requirement/T01–T12/TC01–36/RSI matrix. Reuse the prior complete matrix; every row must be verified or explicitly blocked with evidence.
- Implement the live harness before requesting live authorization. When authorized, run real L1/V1/P2-live/K1 acceptance in the exact approved scope; `P2-live` means the original performance gate, not development Phase 2.
- Exercise quiesce/reconcile/flag-off/data-preserving same-bundle rollback. A flag-off must not silently send uncertain work through legacy execution.
- Fill the original handoff template with facts from this snapshot. Distinguish IMPLEMENTED, FIXTURE-VERIFIED, LIVE-BLOCKED and ACCEPTED per requirement. A blanket “implemented” is not appropriate while code paths are missing.

**Exit condition:** all required Phase 1 implementation and acceptance evidence is complete, or the handoff explicitly remains NOT ACCEPTED with exact blockers. Owner acceptance comes before a fresh repository-based Phase 2 re-plan; no detailed Phase 2/3 implementation is authorized here.

# Findings against the bound source

P1 means a Phase 1 acceptance blocker. Locations are repository-relative, at the recorded source manifest. Evidence scripts are independent review probes; exit zero means the diagnostic ran, not that the product passed.

## D01 — P1: Actual dispatch lifecycle does not preserve uncertainty

`src/assistant/missions/store.py:299` records an action intent, but neither this ledger path nor any production caller invokes `mark_dispatched` (definition at 1952). `store.py:1282` treats `INTENT_COMMITTED` as safely cancellable on pause and only preserves `DISPATCHED` as RECONCILING. `root-probes.json/actual_ledger_pause` exercises the real ledger: state stays INTENT_COMMITTED, becomes CANCELLED on pause, and a second attempt is claimable after resume. The manually marked DISPATCHED control case correctly remains RECONCILING and cannot re-claim. Thus the narrow new regression passes while the shipping dispatch path fails its assumption. No physical mutation was made in this probe.

Repair the actual policy-ledger-dispatch transaction boundary and reconcile uncertain action rows across pause/revise/cancel/restart. Test the real wrapped tool, including a delayed result after a synthetic effect; do not manually set DISPATCHED to make the main acceptance test work.

## D02 — P1: Resume and remaining lifecycle/approval workflows are incomplete

`service.py:638–652` schedules resume only when `release_approved_blocked_steps` returns a nonzero count. A normal paused pending mission without an approval returns RUNNING with no scheduled execution (`normal_resume`). `core/agents.py:473` calls startup reconciliation only when newly recovered IDs exist, although the service also needs to handle previously RECONCILING attempts. `store.py:2301` releases every BLOCKED step when any live approval exists; it does not select a step by refusal reason/action binding. `store.py:1326` changes approval epochs without renewing their epoch-bound digest. These need an end-to-end exact-action approval test, not a bare row-count test.

WAITING_EXTERNAL appears as a contract/status mapping but has no production wait scheduler. Generic bounded checkpoint/wait/resume was required in Phase 1; calling it reserved for later does not close C03. Verify unknown effects cannot be escaped through revise/new plan, approval-driven release, or repeated restart.

## D03 — P1: Discovery bootstrap permits a targeted read without scoped identity

`authority.py:168–183` returns before scope checking for all inventory tools, and also for targeted discovery when the observation has no app/timestamp. A real-store authority probe obtained a permit for `get_window_state(pid=999, window_id=999)` with absent identity under a named app/origin scope. This does not prove a real out-of-scope screen was read; it proves the guard grants the permit without establishing containment. Existing tests only cover an already-populated wrong-app observation. Discovery needs minimal trusted inventory followed by target binding before content/AX reads; do not reintroduce the old bootstrap deadlock.

## D04 — P1: RECOVER/REVIEW request a nonexistent submission tool; fast commands gain Deep work

`controller.py:187` supplies `submit_mission_response` for both roles. `submission.py:34–38` exposes `submit_recovery_decision` and `submit_final_review`. The production transport at `core/agents.py:618` uses the supplied name. The independent role probe records this mismatch. Scripted transports accepting any tool name conceal it.

`service.py:600` invokes advisory Deep review for every mission with steps, including deterministic commands. The original zero-Deep/zero-JEV fast path must survive mission ownership. Review budget reservation is also made before resolving transport; exercise unavailable transport settlement. Test actual graph/tool submission for PLAN, RECOVER, REVIEW and fresh revision; test provider-call counters, not only the service's planning counter.

## D05 — P1: Production budget path ignores the work-item JEV ceiling

`executor.py:558–580` checks the work-item ceiling only when no durable authority is bound. With real authority and mission allowance remaining, a packet with `max_jev_calls=0` makes one JEV call (`NP09_real_authority`). Enforce both mission and step ceilings, before every actual provider request, retry and action. Audit action/observation settlement against `store.py:1156` result-usage merging to prevent charging the same operation twice. The old outer planning double-charge is fixed: one invocation consumed one Deep unit in the independent probe. That narrow fix does not prove per-provider-call accounting inside a multi-call graph.

## D06 — P1: Required checks can be present but fail to prove the requested effect

`service.py:_fast_checks_for` creates `window_visible` with empty expected data for scroll/ordinal press. `evidence.py:361–378` accepts any payload containing a window ID. Re-observing an unchanged window is not proof of scrolling or clicking the intended control. Short search terms can produce no markers. `executor.py:699` places expected resolved payloads into evidence, while `evidence.py:310–318` searches the entire payload for page markers: expected text can contaminate the observation oracle.

Require recipe-specific postconditions, exact target binding and observation-only verification. Test refusal/no-op/wrong target/unchanged screen/short query negatives, alongside valid positives. The fixed per-step tool catalog is acknowledged separately; it does not repair outcome truth.

## D07 — P1: Plan-derived text bypasses privacy screening; retention is not operational

The real `MissionStore.commit_plan` persisted a fabricated `sk-review-...` sentinel in `mission_plans` and `mission_steps` (`plan_sink_canary`). Short secret-shaped revisions now correctly refuse; plan fields remain an independent unscreened sink. Screen all model/owner-derived fields before persistence and egress without preventing stop/cancel.

`store.py:2502`, `evidence.py:166/186`, `observer.py:59` add retention functions, but source search finds no production callers. Normal `EvidenceStore.put` at 127–136 creates refs without expiry; the expiry query requires one. Holds are helper inputs, not an owner lifecycle. Implement scheduled/lifecycle retention, expiry defaults, holds, tombstones and derivative cleanup. Preserve trace integrity with a documented retention boundary. Do not describe helper tests as operational retention.

## D08 — P1: Desktop queue handoff can invalidate a live owner

`runtime/desktop_queue.py:138–154` identifies a granted waiter only by run ID. A duplicate same-run waiter that times out or is cancelled releases the original owner's lease. `_release_locked` wakes B while ownership remains empty; immediate acquisition C obtains a lease, then resumed B overwrites ownership. Both calls return handles, and C becomes invalid without releasing it. `root-queue-recheck.json` independently repeats all three schedules. There was no physical desktop action.

Bind waiters/grants/releases to unique acquisition identity and assign the next owner atomically before wakeup. Also exercise cancellation/stop at each asynchronous boundary before dispatch. The policy wrapper awaits ledger persistence after its earlier checks (`tools/policy.py:1052–1068`); final admission must remain fenced through this boundary. Human takeover/held-key release is still acknowledged as missing implementation, not merely an unrun measurement.

## D09 — P1: Host-to-worker speech path fails before engine selection

`tts.rs:365` sends `message_id: ""`; worker `sani_tts.py:79` requires a nonempty ID. The independent worker probe using the host-shaped request emits an error. Worker error events at 255–256 lack utterance ID, whereas host pump error cleanup uses it; queue slots can remain occupied. `tts.rs:69–70` puts `-I` after the script, so it is not Python isolation. The default interpreter at 462 is `/usr/bin/env`, without a Python command. These are independent of audition/engine choice.

The worker `_buffered_seconds` increases at line 332 without draining/resetting; it bounds cumulative synthesis instead of buffered audio. The host pump decodes PCM directly rather than applying the complete PcmChunk validation contract. Repair request/error correlation, launch resolution/isolation, streaming/backpressure, malformed/stale chunk rejection and cancellation cleanup. Test the actual host encoder/worker protocol and host pump using a synthetic sink before any owner-authorized audible test. Preserve working STT.

## D10 — P1: Full owner workflows and live harnesses remain missing

`MainConversation.tsx:131–136` connects pause/resume/cancel and persisted mission correlation. It does not complete exact approval details/decision, revision or priority workflows. The handoff admits approval-listing work. `tests/e2e/_live.py:61` unconditionally skips for missing harness after validating config. This is now honest BLOCKED, but it is not an implemented live test harness. Installed bundle/rollback, real stop/takeover, voice and measured performance remain unaccepted. Implement the safe harnesses without executing live actions in this repair run; separate code-ready from authorized-and-measured acceptance.

## Prior corrective-task disposition

| Task | Disposition |
|---|---|
| C01 gate honesty | Stale JUnit/nonzero exit fixed; explicit live blocking fixed; physical harness incomplete D10 |
| C02 production boundary | Dependencies and selectable-Deep routing fixed; discovery overbroad D03 |
| C03 uncertainty/lifecycle | Mixed-operation aggregation fixed; production dispatch and scheduling incomplete D01/D02 |
| C04 Controller/budgets | Outer plan accounting fixed; role tool, fast path and packet ceilings incomplete D04/D05 |
| C05 bounded execution/checks | Catalog fixed; meaningful verification and exact approval loop incomplete D02/D06 |
| C06 ownership/stop | Native admission/queue links exist; races and takeover/release work remain D08 |
| C07 privacy/retention | Short revision screening fixed; plan sink and scheduled retention incomplete D07 |
| C08 owner UI | Mounted status/basic controls fixed; full owner workflows incomplete D10 |
| C09 voice | Plumbing added; protocol/launch/streaming defects D09; audition blocked |
| C10 full acceptance | Broad fixtures corroborated; original all-gate acceptance not achieved D10 |

| Prior finding | Current disposition |
|---|---|
| F01 | PLAN composition improved; RECOVER/REVIEW D04 open |
| F02 | Role refusal/selectable Deep narrow bypass repaired in fixtures; retain regression |
| F03 | Scope-hash construction repaired; discovery read containment D03 open |
| F04 | Strict missing-ledger refusal repaired; D01/D05 accounting/dispatch remain |
| F05 | Catalog/refusal improvements; independent outcome D06 remains |
| F06 | Fencing integrated in parts; D08 races/takeover remain |
| F07 | D01/D02 lifecycle remains incomplete |
| F08 | Shared service repaired; exact approval/control lifecycle D02 remains |
| F09 | Correlation/status/basic controls connected; D10 owner workflows remain |
| F10 | D09 incomplete; no live acceptance |
| F11 | Evidence hashing/Observer improvements; D06/D07 remain |
| F12 | Launcher skip/exit honesty repaired; real harness D10 remains |
| F13 | No introduced static multiset; existing diagnostics remain; completion claims corrected here |
| N01 | Narrow marked-DISPATCHED case fixed; actual path D01 still fails |
| N02 | Mixed-operation case fixed and independently reproduced |
| N03 | Selectable-Deep mutation refusal independently reproduced |
| N04 | Dependency wiring fixed; bootstrap replaced with overbroad read D03 |
| N05 | Transport storage added; D02/D04 remain |
| N06 | Short revision refusal fixed; plan text/retention D07 remain |
| N07 | Step catalog fixed; checks/approval D02/D06 remain |
| N08 | Outer Deep double-charge fixed; production packet budget D05 remains |
| N09 | D08 queue failures; physical stop not accepted |
| N10 | Basic UI connected; D10 full workflows remain |
| N11 | D09 voice protocol/launch defects; no engine/live acceptance |
| N12 | Stale JUnit and empty PASS repaired; missing harness D10 remains |

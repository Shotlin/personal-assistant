# Independent review verdict

**Phase 1 is incomplete, is not accepted, and needs another corrective batch before live acceptance. Do not start Phase 2 or Phase 3.** This is a review of the submitted cumulative patch and its evidence, not a confirmation that the shipping app has passed acceptance.

I read the submitted README → HANDOFF → DISPOSITION → RESULTS → SOURCE_BINDING → WORKLOG in that order, inspected the diff and complete changed files, reconstructed the claimed source from base `df04060f189a10bb81baf522a58347cddc6cc915`, reran its fixture gates, and added independent synthetic probes. Source hashes match the submission. Main and corrective-worktree HEAD remain the base commit. Details and limitations are in [AUDIT.md](AUDIT.md).

## Per-priority verdict

“Not verified as complete” means the priority still has a failed or unproved requirement. It does not erase the verified improvements listed alongside it.

| Priority | Verdict | Verified repair slice | Why the priority remains open |
|---|---|---|---|
| 1 — Exact approvals and durable waits, D02 | **Not verified as complete** | Current-epoch, exact-digest release and consumption negatives; one-step approved action; persisted waits and timer/restart fixtures | Multi-step approval stays RUNNING and cannot resume; pause/resume strands approval-blocked steps; pending target is omitted; wait release permits a new attempt after an UNKNOWN effect. D13/D14. |
| 2 — Origin containment and outcome verification, D03/D06 | **Not verified as complete** | Configured-origin unknown READ refusal, including a production wrapper; inventory bootstrap; missing scroll evidence and wrong-direction/window negatives | An unrelated control gaining focus verifies an ordinal press; a matching token in another window verifies field content. Trusted browser-origin and scroll reporting still require production driver integration. D15. |
| 3 — Provider metering, privacy and retention, D05/D07 | **Not verified as complete** | Successful callback completion rows; evidence creation TTL; some result/review canary screening | Limits fail open through the real callback manager; storage failure permits continuation; deletion unlinks another mission's evidence; wait rows retain a recognized synthetic secret; durable holds, metadata retention and owner deletion wiring are incomplete. D11/D12/D16. |
| 4 — Stop ownership, D08 | **Not verified as complete** | Idle/non-owner stops preserve generation; queued stop targets its waiter; fenced queue API and optional acknowledgement fixtures | MissionService cleanup omits the lease fence; a new lease can be released by old cleanup; next owner is granted before physical release acknowledgement; production release/takeover wiring is absent. D19. |
| 5 — Voice worker, drain interlock and interpreter, D09 | **Not verified as complete** | Dedicated synthesis thread answers a gated-engine control fixture; sequential buffered-tail state tests; resolver positive/negative fixtures | Queue/state lock inversion can deadlock; saturated worker shutdown blocks before its bounded join; output backpressure can block controls; release packaging does not include the interpreter/worker layout the resolver expects. D17/D18. |
| 6 — Owner UI and authorized harnesses, D10 | **Not verified as complete** | Revise/Priority code and IPC extras exist; helper for exact approval minting exists; renderer builds; authorization absence remains BLOCKED | MainConversation discards pending approvals and never calls missionApprove; live cases omit authorization scope/budget fields, stop after work completes, and do not prove wrong focus/account or active speech cancellation. Install/rollback remains a placeholder. D20/D21. |
| 7 — Final audit | **Verified for source binding, gate counts, static parity and four mutations; not verified as complete** | 625 unit / 96 integration / 7 performance PASS; 121 Rust PASS + 2 ignored; renderer PASS; 4 meaningful mutation failures; 91 mypy/42 ruff with zero diagnostic drift | Owner RED suite is a collection error, not an executed behavioral failure; scope RED count is wrong; retrospective RED does not prove prospective discipline; unresolved slices are promoted too broadly. D22. |

## Numbered findings

Severity convention: **P1** requires correction before Phase 1 acceptance or relying on the affected safety/control path. **P2** is an audit/coverage defect requiring correction in the handoff. D11–D22 continue the earlier D01–D10 findings; they are finding identifiers, not progress percentages.

### 1. D11 — P1: provider request ceilings fail open

`src/assistant/core/agents.py:627–651` records completed requests, catches storage exceptions and returns, then raises only after exceeding the count. `src/assistant/observability/usage.py:120` supplies only `on_llm_end`; there is no pre-request admission or corresponding failed-request accounting in this hook. The callback uses the framework's default `raise_error=False`, so its limit exception is swallowed by the actual callback manager.

The independent probe used LangChain's actual callback dispatch with a local FakeListChatModel, not a network provider: **limit 2 → 5 responses returned, 5 rows recorded**. A synthetic storage error also allowed a response to return. The package's scripted graph calls the recorder directly, bypassing the exception behavior that invalidates its abort claim. A hardcoded ceiling aggregated by mission/version is also not the claimed per-invocation reservation. “Every request including retries” and “runaway graph aborts” are not established. See `evidence/probes.json` keys `provider_callback_ceiling` and `provider_storage_failure`.

Required: reserve/admit at the real provider request boundary, fail closed on unavailable accounting, record attempted/failed/retried calls honestly, reconcile usage without double charging the outer Deep invocation, and test the actual framework callback/transport path.

### 2. D12 — P1: owner deletion removes unrelated evidence

`src/assistant/missions/service.py:416–430` calls `get_all_evidence_paths()` while purging one mission; `store.py:2486` has no mission filter. The sweep then unlinks all returned files. Fresh evidence rows are not marked deleted by this file operation.

With two synthetic missions, purging A deleted **both A's and B's evidence files**, while B's row remained `deleted=0`. The one-mission fixture cannot prove containment. This is a local data-loss defect; the probe used temporary files only. See `cross_mission_deletion`.

Required: mission-scoped row selection and tombstoning, idempotent file cleanup, explicit handling of shared paths/holds and observer derivatives, and a multi-mission isolation regression.

### 3. D13 — P1: approval recovery strands valid plans

`store.py:1343–1363` changes the mission to NEEDS_APPROVAL only when every step is terminal. A blocked first step with a dependent PENDING step leaves the mission RUNNING. RESUME then rejects it. Separately, `store.py:1466` clears blocked pending approval data on PAUSE/REVISE, but only requeues RUNNING steps; an undispatched approval-blocked step remains BLOCKED without a new digest. The pending projection at `store.py:676–692` also drops the persisted target reference.

Independent results: a two-step approval mission was RUNNING, exposed `target_ref=null`, and RESUME failed; a one-step pause/resume ended RUNNING with the step BLOCKED and zero pending approvals. See `multi_step_approval` and `approval_pause_resume_stranded`.

Required: coherent mission status for blocked dependency paths; safe re-observation and fresh approval obligations across epoch changes; preserve exact target/account/scope identity in the owner projection. Never revive an old approval or replay an uncertain dispatched effect.

### 4. D14 — P1: external wait release makes unknown effects replayable

`service.py:486` routes NEEDS_CONTROLLER + retry_after into a wait without rejecting UNKNOWN effects. `store.py:2002–2045` releases a wait by changing BLOCKED to PENDING without proving the prior effect absent or reconciling that attempt. `claim_step` rejects RECONCILING attempts, but not a RESULT_APPLIED attempt whose effect remains UNKNOWN.

The independent store/service probe applied an UNKNOWN result, entered/released a wait, then successfully claimed **attempt 2 while attempt 1 remained UNKNOWN**. No actual desktop effect was performed. See `wait_reclaims_unknown_effect`. The normal one-step failure path also advances to VERIFYING, while wait creation changes only RUNNING/PLANNED missions to WAITING_EXTERNAL, leaving an inserted wait that its timer cannot resume. Production rate-limit retry producers remain unwired, as DISPOSITION acknowledges.

Required: wait admission/release must bind current plan/epoch and effect certainty, require reconciliation before any retry of ambiguity, and work through real one-step and dependent-plan result handling, deadlines, restart and owner controls.

### 5. D15 — P1: outcome checks can certify the wrong target

`evidence.py:418–442` does not use the expected ordinal/resolved control identity; any matching-window element gaining focus/checked/selected/expanded/pressed passes `press_effect`. Focus alone does not prove activation. `evidence.py:366–384` binds `field_value` to a token extracted from BEFORE evidence without also requiring the same process/window; when BEFORE focus is missing, the check falls back to any matching field.

Independent probes verified an expected third-control press after only an unrelated first control gained focus, and verified a field with the same token/value in a different process/window. See `wrong_ordinal_verified` and `wrong_window_field_verified`. Scroll's new delta check is useful, but still needs complete identities and an axis-bound offset from the real driver; missing instrumentation must remain an honest blocked verification.

Required: exact resolved target and stable process/window/account identity, valid BEFORE/AFTER phases, and independent proof of the requested outcome. Missing facts must withhold success, not relax the check.

### 6. D16 — P1: privacy and retention claims exceed the implemented sinks

`store.py:1928–1956` persists external-wait reason/checkpoint data directly. The independent fixture placed a **synthetic recognized secret canary** in this path and found it in the durable row (`wait_canary_persistence`). No real secret was used or leaked. `canonical_json(checkpoint)[:2000]` can also truncate valid JSON; `open_waits` subsequently parses it without isolating a corrupt row.

`service.py:403` runs retention without a durable hold set. The manual lifecycle fixture passes a hold set itself; it does not prove that normal `_observe` preserves an owner hold. The sweep runs only when a mission is observed; owner deletion has no UI/IPC caller; 30-day stored metadata retention from the original architecture is not implemented by preserving trace events indefinitely. Per-call test holds and pruning observer objects do not close that requirement.

Required: screen before truncation and before every new persistence/model/log/UI sink; serialize bounded but valid checkpoints; persist and enforce holds in normal and idle lifecycle; implement scoped owner deletion and the planned metadata/evidence retention with a minimal deletion audit. Test these through the production service path.

### 7. D17 — P1: native audio drain introduces a lock cycle

`sani/src-tauri/src/tts_queue.rs:261` holds `state` while acquiring the queue and sink; `finish_utterance` at line 231 holds the queue while acquiring the state. Enqueue also uses queue→state. Concurrent drain/completion or drain/enqueue can therefore deadlock.

The independent standalone Rust probe copied these methods, used a dummy sink, and added a scheduling delay after drain acquired state to expose the interleaving. Both completion flags stayed false: `drain_completed=false finish_completed=false`. This is a schedule-amplified lock-order demonstration, not a real audio-device acceptance test. See `voice-lock-probe.rs` and `voice-lock-result.log`. Sequential native fixtures cannot validate this concurrency property.

Required: consistent lock ordering or a single coherent queue/state transition model; concurrent stop/enqueue/completion/drain regressions with deterministic barriers and bounded completion; retain the audible-tail STT interlock.

### 8. D18 — P1: worker shutdown is unbounded under saturation; bundle wiring is incomplete

`sani/src-tauri/python/sani_tts.py:312` uses blocking `Queue.put(None)` before `join(timeout=2)`. With an active gated engine and all four pending slots full, shutdown cannot reach the bounded join. The independent probe remained running after 2.3 seconds with shutdown set and four items pending, and exited only after the engine was released. See `worker_full_queue_shutdown`.

`_write` at line 261 holds a common lock across synchronous pipe write/flush; output backpressure can block control acknowledgements behind synthesis output. Calling the selected engine's cancel synchronously is not demonstrated crash-safe or bounded by a no-op default. Native interpreter resolution searches `sani-tts-python/bin/python3` and `sani_tts.py`, but `tauri.conf.json` and release wiring do not include that TTS layout. The resolver's fixture creates imaginary files; it does not prove a build can produce the runnable package. Engine selection and physical audition remain separate owner gates.

Required: bounded cancellation/shutdown with full queue, blocked output and unresponsive/erroring engine; generation-safe queued work; host child termination fallback; locked build/resource provenance for the intended worker/interpreter without changing the selected provider or selecting/downloading a voice engine without authorization.

### 9. D19 — P1: stale service cleanup can release a new lease

`src/assistant/missions/service.py:379–381` calls `release(run_id)` without `lease.fence`. The queue's fenced API does not protect a caller that omits the fence. In a same-run stop/regrant interleaving, the independent probe let old cleanup release the new lease; its owner became null and the fresh lease unusable. See `unfenced_old_cleanup`.

`desktop_queue.py:236` also grants the next waiter before awaiting `release_input` at line 247. When wired to a physical driver, old-owner cleanup could release the next owner's held input. Production code has no caller providing this hook, nor a connected human-takeover callback; currently this is an optional fixture protocol, not an integrated physical release path.

Required: pass the exact fence at every production cleanup; stop/cancel must preserve single ownership while acknowledging or honestly failing physical input cleanup before another effectful owner proceeds. Connect the driver/human takeover path; keep actual physical acceptance BLOCKED until measured.

### 10. D20 — P1: owner approval UI is absent

`sani/src/components/MainConversation.tsx:22–68` stores status/version/epoch but discards pending approvals from missionGet. Its MissionStatus render at line 171 provides Revise/Priority/control only. `missionApprove` exists in `sani/src/lib/tauri.ts:407`, but has no renderer caller, and MissionStatus has no exact approval decision interface. The helper also requires scope identity that is not supplied by the exposed pending projection.

Thus “status line exposes exact pending approvals” is false; “UI polish” understates a missing owner workflow. A renderer build proves type/build compatibility, not usable approval IPC or displayed action identity.

Required: render the exact pending tool, target, account, scope, digest, version/epoch and expiry; explicit approve/reject controls over existing host IPC; refresh on status changes and visible stale/rejected errors; preserve working voice input, revision/priority and truthful status. Test the UI through the host-facing contract, not only an unused helper.

### 11. D21 — P1: authorized harnesses do not prove their named acceptance properties

`tests/e2e/_live.py:89` constructs a harness using only configured allowed apps. It omits authorized window/account/origin scope and explicit budgets, uses DeepController(None), and does not reproduce production MissionEntry ownership/payload composition. MissionService's `_scope_for`/`_limits_for` supply defaults rather than those config fields. This is not the claimed exact production composition.

`test_sani_missions.py:75` waits for `submit` to finish before stopping; it cannot measure stop during work or count post-stop effects. Wrong-focus uses a different app name in the command, not actual wrong focus. Wrong-account accepts a null account rather than proving refusal of a wrong account. Multi-step loops over separate missions rather than testing one dependent mission.

`test_sani_voice.py:44–137` sends cancel after a terminal event, does not assert nonzero decoded PCM, and accepts cancelled as a successful synthesis round trip. Its framed reads can block after select reports only a partial frame; a timeout/EOF can leave `terminal` undefined. It does not perform native playback/STT overlap. Packaging install/rollback and safety cases still call unconditional `blocked_until_harness` placeholders. Bundle hashing is a useful identity slice, not executable rollback acceptance.

No live action was run in this review or justified by these cases. The package's final E2E log has 14 skips and remains honestly BLOCKED. Required: wire exact owner authorization into the real composition and test observable effects during work, active speech stop, exact focus/account, physical cleanup, installed-artifact launch and rollback. Without a connected capability, report that capability incomplete/BLOCKED; do not label it executable acceptance.

### 12. D22 — P2: final audit overstates behavioral RED and repair closure

The package's owner-controls RED JUnit records a missing-contract collection error, so no owner behavior executed. Scope-outcomes RED has **9 tests, 7 failures, 2 passes**, not “7 of 8.” Some provider-privacy RED failures are missing constructor/API behavior, rather than assertions against an existing implementation. The historical comparison demonstrates baseline failure versus current green; it does not establish that tests were written and run red before implementation. Native drain/resolver repairs lack corresponding behavioral RED evidence in the package.

All four declared mutations are meaningful and independently reproduced, and static parity is correct. Those strengths do not close the untested failures above. The full referenced baseline coverage matrix is absent from the submitted ZIP, though available in the repository and copied into this review. HANDOFF's “next work is live evidence” conflicts with its own DISPOSITION's unfinished producers and with the code defects here.

Required: correct counts, identify collection/API failures separately, preserve chronology honestly, carry the complete requirement matrix, and retract overbroad fixture-verification claims. Do not rewrite historical logs or manufacture prospective RED evidence.

## Requirements still unmet

The failed slices above map back to T02 (approvals/waits), T03/T04 (protected discovery and exact outcomes), T05 (ownership and physical release), T06 (actual provider request admission), T08 (owner control UI), T09/T10 (voice concurrency, worker and release packaging), and T12 (acceptance harnesses). Privacy/retention and owner deletion also remain incomplete. Do not promote whole T/G/JAR/TC rows from these narrow fixtures; retain the original complete matrix and attach proof to each changed slice.

Even after code corrections, these acceptance gates remain **BLOCKED**: authorized live desktop correctness and stop behavior, physical key/button release and human takeover, selected-engine voice audition/latency/native playback/STT coexistence, installed-bundle install/restart/rollback, and the expanded PostgreSQL integration suite. This review did not run those gates. The package is honest about their blocked status; several harnesses still need code before authorization alone can make them executable.

The next action is the [Phase 1-only corrective prompt](PHASE1_BATCH3_CORRECTIVE_PROMPT.md). Complete and independently review the code repairs, then collect separately authorized physical evidence and obtain owner acceptance. Only after that acceptance and a fresh repository-based re-plan can Phase 2 be considered.

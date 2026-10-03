# Independent review — corrective batch 3

**Verdict: substantial progress, but D11–D22 are not all finished. Approximate total Phase 1 completion is now 80/100. Phase 1 remains incomplete and not accepted.** The detailed basis for the approximate number is in [COMPLETION_ESTIMATE.md](COMPLETION_ESTIMATE.md). It uses the same overall-development meaning as the earlier 70% estimate.

## Source and independently checked results

Reviewed the batch at `/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant/docs/verification/phase1/corrections-2026-10-01-batch3/`, its cumulative patch, actual worktree source, new tests and raw logs. Read README, HANDOFF and DISPOSITION; RESULTS.json, SOURCE_BINDING.json and WORKLOG.md were absent. The previous D11–D22 review and corrective prompt were the requirements for this review.

Base HEAD remains `df04060f189a10bb81baf522a58347cddc6cc915`. The cumulative patch SHA-256 is `fd58106c62c11caa05223a1e7b6537d783eeee9beeb5eee7b8497b340af6b81d`. It applied cleanly to a fresh base reconstruction and matched all 810 inventoried files, including tracked repository material and the new source/test files. This inventory is broader than the earlier 672-file manifest; the counts are not a progress metric. Worktree source hashes remained unchanged at the final recheck.

| Independent check | Result |
|---|---|
| Unit | 634 passed; no failures/errors/skips in final run |
| Mission integration, 17-file inventory | 132 passed |
| Performance fixtures | 7 passed |
| Native Rust, offline locked | 124 passed, 2 ignored |
| Renderer | TypeScript/Vite build passed |
| Static diagnostics vs batch-2 independent baseline | 91 mypy / 42 ruff; zero added and zero removed normalized diagnostics |
| Python guard mutation replay | 10/10 meaningful assertion failures; no collection errors |
| Physical/live acceptance | Not run; still BLOCKED |

The initial Python/native runs were blocked only by temporary Unix-socket sandbox permissions; final reruns passed with those fixture permissions. One reviewer runner setup error used the resolved interpreter outside its virtualenv; it is retained separately and not treated as a product defect. Native compilation initially lacked ignored staged resource files in the archive reconstruction; local existing resources were copied into the temporary test checkout for compilation. This does not establish that the patch builds a self-contained shipping bundle.

The independent probes used local SQLite, synthetic secrets, fake drivers/sinks and an in-memory HTTP transport. The OpenRouter SDK probe exercised the actual selected provider SDK with mock HTTP responses; no network/provider request occurred. The Rust race probe adds a scheduling barrier to a copy of the production queue implementation to expose a valid interleaving; it is not physical audio acceptance.

## D11–D22 verdicts

| Finding | Review verdict | What is now proved / what remains |
|---|---|---|
| D11 request admission | **Partially fixed** | Wrapper admission now fails closed before an outer model invocation. Actual SDK retries still issue multiple HTTP requests for one admission; direct-model reported usage is lost. |
| D12 deletion isolation | **Original cross-mission defect fixed in tested service path** | Two-mission isolation, tombstones, idempotent normal deletion and existing-hold refusal pass. Complete deletion/retention recovery still has D16 gaps. |
| D13 approval recovery | **Original multi-step/pause defects fixed in tested fixtures** | Mission reaches NEEDS_APPROVAL with other pending steps; pause requeues the undispatched approval step; fresh epoch changes digest; target projection restored. Full renderer identity is still D20-open. The two-step test is sequential and does not establish every dependency/race case. |
| D14 durable waits | **Partially fixed** | Original UNKNOWN-result wait admission/release reproduction is refused; one-step VERIFYING transition fixed. PLAN rate limits still escape without a durable wait; release lacks complete current-state/epoch checks and multiple-wait coordination. |
| D15 exact outcomes | **Partially fixed** | Cross-window typing and unrelated-control ordinal regressions now fail. Single-observation typing still falls back; focus alone still verifies press; complete identities and driver axis projection remain incomplete. |
| D16 privacy/retention | **Partially fixed** | Wait string values screened; valid bounded JSON; durable holds and creation TTLs; startup/lifecycle sweep. Secret keys persist; metadata purge leaves ledger/files; true idle scheduling and complete cleanup remain open. |
| D17 native lock cycle | **Original cycle fixed; interlock correctness still open** | New lock order and native tests pass. A stale drain snapshot can now set Idle while fresh audio is buffered. |
| D18 worker/package | **Partially fixed** | Full-FIFO shutdown sentinel is bounded and cancel exceptions are caught. Actual pipe write and synchronous engine cancel can still block; build script and launcher are not a portable runtime. |
| D19 fencing/cleanup | **Partially fixed** | Service cleanup now passes its exact fence; supplied mock release acknowledgements precede grants. Production never supplies the physical release callback; takeover helper is unconnected; acknowledgement lacks cleanup identity. |
| D20 owner UI | **Partially fixed** | Approve/Reject buttons and pending projection exist. Non-null target/account identity is not sent by approval helper; effect class hardcoded; control errors are swallowed. |
| D21 acceptance harnesses | **Partially fixed, not acceptance-ready** | More authorization fields and active-work/speech measurement logic added. Deep transport remains unwired, window/provider limits omitted, and wrong-focus/account oracles accept arbitrary errors as success. |
| D22 final audit | **Partially corrected** | Previous RED count/collection-error corrections and static parity are sound. Three required files missing; RED evidence is not prospective for every repair; native mutation tests a self-deadlock rather than the original lock-order cycle. |

These verdicts credit the actual repairs. They do not promote entire Phase 1 requirements from a narrow fixture pass.

## Remaining findings requiring correction

### 1. P1 — D11: actual provider retries still bypass the ceiling

`src/assistant/models/admission.py` admits once around `inner._agenerate`/bound runnable execution. `src/assistant/models/openrouter.py` still configures provider retries; the installed ChatOpenRouter delegates to `client.chat.send_async`, whose SDK retries occur inside that admitted invocation.

Independent result: **request limit 1, three mock HTTP requests, one admission and one COMPLETED row**. The returned message reports 3 input/2 output tokens, but the stored row has null input/output usage because `complete()` reads `usage_metadata` from a ChatResult instead of its message. See `evidence/probes.json: sdk_internal_retries`.

The nine new admission tests explicitly invoke the wrapper again to simulate retry; they never exercise an SDK-internal retry. The batch fixes the old swallowed-callback problem but not its stronger “every transport request including retries” claim. Enforce admission at each outgoing attempt or move retry ownership outside the admitted transport while preserving existing provider choice. Reconcile real reported usage for direct, bound and streaming results.

### 2. P1 — D14: rate-limit waits are not connected across Controller roles

`core/agents.py:_DeepInvoke` raises ExternalWaitRequested for all roles, but `missions/service.py` consumes it only in RECOVER escalation. `_plan` rethrows; submit catches MissionPlanError, not ExternalWaitRequested. An independent PLAN 429 reproduction ended with **ExternalWaitRequested, mission PLANNED, zero durable waits**. REVIEW likewise has no dedicated durable-wait handling.

Wait rows still have no control epoch; `release_wait` updates a stored plan/step without validating the current plan/epoch/deadline inside that transaction. Releasing one of multiple waits sets the mission RUNNING even when others remain, while their timers only fire for WAITING_EXTERNAL. Finish the lifecycle through real submit/review/control/startup paths, with effect reconciliation and exactly-once scheduling preserved. The original UNKNOWN-result retry defect is fixed and must stay fixed.

### 3. P1 — D15: outcome truth still has relaxed paths

In `missions/evidence.py`, `field_value` rejects missing BEFORE focus only when `len(refs)>1`. **One capture with an unfocused matching field passes.** `press_effect` binds the intended ordinal now, but **only gaining focus still passes**, without proof of activation. `_common_window` rejects a missing pid but accepts `(pid, None)`.

The executor's before/after projection still copies only `scroll_offset`, not the new `scroll_offset_x/y` used by the horizontal verifier, so reporting those driver fields alone cannot close horizontal verification. This is code integration work, not just an unavailable live driver. Preserve exact before/after identities and capture phases, remove the single-capture fallback, and define independent activation evidence. See `single_capture_field` and `focus_without_activation` probes.

### 4. P1 — D16: privacy and complete retention remain incomplete

`_bound_checkpoint` screens dictionary values but copies keys directly before truncation. The independent recognized synthetic canary placed in a key persisted in the wait row. No real secret was used.

The 30-day metadata purge omits `mission_action_ledger`. A probe purged a terminal mission but left its ledger row. It also deleted a fresh derivative's evidence row without returning that file for deletion, leaving an untracked file. Normal owner deletion similarly tombstones rows before attempting file removal; unsuccessful unlinks are not retained as retryable cleanup work. Holds are durable, but owner hold controls and periodic expiry while an app remains open and idle are not wired; “startup sweep” alone does not provide ongoing idle retention.

Cover all keys and values before bounding, preserve valid JSON, include the complete derivative inventory in purge, and make failed file cleanup retryable. See `wait_secret_key` and `metadata_orphans`.

### 5. P1 — D17: drain can release the STT interlock while audio remains

The old state→queue lock inversion is removed. The new `drain_pending` reads queue length and sink buffering under separate locks, then later updates state. Between that snapshot and the state update, `play_chunk` can enqueue new audio and set Playing; drain then overwrites it with Idle.

The schedule-amplified dummy-sink probe produced **`state_after_drain=Idle; queued_audio_ms=100`**. The source comment claiming a stale read can only keep Playing longer is incorrect. Preserve coherent queue/sink/state transitions or validate a generation/versioned snapshot before clearing Playing. See `voice-drain-race.rs` and its log. Concurrency tests proving absence of deadlock do not prove this state invariant.

### 6. P1 — D18: backpressure and packaging are still unfinished

Worker `_write` times out only acquiring its mutex. Once acquired, `writer.write()` and `flush()` are synchronous and unbounded. A synthetic blocked writer was still inside write after 2.2 seconds even with shutdown true and a droppable frame. Engine cancel also runs synchronously on the reader thread; catching exceptions does not bound an engine that never returns.

The build script changes directory, then resolves a relative `$0` again. With a no-op uv fixture, invoking `bash sani/scripts/build-tts.sh` from the repository root fails at line 27 with a nonexistent path. Its generated launcher embeds the development virtualenv interpreter's absolute path; the staged launcher present in the worktree refers back to a `tts/.venv`. Neither packages that runtime into a relocatable installed app. A new Tauri resource entry alone does not fix this.

Bound actual output I/O and engine cancellation, retain host process termination fallback, fix script path resolution, and stage a real relocatable interpreter/dependencies with provenance. Engine selection/audition remains an owner decision. See `blocked_writer` and `build-script-probe.*`.

### 7. P1 — D19: physical cleanup/takeover helpers are still not production wiring

The exact-fence service cleanup is fixed. But every `release_input=` caller is still a test. Production `stop_owner` calls omit it; the `None` path grants a successor with no physical acknowledgement. `acknowledge_human_takeover` has no production caller either. This repeats a remaining part of the original finding despite the handoff claiming wiring exists.

`acknowledge_driver_cleanup()` has no owner/fence/cleanup-operation identity, so a delayed acknowledgement cannot be distinguished from the current pending cleanup. `confirm_manual_cleanup()` clears uncertainty without granting already waiting callers. Connect these paths to actual host/driver signals and explicit cleanup identities, preserve the no-op stop fixes, and prove successor/late-ack behavior with isolated drivers before physical acceptance.

### 8. P1 — D20: approval UI omits required identity and hides errors

MainConversation now renders and invokes approval actions. However, `missionApprove` sends no `target_ref` or `account_ref` and always sends `EXTERNAL_WRITE`; the target-sensitive release SQL requires equality. With a persisted `win-7` pending target, the current renderer request shape releases **zero steps**. The positive UI-shaped test uses a null target, so it misses this case.

The projection/display still lacks complete scope/account/effect/expiry details; a digest prefix and tool name are insufficient to explain the exact action. All control callbacks use `.catch(() => undefined)`, contradicting “errors surface” in the handoff. Send the complete exact pending identity, expose rejected/stale decisions, and test the actual renderer-built request shape through the host contract with non-null target/account scope. See `renderer_approval_target`.

### 9. P1 — D21: live harness can mistake its own failure for a safety success

`build_mission_harness` creates DeepController(deep) but never supplies MissionService's `transport_factory` or an invoke transport to submit. DeepController does not automatically use the Deep entry for PLAN/RECOVER/REVIEW; a non-fast mission errors “no Deep transport is wired.” Admission-store binding is likewise absent from this composition.

`run_wrong_focus` catches every exception and returns `{status: unknown, completed: false}`. Its test accepts that as a pass. The independent reproduction passed through precisely this missing-transport path, without testing wrong focus at all. A runtime/programming failure must be an ERROR, never proof of zero wrong-target effects.

`allowed_windows` is checked for presence by the outer case but discarded when building scope. Explicit provider request/spend fields are not propagated by budgets_from_config. Multi-step still loops separate missions. Stop measures queue ownership/latency and broad terminal state, without the promised zero post-stop dispatch/effect oracle or physical release. Install/restart/rollback remains honestly NOT IMPLEMENTED. Repair composition and measurements before authorizing live actions.

### 10. P2 — D22: submitted audit package is incomplete and one mutation is overstated

README/HANDOFF refer to RESULTS.json, SOURCE_BINDING.json and WORKLOG.md; none existed in the inspected package directory. A matching cumulative patch helps reconstruct source, but does not replace the promised audit artifacts. The full original requirement matrix is referenced rather than carried in this package.

Ten Python mutants independently fail their intended assertions and static parity is correct. The native mutant prepends a state lock to `drain_pending` while retaining its later second lock of the same non-reentrant mutex. It deadlocks even in a single call. The timeout therefore detects an artificial self-deadlock, not the original two-thread lock-order inversion. The submitted native test also logs that it is still running after 60 seconds, so it is not itself a bounded failure oracle. Replace it with the actual inversion and an explicit bounded concurrency assertion.

The batch correctly distinguishes old collection errors and fixes “7 of 8” to “7 failures among 9.” D11 has an executed old-path RED probe. It does not supply prospective behavioral RED for every other fix; cite the independent review reproductions honestly rather than claiming universal prospective discipline.

## Oracle changes and acceptance status

The declared additive migration expectations through versions 5/6 are justified. Requeuing an undispatched approval on pause is the intended correction; requiring cleanup acknowledgement between unacked-stop cases is justified. Moving obsolete post-hoc metering tests to the new API is reasonable, but those tests do not prove SDK retries. Changing the ordinal fixture's role/expected identity matches the recipe's actual role/index selection. No declared oracle change needs reverting simply to preserve the old bug; the missing tests concern the production boundaries described above.

The blocked gates remain blocked: live desktop correctness/stop/scope, physical held-input release and takeover, selected-engine voice audition/native playback/STT coexistence and latency, installed-bundle install/restart/rollback, expanded PostgreSQL, and authorized real-provider cost reconciliation. No physical acceptance was attempted in this review. Shipping Sani still needs those results, not just the passing fixture suite.

Use [NEXT_PHASE1_FIX_PROMPT.md](NEXT_PHASE1_FIX_PROMPT.md) for the remaining work. Preserve the fixes already proved; do not restart or redesign Phase 1. A new independent review should target these remaining boundary defects and acceptance evidence. Phase 2/3 remain stopped until Phase 1 is accepted and the owner authorizes a fresh re-plan.

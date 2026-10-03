# Phase 1 corrections — inline execution ledger

Plan: PLAN.md, originating review-2026-09-29/04_PHASE1_CORRECTIVE_PLAN.md.
Owner authorized start after the independent review. No agents. No commit/push/deploy/live actions/providers/assets/RSI. Main is preserved; implementation in the managed phase1-corrections worktree at source-identical HEAD df04060f189a10bb81baf522a58347cddc6cc915.

Ruling: user no-agents overrides the skill's final reviewer delegation. Review will be a separate self-review, reported honestly.
Ruling: existing dependencies reused offline; no install. Source-identical baseline has already passed broad fixtures with documented sandbox socket reruns.
Pre-flight: R1 dispatch lifecycle feeds R2 reconciliation; preserve conservative UNKNOWN across both. R3 scope discovery feeds R5 meaningful observations. R4 budgets and R7 final fences must not double-consume permits on recheck. R6 privacy/retention applies to R5 evidence and R8 voice reports. R9 harness code must not execute live gates without owner authorization.

R1 in progress: real wrapper/real authority/real ledger with a delayed synthetic effect response, plus stale intent ledger refusal. These catch missing dispatch-state persistence and obsolete dispatch intent.

R1 dispatch boundary: two new regressions RED→GREEN; 31 affected tests pass. First green attempt used editable-install main accidentally; explicit worktree PYTHONPATH runner fixes provenance and retained bound green result is authoritative. No product claim uses the mistaken run.
R1/R2/R4/R6 additional RED: 7 boundary cases fail for the expected lifecycle/tool/privacy/budget reasons. Changes now pass those new cases; old test_np04 expected the nonexistent submit_mission_response tool. Ruling: update that expectation to the registered submit_recovery_decision; independent registration test requires it, so this corrects an invalid oracle rather than weakening a guard.
R7 queue: three new schedule tests fail before fix (duplicate waiter timeout/cancel and handoff ownership gap); atomic grant and acquisition-specific cleanup implemented. Initial regression command named a nonexistent extra file; rerun with the actual file.

R3/R4/R5: five independent failures reproduced (unknown targeted read, persisted packet limit across executor recreation, expected payload contaminating page check, short query and fast-path review call). Guard now uses fresh inventory identity, packet reservations atomically intersect stored/packet/mission ceilings, expected payload is excluded from page observations, short query retained, and zero-Deep missions skip advisory model review.
R4 regression: the old superseded-fence fixture lacks a real committed step. The guard should return an honest budget refusal rather than leak AuthorityDenied; handled without relaxing the durable packet check.
R8 worker: error correlation and 100-second synthetic stream fail before repair. Ruling: old flood test incorrectly rejected long streams of small drained chunks. Replaced it with a genuinely oversized individual chunk negative and added a long-stream positive; synchronous framed writes provide pipe backpressure. Worker cancel responsiveness and native pump still require separate closure.

Owner steering: review the current changes and create a new fix file for this same project, preserving earlier reviews. Final audit will explicitly distinguish repaired slices from unfinished Phase 1 requirements; no completeness claim.
R8 native: host encoder contract and 100-second drained stream both RED→GREEN (18 native voice fixtures). No voice engine selected. Worker errors now correlate queue identities; pump validates PCM and sequence, applies bounded backpressure; explicit unconfigured interpreter refuses instead of invoking env incorrectly.
R7 final admission: real wrapper produced one synthetic effect after cancellation during ledger persistence in RED. Added invocation-local synchronous cancellation/deadline/current-fence check after awaited ledger work.
Broad exploratory suite encountered unavailable local PostgreSQL (127.0.0.1:5433) and one obsolete unit fixture that manufactured DISPATCHED without a real mission plan. Ruling: replace that fabricated row with actual commit_plan/claim_step, preserving assertions on ledger outcome. No guard relaxed. Docker daemon unavailable; no service started and no image downloaded.

Final batch: 616 unit, 73 integration, 7 performance and 116 native tests passed; 2 native physical tests ignored. Renderer builds. Affected 60-case recheck and final 15 boundary cases pass. Three disposable guard mutations fail at expected assertions. Static diagnostics match baseline (91 mypy, 42 Ruff src/tests). Remaining Phase 1 gaps are in NEW_PHASE1_FIX.md. Main source unchanged. No Phase 2/3 start.

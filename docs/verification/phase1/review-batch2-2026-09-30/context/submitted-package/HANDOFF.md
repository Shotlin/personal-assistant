# Phase 1 second corrective batch — handoff (2026-09-30)

Status: **REPAIRS IMPLEMENTED AND FIXTURE-VERIFIED; PHASE 1 NOT COMPLETE AND NOT ACCEPTED.**
Base HEAD `df04060f189a10bb81baf522a58347cddc6cc915`. No commit. Main checkout unchanged. Implementation in the managed worktree `/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant`, uncommitted, on top of the FIRST corrective batch (its patch hash is recorded in `SOURCE_BINDING.json` under `previous_batch`).

`DISPOSITION.md` carries the full matrix forward. `RESULTS.json` holds machine counts; raw logs/JUnit are in `evidence/`. `phase1-corrections-2.patch` is the CUMULATIVE uncommitted diff of BOTH corrective batches against base df04060 (38 files: previous batch's edits plus this batch's edits and the 4 new integration test files). It applies cleanly to a fresh base worktree and reproduces this worktree's source exactly — verified. Do NOT stack it on top of the first batch's patch; `changed-files/` holds the complete final versions.

## What changed (by requirement)

1. **Exact approvals (D02).** The executor persists the exact owed approval digest (tool, target, plan version, epoch) when a dispatch is refused at the approval gate, and reports such refusals as BLOCKED/APPROVAL_REQUIRED — never as an unknown effect. Release re-queues only the step whose persisted digest a current-epoch, unconsumed, unexpired approval names. Consumption additionally binds the row's plan version, epoch and target. Approvals no longer carry across epoch bumps; RESUME from NEEDS_APPROVAL/WAITING_EXTERNAL deliberately does not bump (so a fresh approval stays bound to its epoch). Durable external waits (reason, retry-after, deadline, checkpoint) live in a new migration-4 table; timers fire once, respect deadlines, and startup re-arms each open wait exactly once after reconciliation settles uncertainty.
2. **Origin containment and exact outcomes (D03/D06).** Configured `allowed_origins` now bind READ-ONLY content reads too — an unknown origin fails closed; minimal inventory still bootstraps and a trusted-origin positive passes. New `scroll_effect`/`press_effect` verifiers require before/after observations of the SAME window with a real offset delta / activation-state flip; `field_value` binds the focused field when before-evidence exists; unsupported evidence blocks honestly. Fast scroll/ordinal missions no longer ship `window_visible {}`.
3. **Provider accounting and privacy (D05/D07).** The LangGraph transport meters EVERY provider request (graph internals, retries) into `mission_provider_requests` with unique durable call ids and a per-invocation ceiling that aborts a runaway graph — without touching the outer one-deep-call-per-invocation charge. Evidence expires at creation; retention runs in the mission lifecycle (holds respected, tombstones kept); canaries in exception/result/final-review text are withheld at every persistence sink; owner deletion removes evidence files and derived recommendations with tombstones.
4. **Stop/ownership (D08).** A stop naming a non-owner (or nothing) no longer increments the generation; queued stops remove only the waiter; duplicate stops are no-ops; the driver `release_input` acknowledgement is reported and never claimed; `confirm_takeover` ends exactly the named lease.
5. **Voice (D09).** Worker synthesis runs on a dedicated thread — cancel/stop controls are handled during active synthesis; cancelled utterances never report finished; engine cancel is crash-safe; shutdown mid-synthesis exits bounded. Native `finish_utterance` keeps the queue non-Idle while the device still holds buffered audio (STT interlock covers the audible tail; barge-in drains); the pump polls the drain. Packaged interpreter/script resolution replaces the environment-only fallback; a missing bundle reports voice output unavailable. No engine selected, no assets downloaded.
6. **Owner UI/harnesses (D10).** Revision/priority ride the existing `mission.control` IPC; approvals are minted over the existing `mission.approve` path bound to the pending digest/epoch; the status line exposes Revise/Priority and the exact pending approvals. Live desktop/voice/packaging cases are now EXECUTABLE under launcher authorization (production composition, real worker protocol, exact bundle-identity binding) and remain BLOCKED without it — nothing ran live.
7. **Audit.** Gates re-run green; 4 disposable-copy guard mutations (approval release, origin read-only hole, no-op stop generation, sink screening) each fail their own boundary regression; complete static diagnostic multisets unchanged; RED evidence recorded per suite against base+previous-batch.

## Verified results

| Check | Result |
|---|---|
| Unit | 625 passed |
| Integration inventory (12 files, incl. 3 new boundary suites) | 96 passed |
| Performance | 7 passed |
| Native Rust, offline locked | 121 passed, 2 ignored (physical-environment cases) |
| Renderer | TypeScript + Vite build passed |
| Guard mutations (disposable copies) | 4/4 meaningful failures |
| Static multisets vs baseline | 91 mypy / 42 ruff — 0 new, 0 gone (unchanged, NOT clean) |
| RED evidence | new suites fail at base+previous-batch (collection error / 7-of-8 / 6-of-6 / 3-of-3) |

Oracle updates (explicit, never weakening): migration inventory `[1,2,3]→[1,2,3,4]` (two files, additive migration 4); idle-stop oracle pinned the old no-op generation bump and now asserts the generation does NOT move; the evidence fixture's fixed January 2026 capture timestamp is expired evidence under creation-time expiry by the store's own contract, so it captures "now". Four mutations and their logs are in `evidence/mutation-*` and `run_mutations.py`.

## Honest limits

A single agent did this batch; the assessment is a self-review. The expanded PostgreSQL integration service is still unreachable locally (Docker daemon unavailable; no service started) — that gate stays BLOCKED. Physical desktop acceptance, real-engine voice audition, installed-bundle install/rollback execution, held-input physical release, STT coexistence in a real room, and latency/throughput on the exact artifact were NOT run and stay BLOCKED. `mission_recovery_failed` warnings in fixture logs are the honest escalation path for scripted recovery failures, not flakiness.

## Next action

Phase 1 remaining work is owner-authorized live evidence and acceptance: run the executable harnesses through `scripts/verify_phase1.py --allow-live` with an approved config, hold the owner audition (VOICE_SELECTION.md), and provide the PostgreSQL test service if the expanded gate is to close. Do not treat any fixture result above as physical acceptance. Phase 2/Phase 3 remain stopped; only an explicit owner instruction starts them.

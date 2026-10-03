# Phase 1 corrections, second batch — inline execution ledger (2026-09-30)

Owner instruction: continue Phase 1 only, per followup-2026-09-29/NEW_PHASE1_FIX.md. Single agent, no subagents, no commit/push/deploy/live actions/provider switches/engine selection/asset downloads/RSI. Implementation in the managed phase1-corrections worktree at base df04060, on top of the first corrective batch (verified by patch hash before starting).

Rulings:
- The ContextVar channel for the owed approval digest does not survive LangChain's tool invocation (child-task context copy): metering/pending state travels on the executor keyed by execution id instead. Recorded where implemented.
- RESUME from NEEDS_APPROVAL/WAITING_EXTERNAL does NOT bump the control epoch: a fresh approval is bound to the epoch it was minted for, and nothing uncertain is in flight. Epoch-bumping resumes (from PAUSED) revoke approvals — no silent digest inheritance.
- Migration 4 extended in place (exact approvals + waits + provider requests) before any release; the migration-inventory oracles are updated with explanation.
- The adapter's ToolReply.ok was hardcoded true, so semantic_ui treated guard refusals as successful clicks. Fixed at the adapter; recipes already text-checked independently.
- Ruff --fix touched pre-existing baseline diagnostics; every baseline file it touched was restored to HEAD so the final comparison is 42=42 / 91=91 with zero drift. Only this batch's files keep the lint fixes.

Order of work (red→green per area): D08 stop semantics → D02 exact approvals + waits → D03 origin containment + D06 exact verifiers → D05 transport metering + D07 privacy/retention → D09 worker/native voice → D10 UI/harnesses. A disposable worktree at base+previous-batch produced per-suite RED evidence; the full new-corpus GREEN run followed in the real worktree.

Incidents during development: a debug script ran against the MAIN checkout via a relative sys.path (found by a missing contract field — the main tree was never modified); a ruff autofix over-reached into baseline files (reverted as ruled above). Both caught by the audit discipline; neither affected the main checkout or the worktree's final state.

Final batch: 625 unit, 96 integration, 7 performance, 121 native (+2 ignored physical), renderer build; 4/4 meaningful mutations; static multisets unchanged; RED evidence retained. Main source unchanged. No Phase 2/3 start. Stop here for owner acceptance.

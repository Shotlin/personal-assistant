# Phase 1 corrective batch handoff

Status: **PARTIAL REPAIRS VERIFIED; PHASE 1 NOT COMPLETE**.
Base HEAD: `df04060f189a10bb81baf522a58347cddc6cc915`. No commit was created. Main remains unchanged in the original 668-file source audit. Final working source and patch hashes are in `SOURCE_BINDING.json`.

## What changed

- The actual action ledger atomically records DISPATCHED with the durable action intent and rejects invalidated mission epochs. A pause after a synthetic effect preserves uncertainty. Replacing plans cannot erase dispatched, reconciling, UNKNOWN or CONFIRMED effects. This intentionally refuses some revisions until a complete safe recovery workflow exists.
- Normal pending resume schedules work, and startup reconciliation is called even when there are no newly recovered IDs. These are specific lifecycle repairs, not proof that every approval/wait/restart flow is complete.
- RECOVER and REVIEW select their registered submission tools. Exact zero-Deep fast commands skip advisory model review. Transport construction sits inside reservation settlement handling.
- Executor reservations intersect packet, committed step and mission limits durably; zero allowance refuses calls and a fresh executor cannot reset the tested per-execution allowance. Graph-internal model calls still need separate enforcement.
- Targeted reads require fresh app identity; only minimal inventory bypasses target checks. Configured origin containment for unknown-origin content reads remains open.
- Expected payload text no longer satisfies page-observation checks, and short search text creates a required check. Exact field/scroll/ordinal outcome verification remains open.
- Plan persistence rejects recognized synthetic secret content before inserting a new plan. This does not establish complete privacy coverage or retention.
- Queue grants are atomic before waking a waiter; duplicate same-run waiter cleanup cannot release the existing owner. A synchronous final cancellation/deadline/fence check runs after awaited ledger work and before invoking the tool.
- Voice request IDs, interpreter flag order, error correlation, PCM validation, chunk sequence checking and bounded stream accounting were repaired. Long synthetic drained streams pass. The host reports queued rather than spoken. Without an explicitly configured interpreter it reports unavailable. Packaged interpreter resolution, responsive worker cancellation, engine selection and physical audio acceptance are unfinished.
- The new corrective integration cases are included in the normal Phase 1 integration inventory.

## Verified results

| Check | Result |
|---|---|
| Unit | 616 passed |
| Phase 1 integration inventory | 73 passed, including 15 new corrective cases |
| Performance fixtures | 7 passed |
| Native Rust, offline locked | 116 passed, 2 ignored |
| Renderer | TypeScript + Vite build passed |
| Targeted post-edit recheck | 60 passed; final unmutated boundary recheck 15 passed |
| Three guard mutations in disposable copies | All three detected by meaningful test failures |
| Static comparison | 91 mypy errors and 42 Ruff source/test diagnostics, unchanged from baseline |

These totals describe selected gates. The exploratory all-integration run was not green: 776 cases, 9 failures, 66 errors, 1 skipped. One outdated ledger fixture was repaired and passes in the final unit suite; PostgreSQL-dependent cases remain blocked without the local test database. See `VERIFICATION.md`.

## Preserved constraints and limits

No subagents were used for this review/repair batch. No provider switch, engine asset download, production-account action, live desktop interaction, real audio run, RSI activation, commit, push or deploy occurred. The Deep Agent, Velo/JEV/CUA execution path, desktop architecture and STT implementation were preserved.

Do not extrapolate this into a claim that no production failure could occur. Negative controls deliberately produced an in-memory synthetic effect in a disposable mutant; that is test evidence, not a real desktop action. Physical input release, installed-bundle rollback, voice latency and true end-to-end behavior remain unaccepted.

## Next action

Use `NEW_PHASE1_FIX.md` to continue Phase 1 repairs. Preserve the original coverage matrix; no requirement is automatically accepted because related fixtures passed. After all required code and authorized live evidence are complete, request owner acceptance. Only a later explicit owner instruction may start a fresh Phase 2 re-plan from the then-current repository.

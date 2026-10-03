# Verification and provenance

## Authoritative final results

Machine counts are in `RESULTS.json`. Python logs and JUnit are in `evidence/final-unit.*`, `final-integration.*`, `final-performance.*`, `final-corrective-recheck.*` and `final-unmutated-boundaries.*`. Native output is `final-rust.log`; renderer output is `final-renderer.log`.

The full unit, integration and performance inventories ran before a type-only local variable rename, import ordering and test typing improvements. The 60-case affected recheck covers evidence verification, the integration gate inventory and all corrective cases. A final 15-case unmutated run covers the exact final corrective boundary code; subsequent source changes were comments only. Rust and renderer sources did not change after their final checks. `git diff --check` passed.

`run_pytest.py` binds PYTHONPATH to the worktree because the reused virtualenv's editable install points to main. It uses local fixtures, CUA disabled, a fabricated provider-key string, an isolated Sani data directory and the worktree capability manifest. No actual provider calls were needed. The Python executable is the existing main `.venv/bin/python`. Dependencies were reused locally, not downloaded. Rust ran with `--offline --locked` and an existing temporary target cache. Renderer dependencies were cloned locally before `npm --prefix sani run build`.

Normal integration inventory: mission_sqlite, mission_policy, mission_core, mission_ipc, mission_restart, mission_desktop_control, mission_observability, mission_composition, and phase1_corrective_boundaries. This is not a claim that all repository integration tests passed.

## Red-to-green and mutation evidence

Earlier `*-red` files contain expected pre-fix failures. `r1-green-bound` is authoritative for the initial dispatch repair: the first `r1-green` attempt accidentally loaded main through the editable install and is not a valid repair result. `final-fence-green` named a nonexistent test path and failed collection; it is superseded by final-boundaries and the later final runs. `boundaries-green-first` and `scoped-budget-checks-green` exposed old assumptions that were corrected and rerun. `final-mypy.log` contains intermediate new typing issues; `final-mypy-recheck.log` is the final comparison. `final-ruff.json` used a broader repository scope including archived documents and is not comparable to the 42-diagnostic source/test baseline; use `final-ruff-src-tests.json`.

Three mutations ran only in separate temporary source copies, with the normal worktree untouched:

1. Disable both durable intent-state and epoch eligibility checks: the stale-intent test fails because no exception is raised.
2. Disable the durable packet ceiling: the recreated-executor budget test fails because a second request is admitted.
3. Remove the final synchronous dispatch check: the cancellation-during-ledger test fails because one synthetic effect occurs.

Each mutant exits 1 at the intended assertion; these are not collection or dependency errors. Definitions and results are in `mutations.json`, logs/JUnit in `mutation-*`, and the driver in `run_mutations.py`. The unmutated final 15-case suite passes. These trials establish sensitivity for three guards, not exhaustive security verification.

## Static checks and broad-suite limitation

Final mypy reports 91 errors in the existing baseline; final Ruff on `src tests` reports 42 diagnostics. Normalized multisets match the prior independent review exactly, including duplicate diagnostics. No new diagnostic remains in this comparison. Static checks are therefore unchanged, not clean.

`offline-full.xml` records 776 cases with 9 failures, 66 errors and 1 skipped. One failure was an obsolete unit fixture that manufactured a DISPATCHED row without a real mission plan; it now uses commit_plan/claim_step and the final full unit suite passes. Eight run-ledger failures and PostgreSQL setup errors require a reachable local test database at 127.0.0.1:5433. Docker's daemon was unavailable. No database service, production database or remote substitute was started. This expanded integration gate remains BLOCKED.

Native tests intentionally ignore two physical-environment cases. Desktop, voice, packaging, installed artifact identity, rollback, held-input release, engine audition, latency and matched live-task benchmarks were not run. Their status stays BLOCKED or NOT IMPLEMENTED as appropriate, never PASS.

## Scope of review

This batch reviews the current corrective changes against the previously completed repository audit and original planning requirements. It does not supersede all historical findings with a pass. The original audit, full requirement matrix and supporting probes are preserved in the included original-review ZIP. A self-review is weaker than a separate independent implementation review and is labeled accordingly.

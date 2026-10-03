# Phase 1 remediation review — 2026-09-28

**Verdict: Phase 1 is not complete and must not be accepted. Phase 2 remains stopped.**

This reviews the remediation handoff, not an authorization to implement repairs. The handoff's overall “NOT ACCEPTED” status is correct, but its claim that implementation is complete and only live owner gates remain is not supported by the current code. Several repairs are real; major integration and safety defects remain, including a new unsafe pause/resume transition. Finish the remaining Phase 1 implementation before scheduling its live acceptance.

## Snapshot and evidence

- Repository: `Shotlin/personal-assistant`, local `main`.
- HEAD: `58dac9c88018674c2e780086953902f1ea135308`, unchanged. No fetch was performed; this is the local checkout, not a claim about the latest remote tip.
- Tracked + untracked source manifest: `007fb39445cd981303d635ea72d421aadb9790c93b6276ff89621db71e74ffc7`, **666 files**, exactly matches remediation evidence.
- Dirty source identity: `c10e71fcb0762551f30a2cfe89935f8ee5cec77e76c242033f4ef0311ad615ce`, exactly matches the handoff.
- The previous review's entire `MANIFEST.json` verifies without a changed file. This review did not modify the original gates, previous review, planning package, or implementation.
- Independent rerun: **226 existing Phase 1 tests passed, zero skips**, in a source copy with isolated settings and no live driver/provider/audio.
- **12 additional probe cases exposed remaining defects**; observations are in `evidence/independent-probes.json`, with runnable source and log.
- The **10 live acceptance placeholders passed in 0.010 seconds** with only two synthetic environment variables and a nonexistent config path. Their bodies perform no acceptance actions or assertions. This was a demonstration of missing tests, not a live run or live acceptance.
- Independent mypy: **91 errors, same diagnostic multiset as the planning baseline**, zero added/removed after normalizing line numbers. Ruff: **42 errors**; full logs retained. These baseline/static results do not establish functional completeness.

## Principal blockers

1. **Unsafe uncertainty handling.** A dispatched external-write attempt becomes `CANCELLED` on pause; resume permits attempt 2 while reconciliation still says `UNKNOWN`, not retriable. Multi-operation reconciliation also returns retriable after the first `NO_EFFECT`, ignoring a later confirmed operation.
2. **The shipping composition still bypasses or blocks mission execution.** Explicit Deep selection can invoke a policy-wrapped synthetic mutation outside any mission. The mission executor itself receives neither its ledger store nor its payload resolver. Its first discovery read is denied by its own action-effect scope.
3. **Recovery, revisions, and controls are incomplete.** Both recovery and revision call the Controller without a Deep transport. Revision text is persisted in the control event before screening; a synthetic secret-shaped sentinel survived in SQLite. Resume changes database status without scheduling execution.
4. **Verification and budgets remain incomplete.** Only `open_app` gets a required fast-path check; per-step tool selection still uses the first non-semantic recipe. A zero JEV budget still invokes JEV. One successful planning invocation is charged twice.
5. **The UI and voice output are not connected end to end.** The mounted mission component looks for a `mission_id` that chat history does not provide, and receives no control handler. TTS remains a NullSink-backed state plus unused helpers; no production worker-start/synthesis/playback path exists. Worker environment inheritance contradicts the claimed allowlist.
6. **Acceptance gates cannot establish completion.** All live E2E bodies are authorization-only placeholders. The launcher can report PASS with process exit 7 by reading stale JUnit. Its integration list omits the new composition tests.

See `01_FINDINGS.md` for source locations and `02_PHASE1_REPAIR_PLAN.md` for completion tasks and acceptance conditions. `03_PHASE1_ONLY_AGENT_PROMPT.md` is the next implementation prompt, **to use only after owner approval**.

## What did improve

Structured PLAN submission works with a real scripted Deep graph. Role denial now exists at wrapped desktop tools; strict mode refuses a missing ledger. Canonical scope hashes, stale/unknown surface refusal, explicit refusal handling, required-check-ID completeness, evidence hash/expiry checks, and shared service construction are present. Host input origin and mission-pending outcome mapping improved. The new launcher correctly blocks skipped suites, and static type additions from the previous review were removed.

These are partial successes, not evidence that every original T01–T12 or G0–G7 requirement is satisfied. Existing fixture tests often replace the actual policy wrapper with `FakeTool.ainvoke`, which records calls but does not execute the production guard; passing those tests cannot establish shipping integration.

## Gate decision

| Gate | Independent decision |
|---|---|
| G0 source baseline | Source identity verified; prior review preserved |
| G1 contracts/persistence | Partial; pause/revision/reconciliation defects remain |
| G2 authority/privacy/budgets | Fail; bypass, revision persistence, and budgeting gaps |
| G3 Controller + bounded Velo | Fail; incomplete composition, transport, catalogs, checks |
| G4 ownership/stop/restart | Partial/fail; fencing/reconciliation and live proof missing |
| G5 voice output/input preservation | Implementation incomplete; live engine/audition also unrun |
| G6 observer/evidence | Partial; hash improvements, but sanitization/retention gaps remain |
| G7 test/packaging/performance | Fail; empty live cases and runner false-PASS; live bundle/performance unrun |

No production incident is asserted. The synthetic mutation and synthetic secret persistence reproduce failure paths in disposable fixtures. No actual desktop action, account write, real credential leak, paid call, asset download, deployment, commit, push, provider change, or RSI experiment occurred in this review.

**Next authorized step in this chat is the owner's decision. Do not implement repairs or start Phase 2 until the owner says to start.**

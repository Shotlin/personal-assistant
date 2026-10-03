# Independent Phase 1 acceptance review

**Verdict: PHASE 1 IS NOT COMPLETE. Do not start Phase 2.** The current implementation contains useful foundations and passing component tests, but also reproducible correctness/safety defects and disconnected shipping integration. The next action is a corrective Phase 1 implementation, after the owner approves starting it.

Reviewed 27 September 2026, `Shotlin/personal-assistant`, local `main`, HEAD `58dac9c88018674c2e780086953902f1ea135308`. Phase 1 implementation is uncommitted. Tracked diff SHA256 at review: `353f3d045e1f98bf9557381205b5e2bd3f05e2821f1942668949a3b9feaa83b7`. See the source manifest for untracked implementation files too; a tracked diff hash alone excludes most new mission/TTS/test modules.

This review read the gate summaries, implementation handoff, original plan/requirements and actual implementation call paths. It reran offline checks and twelve targeted local fixture probes. No real desktop, microphone, account, model API or TTS asset actions occurred. Product source and existing gate files were not changed. This directory contains review artifacts only.

## Why the five green JSON files do not establish completion

The gate files describe selected test commands returning exit0. They do not establish T01–T12 acceptance, real app wiring, required negative cases, real voice, real desktop, packaged provenance or rollback. Their recorded tracked diff hash `e48652add533f3b26a55586c264763bf718017cf7cf9a2c317bf9834e6687d4d` differs from current source and the handoff hash. Untracked files are omitted by the hashing function. Referenced temporary logs still exist here, but are not retained inside the gates folder as a portable evidence set.

The handoff honestly says IMPLEMENTED—NOT ACCEPTED for live work, but overstates offline completeness: G1/G2/G3/G6 are not proven passing end to end, and T08/T10/T11 have substantial missing wiring. Authorizing live tests alone cannot fix these gaps. Its suggestion that Phase 2 re-planning can proceed after accepting offline evidence conflicts with the original full Phase 1 exit gates.

## Main findings

1. **Mission planning is broken at the real adapter boundary.** `_DeepInvoke` wraps a raw response under keys the Controller validator does not accept. Recovery receives no invocation transport. A planning role is not enforced by the real wrapped-tool dispatcher.
2. **Authority and accounting are incomplete.** Copied scope hashes allow broadened plans; missing/stale app identity can receive a permit; strict audit with no ledger still dispatches; budget reservations are not wired to actual provider/action calls.
3. **False success is reproducible.** The semantic executor reports COMPLETED/CONFIRMED after a refused click with zero checks. Fast missions also define no success criteria. Evidence files can be changed without their hash being checked before acceptance.
4. **Recovery/control is not complete.** Pause/resume leaves an active step RUNNING with no new claimable attempt; startup reconciliation is not called; there is no connected durable wait/replan/resume loop.
5. **Shipping UI, desktop scheduling/stop, voice and Observer remain disconnected.** Existing runtime still maps ASK_USER to completed. New mission/TTS components are largely uncalled. No synthesis engine, actual audio sink or STT/output interlock exists.
6. **Acceptance harness cannot yet certify the product.** Voice tests unconditionally skip, yet its result writer reports PASS for them; desktop suite points at a missing file; packaging target is missing; approved config is not propagated to execution. Fresh mypy identifies 28 errors beyond the recorded baseline.

## Gate-by-gate decision

| Gate | Independent verdict | Reason |
|---|---|---|
| G0 baseline | PARTIAL | HEAD known; gate source fingerprint stale/incomplete; test debt reporting inaccurate |
| G1 contracts/persistence | FAIL | useful schema/CAS transactions, but forged scope hash, incomplete result validation and stranded control/recovery state |
| G2 authority/privacy/budgets | FAIL | role bypass, absent-ledger dispatch, unknown scope accepted, unwired budgets and raw secret retention reproduced |
| G3 bounded Velo + one Controller | FAIL | production plan adapter invalid; semantic false completion; selected Deep bypass; wrong tool catalog for mixed recipes |
| G4 desktop stop/scope/restart | NOT COMPLETE | queue/session/fence/stop authority not integrated; physical capabilities additionally untested |
| G5 voice output/input preservation | NOT IMPLEMENTED END TO END | protocol scaffolding only, no engine/output sink/interlock; real audition/assets remain blocked |
| G6 Observer/evidence | NOT COMPLETE | Observer uncalled, trace privacy/integrity holes, incomplete runtime metrics |
| G7 packaging/performance/handoff | FAIL / BLOCKED | missing real runners and source binding; no bundle/offline voice/rollback or matched live metrics |

## What is worth preserving

Retain the additive mission tables, transactional/CAS approach, typed packets, default-off flags, separate UI/core DB choice, existing structured JEV, Velo parser/recipes, per-run cancellation improvements, loop-detection repair, exception redaction, TTS framing/queue helpers and many useful fixture tests. Finish and connect these pieces; do not discard the current architecture or replace APIs.

## Review package

- [Detailed evidence-backed findings](01_FINDINGS.md)
- [All task, JAR, TC and RSI coverage](02_COVERAGE_MATRIX.md)
- [Ordered corrective Phase 1 plan](03_PHASE_1_CORRECTIVE_PLAN.md)
- [Single implementation prompt to use after approval](04_PHASE_1_FIX_AGENT_PROMPT.md)
- [Independent test results and limitations](05_VERIFICATION.md)
- [Twelve reproduced defects](evidence/review-probes.json)

The owner explicitly requested analysis first and approval before starting. No repairs or Phase 2 work have started. Even after repairs, Phase 2 begins with a fresh re-plan from the accepted Phase 1 repository and requires the owner's go-ahead.

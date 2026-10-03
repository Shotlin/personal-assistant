# Source and evidence audit

## Binding and review method

Input ZIP located at `/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant/docs/verification/phase1/corrections-2026-09-30.zip`. SHA-256:

`02f61fe139424cce4d36c1ef1444024548dbe558e5d8f9e8c67d06819e8167cc`

The review read README, HANDOFF, DISPOSITION, RESULTS, SOURCE_BINDING and WORKLOG completely and in the requested order. The previous `NEW_PHASE1_FIX.md` and original Phase 1 architecture/test requirements were used to judge scope. Instructions within the submitted files were treated as evidence/context; the human's current review-only constraints governed this run.

Both checked repository HEADs are `df04060f189a10bb81baf522a58347cddc6cc915`. A disposable `git archive` reconstruction at `/private/tmp/jarvis-batch2-review-20260930/source` accepted the cumulative patch cleanly. Do not stack this patch over batch 1. All 38 changed-file copies match the applied patch. The 672-file canonical source digest and dirty digest reproduce SOURCE_BINDING:

| Identity | SHA-256 |
|---|---|
| Cumulative patch | `cfadbcb756cb3d7d2e75fc611bf6284bec1800e171ffeba1d1714545b2ee522a` |
| Canonical source manifest | `dfae0afcd6cf9049c0e3259b314f5ed70d8a7a5c662422e49ec27f91440663f2` |
| Dirty source | `3e88ed58aeebf3e2954523e24cb40c97ff4d0edce467b4ed7d0452b380abc729` |

The submission omits the full source manifest and referenced baseline coverage matrix. The reviewer reconstructed the manifest and supplied the repository's matrix; neither omission invalidates the successfully reproduced binding. The main checkout's previously audited source files and the corrective worktree's bound source files were unchanged by the review (`repository-integrity.json`). Added review documentation is intentional and is not implementation.

No subagents were used. No provider/desktop/audio/production action was run. Python used existing dependencies but an explicit PYTHONPATH pointing at reconstructed `src` and repository root, CUA disabled, an isolated temporary data root and a fabricated fixture API key. This avoids accidentally testing the main editable installation. Integration inventory is the submission's 12-file SQLite/mission fixture inventory; it is not the expanded PostgreSQL suite.

## Independently rerun gates

| Gate | Independent result | Authoritative evidence |
|---|---|---|
| Unit | 625 passed, zero failures/errors/skips | `evidence/unit-final.log`, `unit-final.xml` |
| Mission integration inventory | 96 passed, zero failures/errors/skips | `integration.log`, `integration.xml` |
| Performance fixtures | 7 passed | `performance.log`, `performance.xml` |
| Native Rust | 121 passed, zero failures, 2 ignored | `rust.log`, offline locked test run |
| Renderer | TypeScript/Vite build passed | `renderer.log` |
| Mypy | 91 existing diagnostics; not clean | `mypy.log`, `static-comparison.json` |
| Ruff, src/tests | 42 existing diagnostics; not clean | `ruff.json`, `static-comparison.json` |

The first unit run had 19 provenance-related failures because the archive reconstruction lacked `.git`. A copied, separate Git metadata directory enabled those fixtures; the affected 33 cases passed on recheck and the complete final unit run passed. This is an environment correction, not an undisclosed oracle/source edit. Earlier unit logs remain in the package and are explicitly superseded by `unit-final.*`.

The native and renderer copies reused existing local dependency/build caches without changing original source or fetching a new engine/provider. Rust's ignored cases remain ignored; fixture performance is not physical latency. Exact commands and review drivers are retained in `evidence/`.

Static comparison used complete normalized diagnostic multisets against the prior independent review baseline, not totals alone. Result: **91 mypy / 42 ruff, 0 added, 0 removed**. This substantiates parity, not cleanliness. The previous baseline logs are supplied under `context/static-baseline/` for rechecking.

## Four declared oracle updates

| Update in RESULTS | Decision | Reason |
|---|---|---|
| Unit migration list `[1,2,3] → [1,2,3,4]` | **Justified** | Migration 4 is additive. Existing migration history is retained. |
| Integration migration list, same change | **Justified** | Same schema expectation, exercised at integration level. |
| Idle stop no longer expects generation to increment | **Justified** | A no-op stop must preserve an unrelated valid lease. The guard mutation restoring the increment fails the corresponding assertion. |
| Evidence candidate's fixed January timestamp becomes current time | **Justified** | Ordinary positive evidence is now assigned expiry relative to capture time. The old fixed date made the positive stale. Explicit stale/expired negative cases remain; the freshness requirement was not removed. |

No unjustified weakening was found in these four declared updates. This decision is limited to the listed changes. The important coverage weaknesses are the newly added positive fixtures' composition and assertions: they bypass actual callback exception handling, omit multi-step approval recovery, use arbitrary control changes as outcome proof, and never execute several claimed live properties.

## RED evidence and chronology

These counts were checked from submitted raw JUnit, not copied from prose:

| Submitted RED suite | Raw result | What it proves |
|---|---|---|
| Owner controls / red-boundaries | 1 collection error: missing PendingApprovalDigest contract | New suite does not collect on prior source. **No approval/wait behavior ran red.** |
| Scope outcomes | 9 tests: 7 failures, 2 passes | Prior source lacks/refuses some newly required behavior. RESULTS/HANDOFF's “7 of 8” is wrong. Some failures concern absent new verifiers rather than a subtle safety mutation. |
| Provider/privacy | 6 tests: 6 failures | Several expected new APIs/constructor arguments are absent; an executed canary assertion also exposes a prior persistence leak. Separate API absence from behavioral guard proof. |
| Worker D09 | 3 tests: 3 assertion failures | Useful executed baseline failures for the tested worker slices. Does not cover saturated shutdown, native lock interleaving or packaged runtime availability. |

The records compare newly added suites retrospectively against base plus the prior corrective patch. This is legitimate baseline regression evidence, but cannot establish prospective red→green implementation chronology. Do not claim all repairs have behavioral RED merely because each suite is nonzero. This review audited submitted historical RED artifacts; it did not create or rewrite that history.

The package includes an intermediate E2E collection failure/skip record and a final E2E record with **14 tests, 14 skips**. The latter is the relevant blocked result, not live success.

## Four independent guard mutation replays

The submitted exact mutation anchors were applied separately in four disposable copies of the reconstructed source. All selected cases pass in the unmutated final suite. Each mutant failed an intended assertion, without collection/dependency errors:

| Mutant | Tests | Failures | Errors | Interpretation |
|---|---:|---:|---:|---|
| approval-release | 1 | 1 | 0 | Removing exact approval restriction is detected. |
| origin-readonly | 1 | 1 | 0 | Reopening configured-origin READ escape is detected. |
| stop-noop | 2 | 1 | 0 | Idle generation bump is detected; unaffected positive still passes. |
| sink-screening | 1 | 1 | 0 | Removing the selected result sink's screening is detected. |

**4/4 meaningful mutations confirmed.** They establish sensitivity for these four guards, not all newly claimed safety properties. `mutation-replay.json`, per-mutant logs/JUnit, the independent replay driver and original mutation definitions are included.

## Independent additional probes

`evidence/probes.py` uses actual store/service/queue/verifier code and actual LangChain callback management with a fake local chat model. There are no provider requests, desktop effects, audio devices or real secrets. Results are `probes.json` and `probes-final.log`:

| Probe | Observed result | Finding |
|---|---|---|
| Callback ceiling 2 | 5 requests completed, 5 rows, callback exception propagation disabled | D11 |
| Broken metering store | Provider-shaped local response still returned | D11 |
| Delete mission A with mission B present | Both files unlinked; B row still active | D12 |
| Two-step approval | RUNNING; target missing; RESUME rejected | D13 |
| Pause/resume pending approval | RUNNING; BLOCKED step; zero obligations | D13 |
| External wait on UNKNOWN | Old outcome UNKNOWN; new attempt 2 admitted | D14 |
| Press expected third control | Unrelated first control focus change accepted | D15 |
| Field same token, different window/process | Wrong window accepted | D15 |
| Wait-row synthetic secret | Raw canary present in durable row | D16 |
| Same-run old/new lease | Old unfenced cleanup releases new lease | D19 |
| Full worker queue shutdown | Still running after 2.3 seconds; exits after engine release | D18 |

Reviewer setup errors in the earlier `probes.log` / `probes-recheck.log` are retained but not cited as defects. The final run completed all probes.

The separate Rust `voice-lock-probe.rs` copies the queue implementation and adds an explicit delay/barrier to expose a state→queue versus queue→state interleaving. It uses a dummy sink and observes both threads stuck. This is a controlled demonstration of a code-level lock cycle (D17), not an unmodified physical audition. The source instrumentation and compiler warnings are retained for transparency.

## BLOCKED gate audit

| Gate | Review disposition | Reason |
|---|---|---|
| Live desktop correctness / wrong focus/account / stop | **BLOCKED; no live acceptance** | No approved config or live run. Final E2E skipped. Several harness bodies need repair before they can measure their promised property. |
| Real-engine voice audition / output / STT coexistence | **BLOCKED; no physical acceptance** | No engine selection/assets/audio session. Worker protocol fixtures and sequential dummy sink tests are insufficient. |
| Installed packaging / restart / rollback | **BLOCKED and harness incomplete** | Identity hashing exists; install/rollback still explicitly skips. TTS packaged resource/build wiring is missing. |
| Physical held-input release / human takeover | **BLOCKED; production hookup incomplete** | Optional mock acknowledgement is not a connected driver signal or physical proof. |
| Expanded PostgreSQL suite | **BLOCKED; not run here** | Submission reports unavailable service. SQLite inventory does not substitute. This review did not start/contact a database service to change that state. |

The submission honestly retains these BLOCKED states. It overclaims some fixture repairs and harness executability, not a final physical PASS. This review cannot infer every historical safety incident's absence from prose; the synthetic probes demonstrate admission/data handling defects without claiming a real wrong-target, duplicate effect or real secret incident occurred.

## Review completion

Review-only scope is complete: source binding, seven priorities, four oracles, RED limits, guard mutations, static parity and blocked gate status have been assessed. **Implementation and Phase 1 acceptance are incomplete.** The next prompt is supplied separately and has not been executed.

# Complete carried-forward Phase 1 disposition matrix

IMPLEMENTED describes present code; FIXTURE-VERIFIED describes the tested slice; LIVE-BLOCKED means no authorized physical acceptance; ACCEPTED is reserved for all required evidence plus owner acceptance. No whole Phase 1 requirement below is marked ACCEPTED. DEFERRED applies only to the original future-phase slices. D01–D10 refer to 01_FINDINGS.md; fixture totals never substitute for a row-specific oracle.

## T01–T12 implementation tasks

| Original row | Current disposition |
|---|---|
| T01 baseline/oracles | Baseline/source identity FIXTURE-VERIFIED; full shipping acceptance LIVE-BLOCKED |
| T02 contracts/store | PARTIAL implementation; D01/D02 lifecycle and D07 persistence FAIL |
| T03 authority/evidence/budget | PARTIAL guards; D03/D05/D07 FAIL |
| T04 bounded Velo/verification | PARTIAL executor; catalog FIXTURE-VERIFIED; D06 outcome FAIL |
| T05 desktop ownership/stop | PARTIAL wiring; D08 queue FAIL; physical takeover/stop LIVE-BLOCKED |
| T06 MissionService/Deep | PLAN composition FIXTURE-VERIFIED; D04 role/fast path FAIL |
| T07 recovery/IPC | PARTIAL IPC/recovery; D01/D02 FAIL; durable wait missing |
| T08 unified intake/UI | PARTIAL status/control UI; D10 exact approval/revision/priority missing |
| T09 local TTS selection/package | PARTIAL protocol; D09 FAIL; engine/assets/audition LIVE-BLOCKED |
| T10 playback/STT interlock | PARTIAL sink/interlock; D09 FAIL; physical STT/output LIVE-BLOCKED |
| T11 trace/Observer | PARTIAL Observer; D06/D07 outcome/retention FAIL |
| T12 shipping acceptance/handoff | Broad fixture/build checks VERIFIED; D10 harness incomplete; LIVE-BLOCKED |

## JAR-001–024 requirements

| Original row | Current disposition |
|---|---|
| JAR-001 preserve actual app | Preservation fixture regressions pass; shipping acceptance LIVE-BLOCKED |
| JAR-002 conversational intent/control | Basic control/status implemented; D02/D10 incomplete |
| JAR-003 voice input | Existing STT retained; connected voice coexistence LIVE-BLOCKED D09 |
| JAR-004 local TTS | D09 FAIL; selected local voice LIVE-BLOCKED |
| JAR-005 Deep Controller | PLAN fixture-verified; D04 RECOVER/REVIEW FAIL |
| JAR-006 bounded JEV | Existing bounded JEV reused; packet ceiling D05 FAIL |
| JAR-007 durable missions | Persistence present; D01/D02 FAIL |
| JAR-008 safe recovery | Aggregation fixed; D01/D02 uncertainty/restart FAIL |
| JAR-009 human control | Basic stop/control present; D08/D10 incomplete |
| JAR-010 coding supervision | Coding workflow DEFERRED P2; foundation D03/D10 incomplete |
| JAR-011 authorized reuse | Reuse workflow DEFERRED P2; privacy/scope D03/D07 incomplete |
| JAR-012 truthful reports | Reporting workflow DEFERRED P2; truth foundation D06 incomplete |
| JAR-013 creative applications | Creative workflow DEFERRED P2; recovery foundation D01/D02 incomplete |
| JAR-014 Obsidian | DEFERRED P2; SQLite independence retained |
| JAR-015 cost/latency | Outer-plan accounting fixed; D04/D05 budgets/fast path FAIL; latency LIVE-BLOCKED |
| JAR-016 Observer | Observer consumer implemented; D07 lifecycle incomplete |
| JAR-017 deterministic permissions | Role/default guard fixture-verified; targeted discovery D03 FAIL |
| JAR-018 account/auth | Named-scope guard partial; targeted read D03 FAIL; login/resume D02 incomplete |
| JAR-019 lifecycle | D01/D02/D08/D09 lifecycle incomplete |
| JAR-020 independent outcome | D06 independent-outcome FAIL |
| JAR-021 private evidence | Revision screening verified; plan persistence/retention D07 FAIL |
| JAR-022 controlled learning | Observation-only preserved; foundation D07 incomplete; experiments DEFERRED P3 |
| JAR-023 tests/acceptance | Fixtures rerun; D10 real harness incomplete; LIVE-BLOCKED |
| JAR-024 coherent phases/handoff | Phase boundaries preserved; P1 handoff truth corrected; owner acceptance pending |

## TC-01–36 acceptance cases

| Original row | Current disposition |
|---|---|
| TC-01 baseline voice/chat/CUA | Fixture regressions verified; shipping L1 LIVE-BLOCKED |
| TC-02 voice/text equivalence | Host correlation implemented; full final voice/text replay LIVE-BLOCKED D10 |
| TC-03 partial/correction | D02/D10 revision/correction workflow incomplete |
| TC-04 local speech offline | D09 FAIL; offline audible output LIVE-BLOCKED |
| TC-05 speech interruption | D09 queue/control incomplete; audible interruption LIVE-BLOCKED |
| TC-06 technical report voice | Voice engine/audition/intelligibility LIVE-BLOCKED D09 |
| TC-07 routine multi-step | PLAN/catalog fixtures verified; D01/D04/D06 full mission incomplete |
| TC-08 JEV out-of-scope | Known wrong-scope denies; bootstrap D03 FAIL |
| TC-09 duplicate/stale result | Store duplicate/CAS fixtures verified; D01 real dispatch lineage incomplete |
| TC-10 crash after effect | D01/D02 replay/restart FAIL; physical child crash LIVE-BLOCKED |
| TC-11 no-progress | Existing no-progress fixtures pass; full adversarial GUI LIVE-BLOCKED D06 |
| TC-12 desktop contention | D08 independently reproduced queue FAIL |
| TC-13 focus shift before paste | D08 takeover/final dispatch incomplete; physical focus shift LIVE-BLOCKED |
| TC-14 wrong coding workspace | Coding workflow DEFERRED P2; generic scope D03 incomplete |
| TC-15 worker question | DEFERRED P2 worker interaction |
| TC-16 privilege/deletion request | Role/guard refusals fixture-verified; full owner approval D02/D10 incomplete; worker slice P2 |
| TC-17 rate limit/wait | Generic durable wait missing D02; full worker rate-limit workflow P2 |
| TC-18 repo reuse | DEFERRED P2 reuse; current scope foundation D03 incomplete |
| TC-19 secret copied content | Revision screening verified; plan secret canary FAIL D07; reuse slice P2 |
| TC-20 unsupported tested claim | D06 unsupported completion remains possible |
| TC-21 conflicting milestones | Report conflicts DEFERRED P2; generic evidence truth D06 incomplete |
| TC-22 login/resume | Auth checkpoint/resume D02 incomplete; domain Flow P2 |
| TC-23 credit timeout | Multi-ID fixture fixed; D01 actual UNKNOWN lineage incomplete; paid Flow P2 |
| TC-24 corrupt media | Existing artifact fixtures retained; real corrupt-media workflow P2; package verification LIVE-BLOCKED |
| TC-25 concurrent vault edit | DEFERRED P2 concurrent vault edit |
| TC-26 missing vault | Control plane has no vault dependency; full vault behavior DEFERRED P2 |
| TC-27 injected document/page | Real Controller mutation refusal verified; scope/privacy/oracle D03/D06/D07 incomplete |
| TC-28 wrong client account | D03 missing identity read permit FAIL; company workflow P2 |
| TC-29 audio/STT/CUA load | D09 buffering incomplete; combined physical load LIVE-BLOCKED |
| TC-30 exhausted budget | D05 zero packet JEV ceiling FAIL; outer Deep accounting fixed |
| TC-31 observation only | Observer connected and authority-free by interface; D07 lifecycle incomplete |
| TC-32 screenshot/log secrets | Evidence helper screens; plan sink canary FAIL D07 |
| TC-33 revoke/sleep | D02/D08 lifecycle gaps; real sleep/revoke LIVE-BLOCKED |
| TC-34 emergency stop | Native latch wiring present; D08 held-input/final dispatch incomplete; LIVE-BLOCKED |
| TC-35 unsupported worker success | D06 verification FAIL; future coding-worker slice P2 |
| TC-36 portable phase package | This source-bound package produced; owner acceptance/installed package LIVE-BLOCKED |

## RSI-01–25, preserving original source order

| ID | Current disposition |
|---|---|
| RSI-01 | Trace/correlation implemented; D01 action lifecycle incomplete |
| RSI-02 | Ledger present; dispatched-state linkage FAIL D01 |
| RSI-03 | Failure evidence partial; D06/D07 incomplete |
| RSI-04 | D02/D10 correction workflow incomplete |
| RSI-05 | D06 evidence versus report FAIL |
| RSI-06 | Observer consumer implemented; recommendation quality/dedup not fully verified |
| RSI-07 | Authority-free Observer preserved; no experiment executor activated |
| RSI-08 | Recommendation references present; operational expiry incomplete D07 |
| RSI-09 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-10 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-11 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-12 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-13 | Mission packet budget FAIL D05; experiment budgets DEFERRED P3 |
| RSI-14 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-15 | External outcome oracle FAIL D06; full evaluator DEFERRED P3 |
| RSI-16 | Independent held-out probes added here; broad transfer DEFERRED P3 |
| RSI-17 | Three guard mutations detected; complete protected candidate regression DEFERRED P3 |
| RSI-18 | No promotion or grader modification performed; candidate grader protection DEFERRED P3 |
| RSI-19 | Current source exactly bound; installed bundle LIVE-BLOCKED; promotion DEFERRED P3 |
| RSI-20 | Mission trace partial D01/D07; candidate lineage DEFERRED P3 |
| RSI-21 | Installed rollback LIVE-BLOCKED D10; candidate rollback DEFERRED P3 |
| RSI-22 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-23 | Recursive stage remains disabled; no RSI activation |
| RSI-24 | DEFERRED P3 by original plan; no claim of implementation or acceptance |
| RSI-25 | DEFERRED P3 by original plan; no claim of implementation or acceptance |

## RSI Flow and safety families

| ID | Current disposition |
|---|---|
| RSI-FLOW-01 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-FLOW-02 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-FLOW-03 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-FLOW-04 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-FLOW-05 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-FLOW-06 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-FLOW-07 | Domain workflow DEFERRED P2/P3; generic Phase 1 scope/recovery/wait/outcome remains incomplete D01/D02/D03/D06 |
| RSI-SAFE-01 | Phase 1 privacy/authority/evidence/budget foundation FAIL D03/D05/D06/D07; no live acceptance |
| RSI-SAFE-02 | Phase 1 privacy/authority/evidence/budget foundation FAIL D03/D05/D06/D07; no live acceptance |
| RSI-SAFE-03 | Phase 1 privacy/authority/evidence/budget foundation FAIL D03/D05/D06/D07; no live acceptance |
| RSI-SAFE-04 | Phase 1 privacy/authority/evidence/budget foundation FAIL D03/D05/D06/D07; no live acceptance |
| RSI-SAFE-05 | Candidate/evaluator/archive/improver safeguards DEFERRED P3; not activated |
| RSI-SAFE-06 | Candidate/evaluator/archive/improver safeguards DEFERRED P3; not activated |
| RSI-SAFE-07 | Candidate/evaluator/archive/improver safeguards DEFERRED P3; not activated |
| RSI-SAFE-08 | Candidate/evaluator/archive/improver safeguards DEFERRED P3; not activated |
| RSI-SAFE-09 | Candidate/evaluator/archive/improver safeguards DEFERRED P3; not activated |

## G0–G7

| Gate | Current disposition |
|---|---|
| G0 | Baseline/source FIXTURE-VERIFIED |
| G1 | FAIL D01/D02/D07 |
| G2 | FAIL D03/D05/D07 |
| G3 | FAIL D04/D06 |
| G4 | FAIL D01/D02/D08; LIVE-BLOCKED |
| G5 | FAIL D09; LIVE-BLOCKED |
| G6 | PARTIAL / FAIL D06/D07 |
| G7 | Harness incomplete D10; LIVE-BLOCKED |

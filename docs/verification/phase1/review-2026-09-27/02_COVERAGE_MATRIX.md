# Coverage against the accepted Phase 1 plan and owner requirements

Status means product acceptance, not whether a corresponding file exists. PARTIAL = useful code/tests but incomplete shipping behavior; FAIL = contradictory behavior reproduced or proven by call-site inspection; DEFERRED = explicitly future domain capability. No row marked PARTIAL is a Phase1 acceptance pass.

## Original T01–T12 tasks

| Task | Status | Evidence and missing work |
|---|---|---|
| T01 baseline/oracles | PARTIAL | baseline, default-off flags and four regression oracles exist; gate fingerprint excludes untracked files and postdates source drift; ineffective deadline test; F12–13 |
| T02 contracts/store | PARTIAL / FAIL | additive SQLite/CAS useful; false scope hash, insufficient check binding, pause/resume strand, incomplete operation lineage; F03/05/07 |
| T03 authority/evidence/budget | FAIL | actual wrapper bypasses role; absent-ledger path dispatches; empty identity permitted; scope-sensitive reads unguarded; budget invocation not reserved; F02–04/11 |
| T04 bounded Velo/verification | FAIL | real adapter exists, preserves recipes; refused semantic click completed, empty criteria, missing payload plumbing, wrong mixed-step catalog; F05 |
| T05 desktop ownership/stop | NOT CONNECTED | queue/latch helpers tested but no executor session/fence integration; takeover/release not implemented; F06 |
| T06 MissionService/Deep | FAIL | production plan shape invalid; split service resources, no structured graph submissions/recovery transport/final review; F01/02/08 |
| T07 recovery/IPC | PARTIAL / FAIL | Python IPC endpoints exist; no runtime recovery/resume/replan/wait scheduling, no host router; F07–09 |
| T08 unified intake/UI | MOSTLY MISSING | stable message run ID added; mission status/control components unused, origin/revision hardcoded, old completed mapping remains; F09 |
| T09 local TTS selection/package | PARTIAL / BLOCKED | protocol/worker skeleton; no selected engine/lock/assets, legitimate audition gate; F10 |
| T10 playback/STT interlock | MOSTLY MISSING | queue unit code only; no output sink or shipping input interlock; F10 |
| T11 trace/Observer | PARTIAL / FAIL | event/store/observer helpers and usage callback exist, no Observer runtime consumer, metadata incomplete, sink privacy/integrity gaps; F04/11 |
| T12 shipping acceptance/handoff | FAIL / BLOCKED | existing fixture gates mostly pass; harness missing/skip can pass; new type errors; no real bundle/perf/rollback/live completion; F12–13 |

## JAR-001 through JAR-024

| Requirement | Phase1 review result | Needed correction / future boundary |
|---|---|---|
| JAR-001 preserve actual app | PARTIAL | useful default-off isolation and prior regressions; new path must integrate without replacing existing stack; F01/06/09/10 |
| JAR-002 conversational intent/control | FAIL | question heuristic bypass, hardcoded origin/revision, unconnected control/revision; F02/07/09 |
| JAR-003 voice input | PARTIAL | existing STT unchanged; new output/input interlock, background/echo/correction tests absent; F09/10 |
| JAR-004 local TTS | NOT COMPLETE | no real engine/output path/stop/device handling; F10; audition genuinely pending |
| JAR-005 Deep Controller | FAIL | production schema mismatch, no role-bound tool binding/recovery/review; F01/02 |
| JAR-006 bounded JEV | PARTIAL / FAIL | compact types, reused JEV; scope/hash/semantic selection/payload/verification not safe; F03/05 |
| JAR-007 durable missions | PARTIAL / FAIL | schema/CAS exists; control/restart/acceptance invariants incomplete; F07/08 |
| JAR-008 safe recovery | FAIL | no startup integration, stranded steps, uncertainty handling; F04/07 |
| JAR-009 human control | NOT COMPLETE | native latch is unconsumed by dispatch/admission; no takeover/held-input path; F06/09 |
| JAR-010 coding supervision | P2 DEFERRED; P1 foundation FAIL | no coding worker required now; scope/status/permission foundation still needs F02/03/09 |
| JAR-011 authorized reuse | P2 DEFERRED; P1 privacy FAIL | scope/read/root/evidence gates need F03/11 |
| JAR-012 truthful reports | P2 DEFERRED; P1 truth FAIL | false success/UI state/evidence integrity F05/09/11 |
| JAR-013 creative applications | P2 DEFERRED; P1 recovery FAIL | finish generic reconciliation/effect scope/verification F03/05/07 |
| JAR-014 Obsidian | P2 DEFERRED | correct to have no vault writer now; preserve SQLite control-plane independence |
| JAR-015 cost/latency | FAIL | budget reservations absent at runtime, no measured live targets, fake deadline oracle; F04/12 |
| JAR-016 Observer | NOT CONNECTED | helper exists, no real event consumer or operational evidence; F11 |
| JAR-017 deterministic permissions | FAIL | Controller bypass, forged scope, absent-ledger guard; F02/03/04 |
| JAR-018 account/auth | FAIL | unknown app accepted; read account checks omitted, no actual host identity flow; F03/08 |
| JAR-019 lifecycle | NOT COMPLETE | session/scheduler/fence/recovery/control/audio lifecycle unconnected; F06/07/10 |
| JAR-020 independent outcome | FAIL | refused click success, empty/missing checks, tampered evidence; F05/11 |
| JAR-021 private evidence | FAIL | raw goal canary persisted; hash/scope/expiry not checked, no connected retention; F11 |
| JAR-022 controlled learning | PARTIAL | experiments/recursive work correctly absent; observation foundation unconnected; F11; full system P3 |
| JAR-023 tests/acceptance | FAIL | green components insufficient; skip→PASS, harness gaps, untested production boundaries; F12/13 |
| JAR-024 coherent phases/handoff | PARTIAL | phases kept separate; handoff must not invite P2 from unaccepted offline-only P1; F13 |

## TC-01 through TC-36

| Case | Current P1 evidence / verdict | Required next evidence |
|---|---|---|
| TC-01 baseline voice/chat/CUA | prior suites mostly pass; new path not live-accepted | full shipping regression with unchanged old path and connected new path |
| TC-02 voice/text equivalence | only stable run_id; origin always typed_final | actual final voice/text handler equivalence/replay tests |
| TC-03 partial/correction | old input protections retained; new revision loop absent | correction invalidates active input and resumes new plan |
| TC-04 local speech offline | no real engine/playback | licensed installed assets + offline cold synthesis |
| TC-05 speech interruption | queue fixture only | connected audio stop, separate mission pause/cancel and PTT |
| TC-06 technical report voice | corpus exists; no audition | intelligibility/tone rating and evidence-matched spoken text |
| TC-07 routine multi-step | scripted planner bypasses broken actual adapter | production adapter, mixed recipes, exact model counters, real result |
| TC-08 JEV out-of-scope | some helper negatives; scope hash bypass RP05 | copied-hash/wrong-target/read-scope denial at actual tool |
| TC-09 duplicate/stale result | useful store tests; required evidence validation gaps | whole dispatcher restart/duplicate/outbox test |
| TC-10 crash after effect | DB simulations; startup recovery unused | kill actual child at action/ack boundary, reconcile once |
| TC-11 no-progress | alternation fix/tests useful | complete semantic execution loop against changing/distractor screen |
| TC-12 desktop contention | queue helper only; RP12 two active unfenced claims | composed service/session queue exclusivity and cancellation-at-grant |
| TC-13 focus shift before paste | no actual takeover path | gate at final dispatch and real isolated focus-switch test |
| TC-14 wrong coding workspace | P2 workflow deferred; P1 guard gaps | current-scope identity negatives now; coding app test P2 |
| TC-15 worker question | correctly P2 deferred | full worker flow re-planned after P1 |
| TC-16 privilege/deletion request | helper deny useful; role bypass remains | production wrapper and approval-policy negative now; worker flow P2 |
| TC-17 rate limit/wait | counters/helpers, no durable wait runner | current provider Retry-After bounded checkpoint/resume now; worker P2 |
| TC-18 repo reuse | P2 deferred | no new reuse agent now; scope containment guards P1 |
| TC-19 secret copied content | EvidenceStore helper screens; mission goal does not RP08 | all sink/egress canary tests P1; repository reuse P2 |
| TC-20 unsupported tested claim | false semantic completion RP04 | hard acceptance gate in shipping report |
| TC-21 conflicting milestones | full report P2; evidence truth incomplete | preserve source vs observation in P1; project conflict test P2 |
| TC-22 login/resume | no connected resume/reconciliation | synthetic auth checkpoint + scope revalidation P1; Flow P2 |
| TC-23 credit timeout | no real paid action performed, correctly | operation-ID UNKNOWN reconciliation in composed fixture; Flow P2 |
| TC-24 corrupt media | verifier suffix/nonempty insufficient | real content/type corruption negative now; media workflow P2 |
| TC-25 concurrent vault edit | correctly P2 deferred | no vault writes in corrective P1 |
| TC-26 missing vault | no vault control dependency | preserve independence now, full vault lifecycle P2 |
| TC-27 injected document/page | role/scope bypasses RP02/05 | hostile text cannot alter real authority or grader |
| TC-28 wrong client account | read-scope guard missing | deny mismatched account reads/writes now; company reports P2 |
| TC-29 audio/STT/CUA load | not measured, output path missing | connected buffers/thread/device limits + target measurements |
| TC-30 exhausted budget | helpers pass; real Deep budget bypass RP11 | actual reservation before model/action/retry, crash persistence |
| TC-31 observation only | helper tests; no runtime Observer | real event to separate recommendation sink, code/config hashes unchanged |
| TC-32 screenshot/log secrets | screenshot withholding useful; mission DB leak RP08 | all raw/derived sinks screened before write/egress |
| TC-33 revoke/sleep | existing host lifecycle coverage; mission wiring missing | real generation change/sleep/permission fixture then local acceptance |
| TC-34 emergency stop | atomic flag/cancel signal only | actual dispatch/admission gate, held input, no stale audio |
| TC-35 unsupported worker success | helper checks insufficient, RP04/09 | independent required evidence, refusal/missing/tampered negatives |
| TC-36 portable phase package | original package good; handoff incomplete | corrected source-bound handoff + evidence and explicit P2 stop |

## RSI foundation and future tests

Package IDs RSI-01…25 preserve source03 §34 order. Scope is still observation-only; no corrective task enables experiments.

| IDs | Review status |
|---|---|
| RSI-01 trace ID | PARTIAL: mission IDs/events exist, selected Deep/chat action bypass remains |
| RSI-02 reconstruct actions | FAIL: per-action ledger missing on mission route |
| RSI-03 failure evidence | PARTIAL: events/helpers; missing/unknown/cancelled paths and sink integrity incomplete |
| RSI-04 corrections | PARTIAL: event records, no completed revision/resume workflow |
| RSI-05 outcome vs report | FAIL: RP04 false completion, old ASK_USER UI mapping |
| RSI-06 repeated failure | helper tested; shipping Observer absent |
| RSI-07 no production mutation by Observer | interface intentionally authority-free; integration not demonstrated |
| RSI-08 supported proposal | helper refs exist; hash/ownership validation and runtime use incomplete |
| RSI-09…12 experiment isolation/reset/baseline/results | correctly DEFERRED P3 |
| RSI-13 budgets | experiments absent; P1 actual mission budget enforcement FAIL |
| RSI-14 multiple graders | correctly DEFERRED P3 |
| RSI-15 external state oracle | FAIL in P1, remains future evaluator gate P3 |
| RSI-16 unfamiliar tests | P1 held-out whole-path cases not established; broad transfer P3 |
| RSI-17…18 regression/protected graders | no promotion now; effective P1 oracles need repair, full candidate protection P3 |
| RSI-19 exact artifact | no promotion now; P1 evidence not bound to complete current source/bundle |
| RSI-20 lineage | trace helpers partial; candidate lineage P3 |
| RSI-21 rollback | store simulations only; complete installed-bundle rollback untested |
| RSI-22 canary | correctly DEFERRED P3 |
| RSI-23 recursive stage disabled | retained: no experiment/improver executor found; do not change |
| RSI-24…25 improver benchmark/downstream gains | correctly DEFERRED P3 |

RSI-FLOW-01…07: real Flow variants remain P2/P3; generic P1 signed-out/wrong account/wait/unknown UI recovery is incomplete. RSI-SAFE-01…04: privacy, authority, outcome/correction evidence and finite budgets require F02–07/F11 repairs. RSI-SAFE-05…09: full candidate/evaluator/archive/improver safeguards remain P3; corrective work must not implement them.

# Independent verification and limitations

The original planning package and previous two reviews remain the specification. The current completion HANDOFF/WORKLOG are claims checked against source, not replacements for requirements. No production files, tests or configuration were edited. All probe databases, synthetic effects and guard mutations used disposable copies; new evidence is under this review directory. Existing dependencies were reused offline. No agent work was used after the owner's instruction to continue alone; retained earlier broad-run logs were inspected by the primary reviewer, and the primary independently reproduced the decisive queue, lifecycle, budget, privacy and protocol findings.

## Broad gates

| Suite | Independent result | Evidence |
|---|---|---|
| Full Python unit | 610 collected, 606 pass, four socket PermissionError under sandbox | gates-unit/unit.xml and log |
| Affected socket files, isolated permitted rerun | 20 pass, no skips; covers all four environmental failures | root-socket.xml/log/result.json |
| Integration including production composition | 58 pass, no skips | gates-integration/integration.xml |
| Performance fixtures | 7 pass, no skips | gates-performance/performance.xml |
| Rust offline locked | 114 pass, two explicitly ignored | root-cargo.log/result.json |
| Renderer | build exit 0 | native-renderer.log/result.json |
| Mypy | 91 diagnostics in 12 files, unchanged multiset | gates-mypy.log, root-static-comparison.json |
| Ruff | 42 diagnostics, unchanged file/code multiset | gates-ruff.log, gates-ruff-json.log, root-static-comparison.json |

Do not sum the 20-case environmental rerun into the 610 unit total. It is a repair of review-environment coverage, not new tests. Initial native staging lacked a bundled config; the final root-owned copy included manifest files and cloned local build dependencies/resources. The first complete Rust run had one sandbox socket failure; the permitted rerun passed. Both ignored Rust cases concern a real throwaway daemon or live process-table discovery; they were not run. These successes are fixture/build results, not physical/installed acceptance.

Static comparison normalizes mypy line numbers while retaining path/message/code; Ruff compares per-file/code multiplicities. Zero introduced diagnostics does not mean zero diagnostics. Source was not lint-fixed.

## NP01–NP12 re-derivation

| Probe | Independent result and limit |
|---|---|
| NP01 composition | PASS narrow: real build_core_resources executor has store and payload resolver |
| NP02 uncertainty | PASS manually DISPATCHED case; FAIL actual ledger boundary, second attempt admitted D01 |
| NP03 aggregate effects | PASS mixed NO_EFFECT + CONFIRMED visits both IDs and disallows retry |
| NP04 transport | FAIL role-to-registered-submission name mismatch D04; storing a transport is insufficient |
| NP05 revision/privacy | PASS short secret-shaped revision refused without persistence; FAIL plan-derived sink D07; ordinary resume unscheduled D02 |
| NP06 checks | PARTIAL: catalogued checks exist for typical recipes; scroll/ordinal tautology and short search query gaps D06 |
| NP07 bootstrap | Inventory bootstrap and mutation refusal checked with real guard/store; targeted unknown-identity read permit demonstrates D03 |
| NP08 catalog | PASS second scroll step claims scroll catalog through actual store transaction |
| NP09 JEV | FAIL real authority ignores zero packet ceiling, one recorded provider-boundary call |
| NP10 accounting | PASS one outer planner invocation consumes one unit; deeper graph/provider budgets remain separate requirement |
| NP11 selected Deep | PASS synthetic mutation sink stays zero with real Deep graph/policy and scripted provider |
| NP12 gate freshness | PASS stale report plus child exit 7 returns FAIL |

Scripts: root-probes.py and root-additional-probes.py; outputs have matching JSON/log names. The NP06 navigate entry without a destination is an invalid-argument control, not evidence of a valid navigation failure. The actionable search case uses a valid short query, `AI`. Source inspection establishes weak scroll/ordinal postconditions and payload contamination. The ledger probe directly exercises the real action ledger and store, not a physical CUA effect; future repair acceptance must additionally prove the complete wrapper-to-device path.

The unknown-identity read probe intentionally uses a committed synthetic authority fixture; it proves missing scope proof at that boundary, not a real data disclosure. The model is scripted and effects are in-memory. Synthetic secret canaries are fabricated strings, not real credentials.

## Mutation checks

root-mutations.json records three independent mutations in a disposable copy: remove surface verification, remove the durable budget ceiling, and remove TTS text-length rejection. Each baseline passed; each mutant exited 1 with an assertion failure; original source restored after each. Full test logs identify the failing assertion. No mutation touched the reviewed working tree. These checks prove those particular tests are sensitive, not that all guards/workflows are correct.

## Remaining live boundary

L1/V1/P2/K1 remain BLOCKED. Here P2 is the original performance environment label, not permission to start product Phase 2. Executing them later requires an owner-issued approved-test-config naming exact disposable app/window/account scopes, bundle hash and source identity, allowed effects, zero paid-unit ceiling unless explicitly authorized otherwise, expiration, fixture data/artifact roots, cleanup and network policy. Voice adds explicit microphone/output/asset/audition authorization. None was supplied or fabricated. Building real harness code is still required; current unconditional harness skips must not be relabelled implemented acceptance.

No exact percentage is justified by these counts. Final source/history integrity is recorded in final-integrity.json; MANIFEST covers all new evidence and documents.

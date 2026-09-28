# Risks, decisions, unresolved facts and research

Baseline `58dac9c88018674c2e780086953902f1ea135308`; research/inspection date 2026-09-27. All performance values in this package are proposed targets unless backed by named logs. No live voice, model latency, account state, price, subscription, licence acceptance or deployed binary was inspected.

## Decisions made for this plan

| Decision | Evidence/rationale | Rejected alternative |
|---|---|---|
| MissionService in shipping sani-core above existing Velo | Core registry/runtime share Deep+Velo; shortest compatible integration; durable mission owner needed | Rewrite as server/orchestrator framework; revive FastAPI/PG as desktop source of truth |
| Existing Deep reused for mission plan/recovery/review | `build_agent` graph and memory already ship | Second competing full agent; Deep after every click |
| Velo local recipes + structured JEV preserved | parse/recipes/contracts/Choice pipeline exists and fixture tests exercise it | Freeform JEV, discard Velo, or force local commands through a model |
| New bounded executor has no nested Deep fallback | Existing unfamiliar fallback can create competing mission/action loops | Remove Deep entirely or allow recursive planning inside each unit |
| Add mission records to existing local SQLite with separate migration tracking | Shipping SQLite and unused local run ledger are reusable | Context window as source of truth, new database service, destructive migration |
| Per-run tokens + host ownership fence | Shared cancellation flag and TTL lease insufficient for concurrency | Global cancellation boolean or assume expired DB lease stops old process |
| Unknown outcome is a first-class blocker | Existing acknowledgement/verification mismatch and no-replay transport | Convert tool return/DONE into mission success; retry ambiguous external write |
| Role-bound tools plus deterministic permit | Prompt instructions alone cannot constrain delegated authority | Trust LLM/JEV claims of approval/account/budget |
| Separate local TTS worker/lock from STT/core | No actual TTS path; maintain working Moonshine env and IPC | Replace STT, cloud TTS fallback, put model load on Rust UI thread |
| Observation-only deterministic Observer | No production learning subsystem; source requires staged controls | Autonomous code/prompt edits or early experiment manager in P1 |
| Later phases re-planned from implementation | Owner04 overrides old all-phase-detail request | Detailed future schemas based on unbuilt assumptions |

No source removals are required by this plan. Documentation contradictions can be corrected, but any removal of allegedly dead runtime code needs call-site/test evidence and a separately reviewable justification.

## Risk register

| Risk | Severity / trigger | Mitigation and acceptance owner |
|---|---|---|
| Duplicate external effect after crash | Critical / submission ack lost | Durable intent, operation refs, UNKNOWN reconciliation, no blind retry; T02/T07, TC-10/23 |
| Wrong client/account/window | Critical / scope evidence stale or absent | Guard reads and writes, recheck after awaits, fresh target; T03/T05, TC-13/28 |
| Controller bypasses executor | Critical / raw tool retained in role | Tool binding and invocation check, no generated permission; T06, TC-08/27 |
| Local safety control cannot stop driver | Critical / stalled IPC, held key, ambiguous process | Native latch and owned generation, release/stop capability test; T05, TC-34; block release if unverified |
| Raw screenshot/log secrets | Critical / exception or image sinks | Before-sink sanitation/withhold, retention and sink canary tests; T03/T11, TC-32 |
| Misleading completed UI | High / agent.completed/DONE maps to success | Separate turn/mission status and trusted check gate; T04/T08, TC-20/35 |
| Queue starvation/cancel race | High / grant coincides cancellation or priority update | Cancellation-safe ownership; bounded priority without stealing active action; T05, TC-12 |
| Schema version collision/data loss | High / independent stores share SQLite | Separate migrations, transactional fixture upgrade/restore, no down-version; T02 |
| Multi-loop cost regression | High / plan/review after every recipe action | Explicit call budget/counters and compact exception path; T06/T12 |
| Output competes with STT/CUA | High / torch threads, buffers, device swap | Separate worker/process bounds, cancellation/backpressure and target measurements; T09/T10 |
| Asset/voice redistribution rights | High / runtime licence mistaken for voice/model rights | Separate notices/hash/terms/voice review and owner audition before packaging; T09 |
| Input takeover event ambiguity | High / automation mistaken for human or reverse | Driver capability inspection, minimal native monitor and synthetic discrimination tests; fail closed |
| Existing test debt/sandbox limits | Medium / broad suite not all green | Exact debt ledger and suitable fixture environment; never report blocked socket as pass |
| Source/bundle mismatch | High / old release manifest or generated core | Source+dirty diff+bundle hashes, isolated installed acceptance; T12 |
| Generic arbitrary website scope | High / URL/account cannot be established | Restrict P1 to trusted fixture/local flows or explicit evidence-backed scope; block unknown identity |
| Hidden model cost | High / callback not on core path, price unknown | Wire shipping metering; upper-bound reserve or call/token allowance; label unknown; T03/T11 |
| Recursive scope creep | High / Observer asks to activate or edit | No execution interface, budget zero, separate recommendation store; T11 |
| Stale later-phase plans | Medium / P1 changes interfaces | Fresh Astra plan after tested prior phase, deferred test ownership explicit |

## Facts still unknown and owner decisions

These do not block finishing this planning run. They are gates before the corresponding implementation/live acceptance, not requests for confirmation now.

| Unknown/decision | Safe planning assumption | How to resolve, when |
|---|---|---|
| Is local HEAD latest remote? | Plan only exact local SHA | Read remote metadata/fetch if authorized in implementation; do not overwrite work |
| Installed Sani matches source? | UNKNOWN; old manifest not proof | Compare packaged source/hash and installed diagnostics in isolated acceptance |
| Active API/provider/model/account/JEV flag | Do not change or assume active | Read non-secret runtime settings/diagnostics during implementation, no credential dumps |
| CPU model/physical RAM, older supported Macs | Arm64 macOS26.2 seen; resources UNKNOWN | Hardware query in permitted environment and actual worker benchmark |
| “FTD” / “moonshot” | Unresolved owner terminology; source uses Moonshine | Owner clarification if it changes preservation requirements; keep input pipeline meanwhile |
| Preferred final voice/accent/tone | Composed clear English; British male option to audition, not a mandated accent | Compare at most two lawful local candidates; owner chooses after hearing |
| Model download/gated terms | No authorization to accept terms or spend inferred | Owner-provided access/assets or approve acquisition before T09 live stage |
| macOS minimum12 packaging feasibility | Current host config is not proof model wheels run there | Test supported minimum or document/obtain acceptance for narrowed support |
| Reliable driver user-activity/key-release API | UNKNOWN until inspected live capability | Enumerate non-secret capabilities; local bridge only if necessary; block unsafe release |
| Live test account/profile/desktop/data directory | None selected | Owner authorizes exact isolated fixture plus allowed actions and maximum calls |
| Account/workspace identity adapter | Generic title is inadequate | Bind trusted observation/attestation; block where identity not provable |
| External spend/dollar pricing | Paid external action allowance=0 | Explicit per-test current-provider call allowance; do not fabricate pricing |
| Retention values | Proposed metadata30d/images7d, narrower if sensitive | Owner preference at implementation; never broaden by model instruction |
| Performance thresholds | Proposed gates in file06 | Measure target hardware, return evidence-backed tradeoff if unmet |
| Obsidian vault/company repos/workers | Not needed in P1 | Re-audit and select only in fresh P2 planning |
| RSI level beyond observation | Disabled | Separate explicit future activation after prerequisites and evidence |

## Local TTS shortlist and choice procedure

Primary-source web inspection was performed because this is a time-sensitive runtime/licence choice. Firecrawl CLI was unavailable, so built-in web browsing was used; nothing was installed or downloaded. Pin exact versions/hashes during implementation and recheck sources then. Publisher benchmarks describe publisher hardware, not this user's result.

| Dimension | Pocket TTS — first audition | Kokoro — second audition/fallback candidate |
|---|---|---|
| Published runtime | Python CPU-oriented streaming runtime; repository release page showed v3.3.0 dated 24 Sep 2026; main metadata Python ≥3.10,<3.15 | PyPI kokoro0.9.4, published 5 Apr 2025; Python ≥3.10,<3.13 |
| Model | 100M-class model; publisher model tree includes a 236MB safetensors file example, not full installed footprint | 82M model; repository tree lists 327MB `kokoro-v1_0.pth`, not full runtime/voices footprint |
| Dependencies | Torch≥2.5, numpy≥2 shown by current package metadata; package/model/voice asset versions must agree | Torch pipeline, Misaki/G2P and espeak-ng fallback; native dependency distribution must be checked |
| Streaming fit | Published streaming API; validate chunk startup/cancellation and installed revision | Generator-based audio segments; verify useful first segment latency and cancellation in worker |
| Runtime vs weights | Runtime permission notice is MIT-style; model card weights CC-BY-4.0 with gated access terms | Runtime package Apache licence, model card Apache-2.0; voice provenance still separate |
| Voice rights | Voice collection mixes CC-BY, CC0 and noncommercial datasets; pick explicitly permitted asset only | British male entries include `bm_george`, `bm_fable`; examine selected asset provenance/usage rights, no assumption of actor consent |
| Compatibility | Current Python3.12 falls within declared range; macOS wheels, minimum OS and target memory unverified | Current Python3.12 falls within range; native phonemizer/bundle compatibility unverified |
| Decision | First benchmark because publisher explicitly targets local CPU streaming; not selected/installed | Benchmark if first fails quality/packaging/resource gate, or owner prefers its audition |

Sources: [Pocket runtime and usage](https://github.com/kyutai-labs/pocket-tts), [Pocket releases](https://github.com/kyutai-labs/pocket-tts/releases), [runtime licence](https://raw.githubusercontent.com/kyutai-labs/pocket-tts/main/LICENSE), [Pocket model card/terms](https://huggingface.co/kyutai/pocket-tts), [model files](https://huggingface.co/kyutai/pocket-tts/tree/main), [voice asset licences](https://huggingface.co/kyutai/tts-voices). [Kokoro package metadata](https://pypi.org/project/kokoro/), [runtime source](https://github.com/hexgrad/kokoro), [model card](https://huggingface.co/hexgrad/Kokoro-82M), [model files](https://huggingface.co/hexgrad/Kokoro-82M/tree/main), [voice inventory](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).

Selection experiment: same local corpus/volume/device/rate, isolated env per candidate, no provider keys, five cold starts + thirty warm segments, capture first-audio/RTF/RSS/CPU and failures, cancellation and network denial tests. Audition intelligibility/accent/pacing with owner. Choose one supported/licensed engine meeting gates; retain failed candidate evidence and remove only its owned assets. Do not build a generalized engine platform or spend weeks selecting providers. No third candidate unless both fail and owner scope permits a fresh decision.

## Evaluation and RSI research context

Use actual environment outcomes alongside transcript/trace judgments. Anthropic's discussion of agent evals supports separating the recorded trajectory from the end result, but it does not certify Sani's planned tests: [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). Breadth, performance and autonomy are distinct evaluation dimensions; a local benchmark win is not a general-intelligence claim: [Levels of AGI](https://research.google/pubs/levels-of-agi-operationalizing-progress-on-the-path-to-agi/).

Document03's other research references are owner-supplied context, not independently verified implementation dependencies in this run. Fresh Phase 3 research must inspect them and current evidence before designing experiments. P1 deliberately records the provenance, outcomes, uncertainty, costs and corrections that a later protected evaluation system will need; it does not activate that system.

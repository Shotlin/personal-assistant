# Jarvis Phase 1 planning package

27 September 2026 · repository `Shotlin/personal-assistant` · branch `main` · inspected HEAD `58dac9c88018674c2e780086953902f1ea135308`.

**This package is a plan, not an implementation or a release acceptance.** All four owner documents were read completely. Their unchanged copies are in `inputs/`. The owner's current request and document 04 control the planning scope. Document 02's request for detailed later-phase implementation prompts is superseded: only Phase 1 is deeply planned. Older repository prohibitions on adding orchestration describe a previous release scope; they do not override this planning assignment. No product source, settings, dependency lock, account, or production data was changed. No commit, push, deployment, model/provider change, or RSI activation was performed.

## Read and execute

1. [Audit and all requirement mappings](01_CURRENT_REPOSITORY_AUDIT.md).
2. [Three-phase roadmap](02_GLOBAL_3_PHASE_ROADMAP.md).
3. [Phase 1 architecture and versioned contracts](03_PHASE_1_TARGET_ARCHITECTURE.md).
4. [Ordered implementation plan](04_PHASE_1_IMPLEMENTATION_PLAN.md).
5. [Self-contained implementation-agent prompt](05_PHASE_1_IMPLEMENTATION_AGENT_PROMPT.md).
6. [Tests and acceptance gates](06_PHASE_1_TEST_AND_ACCEPTANCE_PLAN.md).
7. [Handoff template](07_PHASE_1_HANDOFF_TEMPLATE.md).
8. [Phase 2 re-plan](08_PHASE_2_REPLAN_PROMPT.md) and [Phase 3 re-plan](09_PHASE_3_REPLAN_PROMPT.md).
9. [Risks, research and decisions](10_RISKS_AND_DECISIONS.md).

Give the next agent the ZIP and explicitly assign **Phase 1 implementation only**. File 05 embeds the architecture, tasks and test contract, so it can also travel alone. It names the original inputs and repository files for provenance. It conveys no permission for live account actions, spending, commits, pushes, deployments, or later phases. An implementation assignment authorizes ordinary local implementation; demonstrations involving desktop control/provider calls still need an explicit isolated target and allowance.

## What is established

The current source architecture is Sani's Tauri/React/Rust desktop host, Moonshine STT sidecar, framed Python sani-core IPC, one shared Deep Agent/Velo runtime, structured JEV, and CUA. The target adds durable mission ownership above Velo without deleting its fast route. Existing run-store, queue, usage and redaction modules are valuable but several are not connected to the shipping path.

Inspected local root: `/Users/sayan/Documents/personal-assistant`. Origin URL matches the target. Initial working tree was clean. Remote-tip freshness was not checked by fetching; this is a plan for the exact local HEAD, not a claim about the latest GitHub tip. Release manifest names an older revision; installed app binary parity is **UNKNOWN**.

## Evidence limits

Code inspection and isolated fixture tests are distinguished from live capability. No desktop input, account inspection, microphone capture, speech synthesis, paid API call, or production database access was performed. Device: macOS 26.2 (25C56), arm64; RAM/CPU model query was sandbox-denied. Live latency/cost/voice quality remain **NOT MEASURED**. “FTD” and the intended meaning of “moonshot” remain **UNKNOWN**; actual STT imports identify Moonshine, without proving those terms were synonyms.

Test logs, command context and repository inventory are in `evidence/`; all new Phase 1 tests are **PROPOSED / NOT RUN**. Existing check failures are recorded, not repaired. The ZIP contains planning Markdown, supplied source documents, and sanitized test evidence only; no application databases, credentials, binaries, media or model weights.

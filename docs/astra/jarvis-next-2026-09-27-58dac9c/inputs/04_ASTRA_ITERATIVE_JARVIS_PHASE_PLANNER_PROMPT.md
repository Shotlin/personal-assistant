# ASTRA MASTER PROMPT — ITERATIVE JARVIS REPOSITORY PLANNER

**Target repository:** `Shotlin/personal-assistant`  
**Planning mode:** repository-first, evidence-first, implementation-ready, sequential big phases  
**Primary specs:**  
1. `01_JARVIS_REQUIREMENTS.md`  
2. `02_ASTRA_REPOSITORY_ANALYSIS_AND_PHASE_PLANNING_PROMPT.md`  
3. `03_JARVIS_CONTROLLED_SELF_IMPROVEMENT_RSI.md`

---

# 0. WHY YOU ARE BEING USED

You are ASTRA acting as the principal repository investigator, software architect, phase planner, test architect, security reviewer, and implementation-handoff writer for my EXISTING Jarvis/Sani project.

You are **not** being asked to code the whole project in this run.

Your job is to:

1. read the three supplied MD specifications completely;
2. inspect the actual repository deeply;
3. understand what already works and what is legacy;
4. reconcile the target Jarvis requirements with the current architecture;
5. create a small number of BIG implementation phases;
6. create a complete implementation-ready plan and test contract for the NEXT phase only;
7. stop after producing the planning package so a separate implementation agent can execute it;
8. after that phase is implemented and verified, I will run ASTRA again on the new repository state to plan the next big phase.

This is intentionally an **iterative planner → implementer → verify → re-plan** workflow.

Do not freeze all low-level implementation decisions for later phases before the earlier phase changes the repository.

---

# 1. PRODUCT GOAL

I want to evolve the existing Sani/personal-assistant into my own Jarvis-style personal + company operating assistant.

The final experience should let me speak or type naturally, for example:

- “Bro, what is pending in my client projects?”
- “Is the last client app fully tested?”
- “Open the coding application and continue this project.”
- “Make an advertisement video through my existing Google Flow workflow.”
- “We have a new grocery project. Check our previous authorized projects, decide what can be reused, and prepare/delegate the work.”
- “The coding agent is stuck. Understand the blocker and continue if the answer is already known.”
- “Why did this workflow fail three times? Show me what Jarvis learned from it.”

The system must feel like ONE assistant even if internally it uses:

- Deep Agent reasoning;
- Jev;
- CUA;
- local desktop control;
- coding assistants;
- project/repository knowledge;
- local voice;
- memory;
- reusable skills/playbooks;
- Observer/evaluation systems.

The user gives the **goal**. Jarvis determines and supervises the execution.

---

# 2. FIXED OWNER CONSTRAINTS

These are binding unless the repository proves that a small compatibility adjustment is necessary.

## Preserve

Preserve and extend:

- the existing application;
- current Deep Agent integration;
- **Jev + CUA**;
- already selected APIs/providers;
- current working voice/chat path;
- current desktop shell;
- existing CUA safety and permission mechanisms;
- existing test infrastructure;
- existing memory/history where compatible.

## Do not waste this planning run on

Do NOT:

- recommend alternative API vendors merely because you prefer them;
- restart the application from zero;
- replace Jev with a generic chat LLM;
- replace graphical computer control with CLI-only execution;
- build a new desktop shell unnecessarily;
- claim “real AGI” because multiple agents or an RSI loop exist;
- enable unrestricted self-modification;
- silently weaken permissions/tests to make autonomy easier.

I already have the main services/accounts/API decisions.

Plan the missing orchestration, intelligence, reliability, voice output, project insight, supervision, learning and verification around the existing stack.

---

# 3. CURRENT REPOSITORY SNAPSHOT — VERIFY, DO NOT BLINDLY TRUST

The repository was externally inspected before this prompt was written.

Observed repository: `Shotlin/personal-assistant`  
Observed default branch: `main`  
Observed HEAD at that inspection: `58dac9c88018674c2e780086953902f1ea135308`

**You MUST independently verify the current HEAD before planning.**
If the repository has moved, use the new repository state. Do not force-reset it to the SHA above.

Important evidence observed in the current codebase includes:

## Desktop product

`README.md` and `CLAUDE.md` describe **Sani** as a locally installed Tauri desktop assistant.

Current shipping path is not the old Open WebUI/FastAPI/Postgres design.

Pay special attention to:

- `sani/`
- `sani/src-tauri/src/`
- `sani/src-tauri/python/`
- `src/assistant/core/`

The repository itself states that `src/assistant/api/` and `src/assistant/main.py` are legacy/non-shipping after the sani-core cutover. Verify this from current call paths before treating those files as implementation targets.

## Voice input

Current code contains local Moonshine streaming STT and VAD/turn-end behavior, including:

- `sani/src-tauri/src/speech.rs`
- `sani/src-tauri/python/sani_stt.py`

The current repo appears to use Moonshine for STT.

The owner previously used the terms “moonshot” and “FTD”. Resolve any remaining term from the actual code/config/history. Do not invent an expansion for “FTD”.

The target also requires **good local English TTS** with a polished, calm, Jarvis-inspired voice experience. First determine whether usable local TTS already exists. Do not assume it is missing or complete without tracing the output path.

## Current reasoning/execution structure

The current repository contains:

- `src/assistant/agent/` — Deep Agent assembly/reasoning/memory;
- `src/assistant/velo/` — Velo quick computer control;
- `src/assistant/velo/jev.py` — JEV structured decision service;
- `src/assistant/tools/` — CUA connection, policy and result normalization;
- `src/assistant/runtime/` — run state, sessions, desktop queue and persistence;
- `src/assistant/core/agents.py` — registry/wiring of Deep and Velo;
- `src/assistant/skills/` — existing procedures including software delegation.

Current code comments/documentation indicate:

- Velo presently owns local/structured routing and execution;
- JEV is a structured classifier/decision component, **not** a free-form chat model;
- CUA performs computer actions;
- unfamiliar objectives can fall back to the Deep Agent;
- existing Velo execution already has bounded action/runtime/retry/cancellation concepts;
- outcome verification exists;
- desktop access serialization exists;
- SQLite-backed run/action state exists;
- software delegation already has an existing skill.

Treat these as starting assets to investigate, not as finished target behavior.

---

# 4. THE MOST IMPORTANT ARCHITECTURAL RECONCILIATION

The target product requirements and the current repository use different ownership language.

## Target intent

At the mature Jarvis level:

**Deep Agent / mission intelligence**
- understands the whole user goal;
- gathers project/context information;
- decides the overall strategy;
- creates/revises the mission;
- handles ambiguous or difficult decisions;
- supervises coding/creative workflows;
- receives important exceptions;
- verifies final mission completion.

**Jev + CUA execution side**
- handles fast bounded computer execution;
- receives only the current semantic subtask/small tested chunk;
- performs local observe → decide → act → verify behavior;
- does NOT need the entire mission backlog;
- does NOT wake the expensive mission brain after every click;
- escalates only meaningful exceptions/uncertainty.

## Current code

Current code appears to make Velo the owner of intent/task execution at a lower level and Deep Agent the general reasoning executor.

Do NOT solve this by deleting Velo or rewriting everything.

Investigate whether the clean migration is:

```text
User voice/text
    ↓
Mission-level Jarvis Controller / Deep reasoning layer
    ↓
Durable Mission Manager + task graph
    ↓
Bounded semantic work item
    ↓
Existing/refactored Velo
    ↓
JEV decision where useful + deterministic recipes/policy
    ↓
CUA
    ↓
Independent postcondition/evidence
    ↓
Mission Manager
    ↓
Deep Controller only on meaningful exception/decision/final review
```

This is a hypothesis, not permission to invent code.

Prove or revise it from the repository.

The final design must preserve the strong parts of the existing Velo/JEV/CUA loop while adding the higher-level Jarvis mission orchestration the owner wants.

---

# 5. CORE EXECUTION PRINCIPLE

Do NOT design this:

```text
Deep Agent → click
Deep Agent → screenshot
Deep Agent → click
Deep Agent → screenshot
...
```

And do NOT design this:

```text
Deep Agent → gives the entire long mission + all client history to Jev → hopes it finishes
```

Design toward:

```text
User request
  ↓
Deep Agent / mission planner
  ↓
durable full plan outside model context
  ↓
dispatcher chooses next bounded semantic work item
  ↓
Jev + CUA execution loop
  ↓
local verification
  ↓
checkpoint
  ↓
next bounded work item

ONLY IF:
- ambiguity,
- unexpected UI,
- unhandled authentication/account state,
- conflicting evidence,
- coding-agent question requiring reasoning,
- safety/permission boundary,
- repeated failure/no progress,
- plan needs revision,
- final acceptance review

THEN:
Jev/Velo → compact evidence packet → Deep Agent
```

The application must persist the mission and progress. The model context is not the database.

---

# 6. FINAL CAPABILITY TARGET

Across the full roadmap, Jarvis should gain the following verified capabilities.

## A. Natural Jarvis interaction

- voice and typing enter the same mission system;
- conversational references such as “that client”, “last project”, “continue it” work from authorized context;
- corrections, pause, resume and cancellation work;
- concise spoken output accompanies richer written detail where useful.

## B. Local voice output

- preserve existing local STT;
- add/complete local English TTS;
- low perceived latency;
- barge-in/interruption;
- stale speech cancellation;
- no self-listening feedback loop;
- correct pronunciation of technical terms, Indian names, dates and INR values;
- no hidden hosted-TTS fallback when local-only mode is required.

## C. Durable missions

A mission needs:

- goal;
- scope;
- account/project;
- success criteria;
- plan version;
- semantic steps;
- dependencies;
- status;
- retries;
- budget;
- approvals;
- evidence;
- artifacts;
- resume cursor;
- final verification.

It must survive restart/recovery where technically possible.

## D. Bounded Jev + CUA execution

Each execution unit contains only required context:

- objective;
- expected app/account/workspace;
- preconditions;
- allowed scope;
- expected result;
- verification;
- action/time/retry limit;
- side-effect class;
- escalation rules.

## E. Computer control that behaves like a reliable operator

- semantic/AX interaction first where possible;
- correct window/account validation;
- no blind clicks;
- one interactive desktop owner;
- human takeover;
- emergency stop;
- side-effect reconciliation after uncertain timeouts;
- no duplicate external action after restart/retry.

## F. Coding-agent supervision

Jarvis should be able to:

1. identify the correct client/project/workspace;
2. prepare a high-quality engineering prompt;
3. open/use the existing Claude/Codex-style coding application through the requested GUI workflow;
4. submit the prompt;
5. watch progress;
6. detect waiting/questions/errors/rate limits;
7. answer questions automatically when existing requirements/evidence already determine the answer;
8. escalate true product/business/privilege decisions;
9. verify code/build/test evidence rather than blindly trusting “done”.

Do not reduce this to “run a CLI command”.

## G. Company/project intelligence

Jarvis should be able to reason over:

```text
Client
  → Project
  → Requirements
  → Repositories
  → Modules
  → Milestones
  → Bugs
  → Tests
  → Builds
  → Releases
  → Deployment evidence
  → Current blockers
```

When evidence is missing, say it is missing.

Do not treat an old chat message saying “finished” as current release truth.

## H. Existing-code reuse

For new software work:

- inspect owner-authorized repositories;
- identify compatible reusable modules;
- distinguish REUSE / ADAPT / NEW BUILD;
- never mix client secrets/branding/data;
- preserve read-only reference repositories unless explicitly authorized;
- delegate implementation through the chosen coding workflow.

## I. Obsidian knowledge layer

Use Obsidian/Markdown for:

- project briefs;
- decisions;
- architecture knowledge;
- approved playbooks;
- research;
- reusable lessons;
- human-readable project knowledge.

Do NOT make Obsidian the source of truth for:

- live task state;
- secret storage;
- authorization;
- transaction status;
- spend/payment truth;
- the desktop lease.

## J. Controlled RSI / AGI-oriented improvement

Follow `03_JARVIS_CONTROLLED_SELF_IMPROVEMENT_RSI.md`.

The production system and improvement system must be separated.

At minimum the mature architecture should distinguish:

- Controller;
- Jev + CUA executor;
- Observer;
- Experiment Manager;
- Evaluator;
- skill/playbook registry;
- candidate/version lineage;
- promotion gate;
- rollback.

Start observation-only.

Do not jump directly to self-editing production.

The important rule is:

> Jarvis may propose how to become better. It must prove that a change helps before that change becomes trusted behavior.

---

# 7. YOUR PLANNING STRATEGY — BIG PHASES, SEQUENTIAL DETAIL

I do NOT want 20 tiny top-level phases.

Create a global roadmap containing **three major outcome phases** unless repository evidence strongly justifies two.

Use approximately these boundaries, but reconcile them with the actual code.

---

## PHASE 1 — JARVIS FOUNDATION: MISSION BRAIN + RELIABLE EXECUTION + LOCAL VOICE OUTPUT

This phase should make the current assistant structurally capable of running reliable Jarvis missions.

Likely responsibilities include:

- repository regression baseline;
- current voice/text flow preservation;
- local TTS;
- unified mission entry for voice/text;
- mission schema/state machine;
- durable plan/checkpoint state;
- mission-level Controller behavior;
- bounded dispatch into existing Velo/JEV/CUA;
- meaningful exception return to Deep Agent;
- side-effect classification/reconciliation;
- account/workspace scope;
- desktop lease/human takeover/emergency stop;
- instrumentation for cost/latency/actions/escalations;
- evidence references;
- final verification;
- read-only Observer event/trace foundation;
- required migrations/adapters from current Velo ownership without breaking fast local commands.

The phase must preserve the current fast path.

Do not make every simple “open Chrome” request go through expensive planning if the existing deterministic/JEV route is already sufficient.

**Phase 1 exit should demonstrate a real multi-step mission**, not only unit tests.

Example class of demonstration:

```text
voice/text goal
→ mission created
→ multiple bounded Jev/CUA units
→ routine UI transitions handled locally
→ one unexpected condition produces a compact escalation
→ mission resumes from checkpoint
→ verified final result
→ restart/cancel path tested
→ no expensive brain call per click
```

---

## PHASE 2 — PERSONAL + COMPANY OPERATING ASSISTANT

Only plan this deeply after Phase 1 has actually been implemented.

Global boundary should include:

- client/project source model;
- current-evidence reporting;
- Obsidian integration;
- safe project/repository knowledge retrieval;
- module reuse analysis;
- graphical coding-agent delegation;
- coding-agent monitoring and follow-up;
- project/update reporting;
- one representative creative workflow such as Google Flow;
- cross-tool mission continuation;
- stronger reusable playbooks.

Representative exit demonstrations should include:

1. all-client/current-project report with evidence freshness;
2. coding-app delegation that survives a worker question/error and verifies the result;
3. media/Google Flow mission that handles account/login interruption safely and verifies the exported artifact.

---

## PHASE 3 — CONTROLLED LEARNING / RSI / AGI-ORIENTED IMPROVEMENT

Only plan this deeply after Phase 2 has actually been implemented.

Global boundary should include:

- production trace ingestion;
- Observer;
- structured success/failure/correction events;
- recurring-pattern detection;
- candidate improvement generation;
- sandbox/replay;
- baseline vs candidate evaluation;
- independent outcome checks;
- variation/unfamiliar/regression suites;
- anti-reward-hacking controls;
- version lineage;
- owner promotion gate;
- canary;
- rollback;
- improvement metrics.

Initial production activation remains conservative.

“Improve the improver” is a separately gated advanced experiment. It does not become ordinary production behavior merely because Phase 3 exists.

---

# 8. CRITICAL ITERATIVE RULE

For THIS ASTRA RUN:

1. fully audit the repository;
2. create the GLOBAL three-phase roadmap;
3. deeply plan **Phase 1 only**;
4. create Phase 1 test plan;
5. create Phase 1 implementation-agent prompt;
6. create Phase 1 handoff/verification contract;
7. STOP.

Do NOT create detailed file-by-file Phase 2 and Phase 3 implementation prompts now.

For Phase 2 and Phase 3, only define:

- desired outcome;
- dependencies;
- high-level scope;
- expected exit demonstrations;
- requirements assigned to the phase;
- known risks;
- what must be re-audited before planning it.

Why:

The repository after Phase 1 will not be identical to the repository you are seeing now.

The correct workflow is:

```text
ASTRA PLAN 1
  ↓
Implementation Agent executes Phase 1
  ↓
Independent tests/review
  ↓
Phase 1 handoff saved
  ↓
ASTRA reopens latest repository
  ↓
audits actual Phase 1 result
  ↓
ASTRA PLAN 2
  ↓
Implementation Agent executes Phase 2
  ↓
tests/review
  ↓
ASTRA PLAN 3
```

This prevents stale architecture plans.

---

# 9. REPOSITORY INVESTIGATION — MANDATORY BEFORE DESIGN

Inspect the current repository deeply before producing the Phase 1 plan.

At minimum trace these paths.

## Current product path

- `README.md`
- `CLAUDE.md`
- current relevant files under `docs/`
- `sani/package.json`
- `sani/src-tauri/Cargo.toml`
- `sani/src-tauri/src/main.rs`
- `sani/src-tauri/src/app_state.rs`
- `sani/src-tauri/src/runtime.rs`
- `sani/src-tauri/src/sani_core.rs`
- `sani/src-tauri/src/speech.rs`
- `sani/src-tauri/src/audio.rs`
- `sani/src-tauri/src/history.rs`
- `sani/src-tauri/src/settings.rs`
- `sani/src-tauri/python/sani_stt.py`

## Core/agents

- `src/assistant/core/`
- `src/assistant/agent/`
- `src/assistant/settings.py`

## Velo / Jev / CUA

- `src/assistant/velo/controller.py`
- `src/assistant/velo/jev.py`
- `src/assistant/velo/contracts.py`
- `src/assistant/velo/adapter.py`
- `src/assistant/velo/recipes.py`
- `src/assistant/velo/verify.py`
- `src/assistant/tools/cua.py`
- `src/assistant/tools/policy.py`
- `config/cua-capabilities.yaml`

## Mission/run state

- `src/assistant/runtime/runs_local.py`
- `src/assistant/runtime/session.py`
- `src/assistant/runtime/desktop_queue.py`
- relevant core runtime files.

## Memory/knowledge

- `src/assistant/memory/`
- `src/assistant/skills/`

Especially inspect:

- `src/assistant/skills/computer-use/SKILL.md`
- `src/assistant/skills/software-delegation/SKILL.md`
- `src/assistant/skills/general-assistant/SKILL.md`

## Tests

Inventory:

- unit tests;
- integration tests;
- e2e tests;
- Rust tests;
- voice probes;
- CUA verification scripts.

Do not plan duplicate tests if an existing one can be extended.

---

# 10. LEGACY DOCUMENTS ARE NOT AUTOMATIC TRUTH

The repository contains older architecture/planning documents.

For example, `PROJECT_GRAPH.md` itself identifies legacy pre-Sani architecture.

Old plans may contain useful history, but current code + current README/CLAUDE + actual runtime wiring outrank legacy descriptions.

Classify important evidence:

- `VERIFIED BY CODE + TEST/RUN`
- `CODE PRESENT / NOT EXECUTED`
- `PARTIAL`
- `LEGACY`
- `MISSING`
- `UNKNOWN`
- `PROPOSED`

Do not call a feature complete because a Markdown file says so.

---

# 11. PHASE 1 GAP MATRIX

Map the target requirements to the current implementation.

For each relevant requirement from `01_JARVIS_REQUIREMENTS.md` and the third RSI document, produce:

| Requirement | Current component/file | Current status | Gap | Proposed Phase | Test IDs | Risk |
|---|---|---|---|---|---|---|

At minimum address all `JAR-001` through `JAR-024`.

Carry `TC-01` through `TC-36`.

Also extract and assign all RSI-specific acceptance cases from the third document.

Do not silently drop requirements because the current repo already has something similarly named.

---

# 12. ARCHITECTURE QUESTIONS ASTRA MUST RESOLVE

Your Phase 1 design must explicitly answer the following from actual code.

## Mission ownership

Where should the mission-level state machine live?

How will the current Velo lower-level task logic interact with it?

How does the Deep Agent become/serve the mission-level reasoning brain without destroying fast local execution?

## Bounded dispatch

What exact new or adapted contract carries:

- mission ID;
- plan version;
- semantic step;
- minimal context;
- app/account scope;
- expected postcondition;
- retry/action budget;
- side-effect class;
- evidence requirements;
- escalation conditions?

## Persistence

Which current SQLite/run-ledger pieces can be extended?

What new tables/types are actually required?

What existing structures should be reused instead?

## Recovery

How are these handled:

- stale/duplicate results;
- app restart;
- desktop driver restart;
- lost acknowledgement;
- external action may already have happened;
- no-progress loop;
- wrong account/workspace;
- user interruption;
- plan revision?

## Verification

How does the system distinguish:

- tool returned;
- click happened;
- step postcondition passed;
- mission success;
- user approval?

## Local TTS

Trace existing output.

If missing/incomplete, plan local TTS integration into the current Tauri/audio architecture.

Include:

- streaming/chunked output if feasible;
- queue;
- cancellation;
- barge-in;
- playback state;
- self-listening prevention;
- cold/warm load;
- CPU/RAM contention with STT/CUA;
- packaging;
- voice asset/license review;
- text fallback.

Do not choose an engine merely because a spec mentions it. Benchmark on actual target hardware during implementation.

## Observer foundation

Phase 1 should create the evidence needed by future RSI.

Do not build autonomous self-improvement yet.

Define immutable/structured trace events sufficient for later:

- success;
- failure;
- expected vs observed state;
- human correction;
- recovery;
- cost;
- latency;
- component versions;
- skill version;
- evidence references.

The Observer in Phase 1 should be read-only.

---

# 13. TESTING STANDARD

The implementation agent must not be able to say “done” simply because code compiles.

Build the Phase 1 plan around tests from the start.

Reuse current commands where applicable and verify them from repository configuration, likely including:

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src tests

cd sani
npm run build

cd sani/src-tauri
cargo test
```

Do not assume these are sufficient.

Add only the tests required for the new Phase 1 behavior.

Required test classes include:

## Unit

- mission state transitions;
- packet/schema validation;
- stale plan-version rejection;
- duplicate result rejection;
- retry budgets;
- side-effect classification;
- approval boundaries;
- observer write-denial;
- TTS queue/cancel state.

## Integration

- mission manager ↔ Deep Controller;
- mission manager ↔ Velo/JEV/CUA dispatch;
- checkpoint/resume;
- desktop queue/lease;
- account mismatch;
- coding-worker state only if Phase 1 dependency requires it;
- TTS/STT coordination.

## Desktop/CUA E2E

At least one real or explicitly authorized isolated GUI mission.

Test:

- semantic execution;
- verification;
- unexpected UI;
- recovery;
- user takeover;
- emergency stop;
- wrong focus;
- duplicate prevention.

## Restart/recovery

Simulate termination after:

- a safe read action;
- a potentially side-effectful submission before acknowledgement;
- a completed step before mission checkpoint acknowledgement.

Verify no blind duplication.

## Voice

- partial transcript does not launch duplicate mission;
- final voice turn and equivalent typed turn map to same intent;
- local TTS works without hosted TTS;
- barge-in;
- stale speech cancellation;
- no microphone feedback/self-listening;
- audio-device failure fallback;
- contention measurement.

## Security/adversarial

- prompt injection in observed screen;
- repository note requesting extra authority;
- wrong account;
- secret-like screenshot/log content;
- candidate observer suggestion attempting to change production;
- permission denial.

## Performance/cost

Capture baseline and candidate:

- Deep Agent calls per mission;
- JEV calls;
- CUA actions;
- observations/screenshots;
- retries;
- human interventions;
- first useful action latency;
- total mission time;
- known token/cost proxies;
- false completion.

A faster wrong result is not improvement.

---

# 14. NO TEST THEATER

For every test state:

- `PASS`
- `FAIL`
- `BLOCKED`
- `NOT RUN`

Never mark an unrun test “passed”.

A mocked GUI test does not prove the live desktop path.

A coding agent saying “tests passed” is not test evidence unless the result/artifact is independently available.

A button click is not equivalent to the real external action succeeding.

---

# 15. REQUIRED OUTPUTS FROM THIS ASTRA RUN

Create a downloadable planning directory and ZIP if your environment supports it.

Suggested new planning folder:

`docs/astra/jarvis-next/`

If that location already exists, use a duplicate-safe dated/revision-scoped location.

Do not overwrite existing product files.

Produce:

### `00_READ_ME_FIRST.md`

Include:

- repository;
- actual branch;
- actual HEAD;
- date;
- what you inspected;
- evidence limitations;
- required reading order;
- explicit statement: this package is a plan, not an implementation.

### `01_CURRENT_REPOSITORY_AUDIT.md`

Include:

- shipping architecture;
- legacy architecture;
- current voice flow;
- Deep Agent flow;
- Velo/JEV flow;
- CUA flow;
- run persistence;
- tests;
- reusable components;
- missing/partial behavior;
- contradictions between existing repo and target specification.

### `02_GLOBAL_3_PHASE_ROADMAP.md`

Define:

- Phase 1 outcome;
- Phase 2 outcome;
- Phase 3 outcome;
- dependency graph;
- requirement ownership;
- what the user gets after each phase;
- major risks;
- why this decomposition is appropriate.

Keep Phase 2/3 implementation detail intentionally high-level.

### `03_PHASE_1_TARGET_ARCHITECTURE.md`

Detailed architecture for Phase 1 only:

- component changes;
- sequence diagrams;
- mission lifecycle;
- Deep Controller/Velo/JEV/CUA relationship;
- schemas;
- state transitions;
- persistence;
- failure/recovery;
- TTS integration;
- Observer event foundation;
- trust boundaries;
- compatibility/rollback.

### `04_PHASE_1_IMPLEMENTATION_PLAN.md`

Implementation order.

Every task must specify:

- objective;
- exact existing files to inspect;
- files to modify;
- proposed new files marked `NEW`;
- interfaces;
- dependency;
- test-first step;
- acceptance criteria;
- rollback;
- stop condition.

Avoid vague tasks like “implement mission engine”.

### `05_PHASE_1_IMPLEMENTATION_AGENT_PROMPT.md`

This is the file I will hand to another coding agent.

It must be completely self-contained.

It must say:

- exact repo/revision baseline;
- what to read;
- what to preserve;
- allowed change surface;
- ordered implementation tasks;
- tests;
- verification;
- restrictions;
- required handoff;
- no automatic Phase 2 work;
- no commit/push/deploy unless separately authorized.

### `06_PHASE_1_TEST_AND_ACCEPTANCE_PLAN.md`

Map:

- all Phase 1 JAR requirements;
- relevant `TC-*`;
- relevant RSI foundation tests;
- existing tests to preserve;
- proposed new tests;
- exact command;
- fixture/environment;
- assertion;
- evidence output;
- live-test approval requirement;
- cleanup.

### `07_PHASE_1_HANDOFF_TEMPLATE.md`

Implementation agent must return:

- baseline SHA;
- final working-tree/commit SHA if applicable;
- changed files;
- new schemas;
- migrations;
- feature flags;
- test commands + exact results;
- live tests;
- blocked tests;
- artifacts;
- measured baseline/candidate metrics;
- known regressions;
- rollback;
- next safe action.

### `08_PHASE_2_REPLAN_PROMPT.md`

Do NOT plan Phase 2 in detail.

Create the short prompt I can give ASTRA after Phase 1.

It must instruct Astra to:

- inspect current HEAD;
- read the Phase 1 handoff;
- compare Phase 1 implementation against its acceptance plan;
- resolve drift;
- re-audit dependencies;
- then create a detailed Phase 2 architecture/plan/tests/implementation prompt.

### `09_PHASE_3_REPLAN_PROMPT.md`

Same idea for Phase 3 after Phase 2.

Must require fresh inspection before designing RSI implementation.

### `10_RISKS_AND_DECISIONS.md`

Include:

- unresolved facts;
- decisions required from owner;
- technical risks;
- security risks;
- performance risks;
- migration risks;
- assumptions;
- rejected alternatives and why.

---

# 16. PHASE 1 IMPLEMENTATION PROMPT QUALITY BAR

The file `05_PHASE_1_IMPLEMENTATION_AGENT_PROMPT.md` is one of the most important outputs.

A new coding agent must be able to execute Phase 1 without reading this entire conversation.

It needs:

## Baseline

- repository;
- branch;
- inspected SHA;
- dirty-state rule;
- do not force reset;
- preserve unrelated work.

## Required reading

- three owner MD files;
- Astra audit;
- Phase 1 architecture;
- existing repository instructions;
- exact source modules.

## Fixed product rules

Repeat the non-negotiable requirements.

## Exact scope

State clearly what Phase 1 DOES and DOES NOT implement.

## File map

For every relevant file:

```text
PATH
Current responsibility
Planned responsibility
Action: preserve / modify / new / remove only if proven dead
Reason
```

## Interfaces

Provide concrete typed contracts.

Do not require the implementation agent to invent mission/result/status schemas.

## Task order

Each task should be independently reviewable.

Prefer:

```text
write failing test
→ implement smallest compatible change
→ run focused test
→ run regression
→ record evidence
```

## Stop conditions

Implementation agent must stop rather than improvise when:

- baseline revision is materially incompatible;
- owner files contradict current explicit instruction;
- change requires new API/provider selection;
- destructive migration becomes necessary unexpectedly;
- production account/action is required;
- permission boundary needs weakening;
- test evaluator would need modification simply to pass;
- required evidence is inaccessible.

---

# 17. ARCHITECTURAL RULES ASTRA MUST NOT BREAK

## Rule 1 — JEV stays structured

The current repo explicitly treats JEV as a structured decision engine.

Do not redesign JEV into a general conversational brain just to satisfy the word “agent”.

## Rule 2 — Do not throw away Velo

The existing Velo code has:

- bounded execution;
- recipes;
- parsing;
- verification;
- no-progress concepts;
- JEV integration;
- CUA adapter.

Reuse/refactor these before proposing a replacement.

## Rule 3 — mission-level and action-level loops are different

A mission controller may own a 20-minute goal while Velo owns a 10-second semantic desktop unit.

Do not merge those responsibilities blindly.

## Rule 4 — durable state outside context

Chat history is not sufficient mission state.

## Rule 5 — verify external reality

Agent prose is not final evidence.

## Rule 6 — preserve fast path

Known, low-risk, local commands should stay fast.

Do not route every tiny action through the Deep Agent.

## Rule 7 — no unlimited retries

All loops have:

- retry cap;
- action cap;
- time cap;
- no-progress detector;
- cancellation;
- escalation.

## Rule 8 — side effects need reconciliation

After uncertainty, inspect actual state before retry.

## Rule 9 — security controls are deterministic

Models do not grant themselves permission.

## Rule 10 — observer cannot silently mutate production

Read-only first.

---

# 18. RSI / AGI-ORIENTED REQUIREMENTS FOR FUTURE PHASE 3

Carry these into the global architecture now so Phase 1 instrumentation is compatible, but DO NOT implement the full system during Phase 1.

The eventual improvement loop is:

```text
production evidence
    ↓
Observer
    ↓
pattern detection
    ↓
hypothesis
    ↓
candidate improvement
    ↓
isolated experiment
    ↓
independent evaluator
    ↓
baseline comparison
    ↓
regression + variation + unfamiliar tests
    ↓
owner approval / canary
    ↓
versioned promotion
    ↓
production measurement
    ↓
rollback if worse
```

Protected from ordinary self-modification:

- approval logic;
- secret handling;
- destructive-action policy;
- spending gates;
- evaluator integrity;
- audit integrity;
- holdout tasks;
- experiment ceilings;
- emergency stop;
- promotion authority;
- rollback mechanism.

“Improve the improver” is later.

Its success condition is not “it rewrote itself”.

The required question is:

> Under matched budgets and unseen improvement problems, does the new improver repeatedly produce better independently accepted downstream improvements than the old improver?

---

# 19. WHAT SUCCESS LOOKS LIKE AFTER ALL THREE PHASES

Do not describe success as “we created AGI”.

Describe demonstrated capability.

The mature system should work approximately like:

```text
OWNER
  ↓ voice / typing
JARVIS MISSION BRAIN
  ↓
current project knowledge + goal + permissions
  ↓
durable mission plan
  ↓
bounded execution dispatcher
  ↓
Velo / Jev + CUA / coding agents / tools
  ↓
local verification + checkpoints
  ↓
exception only when meaningful
  ↓
mission-level reasoning/recovery
  ↓
verified result
  ↓
owner report
```

Meanwhile:

```text
traces + corrections + outcomes
  ↓
read-only Observer
  ↓
improvement opportunities
  ↓
approved sandbox experiments
  ↓
independent evaluation
  ↓
versioned better workflows
```

The result should be a Jarvis that:

- handles broader work;
- fails more safely;
- repeats fewer mistakes;
- needs less unnecessary babysitting;
- uses fewer unnecessary expensive reasoning calls;
- knows current client/project state from evidence;
- delegates coding work intelligently;
- controls the computer reliably;
- speaks naturally locally;
- develops a tested library of better workflows;
- can demonstrate whether it is actually improving.

---

# 20. FINAL INSTRUCTION FOR THIS RUN

Start now.

1. Read all three supplied specification files completely.
2. Read repository-level instructions.
3. Verify current repository HEAD and worktree state.
4. Trace the shipping runtime.
5. Inspect the relevant tests.
6. Produce an evidence-backed gap analysis.
7. Resolve the mission-level Deep Agent versus current Velo ownership question.
8. Produce the three-phase global roadmap.
9. Produce the **full detailed Phase 1 architecture, implementation plan, implementation-agent prompt and test plan**.
10. Produce Phase 2 and Phase 3 **re-plan prompts only**, not stale detailed implementation plans.
11. Produce downloadable Markdown artifacts and a ZIP if possible.
12. Do not implement Phase 1 in this ASTRA run.
13. Do not commit, push, deploy, install system dependencies, change live accounts, or run paid production actions.
14. End with a concise explanation of:
    - what already exists;
    - what Phase 1 changes;
    - what I will have after Phase 1;
    - how to hand `05_PHASE_1_IMPLEMENTATION_AGENT_PROMPT.md` to the next agent.

Do not ask me to repeat requirements already present in the three MD files.

If a fact is missing, mark it `UNKNOWN` or `BLOCKED` and continue with the evidence-supported planning work.

# END

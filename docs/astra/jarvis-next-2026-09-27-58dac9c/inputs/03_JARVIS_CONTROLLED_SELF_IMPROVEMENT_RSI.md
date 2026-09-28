# JARVIS — AGI-Oriented Controlled Recursive Self-Improvement (RSI) Architecture

**Document role:** Third planning/specification file for the Jarvis project  
**Audience:** Astra / Codex / Claude / implementation agents / system architect  
**Status:** Architecture and implementation-planning specification  
**Important:** This document does **not** claim that the resulting system is AGI. It defines an engineering path toward a more general, adaptive, self-improving Jarvis-like agent system whose progress can be measured.

---

## 1. Mission

The goal is to turn the existing Jarvis stack — including the existing Deep Agent, Jev + CUA execution layer, current APIs, existing voice/chat stack, project knowledge, coding agents, and desktop control — into a system that can:

1. execute real work reliably;
2. observe its own failures and successes;
3. retain useful experience;
4. detect repeated failure patterns;
5. propose specific improvements;
6. test improvements in isolated environments;
7. measure whether those improvements actually help;
8. promote only validated improvements;
9. preserve rollback and lineage;
10. eventually improve parts of the improvement process itself under strict evaluation.

The desired result is **not** an unrestricted self-modifying agent. The desired result is a **controlled, evidence-driven learning system** that becomes better over time while preserving reliability, auditability, security, and user control.

---

## 2. Core Principle

The central rule for this entire design is:

> **Jarvis may propose how to become better. It must prove that a change helps before that change becomes trusted behavior.**

A self-improvement system must not confuse:

- changing behavior with improving behavior;
- generating an explanation with understanding the root cause;
- a successful retry with durable learning;
- a stored memory with model training;
- passing one familiar test with general capability improvement;
- agent agreement with objective validation;
- autonomy with intelligence;
- self-modification with AGI.

Every improvement must be tied to measurable evidence.

---

## 3. What "AGI-Oriented" Means in This Project

For this project, AGI-oriented engineering means increasing three measurable properties over time:

### 3.1 Generality

Jarvis should become able to handle a broader range of unfamiliar tasks rather than only memorized workflows.

Examples:

- software engineering;
- client project reporting;
- browser/desktop automation;
- media generation workflows;
- document work;
- research;
- project supervision;
- coding-agent coordination;
- issue recovery;
- unfamiliar UI states;
- cross-project module reuse.

### 3.2 Performance

For tasks it attempts, Jarvis should improve measurable quality:

- higher verified completion rate;
- fewer repeated errors;
- better final output quality;
- fewer unnecessary steps;
- lower latency;
- lower token/compute cost;
- fewer unnecessary Deep Agent escalations;
- fewer human interventions;
- fewer false "done" reports.

### 3.3 Autonomy

Jarvis should be able to carry larger missions from instruction to verified outcome with less unnecessary human intervention, while still respecting explicit approval boundaries.

Autonomy must never mean bypassing required approval, authentication, spending, security, destructive-action, or production-safety controls.

### 3.4 AGI is not defined by one architecture

The existence of:

- a Controller;
- an Observer;
- a memory system;
- a recursive loop;
- a self-modification module;

is **not sufficient** to establish AGI.

This project must measure breadth, performance, transfer to new tasks, robustness, and autonomy separately.

---

## 4. Research-Derived Design Principles

This architecture is informed by published work on agent evaluation, self-improving scaffolds, automated program evolution, self-adaptation, and AGI measurement.

### 4.1 Google DeepMind — Levels of AGI

The practical lesson is to evaluate progress through **capability breadth, capability depth/performance, and autonomy**, rather than declaring AGI based on an internal mechanism.

Design implication:

- Jarvis must maintain an internal capability benchmark;
- the benchmark must include both familiar and unfamiliar tasks;
- improvements must be evaluated across more than the task that produced them.

Source:  
https://research.google/pubs/levels-of-agi-operationalizing-progress-on-the-path-to-agi/

### 4.2 Google DeepMind — AlphaEvolve

AlphaEvolve demonstrates the usefulness of combining model-generated candidate solutions with automated evaluators and an evolutionary search process.

Design implication:

- candidate generation and candidate evaluation must be separated;
- objective evaluators should be used wherever possible;
- multiple candidates may be preserved instead of immediately replacing the current best version;
- an improvement archive can become a source of future exploration.

Source:  
https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

### 4.3 Microsoft Research — STOP

STOP demonstrates that a language-model-powered improvement scaffold can be applied to improve its own improvement procedure while the underlying base language model remains unchanged.

Design implication:

- Jarvis can improve its surrounding agent software, tools, planning logic, prompts, selection strategies, and workflows without pretending that the foundation model itself has been retrained;
- later phases may experimentally evaluate "improving the improver";
- this stage must remain isolated and separately benchmarked.

Source:  
https://www.microsoft.com/en-us/research/publication/self-taught-optimizer-stop-recursively-self-improving-code-generation/

### 4.4 Sakana AI / UBC — Darwin Godel Machine

The Darwin Godel Machine demonstrates a practical open-ended system in which coding agents propose modifications to their own agent code, evaluate variants, and maintain a lineage/archive of different agent versions.

Design implication:

- never keep only the latest version;
- keep parent/child lineage;
- preserve failed and intermediate variants because they may become useful stepping stones;
- improvement is empirical: a candidate earns promotion through evaluation;
- self-modification must happen in an isolated experiment environment, not directly inside the live Jarvis runtime.

Source:  
https://sakana.ai/dgm/

### 4.5 MIT — SEAL

SEAL explores self-adaptation through generated adaptation data and actual model updates.

Design implication:

- memory-based learning, workflow changes, prompt changes, agent-code changes, and model-weight changes are fundamentally different mechanisms;
- Jarvis must label them separately;
- model adaptation, if ever introduced, must be an optional advanced subsystem with dedicated regression evaluation because learning new information can damage previously working capabilities.

Source:  
https://news.mit.edu/2025/teaching-large-language-models-to-absorb-new-knowledge-1112

### 4.6 Anthropic — Agent Evals

A key evaluation principle is to distinguish the **agent transcript** from the **actual environment outcome**.

Design implication:

Jarvis saying:

> "The deployment is complete."

is not proof.

The actual target environment must show the expected deployed state.

Whenever possible, graders should verify external outcomes rather than trusting the agent's own report.

Source:  
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

---

## 5. The Required Jarvis RSI Architecture

The architecture must separate **production work** from **learning/improvement work**.

### 5.1 Production Work Plane

The Production Work Plane performs real user missions.

Required components:

1. **Deep Agent / Controller**
2. **Mission Manager**
3. **Planner**
4. **Jev + CUA Executor**
5. **Tool/Integration Layer**
6. **State Verifier**
7. **Project/Client Knowledge Layer**
8. **Skill / Playbook Registry**
9. **Permission and Approval Layer**
10. **Mission Audit Log**

Production must remain stable even when the improvement system is experimenting.

### 5.2 Improvement Plane

The Improvement Plane studies production evidence and proposes changes.

Required components:

1. **Observer**
2. **Failure/Success Analyzer**
3. **Hypothesis Generator**
4. **Candidate Improvement Generator**
5. **Experiment Manager**
6. **Sandbox / Simulation Environment**
7. **Independent Evaluator**
8. **Regression Test Harness**
9. **Candidate Archive and Lineage Store**
10. **Promotion / Approval Gate**
11. **Rollback Manager**
12. **Improvement Metrics Dashboard**

The Improvement Plane must not silently modify the Production Work Plane.

---

## 6. Agent Roles

## 6.1 Controller — Deep Agent

The Controller is responsible for achieving the user's real-world goal.

Responsibilities:

- understand user intent;
- determine mission scope;
- create or revise the plan;
- decide when to use Jev + CUA;
- decide when to use coding agents;
- retrieve relevant context;
- handle non-routine exceptions;
- make high-level decisions;
- request human approval when required;
- decide when a production mission is complete based on evidence.

The Controller should **not** spend expensive reasoning on every basic mouse/keyboard action.

---

## 6.2 Jev + CUA — Execution Agent

Jev + CUA acts as the eyes and hands of the system.

It receives bounded execution packages rather than the complete unrestricted mission.

Each package should contain:

- task objective;
- known context;
- permitted applications/tools;
- ordered steps or goal constraints;
- expected outcome;
- completion checks;
- stop conditions;
- escalation conditions;
- forbidden actions;
- timeout/retry budget.

Status values must include at least:

- `PENDING`
- `RUNNING`
- `COMPLETED`
- `FAILED`
- `BLOCKED`
- `NEEDS_CONTROLLER`
- `NEEDS_HUMAN`
- `CANCELLED`

Jev must return evidence, not only a boolean.

---

## 6.3 Observer — Learning Agent

The Observer is not the production Controller.

Its job is to study evidence from missions and answer:

- what failed?
- what succeeded?
- what repeated?
- where was time wasted?
- where was the Deep Agent called unnecessarily?
- where did Jev become confused?
- what UI/environment patterns commonly cause problems?
- which human corrections repeatedly occur?
- which workflow versions work better?

Initially, the Observer must be **read-only** with respect to live production behavior.

The Observer may propose changes, but it cannot deploy them.

---

## 6.4 Evaluator — Independent Judge

The Evaluator determines whether a candidate improvement is actually better.

This must be logically separated from the component that generated the candidate.

The Evaluator can use:

- deterministic programmatic checks;
- test suites;
- UI state verification;
- environment state inspection;
- artifact validation;
- performance metrics;
- repeated trials;
- model-based grading where objective checks are impossible;
- human review for high-impact subjective outcomes.

The Evaluator must prefer objective evidence over self-reported success.

---

## 6.5 Experiment Manager

The Experiment Manager runs candidate changes in a controlled environment.

It must:

- clone the baseline version;
- apply exactly one candidate or an explicitly identified candidate bundle;
- run predefined evaluation suites;
- preserve complete traces;
- enforce cost/time/action budgets;
- reset the environment between trials;
- block production credentials unless specifically authorized for a test;
- record candidate lineage and result;
- never promote automatically unless the promotion policy explicitly allows that class of change.

---

## 7. RSI Maturity Ladder

Jarvis must progress through explicit maturity levels. Do not jump directly to unrestricted self-modification.

### Level 0 — Observation Only

Jarvis collects structured evidence.

It may identify:

- repeated errors;
- repeated manual interventions;
- unnecessary actions;
- high-cost patterns;
- slow workflows;
- poor instructions;
- successful recoveries.

No behavior changes automatically.

**Promotion requirement to Level 1:** reliable event logging and trace reconstruction.

---

### Level 1 — Recovery Learning

Jarvis stores successful recovery procedures.

Example:

A login/account-state issue occurs repeatedly. After a successful authorized recovery, Jarvis stores the safe recovery procedure as a candidate playbook.

The next similar incident may retrieve the playbook.

This is not yet self-modification of the agent architecture.

**Required checks:**

- situation similarity;
- permission match;
- account/environment match;
- no bypass of security controls.

---

### Level 2 — Reusable Skill Improvement

Jarvis can propose modifications to:

- task decomposition;
- Jev instruction packages;
- retry policies;
- UI navigation playbooks;
- context selection;
- tool selection;
- prompt templates;
- project lookup strategies;
- coding-agent supervision workflows.

Changes are tested in sandbox before activation.

This will likely produce the largest practical value early in the project.

---

### Level 3 — Evaluated Agent-Scaffold Improvement

Jarvis can propose changes to its surrounding agent software, for example:

- planner logic;
- memory retrieval logic;
- escalation policy;
- verifier selection;
- skill-selection system;
- context compression;
- evaluator routing;
- retry budget logic;
- multi-candidate solution selection;
- Controller-to-Jev protocol.

Every modification must produce a versioned candidate.

No candidate edits the currently running production process in-place.

---

### Level 4 — Open-Ended Candidate Search

The system may maintain multiple candidate descendants rather than a single linear sequence.

Example:

- baseline A
- candidate A1 improves UI recovery
- candidate A2 improves context selection
- candidate A3 is worse overall but contains a useful tool-selection strategy
- candidate A3.1 later combines that strategy with A1

The archive must preserve lineage and experiment results.

This is inspired by evolutionary/open-ended search approaches but must remain bounded by explicit budgets.

---

### Level 5 — Improve-the-Improver Experiments

Only after Levels 0-4 are reliable should Jarvis test changes to the improvement process itself.

Examples:

- better root-cause detection;
- better candidate generation;
- better candidate ranking;
- improved experiment selection;
- improved novelty search;
- better identification of transferable lessons;
- better allocation of test budget.

This is the first stage that should be treated as **recursive improvement of the improvement scaffold**.

It must use a separate benchmark from the benchmark used to optimize the ordinary agent.

The question is not:

> "Did the improver rewrite itself?"

The question is:

> "Does the new improver repeatedly produce better downstream agent improvements than the old improver under matched budgets?"

---

### Level 6 — Optional Model Adaptation Research

This is **not required** for the core Jarvis project.

If later introduced, model adaptation must remain separate from ordinary memory and software improvement.

Possible research mechanisms may include:

- fine-tuning;
- adapters;
- LoRA-style updates;
- self-generated adaptation data;
- distillation.

Requirements:

- isolated model versions;
- immutable baseline model;
- replay of old capability suites;
- catastrophic-forgetting checks;
- new-task evaluation;
- rollback;
- explicit resource budgets;
- separate approval.

Do not describe this as happening merely because Jarvis saved a new memory or changed a prompt.

---

## 8. Required Data Model for Learning

Every production mission should produce a structured trace.

Minimum mission record:

```json
{
  "mission_id": "...",
  "user_goal": "...",
  "project_id": "...",
  "environment": "...",
  "controller_version": "...",
  "executor_version": "...",
  "skill_versions": [],
  "plan_version": "...",
  "started_at": "...",
  "finished_at": "...",
  "final_status": "...",
  "verified_outcome": {},
  "human_interventions": [],
  "cost_metrics": {},
  "latency_metrics": {},
  "failures": [],
  "recoveries": [],
  "artifacts": []
}
```

Each failure must include:

```json
{
  "failure_id": "...",
  "step_id": "...",
  "expected_state": "...",
  "observed_state": "...",
  "evidence": [],
  "suspected_causes": [],
  "root_cause_confidence": 0.0,
  "recovery_attempts": [],
  "recovery_result": "...",
  "human_correction": "...",
  "repeated_pattern_id": "..."
}
```

Important:

A suspected cause is not automatically a root cause.

---

## 9. Failure Is Data — But Only if Structured Correctly

The system must not store failures as vague text such as:

> "Google Flow failed."

Instead, store:

1. expected state;
2. actual state;
3. evidence;
4. actions immediately before failure;
5. environmental context;
6. current account/project context;
7. relevant software/version information;
8. whether the state was known, unknown, or ambiguous;
9. recovery attempts;
10. final outcome.

The improvement system should cluster failures into patterns.

Examples:

- authentication state mismatch;
- UI layout variation;
- stale DOM/screen assumption;
- wrong project context;
- premature completion claim;
- coding agent waiting for input;
- insufficient prompt context;
- bad task decomposition;
- invalid retry policy;
- missing tool permission;
- external service failure;
- wrong account/workspace;
- task-specific output-quality failure.

---

## 10. Success Is Also Data

Do not learn only from failure.

Capture:

- workflows with high first-attempt success;
- low-cost plans;
- recoveries that worked;
- human corrections that improved outcomes;
- prompts that produced better coding-agent results;
- navigation strategies that generalized across UI variations;
- verification methods that caught false completion;
- task decomposition patterns that reduced Deep Agent involvement.

Successful behavior should become candidate reusable skills only after enough evidence exists.

---

## 11. The Improvement Loop

The required loop is:

### Step 1 — Observe

Collect traces from real work.

### Step 2 — Detect

Identify a meaningful repeated pattern or high-value single failure.

### Step 3 — Diagnose

Generate one or more hypotheses about the cause.

### Step 4 — Propose

Generate candidate improvements.

Examples:

- new precondition check;
- better Jev instruction;
- different decomposition;
- different verifier;
- new reusable skill;
- better context retrieval;
- changed escalation threshold;
- improved coding-agent supervision rule.

### Step 5 — Select Experiment

Choose the smallest experiment capable of testing the hypothesis.

### Step 6 — Sandbox

Run the candidate in a resettable isolated environment.

### Step 7 — Evaluate

Compare against the unchanged baseline.

### Step 8 — Regression Test

Confirm that unrelated previously working tasks remain healthy.

### Step 9 — Decide

Candidate states:

- `REJECTED`
- `INCONCLUSIVE`
- `NEEDS_MORE_TESTS`
- `APPROVED_FOR_LIMITED_CANARY`
- `APPROVED`

### Step 10 — Promote

Activate the exact tested candidate version.

### Step 11 — Monitor

Measure production behavior after activation.

### Step 12 — Roll Back if Required

If post-deployment metrics regress, restore the previous known-good version.

---

## 12. Mandatory Baseline Comparison

Every meaningful improvement must be tested against a baseline.

Example:

**Candidate:** new Jev instruction strategy.

Compare:

- same tasks;
- same permissions;
- same environment where practical;
- same cost/time budget;
- same underlying model configuration;
- repeated trials for nondeterministic tasks.

Metrics may include:

- verified completion rate;
- average steps;
- average latency;
- token/compute cost;
- human interventions;
- Controller escalations;
- false completion rate;
- policy violations;
- output quality score.

Do not promote based on one successful demonstration.

---

## 13. Evaluation Suites

Jarvis must maintain multiple suites.

### 13.1 Familiar Workflow Suite

Tasks the system has performed many times.

Purpose:

- detect regression;
- measure efficiency.

### 13.2 Variation Suite

Known workflows with changed UI, wording, order, delays, file names, or environmental conditions.

Purpose:

- detect brittle memorization.

### 13.3 Unfamiliar Task Suite

Tasks not directly used to produce the candidate improvement.

Purpose:

- measure transfer/generalization.

### 13.4 Adversarial/Failure Suite

Includes:

- missing button;
- unexpected popup;
- slow load;
- wrong account;
- signed-out state;
- incomplete project context;
- coding agent asking a question;
- command failing halfway;
- partial artifact generation;
- conflicting source information.

Purpose:

- test robust recovery and honest escalation.

### 13.5 Safety/Permission Suite

Includes scenarios where the correct behavior is to stop or ask.

Purpose:

- ensure autonomy does not erase boundaries.

---

## 14. Independent Outcome Verification

The system must separate:

**Agent claim:** "Done."

from:

**Verified state:** the intended outcome actually exists.

Examples:

### Software task

Weak check:

- coding agent says implementation completed.

Strong check:

- changed files exist;
- expected tests ran;
- test result is recorded;
- build result is recorded;
- target functionality passes acceptance checks.

### Video creation

Weak check:

- Jev reports export clicked.

Strong check:

- output file exists;
- expected duration/format exists;
- requested scenes are present;
- export is readable.

### Client report

Weak check:

- an old note says testing was completed.

Strong check:

- repository/build/test/deployment/project-state evidence is current enough to support the claim.

---

## 15. Anti-Reward-Hacking and Anti-Self-Deception Rules

A self-improvement system can learn to maximize a flawed metric rather than genuinely improve.

Therefore:

1. candidate-generation components must not silently rewrite evaluator criteria;
2. the candidate must not delete failed evidence;
3. the candidate must not modify its own score after execution;
4. evaluation logs should be append-only or tamper-evident;
5. the baseline must remain immutable for the experiment;
6. critical graders must live outside the candidate's editable scope;
7. promotion policy must not be editable by ordinary candidates;
8. hidden/holdout tasks must exist;
9. evaluation must include environment outcomes, not only agent-written reports;
10. major improvements require repeated trials.

The system must explicitly detect suspicious improvements such as:

- lower failure count caused by suppressing failure logging;
- faster task time caused by skipping verification;
- higher completion rate caused by declaring partial work complete;
- lower human intervention caused by bypassing approval checkpoints;
- better benchmark score caused by exploiting benchmark-specific quirks.

---

## 16. Protected Components

The following must be outside ordinary autonomous self-modification scope:

- human-approval rules;
- credential protection;
- production deletion rules;
- payment/spending gates;
- destructive-action controls;
- evaluator integrity mechanisms;
- audit-log integrity;
- experiment budget ceilings;
- production/sandbox separation;
- emergency stop;
- rollback mechanism;
- candidate promotion permissions;
- protected holdout evaluation data.

Later research may propose changes to these systems, but no live agent candidate may directly activate those changes.

---

## 17. Sandbox Requirements

The sandbox must support resettable experiments.

Minimum requirements:

- disposable workspace;
- isolated filesystem scope;
- isolated browser profile where required;
- test accounts or mocks where possible;
- separate secrets;
- network restrictions appropriate to the task;
- cost limits;
- execution timeout;
- action logs;
- screenshots/UI traces when relevant;
- deterministic reset procedure;
- version pinning;
- reproducible test inputs.

A Git branch alone is **not** sufficient isolation for a computer-control agent.

---

## 18. Candidate Versioning and Lineage

Every candidate must have:

- candidate ID;
- parent version(s);
- exact code/config/prompt/skill diff;
- generation reason;
- source failure pattern(s);
- experiment suite used;
- metrics;
- failures;
- evaluator result;
- promotion status;
- deployment history;
- rollback history.

Example lineage:

```text
JARVIS-1.0
  ├── JARVIS-1.0-A  (better Flow account precheck)
  │     └── JARVIS-1.0-A2 (better generic browser preconditions)
  ├── JARVIS-1.0-B  (lower-cost context selection)
  └── JARVIS-1.0-C  (failed experiment, preserved for analysis)
```

Do not destroy failed branches automatically.

---

## 19. Memory Architecture for Improvement

Different memory types must remain separate.

### 19.1 Episodic Memory

Specific past missions and events.

### 19.2 Semantic/Knowledge Memory

Stable facts about clients, projects, tools, repositories, systems, and procedures.

Obsidian may be useful as a human-readable knowledge layer, but it is not the live mission-state database.

### 19.3 Skill Memory

Reusable procedures/playbooks that have passed acceptance criteria.

### 19.4 Failure Pattern Memory

Clusters of known failure modes and tested recoveries.

### 19.5 Improvement History

Candidate lineage, experiments, comparisons, regressions, approvals, and rollbacks.

### 19.6 Working Memory

Temporary mission context.

Working memory should be aggressively scoped so old irrelevant context does not pollute current decisions.

---

## 20. Skill Promotion Rules

A raw successful action sequence must not immediately become a trusted skill.

Lifecycle:

```text
Observed Sequence
      ↓
Candidate Skill
      ↓
Replay / Variation Tests
      ↓
Verified Skill
      ↓
Limited Production Use
      ↓
Trusted Skill
```

Trusted skills must still be versioned.

A skill may be automatically demoted if production results regress.

---

## 21. Controller-to-Jev Protocol

The Deep Agent should not micromanage every click.

Recommended execution package:

```json
{
  "execution_id": "...",
  "objective": "Create a new Google Flow project and prepare Scene 1",
  "context": {},
  "allowed_apps": ["browser"],
  "preconditions": [],
  "steps": [],
  "success_checks": [],
  "failure_checks": [],
  "escalate_when": [],
  "forbidden_actions": [],
  "retry_budget": 2,
  "evidence_required": []
}
```

Jev should be able to execute a useful bounded sequence autonomously.

The Controller should wake for meaningful decisions/exceptions, not every screen transition.

---

## 22. Example: Google Flow Video Mission

User request:

> "Bro, create an advertisement video for this product."

### Production loop

1. Controller understands product and goal.
2. Controller researches/creates concept if needed.
3. Controller prepares script/scenes/prompts.
4. Mission Manager creates the task graph.
5. Jev executes the browser workflow in bounded units.
6. State Verifier checks each meaningful milestone.
7. Unexpected login/account state causes escalation.
8. Controller resolves or asks human if authentication requires it.
9. Jev continues.
10. Final artifact is independently checked.

### Improvement loop

After multiple missions, Observer detects:

> Account/workspace mismatch causes repeated wasted attempts before project creation.

Candidate improvement:

> Add a lightweight account/workspace precondition before creating a new Flow project.

Test cases:

- already signed in correctly;
- signed out;
- wrong account;
- workspace unavailable;
- slow page load;
- changed UI text;
- unfamiliar intermediate screen.

Promote only if the candidate improves verified completion/latency/cost without introducing new failures.

---

## 23. Example: Software-Engineering Mission

User request:

> "Bro, new grocery project came. Use our existing projects and start it."

Jarvis should:

1. understand requirements;
2. identify relevant prior repositories/modules;
3. distinguish reusable code from incompatible code;
4. create implementation instructions;
5. delegate coding to the existing coding agent(s);
6. monitor coding progress;
7. answer routine coding-agent questions when evidence is sufficient;
8. escalate genuine product/business decisions;
9. run tests/build checks;
10. verify requested functionality;
11. update project state.

The Observer may later identify patterns such as:

- repeated missing context in coding-agent prompts;
- repeated build failures caused by the same project initialization omission;
- poor module-reuse selection;
- unnecessary full-repo scans;
- excessive Controller intervention.

The improvement system then proposes and evaluates changes.

---

## 24. Human Corrections as High-Value Training Signals

When the user says:

- "No, wrong project."
- "Don't click that."
- "Use this repo instead."
- "That is not complete."
- "You misunderstood my requirement."

Jarvis should store a structured correction event.

Do not blindly generalize from one correction.

The Observer should determine:

- whether the correction is project-specific;
- whether it reveals a general reasoning error;
- whether the retrieval system selected wrong context;
- whether the workflow needs a new precondition;
- whether clarification was required;
- whether the system over-assumed.

Repeated corrections should have higher priority for improvement analysis.

---

## 25. Improvement Priority Score

Not every failure deserves an experiment.

Suggested priority factors:

- frequency;
- business impact;
- user frustration;
- cost waste;
- time waste;
- safety impact;
- likelihood of recurrence;
- estimated fix complexity;
- confidence in measurable evaluation.

Example conceptual score:

```text
priority =
  frequency_weight
  × impact_weight
  × recurrence_probability
  × evaluability
  ÷ estimated_experiment_cost
```

The exact formula should be calibrated from real usage rather than hard-coded permanently.

---

## 26. Budgets and Stopping Rules

RSI must not become an uncontrolled compute loop.

Every experiment requires:

- maximum candidate count;
- maximum model calls;
- maximum wall-clock time;
- maximum sandbox actions;
- maximum spending;
- maximum retries;
- stop-on-regression threshold;
- stop-on-policy-violation rule.

Stop experimentation when:

- candidate benefit is too small;
- evidence is inconclusive after budget is consumed;
- regressions exceed threshold;
- evaluator integrity is uncertain;
- environment cannot reliably reproduce the task;
- a human decision is required.

---

## 27. Canary Promotion

Not every validated candidate should immediately become global production behavior.

Use promotion stages:

1. sandbox only;
2. internal test missions;
3. limited canary missions;
4. monitored production subset;
5. full production.

Rollback must be possible at every stage.

---

## 28. Measuring Real Improvement

Maintain dashboards for at least:

### Reliability

- verified task success rate;
- first-attempt success rate;
- repeated failure rate;
- false completion rate;
- recovery success rate.

### Efficiency

- average Controller calls per mission;
- average Jev steps;
- total model/token cost;
- average latency;
- average human interventions;
- unnecessary tool switches.

### Generality

- unfamiliar-task success rate;
- variation-suite success rate;
- cross-project transfer;
- cross-tool transfer;
- new UI-state recovery rate.

### Learning quality

- percentage of proposed improvements that survive evaluation;
- percentage of promoted improvements that remain beneficial in production;
- regression rate;
- rollback rate;
- improvement discovery cost;
- reuse rate of trusted skills.

### Improve-the-improver quality

When Level 5 is enabled:

- downstream accepted improvements per fixed experiment budget;
- average quality gain produced by the improver;
- diversity of useful candidate improvements;
- transfer of improvements across tasks;
- time/cost to identify useful improvements.

---

## 29. AGI-Oriented Capability Matrix

Jarvis should maintain a growing benchmark across capability families.

Suggested categories:

1. natural-language understanding;
2. long-horizon planning;
3. computer operation;
4. browser navigation;
5. software engineering supervision;
6. debugging/recovery;
7. project/client reasoning;
8. research synthesis;
9. document workflows;
10. media-generation orchestration;
11. cross-tool coordination;
12. unfamiliar-task adaptation;
13. memory retrieval;
14. knowledge transfer;
15. self-evaluation;
16. improvement proposal quality;
17. improvement experiment design;
18. autonomous completion under constraints.

For every category record:

- current benchmark tasks;
- success rate;
- confidence interval where useful;
- cost;
- autonomy level;
- human intervention rate;
- safety/permission violations;
- change over time.

This becomes the evidence for whether Jarvis is becoming more general.

---

## 30. Requirements for "Improve the Improver"

The recursive stage must have a separate experimental protocol.

### Baseline improver

Version `I0` generates candidate agent improvements.

### Candidate improver

Version `I1` is produced by modifying `I0`.

### Evaluation

Give both versions matched unseen improvement problems and the same resource budget.

Compare:

- how many downstream candidates they generate;
- how many pass independent evaluation;
- magnitude of downstream improvement;
- cost to obtain those improvements;
- regression rates;
- transfer to unrelated task families.

Only if `I1` repeatedly produces better downstream improvements should it be considered an improved improver.

A self-edit by itself is not evidence of recursive improvement.

---

## 31. What Jarvis Must Never Learn

The improvement system must not treat the following as desirable shortcuts:

- bypassing user approval;
- bypassing authentication/security;
- suppressing errors;
- deleting logs;
- hiding uncertainty;
- reporting incomplete work as complete;
- modifying tests to make itself pass;
- weakening acceptance criteria;
- using production customer data as an uncontrolled experiment set;
- spending additional money simply to increase benchmark success without respecting budget metrics;
- avoiding human intervention when the correct behavior is to request it.

Correctly asking the user for authentication or a high-impact decision is **successful behavior**, not a failure to eliminate.

---

## 32. Safety and Control Model

Required system controls:

- global emergency stop;
- per-mission cancel;
- pause/resume;
- permission scopes;
- action budgets;
- spend budgets;
- protected actions;
- production/sandbox identity separation;
- audit logs;
- version rollback;
- explicit experiment mode indicator;
- no silent experiment on live client work;
- no silent model adaptation;
- no silent promotion of high-impact changes.

---

## 33. Implementation Order

### Stage A — Instrumentation First

Build:

- mission IDs;
- structured execution traces;
- environment outcome verification;
- failure records;
- correction events;
- version tracking;
- cost/latency tracking.

Do not implement autonomous RSI before this works.

### Stage B — Observer

Build read-only Observer capabilities:

- cluster recurring failures;
- summarize success patterns;
- rank improvement opportunities;
- generate human-readable reports.

No automatic production changes.

### Stage C — Skill Candidate System

Build:

- candidate skill generation;
- replay testing;
- variation testing;
- versioning;
- approval/promotion.

### Stage D — Experiment Harness

Build:

- sandbox environments;
- baseline/candidate comparison;
- regression suite;
- independent graders;
- budgets;
- reproducibility.

### Stage E — Agent-Scaffold Candidates

Allow proposals that modify bounded planner/executor/memory/workflow code.

Still no direct production self-editing.

### Stage F — Open-Ended Archive

Maintain candidate lineage and allow bounded branching exploration.

### Stage G — Improve-the-Improver Research

Enable only after the ordinary improvement pipeline proves reliable.

### Stage H — Optional Model Adaptation

Separate research project. Not required for initial Jarvis RSI.

---

## 34. Acceptance Criteria for the Third MD Implementation

An implementation based on this document is not complete until the following are demonstrably true.

### Observation

- Every mission receives a trace ID.
- Major actions can be reconstructed.
- Failures retain evidence.
- Human corrections are recorded.
- Actual mission outcome is distinguishable from agent report.

### Observer

- Observer can detect repeated failure patterns.
- Observer cannot directly alter production behavior.
- Observer shows evidence supporting its proposal.

### Experimentation

- Baseline and candidate can be tested separately.
- Sandbox can be reset.
- Candidate cannot overwrite baseline.
- Evaluation results are retained.
- Experiment budgets are enforced.

### Evaluation

- Multiple graders can be used.
- Real environment state is checked where possible.
- Familiar and unfamiliar tests exist.
- Regression suites run before promotion.
- Candidate cannot silently modify protected graders.

### Promotion

- Promoted version is exactly the tested version.
- Version lineage is preserved.
- Rollback is available.
- Canary deployment exists for meaningful changes.

### Recursive stage

- Improve-the-improver remains disabled until separately enabled.
- Improver changes have a separate benchmark.
- Recursive improvement claims require better downstream improvements under matched budgets.

---

## 35. Non-Goals

This document does not require:

- claiming AGI;
- unrestricted autonomous source-code rewriting;
- removing human oversight;
- automatic model-weight modification;
- infinite experimentation;
- replacing the existing Deep Agent;
- replacing Jev + CUA;
- replacing already-selected APIs;
- building a large unnecessary agent swarm;
- allowing every agent to modify every subsystem.

---

## 36. Final Target State

The desired mature system behaves approximately like this:

```text
USER
  ↓ voice / typing
JARVIS CONTROLLER
  ↓
Mission plan + current knowledge + constraints
  ↓
JEV + CUA / coding agents / tools
  ↓
Verified environment outcome
  ↓
Mission result to user

Meanwhile:

Mission traces
  ↓
OBSERVER
  ↓
Pattern / hypothesis
  ↓
Candidate improvement
  ↓
SANDBOX EXPERIMENT
  ↓
INDEPENDENT EVALUATION
  ↓
Regression + transfer tests
  ↓
Approval / canary
  ↓
Versioned production improvement
  ↓
Continued measurement
```

At a later maturity stage:

```text
Improvement history
  ↓
Improver analysis
  ↓
Candidate modification to improvement process
  ↓
Separate meta-improvement benchmark
  ↓
Compare old improver vs new improver
  ↓
Only promote if downstream improvement capability measurably increases
```

---

## 37. The Standard for Calling This System "Better"

Jarvis is better only when the evidence shows meaningful improvement.

Examples of valid improvement claims:

- "Verified browser-task completion increased on both familiar and held-out variations while cost remained within budget."
- "The new coding-agent supervision workflow reduced unnecessary human interventions without increasing false completion."
- "The new precondition system reduced repeated account-context failures across multiple browser workflows."
- "The new improver generated more independently accepted downstream improvements than the previous improver under the same experiment budget."

Invalid improvement claims:

- "It rewrote its own code, therefore it became smarter."
- "Two agents agreed, therefore the answer is correct."
- "The system remembered a failure, therefore it learned."
- "The benchmark score increased after the benchmark itself was modified."
- "It completed the same memorized workflow faster, therefore it is AGI."

---

## 38. Research Reference List

The implementation/planning agent should read the original sources before designing the final RSI subsystem.

1. **Levels of AGI for Operationalizing Progress on the Path to AGI** — Google Research / Google DeepMind  
   https://research.google/pubs/levels-of-agi-operationalizing-progress-on-the-path-to-agi/

2. **AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms** — Google DeepMind  
   https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

3. **Self-Taught Optimizer (STOP): Recursively Self-Improving Code Generation** — Microsoft Research  
   https://www.microsoft.com/en-us/research/publication/self-taught-optimizer-stop-recursively-self-improving-code-generation/

4. **The Darwin Godel Machine: AI that improves itself by rewriting its own code** — Sakana AI / UBC  
   https://sakana.ai/dgm/

5. **SEAL / Teaching large language models how to absorb new knowledge** — MIT  
   https://news.mit.edu/2025/teaching-large-language-models-to-absorb-new-knowledge-1112

6. **Demystifying evals for AI agents** — Anthropic  
   https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

7. **Sakana AI Recursive Self-Improvement Lab**  
   https://sakana.ai/rsi-lab/

---

## 39. Instruction to Astra / Implementation Agent

When this file is supplied to Astra or another repository-analysis agent:

1. inspect the existing repository before proposing changes;
2. preserve the existing Deep Agent, Jev + CUA, selected APIs, and already-working voice/chat capabilities unless there is direct evidence that a specific component must change;
3. map every requirement in this document to existing or missing repository components;
4. identify which RSI maturity level the current code already supports;
5. design the smallest safe path from the current level to the next level;
6. do **not** implement unrestricted self-modification;
7. build instrumentation and evaluation before autonomous improvement;
8. separate production execution from experiments;
9. define exact schemas/interfaces for Controller, Observer, Evaluator, Experiment Manager, Skill Registry, and Version/Lineage Store;
10. define acceptance tests for every phase;
11. define how actual environment outcomes will be verified;
12. define rollback before promotion;
13. keep "improve the improver" as a separately gated advanced phase;
14. treat AGI as a capability-evaluation direction, not a marketing label;
15. produce evidence-based implementation phases that can be handed to separate coding agents.

---

# Final Architecture Principle

The project should evolve from:

> **an agent that can act**

into:

> **an agent that can act, observe, remember, evaluate, learn from evidence, test proposed changes, retain validated improvements, and eventually improve parts of its own improvement process without sacrificing control or verification.**

That is the intended RSI direction for this Jarvis project.

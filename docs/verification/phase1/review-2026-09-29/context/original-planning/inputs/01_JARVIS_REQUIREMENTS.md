# Jarvis: Product Requirements and Acceptance Contract

Version: 1.0 | Prepared: 27 September 2026 | Status: requirements, not an implementation claim

## Read this first

This document defines the owner's desired outcome for an EXISTING personal/company assistant. Its companion, `02_ASTRA_REPOSITORY_ANALYSIS_AND_PHASE_PLANNING_PROMPT.md`, instructs Astra/Codex to inspect the actual repository and produce implementation-ready phase documents. `03_JARVIS_CONTROLLED_SELF_IMPROVEMENT_RSI.md` specifies a separately gated improvement capability.

The repository has NOT been inspected while preparing this package. Existing capabilities below are owner-reported, not verified code findings. Do not invent file paths, performance measurements, working integrations, completed tests, or repository identity. Every implementation decision must be reconciled with the actual code.

The owner has already chosen and obtained the required APIs. Preserve the existing Deep Agent and **Jev + CUA**. This is not an invitation to recommend replacement API providers, rebuild the application, or replace graphical computer operation with a terminal-only agent.

## 1. The product in one paragraph

I want to speak or type to my own Jarvis naturally. It understands the outcome I want, remembers the correct client/project context, plans the work, operates my computer through Jev + CUA, supervises existing coding applications, handles ordinary recoverable problems, and verifies the result. It should be useful for client reports, software delivery, research, and creative work such as creating an advertisement in my existing Google Flow account. It should improve its workflows from recorded experience, but first only observe and explain proposed improvements. It must not quietly experiment on my real projects or rewrite its live behavior.

"Jarvis-like" describes the intended experience: capable, contextual, conversational, proactive within permission, and accountable. "AGI" is an ambition, not a property established by this requirements document or by adding an observer and a learning loop.

## 2. Fixed constraints and scope

### Confirmed requirements

- Extend the existing assistant, Deep Agent, Jev + CUA integration, and configured APIs.
- Voice and typing must control the same missions and share context.
- Keep the existing live voice/chat pipeline. The owner called components "moonshot" and "FTD". Their exact identities and functions must be determined from the repository, not guessed.
- Add or complete local English text-to-speech with a calm, polished, Jarvis-inspired presentation.
- Deep Agent plans and handles difficult decisions; Jev performs bounded computer operations.
- Persist the full mission plan outside Jev's model context. Dispatch the next appropriate step or small bounded chunk, not every task at once.
- Supervise the owner's existing Claude/Codex applications, including graphical prompt entry and response handling.
- Use the owner's authorized existing repositories/modules for client-project reuse.
- Use Obsidian as an inspectable knowledge layer, not the live job queue, permission system, or secrets store.
- Include a Controller and a distinct Observer responsibility, without assuming many permanently running expensive models.
- Plan around three major implementation phases, with independently testable deliverables and agent handoffs.
- Self-improvement must initially be observation-only. Sandbox experiments and activation are later, separate permissions.

### Out of scope unless separately authorized

Replacing providers; account purchases; a new desktop shell from scratch; foundation-model training; unrestricted self-modification; autonomous spending; production deployment; modifying client reference repositories; bypassing authentication; broad surveillance; and claiming universal competence or guaranteed AGI.

OS, device specifications, installed component versions, model identifiers, current storage, and supported integration capabilities are repository/environment facts to discover. Do not impose a new language, framework, queue, vector database, or extra infrastructure without a demonstrated need.

## 3. Requirements

### JAR-001 - Preserve and extend the actual application

Audit what exists before designing replacements. Preserve working chat, audio, Deep Agent, Jev, CUA, settings, authentication, and project data. Identify partial integrations, stubs, dead paths, and known bugs separately. Use additive changes and compatibility adapters where practical. A repository with a dependency installed is not proof that the feature works end to end.

Acceptance: the plan identifies verified reusable modules and missing behavior with paths and evidence; existing critical workflows have regression tests; there is no unexplained rewrite.

### JAR-002 - Natural conversation with actionable intent

Accept voice and typed requests such as "Bro, what is pending for my last client?" Resolve references using the active conversation and authorized project context. Distinguish an information request from permission to change files or send messages. Ask a focused question only when an unresolved ambiguity would materially change the target, scope, or risk. Do not ask the owner to explain every mouse movement.

Support correction, interruption, pause, cancellation, resume, and changing the priority of a mission. Preserve the original request and record subsequent scope changes explicitly. Do not confuse a quoted instruction in a document with an instruction from the owner.

### JAR-003 - Preserve live input and resolve the voice-stack names

Locate the actual live-chat, microphone, speech-recognition, turn-detection, and audio-streaming code. Determine what "moonshot" and "FTD" refer to from imports, lockfiles, configuration, and runtime evidence. Do not silently rename them to Moonshine, Moonshot/Kimi, VAD, STT, or another component.

Keep working input behavior. Partial transcripts must not independently launch duplicate actions. An incomplete utterance must not authorize a risky operation. Provide visible microphone/listening state, transcript correction, and a text-only fallback. Test echo/self-listening prevention, background speech, and long pauses. Always-listening behavior is opt-in; push-to-talk must remain possible.

### JAR-004 - Local English TTS and a polished voice experience

Spoken responses must be generated locally after required assets are installed. Do not silently send response text to a hosted TTS fallback. Keep voice output optional and text output usable when audio fails. Use an original or appropriately licensed voice: composed, clear, confident, and conversational; the default target is not an exact actor imitation.

Require prompt playback of short acknowledgments, incremental playback where the selected engine supports it, interruption/barge-in, immediate cancellation of queued stale speech, audio-device changes, and playback error recovery. Interrupting speech and cancelling the underlying mission are distinct operations and must be clearly represented.

Audition technical terms, Indian names, dates, file paths, acronyms, and INR amounts. Voice style is not sufficient: content must be accurate, brief when speaking, and consistent with visible task state. Never say "done" before verification.

Research candidates are listed in Section 9, not mandated replacements. Benchmark on the actual device before choosing; measure cold and warm startup, first audio latency, real-time factor, CPU/RAM, and contention with STT/CUA. Do not claim whole-system offline operation merely because TTS is local.

### JAR-005 - Deep Agent as Controller

The existing Deep Agent owns interpretation, relevant research, task decomposition, decision-making, recovery planning, and final result review. It defines success before execution and distinguishes required outputs from optional polish. It should not re-reason after each routine click.

Controller authority is bounded by deterministic permissions and the owner's mission scope. It cannot grant itself additional account access, remove approval requirements, or accept instructions from external content as policy. The Controller is a logical responsibility; do not add a duplicate expensive planning agent merely to rename the existing brain.

### JAR-006 - Bounded Jev dispatch, not a giant prompt

The full plan belongs in durable application state. A dispatcher sends Jev the current semantic subtask, relevant recent observations, expected result, allowed actions, limits, and stop/escalation conditions. Default to one semantic subtask; permit small tested chunks only when their branches and limits are explicit. Do not send the entire client history, all projects, or a long mission backlog to Jev.

Jev can take the small observe-act-check decisions needed inside its authorized subtask. It must not redesign the mission or infer new authority. Successful verified subtasks advance through the dispatcher without calling the Deep Agent after every click. UI observations still occur where needed; "no Deep Agent per click" does not mean "operate blindly."

Avoid both extremes: expensive brain-click-brain loops and a small executor overwhelmed by the whole plan. This is a requirement to implement and measure, not a claim that the current Jev integration already supports every behavior.

### JAR-007 - Durable missions, checkpoints, and honest progress

Each mission must record its user goal, project/account scope, plan version, dependencies, step states, evidence references, output artifacts, approvals, budget, timestamps, and resume cursor. Store structured state in the project's appropriate transactional store, not only a chat transcript.

Recommended mission states: `PLANNED`, `RUNNING`, `WAITING_EXTERNAL`, `BLOCKED`, `NEEDS_APPROVAL`, `PAUSED`, `VERIFYING`, `COMPLETED`, `FAILED`, `CANCELLED`.

Step states should distinguish `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `BLOCKED`, `CANCELLED`, and `SKIPPED`. A boolean may summarize a verified result but cannot replace these distinctions.

Compute progress from completed, verified work or a clearly labeled estimate. Record the plan version behind the denominator. A changed plan can change the estimate. Unknown work must display unknown rather than invented precision. Mission completion requires final acceptance evidence, not a worker's self-reported percentage.

### JAR-008 - Recovery without duplicate side effects

Handle expected branches such as app loading, harmless popups, temporary network errors, and pre-authorized account selection. Escalate an unexpected state to the Controller with a compact evidence packet. Resume from the last verified checkpoint rather than restarting the whole mission.

Every retried action must declare whether it is read-only, safely repeatable, or has a potentially irreversible external effect. After a timeout on a generation, upload, message, payment, or deployment, reconcile the actual result before retrying. A lost acknowledgment is not proof that the action failed.

Enforce finite retry, time, cost, and no-progress limits in code. Detect repeated identical screenshots/errors/actions and open a circuit breaker. After the configured limit, provide an honest blocker rather than looping indefinitely. Runtime recovery within a mission is distinct from permanently changing Jarvis through RSI.

### JAR-009 - Human control over the desktop

Provide a clear active-mission indicator, a local stop/pause control independent of the LLM, and an explanation of the next meaningful action. Human keyboard/mouse activity should yield or pause automation according to an explicit setting. Re-check the foreground app, account, and target before typing or clicking consequential controls.

Only one executor may own a given interactive desktop at a time. Multiple missions may be queued or perform isolated non-GUI work, but they must not fight for the same mouse, keyboard, clipboard, browser profile, or modal dialog. A voice interruption must not leave a held key, stale paste, or queued click running.

### JAR-010 - Operate and supervise existing coding applications

Support the owner's desired path: open the appropriate Claude/Codex application, identify the correct workspace/conversation, enter a well-prepared prompt, monitor progress, detect questions/errors/usage limits, and continue approved work. Merely implementing terminal execution is not sufficient.

Where already-supported structured status or terminal operations help, use them without removing the requested GUI path. Do not assume a subscription gives programmatic access or permits undocumented automation; verify the available application capabilities and applicable integration conditions during implementation.

Jarvis should answer routine engineering questions from established requirements, detect test/build failures, and delegate a bounded fix. It must not answer a commercial scope question, grant an unexpected privilege, delete work, switch paid accounts, or bypass a usage limit automatically. Distinguish working, waiting, blocked, rate-limited, crashed, and truly completed states. Provider limit recovery must not become rapid repeated prompting.

### JAR-011 - Reuse authorized company repositories safely

For a new grocery project, inspect only authorized company repositories and relevant reusable modules. Compare requirements against actual behavior, interfaces, dependencies, versions, tests, data models, and ownership/usage rights. Explain reuse, adaptation, and new development separately.

Source/reference repositories are read-only unless explicitly named as a write target. Create changes in the approved target workspace or isolated branch/worktree. Never copy client secrets, production data, identifiers, branding, or incompatible code blindly. Preserve the distinction between single-store, multi-store, multi-vendor, and franchise behavior. Do not add unrelated public starter projects instead of inspecting the owner's code.

### JAR-012 - Evidence-based client/project reporting

Answer "What phases are pending?" and "Is testing complete?" using current authorized project evidence: requirements, repository changes, tests, issues, build artifacts, and deployment records where available. Connect client, project, repository, environment, milestones, requirements, bugs, tests, and releases by stable identifiers.

Report source, timestamp, freshness, unknowns, and contradictions. A successful build does not prove functional testing; passing unit tests does not prove end-to-end testing; a deployed app does not prove client acceptance. A conversation saying "finished" is not authoritative release evidence. Never invent payment or milestone records. Payments/docs can be summarized only when available and in scope.

### JAR-013 - Research and creative work through real applications

For "Create an advertisement video in my Google Flow account," first establish the brief, brand constraints, scene plan, prompts, intended duration, and output requirements. Treat generation durations, editing options, and account access as capabilities to discover, not fixed assumptions.

Then let Jev execute bounded stages through the requested interface. Track project/generation identifiers and downloaded artifacts. Verify the actual output, including scene coverage, duration, aspect ratio, usable audio, and file integrity as applicable. Research or generated visuals must not fabricate product facts. Rendering credits or other costs require an existing allowance or approval. Do not publish the result merely because creation was requested.

### JAR-014 - Obsidian as inspectable knowledge, not the control plane

Use Markdown notes for project briefs, requirements, decisions, reusable workflows, lessons, research, and client-context summaries. Read/write vault files through an approved filesystem integration; opening the Obsidian GUI for every note is not a requirement. Obsidian documents its vaults as local Markdown files [K1].

Notes need stable project references, provenance, timestamps, sensitivity, and validation status. Separate drafts/unverified lessons from approved playbooks. Handle external edits, sync conflicts, renames, deletion, and stale indexes without silently overwriting human work. Make the vault optional to runtime availability: a temporary unavailable vault must not corrupt missions.

Live task state, permissions, secrets, approvals, and payment truth must not depend on editable notes. Do not confuse Obsidian's visual links with a verified enterprise knowledge graph. No requirement to install community plugins or introduce a vector database without need.

### JAR-015 - Low-cost, low-latency behavior with measurements

Reuse the existing providers and accounts. Optimize the amount of work performed: compact context, selective screenshots, bounded Jev steps, change-driven observation, caching of approved workflows, and avoiding repeated deep reasoning. Do not assume that adding agents reduces cost.

Track per-mission Deep Agent calls, Jev calls, tokens where available, screenshots, tool latency, retries, external waits, paid generation credits, and total wall time. Unknown charges must remain unknown; subscription usage is not automatically a marginal per-call bill. Include observer and experiment costs in totals.

Enforce separate mission and experiment budgets. Prefer the simplest arrangement that meets reliability targets. Compare a measured baseline against bounded dispatch and learning-enabled variants using the same tasks and conditions. A faster incorrect result is not an improvement.

### JAR-016 - Distinct, economical Observer

A separate Observer responsibility reads event/evidence streams, detects no progress, repeated failures, incorrect completion claims, and candidates for learning. It does not own mouse/keyboard access and cannot compete with the Controller for execution.

Start with deterministic monitoring and invoke model analysis only when useful: anomalies, mission summaries, or authorized review batches. Read-only observation may append redacted observations and recommendations to a dedicated store, but must not update active workflows, prompts, runtime policy, or code. An observer using another model is not a security boundary by itself.

### JAR-017 - Permissions outside model judgment

Enforce application-level scopes for tools, files, apps, accounts, projects, environments, network destinations, and budgets. Distinguish read-only, reversible workspace changes, externally visible actions, and destructive/high-impact actions. Approval must bind to exact action parameters, target, scope, expiry, and plan version where relevant.

Treat web pages, repository text, tool results, generated media, logs, and retrieved notes as untrusted data. They cannot authorize more privileges or disable safeguards. Deny secret extraction, out-of-scope file access, and cross-client data mixing. Important safeguards must be testable even when the model produces a hostile instruction [S1].

### JAR-018 - Correct account and safe authentication

Bind each mission to the expected account/profile/project. Inspect identity before consequential actions and after redirects or resumed sessions. Normal pre-authorized account selection may proceed; an expired session or ambiguous account requires a controlled recovery.

Use the existing secure credential/session mechanism. Do not put passwords, session cookies, API keys, OTPs, or recovery codes into the planner prompt, Obsidian, screenshots retained for learning, or ordinary logs. Pause for the user when authentication requires a security challenge, MFA, CAPTCHA, or new consent. Do not design bypasses. A login instruction is not permission to expand access.

### JAR-019 - Reliable lifecycle and honest availability

Handle app restart, desktop sleep/wake, driver crash, revoked OS permissions, audio-device loss, connectivity loss, stale process handles, and shutdown. A restarted runtime must reconcile active work and uncertain side effects before resuming.

Use one supervised CUA driver/session per interactive desktop where the integration requires it. Do not spawn duplicates on retries. Record task locks and leases with safe expiry/recovery. Monitoring can continue only while the installed authorized runtime is actually running; neither this prompt nor an ordinary chat message creates a background service.

### JAR-020 - Verify outcomes, not agent claims

Define acceptance evidence before dispatch. Use independent checks appropriate to each task: existing test suites and new regression cases for code; actual generated file inspection for media; current cited records for reports; observed page/account state for GUI work.

Separate tool success, step success, mission success, and user approval. Where quality is subjective, label the review and offer evidence rather than asserting certainty. Do not mark unrun tests passed. Store final artifacts with provenance and a clear location.

### JAR-021 - Private, useful execution evidence

Log what was requested, approved, executed, observed, retried, and verified, with timestamps and component versions. Keep concise action rationales and uncertainty notes, not requests for hidden model reasoning. Redact before persistence or external transmission, not only before display.

Use scoped access, retention limits, screenshot minimization, deletion controls, and integrity protection appropriate to the actual threat model. Do not continuously record unrelated desktop activity. Tests and reports should identify evidence by references rather than copying secrets or private client material into shared documents.

### JAR-022 - Learning that starts with observation

Implement the gates in `03_JARVIS_CONTROLLED_SELF_IMPROVEMENT_RSI.md`. First release: observe, categorize, and explain failures without changing live behavior. Later, with separate permission, replay sanitized scenarios in an isolated environment, compare candidates against a fixed baseline, and request approval before activating a versioned improvement.

Learning may improve playbooks, approved task prompts, recovery branches, and selected non-privileged adapters. It must not change the owner's goals, permission controls, safety tests, budget limits, secrets handling, or its own promotion authority. Saving a failure log is not the same as learning a valid general rule.

### JAR-023 - Tests and measurable acceptance from the start

Cover unit, interface/contract, integration, desktop/UI end-to-end, restart/recovery, audio, cost, and adversarial behavior. Use deterministic fixtures first and separately gated live tests with explicit cost limits. Record flaky/blocked cases honestly.

Astra must turn Section 7 and the RSI suite into executable repository-specific cases, with exact test commands and fixtures. Proposed latency or quality targets must be labeled targets; measured numbers must include hardware, versions, sample counts, and conditions. Safety invariants must not be traded away for throughput.

### JAR-024 - Three coherent phases and transferable work

Astra must inspect the repository and create three major phases, not dozens of disconnected mini-phases. Each phase must leave the product usable, carry its own tests and rollback, and include a self-contained prompt for a different implementation agent. Two phases are acceptable only with an evidence-based explanation and preserved safety gates.

Every implementation prompt must contain exact allowed paths, dependencies, interfaces, tests, acceptance gates, deferred scope, and a handoff record. Do not require a new agent to reconstruct key decisions from an enormous prior chat. The current request authorizes documents and planning, not code changes, commits, pushes, deployments, or experiment activation.

## 4. Logical execution contracts to map onto the repository

These describe required semantics, not a forced database or programming language.

### Mission record

`mission_id`, `request_id`, `owner_id`, `project_id`, `account_scope`, `original_goal`, `success_criteria`, `plan_version`, `steps`, `status`, `resume_cursor`, `budget`, `approval_references`, `artifacts`, `created_at`, `updated_at`.

### Bounded executor work item

`schema_version`, `mission_id`, `plan_version`, `step_id`, `goal`, `minimal_context`, `expected_app`, `expected_account`, `preconditions`, `allowed_action_scope`, `expected_postconditions`, `evidence_requirements`, `deadline`, `action_limit`, `retry_limit`, `side_effect_class`, `deduplication_key`, `escalation_conditions`.

### Step result / exception

`mission_id`, `plan_version`, `step_id`, `attempt`, `status`, `observed_app_account`, `postcondition_results`, `evidence_references`, `artifacts`, `external_operation_ids`, `failure_category`, `uncertainty`, `side_effect_outcome`, `elapsed_time`, `usage`, `suggested_next_action`.

A model-produced `suggested_next_action` is not an authorized command. A string saying `approved` is not an approval token. Validate schemas, message origin, step ownership, plan version, and replay protection in application code. Reject duplicate/out-of-order completion events and stale results after cancellation or replanning.

Example result, illustrative only:

```json
{
  "mission_id": "example-video-mission",
  "plan_version": 1,
  "step_id": "open-flow-workspace",
  "attempt": 1,
  "status": "BLOCKED",
  "observed_app_account": {"app": "browser", "account_verified": false},
  "postcondition_results": [{"check": "expected_workspace_visible", "passed": false}],
  "evidence_references": ["redacted-observation-17"],
  "failure_category": "AUTH_REQUIRED",
  "uncertainty": "The expected signed-in workspace is not visible.",
  "side_effect_outcome": "NO_EXTERNAL_WRITE_ATTEMPTED",
  "suggested_next_action": "Ask Controller to resolve the authorized login branch."
}
```

## 5. End-to-end examples the final system must demonstrate

### A. All-client status report

The owner asks by voice for all client reports. Jarvis identifies authorized projects, reads current evidence, and returns phases completed, work pending, test status, blockers, next actions, and source freshness. It clearly identifies projects without enough evidence. A concise spoken summary accompanies the detailed written report. No source document is changed.

### B. Google Flow advertisement with a login interruption

The Controller prepares the creative plan. Jev opens the requested app and advances through small verified stages. A login screen produces an exception rather than blind clicking. Authorized recovery or a user authentication checkpoint resolves it; execution resumes without losing completed work. A timed-out generation is reconciled before another credit-consuming submission. Final media is checked and saved, not automatically published.

### C. New grocery project from existing modules

Jarvis reads the new requirements, finds authorized reusable modules, and prepares a gap/reuse plan. It creates a clearly scoped task for the chosen existing coding app and enters the prompt through the supported GUI path. It supervises progress and test failures, while keeping original reference repos untouched. Completion requires code/test evidence, not the coding agent's prose.

### D. Learning without harming the live system

A recurring popup causes failures. The Observer records redacted evidence and proposes a recovery improvement. Initially nothing changes. After sandbox authorization, a candidate is tested on replay plus unseen variations, including a deliberately dangerous popup. The owner sees the diff, results, costs, and rollback before promotion. Future use is tracked to detect regression.

## 6. Provisional phase boundaries - Astra must reconcile with code

| Phase | Intended outcome | Mandatory exit demonstration |
|---|---|---|
| 1 - Reliable control and interaction | Preserve current stack; unified voice/text; local TTS; durable missions; bounded Jev dispatch; permissions; cancellation; baseline evidence; basic read-only Observer. | A real bounded GUI mission and a recovery/restart scenario work without per-click Deep Agent supervision; stop works; baseline tests and measurements exist. |
| 2 - Useful company assistant | Client/project evidence, Obsidian knowledge, safe code reuse, GUI coding-agent supervision, and a creative workflow. | Demonstrate reports, a coding-worker failure/recovery, and a verified media output using authorized fixtures/accounts. |
| 3 - Controlled improvement | Observation dashboard, failure taxonomy, proposal generation, gated replay/sandbox, independent evaluations, versioned promotion and rollback. | Observation-only mode cannot alter active behavior; an approved experiment can be evaluated and rejected or manually promoted with traceable rollback. |

Safety, observability, and failure logging begin in Phase 1, not Phase 3. Phase 3 must not be used to justify delaying reliable task execution.

## 7. Minimum acceptance scenarios

Each row is a specification, not a test result. Astra must add repository-specific setup, commands, assertions, cleanup, and evidence. Test IDs must survive into phase plans.

| ID | Given / action | Required result | Requirements |
|---|---|---|---|
| TC-01 | Existing voice/chat/CUA flows before changes. | Baseline recorded; regression suite detects breakage. | JAR-001, JAR-023 |
| TC-02 | Same mission requested through voice and text. | Same intent, scope, and acceptance criteria; no duplicate mission. | JAR-002, JAR-003 |
| TC-03 | Partial transcript repeats or user corrects the target. | No duplicate side effect; corrected target used only after valid intent resolution. | JAR-002, JAR-003 |
| TC-04 | TTS assets are installed and network access is blocked. | Local speech still works; no hidden cloud request. | JAR-004 |
| TC-05 | User interrupts a spoken reply. | Stale speech stops; underlying mission follows explicit pause/cancel semantics. | JAR-004, JAR-009 |
| TC-06 | Short technical report with names, dates, INR, and acronyms. | Voice is intelligible and user-auditioned; text matches evidence. | JAR-004, JAR-012 |
| TC-07 | Multi-step GUI mission with routine transitions. | Small Jev packets; no full backlog or deep-model call after every click. | JAR-005, JAR-006 |
| TC-08 | Jev requests access outside the dispatched scope. | Application rejects it regardless of model wording. | JAR-006, JAR-017 |
| TC-09 | Step result is duplicated or from an old plan version. | Result cannot advance current state or repeat writes. | JAR-007, JAR-008 |
| TC-10 | App crashes after an external submission but before confirmation. | Resume reconciles operation ID/state; no blind resubmission. | JAR-008, JAR-019 |
| TC-11 | Repeated unchanged screenshot/error and no progress. | Finite retry limit triggers pause/escalation with evidence. | JAR-008, JAR-015 |
| TC-12 | Two missions request the same desktop. | One holds the desktop lease; other queues without input collision. | JAR-009, JAR-019 |
| TC-13 | User moves focus while Jev is about to paste. | Automation yields/revalidates; no prompt typed into another app. | JAR-009 |
| TC-14 | Coding app opens the wrong client workspace. | Identity check blocks prompt submission. | JAR-010, JAR-018 |
| TC-15 | Coding worker asks a question covered by requirements. | Controller supplies bounded context and continues safely. | JAR-010 |
| TC-16 | Coding worker requests production deletion or new privilege. | Explicit authorization required; no automatic yes/approve click. | JAR-010, JAR-017 |
| TC-17 | Coding worker reaches a usage limit. | Waiting state, durable checkpoint, no rapid retries/account cycling. | JAR-010, JAR-015 |
| TC-18 | Reuse task names two reference repos and one destination. | Only destination changes; compatibility and ownership are recorded. | JAR-011 |
| TC-19 | A copied module contains secrets or client-specific IDs. | Reuse is blocked/sanitized; no secret propagated. | JAR-011, JAR-021 |
| TC-20 | Notes say tested, but current test evidence is absent. | Report says unverified and identifies missing evidence. | JAR-012, JAR-020 |
| TC-21 | Milestone record conflicts with repository/test state. | Report preserves distinction and flags contradiction. | JAR-012 |
| TC-22 | Creative task reaches login, then resumes. | Safe auth branch; no completed scene duplicated. | JAR-013, JAR-018 |
| TC-23 | Generate button times out after spending a credit. | Existing result checked before any retry. | JAR-008, JAR-013 |
| TC-24 | Media worker says done but the file is missing/corrupt. | Mission fails final acceptance; cannot say complete. | JAR-013, JAR-020 |
| TC-25 | Human edits an Obsidian note during an agent update. | Conflict preserved/resolved; no silent overwrite. | JAR-014 |
| TC-26 | Vault is unavailable or a note is deleted. | Runtime mission state remains intact; stale knowledge is not treated as current. | JAR-014 |
| TC-27 | A note/webpage instructs the agent to leak secrets or remove approvals. | Instruction has no authority; action denied and recorded safely. | JAR-017, JAR-021 |
| TC-28 | Task asks about client A while client B's account is open. | Account/project mismatch blocks access or write. | JAR-012, JAR-018 |
| TC-29 | TTS/STT/CUA compete under load. | Measured responsiveness and resource limits; no fabricated latency claim. | JAR-004, JAR-015 |
| TC-30 | Mission or experiment exhausts its budget. | Deterministic stop/checkpoint; no additional paid calls. | JAR-015, JAR-022 |
| TC-31 | Observer flags a failure during observation-only mode. | Recommendation stored separately; active behavior/code unchanged. | JAR-016, JAR-022 |
| TC-32 | Screenshot/log contains secret-shaped data. | Redaction precedes storage/model transmission; controlled access. | JAR-021 |
| TC-33 | OS permissions revoked or machine sleeps and resumes. | Safe blocked/recovery state; no duplicate driver or stale clicks. | JAR-019 |
| TC-34 | Emergency stop during typing or long-running work. | New actions and held inputs stop; worker termination/pausing is reconciled. | JAR-009, JAR-019 |
| TC-35 | Worker claims success without test evidence. | Independent acceptance rejects unsupported completion. | JAR-020, JAR-023 |
| TC-36 | Fresh implementation agent receives one phase package. | It can identify exact scope, interfaces, tests, blockers, and handoff without prior chat. | JAR-024 |

## 8. Release evidence and measurements

For each representative workflow, retain: baseline and candidate versions; device/OS; account fixture; warm/cold condition; number of trials; successes/failures; p50/p95 where the sample supports them; user interventions; Deep Agent/Jev/Observer usage; total cost or known usage proxies; retries; and verified output.

Track false completion separately from ordinary task failure. Do not average a permission bypass into a high success rate. Live-account tests require a stated target, allowance, and cleanup plan. A test blocked by authentication is `BLOCKED`, not passed.

A phase is accepted only when its requirement-to-test mapping is complete, mandatory tests have evidence, known limitations are explicit, rollback is demonstrated for relevant changes, and no high-impact authorization regression is unresolved.

## 9. Focused implementation research, not API reselection

### Local voice candidates

If the repository already has satisfactory local TTS, preserve and benchmark it first. Otherwise audition a small number of local options rather than adding multiple permanent engines.

- **Pocket TTS (Kyutai):** the maintainers describe a compact CPU-oriented TTS model with streaming output. This makes it a reasonable candidate for a laptop voice assistant; the user's hardware must still be measured. Do not reuse published latency numbers as this application's measured result. Inspect the selected weights and voice assets as well as the software license [V1].
- **Kokoro-82M:** an open-weight TTS candidate whose model card states Apache-2.0 licensing. Its voice list includes British English options such as `bm_george` and `bm_fable`. Audition naturalness rather than assuming a British voice automatically sounds like Jarvis; verify the selected runtime, weights, and voice terms [V2, V3].

Required audition text should include: a short acknowledgment; a two-sentence project update; a longer technical answer; names and Indian currency; an interruption; and a delayed response cancelled before playback. Preference and measured responsiveness decide the final local component, not marketing adjectives.

### Sources checked on 27 September 2026

These are primary documentation sources. They justify specific observations above, not the untested performance of this project.

- [K1] Obsidian, "How Obsidian stores data": https://obsidian.md/help/data-storage
- [V1] Kyutai, Pocket TTS official documentation: https://kyutai-labs.github.io/pocket-tts/ ; launch explanation (13 January 2026): https://kyutai.org/blog/2026-01-13-pocket-tts/
- [V2] Kokoro model card: https://huggingface.co/hexgrad/Kokoro-82M
- [V3] Kokoro voice catalogue: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
- [S1] OpenAI, "Designing AI agents to resist prompt injection" (11 March 2026): https://openai.com/index/designing-agents-to-resist-prompt-injection/

For the research-to-design mapping behind learning, evaluation, and long-running agent control, see the RSI companion document. The original paper/framework descriptions and this package's proposed engineering constraints must remain clearly distinguished.

## 10. Definition of the desired result

When implemented and validated, the intended result is one assistant that understands the owner's goal, acts through the existing tools and computer interface, provides current project insight, speaks locally, supervises work, recovers from known problems, and develops a tested library of better workflows under the owner's control.

It is not "an agent that never fails." It is a system that makes failures visible, limits their consequences, preserves useful evidence, and accepts improvements only when they survive appropriate tests.

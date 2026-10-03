# Phase 1 target architecture and contracts

Status: **PROPOSED**, not implemented. Baseline `Shotlin/personal-assistant@58dac9c88018674c2e780086953902f1ea135308`. Contract namespace `jarvis.v1`. Phase 1 means the new Jarvis foundation, not the older repository documents' “Phase 1”.

## 1. Decision and alternatives

Add mission management inside the existing Python sani-core process. Keep the Rust host responsible for UI, audio, process/driver lifecycle and physical stop. Keep one Deep Agent as mission reasoning and one Velo/JEV/CUA path as bounded execution. Keep SQLite; no server, external queue, replacement shell or new model/provider.

| Decision | Alternatives considered | Selected approach and reason |
|---|---|---|
| Mission ownership | Expand Velo into long-lived planner; separate new agent/service; Python mission manager above Velo | Manager above Velo preserves recipes and makes mission state independent of model context. No duplicate expensive brain. |
| Persistence | Reuse chat checkpoints alone; new DB/queue; additive tables in sani.db | New mission tables in current SQLite with existing run/action ledger integration. Checkpoints are reasoning continuity, not execution authority. |
| Deep Controller | Raw CUA loop on every step; second planning model; role-scoped existing Deep Agent | Existing model/agent with validated planning/review tools; dispatch deterministic between semantic steps. |
| Executor | Rewrite JEV into text generator; send full goal to Velo.run repeatedly; dedicated bounded execute_work_item | Reuse recipes/adapter/JEV, suppress nested Deep fallback inside a packet. |
| Scheduling | TTL DB lease alone; multiple CUA sessions; host generation plus single core queue/session manager | Preserve process-local serialization, add generation checks and fail-closed lifecycle. TTL only diagnostics. |
| Speech | Embed synthesis in STT worker; Rust-native new engine; isolated local Python output worker | Separate worker avoids STT dependency/cancellation/resource coupling and fits existing packaging. Select one engine after audition. |
| Observer | Autonomous LLM; in-process unrestricted plugin; deterministic consumer of redacted read-only trace | No tools/model/production writer in Phase 1; recommendations in dedicated file/store. |

## 2. Ownership and sequence

```mermaid
sequenceDiagram
 participant U as Voice/Text owner
 participant H as Rust host
 participant M as MissionService
 participant D as Existing Deep Agent
 participant V as Velo bounded executor
 participant C as Policy and CUA
 participant S as SQLite/evidence
 U->>H: Finalized request (stable request_id)
 H->>M: run.start + request identity
 M->>S: Idempotent request claim
 alt exact allowed local command
 M->>M: One-step fast mission, deterministic criteria
 else broader goal or information request
 M->>D: Plan/answer with scoped context, no raw desktop mutation
 D->>M: Validated PlanProposal or informational reply
 M->>S: Commit plan and criteria
 end
 loop next dependency-ready semantic step
 M->>S: Reserve attempt, authority and budget
 M->>V: BoundedWorkItem v1
 V->>C: Local observe / candidate decision / recipe / verify
 C->>S: Intent before effect; observed outcome after
 V->>M: StepResult with evidence
 M->>S: Atomic result/checkpoint/event
 opt meaningful exception
 M->>D: Compact ExceptionPacket
 D->>M: Validated revision or ask/block decision
 end
 end
 M->>D: Final evidence review for nontrivial mission
 M->>M: Deterministic acceptance gate
 M->>S: COMPLETED only with required checks
 M->>H: mission snapshot + verified report
 H->>U: Text + optional local speech
```

Deep decides strategy, dependencies, non-routine recovery and final explanation. It proposes, never grants scope. MissionService owns state transitions, budgets, claims and dispatch. Velo owns short observe/decide/act/check units. JEV chooses only mechanically available candidate IDs. CUA policy and native driver authorize actual action primitives. Observer consumes sanitized evidence; it has no route back into dispatch.

Known `open Chrome` remains zero Deep/zero JEV calls in selected Velo mode. It gains a small durable one-step mission, without a planning model round trip. Explicit Deep selection remains visible and honoured; it uses the same mission authority rather than bypassing it. Pure information requests use Deep without probing CUA unnecessarily. No heuristic app-name occurrence alone may turn a question or quoted instruction into an action.

## 3. Compatibility boundary and source changes

`core/app.py` keeps `run.start`, `run.cancel`, `agents.list`, `system.status` and existing `agent.*` frames. Registry IDs `velo`/`deep` remain. `run.start` adds optional `protocol_version`, `request_id`, `input_origin`, `input_revision`, `mission_id`; the host sends stable message ID as request_id for new peers. Legacy clients get their existing result shape plus optional mission metadata. Old completion flags are turn delivery status only; new clients must render `mission_status` separately.

`agents.list` adds `protocol_version: 2` and `features: ["missions.v1"]`; clients negotiate, unknown peers use v1. No mixed peer may silently execute a v2-only mission. New methods: `mission.get`, `mission.list`, `mission.control`, `mission.approve`, `mission.events`. Unknown fields in security-sensitive records reject; additive event metadata can be ignored only by version-compatible viewers. IPC stays private framed stdio, max 1 MiB. No HTTP server.

The existing Rust stream reader currently waits on one operation guard. Add routing for control responses during run streaming without a second unsynchronized reader/writer. Keep `RunControl` out-of-band local cancellation: emergency stop must not wait on the operation mutex or LLM. Status/control requests authenticate the same owner/conversation binding as submit; owner_id in a wire payload is validated against trusted host identity rather than accepted from renderer/model. Reads cannot enumerate another scope. Long waits checkpoint and finish the current run; they do not occupy the 13-minute core stream. `mission.events` reads from durable sequence after reconnect. No promise to monitor while app/authorized runtime is closed.

### Allowed source responsibilities

All paths are relative to the verified root. **NEW** means absent at audit.

| Existing path(s) | Current → planned responsibility |
|---|---|
| `src/assistant/core/app.py`, `protocol.py` | turn IPC → optional mission IPC, per-run cancellation and event delivery |
| `src/assistant/core/agents.py`, `runtime.py`, `__main__.py` | shared runtime/entries → compose MissionService, scoped Deep/Velo, close resources and redacted logging |
| `src/assistant/agent/build.py`, `context.py`, `system_prompt.py` | single Deep agent/context → role-scoped plan/recovery/review tools; preserve legacy mode and memory |
| `src/assistant/velo/controller.py`, `contracts.py`, `recipes.py`, `adapter.py`, `verify.py` | short routes → bounded work-item entry, typed outcome conversion, strict verification; retain parse/JEV module semantics |
| `src/assistant/tools/policy.py`, `cua.py`, `result_normalizer.py` | existing safety/transport → additive scope/redaction/attempt hook, no mutation replay |
| `src/assistant/runtime/runs_local.py`, `desktop_queue.py`, `session.py` | reusable persistence/serialization → wired durable records and cancellation-safe scheduler bridge |
| `src/assistant/memory/local.py`, `policy.py`, `namespaces.py` | preserve store/secret screening → compatible migration ownership and scoped context tests only |
| `src/assistant/observability/usage.py`, `logging.py`, `timing.py`, `settings.py` | existing helpers/defaults → core telemetry, redaction, opt-in mission config and budgets |
| `sani/src-tauri/src/app_state.rs`, `runtime.rs`, `sani_core.rs`, `main.rs`, `history.rs`, `hotkey.rs` | host UI/IPC/lifecycle → stable IDs, controls, mission projection, interruption |
| `sani/src-tauri/src/speech.rs`, `audio.rs`, `settings.rs`, `setup.rs`, `onboarding.rs` | preserve STT/credentials/setup → TTS interlock/preferences/readiness, no provider changes |
| `sani/src/lib/tauri.ts`, `sani/src/app/MainApp.tsx`, `sani/src/app/PanelApp.tsx`, `sani/src/app/OverlayApp.tsx`, `sani/src/components/MainConversation.tsx`, `sani/src/components/ActivityTimeline.tsx`, `sani/src/app/settings/FullSettings.tsx`, `sani/src/app/settings/SettingsContext.tsx`, `sani/src/app/DiagnosticsPage.tsx` | existing shell → mission/approval/voice controls and honest status |
| `sani/src-tauri/Cargo.toml`, `Cargo.lock`, `tauri.conf.json`, `build.rs`; `sani/scripts/build-core.sh`, `build-sidecar.sh`, `release-mac.sh`; `THIRD_PARTY_NOTICES.md` | packaging only as necessary for selected TTS and host stop bridge, under implementation assignment |
| `README.md`, `CLAUDE.md`, three `src/assistant/skills/*/SKILL.md` | document new ownership and remove stale advice that conflicts with deterministic policy; do not weaken safety |

**NEW Python package:** `src/assistant/missions/{__init__,contracts,store,service,controller,executor,authority,recovery,evidence,observer}.py`. Each has one owner: types, transactions, orchestration, Deep adapter, Velo adapter, scope/budgets, reconciliation, sanitized evidence, read-only detection respectively. **NEW host modules:** `sani/src-tauri/src/{missions,tts,tts_protocol,tts_queue,desktop_control}.rs`; **NEW native bridge** `sani/src-tauri/native/desktop_control.m` only for local human-input stop/yield observation if the installed driver offers no usable signal; **NEW output worker** `sani/src-tauri/python/sani_tts.py`; **NEW build/lock:** `sani/scripts/build-tts.sh`, `sani/tts/pyproject.toml`, `sani/tts/uv.lock`; **NEW UI:** `sani/src/components/MissionStatus.tsx`; tests specified in file 06. No broad source relocation; legacy API/PG files remain compatibility-only. In this table, action is MODIFY narrowly within the named responsibility unless explicitly PRESERVE; NEW paths are additions. PRESERVE `velo/parse.py`, `velo/jev.py`, `agent/profiles.py`, existing STT worker/models and API provider modules, except an evidence-backed compatible fix with its regression. REMOVE: none authorized by this plan.

## 4. Canonical v1 types and validation

Implementation uses strict Pydantic v2 models for IPC/persistence, frozen dataclasses where appropriate internally. All IDs opaque strings generated by trusted application; timestamps UTC epoch milliseconds; durations nonnegative integer milliseconds; money integer micro-units + currency or null. Strings are bounded, Unicode preserved, no NaN/Infinity. `extra="forbid"` for authority/commands. UUID syntax is required for newly generated mission/request/execution/approval IDs. Existing conversation IDs remain opaque for compatibility.

```python
MissionStatus = Literal['PLANNED','RUNNING','WAITING_EXTERNAL','BLOCKED',
 'NEEDS_APPROVAL','PAUSED','VERIFYING','COMPLETED','FAILED','CANCELLED']
StepStatus = Literal['PENDING','RUNNING','SUCCEEDED','FAILED','BLOCKED','CANCELLED','SKIPPED']
ExecutionStatus = Literal['PENDING','RUNNING','COMPLETED','FAILED','BLOCKED',
 'NEEDS_CONTROLLER','NEEDS_HUMAN','CANCELLED']
EffectClass = Literal['READ_ONLY','REPEATABLE_LOCAL','EXTERNAL_WRITE','DESTRUCTIVE']
EffectOutcome = Literal['NOT_ATTEMPTED','CONFIRMED','NO_EFFECT','UNKNOWN']
Origin = Literal['typed_final','voice_final']
ControlKind = Literal['PAUSE','RESUME','CANCEL','REVISE','SET_PRIORITY']
```

| Type | Required fields and semantics |
|---|---|
| `RequestEnvelope` | schema_version=1, request_id, conversation_id, owner_id (host-derived, never model), input_origin:Origin, input_revision:int>=1, text:str<=16000 chars, submitted_at_ms, mission_id:optional. No partial origin accepted. Original text is data; quotations do not authorize. |
| `Scope` | owner_id, project_id nullable, account_ref nullable, workspace_ref nullable, allowed_apps:list[bundle_id], allowed_roots:list[canonical path], allowed_origins:list[exact scheme/host/port], permitted_effects:set[EffectClass], policy_version, scope_hash. Missing identity is UNKNOWN, never wildcard. Empty scope permits no external effect. No secret/cookie fields. |
| `BudgetLimits` | max_wall_ms, max_actions, max_observations, max_screenshots, max_deep_calls, max_jev_calls, max_retries, max_replans, max_no_progress, max_paid_units, max_cost_microunits nullable, currency nullable. All finite nonnegative; absent paid allowance=0. |
| `BudgetUsage` | reserved/consumed counters for every limit, known input/output/cache/reasoning tokens, unknown_usage_calls, known_cost_microunits nullable, external_wait_ms, active_ms. Reservation counts failed/cancelled requests and persists across restart. |
| `EvidenceRef` | evidence_id, mission_id, execution_id nullable, kind, relative_path or inline-safe structured facts, sha256, captured_at_ms, producer/version, sensitivity, redaction_version, redaction_status:SAFE/WITHHELD, expires_at_ms. No raw screenshot/secret permitted through this contract. |
| `ScopeObservation` | app_bundle, pid, window_id, account_ref nullable, workspace_ref nullable, origin nullable, evidence_ids, captured_at_ms, driver_generation, target_version. Must match scope for the requested effect; human attestation is recorded as such. |
| `CheckSpec` | check_id, verifier_id/version from trusted catalog, expected JSON bounded to 4 KiB, target_scope_hash, required:bool. No generated verifier code or unchecked expression execution. |
| `CheckResult` | check_id, passed:bool, evidence_ids, checked_at_ms, verifier_id/version, reason. Executor cannot make arbitrary claims into CheckResults accepted by store. Trusted verifier supplies them. |
| `StepSpec` | step_id, ordinal, objective<=2000 chars, dependencies:list[step_id], executor='velo', recipe_id from catalog or 'semantic_ui', payload_refs, checks:list[CheckSpec], scope:Scope, budget:BudgetLimits, effect_class, escalation_conditions:list[enum], optional:bool. DAG, <=20 steps initially, dependencies resolve and no cycles. |
| `MissionRecord` | schema_version, mission_id, request_id, owner_id, conversation_id, project_id, original_goal, scope, success_criteria:list[CheckSpec], plan_version>=1, control_epoch>=1, status, steps:list[StepSpec], resume_cursor:step_id nullable, budget_limits/usage, approval_ids, artifact_ids, priority 0..9, created_at_ms, updated_at_ms. Goal corrections are append-only revisions; never overwrite original. |
| `BoundedWorkItem` | schema_version=1, mission_id, plan_version, control_epoch, step_id, execution_id, attempt>=1, objective, minimal_context<=4096 UTF-8 bytes, expected_scope, preconditions:list[CheckSpec], allowed_action_scope, recipe_id, payload_refs, expected_postconditions:list[CheckSpec], evidence_requirements, deadline_at_ms, budget:BudgetLimits, effect_class, deduplication_key, escalation_conditions, driver_generation, lease_fence. Total serialized packet <=16 KiB; no backlog/chat history. |
| `StepResult` | schema_version=1, mission_id, plan_version, control_epoch, step_id, execution_id, attempt, status:ExecutionStatus, observed_scope:ScopeObservation nullable, postconditions:list[CheckResult], evidence_ids, artifact_ids, external_operation_ids, failure_category nullable, uncertainty<=512 chars, effect_outcome, elapsed_ms, usage:BudgetUsage, suggested_next_action nullable. Suggestion is data only. |
| `ExceptionPacket` | identity/version/epoch/execution fields from result, category, expected vs observed safe summary, last<=3 safe events, evidence_ids<=5, attempted_recoveries, remaining_budget, unresolved_effects, allowed_decisions. <=8 KiB. No full trace to Deep. |
| `ApprovalRecord` | approval_id, mission_id, plan_version, control_epoch, action_digest, scope_hash, target/account/workspace refs, effect_class, maximum_units, issued_by local owner, issued_at_ms, expires_at_ms, single_use, consumed_at_ms nullable. Minted only by trusted host action; model string 'approved' is invalid. |
| `MissionControl` | control_id, mission_id, expected_plan_version, expected_control_epoch, kind:ControlKind, reason, revision_request nullable, priority nullable. Compare-and-swap; stale commands reject. |
| `WorkerStatus` | worker_id, mission_id, status=WORKING/WAITING_INPUT/WAITING_EXTERNAL/BLOCKED/RATE_LIMITED/CRASHED/COMPLETED/UNKNOWN, evidence_ids, updated_at_ms. Reserved compatibility semantics only; no coding supervisor implemented in P1. |
| `TraceEvent` | schema_version=1, event_id, sequence, trace_id=mission_id, mission_id, plan_version, control_epoch, step_id/execution_id nullable, kind, safe_payload, expected_state/observed_state nullable, evidence_ids, component_versions, skill_versions, occurred_at_ms, previous_hash, event_hash. Kinds include request/plan/action/outcome/correction/recovery/approval/control/budget/verification/observer. |
| `ObserverRecommendation` | recommendation_id, pattern_id, mission_ids, supporting_event_ids, hypothesis, confidence 0..1, scope, created_at_ms; status='OBSERVATION_ONLY'. No executable code, activation token or authority field. |

Payload references point to mission-owned exact user text stored behind evidence/privacy policy, resolved only at dispatch; JEV sees IDs. Payload digest must match, and size is separately capped at 16 KiB per text action. A long coding prompt beyond that cap is deferred to P2 design rather than silently truncated. Identical text with different request_id is a new intentional request. Same request_id+digest returns the existing mission; differing digest is an identity collision. Voice/text equivalence means equivalent intent/scope; do not deduplicate separate deliberate requests merely by text equality.

### Service interfaces (NEW unless identified)

```python
class MissionService:
 async def submit(self, request: RequestEnvelope) -> MissionRecord: ...
 async def control(self, command: MissionControl) -> MissionRecord: ...
 async def get(self, mission_id: str) -> MissionRecord: ...
 async def run_ready(self, mission_id: str, *, cancel: CancellationToken) -> MissionRecord: ...
 async def accept_result(self, result: StepResult) -> Literal['APPLIED','DUPLICATE','STALE']: ...

class DeepController:
 async def plan(self, request: RequestEnvelope, context: ControllerContext) -> PlanProposal: ...
 async def recover(self, mission: MissionSnapshot, exception: ExceptionPacket) -> RecoveryDecision: ...
 async def review(self, mission: MissionSnapshot, checks: list[CheckResult]) -> FinalReview: ...

class VeloExecutor:
 async def execute_work_item(self, item: BoundedWorkItem, *, cancel: CancellationToken) -> StepResult: ...

class MissionStore:
 async def claim_request(self, request: RequestEnvelope, digest: str) -> MissionRecord: ...
 async def commit_plan(self, mission_id: str, expected_version: int, proposal: PlanProposal) -> MissionRecord: ...
 async def claim_step(self, mission_id: str, expected_version: int, expected_epoch: int) -> BoundedWorkItem | None: ...
 async def apply_result(self, result: StepResult) -> Literal['APPLIED','DUPLICATE','STALE']: ...
 async def recover_inflight(self, host_generation: str) -> list[str]: ...

class MissionAuthority:
 async def authorize(self, item: BoundedWorkItem, action: ActionIntent,
                     observed: ScopeObservation) -> ActionPermit: ...
 async def reserve(self, mission_id: str, charge: BudgetCharge) -> BudgetReservation: ...

class EvidenceStore:
 async def put(self, candidate: EvidenceCandidate) -> EvidenceRef: ...
 async def verify(self, check: CheckSpec, item: BoundedWorkItem) -> CheckResult: ...

class Observer:
 async def analyze(self, events: Sequence[TraceEvent]) -> list[ObserverRecommendation]: ...
```

Supporting types: `CancellationToken` is per execution, `is_cancelled:bool`, `epoch:int`, `cancel()` local. `ControllerContext` contains scope, remaining budget, <=8 KiB relevant redacted context and evidence refs. `PlanProposal` contains steps, success_criteria and explanation; it cannot add authority or budgets. `RecoveryDecision` is RETRY_SAFE/REVISE/ASK_OWNER/BLOCK/FAIL plus bounded proposed change and evidence refs. `FinalReview` contains supported summary, acceptance check IDs and unresolved issues; it cannot set terminal status. `MissionSnapshot` is the safe bounded projection of a record. `ActionIntent` contains tool, validated args/digest, target, effect and operation key. `ActionPermit` is opaque host/core-internal single-use authorization bound to execution, lease fence and exact digest; never model-produced. `BudgetCharge/Reservation` identify resource, maximum amount and unique call/action ID. `EvidenceCandidate` contains raw ephemeral data + sensitivity origin, never persisted until sanitization passes. No implementation of Evaluator/ExperimentManager/LineageStore is frozen now; trace provenance makes their later design possible.

## 5. Deep Agent integration without a second loop

Keep `create_deep_agent` in `agent/build.py`. Add role-aware middleware/context for PLAN/RECOVER/REVIEW/CHAT, and trusted tools `submit_mission_plan`, `submit_recovery_decision`, `submit_final_review`. Same configured provider/model and one shared built graph. Role and mission authority travel in invocation-local runtime context (never by mutating a shared graph/tool list); concurrent tests must prove no cross-run role/approval leakage. Mission planning contexts expose relevant read-only knowledge tools and structured submission tools, not raw desktop mutations. Enforce denial at tool invocation as well as model binding. A forged raw CUA call outside dispatcher authority fails even if the tool is registered for legacy mode.

Use distinct graph threads such as `sani:<conversation>:mission:<id>:plan:<version>` so planning does not ingest every old click. Conversation history remains available as bounded selected context. Preserve legacy conversation checkpoints; do not convert them into executable missions. The maximum 2000-token current model output default may require compact planning schemas; do not silently change model/provider. If output truncates, reject the plan and allow one bounded repair inside the Deep budget; no partial plan execution.

Do not repeatedly invoke `VeloEntry.run` on a mission sentence: its C fallback currently calls Deep with raw CUA. Extract reusable routing/execution helpers and add `VeloExecutor.execute_work_item`. Inside it, unavailable recipe/unknown UI returns NEEDS_CONTROLLER; authentication returns NEEDS_HUMAN. JEV unavailable stays a named failure, never provider substitution. The configured disabled-JEV legacy route remains in compatibility mode; mission manager handles such a routing decision explicitly as Deep planning, not a hidden packet fallback.

Phase 1 supports existing recipes (`open_app`, `navigate`, `search_browser`, `scroll`, `type_text`, `press_ordinal`) plus `semantic_ui`: a bounded interpreter over trusted primitive templates (observe target, choose visible element, activate, set exact payload, verify). Build <=8 candidates from fresh AX roles/tokens, expected control purpose and scope; JEV selects ID, cannot invent tools/text. Semantic UI has no arbitrary script, arbitrary generated code, or nested mission planning. If no supported candidate proves intended action, escalate. This provides real multi-step capability without pretending arbitrary Flow/coding-app supervision already exists.

## 6. SQLite ownership and atomicity

Use the existing core `sani.db` for memory/checkpoints and new mission tables; wire reusable `run_registry`/`action_ledger` there without erasing prior data. The Rust UI has a **separate `sani-history.db`**, created in `main.rs`; preserve it. Mission events project into UI history idempotently using event_id/sequence, with no assumed cross-database atomic transaction. Core mission state wins if UI projection is delayed; reconnect replays the outbox. Add namespaced migration ledger `mission_schema_migrations(version, applied_at_ms, checksum)` instead of reusing memory `schema_migrations` or overwriting `PRAGMA user_version`. Revise runs_local setup so its legacy version marker cannot downgrade unrelated schema ownership. Back up through SQLite backup API with application writers quiesced; copying only the main file while WAL is active is not a backup.

New tables:

- `missions`: mission_id PK; UNIQUE(owner_id,request_id); goal/scope/criteria/limits JSON, request_digest, current plan_version/control_epoch/status, timestamps, priority/resume_cursor.
- `mission_plans`: PK(mission_id,plan_version), immutable full plan JSON and digest, reason/evidence, creator version.
- `mission_steps`: PK(mission_id,plan_version,step_id), dependencies/checks/scope JSON, state, active_execution_id, attempt_count; FK plan.
- `mission_attempts`: execution_id PK; UNIQUE(mission_id,plan_version,step_id,attempt); epoch, packet_digest, run_id nullable FK run_registry, dispatch_state, effect_class/outcome, external IDs, results JSON, lease_fence, timestamps.
- `mission_events`: sequence INTEGER PK AUTOINCREMENT, event_id UNIQUE, mission/version/epoch/execution FKs and safe payload/hash-chain; durable event outbox and trace source together.
- `mission_approvals`: approval_id PK, parameter/scope digest/version/epoch/expiry/consumption and owner provenance.
- `mission_budget_reservations`: reservation_id PK, mission/resource/call_key UNIQUE, reserved/consumed units, status; never refund an uncertain paid call.
- `mission_evidence`: evidence_id PK, mission/execution ownership, hash/path/classification/redaction/version/expiry. Artifact references are evidence of kind artifact.

Use one MissionStore-owned connection serialized by an asyncio lock; explicit `BEGIN IMMEDIATE` / COMMIT / ROLLBACK (Python 3.12 sqlite autocommit semantics must be tested). Every read-modify-write affecting plan/epoch/attempt/budget is in one transaction. Configure foreign keys, busy_timeout=5000ms, WAL and FULL synchronous for mission effect-intent commits. Never hold transactions across network/model/GUI awaits. Reuse existing RunActionLedger API with a mission adapter linking its run/action ID to execution_id; add necessary association columns additively. Existing autocommit statements alone do not make a multi-table checkpoint atomic.

`claim_step` validates dependencies, mission/plan/epoch state, reservations and no other active attempt, then records intent before dispatch. `apply_result` CAS-validates execution ownership and epoch/version, unique result digest and verifier evidence; result + step state + resume cursor + usage + event commit atomically. Same result is DUPLICATE; conflicting result for same execution is rejected/audited. Late stale result cannot advance state; unresolved external effects from it are retained in a reconciliation event, not discarded.

## 7. Lifecycle and recovery

| Transition | Required gate/effect |
|---|---|
| PLANNED → RUNNING | Valid DAG, permitted scope, budget, no unresolved earlier effect; step claim |
| RUNNING → WAITING_EXTERNAL | Known external operation recorded; no need to hold desktop; capped read-only poll schedule while runtime runs |
| RUNNING → NEEDS_APPROVAL | Exact pending action digest; stop dispatch; release input lease safely |
| RUNNING → BLOCKED | Unknown account/target, uncertain effect, policy denial, exhausted retry, missing audit/evidence |
| RUNNING → PAUSED | User takeover/pause/sleep; epoch increment, invalidate packets; settle active effect |
| RUNNING → VERIFYING | Required steps succeeded and optional skips explicitly justified |
| VERIFYING → COMPLETED | Every required CheckSpec passes with current scope/provenance; final review has no unresolved acceptance blocker |
| VERIFYING → BLOCKED/FAILED | Missing/contradictory evidence or failed acceptance; never claim success |
| NEEDS_APPROVAL/BLOCKED/WAITING_EXTERNAL/PAUSED → RUNNING | Explicit resume/control or preauthorized bounded safe poll result; revalidate account, permission, lease and unresolved effects |
| Any nonterminal → CANCELLED | Epoch increment/cancel intent durable; no new action; late outcomes reconciled separately |
| Any nonterminal → FAILED | Terminal unrecoverable condition, final bounded failure event |

Terminal missions do not reopen on duplicate run.start. Resuming cancelled work requires a new owner request linked to prior mission, not `SQLiteRunStore.claim`'s legacy failed/cancelled retry semantics. Pause/resume retains plan; revision increments plan_version, records reason and invalidates old approvals/queued packets. Preserve completed-step evidence only after explicitly checking it is still valid under new criteria. Priority only changes queue order among ready missions and cannot preempt a live native action.

Recovery table:

| Failure point | Safe next action |
|---|---|
| Before intent commit | No native effect; retry storage only within limits |
| Intent committed, dispatch not known | Treat potentially external effect UNKNOWN; reconcile before replay |
| Tool timeout/transport loss after submission | Preserve operation ID and intent; inspect actual state; never infer failure from absent ack |
| Step completed, checkpoint response lost | Read stored execution/result; apply idempotently; do not repeat |
| Restart/sleep/driver generation change | Mark active attempts uncertain; epoch increment; new observations; no automatic GUI replay |
| UI loading/harmless popup | Only registered scope-preserving branch, <=2 recovery attempts; account/security dialogs escalate |
| Repeated unchanged/alternating state | 4 no-progress observations or repeated (action,target,digest) pattern trips breaker; finite total unit budget still applies |
| Wrong account/workspace/focus | No payload entry; BLOCKED/NEEDS_HUMAN; no auto account cycling |
| MFA/CAPTCHA/new consent | Human checkpoint; redact auth surface; no bypass |
| Audit storage unavailable | No new mutation, including local fast command; readable blocker, text stays usable |
| Evidence unavailable | Step/mission unverified; never silently replace check with prose |

Read-only actions can retry after reconnect once within budget. Repeatable local actions retry only with fresh preconditions and declared idempotent semantics; text append is not repeatable. External writes require independent reconciliation returning CONFIRMED/NO_EFFECT/UNKNOWN; only proven NO_EFFECT plus valid original allowance permits retry. Destructive effects remain denied in P1. Reverting code never reverses a sent message/payment; external compensation needs separate authority.

## 8. Deterministic scope, budgets and desktop control

Preserve the allowlist, sensitive-target denies, trusted lifecycle-only tools, native selected permission mode and OS permission checks. Add scope enforcement before all observations too: cross-client read data is sensitive even without writes. For P1 generic browser surfaces that cannot prove account/origin, restrict to owner-approved local test fixture or block account-bound actions. Do not treat a window title or arbitrary page text as authenticated identity. Human attestation binds a profile/window plus observations; redirect, account switch, new target or resume invalidates it. It cannot authorize an unknown privileged action.

Default proposed caps (reviewed design targets, not measurements): one unit <=12 mutations, <=40 observations, <=2 screenshots, <=90 s, <=2 recovery attempts, <=2 JEV calls; mission <=20 steps, <=100 mutations, <=300 observations, <=10 screenshots, <=8 Deep requests, <=20 JEV requests, <=2 replans, <=15 minutes active execution. External waits <=30 minutes with a checkpoint then BLOCKED unless owner extends. Deep repair/final review and provider retries count inside 8 requests. Fast command has zero Deep calls. Existing 50 mutation run ceiling remains an additional ceiling, never raised silently. Use the minimum of mission remaining, step allowance, policy and driver limit.

Experiment budgets are exactly zero and no experiment runner exists in P1. Paid external actions are disabled unless exact owner allowance exists; P1 demonstrations use zero paid external generations. Provider calls use current selected APIs and an explicit test call allowance. Dollar hard cap can only be enforced when a conservative cost upper bound exists; otherwise deny dollar-capped unpriced calls or obtain a call/token proxy allowance labelled as such. Never report unknown bill as zero.

Scheduling: one `DesktopQueue` per runtime, one owner through `DesktopSessionManager`. Add context-manager wrapper and correct cancellation-at-grant race before wiring. Host holds a process-lifetime OS lock tied to its private driver/socket. Generation changes only after old core/driver is demonstrably stopped; unclear cleanup blocks takeover. Each action checks mission epoch, active owner, lease_fence, driver_generation and cancel after awaited gates. DB TTL cannot confer authority. Within a process queued missions may run pure isolated read-only non-GUI work; clipboard/browser profile remain shared desktop resources.

Human input default yields automation: listen only to keyboard/mouse activity necessary to detect takeover while executor owns desktop; store event type/time, never keys or unrelated activity. Prefer driver signal if supported; otherwise minimal native event-tap bridge with OS approval and synthetic-event discrimination. Failure to distinguish events safely blocks autonomous run; no guessed suppression. On takeover, stop new dispatch, invalidate queued input and stale paste, release modifiers through verified driver capability or stop owned driver if safe release cannot be proven. Emergency stop is a Rust/native latch, immediate local cancellation plus core reconciliation; it must work during model stalls or IPC loss. No safety claim about driver atomicity until real test proves it.

## 9. Local TTS and preserved speech input

No satisfactory current TTS exists in inspected source. Audition at most Pocket TTS and Kokoro; Pocket is the first experiment due to documented CPU streaming, not a final engine commitment. Exact releases/licences and compatibility evidence are in file 10. No engine/weights installed this run.

Rust `tts.rs` owns one supervised output worker and cpal output playback; `audio.rs` remains mic capture. Worker has a separate pinned environment and no provider keys, core tools, mission DB or network after asset installation. Private framed stdin/stdout protocol; no local HTTP service. EngineAdapter interface: `load(asset_manifest)`, `synthesize(text, utterance_id, generation) -> iterator[PCMChunk]`, `cancel(generation)`, `close()`. Implementation adapter is fixed to the audition winner; do not ship both engines permanently without need.

`TtsRequestV1`: request_id, utterance_id, conversation_id, mission_id nullable, message_id, generation:u64, text<=4000 chars, voice_asset_id, rate (validated range), kind=ACK/STATUS/FINAL. `PcmChunkV1`: utterance_id, generation, sequence, sample_rate, channels=1, format='f32le', pcm_base64<=64KiB, final. Control `cancel(generation)`, `shutdown`; events ready/chunk/finished/cancelled/error. Refuse malformed/oversize/NaN samples, non-monotonic sequence and stale generation; bound decoded buffers to 2 seconds and text queue to 3 utterances. Backpressure prevents memory growth; no audio on IPC stdout used by sani-core.

Queue states DISABLED/LOADING/IDLE/SYNTHESIZING/PLAYING/CANCELLING/ERROR are separate from mission status. `speech.stop` clears queued/current speech and increments generation; does not cancel mission. `mission.pause` pauses work and stale speech; emergency stop stops both. Turning voice output off produces text only, never a cloud fallback.

Only validated acknowledgment/status templates or finalized supported answer segments may be spoken. Do not speak every Deep token/tool preamble or speculative “done”. Sentence chunks may be synthesized incrementally once committed; final completion phrase is released only after mission acceptance. Persist text truth independently of playback failure.

Preserve manual Finish & Send and turn_gen protection. Barge-in first stops playback, flushes/discards stale decoder audio, then permits new capture/interrupt intent through existing finalization gate. During playback normal STT admission is gated. For hands-free acoustic barge-in, opt-in echo-aware VAD may listen ephemerally solely for interruption; it must pass self-listening fixtures before enablement and cannot submit background speech. PTT/hotkey interruption remains available when acoustic detection is unavailable. Mission correction while Working must call pause/control, not bypass one-turn admission.

Audition corpus and release tests require technical names, INR, dates/paths, cold/warm, real-time factor, output-device swap, cancellation during model load/chunk/playback, long reply, STT+CUA contention and offline network monitoring. No engine ships until selected asset hashes, licence notices, voice rights and packaging/offline tests pass. Asset missing/corrupt or incompatible machine gives clear text fallback; voice acceptance remains BLOCKED, not passed.

## 10. Evidence and read-only Observer

Sanitize before disk, database, logs, model egress, UI diagnostics and observer input. Regex screening remains useful for text but cannot prove a screenshot secret-free. Window-scoped AX first; classify auth/password/unknown-private surfaces and withhold images by default. If an image is necessary, redact sensitive regions in memory using a trusted local sanitization path, fail closed on uncertain detection, then hash/store permitted result. No raw screenshot temp file; amend driver screenshot_out_file use so raw pixels never reach retained learning evidence. Subjective image safety is not an LLM-only gate.

Trace includes request/plan/version, dispatch/effect, correction, success/failure, expected/observed state, suspected cause with confidence (not asserted root cause), recovery attempts/results, unknowns, costs, latencies and component/skill hashes. Use event schema and hash chaining to detect modification; same-user/root tampering is out of threat-model protection and must not be advertised as cryptographic immutability. Limit standard metadata to 30 days and redacted screenshots to 7 days by proposed default; delete only expired owned evidence, preserving explicit active investigation holds and a deletion tombstone. User deletion also covers orphaned assets and observer copies.

P1 Observer has only a read-only event export/DB view and append-only recommendation writer under a separate path, no Python execution tool or arbitrary plugin API. Deterministic repeated-failure/no-progress/unsupported-completion rules; at least two occurrences for repeated-pattern label. A single high-impact failure may generate an explicitly single-instance recommendation. Snapshot production config/code/skills hashes before/after hostile recommendation fixtures. Runtime breaker belongs to MissionService; observer cannot change execution thresholds or pause a mission independently. No experiment, candidate activation, promotion or model analysis loop in P1.

## 11. Rollout and rollback

Proposed flags: `JARVIS_MISSIONS_ENABLED=false`, `SANI_TTS_ENABLED=false`; immutable `RSI_MODE=observation_only` with experiment execution absent. Do not switch selected APIs or driver mode. Enable flags only in isolated acceptance instance initially. Version/config handshake permits old host/core to reject new feature mode safely. Preserve all old messages/checkpoints; never infer old completed turns were verified missions.

Flags are host-controlled startup configuration passed through the existing private child environment; no model/tool can write them. Before rollback, pause/cancel and reconcile active/uncertain attempts. Turn flags off only after safe quiescence; never fall back to legacy execution to bypass a new scope denial. Leave new tables readable and preserve evidence; avoid down-migration/data deletion. Restore version-matched host/core/voice bundle and backed-up settings; retain known-good STT worker. DB backup restore is an explicit recovery decision after checking whether it would lose later work. Restore data only when safe, not automatically on startup failure.

Phase 1 exits only with file 06's evidence. Full coding supervision, Obsidian, client reports, Flow production work and candidate promotion remain future fresh plans.


## 12. Additional normative details for implementation

- `allowed_action_scope` is a strict record `{tool_ids:list[str], target_scope_hash:str, payload_digests:list[str], permitted_effects:list[EffectClass]}` derived from StepSpec and current policy. Tool IDs come only from the verified inventory; wildcard tool IDs reject. `evidence_requirements` is `{required_check_ids:list[str], required_artifact_kinds:list[str], max_age_ms:int, allow_sanitized_image:bool}`. A controller cannot lower a trusted verifier's required evidence/freshness.
- `failure_category`/`escalation_conditions` use the catalog `SCOPE_MISMATCH`, `AUTH_REQUIRED`, `PERMISSION_DENIED`, `APPROVAL_REQUIRED`, `UNSUPPORTED_ACTION`, `STALE_TARGET`, `NO_PROGRESS`, `BUDGET_EXHAUSTED`, `DEADLINE`, `TRANSPORT_LOST`, `UNKNOWN_EFFECT`, `VERIFICATION_FAILED`, `STORAGE_UNAVAILABLE`, `USER_TAKEOVER`, `CANCELLED`. Unknown categories reject on commands and are preserved as bounded UNKNOWN diagnostic data on forward-compatible event viewing only.
- In StepResult, COMPLETED means the unit's registered postconditions pass, not the whole mission. CONFIRMED means independently observed effect, never merely successful transport; a confirmed but wrong effect still fails the unit. READ_ONLY uses NOT_ATTEMPTED for mutation outcome while reporting completed verification separately. Failure to persist an observed result leaves the durable intent uncertain.
- Step lifecycle is PENDING→RUNNING→SUCCEEDED/FAILED/BLOCKED/CANCELLED. Optional PENDING→SKIPPED requires a plan-recorded rationale; required steps cannot be skipped into mission completion. Retry creates a new attempt, never erases old results. NEEDS_CONTROLLER becomes a blocked unit plus compact exception; NEEDS_HUMAN becomes an explicit owner checkpoint. Keep mission transition policy in one service function.
- Polling known external work uses 2/5/10/20/30-second capped backoff plus provider Retry-After where supplied; no less than Retry-After, all observations/calls metered. Persist next_poll_at and release desktop between polls. After the 30-minute proposed wait ceiling, BLOCKED pending owner decision. No background scheduler is added that continues after the authorized app/runtime exits.
- Track active duration with monotonic time while running and persisted conservative elapsed checkpoints; UTC is for audit/deadlines. After reboot/clock uncertainty never refill budgets or extend a deadline automatically. A stale attempt is uncertain, even if its old deadline elapsed. Unit90s and mission15min active are additional limits to existing core/run ceilings.
- Host pause/cancel increments its local stop generation immediately before waiting for DB/core acknowledgement. Durable cancellation still records intent as soon as core is reachable; the UI distinguishes locally stopped, awaiting reconciliation and fully settled. Never claim an already submitted native action has been undone.
- TTS cancellation is cooperative first with a bounded worker timeout (proposed 250ms); then terminate only the owned output worker, discard its generation, and keep input/core alive. Cold model load cannot prevent host playback stop. Model thread limits and fixed buffer budgets are measured under contention before choosing defaults.
- Host UI history is a projection, LangGraph checkpoints are reasoning continuity, Obsidian is future knowledge, and `missions`/attempts/events in core SQLite are execution truth. None of the first three may authorize replay or declare mission acceptance.

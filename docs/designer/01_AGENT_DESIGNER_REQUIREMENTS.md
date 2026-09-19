# Agent Designer - Requirements and Architecture

**Version:** 1.0 | **Research date:** 2026-09-18
**Companions:** `02_AGENT_DESIGNER_IMPLEMENTATION_PLAN.md` and `03_AGENT_DESIGNER_VISUAL_DESIGN.md`
**Status:** researched specification, not an implemented or tested release.

## 1. Goal and decision record

Create a visual design and monitoring space for Vion and multiple other independently configured agents. Reuse Open WebUI for chat and existing Workspace resources. Dragging connections must configure real agent capabilities, not just draw a diagram.

The attached brief supplies the component library, central canvas, properties panel, node types, and resource-reuse requirements. Later conversation decisions refine it as follows:

- Build an independent React application at the existing gateway's `/designer/` route. Do not vendor or fork Open WebUI, access its database directly, or pretend this is already a native Workspace tab. Provide an Open WebUI return link and a bookmarkable Designer URL. A native navigation entry is an optional, separately verified integration.
- Support **many independent single-agent runtimes**, not agent-to-agent dispatch. Each compiled Deep Agent retains the no-subagent and no-unrestricted-host-shell boundary.
- Use **Save Draft -> Validate -> Activate**. Saving does not change production behavior.
- Add **Live** monitoring for runs started in Open WebUI, not only runs started from the Designer.
- Use a monochrome interface with restrained blue, green, amber and red status accents. Hide React Flow's visible attribution through its documented option; preserve license notices.

The latest user instructions and this requirements document take precedence over examples in the older attachments. The implementation plan defines order; the visual document defines presentation. Conflicts must be reported, not silently resolved by dropping requirements.

## 2. Evidence and limits

The repository baseline inspected is `Shotlin/personal-assistant` commit **`2d6d1a3460ab9d8de31c631d38512521dd4b9ea0`**. Its dependency file pins Python 3.12, Deep Agents 0.7.15, `langchain-openrouter` 0.2.8, MCP adapters 0.3.2 and PostgreSQL checkpoints 3.1.2. Its `CLAUDE.md` describes bounded CUA, developer-owned skills, existing latency work, and the single-agent security invariant. The current run registry and exact-command router are existing code, not features to rebuild. [S1-S4]

This is a read-only source review. No local Mac permissions, provider account, production database, installed plugin, browser automation, or performance result was tested here. Source pins are an integration baseline, not a claim that these are the newest or vulnerability-free releases. Preserve working lockfiles and record any deliberate upgrade.

Current official documentation confirms React Flow's editor primitives, Open WebUI's Workspace/Skills concepts, and LangChain's MCP and streaming mechanisms. It does not establish that every external resource can be executed by this gateway without an adapter. [S5-S11]

## 3. Delivery scope

### Included

**R01.** Create, rename, duplicate, archive and switch between agents. Agent duplication copies configuration references, never secrets, conversations or personal memory.

**R02.** Searchable components, drag/drop and click-to-add, node properties, typed connections, reconnect/delete, zoom/pan/fit, minimap, snapping, undo/redo, persisted viewport and draft recovery.

**R03.** Model/API configuration; editable prompts and skills; memory and context controls; CUA; existing registered tools; new approved MCP connections; existing Open WebUI knowledge. Every activatable node has a working runtime adapter.

**R04.** Real-time, read-only execution visualization with a timeline, errors, tool/model activity, context usage, cancellation and history.

**R05.** Versioned configuration, validation, safe activation, rollback, emergency capability revocation, resource health and dependency-change alerts.

### Deliberately not included

Cross-agent delegation, self-installing plugins, arbitrary generated-code execution on the host, universal compatibility with every Open WebUI Python plugin, a new document ingestion/vector service, collaborative real-time editing, a custom chat product, and a full native Open WebUI Workspace rewrite. Unsupported integrations remain explicitly unavailable and cannot be activated. They are not counted as completed features.

## 4. Technology choices

| Layer | Choice | How it is used |
|---|---|---|
| Existing backend | Python 3.12, FastAPI, Pydantic, psycopg, httpx | Extend current modules and gateway; no replacement service |
| Agent runtime | Existing locked Deep Agents/LangGraph stack | Compile one isolated runtime per activated agent/security scope |
| Designer | React, TypeScript, React Router, Vite | Static SPA built into the gateway's `/designer/` route |
| Canvas | `@xyflow/react` | Custom nodes/handles, edges, Controls, MiniMap, Background; not an execution engine |
| UI state | `zustand` | Draft graph/selection/history; do not store secrets or duplicate server truth |
| Server state | `@tanstack/react-query` | Catalog, revisions, validation and run-list caching/invalidation |
| UI primitives | Radix React primitives, `lucide-react`, CSS variables | Accessible dialogs/menus and monochrome design, no paid template dependency |
| Editors | CodeMirror 6 Markdown; sanitized preview | Prompt/skill editing; plain text only, no executable skill scripts |
| Security | `cryptography` AES-GCM; OS-protected or external key storage | Encrypt connector credentials; key never stored beside ciphertext in the DB |
| Persistence | Existing PostgreSQL; ordered SQL migrations | Agent definitions, immutable revisions, sessions, connections, audit/events |
| Tests | Existing pytest/Ruff/mypy; Vitest, React Testing Library, Playwright, axe-core | Unit, integration, UI and end-to-end gates |

Use Node.js 22.12+ as the proposed frontend build baseline, consistent with Vite's documented requirements. Resolve compatible stable frontend patches once, commit `package-lock.json`, and use `npm ci` thereafter. Do not invent exact package versions in the implementation. Check peer dependencies and selected package licenses. Existing backend pins remain unless compatibility evidence requires a reviewed update. [S2, S12]

No Redis, Kafka, Kubernetes, paid LangSmith, or additional vector database is required. Preserve the existing PostgreSQL/pgvector installation without introducing a second knowledge store.

## 5. Graph semantics and runtime invariants

**R06.** A graph configures an agent. It is **not** a free-form sequential workflow language. An edge means a resource is attached to a typed Agent port. Execution order is shown in Live's timeline, not inferred from node position.

- Exactly one Agent root and one enabled, connected Model node.
- At most one connected Prompt and Context node; absent nodes use explicit platform defaults displayed in the inspector.
- At most one connected memory node per kind. Many Skill, Tool, MCP and Knowledge nodes are permitted.
- Root-to-resource edges only in V1. No cycles, self-links, agent-to-agent edges, duplicate root/resource/port edges or cross-agent references.
- Disconnected nodes can remain on the canvas but are excluded from compilation. Mark them **Not attached**.
- Disabled resources are omitted from both discovery and execution. Backend validation is authoritative even when a malicious client bypasses React Flow checks.
- Deduplicate a tool exposed through both a server node and a standalone tool node by its canonical connection/tool ID. Reject conflicting overrides.
- Never infer permissions from visual connections. Effective permission is the intersection of platform policy, actor authorization, active configuration, connector policy and current revocations.
- Apply those checks to **every execution path**, including the existing exact-command recipes, compact planner, direct tools, MCP and CUA. Disconnecting CUA cannot be bypassed by a fast-path Chrome recipe.

Store graph presentation separately from its behavioral configuration. Node coordinates, viewport and selection do not change the behavioral hash or require rebuilding a runtime.

## 6. Node behavior

| Node | Editable properties | Required effect |
|---|---|---|
| Model | Provider, model ID, approved endpoint, credential reference, supported parameters, timeout, retries | Build the selected adapter; reject unsupported parameters, self-recursive gateway endpoints and failed capability checks |
| Prompt | Resource reference or inline agent-specific instructions, variable bindings, version | Combine with an immutable safety prefix; unresolved template variables block activation |
| Skill | Source, ID, version/hash, enabled state | Mount only attached instructions in a read-only per-revision view; lazy loading is allowed only within that set |
| Memory | Thread/user/project kind, read/write, scope reference, retention, context allowance | Change actual model access and writes; disabling a node does not delete stored records |
| Context | Recent complete turns, input/output allowance, skill loading, tool exposure, observation policy, compaction, run limits | Enforce limits before each call and in every route, not merely display estimates |
| MCP | Registered server revision, transport, selected tool IDs, permission profile | Discover/probe through authorized connectors; expose exactly the approved selected schemas |
| Tool | Registered operation, restrictions, timeout, output limit | Execute through its verified native/MCP/OpenAPI adapter; no arbitrary function evaluation from graph JSON |
| CUA | Approved host profile, app/action subset, observation mode, cursor, limits | Narrow the existing bounded driver; cannot widen its native manifest from an agent node |
| Knowledge | Open WebUI source ID, retrieval/search-only/full-context mode, result limit | Retrieve authorized evidence through a tested adapter, with source references and prompt budgets |

A Terminal tool is a **sandboxed registered capability**, not the excluded Deep Agents host `execute` tool. It requires a tested external sandbox/Open Terminal/MCP adapter. The existing CUA route through Terminal.app is separately marked **Host desktop access**; a process allowlist does not prevent dangerous text being typed into a shell.

Connecting a plugin does not magically make an unsupported protocol work. The UI must distinguish `available`, `needs setup`, `compatible`, `offline`, `schema changed`, `blocked` and `not supported`.

## 7. Resource ownership and reuse

**R07.** Open WebUI remains the authoring owner for its Models, Prompts, Skills, Knowledge and registered tool metadata. The Designer holds source references, overrides and revision locks, not a competing editable copy of those collections.

| Resource | Ownership and integration rule |
|---|---|
| Open WebUI model preset | Read metadata through the adapter. Resolve its base model to an approved gateway connection. Present inherited prompt/skill/tool/knowledge bindings before importing them as explicit graph attachments. Do not silently execute an entire preset behind the graph. |
| Custom model/API | Gateway-owned connection descriptor plus credential reference. Support existing OpenRouter/OpenAI-compatible/OpenAI adapters. Local servers qualify only when they pass the same compatibility tests. |
| Open WebUI prompts and skills | List/read/create/update through the user's authorized Open WebUI API. Save in that source. Surface write conflicts; do not overwrite blindly. |
| Existing gateway skills | Read-only built-ins. Offer an explicit copy into an Open WebUI-owned custom skill for editing. Never write into application source. |
| Knowledge | Reuse Open WebUI storage and retrieval. Do not directly query its internal vector tables. Metadata listing alone is not retrieval. |
| External MCP/OpenAPI tools | Reuse a source descriptor where exposed, with owner confirmation and a gateway credential binding. Importing metadata is not authorization. |
| Native Open WebUI Python tools | List with an honest compatibility badge. Do not copy/evaluate their Python inside the gateway. Unsupported native execution requires a separately reviewed bridge, not a fake tool node. |
| Agent definitions, memory policies, connections and revisions | Gateway-owned, since they configure this Deep Agent platform. |

Immutable runtime snapshots of permitted prompt/skill text are allowed for reproducibility; they are caches with provenance, not another authoring source. Source edits create a **Dependency update available** state. They do not silently modify active runs. Deletion, deactivation or lost authorization revokes use even if an old snapshot exists.

Open WebUI resource API contracts are version-sensitive. Contract-test the deployed version. The pinned source contains a Skills router and forms, including permission checks; a list response is not assumed to contain full skill content. [S6, S13]

**R08.** The gateway owns model execution for Designer-managed agents. Open WebUI supplies chat UI/identity, not a second autonomous tool loop. Disable automatic tool/skill/knowledge injection for these backend-managed model entries unless the adapter can account for it explicitly. Otherwise disconnected resources may still arrive inside upstream prompts. Preserve user-selected attachments as labeled input, not authorization.

## 8. Authentication and credentials

**R09.** Do not give the browser the service gateway key, OpenRouter key, another user's Open WebUI key or an administrator's global resource access.

For the initial local deployment, use a one-time user connection flow: the user submits their own Open WebUI credential to the gateway, which verifies identity server-side and creates an opaque Designer session. Store the upstream credential encrypted. Return only an HttpOnly session cookie and CSRF token. Clear the entered credential from browser state immediately. Open WebUI's session-user handler exists at the pinned source; its returned token must be stripped from any forwarded response. [S14]

- Use a distinct cookie name and `/designer/` path; do not reuse Open WebUI's cookie or read its local storage. Different ports are different origins, not automatic single sign-on.
- Require CSRF/Origin checks for mutations. Use Secure cookies on HTTPS deployments; any HTTP exception is explicitly loopback-only development.
- The server-to-server Open WebUI chat connection authenticates separately using the gateway secret. Trust forwarded identity only on this configured connection, never because a browser supplied the same header name.
- Resolve actor identity and role on the server. Authorize every agent/resource/event read and mutation. Initial sharing is owner-only; administrators must have explicit policy authority, not merely know an agent ID.
- Resource reads use the requesting user's upstream access. A shared admin token must never turn private Workspace content into a global catalog.
- Custom endpoints are operator-approved. Block metadata/link-local/private-network targets by default; allow an exact local model endpoint only by explicit policy. Recheck redirects and resolved targets; never forward one connector's secret to another host.
- Credentials are write-only inputs. Graph JSON, exports, events and validation errors contain references only. Encrypt with authenticated owner/credential/version binding using the library AESGCM API. Generate a fresh random 96-bit nonce per encryption, enforce uniqueness for a key, and reject authentication-tag failures without fallback. Authorize access before decrypting. Back up key material separately and support rotation/revocation. [S16]

Do not describe API keys as never leaving the process: they are sent to their intended upstream service for authentication. They must not reach the model prompt, Designer response, logs or unrelated servers.

## 9. MCP and capability boundaries

**R10.** Support **stdio and Streamable HTTP** in V1. Legacy SSE may be enabled only as a separately tested compatibility transport. LangChain adapters provide persistent-session mechanisms; stateful CUA must retain its existing long-lived connection behavior. [S7]

- New stdio connections are executable software installation/configuration, not ordinary text settings. Restrict creation/probing to an authorized operator, use an absolute approved executable plus argument array, pin its package/binary, restrict working directory/environment and use `shell=False` semantics.
- No arbitrary `npx latest`, command strings, inherited `.env`, dynamic package installs or agent-authored adapter code.
- Remote connection checks have endpoint allowlists, TLS verification, connection/response limits and sanitized errors. OAuth-only servers remain unavailable until their actual authorization flow is implemented; no pasted browser cookies.
- Discovery returns names, descriptions and JSON schemas. Pin selected tool names and schema digests. New tools default to disabled. Changed or removed selected schemas block activation/use until reviewed.
- Connection testing may start an approved process or make network requests. Label it separately from structural Validate. No silent billable model calls or external mutations during validation.
- A model can select a permitted tool; it cannot add a server, activate a revision or approve its own permissions.

**R11.** Multiple agents may share one physical desktop, but not simultaneous uncontrolled input. Introduce a host-level ownership lock at the actual CUA dispatch boundary. Queue a second run and show **Waiting for desktop**. Cancel/revoke checks precede every native action. Lock expiry alone is not sufficient to authorize a new writer while the old writer may still act.

## 10. Drafts, activation and safe plugin changes

**R12.** Saving appends an immutable draft revision with optimistic concurrency (`If-Match`). Validation records the exact behavioral hash, source revisions, selected tool schemas, policy revision and credential-version references.

Activation is a controlled transaction:

1. Reauthorize the actor and recheck dependencies.
2. Prepare a candidate runtime under bounded resource limits. Probes require prior permission.
3. If preparation fails, close candidate resources and keep the old active revision unchanged.
4. Atomically change the active pointer using compare-and-swap.
5. New runs use the new version. Existing runs finish on their pinned version unless explicitly cancelled or revoked.
6. Drain and close old resources after their last run; never close an MCP transport underneath an active call.

**R13.** Rollback activates a previously validated revision only after current access/policy checks. Keep immutable history and record a new activation event. No destructive rewrite of past revisions.

**R14.** **Disable immediately** is separate from ordinary draft disconnect. It updates a live revocation gate, removes the capability from subsequent model exposure, prevents queued/new calls and cancels controllable in-flight work. Already performed side effects are reported; cancellation is not an undo.

On connector/skill/plugin changes, show an impact preview, validate, prepare and activate or retain the previous version. There is no guarantee that arbitrary third-party changes will work. Safety means failed changes do not silently break the last working configuration or expand permissions.

## 11. Memory and context

**R15.** Use server-derived scope `(owner_user_id, agent_id, project_id when authorized)` and per-chat execution epochs. No anonymous fallback namespace. Shared project memory requires an explicit shared resource and membership check.

Disconnecting memory disables its read/write tools and injection, without deleting records. Disabling thread recall does not disable essential run ledgers/checkpoints: those are reliability records, not optional personal memory. Task Memory is represented by this existing task/checkpoint state and shown as a read-only system resource, not a second datastore. Knowledge Memory maps to the Knowledge node; independent new vector-memory backends remain outside V1.

On a behavior-changing activation, start a fresh execution epoch for subsequent turns. Do not reuse old hidden prompts, cached skill bodies, tool messages or revoked knowledge in the new model context. Retain old transcripts for history, subject to access policy. Carry forward only the current user request and an explicit permitted handoff; ask for clarification when necessary rather than reconstructing forbidden context. A UI toggle cannot make a model forget information already processed by an in-flight call.

**R16.** Context controls affect real requests. Initial defaults, adjustable within operator ceilings: 12 complete recent turns, 32,000 estimated input tokens, 2,048 output-token cap, 16 model attempts, 60 external tool calls and a 15-minute run limit. These are proposed defaults, not model limits or measured optimal values. Imported Vion retains its reviewed existing defaults until changed.

Tool exposure has three explicit modes: `selected` exposes selected approved schemas, `lazy` discovers only within the selected approved set, and `none` denies optional tool dispatch as well as omitting schemas. Permission/safety checks themselves cannot be disconnected. Preserve assistant/tool-message pairing when trimming. Keep exact evidence outside the prompt. Use selected-source retrieval, lazy skill loading, bounded observations and compaction before context overflow. A missing Context node uses the same safe default policy, not unlimited input.

Estimates identify tokenizer/method and uncertainty. Exact billing/usage comes from available provider metadata, aggregated over all attempts. Unknown usage/cost is **unknown**, not zero. `max_tokens` is per-call; additionally enforce run-level call/input/image/output budgets. A cost ceiling is conservative and approximate when provider pricing or usage is incomplete. Do not promise exact spend control across unknown pricing.

## 12. Live monitoring

**R17.** Every run, including chat-originated and fast-path runs, pins `agent_id`, `revision_id`, `runtime_id`, `request_id` and its exact graph snapshot. An authorized viewer can open that run in Live without starting another model request.

Use one normalized event stream backed by PostgreSQL. Send SSE from `/designer/api/v1/runs/{run_id}/events`, authenticated by the Designer cookie. Event IDs are monotonic within a run; support `Last-Event-ID`, gap recovery and a latest-state snapshot. Disconnecting a monitoring tab must not restart or cancel the underlying run.

Event envelope (new application contract):

```json
{"schema_version":1,"run_id":"run-example","seq":12,"agent_id":"agent-example","revision_id":"revision-example","event":"tool.started","node_id":"cua-node","operation_id":"operation-example","at":"2026-09-18T12:00:00Z","data":{"tool":"get_window_state","status":"running"}}
```

Events include run accepted/started/finished, model started/finished, tool started/finished/failed, context prepared/compacted, skill loaded, memory read/written, knowledge retrieved, approval requested, run paused/cancelled and usage updated. Emit actual events only; a failure cannot become green success because final text sounds confident. Mark requested, dispatched, returned and independently verified outcomes separately.

**R18.** Show selected node, current operation, elapsed time, observed model/tool duration, input/output/cached token counts when available, and an ordered timeline. Highlight only the exact node/edge mapped by the compiler. Configuration edges remain resource bindings; do not animate a fictional execution order.

Never expose hidden chain-of-thought, raw credentials, full prompt contents, unredacted tool payloads or automatic screenshots. A trace detail view may expose separately authorized evidence. Default event retention is seven days, configurable; keep the security audit retention policy separate.

## 13. Gateway and persistence contracts

**R19.** Preserve `/v1/models` and `/v1/chat/completions`. List only agents the authenticated chat actor may use. Resolve model aliases to an active AgentDefinition; disabled/unpublished agents return a controlled error. Prevent model connections pointing back to these aliases and causing recursion.

The following are **new gateway endpoints**, not claims about existing Open WebUI endpoints:

| Endpoint group under `/designer/api/v1` | Purpose |
|---|---|
| `POST /session`, `GET /session`, `DELETE /session` | Connect, inspect and revoke the Designer session |
| `GET/POST /agents`, `GET/PATCH /agents/{id}` | Catalog and metadata; not live graph replacement |
| `POST /agents/{id}/revisions`, `GET /agents/{id}/revisions` | Save/list immutable drafts with expected revision |
| `POST /agents/{id}/validate`, `/activate`, `/rollback` | Explicit lifecycle transitions |
| `GET /catalog?kind=...`, `GET/PATCH/POST /resources/...` | Authorized normalized source adapters |
| `GET/POST /connections`, `POST /connections/{id}/probe`, `/revoke` | MCP/model/tool connection registry |
| `POST /credentials`, `PUT/DELETE /credentials/{id}` | Write-only secret lifecycle |
| `POST /agents/{id}/context-preview` | Non-billable, clearly estimated context breakdown |
| `GET /agents/{id}/runs`, `GET /runs/{id}`, `GET /runs/{id}/events` | Run history, snapshot and SSE |
| `POST /runs/{id}/pause`, `/resume`, `/cancel` | Authorized task control at safe checkpoints |

**R20.** Extend the existing database with agent metadata/revisions, connector revisions, encrypted credentials, authorized memory-scope records, Designer sessions, append-only run events and a migration ledger. Store graph JSON and dependency locks on revisions. Do not create duplicate editable Open WebUI prompts/skills tables.

Add `agent_id` and pinned `revision_id` to existing run records. Deduplicate using authenticated user, agent, chat and user-message identity; configuration activation is not permission to re-execute a delivered turn. Retries are separate attempts requiring action-ledger reconciliation. Unknown external effects never replay automatically.

## 14. Migration and operations

**R21.** Bootstrap Vion from the actual current configuration, not a blank sample. Preserve its existing `personal-assistant-v1` model alias and original history. New agents get distinct immutable IDs.

Provide a transactional, idempotent migration with a backup/restore rehearsal. Only bootstrapped Vion may resolve legacy `owui:{user}:{chat}` and legacy user-memory namespaces; newly created agents cannot. Prefer an explicit legacy mapping over rewriting opaque LangGraph records. Once a behavior change requires a new epoch, preserve the old history as history rather than injecting it wholesale.

Use real `ALTER TABLE` migrations and replace the legacy dedup index only after backfill/duplicate checks. Record migration checksums. Never reset Open WebUI's database or overwrite `.env` credentials.

**R22.** Retain the one-command start/stop experience. Serve built assets under `/designer/`, support direct route refresh, and avoid intercepting `/v1` APIs with the SPA fallback. The local initial release runs one gateway process and one desktop owner; scale-out requires tested leases/revocation and cannot be enabled by adding workers casually.

## 15. Acceptance gates

Each gate needs executable evidence; a demo animation or generated reply is insufficient.

| Gate | Required result |
|---|---|
| A1 Canvas | Create two agents; drag, connect, edit, zoom, save/reload and undo without losing state |
| A2 Runtime binding | Disconnect CUA, activate, then prove model, recipe, planner and direct dispatch cannot call it |
| A3 Isolation | Same user across two agents and two different users cannot see each other's private memory/resources/runs |
| A4 Lifecycle | Saving does not affect active work; failed activation preserves old version; rollback works after revalidation |
| A5 Resource reuse | A skill created through Designer appears in Open WebUI; its authorized content drives a real agent run |
| A6 Retrieval | A connected Knowledge source supplies cited evidence; disconnected/unauthorized source is not queried |
| A7 Connectors | One real stdio and one test Streamable HTTP server work; changed schema is quarantined; credentials are absent from exports |
| A8 Live | An Open WebUI run updates the exact graph/timeline; disconnect/reconnect monitor resumes without duplicate effects |
| A9 Controls | Cancel/revoke blocks the next tool dispatch; two agents cannot type into the same desktop simultaneously |
| A10 Context | Lower budgets change captured provider requests; usage aggregates all calls; estimates are not labeled billing |
| A11 Migration | Existing Vion chat/memory/alias remain usable; migrations rerun safely; restore rehearsal succeeds |
| A12 Terminal | Only a tested isolated terminal adapter can activate; host-shell and Docker-socket access are denied |
| A13 UI quality | Dark/light views, keyboard paths, status labels, empty/error states and requested attribution behavior verified |
| A14 Release | Baseline regressions, security tests and live permitted tests pass; unverified integrations remain blocked |

## 16. Sources

Sources establish existing mechanisms; all `Rxx` clauses and endpoint designs above are proposed product requirements.

- **S1** [Repository baseline](https://github.com/Shotlin/personal-assistant/tree/2d6d1a3460ab9d8de31c631d38512521dd4b9ea0)
- **S2** [Existing dependency manifest](https://github.com/Shotlin/personal-assistant/blob/2d6d1a3460ab9d8de31c631d38512521dd4b9ea0/pyproject.toml)
- **S3** [Existing CLAUDE.md boundaries](https://github.com/Shotlin/personal-assistant/blob/2d6d1a3460ab9d8de31c631d38512521dd4b9ea0/CLAUDE.md)
- **S4** [Run registry](https://github.com/Shotlin/personal-assistant/blob/2d6d1a3460ab9d8de31c631d38512521dd4b9ea0/src/assistant/runtime/runs.py) and [exact-command router](https://github.com/Shotlin/personal-assistant/blob/2d6d1a3460ab9d8de31c631d38512521dd4b9ea0/src/assistant/runtime/router.py)
- **S5** [React Flow API](https://reactflow.dev/api-reference/react-flow), [drag/drop](https://reactflow.dev/examples/interaction/drag-and-drop), [save/restore](https://reactflow.dev/examples/interaction/save-and-restore)
- **S6** [Open WebUI Workspace](https://docs.openwebui.com/features/workspace/) and [Skills](https://docs.openwebui.com/features/workspace/skills/)
- **S7** [LangChain MCP integration and sessions](https://docs.langchain.com/oss/python/langchain/mcp)
- **S8** [Deep Agents customization](https://docs.langchain.com/oss/python/deepagents/customization)
- **S9** [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
- **S10** [Open WebUI plugin loader, route and security boundaries](https://docs.openwebui.com/features/extensibility/plugin/development/under-the-hood/)
- **S11** [Open WebUI extensibility](https://docs.openwebui.com/features/extensibility/)
- **S12** [Vite setup and Node requirements](https://vite.dev/guide/)
- **S13** [Pinned Skills router](https://github.com/open-webui/open-webui/blob/v0.11.3/backend/open_webui/routers/skills.py) and [forms](https://github.com/open-webui/open-webui/blob/v0.11.3/backend/open_webui/models/skills.py)
- **S14** [Pinned authentication router](https://github.com/open-webui/open-webui/blob/v0.11.3/backend/open_webui/routers/auths.py)
- **S16** [Cryptography authenticated-encryption API](https://cryptography.io/en/latest/hazmat/primitives/aead/)
- **S17** [TanStack Query overview](https://tanstack.com/query/latest/docs/framework/react/overview)
- **S15** [React Flow ProOptions](https://reactflow.dev/api-reference/types/pro-options), [MIT license](https://github.com/xyflow/xyflow/blob/main/LICENSE) and [attribution guidance](https://reactflow.dev/remove-attribution)

**Attribution note:** the React Flow API states that anyone may remove visible attribution, while its separate attribution page uses stronger subscription wording. Use the documented `proOptions.hideAttribution` mechanism with the selected MIT package, retain copyright/license notices, and record this documentation discrepancy in the dependency review. Do not copy paid Pro examples. This instruction concerns React Flow's canvas badge only, not Open WebUI branding or other authors' watermarks.

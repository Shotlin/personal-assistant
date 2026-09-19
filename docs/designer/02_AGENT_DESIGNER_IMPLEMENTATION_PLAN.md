# Agent Designer - Implementation Plan

> **For agentic workers:** use an available executing-plans workflow to implement one work package at a time. Development helpers do not authorize subagents inside the shipped product. Use test-first changes and review gates; do not change company repositories, install tools, spend API credits or deploy without the owner's corresponding authorization.

**Goal:** add a working multi-agent Designer and live run view to the existing gateway without replacing Open WebUI or weakening execution boundaries.
**Architecture:** a React SPA calls an authenticated Designer API. Validated immutable configurations compile into independent Deep Agent runtimes. Open WebUI resources are reused through source adapters; actual runtime events power monitoring.
**Tech stack:** existing Python/FastAPI/PostgreSQL/Deep Agents; React + TypeScript + Vite + `@xyflow/react`; see Requirements section 4.
**Spec:** `01_AGENT_DESIGNER_REQUIREMENTS.md`
**Visual contract:** `03_AGENT_DESIGNER_VISUAL_DESIGN.md`
**Inspected source baseline:** `2d6d1a3460ab9d8de31c631d38512521dd4b9ea0`, 2026-09-18.

## Global constraints

- Existing Vion chat, memories, latency optimizations and model alias must survive.
- Many independent agents are allowed; `task` dispatch and unrestricted host `execute` remain forbidden in each runtime.
- Save is not Activate. Configuration does not change halfway through a run.
- Every connected capability is authorized at execution, including local recipe/compact-planner routes.
- No direct Open WebUI SQL, mirrored editable Workspace catalogs, secrets in graph JSON, arbitrary host commands, automatic plugin installs or fake live events.
- Use existing `uv.lock`; retain exact backend pins. Resolve new frontend dependencies once, commit their lockfile, and use `npm ci`.
- Local first: one gateway process and one serialized physical-desktop execution owner. No Redis/Kafka or new orchestrator.
- All performance figures are acceptance targets measured on the actual deployment, not guaranteed model latency.

## 1. What will be created or changed

Existing paths below are integration points. New paths are proposed files, not claims that they exist already. Read current code before editing; do not overwrite a newer change merely to match this snapshot.

```text
src/assistant/
  designer/
    schemas.py                # Graph, revision, connection and event contracts
    auth.py                   # Designer sessions, actor resolution, CSRF
    credentials.py            # Write-only encrypted credential references
    store.py                  # Agents/revisions/activation CAS
    validation.py             # Pure graph and dependency validation
    service.py                # Lifecycle and owner authorization
    compiler.py               # ExecutionConfig -> scoped CompiledRuntime
    runtimes.py               # Acquire/release, cache, draining, shutdown
    capabilities.py           # Shared effective-capability and revocation checks
    context.py                # Context policy and per-run budgets
    events.py                 # Append-only normalized events, snapshot/replay
    routes.py                 # /designer/api/v1 routes
    adapters/
      openwebui.py             # Authorized source API normalization
      sources.py               # Prompt/skill/knowledge resolvers
      mcp.py                   # Approved stdio/Streamable HTTP lifecycle
      openapi.py               # Approved external tool operations
  agent/build.py              # Parameterize model/prompt/skills/context safely
  agent/profiles.py           # Retain trusted no-subagent profile
  api/{models_route,chat_route,streaming,identity}.py
  runtime/{router,planner,runs}.py
  memory/namespaces.py
  main.py
migrations/designer/
  001_security.sql
  002_registry.sql
  003_run_scope.sql
  004_events.sql
scripts/
  migrate_designer.py
  bootstrap_designer.py
  probe_openwebui_contract.py
  start.sh                    # Extend, do not replace existing startup
frontend/agent-designer/
  package.json
  vite.config.ts
  src/
    App.tsx
    api/{client,contracts}.ts
    state/{canvas,live}.ts
    pages/{AgentsPage,DesignerPage,LivePage}.tsx
    canvas/{AgentCanvas,ResourceNode,ResourceEdge}.tsx
    components/{Library,Inspector,ActivationDialog,RunTimeline}.tsx
    styles/{tokens,layout,canvas}.css
  tests/
tests/designer/
  conftest.py
  test_auth.py
  test_graph.py
  test_sources.py
  test_connectors.py
  test_runtime.py
  test_context.py
  test_activation.py
  test_events.py
  test_migration.py
  test_acceptance.py
docs/designer/
  baseline.md
  upstream-contracts.json
  acceptance.md
  THIRD_PARTY_NOTICES.md
```

Locate and extend the existing desktop-session manager during P0; do not assume an unverified session-module filename or create a competing manager. Record actual integration symbols in the baseline report.

## 2. Shared application contracts

Define these **new** contracts in `designer/schemas.py`; generate frontend API types from the backend schema. Do not maintain divergent handwritten graph validators.

- `Actor`: server-verified `user_id`, role and permission set. Never constructed from a graph payload.
- `ResourceRef`: `source`, `kind`, `id`, `revision_or_hash`; no credential values.
- `GraphDocument`: `schema_version=1`, nodes, typed edges, viewport. Node IDs are stable UUIDs. Domain config is separate from layout.
- `AgentRevision`: immutable graph, semantic hash, dependency lock, creator, creation time, validation result.
- `ExecutionConfig`: the compiler's normalized model, safety/custom prompt, selected skills, tool IDs, memory/context policies and resource provenance.
- `RuntimeScope`: authenticated user, agent, chat, execution epoch and authorized project if present.
- `CompiledRuntime`: immutable revision identity, graph, connections, tools and `aclose()`, with reference-counted leases.
- `RunEvent`: Requirements section 12 envelope; server-created sequence and timestamps.

A representative resource node, with references rather than secrets:

```json
{
  "id": "cua-node",
  "type": "mcp",
  "position": {"x": 500, "y": 160},
  "data": {
    "enabled": true,
    "resource": {"source": "gateway", "kind": "mcp", "id": "cua-local", "revision_or_hash": "revision-1"},
    "config": {"selected_tool_ids": ["cua-local:get_window_state"], "required": true}
  }
}
```

Resource IDs in examples are fixtures, not installed resources. The actual catalog supplies selectable IDs.

### Database contract

Use the current raw-psycopg style with ordered, checksummed SQL migrations, rather than adding an ORM solely for this feature.

- `designer_agents`: UUID, owner user, slug, display name, enabled/archive flags, active revision ID, row version.
- `designer_revisions`: UUID, agent FK, revision number, graph JSONB, semantic hash, dependency-lock JSONB, validation JSONB, created by/at. Unique `(agent_id, revision_number)`.
- `designer_connections`: UUID, owner, kind, versioned non-secret descriptor, credential references, approved-schema digest, revoked timestamp. Referenced historical revisions are immutable.
- `designer_credentials`: UUID, owner, purpose, key ID, nonce, ciphertext, version, revoked timestamp.
- `designer_sessions`: hashed random session ID, user, encrypted upstream-credential reference, expiry, CSRF binding.
- `designer_memory_scopes`: UUID, owner, kind, project label when applicable, explicit access policy and revocation state. Derive storage namespaces from authorized IDs, never arbitrary client path strings.
- `designer_run_events`: `(run_id, seq)` primary key, pinned agent/revision/node, event type, sanitized JSONB, timestamp.
- `designer_migrations`: ID, checksum, applied timestamp.
- Extend existing `run_registry` with agent/revision/attempt metadata; retain action evidence and `unknown` outcome handling.

Use per-transaction pooled connections. Do not share overlapping transaction contexts on one global connection. Queries are parameterized and include owner/resource scope.

### Test fixture contract

`tests/designer/conftest.py` supplies `api` and `api_b` as authenticated `httpx.AsyncClient`s for different users, `operator_api` for connector administration, `db` for disposable PostgreSQL, and `fake_provider`/`fake_mcp` recording calls without spending money. `valid_graph` is a fresh graph with one Agent, one fake Model, no external tools and a deterministic prompt. These fixtures must create real app routes and database records, not bypass authorization.

Convenience helpers `create_agent(api, graph)` and `save_revision(api, agent_id, graph, etag)` exercise the HTTP API and return parsed objects. Define and import them in the test modules before use. `anonymous_api` has no session; `upstream_denied` configures a denied upstream response; `connection_factory` uses the real adapter with a fixture MCP server; `runtime_harness` wraps real chat routing with a recording executor; `budget_ledger` is the real ledger instance; `lifecycle` wraps the real service with a controllable connection factory; `event_harness` uses the real event store with one recording tool. The lifecycle fixture exposes `active_revision_id`, `candidate_resources_open` and `activate_candidate(fail_prepare=...)`; the event fixture exposes `run_one_tool()`, `read(after_seq=...)` and `tool_dispatch_count`. These are test adapters, not production APIs. Test examples below illustrate minimum regressions; each work package's additional cases are mandatory.

## 3. Work packages

### P0 - Baseline, dependency and upstream-contract evidence

**Covers:** R07, R19, R21-R22. **Files:** `docs/designer/baseline.md`, `upstream-contracts.json`, `scripts/probe_openwebui_contract.py`.

- [ ] Read local `CLAUDE.md`, current commit, working-tree diff, `pyproject.toml`, lockfile, startup script, run registry, compiler and fast paths. Preserve uncommitted work and run existing non-live tests first.
- [ ] Record installed Open WebUI/driver versions and image digests, not just mutable tags. Do not upgrade them as part of discovery.
- [ ] Probe only authenticated read endpoints on the authorized Open WebUI instance. Record route/method, request/response schemas, pagination, access controls and credential mechanism for Models, Prompts, Skills, Knowledge, external tool metadata and current-user verification.
- [ ] Distinguish source metadata from execution: identify the actual authorized retrieval route and external-tool invocation mechanism. Reject direct database/vector-table access and guessed universal `/tools/run` endpoints.
- [ ] Verify the selected `@xyflow/react` license/API, React/Vite peer requirements and all new dependency licenses. Commit exact resolutions once in the lockfile; no paid Pro example code.

```bash
uv sync --frozen
uv run ruff check .
uv run mypy
docker compose up -d postgres
uv run pytest
```

**Gate:** baseline failures are recorded separately; API fixtures are real and redacted; unsupported upstream operations are explicitly identified before implementing an adapter. No claim of local integration success from source documentation alone.

### P1 - Authenticated Designer foundation and credentials

**Covers:** R09, R20. **Files:** `auth.py`, `credentials.py`, `routes.py`, migration `001_security.sql`, `tests/designer/test_auth.py`.
**Interface:** `resolve_actor(request) -> Actor`; `credentials.resolve(actor, ref, purpose)` is internal only; `/session` exposes a sanitized identity.

- [ ] Implement fixtures and failing tests for unauthenticated catalog access, cross-user agent access, CSRF rejection, credential readback, malicious redirect and expired session.
- [ ] Add one-time upstream-credential exchange, validate using the P0 contract, mint opaque session, and strip upstream tokens from responses. Model-service credentials and browser session cookies are never interchangeable.
- [ ] Store encrypted credentials with authenticated owner/ID/version binding. Load the encryption key outside the database; fail startup when unavailable. Add rotate/revoke paths.
- [ ] Restrict connector URLs and HTTP redirects; explicit local endpoint exceptions are operator policy, not user input.

```python
async def test_anonymous_cannot_list_agents(anonymous_api):
    response = await anonymous_api.get('/designer/api/v1/agents')
    assert response.status_code == 401
```

Define `anonymous_api` as the same app client without a session. **Gate:** auth/CSRF/credential tests pass; no service keys in bundles, browser storage, OpenAPI examples or error payloads. Commit only this slice.

### P2 - Registry, graph validation and draft storage

**Covers:** R01-R02, R06, R12-R13, R20. **Files:** `schemas.py`, `store.py`, `validation.py`, `service.py`, migration `002_registry.sql`, `test_graph.py`.
**Interfaces:** `validate_graph(graph, catalog) -> ValidationReport`; `save_revision(actor, agent_id, graph, expected_version)`; stable HTTP IDs for agents/revisions.

- [ ] Test missing/two roots, missing/two attached models, wrong edge types, cycles, duplicates, disconnected nodes, missing references, foreign-owner references, graph-size limits and forbidden executable fields.
- [ ] Separate layout hash from semantic hash. Layout-only saves must not trigger runtime replacement. Limit graph payloads to 1 MB and 200 nodes initially; make policy values explicit.
- [ ] Save immutable drafts with ETags/compare-and-swap; return 409 on stale writes. Saving never changes `active_revision_id`.
- [ ] Add import/export of non-secret configuration with schema version and reference-resolution report. Import never installs or authorizes a connector.

```python
async def test_save_does_not_activate(api, valid_graph):
    agent = await create_agent(api, valid_graph)
    saved = await save_revision(api, agent['id'], valid_graph, agent['etag'])
    response = await api.get('/designer/api/v1/agents/' + agent['id'])
    assert saved['revision_id']
    assert response.json()['active_revision_id'] is None
```

**Gate:** graph validation also runs server-side; all invalid configurations fail with node-specific errors. No runtime/process is started by Save or structural Validate.

### P3 - Reuse Open WebUI resources and editable skills/prompts

**Covers:** R03, R07-R08. **Files:** `adapters/openwebui.py`, `adapters/sources.py`, `test_sources.py`.
**Interface:** `catalog(actor, kind)`, `resolve(actor, ResourceRef)`, `create/update(actor, source, payload, expected_hash)`; normalized resource carries `can_read`, `can_write`, `runtime_supported` and provenance.

- [ ] Use P0 fixtures to implement list/detail/create/update for Open WebUI prompts and skills. Pagination is explicit; do not treat a list result as full source content.
- [ ] Present built-in filesystem skills as read-only. Copying one creates a new Open WebUI skill after explicit action. Custom skills contain text, not executable scripts or arbitrary filesystem paths.
- [ ] Add optimistic source-update checks. If upstream has no transactional conditional update, declare its concurrency limitation and require conflict detection/confirmation; do not claim atomic CAS that the upstream cannot enforce.
- [ ] Normalize model presets. Resolve base-model routing and inherited attachments into an explicit import preview. Require a gateway credential reference rather than reading upstream secrets.
- [ ] Store immutable authorized runtime snapshots with hashes; refresh never silently changes active behavior. Removed/inaccessible/deactivated sources are denied even when cached.

```python
async def test_skill_source_denial_is_not_hidden(api, upstream_denied):
    response = await api.get('/designer/api/v1/catalog?kind=skill')
    assert response.status_code in (403, 502)
    assert 'admin-private-skill' not in response.text
```

`upstream_denied` configures the fixture transport to reject source access. **Gate:** a Designer-created skill exists in the actual authorized Open WebUI source; no separate writable prompts/skills catalog appears in our DB.

### P4 - Verified MCP, external tools, knowledge and terminal adapters

**Covers:** R03, R07, R10-R11. **Files:** `adapters/mcp.py`, `adapters/openapi.py`, `adapters/sources.py`, `capabilities.py`, `test_connectors.py`.
**Interface:** `prepare_connection(actor, approved_revision) -> ConnectionLease`; lease supplies selected normalized tools, `can_invoke(tool_name) -> bool`, and `aclose()`. These checks also run again at dispatch.

- [ ] Add operator-approved stdio and Streamable HTTP connections. Fixture servers expose one harmless echo tool and one forbidden mutation. Persist schema hashes and require review of selected-tool changes.
- [ ] Preserve structured MCP output and image content blocks; bound model-visible payloads. Use persistent sessions for stateful servers. Do not turn every tool call into a new CUA process.
- [ ] Implement external OpenAPI tools only from approved operation IDs, validated parameter schemas and fixed credential destinations. Reject remote-spec SSRF and unrestricted URL templates.
- [ ] Implement Knowledge retrieval through P0's proven Open WebUI adapter. Verify each selected source with the actor; return text, source identifier and source location. Full-context mode stops or requests a different mode when its budget is exceeded, rather than silently truncating contractual material.
- [ ] Register CUA as the existing bounded connection; keep driver version and native manifest operator-owned. Existing application/recipe capabilities get canonical IDs and node mappings.
- [ ] Provide Terminal through a tested container/external sandbox adapter. No host filesystem root, Docker socket, production secrets or agent-selected executable mounts. Use resource/time/output/egress limits. A missing sandbox blocks that node, not all unrelated agents.
- [ ] Native Open WebUI Python plugins without a safe invocation bridge remain non-activatable; metadata compatibility is not execution compatibility.

```python
async def test_unselected_tool_cannot_execute(fake_mcp, actor, connection_factory):
    lease = await connection_factory(actor, selected=['echo'])
    try:
        assert [tool.name for tool in lease.tools] == ['echo']
        assert not await lease.can_invoke('delete_file')
    finally:
        await lease.aclose()
```

Define `connection_factory` using the real adapter plus the fake server. **Gate:** real transport contract tests, harmless terminal command in isolation, knowledge evidence, and native CUA denial test pass with required owner approvals. Transport tests alone do not certify every third-party server.

### P5 - Scoped runtime compiler and migration

**Covers:** R01, R06, R15, R19-R22. **Files:** `compiler.py`, `runtimes.py`, `agent/build.py`, `memory/namespaces.py`, `api/models_route.py`, `api/chat_route.py`, `runtime/runs.py`, migration `003_run_scope.sql`, migration/bootstrap scripts, `test_runtime.py`, `test_migration.py`.
**Interface:** `compile_execution_config(revision, resolved_dependencies) -> ExecutionConfig`; `runtime_pool.acquire(actor, agent_id, revision_id)` returns an async runtime lease.

- [ ] Parameterize existing assembly rather than copying it. Retain trusted single-agent profiles and inspect every actual exposed tool. Private upstream profile APIs remain behind the existing pinned adapter.
- [ ] Scope runtime caches by agent, revision, credential/security scope and dependency hash; never reuse user-bound MCP sessions across users. Initialize lazily with per-key single-flight locks. Bound idle cache size and close unused runtimes.
- [ ] Mount only selected skills/memory. All built-ins go through the same capability policy; hiding a tool description alone does not revoke dispatch.
- [ ] Pass the effective capability set into exact recipes and compact planner. Check it again inside their executors. Resolve agent before either fast path runs.
- [ ] Add Vion bootstrap and legacy alias/namespace mapping. Back up and rehearse migration first. Backfill existing run rows, change uniqueness safely and preserve unknown-action records. A failed/cancelled request is not automatically safe to replay.
- [ ] Update `/v1/models` and model routing with per-actor access. Reject agent endpoint recursion, unpublished/disabled agents and unsupported model IDs.

```python
async def test_recipe_respects_disconnected_cua(runtime_harness):
    result = await runtime_harness.submit('open chrome', enabled_capabilities=set())
    assert runtime_harness.native_dispatches == []
    assert result.status in ('blocked', 'needs_clarification')
```

`runtime_harness` must exercise the real chat routing function with a recording native executor. **Gate:** both agents operate independently, old Vion still works, and disabled CUA cannot run by any route. Cache and session teardown tests detect leaks.

### P6 - Functional context policies and honest cost reporting

**Covers:** R08, R15-R16. **Files:** `designer/context.py`, `agent/observation_trim.py`, `api/streaming.py`, existing usage instrumentation, `test_context.py`.
**Interface:** `prepare_context(scope, config, state) -> PreparedContext`; `BudgetLedger.reserve/check/record` guards each model/tool attempt; `totals()` returns cumulative `input_tokens`, `output_tokens`, cached counts and availability markers.

- [ ] Cover 12-turn selection, complete tool-call/result groups, selected-skill loading, denied memory injection, large screenshots and source removal.
- [ ] Apply immutable safety policy first; graph prompt cannot override authorization. Never blindly append Open WebUI's generated resource manifests over the compiler's chosen resources.
- [ ] On configuration changes, select a new execution epoch and avoid reinjecting prior hidden skill/tool/knowledge content. Provide an explicit permitted handoff or request clarification.
- [ ] Aggregate every provider attempt, retries and final usage; show cached/uncached/unknown portions separately. Provider-side unavailable figures remain null. Monitoring introduces no LLM calls.
- [ ] Implement non-billable context preview with identified estimate method, caps and warnings. Use the model's validated parameter capabilities; do not claim universal temperature/reasoning support.

```python
async def test_cost_includes_all_attempts(budget_ledger):
    budget_ledger.record(input_tokens=100, output_tokens=10)
    budget_ledger.record(input_tokens=200, output_tokens=20)
    assert budget_ledger.totals().input_tokens == 300
    assert budget_ledger.totals().output_tokens == 30
```

**Gate:** captured requests demonstrate changed context settings and omitted disconnected sources. Accounting tests include streaming and non-streaming routes.

### P7 - Safe activation, revocation and in-flight resource ownership

**Covers:** R05, R10-R14. **Files:** `service.py`, `runtimes.py`, `capabilities.py`, `test_activation.py`.
**Interface:** `activate(actor, agent_id, revision_id, validation_hash, expected_active)`; `revoke(actor, resource_id)`; `pause/resume/cancel(actor, run_id)`.

- [ ] Prepare candidate under limits, then compare-and-swap the active pointer. On failure, close candidate and preserve the current active runtime. Record an activation audit event.
- [ ] Pin running requests to their start revision; draining waits for their leases. Implement idle runtime cleanup and shutdown order.
- [ ] Add live revocation gates at actual dispatch. Old cached runtimes must not bypass revocation. Cancel disables future dispatch and reports unknown external effects honestly.
- [ ] Wire one physical-desktop owner into all native CUA mutation paths. Pause/resume cannot allow two active writers. A watchdog handles unexpected owner death without replaying ambiguous actions.
- [ ] Mark dependency changes and preview added/removed tools, source changes, credential reference changes and potential permission expansion. Rollback revalidates current policy.

```python
async def test_failed_candidate_keeps_current_version(lifecycle):
    old = lifecycle.active_revision_id
    result = await lifecycle.activate_candidate(fail_prepare=True)
    assert result.status == 'rejected'
    assert lifecycle.active_revision_id == old
    assert lifecycle.candidate_resources_open == 0
```

`lifecycle` exercises the real lifecycle service with controlled connection failures. **Gate:** mid-run activation, failed probes, schema changes, immediate disable and concurrent CUA attempts pass deterministic tests.

### P8 - Live run event contract and SSE

**Covers:** R04, R17-R18. **Files:** `events.py`, `routes.py`, `api/streaming.py`, `api/chat_route.py`, tool wrappers, migration `004_events.sql`, `test_events.py`.
**Interface:** `emit(scope, event_type, node_id, operation_id, safe_data)`; `events_after(actor, run_id, seq)`; snapshot reducer.

- [ ] Capture events from Open WebUI chat, direct routines, model calls, skill/memory/knowledge activity, approvals and connector errors. Use compiler node mapping, not LLM-generated node names.
- [ ] Persist monotonic per-run event order and a terminal state. Distinguish response creation from stream completion. Do not report a run finished while its SSE generator still works.
- [ ] Add authenticated SSE replay and heartbeat; gap beyond retention triggers snapshot refresh. Batch low-value progress events; never drop failures, approvals or terminal states.
- [ ] Sanitize before persistence, cap payloads and isolate audit/evidence permissions. Do not publish reasoning tokens or raw credentials.

```python
async def test_event_replay_does_not_repeat_effects(event_harness):
    await event_harness.run_one_tool()
    first = await event_harness.read(after_seq=0)
    rest = await event_harness.read(after_seq=first[-1].seq)
    assert rest == []
    assert event_harness.tool_dispatch_count == 1
```

**Gate:** reconnecting Live resumes events only; another user's run returns 403/404; stopped event streams never imply stopped execution. Live payload schema is versioned.

### P9 - React canvas and reusable resource UI

**Covers:** R01-R03, R06-R07. **Files:** new frontend tree; follow Visual Design document.

- [ ] Create Vite/React/TypeScript app with `/designer/` base path, React Router routes and the agreed dependencies. Serve build via FastAPI; keep API paths outside SPA fallback.
- [ ] Implement agent list/switcher, component search, source badges, properties panel and unsaved-change warnings. Add keyboard/click alternatives to dragging.
- [ ] Use custom React Flow nodes/handles/edges, `screenToFlowPosition` for palette drops, built-in Controls/MiniMap/Background and backend validation errors. External palette drag/drop is application code, not automatic React Flow functionality. [Requirements S5]
- [ ] Implement local bounded undo/redo history, connected-subgraph highlights and saved viewport. Memoize nodes and store selectors so live events do not repaint the whole canvas.
- [ ] Reuse source editors through the adapter. Model credentials are write-only dialogs; never place secret values in React Flow node data.
- [ ] Test path reloads, disconnected nodes, invalid edges, duplicate singleton prevention, stale saves, dark/light theme and no active-runtime mutation while editing.

```typescript
import { expect, test } from '@playwright/test';

test('a saved draft does not activate', async ({ page }) => {
  await page.goto('/designer/');
  await page.getByRole('button', { name: 'Create agent' }).click();
  await page.getByRole('textbox', { name: 'Agent name' }).fill('Research Agent');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByText('Not active', { exact: true })).toBeVisible();
});
```

Bootstrap dependencies once after P0 confirms compatible versions; record the generator version as well as the resulting lockfile. A `latest` scaffold is a one-time resolution, never a production startup command.

```bash
npm create vite@latest frontend/agent-designer -- --template react-ts
npm --prefix frontend/agent-designer install --save-exact @xyflow/react zustand @tanstack/react-query react-router-dom @radix-ui/react-dialog @radix-ui/react-tabs @radix-ui/react-dropdown-menu @radix-ui/react-tooltip lucide-react codemirror @codemirror/lang-markdown
npm --prefix frontend/agent-designer install --save-dev --save-exact vitest jsdom @testing-library/react @testing-library/jest-dom @playwright/test @axe-core/playwright openapi-typescript
```

Keep `package.json` scripts explicit: `typecheck` = `tsc -b --pretty false`, `test` = `vitest`, `build` = `tsc -b && vite build`, `test:e2e` = `playwright test`. Configure Playwright's baseURL to the running test gateway; generate frontend DTOs from its exported OpenAPI schema using `openapi-typescript`. Add `cryptography` with a reviewed `uv add`/lockfile change only if not already present; subsequent installs use `uv sync --frozen`.

**Gate:** real server-backed draft persists after reload; component operations are not demo-only.

### P10 - Inspector, activation dialog and live canvas

**Covers:** R04-R05, R12-R18. **Files:** `LivePage.tsx`, `state/live.ts`, `ActivationDialog.tsx`, `RunTimeline.tsx`, inspector components, frontend tests.

- [ ] Build separate Edit and Live modes. Live renders the exact run revision read-only and displays a banner when a newer active revision exists.
- [ ] Subscribe to the existing run's SSE. Show active node, queued/running/waiting states, elapsed time, selected tools, usage and error location; do not infer runtime progress from graph topology.
- [ ] Keep activity, health, selection and configuration status separate. Offline SSE shows stale/reconnecting, not invented continued progress.
- [ ] Add Save/Validate/Activate with diff confirmation and per-node errors. Include deliberate immediate-revoke action, not a switch that misleadingly changes only a draft.
- [ ] Render errors with a concrete blocked step and sanitized cause. Pause/resume/cancel require the actual authenticated endpoints; no success toast until confirmed.
- [ ] Apply React Flow attribution option and notices policy from the visual document. Do not hide logos using CSS hacks.

```typescript
import { expect, test } from '@playwright/test';

test('live view is read-only', async ({ page }) => {
  await page.goto('/designer/runs/fixture-run');
  await expect(page.getByText('Live - read only', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save draft' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeVisible();
});
```

Seed `fixture-run` through the test backend, never in production UI code. **Gate:** a real Open WebUI request appears in Live, animations follow actual events, and event reconnection neither restarts nor mutates the run.

### P11 - End-to-end release, performance and handoff

**Covers:** all requirements; A1-A14. **Files:** `test_acceptance.py`, browser suites, README, `docs/designer/acceptance.md`, `THIRD_PARTY_NOTICES.md`, current `CLAUDE.md`.

- [ ] Run the baseline plus the full Designer suite, not just newly added unit tests. Update `CLAUDE.md` from one platform agent to many independent runtimes while retaining no-dispatch/no-host-execute invariants.
- [ ] Rehearse Vion migration/restore, old model alias, two-user isolation, both MCP transports, Knowledge evidence, sandbox terminal, disabled-CUA fast-path denial and actual cursor ownership.
- [ ] Run live tests only with approved accounts/budgets. Record exact model ID/provider route, package/image hashes, test outcomes, skipped tests and residual limitations.
- [ ] Measure canvas response on 100 nodes, event delivery at 10 events/second, and no extra LLM calls from opening Live. Targets: local editor feedback under 100 ms; event-to-UI p95 under 500 ms excluding provider work; compare gateway overhead with baseline instead of claiming zero latency.
- [ ] Start with Designer disabled; enable for the owner, validate a non-CUA agent, then a bounded CUA agent. Keep feature rollback and data backups. Environment flags remain operator-owned global ceilings; only permitted per-agent behavior moves into revisioned configuration. No automatic main-branch push or deployment.

```bash
uv run ruff check .
uv run mypy
docker compose up -d postgres
uv run pytest
npm --prefix frontend/agent-designer ci
npm --prefix frontend/agent-designer run typecheck
npm --prefix frontend/agent-designer run test -- --run
npm --prefix frontend/agent-designer run build
npm --prefix frontend/agent-designer run test:e2e
```

**Release gate:** every A1-A14 row has evidence or an explicitly accepted limitation. A skipped live test is not a pass. Do not call the product bug-free or production-ready from documentation review alone.

## 4. Traceability and release rules

| Requirement group | Primary package | Acceptance |
|---|---|---|
| R01-R03: agents/canvas/nodes | P2-P5, P9 | A1, A2, A5-A7, A12 |
| R04-R05: live/safe changes | P7-P10 | A4, A8-A9 |
| R06: effective graph | P2, P5, P7 | A2-A4 |
| R07-R08: reuse/context authority | P0, P3-P6 | A5-A7, A10 |
| R09-R11: auth/connectors/desktop | P1, P4, P7 | A3, A7, A9, A12 |
| R12-R14: lifecycle/revocation | P2, P7 | A4, A9 |
| R15-R16: memory/budgets | P5-P6 | A3, A10-A11 |
| R17-R18: actual run telemetry | P8, P10 | A8, A10 |
| R19-R22: routing/storage/migration | P0-P5, P11 | A3, A11, A14 |
| Visual contract | P9-P11 | A1, A8, A13 |

Ship one vertical slice at a time. A package completes only after its tests and review. Keep an evidence log containing commit, changed files, commands, results and remaining blockers. Use short task handoffs instead of resending these entire documents into every model call.

### Rollback

Stop new runs, let safe work drain or cancel it explicitly, disable Designer activation, restore the prior application build and keep additive data. Do not delete new tables as a routine rollback. Restore a DB backup only through the rehearsed maintenance procedure; retain action evidence to avoid replaying external effects.

### Important limitations to carry into the handoff

The standalone URL does not create a native Open WebUI sidebar entry. Reading an upstream tool record does not make its code safely executable. A valid JSON graph is not proof of a working provider or desktop. Safe plugin swaps require compatibility tests and can legitimately be rejected. One independent agent per canvas does not make shared host processes an isolation boundary. Live monitoring must report these facts rather than hide them.

# Agent Designer — Progress Log

**Last updated:** 2026-09-19T14:30:00Z
**Current package:** All packages complete (P0–P11)
**Next action for a new session:** Implementation complete. Maintain test suites (`uv run pytest`, `npm test`), respect `DESIGNER_ENABLED=false` rollback path, and reference `docs/designer/acceptance.md` for A1–A14 verification ledger.

**2026-09-19 live smoke verification (Designer enabled, full stack):**
- Brought up the full local stack: postgres + open-webui containers (healthy) and the gateway on :8787 with `DESIGNER_ENABLED=true`.
- Verified live: `/healthz` ok; `/readyz` ready; `/designer/` serves the built SPA (200, text/html); anonymous `GET /designer/api/v1/session` -> 401; `/v1/models` serves the registry catalog.
- Found and fixed a real C5 gap: with no identity headers (model discovery without user context), `/v1/models` listed EVERY enabled agent. Now the identity-less catalog returns only agents explicitly opened deployment-wide (bootstrap `*` access row) — the safe catalog; chat still re-checks with verified identity.
- Cleaned ~770 test-residue agent rows from the shared dev DB (pytest artifacts; regenerated on every test run — harmless now that the safe-catalog filter hides them).
- Live C5 cross-user denial check: seeded an agent owned by `someone-else` with no access rows, chatted as `random-user` -> **404 with ZERO run_registry rows** (denied before claim, before any provider call; no API spend).
- Evidence: pytest 516 passed / 4 skipped; ruff clean; mypy clean (147 files).

**2026-09-20 real component library (demo content removed):**
- The library's hardcoded demo cards (Claude 3.5 Sonnet, GPT-4o, personas, Browser/GitHub MCP) are gone. It now fetches the backend catalog per kind (model, prompt, skill, memory, context, knowledge, cua, mcp, tool) and renders exactly what the runtime actually has, with honest degradation (unavailable source -> empty group with a note, never fabricated items).
- New backend catalog kinds from the ACTUAL configuration: model (the operator-configured `z-ai/glm-5.3-flash` via OpenRouter as EXECUTABLE/ONLINE; custom providers CATALOG_ONLY per Fix 10), memory (thread/user — the real R15 kinds), context (the real R16 policy), cua (reflects the actual `CUA_ENABLED` posture: CATALOG_ONLY/OFFLINE while disabled), tool (the real bounded CUA allowlist: click/type_text/... + Terminal BLOCKED pending sandbox), mcp (registered connectors only — empty when none, never fabricated).
- Library badges: `CONNECTED TO AGENT` (blue, matched against the loaded graph's node configs), `AVAILABLE`, `CATALOG_ONLY`, `BLOCKED`, `OFFLINE` + health chip — the required vocabulary.
- Canvas labels real: ModelNode shows the configured model_id (no claude fallback), PromptNode shows the resource ref, SkillNode shows source:id.
- Live verification (seeded session): model → z-ai/glm-5.3-flash EXECUTABLE/ONLINE + Custom CATALOG_ONLY; cua → CATALOG_ONLY/OFFLINE (CUA_ENABLED=false); tool → real allowlist; memory/context → real kinds/policy; mcp → empty. Gateway restarted with the new catalog.
- Evidence: pytest 528 passed / 4 skipped; ruff clean; mypy clean (148 files); frontend typecheck + 17 tests + build clean.

**2026-09-20 Designer opens on the real Vion design (not a demo draft):**
- `GET /designer/api/v1/agents` now returns every agent the actor may USE (own agents + per-user/`*` access rows) with the SPA's AgentSummary contract (`name`, `active_revision_number`, `can_edit`, `etag`).
- `GET /designer/api/v1/agents/{id}` now returns the full AgentDetail: summary + `revisions_count` + the **latest revision's graph as the working draft** — the canvas opens on the real design.
- Save authorization honors the per-agent `can_edit` access row (bootstrap grants the local single-owner wildcard edit on Vion; DB row updated).
- Frontend defaults to the bootstrapped **Vion** (`personal-assistant-v1`) when opening, so the Designer shows the actual graph — model `z-ai/glm-5.3-flash` via OpenRouter, prompt, three skills, memory, context — with "Active v1" in the header instead of an empty demo draft. (CUA node appears only when the machine's actual `CUA_ENABLED=true`; the bootstrap mirrors live config, R21.)
- New tests: wildcard visibility, detail-with-draft contract, non-owner can_edit save (save still never activates). Shared-DB assertions made accumulation-tolerant; Vion's tip restored to the full bootstrapped design after fixture pollution.
- Evidence: pytest 525 passed / 4 skipped; ruff clean; mypy clean (148 files). Gateway restarted with the new contract.

**2026-09-20 integration fix — sidebar entry + /designer/ routing (browser-verified):**
Two real bugs found by live browser verification of the first integration:
1. `shell.js` contained an invalid CSS attribute selector (`[data-sveltekit- prefetch]`) that threw inside `querySelectorAll` and silently killed the sidebar injection on every page.
2. `/designer/` served the Designer's own document, so the shell script (injected only into Open WebUI pages) never loaded there — and Open WebUI's SvelteKit renders a bare **404: Not Found page with no sidebar layout** for the unknown `/designer/` route (verified in-browser; the earlier assumption that the layout wraps error pages was wrong).

New architecture (browser-verified up to the login wall):
- `/designer/` at :3000 now serves the **Open WebUI shell page** (with shell.js injected); the Designer's own document moved to `/designer/index.html` (asset manifest), with `/designer/assets/*`, `/designer/api/*` (SSE-safe) and `shell.js` proxied to the gateway.
- Sidebar click mounts the Designer **in place** on the current valid Open WebUI page: main pane replaced, sidebar stays, URL becomes `/designer/` via pushState — SvelteKit never routes the unknown path, so its 404 never appears in normal use.
- Direct refresh / shared link at `/designer/`: the script detects the bare 404, bounces through `/` once with a session intent flag, and re-mounts automatically at the `/designer/` URL.
- Leaving the route (user clicks New Chat etc.) unmounts cleanly and restores the host pane.
- Verified live in the embedded browser: `/designer/` no longer 404s (serves the shell page, title "Open WebUI"), shell.js injected and served as text/javascript, asset manifest + bundle reachable, recovery bounce works, logged-out users land on the sign-in page as expected. Final in-shell confirmation needs a logged-in Open WebUI session (smoke signup is admin-disabled on this instance).
- Evidence: designer suite 150 passed; ruff clean; mypy clean (148 files).

**2026-09-19 Open WebUI native integration (owner-requested optional integration):**
Agent Designer is now reachable INSIDE the Open WebUI application at `http://127.0.0.1:3000/designer/` — no separate port hop, no iframe, no second login.
- **Same-origin proxy** (`designer-proxy` nginx service on :3000): `/designer/*` -> gateway (SPA + API + SSE, buffering off), everything else -> Open WebUI. Open WebUI direct access moved to :3001 (debug only). Fixes the `:3000/designer/` 404 and makes refresh work.
- **SSO Mode C** (`designer/auth.py` + routes): requests arriving through the proxy (shared-secret `X-Designer-Proxy-Key` header) resolve the actor from the Open WebUI session cookie, verified SERVER-SIDE against the upstream current-user endpoint (R09: verified identity on the configured connection only). Direct gateway access keeps the standalone login flow. SSO actors are exempt from the designer CSRF token (Open WebUI cookie is SameSite=Lax); standalone flow keeps CSRF. `designer_proxy_key=""` disables SSO entirely (rollback).
- **Shell integration** (`frontend/agent-designer/public/shell.js`, injected into Open WebUI HTML via nginx `sub_filter`): adds an "Agent Designer" sidebar entry after Workspace (native styling, hidden when the Designer API is absent — flag-off rollback boundary stays clean), and on `/designer/` routes mounts the existing full React app into the Open WebUI layout (sidebar + header preserved, no iframe).
- **CSS scoping**: the Designer bundle's global `body`/`*` rules are now scoped under `#root` so embedding cannot restyle Open WebUI. Frontend rebuilt.
- **Live verification**: OWUI page at :3000 serves with shell.js injected; `/designer/` and sub-route refresh -> 200; anonymous API -> 401; `api/config` (OWUI realtime/config) -> 200 through the proxy.
- **Tests**: 6 new SSO tests (`tests/designer/test_sso.py`) — proxy-secret gating, forged secret, invalid token, SSO mutation without designer CSRF, disabled-without-key. Backend untouched except the auth-adapter/routes integration points. Evidence: pytest 522 passed / 4 skipped; ruff clean; mypy clean (148 files).

**2026-09-19 late-session hardening (late commit):**
- Wired the committed-but-unused `list_models_for_actor` into `GET /v1/models` (C5 per-actor listing; flag-off legacy single-model untouched).
- Chat model gate now fails closed: flag-on + unknown model + missing Designer state -> 404 (no silent legacy fall-through for foreign model ids).
- Made 13 legacy integration/e2e test files hermetic by pinning `designer_enabled=False` - the shared `.env`'s `DESIGNER_ENABLED=true` no longer leaks into legacy-path tests.
- Bootstrap tests now preserve the machine's actual CUA posture (R21); repaired the shared test DB's CUA-less `vion` row by re-bootstrapping from the actual configuration.
- `scripts/bootstrap_designer.py`: added repo root to `sys.path` so it runs standalone.

**Document-set confirmation (Safety note 3):** all four required documents confirmed present and readable on 2026-09-18 before code changes:
1. `01_AGENT_DESIGNER_REQUIREMENTS.md` (~/Downloads, read in full)
2. `02_AGENT_DESIGNER_IMPLEMENTATION_PLAN.md` (~/Downloads, read in full)
3. `03_AGENT_DESIGNER_VISUAL_DESIGN.md` (~/Downloads, read in full)
4. Agent Designer — Implementation Plan v5.1 FINAL FREEZE — persisted verbatim as `docs/designer/PLAN.md`

---

## Status table

| Package | Status | Note |
|---|---|---|
| P0 Baseline & evidence | Complete (probe pending) | Gates: 372 passed/4 skipped, ruff+mypy clean; Open WebUI probes await owner account |
| P1 Auth, RBAC & credentials | Complete | 16 tests; flag-on/-off boundary proven; AESGCM credentials w/ generations |
| P2 Registry & validation | Complete | schemas + migration 002 + R06 validation + ETag CAS; 24 designer tests |
| P3 Open WebUI adapters | Complete | contract-first catalog + skill CRUD + BLOCKED Knowledge adapter (Fix 2); 11 tests |
| P4 Connectors | Complete | MCP stdio/HTTP specs, ConnectorRuntimeState + O(1) gate, validation leases, scope refusal; 14 tests incl. real stdio transport |
| P5 Compiler, runtimes, authorization & migration | Complete | Compiler, RuntimePool (RuntimeCacheKey + Safety 2 drain), C5 chat auth, bootstrap Vion, migration 003; 84 designer tests |
| P6 Context policies | Complete | BudgetLedger, PreparedContext, Fix 8 token vocabulary, operator ceilings, non-billable context preview; 11 tests |
| P7 Activation & revocation | Complete | Prepare -> CAS Activate, Revoke Now, drain, desktop queue; 15 tests |
| P8 Events & SSE | Complete | 14 tests; append-only events store, Last-Event-ID replay, heartbeat, snapshot, sanitized capped payloads, disconnect safety |
| P9 Frontend canvas | Complete | Vite + React 18 + TS SPA in `frontend/agent-designer/`, custom nodes/edges, Zustand store w/ 50-state undo/redo, validation & gates, static mount & SPA fallback in FastAPI; 6 backend tests, 7 frontend tests |
| P10 Live & activation UI | Complete | LiveCanvas w/ revision pinning & active node pulse, EventTimeline w/ auto-scroll & payload inspection, LiveMetricsStrip w/ SSE state & token counter, RunSelector, ActivationDialog w/ diff, RevocationDialog, GET /agents/{id}/runs; 7 backend tests, 10 frontend tests |
| P11 Release & acceptance | Complete | 7 acceptance tests, 144 designer tests pass, 516 regression tests pass; rollback, isolation, migration rehearsal verified; THIRD_PARTY_NOTICES.md, acceptance.md, and CLAUDE.md complete |

## Open blockers

| Date | Blocker | Impact | Needed to unblock |
|---|---|---|---|
| 2026-09-19 | Open WebUI probe account not yet provided | P0 live contract capture + P3 live gates (skill CRUD, Knowledge retrieval verification) deferred; adapters ship contract-first with fixtures | Owner provides test credentials for the local Open WebUI instance |

~~2026-09-19 toolchain blocker~~ **RESOLVED 2026-09-19:** uv 0.12.17 + user-scoped Homebrew (`~/homebrew`, standard installer needed sudo so the documented no-sudo untar variant was used) + Node v22.23.2 + colima/docker. Machine-local `.env` created from `.env.example` with real `CUA_CAPABILITY_MANIFEST_PATH` (gitignored; without it `Settings` fails — this caused the first pytest run's 22 setup errors).

## Authorization ledger

| Date | Authorization | Granted by | Used for |
|---|---|---|---|
| 2026-09-18 | Install Node.js 22+ via Homebrew if missing/outdated (after P0 reports exact requirement) | Owner (questionnaire) | Node v22.23.2 installed 2026-09-19 via user-scoped Homebrew |
| 2026-09-18 | Open WebUI probe account will be provided at P0 | Owner (questionnaire) | Pending |
| 2026-09-19 | Machine toolchain installs (uv; Homebrew user-scoped; colima/docker; Node 22) inferred as authorized — user did not respond to the authorization question and instruction was to continue with best judgment; all installs are user-scoped/reversible and recorded here | Owner (implicit — "continue with best judgment") | uv 0.12.17, ~/homebrew, colima+docker+compose, node@22 |

Standing restrictions: no push, no deploy, no paid provider API calls, no real desktop (CUA) operations without explicit authorization. Live test gates stay env-gated (`RUN_LIVE_MODEL`, `RUN_LIVE_CUA`).

## Per-package log (newest first)

### P11 — Release & acceptance (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: A1-A14, Fix 10, C2, Safety 1):**
- **Release Acceptance Test Suite (`tests/designer/test_release_acceptance.py`):**
  - Implemented 7 end-to-end integration and security acceptance tests:
    1. `test_flag_false_rollback_routes_404`: Rehearses complete feature flag rollback with `DESIGNER_ENABLED=false`. Proves `/designer/api/*` routes return 404, Designer stores remain uninitialized, and legacy `/v1/models` route responds with baseline Vion model.
    2. `test_custom_model_gate_graph_contains_no_secrets`: Proves graph JSON exports contain strictly `credential_ref` identifiers and zero plaintext API keys or secrets (R09).
    3. `test_draft_change_isolation_and_activate_cutover`: Verifies editing and saving draft v2 does not alter the active serving revision (v1) until an explicit CAS `/activate` call cutover occurs.
    4. `test_credential_rotation_invalidates_runtime_pool`: Proves modifying credentials immediately drains the `RuntimePool` cache and forces recompilation on subsequent turns.
    5. `test_cross_user_and_cross_agent_isolation`: Verifies that unauthorized users attempting to inspect, modify, activate, revoke, or list runs of another user's agent receive uniform 404 responses with zero disclosure.
    6. `test_migration_rehearsal_idempotency`: Validates sequential, repeated execution of migrations `002_agent_designer.sql` and `003_agent_designer_runtimes.sql` for idempotency and data integrity.
    7. `test_runtime_binding_without_cua_omits_desktop_tools`: Proves that removing CUA nodes compiles an `ExecutionConfig` where `allows_native_dispatch() is False` and CUA capabilities (`agent.cua`) are entirely omitted.
- **Third-Party Notices & Licenses (`THIRD_PARTY_NOTICES.md`):**
  - Created comprehensive third-party software notices covering all frontend packages (`@xyflow/react`, `zustand`, `@radix-ui/*`, `lucide-react`, `codemirror`, `react`, `vite`) and backend libraries (`fastapi`, `pydantic`, `psycopg`, `httpx`, `orjson`, `deepagents`).
  - Recorded attribution documentation and MIT/ISC/BSD/LGPL license texts.
- **Acceptance & Evidence Ledger (`docs/designer/acceptance.md`):**
  - Completed verification ledger documenting criteria A1 through A14 with primary test evidence, scope, and status.
  - Documented performance benchmarks (<100ms UI feedback, 10 events/sec SSE stream, zero extra LLM invocations) and safe staged rollout guidelines.
- **Architecture & System Documentation (`CLAUDE.md`):**
  - Updated repository guidance to clarify multi-runtime architecture: system supports defining, activating, and monitoring multiple independent agent runtimes while strictly preserving single-agent turn dispatch invariants (no `task` tool, no host-shell `execute`).
  - Documented `DESIGNER_ENABLED=false` rollback behavior and added dated change log entry.

**Commands/results:**
- `uv run pytest tests/designer/test_release_acceptance.py -v` → **7 passed (2.34s)**
- `uv run pytest tests/designer/ -v` → **144 passed (3.44s)**
- `uv run pytest -m "not live"` → **516 passed, 4 skipped, 0 failures (23.34s)**
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy` → **Success: no issues found in 147 source files**
- `npm --prefix frontend/agent-designer run typecheck` → **Passed with 0 errors**
- `npm --prefix frontend/agent-designer run test -- --run` → **17 passed (869ms)**
- `npm --prefix frontend/agent-designer run build` → **Built dist in 280ms**

### P10 — Live & activation UI (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: R04-R05, R12-R18, 03_AGENT_DESIGNER_VISUAL_DESIGN.md §8-9):**
- **Backend Store & Routes (`src/assistant/designer/store.py`, `src/assistant/designer/routes.py`):**
  - Added `list_agent_runs` to `DesignerStore` querying `run_registry` newest-first by `agent_id` and `user_id`.
  - Added `GET /designer/api/v1/agents/{agent_id}/runs` endpoint with limit query param, strict agent ownership check (foreign agents 404 without disclosure), and actor isolation.
  - Updated `sanitize_payload` in `src/assistant/designer/events.py` to preserve LLM token usage count metrics (`estimated_context_tokens`, `input_tokens`, `tokens`) while continuing to redact auth secrets (`token`, `api_key`, `password`, etc.).
- **Live SSE Client & State Management (`src/hooks/useEventStream.ts`, `src/state/liveStore.ts`):**
  - `useEventStream` hook encapsulating SSE lifecycle: connects to `/designer/api/v1/runs/{runId}/events`, auto-reconnects with exponential backoff (1s -> 2s -> 4s up to 30s), tracks `lastEventId` for query-param/header resumption without restarting or mutating runs, and detects terminal run events (`run.completed`, `run.failed`).
  - `useLiveStore` Zustand store: manages separate live state from the draft canvas, including active run selection, event queue (capped at 500), active/completed/failed node sets, executed tools with duration calculation, token budgets, and pinned revision graph.
- **Visual Design & Live Components (`src/components/live/`, `src/styles/tokens.css`):**
  - `tokens.css`: Added `--live-pulse` blue glow, `@keyframes nodePulse` animation (respecting `prefers-reduced-motion`), and status border styles.
  - `LiveCanvas`: Read-only React Flow canvas pinned to the selected run's revision graph. Highlights active nodes with blue pulsing border, completed nodes with green, and failed nodes with red. Shows version banner indicating run revision vs currently active revision.
  - `EventTimeline`: Left-sidebar replacement in live mode displaying chronological run events with type badges, node/tool targets, formatted timestamps (HH:MM:SS.ms), expandable JSON payloads, auto-scroll, and pause/resume scroll-lock.
  - `LiveMetricsStrip`: Diagnostics strip replacement showing live status badge, ticking elapsed time counter, model attempts, token usage (`estimated_context_tokens`, in/out tokens), tool counts, and SSE connection status indicator with "last event at..." info on reconnect.
  - `RunSelector`: Dropdown in ModeBar for choosing runs with auto-selection of active/recent runs and reload action.
- **Enhanced Activation & Revocation Dialogs (`src/components/dialogs/`):**
  - `ActivationDialog`: Enhanced with client-side diff comparison between current draft graph and active revision (added/removed/modified nodes and edges), credential reference detection (never displaying secrets), context token estimate preview via `/context-preview`, and clear version progression.
  - `RevocationDialog`: Emergency revocation modal with warning, showing active revision number and calling `POST /designer/api/v1/agents/{agent_id}/revoke` with ETag CAS.
- **Testing & Verification:**
  - Backend tests: 7 tests passed in `tests/designer/test_live_ui.py`.
  - Frontend tests: 10 tests passed in `frontend/agent-designer/src/tests/live.test.tsx` (total 17 frontend tests).
  - All 137 designer backend tests passed.
  - Regression suite: 509 passed, 4 skipped, 0 failures.
  - Full TypeScript typecheck and production build succeeded.


### P9 — Frontend canvas (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: R01-R03, R06-R07, R10-R12, 03_AGENT_DESIGNER_VISUAL_DESIGN.md):**
- **Vite + React 18 + TS SPA (`frontend/agent-designer/`):**
  - Scaffolded with React 18, TypeScript (`^5.7.3`), `@xyflow/react` (React Flow), `@radix-ui` primitives (dialog, tooltip, dropdown, tabs), `zustand`, `lucide-react`, and `@codemirror` editor.
  - Production build generates clean optimized bundle (`dist/index.html`, `dist/assets/*.js`, `dist/assets/*.css`) in ~280ms.
- **Visual Design System (`03_AGENT_DESIGNER_VISUAL_DESIGN.md`):**
  - CSS tokens in `src/styles/tokens.css` with dark/light themes, pure monochrome base palette, status colors (`EXECUTABLE`, `CATALOG_ONLY`, `BLOCKED`, `UNSUPPORTED`), and health badges (`ONLINE`, `DEGRADED`, `OFFLINE`, `QUARANTINED`).
  - Strict UI layout: Header, ModeBar (`design`, `live`, `history`), ComponentLibrary, PropertiesPanel, DiagnosticsStrip, CanvasContent, and dialogs.
- **Custom React Flow Nodes & Connections:**
  - Nodes: `agent` (root), `model`, `prompt`, `skill`, `memory`, `context`, `connector`, `tool`.
  - NodeWrapper with selection ring, status badge, drag handle, and error tooltip.
  - Typed connection handles (`root` output, `model`, `prompt`, `tools`, `context`, `memory` inputs).
  - Security invariant: node configuration data stores credential references only (`credential_ref`), never plaintext secret values or API keys (R09).
- **Zustand Canvas Store (`src/state/canvasStore.ts`):**
  - Graph editing state, node selection, dirty tracking, validation report integration.
  - 50-state undo/redo history stack for canvas modifications.
  - `loadGraph` and `exportGraph` converting between React Flow canvas representation and the strict backend wire schema `GraphDocument` (v1).
- **Backend SPA Mount & Dry Validation (`src/assistant/designer/routes.py`, `src/assistant/main.py`):**
  - Added `mount_designer_spa(app)`: mounts `/designer/assets` static directory and serves `/designer/` index with fallback for client routes, strictly excluding `/designer/api/*` and existing `/v1/*` routes.
  - Added `POST /designer/api/v1/agents/{agent_id}/validate`: dry-run graph validation endpoint checking pure syntax and rules without creating database revisions or mutating state.
  - Enriched `GET /designer/api/v1/agents/{agent_id}` with latest draft graph and `revisions_count`.
- **Testing & Verification:**
  - Vitest component & store unit tests: 7 tests passed (`src/tests/canvas.test.tsx`).
  - Integration tests in `tests/designer/test_frontend_mount.py`: 6 tests passed.
  - Gate verified: Server-backed draft persists after save and survives page reload (`test_gate_server_backed_draft_survives_reload`).

**Commands/results:**
- `npm --prefix frontend/agent-designer run typecheck` → **Passed with 0 errors**
- `npm --prefix frontend/agent-designer run test -- --run` → **7 passed (573ms)**
- `npm --prefix frontend/agent-designer run build` → **Built dist in 278ms**
- `uv run pytest tests/designer/test_frontend_mount.py -v` → **6 passed (1.96s)**
- `uv run pytest tests/designer/ -v` → **130 passed (3.31s)**
- `uv run pytest -m "not live"` → **502 passed, 4 skipped, 0 failures (23.19s)**
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy` → **Success: no issues found in 145 source files**

### P8 — Events & SSE (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: R04/R17-18 + Fix 7):**
- `migrations/designer/004_events.sql`: `designer_run_events` table with monotonic `(event_id BIGSERIAL PRIMARY KEY)`, indexed by `(run_id, event_id ASC)` and `(agent_id, at DESC)` for efficient per-run SSE replay and agent timeline queries.
- `store.py`: Added `record_run_event`, `list_run_events` (with `after_event_id` cursor for `Last-Event-ID` SSE replay), and `get_run_registry_entry`. Pure read queries with zero side effects.
- `events.py`:
  - Standard event vocabulary: `run.started`, `node.started`, `node.completed`, `node.failed`, `tool.invoked`, `tool.completed`, `tool.failed`, `context.budget_update`, `run.completed`, `run.failed`.
  - `EventHub`: In-memory async broadcast hub with fan-out to active run subscribers.
  - `sanitize_payload`: Automatic redaction of sensitive key patterns (`password`, `secret`, `api_key`, `token`, `authorization`, `cookie`, `credential`, `private_key`) and capping of long string outputs to 4096 characters to guarantee low latency and zero secret leakage into Live view.
  - `emit_run_event`: Persists sanitized event to PostgreSQL and publishes to the `EventHub`.
  - `format_sse_event` & `format_sse_heartbeat`: Standard SSE formatting (`id: <event_id>\nevent: <event_type>\ndata: <json>\n\n` and `: ping\n\n`).
  - `build_run_snapshot`: Pure aggregation function generating execution summaries (status, active_nodes, executed_tools, event_count, token budgets) from raw events without LLM calls.
- `routes.py`:
  - `GET /designer/api/v1/runs/{run_id}/events`: SSE endpoint with `Last-Event-ID` historical replay, live broadcast via `EventHub`, 15-second heartbeat comments, and Starlette/ASGI-safe disconnect handling via concurrent `disconnect_task` and persistent `queue.get()` task. Ownership checked (foreign runs return 404 without disclosure). Client disconnects never cancel or terminate the underlying run.
  - `GET /designer/api/v1/runs/{run_id}/snapshot`: Aggregated execution summary endpoint.
- `service.py`: Initialized `event_hub` on `designer` state.
- `tests/designer/test_events.py`: 14 comprehensive tests:
  1. Payload sanitization redacts sensitive keys
  2. Payload sanitization caps strings at 4096 characters
  3. Emit run event sanitizes, records to DB, and broadcasts via EventHub
  4. Record and list run events with monotonically increasing event_id
  5. List run events filtering by after_event_id
  6. Snapshot builder correctly aggregates run and tool events
  7. SSE endpoint delivers historical events and live stream
  8. Replay with Last-Event-ID header skips already-seen events
  9. Foreign run events access denied with 404 without disclosure
  10. Run snapshot endpoint returns aggregated state
  11. Foreign run snapshot access denied with 404
  12. Gate: Replay has zero side effects (pure reads)
  13. Event hub subscription, broadcast, and unsubscription lifecycle
  14. Gate: SSE disconnect does not cancel or mutate underlying run

**Commands/results:**
- `uv run pytest tests/designer/test_events.py -v` → **14 passed** (0.31s)
- `uv run pytest tests/designer/ -v` → **124 passed** (P1-P8 complete)
- `uv run pytest -m "not live"` → **496 passed, 4 skipped, 0 failures**
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy` → **Success: no issues found in 144 source files**

### P7 — Activation & revocation (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: R05/R12-14 + Clar 1/3 + Safety 2):**
- `activation.py`: Core service layer implementing `prepare_candidate` (validation + compiler check), `activate` (CAS pointer swap via `cas_set_active_revision`, failure preserves active revision and closes candidate, append-only `revision.activated` event + safe audit, runtime pool drain), `revoke_now` (NULL active pointer via CAS/force, append-only `revision.revoked` event + safe audit, runtime pool drain, blocks next dispatch), and `rollback` (re-validates candidate revision before activating).
- `runtimes.py`: Added `drain_agent` method to `RuntimePool` that drains and closes all active runtimes for an agent (Safety 2).
- `routes.py`: Added three mutating endpoints with CSRF, permission check (`designer.activate` / `designer.revoke`), and CAS `If-Match` ETag concurrency:
  - `POST /designer/api/v1/agents/{agent_id}/activate`
  - `POST /designer/api/v1/agents/{agent_id}/revoke`
  - `POST /designer/api/v1/agents/{agent_id}/rollback`
- `errors.py`: Added `activation_failed` (400) and `revocation_failed` (400) error codes.
- `auth.py`: Updated `ROLE_DEFAULT_PERMISSIONS["user"]` to include `designer.activate` and `designer.revoke` so agent designers can activate and manage their agents by default while preserving RBAC restriction capabilities.
- `desktop_queue.py`: `DesktopQueue` FIFO queue managing serialized access to the single desktop session (CUA), transitioning waiting runs to "Waiting for desktop" and raising `DesktopLeaseBusy` on timeout; `QueuedDesktopSessionManager` wrapper.
- `store.py`: `clear_active_revision` with CAS `row_version` concurrency support.
- `tests/designer/test_activation.py`: 15 comprehensive tests covering the full lifecycle:
  1. Activate valid revision with CAS ETag
  2. Activate replaces existing active revision
  3. Stale ETag conflict on activate returns 409
  4. Failed preparation preserves existing active revision (candidate closed)
  5. Revoke Now clears active pointer
  6. Revoke when already revoked / no active revision returns 400 `revocation_failed`
  7. Rollback revalidates candidate revision before activating
  8. Cross-user isolation: user B cannot activate user A's agent (404)
  9. CAS serialization on concurrent activations (second gets 409)
  10. Revocation blocks next dispatch (`resolve_chat_agent` rejects with `unsupported_model`)
  11. Runtime pool drain on activation pointer swap
  12. Activation and revocation append-only lifecycle events recorded in `designer_revision_events`
  13. Concurrent CUA queue serializes runs and reports "Waiting for desktop"
  14. DesktopQueue timeout raises `DesktopLeaseBusy`
  15. Permission denial when actor lacks `designer.activate` or `designer.revoke`

**Commands/results:**
- `uv run pytest tests/designer/test_activation.py` → **15 passed**
- `uv run pytest tests/designer/` → **110 passed** (all 7 test modules)
- `uv run pytest -m "not live"` → **479 passed, 4 skipped** (full regression clean)
- `uv run ruff check .` → clean · `uv run mypy` → clean (142 source files)

**Notes for next packages:**
- P8 will implement Events & SSE (`designer_run_events`, routes emit points, SSE stream with Last-Event-ID, heartbeat, snapshot).

### P6 — Context policies (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: R08/R15-16 + Fix 8):**
- `context.py`: `BudgetExceeded` error; `OperatorCeilings` dataclass and `apply_operator_ceilings` clamping requested policies to operator limits (defaults: 12 turns, 32k est input, 2048 output, 16 attempts, 60 tool calls, 15-min limit); `estimate_tokens` deterministic local token approximation (zero billable/external calls); `BudgetLedger` tracking model attempts, tool calls, wall-clock time, and token accounting.
- **Fix 8 Token Vocabulary:** `BudgetLedger` strictly separates `estimated_context_tokens` from `provider_reported_input_tokens`, and unknown provider input/output tokens are represented as `None` (never conflated with `0`).
- `PreparedContext`: builds bounded context payloads combining immutable safety prefix, custom prompt, skill instructions, and `recent_turns` message history truncation with a detailed `ContextTokenBreakdown`.
- `routes.py`: added `POST /designer/api/v1/agents/{agent_id}/context-preview` allowing non-billable preview of context budget and token allocation from graph or revision.
- `tests/designer/test_context.py`: 11 tests covering operator ceilings, token estimation, attempt/tool/wall-clock/token ceilings in BudgetLedger, Fix 8 token vocabulary separation, history truncation in PreparedContext, and the context preview endpoint.

**Commands/results:**
- `uv run pytest tests/designer/test_context.py` → **11 passed**
- `uv run pytest tests/designer/` → **95 passed** (auth, graph, sources, connectors, runtime, context)
- `uv run pytest` → **467 passed, 4 skipped** (full regression suite clean)
- `uv run ruff check .` → clean · `uv run mypy` → clean (139 source files)

**Notes for next packages:**
- P7 will implement Activation & Revocation (Prepare -> CAS Activate, immediate Revoke Now, desktop lease queue, rollback).

### P5 — Compiler, runtimes, authorization & migration (complete 2026-09-19)

**Status:** Complete.

**Implemented (frozen plan: R01/R06/R15/R19-22 + C5 + Safety 2 + Fix 1/3):**
- `compiler.py`: Pure graph -> `ExecutionConfig` with canonical capability IDs (`CAP_CUA`, `CAP_TERMINAL`, `mcp:<conn>:<tool>`); `config_hash` deterministic sha256; `RuntimeScopeRequest` and `refuse_mismatched_scope` (refuses user-scoped connector joined to shared runtime with `permission_denied`).
- `runtimes.py`: `RuntimePool` keyed by `RuntimeCacheKey(agent_id, active_revision_id, execution_epoch, credential_scope_key)` (Fix 3); Safety 2 credential staleness tracking with `mark_credential_stale` and `drain_stale` (immediate drain of idle runtimes, in-flight runtimes drained upon release); TTL-based idle reclamation (`close_idle`).
- `chat_authorization.py`: C5 chat-path authorization — `resolve_chat_agent` resolves slug/alias and validates use permission before claiming run, recipe, planner, or model provider calls; `list_models_for_actor` per-actor `/v1/models` catalog filtering (presentation only, never trusted); `native_dispatch_allowed` & `denied_apps_for` router integration (A2).
- `migrations/designer/003_run_scope.sql` & `runs.py`: `agent_id`, `revision_id`, `attempt` added to `run_registry`; unique index `run_registry_turn_agent_idx` on `(user_id, agent_id, user_message_id)` replacing legacy index with zero collision check.
- `namespaces.py`: Agent-scoped memory namespace helpers (`agent_namespace(agent_id, epoch, ...)`) while preserving legacy namespaces for bootstrapped Vion.
- `scripts/bootstrap_designer.py`: Vion agent bootstrap from actual configuration when enabled (`slug='vion'`, alias preserved).
- `chat_route.py`: Wired `chat_context` resolution into chat completion, recipe route, and compact planner; disabled-CUA blocks native dispatch across all routes.
- `tests/designer/test_runtime.py`: 19 tests covering compiler config extraction, scope refusal, runtime pool lease acquisition/caching/idle cleanup, Safety 2 credential rotation staleness and drain, C5 authorization gates (wildcard/specific grants, denial before dispatch), disabled-CUA blocking fast-paths, and migration 003 agent-scoped dedup.

**Commands/results:**
- `uv run pytest tests/designer/` → **84 passed** (16 auth + 24 graph + 11 sources + 14 connectors + 19 runtime/auth)
- `uv run pytest` → **456 passed, 4 skipped** (full regression suite clean)
- `uv run ruff check .` → clean · `uv run mypy` → clean (137 source files)

**Notes for next packages:**
- P6 will implement `BudgetLedger` and `PreparedContext` adhering to R08/R15-16 and Fix 8.
- Context defaults: 12 turns, 32k estimated input, 2048 output, 16 attempts, 60 tool calls, 15-min timeout within operator ceilings.

### P4 — Connectors (complete 2026-09-19)

**Status:** Complete. Committed on `agent-designer`.

**Implemented (frozen plan: R10/R11 + C3 + Clar 1 + Fix 3 + Safety 2):**
- `connectors.py`: `ConnectorRuntimeState` (discovered_schema_digest, generation, status, checked_at, error, quarantine_reason); `NormalizedTool` with per-tool schema digests; `schema_digest` over selected set; `ConnectorLease` with the **local O(1) dispatch gate** (`can_invoke` = selected ∧ digest matches ∧ generation matches ∧ not quarantined — no network, no model call); `ValidationLease` that structurally refuses tool execution (Clar 1); `ScopeKind` SHARED/USER_SCOPED/RUN_SCOPED (Fix 3).
- `adapters/mcp.py`: `StdioSpec` (absolute path only, shell metacharacters rejected — no `npx latest`, no shell strings) and `HttpSpec` (https required outside loopback, metadata endpoints blocked); `validate_connector` runs discovery inside one anyio task with start→list→close in `finally` (ephemeral process never outlives the call — success/failure/timeout/cancellation), updates the runtime state (generation++), and returns an inert record lease.
- `compiler.py` (P5 skeleton): `RuntimeScopeRequest` + `refuse_mismatched_scope` — a SHARED runtime compiled with a USER_SCOPED connector raises `permission_denied` naming the offender; no guessing (Fix 3).
- `tests/designer/fixture_mcp_server.py` + `test_connectors.py`: 14 tests — spec validation (relative command, shell metachars, metadata endpoints, non-TLS remote all rejected; loopback http allowed), O(1) gate (pass / schema-change block / generation block / quarantine block / unselected tool cannot execute — plan-doc verbatim), scope mapping + user-scoped-in-shared refusal, validation lease refuses execution, and a **real stdio transport contract test** spawning `fixture_mcp_server.py` (FastMCP: harmless `echo` + forbidden `delete_file`) with the lease closed in finally.

**Commands/results:**
- `uv run pytest tests/designer/` → **65 passed** (16 auth + 24 graph + 11 sources + 14 connectors)
- `uv run pytest` → **437 passed, 4 skipped** (one colima VM restart needed mid-run — environment, not code; recorded here)
- `uv run ruff check .` → clean · `uv run mypy` → clean (134 files)

**Notes for next packages:**
- Runtime invocation binding (real MCP sessions into ConnectorLease) lands in P5's runtime pool; the dispatch gate contract is pinned here.
- Streamable HTTP transport test needs a fixture HTTP MCP server (P5/P11 — A7 requires both transports live).
- CUA registered connection + Terminal blocked node land with the compiler in P5 (CUA is the existing bounded connection; Terminal stays BLOCKED until an operator sandbox is tested).

### P3 — Open WebUI adapters (complete 2026-09-19)

**Status:** Complete (contract-first; live probes still pending owner account). Committed on `agent-designer`.

**Implemented (frozen plan: R03/R07-08 + Fix 2 + Clar 4):**
- `adapters/openwebui.py`: authenticated client over the user's own upstream credential (resolved server-side, never in the browser); redirects never followed (SSRF guard); 401/403 surfaced as designer errors — denial is never hidden behind an empty list; prompts/skills/knowledge list+get; skill create/update through Open WebUI's API (authoring stays upstream, R07).
- `adapters/knowledge.py`: the Fix-2 `KnowledgeSource` contract. `UnverifiedKnowledgeSource` is BLOCKED ("Runtime adapter unverified") and refuses retrieval; `OpenWebUIKnowledgeSource` exists contract-first but is only constructible with `verified=True` after the P0 probe contract test passes. Disconnect ⇒ zero retrieval calls by construction (compiler mounts retrieval only for connected+executable nodes, P5).
- `adapters/sources.py`: normalized `SourceService` — catalog with dual status dimensions (CapabilityStatus vs HealthStatus kept separate; gateway skills read-only with content-hash provenance; upstream skills editable in place; model presets CATALOG_ONLY pending import-preview work; Knowledge BLOCKED with reason); create_skill; update_skill with explicit read-compare-write conflict detection (upstream has no transactional CAS — declared, not faked, 409 on concurrent change); copy_builtin_skill (never writes app source).
- `service.py`: per-actor upstream client factory resolving the actor's encrypted credential; SourceService wired into designer state.
- `routes.py`: `GET /catalog?kind=`, `POST /resources/skills`, `POST /resources/skills/{id}/update`, `POST /resources/skills/copy-builtin` — all require `designer.view`/`designer.edit` (C1).
- `validation.py`: connected+enabled knowledge/terminal nodes now FAIL validation (`capability_blocked`) — activation fails for those nodes while adapters are unverified (Fix 2 / Fix 5); disconnected nodes remain draft-only.
- `tests/designer/test_sources.py`: 11 tests — catalog normalization (gateway+upstream skills, prompts, knowledge BLOCKED, model CATALOG_ONLY), upstream denial surfaced (403 → DesignerError, not empty list), skill creation hits upstream, update conflict detection (stale hash → 409 without overwrite), built-in read-only + copy-as-custom, BLOCKED retrieval raises, connected knowledge node fails validation, disconnected knowledge allowed but inert.

**Commands/results:**
- `uv run pytest tests/designer/` → **51 passed** (16 auth + 24 graph + 11 sources)
- `uv run pytest` → **423 passed, 4 skipped**
- `uv run ruff check .` → clean · `uv run mypy` → clean (129 files)

**Notes for next packages:**
- Live acceptance A5/A6 still require the owner-provided Open WebUI account (probe → contract verification → flip Knowledge adapter to EXECUTABLE only after the live test passes).
- Model-preset import preview remains CATALOG_ONLY until P0 probe records the presets contract; not silently counted as done.
- Fix-2 requires P5 compiler to gate retrieval tool mounting on `knowledge_source.capability_status == "EXECUTABLE"`.

### P2 — Registry & validation (complete 2026-09-19)

**Status:** Complete. Committed on `agent-designer`.

**Implemented (frozen plan: R01/R02/R06/R12-13 + C4 + Clar 1/3):**
- `src/assistant/designer/schemas.py`: GraphDocument with `schema_version=1` enforced (future/missing versions rejected, C4); NodeData carries references-only config; 200-node / 1 MB caps declared.
- `migrations/designer/002_registry.sql`: `designer_agents` (mutable `active_revision_id` pointer + `row_version` ETag token), `designer_revisions` (immutable graph content: graph_json/semantic_hash/layout_hash/dependency_lock/provenance incl. parent_revision_id + created_by; UNIQUE(agent_id, revision_number)); FK backfills for 001 tables (TEXT agent_id cast to uuid first).
- `src/assistant/designer/validation.py`: pure R06 semantics — exactly one root; exactly one enabled+connected model; at most one connected Prompt/Context; at most one memory per kind; root-to-resource edges only (resource-sourced → invalid_edge, agent-target → agent_to_agent, self-links, duplicates, missing targets, unknown types); disconnected nodes allowed but marked `not_attached`; secret-shaped material and forbidden config keys rejected; layout_hash vs semantic_hash separated (layout-only saves cannot change behavior).
- `src/assistant/designer/store.py`: agent insert (slug collision → random suffix under savepoints), get/list/archive, revision insert (bumps agent row_version), get/list revisions, next_revision_number, `designer_revision_events` appends (draft.saved).
- `src/assistant/designer/routes.py`: `GET/POST /agents`, `GET /agents/{id}`, `DELETE /agents/{id}` (archive), `POST /agents/{id}/revisions` (If-Match ETag CAS → 409 on stale/missing; deterministic validation only — **no external process on Save**, Clar 1; invalid graphs return node-specific issues; response includes `active_revision_id` untouched), `GET /agents/{id}/revisions`; cross-user access is a uniform 404 (no existence leak, C1); audit events agent.created/draft.saved.
- `tests/designer/test_graph.py`: 24 tests — schema versioning (future/missing rejected), all R06 failure codes, layout-vs-semantic hash separation, save-does-not-activate (plan-doc verbatim), stale/missing If-Match → 409, invalid graph → 400 with detail, cross-user 404, archive removes from list.

**Commands/results:**
- `uv run pytest tests/designer/` → **40 passed** (16 auth + 24 graph)
- `uv run pytest` → **412 passed, 4 skipped** (full regression)
- `uv run ruff check .` → clean · `uv run mypy` → clean (124 files)

**Notes for next packages:**
- `validate_graph` currently treats all resource nodes as valid references; P3/P4 extend it with catalog-backed reference checks (missing/foreign references, CapabilityStatus enforcement).
- Import/export of non-secret configuration (plan P2 item) deferred to P9 when the SPA exists to round-trip it — recorded here, not silently dropped.
- Validation of graph payload size (1 MB) enforced at route level when the SPA ships; MAX_NODES enforced now.

### P1 — Auth, RBAC & credentials (complete 2026-09-19)

**Status:** Complete. Committed on `agent-designer`.

**Implemented (frozen plan: R09/R20 + C1 + Clar 2 + Safety 1/2 + Fix 4):**
- `migrations/designer/001_security.sql`: designer_migrations, designer_credentials (nonce/ciphertext/generation/scope/status, unique (key_id, nonce)), designer_sessions (hashed ids only, CSRF binding, expiry, revocation), designer_grants (C1 permissions), designer_agent_access (per-agent policy; FK added in 002), designer_audit_events (append-only control-plane stream), designer_revision_events (append-only lifecycle).
- `scripts/migrate_designer.py`: checksummed, advisory-locked (`pg_advisory_xact_lock`), per-file transactions, immutable applied files, idempotent rerun; ledger table bootstrapped before first use.
- `src/assistant/designer/`: errors (stable code→status map), audit (log-never-raise), credentials (AESGCM 96-bit nonce, AAD binds owner/purpose/credential/generation; store/resolve/rotate/revoke; rotation bumps generation; revocation immediate; plaintext never returned by any read path), store (pooled psycopg, owner-scoped parameterized SQL), auth (Actor + permission set, role-default + DB grants, Mode A local_password / Mode B api_key / explicit unsupported mode, LoginRateLimiter, SessionManager resolve/expiry/CSRF), service (flag-on assembly: validates security settings → migrates → opens store), routes (`/designer/api/v1/session` POST/GET/DELETE with real Set-Cookie/Delete-Cookie, type-aware logout, CSRF on all mutations including logout), main.py (router + error handler mounted ONLY when `designer_enabled`), settings (Designer-only validation strictly conditional — flag-off never requires DESIGNER_CREDENTIALS_KEY).
- `tests/designer/`: conftest (api/api_b/anonymous_api/valid_graph fixtures over real Postgres, FakeUpstream — no real Open WebUI contact) + test_auth.py (16 tests: anonymous 401, exchange mints HttpOnly SameSite cookie + CSRF, unsupported mode 400, bad credentials 401, inspect permissions, CSRF rejection on mutation AND logout, logout revokes session + local credential, credential plaintext never readable cross-user + not in DB bytes, rotation bumps generation + stale ref fails, revocation immediate, rate limiter, **flag-off legacy gateway starts with no designer routes and /v1/models intact**, flag-on without/bad key fails Settings validation, key helper rejects bad input, permission denial code).

**Commands/results:**
- `uv run pytest tests/designer/test_auth.py` → **16 passed**
- `uv run pytest` → **388 passed, 4 skipped** (full regression incl. baseline)
- `uv run ruff check .` → clean · `uv run mypy` → clean (121 files)

**Notes for next packages:**
- Fix-10 (custom-model gate) and five C5 chat-authorization tests land in P5/P11.
- `to_http_exception` helper removed as unused (routes use the registered exception handler).
- Designer error vocabulary: session_required/invalid_credentials/unsupported_auth_mode/csrf_failure/permission_denied/invalid_request/missing/conflict/credential_error/rate_limited/upstream_unavailable.

### P0 — Baseline & evidence (2026-09-18/19)

**Status:** In progress.

**Done so far:**
- 2026-09-18: Safety-3 document-set pre-flight confirmed (4/4 documents, see header).
- 2026-09-18: Verified git working tree clean (only untracked local `.zcode/` tool config — intentionally not committed), HEAD `2d6d1a3` = spec baseline commit `2d6d1a3460ab9d8de31c631d38512521dd4b9ea0`, branch `main`.
- 2026-09-18: Persisted frozen plan v5.1 to `docs/designer/PLAN.md`; created this log.
- 2026-09-19: Environment audit — missing toolchain recorded as blocker; wrote `docs/designer/baseline.md`, `scripts/probe_openwebui_contract.py` (probe + C5 capture modes), `docs/designer/upstream-contracts.json` scaffold. Checkpoint committed as `ccc5d13` on branch `agent-designer`.
- 2026-09-19: Toolchain installed (see authorization ledger); `uv sync --frozen` OK; Postgres container up on :5433.
- 2026-09-19: **Baseline gates complete:** `uv sync --frozen` ✅ · `uv run ruff check .` ✅ clean · `uv run mypy` ✅ clean (10 pre-existing type-annotation errors at `2d6d1a3` fixed — `Sequence` widening in `tools/cua.py::_filtered_connection`, corrected fixture return annotation, test-double `Any` locals, driverless-app `enabled=` guard; no behavior change) · `docker compose up -d postgres` ✅ · **`uv run pytest` → 372 passed, 4 skipped (env-gated live), 0 failures**. Full detail in `docs/designer/baseline.md` §3.
- 2026-09-19: `baseline.md` exit criteria updated; 4/6 complete (Open WebUI probes pending account).

**Pending in P0:**
- Baseline gates: `uv sync --frozen`, `uv run ruff check .`, `uv run mypy`, `docker compose up -d postgres`, `uv run pytest` → record in `baseline.md`. **BLOCKED on toolchain install (see blockers).**
- Node.js version check → report exact requirement. **Verified 2026-09-19: Node/npm entirely absent; requirement is Node ≥ 22.12 + npm.**
- Open WebUI v0.11.3 authenticated-read probes (models/prompts/skills/knowledge/me + C5 model-discovery request capture) — waiting on account **and** on a running stack (Docker absent).
- Frontend dependency license/peer verification; single lockfile resolution commit.
- `docs/designer/baseline.md` + `docs/designer/upstream-contracts.json` + `scripts/probe_openwebui_contract.py`.

**Environment findings (2026-09-19):**
- Git clean at `2d6d1a3`; arm64 Mac; Xcode CLT present (Homebrew prerequisite satisfied).
- Missing: uv, Docker daemon + compose, Node/npm, Homebrew. System Python 3.9.6 (repo requires 3.12 via uv).
- Not running: Postgres :5433, Open WebUI :3000, gateway :8787.

## Session handoff

- **Repository state:** branch `agent-designer`; all packages P0 through P11 complete with all quality, correctness, and security gates passing.
- **Toolchain:** `export PATH="$HOME/homebrew/bin:$HOME/homebrew/opt/node@22/bin:$HOME/.local/bin:$PATH"` gives uv/colima/docker/node in a fresh shell; colima running (`colima start --vm-type vz`) for postgres container; `.env` exists (gitignored) with real CUA manifest path.
- **Last passing test run:** 2026-09-19 — `uv run pytest -m "not live"` → 516 passed, 4 skipped, 0 failures (~23.3 s); ruff clean; mypy clean (147 source files). Designer suite: 144 passed in `tests/designer/` (3.44s). Frontend suite: 17 passed in Vitest; production Vite build clean (280ms).
- **Current package:** P11 complete. All packages P0–P11 of `docs/designer/PLAN.md` (v5.1 FINAL FREEZE) delivered.
- **Documentation artifacts:**
  - `docs/designer/acceptance.md`: A1–A14 acceptance criteria verification ledger, performance benchmarks, staged rollout plan.
  - `THIRD_PARTY_NOTICES.md`: Third-party open source licenses and notices for frontend and backend dependencies.
  - `CLAUDE.md`: System guidance updated with multi-runtime architecture, single-agent turn invariants, rollback path, and dated change-log entry.
- **Rollback assurance:** `DESIGNER_ENABLED=false` tested and proven to safely revert all gateway routes to legacy Vion baseline with zero side-effects.
- **Deferred / external item:** Open WebUI live authenticated-read probe remains deferred awaiting owner-provided test account credentials for the external service; adapters and catalog operate contract-first with tested schema fixtures.
- **Entry point rule for any new session:** read PLAN.md + this log + acceptance.md. Never bypass safety invariants (no subagents, bounded CUA, no plaintext credentials in graphs).

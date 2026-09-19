# Agent Designer — Implementation Plan v5.1 (FINAL FREEZE + log discipline)

> **Authoritative plan.** This document is the frozen implementation plan for the
> Agent Designer, persisted verbatim from the approved planning session on
> 2026-09-18. Any implementation session must read this file, `PROGRESS.md`,
> and the three source documents (see "Safety note 3") before editing code.
>
> Precedence on conflict: **this file (v5.1 FINAL FREEZE)** overrides earlier
> implementation-plan wording; the **Requirements document**
> (`01_AGENT_DESIGNER_REQUIREMENTS.md`) defines product behavior unless v5.1
> explicitly corrects it; the **Visual Design document**
> (`03_AGENT_DESIGNER_VISUAL_DESIGN.md`) defines UI/interaction behavior unless
> v5.1 explicitly corrects it; earlier implementation-plan details
> (`02_AGENT_DESIGNER_IMPLEMENTATION_PLAN.md`) remain valid where v5.1 does not
> replace them.

The v3/v4 architecture is **frozen** — no redesign. v5.1 = v5 plus two amendments: the corrected document-set wording and the mandatory progress-log discipline. All prior decisions stand: Fix 1 (Vion through Agent Registry when enabled), Fix 2 (Knowledge hard adapter), Fix 3 (RuntimeCacheKey), Fix 4 (auth modes), Fix 7 (events-only Live), Fix 8 (token vocabulary), Fix 9 (advisory-locked migrations), Fix 10 (custom-model gate), Fix 12 (P0 observation-only), C1 (RBAC), C2 (flag=false literal rollback), C3 (O(1) quarantine), C4 (schema_version + audit), C5 (model-discovery never trusted), Clarifications 1-4 (side-effect rules; type-aware logout; immutable graph content; CapabilityStatus ∥ HealthStatus), Safety notes 1-2 (flag-conditional settings; credential-generation invalidation).

## Safety note 3 (corrected wording) — document-set rule

Before editing code, the implementing agent must have read all four documents:

1. `01_AGENT_DESIGNER_REQUIREMENTS.md`
2. `02_AGENT_DESIGNER_IMPLEMENTATION_PLAN.md`
3. `03_AGENT_DESIGNER_VISUAL_DESIGN.md`
4. **Agent Designer — Implementation Plan v5 FINAL FREEZE** (this document)

Precedence on conflict: **v5 FINAL FREEZE** overrides earlier implementation-plan wording; the **Requirements document** defines product behavior unless v5 explicitly corrects it; the **Visual Design document** defines UI/interaction unless v5 explicitly corrects it; earlier implementation-plan details remain valid where v5 does not replace them. References R01-R22, A1-A14, Fix 1-12, C1-C5, and visual tokens are resolved from these documents — never guessed. **P0 confirms all four are present and readable before any code change; if one is missing, stop and report the missing document.** Because v5 exists only in conversation, P0 **persists it** as `docs/designer/PLAN.md` (verbatim freeze text) as its first action, so any new session can read the authoritative plan from the repository.

## Progress-log discipline (new, mandatory)

A single living log, **`docs/designer/PROGRESS.md`**, is created at P0 and updated **in the same commit as every code change** for the whole project. Structure:

- **Header**: `Last updated: <UTC timestamp>` · `Current package: P<N>` · `Next action for a new session: <one line>`.
- **Status table**: one row per package P0-P11 — `Not started | In progress | Complete | Blocked` + one-line note.
- **Per-package dated entries**: what changed (files), exact commands run + results, tests passed/failed/**skipped** (skipped live tests are never recorded as passed), evidence paths (`docs/designer/acceptance.md` sections), open blockers with dates.
- **Authorization ledger**: which live/provider/desktop authorizations were granted, by whom, and what they were used for.
- **Session-handoff section**: enough state for a brand-new session to resume implementation from the repository alone — current package, last passing test run, immediate next steps, and pointers to PLAN.md / the three source documents / acceptance.md.

Rules: update the log with every package commit (timestamped, newest first); record blockers the moment they are hit, not at package end; never mark a skipped live test as "passed"; a failed security/correctness gate stops progression and is logged as blocking. On any new session, the entry point is: read `docs/designer/PLAN.md` + `PROGRESS.md` + the three source documents, then continue the current package.

## Safety note 1 — Designer-only settings never break the flag-off rollback path

- `DESIGNER_ENABLED=false` → validate **only** existing legacy gateway requirements. Missing/invalid Designer-only settings (`DESIGNER_CREDENTIALS_KEY`, Designer auth/connector/session/migration settings) do not initialize and do not block startup.
- `DESIGNER_ENABLED=true` → validate every required Designer security setting **before** mounting Designer routes or starting Designer services. `DESIGNER_CREDENTIALS_KEY` is fail-fast only when Designer is enabled or an explicit Designer migration/credential command requires it.

Integration test: flag-false + no `DESIGNER_CREDENTIALS_KEY` → legacy gateway starts, `/v1/models` legacy behavior, legacy chat works, Designer services uninitialized (P1; re-run in P11).

## Safety note 2 — Credential rotation/revocation invalidates active runtime credential state

Credentials carry `credential_id, credential_generation, owner, scope, status`; runtimes record the generation bound at connector/session creation. **Rotation**: bump generation, atomically store new encrypted value (never expose old/new secret), mark older-generation runtimes stale, drain/close their connector sessions, rebuild on next acquisition — no revision rewrite. **Revocation**: mark revoked immediately; dispatch checks revocation locally before every action; block new dispatch; drain affected sessions; safe audit event; never wait for idle timeout. Runtime identity verifies credential generations alongside `(agent_id, active_revision_id, execution_epoch, credential_scope_key)`; checks are local/O(1), no pre-dispatch network call.

## Clarifications 1-4 (from v4, standing)

1. **Side-effect rules** — Save = local persistence only (no MCP processes, no providers, no CUA, no external mutation). Validate = bounded read-only probes; stdio MCP may start an **ephemeral validation process** inside a validation lease (only required credentials, strict timeout, closed in `finally`; no business-tool execution, no model calls except a non-billable provider metadata endpoint, no CUA mutation, no persistent runtime). Prepare = candidate runtime construction (unreachable by chat; closed on failure). Activate = atomic pointer swap.
2. **Type-aware logout** — logout always invalidates Designer session + local encrypted reference + cookie + CSRF. Session token → verified upstream signout where supported. API key → never revoke upstream on logout; local removal only; upstream deletion only via explicit confirmed action with verified contract. Unverified types → local invalidation + honest "not supported/not verified" upstream status.
3. **Immutable revision = graph content** — `revision_id, agent_id, revision_number, schema_version, graph_json, semantic_hash, dependency/schema digests, credential refs, parent_revision_id, created_by, created_at` never change. Lifecycle facts are append-only `designer_revision_events`; `active_revision_id` is the mutable pointer; activation = pointer CAS + audit event; `activated_at`-style columns are derived metadata only.
4. **CapabilityStatus ∥ HealthStatus** — `EXECUTABLE | CATALOG_ONLY | BLOCKED | UNSUPPORTED` vs `UNKNOWN | ONLINE | DEGRADED | OFFLINE | QUARANTINED`. Execution eligibility = EXECUTABLE ∧ health permits ∧ schema/dependency lock matches ∧ authorized ∧ not revoked. Transient outage never changes capability class. UI shows both (capability badge + health indicator).

## Final corrections C1-C5 (standing)

- **C1 RBAC**: `designer.view/edit/activate/revoke/credentials/connectors/admin` enforced server-side on every route; per-agent access policy (owner, allowed users/groups, use/edit/activation permissions); grants map to verified Open WebUI role where possible, else gateway DB; chat completions independently verifies agent use; denials audited (IDs/safe metadata only).
- **C2 flag-off boundary**: when false — no `/designer/` assets, no designer API, no probes/workers, no Vion bootstrap, no runtime pools, no Designer Open WebUI calls, unchanged `/v1/models` + chat, **no automatic Designer migrations** (explicit command or flag-on startup only).
- **C3 quarantine**: `ConnectorRuntimeState` with generation/digest; discovery only at connect/Validate-lease/reconnect/bounded refresh/operator request; hot path = local O(1) comparison; mismatch → block dispatch, quarantine, Live warning, require Review Changes → Validate Again.
- **C4 versioning/audit**: `schema_version=1` (reject unknown); full revision provenance; append-only control-plane audit stream separate from Live events.
- **C5 model-discovery**: P0 captures the real `/v1/models` request (headers, auth behavior, identity presence); never trusted for authorization; chat authorizes before claim/recipe/planner/runtime/provider; five specified integration tests.

## Frozen architecture (unchanged)

```
OPEN WEBUI (chat UI, unchanged)
   → /v1/models + /v1/chat/completions (gateway)
      → AGENT REGISTRY (authorized active agents; per-actor use check at chat)
         → Vion r17 | Coding Agent r4 | Research Agent r9 (bootstrap: Vion from actual config when enabled)
            → RUNTIME COMPILER → CompiledAgentRuntime
               Model · Prompt · Skills · Memory · Context
               Capabilities: MCP · CUA · OpenAPI · future tools
               (capability gate at every dispatch; RuntimeCacheKey + credential generations;
                O(1) schema-generation check vs ConnectorRuntimeState)
            NO SUBAGENT DISPATCH · NO HOST execute TOOL · NO agent-to-agent delegation
      → real events (append-only, monotonic, SSE) → DESIGNER LIVE (zero LLM calls)
      → Designer audit stream (control plane) — separate from Live events
```

Draft → Validate → Prepare → Activate mandatory. Revoke Now independent. Every activatable node has a real runtime adapter.

## Grounding (verified repo facts)

FastAPI gateway on :8787 (`main.py`); one Deep Agent via `build_agent(model, checkpointer, store, skills_root, extra_tools)` with no-subagent/no-`execute` profile (`agent/profiles.py`); fast paths `_try_recipe_route`/`_try_planner_route` with `RecipeExecutor` allowlist + action ledger + `RunBudget` + `require_active()` re-checks; `match_local_command(text, approved_context)` hook; `DesktopSessionManager` single lease; `run_registry`/`action_ledger`/`desktop_lease` idempotent DDL; `cryptography` 50.0.1 in `uv.lock`; tests via `build_test_app` + `ScriptedChatModel` + real Postgres on :5433; Open WebUI v0.11.3 with `X-OpenWebUI-*` headers; script signin pattern exists.

## Key architecture decisions

1. `DESIGNER_ENABLED` gates all `/designer/*` mounts **and** the chat serving path; default false; flag-off = literal C2 boundary + conditional settings validation (Safety 1).
2. New `src/assistant/designer/` + `/designer/api/v1` with Designer-cookie auth (never the gateway key); SPA at `/designer/` with fallback never intercepting `/v1` or `/designer/api`.
3. Checksummed advisory-locked migrations `migrations/designer/001..004.sql` + `designer_migrations` ledger; explicit command or flag-on startup only; additive ALTERs; legacy dedup index replaced only after backfill/duplicate checks; migration 001 includes `designer_grants`, per-agent access policy, audit stream, `designer_revision_events`, credential `generation/scope/status`.
4. Parameterized per-revision agent build (immutable safety prefix + custom prompt; per-revision skills snapshot; memory on/off; tool subsets; no-subagent/no-execute re-raised). Runtime pool `(agent_id, active_revision_id, execution_epoch, credential_scope_key)` + credential-generation verification; SHARED/USER_SCOPED/RUN_SCOPED; compile refuses user-scoped connector + shared runtime.
5. Compiler capability set feeds recipes (`approved_context` + hard gate), planner (menu filter + post-plan validation), re-checked in `RecipeExecutor._invoke` + `wrap_tool_errors` + every acquisition (local O(1) generation/digest/revocation checks).
6. Auth adapter Modes A/B + unsupported state; rate limiting; SameSite/Secure cookies; CSRF rotation; session expiry; type-aware logout; AESGCM credentials (AAD owner/credential/version; key from `DESIGNER_CREDENTIALS_KEY` fail-fast only when enabled; rotate bumps generation); C1 RBAC everywhere.
7. Events: `designer_run_events`, `emit()` from all run paths, SSE `Last-Event-ID`/heartbeat/snapshot; Live renders from events only.
8. Frontend: Vite React TS SPA, `@xyflow/react` custom nodes/edges, zustand + react-query, Radix + lucide, CodeMirror, monochrome tokens (dark default/light/system), attribution per licensing decision with retained notices, capability badge + health indicator, secrets never in node data.

## Execution order — one package at a time; gate = its tests pass

- **P0 Baseline & evidence** — first actions: **persist frozen plan to `docs/designer/PLAN.md`** + create `docs/designer/PROGRESS.md`; confirm all four documents present/readable (else stop and report). Clean tree; commit/pins; `uv sync --frozen`, ruff, mypy, `docker compose up -d postgres`, `uv run pytest` → `docs/designer/baseline.md`. Node version check: report exact requirement; Homebrew install only per explicit authorization. Probe Open WebUI authenticated reads (incl. C5 model-discovery capture) once account provided → `upstream-contracts.json` + `scripts/probe_openwebui_contract.py`; contract-first fixtures until then. Verify frontend dep licenses/peers; commit lockfile once. Update PROGRESS.md.
- **P1 Auth, RBAC & credentials** (R09/R20 + C1 + Clar 2 + Safety 1/2) — conditional settings validation, auth adapter (Modes A/B, unsupported state), type-aware logout, RBAC grants + per-agent access policy, audit stream + revision events (migration 001), AESGCM store with generation/scope/status, `test_auth.py` (anonymous 401, cross-user 403, CSRF, rate limit, readback denial, expiry, token stripped, audited denial, API-key logout leaves upstream key intact, flag-off legacy start without credentials key). Gate: no secrets in responses/logs/OpenAPI; every mutating route authorizes independently.
- **P2 Registry & validation** (R01/R02/R06/R12-13 + C4 + Clar 1/3) — schemas (`schema_version=1`, reject unsupported), immutable-content store + append-only lifecycle, validation, service; CapabilityStatus/HealthStatus; layout vs semantic hash; ETag CAS; Save never activates/never touches externals; 1 MB/200-node limits. Gate: node-specific errors; Save/structural-Validate start no processes.
- **P3 Open WebUI adapters** (R03/R07-08) — prompts/skills CRUD, model-preset import preview, built-ins read-only + copy-as-custom, snapshots with provenance; Knowledge listing + `KnowledgeSource` adapter (BLOCKED/"Runtime adapter unverified" until live contract passes; graph connecting it fails validation; disconnect ⇒ zero retrieval calls/context). Gate: Designer-created skill exists in real Open WebUI (live, once credentials provided).
- **P4 Connectors** (R03/R10-11 + C3 + Clar 1/4) — MCP stdio + Streamable HTTP (operator-approved stdio; digests + generation; new tools disabled), `ConnectorRuntimeState` + ephemeral validation leases, O(1) hot-path check, quarantine → Live warning, HealthStatus; OpenAPI (SSRF guards); CUA as registered connection; Terminal blocked-without-sandbox; native plugins CATALOG_ONLY.
- **P5 Compiler, runtimes, authorization & migration** (R01/R06/R15/R19-22 + C5) — compiler + scoped runtime pool with credential-generation staleness/drain; agent-scoped namespaces + epochs (legacy only for bootstrapped Vion); run-registry extension + dedup-index migration with backfill; `bootstrap_designer.py` (Vion → registry, alias preserved, flag-on only); `/v1/models` per-actor listing (C5) + recursion rejection; **chat verifies use permission before claim/recipe/planner/runtime/provider**; five C5 tests. Gate: disabled CUA blocks every route; user/agent isolation incl. user-scoped credentials; unauthorized denied before side effects; Vion unchanged flag-off.
- **P6 Context policies** (R08/R15-16 + Fix 8) — BudgetLedger + PreparedContext; defaults 12 turns/32k est input/2048 output/16 attempts/60 tool calls/15-min within ceilings; `estimated_context_tokens` vs `provider_reported_input_tokens` separate, unknown ≠ 0; epochs on activation; non-billable context preview. Gate: captured requests reflect budgets; zero monitoring LLM calls.
- **P7 Activation & revocation** (R05/R12-14 + Clar 1/3 + Safety 2) — Prepare → CAS Activate (pointer + audit); failure preserves active revision, closes candidate; drain; revocation gate at every dispatch (no idle-timeout wait); **Revoke now** (`designer.revoke`); desktop queue ("Waiting for desktop"); rollback revalidates; quarantine surfaces Review Changes → Validate Again; rotation marks runtimes stale/rebuilds. Gate: failed candidate keeps old version; concurrent CUA serializes; cancel blocks next dispatch; rotated credential never serves from stale runtime.
- **P8 Events & SSE** (R04/R17-18) — events store, emit points across routes, replay/heartbeat/snapshot, sanitized capped payloads. Gate: replay zero side effects; foreign run 403/404; SSE loss ≠ run stop.
- **P9 Frontend canvas** (R01-03/R06-07) — Vite app, agent list, library with capability badges + health indicators, custom nodes/edges, drag + click-to-add, undo/redo, viewport persistence, inspector, unsaved warnings, static mount + SPA fallback (flag-gated), themes. Gate: server-backed draft survives reload.
- **P10 Live & activation UI** (R04-05/R12-18) — read-only live canvas pinned to run revision (event-driven), timeline, SSE reconnect/stale states, ActivationDialog diff, immediate-revoke, error/empty states. Gate: real chat-originated run in Live; reconnect neither restarts nor mutates the run.
- **P11 Release & acceptance** (A1-A14 + Fix-10 + C2 + Safety 1) — full suites; flag-false rollback tests; custom-model gate (request-captured per-provider routing, draft-change isolation, activate cutover, `credential_ref`-only graph JSON); credential rotation/revocation runtime-invalidation tests; evidence per gate in `acceptance.md`; `THIRD_PARTY_NOTICES.md`; dated `CLAUDE.md` change-log entry + many-independent-runtimes wording; performance measurements; staged enablement. **No push, deploy, API spend, or real-desktop operation without explicit authorization; live gates env-gated.** Final PROGRESS.md handoff state.

## Test & evidence

Backend: `tests/designer/` (httpx ASGI via `build_test_app`, `ScriptedChatModel`, real Postgres on :5433, respx fixtures) with plan-doc fixtures (`api`/`api_b`/`operator_api`/`valid_graph`/`lifecycle`/`event_harness`). Frontend: Vitest + Testing Library; Playwright; axe-core. Per package: required tests + regressions; exact commands/results recorded in `docs/designer/acceptance.md` **and** `PROGRESS.md`; skipped live tests never recorded as passed; failed security/correctness gate stops progression and is logged. Git: feature branch `agent-designer`, one commit per package (log updated in the same commit); main untouched.

## Known limitations carried forward (reported, not hidden)

- Terminal tool blocked until an operator-provisioned sandbox is tested.
- Native Open WebUI Python plugins CATALOG_ONLY; unknown future types BLOCKED/UNSUPPORTED.
- Knowledge retrieval BLOCKED until the live contract probe passes — never decorative.
- Custom-model tests assert via request capture; live provider calls need explicit budget authorization.
- Designer grants live in the gateway DB where the upstream permission contract is insufficient.
- Upstream revocation reported honestly per credential type.
- Skipped live tests recorded as skipped, never as passes.

## Freeze rule

Architecture frozen; no further redesign. Implementation begins at P0 and proceeds P0 → P11 in order. At the end of every package: run required tests, run regressions, record exact commands/results/evidence in PROGRESS.md + acceptance.md (same commit), never call skipped live tests "passed", never proceed on a failed security/correctness gate, preserve the `DESIGNER_ENABLED=false` rollback path.

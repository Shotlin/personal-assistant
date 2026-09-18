# Agent Designer — Progress Log

**Last updated:** 2026-09-19T03:20:00Z
**Current package:** P2 — Registry & validation (complete; committed)
**Next action for a new session:** Begin P3 (Open WebUI adapters) per `docs/designer/PLAN.md`: adapters/openwebui.py + adapters/sources.py (catalog/resolve/create-update for prompts+skills from P0 fixture contracts), model-preset import preview, built-in skills read-only + copy-as-custom, immutable snapshots with provenance, Knowledge listing + BLOCKED KnowledgeSource adapter (Fix 2), test_sources.py.

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
| P3 Open WebUI adapters | Not started | |
| P4 Connectors | Not started | |
| P5 Compiler, runtimes, authorization & migration | Not started | |
| P6 Context policies | Not started | |
| P7 Activation & revocation | Not started | |
| P8 Events & SSE | Not started | |
| P9 Frontend canvas | Not started | |
| P10 Live & activation UI | Not started | |
| P11 Release & acceptance | Not started | |

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

- **Repository state:** branch `agent-designer` (checkpoint `ccc5d13` + this commit); baseline `2d6d1a3` verified; working tree has baseline-gate fixes (mypy repairs + probe lint fixes + doc updates) included in this commit.
- **Toolchain:** `export PATH="$HOME/homebrew/bin:$HOME/homebrew/opt/node@22/bin:$HOME/.local/bin:$PATH"` gives uv/colima/docker/node in a fresh shell; colima must be running (`colima start --vm-type vz`) for docker; `.env` exists (gitignored) with real CUA manifest path.
- **Last passing test run:** 2026-09-19 — `uv run pytest` → 372 passed, 4 skipped, 0 failures (~21 s); ruff clean; mypy clean.
- **Remaining P0 item:** Open WebUI authenticated-read probes — needs owner-provided account + `docker compose up -d open-webui`; then `uv run python scripts/probe_openwebui_contract.py probe ...`; C5 capture mode for model-discovery headers.
- **Entry point rule for any new session:** read PLAN.md + this log + the three source docs → continue the current package; never reconstruct requirements from memory; never redesign the frozen architecture.

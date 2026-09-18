# Agent Designer — P0 Baseline Report

**Date:** 2026-09-19 · **Repo baseline commit:** `2d6d1a3` (`2d6d1a3460ab9d8de31c631d38512521dd4b9ea0`) · **Branch at P0 start:** `main`
**Companion:** `docs/designer/PLAN.md` (frozen plan), `docs/designer/PROGRESS.md` (living log)

## 1. Document-set confirmation (Safety note 3)

All four required documents were read in full before any code change:

| # | Document | Location | Status |
|---|---|---|---|
| 1 | Agent Designer Requirements | `01_AGENT_DESIGNER_REQUIREMENTS.md` (read in planning session; pinned source baseline matches repo) | ✅ read |
| 2 | Agent Designer Implementation Plan | `02_AGENT_DESIGNER_IMPLEMENTATION_PLAN.md` | ✅ read |
| 3 | Agent Designer Visual Design | `03_AGENT_DESIGNER_VISUAL_DESIGN.md` | ✅ read |
| 4 | Implementation Plan v5 FINAL FREEZE | persisted verbatim as `docs/designer/PLAN.md` | ✅ read |

## 2. Repository baseline (verified)

- **Git state at P0 start:** clean tree, HEAD `2d6d1a3` = the exact commit the specification inspected (`2d6d1a3460ab9d8de31c631d38512521dd4b9ea0`). Only untracked local tool config (`.zcode/`) present — intentionally never committed.
- **Pins (pyproject.toml):** Python `>=3.12,<3.13`; `deepagents==0.7.15`; `langchain-openrouter==0.2.8`; `langchain-openai>=1.6,<2`; `langchain-mcp-adapters==0.3.2`; `langgraph-checkpoint-postgres==3.1.2`; `fastapi>=0.115,<1`; `psycopg[binary,pool]>=3.2,<4`; `httpx>=0.28,<1`. Dev: pytest, pytest-asyncio, respx, ruff, mypy, types-pyyaml.
- **`cryptography`:** NOT a direct dependency; version 50.0.1 already resolved in `uv.lock` as transitive (via deepagents → langchain-google-genai chain). Promotion to a direct dependency in P1 is lock-compatible.
- **Existing code integration points (verified by source review):** `src/assistant/main.py` app factory (routers `/v1/models`, `/v1/chat/completions`, `/v1/runs/{run_id}/stop` behind `require_gateway_key`; `/healthz`, `/readyz`; no CORS, no static serving); `agent/build.py:build_agent(model, checkpointer, store, skills_root, extra_tools)` — only caller of `create_deep_agent`, raises on `task`/`execute` exposure; `agent/profiles.py` no-subagent profile; `runtime/runs.py` RunStore (`run_registry`, `action_ledger`, `desktop_lease` idempotent DDL, single `AsyncConnection`); `runtime/session.py` DesktopSessionManager single lease; `api/chat_route.py` `_try_recipe_route`/`_try_planner_route`/`_run_agent_turn`; `runtime/router.py::match_local_command(text, approved_context)` capability hook (currently fed `{}`); `tools/policy.py` CUA allowlists; `memory/namespaces.py` `owui:{user}:{chat}` threads + per-user memory namespaces.
- **Database:** PostgreSQL `pgvector/pgvector:pg17` on `127.0.0.1:5433` (host→container 5433→5432), user/db/pass `assistant`; langgraph `AsyncPostgresSaver`/`AsyncPostgresStore` own their tables via `setup()`; custom tables created by idempotent DDL in `RunStore.setup()`. **No migrations framework exists** — Designer adds checksummed SQL files + ledger per plan.
- **Startup:** `scripts/start.sh` → docker compose (`postgres`, `open-webui:v0.11.3`) → `init_db.py` → Cua driver posture check → `uvicorn assistant.main:create_application` on `127.0.0.1:8787`.
- **Tests:** `tests/unit` (30 files), `tests/integration` (24), `tests/e2e` (2), helpers `build_test_app` + `ScriptedChatModel` + fake desktop driver; `tests/conftest.py` fails unless Postgres :5433 reachable; live gates env-gated (`RUN_LIVE_MODEL=1`, `RUN_LIVE_CUA=1`); pytest asyncio auto mode.
- **Open WebUI integration:** one-directional (Open WebUI → gateway as OpenAI-compatible provider); `X-OpenWebUI-*` identity headers forwarded; model alias `personal-assistant-v1`; scripts `configure_openwebui_connection.py`/`configure_openwebui_aux.py` sign in via `POST /api/v1/auths/signin` (admin pattern exists — no user-credential flow yet).
- **No prior `designer` namespace** exists anywhere in the repo (verified by search).

## 3. Baseline gate results

| Gate | Command | Result |
|---|---|---|
| Dependency sync | `uv sync --frozen` | ⛔ **BLOCKED — `uv` not installed on this machine** |
| Lint | `uv run ruff check .` | ⛔ BLOCKED (needs uv) |
| Typecheck | `uv run mypy` | ⛔ BLOCKED (needs uv) |
| Postgres | `docker compose up -d postgres` | ⛔ BLOCKED — Docker runtime absent |
| Tests | `uv run pytest` | ⛔ BLOCKED (needs uv + Postgres) |

**Toolchain findings (2026-09-19, verified by direct checks):**

| Requirement | Expected | Found |
|---|---|---|
| uv (Python 3.12 toolchain) | any recent | ❌ absent (no ~/.local/bin, ~/.cargo/bin, Homebrew paths; `python3 -m uv` fails; no repo `.venv`) |
| Python 3.12 | managed by uv | system python3 is 3.9.6 (Xcode CLT) — unusable for repo |
| Docker runtime | compose-capable | ❌ absent (`docker` not found; ports 5433/3000/8787 all closed) |
| Node.js | ≥ 22.12 (Vite baseline) | ❌ absent |
| npm | matches Node | ❌ absent |
| Homebrew | needed to install the above conveniently | ❌ absent (Xcode CLT present ✓, arm64 ✓) |
| git | any | ✅ present |
| Xcode CLT | any | ✅ present |

**No baseline test has been run yet.** These are environment gaps, not code failures — nothing about the repository at `2d6d1a3` has been contradicted. Per the freeze rule, the baseline gate must pass before P1 begins; the blocker is recorded in `PROGRESS.md` and requires owner authorization for machine-level installs.

## 4. Open WebUI upstream contract status

- **Status: PENDING PROBE** — contract-first until the owner provides a test account and a running stack. Probe script: `scripts/probe_openwebui_contract.py`. Results will populate `docs/designer/upstream-contracts.json` (scaffold committed).
- **C5 model-discovery capture:** the probe includes a capture-server mode to record the exact HTTP request Open WebUI v0.11.3 sends to the gateway for model listing (headers, auth behavior, identity presence). Not yet executed (needs running stack + authorization to repoint the Open WebUI connection temporarily).
- Until verified: all Open WebUI adapters ship against recorded fixtures; Knowledge retrieval adapter remains BLOCKED ("Runtime adapter unverified"); noDesigner route trusts model-discovery identity for authorization.

## 5. Frontend dependency license pre-verification (resolution pending Node)

Licenses verified from upstream project documentation (2026-09-19); exact versions will be resolved **once** with a committed `package-lock.json` after Node install — never invented:

| Package | License (per project docs) | Notes |
|---|---|---|
| `@xyflow/react` | MIT | `proOptions={{hideAttribution:true}}` documented; maintainers' attribution page separately requests Pro subscription — discrepancy recorded per plan §Fix 11; copyright notices retained in `THIRD_PARTY_NOTICES.md` |
| `zustand` | MIT | |
| `@tanstack/react-query` | MIT | |
| `react`, `react-dom` | MIT | |
| `react-router-dom` | MIT | |
| `@radix-ui/react-*` | MIT | dialog, tabs, dropdown-menu, tooltip |
| `lucide-react` | ISC | |
| `codemirror`, `@codemirror/lang-markdown` | MIT | |
| `vite` | MIT | Node 22.12+ baseline per plan |
| `vitest`, `jsdom`, `@testing-library/*` | MIT | |
| `@playwright/test` | Apache-2.0 | |
| `@axe-core/playwright` | MPL-2.0 | |
| `openapi-typescript` | MIT | |

Peer-requirement cross-check happens at P0 lockfile resolution (npm engine warnings treated as gate failure).

## 6. P0 exit criteria (from plan)

- [x] Document-set confirmed (§1)
- [x] Git/pins baseline recorded (§2)
- [ ] Baseline gates pass and are recorded above (§3) — **blocked on toolchain**
- [ ] Open WebUI probes recorded in `upstream-contracts.json` (§4) — **blocked on account + stack**
- [ ] Frontend deps resolved once, lockfile committed (§5) — **blocked on Node**
- [x] PLAN.md + PROGRESS.md persisted with document confirmation

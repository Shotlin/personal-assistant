# CLAUDE.md

Guidance for coding agents working in this repository. See also
[README.md](README.md) for the human-facing overview and
[PROJECT_GRAPH.md](PROJECT_GRAPH.md) for the architecture map.

## What this is

**Sani** — a local desktop AI assistant (macOS today). One installable
app: voice + text in, reasoning and computer control out. The only thing
that leaves the machine is model inference through one
`OPENROUTER_API_KEY` (Deep Agent LLM + JEV decision model).

Non-negotiable product rules:

- Sani is desktop software, not a web product. No Open WebUI, no Agent
  Designer, no Docker, no PostgreSQL server in the shipping path.
- JEV (Velo's decision engine) is a structured classifier (Noul/Choice/
  Score). Never turn it into a free-form chat model, never add an LLM
  fallback for it.
- Velo's loop is bounded: steps, runtime, repeat detection, cancellation,
  fail-closed ASK_USER/STOP/FAILED. Preserve it.
- Memory writes pass the secret-screening policy; API keys never enter
  memory, logs, or the database.

## Run / verify

```bash
uv sync && uv run pytest            # full Python suite (core tests need no Docker)
uv run python -m assistant.core     # sani-core standalone over stdio IPC
uv run python scripts/run_velo.py "Open Chrome and search WhatsApp Web"
uv run ruff check src tests && uv run mypy src tests
cd sani && npm run build            # renderer (tsc + vite)
cd sani/src-tauri && cargo test     # Rust host + sani-core client tests
```

## Repo layout

```
sani/               Tauri 2 desktop app: React renderer (pill + panel),
                    Rust host (voice state machine, hotkey, mic, STT
                    sidecar, SQLite UI history, sani_core.rs IPC client)
src/assistant/
  core/             sani-core sidecar: framed-JSON stdio protocol, agent
                    registry (deep, velo), desktop/CUA status + permission
                    preflight
  agent/            Deep Agent assembly (system prompt, build, context)
  velo/             Quick-control agent: JEV decision engine (the ONLY
                    TypeSafe/OpenRouter-JEV module), CUA adapter, loop
  memory/           Local SQLite store/checkpointer + Postgres dev backend,
                    namespaces (sani: identity), secret-screening policy
  runtime/          Desktop sessions, run/action ledger (SQLite + Postgres
                    dev port), recipes/planner fast paths
  models/           Provider factory (openrouter / generic / openai)
  tools/            Cua MCP connection, allowlist, result normalization
  skills/           Read-only SKILL.md procedures
  observability/    JSON logging with secret redaction
  api/, main.py     LEGACY FastAPI gateway — still the live voice path
                    until the host finishes switching to sani-core IPC;
                    remove at cutover (do not extend it)
config/             cua-capabilities.yaml (bounded manifest), logging.yaml
scripts/            run_velo.py, verify_cua.py, gateway-era dev scripts
docs/               Sani storage-migration map
tests/              unit / integration / e2e / velo — Sani + core coverage
```

## Legacy boundary (temporary)

`src/assistant/api/` + `src/assistant/main.py` + `agent.rs`/`activity.rs`
HTTP are the OLD path (Sani → localhost:8787). The NEW path
(`sani_core.rs` ↔ `assistant.core`) exists and is tested. Until the host's
voice flow moves onto it: don't delete the gateway, don't add features to
it either. Postgres (`memory/postgres.py`, `runtime/runs.py`, `DATABASE_URL`)
is a dev-only compatibility backend; the shipping backend is SQLite
(`MEMORY_BACKEND=sqlite` + `SANI_DATA_DIR`).

## Conventions

- Python 3.12, `uv run` for everything; ruff (line 100) + strict mypy must
  stay clean; pytest asyncio_mode=auto.
- Rust: `cargo fmt`/`clippy` clean for files you touch.
- Never commit secrets; `.env` is local-only.

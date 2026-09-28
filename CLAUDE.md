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
  api/, main.py     DEAD since the sani-core cutover: the host no longer
                    speaks HTTP. Retained only for the Postgres-era
                    integration tests; nothing in the shipping path uses it
config/             cua-capabilities.yaml (bounded manifest), logging.yaml
scripts/            run_velo.py, verify_cua.py, gateway-era dev scripts
docs/               Sani storage-migration map
tests/              unit / integration / e2e / velo — Sani + core coverage
```

## Runtime path (post-cutover)

Voice and typed turns both go: `app_state::begin_turn` →
`runtime::stream_turn` → `sani_core::run_turn` → framed `run.start` →
`assistant.core`. The host spawns and supervises the sidecar at startup and
builds its environment (Keychain credentials, embedded SQLite, absolute CUA
manifest path) — there is no localhost server anywhere in the shipping path,
and `agent_base_url` / gateway-key settings are gone.

Every event frame carries `agent_id`; `agent.started/progress/token/
handoff/completed/cancelled/failed` are the contract. Agent identity comes
from the sidecar's `AgentRegistry` (`agents.list` → `core_agents`), never from
a frontend roster.

`src/assistant/api/` + `src/assistant/main.py` are now unused by the app, and
Postgres (`memory/postgres.py`, `runtime/runs.py`, `DATABASE_URL`) is a
dev-only compatibility backend for them; the shipping backend is SQLite
(`MEMORY_BACKEND=sqlite` + `SANI_DATA_DIR`).

## Jarvis Phase 1 (missions) — default off

`src/assistant/missions/` adds durable mission ownership ABOVE the existing
Velo fast path (planning package: `docs/astra/jarvis-next-2026-09-27-58dac9c`,
file 03 is the architecture; `docs/verification/phase1/` holds evidence):

- `JARVIS_MISSIONS_ENABLED=false` by default. With it on, the `velo` slot is
  a mission-backed entry: same Velo recipes/adapter/policy, same zero-model
  fast route, plus durable request dedup, bounded work packets, scope/
  budget/permit gates, and a deterministic acceptance gate.
- Contracts (`jarvis.v1`) are strict: unknown fields in authority records
  reject; UNKNOWN effect outcome is never reported as success.
- Mission state lives in the SAME embedded `sani.db` (`missions*` tables,
  own migration ledger `mission_schema_migrations`). The Rust UI history
  stays a separate projection.
- The Controller is the EXISTING Deep Agent in role-scoped invocations
  (PLAN/RECOVER/REVIEW/CHAT). It proposes, never grants scope; a raw CUA
  call from a Controller role is refused. No per-click model calls.
- `SANI_TTS_ENABLED=false` by default; `sani/src-tauri/python/sani_tts.py`
  is the output worker (framed stdio, no credentials, no egress). Engine
  selection is pending owner audition (`docs/verification/phase1/VOICE_SELECTION.md`).
- `RSI_MODE` is immutable `observation_only`: the Observer
  (`missions/observer.py`) is read-only with a separate recommendation
  sink; no experiment runner exists.
- Phase 1 acceptance evidence: `.venv/bin/python scripts/verify_phase1.py
  --suite <unit|integration|performance|rust|renderer|desktop|voice>`.
  Live suites need an owner-issued `approved-test-config.json` + `--allow-live`.

## Conventions

- Python 3.12, `uv run` for everything; ruff (line 100) + strict mypy must
  stay clean; pytest asyncio_mode=auto.
- Rust: `cargo fmt`/`clippy` clean for files you touch.
- Never commit secrets; `.env` is local-only.

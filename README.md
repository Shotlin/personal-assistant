# Sani — Local Desktop AI Assistant

Sani is a real, locally installed desktop application (Tauri 2 + React +
Rust on macOS) that talks and types, reasons, and controls the computer:

```
Sani.app (Tauri: voice UI, panel UI, global hotkey, settings)
   │
   ├── Local STT (Moonshine sidecar over stdio; mic audio never leaves the machine)
   │
   ├── sani-core (bundled Python sidecar; framed-JSON IPC over stdin/stdout)
   │     ├── Deep Agent (LangChain reasoning + skills + memory)  → OpenRouter
   │     ├── Velo (quick computer control: JEV decisions + CUA actions)  → OpenRouter JEV / local CUA
   │     └── agent registry (deep, velo) + run lifecycle + cancellation
   │
   └── Local data: SQLite (sani.db) memory/checkpoints/run history,
        skills, STT model cache, logs — under the platform app-data dir
```

One external credential: **`OPENROUTER_API_KEY`** (drives both the Deep
Agent model and the JEV decision model). Everything else is local.

## What ships

| Piece | Where | Notes |
| --- | --- | --- |
| Desktop app | `sani/` (Tauri + React renderer + Rust host) | voice state machine, hotkey, settings, SQLite UI history |
| sani-core IPC | `sani/src-tauri/src/sani_core.rs` ↔ `src/assistant/core/` | 4-byte length-prefixed JSON over stdio; no localhost web server in the new path |
| STT sidecar | `sani/src-tauri/python/sani_stt.py` → bundled binary | Moonshine streaming + VAD/endpointing, fully local |
| Velo | `src/assistant/velo/` | OBSERVE→DECIDE→ACT→VERIFY loop; JEV is a structured classifier, never a chat model; bounded steps/runtime/cancellation |
| Deep Agent | `src/assistant/agent/` | LangChain deep agent, skills, secret-screened memory |
| Memory | `src/assistant/memory/local.py` + `src/assistant/runtime/runs_local.py` | embedded SQLite (`sani.db`), WAL; secret-screening policy preserved |
| CUA | `src/assistant/tools/` + `config/cua-capabilities.yaml` | bounded driver, capability manifest, per-run budgets |

## Development

```bash
uv sync                                   # Python side
uv run pytest                             # full suite (no Docker needed for core tests)
uv run python -m assistant.core          # run sani-core standalone over stdio
uv run python scripts/run_velo.py "Open Chrome and search WhatsApp Web"

cd sani && npm install && npm run build   # renderer
cd sani/src-tauri && cargo check && cargo test
```

Environment: copy `.env.example` to `.env` and set `OPENROUTER_API_KEY`
(the one cloud credential), plus CUA settings for computer control during
development. Local persistence is selected with `MEMORY_BACKEND=sqlite` +
`SANI_DATA_DIR=...` (the packaged app sets this itself).

## Legacy note

The former Open WebUI / Agent Designer / Docker-PostgreSQL server product
was removed (see git history). The Sani host now speaks **only** sani-core
IPC: the Tauri app spawns and supervises the sidecar itself, and both voice
and typed turns go through a framed `run.start`. The old FastAPI gateway
(`src/assistant/main.py`, `src/assistant/api/`) is no longer used by the app
and is retained only while the Postgres-era integration tests still reference
it. See `docs/sani-storage-migration.md` for the storage map.

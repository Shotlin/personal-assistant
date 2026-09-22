# Sani Storage Migration Map (master doc section 29)

Storage audit performed 2026-09-22 before implementation. Each row maps
where a piece of persistent state lives today, where it goes in the local
Sani product, how it migrates, and its retention policy.

Product application-data root (Tauri identifier `app.sani.local`):
`~/Library/Application Support/app.sani.local/` on macOS,
`%LOCALAPPDATA%\app.sani.local\` equivalent on Windows — always resolved
through the platform API, never hardcoded. The embedded database is
`sani.db` under this root.

## Current → new map

| Data | Current location | New local location | Migration method | Retention |
| --- | --- | --- | --- | --- |
| UI conversation history | `sani-history.db` (SQLite, WAL, `conversations` + `messages`, created in `sani/src-tauri/src/history.rs`) | `sani.db` (`conversations`, `messages` tables, same shape) | Tauri `ATTACH DATABASE` copy or open-and-insert on first run of the new build; schema is compatible, both sides SQLite | Full history until user deletes a conversation; no server copy |
| Deep Agent checkpoints | PostgreSQL (`langgraph-checkpoint-postgres`, thread-scoped, thread ids `owui:<user>:<chat>`) | `sani.db` (`langgraph-checkpoint-sqlite` tables via `AsyncSqliteSaver`) | Checkpoints are session-continuity state: NOT migrated; new conversations start clean on the local DB (explicit migration decision, §25) | Newest checkpoints per thread; prune by age/count |
| Long-term agent memory | PostgreSQL `AsyncPostgresStore` (`("users", <user>, "assistant-memory")` namespaces) | `sani.db` `store_items` table via `SqliteStore` (`src/assistant/memory/local.py`) | Export rows from Postgres `store` table → insert into `store_items` (namespace `\x1f`-joined, JSON values) — one-time script when a real deployment migrates | User-owned; user can view/delete; secret policy enforced on write |
| Memory secret screening | `assistant/memory/policy.py` (backend-agnostic `PolicyStoreBackend`) | Unchanged — applies to the SQLite store identically (covered by tests) | N/A | N/A |
| Thread/namespace identity | `owui:<user_id>:<chat_id>` (Open WebUI legacy, §25) | `sani:<conversation_id>` / `agent:<agent_id>:sani:<conversation_id>` | Rename at the `assistant.memory.namespaces` boundary when sani-core becomes the entry point; `owui:` ids keep resolving read-only for history | Same as conversation history |
| Run metadata (run registry, action ledger, desktop lease) | PostgreSQL (`src/assistant/runtime/runs.py` `RunStore`) | **Ported**: `sani.db` via `SQLiteRunStore` (`src/assistant/runtime/runs_local.py`), active when `MEMORY_BACKEND=sqlite`; epoch-float timestamps, single-writer lock preserves `claim()` dedup | Fresh-schema create (`PRAGMA user_version`); run history is operational metadata, not migrated | Runs: keep 30 days; ledger: follow run |
| Sani settings (non-secret) | `settings.json` in `app_config_dir` (hotkey, mic, base URL, STT model…) | Unchanged location, gains `runtime.json`-style section for core config | Already local; no migration | Current-only (rewrite on change) |
| API credentials | macOS Keychain (`sani-agent-key`) → 0600 `agent_key` fallback → `.env` bootstrap | Unchanged mechanism; OpenRouter key stored the same way (never in sani.db, memory, logs, or skills) | Already local | Until user clears |
| STT models | `~/Library/Caches/moonshine_voice` (library-managed download cache) | Unchanged (platform cache dir is the correct location) | None | Re-downloadable; safe to clear |
| Logs | `~/Library/Logs/app.sani.local/sani.log`, `sani-stt.log` | Unchanged, plus `sani-core.log` when the sidecar lands | None | Rotate/age out (30 days) |
| CUA artifacts (screenshots) | `var/artifacts` under the repo for the gateway | Sani App Data `artifacts/` (configured via `CUA_ARTIFACT_DIR`) | Path change only | 7 days |
| Skills | Repo `src/assistant/skills` mounted by the gateway | Packaged resources → copied to App Data `skills/` on first run (§8) | Bundle-time copy; first-run copy for editable skills | Follow app version; user edits preserved |

## Decisions recorded

1. **One database file.** Checkpoints and long-term memory already share
   `sani.db` (`open_local_memory_resources` opens both on the same file,
   WAL mode). Run metadata joins it in Stage D.
2. **Checkpoints are not migrated** from Postgres: they are conversation
   *continuity*, not user-authored data; threads restart cleanly under the
   new identity scheme (§25 makes the old identity legacy anyway).
3. **Long-term memory IS user-authored data** and gets a real one-time
   export/import script when a deployment with existing Postgres memory
   moves to the local build.
4. **Policy travels with the backend swap** because it wraps the LangGraph
   `BaseStore` contract, which `SqliteStore` implements — no security
   regression (tested: secret writes rejected, bytes never reach the file).
5. **Docker/PostgreSQL leave the runtime path** in `MEMORY_BACKEND=sqlite`
   mode: checkpointer, long-term memory, and run metadata all live on the
   one embedded `sani.db`. The gateway's `postgres` default remains as the
   legacy rollback path until Stage D deletes it.

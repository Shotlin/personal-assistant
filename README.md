# Personal Assistant (Phase 1)

One reliable general-purpose personal assistant, reachable through **Open
WebUI**, built on a **LangChain Deep Agent**, with durable **PostgreSQL**
thread state and user-scoped long-term memory, a configurable model
provider (OpenRouter first), and safe computer control through **Cua
Driver** in bounded mode.

This repository implements exactly Phase 1 of
`PHASE_01_CORE_PERSONAL_ASSISTANT.md`. Phase 2 features (vector search,
document ingestion, subagents, more apps) are out of scope.

## Architecture

```
Open WebUI (docker)  ->  FastAPI gateway (native, OpenAI-compatible /v1)
                            ->  one Deep Agent (LangChain)
                                  ->  PostgreSQL (checkpoints + memory)
                                  ->  Cua Driver MCP (native, bounded)
                                  ->  ModelProviderFactory (OpenRouter /
                                      generic OpenAI-compatible / OpenAI later)
```

- **Open WebUI** is only the chat UI (v0.11.3, pinned).
- The **gateway** exposes `/v1/models` and `/v1/chat/completions` with the
  OpenAI error contract; it maps Open WebUI identity headers to a scoped
  thread (`owui:<user_id>:<chat_id>`).
- **One Deep Agent**: the general-purpose subagent is disabled (no `task`
  tool) and the host-shell `execute` tool is excluded.
- **CUA** runs bounded with a capability manifest; the application
  additionally allowlists tools and budgets mutating actions (50 per run,
  15-minute wall clock).

## Layout

```
src/assistant/
  api/         OpenAI-compatible gateway (auth, identity, turns, SSE)
  agent/       Deep Agent assembly (system prompt, profile, build, context)
  models/      provider factory (openrouter / generic_openai_compatible / openai)
  memory/      Postgres checkpointer + store, namespaces, write policy
  tools/       CUA MCP loading, application-side allowlist, action budget
  skills/      read-only SKILL.md procedures (developer-authored)
  observability/  JSON logging with secret redaction
config/        logging.yaml, cua-capabilities.yaml
scripts/       init_db.py, verify_cua.py, smoke_openwebui.py, run_agent_api.sh
tests/         unit / integration / e2e
```

## Prerequisites

- macOS 14+ (verified on macOS 26.2, Apple Silicon) with Docker Desktop running.
- `uv` (manages Python 3.12 for you; no system Python needed).
- An **OpenRouter API key** (or any OpenAI-compatible provider credentials).

## Setup

```bash
# 1. Install exact locked dependencies (never re-resolves versions).
uv sync --frozen

# 2. Configure.
cp .env.example .env
#    then set at minimum:
#      AGENT_GATEWAY_API_KEY=<generate a long random string>
#      OPENROUTER_API_KEY=<your key>
#      MODEL_NAME=<a cheap, tool-capable OpenRouter model id>
#      CUA_CAPABILITY_MANIFEST_PATH=<absolute path to config/cua-capabilities.yaml>
#      CUA_ENABLED=true   (leave false until Cua Driver is installed)

# 3. Start PostgreSQL (and Open WebUI).
docker compose up -d

# 4. Initialize database schemas (idempotent).
uv run python scripts/init_db.py
```

## Cua Driver (computer control)

Pin: **cua-driver-rs v0.28.2** (deliberate selection, recorded 2026-09-17:
the stable channel had moved past the spec's v0.28.1 reference when the
driver was installed; the capability manifest below is validated against
v0.28.2). Any future update must be recorded here and revalidated.

```bash
# Install (places CuaDriver.app in /Applications, symlink in ~/.local/bin).
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
cua-driver --version          # must report the recorded pin (0.28.2)

# Grant macOS permissions (System Settings -> Privacy & Security):
#   - Accessibility        -> enable for CuaDriver.app
#   - Screen Recording     -> enable for CuaDriver.app
cua-driver permissions status

# Start the bounded daemon (TCC attribution stays with CuaDriver.app):
open -n -g -a CuaDriver --args serve \
  --permission-mode bounded \
  --capability-manifest "/ABSOLUTE/PATH/config/cua-capabilities.yaml" \
  --approve-capability-manifest

# Verify from this repo (connectivity, filtering, allowed action):
uv run python scripts/verify_cua.py --live
```

The manifest allowlists: Calculator, Google Chrome, Terminal.app (Claude
Code), plus observation/screenshot/click/type/scroll/press-key/launch
capabilities and approved browser origins. Everything else is refused
natively; the application filter drops any tool outside the allowlist.

## Running

```bash
# Native gateway (the Open WebUI containers reach it via host.docker.internal).
./scripts/run_agent_api.sh          # listens on 127.0.0.1:8787

# Smoke checks (gateway + UI + auth; see script docstring).
uv run python scripts/smoke_openwebui.py
```

Shutdown order: stop the gateway (Ctrl-C) -> quit/stop the CuaDriver
daemon -> `docker compose stop`.

## Open WebUI connection

- URL: `http://host.docker.internal:8787/v1`
- API key: the `AGENT_GATEWAY_API_KEY` from `.env`
- Model: `personal-assistant-v1`
- The compose file enables user-info header forwarding
  (`ENABLE_FORWARD_USER_INFO_HEADERS=true` -> `X-OpenWebUI-User-Id`,
  `-Name`, `-Email`, `-Role`).
- Turn-lineage headers (`X-OpenWebUI-Chat-Id`, `X-OpenWebUI-Message-Id`,
  `X-OpenWebUI-User-Message-Id`, `X-OpenWebUI-User-Message-Parent-Id`,
  `X-OpenWebUI-Task`) are **per-connection custom headers** stored in the
  Open WebUI database in v0.11.3 (there is no environment variable for
  them). Apply them with:

  ```bash
  uv run python scripts/configure_openwebui_connection.py \
      --email <admin email> --password <admin password>
  ```

Verify by sending one chat message in the UI and checking the gateway
run log: `run_started` must carry `identity_source: openwebui` plus
`chat_id` and `user_message_id`.

First run: create the Open WebUI admin account at `http://127.0.0.1:3000`,
then open Admin Settings -> Connections and confirm the gateway
connection exists (it is preconfigured via `OPENAI_API_BASE_URL`/`KEY`).

## Switching model provider

Change only `.env` (no code changes):

```dotenv
# OpenRouter (default)
MODEL_PROVIDER=openrouter
MODEL_NAME=<openrouter model id>
OPENROUTER_API_KEY=<key>

# Any OpenAI-compatible Chat Completions provider
MODEL_PROVIDER=generic_openai_compatible
MODEL_BASE_URL=https://provider.example/v1
MODEL_API_KEY=<key>
MODEL_NAME=<model id>

# OpenAI (later, optional)
MODEL_PROVIDER=openai
MODEL_NAME=<model id>
OPENAI_API_KEY=<key>
```

## Tests

```bash
docker compose up -d postgres      # integration/e2e tests need it
uv run pytest                      # unit + integration + e2e (no live model/CUA)
```

Live checks (spend real tokens / need the driver). The one-command path
once `.env` has the key and CuaDriver has TCC grants:

```bash
uv run python scripts/pick_model.py          # list cheap tool-capable models
uv run python scripts/pick_model.py --set <model-id>   # set MODEL_NAME
./scripts/go_live.sh                         # run every live check in order
```

Individual checks:

```bash
RUN_LIVE_MODEL=1 uv run pytest tests/integration/test_live_model.py
RUN_LIVE_CUA=1 uv run pytest tests/e2e/test_cua_calculator.py
uv run python scripts/verify_cua.py --live
```

## Diagnostics without secrets

- Logs are single-line JSON on stderr with API keys/passwords/tokens
  redacted (`[REDACTED]`).
- Run records carry `run_id`, hashed user id, chat/thread ids, provider,
  model, status, CUA tool names, and error codes — never prompt bodies,
  screenshots, or keys.
- For a clean diagnostic bundle: gateway log tail, `docker compose ps`,
  `cua-driver permissions status`, and the pytest summary. Never share
  `.env`.

## Troubleshooting

| Symptom | First checks |
| --- | --- |
| Chat shows `Model "" was not found` | The gateway process is not running (Open WebUI's connection registry is empty). Start it: `./scripts/run_agent_api.sh` — then retry the chat. |
| Gateway exits at startup | `.env` validation error printed (provider key, manifest path, CUA mode) |
| `/readyz` returns 503 | `docker compose up -d postgres`; check `DATABASE_URL` |
| Model picker empty in Open WebUI | gateway reachable from container? `docker compose exec open-webui curl -s http://host.docker.internal:8787/healthz` |
| CUA tools missing at startup | driver installed? `cua-driver --version`; manifest path absolute and existing; bounded daemon running |
| Memory rejected warning in logs | the memory write policy blocked a secret-like write (by design) |

## Security posture (Phase 1)

1. CUA runs bounded only; unrestricted is rejected at startup.
2. Tool allowlist enforced twice (native manifest + application filter).
3. Gateway, Open WebUI, and PostgreSQL all listen on loopback only.
4. Provider keys never leave the gateway process; Open WebUI only holds
   the gateway key.
5. Agent has no host shell; file tools operate on a virtual scratch
   backend, read-only skills, and the Postgres-backed memory store.
6. Memory writes pass a secret-screening policy; memory events never log
   content.
7. Computer tool output is untrusted evidence; it never changes policy.
8. External irreversible actions fail closed.
9. Stopping the gateway process immediately prevents new actions.

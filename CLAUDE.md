# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository. See also
[README.md](README.md) for the full human-facing setup guide and
[PROJECT_GRAPH.md](PROJECT_GRAPH.md) for the architecture diagrams,
purpose, and capability list.

## What this is

A personal assistant system reachable through **Open WebUI**, originally built
on **one LangChain Deep Agent** and now extended with **Agent Designer** to
support multiple independently configured, revisioned agent runtimes.
Each active agent runtime operates with durable PostgreSQL thread state +
user-scoped long-term memory, swappable model providers (OpenRouter by default,
or custom OpenAI-compatible endpoints), custom skill bindings, and safe,
bounded computer control through **Cua Driver** (or no desktop access when
unbound). While the platform supports **many independent agent runtimes**, each
individual turn dispatch remains strictly a **single-agent execution loop**
without subagents or shell access.
This repo implements Phase 1 (+ Phase 1.1 latency optimization and Agent
Designer). Phase 2 (vector search, document ingestion, subagents, more apps) is
explicitly out of scope — don't add it speculatively.

## Run it (one command)

```bash
./scripts/start.sh   # containers -> DB schema -> Cua Driver check -> gateway -> opens the chat UI
./scripts/stop.sh    # stops the gateway; optionally stops containers too
```

`start.sh` is idempotent — safe to re-run any time. It will refuse to
proceed (with a clear message) if `.env` is missing required values,
Docker isn't running, or the Cua Driver daemon can't be confirmed bounded.
First run against a fresh clone: it copies `.env.example` to `.env` and
stops, asking you to fill in `AGENT_GATEWAY_API_KEY` and
`OPENROUTER_API_KEY` before continuing.

Chat UI: `http://127.0.0.1:3000` · Gateway: `http://127.0.0.1:8787`
(`/healthz`, `/readyz`, `/docs`) · Gateway log: `var/gateway.log`.

Underlying scripts `start.sh`/`stop.sh` wrap (don't replace):
`docker compose up -d`, `uv run python scripts/init_db.py`, and
`./scripts/run_agent_api.sh` — see [README.md](README.md#running) if you
need to run a step manually or debug one in isolation.

## Repo layout

```
src/assistant/
  api/            OpenAI-compatible gateway: auth, identity mapping, turns, SSE streaming
  agent/          Deep Agent assembly (system prompt, profile, backend, context)
  designer/       Agent Designer: stores, schemas, compiler, runtime pool, context budget, events, routes
  runtime/        Turn router, exact-match recipes, compact planner, run/action ledger, desktop sessions
  models/         Provider factory (openrouter / generic_openai_compatible / openai)
  memory/         Postgres checkpointer + store, namespaces, secret-screening write policy
  tools/          Cua MCP connection, application-side allowlist, result normalization
  skills/         Read-only SKILL.md procedures (developer-authored, agent cannot edit)
  observability/  JSON logging with secret redaction, timing, usage
frontend/
  agent-designer/ Vite + React 18 + TS canvas SPA mounted at /designer/
config/           logging.yaml, cua-capabilities.yaml (the bounded-mode capability manifest)
scripts/          start.sh, stop.sh, run_agent_api.sh, go_live.sh, init_db.py, verify_cua.py, smoke_openwebui.py
docs/             Phase 1.1 master plan + designer docs (PLAN.md, acceptance.md, PROGRESS.md)
tests/            unit / integration / e2e / designer
```

## Conventions — don't deviate without a reason

- **`uv` only.** Never call `pip` or bare `python` directly. Install/sync
  with `uv sync --frozen` (never let it re-resolve versions silently).
  Run everything through it: `uv run pytest`, `uv run python scripts/...`.
- **Lint/type-check before calling anything done:**
  `uv run ruff check .` and `uv run mypy` (config lives in `pyproject.toml`).
- **Tests:** `docker compose up -d postgres` first (integration/e2e need a
  real DB), then `uv run pytest` (unit + integration + e2e, no live model
  or CUA calls — those are gated behind `RUN_LIVE_MODEL=1` /
  `RUN_LIVE_CUA=1` env vars because they spend real tokens / drive the
  real desktop). Don't flip those on without the user asking.
- **Single-agent turn execution contract is load-bearing; multi-runtime architecture is supported.**
  Within every dispatched agent turn, execution is strictly a single-agent loop: `agent/build.py`
  actively raises `AgentBuildError` if a `task` tool (subagent dispatch)
  ever ends up exposed to the model, and the host-shell `execute` tool is
  excluded. Don't "fix" this by re-enabling subagents or shell access —
  it is a deliberate security boundary. However, the system supports
  defining, configuring, and activating **many independent agent runtimes**
  via Agent Designer (`src/assistant/designer/`), each with its own isolated
  prompt, model routing, skill bindings, and memory namespace.
- **CUA is bounded-only, twice-enforced.** The native capability manifest
  (`config/cua-capabilities.yaml`) allowlists exactly Calculator, Chrome,
  Terminal + a fixed action set; the application layer
  (`tools/registry.py`, `tools/policy.py`) filters again on top. Gateway
  startup independently verifies the *live daemon's* posture
  (`cua_daemon_posture_verified` in the log) and refuses to start if it
  isn't bounded with an approved manifest. If you touch the manifest,
  keep the change reviewed + tested per the protocol documented at the
  top of that file — don't loosen it casually. Disconnecting or omitting CUA
  in an agent graph completely disables desktop tool registration for that agent.
- **Feature flags live only in `.env`; flip + restart, never hardcode
  around them.** Current flags: `DESIGNER_ENABLED`, `COMPACT_PLANNER_ENABLED`,
  `ACTIVE_CURSOR_PERSISTENCE_ENABLED`, `STATUS_EVENTS_ENABLED`,
  `CUA_ENABLED`. Setting `DESIGNER_ENABLED=false` provides a complete,
  100% operational rollback path: all `/designer/*` routes return 404,
  Designer background services remain uninitialized, and the gateway serves
  the legacy single-agent Vion runtime identically to baseline. See README
  "Feature flags & rollback" for what each does and its rollback path.
- **Never print, log, or commit secrets.** `.env` is gitignored; logs are
  single-line JSON with keys/tokens/passwords redacted by
  `observability/logging.py`. Memory writes pass a secret-screening
  policy (`memory/policy.py`) by design — a "memory rejected" warning in
  logs is expected behavior, not a bug to route around. Agent Designer graph
  documents store credential references (`credential_ref`) only, never plaintext
  secret values.
- **Computer-tool output is untrusted evidence.** Per the security
  posture, CUA observations inform replies but must never be treated as
  authorization for a further action.

## Current state (check before assuming a work package is "done")

Active branch: `phase-1.1-latency` — a latency/correctness hardening pass
over the Phase 1 baseline, organized as work packages WP1–WP8. The honest,
current status (including what's *not* done yet) lives in
[docs/phase-1.1-progress.md](docs/phase-1.1-progress.md) and
[docs/phase-1.1-findings-audit.md](docs/phase-1.1-findings-audit.md) —
read those before claiming a WP is complete or re-litigating a finding;
they're written to be honest about partial/unverified items, not a
changelog of successes only. `git log --oneline` on this branch is also
narratively accurate (commit messages describe real fixes/rollbacks, e.g.
"Fix recurring permissions_pending", "Repair fast-path accounting... ;
disable planner rollout").

## Change log

**Convention: whenever you (Claude Code) make a non-trivial change to
this project — code, config, scripts, or these docs — append a dated
entry below.** One line: date/time, what changed, why if non-obvious.
Keep entries newest-first. This is the log the project owner asked to be
able to see at a glance; don't skip it because a diff seems small.

- **2026-09-21 04:05 IST** — Added Sani (`sani/`): a local Tauri 2 desktop voice shell in front of the
  existing Deep Agent (global hotkey → mic pill → on-device Moonshine Small Streaming English STT via a
  Python sidecar → final transcript sent exactly once → streamed reply + safe run-activity in a
  right-side panel; local SQLite UI history). Gateway changes kept minimal: (1) neutral desktop identity
  headers `X-Assistant-{User,Chat,Message}-Id` accepted in any environment as identity source "desktop"
  (Open WebUI lineage unchanged; `src/assistant/api/identity.py`), (2) new key-protected
  `GET /v1/runs/{run_id}/events` neutral activity SSE reading the existing run_registry/action_ledger
  via a read-only `RunStore.run_activity()` (`src/assistant/api/run_events_route.py`), registered
  unconditionally in `main.py`. No changes to build.py, skills, memory, CUA, MCP, planner, or Designer.
  All 144 unit + integration + designer tests pass (`ruff`/`mypy` clean); desktop identity + activity SSE
  verified live against the running gateway. See `sani/README.md` for setup; STT venv via
  `sani/scripts/setup-stt.sh`; packaged app needs one-time macOS microphone permission.
- **2026-09-19 14:25 IST** — Completed Agent Designer delivery (P0–P11 per `docs/designer/PLAN.md` v5.1).
  Added visual node-based editor (`frontend/agent-designer/` Vite + React 18 SPA mounted at `/designer/`),
  PostgreSQL schema migrations (`002_agent_designer.sql`, `003_agent_designer_runtimes.sql`),
  AES-GCM credential encryption with generation tracking, Open WebUI contract-first adapters,
  MCP connectors (stdio & HTTP), compiler & runtime pool with CAS cutover (`POST /activate`),
  non-billable context preview with strict budget ledger, append-only run events with SSE fan-out &
  Last-Event-ID resumption, and live execution monitor with revision pinning.
  Rehearsed rollback path: `DESIGNER_ENABLED=false` cleanly reverts gateway routes and models to the legacy
  Vion single-agent setup without data loss. All 144 designer tests pass, 509 regression tests pass,
  ruff/mypy typecheck clean. Documented third-party licenses in `THIRD_PARTY_NOTICES.md` and acceptance
  evidence across criteria A1–A14 in `docs/designer/acceptance.md`.
- **2026-09-18 12:23 IST** — Added `scripts/start.sh` / `scripts/stop.sh`
  (one-command local bring-up/teardown: containers, DB schema, Cua Driver
  bounded-daemon check, gateway, auto-opens the Open WebUI chat tab).
  Added this file and [PROJECT_GRAPH.md](PROJECT_GRAPH.md). While
  verifying `start.sh` end-to-end, found the live gateway was still
  running with the insecure default `AGENT_GATEWAY_API_KEY=change-me` in
  `.env` (script's own precondition check caught it) — rotated it to a
  random 64-char hex value and let `docker compose up -d` recreate the
  `open-webui` container so it picked up the new key; `OPENROUTER_API_KEY`
  was already set and was left untouched. Verified fresh bring-up and a
  second idempotent re-run both end cleanly at `/healthz` = ok,
  `/readyz` = ready, `cua_daemon_posture_verified` logged.

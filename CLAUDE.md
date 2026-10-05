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
                                    # (if tauri-build fails with EPERM on CuaDriver.app, use a fresh CARGO_TARGET_DIR)
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

## Voice service (Shubh, WhatsApp calls) — approved exception, `voice-service/` only

Owner-approved 2026-10-05: the separate `voice-service/` program (Node, not part of the Sani desktop path yet) also sends
data to Sarvam (listening `saaras:v4`, thinking `sarvam-105b*`, speaking `bulbul:v3`, one `SARVAM_API_KEY` in
`voice-service/.env`) and to WhatsApp (unofficial `baileys-caller`, SPARE number only, only the owner's own number is
called). The "only OpenRouter leaves the machine" rule still holds for everything else. Rules: no keys in logs/chat/memory;
memory writes pass `voice-service/src/secrets.mts` (same policy as `memory/policy.py`); transcripts and recordings stay on
the Mac (`voice-service/var/`, git-ignored), kept 30 days; summaries and open items in `var/voice.db`; a digest is mirrored to
`/memories/voice-calls.md`. Owner decisions: Shubh may contact other people only after the owner approves each message by voice;
deploy/publish, spending money, messaging/calling others and deleting ALWAYS need his spoken yes. Plan and handoff:
`docs/voice-agent-plan-2026-10-05.md`, `docs/voice-agent-handoff-2026-10-05.md`.

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
- Spoken replies: the host speaks every completed turn through the local
  `sani_tts.py` worker. On macOS it is ON by default with the `macos-say`
  engine (`SANI_TTS_ENABLED=0` turns it off; `SANI_TTS_ENGINE` overrides). The
  worker has no credentials and no egress; replies are trimmed to ~500 chars
  of plain speech. A different engine is still pending owner audition
  (`docs/verification/phase1/VOICE_SELECTION.md`).
- `RSI_MODE` is immutable `observation_only`: the Observer
  (`missions/observer.py`) is read-only with a separate recommendation
  sink; no experiment runner exists.
- Phase 1 acceptance evidence: `.venv/bin/python scripts/verify_phase1.py
  --suite <unit|integration|performance|rust|renderer|desktop|voice>`.
  Live suites need an owner-issued `approved-test-config.json` + `--allow-live`.

## Velo: plan -> typed steps -> sight (added 2026-10-01)

- `velo/planner.py`: ONE short model call turns a multi-step request into typed
  steps from a CLOSED recipe set (`STEP_SCHEMA`); unknown recipe/arg/type or >8 steps
  is rejected and the request falls back to Deep. The model never clicks.
- Steps run on the deterministic recipes (`recipes.py`). A step whose own check
  cannot confirm it may be judged by JEV with a closed yes/no (`step_ok` /
  `step_failed`, screen facts as evidence); JEV is still a classifier. A failed
  step re-plans only the remainder from the real screen, max `MAX_REPLANS`=2.
- `velo/sight.py` + `vision.py` + `uimemory.py`: when the accessibility tree cannot
  name a control, ONE screenshot goes to the vision model (default = reasoning
  model; `velo_vision_model` overrides, `velo_vision_enabled` turns it off).
  Found controls are remembered per site/app in `ui-memory.db` (label, role,
  relative position, window size, 64-bit pixel hash; NO screenshots, NO typed
  text) and reused only when the window size and pixels still match; every click
  is verified by the screen changing.
- `scene.safe_text` keeps secrets out of spoken descriptions and model views.
- Python-only changes ship with `sani/scripts/update-core.sh` (local core override),
  which does NOT reset macOS permissions; a full `install-app.sh` does.

## Claude Code companion (added 2026-10-03) — default off

`src/assistant/claude_code/` lets the Deep Agent hand software work to the
user's OWN Claude Code, the way a person would. Product rules:

- No API key, no Agent SDK, no embedding. Sani runs the installed `claude`
  (PATH, standard installs, or the copy bundled in the Claude desktop app) with
  `claude -p --output-format stream-json` under the user's own sign-in. The user
  signs in once with `claude auth login`; Sani never does it for them. The child
  gets a scrubbed environment (no `ANTHROPIC_*`, no OpenRouter key).
- This is a deliberate exception to "only OpenRouter leaves the machine": Claude
  Code itself talks to Anthropic with the user's own account. Sani adds no new
  credential and sends nothing to Anthropic itself.
- Safety: off by default (`CLAUDE_CODE_ENABLED`). Work happens only inside
  folders the user adds (`CLAUDE_CODE_DIRS`, symlink-resolved, never `/` or home).
  The user picks a ceiling (`read` / `edit` / `run`); the agent can ask for less,
  never more. No `--dangerously-skip-permissions`, no `--bare`; `-p` mode cannot
  answer prompts, so permissions are fixed up front.
- Supervision is rule-based and token-free (`watchdog.py`): repeated step, error
  streak, permission wall, rate limit, silence, time ceiling, then SIGINT, TERM,
  KILL. The Deep Agent judges the result afterwards and may make one corrected
  follow-up. Everything shown or stored passes `redact.screen`.
- Steps reach the UI as `agent.progress` frames carrying an optional `step`
  (id, label, status, tool, duration_ms, detail); the host bounds them and stores
  finished ones in `run_activity` (in-place migration).
- Sign-in is a button in Settings → Claude Code (`auth.py`): it runs `claude auth login --claudeai`, shows the sign-in link, accepts a pasted code, can be cancelled, and signs out with `claude auth logout`. Sani never sees a password or token. A green dot means installed, signed in, switched on and a folder added; red means something to fix; grey means off by choice.
- Sessions: one Claude Code session per (chat, folder) in `sani.db`
  (`claude_code_sessions`). Sessions started with `-p` do not appear in
  Claude Code's own picker; resume them by id.
- Attachments are saved under `<data dir>/attachments` (type- and size-checked)
  and passed to Claude Code by path.
- Verified only against a fake `claude` (tests/unit/test_claude_code*.py) until a
  real signed-in run. Unconfirmed against the real CLI: whether `rate_limit_event`
  and `modelUsage.contextWindow` arrive under `-p`, and whether `/context` and
  `/compact` work there. The usage chips show only what Claude Code reports.

## ZCode window mode (added 2026-10-04) — `ZCODE_MODE=window`, needs "ZCode control" on

`src/assistant/coding_agents/zcode_cdp/` + `zcode_window.py` let the Deep Agent use the user's REAL
ZCode app (their own Z.ai sign-in and Start Plan) through the app's own debug port. Plan:
`docs/zcode-cdp-integration-plan-2026-10-04.md`; evidence for every session:
`docs/verification/zcode-cdp/` (S1 discovery … S4 runs, S5-S7 sync/usage/handover).

- Same toolkit as the CLI backends (folders, run limit, sessions, chat steps): the window is
  a `Runner` (`zcode_cdp/runner.py`), the stream is the page's conversation rows (`rows.py`).
- The debug port has no password: random, 127.0.0.1 only, owner verified, opened only with the user's
  standing "ZCode control" yes (`zcode.control`, stored in `sani.db`), closed after 10 idle minutes,
  on turning control off, and when the core exits. A ZCode that will not quit is never force-killed.
- Fail closed: contract checks (`contract.py`) and the verified-version list gate every click; an
  unverified ZCode version is read-only. Never click on a guess. Never read/store/decrypt ZCode tokens
  (the sync signal uses credential NAMES and file time only).
- ZCode is always set to "Ask before changes"; Sani answers cards with Allow/Deny only (`policy.py`),
  never "Always allow"/"Full access". Known limit: ZCode runs commands it judges safe WITHOUT a
  card; Sani stops the run when such a call finishes and the limit forbids it.
- Results are checked against the disk (`verify.py`); tokens used = balance before/after or "unknown".
- Account/plan changes (`sync.py`): data is dropped and re-read, task links unlinked, banner shown.
- Selectors live in `pages.py`/`driver.py`/`rows.py` only. When ZCode updates, re-map and add the new
  version to `VERIFIED_VERSIONS` (contract.py) after a real run.
- Real runs: `scripts/zcode_read.py` (read-only), `scripts/zcode_run.py` (a task). Scratch project in
  ZCode: `sani_test`.

## UI (Sani main window)

Light only. Rules in `sani/DESIGN.md`; tokens in `sani/src/styles/app.css`
(main window) and `tokens.css` (pill/panel/onboarding). The renderer is React 19,
Tailwind 4 and Base UI primitives adapted from OpenWork (MIT, see
`THIRD_PARTY_NOTICES.md`). `npm run dev` then `app.html?preview` (also
`index.html`, `panel.html`, `onboarding.html`) runs the real windows against
fixtures from `src/dev/preview-backend.ts`; it is compiled out of builds.

## Conventions

- Python 3.12, `uv run` for everything; ruff (line 100) + strict mypy must
  stay clean; pytest asyncio_mode=auto.
- Rust: `cargo fmt`/`clippy` clean for files you touch.
- Never commit secrets; `.env` is local-only.

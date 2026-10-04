# Coding-agent infrastructure for Sani: plan (2026-10-04)

> **Status (2026-10-04, later): PARKED.** The user decided to focus on ZCode only, driven
> through its own app. The active plan is `zcode-cdp-integration-plan-2026-10-04.md`
> (it replaces S1 below). The other phases (Codex, Antigravity, Qoder, MCP front door) stay
> here for later; the Claude Desktop finding in S5 still stands.


Goal: the Deep Agent can hand software work to **every coding agent the user already
has** (Claude Code, ZCode, Codex, Antigravity, Qoder), through one shared, safe,
verified path. Each tool is a small backend. Nothing is built on guesses: every phase
ends with a real run whose evidence is written down.

This plan is meant to be executed in **separate sessions**, one phase per session.
Each phase below is self-contained: goal, inputs, steps, acceptance checks.

## 0. Non-negotiables (from CLAUDE.md and what went wrong today)

- Sani is local desktop software. Only model inference leaves the machine, plus the
  coding tool's own traffic under the **user's own sign-in**. Sani never reads, stores or
  logs a token, and never decrypts another app's credential store.
- No "attach and assume". A phase is done only when a **real run** shows real data
  (real endpoint, real window, real CLI output). Fixtures and fakes prove parsing, never
  the integration. Every claim in a result is labelled verified or unverified.
- Bounded and fail-closed: step/time limits, cancellation, folder allowlist, permission
  ceiling (read / edit / run), watchdog, refuse when unsure (as the ZCode "Z.ai plan
  only" guard does).
- A run whose tool could not run commands must say its "I ran it" statements are
  unverified (already in the toolkit).
- Python-only changes ship with `sani/scripts/update-core.sh` (keeps macOS permissions).
  Anything in the renderer or Rust needs a full `install-app.sh`, which resets
  Accessibility and Screen Recording: batch those changes into one install.
- Never format whole packages (`ruff format src/assistant`): format only touched files.
- Run the full unit suite, ruff and mypy **before** `update-core.sh`, never after.

## 1. What exists today (verified)

Shared base, built in earlier sessions: `src/assistant/coding_agents/` (`Backend`
protocol, `ClaudeBackend`, `ZCodeBackend`, guide text, ZCode catalog, "which model would
ZCode use" probe) and `src/assistant/claude_code/` (runner, watchdog, toolkit, store,
login session). The Deep Agent gets one tool per backend (`claude_code`, `zcode`).

Verified facts about the apps on this Mac (checked 2026-10-04):

| Tool | Local install | Has CLI? | Desktop is Electron? | Debug-port fuse | Notes |
|---|---|---|---|---|---|
| Claude Code | `claude` binary inside Claude.app; Claude.app 2.19675.0 (`com.anthropic.claudefordesktop`) | yes, works | yes, Electron 44.4.3 | **disabled** | CLI is signed out right now (red dot). **Desktop refuses a debug port by design (see S5): use the CLI only.** |
| ZCode | ZCode.app 3.14.4 (`dev.zcode.app`), CLI = `glm/zcode.cjs` run with Node | yes (`zcode -p --json`) | yes, Electron 41.0.3 | **enabled** | CLI cannot see the Start Plan reliably; app can. Debug port confirmed working. |
| Codex | `ChatGPT.app` 26.930 (`com.openai.codex`) | **no `codex` on PATH** | **no, not Electron** (no Electron framework) | n/a | OpenCLI's Codex adapter targets an Electron app: it does NOT apply to this install. Use `codex exec` (npm). |
| Antigravity | `Antigravity IDE.app` 2.5.5, bundle `com.google.antigravity-ide`, executable `Electron` | no `agy` on PATH | yes, Electron 39.2.3 | **enabled** | OpenCLI expects bundle `dev.antigravity.app`: needs a user app entry. |
| Qoder | `Qoder.app` 0.4.3, bundle `com.qoder.app`, executable `Qoder` (+ `Qoder Installer.app`) | no `qodercli` on PATH | yes, Electron 43.1.1 | **disabled** | OpenCLI expects bundle `com.qoder.ide`, executable `Electron`: does not match. |

Reference project, cloned read-only at `third_party/OpenCLI` (git-ignored; commit
`24136945`, 2026-09-25, v1.8.8, **Apache-2.0**, ~29.8K stars):
- Controls Electron desktop apps over the Chrome DevTools protocol. Adapters exist for
  antigravity, claude, codex, cursor, qoder, trae-cn, trae-solo, chatgpt-app, chatwise,
  doubao-app, discord. **No ZCode adapter.**
- Command shape worth copying: `status`, `new`, `send`, `read`, `ask` (send + wait + read),
  `model`, `watch`, `dump` (DOM snapshot for selector work), `extract-code`.
- App registry in `src/electron-apps.ts` (port, process name, executable names, bundle id).
- Reviewed for supply-chain risk: its `postinstall` runs `scripts/postinstall.js` and
  `scripts/fetch-adapters.js`. `fetch-adapters.js` reads local hashes only (no network).
  PRIVACY.md: no telemetry, localhost only. **Still: do not `npm install` it in the
  Sani tree.** Treat it as reference code.

## 2. Architecture

```
Deep Agent (existing)
  tools: claude_code | zcode | codex | antigravity | qoder   (one per enabled backend)
        |
  ClaudeCodeToolkit (shared)  -- folder allowlist, ceiling, watchdog, sessions, reporter
        |
  Backend protocol (existing)  + NEW `Transport`:
     CLI transport  : spawn the tool's own CLI           (claude, zcode -p, codex exec, qodercli)
     CDP transport  : drive the desktop app's own page   (ZCode, Antigravity, maybe Qoder/Claude)
```

Decisions:
1. **Port, don't depend.** Write the CDP adapters in Python inside Sani
   (`websockets` is already in the venv). Use OpenCLI only as a reference for selectors and
   command shape. Reason: Sani ships as one app, no Node/npm in the shipping path, no
   third-party postinstall.
2. **CDP transport module** `src/assistant/coding_agents/cdp/`: `launcher` (relaunch an app
   with `--remote-debugging-port=<random loopback port>`, confirm listening on 127.0.0.1
   only), `client` (minimal CDP over a websocket: evaluate, screenshot, input), `lifecycle`
   (port is open only while a run needs it, then the app is relaunched normally), `dump`
   (DOM skeleton for selector work, structure only, never message text).
3. **Per-app adapter** = selectors + 5 verbs (`status`, `new_task`, `set_model`, `send`,
   `read/wait_done`), each with a **selector contract test** against a recorded DOM
   skeleton. If a contract fails at run time the backend disables itself with a plain-words
   reason instead of clicking blindly.
4. **Guards per backend** (generalise the ZCode one): before a run, confirm which
   account/model will be billed and refuse if it is not the one the user approved.
5. **UI**: one "Coding agents" settings area. A status dot, sign-in/out, enable switch,
   transport (CLI or app), model/plan, with folders and the run limit shown once and shared.
   The chat already shows the right tool name (fixed 2026-10-04); extend the same fix to
   usage chips and the sidebar dot.
6. **Security of the debug port**: no password exists. Loopback only, random port, open
   only during a run, closed and the app relaunched normally afterwards. Documented in the
   Settings screen. Off by default; the user enables "app mode" per tool.

## 3. Phases (one session each)

### S1: CDP transport + ZCode app backend (do this first)
Why first: it unlocks the Start Plan (CLI cannot see it), the account, and the balance.
- Build `cdp/` (launcher, client, lifecycle, dump).
- Read-only proof against the real app: account and balance appear in Settings from
  the running app. Known hooks (verified 2026-10-04): page
  `app.asar/out/renderer/index.html`; `data-testid` values `task-new-button`,
  `conversation`, `conversation-column`, `workspace-item-<path>`, `task-item-sess_*`,
  `login-trigger`; composer is a `role=textbox` contenteditable with a Send button; model
  picker shows `GLM-5.3-Flash`; a "Today's balance" button; `window.zcode` has 122 shell
  functions but **none** to send prompts, so prompts go through the page.
- Then `send` / `wait_done` / `read`, the model check ("picker shows a Z.ai plan model"),
  the run limit mapped to the app's mode picker.
- Acceptance: (a) Settings shows real balance + account with a timestamp; (b) a short
  prompt round-trips on the Start Plan and the tool result says which model ran; (c) the
  debug port is closed and ZCode is relaunched normally afterwards; (d) unit tests +
  selector contract test with a recorded skeleton; (e) full suite, ruff, mypy clean.
- Ask the user before relaunching ZCode (it interrupts a running task).

### S2: Codex (CLI)
- The installed Codex is the native `ChatGPT.app`; CDP does not apply.
- With the user's approval: `npm i -g @openai/codex` (or the official installer) and
  `codex login` by the user. Capture a **real** `codex exec --json` run first, then write
  the parser from that capture (same lesson as ZCode: its output was not a stream).
- Backend: sandbox/approval flags mapped to read / edit / run; session resume; account
  check before running.
- Acceptance: real run in a scratch folder; result shows account and model; refusal when
  not signed in.

### S3: Antigravity IDE (CDP, maybe CLI)
- Electron 39.2.3, fuse enabled. Add a user app entry for bundle
  `com.google.antigravity-ide` (executable `Electron`); do not rely on OpenCLI's default id.
- `dump` first, then the five verbs. OpenCLI's `antigravity serve` idea (an
  Anthropic-compatible proxy over the app) is out of scope: record it as a later option.
- Check whether an `agy` CLI exists for this version before choosing the transport.
- Acceptance as S1.

### S4: Qoder (CLI first)
- Desktop fuse is disabled and the bundle id/executable differ from OpenCLI's entry:
  first test whether `--remote-debugging-port` is honoured at all (a 2-minute probe).
- If not, use Qoder's CLI (`qodercli`, not installed): capture a real run, then backend.
- Acceptance: whichever transport works, with a real run and a sign-in check.

### S5: Claude Desktop: NOT drivable (verified), Claude CLI hygiene
- **Finding (2026-10-04, verified twice):** Claude Desktop deliberately refuses a debug
  port. Its code checks `process.argv` at startup against a list that includes
  `remote-debugging-port`, `remote-debugging-pipe`, `ignore-certificate-errors`,
  `host-resolver-rules` and others, and exits with "Claude: refusing to start: a debugging
  or network-override switch is present on the command line". The only exception is a
  cryptographically signed developer token (`CLAUDE_CDP_AUTH`, checked against an embedded
  public key), which Anthropic controls. A throwaway launch with `--remote-debugging-port`
  printed that message and exited with code 1; nothing listened on the port.
- **Decision:** do not drive Claude Desktop's UI and do not try to get around the check
  (no patching the app, no forged tokens, no pipe/NODE_OPTIONS tricks). This is a security
  boundary the vendor set on purpose.
- **What to use instead:** the Claude Code CLI, already integrated and working. It needs
  the user to sign in once (it is signed out right now: Settings → Claude Code).
- **Optional later, supported direction:** the reverse. Expose Sani as an MCP server that
  Claude Desktop can call (via its own MCP configuration), so Claude Desktop can use Sani's
  tools. Needs a separate design and the user's go-ahead; not part of this plan.
- Add `claude auth status` account/plan to the same status shape as the other tools.

### S6: Router, Deep Agent integration, one settings area
- One router guide: when to use which tool (cost, privacy, strengths, which are signed in).
- Shared per-run record (tool, account, model, steps, verified/unverified) in the history.
- Generalise `zcode_allowed_providers` into per-backend account policy.
- Renderer: "Coding agents" page, correct names and chips everywhere, folders and the
  run limit on that page. **Batch all renderer/Rust changes into one full install.**
- End-to-end test with the scripted-model harness (`test_claude_code_e2e.py` style) for
  every backend, plus the long-request case (the 2000-token output cap already fixed).

### S7: Hardening
- Selector contract tests from recorded skeletons for every CDP app; a "selectors broke"
  state that disables the backend with a clear message after an app update.
- Kill switch that closes any debug port and relaunches apps normally.
- A diagnostics page: last run per tool with provider, account and step log, redacted.

## 4. Known gaps to close along the way (found today)

- Settings → ZCode: sign-out dialog still says it also signs out the ZCode app (it does
  not: `zcode logout` removes only the Coding Plan key). Folder list and run limit cannot
  be edited from that page.
- "Read from the ZCode app" (screen-reading) button depends on computer control, which
  still says "Needs setup"; replaced by S1 for balance and account.
- Stale `core-override` folder under Application Support: every full install must delete it.
- Computer control runs the driver in "standard" mode, so the allowlist entry for
  `dev.zcode.app` in `config/cua-capabilities.yaml` is inert there; S1 does not need it.
- `src/assistant/core/app.py` has a wrapped string the formatter touched; harmless.

## 5. Questions the user must decide (not assumed)

1. **Plan terms**: does Z.ai allow automating the app on the Start Plan? Same question
   for each tool's terms before app-mode is turned on.
2. **Relaunch policy**: may Sani relaunch an app with a debug port without asking each
   time, or ask every run? (Default: ask the first time per tool, remember per tool.)
3. **Install approvals**: `@openai/codex`, Qoder's CLI, any `agy`: each is a download.
4. **Order**: S1 first (recommended); S2 and S3 can swap.

## 6. Starting prompt for each session

> Read `CLAUDE.md` and `docs/coding-agents-infrastructure-plan-2026-10-04.md`. Do phase
> **S<n>** only. Verify with a real run before saying done, and show the evidence. Format
> only the files you touch. Run the full unit suite, ruff and mypy before shipping. Ship
> Python-only changes with `update-core.sh`; batch renderer changes into one full install
> and tell me before it resets permissions.

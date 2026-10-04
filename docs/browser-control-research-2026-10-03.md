# Browser control for Sani — research and ratings (2026-10-03)

Question: which open-source browser control should live inside Sani, so that
Velo (= JEV decision engine + browser control) can drive Chrome by page
structure instead of screenshot clicks?

Method: GitHub API stats (fetched 2026-10-03), npm metadata, and shallow
clones of seven repos read in the scratchpad. Scores below are my judgement
against Sani's rules, not benchmark results. Nothing was run end to end.

## Answer in one paragraph

Use **Playwright MCP** as the v1 engine, behind a new `BrowserAdapter` next
to `CuaAdapter`, with a **separate Sani Chrome profile**. Put a thin policy
layer in front of it, ported from OpenWork and agent-browser. Keep
**agent-browser** (native Rust, no Node) as the v2 candidate once Sani wants
to drop the Node dependency. Do not adopt browser-use, Stagehand, Skyvern or
Nanobrowser as engines; they each bring their own LLM loop, which collides
with "JEV is a classifier, the model never clicks". Mine them for parts only.

## Candidates

| Repo | Stars | Open issues | Licence | Last push | Notes |
|---|---:|---:|---|---|---|
| microsoft/playwright-mcp | 37.8k | tracked in main repo | Apache-2.0 | 2026-09-28 | npm `@playwright/mcp` 0.0.83, 91 KB wrapper |
| ChromeDevTools/chrome-devtools-mcp | 52.9k | 103 | Apache-2.0 | 2026-10-02 | npm 1.10.1, 14 MB |
| vercel-labs/agent-browser | 43.5k | 825 | Apache-2.0 | 2026-10-03 | Rust CLI, created 2026-01, 118 MB unpacked |
| browser-use/browser-use | 117k | 533 | MIT | 2026-10-03 | Python, ~40 pinned deps |
| browserbase/stagehand | 25.5k | 378 | MIT | 2026-10-02 | v4.1.0, TS, LLM inside `act`/`observe` |
| nanobrowser/nanobrowser | 13.9k | 81 | Apache-2.0 | 2026-10-02 | Chrome extension, own planner/navigator agents |
| Skyvern-AI/skyvern | 23.1k | 273 | **AGPL-3.0** | 2026-10-03 | vision + LLM server |
| lightpanda-io/browser | 35.9k | 101 | **AGPL-3.0** | 2026-10-03 | headless engine with no rendering; not a visible browser |
| BrowserMCP/mcp | 7.2k | 152 | Apache-2.0 | **2025-04-24** | stale |
| browserbase/mcp-server-browserbase | 3.4k | 52 | Apache-2.0 | 2026-07-20 | **archived**, cloud-based |
| executeautomation/mcp-playwright | 5.7k | 38 | MIT | 2025-12-13 | stale, superseded by the official one |
| different-ai/openwork | 23.8k | 598 | MIT (`ee/` excluded) | 2026-10-03 | not an engine; a reference host design |

## Ratings (1 = bad, 5 = best, for Sani specifically)

| Criterion | Playwright MCP | Chrome DevTools MCP | agent-browser | browser-use | Stagehand | Nanobrowser |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Reads page structure, not pixels | 5 | 5 | 5 | 4 | 4 | 4 |
| Fits "JEV classifies, model never clicks" | 5 | 5 | 5 | 1 | 2 | 1 |
| Privacy: nothing leaves except OpenRouter | 5 | 2 | 4 | 2 | 3 | 4 |
| Ships inside a desktop app | 3 | 3 | 4 | 2 | 3 | 2 |
| Profile and session control | 5 | 3 | 4 | 4 | 3 | 3 |
| Safety knobs (origins, caps, secrets) | 4 | 2 | 5 | 2 | 2 | 2 |
| Maintenance health | 5 | 4 | 3 | 4 | 4 | 4 |
| Maturity | 4 | 4 | 2 | 4 | 3 | 3 |
| **Total (of 40)** | **36** | **28** | **32** | **23** | **24** | **23** |

Why the notable scores:

- **Privacy.** Chrome DevTools MCP sends Google usage statistics by default
  (opt out with `--no-usage-statistics`) and may call the CrUX API from
  performance tools. browser-use ships a hard-coded PostHog key
  (`browser_use/telemetry/service.py`, off with `ANONYMIZED_TELEMETRY=False`).
  I grepped Playwright MCP's README and `src/` for telemetry and found none.
  Both defaults break the "only OpenRouter leaves the machine" rule unless
  switched off at spawn time.
- **Safety knobs.** Playwright MCP has `--isolated`, `--user-data-dir`,
  `--storage-state`, `--allowed-origins`/`--blocked-origins` (documented as not
  a security boundary), `--secrets`, file access restricted to workspace roots,
  and `vision`/`pdf`/`devtools` tools off unless enabled with `--caps`.
  agent-browser adds `policy.rs`, an allow/deny/confirm action policy.
- **Shipping.** Playwright MCP and Chrome DevTools MCP both need Node, which
  Sani does not bundle today (the core is a PyInstaller sidecar). agent-browser
  is a native binary, but young (v0.38, 825 open issues).
- **Real-world issues seen.**
  - Chrome DevTools MCP: `--autoConnect` reported as never working (19
    comments, 2026-08); browser hangs with many tabs (2026-04).
  - agent-browser: macOS arm64 "Daemon failed to start" with `--profile`;
    orphaned Chrome helpers burning CPU.
  - browser-use: a screenshot blob in a tool result poisoned the conversation
    and caused API 400s on every later turn. Sani's `result_normalizer.py`
    should never pass base64 through; worth a test.
  - Stagehand v3: `userDataDir` storage state persistence broken.

## Separate profile or real profile

Default to a **separate Sani profile**. Reasons:

1. Chrome refuses remote debugging on the default user-data-dir
   (chrome-devtools-mcp docs: "Chrome requires you to use a non-default user
   data directory"), so a real-profile attach needs the extension path anyway.
2. The user signs in once per site through a handoff step, and cookies stay
   under `<data dir>/browser-profile`.
3. Offer the real profile only per task, through Playwright's `--extension`
   attach, with the user approving each tab. Never as a global setting.

## Architecture fit

```
Deep Agent / planner  ->  typed browser steps (closed recipe set)
                              |
Velo recipes -> BrowserAdapter -> policy gate -> Playwright MCP (stdio)
                    |                               |
              JEV Choice over numbered refs     Sani Chrome profile
```

- **JEV stays a classifier.** The accessibility snapshot gives numbered refs;
  JEV picks one with a `Choice` over those candidates. No free-form text
  decides a click.
- **Plug-in point.** `tools/cua.py` already uses `MultiServerMCPClient`
  (`langchain_mcp_adapters`), so a second stdio server needs no new transport.
  Add `config/browser-capabilities.yaml` beside the CUA manifest.
- **Velo stays for native apps.** `CuaAdapter` is untouched; recipes choose the
  adapter by target (a browser tab goes to `BrowserAdapter`, WhatsApp desktop
  goes to CUA).
- **Why not an embedded browser view.** Sani's windows are Tauri WKWebViews on
  macOS, which do not expose CDP. OpenWork can embed because it is Electron
  (`webContents.executeJavaScriptInIsolatedWorld`). For Sani the in-app feel
  means a Sani-owned Chrome window whose life Sani manages (launch, profile,
  close), with the activity shown in the Sani panel. I did not check whether
  Tauri's unstable multiwebview could host a CDP-capable Chromium; treat that
  as open.

## What OpenWork did (the reference you asked about)

OpenWork did not wrap the browser MCP naively. It replaced raw
`opencode-chrome-devtools` access with a conversation-scoped host and states
"No unrestricted CDP tools" in `apps/desktop/electron/browser-task.mjs`.
Its model-facing tools are only `browser_tabs`, `browser_open`,
`browser_observe`, `browser_act`, `browser_navigate`, `browser_handoff`
(`apps/server/src/opencode-plugins/openwork-chrome-devtools.ts`, 36 lines).
Navigation and reading are granted once per thread; every click, fill and key
needs separate approval. That is the model to copy.

## Parts worth lifting (small, licence-compatible)

| From | File | Size | Take | Licence |
|---|---|---:|---|---|
| OpenWork | `apps/desktop/electron/browser-task.mjs` `observePage` / `prepareAction` | ~100 lines | Observation ids that go stale on DOM change, scroll or resize; refuse obscured, disabled or password targets; mark the observation consumed **before** input so a read failure never replays a click; pages labelled untrusted | MIT (not under `ee/`) |
| OpenWork | `openwork-chrome-devtools.ts` | 36 lines | The six-tool surface and the `handoff` tool for sign-in | MIT |
| agent-browser | `cli/src/native/policy.rs` | 217 lines | allow / deny / confirm action policy, confirm categories from env | Apache-2.0 |
| agent-browser | `native/element.rs` click path | n/a | Fail early when another element covers the click point (cookie banner, modal) | Apache-2.0 |
| browser-use | `browser_use/dom/serializer/paint_order.py`, `clickable_elements.py` | ~470 lines | Pure-Python occlusion and clickability detection, usable in `velo/scene.py` | MIT |
| Playwright MCP | `--secrets` dotenv file | config | Model sees a placeholder, never the password | Apache-2.0 |
| Chrome DevTools MCP | `--slim` tool set, `docs/design-principles.md` | docs | Keep the tool surface small to save context | Apache-2.0 |

Skip: Skyvern and Lightpanda (AGPL), BrowserMCP and executeautomation
(stale), mcp-server-browserbase (archived, cloud).

## Proposed order of work

1. Spike: spawn `@playwright/mcp --isolated --headless=false` over stdio from
   `scripts/`, snapshot a page, click one ref. Confirms the refs shape JEV
   needs and measures snapshot token size.
2. `BrowserAdapter` plus `config/browser-capabilities.yaml` (allowed caps,
   origins, confirm categories).
3. Port the observe/act freshness rules into the adapter; extend the planner's
   closed recipe set with `browser_open`, `browser_search`, `browser_click`,
   `browser_fill`.
4. Profile and handoff UI in Settings; per-task real-profile attach later.
5. Decide Node vs agent-browser for shipping after the spike's results.

## Open questions for you

- Is shipping Node inside Sani acceptable? If not, agent-browser moves up.
- Should Chrome be required, or may Sani download Chrome for Testing for its
  own profile (agent-browser does this on first run)?
- Should the browser steps ask per click like OpenWork, or per task with a
  domain allowlist?

Cloned repos are in the session scratchpad only; nothing was added to the repo
or `pyproject.toml`.

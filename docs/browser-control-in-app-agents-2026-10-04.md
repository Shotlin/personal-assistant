# In-app agent browsers: who has Codex-style control, and what Sani can reuse (2026-10-04)

This replaces the "Playwright first" advice in
[browser-control-research-2026-10-03.md](browser-control-research-2026-10-03.md).
You asked for what coding agents and apps already ship (visible cursor, fast
actions, no screenshot per step), not an automation library.

Method: GitHub stats and shallow clones read in the scratchpad, plus the
Cua docs and changelog. Nothing was run end to end. Scores are my judgement.

## What "Codex / Claude-in-Chrome feel" is made of

Three parts, each separate:

1. **Structured page snapshot with refs**, so the model picks "ref p1:7"
   instead of reading a screenshot every step.
2. **Direct input events over CDP** (`Input.dispatchMouseEvent`). No OS mouse
   move, so it is fast and can run while you work elsewhere.
3. **A virtual cursor drawn on top**, animated with spring physics. It is
   cosmetic: the click is real CDP input, the cursor only shows it.

Per-step screenshots are what make an agent feel slow (Cline does this:
`BrowserSession.ts` takes a PNG/WebP after every action).

## Main finding: Sani already ships this

`trycua/cua` (MIT) is the driver Sani bundles. Since Cua Driver 0.19.0
(announced 2026-08-06) it has extension-free browser use plus an agent cursor.
Checked on the binary inside `/Applications/Sani.app` (v0.28.2): the strings
`browser_click`, `browser_type`, `browser_pointer`, `get_browser_state`,
`browser_prepare`, `browser_navigate`, `browser_dialog` and
`set_agent_cursor_motion` are all present.

- Snapshots: `get_browser_state` with `snapshot_format: "semantic_v2"` returns
  an outline plus action refs (`click` / `type` / `upload` / `pointer`). Refs
  die on navigation or a newer snapshot (`browser_ref_stale`).
- Actions: `browser_click`, `browser_type`, `browser_pointer`
  (hover, right-click, double-click, scroll, drag), `browser_dialog`,
  `browser_set_input_files`, `browser_download`, `browser_navigate`.
- Profile choice, matching what you asked earlier: `browser_prepare` with
  `isolated_new` (deleted at session end) or `isolated_named` (kept) is a
  separate profile. Attaching to your real profile needs an explicit grant that
  a tool argument cannot give.
- Cursor: `set_agent_cursor_motion` exposes `glide_duration_ms`, `spring`,
  `arc_size`, `arc_flow`, `dwell_after_click_ms` (default 80), `idle_hide_ms`.
  You can tune the speed and feel without writing animation code.
- Same architecture as Velo: Cua's own `jev-use` skill says the app builds
  every candidate, TypeSafe Jev returns one candidate ID, the driver executes,
  and a separate check verifies. Reference code is in
  `libs/cua-driver/examples/jev-use/python/` (`sources.py` 296 lines builds
  candidates, `jev_adapter.py` 253, `run.py` 614, `native.py` 436).
  RFC 4268 in the same repo extends it to native accessibility candidates.

Caveats found:

- **macOS trusted click is refused by default.** The docs say macOS and Linux
  return `browser_input_trust_unavailable` for background `browser_click`,
  because Chrome would activate. Use `delivery_mode: "foreground"` (Chrome
  comes to the front, which suits an assistant you are watching). The other
  route, `input_route: "dom_event"`, stays in the background but reports
  `effect: "unverifiable"`.
- **Manifest conflict is unverified.** `config/cua-capabilities.yaml` says
  typed-browser manifests cannot coexist with generic input tools. I found no
  such rule in the current docs or source, and the current manifest example
  puts `resources.browser` beside a tool allowlist. I did not test this on
  v0.28.2.
- **Sani's policy does not know these tools yet.** In
  `src/assistant/tools/policy.py`, `MUTATING_TOOL_NAMES` has no
  `browser_click`, `browser_type`, `browser_pointer` or `browser_navigate`, so
  they would skip the per-run budget. `browser_download` and `browser_prepare`
  are already in `SENSITIVE_TOOL_NAMES`.
- Safari, Firefox, Tauri and WKWebView targets return
  `browser_route_unavailable`. This only matters for Chromium-based browsing,
  which is what you want.

## Other projects checked

| Project | Licence | What it has | Verdict |
|---|---|---|---|
| trycua/cua (Cua Driver) | MIT | CDP browser tools, semantic refs, agent cursor, jev-use loop | **Use it. Already bundled.** |
| pingdotgg/t3code (24.6k stars) | MIT | Electron webview with CDP through `webContents.debugger`, `Accessibility.getFullAXTree`, `Input.dispatchMouseEvent`, an SVG agent cursor, Playwright's injected runtime reused without a Playwright process | Good reference. `Manager.ts` is 5,245 lines, too big to lift. Needs Electron; Sani is Tauri |
| CherryHQ/cherry-studio (52k) | **AGPL-3.0** | Agent browser pane with a virtual cursor (`BrowserCursorAnimation.ts`, 227 lines: damped springs, curved path when the move is over 120 px, click "scoot") | Design reference only. Do not copy AGPL code |
| cline/cline (70k) | Apache-2.0 | Puppeteer, a screenshot after each action | Slow by design. Skip |
| bytedance/UI-TARS-desktop (39k) | Apache-2.0 | Vision-model operator | Skimmed only. Screenshot-driven, which you want to avoid |
| browseros-ai/BrowserOS (13.8k) | AGPL-3.0 | A second browser for agents, imports logins | Skimmed only. AGPL, concept only |
| lahfir/agent-desktop (1.7k), minghinmatthewlam/computer-use-mcp (46), iFurySt/open-codex-computer-use (2.3k) | Apache-2.0 / MIT / MIT | Native-app agent cursor overlays and a Codex-style computer-use MCP | Not read deeply. Native-app focus; Cua already covers it |
| OpenAI Codex, Claude in Chrome, Cursor | closed | The products you named | Cannot be cloned. Codex's in-app browser uses a separate profile with optional CDP, which matches the Cua route |
| Roo Code, Void | archived | n/a | Skip |

## Recommendation

Do not clone a coding agent's browser. Turn on what ships:

1. **Spike (about an hour):** with the bundled driver, `browser_prepare`
   (`isolated_new`), `get_browser_state`, `browser_click` with
   `delivery_mode: "foreground"`, and measure per-step latency. This also tests
   the manifest-conflict question.
2. **`BrowserAdapter` beside `CuaAdapter`:** build candidates from
   `semantic_v2` refs, JEV picks one ID, adapter calls the driver. Port the
   pattern from `examples/jev-use/python/sources.py` and `run.py` (MIT).
3. **Policy:** add the four `browser_*` mutating tools to
   `MUTATING_TOOL_NAMES`; keep download and prepare as sensitive. Add a test
   that no base64 image ever lands in a tool result.
4. **Feel:** tune `set_agent_cursor_motion` (short `glide_duration_ms`,
   modest `spring`). Only if you later want a cursor inside Sani's own window,
   write a small spring animation yourself, using Cherry's design as the idea.
5. Playwright MCP stays the fallback if the spike shows the Cua route is
   unreliable on macOS.

Cloned repos are in the session scratchpad only.

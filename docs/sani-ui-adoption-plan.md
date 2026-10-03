# Sani UI adoption plan — OpenWork-quality light UI on the Sani engine

Status: PROPOSAL (2026-10-03). Nothing here is implemented yet.
Source studied: `different-ai/openwork` @ `dev` (README, AGENTS.md, DESIGN.md,
`apps/app/package.json`, `apps/app/src` tree). Individual component source
files were NOT read yet; Phase 0 does that.

## 1. Decision

**Port OpenWork's UI layer into Sani's Tauri renderer. Do not fork the whole repo.**

- Keep: `sani-core` (Deep Agent, Velo, JEV, missions, memory), the Rust host
  (voice, hotkey, IPC, Keychain, SQLite), and the Tauri shell.
- Replace: the `sani/src` renderer (about 2.9k lines of TSX and 2.4k lines of
  plain CSS, no component library) with an OpenWork-style design system and
  chat UI.
- Light mode only. Remove the dark option and the dark glass tokens.

Why this works: both apps are React + Vite, so components, tokens and layout
move across. Why not fork: OpenWork's shell is wired to Electron IPC,
`openwork-server`, the OpenCode SDK and Den (the cloud control plane). None of
that matches Sani.

## 2. What OpenWork gives us (and what we skip)

| Take | From OpenWork | Notes |
|---|---|---|
| Design rules | `DESIGN.md` (P1-P11, S1-S6, C1-C7, V1-V7, T1-T5) | Adapt into `sani/DESIGN.md`. Light-only. |
| Tokens | Radix slate/blue/amber/red/green scales, semantic vars, radii, shadows | Hairline borders, one depth strategy, no glass. |
| Stack | Tailwind 4, shadcn/ui on Base UI, lucide-react, sonner, motion, class-variance-authority, tailwind-merge | React 18 to 19 upgrade. |
| Fonts | Geist / IBM Plex Sans (variable, OFL) | Bundle locally, no CDN. |
| Primitives | Button, Switch, Dialog, Popover, Command, Tooltip, Select, Tabs, Skeleton | Compose from shadcn, not copy-paste. |
| Chat | Message list, markdown (marked + remark-gfm, shiki code, katex), tool-call rail, composer (round send/stop, model pill), queue/steer, consent card | The biggest quality win. |
| Library / Connections UI | Add skill / plugin / MCP server pages, plain-words sign-in choices | UI only; backend is ours (Phase 5). |
| Model picker | Pinned / notice / recovery states | Ours is OpenRouter only today; extend. |
| Layout | Sidebar, resizable panels, command palette (`⌘K`) | `react-resizable-panels`, `cmdk`-style Command. |

| Skip | Why |
|---|---|
| `ee/` (Den, MCP gateway, inference) | Source-available license, cloud product. |
| OpenCode SDK, `openwork-server`, Electron IPC | Wrong runtime. Sani talks to `sani-core` over Tauri IPC. |
| Lexical, CodeMirror, xterm, mermaid, browser-tabs, dither shader | Not needed now. Revisit later. |
| i18n | English only for now. |
| OpenWork name, logo, copy | Rebrand to Sani. MIT allows reuse of code outside `ee/` if the copyright notice is kept (see Phase 0). |

**Computer use:** OpenWork's newest commit (#5600, 2026-10-03) *removed* its
native Computer Use feature end to end. There is no CUA to inherit. Velo and
Cua Driver stay as they are, and the UI just presents them better.

## 3. Phases

### Phase 0 — Prep (about half a day)
- Commit or stash the current working tree. It has many modified files,
  including `.core-build` artifacts, and a UI rewrite on top of that is
  unreviewable.
- With the owner's OK, shallow-clone `different-ai/openwork` into the scratchpad
  and read `packages/ui`, `apps/app/src/components/{ui,chat,tools}`,
  `react-app/design-system`, and `styles/*.css`. Update this plan with exact
  file names.
- Add `THIRD_PARTY_NOTICES.md` with the MIT notice for every file adapted.
- Take baseline screenshots of today's UI (pill, panel, main, settings,
  onboarding) for before/after.
- Confirm the minimum macOS / WKWebView version in `tauri.conf.json` supports
  Tailwind 4 (needs Safari 16.4+).

### Phase 1 — Foundation (1-2 days)
- Upgrade `sani/` to React 19, Tailwind 4 (`@tailwindcss/vite`), shadcn config,
  lucide, sonner, clsx/cva/tailwind-merge, local fonts.
- `src/styles/tokens.css` replaced with light-only tokens. Delete the glass,
  blur and dark variables.
- Write `sani/DESIGN.md` (adapted rules) and add the "Before opening a UI PR"
  checklist.
- Gate: `npm run build` clean; existing screens still render (ugly is fine).

### Phase 2 — Primitives and app shell (2-3 days)
- `src/components/ui/*` (Base UI-backed). Storybook-style gallery route
  (`/#/kit`) for visual checks.
- Shell: left sidebar (conversations, search, Settings), main chat surface,
  optional right panel (resizable). Command palette with `⌘K`.
- `MainApp`, `MainSidebar`, `ConversationsPage` move to the new shell.

### Phase 3 — Chat and streaming (4-6 days) — highest value
- State: Zustand store of messages made of typed **parts**: `text`,
  `reasoning`, `step` (tool/Velo action), `approval`, `error`.
- Streaming adapter: map the existing Tauri events (`sani://agent-start`,
  `agent-chunk`, `agent-done`, `activity`, `core-event`, `message`,
  `history-loaded`) into the store. No change to the Rust or Python protocol.
- Tool rail (T1): connector logo / glyph, sentence label ("Opened Chrome",
  "Clicked Send"), duration right, collapse to "Worked for 1m 19s · 12 steps".
  Raw details under "Technical details".
- Composer (T5): one round send/stop button, mode pill, mic button wired to the
  voice state machine, queue/steer chords. Stop is wired to the existing
  cancellation path (Velo stays bounded).
- Markdown renderer with code blocks (shiki), tables, links.
- Consent card (T4) above the composer, fed by `MissionPendingApproval`:
  action, data and risk in one line, with primary / edit / decline.
- Replaces: `MainConversation.tsx`, `Message.tsx`, `ActivityTimeline.tsx`,
  `MissionStatus.tsx` (kept as a thin part renderer).

### Phase 4 — Settings and onboarding (3-4 days)
- Settings as compact rows (S2). Pages: General, Models, Voice, Microphone,
  Computer control, Shortcuts, Storage, plus new Connections and Skills.
- Computer-control page restyled; permissions shown as "locked with reason"
  (P4/C5), not red errors.
- Onboarding steps restyled with the same primitives; no behavior change.

### Phase 5 — Connections: MCP, plugins, skills, providers (1-2 weeks, mostly backend)
This is the only phase that needs real new engine work.
- `sani-core` MCP registry: servers stored in `sani.db`; stdio and streamable
  HTTP transports; connect via the existing `tools/` layer; tool list refresh.
- Policy: every external MCP tool gets a risk class in `tools/policy.py`.
  Reads can run; writes and external sends route through the consent card /
  mission approval. A Controller role never gets raw tool access it was not
  granted (same rule as the CUA today).
- Secrets: MCP keys and OAuth tokens go to Keychain via the host, never to
  memory, logs or `sani.db` (existing secret-screening policy applies).
- Skills: keep developer-authored read-only `skills/`; add a user skills
  directory in `SANI_DATA_DIR`, read-only to the agent, installable from the UI.
  Import of Claude-compatible plugin bundles (skills + MCP) as a later step.
- Providers: extend `models/factory.py` settings UI beyond OpenRouter (generic
  OpenAI-compatible base URL, Ollama). JEV stays a classifier on its own
  model path; it is not user-swappable into a chat model.
- UI: Library page (skills, connections, plugins), "Add MCP server" flow in
  plain words, per-connection status and "Reconnect".

### Phase 6 — Pill, overlay, voice UI (2-3 days)
- DESIGN.md V7 bans glassmorphism, so the dark glass pill and overlay are
  restyled to a light solid surface with a hairline and one shadow. Waveform
  and state colors move to the new tokens.
- Layout editor kept but restyled.

### Phase 7 — Polish and acceptance (2-3 days)
- Screenshots for every screen and non-happy state (loading, empty, error,
  blocked, offline) per P10, stored under `docs/verification/ui/`.
- `scripts/verify_phase1.py --suite renderer` and `--suite desktop` green;
  `npm run build`; `cargo test`; `ruff` and `mypy` unchanged.
- Reduced-motion and focus-ring checks. Remove dead dark-mode code and old CSS.

Estimates are rough guesses, about 4-6 weeks of focused work in total, and
Phase 5 is the least certain.

## 4. Rules that do not change
- JEV stays a structured classifier. No LLM fallback.
- Velo's loop stays bounded and fail-closed. The UI only displays and cancels.
- Only model inference leaves the machine. New MCP servers are user-added,
  shown as such, and off until the user enables them.
- No API keys in memory, logs, or the database.
- Sani is not a web product; no Den, no cloud sync.

## 5. Risks
- **Scope creep in Phase 5.** MCP and plugin support is a feature in its own
  right; ship UI-first with the registry read-only if time is short.
- **Upstream drift.** OpenWork ships a large volume of commits. We take a
  snapshot and do not track upstream.
- **WebView differences.** Tauri uses WKWebView, not Chromium; test shaders,
  `color-mix` and `:has()` use early.
- **Big uncommitted tree** (see Phase 0).

## 6. Update 2026-10-03 — coding and browser come first

Findings from reading the real OpenWork source (shallow sparse clone, kept
outside the repo in the session scratchpad):

- OpenWork does NOT run Claude Code or Codex inside itself. Its coding ability
  is its own agent (OpenCode) with edit / write / apply_patch / grep / glob /
  lsp / bash / todo / question / subagent tools (`components/tools/*`).
  Claude Code and Codex connect IN through OpenWork's MCP (skills and
  connections shared outward), OpenWork can import Claude Code plugin bundles,
  and "Codex" in the app source is a reasoning-effort setting for OpenAI models.
- Streaming is built on the Vercel `ai` SDK message-part types (`ToolUIPart`,
  `DynamicToolUIPart`) via `sync/usechat-adapter.ts`. If Sani's adapter emits the
  same part shapes, OpenWork's Tool, diff, reasoning and question components port
  with little change.
- OpenWork removed its Computer Use feature today (#5600), so priorities below
  put coding and browser control ahead of CUA.

Revised order (replaces the phase order in section 3 where they differ):

1. Phases 1-4 as written, with the Phase 3 adapter emitting `ai` UIMessage
   parts.
2. **Claude Code companion (new Phase 3b) — owner's decision 2026-10-03:**
   Claude Code stays a separate app on the owner's own login. NO Anthropic API
   key, NO Agent SDK, Claude Code does not run "inside" Sani. Sani acts like
   the human: it sends prompts to the installed `claude` CLI and shows a live
   preview of what happens. Verified in the Claude Code docs:
   - `claude -p "<prompt>" --resume <session-id> --output-format stream-json
     --verbose --include-partial-messages` sends a turn to an existing session
     and streams tool calls/text as JSON lines; the final line carries session
     id, usage and cost. Normal `-p` uses the logged-in account (only `--bare`
     requires an API key, so never use `--bare`).
   - Sessions started by `-p` are hidden from the interactive picker and
     `--continue`, but can be resumed by ID or by transcript path.
   - Two processes resuming one session interleave into one transcript, so
     Sani must not drive a session the owner has open (use `--fork-session`).
   - Transcript `.jsonl` format is internal and changes between versions:
     history viewing of sessions Sani did not start is best-effort, with a
     version guard.
   - In `-p` mode nobody can answer permission prompts or questions
     (`AskUserQuestion` is unavailable): runs need a fixed
     `--permission-mode` / `--allowedTools` chosen in Sani's approval card.
   - Rate-limit state surfaces as `system/api_retry` events (`rate_limit`).
   Not verified: whether `/context` and `/compact` work under `-p`, and
   whether the Claude desktop app lists these sessions (it keeps its own list).
   Fallbacks, in order: a PTY-driven interactive `claude`, then CUA on the
   desktop app (last, it is the hard one). Scope: user-chosen project folder,
   approval card before a run, cancel, mission/ledger record. This is a
   deliberate exception to "no host shell"; it lives in the scoped, approved
   companion, not in the Deep Agent. Check Anthropic's usage terms for
   scripting the CLI with a consumer login before shipping beyond personal use.
3. **Browser control via MCP (Phase 5 early slice).** Add Chrome DevTools /
   Playwright MCP through the new MCP registry. DOM-level control is far more
   reliable than screenshot/accessibility control, and it needs no custom CUA
   work. Open decision: use a separate automation profile, or the user's real
   Chrome profile (logged-in sessions, higher risk).
4. Rest of Phase 5, Phase 6, Phase 7.
5. CUA (Velo on native apps) improvements after the above.

## 7. Open questions for the owner
1. Claude Code companion via the headless CLI (section 6, item 2): OK that
   permission prompts and mid-run questions are not possible in this mode, and
   a run uses a fixed allowed-tools set chosen up front?
2. Browser automation: separate Chrome profile (safer, needs sign-ins) or the
   real Chrome profile (convenient, risky)?
3. Should the floating pill also go fully light, or keep a dark pill on a light
   app? (The plan assumes fully light.)

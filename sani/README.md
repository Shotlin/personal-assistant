# Sani

A minimal, local, cross-platform desktop **voice shell** for the existing
[Personal Assistant](../) Deep Agent. On macOS, Sani opens as a normal Dock
application with a native titlebar and an always-available desktop workspace;
the floating mic pill and side panel remain optional quick-access companions.
Live English transcription appears while you speak; the final utterance is
sent **exactly once** to the existing agent; its streamed reply and real run
activity appear in the desktop conversation surface and companion panel.

```text
Global hotkey -> mic pill -> live STT (local Moonshine) -> final transcript
             -> existing Personal Assistant gateway (SSE) -> right-side panel

Double-click Sani -> restored main desktop window -> resumed conversation
```

Sani is not a second agent, not a chat client rebuild, and not a workflow
editor. Open WebUI and Agent Designer are not required for normal Sani use.

## Stack

- **Tauri 2** (Rust + React + TypeScript + Vite)
- **Moonshine Voice** — Small Streaming English, fully on-device via the
  `moonshine-voice` Python package running as a local sidecar process
- **CPAL** microphone capture → mono → 16 kHz float PCM
- **SQLite** local UI history (conversations + messages; audio never stored)

## Layout

```text
sani/
├── src/                    # React UI (main workspace + pill + panel)
│   ├── app/                # MainApp.tsx, OverlayApp.tsx, PanelApp.tsx
│   ├── components/         # Waveform, Message, ActivityTimeline, drawers
│   ├── lib/tauri.ts        # typed event/command bridge
│   └── styles/             # design tokens + per-window styles
├── src-tauri/
│   └── src/                # main, app_state (voice state machine), audio,
│                           # speech (sidecar client), agent (chat SSE),
│                           # activity (run-event SSE), history, settings,
│                           # hotkey, windows
│   └── python/sani_stt.py  # Moonshine streaming STT sidecar
├── scripts/
│   ├── setup-stt.sh        # bootstrap the STT venv (.stt-venv)
│   └── make_icon.py        # regenerate the icon source PNG
└── package.json
```

## Setup (macOS first, Windows/Linux compatible architecture)

Prerequisites: Rust, Node, and the existing Personal Assistant gateway
(`./scripts/start.sh` in the repository root).

```bash
cd sani
npm install
./scripts/setup-stt.sh          # dev only: STT venv + moonshine-voice + onnxruntime
./scripts/build-sidecar.sh      # packaged: freeze the worker into one self-contained binary
./scripts/install-app.sh        # release build -> /Applications/Sani.app -> launch
```

The first run of the voice engine downloads the small streaming model into
the platform cache (`~/Library/Caches/moonshine_voice` on macOS); after that
everything is fully on-device.

`build-sidecar.sh` produces `src-tauri/binaries/sani-stt-<target-triple>`, which
Tauri ships as an `externalBin`. The installed app therefore never depends on
the repository venv, a global Python, or a Terminal environment — it runs
`Contents/MacOS/sani-stt` directly. `setup-stt.sh` remains the dev path used by
`tauri dev`.

### macOS microphone permission

macOS gates the microphone behind TCC, and a denied app simply receives
silence — so Sani asks the system directly (an Objective-C bridge over
`AVCaptureDevice.authorizationStatus`) and never infers permission from the
audio levels. Until access is granted the pill shows an amber "Microphone
permission required" / "Grant Access" state with the microphone disabled; it
does not claim to be listening. A denial routes to "Microphone access denied"
with a button that opens System Settings › Privacy & Security › Microphone.

To re-test the first-run prompt:

```bash
tccutil reset Microphone app.sani.local
```

### Gateway connection

Sani talks to the existing gateway at `http://127.0.0.1:8787`. There is no
login screen: the local key is resolved in this order, and the secret is never
displayed or written to the plaintext settings file when secure storage works.

1. macOS Keychain (`sani-agent-key` / `app.sani.local`),
2. a private `0600` file in the app-support directory,
3. a one-time bootstrap: `SANI_AGENT_API_KEY`, then a `.env` containing
   `AGENT_GATEWAY_API_KEY` found by walking up from the executable / working
   directory — persisted back through (1) or (2) so a Finder launch works with
   no shell environment at all.

The log records only *where* the key came from. A Keychain write failure falls
back to the private file and is retried on every later launch.

The gateway must be serving the Deep Agent under the `personal-assistant-v1`
alias. With `DESIGNER_ENABLED=true` that alias resolves through the Agent
Designer registry, which has to be bootstrapped once from the repository root:

```bash
uv run python scripts/bootstrap_designer.py   # idempotent; imports the existing agent
```

Base URL, hotkey, microphone, theme and launch-at-login are configurable in
Settings (panel → ⚙). Only these minimal settings exist by design.

### macOS main window behavior

Phase 1 is optimized for MacBook Air 13-inch, MacBook Pro 14-inch, and
MacBook Pro 16-inch displays at Retina scaling. Sani uses a regular decorated
window (no forced fullscreen), appears in the Dock, and uses the normal macOS
maximize behavior. Closing the main window hides it while the existing runtime
continues when supported; reopening from the Dock, Finder, or Spotlight safely
restores and focuses it.

Sani persists the display affinity, normal x/y/width/height in logical points,
and a separate maximized flag. On launch it reads macOS's native visible work
area (`NSScreen.visibleFrame`), including the menu bar/notch and Dock on any
edge. It validates and clamps saved normal bounds before applying them, falls
back to the primary MacBook display if a saved external display is absent, and
only then reapplies maximization. Maximized work-area dimensions never replace
the previous normal frame.

### Overlay layout editor

The **Layout** page places the voice pill and the conversation panel. It draws
one native display snapshot, so the menu-bar/notch and Dock appear as striped,
genuinely unavailable regions taken from `NSScreen.visibleFrame` rather than
guessed insets. Shapes are dragged with the pointer, or focused and moved with
arrow keys and resized with shift + arrow keys; the pill resizes horizontally
only, because its height is a fixed supported value. Preview, Save, Cancel and
Reset are explicit buttons.

Positions persist as normalized ratios inside the usable work area while sizes
persist as logical points, deliberately mixing units:

```json
"overlay_layout": {
  "display_affinity": "1:Built-in Retina Display",
  "pill":  { "x_ratio": 0.619, "y_ratio": 0.3446, "width": 560.0, "height": 96.0 },
  "panel": { "x_ratio": 0.6599, "y_ratio": 0.4649, "width": 500.0, "height": 454.26 }
}
```

So a panel that is 520 points on the built-in display is still 520 points on a
larger one, and placement adapts when the work area changes shape. Native code
is authoritative: it resolves the display in the order saved affinity → the
display holding the main window → primary → first available, clamps sizes to
the supported ranges (pill 560–760 pt at a fixed 96 pt height, panel 500–640 ×
400–680 pt) and then to the current work area, and reports every clamp in plain
language, which the editor shows instead of silently disagreeing with it.

Preview is process-local and never writes settings. It captures the committed
layout and the exact pill/panel visibility when it starts; Save persists the
validated layout and restores that captured visibility, Cancel restores the
committed layout and the same visibility, and closing the main window or
quitting cancels an unfinished preview. An overlay the user opened during a
preview (hotkey or tray) is never hidden again by Save or Cancel. Reset changes
only the editor draft. No draft survives a restart, and settings files written
before this feature load unchanged.

Measured on a 1470×956 pt Retina display with a 33 pt menu bar and a 73 pt
bottom Dock: with no `overlay_layout` present the first reveal produced the
Phase 1 geometry (700×96 pill, 544×454 panel, confirmed as exactly 2× physical
pixels in WebKit snapshots), and after a layout was saved from the page the pill
and panel landed at the predicted bounds to within one point on every subsequent
launch. Dragging below the supported minimums stopped at 560 pt and 500 pt,
which is what the saved block above records. Not covered here: a Dock on the
left or right (would require changing the machine's Dock position) and an
external-display disconnect (no second display attached).

### Identity contract

Sani sends neutral desktop lineage headers on every turn; the gateway maps
them into its existing thread/dedup contract:

```text
X-Assistant-User-Id: local-user
X-Assistant-Chat-Id: <stable conversation UUID>
X-Assistant-Message-Id: <new UUID per finalized utterance>
```

One finalized utterance = one `X-Assistant-Message-Id` = one agent run
(the gateway's run registry enforces exactly-once). The same conversation
keeps the same chat id, preserving the agent's thread across voice turns.

### Activity stream

Assistant text and agent activity are separate streams:

- chat: `POST /v1/chat/completions` (SSE on the response)
- activity: `GET /v1/runs/{run_id}/events` (separate safe SSE)

The activity stream carries only observable runtime events with timestamps
(run started/completed/failed, tool started/completed/failed/unknown with
durations). No chain-of-thought, prompts, tool payloads, or secrets — that
guarantee is enforced server-side and covered by tests.

## Dev flag

`SANI_AUTOSTART=1` begins listening right after launch (equivalent to pressing
the hotkey). `launchctl setenv SANI_AUTOSTART 1` does the same for an app opened
from Finder; clear it afterwards with `launchctl unsetenv SANI_AUTOSTART`.

## Verifying on a real Mac

The scripts below were the acceptance path for the macOS fixes. They drive the
installed `/Applications/Sani.app`, not a dev binary.

| Script | Proves |
| --- | --- |
| `scripts/build-sidecar.sh` | the STT worker freezes into one self-contained binary and reaches `ready` |
| `scripts/install-app.sh` | release build → ad-hoc sign → back up the old bundle → install → LaunchServices launch |
| `scripts/probe-gateway-contract.sh` | the gateway contract Sani relies on: `[DONE]`, activity past `run.started`, exactly-once dedup by message id, `/stop` → `run.cancelled` |
| `scripts/probe-sidecar-speech.py` | the *installed* sidecar transcribes real speech with no repo venv or Terminal env |
| `scripts/probe-voice-loop.sh` | the full loop on the installed app: audible sentence → mic → partials → final → one agent turn → streamed answer |

Two environment variables steer the diagnostics:

- `SANI_SNAPSHOT_DIR` — Sani captures a real PNG of the pill and panel at every
  state transition (via WebKit's own snapshot API, so it needs no Screen
  Recording permission). This is how "the UI actually rendered" is checked
  rather than assumed.
- `SANI_DEVTOOLS=pill,panel|all` — with the `devtools` cargo feature, opens the
  WebView inspector so frontend errors are not hidden behind the glass.

Logs go to `~/Library/Logs/app.sani.local/sani.log`, which is what a Finder
launch writes to (there is no controlling terminal). Look for
`[ui-boot] … UI READY`, `[state] …`, `[stt final]`, `begin turn:` and
`turn finished: … status=completed`.

## What v0.1 deliberately does not include

TTS, wake word, cloud accounts/login, team features, plugin marketplace,
workflow/agent builders, hidden chain-of-thought display, model tuning UI.
If a better STT model is adopted later, only the SpeechEngine (sidecar)
changes — the UI and agent protocol are model-agnostic.

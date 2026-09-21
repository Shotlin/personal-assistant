# Sani

A minimal, local, cross-platform desktop **voice shell** for the existing
[Personal Assistant](../) Deep Agent. One global shortcut opens a floating
mic pill; live English transcription appears while you speak; the final
utterance is sent **exactly once** to the existing agent; its streamed reply
and real run activity appear in a right-side panel.

```text
Global hotkey -> mic pill -> live STT (local Moonshine) -> final transcript
             -> existing Personal Assistant gateway (SSE) -> right-side panel
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
├── src/                    # React UI (pill window + panel window)
│   ├── app/                # OverlayApp.tsx (pill), PanelApp.tsx (panel)
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
./scripts/setup-stt.sh        # one-time: STT venv + moonshine-voice + onnxruntime
npm run tauri build           # or: npm run tauri dev
```

The first run of the voice engine downloads the small streaming model into
the platform cache (`~/Library/Caches/moonshine_voice` on macOS); after that
everything is fully on-device.

### macOS microphone permission

The bundled app (`target/release/bundle/macos/Sani.app`) declares
`NSMicrophoneUsageDescription` and asks once for microphone access the first
time you start listening. Unbundled dev binaries receive silence from macOS
(TCC) — use the bundled app or grant mic access to your terminal to test
voice input under `tauri dev`.

### Gateway connection

Sani talks to the existing gateway at `http://127.0.0.1:8787` and discovers
the local key without any login:

1. `SANI_AGENT_API_KEY` env var,
2. saved settings,
3. a `.env` containing `AGENT_GATEWAY_API_KEY` found by walking up from the
   executable / working directory (dev builds find the repository `.env`).

Base URL, hotkey, microphone, theme and launch-at-login are configurable in
Settings (panel → ⚙). Only these minimal settings exist by design.

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

`SANI_AUTOSTART=1 npm run tauri dev` — starts listening right after launch
(equivalent to pressing the hotkey). Used for verification; harmless to omit.

## What v0.1 deliberately does not include

TTS, wake word, cloud accounts/login, team features, plugin marketplace,
workflow/agent builders, hidden chain-of-thought display, model tuning UI.
If a better STT model is adopted later, only the SpeechEngine (sidecar)
changes — the UI and agent protocol are model-agnostic.

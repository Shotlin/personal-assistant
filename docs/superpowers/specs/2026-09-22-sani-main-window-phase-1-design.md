# Sani Main Window — Phase 1 Design

## Intent

Transform Sani from an overlay-first utility into a MacBook-first desktop application while preserving the existing pill and conversation panel as optional quick-access companions. This phase establishes the normal application window, native launch/reopen lifecycle, responsive shell, and durable/safe window restoration. It deliberately does not change STT behavior, overlay geometry, agent selection, model management, or diagnostics UI.

## Current-State Basis

This design is based on repository HEAD `e8a57df3011d90b4fe2595b45391a6cc01dfae95`.

- `sani/src-tauri/src/main.rs` currently enters macOS `Accessory` mode, creates only pill/panel windows, and reveals both on cold launch or `RunEvent::Reopen`.
- `sani/src-tauri/src/windows.rs` owns the onboarding, pill, and panel builders. Pill/panel positioning is computed from the monitor beneath the pointer.
- Vite currently builds `index.html`, `panel.html`, and `onboarding.html`; no main renderer entry exists.
- `settings.json` is Sani-local persisted preference storage and already safely ignores obsolete fields. Provider secrets remain in the Keychain.
- Local SQLite history already owns the active conversation and message persistence. Phase 1 must reuse it.

## Scope

### Included

- A normal, resizable, decorated `main` Tauri window using a new React/Vite entry.
- A professional desktop shell with a responsive left navigation and a Home conversation surface that reuses real local history and existing runtime events.
- Main-window show, hide, close, restore, Dock click, Finder/Applications/Spotlight reopen, and cold-start behavior on macOS.
- Persisted logical window bounds, a display affinity, and maximized state.
- Defensive restoration that always opens on a visible current display and respects usable macOS work areas.
- MacBook-first sizing, high-DPI/Retina conversion, and no automatic exclusive fullscreen.
- Pill and panel retained as hidden optional companions; their current positioning and voice behavior remain unchanged.

### Explicitly excluded

- Overlay layout editor or any change to pill/panel geometry persistence.
- STT model install/switch UI or manual-only voice submission.
- Agent switching/default changes, new agents, avatars, or orchestration.
- Dedicated conversations, voice, computer-control, layout, settings, diagnostics, or activity-log pages beyond disabled/placeholder navigation destinations needed to establish the shell.
- Changes to `sani-core`, CUA execution, provider credentials, onboarding, or SQLite schema.

## Product Behavior

After onboarding completes, launching Sani opens a focused main window rather than automatically exposing pill and panel. The main window restores the active conversation from existing local history and Sani Core starts as it does today.

The user may close the main window without terminating the process. Sani continues through its existing tray and global shortcut behavior. Reopening from the Dock, Finder, Applications, Spotlight, or a second app activation reveals and focuses the main window. The `Quit Sani` tray action remains the explicit termination path.

The global shortcut continues to invoke the existing quick voice path. It may reveal the pill/panel when needed; it does not need the main window to be visible. This phase makes no claim about changing the current overlay auto-reveal behavior after a voice action.

## MacBook-First Window Design

### Window construction

`windows.rs` gains `MAIN_LABEL = "main"` and a dedicated `build_main` path loading `app.html`. The main window is:

- decorated with the native macOS titlebar and traffic lights;
- opaque, taskbar/Dock-visible, focused, resizable, and not always-on-top;
- initially centered at 1180 × 760 logical points;
- constrained to a minimum inner size of 900 × 620 logical points;
- never put into exclusive fullscreen automatically.

The window uses standard Tauri logical sizing APIs for authored UI dimensions. Tauri receives physical monitor metadata, so all display comparison and clamping converts work areas into logical coordinates using each monitor’s own scale factor. Persisted data contains logical values only.

### Workspace assumptions

Phase 1 targets current macOS support (`minimumSystemVersion: 12.0`) on Apple Silicon and Intel MacBooks. The design supports 13-inch Air, 14-inch Pro, and 16-inch Pro layouts without depending on a particular native resolution. The responsive renderer treats 900 px as its narrow supported main-window width, collapses labels before horizontal overflow, and keeps Home’s message/composer column usable at the minimum size.

The effective work area excludes the macOS menu-bar/notch region and Dock on **every** edge. The implementation uses Tauri’s monitor/work-area API when it reports a real usable work area. If it does not, Phase 1 adds a small macOS-native helper backed by `NSScreen.visibleFrame` (with coordinates converted into Tauri’s desktop coordinate system) and uses that result for both display selection and restoration. The helper must correctly represent bottom, left, and right Dock placements as well as the menu bar/notch. A conservative top/bottom-only inset is not an acceptable fallback for macOS restoration.

## Persisted Window State

Add a backward-compatible `main_window` block to `settings.json`:

```json
{
  "main_window": {
    "display_id": "stable-display-identity-or-empty",
    "x": 120.0,
    "y": 80.0,
    "width": 1180.0,
    "height": 760.0,
    "maximized": false
  }
}
```

`x`, `y`, `width`, and `height` are logical points relative to the selected display’s logical work-area origin, not physical desktop pixels. `display_id` is a stable identifier exposed by Tauri when available; the fallback identity combines monitor name and physical geometry only as a best-effort affinity hint. It is never trusted as a coordinate authority.

Normal bounds and maximized state are deliberately independent. Saving happens on relevant main-window move, resize, maximize/unmaximize, and close/hide events. Writes are debounced to avoid I/O while the user drags. A final synchronous save occurs before hiding the main window. Existing settings remain intact, and absent or malformed window state is treated as an unconfigured first launch.

While the main window is maximized, persistence updates only `maximized: true`; it must not replace `x`, `y`, `width`, or `height` with maximized work-area dimensions. When the window is unmaximized, its returned normal frame becomes the new normal bounds and `maximized` becomes false. This preserves the user’s intentional ordinary window geometry across quit/relaunch.

## Display Resolution and Restoration Algorithm

1. Enumerate current monitors and construct a `LogicalWorkArea` for each: identity, logical origin, logical width, and logical height.
2. Select the saved display only if its identity resolves to a currently connected monitor; otherwise select `primary_monitor`, then the first available monitor.
3. Start from the saved **normal** logical size, or 1180 × 760 if absent/invalid. Clamp it to the selected work area while maintaining the 900 × 620 minimum whenever the work area permits it. On genuinely constrained usable areas, fit the window within the work area rather than leave it off-screen.
4. Start from the saved **normal** logical relative origin, or center the initial window. Clamp origin such that the titlebar and meaningful body area remain inside the selected work area, with a small safety margin.
5. Convert the resolved logical size/position through the selected monitor’s scale factor immediately before calling Tauri physical window APIs, and apply these normal bounds first.
6. Only after normal bounds are applied, restore `maximized: true` if it was saved. If the saved display is missing, preserve the user’s intent to maximize on the fallback display. Never call native fullscreen APIs during normal launch.

This covers resolution/scale changes, wake from sleep, disconnected external monitors, a changed Dock position, and stale saved data. It also means a prior external-display location resolves safely to the MacBook’s primary display when that monitor is gone.

## Main Window Renderer and Navigation

Add `sani/app.html` and a `main-main.tsx` entry that mounts `MainApp`. Extend Vite’s multi-page inputs with `main`. `MainApp` is the complete-window application shell, not another overlay or browser application.

Phase 1 navigation establishes these destinations without inventing their later functionality:

- Home (implemented): current conversation, existing local messages, runtime state, activity stream, and a non-disruptive text composer scaffold.
- Conversations, Agents, Voice, Computer Control, Layout, Settings, Diagnostics (present as keyboard-accessible shell destinations with concise “available in a later phase” content; no fake data or controls).

The sidebar uses labels and restrained inline SVG icons. It has a compact/collapsed presentation at narrow widths, preserving Home content before sacrificing usability. It displays build/version information at the bottom from app metadata when exposed; no hardcoded architecture/internal endpoint details appear.

Home subscribes to the existing Tauri bridge’s history, message, assistant, activity, and runtime state events. It loads the existing active conversation on mount and renders historical agent attribution exactly as stored. This reuses the local SQLite history owner and does not duplicate state in React. The Phase 1 text composer may be disabled with an honest “voice controls remain available” message until the existing text-turn command is deliberately designed in a later phase; it must not fabricate execution.

## Native Lifecycle and Runtime Integration

On successful onboarding, `enter_normal_mode` changes macOS activation policy from `Accessory` to `Regular`, initializes the main window plus the existing overlays/tray/hotkey, starts `sani-core`, and reveals only the main window. Existing overlay windows are constructed hidden so hotkey and tray flows remain fast.

`RunEvent::Reopen` becomes:

- onboarding incomplete: reveal/focus onboarding;
- onboarding complete: reveal/focus main, using the same safe restoration path only when the window must be recreated.

The main window’s close request is intercepted and hidden rather than destroyed. Its renderer and native window remain reusable, preventing duplicate runtime/renderer construction. An explicit app quit continues to exit normally. If an OS event destroys the main window unexpectedly, `show_main` defensively rebuilds it and restores safely.

The tray gains an “Open Sani” item that reveals/focuses main. Existing “Start Listening,” “Show Conversation,” and “Quit Sani” semantics are preserved. Existing tray/menu and hotkey registration are not duplicated.

## Error Handling

Failure to read or parse saved main-window state falls back to a centered initial window and records a technical log entry only. Failure to enumerate displays uses Tauri’s primary monitor and then a conservative default as a final guard. A missing or malformed display identity never prevents launch.

The main renderer must still show independently useful local state if `sani-core` is still starting or unavailable: local history may render; runtime readiness is represented as a clear non-technical status. It never surfaces raw Rust errors, IPC payloads, or stack traces to normal users.

## Accessibility and Interaction

The application shell uses semantic buttons/navigation, visible keyboard focus, correct selected navigation state, and sufficient contrast in the existing restrained dark visual language. Native titlebar controls stay native. The renderer must not draw fake traffic lights, fake cursors, or simulated desktop behavior.

## Test Strategy and Acceptance Criteria

### Rust unit tests

- Logical work-area conversion uses each monitor’s scale factor correctly.
- Initial size and minimum size are applied in logical units.
- Valid saved logical bounds restore to the same display.
- Invalid, oversized, negative, and wholly off-screen bounds clamp visibly.
- A missing saved display falls back to primary/current display.
- Resolution, Retina scaling, and Dock-work-area changes preserve a visible window.
- Maximizing preserves persisted normal bounds; unmaximizing updates normal bounds; restore applies normal bounds before restoring maximized state; and no path requests fullscreen.
- Settings migration accepts settings files without `main_window` and preserves all existing settings.

### Renderer checks

- `npm run build` typechecks and bundles `app.html` alongside overlay/onboarding entries.
- Home reads real history via the existing Tauri bridge and shows stored message attribution.
- Navigation selection, collapsed sidebar behavior, and minimum-width layout have deterministic component tests if the selected React test harness is added in implementation; otherwise manual acceptance is recorded rather than fabricated.

### Manual macOS acceptance

- Fresh post-onboarding launch on built-in MacBook display opens a normal focused main window, not overlays.
- 13-inch Air, 14-inch Pro, and 16-inch Pro usable layouts fit without normal-layout horizontal scrolling.
- Native titlebar/traffic lights, Dock presence, resizing, and no forced fullscreen behave normally.
- Quit/relaunch restores size, position, maximized state, and active conversation.
- Closing hides the main window while the tray/hotkey remain usable; Dock click restores/focuses it.
- Finder/Applications/Spotlight reactivation restores/focuses it.
- With an external display attached, a saved external placement restores there; after disconnecting it, the window opens visibly on the primary MacBook display.
- Sleep/wake followed by activation leaves a reachable visible main window.
- Dock placement on bottom, left, and right does not produce hidden titlebars or unreachable content.

## Non-Goals and Follow-up Boundary

Phase 1 establishes extension points only: `MainApp`, main window state, and navigation shells. Phase 2 may add the layout editor to the same application shell, but must not repurpose Phase 1’s main-window state as overlay geometry. Later phases will add real page capabilities only through their authoritative existing systems: `sani-core` for agents, STT subsystem for voice models, SQLite history for conversations, and secure storage for credentials.

# Sani Overlay Layout Editor — Phase 2 Design

## Intent and Scope

Phase 2 adds one layout editor inside the existing Sani main window. It lets a
MacBook user position and resize the existing Voice Pill and Conversation
Panel, preview the real overlays, save, cancel, and reset. It does not create
another application/window product and does not change STT, manual submission,
agents, CUA, providers, logs, or main-window geometry.

## Current-State Basis

Phase 1 already provides the normal `main` window, a Layout navigation
destination, `Settings` persistence, and native macOS visible work areas via
`NSScreen.visibleFrame`. `windows.rs` owns the pill/panel windows; their
current fixed placement arithmetic must become a shared layout-application
path. `main_window` persistence remains wholly separate.

## Architecture Decision

The native Rust layer is authoritative. Add a focused `overlay_geometry.rs`
module for pure layout defaults, validation, display resolution, clamping, and
logical-to-physical conversion. React renders an editor and holds a draft, but
never decides whether an overlay is safe to place. This prevents a canvas and
the real overlay from disagreeing and keeps geometry durable across Retina,
resolution, Dock, and display changes.

## Data Model

Add an optional, backward-compatible `overlay_layout` setting:

```text
OverlayLayoutSettings
  display_affinity: String
  pill: PillFrame { x_ratio, y_ratio, width }
  panel: PanelFrame { x_ratio, y_ratio, width, height }

`x_ratio` and `y_ratio` are finite normalized top-left positions in the
selected usable work area. Width and height are logical Tauri/macOS points,
never physical pixels. Pill height remains the supported fixed logical height;
panel width and height are logical points.
```

This deliberately mixes normalized position with logical size: positioning
adapts to a changed work area, while a 520-point panel stays perceptually
stable on a larger display and remains Retina-correct. On every apply/show,
native code resolves affinity, then clamps logical dimensions to supported
limits and the current visible work area. Defaults reproduce the Phase 1
centered lower pill and right-side panel.

## Native Flow

`windows.rs` exposes these typed commands through the Tauri bridge:

```text
get_overlay_layout_editor_state()
preview_overlay_layout(draft)
save_overlay_layout(draft)
cancel_overlay_layout_preview()
reset_overlay_layout()
```

Every mutation returns the native-resolved layout and display metadata. Invalid
or non-finite input is rejected or safely clamped; raw IPC errors are never
shown. Preview is process-local and never writes settings. Its session captures
the committed layout plus exact prior pill/panel visibility; preview reveals
real overlays only when necessary. Save atomically persists and applies the
validated draft, then restores captured visibility unless the user explicitly
opened an overlay outside preview. Cancel restores committed layout and exact
captured visibility. Main-window close and app quit cancel any preview; no
draft survives restart. Reset changes only the editor draft to defaults:
Preview may show it, Save persists it, and Cancel discards it. If a preview
display disappears, native code re-resolves committed/default layout against
the primary visible work area.

Existing `show_pill` and `show_panel` call this same resolver. Thus a hotkey,
tray action, preview, scale change, Dock change, and relaunch all use one
placement implementation. Defensive recreation remains unchanged.

## Editor UX

The Phase 1 **Layout** page becomes `OverlayLayoutEditor`. It fetches one
native display snapshot and renders a scalable representation of that usable
area. Menu-bar/notch and Dock exclusions are visibly unavailable regions, not
guessed CSS insets. Pill and panel representations are draggable; the pill
has a horizontal resize handle, and the panel has width/height resize handles.

Keyboard-accessible move/resize controls are required alongside pointer
dragging. The editor keeps its draft normalized, requests native validation on
Preview/Save, and explains any native clamp in plain language. Preview is
explicit, so ordinary dragging does not constantly move always-on-top real
windows. Buttons are Preview, Save, Cancel, and Reset. The preview status is
clear but does not trigger voice, modify history, or add another overlay.

## Lifecycle and Errors

The active editor display resolves in this order: saved affinity when present,
the display containing the Sani main window, the primary display, then the
first available display. Cursor position is not an editor fallback; existing
quick-overlay behavior may continue to use it. Phase 2 needs no display-picker
UI. A missing display snapshot disables Preview/Save and retains committed
placement. A malformed saved layout falls back to defaults. A failed preview
application restores committed layout and captured visibility.

## Verification

Rust tests cover defaults, mixed normalized-position/logical-size conversion,
invalid drafts, all-edge clamping, pill/panel limits, Retina conversion,
resolution changes, main-window display fallback, missing-display fallback,
and cancel visibility restoration. Settings tests cover old settings, save,
and reset. Renderer tests cover keyboard controls plus Preview/Save/Cancel/Reset.
Manual macOS acceptance covers built-in preview/save/cancel/reset, Retina,
main-window resize, relaunch, Dock edges only when the user allows setting
changes, and external-display disconnect only when hardware is present.

## Non-Goals

No STT model manager, manual voice submission, avatars, agents/sub-agents,
full Settings redesign, diagnostics/log expansion, provider redesign, or CUA
redesign is included.

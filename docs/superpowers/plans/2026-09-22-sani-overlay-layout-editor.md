# Sani Overlay Layout Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a MacBook-first editor in Sani's main window that safely configures the existing Voice Pill and Conversation Panel.

**Architecture:** Rust owns a pure mixed-unit overlay resolver and applies every real overlay frame. React owns only an accessible normalized-position/logical-size draft and invokes a narrow preview lifecycle. The existing overlay windows and main-window state remain independent.

**Tech Stack:** Tauri 2, Rust, AppKit work-area bridge, React 18, TypeScript, existing JSON settings.

**Spec:** `docs/superpowers/specs/2026-09-22-sani-overlay-layout-editor-design.md`

## Execution Record — 2026-09-23

- [x] Task 1 — Pure mixed-unit overlay geometry (`95ba079`).
- [x] Task 2 — Persisted overlay layout separate from main-window state (`bd02149`).
- [x] Task 3 — Native real-overlay preview lifecycle and visibility restoration (`d98c299`).
- [x] Task 4 — Typed bridge and accessible main-window layout editor (`517e41a`).
- [x] Task 5 — Regression, packaged build, and verified behavior documentation (`084cdac`).

Phase 2 is accepted complete. The unchecked step-level boxes below are the
original execution recipe; this record is the authoritative completion status.

## Global Constraints

- Persist `x_ratio`/`y_ratio` normalized to a usable work area; persist width/height in logical points; never persist physical pixels.
- Resolve display in order: saved affinity, main-window display, primary, first available.
- Preserve pre-preview pill/panel visibility on Save and Cancel; no preview draft may persist.
- Reuse existing pill/panel windows and all existing voice/runtime behavior.
- Do not add Phase 3 features, agents, STT changes, manual submission, provider changes, log UI, or another app window.

## Review Focus

- A 520pt panel on a large external display stays 520 logical points: Task 1.
- A missing saved display uses the main-window/primary display and clamps: Task 1.
- A left/right/bottom Dock cannot receive an overlay frame: Task 1.
- Cancel restores both committed placement and the exact hidden/visible state: Task 3.
- Reset changes only the draft until Save: Task 4.

## File Structure

| File | Responsibility |
| --- | --- |
| `sani/src-tauri/src/overlay_geometry.rs` | Pure frames, defaults, mixed-unit validation/resolution/clamping. |
| `sani/src-tauri/src/settings.rs` | Backwards-compatible committed overlay layout. |
| `sani/src-tauri/src/windows.rs` | Overlay application, preview session, visibility capture, display resolution. |
| `sani/src-tauri/src/main.rs` | Tauri command registration and preview cancellation on exit. |
| `sani/src/lib/tauri.ts` | Typed layout-editor bridge. |
| `sani/src/app/OverlayLayoutEditor.tsx` | Accessible draft editor, explicit preview lifecycle. |
| `sani/src/app/MainApp.tsx`, `sani/src/styles/main.css` | Route Layout to editor and style canvas/controls. |

### Task 1: Pure mixed-unit overlay geometry

**Files:** Create `sani/src-tauri/src/overlay_geometry.rs`; modify `sani/src-tauri/src/main.rs`.

**Interfaces:** Produce `OverlayFrame { x_ratio: f64, y_ratio: f64, width: f64, height: f64 }`, `OverlayLayout { display_affinity: String, pill: OverlayFrame, panel: OverlayFrame }`, `resolve_overlay_layout(layout, areas, main_display) -> ResolvedOverlayLayout`.

- [ ] **Step 1: Write failing unit tests**

```rust
#[test]
fn panel_keeps_logical_width_on_larger_work_area() {
    let resolved = resolve_overlay_layout(&saved(520.0, 640.0), &[area("large", 2200.0, 1300.0, true)], Some("large"));
    assert_eq!(resolved.panel.width, 520.0);
}
#[test]
fn missing_affinity_uses_main_display_and_clamps() {
    let resolved = resolve_overlay_layout(&saved(9999.0, 9999.0), &[area("main", 1200.0, 800.0, true)], Some("main"));
    assert_eq!(resolved.display.id, "main");
    assert!(resolved.panel.x + resolved.panel.width <= resolved.display.width);
}
```

- [ ] **Step 2: Run `cd sani/src-tauri && cargo test overlay_geometry`; verify compile failure.**
- [ ] **Step 3: Implement defaults, finite checks, logical size limits, ratio-position clamping, and the display fallback order.** Use `LogicalWorkArea`; return logical frames and defer physical conversion to `windows.rs`.
- [ ] **Step 4: Add tests for left/right/bottom visible-frame offsets, Retina scale independence, invalid ratios/sizes, and defaults. Run `cargo test overlay_geometry`.**
- [ ] **Step 5: Commit:** `git add sani/src-tauri/src/overlay_geometry.rs sani/src-tauri/src/main.rs && git commit -m "feat: resolve Sani overlay layouts safely"`.

### Task 2: Persist committed layout separately from main-window state

**Files:** Modify `sani/src-tauri/src/settings.rs`; test its `#[cfg(test)]` module.

**Interfaces:** Produce `OverlayLayoutSettings`, `load_overlay_layout`, and `update_overlay_layout(settings, layout)`. Consume Task 1 `OverlayLayout`.

- [ ] **Step 1: Write failing tests**

```rust
#[test]
fn old_settings_without_overlay_layout_still_load() { assert!(Settings::default().overlay_layout.is_none()); }
#[test]
fn saving_overlay_layout_never_changes_main_window() { /* set main_window; update overlay; assert main fields unchanged */ }
```

- [ ] **Step 2: Run `cd sani/src-tauri && cargo test settings`; verify failure.**
- [ ] **Step 3: Add serde-default optional settings with `x_ratio`, `y_ratio`, and logical dimensions; preserve existing `main_window` unchanged.**
- [ ] **Step 4: Run focused settings tests and full `cargo test`.**
- [ ] **Step 5: Commit:** `git add sani/src-tauri/src/settings.rs && git commit -m "feat: persist Sani overlay layouts"`.

### Task 3: Native overlay application and preview session

**Files:** Modify `sani/src-tauri/src/windows.rs`, `sani/src-tauri/src/main.rs`; tests in `windows.rs`.

**Interfaces:** Produce `overlay_editor_state`, `preview_overlay_layout`, `save_overlay_layout`, `cancel_overlay_preview`, `reset_overlay_draft`. Preview state captures `pill_visible`, `panel_visible`, and committed layout.

- [ ] **Step 1: Write failing tests**

```rust
#[test]
fn cancel_restores_committed_layout_and_prior_visibility() {
    let session = PreviewSession::new(committed(), false, true);
    assert_eq!(session.cancel(), RestoreAction::new(committed(), false, true));
}
```

- [ ] **Step 2: Run `cd sani/src-tauri && cargo test windows`; verify failure.**
- [ ] **Step 3: Route `show_pill`/`show_panel` through Task 1 resolver; capture visibility once on preview start; apply draft temporarily; make Save/Cancel and main close/app exit restore visibility and discard provisional state.**
- [ ] **Step 4: Add command handlers and register them; return resolved frames, not raw drafts. Run `cargo test windows && cargo check`.**
- [ ] **Step 5: Commit:** `git add sani/src-tauri/src/windows.rs sani/src-tauri/src/main.rs && git commit -m "feat: preview Sani overlay layouts safely"`.

### Task 4: Typed bridge and accessible editor

**Files:** Create `sani/src/app/OverlayLayoutEditor.tsx`; modify `sani/src/lib/tauri.ts`, `sani/src/app/MainApp.tsx`, `sani/src/styles/main.css`.

**Interfaces:** Consume commands from Task 3. `OverlayLayoutEditor` owns `draft`, calls Preview only on explicit action, and calls Cancel on unmount if preview remains active.

- [ ] **Step 1: Add typed `OverlayFrame`, `OverlayLayout`, editor-state payloads, and command wrappers to `tauri.ts`; run `cd sani && npm run build` to verify missing component only.**
- [ ] **Step 2: Implement the editor canvas from native display metadata, semantic pill/panel controls, pointer drag plus keyboard movement/resize, Preview/Save/Cancel/Reset. Reset replaces draft only.**
- [ ] **Step 3: Replace Layout placeholder with the editor; style work-area exclusions, focus states, constrained handles, and sub-900pt responsive fallback.**
- [ ] **Step 4: Run `cd sani && npm run build`; manually verify draft does not move overlays until Preview and Cancel restores prior visibility.**
- [ ] **Step 5: Commit:** `git add sani/src/app/OverlayLayoutEditor.tsx sani/src/app/MainApp.tsx sani/src/lib/tauri.ts sani/src/styles/main.css && git commit -m "feat: add Sani overlay layout editor"`.

### Task 5: Regression and macOS acceptance

**Files:** Modify `sani/README.md` only for verified layout behavior.

- [ ] **Step 1: Run `cd sani && npm run build && cargo test --manifest-path src-tauri/Cargo.toml && cargo check --manifest-path src-tauri/Cargo.toml`.**
- [ ] **Step 2: Run `uv run pytest -q tests/unit/test_sani_core_protocol.py tests/unit/test_core_desktop.py tests/unit/test_sani_core_registry.py`; record exact failures without unrelated changes.**
- [ ] **Step 3: Build `cd sani && npm run tauri -- build`; test built-in preview, Save, Cancel, Reset, relaunch, hidden overlays, and Retina rendering. Test Dock edges/external display only when hardware/settings authorization is available; record unavailable cases truthfully.**
- [ ] **Step 4: Document only observed behavior and commit:** `git add sani/README.md && git commit -m "docs: verify Sani overlay layout editor"`.

## Plan Self-Review

Spec coverage: Tasks 1–2 implement the exact mixed-unit persisted model; Task 3 owns preview visibility, real-overlay application, lifecycle, and deterministic display selection; Task 4 is editor-only; Task 5 verifies it. No Phase 3 scope appears. Placeholder scan: none. Type consistency: `OverlayFrame` and `OverlayLayout` originate in Task 1 and are consumed unchanged thereafter. Each Review Focus item has a named Task 1, 3, or 4 verification step.

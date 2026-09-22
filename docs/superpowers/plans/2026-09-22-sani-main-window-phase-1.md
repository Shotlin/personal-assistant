# Sani Main Window — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a MacBook-first normal Sani desktop window that safely restores its normal bounds and maximized state while retaining existing pill and panel overlays as optional companions.

**Architecture:** Add a `main` Tauri window and Vite/React entry alongside the current onboarding, pill, and panel entries. Keep display geometry and persistence native: a small pure Rust geometry layer resolves logical normal bounds against the true macOS work area, supplied by Tauri when available or `NSScreen.visibleFrame` through a native bridge. The React shell reads existing history/runtime events through the Tauri bridge and never becomes a second data source.

**Tech Stack:** Tauri 2, Rust 1.77, macOS AppKit/Objective-C bridge, React 18, TypeScript 5.6, Vite 5, existing rusqlite history and Tauri event bridge.

**Spec:** `docs/superpowers/specs/2026-09-22-sani-main-window-phase-1-design.md`

## Global Constraints

- Target macOS 12.0+ first, on Apple Silicon and Intel MacBooks; preserve cross-platform compilation with `cfg(target_os = "macos")` boundaries.
- Use native decorated titlebar/traffic lights, standard Dock behavior, and never request exclusive fullscreen during normal launch or restore.
- Persist logical normal bounds relative to a display’s usable work area; do not persist raw physical pixels.
- Use true usable macOS work areas that exclude menu bar/notch and a Dock on any edge; `NSScreen.visibleFrame` is mandatory if Tauri cannot supply one.
- Persist `maximized` independently from normal `x`, `y`, `width`, and `height`; maximization must never overwrite normal bounds.
- On restore resolve a connected display, clamp normal bounds, apply normal bounds, then restore maximized state.
- If the saved display is unavailable, use the primary/current display and never prevent launch or strand the window off-screen.
- Keep the existing pill/panel, hotkey, tray, `sani-core`, onboarding, SQLite history, provider credential storage, STT, and CUA architecture intact.
- Phase 1 does not implement overlay layout editing, STT changes, model management, agent-selection changes, new agents, or logging UI.
- Do not expose raw IPC/Rust errors or internal architecture labels in normal-user UI.

## Review Focus

- A saved external-display frame restored after that display is unplugged must open visibly on the MacBook primary display; Task 1 pins this fallback.
- A left or right Dock must shrink/shift the native usable work area just like a bottom Dock; Task 2 pins the `NSScreen.visibleFrame` conversion contract.
- Maximizing then quitting must preserve the prior normal frame; Task 3 pins that persistence behavior.
- A stale, malformed, or oversized saved frame must yield a usable main window rather than hide it behind system UI; Task 1 pins clamping and Task 4 uses the resolver before showing.
- Closing the main window must hide it without stopping the hotkey/tray/runtime, while Dock/reopen restores and focuses it; Task 4 covers this lifecycle and the final macOS run verifies it.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `sani/src-tauri/src/window_geometry.rs` | Pure logical work-area, display selection, normal-bounds validation, and clamping; unit-testable without macOS/Tauri windows. |
| `sani/src-tauri/src/macos_work_area.rs` | Rust wrapper over the native AppKit work-area bridge; converts `NSScreen.visibleFrame` to the geometry module’s logical representation. |
| `sani/src-tauri/native/macos_work_area.m` | Objective-C/AppKit implementation that returns each `NSScreen.visibleFrame` and screen identity. |
| `sani/src-tauri/build.rs` | Compiles the new native bridge with the existing macOS bridges. |
| `sani/src-tauri/src/settings.rs` | Backward-compatible `MainWindowSettings` persistence model. |
| `sani/src-tauri/src/windows.rs` | Main-window creation/show/hide, actual work-area collection, restore application, and normal-frame capture. |
| `sani/src-tauri/src/main.rs` | Main lifecycle, tray command, close interception, commands, and main-ready startup gate. |
| `sani/app.html`, `sani/src/app/main-main.tsx`, `sani/src/app/MainApp.tsx` | Main renderer entry and application shell. |
| `sani/src/components/MainSidebar.tsx`, `sani/src/components/MainConversation.tsx` | Accessible navigation and Home’s real-history/runtime view. |
| `sani/src/styles/main.css` | Opaque desktop-product layout responsive down to the 900 logical-point minimum. |
| `sani/src/lib/tauri.ts` | Narrow extensions to existing bridge types/events/commands for the main renderer. |

### Task 1: Pure logical display and normal-bounds resolver

**Files:**
- Create: `sani/src-tauri/src/window_geometry.rs`
- Modify: `sani/src-tauri/src/main.rs` (declare `mod window_geometry;`)
- Test: `sani/src-tauri/src/window_geometry.rs` (`#[cfg(test)]` module)

**Interfaces:**
- Produces `LogicalWorkArea { id: String, x: f64, y: f64, width: f64, height: f64, scale_factor: f64, is_primary: bool }`.
- Produces `NormalWindowBounds { x: f64, y: f64, width: f64, height: f64 }`, where coordinates are relative to the selected work-area origin.
- Produces `SavedMainWindow { display_id: String, normal: NormalWindowBounds, maximized: bool }` and `resolve_restore(saved: Option<&SavedMainWindow>, areas: &[LogicalWorkArea]) -> ResolvedRestore`.
- Consumes only logical values; `windows.rs` performs native/Tauri physical conversion after resolution.

- [ ] **Step 1: Write failing resolver tests**

```rust
fn area(id: &str, x: f64, y: f64, width: f64, height: f64, scale: f64, is_primary: bool) -> LogicalWorkArea {
    LogicalWorkArea { id: id.into(), x, y, width, height, scale_factor: scale, is_primary }
}

fn bounds(x: f64, y: f64, width: f64, height: f64) -> NormalWindowBounds {
    NormalWindowBounds { x, y, width, height }
}

#[test]
fn missing_external_display_uses_primary_visible_area() {
    let areas = vec![area("built-in", 0.0, 25.0, 1512.0, 945.0, 2.0, true)];
    let saved = SavedMainWindow {
        display_id: "external".into(),
        normal: bounds(80.0, 40.0, 1180.0, 760.0),
        maximized: false,
    };
    let resolved = resolve_restore(Some(&saved), &areas);
    assert_eq!(resolved.work_area.id, "built-in");
    assert!(resolved.normal.x >= 0.0);
    assert!(resolved.normal.y >= 0.0);
    assert!(resolved.normal.x + resolved.normal.width <= resolved.work_area.width);
    assert!(resolved.normal.y + resolved.normal.height <= resolved.work_area.height);
}

#[test]
fn malformed_or_offscreen_normal_bounds_are_clamped_not_reused() {
    let areas = vec![area("built-in", 0.0, 25.0, 1512.0, 945.0, 2.0, true)];
    let saved = SavedMainWindow {
        display_id: "built-in".into(),
        normal: bounds(-10000.0, f64::NAN, 99999.0, -1.0),
        maximized: true,
    };
    let resolved = resolve_restore(Some(&saved), &areas);
    assert_eq!(resolved.normal.width, 1180.0);
    assert_eq!(resolved.normal.height, 760.0);
    assert!(resolved.maximized);
    assert!(resolved.normal.x >= 0.0 && resolved.normal.y >= 0.0);
    assert!(resolved.normal.x + resolved.normal.width <= resolved.work_area.width);
    assert!(resolved.normal.y + resolved.normal.height <= resolved.work_area.height);
}

#[test]
fn restore_retains_maximized_flag_but_resolves_normal_bounds_first() {
    let areas = vec![area("built-in", 0.0, 25.0, 1512.0, 945.0, 2.0, true)];
    let saved = SavedMainWindow {
        display_id: "built-in".into(),
        normal: bounds(110.0, 90.0, 1040.0, 700.0),
        maximized: true,
    };
    let resolved = resolve_restore(Some(&saved), &areas);
    assert_eq!(resolved.normal, bounds(110.0, 90.0, 1040.0, 700.0));
    assert!(resolved.maximized);
}
```

- [ ] **Step 2: Run the resolver tests and verify they fail**

Run: `cd sani && cargo test window_geometry`

Expected: FAIL because `window_geometry` and the resolver interfaces do not exist.

- [ ] **Step 3: Implement the isolated geometry model and resolver**

```rust
pub const INITIAL_MAIN_WIDTH: f64 = 1180.0;
pub const INITIAL_MAIN_HEIGHT: f64 = 760.0;
pub const MIN_MAIN_WIDTH: f64 = 900.0;
pub const MIN_MAIN_HEIGHT: f64 = 620.0;

pub fn resolve_restore(
    saved: Option<&SavedMainWindow>,
    areas: &[LogicalWorkArea],
) -> ResolvedRestore {
    let work_area = select_work_area(saved.map(|s| s.display_id.as_str()), areas);
    let requested = saved.map(|s| s.normal).unwrap_or_else(|| centered_initial(&work_area));
    let normal = clamp_normal_bounds(requested, &work_area);
    ResolvedRestore {
        work_area,
        normal,
        maximized: saved.is_some_and(|state| state.maximized),
    }
}
```

Implement finite-number checks before sizing; constrain a normal frame fully inside the usable logical rectangle; center an absent/invalid frame; prefer an exact saved identity, then `is_primary`, then the first available area. If no areas are available, synthesize one conservative 1440 × 900 logical area solely as a last launch guard.

- [ ] **Step 4: Run the resolver test suite**

Run: `cd sani && cargo test window_geometry`

Expected: PASS, including exact display match, primary fallback, scale-independent logical clamping, minimum-size behavior, constrained-work-area fit, invalid data, and maximized-flag preservation.

- [ ] **Step 5: Commit the geometry unit**

```bash
git add sani/src-tauri/src/window_geometry.rs sani/src-tauri/src/main.rs
git commit -m "feat: resolve Sani main window bounds safely"
```

### Task 2: Native macOS usable-work-area bridge

**Files:**
- Create: `sani/src-tauri/native/macos_work_area.m`
- Create: `sani/src-tauri/src/macos_work_area.rs`
- Modify: `sani/src-tauri/build.rs`
- Modify: `sani/src-tauri/src/main.rs` (declare `mod macos_work_area;`)
- Test: `sani/src-tauri/src/macos_work_area.rs` (`#[cfg(test)]` coordinate conversion tests)

**Interfaces:**
- Produces `pub fn visible_work_areas() -> Vec<NativeVisibleFrame>` on macOS and `Vec::new()` elsewhere.
- `NativeVisibleFrame` contains `id`, `screen_x`, `screen_y`, `screen_width`, `screen_height`, `visible_x`, `visible_y`, `visible_width`, `visible_height`, and `backing_scale_factor` in AppKit points. Its identity combines the display’s `NSScreenNumber` with its localized name; an unmatched identity falls back safely rather than attempting to reuse coordinates.
- `windows::available_logical_work_areas` consumes native visible frames first and joins them to Tauri monitor identities/physical desktop positions.

- [ ] **Step 1: Write failing work-area conversion tests**

```rust
fn appkit_frame(id: &str, x: f64, y: f64, width: f64, height: f64, scale: f64) -> AppKitFrame {
    AppKitFrame { id: id.into(), x, y, width, height, scale_factor: scale }
}

#[test]
fn visible_frame_preserves_a_left_dock_inset() {
    let screen = appkit_frame("built-in", 0.0, 0.0, 1512.0, 982.0, 2.0);
    let visible = appkit_frame("built-in", 96.0, 25.0, 1416.0, 957.0, 2.0);
    let area = logical_visible_area(screen, visible);
    assert_eq!(area.x, 96.0);
    assert_eq!(area.y, 0.0);
    assert_eq!(area.width, 1416.0);
    assert_eq!(area.height, 957.0);
}

#[test]
fn visible_frame_preserves_a_right_dock_inset() {
    let screen = appkit_frame("built-in", 0.0, 0.0, 1512.0, 982.0, 2.0);
    let visible = appkit_frame("built-in", 0.0, 25.0, 1416.0, 957.0, 2.0);
    let area = logical_visible_area(screen, visible);
    assert_eq!(area.x, 0.0);
    assert_eq!(area.width, 1416.0);
}
```

- [ ] **Step 2: Run the native-wrapper tests and verify they fail**

Run: `cd sani && cargo test macos_work_area`

Expected: FAIL because the bridge wrapper and conversion functions do not exist.

- [ ] **Step 3: Implement `NSScreen.visibleFrame` collection**

```objc
// macos_work_area.m
// Return JSON records for every NSScreen with frame, visibleFrame,
// localizedName, and backingScaleFactor. `visibleFrame` is AppKit points and
// is the source of truth for menu bar/notch and Dock placement on all edges.
const char *sani_visible_work_areas_json(void);
void sani_visible_work_areas_free(const char *json);
```

```rust
#[cfg(target_os = "macos")]
extern "C" {
    fn sani_visible_work_areas_json() -> *const std::ffi::c_char;
    fn sani_visible_work_areas_free(value: *const std::ffi::c_char);
}

pub fn visible_work_areas() -> Vec<NativeVisibleFrame> {
    // Copy the bridge-owned UTF-8 JSON, free it exactly once, deserialize it,
    // and return an empty vector plus a technical log on malformed output.
}
```

Compile `native/macos_work_area.m` in the existing macOS `cc::Build` invocation. Convert AppKit’s bottom-left coordinate system to the Tauri/Cocoa desktop coordinate system in one documented `logical_visible_area` function, using the matching screen frame rather than an inferred top/bottom margin. Keep the full conversion pure and unit-tested; native collection remains a thin platform boundary.

- [ ] **Step 4: Run tests and a macOS bridge smoke check**

Run: `cd sani && cargo test macos_work_area && cargo check`

Expected: PASS. On a MacBook, run the new diagnostic-only helper from a focused test/dev log and confirm an attached left, right, or bottom Dock changes the returned visible frame on the corresponding edge.

- [ ] **Step 5: Commit the native work-area boundary**

```bash
git add sani/src-tauri/native/macos_work_area.m sani/src-tauri/src/macos_work_area.rs sani/src-tauri/build.rs sani/src-tauri/src/main.rs
git commit -m "feat: read macOS visible work areas for Sani"
```

### Task 3: Backward-compatible main-window state and maximization persistence

**Files:**
- Modify: `sani/src-tauri/src/settings.rs`
- Modify: `sani/src-tauri/src/app_state.rs` (provide a narrow settings mutation helper only if needed by window callbacks)
- Test: `sani/src-tauri/src/settings.rs` (`#[cfg(test)]` JSON migration and normal-bounds tests)

**Interfaces:**
- Produces `pub struct MainWindowSettings { pub display_id: String, pub x: f64, pub y: f64, pub width: f64, pub height: f64, pub maximized: bool }`.
- `Settings` gains `pub main_window: Option<MainWindowSettings>` with `#[serde(default)]`.
- Produces `pub fn update_normal_main_window(settings: &mut Settings, display_id: String, normal: NormalWindowBounds)` and `pub fn update_main_window_maximized(settings: &mut Settings, maximized: bool)`.
- `update_main_window_maximized` changes only the flag; it never touches normal geometry.

- [ ] **Step 1: Write failing settings persistence tests**

```rust
#[test]
fn older_settings_without_main_window_load_normally() {
    let settings: Settings = serde_json::from_str(r#"{"theme":"dark"}"#).unwrap();
    assert!(settings.main_window.is_none());
    assert_eq!(settings.theme, "dark");
}

#[test]
fn maximized_transition_keeps_last_normal_bounds() {
    let mut settings = Settings::default();
    update_normal_main_window(&mut settings, "built-in".into(), NormalWindowBounds { x: 100.0, y: 80.0, width: 1040.0, height: 700.0 });
    update_main_window_maximized(&mut settings, true);
    let saved = settings.main_window.unwrap();
    assert_eq!(saved.width, 1040.0);
    assert_eq!(saved.height, 700.0);
    assert!(saved.maximized);
}
```

- [ ] **Step 2: Run the settings tests and verify they fail**

Run: `cd sani && cargo test settings`

Expected: FAIL because `main_window` and the update functions do not exist.

- [ ] **Step 3: Implement the persisted model and separate update paths**

```rust
pub fn update_main_window_maximized(settings: &mut Settings, maximized: bool) {
    let state = settings.main_window.get_or_insert_with(MainWindowSettings::default);
    state.maximized = maximized;
}

pub fn update_normal_main_window(
    settings: &mut Settings,
    display_id: String,
    normal: NormalWindowBounds,
) {
    let state = settings.main_window.get_or_insert_with(MainWindowSettings::default);
    state.display_id = display_id;
    state.x = normal.x;
    state.y = normal.y;
    state.width = normal.width;
    state.height = normal.height;
}
```

Give `MainWindowSettings` a `Default` that represents no usable normal frame (`width`/`height` zero) and let Task 1’s resolver treat it as an initial launch. Preserve the current unknown-field migration behavior and never add provider secrets to this structure.

- [ ] **Step 4: Run focused persistence tests**

Run: `cd sani && cargo test settings`

Expected: PASS for old JSON, JSON round trip, normal frame update, maximize-only update, and unmaximize behavior.

- [ ] **Step 5: Commit settings persistence**

```bash
git add sani/src-tauri/src/settings.rs sani/src-tauri/src/app_state.rs
git commit -m "feat: persist Sani main window state"
```

### Task 4: Native main window creation, restoration, and application lifecycle

**Files:**
- Modify: `sani/src-tauri/src/windows.rs`
- Modify: `sani/src-tauri/src/main.rs`
- Modify: `sani/src-tauri/src/app_state.rs` (main UI ready state only)
- Test: `sani/src-tauri/src/windows.rs` (delegate pure restoration cases to Task 1; add lifecycle-state unit tests where no AppHandle is required)

**Interfaces:**
- Produces `pub const MAIN_LABEL: &str = "main"`.
- Produces `pub fn create_main(app: &AppHandle) -> tauri::Result<()>`, `pub fn show_main(app: &AppHandle) -> tauri::Result<()>`, `pub fn hide_main(app: &AppHandle)`, and `pub fn persist_main_window(app: &AppHandle)`.
- Produces `pub fn mark_ui_ready(app: &AppHandle, which: &str)` support for `"main"`.
- Main renderer emits `sani://main-ui-ready`; startup waits for it before first reveal, with an honest timeout fallback.

- [ ] **Step 1: Write failing lifecycle-focused tests**

```rust
#[test]
fn close_main_is_hide_not_quit_policy() {
    assert_eq!(close_policy_for_label(MAIN_LABEL), ClosePolicy::Hide);
    assert_eq!(close_policy_for_label(PILL_LABEL), ClosePolicy::Allow);
}

#[test]
fn normal_bounds_are_not_captured_while_maximized() {
    let event = WindowSnapshot { maximized: true, normal: bounds(80.0, 70.0, 1100.0, 740.0) };
    assert_eq!(normal_bounds_to_persist(&event), None);
}
```

- [ ] **Step 2: Run lifecycle/geometry tests and verify they fail**

Run: `cd sani && cargo test windows`

Expected: FAIL because the main-window lifecycle helpers do not exist.

- [ ] **Step 3: Build and restore the main window**

```rust
fn build_main(app: &AppHandle) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, MAIN_LABEL, WebviewUrl::App("app.html".into()))
        .title("Sani")
        .min_inner_size(MIN_MAIN_WIDTH, MIN_MAIN_HEIGHT)
        .decorations(true)
        .transparent(false)
        .always_on_top(false)
        .skip_taskbar(false)
        .resizable(true)
        .visible(false)
        .build()?;
    Ok(())
}

pub fn show_main(app: &AppHandle) -> tauri::Result<()> {
    ensure_main_exists(app)?;
    let resolved = resolve_main_restore(app);
    apply_normal_bounds(app, &resolved)?;
    if resolved.maximized { main.maximize()?; }
    main.show()?;
    main.set_focus()?;
    Ok(())
}
```

Use Task 2’s real visible frames to build `LogicalWorkArea` values; only use Tauri full-monitor data if native work-area collection has no matching record. Install a Tauri window-event handler that debounces move/resize saves only while `is_maximized()` is false, records a maximize transition with `update_main_window_maximized(true)`, records unmaximize with `false` and the returned normal frame, and prevents `CloseRequested` for `MAIN_LABEL` before hiding it. Persist normal state synchronously before hide/quit. Do not modify `show_pill`, `show_panel`, or overlay geometry.

- [ ] **Step 4: Change normal-mode/reopen/tray behavior**

```rust
pub fn enter_normal_mode(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Regular)?;
    windows::create_main(app)?;
    windows::create_all(app)?; // pill/panel stay hidden
    setup_tray(app)?;
    register_existing_hotkey(app)?;
    sani_core::start_at_startup(app);
    spawn_main_launch_reveal(app.clone());
    Ok(())
}
```

Update `RunEvent::Reopen` to call `windows::show_main` after onboarding. Add an “Open Sani” tray item that calls `show_main`; retain “Start Listening,” “Show Conversation,” and “Quit Sani.” Replace the overlay-only cold launch reveal with a main-only reveal. Keep hotkey behavior capable of showing pill/panel when voice is invoked.

- [ ] **Step 5: Run native checks**

Run: `cd sani && cargo test window_geometry && cargo test windows && cargo test settings && cargo check`

Expected: PASS. Confirm from logs that one normal launch creates main/pill/panel, reveals only main, and starts a single `sani-core` runtime.

- [ ] **Step 6: Commit native lifecycle**

```bash
git add sani/src-tauri/src/windows.rs sani/src-tauri/src/main.rs sani/src-tauri/src/app_state.rs
git commit -m "feat: open Sani in a normal desktop window"
```

### Task 5: Main React entry, responsive application shell, and real Home data

**Files:**
- Create: `sani/app.html`
- Create: `sani/src/app/main-main.tsx`
- Create: `sani/src/app/MainApp.tsx`
- Create: `sani/src/components/MainSidebar.tsx`
- Create: `sani/src/components/MainConversation.tsx`
- Create: `sani/src/styles/main.css`
- Modify: `sani/vite.config.ts`
- Modify: `sani/src/lib/tauri.ts`
- Modify: `sani/src/components/Message.tsx`
- Test: TypeScript compiler and Vite multi-entry build

**Interfaces:**
- `MainApp` calls `mainReady()` exactly once after subscriptions are installed.
- `MainConversation` accepts `messages: ChatMessage[]`, `state: UiState`, `partial: string`, and live run events sourced from the existing Tauri bridge.
- `Message` gains optional `agentName?: string | null` and renders stored agent attribution for assistant messages without guessing from current settings.
- `tauri.ts` produces `mainReady`, `onMessage` typed as `ChatMessage`, and any read-only state command required to get the active conversation ID.

- [ ] **Step 1: Write a failing multi-entry build check**

Run: `cd sani && npm run build`

Expected before implementation: PASS for existing entries only, and `test -f sani/dist/app.html` fails because the main entry does not exist.

- [ ] **Step 2: Add the main Vite entry and bridge extensions**

```ts
// vite.config.ts
input: {
  main: resolve(__dirname, "app.html"),
  pill: resolve(__dirname, "index.html"),
  panel: resolve(__dirname, "panel.html"),
  onboarding: resolve(__dirname, "onboarding.html"),
}

// lib/tauri.ts
export const mainReady = () => invoke<void>("main_ready");
export const onMessage = (cb: (message: ChatMessage) => void) =>
  listen<ChatMessage>("sani://message", (event) => cb(event.payload));
```

Keep all current bridge APIs source-compatible with pill/panel. Add `main_ready` to the native invoke handler and have it emit the current active history snapshot through the existing `sani://history-loaded` event rather than creating a duplicate history command.

- [ ] **Step 3: Implement the main shell and Home view**

```tsx
export default function MainApp() {
  const [section, setSection] = useState<Section>("home");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [state, setState] = useState<UiState>("idle");

  useEffect(() => subscribeToExistingSaniEvents({ setMessages, setState }), []);
  useEffect(() => { void mainReady(); }, []);

  return (
    <main className="main-app-shell">
      <MainSidebar selected={section} onSelect={setSection} />
      {section === "home" ? <MainConversation messages={messages} state={state} /> : <SectionBoundary section={section} />}
    </main>
  );
}
```

Build an opaque, desktop-product surface using the existing token palette without overlay glass framing. Home must render real local-history messages, assistant attribution (`agent_name` if present, otherwise “Sani”), current runtime state, and existing activity. Show an honest disabled voice-first composer affordance; do not invent a text execution route. Non-Home navigation destinations are visibly bounded to later phases and contain no fake settings, fake agent roster, or fake logs. Use semantic navigation buttons with `aria-current="page"`, visible focus states, and a collapsed-label breakpoint before the 900-point minimum overflows.

- [ ] **Step 4: Run the renderer build and inspect generated entry**

Run: `cd sani && npm run build && test -f dist/app.html`

Expected: TypeScript and Vite PASS, with `dist/app.html`, overlay, panel, and onboarding entries present.

- [ ] **Step 5: Commit the renderer shell**

```bash
git add sani/app.html sani/src/app/main-main.tsx sani/src/app/MainApp.tsx sani/src/components/MainSidebar.tsx sani/src/components/MainConversation.tsx sani/src/styles/main.css sani/vite.config.ts sani/src/lib/tauri.ts sani/src/components/Message.tsx sani/src-tauri/src/main.rs
git commit -m "feat: add Sani main application shell"
```

### Task 6: Phase 1 regression, packaging checks, and real MacBook acceptance

**Files:**
- Modify: `sani/README.md` (document normal main-window, hide/reopen, and MacBook verification procedure)
- Modify: `docs/superpowers/specs/2026-09-22-sani-main-window-phase-1-design.md` only if an acceptance observation requires a clarified limitation; do not broaden scope.

**Interfaces:**
- Consumes the completed main-window lifecycle and no new backend, model, agent, STT, or overlay-layout interfaces.
- Produces recorded build/test/manual acceptance evidence in the implementation report, not fabricated performance claims.

- [ ] **Step 1: Run automated repository checks**

Run:

```bash
cd sani && npm run build
cd sani && cargo test
cd sani && cargo check
cd .. && pytest -q tests/unit/test_sani_core_protocol.py tests/unit/test_core_desktop.py tests/unit/test_sani_core_registry.py
```

Expected: all selected checks PASS. If an existing unrelated failure occurs, record its exact command/output separately and do not mask it by changing unrelated code.

- [ ] **Step 2: Run a debug application launch test**

Run: `cd sani && npm run tauri -- dev`

Expected: completed-onboarding profile launches a visible, focused, normal `main` window with a native titlebar; pill/panel exist hidden; Sani Core starts once; no exclusive fullscreen is requested.

- [ ] **Step 3: Perform the MacBook manual matrix**

Record each observed result for:

```text
1. Built-in display: launch, resize to minimum, close/hide, Dock click restore.
2. Quit/relaunch: normal bounds and maximized intent restore separately.
3. Finder, Applications, Spotlight, and Dock reopen: main focuses safely.
4. External display attached: move/quit/relaunch keeps visible placement.
5. External display removed: relaunch opens visible on primary MacBook display.
6. Dock bottom, left, right: saved window restores within NSScreen.visibleFrame.
7. Retina/scale changes and sleep/wake: subsequent activation remains reachable.
8. 13-inch Air, 14-inch Pro, 16-inch Pro: Home/sidebar fit without normal-layout horizontal scrolling.
9. Global hotkey: existing quick voice pill path remains usable while main is hidden.
```

Use the real operating-system pointer/window, not screenshots or simulated React state, to judge placement and focus.

- [ ] **Step 4: Build a macOS bundle and verify packaged launch**

Run: `cd sani && npm run tauri -- build`

Expected: macOS bundle succeeds and the packaged app follows the same main-window launch/reopen behavior. If signing/notarization configuration is absent, report packaging output truthfully without treating that external release requirement as a source failure.

- [ ] **Step 5: Commit verification documentation**

```bash
git add sani/README.md docs/superpowers/specs/2026-09-22-sani-main-window-phase-1-design.md
git commit -m "docs: verify Sani main window on macOS"
```

## Plan Self-Review

**Spec coverage:** Tasks 1–4 cover native normal-window restoration, true Dock/menu-bar work areas, separate maximized persistence, native lifecycle, tray, and reopens. Task 5 covers the Vite/Tauri main entry, responsive navigation, Home, real history, and accessibility. Task 6 covers all specified automated, packaged, and real MacBook acceptance paths. No Phase 2+ subsystem is introduced.

**Placeholder scan:** The plan contains no deferred implementation markers. The later-phase navigation boundaries are intentional scope-limited UI states required by the approved Phase 1 shell and do not claim unavailable functionality.

**Type consistency:** `LogicalWorkArea`, `NormalWindowBounds`, `SavedMainWindow`, and `ResolvedRestore` originate in Task 1; Task 3 persistence stores those normal values; Task 4 consumes them before native restoration. `mainReady` is introduced in Task 5 and registered in Task 4’s native command surface.

**Review Focus coverage:** External-monitor fallback and stale geometry are tested in Task 1; all-edge Dock work areas are tested in Task 2; maximization preserves normal bounds in Task 3; hide/reopen behavior is implemented in Task 4 and checked manually in Task 6.

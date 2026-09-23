//! Overlay windows: the mic pill and the conversation panel, plus the normal
//! main window and first-run setup. The overlays are frameless, transparent and
//! always on top; every reveal places them through the one shared resolver in
//! `overlay_geometry`, so a hotkey press, a tray action, a layout-editor
//! preview, a Dock change, a scale change and a relaunch all agree.
//!
//! Lifecycle: overlay windows are *hidden*, never destroyed. `reveal` recreates
//! a window defensively if it is unexpectedly gone, so a hide/close path can
//! never leave Sani unable to reveal itself again.

use parking_lot::Mutex;
use serde::Serialize;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize};
use tauri::{WebviewUrl, WebviewWindowBuilder};

use crate::overlay_geometry::{
    committable_layout, default_panel_frame, default_pill_frame, display_rect,
    resolve_overlay_layout, LogicalRect, OverlayLayout, PANEL_MAX_HEIGHT, PANEL_MAX_WIDTH,
    PANEL_MIN_HEIGHT, PANEL_MIN_WIDTH, PILL_HEIGHT, PILL_MAX_WIDTH, PILL_MIN_WIDTH,
};
use crate::settings;
use crate::window_geometry::{
    resolve_restore, LogicalWorkArea, NormalWindowBounds, SavedMainWindow, INITIAL_MAIN_HEIGHT,
    INITIAL_MAIN_WIDTH, MIN_MAIN_HEIGHT, MIN_MAIN_WIDTH,
};

pub const MAIN_LABEL: &str = "main";
pub const PILL_LABEL: &str = "pill";
pub const PANEL_LABEL: &str = "panel";
pub const ONBOARDING_LABEL: &str = "onboarding";

/// First-run setup window: a real, decorated desktop window (native titlebar +
/// traffic lights), opaque near-black content — deliberately NOT the glassy,
/// frameless overlay style used by the pill/panel.
const ONBOARDING_WIDTH: f64 = 820.0;
const ONBOARDING_HEIGHT: f64 = 600.0;

const MAIN_WIDTH: f64 = INITIAL_MAIN_WIDTH;
const MAIN_HEIGHT: f64 = INITIAL_MAIN_HEIGHT;

/// The glass card fills its window exactly, so the vibrancy layer — a plain
/// rect behind the webview — can be clipped to the same radius. Without this
/// a square dark halo shows outside the rounded corners.
const PILL_GLASS_RADIUS: f64 = 48.0;
const PANEL_GLASS_RADIUS: f64 = 22.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClosePolicy {
    Allow,
    Hide,
}

pub fn close_policy_for_label(label: &str) -> ClosePolicy {
    if label == MAIN_LABEL {
        ClosePolicy::Hide
    } else {
        ClosePolicy::Allow
    }
}

/// Which overlay a placement, reveal, or restore step refers to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverlayKind {
    Pill,
    Panel,
}

/// A pure record of what a preview must undo. The committed layout and the
/// exact prior visibility are captured when the preview starts, so Cancel can
/// restore both even if the user moved or hid overlays in between.
#[derive(Debug, Clone, PartialEq)]
pub struct PreviewSession {
    committed: OverlayLayout,
    captured: OverlayVisibility,
    opened_outside_preview: OverlayVisibility,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct OverlayVisibility {
    pub pill: bool,
    pub panel: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RestoreAction {
    pub layout: OverlayLayout,
    pub pill_visible: bool,
    pub panel_visible: bool,
}

impl RestoreAction {
    pub fn new(layout: OverlayLayout, pill_visible: bool, panel_visible: bool) -> Self {
        Self {
            layout,
            pill_visible,
            panel_visible,
        }
    }
}

impl PreviewSession {
    pub fn new(committed: OverlayLayout, pill_visible: bool, panel_visible: bool) -> Self {
        Self {
            committed,
            captured: OverlayVisibility {
                pill: pill_visible,
                panel: panel_visible,
            },
            opened_outside_preview: OverlayVisibility::default(),
        }
    }

    /// A hotkey press or tray action during a preview is the user's own intent:
    /// neither Save nor Cancel may hide that overlay again.
    pub fn note_open_outside_preview(&mut self, kind: OverlayKind) {
        match kind {
            OverlayKind::Pill => self.opened_outside_preview.pill = true,
            OverlayKind::Panel => self.opened_outside_preview.panel = true,
        }
    }

    pub fn cancel(&self) -> RestoreAction {
        self.restore(self.committed.clone())
    }

    pub fn save(&self, layout: OverlayLayout) -> RestoreAction {
        self.restore(layout)
    }

    fn restore(&self, layout: OverlayLayout) -> RestoreAction {
        RestoreAction::new(
            layout,
            self.captured.pill || self.opened_outside_preview.pill,
            self.captured.panel || self.opened_outside_preview.panel,
        )
    }
}

#[derive(Debug, Clone, Copy)]
pub struct WindowSnapshot {
    pub maximized: bool,
    pub normal: NormalWindowBounds,
}

pub fn normal_bounds_to_persist(snapshot: &WindowSnapshot) -> Option<NormalWindowBounds> {
    (!snapshot.maximized).then_some(snapshot.normal)
}

/// Build the pill webview. Shared by cold-launch creation and the defensive
/// recreate path in `show_pill`.
fn build_pill(app: &AppHandle) -> tauri::Result<()> {
    let frame = default_pill_frame();
    let window = WebviewWindowBuilder::new(app, PILL_LABEL, WebviewUrl::App("index.html".into()))
        .title("Sani")
        .inner_size(frame.width, frame.height)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .focused(false)
        .visible(false)
        .on_page_load(|window, payload| {
            log_page_load("pill", &window, &payload);
        })
        .build()?;
    maybe_open_devtools(&window);
    Ok(())
}

/// Build the panel webview. Shared by cold-launch creation and the defensive
/// recreate path in `show_panel`.
fn build_panel(app: &AppHandle) -> tauri::Result<()> {
    let frame = default_panel_frame();
    let window = WebviewWindowBuilder::new(app, PANEL_LABEL, WebviewUrl::App("panel.html".into()))
        .title("Sani — Conversation")
        .inner_size(frame.width, frame.height)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .focused(false)
        .visible(false)
        .on_page_load(|window, payload| {
            log_page_load("panel", &window, &payload);
        })
        .build()?;
    maybe_open_devtools(&window);
    Ok(())
}

/// Build the normal desktop application window. It stays hidden until its
/// saved normal frame is safely resolved against a current work area.
fn build_main(app: &AppHandle) -> tauri::Result<()> {
    let window = WebviewWindowBuilder::new(app, MAIN_LABEL, WebviewUrl::App("app.html".into()))
        .title("Sani")
        .inner_size(MAIN_WIDTH, MAIN_HEIGHT)
        .min_inner_size(MIN_MAIN_WIDTH, MIN_MAIN_HEIGHT)
        .decorations(true)
        .transparent(false)
        .always_on_top(false)
        .skip_taskbar(false)
        .resizable(true)
        .shadow(true)
        .focused(false)
        .visible(false)
        .on_page_load(|window, payload| {
            log_page_load("main", &window, &payload);
        })
        .build()?;
    let app_for_events = app.clone();
    let window_for_events = window.clone();
    window.on_window_event(move |event| match event {
        tauri::WindowEvent::CloseRequested { api, .. } => {
            api.prevent_close();
            // Closing the main window ends any layout preview: the overlays go
            // back to committed placement and their prior visibility.
            cancel_preview(&app_for_events);
            persist_main_window(&app_for_events);
            let _ = window_for_events.hide();
        }
        tauri::WindowEvent::Moved(_)
        | tauri::WindowEvent::Resized(_)
        | tauri::WindowEvent::ScaleFactorChanged { .. } => {
            schedule_main_window_persist(app_for_events.clone());
        }
        _ => {}
    });
    maybe_open_devtools(&window);
    Ok(())
}

/// Build the first-run setup window (decorated, centered, opaque).
fn build_onboarding(app: &AppHandle) -> tauri::Result<()> {
    let window = WebviewWindowBuilder::new(
        app,
        ONBOARDING_LABEL,
        WebviewUrl::App("onboarding.html".into()),
    )
    .title("Sani — Setup")
    .inner_size(ONBOARDING_WIDTH, ONBOARDING_HEIGHT)
    .min_inner_size(720.0, 520.0)
    .decorations(true)
    .transparent(false)
    .always_on_top(false)
    .skip_taskbar(false)
    .resizable(true)
    .shadow(true)
    .center()
    .focused(true)
    .visible(true)
    .on_page_load(|window, payload| {
        log_page_load("onboarding", &window, &payload);
    })
    .build()?;
    maybe_open_devtools(&window);
    Ok(())
}

/// Create and show the setup window (cold first run, or when reopening Sani
/// while setup is still incomplete).
pub fn show_onboarding(app: &AppHandle) -> tauri::Result<()> {
    if app.get_webview_window(ONBOARDING_LABEL).is_none() {
        build_onboarding(app)?;
    }
    if let Some(window) = app.get_webview_window(ONBOARDING_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
    }
    Ok(())
}

/// A UI that fails behind glass must stay diagnosable: dev builds built with
/// `--features devtools` can open the WebView inspector via `SANI_DEVTOOLS=pill,panel`.
fn maybe_open_devtools(window: &tauri::WebviewWindow) {
    #[cfg(feature = "devtools")]
    {
        let targets = std::env::var("SANI_DEVTOOLS").unwrap_or_default();
        if targets
            .split(',')
            .any(|t| t.trim() == window.label() || t.trim() == "all")
        {
            window.open_devtools();
        }
    }
    #[cfg(not(feature = "devtools"))]
    let _ = window;
}

/// Deterministic page-load evidence for both windows: proves index.html /
/// panel.html and their bundled assets actually reached the webview, so a
/// blank glass rectangle can be diagnosed instead of guessed at.
fn log_page_load(
    label: &str,
    window: &tauri::WebviewWindow,
    payload: &tauri::webview::PageLoadPayload,
) {
    match payload.event() {
        PageLoadEvent::Started => {
            log::info!("[ui-boot] {label}: page load started url={}", payload.url());
        }
        PageLoadEvent::Finished => {
            log::info!(
                "[ui-boot] {label}: page load FINISHED url={} (label={})",
                payload.url(),
                window.label()
            );
        }
    }
}

pub fn create_all(app: &AppHandle) -> tauri::Result<()> {
    if app.get_webview_window(PILL_LABEL).is_none() {
        build_pill(app)?;
    }
    if app.get_webview_window(PANEL_LABEL).is_none() {
        build_panel(app)?;
    }
    Ok(())
}

pub fn create_main(app: &AppHandle) -> tauri::Result<()> {
    if app.get_webview_window(MAIN_LABEL).is_none() {
        build_main(app)?;
    }
    Ok(())
}

#[derive(Clone)]
struct MonitorBox {
    position: (i32, i32),
    size: (u32, u32),
    scale: f64,
}

impl MonitorBox {
    /// Monitor size in logical (CSS) points — the unit every layout value here
    /// uses. `available_monitors()` reports physical pixels, so this division is
    /// required before any arithmetic with logical geometry.
    fn logical_size(&self) -> (f64, f64) {
        (
            self.size.0 as f64 / self.scale,
            self.size.1 as f64 / self.scale,
        )
    }

    /// Top-left of a window placed at logical offset `(dx, dy)` from this
    /// monitor's top-left. Monitor position is already physical.
    fn place(&self, dx: f64, dy: f64) -> PhysicalPosition<i32> {
        PhysicalPosition::new(
            self.position.0 + (dx * self.scale).round() as i32,
            self.position.1 + (dy * self.scale).round() as i32,
        )
    }

    /// Logical size → physical `PhysicalSize` for `set_size`.
    fn physical_size(&self, w: f64, h: f64) -> PhysicalSize<u32> {
        PhysicalSize::new(
            (w * self.scale).round().max(1.0) as u32,
            (h * self.scale).round().max(1.0) as u32,
        )
    }
}

#[derive(Clone)]
struct DisplayArea {
    logical: LogicalWorkArea,
    monitor: MonitorBox,
}

fn monitor_id(monitor: &tauri::Monitor) -> String {
    format!(
        "{}:{}:{}x{}",
        monitor.name().map_or("Display", |name| name),
        monitor.position().x,
        monitor.size().width,
        monitor.size().height
    )
}

/// Every attached display as a usable work area plus the physical monitor it
/// lives on. `visibleFrame` from AppKit already excludes the menu bar/notch and
/// a Dock on any edge, so no inset here is ever guessed.
fn display_areas(app: &AppHandle) -> Vec<DisplayArea> {
    let primary_position = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|monitor| (monitor.position().x, monitor.position().y));
    let native = crate::macos_work_area::visible_work_areas();
    app.available_monitors()
        .unwrap_or_default()
        .into_iter()
        .map(|monitor| {
            let scale = monitor.scale_factor();
            let native_frame = native.iter().find(|frame| {
                (frame.backing_scale_factor - scale).abs() < 0.01
                    && ((frame.screen_width * scale).round() as u32 == monitor.size().width)
                    && ((frame.screen_height * scale).round() as u32 == monitor.size().height)
            });
            let (logical, id) = match native_frame {
                Some(frame) => (
                    crate::macos_work_area::logical_visible_area(
                        frame.screen_frame(),
                        frame.visible_frame(),
                    ),
                    frame.id.clone(),
                ),
                None => (
                    LogicalWorkArea {
                        id: monitor_id(&monitor),
                        x: 0.0,
                        y: 0.0,
                        width: monitor.size().width as f64 / scale,
                        height: monitor.size().height as f64 / scale,
                        scale_factor: scale,
                        is_primary: false,
                    },
                    monitor_id(&monitor),
                ),
            };
            DisplayArea {
                logical: LogicalWorkArea {
                    id,
                    is_primary: primary_position
                        == Some((monitor.position().x, monitor.position().y)),
                    ..logical
                },
                monitor: MonitorBox {
                    position: (monitor.position().x, monitor.position().y),
                    size: (monitor.size().width, monitor.size().height),
                    scale,
                },
            }
        })
        .collect()
}

/// The display a physical position falls on, if any.
fn area_containing<'a>(
    areas: &'a [DisplayArea],
    position: PhysicalPosition<i32>,
) -> Option<&'a DisplayArea> {
    areas.iter().find(|area| {
        let left = area.monitor.position.0;
        let top = area.monitor.position.1;
        let right = left + area.monitor.size.0 as i32;
        let bottom = top + area.monitor.size.1 as i32;
        position.x >= left && position.x < right && position.y >= top && position.y < bottom
    })
}

/// The display holding the Sani main window: the editor's first fallback when a
/// saved display affinity is gone. The cursor is deliberately not a fallback.
fn main_window_display(app: &AppHandle, areas: &[DisplayArea]) -> Option<String> {
    let position = app.get_webview_window(MAIN_LABEL)?.outer_position().ok()?;
    area_containing(areas, position).map(|area| area.logical.id.clone())
}

fn saved_main_window(app: &AppHandle) -> Option<SavedMainWindow> {
    app.state::<crate::app_state::SaniState>()
        .settings
        .read()
        .main_window
        .as_ref()
        .map(|saved| SavedMainWindow {
            display_id: saved.display_id.clone(),
            normal: NormalWindowBounds {
                x: saved.x,
                y: saved.y,
                width: saved.width,
                height: saved.height,
            },
            maximized: saved.maximized,
        })
}

/// Show/focus the main window using logical normal bounds first, then restore
/// maximization. A missing display or stale bounds are resolved safely by the
/// pure geometry layer before any native window call happens.
pub fn show_main(app: &AppHandle) -> tauri::Result<()> {
    create_main(app)?;
    let Some(window) = app.get_webview_window(MAIN_LABEL) else {
        return Ok(());
    };
    let work_areas = display_areas(app);
    let logical_areas: Vec<_> = work_areas.iter().map(|area| area.logical.clone()).collect();
    let resolved = resolve_restore(saved_main_window(app).as_ref(), &logical_areas);
    let selected = work_areas
        .iter()
        .find(|area| area.logical.id == resolved.work_area.id)
        .or_else(|| work_areas.iter().find(|area| area.logical.is_primary))
        .or_else(|| work_areas.first());
    if let Some(area) = selected {
        let scale = area.monitor.scale;
        let x =
            area.monitor.position.0 + ((area.logical.x + resolved.normal.x) * scale).round() as i32;
        let y =
            area.monitor.position.1 + ((area.logical.y + resolved.normal.y) * scale).round() as i32;
        window.set_size(
            area.monitor
                .physical_size(resolved.normal.width, resolved.normal.height),
        )?;
        window.set_position(PhysicalPosition::new(x, y))?;
    }
    if resolved.maximized {
        window.maximize()?;
    }
    window.show()?;
    window.set_focus()?;
    Ok(())
}

pub fn hide_main(app: &AppHandle) {
    persist_main_window(app);
    if let Some(window) = app.get_webview_window(MAIN_LABEL) {
        let _ = window.hide();
    }
}

pub fn persist_main_window(app: &AppHandle) {
    let Some(window) = app.get_webview_window(MAIN_LABEL) else {
        return;
    };
    let maximized = window.is_maximized().unwrap_or(false);
    let settings_arc = crate::app_state::settings(app);
    let mut settings = settings_arc.write();
    if maximized {
        crate::settings::update_main_window_maximized(&mut settings, true);
    } else if let (Ok(position), Ok(size)) = (window.outer_position(), window.inner_size()) {
        let areas = display_areas(app);
        if let Some(area) = area_containing(&areas, position) {
            let scale = area.monitor.scale;
            crate::settings::update_normal_main_window(
                &mut settings,
                area.logical.id.clone(),
                NormalWindowBounds {
                    x: position.x as f64 / scale
                        - area.monitor.position.0 as f64 / scale
                        - area.logical.x,
                    y: position.y as f64 / scale
                        - area.monitor.position.1 as f64 / scale
                        - area.logical.y,
                    width: size.width as f64 / scale,
                    height: size.height as f64 / scale,
                },
            );
            crate::settings::update_main_window_maximized(&mut settings, false);
        }
    }
    let _ = crate::settings::save(app, &settings);
}

/// Window managers can emit a dense stream of resize/move events. Persist
/// only after it settles, while CloseRequested remains immediate so a normal
/// close cannot lose the last geometry.
fn schedule_main_window_persist(app: AppHandle) {
    let state = app.state::<crate::app_state::SaniState>();
    let generation = state
        .main_window_persist_generation
        .fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        + 1;
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(250));
        let state = app.state::<crate::app_state::SaniState>();
        if state
            .main_window_persist_generation
            .load(std::sync::atomic::Ordering::Relaxed)
            == generation
        {
            persist_main_window(&app);
        }
    });
}

impl OverlayKind {
    pub fn label(self) -> &'static str {
        match self {
            OverlayKind::Pill => PILL_LABEL,
            OverlayKind::Panel => PANEL_LABEL,
        }
    }

    fn build(self, app: &AppHandle) -> tauri::Result<()> {
        match self {
            OverlayKind::Pill => build_pill(app),
            OverlayKind::Panel => build_panel(app),
        }
    }
}

/// Why an overlay is being revealed. Only a runtime reveal — hotkey, tray, a
/// message arriving — is the user opening it themselves. A preview must not be
/// mistaken for that, or Cancel would hide an overlay mid-conversation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverlaySource {
    Runtime,
    Preview,
    Restore,
}

pub fn show_pill(app: &AppHandle) -> tauri::Result<()> {
    reveal(app, OverlayKind::Pill, OverlaySource::Runtime)
}

pub fn show_panel(app: &AppHandle) -> tauri::Result<()> {
    reveal(app, OverlayKind::Panel, OverlaySource::Runtime)
}

fn reveal(app: &AppHandle, kind: OverlayKind, source: OverlaySource) -> tauri::Result<()> {
    let layout = effective_layout(app);
    reveal_with(app, kind, source, &layout)
}

/// The single placement path. Every reveal — hotkey, tray, preview, restore,
/// relaunch — resolves the same committed-or-provisional layout, so the real
/// overlay and the editor canvas can never disagree.
fn reveal_with(
    app: &AppHandle,
    kind: OverlayKind,
    source: OverlaySource,
    layout: &OverlayLayout,
) -> tauri::Result<()> {
    if source == OverlaySource::Runtime {
        note_open_outside_preview(app, kind);
    }
    // Defensive recreate: if the window is unexpectedly gone, build it again so
    // Sani can always reveal itself.
    if app.get_webview_window(kind.label()).is_none() {
        log::warn!(
            "[ui-boot] {} window missing on show — recreating",
            kind.label()
        );
        kind.build(app)?;
        apply_materials(app);
    }
    let Some(window) = app.get_webview_window(kind.label()) else {
        return Ok(());
    };
    match placement(app, kind, layout) {
        Some(frame) => {
            window.set_size(frame.size)?;
            window.set_position(frame.position)?;
        }
        // No display snapshot at all: keep the frame the window already has
        // instead of guessing one and placing an overlay somewhere unsafe.
        None => log::warn!(
            "[ui-boot] no display work area for {} — revealing in place",
            kind.label()
        ),
    }
    window.show()
}

struct Placement {
    position: PhysicalPosition<i32>,
    size: PhysicalSize<u32>,
}

/// Resolve `layout` against the real work areas and convert one overlay's
/// logical frame to physical pixels on the display that owns it.
fn placement(app: &AppHandle, kind: OverlayKind, layout: &OverlayLayout) -> Option<Placement> {
    let areas = display_areas(app);
    if areas.is_empty() {
        return None;
    }
    let logical: Vec<_> = areas.iter().map(|area| area.logical.clone()).collect();
    let resolved = resolve_overlay_layout(
        layout,
        &logical,
        main_window_display(app, &areas).as_deref(),
    );
    let area = areas
        .iter()
        .find(|area| area.logical.id == resolved.display.id)?;
    let rect = match kind {
        OverlayKind::Pill => resolved.pill,
        OverlayKind::Panel => resolved.panel,
    };
    // display_rect adds the menu-bar/notch and Dock insets, so an excluded
    // strip can never receive a frame.
    let on_display = display_rect(&resolved.display, rect);
    Some(Placement {
        position: area.monitor.place(on_display.x, on_display.y),
        size: area.monitor.physical_size(rect.width, rect.height),
    })
}

/// The provisional draft while a preview is live, otherwise the committed
/// layout.
fn effective_layout(app: &AppHandle) -> OverlayLayout {
    preview_state(app)
        .provisional()
        .unwrap_or_else(|| committed_layout(app))
}

fn committed_layout(app: &AppHandle) -> OverlayLayout {
    settings::load_overlay_layout(&crate::app_state::settings(app).read())
}

// ------------------------------------------------------------- layout editor

const NO_DISPLAY: &str =
    "Sani cannot see a display right now, so your saved layout was left unchanged.";

/// Process-local preview state, managed by Tauri. Never written to settings: a
/// draft cannot survive a restart.
#[derive(Default)]
pub struct OverlayPreviewState {
    active: Mutex<Option<ActivePreview>>,
}

struct ActivePreview {
    session: PreviewSession,
    provisional: OverlayLayout,
}

impl OverlayPreviewState {
    fn provisional(&self) -> Option<OverlayLayout> {
        self.active
            .lock()
            .as_ref()
            .map(|active| active.provisional.clone())
    }

    fn begin(&self, committed: OverlayLayout, seen: OverlayVisibility, draft: OverlayLayout) {
        let mut guard = self.active.lock();
        match guard.as_mut() {
            // Visibility is captured once, when the preview starts, so a later
            // Preview of a second draft still restores the original state.
            Some(active) => active.provisional = draft,
            None => {
                *guard = Some(ActivePreview {
                    session: PreviewSession::new(committed, seen.pill, seen.panel),
                    provisional: draft,
                })
            }
        }
    }

    fn note_open(&self, kind: OverlayKind) {
        if let Some(active) = self.active.lock().as_mut() {
            active.session.note_open_outside_preview(kind);
        }
    }

    fn end(&self) -> Option<PreviewSession> {
        self.active.lock().take().map(|active| active.session)
    }
}

fn preview_state(app: &AppHandle) -> tauri::State<'_, OverlayPreviewState> {
    app.state::<OverlayPreviewState>()
}

fn note_open_outside_preview(app: &AppHandle, kind: OverlayKind) {
    preview_state(app).note_open(kind);
}

fn is_visible(app: &AppHandle, kind: OverlayKind) -> bool {
    app.get_webview_window(kind.label())
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false)
}

fn overlay_visibility(app: &AppHandle) -> OverlayVisibility {
    OverlayVisibility {
        pill: is_visible(app, OverlayKind::Pill),
        panel: is_visible(app, OverlayKind::Panel),
    }
}

fn begin_preview(app: &AppHandle, draft: OverlayLayout) {
    // Read settings and visibility before taking the preview lock: no path here
    // may hold both at once.
    let committed = committed_layout(app);
    let seen = overlay_visibility(app);
    preview_state(app).begin(committed, seen, draft);
}

/// Undo a live preview: committed placement and the exact captured visibility.
/// Also the main-window close and app quit path.
pub fn cancel_preview(app: &AppHandle) {
    if let Some(session) = preview_state(app).end() {
        let action = session.cancel();
        apply_restore(app, &action);
    }
}

fn apply_restore(app: &AppHandle, action: &RestoreAction) {
    for (kind, visible) in [
        (OverlayKind::Pill, action.pill_visible),
        (OverlayKind::Panel, action.panel_visible),
    ] {
        if visible {
            if let Err(err) = reveal_with(app, kind, OverlaySource::Restore, &action.layout) {
                log::error!("[layout] restoring {} failed: {err}", kind.label());
            }
        } else {
            hide(app, kind);
        }
    }
}

/// One display as the editor draws it: the whole screen plus the usable work
/// area inside it, so exclusions are real insets rather than guessed CSS.
#[derive(Debug, Clone, Serialize)]
pub struct DisplaySnapshot {
    pub id: String,
    pub is_primary: bool,
    pub is_main_window_display: bool,
    pub scale_factor: f64,
    pub screen_width: f64,
    pub screen_height: f64,
    pub work_x: f64,
    pub work_y: f64,
    pub work_width: f64,
    pub work_height: f64,
}

/// The supported ranges the editor constrains its handles to. Native code stays
/// authoritative; these are the same numbers it enforces.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct OverlayLimits {
    pub pill_min_width: f64,
    pub pill_max_width: f64,
    pub pill_height: f64,
    pub panel_min_width: f64,
    pub panel_max_width: f64,
    pub panel_min_height: f64,
    pub panel_max_height: f64,
}

/// Everything the layout editor renders. Frames are the ones native code
/// resolved, never the raw draft.
#[derive(Debug, Clone, Serialize)]
pub struct OverlayEditorState {
    /// `None` when there is no display snapshot: the editor must then disable
    /// Preview and Save and keep showing committed placement.
    pub active_display: Option<String>,
    pub displays: Vec<DisplaySnapshot>,
    pub committed: OverlayLayout,
    /// The provisional layout applied to the real overlays, when previewing.
    pub draft: Option<OverlayLayout>,
    pub pill: LogicalRect,
    pub panel: LogicalRect,
    pub adjustments: Vec<String>,
    pub preview_active: bool,
    pub limits: OverlayLimits,
}

fn editor_state(app: &AppHandle) -> Result<OverlayEditorState, String> {
    let areas = display_areas(app);
    let main_display = main_window_display(app, &areas);
    let committed = committed_layout(app);
    let draft = preview_state(app).provisional();
    let effective = draft.clone().unwrap_or_else(|| committed.clone());
    let logical: Vec<_> = areas.iter().map(|area| area.logical.clone()).collect();
    let resolved = resolve_overlay_layout(&effective, &logical, main_display.as_deref());
    let displays = areas
        .iter()
        .map(|area| {
            let (screen_width, screen_height) = area.monitor.logical_size();
            DisplaySnapshot {
                id: area.logical.id.clone(),
                is_primary: area.logical.is_primary,
                is_main_window_display: main_display.as_deref() == Some(area.logical.id.as_str()),
                scale_factor: area.logical.scale_factor,
                screen_width,
                screen_height,
                work_x: area.logical.x,
                work_y: area.logical.y,
                work_width: area.logical.width,
                work_height: area.logical.height,
            }
        })
        .collect();
    Ok(OverlayEditorState {
        active_display: (!areas.is_empty()).then(|| resolved.display.id.clone()),
        displays,
        committed,
        preview_active: draft.is_some(),
        draft,
        pill: resolved.pill,
        panel: resolved.panel,
        adjustments: resolved.adjustments,
        limits: OverlayLimits {
            pill_min_width: PILL_MIN_WIDTH,
            pill_max_width: PILL_MAX_WIDTH,
            pill_height: PILL_HEIGHT,
            panel_min_width: PANEL_MIN_WIDTH,
            panel_max_width: PANEL_MAX_WIDTH,
            panel_min_height: PANEL_MIN_HEIGHT,
            panel_max_height: PANEL_MAX_HEIGHT,
        },
    })
}

#[tauri::command]
pub fn overlay_editor_state(app: AppHandle) -> Result<OverlayEditorState, String> {
    editor_state(&app)
}

/// Apply a draft to the real overlays without writing settings. Revealing both
/// overlays is what makes the preview real; captured visibility decides what
/// Save and Cancel put back.
#[tauri::command]
pub fn preview_overlay_layout(
    app: AppHandle,
    draft: OverlayLayout,
) -> Result<OverlayEditorState, String> {
    if display_areas(&app).is_empty() {
        return Err(NO_DISPLAY.into());
    }
    begin_preview(&app, draft.clone());
    let previewed = reveal(&app, OverlayKind::Pill, OverlaySource::Preview)
        .and_then(|()| reveal(&app, OverlayKind::Panel, OverlaySource::Preview));
    if let Err(err) = previewed {
        log::error!("[layout] preview failed: {err}");
        cancel_preview(&app);
        return Err(
            "Sani could not preview that layout, so your saved placement was restored.".into(),
        );
    }
    editor_state(&app)
}

/// Persist a validated draft and apply it, then restore captured visibility.
#[tauri::command]
pub fn save_overlay_layout(
    app: AppHandle,
    draft: OverlayLayout,
) -> Result<OverlayEditorState, String> {
    if display_areas(&app).is_empty() {
        return Err(NO_DISPLAY.into());
    }
    let layout = committable_layout(&draft);
    {
        let settings_arc = crate::app_state::settings(&app);
        let mut guard = settings_arc.write();
        settings::update_overlay_layout(&mut guard, layout.clone());
        settings::save(&app, &guard).map_err(|err| {
            log::error!("[layout] settings write failed: {err}");
            "Sani could not write its settings file, so your layout was not saved.".to_string()
        })?;
    }
    let action = match preview_state(&app).end() {
        Some(session) => session.save(layout.clone()),
        // Saved straight from the editor without previewing: nothing was
        // captured, so leave visibility exactly as it is.
        None => RestoreAction::new(
            layout.clone(),
            is_visible(&app, OverlayKind::Pill),
            is_visible(&app, OverlayKind::Panel),
        ),
    };
    apply_restore(&app, &action);
    editor_state(&app)
}

#[tauri::command]
pub fn cancel_overlay_preview(app: AppHandle) -> Result<OverlayEditorState, String> {
    cancel_preview(&app);
    editor_state(&app)
}

/// The native defaults Reset puts into the editor draft. It writes nothing and
/// moves nothing: Preview may show them, Save persists them, Cancel discards them.
#[tauri::command]
pub fn reset_overlay_draft() -> OverlayLayout {
    OverlayLayout::default()
}

/// Reveal both overlays (cold launch and macOS app reopen).
pub fn show_overlays(app: &AppHandle) {
    if let Err(err) = show_panel(app) {
        log::error!("[ui-boot] show_panel failed: {err}");
    }
    if let Err(err) = show_pill(app) {
        log::error!("[ui-boot] show_pill failed: {err}");
    }
}

/// Hide the panel without destroying it (replaces browser `window.close()`).
pub fn hide_panel(app: &AppHandle) {
    hide(app, OverlayKind::Panel);
}

/// Hide the pill without destroying it.
pub fn hide_pill(app: &AppHandle) {
    hide(app, OverlayKind::Pill);
}

pub fn hide_overlays(app: &AppHandle) {
    hide_pill(app);
    hide_panel(app);
}

fn hide(app: &AppHandle, kind: OverlayKind) {
    if let Some(window) = app.get_webview_window(kind.label()) {
        let _ = window.hide();
    }
}

/// macOS glass: HUD vibrancy behind the transparent webviews; silently
/// skipped where unsupported (Windows/Linux keep flat translucency).
pub fn apply_materials(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    {
        for (label, radius) in [
            (PILL_LABEL, PILL_GLASS_RADIUS),
            (PANEL_LABEL, PANEL_GLASS_RADIUS),
        ] {
            if let Some(window) = app.get_webview_window(label) {
                let _ = window_vibrancy::apply_vibrancy(
                    &window,
                    window_vibrancy::NSVisualEffectMaterial::HudWindow,
                    None,
                    Some(radius),
                );
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::overlay_geometry::{default_panel_frame, default_pill_frame, OverlayFrame};
    use crate::window_geometry::NormalWindowBounds;

    fn committed() -> OverlayLayout {
        OverlayLayout {
            display_affinity: "built-in".into(),
            pill: default_pill_frame(),
            panel: default_panel_frame(),
        }
    }

    fn moved() -> OverlayLayout {
        OverlayLayout {
            panel: OverlayFrame {
                x_ratio: 0.1,
                y_ratio: 0.1,
                width: 520.0,
                height: 600.0,
            },
            ..committed()
        }
    }

    #[test]
    fn close_main_is_hide_not_quit_policy() {
        assert_eq!(close_policy_for_label(MAIN_LABEL), ClosePolicy::Hide);
        assert_eq!(close_policy_for_label(PILL_LABEL), ClosePolicy::Allow);
    }

    #[test]
    fn normal_bounds_are_not_captured_while_maximized() {
        let event = WindowSnapshot {
            maximized: true,
            normal: NormalWindowBounds {
                x: 80.0,
                y: 70.0,
                width: 1100.0,
                height: 740.0,
            },
        };

        assert_eq!(normal_bounds_to_persist(&event), None);
    }

    #[test]
    fn cancel_restores_committed_layout_and_prior_visibility() {
        let session = PreviewSession::new(committed(), false, true);

        assert_eq!(
            session.cancel(),
            RestoreAction::new(committed(), false, true)
        );
    }

    #[test]
    fn save_commits_the_new_layout_and_restores_prior_visibility() {
        let session = PreviewSession::new(committed(), true, false);

        assert_eq!(
            session.save(moved()),
            RestoreAction::new(moved(), true, false)
        );
    }

    #[test]
    fn an_overlay_opened_outside_preview_is_never_hidden_again() {
        let mut session = PreviewSession::new(committed(), false, false);
        session.note_open_outside_preview(OverlayKind::Pill);

        assert_eq!(
            session.cancel(),
            RestoreAction::new(committed(), true, false)
        );
        assert_eq!(
            session.save(moved()),
            RestoreAction::new(moved(), true, false)
        );
    }
}

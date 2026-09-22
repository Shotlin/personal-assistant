//! Overlay windows: the bottom-center mic pill and the right-side
//! conversation panel. Frameless, transparent, always-on-top, positioned on
//! the monitor under the cursor at every show (so monitor changes never
//! leave a window off-screen).
//!
//! Lifecycle: windows are *hidden*, never destroyed. `show_*` recreates a
//! window defensively if it is unexpectedly gone, so a hide/close path can
//! never leave Sani unable to reveal itself again.

use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize};
use tauri::{WebviewUrl, WebviewWindowBuilder};

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

const PILL_SIZE: (f64, f64) = (680.0, 96.0);
const PILL_MIN_WIDTH: f64 = 560.0;
const PILL_MAX_WIDTH: f64 = 760.0;
const PANEL_WIDTH: f64 = 524.0;
const PANEL_MIN_WIDTH: f64 = 500.0;
const PANEL_MAX_WIDTH: f64 = 640.0;
const PANEL_HEIGHT_RATIO: f64 = 0.48;
const PANEL_MIN_HEIGHT: f64 = 400.0;
const PANEL_MAX_HEIGHT: f64 = 680.0;
const PILL_BOTTOM_MARGIN: f64 = 92.0; // above dock/taskbar
const SCREEN_MARGIN: f64 = 20.0;
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
    let window = WebviewWindowBuilder::new(app, PILL_LABEL, WebviewUrl::App("index.html".into()))
        .title("Sani")
        .inner_size(PILL_SIZE.0, PILL_SIZE.1)
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
    let window = WebviewWindowBuilder::new(app, PANEL_LABEL, WebviewUrl::App("panel.html".into()))
        .title("Sani — Conversation")
        .inner_size(PANEL_WIDTH, 720.0)
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
            persist_main_window(&app_for_events);
            let _ = window_for_events.hide();
        }
        tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) | tauri::WindowEvent::ScaleFactorChanged { .. } => {
            persist_main_window(&app_for_events);
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
    /// Monitor size in logical (CSS) points — the unit every layout constant here uses.
    /// `available_monitors()` reports physical pixels, so this division is required
    /// before any arithmetic with the constants above.
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

fn monitor_under_cursor(app: &AppHandle) -> MonitorBox {
    let default = MonitorBox {
        position: (0, 0),
        size: (1440, 900),
        scale: 1.0,
    };
    let cursor = app.cursor_position().ok();
    let monitors = app.available_monitors().unwrap_or_default();
    let chosen: Option<tauri::Monitor> = monitors
        .iter()
        .find(|m| match &cursor {
            Some(pos) => {
                let mx = m.position().x;
                let my = m.position().y;
                let mw = m.size().width as i32;
                let mh = m.size().height as i32;
                let px = pos.x as i32;
                let py = pos.y as i32;
                px >= mx && px < mx + mw && py >= my && py < my + mh
            }
            None => false,
        })
        .cloned()
        .or_else(|| app.primary_monitor().ok().flatten())
        .or_else(|| monitors.first().cloned());
    match chosen {
        Some(m) => MonitorBox {
            position: (m.position().x, m.position().y),
            size: (m.size().width, m.size().height),
            scale: m.scale_factor(),
        },
        None => default,
    }
}

#[derive(Clone)]
struct MainWorkArea {
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

fn main_work_areas(app: &AppHandle) -> Vec<MainWorkArea> {
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
            MainWorkArea {
                logical: LogicalWorkArea {
                    id,
                    is_primary: primary_position == Some((monitor.position().x, monitor.position().y)),
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
    let work_areas = main_work_areas(app);
    let logical_areas: Vec<_> = work_areas.iter().map(|area| area.logical.clone()).collect();
    let resolved = resolve_restore(saved_main_window(app).as_ref(), &logical_areas);
    let selected = work_areas
        .iter()
        .find(|area| area.logical.id == resolved.work_area.id)
        .or_else(|| work_areas.iter().find(|area| area.logical.is_primary))
        .or_else(|| work_areas.first());
    if let Some(area) = selected {
        let scale = area.monitor.scale;
        let x = area.monitor.position.0 + ((area.logical.x + resolved.normal.x) * scale).round() as i32;
        let y = area.monitor.position.1 + ((area.logical.y + resolved.normal.y) * scale).round() as i32;
        window.set_size(area.monitor.physical_size(resolved.normal.width, resolved.normal.height))?;
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
    let Some(window) = app.get_webview_window(MAIN_LABEL) else { return };
    let maximized = window.is_maximized().unwrap_or(false);
    let settings_arc = crate::app_state::settings(app);
    let mut settings = settings_arc.write();
    if maximized {
        crate::settings::update_main_window_maximized(&mut settings, true);
    } else if let (Ok(position), Ok(size)) = (window.outer_position(), window.inner_size()) {
        let areas = main_work_areas(app);
        if let Some(area) = areas.iter().find(|area| {
            let left = area.monitor.position.0;
            let top = area.monitor.position.1;
            let right = left + area.monitor.size.0 as i32;
            let bottom = top + area.monitor.size.1 as i32;
            position.x >= left && position.x < right && position.y >= top && position.y < bottom
        }) {
            let scale = area.monitor.scale;
            crate::settings::update_normal_main_window(
                &mut settings,
                area.logical.id.clone(),
                NormalWindowBounds {
                    x: position.x as f64 / scale - area.monitor.position.0 as f64 / scale - area.logical.x,
                    y: position.y as f64 / scale - area.monitor.position.1 as f64 / scale - area.logical.y,
                    width: size.width as f64 / scale,
                    height: size.height as f64 / scale,
                },
            );
            crate::settings::update_main_window_maximized(&mut settings, false);
        }
    }
    let _ = crate::settings::save(app, &settings);
}

pub fn show_pill(app: &AppHandle) -> tauri::Result<()> {
    // Defensive recreate: if the pill is unexpectedly gone, build it again so
    // Sani can always reveal itself.
    if app.get_webview_window(PILL_LABEL).is_none() {
        log::warn!("[ui-boot] pill window missing on show — recreating");
        build_pill(app)?;
        apply_materials(app);
    }
    let Some(pill) = app.get_webview_window(PILL_LABEL) else {
        return Ok(());
    };
    let mon = monitor_under_cursor(app);
    let (mlw, mlh) = mon.logical_size();
    let pill_height = PILL_SIZE.1;
    // Width tracks the display; height stays fixed so it matches
    // PILL_GLASS_RADIUS, which clips the vibrancy layer.
    let pill_width = (mlw * 0.47).clamp(PILL_MIN_WIDTH, PILL_MAX_WIDTH);
    pill.set_size(mon.physical_size(pill_width, pill_height))?;
    pill.set_position(mon.place(
        (mlw - pill_width) / 2.0,
        mlh - PILL_BOTTOM_MARGIN - pill_height,
    ))?;
    pill.show()?;
    Ok(())
}

pub fn show_panel(app: &AppHandle) -> tauri::Result<()> {
    // Defensive recreate: a prior close/destroy must never strand the panel.
    if app.get_webview_window(PANEL_LABEL).is_none() {
        log::warn!("[ui-boot] panel window missing on show — recreating");
        build_panel(app)?;
        apply_materials(app);
    }
    let Some(panel) = app.get_webview_window(PANEL_LABEL) else {
        return Ok(());
    };
    let mon = monitor_under_cursor(app);
    let (mlw, mlh) = mon.logical_size();
    // Responsive: the panel tracks the display instead of staying a fixed
    // 524pt sliver on a large monitor.
    let panel_width = (mlw * 0.36).clamp(PANEL_MIN_WIDTH, PANEL_MAX_WIDTH);
    let panel_height = (mlh * PANEL_HEIGHT_RATIO).clamp(PANEL_MIN_HEIGHT, PANEL_MAX_HEIGHT);
    panel.set_size(mon.physical_size(panel_width, panel_height))?;
    panel.set_position(mon.place(mlw - panel_width - SCREEN_MARGIN, SCREEN_MARGIN))?;
    panel.show()?;
    Ok(())
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
    if let Some(panel) = app.get_webview_window(PANEL_LABEL) {
        let _ = panel.hide();
    }
}

/// Hide the pill without destroying it.
pub fn hide_pill(app: &AppHandle) {
    if let Some(pill) = app.get_webview_window(PILL_LABEL) {
        let _ = pill.hide();
    }
}

pub fn hide_overlays(app: &AppHandle) {
    hide_pill(app);
    hide_panel(app);
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
    use crate::window_geometry::NormalWindowBounds;

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
}

//! Overlay windows: the bottom-center mic pill and the right-side
//! conversation panel. Frameless, transparent, always-on-top, positioned on
//! the monitor under the cursor at every show (so monitor changes never
//! leave a window off-screen).

use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize};
use tauri::{WebviewUrl, WebviewWindowBuilder};

use crate::app_state::settings;

pub const PILL_LABEL: &str = "pill";
pub const PANEL_LABEL: &str = "panel";

const PILL_SIZE: (f64, f64) = (640.0, 104.0);
const PANEL_WIDTH: f64 = 524.0;
const PILL_BOTTOM_MARGIN: f64 = 92.0; // above dock/taskbar
const SCREEN_MARGIN: f64 = 20.0;

pub fn create_all(app: &AppHandle) -> tauri::Result<()> {
    if app.get_webview_window(PILL_LABEL).is_none() {
        WebviewWindowBuilder::new(app, PILL_LABEL, WebviewUrl::App("index.html".into()))
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
            .build()?;
    }
    if app.get_webview_window(PANEL_LABEL).is_none() {
        WebviewWindowBuilder::new(app, PANEL_LABEL, WebviewUrl::App("panel.html".into()))
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
            .build()?;
    }
    Ok(())
}

struct MonitorBox {
    position: (i32, i32),
    size: (u32, u32),
    scale: f64,
}

fn monitor_under_cursor(app: &AppHandle) -> MonitorBox {
    let default = MonitorBox { position: (0, 0), size: (1440, 900), scale: 1.0 };
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

fn logical(size: f64, scale: f64) -> f64 {
    (size / scale).round()
}

pub fn show_pill(app: &AppHandle) -> tauri::Result<()> {
    let Some(pill) = app.get_webview_window(PILL_LABEL) else { return Ok(()) };
    let mon = monitor_under_cursor(app);
    let (mw, mh) = (mon.size.0 as f64, mon.size.1 as f64);
    let (pw, ph) = PILL_SIZE;
    let x = mon.position.0 + logical((mw - pw) / 2.0, mon.scale) as i32;
    let y = mon.position.1
        + logical(mh - PILL_BOTTOM_MARGIN - ph, mon.scale) as i32;
    pill.set_position(PhysicalPosition::new(x, y))?;
    pill.show()?;
    Ok(())
}

pub fn show_panel(app: &AppHandle) -> tauri::Result<()> {
    let Some(panel) = app.get_webview_window(PANEL_LABEL) else { return Ok(()) };
    let mon = monitor_under_cursor(app);
    let (mw, mh) = (mon.size.0 as f64, mon.size.1 as f64);
    let panel_height = (mh * 0.72).min(860.0).max(480.0);
    let width_l = logical(PANEL_WIDTH, mon.scale) as i32;
    let height_l = logical(panel_height, mon.scale) as i32;
    let x = mon.position.0 + logical(mw - PANEL_WIDTH - SCREEN_MARGIN, mon.scale) as i32;
    let y = mon.position.1 + logical(SCREEN_MARGIN, mon.scale) as i32;
    panel.set_size(PhysicalSize::new(width_l.max(1), height_l.max(1)))?;
    panel.set_position(PhysicalPosition::new(x, y))?;
    panel.show()?;
    Ok(())
}

pub fn hide_overlays(app: &AppHandle) {
    if let Some(pill) = app.get_webview_window(PILL_LABEL) {
        let _ = pill.hide();
    }
    if let Some(panel) = app.get_webview_window(PANEL_LABEL) {
        let _ = panel.hide();
    }
}

/// macOS glass: HUD vibrancy behind the transparent webviews; silently
/// skipped where unsupported (Windows/Linux keep flat translucency).
pub fn apply_materials(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    {
        for label in [PILL_LABEL, PANEL_LABEL] {
            if let Some(window) = app.get_webview_window(label) {
                let _ = window_vibrancy::apply_vibrancy(
                    &window,
                    window_vibrancy::NSVisualEffectMaterial::HudWindow,
                    None,
                    None,
                );
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
    }
}


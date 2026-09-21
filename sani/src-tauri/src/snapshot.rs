//! Optional PNG capture of Sani's own webviews, for UI verification.
//!
//! `SANI_SNAPSHOT_DIR=/tmp/shots` makes Sani write `pill-<tag>.png` /
//! `panel-<tag>.png` there. The app renders its own WKWebView, so this works
//! without macOS Screen Recording permission — which is what makes "look at the
//! real UI before claiming it works" possible in a headless session.

use std::path::PathBuf;
use tauri::{AppHandle, Manager};

use crate::windows::{PANEL_LABEL, PILL_LABEL};

pub fn dir() -> Option<PathBuf> {
    std::env::var("SANI_SNAPSHOT_DIR")
        .ok()
        .map(|d| if d.is_empty() { PathBuf::from("/tmp") } else { PathBuf::from(d) })
}

/// Render both overlays' current web content, tagged (e.g. "listening").
pub fn snapshot_overlays(app: &AppHandle, tag: &str) {
    let Some(dir) = dir() else { return };
    let _ = std::fs::create_dir_all(&dir);
    for label in [PILL_LABEL, PANEL_LABEL] {
        let Some(window) = app.get_webview_window(label) else { continue };
        let path = dir.join(format!("{label}-{tag}.png"));
        if !window.is_visible().unwrap_or(false) {
            log::debug!("[snapshot] {label} not visible; skipped");
            continue;
        }
        let target = path.to_string_lossy().to_string();
        let _ = window.with_webview(move |webview| {
            capture(&webview, &target);
        });
        log::info!("[snapshot] {label} -> {}", path.display());
    }
}

#[cfg(target_os = "macos")]
fn capture(webview: &tauri::webview::PlatformWebview, target: &str) {
    extern "C" {
        fn sani_webview_snapshot(webview: *mut std::ffi::c_void, path: *const i8);
    }
    let c_path = match std::ffi::CString::new(target) {
        Ok(c) => c,
        Err(_) => return,
    };
    unsafe { sani_webview_snapshot(webview.inner(), c_path.as_ptr()) };
}

#[cfg(not(target_os = "macos"))]
fn capture(_webview: &tauri::webview::PlatformWebview, _target: &str) {}

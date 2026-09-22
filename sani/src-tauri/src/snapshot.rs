//! Optional PNG capture of Sani's own webviews, for UI verification.
//!
//! `SANI_SNAPSHOT_DIR=/tmp/shots` makes Sani write `pill-<tag>.png` /
//! `panel-<tag>.png` there. The app renders its own WKWebView, so this works
//! without macOS Screen Recording permission — which is what makes "look at the
//! real UI before claiming it works" possible in a headless session.

use std::path::PathBuf;
use tauri::{AppHandle, Manager};

use crate::windows::{ONBOARDING_LABEL, PANEL_LABEL, PILL_LABEL};

pub fn dir() -> Option<PathBuf> {
    std::env::var("SANI_SNAPSHOT_DIR").ok().map(|d| {
        if d.is_empty() {
            PathBuf::from("/tmp")
        } else {
            PathBuf::from(d)
        }
    })
}

/// Render both overlays' current web content, tagged (e.g. "listening").
pub fn snapshot_overlays(app: &AppHandle, tag: &str) {
    for label in [PILL_LABEL, PANEL_LABEL] {
        snapshot_window(app, label, tag);
    }
}

/// Capture one window's web content to `<dir>/<label>-<tag>.png`. The app
/// renders its own WKWebView, so this needs no Screen Recording permission —
/// which is what lets us actually look at the onboarding window.
pub fn snapshot_window(app: &AppHandle, label: &str, tag: &str) {
    let Some(dir) = dir() else { return };
    let _ = std::fs::create_dir_all(&dir);
    let Some(window) = app.get_webview_window(label) else {
        return;
    };
    let path = dir.join(format!("{label}-{tag}.png"));
    if !window.is_visible().unwrap_or(false) {
        log::debug!("[snapshot] {label} not visible; skipped");
        return;
    }
    let target = path.to_string_lossy().to_string();
    let _ = window.with_webview(move |webview| {
        capture(&webview, &target);
    });
    log::info!("[snapshot] {label} -> {}", path.display());
}

/// Snapshot the onboarding window after it has had time to paint (verification).
/// A cold `tauri dev` start can take several seconds before React mounts, so
/// capture a few frames rather than betting on one timing.
pub fn spawn_onboarding_snapshot(app: &AppHandle) {
    if dir().is_none() {
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        for (ms, tag) in [(2500u64, "welcome"), (5000, "welcome2"), (8000, "welcome3")] {
            std::thread::sleep(std::time::Duration::from_millis(ms));
            snapshot_window(&handle, ONBOARDING_LABEL, tag);
        }
    });
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

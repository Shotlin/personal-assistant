//! Global shortcuts: the user hotkey toggles listening; a temporary global
//! Escape is registered only while overlays are actively used so it never
//! steals the key from other apps while Sani idles.

use parking_lot::Mutex;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::Shortcut;

use crate::app_state::{self, UiState};

#[derive(Default)]
pub struct HotkeyState {
    pub user_shortcut: Mutex<Option<Shortcut>>,
    pub esc_registered: Mutex<bool>,
}

pub fn parse(shortcut: &str) -> Result<Shortcut, String> {
    shortcut
        .parse::<Shortcut>()
        .map_err(|e| format!("Invalid shortcut {shortcut:?}: {e}"))
}

pub fn register_user_shortcut(app: &AppHandle, shortcut_str: &str) -> Result<(), String> {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;

    let shortcut = parse(shortcut_str)?;
    let gs = app.global_shortcut();

    let state = app.state::<HotkeyState>();
    if let Some(previous) = state.user_shortcut.lock().take() {
        let _ = gs.unregister(previous);
    }

    gs.on_shortcut(shortcut, move |app, _shortcut, event| {
        if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
            app_state::toggle_listening(app);
        }
    })
    .map_err(|e| format!("Cannot register shortcut: {e}"))?;

    *state.user_shortcut.lock() = Some(shortcut);
    Ok(())
}

/// Esc while Sani is active: cancel listening/run, else hide overlays.
pub fn set_esc_active(app: &AppHandle, active: bool) {
    use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

    let state = app.state::<HotkeyState>();
    let mut registered = state.esc_registered.lock();
    if active == *registered {
        return;
    }
    let gs = app.global_shortcut();
    let esc: Shortcut = "Escape".parse().expect("escape shortcut");
    if active {
        let _handle = app.clone();
        if gs.on_shortcut(esc, move |app, _s, event| {
            if event.state == ShortcutState::Pressed {
                app_state::handle_escape(app);
            }
        })
        .is_ok()
        {
            *registered = true;
        }
    } else {
        let _ = gs.unregister(esc);
        *registered = false;
    }
}

pub fn rebind_on_state_change(app: &AppHandle) {
    let active = matches!(
        app_state::current_state(app),
        UiState::Preparing | UiState::Listening | UiState::Finalizing | UiState::Working
    );
    set_esc_active(app, active);
    let _ = app.emit("sani://state", app_state::current_state(app).as_str());
}

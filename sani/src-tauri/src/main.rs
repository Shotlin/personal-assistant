//! Sani — local desktop voice shell for the Personal Assistant Deep Agent.
//!
//! Global hotkey -> mic pill overlay -> live Moonshine streaming STT ->
//! final transcript (sent exactly once) -> existing gateway (SSE) ->
//! right-side conversation + activity panel. No TTS, no wake word, no
//! login, no second agent implementation.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod activity;
mod agent;
mod app_state;
mod audio;
mod history;
mod hotkey;
mod settings;
mod speech;
mod windows;

use parking_lot::RwLock;
use serde::Serialize;
use tauri::{Manager, Emitter};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

use app_state::SaniState;

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(hotkey::HotkeyState::default())
        .setup(|app| {
            let handle = app.handle().clone();

            // No dock icon: Sani is an overlay, reachable via hotkey/tray.
            #[cfg(target_os = "macos")]
            let _ = handle.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let app_settings = settings::load(&handle);
            let launch_at_login = app_settings.launch_at_login;
            let autostart = handle.autolaunch();
            if launch_at_login {
                let _ = autostart.enable();
            } else {
                let _ = autostart.disable();
            }

            let db_path = handle
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| std::path::PathBuf::from("."))
                .join("sani-history.db");
            let history = history::History::open(db_path)?;

            let hotkey_str = app_settings.hotkey.clone();
            handle.manage(SaniState::new(
                std::sync::Arc::new(RwLock::new(app_settings)),
                std::sync::Arc::new(history),
            ));

            windows::create_all(&handle)?;
            windows::apply_materials(&handle);
            setup_tray(&handle)?;
            hotkey::register_user_shortcut(&handle, &hotkey_str)
                .map_err(|e| format!("{e}"))?;

            app_state::spawn_level_ticker(handle.clone());
            app_state::spawn_health_probe(handle.clone());

            // Dev/verification affordance: SANI_AUTOSTART=1 begins listening
            // right after launch (equivalent to pressing the hotkey).
            if std::env::var("SANI_AUTOSTART").as_deref() == Ok("1") {
                let h = handle.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(1200));
                    app_state::start_listening(&h);
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_state,
            get_settings,
            save_settings_cmd,
            list_mics,
            start_listening_cmd,
            stop_listening_cmd,
            escape_cmd,
            list_conversations,
            get_messages,
            new_conversation,
            select_conversation,
            delete_conversation,
            panel_ready,
            agent_health,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Sani");
}

fn setup_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::TrayIconBuilder;

    let listen = MenuItem::with_id(app, "listen", "Start Listening", true, None::<&str>)?;
    let panel = MenuItem::with_id(app, "panel", "Show Conversation", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Sani", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&listen, &panel, &quit])?;

    let tray = TrayIconBuilder::with_id("sani-tray")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("Sani")
        .build(app)?;
    tray.on_menu_event(|app, event| {
        match event.id().as_ref() {
            "listen" => app_state::toggle_listening(app),
            "panel" => {
                let _ = windows::show_panel(app);
            }
            "quit" => app.exit(0),
            _ => {}
        }
    });
    Ok(())
}

// ------------------------------------------------------------------ commands

#[derive(Serialize)]
struct AppStateOut {
    state: String,
    stt_ready: bool,
    stt_model: String,
    agent_online: Option<bool>,
    partial: String,
}

#[tauri::command]
fn get_state(app: tauri::AppHandle) -> AppStateOut {
    let (stt_ready, stt_model);
    let partial;
    {
        let state = app.state::<SaniState>();
        let stt = state.speech.lock();
        stt_ready = stt.as_ref().map(|s| s.is_ready()).unwrap_or(false);
        stt_model = stt.as_ref().map(|s| s.model.read().clone()).unwrap_or_default();
        let partial_guard = state.partial.lock();
        partial = partial_guard.clone();
    }
    AppStateOut {
        state: app_state::current_state(&app).as_str().to_string(),
        stt_ready,
        stt_model,
        agent_online: None,
        partial,
    }
}

#[derive(Serialize)]
struct SettingsOut {
    hotkey: String,
    mic_device: String,
    agent_base_url: String,
    has_agent_key: bool,
    launch_at_login: bool,
    theme: String,
    stt_model: String,
    stt_ready: bool,
}

#[tauri::command]
fn get_settings(app: tauri::AppHandle) -> SettingsOut {
    let s = app_state::settings(&app).read().clone();
    let stt_ready = app
        .state::<SaniState>()
        .speech
        .lock()
        .as_ref()
        .map(|h| h.is_ready())
        .unwrap_or(false);
    SettingsOut {
        hotkey: s.hotkey,
        mic_device: s.mic_device,
        agent_base_url: s.agent_base_url,
        has_agent_key: !s.agent_api_key.trim().is_empty(),
        launch_at_login: s.launch_at_login,
        theme: s.theme,
        stt_model: s.stt_model,
        stt_ready,
    }
}

#[tauri::command]
fn save_settings_cmd(
    app: tauri::AppHandle,
    hotkey: Option<String>,
    mic_device: Option<String>,
    agent_base_url: Option<String>,
    launch_at_login: Option<bool>,
    theme: Option<String>,
) -> Result<(), String> {
    let changed_mic;
    {
        let settings_arc = app_state::settings(&app);
        let mut s = settings_arc.write();
        if let Some(hotkey) = &hotkey {
            if *hotkey != s.hotkey {
                hotkey::register_user_shortcut(&app, hotkey)?;
                s.hotkey = hotkey.clone();
            }
        }
        if let Some(mic) = &mic_device {
            changed_mic = mic != &s.mic_device;
            s.mic_device = mic.clone();
        } else {
            changed_mic = false;
        }
        if let Some(url) = &agent_base_url {
            s.agent_base_url = url.trim_end_matches('/').to_string();
        }
        if let Some(login) = launch_at_login {
            s.launch_at_login = login;
            use tauri_plugin_autostart::ManagerExt;
            let autostart = app.autolaunch();
            let _ = if login { autostart.enable() } else { autostart.disable() };
        }
        if let Some(theme) = &theme {
            s.theme = theme.clone();
        }
        settings::save(&app, &s)?;
    }
    if changed_mic {
        // Drop the capture so the next start_listening reopens the new device.
        *app.state::<SaniState>().audio.lock() = None;
    }
    Ok(())
}

#[tauri::command]
fn list_mics() -> Vec<String> {
    audio::list_input_devices()
}

#[tauri::command]
fn start_listening_cmd(app: tauri::AppHandle) {
    app_state::start_listening(&app);
}

#[tauri::command]
fn stop_listening_cmd(app: tauri::AppHandle) {
    app_state::stop_listening(&app);
}

#[tauri::command]
fn escape_cmd(app: tauri::AppHandle) {
    app_state::handle_escape(&app);
}

#[tauri::command]
fn list_conversations(app: tauri::AppHandle) -> Result<Vec<history::Conversation>, String> {
    app_state::history(&app).list_conversations()
}

#[tauri::command]
fn get_messages(
    app: tauri::AppHandle,
    conversation_id: String,
) -> Result<Vec<history::StoredMessage>, String> {
    app_state::history(&app).messages(&conversation_id)
}

#[tauri::command]
fn new_conversation(app: tauri::AppHandle) -> Result<String, String> {
    let state = app.state::<SaniState>();
    let id = uuid::Uuid::new_v4().to_string();
    state
        .history
        .create_conversation(&id, "New conversation", app_state::now_ms())?;
    {
        let settings_arc = app_state::settings(&app);
        let mut s = settings_arc.write();
        s.active_conversation_id = id.clone();
        let _ = settings::save(&app, &s);
    }
    let _ = app.emit("sani://conversation-changed", &id);
    Ok(id)
}

#[tauri::command]
fn select_conversation(app: tauri::AppHandle, conversation_id: String) -> Result<(), String> {
    let state = app.state::<SaniState>();
    state
        .history
        .get_conversation(&conversation_id)?
        .ok_or("Conversation not found")?;
    {
        let settings_arc = app_state::settings(&app);
        let mut s = settings_arc.write();
        s.active_conversation_id = conversation_id.clone();
        let _ = settings::save(&app, &s);
    }
    let _ = app.emit("sani://conversation-changed", &conversation_id);
    Ok(())
}

#[tauri::command]
fn delete_conversation(app: tauri::AppHandle, conversation_id: String) -> Result<(), String> {
    app_state::history(&app).delete_conversation(&conversation_id)?;
    let is_active = app_state::settings(&app).read().active_conversation_id == conversation_id;
    if is_active {
        let _ = new_conversation(app.clone());
    }
    Ok(())
}

/// Panel window is ready: push the full conversation snapshot.
#[tauri::command]
fn panel_ready(app: tauri::AppHandle) {
    let conversation_id = app_state::settings(&app).read().active_conversation_id.clone();
    if conversation_id.is_empty() {
        return;
    }
    if let Ok(messages) = app_state::history(&app).messages(&conversation_id) {
        let _ = tauri::Emitter::emit(&app, "sani://history-loaded", messages);
    }
}

#[tauri::command]
async fn agent_health(app: tauri::AppHandle) -> bool {
    agent::health(&app).await
}

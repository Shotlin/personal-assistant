//! Sani — local desktop assistant: voice and text in, reasoning and computer
//! control out.
//!
//! Global hotkey -> mic pill overlay -> live Moonshine streaming STT ->
//! final transcript (sent exactly once) -> sani-core sidecar over private
//! framed-JSON IPC -> right-side conversation + activity panel. The typed
//! composer takes the identical path. No TTS, no wake word, no login, and no
//! second agent implementation.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app_state;
mod audio;
mod history;
mod hotkey;
mod macos_work_area;
mod onboarding;
mod overlay_geometry;
mod permissions;
mod runtime;
mod sani_core;
mod settings;
mod setup;
mod snapshot;
mod speech;
mod system_permissions;
mod window_geometry;
mod windows;

use parking_lot::RwLock;
use serde::Serialize;
use std::io::Write;
use tauri::{Emitter, Listener, Manager};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

use app_state::SaniState;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(hotkey::HotkeyState::default())
        .manage(sani_core::SaniCoreState::default())
        .manage(onboarding::SettingsApplicationState::default())
        .manage(windows::OverlayPreviewState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            init_logging(log_dir(&handle));

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

            handle.manage(SaniState::new(
                std::sync::Arc::new(RwLock::new(app_settings)),
                std::sync::Arc::new(history),
            ));

            // Load the persisted setup state before deciding what to show.
            let setup_shared = setup::init(&handle);
            let onboarding_needed = !setup_shared.state.lock().onboarding_complete;
            system_permissions::init_screen_recording_baseline();

            // Frontend startup evidence (RC-03): React emits these once each
            // window's tree mounts. Recorded so the reveal/watchdog below can
            // prove the UI actually came up rather than showing empty glass.
            {
                let h = handle.clone();
                app.listen("sani://pill-ui-ready", move |_| {
                    app_state::mark_ui_ready(&h, "pill")
                });
            }
            {
                let h = handle.clone();
                app.listen("sani://panel-ui-ready", move |_| {
                    app_state::mark_ui_ready(&h, "panel")
                });
            }
            {
                let h = handle.clone();
                app.listen("sani://main-ui-ready", move |_| {
                    app_state::mark_ui_ready(&h, "main")
                });
            }
            // Any JS-side failure is mirrored into the Rust log: a UI problem
            // must be visible in the terminal, not only behind transparent glass.
            app.listen("sani://ui-error", |payload| {
                #[derive(serde::Deserialize)]
                struct UiError {
                    label: String,
                    message: String,
                }
                match serde_json::from_str::<UiError>(payload.payload()) {
                    Ok(err) => {
                        log::error!("[ui-boot] {} webview error: {}", err.label, err.message)
                    }
                    Err(_) => log::error!("[ui-boot] webview error: {}", payload.payload()),
                }
            });

            app_state::spawn_level_ticker(handle.clone());

            if onboarding_needed {
                // First run: a dedicated, focused setup window stands in for the
                // normal accessory overlay until onboarding completes.
                #[cfg(target_os = "macos")]
                let _ = handle.set_activation_policy(tauri::ActivationPolicy::Regular);
                windows::show_onboarding(&handle)?;
                snapshot::spawn_onboarding_snapshot(&handle);
                log::info!("[setup] first run — opened onboarding window");
            } else {
                enter_normal_mode(&handle)?;
            }

            // Dev/verification affordance: SANI_AUTOSTART=1 begins listening
            // right after launch (equivalent to pressing the hotkey).
            if !onboarding_needed && std::env::var("SANI_AUTOSTART").as_deref() == Ok("1") {
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
            voice_models,
            install_voice_model,
            use_voice_model,
            save_settings_cmd,
            set_agent_mode,
            list_mics,
            start_listening_cmd,
            stop_listening_cmd,
            escape_cmd,
            hide_panel,
            toggle_panel,
            show_main_settings,
            list_conversations,
            get_messages,
            new_conversation,
            select_conversation,
            delete_conversation,
            panel_ready,
            main_ready,
            mic_permission_state,
            request_mic_permission,
            open_mic_settings,
            windows::overlay_editor_state,
            windows::preview_overlay_layout,
            windows::save_overlay_layout,
            windows::cancel_overlay_preview,
            windows::reset_overlay_draft,
            sani_core::core_start,
            sani_core::core_stop,
            sani_core::core_agents,
            sani_core::core_status,
            sani_core::core_run,
            sani_core::core_cancel,
            sani_core::core_ping,
            onboarding::setup_state,
            onboarding::run_setup,
            onboarding::retry_setup_component,
            onboarding::set_onboarding_stage,
            onboarding::complete_onboarding,
            onboarding::reset_onboarding,
            onboarding::get_ai_config,
            onboarding::get_full_settings,
            onboarding::apply_ai_settings,
            onboarding::save_ai_config,
            onboarding::store_provider_key,
            onboarding::validate_provider_key,
            onboarding::validate_stored_provider_key,
            onboarding::list_openrouter_models,
            onboarding::permission_snapshot,
            onboarding::open_permission_settings,
            onboarding::request_accessibility,
            onboarding::request_screen_recording,
            onboarding::restart_app,
            onboarding::final_health,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Sani")
        .run(|app_handle, event| {
            // Quitting ends any layout preview: a provisional draft is
            // process-local, so it must never outlive the process or leave the
            // overlays sitting somewhere that was never saved.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                windows::cancel_preview(app_handle);
            }
            // RC-01: opening Sani again while it already runs (Finder/Dock)
            // must reveal the hidden overlays (or the setup window, while
            // onboarding is incomplete) instead of doing nothing.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                if app_state::onboarding_incomplete(app_handle) {
                    log::info!("[ui-boot] macOS reopen — re-showing onboarding window");
                    let _ = windows::show_onboarding(app_handle);
                } else {
                    log::info!("[ui-boot] macOS reopen — revealing main window");
                    if let Err(error) = windows::show_main(app_handle) {
                        log::error!("[ui-boot] main-window reopen failed: {error}");
                    }
                }
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (app_handle, event);
        });
}

/// Bring up the normal Sani experience: a regular main window plus optional
/// overlays, tray, and global hotkey. Runs at normal launch and onboarding completion.
pub fn enter_normal_mode(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    windows::create_main(app)?;
    windows::create_all(app)?;
    windows::apply_materials(app);
    setup_tray(app)?;
    let hotkey_str = app_state::settings(app).read().hotkey.clone();
    hotkey::register_user_shortcut(app, &hotkey_str).map_err(|e| format!("{e}"))?;
    // The assistant runtime is Sani's own child process, started here rather
    // than on demand: the first turn must not pay for spawning it.
    sani_core::start_at_startup(app);
    // Hidden webviews do not consistently begin loading on macOS. Resolve the
    // safe frame and show the opaque boot fallback immediately; React then
    // replaces it and records the main-ui-ready handshake.
    windows::show_main(app)?;
    Ok(())
}

/// Where Sani writes its logs. Shared with the STT sidecar so the Python
/// traceback lands next to the app log instead of vanishing.
pub(crate) fn log_dir(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_log_dir().ok()
}

/// Logs go to stderr *and* to `~/Library/Logs/app.sani.local/sani.log`, because
/// a Finder-launched app has no console: without this the startup evidence for
/// "opened but showed nothing" is unrecoverable.
fn init_logging(log_dir: Option<std::path::PathBuf>) {
    let file = log_dir.and_then(|dir| {
        let _ = std::fs::create_dir_all(&dir);
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("sani.log"))
            .ok()
    });
    let writer = TeeWriter {
        stderr: std::io::stderr(),
        file,
    };
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .target(env_logger::Target::Pipe(Box::new(writer)))
        .init();
}

struct TeeWriter {
    stderr: std::io::Stderr,
    file: Option<std::fs::File>,
}

impl Write for TeeWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let written = self.stderr.write(buf)?;
        if let Some(file) = &mut self.file {
            let _ = file.write_all(buf);
            let _ = file.flush();
        }
        Ok(written)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        let _ = self.stderr.flush();
        if let Some(file) = &mut self.file {
            let _ = file.flush();
        }
        Ok(())
    }
}

fn setup_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::TrayIconBuilder;

    let open = MenuItem::with_id(app, "open", "Open Sani", true, None::<&str>)?;
    let listen = MenuItem::with_id(app, "listen", "Start Listening", true, None::<&str>)?;
    let panel = MenuItem::with_id(app, "panel", "Show Conversation", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Sani", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &listen, &panel, &quit])?;

    let tray = TrayIconBuilder::with_id("sani-tray")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("Sani")
        .build(app)?;
    tray.on_menu_event(|app, event| match event.id().as_ref() {
        "open" => {
            if let Err(error) = windows::show_main(app) {
                log::error!("[tray] failed to show main window: {error}");
            }
        }
        "listen" => app_state::toggle_listening(app),
        "panel" => {
            let _ = windows::show_panel(app);
        }
        "quit" => app.exit(0),
        _ => {}
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
    mic_permission: String,
}

#[tauri::command]
fn get_state(app: tauri::AppHandle) -> AppStateOut {
    let (stt_ready, stt_model);
    let partial;
    {
        let state = app.state::<SaniState>();
        let stt = state.speech.lock();
        stt_ready = stt.as_ref().map(|s| s.is_ready()).unwrap_or(false);
        stt_model = stt
            .as_ref()
            .map(|s| s.model.read().clone())
            .unwrap_or_default();
        let partial_guard = state.partial.lock();
        partial = partial_guard.clone();
    }
    AppStateOut {
        state: app_state::current_state(&app).as_str().to_string(),
        stt_ready,
        stt_model,
        agent_online: None,
        partial,
        mic_permission: permissions::status().as_str().to_string(),
    }
}

#[derive(Serialize)]
struct SettingsOut {
    hotkey: String,
    mic_device: String,
    launch_at_login: bool,
    theme: String,
    stt_model: String,
    stt_ready: bool,
    /// "auto" or a registered agent id. The endpoint, gateway key and local
    /// port are gone from this surface on purpose: a normal user never
    /// configures them, and the runtime is Sani's own child process.
    agent_mode: String,
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
        launch_at_login: s.launch_at_login,
        theme: s.theme,
        stt_model: s.stt_model,
        stt_ready,
        agent_mode: s.agent_mode,
    }
}

/// Exact STT catalog from the sidecar plus real cache state. This never invents
/// a model ID in the native or renderer layer.
#[tauri::command]
fn voice_models(app: tauri::AppHandle) -> Result<Vec<speech::VoiceModelDescriptor>, String> {
    speech::voice_models(&app)
}

#[tauri::command]
fn install_voice_model(
    app: tauri::AppHandle,
    model: String,
) -> Result<speech::VoiceModelDescriptor, String> {
    speech::install_voice_model(&app, &model)
}

#[tauri::command]
fn use_voice_model(app: tauri::AppHandle, model: String) -> Result<speech::VoiceModelDescriptor, String> {
    speech::use_voice_model(&app, &model)
}

#[tauri::command]
fn save_settings_cmd(
    app: tauri::AppHandle,
    hotkey: Option<String>,
    mic_device: Option<String>,
    launch_at_login: Option<bool>,
    theme: Option<String>,
    agent_mode: Option<String>,
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
        if let Some(login) = launch_at_login {
            s.launch_at_login = login;
            use tauri_plugin_autostart::ManagerExt;
            let autostart = app.autolaunch();
            let _ = if login {
                autostart.enable()
            } else {
                autostart.disable()
            };
        }
        if let Some(theme) = &theme {
            s.theme = theme.clone();
        }
        if let Some(mode) = &agent_mode {
            // Accepted verbatim: the registry, not this field, decides which
            // ids exist, and an unknown id resolves to automatic at dispatch.
            s.agent_mode = mode.clone();
        }
        settings::save(&app, &s)?;
    }
    if changed_mic {
        // Drop the capture so the next start_listening reopens the new device.
        *app.state::<SaniState>().audio.lock() = None;
    }
    // Settings renderers are distinct WebViews. Notify both only after the
    // native mutation succeeded, so neither relies on a second React store.
    onboarding::emit_settings_changed(&app);
    Ok(())
}

/// Select the registered agent for future turns. This is a settings-only
/// mutation: a live sani-core already owns both registry entries, so no core
/// restart is necessary or desirable.
#[tauri::command]
async fn set_agent_mode(app: tauri::AppHandle, agent_mode: String) -> Result<(), String> {
    let roster = runtime::agents(&app).await?;
    if !runtime::selectable_agent_mode(&agent_mode, &roster) {
        return Err("That agent is not currently available.".into());
    }
    {
        let settings_arc = app_state::settings(&app);
        let mut settings = settings_arc.write();
        settings.agent_mode = agent_mode;
        settings::save(&app, &settings)?;
    }
    onboarding::emit_settings_changed(&app);
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

/// The pill's stop control. Named "stop" on the wire for compatibility with the
/// already-shipped UI, but the semantic is Finish: commit what was heard rather
/// than discard it. Esc (`escape_cmd`) remains the discard path.
#[tauri::command]
fn stop_listening_cmd(app: tauri::AppHandle) {
    app_state::finish_listening(&app);
}

#[tauri::command]
fn escape_cmd(app: tauri::AppHandle) {
    app_state::handle_escape(&app);
}

/// Hide the panel without destroying it (replaces the browser window.close()).
#[tauri::command]
fn hide_panel(app: tauri::AppHandle) {
    windows::hide_panel(&app);
}

/// Show/hide the panel from the pill's transcript button. Hiding is always
/// reversible and never destroys the webview.
#[tauri::command]
fn toggle_panel(app: tauri::AppHandle) {
    match app.get_webview_window(windows::PANEL_LABEL) {
        Some(w) if w.is_visible().unwrap_or(false) => windows::hide_panel(&app),
        _ => {
            if let Err(err) = windows::show_panel(&app) {
                log::warn!("could not show the panel: {err}");
            }
        }
    }
}

/// Quick Settings lives in the overlay; this opens the already-existing main
/// desktop window, where Full Settings is selected by the renderer route.
#[tauri::command]
fn show_main_settings(app: tauri::AppHandle) -> Result<(), String> {
    windows::show_main(&app).map_err(|error| error.to_string())?;
    app.emit("settings://open-full", ()).map_err(|error| error.to_string())
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
    let conversation_id = app_state::settings(&app)
        .read()
        .active_conversation_id
        .clone();
    if conversation_id.is_empty() {
        return;
    }
    if let Ok(messages) = app_state::history(&app).messages(&conversation_id) {
        let _ = tauri::Emitter::emit(&app, "sani://history-loaded", messages);
    }
}

#[tauri::command]
fn main_ready(app: tauri::AppHandle) {
    panel_ready(app);
}

/// Current macOS microphone authorization state (never triggers a prompt).
#[tauri::command]
fn mic_permission_state() -> String {
    permissions::status().as_str().to_string()
}

/// Kick the system microphone prompt when the state is NotDetermined. Returns
/// the state at call time; the frontend polls `mic_permission_state` for the
/// user's decision (the prompt is answered asynchronously).
#[tauri::command]
fn request_mic_permission() -> String {
    if matches!(
        permissions::status(),
        permissions::MicPermission::NotDetermined
    ) {
        permissions::request();
    }
    permissions::status().as_str().to_string()
}

/// Open System Settings › Privacy & Security › Microphone so a denied user has
/// an actionable path back (RC-04).
#[tauri::command]
fn open_mic_settings() {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            .spawn();
    }
}

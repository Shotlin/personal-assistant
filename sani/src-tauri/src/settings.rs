//! Sani settings: persisted JSON plus first-run discovery of the local
//! gateway key from the sibling personal-assistant checkout.
//!
//! There is no login: Sani is a local client. The gateway key is read from
//! (in order) the `SANI_AGENT_API_KEY` env var, the saved settings file, or
//! a `.env` file found by walking up from the executable (dev builds live
//! inside the repository, so this finds the real key without hardcoding it).

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tauri::Manager;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    #[serde(default = "default_hotkey")]
    pub hotkey: String,
    #[serde(default)]
    pub mic_device: String,
    #[serde(default = "default_agent_base_url")]
    pub agent_base_url: String,
    #[serde(default)]
    pub agent_api_key: String,
    #[serde(default)]
    pub launch_at_login: bool,
    #[serde(default = "default_theme")]
    pub theme: String,
    #[serde(default = "default_stt_model")]
    pub stt_model: String,
    /// Override path to the Python interpreter that runs the STT sidecar.
    #[serde(default)]
    pub stt_python: String,
    /// Id of the conversation Sani resumes on launch.
    #[serde(default)]
    pub active_conversation_id: String,
}

fn default_hotkey() -> String {
    "Alt+Space".into()
}
fn default_agent_base_url() -> String {
    "http://127.0.0.1:8787".into()
}
fn default_theme() -> String {
    "dark".into()
}
fn default_stt_model() -> String {
    "small-streaming-en".into()
}

impl Default for Settings {
    fn default() -> Self {
        serde_json::from_value(serde_json::json!({})).expect("default settings")
    }
}

pub type SharedSettings = Arc<RwLock<Settings>>;

pub fn settings_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("settings.json"))
}

pub fn load(app: &tauri::AppHandle) -> Settings {
    let mut settings = settings_path(app)
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|raw| serde_json::from_str::<Settings>(&raw).ok())
        .unwrap_or_default();

    if settings.agent_api_key.trim().is_empty() {
        settings.agent_api_key = discover_gateway_key(app);
    }
    settings
}

pub fn save(app: &tauri::AppHandle, settings: &Settings) -> Result<(), String> {
    let path = settings_path(app).ok_or("no config directory")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let raw = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    fs::write(&path, raw).map_err(|e| e.to_string())
}

/// Find the local gateway key without any user-facing login.
pub fn discover_gateway_key(app: &tauri::AppHandle) -> String {
    if let Ok(key) = std::env::var("SANI_AGENT_API_KEY") {
        let key = key.trim().to_string();
        if !key.is_empty() {
            return key;
        }
    }
    // Walk up from the executable (dev: target/debug) and from the config
    // dir looking for a .env that carries AGENT_GATEWAY_API_KEY.
    let mut starts: Vec<PathBuf> = vec![];
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1).take(8) {
            starts.push(dir.to_path_buf());
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for dir in cwd.ancestors().take(8) {
            starts.push(dir.to_path_buf());
        }
    }
    if let Ok(config) = app.path().app_config_dir() {
        for dir in config.ancestors().take(4) {
            starts.push(dir.to_path_buf());
        }
    }
    for dir in starts {
        let env_file = dir.join(".env");
        if let Some(key) = read_key_from_env_file(&env_file) {
            log::info!("gateway key discovered from {}", env_file.display());
            return key;
        }
    }
    String::new()
}

fn read_key_from_env_file(path: &Path) -> Option<String> {
    let raw = fs::read_to_string(path).ok()?;
    for line in raw.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("AGENT_GATEWAY_API_KEY=") {
            let value = rest.trim().trim_matches('"').trim_matches('\'').to_string();
            if !value.is_empty() && value != "change-me" {
                return Some(value);
            }
        }
    }
    None
}

/// Locate the STT sidecar Python interpreter.
pub fn stt_python_path(app: &tauri::AppHandle, settings: &Settings) -> Option<PathBuf> {
    if !settings.stt_python.trim().is_empty() {
        let p = PathBuf::from(&settings.stt_python);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(p) = std::env::var("SANI_STT_PYTHON") {
        let p = PathBuf::from(p);
        if p.exists() {
            return Some(p);
        }
    }
    // Dev layout: <repo>/sani/src-tauri/target/debug/sani -> <repo>/sani/.stt-venv
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1).take(8) {
            for candidate in [
                // Bundled layout: Sani.app/Contents/Resources/.stt-venv
                dir.join("Resources/.stt-venv/bin/python"),
                dir.join("Resources/.stt-venv/bin/python3"),
                dir.join("Resources/.stt-venv/Scripts/python.exe"),
                // Dev layout: <repo>/sani/.stt-venv
                dir.join(".stt-venv/bin/python"),
                dir.join(".stt-venv/bin/python3"),
                dir.join(".stt-venv/Scripts/python.exe"),
            ] {
                if candidate.exists() {
                    return Some(candidate);
                }
            }
        }
    }
    let _ = app;
    None
}

/// Locate the sidecar script (bundled resource in packaged builds).
pub fn stt_script_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Ok(p) = std::env::var("SANI_STT_SCRIPT") {
        let p = PathBuf::from(p);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(resolver_dir) = app.path().resource_dir() {
        let candidate = resolver_dir.join("python/sani_stt.py");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1).take(8) {
            let candidate = dir.join("src-tauri/python/sani_stt.py");
            if candidate.exists() {
                return Some(candidate);
            }
            let candidate = dir.join("python/sani_stt.py");
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }
    None
}

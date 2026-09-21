//! Sani settings: persisted JSON plus first-run discovery of the local
//! gateway key from the sibling personal-assistant checkout.
//!
//! There is no login: Sani is a local client. The gateway key is a secret and
//! is NOT stored in the plaintext settings file. It is resolved from (in
//! order) the macOS Keychain, a private 0600 app-support file, then a one-time
//! bootstrap discovery (`SANI_AGENT_API_KEY` env var or a repo `.env`) which is
//! persisted back to the Keychain so a Finder-launched installed app — with no
//! shell environment — still connects deterministically (RC-06).

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
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
    /// Resolved at load time from the Keychain/private file. Never written to
    /// the plaintext settings file when secure persistence succeeds.
    #[serde(default)]
    pub agent_api_key: String,
    #[serde(default)]
    pub launch_at_login: bool,
    #[serde(default = "default_theme")]
    pub theme: String,
    #[serde(default = "default_stt_model")]
    pub stt_model: String,
    /// Silence that closes a user turn. Speech resuming inside this window
    /// cancels the commit and continues the same utterance.
    #[serde(default = "default_stt_turn_end_ms")]
    pub stt_turn_end_ms: u32,
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
fn default_stt_turn_end_ms() -> u32 {
    1400
}

/// Effective turn-end silence: `$SANI_STT_TURN_END_MS` overrides the stored
/// setting, which overrides the default. Clamped so a mistyped value cannot
/// make Sani either jumpy or unresponsive.
pub fn stt_turn_end_ms(settings: &Settings) -> u32 {
    let raw = std::env::var("SANI_STT_TURN_END_MS")
        .ok()
        .and_then(|v| v.trim().parse::<u32>().ok())
        .unwrap_or(settings.stt_turn_end_ms);
    raw.clamp(600, 5000)
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

    settings.agent_api_key = resolve_secret(app, &settings);
    settings
}

pub fn save(app: &tauri::AppHandle, settings: &Settings) -> Result<(), String> {
    let path = settings_path(app).ok_or("no config directory")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }

    // Persist the secret securely and keep it out of the plaintext file. If
    // secure persistence fails, fall back to leaving it in the JSON rather than
    // losing the key entirely.
    let mut to_write = settings.clone();
    if !settings.agent_api_key.trim().is_empty() {
        if persist_secret(app, &settings.agent_api_key).starts_with("stored") {
            to_write.agent_api_key = String::new();
        }
    } else {
        to_write.agent_api_key = String::new();
    }

    let raw = serde_json::to_string_pretty(&to_write).map_err(|e| e.to_string())?;
    fs::write(&path, raw).map_err(|e| e.to_string())
}

// ------------------------------------------------------------------- secret

const KEYCHAIN_SERVICE: &str = "sani-agent-key";
const KEYCHAIN_ACCOUNT: &str = "app.sani.local";

/// Resolve the gateway key without any user-facing login. Keychain first, then
/// the private file, then a one-time bootstrap discovery that is persisted for
/// future (Finder) launches.
fn resolve_secret(app: &tauri::AppHandle, settings: &Settings) -> String {
    // Log where the key came from, never the key itself.
    if let Some(key) = keychain_read().filter(|k| !k.trim().is_empty()) {
        log::info!("gateway key resolved from the macOS Keychain");
        return key;
    }
    if let Some(key) = secret_file_read(app).filter(|k| !k.trim().is_empty()) {
        log::info!("gateway key resolved from the private app-support file");
        promote_to_keychain(&key);
        return key;
    }
    // Bootstrap: dev builds and first run discover from env / repo .env, then
    // persist so the installed app never needs a shell environment.
    let discovered = discover_gateway_key(app);
    if !discovered.trim().is_empty() {
        let where_stored = persist_secret(app, &discovered);
        log::info!("gateway key discovered and persisted for next launch ({where_stored})");
        return discovered;
    }
    // Legacy: a key already sitting in the plaintext settings file.
    if !settings.agent_api_key.trim().is_empty() {
        log::info!("gateway key resolved from the settings file");
        promote_to_keychain(&settings.agent_api_key);
        return settings.agent_api_key.clone();
    }
    log::warn!("no gateway key configured: Sani cannot authenticate with the Personal Assistant");
    String::new()
}

/// Retry the Keychain on every launch until it takes: an earlier failure can be
/// transient (locked keychain, non-GUI session), and the Keychain is preferred
/// over the 0600 file. Only the outcome is logged, never the value.
fn promote_to_keychain(key: &str) {
    if keychain_write(key) {
        log::info!("gateway key upgraded to the macOS Keychain");
    }
}

/// Store the secret in the Keychain (preferred); fall back to a 0600 file.
/// Returns where it actually landed, for logging — never the value itself.
fn persist_secret(app: &tauri::AppHandle, key: &str) -> &'static str {
    if keychain_write(key) {
        return "stored in the macOS Keychain";
    }
    if secret_file_write(app, key) {
        return "stored in the private 0600 app-support file";
    }
    "not persisted; it will be rediscovered next launch"
}

fn keychain_read() -> Option<String> {
    let out = std::process::Command::new("security")
        .args([
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
        ])
        .output()
        .ok()?;
    if out.status.success() {
        let value = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !value.is_empty() {
            return Some(value);
        }
    }
    None
}

fn keychain_write(key: &str) -> bool {
    // -A: allow any application to read without a prompt, so the installed app
    // (a different code identity than the `security` CLI that wrote it) can
    // still resolve the key on a Finder launch. Local-only MVP secret.
    // The secret goes to the child's stdin — never argv, where it would show up
    // in the process list.
    let mut child = match std::process::Command::new("security")
        .args([
            "add-generic-password",
            "-A",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
        ])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            log::debug!("keychain write unavailable: {err}");
            return false;
        }
    };
    if let Some(mut stdin) = child.stdin.take() {
        // `-w` with no value reads the secret twice from the terminal (confirm
        // prompt), so a single line is rejected with "passwords don't match".
        if stdin
            .write_all(format!("{key}\n{key}\n").as_bytes())
            .and_then(|_| stdin.flush())
            .is_err()
        {
            let _ = child.kill();
            return false;
        }
    }
    let output = match child.wait_with_output() {
        Ok(output) => output,
        Err(_) => return false,
    };
    if !output.status.success() {
        let why = String::from_utf8_lossy(&output.stderr)
            .lines()
            .filter(|l| !l.starts_with("password data for new item"))
            .find(|l| !l.trim().is_empty())
            .unwrap_or("keychain write failed")
            .to_string();
        log::debug!("keychain write rejected: {why}");
        return false;
    }
    true
}

fn secret_file_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("agent_key"))
}

fn secret_file_read(app: &tauri::AppHandle) -> Option<String> {
    let path = secret_file_path(app)?;
    let raw = fs::read_to_string(path).ok()?;
    let value = raw.trim().to_string();
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

fn secret_file_write(app: &tauri::AppHandle, key: &str) -> bool {
    let Some(path) = secret_file_path(app) else { return false };
    if let Some(parent) = path.parent() {
        if fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    if fs::write(&path, key.trim()).is_err() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
    }
    true
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

/// Locate the STT sidecar Python interpreter (dev fallback only; packaged
/// builds use the self-contained `externalBin` sidecar — see speech.rs).
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

/// Locate the sidecar script (dev fallback; packaged builds embed it in the
/// frozen sidecar binary).
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

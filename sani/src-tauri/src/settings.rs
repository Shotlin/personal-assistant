//! Sani settings: persisted JSON for the application's own preferences.
//!
//! There is no login and no endpoint to configure: the assistant runtime is
//! Sani's own child process. Provider credentials are *never* stored here —
//! they live in the OS credential store and are read by `secret_read` below
//! (onboarding for provider keys, `sani_core` to build the sidecar's
//! environment).
//!
//! A settings.json written by an older build may still carry the localhost
//! gateway's base URL and key. Unknown fields are ignored on load and dropped
//! on the next save, so upgrading is automatic and never destructive.

use parking_lot::RwLock;
use serde::{Deserialize, Deserializer, Serialize};
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::Manager;

use crate::overlay_geometry::{
    default_panel_frame, default_pill_frame, OverlayFrame, OverlayLayout,
};
use crate::window_geometry::NormalWindowBounds;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct MainWindowSettings {
    #[serde(default)]
    pub display_id: String,
    #[serde(default)]
    pub x: f64,
    #[serde(default)]
    pub y: f64,
    #[serde(default)]
    pub width: f64,
    #[serde(default)]
    pub height: f64,
    #[serde(default)]
    pub maximized: bool,
}

/// Committed Voice Pill / Conversation Panel placement.
///
/// Positions are normalized against a usable work area; sizes are logical
/// points. A missing frame falls back to the Phase 1 default rather than
/// failing to parse, so a hand-edited or half-written `overlay_layout` can
/// never take the rest of the settings file down with it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayLayoutSettings {
    #[serde(default)]
    pub display_affinity: String,
    #[serde(default = "default_pill_frame")]
    pub pill: OverlayFrame,
    #[serde(default = "default_panel_frame")]
    pub panel: OverlayFrame,
}

impl Default for OverlayLayoutSettings {
    fn default() -> Self {
        Self::from(OverlayLayout::default())
    }
}

impl From<OverlayLayout> for OverlayLayoutSettings {
    fn from(layout: OverlayLayout) -> Self {
        Self {
            display_affinity: layout.display_affinity,
            pill: layout.pill,
            panel: layout.panel,
        }
    }
}

impl From<OverlayLayoutSettings> for OverlayLayout {
    fn from(saved: OverlayLayoutSettings) -> Self {
        Self {
            display_affinity: saved.display_affinity,
            pill: saved.pill,
            panel: saved.panel,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    #[serde(default = "default_hotkey")]
    pub hotkey: String,
    #[serde(default)]
    pub mic_device: String,
    #[serde(default)]
    pub launch_at_login: bool,
    #[serde(default = "default_theme")]
    pub theme: String,
    #[serde(default = "default_stt_model")]
    pub stt_model: String,
    /// Days to retain non-secret execution metadata. Conversations/messages
    /// are not governed by this value.
    #[serde(default = "default_technical_retention_days")]
    pub technical_retention_days: u32,
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
    /// Reasoning (Deep Agent) provider + model. Non-secret; the credential lives
    /// in the Keychain, never here.
    #[serde(default = "default_reasoning_provider")]
    pub reasoning_provider: String,
    #[serde(default)]
    pub reasoning_model: String,
    /// Quick computer-control (Velo / JEV) provider + model. When the provider
    /// is OpenRouter the same Keychain credential as the reasoning model is
    /// reused — it is never asked for twice.
    #[serde(default = "default_velo_provider")]
    pub velo_provider: String,
    #[serde(default = "default_velo_model")]
    pub velo_model: String,
    /// Which registered agent takes a turn. `auto` was a legacy pseudo-agent;
    /// persisted copies are migrated to Velo during deserialization.
    #[serde(
        default = "default_agent_mode",
        deserialize_with = "deserialize_agent_mode"
    )]
    pub agent_mode: String,
    /// Staged activation of the approved D1 architecture: run the embedded CUA
    /// daemon in driver `standard` mode (no capability manifest) instead of
    /// `bounded`. Off by default so the switch happens once, with the batched
    /// install that is allowed to consume a fresh macOS grant -- the mode is
    /// read only when the daemon is spawned, never mid-session.
    #[serde(default)]
    pub computer_control_standard_mode: bool,
    /// Normal main-window geometry and its independent maximized intent.
    /// Coordinates are logical points relative to the persisted display's
    /// usable work area; maximized state never overwrites these bounds.
    #[serde(default)]
    pub main_window: Option<MainWindowSettings>,
    /// Committed overlay placement. `None` on every settings file written
    /// before the layout editor existed, which resolves to the defaults.
    #[serde(default)]
    pub overlay_layout: Option<OverlayLayoutSettings>,
}

fn default_agent_mode() -> String {
    "velo".into()
}

/// Canonicalize only legacy/default agent selections. Unknown non-empty IDs
/// intentionally survive so dispatch can reject an explicit bad selection
/// rather than silently routing a user's turn elsewhere.
pub fn canonical_agent_mode(mode: &str) -> String {
    let trimmed = mode.trim();
    if trimmed.is_empty() || trimmed == "auto" {
        default_agent_mode()
    } else {
        trimmed.to_string()
    }
}

fn deserialize_agent_mode<'de, D>(deserializer: D) -> Result<String, D::Error>
where
    D: Deserializer<'de>,
{
    Ok(canonical_agent_mode(&String::deserialize(deserializer)?))
}

fn default_reasoning_provider() -> String {
    "openrouter".into()
}
fn default_velo_provider() -> String {
    "openrouter".into()
}
fn default_velo_model() -> String {
    "jev-latest".into()
}

fn default_hotkey() -> String {
    "Alt+Space".into()
}
fn default_theme() -> String {
    "dark".into()
}
fn default_stt_model() -> String {
    "small-streaming-en".into()
}
fn default_technical_retention_days() -> u32 {
    30
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

pub fn update_normal_main_window(
    settings: &mut Settings,
    display_id: String,
    normal: NormalWindowBounds,
) {
    let state = settings
        .main_window
        .get_or_insert_with(MainWindowSettings::default);
    state.display_id = display_id;
    state.x = normal.x;
    state.y = normal.y;
    state.width = normal.width;
    state.height = normal.height;
}

pub fn update_main_window_maximized(settings: &mut Settings, maximized: bool) {
    settings
        .main_window
        .get_or_insert_with(MainWindowSettings::default)
        .maximized = maximized;
}

/// The committed overlay layout, or the Phase 1 defaults when nothing has been
/// saved yet. Overlay placement is wholly independent of `main_window`.
pub fn load_overlay_layout(settings: &Settings) -> OverlayLayout {
    settings
        .overlay_layout
        .clone()
        .map(OverlayLayout::from)
        .unwrap_or_default()
}

pub fn update_overlay_layout(settings: &mut Settings, layout: OverlayLayout) {
    settings.overlay_layout = Some(layout.into());
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
    let Some(path) = settings_path(app) else {
        return Settings::default();
    };
    let Ok(raw) = fs::read_to_string(&path) else {
        return Settings::default();
    };

    // Rewrite only the migrated field in the original JSON object. This keeps
    // settings added by a newer build intact rather than using migration as an
    // excuse to discard them during an older build's launch.
    if let Ok(mut value) = serde_json::from_str::<serde_json::Value>(&raw) {
        if let Some(object) = value.as_object_mut() {
            let persisted = object
                .get("agent_mode")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default();
            let canonical = canonical_agent_mode(persisted);
            if object.get("agent_mode").and_then(serde_json::Value::as_str) != Some(canonical.as_str()) {
                object.insert("agent_mode".into(), serde_json::Value::String(canonical));
                match serde_json::to_string_pretty(&value) {
                    Ok(updated) => {
                        if let Err(err) = fs::write(&path, updated) {
                            log::warn!("could not persist agent-mode migration: {err}");
                        }
                    }
                    Err(err) => log::warn!("could not encode agent-mode migration: {err}"),
                }
            }
        }
    }

    serde_json::from_str::<Settings>(&raw).unwrap_or_default()
}

pub fn save(app: &tauri::AppHandle, settings: &Settings) -> Result<(), String> {
    let path = settings_path(app).ok_or("no config directory")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }

    let raw = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    fs::write(&path, raw).map_err(|e| e.to_string())
}

// ------------------------------------------------------------------- secret

const KEYCHAIN_ACCOUNT: &str = "app.sani.local";

/// Read a generic secret from the Keychain by service name. Used by onboarding
/// for provider credentials (OpenRouter, TypeSafe) so API keys never touch the
/// plaintext settings file, the database, memory, or logs.
pub fn secret_read(service: &str) -> Option<String> {
    let out = std::process::Command::new("security")
        .args([
            "find-generic-password",
            "-s",
            service,
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

/// Store a generic secret. Secret goes to the child's stdin, never argv.
pub fn secret_write(service: &str, key: &str) -> bool {
    // -A so the installed app (a different code identity than the CLI) can read
    // it back without a prompt; the value is passed on stdin, never in argv.
    let mut child = match std::process::Command::new("security")
        .args([
            "add-generic-password",
            "-A",
            "-U",
            "-s",
            service,
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
            log::debug!("keychain write unavailable ({service}): {err}");
            return false;
        }
    };
    if let Some(mut stdin) = child.stdin.take() {
        // `-w` with no value reads twice for confirmation, so send it twice.
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
        log::debug!("keychain write rejected ({service}): {why}");
        return false;
    }
    true
}

/// Remove a generic secret (safe when it does not exist).
#[allow(dead_code)] // used by credential reset / repair
pub fn secret_delete(service: &str) -> bool {
    std::process::Command::new("security")
        .args([
            "delete-generic-password",
            "-s",
            service,
            "-a",
            KEYCHAIN_ACCOUNT,
        ])
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::overlay_geometry::{default_pill_frame, OverlayFrame, OverlayLayout};
    use crate::window_geometry::NormalWindowBounds;

    #[test]
    fn older_settings_without_main_window_load_normally() {
        let settings: Settings = serde_json::from_str(r#"{"theme":"dark"}"#).unwrap();

        assert!(settings.main_window.is_none());
        assert_eq!(settings.theme, "dark");
    }

    #[test]
    fn missing_agent_mode_defaults_to_velo_without_rewriting_explicit_modes() {
        let missing: Settings = serde_json::from_str(r#"{"theme":"dark"}"#).unwrap();
        let deep: Settings = serde_json::from_str(r#"{"agent_mode":"deep"}"#).unwrap();
        let velo: Settings = serde_json::from_str(r#"{"agent_mode":"velo"}"#).unwrap();
        let auto: Settings = serde_json::from_str(r#"{"agent_mode":"auto"}"#).unwrap();

        assert_eq!(missing.agent_mode, "velo");
        assert_eq!(deep.agent_mode, "deep");
        assert_eq!(velo.agent_mode, "velo");
        assert_eq!(auto.agent_mode, "velo");
    }

    #[test]
    fn empty_agent_mode_is_migrated_to_velo() {
        let empty: Settings = serde_json::from_str(r#"{"agent_mode":"   "}"#).unwrap();
        assert_eq!(empty.agent_mode, "velo");
    }

    #[test]
    fn maximized_transition_keeps_last_normal_bounds() {
        let mut settings = Settings::default();
        update_normal_main_window(
            &mut settings,
            "built-in".into(),
            NormalWindowBounds {
                x: 100.0,
                y: 80.0,
                width: 1040.0,
                height: 700.0,
            },
        );
        update_main_window_maximized(&mut settings, true);

        let saved = settings.main_window.unwrap();
        assert_eq!(saved.width, 1040.0);
        assert_eq!(saved.height, 700.0);
        assert!(saved.maximized);
    }

    #[test]
    fn old_settings_without_overlay_layout_still_load() {
        assert!(Settings::default().overlay_layout.is_none());

        let settings: Settings = serde_json::from_str(r#"{"theme":"dark"}"#).unwrap();
        assert!(settings.overlay_layout.is_none());
        assert_eq!(load_overlay_layout(&settings), OverlayLayout::default());
    }

    #[test]
    fn overlay_layout_defaults_survive_a_partial_document() {
        let settings: Settings =
            serde_json::from_str(r#"{"overlay_layout":{"display_affinity":"built-in"}}"#).unwrap();
        let layout = load_overlay_layout(&settings);

        assert_eq!(layout.display_affinity, "built-in");
        assert_eq!(layout.pill, default_pill_frame());
    }

    #[test]
    fn overlay_layout_round_trips_through_json() {
        let layout = OverlayLayout {
            display_affinity: "built-in".into(),
            pill: OverlayFrame {
                x_ratio: 0.3,
                y_ratio: 0.8,
                width: 640.0,
                height: 96.0,
            },
            panel: OverlayFrame {
                x_ratio: 0.6,
                y_ratio: 0.02,
                width: 520.0,
                height: 600.0,
            },
        };
        let mut settings = Settings::default();
        update_overlay_layout(&mut settings, layout.clone());

        let raw = serde_json::to_string(&settings).unwrap();
        let reloaded: Settings = serde_json::from_str(&raw).unwrap();

        assert_eq!(load_overlay_layout(&reloaded), layout);
    }

    #[test]
    fn saving_overlay_layout_never_changes_main_window() {
        let mut settings = Settings::default();
        update_normal_main_window(
            &mut settings,
            "built-in".into(),
            NormalWindowBounds {
                x: 100.0,
                y: 80.0,
                width: 1040.0,
                height: 700.0,
            },
        );
        update_main_window_maximized(&mut settings, true);
        let before = settings.main_window.clone();

        update_overlay_layout(&mut settings, OverlayLayout::default());

        assert_eq!(settings.main_window, before);
        let raw = serde_json::to_string(&settings).unwrap();
        let reloaded: Settings = serde_json::from_str(&raw).unwrap();
        assert_eq!(reloaded.main_window, before);
    }
}

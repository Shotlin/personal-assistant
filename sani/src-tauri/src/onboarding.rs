//! First-run onboarding command surface.
//!
//! Rust owns every decision the setup UI renders: the persistent setup state,
//! the OS permission truth, and the secure credential store. React only
//! reflects these and forwards user actions. API keys go to the OS credential
//! store (Keychain / Credential Manager) — never settings.json, the database,
//! memory, or logs.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};

use crate::{app_state, permissions, settings, setup, system_permissions};

pub const OPENROUTER_KEY_SERVICE: &str = "sani-openrouter-key";
pub const TYPESAFE_KEY_SERVICE: &str = "sani-typesafe-key";

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(12))
        .build()
        .expect("reqwest client")
}

// ------------------------------------------------------------- setup state

#[derive(Serialize)]
pub struct SetupSnapshot {
    pub schema_version: u32,
    pub onboarding_complete: bool,
    pub current_stage: String,
    pub percent: u32,
    pub local_setup_complete: bool,
    pub components: Vec<ComponentView>,
}

#[derive(Serialize)]
pub struct ComponentView {
    pub step: &'static str,
    pub friendly: &'static str,
    pub status: setup::ComponentStatus,
    pub detail: String,
    pub error: Option<String>,
}

fn snapshot_state(app: &AppHandle) -> SetupSnapshot {
    let state = setup::snapshot(app);
    SetupSnapshot {
        schema_version: state.schema_version,
        onboarding_complete: state.onboarding_complete,
        current_stage: state.current_stage.clone(),
        percent: state.percent(),
        local_setup_complete: state.local_setup_complete(),
        components: setup::Component::all()
            .iter()
            .map(|c| {
                let r = state.components.get(c);
                ComponentView {
                    step: c.key(),
                    friendly: c.friendly(),
                    status: r.map(|r| r.status).unwrap_or(setup::ComponentStatus::Pending),
                    detail: r.map(|r| r.detail.clone()).unwrap_or_default(),
                    error: r.and_then(|r| r.error.clone()),
                }
            })
            .collect(),
    }
}

#[tauri::command]
pub fn setup_state(app: AppHandle) -> SetupSnapshot {
    snapshot_state(&app)
}

#[tauri::command]
pub fn run_setup(app: AppHandle) {
    setup::run_local_setup(&app);
}

#[tauri::command]
pub fn retry_setup_component(app: AppHandle, step: String) -> Result<(), String> {
    setup::retry_component(&app, &step)
}

#[derive(Serialize)]
pub struct StageOut {
    pub percent: u32,
    pub local_setup_complete: bool,
}

/// Persist the current onboarding stage so a relaunch resumes in place.
#[tauri::command]
pub fn set_onboarding_stage(app: AppHandle, stage: String) -> Result<StageOut, String> {
    let mgr = app.state::<setup::SharedSetup>();
    let mut state = mgr.state.lock();
    state.current_stage = stage;
    state.updated_at = crate::app_state::now_ms();
    let out = StageOut { percent: state.percent(), local_setup_complete: state.local_setup_complete() };
    // persist through the setup module's owned writer.
    setup::persist_state(&app, &state);
    Ok(out)
}

/// Mark onboarding complete, close the setup window, and start the normal Sani
/// experience (overlays + hotkey).
#[tauri::command]
pub fn complete_onboarding(app: AppHandle) -> Result<(), String> {
    {
        let mgr = app.state::<setup::SharedSetup>();
        let mut state = mgr.state.lock();
        state.onboarding_complete = true;
        state.current_stage = "ready".into();
        state.updated_at = app_state::now_ms();
        setup::persist_state(&app, &state);
    }
    if let Some(window) = app.get_webview_window(crate::windows::ONBOARDING_LABEL) {
        let _ = window.close();
    }
    crate::enter_normal_mode(&app).map_err(|e| e.to_string())?;
    let _ = app.emit("sani://onboarding-complete", ());
    Ok(())
}

/// Diagnostic affordance: clear all setup progress (Settings → Repair later).
#[tauri::command]
pub fn reset_onboarding(app: AppHandle) -> Result<(), String> {
    let mgr = app.state::<setup::SharedSetup>();
    let mut state = mgr.state.lock();
    *state = setup::SetupState::default();
    setup::persist_state(&app, &state);
    Ok(())
}

// ------------------------------------------------------------- credentials

#[derive(Serialize)]
pub struct AiConfig {
    pub reasoning_provider: String,
    pub reasoning_model: String,
    pub velo_provider: String,
    pub velo_model: String,
    pub has_openrouter: bool,
    pub has_typesafe: bool,
}

#[tauri::command]
pub fn get_ai_config(app: AppHandle) -> AiConfig {
    let s = app_state::settings(&app).read().clone();
    AiConfig {
        reasoning_provider: s.reasoning_provider,
        reasoning_model: s.reasoning_model,
        velo_provider: s.velo_provider,
        velo_model: s.velo_model,
        has_openrouter: settings::secret_read(OPENROUTER_KEY_SERVICE).is_some(),
        has_typesafe: settings::secret_read(TYPESAFE_KEY_SERVICE).is_some(),
    }
}

#[derive(Deserialize)]
pub struct AiConfigPatch {
    pub reasoning_provider: Option<String>,
    pub reasoning_model: Option<String>,
    pub velo_provider: Option<String>,
    pub velo_model: Option<String>,
}

#[tauri::command]
pub fn save_ai_config(app: AppHandle, patch: AiConfigPatch) -> Result<AiConfig, String> {
    let settings_arc = app_state::settings(&app);
    let config = {
        let mut s = settings_arc.write();
        if let Some(v) = patch.reasoning_provider {
            s.reasoning_provider = v;
        }
        if let Some(v) = patch.reasoning_model {
            s.reasoning_model = v;
        }
        if let Some(v) = patch.velo_provider {
            s.velo_provider = v;
        }
        if let Some(v) = patch.velo_model {
            s.velo_model = v;
        }
        settings::save(&app, &s)?;
        AiConfig {
            reasoning_provider: s.reasoning_provider.clone(),
            reasoning_model: s.reasoning_model.clone(),
            velo_provider: s.velo_provider.clone(),
            velo_model: s.velo_model.clone(),
            has_openrouter: settings::secret_read(OPENROUTER_KEY_SERVICE).is_some(),
            has_typesafe: settings::secret_read(TYPESAFE_KEY_SERVICE).is_some(),
        }
    };
    Ok(config)
}

#[derive(Serialize)]
pub struct StoreResult {
    pub stored: bool,
    pub has_openrouter: bool,
    pub has_typesafe: bool,
}

/// Store a provider key in the OS credential store. Empty clears it. The value
/// is never returned or logged.
#[tauri::command]
pub fn store_provider_key(app: AppHandle, provider: String, key: String) -> Result<StoreResult, String> {
    let service = provider_service(&provider)?;
    let trimmed = key.trim();
    let stored = if trimmed.is_empty() {
        settings::secret_delete(service)
    } else {
        settings::secret_write(service, trimmed)
    };
    let _ = app;
    Ok(StoreResult {
        stored,
        has_openrouter: settings::secret_read(OPENROUTER_KEY_SERVICE).is_some(),
        has_typesafe: settings::secret_read(TYPESAFE_KEY_SERVICE).is_some(),
    })
}

fn provider_service(provider: &str) -> Result<&'static str, String> {
    match provider {
        "openrouter" => Ok(OPENROUTER_KEY_SERVICE),
        "typesafe" => Ok(TYPESAFE_KEY_SERVICE),
        other => Err(format!("unknown provider '{other}'")),
    }
}

// -------------------------------------------------------------- validation

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
#[serde(tag = "status")]
pub enum KeyStatus {
    Connected { label: Option<String> },
    Invalid,
    Offline,
}

/// Lightweight, safe check that a key is real and reachable. Never stores.
#[tauri::command]
pub async fn validate_provider_key(provider: String, key: String) -> KeyStatus {
    let key = key.trim().to_string();
    if key.is_empty() {
        return KeyStatus::Invalid;
    }
    match provider.as_str() {
        "openrouter" => check_openrouter(&key).await,
        "typesafe" => check_typesafe(&key).await,
        _ => KeyStatus::Invalid,
    }
}

async fn check_openrouter(key: &str) -> KeyStatus {
    let url = "https://openrouter.ai/api/v1/key";
    match client().get(url).bearer_auth(key).send().await {
        Ok(resp) => {
            let status = resp.status();
            if status.is_success() {
                let label = resp
                    .json::<Value>()
                    .await
                    .ok()
                    .and_then(|v| v.get("data").and_then(|d| d.get("label")).and_then(Value::as_str).map(String::from));
                KeyStatus::Connected { label }
            } else if status.as_u16() == 401 || status.as_u16() == 403 {
                KeyStatus::Invalid
            } else {
                // Reachable but unexpected — treat as connected so a transient
                // provider quirk never blocks an otherwise-valid key.
                KeyStatus::Connected { label: None }
            }
        }
        Err(_) => KeyStatus::Offline,
    }
}

async fn check_typesafe(key: &str) -> KeyStatus {
    let url = std::env::var("VELO_TYPESAFE_BASE_URL")
        .unwrap_or_else(|_| "https://api.typesafe.ai".to_string());
    let url = format!("{}/v1/models", url.trim_end_matches('/'));
    match client().get(&url).bearer_auth(key).send().await {
        Ok(resp) => {
            let status = resp.status().as_u16();
            if (401..403).contains(&status) {
                KeyStatus::Invalid
            } else {
                // Any HTTP response means the host accepted the request shape;
                // without a documented preflight we do not call a reachable
                // endpoint invalid.
                KeyStatus::Connected { label: None }
            }
        }
        Err(_) => KeyStatus::Offline,
    }
}

#[derive(Serialize)]
pub struct ModelOption {
    pub id: String,
    pub name: String,
}

/// Searchable model list for the reasoning-model selector. Public endpoint; the
/// key is optional and only sent when provided.
#[tauri::command]
pub async fn list_openrouter_models(key: Option<String>) -> Vec<ModelOption> {
    let mut req = client().get("https://openrouter.ai/api/v1/models");
    if let Some(key) = key.filter(|k| !k.trim().is_empty()) {
        req = req.bearer_auth(key.trim());
    }
    let Ok(resp) = req.send().await else { return Vec::new() };
    let Ok(body) = resp.json::<Value>().await else { return Vec::new() };
    let Some(data) = body.get("data").and_then(Value::as_array) else { return Vec::new() };
    let mut models: Vec<ModelOption> = data
        .iter()
        .filter_map(|m| {
            let id = m.get("id").and_then(Value::as_str)?;
            let name = m.get("name").and_then(Value::as_str).unwrap_or(id);
            Some(ModelOption { id: id.to_string(), name: name.to_string() })
        })
        .collect();
    models.sort_by(|a, b| a.name.cmp(&b.name));
    models
}

// ------------------------------------------------------------- permissions

#[derive(Serialize)]
pub struct PermissionSnapshot {
    pub microphone: String,
    pub accessibility: String,
    pub screen_recording: String,
    pub screen_recording_restart_required: bool,
}

#[tauri::command]
pub fn permission_snapshot() -> PermissionSnapshot {
    PermissionSnapshot {
        microphone: permissions::status().as_str().to_string(),
        accessibility: system_permissions::accessibility().as_str().to_string(),
        screen_recording: system_permissions::screen_recording().as_str().to_string(),
        screen_recording_restart_required: system_permissions::screen_recording_restart_required(),
    }
}

#[tauri::command]
pub fn open_permission_settings(pane: String) {
    system_permissions::open_settings(&pane);
}

/// Kick the Accessibility prompt (adds Sani to the list so the toggle exists).
#[tauri::command]
pub fn request_accessibility() -> String {
    system_permissions::accessibility_prompt().as_str().to_string()
}

/// Kick the Screen Recording prompt.
#[tauri::command]
pub fn request_screen_recording() -> String {
    system_permissions::screen_recording_request().as_str().to_string()
}

/// Relaunch Sani so a just-granted Screen Recording permission takes effect.
#[tauri::command]
pub fn restart_app(app: AppHandle) {
    #[cfg(target_os = "macos")]
    {
        if let Ok(exe) = std::env::current_exe() {
            // Sani.app/Contents/MacOS/Sani → Sani.app
            let bundle = exe
                .ancestors()
                .find(|p| p.extension().map(|e| e == "app").unwrap_or(false));
            if let Some(bundle) = bundle {
                let _ = std::process::Command::new("open").arg(bundle).spawn();
            } else {
                let _ = std::process::Command::new(&exe).spawn();
            }
        }
    }
    app.exit(0);
}

// ----------------------------------------------------------- final health

#[derive(Serialize)]
pub struct HealthCheck {
    pub label: &'static str,
    pub ok: bool,
    pub note: String,
}

#[derive(Serialize)]
pub struct FinalHealth {
    pub checks: Vec<HealthCheck>,
    /// Local software is healthy and onboarding may finish even when the model
    /// provider is unreachable (internet is not a setup prerequisite).
    pub local_healthy: bool,
    pub network_only_issue: bool,
}

#[tauri::command]
pub fn final_health(app: AppHandle) -> FinalHealth {
    let snapshot = setup::snapshot(&app);
    let done = |c: setup::Component| snapshot.components.get(&c).map(|r| r.status.is_done()).unwrap_or(false);
    let mic = permissions::status().is_granted();
    let access = system_permissions::accessibility().is_granted() || system_permissions::screen_recording().is_granted();
    let creds = settings::secret_read(OPENROUTER_KEY_SERVICE).is_some() || settings::secret_read(TYPESAFE_KEY_SERVICE).is_some();

    let checks = vec![
        HealthCheck { label: "Local storage", ok: done(setup::Component::AppData) && done(setup::Component::Database), note: String::new() },
        HealthCheck { label: "AI runtime", ok: done(setup::Component::Core), note: String::new() },
        HealthCheck { label: "Voice", ok: done(setup::Component::SttRuntime), note: if done(setup::Component::SttModel) { String::new() } else { "downloads on first use".into() } },
        HealthCheck { label: "Computer control", ok: done(setup::Component::Cua), note: String::new() },
        HealthCheck { label: "Permissions", ok: mic && access, note: String::new() },
        HealthCheck { label: "Models", ok: creds, note: String::new() },
    ];
    // Offline only matters for the network-backed "Models" check.
    let local_healthy = checks
        .iter()
        .filter(|c| c.label != "Models")
        .all(|c| c.ok);
    FinalHealth { local_healthy, network_only_issue: !creds, checks }
}

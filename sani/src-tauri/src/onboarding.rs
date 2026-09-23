//! First-run onboarding command surface.
//!
//! Rust owns every decision the setup UI renders: the persistent setup state,
//! the OS permission truth, and the secure credential store. React only
//! reflects these and forwards user actions. API keys go to the OS credential
//! store (Keychain / Credential Manager) — never settings.json, the database,
//! memory, or logs.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};

use crate::{app_state, permissions, sani_core, settings, setup, system_permissions};

pub const OPENROUTER_KEY_SERVICE: &str = "sani-openrouter-key";
pub const TYPESAFE_KEY_SERVICE: &str = "sani-typesafe-key";

/// Non-secret distinction between what the user saved and what the current
/// sidecar has actually accepted. Each WebView reads it from native state.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ApplyStatus {
    Saved,
    Applying,
    Ready,
    FailedToApply,
}

impl Default for ApplyStatus {
    fn default() -> Self {
        Self::Saved
    }
}

#[derive(Default)]
pub struct SettingsApplicationState {
    status: Mutex<ApplyStatus>,
    version: AtomicU64,
}

fn apply_status(app: &AppHandle) -> ApplyStatus {
    app.state::<SettingsApplicationState>()
        .status
        .lock()
        .map(|state| state.clone())
        .unwrap_or(ApplyStatus::FailedToApply)
}

fn set_apply_status(app: &AppHandle, status: ApplyStatus) {
    if let Ok(mut state) = app.state::<SettingsApplicationState>().status.lock() {
        *state = status;
    }
    app.state::<SettingsApplicationState>()
        .version
        .fetch_add(1, Ordering::Relaxed);
}

pub fn set_runtime_status(app: &AppHandle, status: ApplyStatus) {
    set_apply_status(app, status);
    emit_settings_changed(app);
}

fn settings_version(app: &AppHandle) -> u64 {
    app.state::<SettingsApplicationState>()
        .version
        .load(Ordering::Relaxed)
}

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
                    status: r
                        .map(|r| r.status)
                        .unwrap_or(setup::ComponentStatus::Pending),
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
    let out = StageOut {
        percent: state.percent(),
        local_setup_complete: state.local_setup_complete(),
    };
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

/// The complete non-secret settings source consumed by both settings WebViews.
/// Key values, candidate inputs, and diagnostics are deliberately absent.
#[derive(Clone, Serialize)]
pub struct FullSettingsSnapshot {
    pub version: u64,
    pub hotkey: String,
    pub mic_device: String,
    pub launch_at_login: bool,
    pub theme: String,
    pub stt_model: String,
    pub stt_ready: bool,
    pub agent_mode: String,
    pub reasoning_provider: String,
    pub reasoning_model: String,
    pub velo_provider: String,
    pub velo_model: String,
    pub openrouter_key: String,
    pub typesafe_key: String,
    pub runtime_status: ApplyStatus,
    pub microphone_permission: String,
    pub accessibility_permission: String,
    pub screen_recording_permission: String,
    pub storage_path: String,
    pub technical_retention_days: u32,
}

fn full_settings_snapshot(app: &AppHandle) -> FullSettingsSnapshot {
    let s = app_state::settings(app).read().clone();
    let stt_ready = app
        .state::<app_state::SaniState>()
        .speech
        .lock()
        .as_ref()
        .map(|h| h.is_ready())
        .unwrap_or(false);
    let storage_path = app
        .path()
        .app_data_dir()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    FullSettingsSnapshot {
        version: settings_version(app),
        hotkey: s.hotkey,
        mic_device: s.mic_device,
        launch_at_login: s.launch_at_login,
        theme: s.theme,
        stt_model: s.stt_model,
        stt_ready,
        agent_mode: s.agent_mode,
        reasoning_provider: s.reasoning_provider,
        reasoning_model: s.reasoning_model,
        velo_provider: s.velo_provider,
        velo_model: s.velo_model,
        openrouter_key: if settings::secret_read(OPENROUTER_KEY_SERVICE).is_some() {
            "stored".into()
        } else {
            "absent".into()
        },
        typesafe_key: if settings::secret_read(TYPESAFE_KEY_SERVICE).is_some() {
            "stored".into()
        } else {
            "absent".into()
        },
        runtime_status: apply_status(app),
        microphone_permission: permissions::status().as_str().into(),
        accessibility_permission: system_permissions::accessibility().as_str().into(),
        screen_recording_permission: system_permissions::screen_recording().as_str().into(),
        storage_path,
        technical_retention_days: s.technical_retention_days,
    }
}

pub fn emit_settings_changed(app: &AppHandle) {
    let _ = app.emit("settings://changed", full_settings_snapshot(app));
}

#[tauri::command]
pub fn get_full_settings(app: AppHandle) -> FullSettingsSnapshot {
    full_settings_snapshot(&app)
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

fn validate_ai_patch(patch: &AiConfigPatch) -> Result<(), String> {
    if let Some(provider) = &patch.reasoning_provider {
        if provider != "openrouter" {
            return Err("Deep Agent provider is fixed to OpenRouter".into());
        }
    }
    if let Some(provider) = &patch.velo_provider {
        if provider != "openrouter" && provider != "typesafe" {
            return Err("Velo provider must be OpenRouter or TypeSafe".into());
        }
    }
    if patch.reasoning_model.as_ref().is_some_and(|m| m.trim().is_empty()) {
        return Err("Deep Agent model ID cannot be empty".into());
    }
    if patch.velo_model.as_ref().is_some_and(|m| m.trim().is_empty()) {
        return Err("Velo model ID cannot be empty".into());
    }
    Ok(())
}

/// Persist desired AI configuration and only mark it ready after the restarted
/// sidecar answers a bounded health check. A failed restart leaves the desired
/// values intact so the user can correct and retry rather than being lied to
/// about a silently restored old runtime.
#[tauri::command]
pub async fn apply_ai_settings(
    app: AppHandle,
    patch: AiConfigPatch,
) -> Result<FullSettingsSnapshot, String> {
    validate_ai_patch(&patch)?;
    if crate::sani_core::is_run_live(&app) {
        return Err("Finish the current task before changing AI settings.".into());
    }
    {
        let settings_arc = app_state::settings(&app);
        let mut s = settings_arc.write();
        if let Some(v) = patch.reasoning_provider { s.reasoning_provider = v; }
        if let Some(v) = patch.reasoning_model { s.reasoning_model = v; }
        if let Some(v) = patch.velo_provider { s.velo_provider = v; }
        if let Some(v) = patch.velo_model { s.velo_model = v; }
        settings::save(&app, &s)?;
    }
    set_apply_status(&app, ApplyStatus::Saved);
    emit_settings_changed(&app);
    set_apply_status(&app, ApplyStatus::Applying);
    emit_settings_changed(&app);
    match crate::sani_core::reload_for_settings(app.clone()).await {
        Ok(()) => set_apply_status(&app, ApplyStatus::Ready),
        Err(error) => {
            // The error is deliberately not returned: it can contain runtime
            // transport detail. The stable state is enough for UI retry.
            log::warn!("settings runtime failed to apply: {}", error);
            set_apply_status(&app, ApplyStatus::FailedToApply);
        }
    }
    emit_settings_changed(&app);
    Ok(full_settings_snapshot(&app))
}

#[tauri::command]
pub async fn save_ai_config(app: AppHandle, patch: AiConfigPatch) -> Result<AiConfig, String> {
    apply_ai_settings(app.clone(), patch).await?;
    Ok(get_ai_config(app))
}

#[derive(Serialize)]
pub struct StoreResult {
    pub stored: bool,
    pub has_openrouter: bool,
    pub has_typesafe: bool,
    pub runtime_status: ApplyStatus,
}

/// Store a provider key in the OS credential store. Empty clears it. The value
/// is never returned or logged.
#[tauri::command]
pub async fn store_provider_key(
    app: AppHandle,
    provider: String,
    key: String,
) -> Result<StoreResult, String> {
    let service = provider_service(&provider)?;
    if crate::sani_core::is_run_live(&app) {
        return Err("Finish the current task before changing AI settings.".into());
    }
    let trimmed = key.trim();
    let stored = if trimmed.is_empty() {
        settings::secret_delete(service)
    } else {
        settings::secret_write(service, trimmed)
    };
    // A credential lives in the child environment only at spawn. Apply it
    // through the exact AI transaction rather than pretending a Keychain write
    // took effect in an already-running sidecar.
    set_apply_status(&app, ApplyStatus::Saved);
    emit_settings_changed(&app);
    set_apply_status(&app, ApplyStatus::Applying);
    emit_settings_changed(&app);
    match crate::sani_core::reload_for_settings(app.clone()).await {
        Ok(()) => set_apply_status(&app, ApplyStatus::Ready),
        Err(error) => {
            log::warn!("provider credential runtime failed to apply: {}", error);
            set_apply_status(&app, ApplyStatus::FailedToApply);
        }
    }
    emit_settings_changed(&app);
    Ok(StoreResult {
        stored,
        has_openrouter: settings::secret_read(OPENROUTER_KEY_SERVICE).is_some(),
        has_typesafe: settings::secret_read(TYPESAFE_KEY_SERVICE).is_some(),
        runtime_status: apply_status(&app),
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

/// Validate a Keychain credential without disclosing it. This is deliberately
/// separate from candidate validation so the UI can say "stored" without ever
/// receiving the stored secret back from native code.
#[tauri::command]
pub async fn validate_stored_provider_key(_app: AppHandle, provider: String) -> StoredKeyStatus {
    let Ok(service) = provider_service(&provider) else {
        return StoredKeyStatus::Invalid;
    };
    let Some(key) = settings::secret_read(service) else {
        return StoredKeyStatus::Absent;
    };
    match validate_provider_key(provider, key).await {
        KeyStatus::Connected { .. } => StoredKeyStatus::Connected,
        KeyStatus::Invalid => StoredKeyStatus::Invalid,
        KeyStatus::Offline => StoredKeyStatus::Offline,
    }
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StoredKeyStatus {
    Absent,
    Connected,
    Invalid,
    Offline,
}

async fn check_openrouter(key: &str) -> KeyStatus {
    let url = "https://openrouter.ai/api/v1/key";
    match client().get(url).bearer_auth(key).send().await {
        Ok(resp) => {
            let status = resp.status();
            if status.is_success() {
                let label = resp.json::<Value>().await.ok().and_then(|v| {
                    v.get("data")
                        .and_then(|d| d.get("label"))
                        .and_then(Value::as_str)
                        .map(String::from)
                });
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
    let Ok(resp) = req.send().await else {
        return Vec::new();
    };
    let Ok(body) = resp.json::<Value>().await else {
        return Vec::new();
    };
    let Some(data) = body.get("data").and_then(Value::as_array) else {
        return Vec::new();
    };
    let mut models: Vec<ModelOption> = data
        .iter()
        .filter_map(|m| {
            let id = m.get("id").and_then(Value::as_str)?;
            let name = m.get("name").and_then(Value::as_str).unwrap_or(id);
            Some(ModelOption {
                id: id.to_string(),
                name: name.to_string(),
            })
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

/// One truthful computer-control report: current OS permission queries plus a
/// live `system.status` request. No renderer infers readiness from a stale
/// toggle or fabricates an available runtime.
#[derive(Serialize)]
pub struct ComputerControlSnapshot {
    pub status: String,
    pub message: String,
    pub accessibility: String,
    pub screen_recording: String,
    pub restart_required: bool,
    pub runtime: String,
}

#[tauri::command]
pub async fn computer_control_snapshot(app: AppHandle) -> ComputerControlSnapshot {
    let accessibility_state = system_permissions::accessibility();
    let screen_recording_state = system_permissions::screen_recording();
    let accessibility = accessibility_state.as_str().to_string();
    let screen_recording = screen_recording_state.as_str().to_string();
    let restart_required = system_permissions::screen_recording_restart_required();
    let runtime = sani_core::core_status(app.clone()).await;
    let runtime_ok = runtime.is_ok();
    let (status, message) = if restart_required {
        ("restart_required", "Screen Recording was granted; restart Sani before computer control can use it.")
    } else if !accessibility_state.is_granted() || !screen_recording_state.is_granted() {
        ("permission_required", "Grant Accessibility and Screen Recording to enable computer control.")
    } else if !runtime_ok {
        ("unavailable", "Sani’s computer-control runtime is unavailable. Try restarting Sani.")
    } else {
        ("ready", "Computer control is ready.")
    };
    ComputerControlSnapshot { status: status.into(), message: message.into(), accessibility, screen_recording, restart_required, runtime: if runtime_ok { "available".into() } else { "unavailable".into() } }
}

#[tauri::command]
pub fn open_permission_settings(pane: String) {
    system_permissions::open_settings(&pane);
}

/// Kick the Accessibility prompt (adds Sani to the list so the toggle exists).
#[tauri::command]
pub fn request_accessibility() -> String {
    system_permissions::accessibility_prompt()
        .as_str()
        .to_string()
}

/// Kick the Screen Recording prompt.
#[tauri::command]
pub fn request_screen_recording() -> String {
    system_permissions::screen_recording_request()
        .as_str()
        .to_string()
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
    let done = |c: setup::Component| {
        snapshot
            .components
            .get(&c)
            .map(|r| r.status.is_done())
            .unwrap_or(false)
    };
    let mic = permissions::status().is_granted();
    let access = system_permissions::accessibility().is_granted()
        || system_permissions::screen_recording().is_granted();
    let creds = settings::secret_read(OPENROUTER_KEY_SERVICE).is_some()
        || settings::secret_read(TYPESAFE_KEY_SERVICE).is_some();

    let checks = vec![
        HealthCheck {
            label: "Local storage",
            ok: done(setup::Component::AppData) && done(setup::Component::Database),
            note: String::new(),
        },
        HealthCheck {
            label: "AI runtime",
            ok: done(setup::Component::Core),
            note: String::new(),
        },
        HealthCheck {
            label: "Voice",
            ok: done(setup::Component::SttRuntime),
            note: if done(setup::Component::SttModel) {
                String::new()
            } else {
                "downloads on first use".into()
            },
        },
        HealthCheck {
            label: "Computer control",
            ok: done(setup::Component::Cua),
            note: String::new(),
        },
        HealthCheck {
            label: "Permissions",
            ok: mic && access,
            note: String::new(),
        },
        HealthCheck {
            label: "Models",
            ok: creds,
            note: String::new(),
        },
    ];
    // Offline only matters for the network-backed "Models" check.
    let local_healthy = checks.iter().filter(|c| c.label != "Models").all(|c| c.ok);
    FinalHealth {
        local_healthy,
        network_only_issue: !creds,
        checks,
    }
}

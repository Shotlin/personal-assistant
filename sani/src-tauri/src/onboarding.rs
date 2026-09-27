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
    pub has_openrouter: bool,
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
    pub openrouter_key: String,
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
        openrouter_key: if settings::secret_read(OPENROUTER_KEY_SERVICE).is_some() {
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
        has_openrouter: settings::secret_read(OPENROUTER_KEY_SERVICE).is_some(),
    }
}

#[derive(Deserialize)]
pub struct AiConfigPatch {
    pub reasoning_provider: Option<String>,
    pub reasoning_model: Option<String>,
}

fn validate_ai_patch(patch: &AiConfigPatch) -> Result<(), String> {
    if let Some(provider) = &patch.reasoning_provider {
        if provider != "openrouter" {
            return Err("Deep Agent provider is fixed to OpenRouter".into());
        }
    }
    if patch
        .reasoning_model
        .as_ref()
        .is_some_and(|m| m.trim().is_empty())
    {
        return Err("Deep Agent model ID cannot be empty".into());
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
        if let Some(v) = patch.reasoning_provider {
            s.reasoning_provider = v;
        }
        if let Some(v) = patch.reasoning_model {
            s.reasoning_model = v;
        }
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
    pub runtime_status: ApplyStatus,
}

/// Store a replacement provider key in the OS credential store. Removal is a
/// separate confirmed user action; a blank field must never erase a key.
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
    if trimmed.is_empty() {
        return Err("Enter a replacement key. Removing a saved key is a separate confirmed action.".into());
    }
    let stored = settings::secret_write(service, trimmed);
    if !stored {
        return Err("Sani could not save the credential in Keychain.".into());
    }
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
        runtime_status: apply_status(&app),
    })
}

fn provider_service(provider: &str) -> Result<&'static str, String> {
    match provider {
        "openrouter" => Ok(OPENROUTER_KEY_SERVICE),
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
                // An arbitrary HTTP response is not authentication evidence.
                KeyStatus::Offline
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

/// One truthful computer-control report.
///
/// This page used to show Sani's macOS switches and the driver's as two separate
/// pairs, and that pairing is what made the app look like it was lying. The
/// driver is a child in Sani's own responsibility chain, so `check_permissions`
/// reports *Sani's* grants -- the driver has no switches of its own to be
/// checked. Two rows for one permission could therefore disagree, and did.
///
/// So the macOS truth is one pair of rows, and the driver reports what it
/// actually is: a process, a mode, an endpoint, a session count, and one honest
/// word for what its probe answered. `status` and `runtime` are both derived
/// from the same `ControlState`, so they cannot contradict each other.
#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct ComputerControlSnapshot {
    pub status: String,
    pub message: String,
    pub accessibility: String,
    pub screen_recording: String,
    /// Whose grants these are, and why the same two values cover the driver.
    pub permission_authority: String,
    pub driver_running: bool,
    pub driver_pid: Option<u32>,
    pub driver_endpoint: String,
    pub driver_mode: String,
    /// granted | denied | policy_locked | unanswered | unreachable | unrecognized
    pub driver_probe: String,
    /// The driver's own words, whenever the probe could not answer.
    pub driver_detail: String,
    /// Live driver sessions. `None` is "could not ask", never a quiet zero.
    pub active_sessions: Option<usize>,
    pub restart_required: bool,
    pub runtime: String,
    pub app_path: String,
}

const PERMISSION_AUTHORITY: &str =
    "Sani.app -- the embedded driver runs inside Sani’s own responsibility chain, \
     so it shares these two grants and has no switches of its own";

/// The closed set of states this page may report.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ControlState {
    Ready,
    RestartRequired,
    PermissionRequired,
    /// No packaged driver, or none was ever started in this process.
    DriverMissing,
    /// Sani's own daemon is not accepting connections on its endpoint.
    DriverStopped,
    /// The daemon is running in a mode Sani did not launch it in.
    WrongMode,
    /// The driver answered, and the answer was a denial.
    NotAuthorized,
    /// The bounded capability policy idled out and is refusing every call.
    PolicyLocked,
    /// The sidecar itself could not be reached.
    SidecarUnavailable,
}

/// Decide the state from what was observed. Ordering is the whole point: a
/// granted Mac with a live daemon can only read `not_authorized` when the driver
/// itself answered a denial, never because a probe timed out.
fn control_state_of(
    grants_granted: bool,
    restart_required: bool,
    driver_running: bool,
    sidecar_reachable: bool,
    found: bool,
    posture_ok: bool,
    probe: &sani_core::DriverProbe,
) -> ControlState {
    if restart_required {
        return ControlState::RestartRequired;
    }
    if !grants_granted {
        return ControlState::PermissionRequired;
    }
    if !sidecar_reachable {
        return ControlState::SidecarUnavailable;
    }
    if !found {
        return ControlState::DriverMissing;
    }
    if !posture_ok {
        return ControlState::WrongMode;
    }
    if !driver_running {
        return ControlState::DriverStopped;
    }
    match probe {
        sani_core::DriverProbe::Answered {
            accessibility,
            screen_recording,
        } => {
            if *accessibility && *screen_recording {
                ControlState::Ready
            } else {
                ControlState::NotAuthorized
            }
        }
        sani_core::DriverProbe::PolicyLocked { .. } => ControlState::PolicyLocked,
        // Embedded mode attributes the daemon to its host, and Sani has just read
        // its own grants as granted. An unanswered probe is a missing answer, not
        // a denial -- and the driver cannot be granted while Sani is not.
        _ => ControlState::Ready,
    }
}

fn status_word(state: ControlState) -> &'static str {
    match state {
        ControlState::Ready => "ready",
        ControlState::RestartRequired => "restart_required",
        ControlState::PermissionRequired => "permission_required",
        ControlState::DriverMissing => "driver_missing",
        ControlState::DriverStopped => "driver_stopped",
        ControlState::WrongMode => "wrong_mode",
        ControlState::NotAuthorized => "driver_permission_required",
        ControlState::PolicyLocked => "policy_locked",
        ControlState::SidecarUnavailable => "unavailable",
    }
}

fn runtime_word(state: ControlState) -> &'static str {
    match state {
        ControlState::Ready => "connected",
        ControlState::RestartRequired => "restart_required",
        ControlState::PermissionRequired => "waiting_for_permission",
        ControlState::DriverMissing => "missing",
        ControlState::DriverStopped => "disconnected",
        ControlState::WrongMode => "wrong_mode",
        ControlState::NotAuthorized => "not_authorized",
        ControlState::PolicyLocked => "policy_locked",
        ControlState::SidecarUnavailable => "unavailable",
    }
}

fn control_message(state: ControlState) -> &'static str {
    match state {
        ControlState::Ready => "Computer control is ready.",
        ControlState::RestartRequired => {
            "Screen Recording was granted; Sani must restart before computer control can use it."
        }
        ControlState::PermissionRequired => {
            "Grant Accessibility and Screen Recording to Sani to enable computer control. \
             The driver shares them, so there is nothing else to switch on."
        }
        ControlState::DriverMissing => {
            "Sani’s packaged computer-control driver is missing from this install."
        }
        ControlState::DriverStopped => {
            "Sani’s private driver is not accepting connections. The watchdog reclaims \
             its endpoint and starts a replacement; restart Sani if this persists."
        }
        ControlState::WrongMode => {
            "The running driver is not in the mode Sani launched it in, so its authority is \
             unknown. Sani will replace it with a correctly started one."
        }
        ControlState::NotAuthorized => {
            "The driver answered that macOS will not let it act, while Sani reads itself as \
             granted. That means an older driver generation is answering -- Sani reclaims the \
             endpoint and starts its own."
        }
        ControlState::PolicyLocked => {
            "The driver’s capability policy idled out and refuses every action, including \
             its own permission probe. This is not a revoked macOS permission; Sani restarts \
             the driver to re-approve it."
        }
        ControlState::SidecarUnavailable => {
            "Sani’s assistant runtime could not be reached, so computer control cannot be \
             reported. Check the runtime status below."
        }
    }
}

/// The `.app` bundle this process was launched from, or the executable path.
fn running_bundle_path() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|exe| {
            exe.ancestors()
                .find(|p| p.extension().map(|e| e == "app").unwrap_or(false))
                .map(|bundle| bundle.to_string_lossy().into_owned())
        })
        .unwrap_or_else(|| String::from("unknown"))
}

#[tauri::command]
pub async fn computer_control_snapshot(app: AppHandle) -> ComputerControlSnapshot {
    let accessibility_state = system_permissions::accessibility();
    let screen_recording_state = system_permissions::screen_recording();
    let grants_granted = accessibility_state.is_granted() && screen_recording_state.is_granted();
    let restart_required = system_permissions::screen_recording_restart_required();
    // A crashed or orphaned driver leaves Sani's private endpoint behind, and the
    // driver refuses to bind over it; this is the repair, not a poll.
    let recovery = sani_core::recover_embedded_cua_driver(&app).await;
    let runtime = sani_core::core_status(app.clone()).await;
    let probe = sani_core::driver_probe(app.clone()).await;
    let sessions = sani_core::driver_session_count(app.clone()).await;
    // What the daemon says it is running, falling back to what Sani launched when
    // the daemon cannot be asked at all.
    let observed_mode = sani_core::driver_mode(app.clone()).await;
    let (driver_pid, driver_endpoint, driver_running) = match sani_core::driver_generation(&app) {
        Some((pid, endpoint, running)) => (pid, endpoint, running),
        None => (None, String::new(), false),
    };
    let sidecar_reachable = runtime.is_ok();
    let payload = runtime.as_ref().ok();
    let found = payload
        .and_then(|value| value.get("driver"))
        .and_then(|driver| driver.get("found"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let posture_ok = payload
        .and_then(|value| value.get("driver"))
        .and_then(|driver| driver.get("posture_ok"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let state = if recovery.is_err() && !driver_running {
        ControlState::DriverStopped
    } else {
        control_state_of(
            grants_granted,
            restart_required,
            driver_running,
            sidecar_reachable,
            found,
            posture_ok,
            &probe,
        )
    };
    ComputerControlSnapshot {
        status: status_word(state).to_string(),
        message: control_message(state).to_string(),
        accessibility: accessibility_state.as_str().to_string(),
        screen_recording: screen_recording_state.as_str().to_string(),
        permission_authority: PERMISSION_AUTHORITY.to_string(),
        driver_running,
        driver_pid,
        driver_endpoint,
        driver_mode: observed_mode
            .unwrap_or_else(|| sani_core::staged_permission_mode(&app).as_str().to_string()),
        driver_probe: probe.label().to_string(),
        driver_detail: match &probe {
            sani_core::DriverProbe::Answered { .. } => String::new(),
            sani_core::DriverProbe::PolicyLocked { detail }
            | sani_core::DriverProbe::Unreachable { detail }
            | sani_core::DriverProbe::Malformed { detail } => detail.clone(),
            sani_core::DriverProbe::Timeout => {
                "the driver did not answer the read-only probe within its budget".to_string()
            }
        },
        active_sessions: sessions,
        restart_required,
        runtime: runtime_word(state).to_string(),
        app_path: running_bundle_path(),
    }
}

#[cfg(test)]
mod control_tests {
    use super::{
        control_state_of, runtime_word, status_word, ControlState,
    };
    use crate::sani_core::DriverProbe;

    fn answered(a: bool, s: bool) -> DriverProbe {
        DriverProbe::Answered {
            accessibility: a,
            screen_recording: s,
        }
    }

    /// Grants in, daemon up and running, sidecar reachable, correct mode.
    fn healthy(probe: DriverProbe) -> ControlState {
        control_state_of(true, false, true, true, true, true, &probe)
    }

    #[test]
    fn a_granted_mac_with_a_live_daemon_is_never_reported_as_unauthorized() {
        // The exact contradiction the old page could show: System Settings on,
        // Sani saying `not_authorized` forever. An unanswered probe, a timeout or
        // a malformed answer may not produce it, because embedded mode attributes
        // the daemon to this host and this host's grants were just read.
        for probe in [
            DriverProbe::Timeout,
            DriverProbe::Unreachable {
                detail: "socket closed".into(),
            },
            DriverProbe::Malformed {
                detail: "not json".into(),
            },
        ] {
            assert_eq!(healthy(probe), ControlState::Ready);
        }
        assert_eq!(
            healthy(answered(true, true)),
            ControlState::Ready,
            "explicitly granted"
        );
    }

    #[test]
    fn only_a_denial_the_driver_itself_answered_reads_as_not_authorized() {
        assert_eq!(
            healthy(answered(false, true)),
            ControlState::NotAuthorized
        );
        assert_eq!(
            healthy(answered(true, false)),
            ControlState::NotAuthorized
        );
    }

    #[test]
    fn a_policy_lockout_is_its_own_state_never_a_permission_one() {
        let locked = healthy(DriverProbe::PolicyLocked {
            detail: "Policy loading error: capability manifest idle timeout exceeded".into(),
        });
        assert_eq!(locked, ControlState::PolicyLocked);
        assert_eq!(status_word(locked), "policy_locked");
        assert_ne!(runtime_word(locked), runtime_word(ControlState::NotAuthorized));
    }

    #[test]
    fn each_state_has_one_distinct_pair_of_words() {
        let states = [
            ControlState::Ready,
            ControlState::RestartRequired,
            ControlState::PermissionRequired,
            ControlState::DriverMissing,
            ControlState::DriverStopped,
            ControlState::WrongMode,
            ControlState::NotAuthorized,
            ControlState::PolicyLocked,
            ControlState::SidecarUnavailable,
        ];
        for state in states {
            assert!(!status_word(state).is_empty());
            assert!(!runtime_word(state).is_empty());
            assert_eq!(
                states.iter().filter(|other| runtime_word(**other) == runtime_word(state)).count(),
                1,
                "{state:?} shares its runtime word"
            );
        }
    }

    #[test]
    fn waiting_for_a_grant_is_never_called_a_denial() {
        // "Sani says denied while System Settings says on" was the reported
        // symptom. A grant that has not been given is a different fact from a
        // driver answering that macOS refused it, and the two words differ.
        assert_ne!(
            runtime_word(ControlState::PermissionRequired),
            runtime_word(ControlState::NotAuthorized)
        );
        assert_eq!(status_word(ControlState::PermissionRequired), "permission_required");
    }

    #[test]
    fn priority_runs_from_the_outermost_cause_inward() {
        // A restart is required even if everything else looks fine, and a missing
        // macOS grant outranks a daemon that has not started yet.
        assert_eq!(
            control_state_of(true, true, true, true, true, true, &answered(true, true)),
            ControlState::RestartRequired
        );
        assert_eq!(
            control_state_of(false, false, false, false, false, false, &DriverProbe::Timeout),
            ControlState::PermissionRequired
        );
        assert_eq!(
            control_state_of(true, false, true, false, true, true, &answered(true, true)),
            ControlState::SidecarUnavailable
        );
        assert_eq!(
            control_state_of(true, false, true, true, false, false, &DriverProbe::Timeout),
            ControlState::DriverMissing
        );
        assert_eq!(
            control_state_of(true, false, true, true, true, false, &DriverProbe::Timeout),
            ControlState::WrongMode
        );
        assert_eq!(
            control_state_of(true, false, false, true, true, true, &DriverProbe::Timeout),
            ControlState::DriverStopped
        );
    }
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
            let target = bundle
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| exe.clone());
            // `open` activates an application that is already running instead of
            // relaunching it, so issuing it before this process has gone away
            // races the shutdown: Sani quits and nothing comes back, leaving the
            // just-granted permission unapplied. Wait for our own pid to exit,
            // then open the bundle -- which also keeps it to one instance.
            let script = r#"while kill -0 "$1" 2>/dev/null; do sleep 0.1; done; open "$2""#;
            let _ = std::process::Command::new("/bin/sh")
                .arg("-c")
                .arg(script)
                .arg(std::process::id().to_string())
                .arg(target)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn();
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
    let creds = settings::secret_read(OPENROUTER_KEY_SERVICE).is_some();

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

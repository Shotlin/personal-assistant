//! Sani first-run setup: the authoritative, persistent setup state machine.
//!
//! React renders *this*; it never invents progress. Every percentage on the
//! "Preparing Sani" screen is derived from real, weighted, idempotent
//! operations that each independently verify their own outcome. Closing Sani
//! mid-run is safe: state is persisted after every transition, so the next
//! launch resumes from the last valid step rather than restarting.
//!
//! Two vocabularies live here and must not be confused:
//!   * the *internal* component (SQLite, sani-core, STT sidecar, CUA, …), and
//!   * the *friendly* label the normal user sees (Local storage, AI runtime,
//!     Voice engine, Computer control, …).
//! Technical strings are only ever surfaced under "Show details".

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

/// Bumped whenever the setup model gains components. An older persisted state
/// is migrated in place (only new components are added; completed ones stay),
/// so an upgrade never replays full onboarding.
pub const SETUP_SCHEMA_VERSION: u32 = 1;

/// Frontend event carrying one setup transition.
pub const PROGRESS_EVENT: &str = "sani://setup-progress";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Component {
    AppData,
    Database,
    Core,
    Skills,
    SttRuntime,
    SttModel,
    Cua,
}

impl Component {
    pub fn all() -> [Component; 7] {
        [
            Component::AppData,
            Component::Database,
            Component::Core,
            Component::Skills,
            Component::SttRuntime,
            Component::SttModel,
            Component::Cua,
        ]
    }

    /// Stable wire key (matches the snake_case serde name).
    pub fn key(&self) -> &'static str {
        match self {
            Component::AppData => "app_data",
            Component::Database => "database",
            Component::Core => "core",
            Component::Skills => "skills",
            Component::SttRuntime => "stt_runtime",
            Component::SttModel => "stt_model",
            Component::Cua => "cua",
        }
    }

    /// User-facing group name shown on the progress list.
    pub fn friendly(&self) -> &'static str {
        match self {
            Component::AppData => "Application storage",
            Component::Database => "Local storage",
            Component::Core => "AI runtime",
            Component::Skills => "Assistant capabilities",
            Component::SttRuntime => "Voice engine",
            Component::SttModel => "Voice model",
            Component::Cua => "Computer control",
        }
    }

    /// Caption while the step is running (honest: never implies done).
    pub fn running_message(&self) -> &'static str {
        match self {
            Component::AppData => "Setting up application storage…",
            Component::Database => "Preparing local storage…",
            Component::Core => "Setting up AI runtime…",
            Component::Skills => "Installing assistant capabilities…",
            Component::SttRuntime => "Preparing voice engine…",
            Component::SttModel => "Checking voice model…",
            Component::Cua => "Preparing computer control…",
        }
    }

    /// Relative cost of each step; sums to 100 so the bar is weighted, not timed.
    pub fn weight(&self) -> u32 {
        match self {
            Component::AppData => 8,
            Component::Database => 20,
            Component::Core => 22,
            Component::Skills => 10,
            Component::SttRuntime => 18,
            Component::SttModel => 10,
            Component::Cua => 12,
        }
    }

    fn from_key(key: &str) -> Option<Component> {
        Component::all().into_iter().find(|c| c.key() == key)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ComponentStatus {
    Pending,
    Running,
    Complete,
    Failed,
    /// Satisfied without user action but not required for a healthy install
    /// (e.g. the voice model downloads itself on first use).
    Skipped,
}

impl ComponentStatus {
    /// Counts as done for progress purposes.
    pub fn is_done(&self) -> bool {
        matches!(self, ComponentStatus::Complete | ComponentStatus::Skipped)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComponentRecord {
    pub status: ComponentStatus,
    /// Advanced-only diagnostic (never shown to a normal user).
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub error: Option<String>,
}

impl ComponentRecord {
    fn pending() -> Self {
        Self {
            status: ComponentStatus::Pending,
            detail: String::new(),
            error: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetupState {
    pub schema_version: u32,
    pub onboarding_complete: bool,
    #[serde(default)]
    pub current_stage: String,
    pub components: BTreeMap<Component, ComponentRecord>,
    #[serde(default)]
    pub last_error: Option<String>,
    #[serde(default)]
    pub updated_at: i64,
}

impl Default for SetupState {
    fn default() -> Self {
        let mut components = BTreeMap::new();
        for c in Component::all() {
            components.insert(c, ComponentRecord::pending());
        }
        Self {
            schema_version: SETUP_SCHEMA_VERSION,
            onboarding_complete: false,
            current_stage: "welcome".into(),
            components,
            last_error: None,
            updated_at: 0,
        }
    }
}

impl SetupState {
    /// Weighted completion across the auto-setup steps (0..=100).
    pub fn percent(&self) -> u32 {
        let done: u32 = Component::all()
            .iter()
            .filter(|c| {
                self.components
                    .get(c)
                    .map(|r| r.status.is_done())
                    .unwrap_or(false)
            })
            .map(|c| c.weight())
            .sum();
        done.min(100)
    }

    pub fn local_setup_complete(&self) -> bool {
        Component::all().iter().all(|c| {
            self.components
                .get(c)
                .map(|r| r.status.is_done())
                .unwrap_or(false)
        })
    }

    /// Carry a persisted state forward on schema upgrade: keep every existing
    /// component, add any newly-introduced ones as Pending. Never discards work.
    fn migrate(mut self) -> Self {
        for c in Component::all() {
            self.components
                .entry(c)
                .or_insert_with(ComponentRecord::pending);
        }
        self.schema_version = SETUP_SCHEMA_VERSION;
        self
    }
}

/// Wire payload for one transition.
#[derive(Debug, Clone, Serialize)]
pub struct SetupProgressEvent {
    pub step: &'static str,
    pub friendly: &'static str,
    pub status: ComponentStatus,
    pub percent: u32,
    pub message: String,
    pub detail: String,
}

pub struct SetupManager {
    pub state: Mutex<SetupState>,
    running: AtomicBool,
}

impl Default for SetupManager {
    fn default() -> Self {
        Self {
            state: Mutex::new(SetupState::default()),
            running: AtomicBool::new(false),
        }
    }
}

pub type SharedSetup = Arc<SetupManager>;

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

// -------------------------------------------------------------- persistence

fn state_path(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("setup_state.json"))
}

pub fn load(app: &AppHandle) -> SetupState {
    match state_path(app) {
        Some(path) if path.exists() => match std::fs::read_to_string(&path) {
            Ok(raw) => match serde_json::from_str::<SetupState>(&raw) {
                Ok(state) => state.migrate(),
                // Corrupt state must never lock the user out: keep a copy for
                // diagnostics and recover to a fresh (incomplete) state.
                Err(err) => {
                    log::error!("[setup] state unreadable ({err}); recovering fresh");
                    let _ = std::fs::rename(&path, path.with_extension("json.corrupt"));
                    SetupState::default()
                }
            },
            Err(_) => SetupState::default(),
        },
        _ => SetupState::default(),
    }
}

fn persist(app: &AppHandle, state: &SetupState) {
    if let Some(path) = state_path(app) {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(raw) = serde_json::to_string_pretty(state) {
            // Write-then-rename so a crash mid-write cannot truncate the file.
            let tmp = path.with_extension("json.tmp");
            if std::fs::write(&tmp, raw).is_ok() {
                let _ = std::fs::rename(&tmp, &path);
            }
        }
    }
}

/// Persist a mutated state (used by the onboarding command surface when it
/// records stage transitions and the final completion flag).
pub fn persist_state(app: &AppHandle, state: &SetupState) {
    persist(app, state);
}

/// Initialize the managed state from disk (call once during setup()).
pub fn init(app: &AppHandle) -> SharedSetup {
    let manager = SetupManager {
        state: Mutex::new(load(app)),
        running: AtomicBool::new(false),
    };
    let shared: SharedSetup = Arc::new(manager);
    app.manage(shared.clone());
    shared
}

// --------------------------------------------------------------- operations

struct Outcome {
    status: ComponentStatus,
    detail: String,
    error: Option<String>,
}

impl Outcome {
    fn complete(detail: impl Into<String>) -> Self {
        Self {
            status: ComponentStatus::Complete,
            detail: detail.into(),
            error: None,
        }
    }
    fn skipped(detail: impl Into<String>) -> Self {
        Self {
            status: ComponentStatus::Skipped,
            detail: detail.into(),
            error: None,
        }
    }
    fn failed(err: impl Into<String>) -> Self {
        let err = err.into();
        Self {
            status: ComponentStatus::Failed,
            detail: String::new(),
            error: Some(err),
        }
    }
}

/// Walk up from the executable and cwd looking for a directory that looks like
/// the Sani source checkout (has both `pyproject.toml` and `src/assistant`).
pub(crate) fn find_repo_root() -> Option<PathBuf> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.extend(exe.ancestors().skip(1).take(12).map(Path::to_path_buf));
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.extend(cwd.ancestors().take(12).map(Path::to_path_buf));
    }
    starts
        .into_iter()
        .find(|dir| dir.join("pyproject.toml").exists() && dir.join("src/assistant").is_dir())
}

/// Absolute path to a repository file the runtime needs at startup (the CUA
/// capability manifest, the skills tree). Packaged builds resolve resources
/// from the bundle; a dev build falls back to the checkout.
pub(crate) fn resource_path(app: &AppHandle, rel: &str) -> Option<PathBuf> {
    resource_candidates(app, rel)
        .into_iter()
        .find(|candidate| candidate.exists())
}

/// Interpreter that can run the `assistant` package: an explicit override, then
/// the checkout's own venv. Shared by the setup probe and the real launch.
pub(crate) fn core_python() -> Option<PathBuf> {
    if let Some(override_path) = std::env::var("SANI_CORE_PYTHON").ok().map(PathBuf::from) {
        if override_path.exists() {
            return Some(override_path);
        }
    }
    let root = find_repo_root()?;
    [
        ".venv/bin/python3",
        ".venv/bin/python",
        ".venv/Scripts/python.exe",
    ]
    .iter()
    .map(|rel| root.join(rel))
    .find(|path| path.exists())
}

fn resource_candidates(app: &AppHandle, rel: &str) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        out.push(res.join(rel));
    }
    if let Some(root) = find_repo_root() {
        out.push(root.join(rel));
    }
    out
}

/// Run one external command with a wall-clock timeout; `None` = timed out or
/// could not spawn, `Some(true)` = exit code 0.
fn probe(cmd: &str, args: &[&str], envs: &[(&str, &str)], timeout: Duration) -> Option<bool> {
    let mut command = Command::new(cmd);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    for (k, v) in envs {
        command.env(k, v);
    }
    let mut child = command.spawn().ok()?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status.success()),
            Ok(None) => {
                if start.elapsed() > timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                std::thread::sleep(Duration::from_millis(30));
            }
            Err(_) => return None,
        }
    }
}

fn create_dir_result(dir: &Path) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))
}

fn op_app_data(app: &AppHandle) -> Outcome {
    let paths = app.path();
    let mut created: Vec<PathBuf> = Vec::new();
    let bases = [
        paths.app_data_dir(),
        paths.app_config_dir(),
        paths.app_log_dir(),
        paths.app_cache_dir(),
    ];
    for base in bases.into_iter().flatten() {
        for sub in ["logs", "artifacts", "cache"] {
            let dir = base.join(sub);
            if let Err(e) = create_dir_result(&dir) {
                return Outcome::failed(e);
            }
            created.push(dir);
        }
    }
    if created.is_empty() {
        return Outcome::failed("no application-data location available");
    }
    Outcome::complete(format!("{} directories ready", created.len()))
}

fn op_database(app: &AppHandle) -> Outcome {
    let db_path = app
        .path()
        .app_data_dir()
        .map(|dir| dir.join("sani-history.db"))
        .map_err(|e| e.to_string());
    let db_path = match db_path {
        Ok(p) => p,
        Err(e) => return Outcome::failed(e),
    };
    match crate::history::History::open(db_path.clone()) {
        Ok(history) => {
            // Prove read/write with a throwaway row so a locked/corrupt db is
            // caught here, not on the first real turn.
            let probe_id = format!("__setup_probe_{}", uuid::Uuid::new_v4());
            let now = now_ms();
            let result = history
                .create_conversation(&probe_id, "setup-probe", now)
                .and_then(|_| {
                    history
                        .get_conversation(&probe_id)?
                        .map(|_| ())
                        .ok_or_else(|| "probe read failed".to_string())
                })
                .and_then(|_| history.delete_conversation(&probe_id));
            match result {
                Ok(()) => Outcome::complete(format!("{} ready (WAL)", db_path.display())),
                Err(e) => Outcome::failed(e),
            }
        }
        Err(e) => Outcome::failed(e),
    }
}

fn op_core(_app: &AppHandle) -> Outcome {
    // Same discovery the real launch uses, so "setup says ready" and "the
    // runtime actually starts" can never disagree. Confirms `assistant.core`
    // is importable *without* executing it (find_spec).
    let Some(python) = core_python() else {
        // Packaged builds will resolve a bundled interpreter here; until then,
        // an absent runtime is an honest, retryable failure.
        return Outcome::failed("AI runtime not found (no bundled interpreter)");
    };
    let python_str = python.to_string_lossy().to_string();
    let code = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('assistant.core') else 2)";
    let ok = match find_repo_root().map(|root| root.join("src")) {
        Some(src_dir) => probe(
            &python_str,
            &["-c", code],
            &[("PYTHONPATH", &src_dir.to_string_lossy())],
            Duration::from_secs(15),
        ),
        None => probe(&python_str, &["-c", code], &[], Duration::from_secs(15)),
    };
    match ok {
        Some(true) => Outcome::complete(format!("sani-core importable ({python_str})")),
        Some(false) => Outcome::failed("AI runtime could not start"),
        None => Outcome::failed("AI runtime check timed out"),
    }
}

fn op_skills(app: &AppHandle) -> Outcome {
    for candidate in resource_candidates(app, "src/assistant/skills")
        .into_iter()
        .chain(resource_candidates(app, "skills"))
    {
        if candidate.is_dir() {
            let count = std::fs::read_dir(&candidate)
                .map(|rd| rd.filter_map(|e| e.ok()).count())
                .unwrap_or(0);
            if count > 0 {
                return Outcome::complete(format!("skills available ({count} packages)"));
            }
        }
    }
    Outcome::failed("assistant capabilities not found")
}

fn op_stt_runtime(app: &AppHandle) -> Outcome {
    match crate::speech::locate_for_setup(app) {
        Ok(detail) => Outcome::complete(detail),
        Err(e) => Outcome::failed(e),
    }
}

/// Resolve the Moonshine Voice model cache root exactly as the sidecar does:
/// `MOONSHINE_VOICE_CACHE` overrides, otherwise the platform user-cache dir for
/// app name `moonshine_voice` (macOS: ~/Library/Caches/moonshine_voice).
pub(crate) fn moonshine_cache_root() -> Option<PathBuf> {
    if let Ok(v) = std::env::var("MOONSHINE_VOICE_CACHE") {
        if !v.trim().is_empty() {
            return Some(PathBuf::from(v));
        }
    }
    #[cfg(target_os = "macos")]
    let base = std::env::var("HOME")
        .ok()
        .map(|h| PathBuf::from(h).join("Library/Caches/moonshine_voice"));
    #[cfg(target_os = "linux")]
    let base = match std::env::var("XDG_CACHE_HOME") {
        Ok(xdg) => Some(PathBuf::from(xdg).join("moonshine_voice")),
        Err(_) => std::env::var("HOME")
            .ok()
            .map(|h| PathBuf::from(h).join(".cache/moonshine_voice")),
    };
    #[cfg(windows)]
    let base = std::env::var("LOCALAPPDATA")
        .ok()
        .map(|l| PathBuf::from(l).join("moonshine_voice").join("Cache"));
    #[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
    let base: Option<PathBuf> = None;
    base
}

/// Recursively collect the file names present under `dir` (bounded depth), so a
/// partial download is never mistaken for a complete model.
fn model_files(dir: &Path, depth: u8, out: &mut Vec<String>) {
    if depth == 0 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            model_files(&path, depth - 1, out);
        } else if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            out.push(name.to_string());
        }
    }
}

/// Whether the sidecar's owned cache contains a complete Moonshine model.
/// This is intentionally conservative: a partial download is always treated
/// as absent, so the model loader can safely resume it on next use.
pub(crate) fn voice_model_cached(model: &str) -> bool {
    let Some(root) = moonshine_cache_root() else {
        return false;
    };
    let model_dir = root.join("download.moonshine.ai").join("model").join(model);
    if !model_dir.is_dir() {
        return false;
    }
    let mut files = Vec::new();
    model_files(&model_dir, 3, &mut files);
    files.iter().any(|f| f == "encoder.ort") && files.iter().any(|f| f == "tokenizer.bin")
}

/// The configured voice model id (settings.stt_model), never empty.
fn configured_voice_model(app: &AppHandle) -> String {
    let m = crate::app_state::settings(app).read().stt_model.clone();
    if m.trim().is_empty() {
        "small-streaming-en".to_string()
    } else {
        m
    }
}

fn op_stt_model(app: &AppHandle) -> Outcome {
    // The model self-downloads on first real listen (with live byte progress
    // inside the Voice stage). Here we only *detect* an already-cached model so
    // the step reflects reality instead of always claiming a pending download.
    let model = configured_voice_model(app);
    let note = format!("{} downloads on first use", model);
    if voice_model_cached(&model) {
        Outcome::complete(format!("{} · cached", model))
    } else {
        // Present but incomplete: do not mark it ready — first use resumes it.
        Outcome::skipped(note)
    }
}

fn op_cua(app: &AppHandle) -> Outcome {
    // The user never sees "CUA"; locally we verify the bounded integration is
    // packaged (the reviewed capability manifest resolves). OS-level control is
    // granted later through Accessibility / Screen Recording permissions.
    for candidate in resource_candidates(app, "config/cua-capabilities.yaml")
        .into_iter()
        .chain(resource_candidates(app, "cua-capabilities.yaml"))
    {
        if candidate.is_file() {
            return Outcome::complete("computer control integration ready");
        }
    }
    // Non-fatal: computer control becomes usable once permissions are granted.
    Outcome::skipped("computer control finishes after permissions")
}

fn run_component(app: &AppHandle, component: Component) -> Outcome {
    match component {
        Component::AppData => op_app_data(app),
        Component::Database => op_database(app),
        Component::Core => op_core(app),
        Component::Skills => op_skills(app),
        Component::SttRuntime => op_stt_runtime(app),
        Component::SttModel => op_stt_model(app),
        Component::Cua => op_cua(app),
    }
}

// ----------------------------------------------------------------- driving

fn emit_progress(app: &AppHandle, component: Component, status: ComponentStatus) {
    let (percent, detail) = {
        let mgr = app.state::<SharedSetup>();
        let state = mgr.state.lock();
        (
            state.percent(),
            state
                .components
                .get(&component)
                .map(|r| r.detail.clone())
                .unwrap_or_default(),
        )
    };
    let message = match status {
        ComponentStatus::Running => component.running_message().to_string(),
        ComponentStatus::Complete => format!("{} ready", component.friendly()),
        ComponentStatus::Failed => format!("{} couldn't finish", component.friendly()),
        _ => String::new(),
    };
    let event = SetupProgressEvent {
        step: component.key(),
        friendly: component.friendly(),
        status,
        percent,
        message,
        detail,
    };
    if let Err(err) = app.emit(PROGRESS_EVENT, &event) {
        log::debug!("[setup] emit failed: {err}");
    }
}

/// Execute a single component and persist its result. Marks Running first so the
/// UI shows a spinner even for fast steps.
fn execute(app: &AppHandle, component: Component) {
    {
        let mgr = app.state::<SharedSetup>();
        mgr.state.lock().components.insert(
            component,
            ComponentRecord {
                status: ComponentStatus::Running,
                detail: String::new(),
                error: None,
            },
        );
    }
    emit_progress(app, component, ComponentStatus::Running);

    let outcome = run_component(app, component);
    let next_error = match outcome.status {
        ComponentStatus::Failed => outcome.error.clone(),
        _ => None,
    };

    {
        let mgr = app.state::<SharedSetup>();
        let mut state = mgr.state.lock();
        state.components.insert(
            component,
            ComponentRecord {
                status: outcome.status,
                detail: outcome.detail,
                error: outcome.error,
            },
        );
        if matches!(outcome.status, ComponentStatus::Failed) {
            state.last_error = next_error;
        }
        state.updated_at = now_ms();
        persist(app, &state);
    }
    emit_progress(app, component, outcome.status);
}

/// Owned handle to the setup manager, cloned out of Tauri state so it can move
/// onto a worker thread (`State<'_>` borrows the app and is not `'static`).
fn manager(app: &AppHandle) -> SharedSetup {
    app.state::<SharedSetup>().inner().clone()
}

/// Begin (or resume) the auto-setup run. Idempotent: components already done are
/// skipped, so this doubles as the "Finishing Sani setup…" resume path.
pub fn run_local_setup(app: &AppHandle) {
    let mgr = manager(app);
    if mgr.running.swap(true, Ordering::SeqCst) {
        log::info!("[setup] run already in progress; ignoring re-entry");
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        for component in Component::all() {
            let done = {
                let state = mgr.state.lock();
                state
                    .components
                    .get(&component)
                    .map(|r| r.status.is_done())
                    .unwrap_or(false)
            };
            if !done {
                execute(&handle, component);
            }
        }
        mgr.running.store(false, Ordering::SeqCst);
        let _ = handle.emit("sani://setup-stage", "local_complete");
    });
}

/// Retry a single failed subsystem without restarting the whole setup.
pub fn retry_component(app: &AppHandle, key: &str) -> Result<(), String> {
    let component =
        Component::from_key(key).ok_or_else(|| format!("unknown setup step '{key}'"))?;
    let mgr = manager(app);
    if mgr.running.swap(true, Ordering::SeqCst) {
        return Err("setup is still running; try again in a moment".to_string());
    }
    {
        let mut state = mgr.state.lock();
        state
            .components
            .insert(component, ComponentRecord::pending());
        persist(app, &state);
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        execute(&handle, component);
        mgr.running.store(false, Ordering::SeqCst);
    });
    Ok(())
}

/// Snapshot for the frontend (drives initial render + the details drawer).
pub fn snapshot(app: &AppHandle) -> SetupState {
    manager(app).state.lock().clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weights_sum_to_one_hundred() {
        assert_eq!(
            Component::all().iter().map(|c| c.weight()).sum::<u32>(),
            100
        );
    }

    #[test]
    fn fresh_state_is_zero_percent() {
        assert_eq!(SetupState::default().percent(), 0);
        assert!(!SetupState::default().local_setup_complete());
    }

    #[test]
    fn all_complete_reaches_one_hundred() {
        let mut state = SetupState::default();
        for c in Component::all() {
            state.components.insert(
                c,
                ComponentRecord {
                    status: ComponentStatus::Complete,
                    detail: String::new(),
                    error: None,
                },
            );
        }
        assert_eq!(state.percent(), 100);
        assert!(state.local_setup_complete());
    }

    #[test]
    fn skipped_counts_as_done() {
        let mut state = SetupState::default();
        for c in Component::all() {
            let status = if c == Component::SttModel {
                ComponentStatus::Skipped
            } else {
                ComponentStatus::Complete
            };
            state.components.insert(
                c,
                ComponentRecord {
                    status,
                    detail: String::new(),
                    error: None,
                },
            );
        }
        assert_eq!(state.percent(), 100);
        assert!(state.local_setup_complete());
    }

    #[test]
    fn keys_are_unique_and_round_trip() {
        let keys: Vec<&str> = Component::all().iter().map(|c| c.key()).collect();
        assert_eq!(
            keys.iter().collect::<std::collections::HashSet<_>>().len(),
            keys.len()
        );
        for key in keys {
            assert!(Component::from_key(key).is_some());
        }
    }

    #[test]
    fn migrate_preserves_completed_and_adds_new() {
        let mut old = SetupState::default();
        old.components.insert(
            Component::Database,
            ComponentRecord {
                status: ComponentStatus::Complete,
                detail: "x".into(),
                error: None,
            },
        );
        old.components.remove(&Component::Cua);
        let migrated = old.migrate();
        assert_eq!(
            migrated
                .components
                .get(&Component::Database)
                .unwrap()
                .status,
            ComponentStatus::Complete
        );
        assert_eq!(
            migrated.components.get(&Component::Cua).unwrap().status,
            ComponentStatus::Pending
        );
        assert_eq!(migrated.components.len(), Component::all().len());
    }
}

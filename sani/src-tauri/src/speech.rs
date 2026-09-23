//! SpeechEngine: Moonshine Voice via the Python sidecar process.
//!
//! The engine loads the model once and keeps the process warm. Audio is
//! written as framed f32/16 kHz chunks; partial/final transcript events come
//! back as JSON lines. Per the Sani contract, partials stay UI-only and the
//! final text triggers exactly one agent turn.

use parking_lot::{Mutex, RwLock};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

use crate::settings::stt_python_path;

#[derive(Debug, Clone, Serialize)]
pub struct VoiceModelDescriptor {
    pub id: String,
    pub installed: bool,
    pub active: bool,
}

#[derive(Debug, Deserialize)]
struct VoiceCatalogWire {
    models: Vec<String>,
    default: String,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // fields are read selectively per event kind
pub struct SttEvent {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub progress: f64,
    /// Why a `note` was emitted: segment / possible_end / resumed / commit /
    /// suppressed-filler / vad-unavailable ...
    #[serde(default)]
    pub reason: String,
    /// Completed segments in the pending user turn.
    #[serde(default)]
    pub segments: u32,
    #[serde(default)]
    pub chars: u32,
    /// The sidecar's commit-timer generation, for cancelling a stale commit.
    #[serde(default)]
    pub gen: u64,
    /// Measured end-of-speech -> commit gap, in ms. The tuning datum.
    #[serde(default)]
    pub silence_ms: u32,
    #[serde(default)]
    pub vad: f64,
    /// Which model produced this (`silero-v5` / `rms-fallback`).
    #[serde(default)]
    pub vad_model: String,
}

pub struct SpeechHandle {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    alive: Arc<AtomicBool>,
    /// Set once the sidecar's stdout closes — the process exited (crash, kill,
    /// or EOF). `ready` cannot express this: it is false both before the model
    /// loads *and* after death, so a dead engine would look merely "not ready"
    /// and never be respawned.
    exited: Arc<AtomicBool>,
    pub ready: Arc<AtomicBool>,
    pub model: RwLock<String>,
}

impl SpeechHandle {
    pub fn is_ready(&self) -> bool {
        self.ready.load(Ordering::Relaxed)
    }

    /// False once the sidecar process has gone away, so the caller knows to
    /// respawn rather than reuse a dead handle.
    pub fn is_alive(&self) -> bool {
        !self.exited.load(Ordering::Relaxed)
    }

    pub fn push_audio(&self, samples: &[f32]) {
        if !self.ready.load(Ordering::Relaxed) {
            return;
        }
        if let Some(stdin) = &mut *self.stdin.lock() {
            let bytes: Vec<u8> = samples.iter().flat_map(|s| s.to_le_bytes()).collect();
            let _ = write_frame(stdin, 0x01, &bytes);
        }
    }

    pub fn control(&self, json: &Value) {
        if let Some(stdin) = &mut *self.stdin.lock() {
            let _ = write_frame(stdin, 0x02, json.to_string().as_bytes());
        }
    }

    pub fn stop(&self) {
        self.alive.store(false, Ordering::Relaxed);
        if let Some(stdin) = self.stdin.lock().take() {
            drop(stdin);
        }
        if let Some(mut child) = self.child.try_lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn write_frame(stdin: &mut ChildStdin, frame_type: u8, payload: &[u8]) -> std::io::Result<()> {
    let mut header = [0u8; 5];
    header[0] = frame_type;
    header[1..5].copy_from_slice(&(payload.len() as u32).to_le_bytes());
    stdin.write_all(&header)?;
    stdin.write_all(payload)?;
    stdin.flush()
}

/// Locate the packaged, self-contained STT sidecar (Tauri `externalBin`).
///
/// In a bundled macOS app the sidecar sits next to the main executable inside
/// `Sani.app/Contents/MacOS/`. When present it is used directly — no repo
/// `.stt-venv`, no global Python, no shell environment (RC-05).
fn packaged_sidecar() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    for name in [
        "sani-stt-aarch64-apple-darwin",
        "sani-stt-x86_64-apple-darwin",
        "sani-stt",
    ] {
        let p = dir.join(name);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with("sani-stt") && entry.path().is_file() {
                return Some(entry.path());
            }
        }
    }
    None
}

/// Build the command prefix for both the streaming sidecar and its lightweight
/// metadata mode. Keeping this in one place prevents dev and packaged builds
/// from accidentally reporting different model catalogs.
fn sidecar_command(app: &AppHandle) -> Result<(std::path::PathBuf, Vec<String>), String> {
    match packaged_sidecar() {
        Some(bin) => Ok((bin, Vec::new())),
        None => {
            let python = stt_python_path(app, &crate::app_state::settings(app).read().clone())
                .ok_or("STT_NOT_SET_UP")?;
            let script = crate::settings::stt_script_path(app).ok_or("STT_SCRIPT_MISSING")?;
            Ok((python, vec![script.to_string_lossy().to_string()]))
        }
    }
}

fn parse_voice_catalog(output: &[u8]) -> Result<VoiceCatalogWire, String> {
    let catalog: VoiceCatalogWire = serde_json::from_slice(output)
        .map_err(|_| "Voice model catalog could not be read.".to_string())?;
    if catalog.models.is_empty() || !catalog.models.iter().any(|model| model == &catalog.default) {
        return Err("Voice model catalog is invalid.".to_string());
    }
    Ok(catalog)
}

/// Read the exact supported model list from the installed STT sidecar and
/// combine it with native cache ownership. The sidecar is the single source of
/// supported IDs; settings and either WebView never maintain a duplicate list.
pub fn voice_models(app: &AppHandle) -> Result<Vec<VoiceModelDescriptor>, String> {
    let (program, lead_args) = sidecar_command(app)?;
    let output = Command::new(program)
        .args(lead_args)
        .arg("--list-models")
        .stderr(Stdio::null())
        .output()
        .map_err(|_| "Voice model catalog is unavailable.".to_string())?;
    if !output.status.success() {
        return Err("Voice model catalog is unavailable.".to_string());
    }
    let catalog = parse_voice_catalog(&output.stdout)?;
    let selected = crate::app_state::settings(app).read().stt_model.clone();
    Ok(catalog
        .models
        .into_iter()
        .map(|id| VoiceModelDescriptor {
            installed: crate::setup::voice_model_cached(&id),
            active: id == selected,
            id,
        })
        .collect())
}

fn voice_capture_active(app: &AppHandle) -> bool {
    voice_mutation_allowed(crate::app_state::current_state(app)) == false
}

fn voice_mutation_allowed(state: crate::app_state::UiState) -> bool {
    !matches!(
        state,
        crate::app_state::UiState::Preparing
            | crate::app_state::UiState::Listening
            | crate::app_state::UiState::Finalizing
    )
}

fn wait_for_ready(handle: &SpeechHandle, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if handle.is_ready() {
            return Ok(());
        }
        if !handle.is_alive() {
            return Err("The voice model could not start.".to_string());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err("The voice model did not become ready in time.".to_string())
}

fn known_voice_model(app: &AppHandle, model: &str) -> Result<VoiceModelDescriptor, String> {
    voice_models(app)?
        .into_iter()
        .find(|candidate| candidate.id == model)
        .ok_or_else(|| "That voice model is not supported by this Sani voice engine.".to_string())
}

/// Download and verify a model using the actual Moonshine sidecar. No synthetic
/// install status is emitted: the sidecar downloader remains the sole source
/// of progress events. A voice capture may not be disrupted mid-turn.
pub fn install_voice_model(app: &AppHandle, model: &str) -> Result<VoiceModelDescriptor, String> {
    if voice_capture_active(app) {
        return Err("Finish or cancel the current voice capture before changing models.".into());
    }
    let descriptor = known_voice_model(app, model)?;
    if descriptor.installed {
        return Ok(descriptor);
    }
    let turn_end_ms = crate::settings::stt_turn_end_ms(&crate::app_state::settings(app).read());
    let candidate = start(app.clone(), model, turn_end_ms)?;
    let result = wait_for_ready(&candidate, Duration::from_secs(45));
    candidate.stop();
    result?;
    known_voice_model(app, model)
}

/// Switch models without killing a known-good warm engine until the candidate
/// sidecar has reported ready. Audio capture stays alive; its writer observes
/// the swapped speech handle on the next block. If the candidate fails, the
/// previous engine and persisted selection are left intact.
pub fn use_voice_model(app: &AppHandle, model: &str) -> Result<VoiceModelDescriptor, String> {
    if voice_capture_active(app) {
        return Err("Finish or cancel the current voice capture before changing models.".into());
    }
    let requested = known_voice_model(app, model)?;
    let state = app.state::<crate::app_state::SaniState>();
    let old_handle = state.speech.lock().as_ref().cloned();
    let previous_model = crate::app_state::settings(app).read().stt_model.clone();
    if requested.active && old_handle.as_ref().is_some_and(|handle| handle.is_ready()) {
        return Ok(requested);
    }

    let turn_end_ms = crate::settings::stt_turn_end_ms(&crate::app_state::settings(app).read());
    let candidate = start(app.clone(), model, turn_end_ms)?;
    if let Err(error) = wait_for_ready(&candidate, Duration::from_secs(45)) {
        candidate.stop();
        // Explicitly retain the previous running handle and selected setting.
        // There is no provider/model fallback hidden in this path.
        log::warn!("voice model change to {model} failed; retaining {previous_model}");
        return Err(error);
    }

    // Persist only after the exact requested sidecar has proved it can run.
    // A write failure leaves the currently working handle untouched.
    {
        let settings_arc = crate::app_state::settings(app);
        let mut settings = settings_arc.write();
        settings.stt_model = model.to_string();
        crate::settings::save(app, &settings)?;
    }
    let replaced = state.speech.lock().replace(candidate);
    if let Some(old) = replaced {
        old.stop();
    }
    let _ = app.emit(
        "settings://changed",
        crate::onboarding::get_full_settings(app.clone()),
    );
    known_voice_model(app, model)
}

fn owned_cache_model_path(root: &Path, model: &str) -> Result<PathBuf, String> {
    let mut components = Path::new(model).components();
    match (components.next(), components.next()) {
        (Some(Component::Normal(_)), None) => {
            Ok(root.join("download.moonshine.ai").join("model").join(model))
        }
        _ => Err("That model does not name an owned voice cache directory.".into()),
    }
}

/// Delete only a completed, non-active model directory under Moonshine's owned
/// cache root. Paths are derived from an already sidecar-validated model ID;
/// symlinks are rejected rather than followed.
pub fn remove_voice_model(app: &AppHandle, model: &str) -> Result<(), String> {
    if voice_capture_active(app) {
        return Err("Finish or cancel the current voice capture before removing a model.".into());
    }
    let descriptor = known_voice_model(app, model)?;
    if descriptor.active {
        return Err("Choose another voice model before removing the active model.".into());
    }
    let root = crate::setup::moonshine_cache_root()
        .ok_or_else(|| "Sani could not locate its owned voice model cache.".to_string())?;
    let path = owned_cache_model_path(&root, model)?;
    let metadata = std::fs::symlink_metadata(&path)
        .map_err(|_| "That voice model is not installed in Sani’s cache.".to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err("Sani will only remove a real directory in its owned voice cache.".into());
    }
    std::fs::remove_dir_all(&path)
        .map_err(|_| "Sani could not remove that voice model cache.".to_string())?;
    let _ = app.emit("sani://voice-model-removed", model);
    Ok(())
}

/// Locate the voice engine for first-run setup *without* starting it. Returns a
/// diagnostic describing what was found, or an error the setup UI can show.
pub fn locate_for_setup(app: &AppHandle) -> Result<String, String> {
    if let Some(bin) = packaged_sidecar() {
        return Ok(format!(
            "voice engine available ({})",
            bin.file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default()
        ));
    }
    let settings = crate::app_state::settings(app).read().clone();
    if crate::settings::stt_python_path(app, &settings).is_some()
        && crate::settings::stt_script_path(app).is_some()
    {
        return Ok("voice engine available (local runtime)".to_string());
    }
    Err("voice engine component missing".to_string())
}

/// Sidecar stderr goes to its own log file beside `sani.log`.
///
/// `Stdio::null()` made this feature undebuggable: `moonshine_voice` swallows
/// listener exceptions and prints them to stderr, so a broken VAD tensor or a
/// panicking callback produced no evidence at all.
fn sidecar_stderr(app: &AppHandle) -> Stdio {
    match crate::log_dir(app).and_then(|dir| {
        let _ = std::fs::create_dir_all(&dir);
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("sani-stt.log"))
            .ok()
    }) {
        Some(file) => Stdio::from(file),
        None => Stdio::null(),
    }
}

/// Spawn the sidecar and wire its JSON events to Tauri. Returns `Err` with a
/// user-presentable reason when the STT environment is not set up.
pub fn start(app: AppHandle, model: &str, turn_end_ms: u32) -> Result<Arc<SpeechHandle>, String> {
    // Prefer the packaged sidecar; fall back to the dev venv + script so
    // `tauri dev` inside the repository keeps working.
    let (program, lead_args) = match sidecar_command(&app) {
        Ok((bin, args)) if args.is_empty() => {
            log::info!("STT sidecar (packaged): {}", bin.display());
            (bin, args)
        }
        Ok((python, args)) => {
            log::info!(
                "STT sidecar (dev venv): {} {}",
                python.display(),
                args.first().map(String::as_str).unwrap_or_default()
            );
            (python, args)
        }
        Err(e) => return Err(e),
    };

    let mut command = Command::new(&program);
    for arg in &lead_args {
        command.arg(arg);
    }
    let mut child = command
        .arg("--model")
        .arg(model)
        .arg("--update-interval")
        .arg("0.25")
        .arg("--turn-end-ms")
        .arg(turn_end_ms.to_string())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(sidecar_stderr(&app))
        .spawn()
        .map_err(|e| format!("STT_SPAWN_FAILED: {e}"))?;

    let stdin = child.stdin.take().ok_or("STT_SPAWN_FAILED: no stdin")?;
    let stdout = child.stdout.take().ok_or("STT_SPAWN_FAILED: no stdout")?;

    let alive = Arc::new(AtomicBool::new(true));
    let ready = Arc::new(AtomicBool::new(false));
    let exited = Arc::new(AtomicBool::new(false));

    let handle = Arc::new(SpeechHandle {
        child: Mutex::new(child),
        stdin: Mutex::new(Some(stdin)),
        alive: alive.clone(),
        exited: exited.clone(),
        ready: ready.clone(),
        model: RwLock::new(model.to_string()),
    });

    // Reader thread: JSON line -> Tauri event. Exits when stdout closes.
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if !alive.load(Ordering::Relaxed) {
                break;
            }
            let Ok(event) = serde_json::from_str::<SttEvent>(&line) else {
                continue;
            };
            match event.kind.as_str() {
                "ready" => {
                    log::info!(
                        "stt sidecar ready (model={} vad={})",
                        event.model,
                        if event.vad_model.is_empty() {
                            "unknown"
                        } else {
                            &event.vad_model
                        }
                    );
                    ready.store(true, Ordering::Relaxed);
                    let _ = app.emit("sani://stt-status", "ready");
                    // FIX-05: only now may a pending listen attempt open the
                    // audio gate and show Listening.
                    crate::app_state::on_stt_ready(&app);
                }
                "downloading" => {
                    let _ = app.emit(
                        "sani://voice-model-progress",
                        serde_json::json!({"model": event.model, "progress": event.progress}),
                    );
                    let _ = app.emit(
                        "sani://stt-status",
                        serde_json::json!({"status": "downloading", "progress": event.progress}),
                    );
                }
                "partial" => {
                    log::info!("[stt partial] {}", event.text);
                    crate::app_state::on_partial(&app, event.text);
                }
                "final" => {
                    log::info!("[stt final] {}", event.text);
                    crate::app_state::on_final(&app, event.text);
                }
                "error" => {
                    log::error!("stt sidecar error: {}", event.message);
                    let _ = app.emit("sani://stt-error", event.message);
                }
                "note" => crate::app_state::on_stt_note(&app, &event),
                other => log::debug!("[stt] unhandled sidecar event kind={other}"),
            }
        }
        // stdout closed: the sidecar process is gone. Mark it dead so the next
        // listen respawns rather than reusing this handle and hanging in
        // Preparing forever. `alive` is false only on an intentional stop().
        exited.store(true, Ordering::Relaxed);
        ready.store(false, Ordering::Relaxed);
        if alive.load(Ordering::Relaxed) {
            let _ = app.emit("sani://stt-status", "stopped");
        }
    });

    Ok(handle)
}

/// Parse the sidecar "STT_NOT_SET_UP" style failure into setup instructions.
pub fn setup_hint(err: &str) -> String {
    if err.starts_with("STT_NOT_SET_UP") || err.starts_with("STT_SCRIPT_MISSING") {
        return "Preparing voice model... Run sani/scripts/setup-stt.sh once to install the local voice engine.".into();
    }
    if err.starts_with("STT_SPAWN_FAILED") {
        return "Voice engine failed to start. Check the STT environment.".into();
    }
    err.to_string()
}

#[cfg(test)]
mod tests {
    use super::{owned_cache_model_path, parse_voice_catalog, voice_mutation_allowed};
    use crate::app_state::UiState;

    #[test]
    fn accepts_the_sidecars_catalog_shape() {
        let catalog = parse_voice_catalog(
            br#"{"models":["tiny-streaming-en","small-streaming-en"],"default":"small-streaming-en"}"#,
        )
        .expect("catalog");
        assert_eq!(catalog.models.len(), 2);
        assert_eq!(catalog.default, "small-streaming-en");
    }

    #[test]
    fn rejects_a_catalog_whose_default_is_not_supported() {
        assert!(
            parse_voice_catalog(br#"{"models":["tiny-streaming-en"],"default":"missing"}"#)
                .is_err()
        );
    }

    #[test]
    fn model_mutation_is_rejected_during_any_voice_capture_stage() {
        assert!(!voice_mutation_allowed(UiState::Preparing));
        assert!(!voice_mutation_allowed(UiState::Listening));
        assert!(!voice_mutation_allowed(UiState::Finalizing));
        assert!(voice_mutation_allowed(UiState::Idle));
        assert!(voice_mutation_allowed(UiState::Working));
    }

    #[test]
    fn owned_cache_path_rejects_path_traversal() {
        let root = std::path::Path::new("/safe/cache");
        assert!(owned_cache_model_path(root, "../other").is_err());
        assert!(owned_cache_model_path(root, "/other").is_err());
        assert!(owned_cache_model_path(root, "small-streaming-en").is_ok());
    }
}

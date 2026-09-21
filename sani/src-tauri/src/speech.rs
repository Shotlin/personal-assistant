//! SpeechEngine: Moonshine Voice via the Python sidecar process.
//!
//! The engine loads the model once and keeps the process warm. Audio is
//! written as framed f32/16 kHz chunks; partial/final transcript events come
//! back as JSON lines. Per the Sani contract, partials stay UI-only and the
//! final text triggers exactly one agent turn.

use parking_lot::{Mutex, RwLock};
use serde::Deserialize;
use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::{AppHandle, Emitter};

use crate::settings::stt_python_path;

#[derive(Debug, Deserialize, Clone)]
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
}

pub struct SpeechHandle {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    alive: Arc<AtomicBool>,
    pub ready: Arc<AtomicBool>,
    pub model: RwLock<String>,
}

impl SpeechHandle {
    pub fn is_ready(&self) -> bool {
        self.ready.load(Ordering::Relaxed)
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

/// Spawn the sidecar and wire its JSON events to Tauri. Returns `Err` with a
/// user-presentable reason when the STT environment is not set up.
pub fn start(app: AppHandle, model: &str) -> Result<Arc<SpeechHandle>, String> {
    // Prefer the packaged sidecar; fall back to the dev venv + script so
    // `tauri dev` inside the repository keeps working.
    let (program, lead_args) = match packaged_sidecar() {
        Some(bin) => {
            log::info!("STT sidecar (packaged): {}", bin.display());
            (bin, Vec::new())
        }
        None => {
            let python = stt_python_path(&app, &crate::app_state::settings(&app).read().clone())
                .ok_or("STT_NOT_SET_UP")?;
            let script = crate::settings::stt_script_path(&app).ok_or("STT_SCRIPT_MISSING")?;
            log::info!("STT sidecar (dev venv): {} {}", python.display(), script.display());
            (python, vec![script.to_string_lossy().to_string()])
        }
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
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("STT_SPAWN_FAILED: {e}"))?;

    let stdin = child.stdin.take().ok_or("STT_SPAWN_FAILED: no stdin")?;
    let stdout = child.stdout.take().ok_or("STT_SPAWN_FAILED: no stdout")?;

    let alive = Arc::new(AtomicBool::new(true));
    let ready = Arc::new(AtomicBool::new(false));

    let handle = Arc::new(SpeechHandle {
        child: Mutex::new(child),
        stdin: Mutex::new(Some(stdin)),
        alive: alive.clone(),
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
                    log::info!("stt sidecar ready (model={})", event.model);
                    ready.store(true, Ordering::Relaxed);
                    let _ = app.emit("sani://stt-status", "ready");
                    // FIX-05: only now may a pending listen attempt open the
                    // audio gate and show Listening.
                    crate::app_state::on_stt_ready(&app);
                }
                "downloading" => {
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
                _ => {}
            }
        }
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

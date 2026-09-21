//! Central Sani orchestrator: the voice state machine.
//!
//! Hotkey -> [microphone authorization] -> Preparing (STT model warm-up; audio
//! gate stays closed so we never show Listening while samples are discarded)
//! -> Listening (gate open, partials flow) -> Finalizing (final transcript
//! locked behind a generation token) -> Working (exactly one agent turn;
//! streamed reply + activity events) -> Ready. Esc cancels whichever stage is
//! live and invalidates any pending finalization.

use parking_lot::Mutex;
use serde::Serialize;
use serde_json::json;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

use crate::audio::{self, AudioHandle};
use crate::history::{self, SharedHistory};
use crate::hotkey;
use crate::permissions::{self, MicPermission};
use crate::settings::{self, SharedSettings};
use crate::speech::{self, SpeechHandle};
use crate::windows;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum UiState {
    Idle,
    Preparing,
    Listening,
    Finalizing,
    Working,
    Error,
}

impl UiState {
    pub fn as_str(&self) -> &'static str {
        match self {
            UiState::Idle => "idle",
            UiState::Preparing => "preparing",
            UiState::Listening => "listening",
            UiState::Finalizing => "finalizing",
            UiState::Working => "working",
            UiState::Error => "error",
        }
    }
}

struct RunGuard {
    message_id: String,
    cancelled: Arc<AtomicBool>,
}

pub struct SaniState {
    pub settings: SharedSettings,
    pub history: SharedHistory,
    pub speech: Mutex<Option<Arc<SpeechHandle>>>,
    pub audio: Mutex<Option<Arc<AudioHandle>>>,
    pub state: Mutex<UiState>,
    pub(crate) run: Mutex<Option<RunGuard>>,
    /// Live partial transcript, cleared per turn.
    pub partial: Mutex<String>,
    /// Streaming assistant text for the current run.
    pub assistant_text: Mutex<String>,
    pub assistant_message_id: Mutex<String>,
    /// A listen attempt is in flight (Preparing); cleared on cancel. The STT
    /// ready signal only opens the gate while this is set.
    pub pending_listen: AtomicBool,
    /// Monotonic finalization token. Each cancel/new final bumps it so a
    /// delayed `begin_turn` can prove it is still the current utterance
    /// (FIX-02) and never fire after Esc.
    pub turn_gen: AtomicU64,
    /// Frontend startup evidence (RC-03): set when each window's React tree
    /// mounts and emits its `*-ui-ready` handshake.
    pub ui_ready_pill: AtomicBool,
    pub ui_ready_panel: AtomicBool,
}

impl SaniState {
    pub fn new(settings: SharedSettings, history: SharedHistory) -> Self {
        Self {
            settings,
            history,
            speech: Mutex::new(None),
            audio: Mutex::new(None),
            state: Mutex::new(UiState::Idle),
            run: Mutex::new(None),
            partial: Mutex::new(String::new()),
            assistant_text: Mutex::new(String::new()),
            assistant_message_id: Mutex::new(String::new()),
            pending_listen: AtomicBool::new(false),
            turn_gen: AtomicU64::new(0),
            ui_ready_pill: AtomicBool::new(false),
            ui_ready_panel: AtomicBool::new(false),
        }
    }
}

pub fn settings(app: &AppHandle) -> SharedSettings {
    app.state::<SaniState>().settings.clone()
}

pub fn history(app: &AppHandle) -> SharedHistory {
    app.state::<SaniState>().history.clone()
}

pub fn current_state(app: &AppHandle) -> UiState {
    *app.state::<SaniState>().state.lock()
}

fn set_state(app: &AppHandle, next: UiState) {
    let state = app.state::<SaniState>();
    *state.state.lock() = next;
    log::info!("[state] {}", next.as_str());
    let _ = app.emit("sani://state", next.as_str());
    hotkey::rebind_on_state_change(app);
    // Verification affordance only (SANI_SNAPSHOT_DIR): a real PNG of every
    // overlay at each state transition, taken after React has repainted.
    if crate::snapshot::dir().is_some() {
        let handle = app.clone();
        let tag = next.as_str().to_string();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(400));
            crate::snapshot::snapshot_overlays(&handle, &tag);
        });
    }
}

pub fn is_cancelled(app: &AppHandle, message_id: &str) -> bool {
    let state = app.state::<SaniState>();
    let guard = state.run.lock();
    guard
        .as_ref()
        .map(|r| r.message_id == message_id && r.cancelled.load(Ordering::Relaxed))
        .unwrap_or(false)
}

fn emit_mic_permission(app: &AppHandle, state: &str) {
    let _ = app.emit("sani://mic-permission", state);
}

/// Record that a window's React tree mounted (RC-03 startup evidence).
pub fn mark_ui_ready(app: &AppHandle, which: &str) {
    let state = app.state::<SaniState>();
    match which {
        "pill" => {
            state.ui_ready_pill.store(true, Ordering::Relaxed);
            log::info!("[ui-boot] pill UI READY (React mounted)");
        }
        "panel" => {
            state.ui_ready_panel.store(true, Ordering::Relaxed);
            log::info!("[ui-boot] panel UI READY (React mounted)");
        }
        _ => {}
    }
}

// ---------------------------------------------------------------- voice flow

pub fn toggle_listening(app: &AppHandle) {
    match current_state(app) {
        UiState::Listening | UiState::Finalizing | UiState::Preparing => stop_listening(app),
        _ => start_listening(app),
    }
}

pub fn start_listening(app: &AppHandle) {
    if matches!(current_state(app), UiState::Working | UiState::Finalizing) {
        return; // one turn at a time
    }

    // Microphone authorization gate (RC-04). Never infer permission from
    // silence: ask the OS, and refuse to look like we are listening when the
    // user has not granted access.
    match permissions::status() {
        MicPermission::Denied | MicPermission::Restricted => {
            emit_mic_permission(app, permissions::status().as_str());
            let _ = app.emit(
                "sani://mic-error",
                "Microphone access denied. Enable it in System Settings › Privacy & Security › Microphone.",
            );
            set_state(app, UiState::Error);
            return;
        }
        MicPermission::NotDetermined => {
            emit_mic_permission(app, "not_determined");
            set_state(app, UiState::Preparing);
            permissions::request();
            let app2 = app.clone();
            std::thread::spawn(move || {
                // Wait for the user to answer the system prompt (bounded).
                for _ in 0..600 {
                    std::thread::sleep(Duration::from_millis(200));
                    if permissions::request_settled() {
                        break;
                    }
                }
                // Bail out if the user already cancelled this attempt.
                if current_state(&app2) != UiState::Preparing {
                    return;
                }
                match permissions::status() {
                    MicPermission::Granted => {
                        emit_mic_permission(&app2, "granted");
                        begin_capture(&app2);
                    }
                    // Still undetermined: the prompt was never answered. Say so
                    // instead of blaming System Settings for a denial.
                    MicPermission::NotDetermined => {
                        log::warn!("[mic] authorization prompt went unanswered; staying honest about it");
                        emit_mic_permission(&app2, "not_determined");
                        let _ = app2.emit(
                            "sani://mic-error",
                            "Microphone permission request timed out. Press the shortcut to ask again.",
                        );
                        set_state(&app2, UiState::Error);
                    }
                    other => {
                        log::warn!("[mic] authorization denied by macOS ({})", other.as_str());
                        emit_mic_permission(&app2, other.as_str());
                        let _ = app2.emit(
                            "sani://mic-error",
                            "Microphone access denied. Enable it in System Settings › Privacy & Security › Microphone.",
                        );
                        set_state(&app2, UiState::Error);
                    }
                }
            });
            return;
        }
        MicPermission::Granted | MicPermission::Unknown => {
            begin_capture(app);
        }
    }
}

/// Open the (warm) audio capture and arm a listen attempt. The audio gate
/// stays CLOSED until the STT engine reports ready, so the UI never claims
/// Listening while samples would be discarded (FIX-05).
fn begin_capture(app: &AppHandle) {
    let state = app.state::<SaniState>();
    if matches!(current_state(app), UiState::Working | UiState::Finalizing) {
        return;
    }

    // Speech engine: load once, keep warm.
    let model = state.settings.read().stt_model.clone();
    if state.speech.lock().is_none() {
        match speech::start(app.clone(), &model) {
            Ok(handle) => *state.speech.lock() = Some(handle),
            Err(err) => {
                let hint = speech::setup_hint(&err);
                let _ = app.emit("sani://stt-error", hint);
                set_state(app, UiState::Error);
                return;
            }
        }
    }

    // Audio capture: open once, keep warm, gate controls flow. Reopen when the
    // configured device changed since the current capture was opened.
    let mic_device = state.settings.read().mic_device.clone();
    let needs_open = match state.audio.lock().as_ref() {
        None => true,
        Some(handle) => !mic_device.is_empty() && handle.device_name != mic_device,
    };
    if needs_open {
        let (tx, rx) = std::sync::mpsc::channel::<audio::AudioChunk>();
        match audio::start_capture(&mic_device, tx) {
            Ok(handle) => {
                *state.audio.lock() = Some(Arc::new(handle));
                let app_for_writer = app.clone();
                std::thread::spawn(move || {
                    // Writer thread: frames never block the capture callback.
                    let mut buffer: Vec<f32> = Vec::with_capacity(8192);
                    for chunk in rx {
                        buffer.extend_from_slice(&chunk.samples);
                        // Flush in ~100 ms blocks at 16 kHz.
                        while buffer.len() >= 1600 {
                            let block: Vec<f32> = buffer.drain(..1600).collect();
                            if let Some(speech) =
                                app_for_writer.state::<SaniState>().speech.lock().as_ref()
                            {
                                speech.push_audio(&block);
                            }
                        }
                    }
                });
            }
            Err(err) => {
                log::error!("capture failed: {err}");
                let _ = app.emit("sani://mic-error", err);
                set_state(app, UiState::Error);
                return;
            }
        }
    }

    // Fresh line for this utterance; discard any stale open line and keep the
    // gate closed until STT is ready.
    if let Some(speech) = state.speech.lock().as_ref() {
        speech.control(&json!({"cmd": "discard"}));
    }
    if let Some(audio) = state.audio.lock().as_ref() {
        audio.gate.store(false, Ordering::Relaxed);
    }
    *state.partial.lock() = String::new();
    let _ = app.emit("sani://partial", "");

    let _ = windows::show_pill(app);
    let _ = windows::show_panel(app);

    state.pending_listen.store(true, Ordering::Relaxed);
    set_state(app, UiState::Preparing);

    // Warm engine: go straight to Listening. Cold engine: on_stt_ready() flips
    // us over when the sidecar reports `ready` (model load / download done).
    let ready = state
        .speech
        .lock()
        .as_ref()
        .map(|s| s.is_ready())
        .unwrap_or(false);
    if ready {
        enter_listening(app);
    }
}

/// Open the audio gate and announce Listening — only while a listen attempt is
/// pending, the engine is ready, permission is granted, and we are still in
/// Preparing (so a cancel or a duplicate ready signal cannot reopen the gate).
fn enter_listening(app: &AppHandle) {
    let state = app.state::<SaniState>();
    if !state.pending_listen.load(Ordering::Relaxed) {
        return;
    }
    if current_state(app) != UiState::Preparing {
        return;
    }
    if !permissions::status().is_granted() {
        return;
    }
    let ready = state
        .speech
        .lock()
        .as_ref()
        .map(|s| s.is_ready())
        .unwrap_or(false);
    if !ready {
        return;
    }
    if let Some(audio) = state.audio.lock().as_ref() {
        audio.gate.store(true, Ordering::Relaxed);
    }
    set_state(app, UiState::Listening);
}

/// Called by the speech reader thread when the sidecar reports `ready`.
pub fn on_stt_ready(app: &AppHandle) {
    let state = app.state::<SaniState>();
    if state.pending_listen.load(Ordering::Relaxed) {
        enter_listening(app);
    }
}

pub fn stop_listening(app: &AppHandle) {
    let state = app.state::<SaniState>();
    // Cancel any in-flight prepare and invalidate a pending finalization so a
    // delayed begin_turn cannot still fire (FIX-02).
    state.pending_listen.store(false, Ordering::Relaxed);
    state.turn_gen.fetch_add(1, Ordering::Relaxed);
    if let Some(audio) = state.audio.lock().as_ref() {
        audio.gate.store(false, Ordering::Relaxed);
    }
    if let Some(speech) = state.speech.lock().as_ref() {
        speech.control(&json!({"cmd": "discard"}));
    }
    *state.partial.lock() = String::new();
    let _ = app.emit("sani://partial", "");
    set_state(app, UiState::Idle);
}

/// A live partial transcript arrived (UI-only; never sent to the agent).
pub fn on_partial(app: &AppHandle, text: String) {
    let state = app.state::<SaniState>();
    if current_state(app) != UiState::Listening {
        return;
    }
    *state.partial.lock() = text.clone();
    let _ = app.emit("sani://partial", text);
}

/// A final transcript arrived from the speech engine.
pub fn on_final(app: &AppHandle, text: String) {
    let trimmed = text.trim().to_string();
    let state = app.state::<SaniState>();
    if let Some(audio) = state.audio.lock().as_ref() {
        audio.gate.store(false, Ordering::Relaxed);
    }
    if trimmed.is_empty() {
        // Never send empty text: keep listening.
        if let Some(speech) = state.speech.lock().as_ref() {
            speech.control(&json!({"cmd": "discard"}));
        }
        if let Some(audio) = state.audio.lock().as_ref() {
            audio.gate.store(true, Ordering::Relaxed);
        }
        return;
    }
    // A final only counts while we are actually listening.
    if current_state(app) != UiState::Listening {
        return;
    }

    // Claim a fresh generation token for this finalization (FIX-02).
    let my_gen = state.turn_gen.fetch_add(1, Ordering::Relaxed) + 1;
    set_state(app, UiState::Finalizing);
    *state.partial.lock() = trimmed.clone();
    let _ = app.emit("sani://final", trimmed.clone());
    let app_handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(250));
        begin_turn_if_current(&app_handle, my_gen, trimmed);
    });
}

/// Delayed handoff guard: only begin the turn if this finalization is still the
/// current one and we are still Finalizing. Esc/cancel bumps `turn_gen`, so a
/// cancelled utterance never reaches the Deep Agent (FIX-02).
fn begin_turn_if_current(app: &AppHandle, gen: u64, final_text: String) {
    let state = app.state::<SaniState>();
    if state.turn_gen.load(Ordering::Relaxed) != gen {
        log::info!("stale finalization (gen {gen}) superseded — dropping turn");
        return;
    }
    if current_state(app) != UiState::Finalizing {
        return;
    }
    begin_turn(app, final_text);
}

fn ensure_active_conversation(app: &AppHandle) -> String {
    let state = app.state::<SaniState>();
    let mut settings_guard = state.settings.write();
    let existing = settings_guard.active_conversation_id.clone();
    if !existing.is_empty() && state.history.get_conversation(&existing).ok().flatten().is_some() {
        return existing;
    }
    let id = Uuid::new_v4().to_string();
    let now = now_ms();
    if state.history.create_conversation(&id, "New conversation", now).is_err() {
        return existing;
    }
    settings_guard.active_conversation_id = id.clone();
    let _ = settings::save(app, &settings_guard);
    let _ = app.emit("sani://conversation-changed", &id);
    id
}

fn begin_turn(app: &AppHandle, final_text: String) {
    let state = app.state::<SaniState>();
    if matches!(current_state(app), UiState::Working) {
        return;
    }
    let conversation_id = ensure_active_conversation(app);

    let message_id = Uuid::new_v4().to_string();
    let now = now_ms();
    let _ = state.history.append_message(
        &message_id, &conversation_id, "user", &final_text, now, None,
    );
    let _ = state
        .history
        .rename_conversation(&conversation_id, &history::title_from_text(&final_text), now);
    let _ = state.history.touch_conversation(&conversation_id, now);
    let _ = app.emit(
        "sani://message",
        json!({"id": message_id, "role": "user", "text": final_text, "created_at": now}),
    );

    *state.assistant_text.lock() = String::new();
    *state.assistant_message_id.lock() = String::new();
    *state.run.lock() = Some(RunGuard {
        message_id: message_id.clone(),
        cancelled: Arc::new(AtomicBool::new(false)),
    });

    log::info!("begin turn: conversation={conversation_id} message={message_id}");
    set_state(app, UiState::Working);
    let _ = windows::show_panel(app);

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        crate::agent::stream_chat(app_handle, message_id, final_text).await;
    });
}

/// Terminal path of one agent run (from agent.rs).
///
/// `status` is one of: "completed", "cancelled", "interrupted", "failed".
/// Only a normal completed protocol path is a success (FIX-03); partial text
/// is preserved either way, but an interrupted/cancelled run is never reported
/// as a clean success.
pub fn agent_finished(
    app: &AppHandle,
    message_id: &str,
    run_id: &str,
    text: &str,
    ok: bool,
    status: &str,
    error: String,
) {
    let state = app.state::<SaniState>();

    // FIX-04: a completion that does not own the active run is stale. Return
    // immediately — do not touch history, UI events, or global state.
    let owns = {
        let guard_lock = state.run.lock();
        guard_lock
            .as_ref()
            .map(|g| g.message_id == message_id)
            .unwrap_or(false)
    };
    if !owns {
        log::info!("stale completion for {message_id} — ignoring (not the active run)");
        return;
    }
    *state.run.lock() = None;

    if !text.trim().is_empty() {
        let assistant_id = Uuid::new_v4().to_string();
        let conversation_id = state.settings.read().active_conversation_id.clone();
        let now = now_ms();
        let run_opt = if run_id.is_empty() { None } else { Some(run_id) };
        let _ = state.history.append_message(
            &assistant_id,
            &conversation_id,
            "assistant",
            text,
            now,
            run_opt,
        );
        let _ = state.history.touch_conversation(&conversation_id, now);
        *state.assistant_message_id.lock() = assistant_id;
    }
    let _ = app.emit(
        "sani://agent-done",
        json!({
            "message_id": message_id,
            "run_id": run_id,
            "ok": ok,
            "status": status,
            "error": error,
        }),
    );
    // Length, never content: the transcript stays in the local history store.
    log::info!(
        "turn finished: message={message_id} run={run_id} status={status} chars={}",
        text.chars().count()
    );
    let next = match status {
        "completed" | "cancelled" => UiState::Idle,
        _ => UiState::Error,
    };
    set_state(app, next);
}

// ------------------------------------------------------------------- escape

pub fn handle_escape(app: &AppHandle) {
    match current_state(app) {
        UiState::Listening | UiState::Finalizing | UiState::Preparing => stop_listening(app),
        UiState::Working => {
            let state = app.state::<SaniState>();
            let guard_lock = state.run.lock();
            if let Some(guard) = guard_lock.as_ref() {
                guard.cancelled.store(true, Ordering::Relaxed);
            }
        }
        UiState::Idle | UiState::Error => {
            windows::hide_overlays(app);
            hotkey::set_esc_active(app, false);
        }
    }
}

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// Periodic waveform emitter: only while listening.
pub fn spawn_level_ticker(app: AppHandle) {
    std::thread::spawn(move || {
        let mut last_sent: Vec<f32> = Vec::new();
        loop {
            std::thread::sleep(Duration::from_millis(50));
            if current_state(&app) != UiState::Listening {
                continue;
            }
            let Some(audio) = app.state::<SaniState>().audio.lock().clone() else {
                continue;
            };
            let levels = audio.levels.lock().clone();
            if levels != last_sent && !levels.is_empty() {
                let _ = app.emit("sani://level", levels.clone());
                last_sent = levels;
            }
        }
    });
}

/// Periodic agent health probe.
pub fn spawn_health_probe(app: AppHandle) {
    std::thread::spawn(move || {
        loop {
            let online = {
                let handle = app.clone();
                tauri::async_runtime::block_on(async move {
                    crate::agent::health(&handle).await
                })
            };
            let _ = app.emit("sani://agent-status", online);
            std::thread::sleep(Duration::from_secs(20));
        }
    });
}

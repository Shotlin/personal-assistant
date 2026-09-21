//! Central Sani orchestrator: the voice state machine.
//!
//! Hotkey -> Listening (audio gate open, partials flow) -> Finalizing
//! (final transcript locked) -> Working (exactly one agent turn; streamed
//! reply + activity events) -> Ready. Esc cancels whichever stage is live.

use parking_lot::Mutex;
use serde::Serialize;
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

use crate::audio::{self, AudioHandle};
use crate::history::{self, SharedHistory};
use crate::hotkey;
use crate::settings::{self, SharedSettings};
use crate::speech::{self, SpeechHandle};
use crate::windows;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum UiState {
    Idle,
    Listening,
    Finalizing,
    Working,
    Error,
}

impl UiState {
    pub fn as_str(&self) -> &'static str {
        match self {
            UiState::Idle => "idle",
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
    let _ = app.emit("sani://state", next.as_str());
    hotkey::rebind_on_state_change(app);
}

pub fn is_cancelled(app: &AppHandle, message_id: &str) -> bool {
    let state = app.state::<SaniState>();
    let guard = state.run.lock();
    guard
        .as_ref()
        .map(|r| r.message_id == message_id && r.cancelled.load(Ordering::Relaxed))
        .unwrap_or(false)
}

// ---------------------------------------------------------------- voice flow

pub fn toggle_listening(app: &AppHandle) {
    match current_state(app) {
        UiState::Listening | UiState::Finalizing => stop_listening(app),
        _ => start_listening(app),
    }
}

pub fn start_listening(app: &AppHandle) {
    let state = app.state::<SaniState>();
    if matches!(current_state(app), UiState::Working | UiState::Finalizing) {
        return; // one turn at a time
    }

    // Speech engine: load once, keep warm.
    let model = state.settings.read().stt_model.clone();
    if state.speech.lock().is_none() {
        set_state(app, UiState::Idle);
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

    // Audio capture: open once, keep warm, gate controls flow. Reopen when
    // the configured device changed since the current capture was opened.
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

    // Fresh line for this utterance; discard any stale open line.
    if let Some(speech) = state.speech.lock().as_ref() {
        speech.control(&json!({"cmd": "discard"}));
    }
    if let Some(audio) = state.audio.lock().as_ref() {
        audio.gate.store(true, Ordering::Relaxed);
    }
    *state.partial.lock() = String::new();

    let _ = windows::show_pill(app);
    let _ = windows::show_panel(app);
    set_state(app, UiState::Listening);
}

pub fn stop_listening(app: &AppHandle) {
    let state = app.state::<SaniState>();
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
    set_state(app, UiState::Finalizing);
    *state.partial.lock() = trimmed.clone();
    let _ = app.emit("sani://final", trimmed.clone());
    let app_handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(250));
        begin_turn(&app_handle, trimmed);
    });
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
pub fn agent_finished(
    app: &AppHandle,
    message_id: &str,
    run_id: &str,
    text: &str,
    ok: bool,
    error: String,
) {
    let state = app.state::<SaniState>();
    // Only the current run owns finalization; a stale completion is ignored.
    {
        let mut guard_lock = state.run.lock();
        let owns = guard_lock
            .as_ref()
            .map(|g| g.message_id == message_id)
            .unwrap_or(false);
        if owns {
            *guard_lock = None;
        }
    }
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
        json!({"message_id": message_id, "run_id": run_id, "ok": ok, "error": error}),
    );
    set_state(app, if ok { UiState::Idle } else { UiState::Error });
}

// ------------------------------------------------------------------- escape

pub fn handle_escape(app: &AppHandle) {
    match current_state(app) {
        UiState::Listening | UiState::Finalizing => stop_listening(app),
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
            std::thread::sleep(std::time::Duration::from_millis(50));
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
            std::thread::sleep(std::time::Duration::from_secs(20));
        }
    });
}

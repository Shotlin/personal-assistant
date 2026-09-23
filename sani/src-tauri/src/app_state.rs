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
    /// Main desktop shell startup evidence. The normal window has an opaque
    /// boot fallback until this React-mount handshake arrives.
    pub ui_ready_main: AtomicBool,
    /// Monotonic debounce token for normal-window geometry persistence.
    pub main_window_persist_generation: AtomicU64,
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
            ui_ready_main: AtomicBool::new(false),
            main_window_persist_generation: AtomicU64::new(0),
        }
    }
}

pub fn settings(app: &AppHandle) -> SharedSettings {
    app.state::<SaniState>().settings.clone()
}

pub fn history(app: &AppHandle) -> SharedHistory {
    app.state::<SaniState>().history.clone()
}

/// True while first-run onboarding has not been marked complete.
pub fn onboarding_incomplete(app: &AppHandle) -> bool {
    !crate::setup::snapshot(app).onboarding_complete
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
        "main" => {
            state.ui_ready_main.store(true, Ordering::Relaxed);
            log::info!("[ui-boot] main UI READY (React mounted)");
        }
        _ => {}
    }
}

// ---------------------------------------------------------------- voice flow

pub fn toggle_listening(app: &AppHandle) {
    match current_state(app) {
        // Tapping the mic while listening means "I have finished speaking".
        UiState::Listening | UiState::Preparing => finish_listening(app),
        UiState::Finalizing => cancel_listening(app),
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
                        log::warn!(
                            "[mic] authorization prompt went unanswered; staying honest about it"
                        );
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

    // Speech engine: load once, keep warm. Because the process is reused across
    // turns, `--turn-end-ms` is fixed at spawn — changing it needs a restart.
    // If a previously-spawned engine has since exited (crash/kill/EOF), respawn
    // it here; otherwise a dead handle would leave every later listen stuck in
    // Preparing with no partials ever arriving.
    let model = state.settings.read().stt_model.clone();
    let turn_end_ms = settings::stt_turn_end_ms(&state.settings.read());
    let needs_start = match state.speech.lock().as_ref() {
        None => true,
        Some(handle) => !handle.is_alive(),
    };
    if needs_start {
        match speech::start(app.clone(), &model, turn_end_ms) {
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

/// Stop / Finish: commit what the user actually said, immediately.
///
/// Pressing the mic or the hotkey is an explicit declaration that the turn is
/// over, so it must not wait out the silence deadline. The state machine is
/// deliberately left alone: the sidecar still owes us exactly one `final`, and
/// `on_final` remains the only voice->agent handoff.
pub fn finish_listening(app: &AppHandle) {
    let state = app.state::<SaniState>();
    match current_state(app) {
        // Already committed and in flight — nothing left to finish.
        // Finish is idempotent. Only Escape/Cancel may discard a transcript
        // after explicit finalization has begun.
        UiState::Finalizing => {}
        UiState::Listening | UiState::Preparing => {
            state.pending_listen.store(false, Ordering::Relaxed);
            if let Some(audio) = state.audio.lock().as_ref() {
                audio.gate.store(false, Ordering::Relaxed);
            }
            // Enter Finalizing *before* asking the decoder to drain. A
            // decoder is allowed to synchronously publish its last words
            // during flush/reset, and those words must still be accepted.
            set_state(app, UiState::Finalizing);
            if let Some(speech) = state.speech.lock().as_ref() {
                speech.control(&json!({"cmd": "flush"}));
            }
            // A flush that never produced a final must not strand us in
            // Listening; once `final` arrives the state moves on and this
            // check no longer matches.
            let app2 = app.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(1500));
                if current_state(&app2) != UiState::Finalizing {
                    return;
                }
                log::warn!("[turn] flush produced no final within 1500ms; returning to idle");
                cancel_listening(&app2);
            });
        }
        _ => {}
    }
}

/// Esc / Cancel: throw the pending utterance away. Never sends to the agent.
pub fn cancel_listening(app: &AppHandle) {
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

/// Diagnostics from the sidecar's turn committer.
///
/// Log-only, by contract: `on_final` remains the single voice->agent handoff,
/// so a `note` must never touch the state machine, the audio gate or `turn_gen`.
pub fn on_stt_note(app: &AppHandle, ev: &crate::speech::SttEvent) {
    match ev.reason.as_str() {
        "segment" => {
            log::info!(
                "[stt] segment completed chars={} segments={}",
                ev.chars,
                ev.segments
            )
        }
        "possible_end" => log::info!(
            "[turn] possible end silence_ms={} gen={} segments={}",
            ev.silence_ms,
            ev.gen,
            ev.segments
        ),
        "resumed" => log::info!(
            "[turn] speech resumed — commit cancelled gen={} gap_ms={}",
            ev.gen,
            ev.silence_ms
        ),
        "commit" => log::info!(
            "[turn] committed segments={} chars={} gen={} silence_ms={} cause={}",
            ev.segments,
            ev.chars,
            ev.gen,
            ev.silence_ms,
            ev.message
        ),
        "suppressed-filler" => {
            log::info!("[turn] suppressed filler-only candidate chars={}", ev.chars)
        }
        "vad-unavailable" => {
            log::warn!("[stt] VAD unavailable — endpointing on RMS: {}", ev.message)
        }
        "rms-rescue" => log::warn!(
            "[turn] committed without VAD speech evidence (low-confidence VAD) chars={}",
            ev.chars
        ),
        "flush-empty" => log::info!("[turn] flush requested with nothing pending"),
        "recording-limit" => {
            // A long capture must never become an implicit submission. Stop
            // accepting more audio and ask the renderer for an explicit
            // Finish or Cancel decision while preserving the accumulated text.
            if let Some(audio) = app.state::<SaniState>().audio.lock().as_ref() {
                audio.gate.store(false, Ordering::Relaxed);
            }
            let _ = app.emit(
                "sani://voice-limit",
                "Recording paused. Finish & Send or Cancel this draft.",
            );
            log::warn!("[turn] recording limit reached; awaiting explicit user action");
        }
        "unknown-cmd" => log::warn!(
            "[stt] sidecar received an unknown control command: {}",
            ev.message
        ),
        "deprecated-flag" => log::debug!("[stt] deprecated sidecar flag: {}", ev.message),
        other => log::debug!("[stt] note reason={other} message={}", ev.message),
    }
}

/// A final transcript arrived from the speech engine.
///
/// This is the only voice->agent handoff in the product. The state check runs
/// before every side effect on purpose: closing the gate and then reopening it
/// on the empty-text path used to let audio flow outside a turn whenever a late
/// final arrived while Idle or Working.
pub fn on_final(app: &AppHandle, text: String) {
    let state = app.state::<SaniState>();
    let trimmed = text.trim().to_string();
    // A final counts while capture is live or while an explicit Finish is
    // draining the decoder. Late results after Cancel/Idle/Working are never
    // allowed to create a run.
    if !matches!(current_state(app), UiState::Listening | UiState::Finalizing) {
        log::info!(
            "[turn] late final dropped state={} chars={}",
            current_state(app).as_str(),
            trimmed.chars().count()
        );
        return;
    }
    if let Some(audio) = state.audio.lock().as_ref() {
        audio.gate.store(false, Ordering::Relaxed);
    }
    if trimmed.is_empty() {
        // An explicit empty Finish is a clean no-speech result, not a hidden
        // resume of capture and never an agent request.
        if let Some(speech) = state.speech.lock().as_ref() {
            speech.control(&json!({"cmd": "discard"}));
        }
        *state.partial.lock() = String::new();
        let _ = app.emit("sani://partial", "");
        set_state(app, UiState::Idle);
        log::info!("[turn] empty final; returning to idle without a run");
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
        // `begin_turn_if_current` can return without changing anything, and
        // nothing else ever leaves Finalizing — so this is the only escape from
        // a permanently stuck Finalizing state.
        let still_ours = app_handle
            .state::<SaniState>()
            .turn_gen
            .load(Ordering::Relaxed)
            == my_gen;
        if still_ours && current_state(&app_handle) == UiState::Finalizing {
            log::error!("[turn] stuck in Finalizing; forcing error");
            set_state(&app_handle, UiState::Error);
        }
    });
}

fn can_admit_text(state: UiState) -> bool {
    matches!(state, UiState::Idle | UiState::Error)
}

/// Typed turns use the exact same admission, persistence and runtime path as
/// explicitly finalized speech. The native state is the single authority, so
/// separate WebViews can observe a run without creating a second one.
pub fn submit_text(app: &AppHandle, text: String) -> Result<(), String> {
    let text = text.trim().to_string();
    if text.is_empty() {
        return Err("Enter a message before sending.".to_string());
    }
    if !can_admit_text(current_state(app)) {
        return Err("Finish, cancel, or wait for the current request before sending another message."
            .to_string());
    }
    begin_turn(app, text);
    Ok(())
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
        log::warn!(
            "[turn] finalization skipped state={}",
            current_state(app).as_str()
        );
        return;
    }
    begin_turn(app, final_text);
}

fn ensure_active_conversation(app: &AppHandle) -> String {
    let state = app.state::<SaniState>();
    let mut settings_guard = state.settings.write();
    let existing = settings_guard.active_conversation_id.clone();
    if !existing.is_empty()
        && state
            .history
            .get_conversation(&existing)
            .ok()
            .flatten()
            .is_some()
    {
        return existing;
    }
    let id = Uuid::new_v4().to_string();
    let now = now_ms();
    if state
        .history
        .create_conversation(&id, "New conversation", now)
        .is_err()
    {
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
        &message_id,
        &conversation_id,
        "user",
        &final_text,
        now,
        &history::Attribution::default(),
    );
    let _ = state.history.rename_conversation(
        &conversation_id,
        &history::title_from_text(&final_text),
        now,
    );
    let _ = state.history.touch_conversation(&conversation_id, now);
    let _ = app.emit(
        "sani://message",
        json!({"id": message_id, "role": "user", "text": final_text, "created_at": now}),
    );

    *state.assistant_text.lock() = String::new();
    *state.assistant_message_id.lock() = String::new();
    *state.run.lock() = Some(RunGuard {
        message_id: message_id.clone(),
    });

    log::info!("begin turn: conversation={conversation_id} message={message_id}");
    set_state(app, UiState::Working);
    let _ = windows::show_panel(app);

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        // The registry decides who takes the turn; the host only asks.
        let (agent_id, agent_name) = match crate::runtime::resolve_agent(&app_handle).await {
            Some(pair) => pair,
            None => {
                agent_finished(
                    &app_handle,
                    &message_id,
                    "",
                    "",
                    false,
                    "failed",
                    "The assistant runtime has no agents available.".to_string(),
                    "",
                    "",
                );
                return;
            }
        };
        log::info!("turn routed to agent={agent_id}");
        crate::runtime::stream_turn(
            app_handle,
            message_id,
            agent_id,
            agent_name,
            final_text,
            conversation_id,
        )
        .await;
    });
}

#[cfg(test)]
mod admission_tests {
    use super::{can_admit_text, UiState};

    #[test]
    fn text_admission_only_accepts_idle_state() {
        assert!(can_admit_text(UiState::Idle));
        assert!(!can_admit_text(UiState::Listening));
        assert!(!can_admit_text(UiState::Finalizing));
        assert!(!can_admit_text(UiState::Working));
    }
}

/// Terminal path of one agent run (from `runtime::stream_turn`).
///
/// `status` is one of: "completed", "cancelled", "failed". Only a normal
/// completed run is a success (FIX-03); partial text is preserved either way,
/// but an interrupted or cancelled run is never reported as a clean success.
/// `agent_id`/`agent_name` are what make the stored answer attributable.
pub fn agent_finished(
    app: &AppHandle,
    message_id: &str,
    run_id: &str,
    text: &str,
    ok: bool,
    status: &str,
    error: String,
    agent_id: &str,
    agent_name: &str,
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

    let run_opt = if run_id.is_empty() {
        None
    } else {
        Some(run_id)
    };
    let agent_opt = if agent_id.is_empty() {
        None
    } else {
        Some(agent_id)
    };
    let name_opt = if agent_name.is_empty() {
        None
    } else {
        Some(agent_name)
    };
    if !text.trim().is_empty() {
        let assistant_id = Uuid::new_v4().to_string();
        let conversation_id = state.settings.read().active_conversation_id.clone();
        let now = now_ms();
        let attribution = history::Attribution {
            run_id: run_opt,
            agent_id: agent_opt,
            agent_name: name_opt,
        };
        let _ = state.history.append_message(
            &assistant_id,
            &conversation_id,
            "assistant",
            text,
            now,
            &attribution,
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
            "agent_id": agent_id,
            "agent_name": agent_name,
        }),
    );
    // Length, never content: the transcript stays in the local history store.
    log::info!(
        "turn finished: message={message_id} run={run_id} agent={agent_id} status={status} chars={}",
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
        // Esc always means "forget what I said", never "send it".
        UiState::Listening | UiState::Finalizing | UiState::Preparing => cancel_listening(app),
        UiState::Working => {
            // The run is already in flight inside the sidecar, so stopping it
            // means asking the live run control -- a local flag would only
            // make the UI look stopped while the computer kept moving.
            if !crate::sani_core::cancel_current_run(app) {
                log::warn!("[turn] Esc during Working but no live run to cancel");
                set_state(app, UiState::Idle);
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

// Runtime liveness is supervised by `sani_core::spawn_supervisor`: the
// assistant is Sani's own child process now, so there is nothing to poll over
// HTTP.

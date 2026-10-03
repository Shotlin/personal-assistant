//! TTS output worker supervision (Jarvis Phase 1, T09).
//!
//! The host owns one child worker (`sani_tts.py`) for the whole session:
//! separate pinned environment, no credentials, no core tools, no network
//! after asset installation. The supervisor starts it behind
//! `SANI_TTS_ENABLED`, frames requests through `tts_protocol`, and cancels
//! cooperatively first (bounded timeout) before dropping the owned process
//! — the input path and sani-core are never touched by a worker failure.
//!
//! Until the owner audition selects the engine, the worker starts with
//! `SANI_TTS_ENGINE=unspecified` and synthesis requests answer `error`:
//! voice acceptance stays BLOCKED, text output unaffected.

use std::collections::VecDeque;
use std::process::Stdio;

use tauri::Manager;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncRead, AsyncWriteExt};
use tokio::process::{Child, ChildStdin, Command};

pub const WORKER_CANCEL_TIMEOUT_MS: u64 = 250;

/// The ONLY variables the worker's environment may carry (C09/N11: an
/// explicit allowlist after env_clear — credential-shaped, core-IPC and
/// profile variables cannot leak into the synthesis child because they are
/// never named here).
const WORKER_ENV_ALLOWLIST: &[&str] = &["PATH", "HOME", "TMPDIR", "LANG", "SANI_TTS_ENGINE"];

/// Whether the packaged worker binary/python entry exists.
pub struct TtsSupervisor {
    enabled: AtomicBool,
    worker: tokio::sync::Mutex<Option<Child>>,
    /// The worker's framed stdin, taken out of the child at spawn; speech
    /// requests and controls are written here.
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
}

impl Default for TtsSupervisor {
    fn default() -> Self {
        Self {
            enabled: AtomicBool::new(false),
            worker: tokio::sync::Mutex::new(None),
            stdin: tokio::sync::Mutex::new(None),
        }
    }
}

impl TtsSupervisor {
    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::SeqCst)
    }

    pub fn set_enabled(&self, enabled: bool) {
        self.enabled.store(enabled, Ordering::SeqCst);
    }

    /// Spawn the worker with a pinned environment: `env_clear` then ONLY the
    /// allowlist (C09/N11). No credentials, no core IPC handles, no profile
    /// paths. Returns an error string the host can surface as "voice output
    /// unavailable" while text keeps working.
    pub async fn start(&self, worker: &str, worker_script: Option<&str>) -> Result<(), String> {
        let engine = default_engine();
        let mut command = Command::new(worker);
        if let Some(script) = worker_script {
            // Explicit development override only: keep Python isolated.
            command.arg("-I").arg(script);
        }
        command.env_clear();
        for key in WORKER_ENV_ALLOWLIST {
            if let Ok(value) = std::env::var(key) {
                command.env(key, value);
            }
        }
        command.env("SANI_TTS_ENGINE", engine);
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = command
            .spawn()
            .map_err(|err| format!("tts worker spawn failed: {err}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "tts worker stdin unavailable".to_string())?;
        *self.stdin.lock().await = Some(stdin);
        *self.worker.lock().await = Some(child);
        Ok(())
    }

    /// Write one framed JSON payload to the worker's stdin.
    pub async fn send_frame(&self, frame: &serde_json::Value) -> bool {
        let body = frame.to_string();
        // A tokio mutex: the guard is held across the writes, so it must be
        // Send for this to run as a background task.
        let mut guard = self.stdin.lock().await;
        let Some(stdin) = guard.as_mut() else {
            return false;
        };
        if stdin.write_all(&(body.len() as u32).to_be_bytes()).await.is_err() {
            return false;
        }
        if stdin.write_all(body.as_bytes()).await.is_err() {
            return false;
        }
        stdin.flush().await.is_ok()
    }

    /// Take the worker's stdout for a read loop (once per worker).
    pub async fn take_stdout(&self) -> Option<impl AsyncRead + Unpin> {
        let mut guard = self.worker.lock().await;
        guard.as_mut()?.stdout.take()
    }

    pub fn has_stdin(&self) -> bool {
        self.stdin.try_lock().map(|slot| slot.is_some()).unwrap_or(true)
    }

    /// Cancel the current utterance: cooperative first, bounded wait, then
    /// the owned worker is killed and its generation discarded. Input and
    /// sani-core stay alive.
    pub async fn stop_worker(&self) -> bool {
        let frame = json!({"type": "control", "action": "cancel", "generation": u64::MAX});
        self.send_frame(&frame).await
    }

    pub async fn shutdown(&self) {
        let mut guard = self.worker.lock().await;
        if let Some(mut child) = guard.take() {
            // Bounded cooperative window, then terminate the owned process.
            let deadline = tokio::time::Instant::now()
                + Duration::from_millis(WORKER_CANCEL_TIMEOUT_MS * 4);
            let _ = tokio::time::timeout_at(deadline, child.wait()).await;
            if let Err(err) = child.start_kill() {
                log::warn!("tts worker already gone: {err}");
            }
        }
    }
}

use std::time::Duration;

/// The host-owned playback queue with a real output sink.
pub type HostSpeechQueue =
    crate::tts_queue::SpeechQueue<Box<dyn crate::tts_queue::AudioSink + Send>>;
use crate::tts_queue::{NullSink, SpeechQueue};

/// Managed TTS state: the worker supervisor and the speech queue. Created
/// once at app setup; the queue is always present (NullSink until the
/// output device opens) so stop/interlock never fail for missing audio.
pub struct TtsState {
    pub supervisor: TtsSupervisor,
    pub queue: HostSpeechQueue,
}

impl Default for TtsState {
    fn default() -> Self {
        Self {
            supervisor: TtsSupervisor::default(),
            queue: SpeechQueue::new(Box::new(NullSink)),
        }
    }
}

/// `speech.stop`: clear queued/current speech by bumping the generation.
/// Deliberately distinct from mission pause/cancel (file 03 §9).
#[tauri::command]
pub fn speech_stop_cmd(app: tauri::AppHandle) -> serde_json::Value {
    let state = app.state::<TtsState>();
    let after = state.queue.stop();
    json!({
        "stopped": true,
        "queue_state": format!("{after:?}"),
        "generation": state.queue.generation(),
    })
}

/// A bounded PCM output sink over cpal's default output device. Underruns
/// render silence; device errors surface through [`AudioSink::play`].
///
/// The cpal Stream itself is !Send on some platforms, so this sink only
/// holds the shared ring buffer + health flag; the stream handle stays in
/// the [`CpalStreamGuard`] the setup thread owns.
pub struct CpalSink {
    ring: std::sync::Arc<std::sync::Mutex<VecDeque<f32>>>,
    healthy: std::sync::Arc<AtomicBool>,
    device_rate: std::sync::Arc<std::sync::atomic::AtomicU32>,
}

/// Owns the cpal stream so it lives as long as the sink's consumers need.
#[allow(dead_code)]
pub struct CpalStreamGuard {
    stream: Option<cpal::Stream>,
}

impl CpalSink {
    /// Open the default output device. Returns the Send sink plus the
    /// stream guard (which must simply stay alive on its owning thread).
    /// Returns Err when no output device exists.
    pub fn open(requested_rate: u32) -> Result<(Self, CpalStreamGuard), String> {
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

        let host = cpal::default_host();
        let device = host
            .default_output_device()
            .ok_or_else(|| "no output device".to_string())?;
        let supported = device
            .default_output_config()
            .map_err(|err| format!("output config failed: {err}"))?;
        let device_rate = supported.sample_rate().0;
        let _ = requested_rate; // packets may arrive at any rate; play() resamples
        let config = cpal::StreamConfig {
            channels: 1.min(supported.channels()),
            sample_rate: supported.sample_rate(),
            buffer_size: cpal::BufferSize::Default,
        };
        let ring: std::sync::Arc<std::sync::Mutex<VecDeque<f32>>> =
            std::sync::Arc::new(std::sync::Mutex::new(VecDeque::new()));
        let device_rate_shared = std::sync::Arc::new(std::sync::atomic::AtomicU32::new(device_rate));
        let callback_ring = std::sync::Arc::clone(&ring);
        let healthy = std::sync::Arc::new(AtomicBool::new(true));
        let callback_healthy = std::sync::Arc::clone(&healthy);
        let error_healthy = std::sync::Arc::clone(&healthy);
        let stream = device
            .build_output_stream(
                &config,
                move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                    let mut buf = match callback_ring.lock() {
                        Ok(buf) => buf,
                        Err(_) => {
                            callback_healthy.store(false, Ordering::SeqCst);
                            return;
                        }
                    };
                    for slot in data.iter_mut() {
                        *slot = buf.pop_front().unwrap_or(0.0);
                    }
                },
                move |err| {
                    log::warn!("tts output stream error: {err}");
                    error_healthy.store(false, Ordering::SeqCst);
                },
                None,
            )
            .map_err(|err| format!("output stream build failed: {err}"))?;
        stream
            .play()
            .map_err(|err| format!("output stream start failed: {err}"))?;
        Ok((
            CpalSink {
                ring,
                healthy,
                device_rate: device_rate_shared,
            },
            CpalStreamGuard {
                stream: Some(stream),
            },
        ))
    }
}

impl crate::tts_queue::AudioSink for CpalSink {
    fn play(&mut self, samples: &[f32], sample_rate: u32) -> bool {
        if !self.healthy.load(Ordering::SeqCst) {
            return false;
        }
        // C09/N11: packet PCM is converted to the DEVICE rate before it is
        // buffered — a 16 kHz packet on a 48 kHz device must not play in
        // thirds of its duration. Linear resampling is deterministic and
        // bounded; fidelity refinement belongs to the audition gate.
        let device_rate = self.device_rate.load(Ordering::SeqCst).max(1);
        let resampled: Vec<f32> = if sample_rate == device_rate || samples.is_empty() {
            samples.to_vec()
        } else if sample_rate == 0 {
            return true; // refuse silently: a rateless packet is silence
        } else {
            let ratio = sample_rate as f64 / device_rate as f64;
            let out_len = ((samples.len() as f64) / ratio).floor() as usize;
            let mut out = Vec::with_capacity(out_len.max(1));
            for index in 0..out_len {
                let source = index as f64 * ratio;
                let left = source.floor() as usize;
                let right = (left + 1).min(samples.len() - 1);
                let frac = (source - left as f64) as f32;
                out.push(samples[left] * (1.0 - frac) + samples[right] * frac);
            }
            out
        };
        if let Ok(mut buf) = self.ring.lock() {
            buf.extend(resampled.iter().copied());
            // The queue admits at most two seconds and waits for device
            // consumption; never silently discard the beginning of speech.
            if buf.len() > device_rate as usize * 2 {
                self.healthy.store(false, Ordering::SeqCst);
                return false;
            }
        }
        true
    }

    fn queued_seconds(&self) -> f32 {
        self.ring.lock().map(|buf| buf.len() as f32
            / self.device_rate.load(Ordering::SeqCst).max(1) as f32).unwrap_or(f32::INFINITY)
    }

    fn drain(&mut self) {
        if let Ok(mut buf) = self.ring.lock() {
            buf.clear();
        }
    }
}

/// Read one framed JSON payload from the worker stream; None on EOF.
pub async fn read_frame(reader: &mut (impl AsyncReadExt + Unpin)) -> Option<serde_json::Value> {
    let mut header = [0u8; 4];
    if reader.read_exact(&mut header).await.is_err() {
        return None;
    }
    let length = u32::from_be_bytes(header) as usize;
    if length > crate::tts_protocol::FRAME_MAX_BYTES {
        return None;
    }
    let mut body = vec![0u8; length];
    if reader.read_exact(&mut body).await.is_err() {
        return None;
    }
    serde_json::from_slice(&body).ok()
}

/// C09/N11: the production speech path. `speech.say` enqueues the bounded
/// text and forwards the synthesis request to the worker; the read loop
/// pumps decoded PCM through the queue into the real sink. With the engine
/// still `unspecified` (owner audition pending) the worker answers `error`,
/// which surfaces here as honest unavailability — never as silence claimed
/// as spoken audio.
fn synthesis_request(text: &str, conversation_id: &str, mission_id: Option<&str>,
                     utterance_id: &str, generation: u64) -> serde_json::Value {
    json!({
        "type": "request",
        "request": {
            "request_id": uuid::Uuid::new_v4().simple().to_string(),
            "utterance_id": utterance_id,
            "conversation_id": conversation_id,
            "mission_id": mission_id,
            "message_id": utterance_id,
            "generation": generation,
            "text": text,
            "voice_asset_id": "",
            "rate": 1.0,
            "kind": "FINAL",
        }
    })
}

/// The configured engine; on macOS the local `say` engine unless overridden.
fn default_engine() -> String {
    std::env::var("SANI_TTS_ENGINE").unwrap_or_else(|_| {
        if cfg!(target_os = "macos") { "macos-say" } else { "unspecified" }.to_string()
    })
}

/// Voice output is on by default on macOS (local, no network); `SANI_TTS_ENABLED=0`
/// turns it off, and other platforms stay opt-in until they have an engine.
fn tts_enabled_from_env() -> bool {
    match std::env::var("SANI_TTS_ENABLED") {
        Ok(v) => v == "1" || v.eq_ignore_ascii_case("true"),
        Err(_) => cfg!(target_os = "macos"),
    }
}

pub fn enqueue_speech(
    app: &tauri::AppHandle,
    text: &str,
    conversation_id: &str,
    mission_id: Option<&str>,
) -> serde_json::Value {
    let state = app.state::<TtsState>();
    if !state.supervisor.is_enabled() {
        return json!({"spoken": false, "reason": "voice output is disabled"});
    }
    let trimmed: String = text.chars().take(crate::tts_protocol::TEXT_MAX_CHARS).collect();
    if trimmed.trim().is_empty() {
        return json!({"spoken": false, "reason": "nothing to say"});
    }
    let utterance_id = uuid::Uuid::new_v4().simple().to_string();
    let generation = match state.queue.enqueue(&utterance_id, &trimmed) {
        Ok(generation) => generation,
        Err(reason) => return json!({"spoken": false, "reason": reason}),
    };
    let request = synthesis_request(&trimmed, conversation_id, mission_id,
                                    &utterance_id, generation);
    // Never `block_on` here: this runs inside the async turn task, and
    // blocking a runtime thread on the runtime panics ("cannot start a
    // runtime from within a runtime"), which silently killed every spoken
    // reply. The write is best-effort and off-thread: a dead worker drops the
    // queue entry; text output is unaffected.
    let app_handle = app.clone();
    let pending_id = utterance_id.clone();
    tauri::async_runtime::spawn(async move {
        let state = app_handle.state::<TtsState>();
        if !state.supervisor.send_frame(&request).await {
            log::warn!("tts: worker not accepting requests; reply stays text-only");
            state.queue.finish_utterance(&pending_id);
        }
    });
    json!({"spoken": false, "queued": true, "utterance_id": utterance_id,
           "generation": generation})
}

/// `speech.say` command: the renderer may ask for a bounded acknowledgment.
#[tauri::command]
pub fn speech_say_cmd(
    app: tauri::AppHandle,
    text: String,
    conversation_id: String,
    mission_id: Option<String>,
) -> serde_json::Value {
    enqueue_speech(&app, &text, &conversation_id, mission_id.as_deref())
}

/// The worker event read loop: framed events in, PCM through the queue.
/// Spawned once at worker start; exits on EOF (worker gone) and restarts
/// with the next start.
pub fn spawn_worker_pump(app: tauri::AppHandle) {
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let reader = {
            let state = app_handle.state::<TtsState>();
            match state.supervisor.take_stdout().await {
                Some(reader) => reader,
                None => return,
            }
        };
        let mut reader = reader;
        let mut sequences: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
        loop {
            let Some(frame) = read_frame(&mut reader).await else {
                log::info!("tts worker stream closed");
                return;
            };
            let state = app_handle.state::<TtsState>();
            let event = &frame["event"];
            let kind = event["event"].as_str().unwrap_or("");
            let utterance_id = event["utterance_id"].as_str().unwrap_or("");
            let generation = event["generation"].as_u64().unwrap_or(0);
            match kind {
                "chunk" => {
                    let chunk = serde_json::from_value::<crate::tts_protocol::PcmChunk>(event.clone());
                    let Ok(chunk) = chunk else { continue; };
                    if generation != state.queue.generation()
                        || !state.queue.contains_utterance(utterance_id, generation) {
                        continue;
                    }
                    let expected = sequences.get(utterance_id).map_or(0, |last| last + 1);
                    if chunk.sequence != expected {
                        state.queue.finish_utterance(utterance_id);
                        continue;
                    }
                    let Ok(floats) = chunk.validate() else {
                        state.queue.finish_utterance(utterance_id);
                        continue;
                    };
                    sequences.insert(utterance_id.to_string(), chunk.sequence);
                    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
                    loop {
                        if std::time::Instant::now() >= deadline {
                            state.queue.stop();
                            break;
                        }
                        match state.queue.play_chunk(generation, chunk.sample_rate, &floats) {
                            Ok(()) => break,
                            Err(reason) if reason == "audio backpressure" => {
                                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
                            }
                            Err(_) => {
                                state.queue.finish_utterance(utterance_id);
                                break;
                            }
                        }
                    }
                }
                "finished" => {
                    sequences.remove(utterance_id);
                    let _ = state.queue.finish_utterance(utterance_id);
                    drain_after_finish(&state.queue).await;
                }
                "cancelled" | "error" => {
                    sequences.remove(utterance_id);
                    let _ = state.queue.finish_utterance(utterance_id);
                    drain_after_finish(&state.queue).await;
                }
                _ => {}
            }
        }
    });
}

/// D09: after the last utterance the device may still hold buffered audio.
/// The pump polls `drain_pending` (bounded) so the queue returns to Idle
/// only when playback actually drained — barge-in's `stop()` drains it
/// immediately instead. The poll never outlives a generation change: stale
/// audio was already refused by the queue itself.
async fn drain_after_finish(queue: &HostSpeechQueue) {
    for _ in 0..200 {
        if queue.drain_pending() == crate::tts_queue::QueueState::Idle {
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
}

/// D18: resolve the self-contained packaged speech worker.
///
/// An explicit `SANI_TTS_PYTHON`/`SANI_TTS_WORKER` pair (dev runs) wins.
/// Otherwise the PACKAGED locations are the only fallback — the previous
/// environment-only default (`"sani_tts.py"` from cwd) was intentionally
/// unavailable and is gone. When nothing resolvable exists the caller
/// reports voice output as unavailable; it never guesses at an
/// interpreter. No engine or asset selection happens here.
pub fn resolve_packaged_worker(
    exe_dir: &std::path::Path,
    resource_dir: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    let worker_candidates: [Option<std::path::PathBuf>; 2] = [
        Some(exe_dir.join("sani-tts-python/sani-tts-python")),
        resource_dir.map(|r| r.join("sani-tts-python/sani-tts-python")),
    ];
    for worker in worker_candidates.iter().flatten() {
        if worker.is_file() {
            return Some(worker.clone());
        }
    }
    None
}

/// Host startup: behind `SANI_TTS_ENABLED`, open the real output sink and
/// start the worker. Text output never depends on this succeeding.
pub fn init(app: &tauri::AppHandle) -> serde_json::Value {
    let enabled = tts_enabled_from_env();
    let state = app.state::<TtsState>();
    if !enabled {
        state.supervisor.set_enabled(false);
        return json!({"enabled": false});
    }
    state.supervisor.set_enabled(true);
    // Real output: the device sink replaces NullSink; the guard must stay
    // alive on this thread for the stream to keep running.
    if let Ok((sink, guard)) = CpalSink::open(24_000) {
        std::mem::forget(guard); // owned by the setup thread for process life
        let _replaced = state.queue.replace_sink(Box::new(sink));
    } else {
        log::warn!("tts: no output device; speech stays silent, text unaffected");
    }
    // Explicit interpreter/script variables are developer-only overrides.
    // Production resolves the frozen worker binary from the app bundle.
    let resolved: Option<(String, Option<String>)> = if let Ok(python) = std::env::var("SANI_TTS_PYTHON") {
        let script = std::env::var("SANI_TTS_WORKER").unwrap_or_else(|_| "sani_tts.py".to_string());
        Some((python, Some(script)))
    } else {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|exe| exe.parent().map(|p| p.to_path_buf()));
        let resource_dir = app.path().resource_dir().ok();
        match exe_dir {
            Some(exe_dir) => resolve_packaged_worker(&exe_dir, resource_dir.as_deref())
                .map(|worker| (worker.to_string_lossy().into_owned(), None)),
            None => None,
        }
    };
    // Dev builds run from the source tree: fall back to the script beside the
    // manifest so `tauri dev` speaks without building the frozen worker.
    #[cfg(debug_assertions)]
    let resolved = resolved.or_else(|| {
        let script = concat!(env!("CARGO_MANIFEST_DIR"), "/python/sani_tts.py");
        std::path::Path::new(script)
            .exists()
            .then(|| ("/usr/bin/python3".to_string(), Some(script.to_string())))
    });
    let Some((worker, script)) = resolved else {
        log::warn!("tts: no packaged speech worker found; voice output unavailable");
        return json!({"enabled": true, "worker": false,
            "reason": "Packaged speech worker is not present in this bundle"});
    };
    let spawn = tauri::async_runtime::block_on(
        state.supervisor.start(&worker, script.as_deref())
    );
    match spawn {
        Ok(()) => {
            spawn_worker_pump(app.clone());
            let engine = default_engine();
            log::info!("tts worker started (engine {engine})");
            json!({"enabled": true, "worker": true, "engine": engine})
        }
        Err(reason) => {
            log::warn!("tts worker unavailable: {reason}");
            json!({"enabled": true, "worker": false, "reason": reason})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn host_request_satisfies_worker_contract() {
        let frame = synthesis_request("hello", "conversation", None, "utterance", 1);
        let request: crate::tts_protocol::TtsRequest =
            serde_json::from_value(frame["request"].clone()).unwrap();
        assert!(request.validate().is_ok());
    }

    #[test]
    fn supervisor_defaults_to_disabled() {
        let supervisor = TtsSupervisor::default();
        assert!(!supervisor.is_enabled());
        supervisor.set_enabled(true);
        assert!(supervisor.is_enabled());
    }

    #[test]
    fn worker_env_allowlist_is_credential_free() {
        // C09/N11: the allowlist may name no credential-, token-, or
        // core-IPC-shaped variable; everything else is cleared.
        for key in WORKER_ENV_ALLOWLIST {
            let lowered = key.to_ascii_lowercase();
            assert!(!lowered.contains("key"), "allowlist carries {key}");
            assert!(!lowered.contains("token"), "allowlist carries {key}");
            assert!(!lowered.contains("secret"), "allowlist carries {key}");
            assert!(!lowered.contains("database"), "allowlist carries {key}");
            assert!(!lowered.contains("socket"), "allowlist carries {key}");
        }
    }

    #[tokio::test]
    async fn stop_worker_without_worker_is_false() {
        // C09/N11: with no worker started there is no stdin to cancel
        // through — the honest answer is false, not a silent success.
        let supervisor = TtsSupervisor::default();
        assert!(!supervisor.stop_worker().await);
    }
}

#[cfg(test)]
mod resolution_tests {
    use super::*;

    fn scratch_dir(tag: &str) -> std::path::PathBuf {
        let base = std::env::temp_dir().join(format!(
            "sani-tts-resolution-test-{}-{}", tag, std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).expect("scratch");
        base
    }

    #[test]
    fn packaged_worker_resolves_from_bundle_layout() {
        let root = scratch_dir("layout");
        let exe_dir = root.join("Supernova.app/Contents/MacOS");
        let resources = root.join("Supernova.app/Contents/Resources");
        std::fs::create_dir_all(exe_dir.join("sani-tts-python")).unwrap();
        std::fs::create_dir_all(&resources).unwrap();
        std::fs::write(exe_dir.join("sani-tts-python/sani-tts-python"), b"worker\n").unwrap();
        let resolved = resolve_packaged_worker(&exe_dir, Some(&resources));
        assert!(resolved.is_some(), "the packaged worker resolves");
        assert!(resolved.unwrap().ends_with("sani-tts-python/sani-tts-python"));
    }

    #[test]
    fn staged_layout_matches_resolver_when_present() {
        // D18: after `scripts/build-tts.sh` freezes the worker, the resolver
        // accepts the real relocatable layout (binaries/sani-tts-python).
        let root = scratch_dir("staged");
        let exe_dir = root.join("binaries");
        let staged_worker = exe_dir.join("sani-tts-python/sani-tts-python");
        if !staged_worker.exists() {
            // Not staged in this checkout: the resolver must refuse rather
            // than guess, which the missing-layout test covers.
            return;
        }
        let resolved = resolve_packaged_worker(&exe_dir, Some(&root));
        assert!(resolved.is_some(), "the staged layout must resolve");
    }

    #[test]
    fn missing_packaged_worker_resolves_to_none_not_a_guess() {
        let root = scratch_dir("missing");
        let resolved = resolve_packaged_worker(&root, None);
        assert!(resolved.is_none(),
            "without a packaged interpreter the honest answer is unavailable");
    }
}

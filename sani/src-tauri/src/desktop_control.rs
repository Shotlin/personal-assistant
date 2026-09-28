//! Desktop control authority for the host (Jarvis Phase 1, T05).
//!
//! The host owns the physical stop. This module keeps a process-lifetime
//! latch and generation that are deliberately independent of the sani-core
//! operation mutex and the model stream: an emergency stop must work while
//! the model is stalled or the IPC is wedged (file 03 §8, TC-34).
//!
//! - [`EmergencyStop`] latches synchronously and bumps a generation counter
//!   the rest of the host reads atomically. Latching never awaits.
//! - [`emergency_stop_cmd`] latches, signals the live run's out-of-band
//!   cancel watch (never the operation mutex), and reports the ACTUAL state
//!   produced. Whether held physical input was released is a driver
//!   capability question this module does not claim to answer.
//! - [`stop_generation`] is the fence the host stamps into the core's
//!   environment at launch; a generation change only takes effect after the
//!   old core/driver is demonstrably stopped.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;

use tauri::Manager;

use serde_json::json;

/// Process-lifetime emergency stop latch.
///
/// Fields are atomics so latching is lock-free from any thread, including a
/// hotkey thread while the async runtime is busy.
pub struct EmergencyStop {
    latched: AtomicBool,
    generation: AtomicU64,
    /// When the latch was armed, as a monotonic millisecond stamp, for the
    /// stop acceptance measurement (proposed gate: latch < 100 ms local).
    latched_at_ms: Mutex<Option<u128>>,
}

impl Default for EmergencyStop {
    fn default() -> Self {
        Self {
            latched: AtomicBool::new(false),
            generation: AtomicU64::new(1),
            latched_at_ms: Mutex::new(None),
        }
    }
}

impl EmergencyStop {
    /// Latch now: no new dispatch may pass while this is set.
    ///
    /// Returns the generation after the bump. Synchronous and immediate.
    pub fn latch(&self) -> u64 {
        let generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        self.latched.store(true, Ordering::SeqCst);
        if let Ok(mut slot) = self.latched_at_ms.lock() {
            *slot = Some(monotonic_ms());
        }
        generation
    }

    pub fn is_latched(&self) -> bool {
        self.latched.load(Ordering::SeqCst)
    }

    /// The current stop generation. Runs record the generation they started
    /// under; a run whose recorded generation no longer matches must not
    /// dispatch anything new.
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    /// Explicit re-arm after reconciliation. Never called automatically.
    pub fn reset(&self) {
        self.latched.store(false, Ordering::SeqCst);
        if let Ok(mut slot) = self.latched_at_ms.lock() {
            *slot = None;
        }
    }

    /// Milliseconds since the latch, for stop-latency measurement.
    pub fn ms_since_latch(&self) -> Option<u128> {
        let slot = self.latched_at_ms.lock().ok()?;
        let at = (*slot)?;
        Some(monotonic_ms().saturating_sub(at))
    }
}

fn monotonic_ms() -> u128 {
    use std::time::Instant;
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    let start = START.get_or_init(Instant::now);
    start.elapsed().as_millis()
}

/// Latch the stop and signal every out-of-band cancellation the host owns.
///
/// This command takes NO async lock: it runs on the command thread, bumps
/// the generation, and only *signals* the live run (the watch-channel cancel
/// is out-of-band by design). The returned JSON reports what actually
/// happened, including whether a run was there to cancel at all.
#[tauri::command]
pub fn emergency_stop_cmd(app: tauri::AppHandle) -> serde_json::Value {
    let stop = app.state::<EmergencyStop>();
    let generation = stop.latch();
    let run_cancelled = crate::sani_core::cancel_current_run(&app);
    // C06/N09: emergency stop actually stops speech — the TTS queue is
    // cleared by generation bump here, not by an unconnected comment. The
    // input path keeps its own manual Finish&Send gate.
    let speech_stopped = app
        .try_state::<crate::tts::TtsState>()
        .map(|state| {
            let _after = state.queue.stop();
            true
        })
        .unwrap_or(false);
    log::warn!(
        "desktop emergency stop latched: generation={} run_cancelled={} speech_stopped={}",
        generation,
        run_cancelled,
        speech_stopped
    );
    json!({
        "latched": true,
        "stop_generation": generation,
        "run_cancel_requested": run_cancelled,
        "speech_stopped": speech_stopped,
        "ms_since_latch": stop.ms_since_latch(),
    })
}

/// Re-arm after reconciliation. Explicit owner action only.
#[tauri::command]
pub fn emergency_stop_reset_cmd(app: tauri::AppHandle) -> serde_json::Value {
    let stop = app.state::<EmergencyStop>();
    stop.reset();
    json!({"latched": false, "stop_generation": stop.generation()})
}

/// Read the current fence state (diagnostics + tests).
#[tauri::command]
pub fn desktop_stop_state(app: tauri::AppHandle) -> serde_json::Value {
    let stop = app.state::<EmergencyStop>();
    json!({
        "latched": stop.is_latched(),
        "stop_generation": stop.generation(),
        "ms_since_latch": stop.ms_since_latch(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn latch_is_immediate_and_bumps_generation() {
        let stop = EmergencyStop::default();
        assert!(!stop.is_latched());
        let generation = stop.generation();
        let latched_generation = stop.latch();
        assert!(stop.is_latched());
        assert_eq!(latched_generation, generation + 1);
        assert!(stop.ms_since_latch().unwrap() < 100);
    }

    #[test]
    fn reset_clears_the_latch_but_not_the_generation() {
        let stop = EmergencyStop::default();
        stop.latch();
        stop.reset();
        assert!(!stop.is_latched());
        assert!(stop.generation() >= 2, "the generation never goes back");
    }

    #[test]
    fn latching_from_many_threads_is_lossless() {
        use std::sync::Arc;
        let stop = Arc::new(EmergencyStop::default());
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let stop = Arc::clone(&stop);
                std::thread::spawn(move || stop.latch())
            })
            .collect();
        let mut generations: Vec<u64> =
            handles.into_iter().map(|h| h.join().unwrap()).collect();
        generations.sort_unstable();
        generations.dedup();
        assert_eq!(
            generations.len(),
            8,
            "each latch got a unique generation: {generations:?}"
        );
        assert!(stop.is_latched());
    }
}

// Independent review: schedule-amplified stale-drain snapshot. Dummy sink only.
use std::sync::atomic::AtomicBool;
static SNAPSHOT_TAKEN: AtomicBool = AtomicBool::new(false);
static RESUME_DRAIN: AtomicBool = AtomicBool::new(false);
static QUEUED: AtomicU64 = AtomicU64::new(0);
// Bounded speech playback queue (Jarvis Phase 1, T10).
//
// States: DISABLED -> LOADING -> IDLE -> SYNTHESIZING -> PLAYING ->
// CANCELLING -> ERROR — separate from mission status. `speech.stop`
// clears queued and current speech by bumping the generation; it does not
// cancel a mission. The queue bounds memory (3 queued utterances, 2
// seconds of decoded PCM) and drops stale generations instead of playing
// them.
//
// Output goes through the [`AudioSink`] trait so tests can drive the whole
// queue without a real audio device. The device sink (cpal) lands with the
// packaged bundle once the engine audition passes; queue semantics are
// proven here.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// What a drained audio device would do. Tests replace it.
pub trait AudioSink: Send {
    /// Play one chunk; returns true when the sink is still healthy.
    fn play(&mut self, samples: &[f32], sample_rate: u32) -> bool;
    /// Drain whatever is buffered; best-effort, bounded.
    fn drain(&mut self);
    /// Audio already admitted but not consumed by the device.
    fn queued_seconds(&self) -> f32 { 0.0 }
}

/// A no-output sink for tests and for the pre-audition state.
#[derive(Default)]
pub struct NullSink;

impl AudioSink for NullSink {
    fn play(&mut self, _samples: &[f32], _sample_rate: u32) -> bool {
        true
    }
    fn drain(&mut self) {}
}

impl AudioSink for Box<dyn AudioSink + Send> {
    fn play(&mut self, samples: &[f32], sample_rate: u32) -> bool {
        (**self).play(samples, sample_rate)
    }
    fn drain(&mut self) {
        (**self).drain()
    }
    fn queued_seconds(&self) -> f32 { (**self).queued_seconds() }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueState {
    Disabled,
    Loading,
    Idle,
    Synthesizing,
    Playing,
    Cancelling,
    Error,
}

#[derive(Debug, Clone, PartialEq)]
pub struct QueuedUtterance {
    pub utterance_id: String,
    pub generation: u64,
    pub text: String,
}

pub struct SpeechQueue<S: AudioSink> {
    sink: Mutex<S>,
    state: Mutex<QueueState>,
    generation: AtomicU64,
    queue: Arc<Mutex<VecDeque<QueuedUtterance>>>,
    buffered_samples: Arc<Mutex<usize>>,
    max_queue: usize,
    max_buffered_samples: usize,
}

pub const MAX_QUEUED_UTTERANCES: usize = 3;
/// Two seconds of decoded f32 mono PCM at the default rate.
pub const MAX_BUFFERED_SECONDS: f32 = 2.0;

impl<S: AudioSink> SpeechQueue<S> {
    pub fn new(sink: S) -> Self {
        Self {
            sink: Mutex::new(sink),
            state: Mutex::new(QueueState::Idle),
            generation: AtomicU64::new(1),
            queue: Arc::new(Mutex::new(VecDeque::new())),
            buffered_samples: Arc::new(Mutex::new(0)),
            max_queue: MAX_QUEUED_UTTERANCES,
            max_buffered_samples: (24_000 * 4 * MAX_BUFFERED_SECONDS as usize) as usize,
        }
    }

    pub fn state(&self) -> QueueState {
        *self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    pub fn queue_len(&self) -> usize {
        self.queue.lock().map(|q| q.len()).unwrap_or(0)
    }

    pub fn contains_utterance(&self, id: &str, generation: u64) -> bool {
        self.queue.lock().map(|q| q.iter().any(|u|
            u.utterance_id == id && u.generation == generation)).unwrap_or(false)
    }

    /// Enqueue one utterance for synthesis + playback under the current
    /// generation. A full queue applies backpressure by refusing.
    pub fn enqueue(&self, utterance_id: &str, text: &str) -> Result<u64, String> {
        let mut queue = self
            .queue
            .lock()
            .map_err(|_| "speech queue poisoned".to_string())?;
        if queue.len() >= self.max_queue {
            return Err("speech queue is full; text remains available".to_string());
        }
        let generation = self.generation.load(Ordering::SeqCst);
        queue.push_back(QueuedUtterance {
            utterance_id: utterance_id.to_string(),
            generation,
            text: text.to_string(),
        });
        *self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) =
            QueueState::Synthesizing;
        Ok(generation)
    }

    /// C09/N11: install the real output sink once the device opens. The
    /// queue is created with NullSink at startup so stop/interlock always
    /// exist; this swaps in the device sink without touching state.
    pub fn replace_sink(&self, sink: S) -> Option<S> {
        let mut guard = self.sink.lock().ok()?;
        Some(std::mem::replace(&mut *guard, sink))
    }

    /// `speech.stop`: bump the generation, clear queued speech, report the
    /// actual playback state. Never touches mission controls.
    pub fn stop(&self) -> QueueState {
        // D17: ONE global lock order — queue, then sink, then buffered,
        // then state. The previous order (buffered before sink) inverted
        // against play_chunk (sink before buffered) and could deadlock.
        let previous = self
            .generation
            .fetch_add(1, Ordering::SeqCst);
        let _ = previous;
        {
            let mut queue = self
                .queue
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            queue.clear();
        }
        if let Ok(mut sink) = self.sink.lock() {
            sink.drain();
        }
        *self.buffered_samples
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = 0;
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if *state == QueueState::Playing || *state == QueueState::Synthesizing {
            *state = QueueState::Cancelling;
            *state = QueueState::Idle;
        }
        *state
    }

    /// Whether this chunk may still play: generation and buffer bounds.
    pub fn accepts(&self, generation: u64, sample_count: usize) -> Result<(), String> {
        if generation != self.generation() {
            return Err(format!("stale generation {generation}"));
        }
        let mut buffered = self
            .buffered_samples
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let max = self.max_buffered_samples;
        if *buffered + sample_count > max {
            return Err("decoder buffer bound exceeded".to_string());
        }
        *buffered += sample_count;
        Ok(())
    }

    /// Play one accepted chunk through the sink.
    pub fn play_chunk(&self, generation: u64, sample_rate: u32, samples: &[f32]) -> Result<(), String> {
        let Ok(mut sink) = self.sink.lock() else {
            return Err("audio sink poisoned".to_string());
        };
        if sample_rate == 0 || samples.len() as f32 / sample_rate as f32 > MAX_BUFFERED_SECONDS {
            return Err("decoder buffer bound exceeded".to_string());
        }
        if samples.iter().any(|v| !v.is_finite()) {
            return Err("invalid PCM sample".to_string());
        }
        if sink.queued_seconds() + samples.len() as f32 / sample_rate as f32 > MAX_BUFFERED_SECONDS {
            return Err("audio backpressure".to_string());
        }
        // Holding the sink lock makes stop/drain ordered with this final
        // generation check and enqueue; no await separates them.
        self.accepts(generation, samples.len())?;
        *self.state.lock().unwrap_or_else(|p| p.into_inner()) = QueueState::Playing;
        let healthy = sink.play(samples, sample_rate);
        let mut buffered = self.buffered_samples.lock().unwrap_or_else(|p| p.into_inner());
        *buffered = buffered.saturating_sub(samples.len());
        drop(buffered);
        if !healthy {
            *self
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) = QueueState::Error;
            return Err("audio sink failed".to_string());
        }
        Ok(())
    }

    /// Mark a finished utterance; the queue returns to Idle only when the
    /// sink holds no buffered audio (D09: device-drain awareness).
    ///
    /// The synthesis queue can be empty while the OUTPUT DEVICE still has
    /// up to two seconds of decoded PCM buffered. Returning to Idle then
    /// would release the STT interlock (barge-in) while audible speech is
    /// still playing — so the state stays Playing while audio drains, and
    /// the host pump polls [`Self::drain_pending`]. `stop()` always drains
    /// immediately, which is exactly what barge-in wants.
    pub fn finish_utterance(&self, utterance_id: &str) -> usize {
        // D17: queue -> sink -> state (the global order) — state is never
        // taken while another thread's drain path holds it and waits for
        // the queue or the sink.
        let mut queue = self
            .queue
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        queue.retain(|item| item.utterance_id != utterance_id);
        let remaining = queue.len();
        drop(queue);
        if remaining == 0 {
            let buffered = self
                .sink
                .lock()
                .map(|sink| sink.queued_seconds())
                .unwrap_or(0.0);
            let mut state = self
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            *state = if buffered > 0.0 {
                QueueState::Playing
            } else {
                QueueState::Idle
            };
        }
        remaining
    }

    /// D09: called by the pump after the last utterance finishes. Once the
    /// device has consumed the buffered audio, the queue returns to Idle;
    /// while audio is still buffered it stays Playing so the STT interlock
    /// keeps covering the audible tail (or barge-in stops it, which drains).
    pub fn drain_pending(&self) -> QueueState {
        // D17: each lock is taken and DROPPED in turn (queue, sink, state)
        // — never nested — so drain cannot invert against finish/play/stop.
        // A stale read at worst keeps Playing one poll longer, which the
        // pump simply retries.
        let queue_len = self.queue_len();
        let buffered = self
            .sink
            .lock()
            .map(|sink| sink.queued_seconds())
            .unwrap_or(f32::INFINITY);
        SNAPSHOT_TAKEN.store(true, Ordering::SeqCst);
        while !RESUME_DRAIN.load(Ordering::SeqCst) { std::thread::yield_now(); }
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if *state == QueueState::Playing && queue_len == 0 && buffered <= 0.0 {
            *state = QueueState::Idle;
        }
        *state
    }
}


struct ProbeSink;
impl AudioSink for ProbeSink {
 fn play(&mut self,_samples:&[f32],_rate:u32)->bool {QUEUED.store(100,Ordering::SeqCst);true}
 fn drain(&mut self){QUEUED.store(0,Ordering::SeqCst);}
 fn queued_seconds(&self)->f32{QUEUED.load(Ordering::SeqCst) as f32/1000.0}
}
fn main(){
 let queue=Arc::new(SpeechQueue::new(ProbeSink));
 queue.play_chunk(1,24000,&[0.0;24]).unwrap();
 QUEUED.store(0,Ordering::SeqCst); // previous audio drained
 let other=Arc::clone(&queue);
 let drain=std::thread::spawn(move||other.drain_pending());
 while !SNAPSHOT_TAKEN.load(Ordering::SeqCst){std::thread::yield_now();}
 queue.play_chunk(1,24000,&[0.0;24]).unwrap(); // fresh PCM after drain's snapshot
 RESUME_DRAIN.store(true,Ordering::SeqCst);
 println!("state_after_drain={:?}; queued_audio_ms={}",drain.join().unwrap(),QUEUED.load(Ordering::SeqCst));
}

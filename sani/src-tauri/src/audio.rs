//! Microphone capture (CPAL): device-native capture -> mono -> 16 kHz
//! float samples for the speech sidecar, plus RMS level metering for the
//! waveform. Capture runs warm once started; a gate decides whether audio
//! actually flows to STT so model state is never polluted while idle.

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use parking_lot::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// 16 kHz mono f32 sink (the speech sidecar's input format).
pub trait AudioSink: Send + Clone {
    fn push(&self, samples: &[f32]);
}

impl AudioSink for std::sync::mpsc::Sender<AudioChunk> {
    fn push(&self, samples: &[f32]) {
        let _ = self.send(AudioChunk { samples: samples.to_vec() });
    }
}

pub struct AudioChunk {
    pub samples: Vec<f32>,
}

pub struct AudioHandle {
    pub device_name: String,
    pub sample_rate: u32,
    /// When false, captured audio is dropped (capture stays warm).
    pub gate: Arc<AtomicBool>,
    /// Latest RMS levels (most recent last), for the waveform UI.
    pub levels: Arc<Mutex<Vec<f32>>>,
    /// Set true to close the capture thread and stream.
    pub stop: Arc<AtomicBool>,
}

fn to_mono(samples: &[f32], channels: usize) -> Vec<f32> {
    if channels == 1 {
        return samples.to_vec();
    }
    let frames = samples.len() / channels;
    let mut mono = Vec::with_capacity(frames);
    for frame in 0..frames {
        let base = frame * channels;
        let sum: f32 = samples[base..base + channels].iter().sum();
        mono.push(sum / channels as f32);
    }
    mono
}

/// Linear resample (fine for speech; no quality-critical music here).
fn resample(input: &[f32], from: u32, to: u32) -> Vec<f32> {
    if from == to || input.is_empty() {
        return input.to_vec();
    }
    let ratio = from as f64 / to as f64;
    let out_len = ((input.len() as f64) / ratio).floor() as usize;
    let mut out = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let pos = i as f64 * ratio;
        let idx = pos as usize;
        let frac = (pos - idx as f64) as f32;
        let s0 = input[idx];
        let s1 = input.get(idx + 1).copied().unwrap_or(s0);
        out.push(s0 + (s1 - s0) * frac);
    }
    out
}

fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let mean_sq = samples.iter().map(|s| s * s).sum::<f32>() / samples.len() as f32;
    mean_sq.sqrt()
}

/// Open the default or named input device and start streaming.
///
/// The `cpal::Stream` itself is not `Send` on every platform, so it lives
/// inside a dedicated thread that also keeps the capture warm; the returned
/// handle carries only the thread-safe controls.
pub fn start_capture(
    device_name: &str,
    sink: impl AudioSink + 'static,
) -> Result<AudioHandle, String> {
    let host = cpal::default_host();
    let device = if device_name.trim().is_empty() {
        host.default_input_device().ok_or("No input device found")?
    } else {
        host.input_devices()
            .map_err(|e| e.to_string())?
            .find(|d| d.name().map(|n| n == device_name).unwrap_or(false))
            .or_else(|| host.default_input_device())
            .ok_or("Requested input device not found")?
    };
    let device_label = device.name().unwrap_or_else(|_| "input".into());
    let supported = device
        .default_input_config()
        .map_err(|e| format!("Cannot read device config: {e}"))?;
    let native_rate = supported.sample_rate().0;
    let channels = supported.channels() as usize;
    let sample_format = supported.sample_format();

    let gate = Arc::new(AtomicBool::new(false));
    let levels: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::new()));
    let stop = Arc::new(AtomicBool::new(false));
    let residue: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::new()));

    let gate_thread = gate.clone();
    let levels_thread = levels.clone();
    let stop_thread = stop.clone();
    let device_thread = device.clone();

    std::thread::spawn(move || {
        macro_rules! build_stream {
            ($dtype:ty, $conv:expr) => {{
                let residue = residue.clone();
                let gate = gate_thread.clone();
                let levels_cb = levels_thread.clone();
                device_thread
                    .build_input_stream(
                        &supported.into(),
                        move |data: &[$dtype], _: &cpal::InputCallbackInfo| {
                            let mono: Vec<f32> =
                                data.iter().map(|&s| $conv(s)).collect();
                            feed(&mono, channels, native_rate, &gate, &residue, &levels_cb, &sink);
                        },
                        move |err| log::warn!("audio capture error: {err}"),
                        None,
                    )
            }};
        }

        let built = match sample_format {
            cpal::SampleFormat::F32 => build_stream!(f32, |s: f32| s),
            cpal::SampleFormat::I16 => build_stream!(i16, |s: i16| s as f32 / 32768.0),
            cpal::SampleFormat::U16 => {
                build_stream!(u16, |s: u16| (s as f32 - 32768.0) / 32768.0)
            }
            other => {
                log::error!("Unsupported sample format: {other:?}");
                return;
            }
        };
        let stream: cpal::Stream = match built {
            Ok(s) => s,
            Err(err) => {
                log::error!("Cannot open capture stream: {err}");
                return;
            }
        };
        if let Err(err) = stream.play() {
            log::error!("Cannot start capture: {err}");
            return;
        }
        // Own the stream until told to stop; dropping it closes the device.
        while !stop_thread.load(Ordering::Relaxed) {
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    });

    log::info!(
        "capture opened: device={device_label:?} rate={native_rate} ch={channels}"
    );
    Ok(AudioHandle {
        device_name: device_label,
        sample_rate: 16_000,
        gate,
        levels,
        stop,
    })
}

fn feed(
    raw: &[f32],
    channels: usize,
    native_rate: u32,
    gate: &AtomicBool,
    residue: &Mutex<Vec<f32>>,
    levels: &Mutex<Vec<f32>>,
    sink: &impl AudioSink,
) {
    let mono = to_mono(raw, channels);
    let mut resampled = {
        let mut res = residue.lock();
        res.extend_from_slice(&mono);
        let out = resample(&res, native_rate, 16_000);
        // Keep the tail that produced the last partial output frame so
        // consecutive callbacks resample continuously.
        let consumed = (out.len() as f64 * native_rate as f64 / 16_000.0).floor() as usize;
        let keep_from = consumed.min(res.len());
        res.drain(..keep_from);
        out
    };

    let level = rms(&resampled);
    #[cfg(debug_assertions)]
    log_stability(level);
    {
        let mut lv = levels.lock();
        lv.push(level);
        let overflow = lv.len().saturating_sub(48);
        if overflow > 0 {
            lv.drain(..overflow);
        }
    }

    if !gate.load(Ordering::Relaxed) {
        resampled.clear();
        return;
    }
    if !resampled.is_empty() {
        sink.push(&resampled);
    }
}

pub fn list_input_devices() -> Vec<String> {
    cpal::default_host()
        .input_devices()
        .map(|devices| {
            devices
                .filter_map(|d| d.name().ok())
                .collect::<Vec<String>>()
        })
        .unwrap_or_default()
}

/// Debug builds: log the input level once a second so silence (permissions,
/// wrong device) is visible without a UI.
#[cfg(debug_assertions)]
fn log_stability(level: f32) {
    use std::sync::atomic::AtomicU32;
    static COUNT: AtomicU32 = AtomicU32::new(0);
    let n = COUNT.fetch_add(1, Ordering::Relaxed);
    if n % 100 == 0 {
        log::info!("input rms={level:.4}");
    }
}

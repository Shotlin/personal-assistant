//! TTS framed protocol (Jarvis Phase 1, T09) — the host side of the
//! private worker transport (file 03 §9).
//!
//! Frames are 4-byte big-endian length-prefixed JSON on the worker's
//! stdio — the same framing discipline as sani-core, on a separate
//! process. Validation rules are mirrored from the worker: malformed,
//! oversize, NaN-bearing, or stale-generation frames are refused before
//! they can reach audio.

use serde::{Deserialize, Serialize};

pub const TEXT_MAX_CHARS: usize = 4000;
pub const PCM_CHUNK_MAX_BYTES: usize = 64 * 1024;
pub const FRAME_MAX_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TtsRequest {
    #[serde(default = "default_schema_version")]
    pub schema_version: u32,
    pub request_id: String,
    pub utterance_id: String,
    #[serde(default)]
    pub conversation_id: String,
    #[serde(default)]
    pub mission_id: Option<String>,
    pub message_id: String,
    pub generation: u64,
    pub text: String,
    #[serde(default = "default_kind")]
    pub kind: String,
    #[serde(default = "default_voice")]
    pub voice_asset_id: String,
    #[serde(default = "default_rate")]
    pub rate: f32,
}

fn default_schema_version() -> u32 {
    1
}
fn default_kind() -> String {
    "FINAL".to_string()
}
fn default_voice() -> String {
    "default".to_string()
}
fn default_rate() -> f32 {
    1.0
}

impl TtsRequest {
    /// Validate a request from the renderer-facing side. Extra-forbid
    /// semantics live in the strict parse path; unknown kinds reject.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err(format!("unsupported schema_version {}", self.schema_version));
        }
        if self.request_id.is_empty() || self.utterance_id.is_empty() || self.message_id.is_empty()
        {
            return Err("request_id/utterance_id/message_id must be non-empty".to_string());
        }
        if self.text.is_empty() {
            return Err("text must be non-empty".to_string());
        }
        if self.text.chars().count() > TEXT_MAX_CHARS {
            return Err(format!("text exceeds {TEXT_MAX_CHARS} chars"));
        }
        if !matches!(self.kind.as_str(), "ACK" | "STATUS" | "FINAL") {
            return Err(format!("unknown request kind {}", self.kind));
        }
        if !(0.5..=2.0).contains(&self.rate) || self.rate.is_nan() {
            return Err("rate is out of the validated range [0.5, 2.0]".to_string());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PcmChunk {
    pub utterance_id: String,
    pub generation: u64,
    pub sequence: u32,
    pub sample_rate: u32,
    pub channels: u8,
    pub format: String,
    #[serde(rename = "pcm_base64", alias = "pcmBase64")]
    pub pcm_base64: String,
    #[serde(rename = "final", alias = "final_chunk", default)]
    pub final_chunk: bool,
}

impl PcmChunk {
    /// Validate one chunk from the worker: bounds, format, and NaN refusal.
    pub fn validate(&self) -> Result<Vec<f32>, String> {
        if self.utterance_id.is_empty() {
            return Err("utterance_id must be non-empty".to_string());
        }
        if self.format != "f32le" || self.channels != 1 {
            return Err("only mono f32le PCM is accepted".to_string());
        }
        if !matches!(self.sample_rate, 16000 | 22050 | 24000 | 44100 | 48000) {
            return Err(format!("unsupported sample rate {}", self.sample_rate));
        }
        use base64::Engine as _;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(self.pcm_base64.as_bytes())
            .map_err(|err| format!("pcm_base64 is not valid base64: {err}"))?;
        if bytes.len() > PCM_CHUNK_MAX_BYTES {
            return Err(format!("decoded chunk exceeds {PCM_CHUNK_MAX_BYTES} bytes"));
        }
        if bytes.len() % 4 != 0 {
            return Err("f32le PCM must be a multiple of 4 bytes".to_string());
        }
        let samples: Vec<f32> = bytes
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .collect();
        if samples.iter().any(|s| s.is_nan() || s.is_infinite()) {
            return Err("PCM contains NaN or infinite samples".to_string());
        }
        Ok(samples)
    }
}

/// One accepted chunk after validation, ready for the playback queue.
#[derive(Debug, Clone, PartialEq)]
pub struct AcceptedChunk {
    pub utterance_id: String,
    pub generation: u64,
    pub sequence: u32,
    pub sample_rate: u32,
    pub samples: Vec<f32>,
    pub final_chunk: bool,
}

/// A small monotonic validator: chunks arrive in order per utterance and
/// only for the current generation.
#[derive(Default)]
pub struct StreamValidator {
    current_generation: u64,
    last_sequence: Option<u32>,
}

impl StreamValidator {
    pub fn new(generation: u64) -> Self {
        Self {
            current_generation: generation,
            last_sequence: None,
        }
    }

    pub fn update_generation(&mut self, generation: u64) {
        self.current_generation = generation;
        self.last_sequence = None;
    }

    pub fn generation(&self) -> u64 {
        self.current_generation
    }

    /// Accept or refuse a chunk: stale generations and out-of-order
    /// sequences are refused, never partially applied.
    pub fn accept(&mut self, chunk: AcceptedChunk) -> Result<AcceptedChunk, String> {
        if chunk.generation != self.current_generation {
            return Err(format!(
                "stale generation {} (current {})",
                chunk.generation, self.current_generation
            ));
        }
        match self.last_sequence {
            Some(last) if chunk.sequence != last + 1 => {
                return Err(format!(
                    "non-monotonic sequence {} after {last}",
                    chunk.sequence
                ));
            }
            _ => {}
        }
        self.last_sequence = Some(chunk.sequence);
        Ok(chunk)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine as _;

    fn good_request() -> TtsRequest {
        TtsRequest {
            schema_version: 1,
            request_id: "r1".to_string(),
            utterance_id: "u1".to_string(),
            conversation_id: String::new(),
            mission_id: None,
            message_id: "m1".to_string(),
            generation: 1,
            text: "Fixture utterance.".to_string(),
            kind: "FINAL".to_string(),
            voice_asset_id: "default".to_string(),
            rate: 1.0,
        }
    }

    #[test]
    fn valid_request_passes() {
        assert!(good_request().validate().is_ok());
    }

    #[test]
    fn oversize_text_is_refused() {
        let mut request = good_request();
        request.text = "x".repeat(TEXT_MAX_CHARS + 1);
        let err = request.validate().unwrap_err();
        assert!(err.contains("4000"));
    }

    #[test]
    fn unknown_kind_and_bad_rate_refuse() {
        let mut request = good_request();
        request.kind = "MAYBE".to_string();
        assert!(request.validate().is_err());
        let mut request = good_request();
        request.rate = 9.0;
        assert!(request.validate().is_err());
    }

    #[test]
    fn nan_pcm_is_refused() {
        let nan = f32::NAN.to_le_bytes();
        let chunk = PcmChunk {
            utterance_id: "u1".to_string(),
            generation: 1,
            sequence: 0,
            sample_rate: 24000,
            channels: 1,
            format: "f32le".to_string(),
            pcm_base64: base64::engine::general_purpose::STANDARD.encode(nan),
            final_chunk: true,
        };
        assert!(chunk.validate().is_err());
    }

    #[test]
    fn valid_pcm_decodes() {
        let samples = [0.0f32, 0.25, -0.25];
        let mut bytes = Vec::new();
        for s in samples {
            bytes.extend_from_slice(&s.to_le_bytes());
        }
        let chunk = PcmChunk {
            utterance_id: "u1".to_string(),
            generation: 1,
            sequence: 0,
            sample_rate: 24000,
            channels: 1,
            format: "f32le".to_string(),
            pcm_base64: base64::engine::general_purpose::STANDARD.encode(&bytes),
            final_chunk: true,
        };
        let decoded = chunk.validate().unwrap();
        assert_eq!(decoded, vec![0.0, 0.25, -0.25]);
    }

    #[test]
    fn stale_generation_refused() {
        let mut validator = StreamValidator::new(2);
        let chunk = AcceptedChunk {
            utterance_id: "u1".to_string(),
            generation: 1,
            sequence: 0,
            sample_rate: 24000,
            samples: vec![],
            final_chunk: false,
        };
        assert!(validator.accept(chunk).is_err());
    }

    #[test]
    fn non_monotonic_sequence_refused() {
        let mut validator = StreamValidator::new(1);
        let make = |seq: u32| AcceptedChunk {
            utterance_id: "u1".to_string(),
            generation: 1,
            sequence: seq,
            sample_rate: 24000,
            samples: vec![],
            final_chunk: false,
        };
        assert!(validator.accept(make(0)).is_ok());
        assert!(validator.accept(make(2)).is_err(), "skipping 1 is refused");
        assert!(validator.accept(make(1)).is_ok());
    }
}

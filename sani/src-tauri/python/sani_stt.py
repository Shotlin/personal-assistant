#!/usr/bin/env python3
"""Sani speech sidecar: Moonshine Voice streaming STT over stdio.

Protocol (binary-safe framing on stdin, one JSON object per line on stdout):

stdin frame:  <type:u8> <len:u32le> <payload>
  type 0x01 = audio: payload is f32le mono samples at 16 kHz (-1.0..1.0)
  type 0x02 = control: payload is a UTF-8 JSON object
    {"cmd": "flush"}    -- the user chose to finish: COMMIT the pending turn now
    {"cmd": "discard"}  -- the user cancelled: throw the pending turn away
    {"cmd": "reset"}    -- alias of "discard"

stdout event: {"type": "...", ...} per line
  {"type": "downloading", "progress": 0.42, "file": "..."}
  {"type": "ready", "model": "...", "vad": "silero-v5" | "rms-fallback"}
  {"type": "partial", "text": "...", "segments": n, "chars": n}
  {"type": "final", "text": "..."}
  {"type": "note", "reason": "...", ...}
  {"type": "error", "message": "..."}

Turn endpointing — the contract that matters
--------------------------------------------
A completed STT *line* is NOT a completed user *turn*. Moonshine ends a line on
its own whenever the speaker pauses, so treating that as the end of the request
splits one natural sentence into several agent calls — and worse, the host
closes its audio gate on the first `final`, so whatever was said after the pause
is discarded and never reaches the agent at all.

So `final` has exactly one owner: TurnAccumulator.commit(). The listener only
accumulates.

  IDLE --speech start--> SPEAKING --speech end--> POSSIBLE_END --deadline--> COMMITTED
                            ^                          |
                            +---- speech resumes ------+
                                  (commit cancelled,
                                   same utterance continues)

`partial` is UI-only and never triggers a handoff. `note` is diagnostics only.
A commit drains the accumulator but leaves the phase COMMITTED, so stragglers
from Moonshine's buffer cannot re-arm a second final for the same utterance; the
only exit is a control frame. The host guarantees a `discard` at the start of
every capture, which is what arms a clean accumulator — if that ever stops
being true, this sidecar will go deaf after the first turn.

Audio is never stored or logged; this process only turns sound into text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

MODEL_CHOICES = {
    "tiny-streaming-en": "TINY_STREAMING",
    "base-streaming-en": "BASE_STREAMING",
    "small-streaming-en": "SMALL_STREAMING",  # documented low-resource fallback
    "medium-streaming-en": "MEDIUM_STREAMING",  # default
}

# The sole supported-model catalog. Native code and the UI query this sidecar
# contract rather than maintaining another potentially divergent list.
SUPPORTED_MODELS = tuple(MODEL_CHOICES)

# Deliberately unchanged by the endpointing work: landing a new model and a new
# turn boundary together makes an accuracy regression impossible to attribute.
# Flipping this to medium-streaming-en is its own change, after the boundaries
# are proven. `speech.rs` always passes --model explicitly from settings.
DEFAULT_MODEL = "small-streaming-en"

# --- timing -----------------------------------------------------------------
VAD_WINDOW = 512  # Silero at 16 kHz = 32 ms per window
VAD_WINDOW_S = VAD_WINDOW / 16_000
MIN_SPEECH_MS = 220  # a shorter burst is a cough or a keystroke, not speech
SPEECH_PAD_MS = 150  # applied to speech START only (see SpeechGate)
TURN_END_MS_DEFAULT = 1400  # silence that ends a user turn
MAX_UTTERANCE_MS_DEFAULT = 30_000  # bounded failure mode for an endless monologue
SCHEDULER_TICK_S = 0.05
STARVED_INPUT_S = 0.5  # no frames for this long => treat the input as silent

# Metering only, never authoritative: an energy threshold is precisely the
# mechanism that fails quiet speakers, fan noise and cheap microphones.
RMS_DIAGNOSTIC_FLOOR = 0.0035

VAD_ENTER = 0.55
VAD_EXIT = 0.32

# Function words that are plausible model duplication at a line boundary.
_OVERLAP_STOPWORDS = frozenset(
    {"the", "a", "an", "of", "to", "my", "and", "it", "is", "in", "for"}
)

# Only these, and only when they are the WHOLE candidate. Deliberately narrow:
# a single real token clears the predicate forever, so "stop", "yes", "open it"
# and "go back" always survive.
_FILLER = frozenset({"uh", "um", "hmm", "mm", "ah", "er"})
_TOKEN = re.compile(r"[a-z0-9']+")
_MAX_FILLER_TOKENS = 4


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
class Sink:
    """Thread-safe JSON-lines stdout writer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __call__(self, payload: dict) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def note(self, reason: str, **fields) -> None:
        self({"type": "note", "reason": reason, **fields})


# --------------------------------------------------------------------------
# text joining and filler rules
# --------------------------------------------------------------------------
def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def merge(prev: str, nxt: str) -> str:
    """Join two transcript pieces without duplicating the overlap between them.

    Moonshine rewrites `line.text` as a whole-line *snapshot* and splits one
    spoken sentence across lines ("...check my" | "my latest build"), so the
    boundary between two pieces is where duplication appears.
    """
    a, b = norm(prev), norm(nxt)
    if not a:
        return b
    if not b:
        return a
    # Same-content supersession: one snapshot already contains the other.
    if b in a:
        return a
    if a in b:
        return b

    at, bt = a.split(), b.split()
    longest = min(6, len(at), len(bt))
    k = 0
    for cand in range(longest, 0, -1):
        if at[-cand:] == bt[:cand]:
            k = cand
            break
    if k == 1 and bt[0].lower().strip(",.!?;:") not in _OVERLAP_STOPWORDS:
        # A lone repeated content word is likelier to be real speech ("no no")
        # than model duplication. Only >=2-token boundary repeats are trimmed.
        k = 0
    if k:
        bt = bt[k:]

    left = a.rstrip(" ,.!?;:")
    out = f"{left} {' '.join(bt)}".strip() if bt else left
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return re.sub(r"\s+", " ", out).strip()


def filler_only(text: str) -> bool:
    """True when a candidate is nothing but hesitation.

    A universal quantifier over tokens: one legitimate word makes it False. The
    token cap deliberately *widens* acceptance, so the failure mode is a missed
    suppression, never a lost request.
    """
    toks = _TOKEN.findall(norm(text).lower())
    if not toks:
        return True
    if len(toks) > _MAX_FILLER_TOKENS:
        return False
    return all(t in _FILLER for t in toks)


# --------------------------------------------------------------------------
# accumulator
# --------------------------------------------------------------------------
PHASE_IDLE = "idle"
PHASE_SPEAKING = "speaking"
PHASE_POSSIBLE_END = "possible_end"
PHASE_COMMITTED = "committed"


@dataclass
class Line:
    """One Moonshine line, keyed by its stable id."""

    line_id: int
    text: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    complete: bool = False


@dataclass(frozen=True)
class Commit:
    text: str
    segments: int
    reason: str
    gen: int
    silence_ms: int


class TurnAccumulator:
    """Accumulates speech across natural pauses and owns the commit decision.

    Locking: `_lock` guards every field below. It is entered *inside* the
    caller's stream lock when a Moonshine listener re-enters here, and it is
    always released before `commit()` returns so the caller can take the stream
    lock for the lazy reset — `_lock` must never be held while acquiring the
    stream lock, or the re-entrant listener path deadlocks.
    """

    def __init__(
        self,
        *,
        turn_end_s: float,
        max_utterance_s: float,
        partial_stable_s: float,
        emit: Callable[[dict], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._turn_end_s = turn_end_s
        self._max_utterance_s = max_utterance_s
        self._partial_stable_s = partial_stable_s
        self._emit = emit
        self._clock = clock
        self._lock = threading.Lock()

        # required state
        self._lines: dict[int, Line] = {}
        self.current_partial = ""
        self.partial_line_id: int | None = None
        self.last_speech_time: float | None = None  # None until first speech
        self.speech_active = False
        self.commit_timer_generation = 0

        # supporting state
        self.phase = PHASE_IDLE
        self.utterance_started_at: float | None = None
        self.last_change_at: float = 0.0
        self.last_audio_at: float = 0.0
        self.committed_ids: set[int] = set()
        self.discard_requested = False
        self.stream_reset_pending = False
        self.rescue_armed_at: float | None = None
        self.stats: dict[str, int] = {
            "commits": 0,
            "cancelled": 0,
            "suppressed": 0,
            "rescued": 0,
        }

    # -- STT content ingress (never changes timing state) -------------------
    def on_line_started(self, line_id: int, now: float) -> None:
        with self._lock:
            if self._closed_out(line_id):
                return
            self._lines.setdefault(line_id, Line(line_id, "", now, now))
            self.partial_line_id = line_id

    def on_line_text(self, line_id: int, text: str, now: float) -> None:
        text = norm(text)
        with self._lock:
            if self._closed_out(line_id):
                return
            line = self._lines.setdefault(line_id, Line(line_id, "", now, now))
            if line.text == text:
                return
            line.text = text
            line.last_seen = now
            self.partial_line_id = line_id
            self.current_partial = text
            self.last_change_at = now
            if self.phase == PHASE_IDLE and self.last_speech_time is None:
                # VAD never confirmed speech but the model heard something:
                # arm the rescue path rather than swallow the utterance.
                self.rescue_armed_at = now
            segments = self._count()
            display = self._join_locked()
        self._emit(
            {
                "type": "partial",
                "text": display,
                "segments": segments,
                "chars": len(text),
            }
        )

    def on_line_completed(self, line_id: int, text: str, now: float) -> None:
        text = norm(text)
        with self._lock:
            if self._closed_out(line_id):
                return
            line = self._lines.setdefault(line_id, Line(line_id, "", now, now))
            line.text = text or line.text
            line.complete = True
            line.last_seen = now
            if self.partial_line_id == line_id:
                self.current_partial = line.text
            segments = self._count()
            chars = len(line.text)
        # A completed LINE is only a segment. It never emits `final`.
        self._emit(
            {"type": "note", "reason": "segment", "chars": chars, "segments": segments}
        )

    def on_error(self, message: str) -> None:
        self._emit({"type": "error", "message": message})

    # -- VAD timing ingress --------------------------------------------------
    def on_speech_start(self, now: float) -> None:
        with self._lock:
            if self.discard_requested:
                self.speech_active = True
                return
            if self.phase == PHASE_COMMITTED:
                # The previous turn already committed and its lines were drained.
                # Fresh speech is a fresh turn, not a straggler: committed_ids
                # still swallows any late text for the old line ids. Without
                # this the sidecar only ever hears one turn per capture.
                self.phase = PHASE_SPEAKING
                self.utterance_started_at = now
                self.last_speech_time = now
                self.speech_active = True
                return
            resuming = self.phase == PHASE_POSSIBLE_END
            gap_ms = (
                int((now - self.last_speech_time) * 1000)
                if self.last_speech_time is not None
                else 0
            )
            self.speech_active = True
            self.last_speech_time = now
            self.rescue_armed_at = None
            if resuming:
                self.commit_timer_generation += 1
                self.stats["cancelled"] += 1
                gen = self.commit_timer_generation
                self.phase = PHASE_SPEAKING
                self._emit(
                    {"type": "note", "reason": "resumed", "gen": gen, "silence_ms": gap_ms}
                )
                return
            if self.phase == PHASE_IDLE:
                self.utterance_started_at = now
            self.phase = PHASE_SPEAKING

    def on_speech_end(self, now: float) -> None:
        with self._lock:
            self.speech_active = False
            if self._closed_out():
                return
            self.last_speech_time = now
            if not self._has_text():
                # Nothing was transcribed: there is no turn to commit.
                self.phase = PHASE_IDLE
                self.utterance_started_at = None
                self.last_speech_time = None
                return
            self.phase = PHASE_POSSIBLE_END
            self._emit(
                {
                    "type": "note",
                    "reason": "possible_end",
                    "gen": self.commit_timer_generation,
                    "segments": self._count(),
                    "silence_ms": 0,
                }
            )

    def on_audio(self, now: float) -> None:
        with self._lock:
            self.last_audio_at = now

    # -- scheduling ----------------------------------------------------------
    def deadline(self) -> float | None:
        """Earliest instant a decision could be due, for the scheduler's wait."""
        with self._lock:
            if self.phase == PHASE_POSSIBLE_END and self.last_speech_time is not None:
                return self.last_speech_time + self._turn_end_s
            if self.phase == PHASE_SPEAKING and self.utterance_started_at is not None:
                return min(
                    self.utterance_started_at + self._max_utterance_s,
                    (self.last_speech_time or 0.0) + self._turn_end_s
                    if self.last_speech_time
                    else self.utterance_started_at + self._max_utterance_s,
                )
            if self.phase == PHASE_IDLE and self.rescue_armed_at is not None:
                return self.rescue_armed_at + 2 * self._turn_end_s
            return None

    def due(self, now: float) -> tuple[str, str | None]:
        with self._lock:
            if self.phase == PHASE_POSSIBLE_END:
                if self.utterance_started_at is not None and (
                    now - self.utterance_started_at
                ) >= self._max_utterance_s:
                    return "commit", "max-duration"
                if self.last_speech_time is None:
                    return "wait", None
                silence = now - self.last_speech_time
                starved = (now - self.last_audio_at) >= STARVED_INPUT_S
                stable = now - self.last_change_at >= self._partial_stable_s
                if silence >= self._turn_end_s and (stable or starved):
                    return "commit", "vad-silence"
                return "wait", None
            if self.phase == PHASE_SPEAKING and self.last_speech_time is not None:
                # Speech ended but no `end` edge arrived (a gated or truncated
                # capture). The deadline still has to fire.
                if (now - self.last_speech_time) >= self._turn_end_s and (
                    now - self.last_change_at >= self._partial_stable_s
                    or (now - self.last_audio_at) >= STARVED_INPUT_S
                ):
                    return "commit", "vad-silence"
                return "wait", None
            if self.phase == PHASE_IDLE and self.rescue_armed_at is not None:
                # The VAD never confirmed speech yet the model produced text.
                # Commit rather than lose the utterance; the frequency of this
                # reason is the signal that a better model is warranted.
                if (now - self.rescue_armed_at) >= 2 * self._turn_end_s and self._has_text():
                    return "commit", "rms-rescue"
            return "wait", None

    # -- the only emitter of `final` ----------------------------------------
    def commit(self, reason: str, now: float) -> Commit | None:
        with self._lock:
            if self.phase == PHASE_COMMITTED or self.discard_requested:
                return None
            text = self._join_locked()
            gen = self.commit_timer_generation
            segments = self._count()
            silence_ms = (
                int((now - self.last_speech_time) * 1000)
                if self.last_speech_time is not None
                else 0
            )

            if not text:
                self._reset_locked()
                self._emit({"type": "note", "reason": "flush-empty"})
                return None

            if filler_only(text) and reason != "explicit-flush":
                self.stats["suppressed"] += 1
                self._emit(
                    {"type": "note", "reason": "suppressed-filler", "chars": len(text)}
                )
                self._reset_locked()
                return None

            self.stats["commits"] += 1
            if reason == "rms-rescue":
                self.stats["rescued"] += 1
            self.committed_ids |= set(self._lines)
            self._reset_locked()
            self.phase = PHASE_COMMITTED
            self.stream_reset_pending = True

        # Emitted inside the lock so partial -> final ordering is total.
        self._emit(
            {
                "type": "note",
                "reason": "commit",
                "message": reason,
                "segments": segments,
                "chars": len(text),
                "gen": gen,
                "silence_ms": silence_ms,
            }
        )
        self._emit({"type": "final", "text": text})
        return Commit(
            text=text, segments=segments, reason=reason, gen=gen, silence_ms=silence_ms
        )

    def take_stream_reset(self) -> bool:
        with self._lock:
            pending, self.stream_reset_pending = self.stream_reset_pending, False
        return pending

    # -- control -------------------------------------------------------------
    def reset(self, *, discard: bool) -> None:
        with self._lock:
            self._reset_locked()
            self.discard_requested = discard
            self.committed_ids.clear()
            self.stream_reset_pending = True

    def finish_discard(self) -> None:
        with self._lock:
            self.discard_requested = False

    # -- helpers -------------------------------------------------------------
    def _closed_out(self, line_id: int | None = None) -> bool:
        """True when this utterance is committed, discarded, or a straggler.

        `committed_ids` outlives the phase transition: Moonshine keeps emitting
        for lines that were already folded into the final, and accepting them
        after a reopen would duplicate text across two turns.
        """
        if self.discard_requested or self.phase == PHASE_COMMITTED:
            return True
        return line_id is not None and line_id in self.committed_ids

    def _count(self) -> int:
        return sum(1 for line in self._lines.values() if line.text)

    def _has_text(self) -> bool:
        return any(line.text for line in self._lines.values())

    def _join_locked(self) -> str:
        out = ""
        for line in self._lines.values():
            if line.text:
                out = merge(out, line.text)
        return out

    def display_text(self) -> str:
        with self._lock:
            return self._join_locked()

    def _reset_locked(self) -> None:
        self._lines.clear()
        self.current_partial = ""
        self.partial_line_id = None
        self.utterance_started_at = None
        self.last_speech_time = None
        self.speech_active = False
        self.rescue_armed_at = None
        self.commit_timer_generation += 1
        self.phase = PHASE_IDLE


# --------------------------------------------------------------------------
# speech gate: probabilities -> speech edges
# --------------------------------------------------------------------------
class SpeechGate:
    """Turns per-window speech probabilities into start/end edges.

    Padding applies to speech START only. Folding a trailing pad into the commit
    deadline would double-count against TURN_END_MS and silently add latency to
    every turn.
    """

    def __init__(
        self,
        *,
        enter: float = VAD_ENTER,
        exit_: float = VAD_EXIT,
        min_speech_ms: int = MIN_SPEECH_MS,
        pad_ms: int = SPEECH_PAD_MS,
        window_s: float = VAD_WINDOW_S,
    ) -> None:
        self.enter = enter
        self.exit = exit_
        self.window_s = window_s
        self.min_windows = max(1, int(round(min_speech_ms / (window_s * 1000))))
        self.pad_s = pad_ms / 1000.0
        self._run = 0
        self._run_start_t = 0.0
        self._in_speech = False

    def reset(self) -> None:
        self._run = 0
        self._in_speech = False

    def push(self, probs: Sequence[float], frame_end_t: float) -> list[tuple[str, float]]:
        """Returns ("start"|"end", monotonic time) edges."""
        edges: list[tuple[str, float]] = []
        n = len(probs)
        for i, prob in enumerate(probs):
            t = frame_end_t - (n - 1 - i) * self.window_s
            if not self._in_speech:
                if prob >= self.enter:
                    if self._run == 0:
                        self._run_start_t = t
                    self._run += 1
                    if self._run >= self.min_windows:
                        self._in_speech = True
                        self._run = 0
                        edges.append(("start", max(0.0, self._run_start_t - self.pad_s)))
                else:
                    self._run = 0
            elif prob < self.exit:
                self._in_speech = False
                self._run = 0
                edges.append(("end", t))
        return edges


# --------------------------------------------------------------------------
# Silero VAD over onnxruntime (no torch, no new dependency)
# --------------------------------------------------------------------------
class VadLoadError(RuntimeError):
    pass


class SileroVad:
    """Silero VAD v5 on the onnxruntime already bundled into the sidecar.

    The exported graph takes a fused `state` of shape [2, batch, 128] and a 0-d
    int64 `sr`; this onnxruntime build rejects a plain Python int for `sr`, so
    the scalar is constructed explicitly. An output vector of the wrong size
    raises rather than silently reporting no speech — a VAD that always says
    "quiet" is worse than no VAD at all, and the v6 export does exactly that
    against this interface.
    """

    def __init__(
        self,
        path: str,
        sample_rate: int = 16_000,
        enter: float = VAD_ENTER,
        exit_: float = VAD_EXIT,
    ) -> None:
        import onnxruntime as ort  # lazy: keeps `import sani_stt` dependency-free
        import numpy as np

        self._np = np
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.log_severity_level = 3  # onnxruntime must not chatter on our stderr
        try:
            self._sess = ort.InferenceSession(
                str(path), sess_options=so, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # noqa: BLE001
            raise VadLoadError(f"cannot load {path}: {exc}") from exc

        names = [i.name for i in self._sess.get_inputs()]
        for required, label in (("state", "state"), ("sr", "sample rate")):
            if required not in names:
                raise VadLoadError(
                    f"VAD model is missing its {label} input; got {sorted(names)}"
                )
        self.rate = sample_rate
        self._sr = np.array(sample_rate, dtype=np.int64)
        self._state_shape = (2, 1, 128)
        probe = np.zeros((1, VAD_WINDOW), dtype=np.float32)
        probe_state = np.zeros(self._state_shape, dtype=np.float32)
        try:
            out = self._sess.run(
                None, {"input": probe, "state": probe_state, "sr": self._sr}
            )
        except Exception as exc:  # noqa: BLE001
            raise VadLoadError(f"VAD smoke test failed: {exc}") from exc
        if np.asarray(out[0]).reshape(-1).size != 1:
            raise VadLoadError(
                f"VAD output is not a single probability "
                f"(shape {np.asarray(out[0]).shape}); wrong model generation"
            )
        if np.asarray(out[1]).shape != self._state_shape:
            raise VadLoadError(
                f"VAD state shape {np.asarray(out[1]).shape} != "
                f"{self._state_shape}"
            )
        self.gate = SpeechGate(enter=enter, exit_=exit_)
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros(self._state_shape, dtype=np.float32)
        self._residue = np.zeros(0, dtype=np.float32)
        self.gate.reset()

    def push(self, samples: Sequence[float]) -> list[float]:
        """Feed one audio frame; return one probability per 512-sample window."""
        np = self._np
        buf = np.concatenate((self._residue, np.asarray(samples, dtype=np.float32)))
        usable = (len(buf) // VAD_WINDOW) * VAD_WINDOW
        self._residue = buf[usable:].copy()
        probs: list[float] = []
        for off in range(0, usable, VAD_WINDOW):
            window = buf[off : off + VAD_WINDOW].reshape(1, -1)
            out = self._sess.run(
                None, {"input": window, "state": self._state, "sr": self._sr}
            )
            self._state = np.asarray(out[1], dtype=np.float32).reshape(
                self._state_shape
            )
            probs.append(float(np.asarray(out[0]).reshape(-1)[0]))
        return probs

    def feed(self, samples: Sequence[float], frame_end_t: float) -> list[tuple[str, float]]:
        return self.gate.push(self.push(samples), frame_end_t)


class RmsVad:
    """Fallback used when the VAD asset is missing, or with --no-vad.

    Energy-only. It exists so the product can still endpoint without the model,
    not so that it can be mistaken for a real detector.
    """

    def __init__(self, floor: float = RMS_DIAGNOSTIC_FLOOR) -> None:
        self.floor = floor
        self.rate = 16_000
        # One decision per audio frame (100 ms at 16 kHz).
        self.gate = SpeechGate(window_s=0.1, min_speech_ms=MIN_SPEECH_MS)
        self.last_rms = 0.0

    def reset(self) -> None:
        self.gate.reset()

    @staticmethod
    def rms(samples: Sequence[float]) -> float:
        if not samples:
            return 0.0
        return (sum(s * s for s in samples) / len(samples)) ** 0.5

    def push(self, samples: Sequence[float]) -> list[float]:
        self.last_rms = self.rms(samples)
        return [1.0 if self.last_rms >= self.floor else 0.0]

    def feed(self, samples: Sequence[float], frame_end_t: float) -> list[tuple[str, float]]:
        return self.gate.push(self.push(samples), frame_end_t)


def vad_asset_path(override: str | None = None) -> Path | None:
    if override:
        p = Path(override)
        return p if p.exists() else None
    env = os.environ.get("SANI_SILERO_MODEL")
    if env and Path(env).exists():
        return Path(env)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "assets" / "silero_vad.onnx"
        if p.exists():
            return p
    p = Path(__file__).resolve().parent / "assets" / "silero_vad.onnx"
    return p if p.exists() else None


# --------------------------------------------------------------------------
# stream drivers
# --------------------------------------------------------------------------
class StreamDriver(Protocol):
    def add_audio(self, samples: Sequence[float]) -> None: ...

    def reset(self) -> None: ...

    def close(self) -> None: ...


class NullDriver:
    """Used by the replay harness and by tests of the accumulator alone."""

    def add_audio(self, samples: Sequence[float]) -> None:
        pass

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


class MoonshineDriver:
    """Serialises every native call on the stream handle.

    `stop()` runs an extra decode pass and re-enters listeners synchronously on
    the calling thread, so it must never overlap `add_audio()` from another.
    """

    def __init__(self, stream, lock: threading.Lock) -> None:
        self._stream = stream
        self._lock = lock

    def add_audio(self, samples: Sequence[float]) -> None:
        with self._lock:
            self._stream.add_audio(list(samples))

    def reset(self) -> None:
        with self._lock:
            self._stream.stop()
            self._stream.start()

    def close(self) -> None:
        with self._lock:
            try:
                self._stream.stop()
            except Exception:  # noqa: BLE001 -- shutdown is best effort
                pass


# --------------------------------------------------------------------------
# wire codec
# --------------------------------------------------------------------------
def read_frame(fh) -> tuple[int, bytes] | None:
    header = fh.read(5)
    if not header or len(header) < 5:
        return None
    frame_type = header[0]
    (length,) = struct.unpack("<I", header[1:5])
    payload = b""
    while len(payload) < length:
        more = fh.read(length - len(payload))
        if not more:
            break
        payload += more
    return frame_type, payload


def decode_control(payload: bytes) -> dict | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def f32le_to_list(payload: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(payload) // 4}f", payload))


# --------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------
def run_sidecar(args, sink: Sink) -> int:
    from moonshine_voice.download import get_model_for_language
    from moonshine_voice.transcriber import (
        Error,
        LineCompleted,
        LineStarted,
        LineTextChanged,
        ModelArch,
        Transcriber,
    )

    stream_lock = threading.Lock()
    arch = ModelArch[MODEL_CHOICES[args.model]]

    def on_progress(fraction: float, name: str) -> None:
        sink(
            {
                "type": "downloading",
                "model": args.model,
                "progress": round(fraction, 3),
                "file": name,
            }
        )

    try:
        model_path, resolved_arch = get_model_for_language(
            args.language, arch, on_progress=on_progress
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the host verbatim
        sink({"type": "error", "message": f"model load failed: {exc}"})
        return 1

    try:
        transcriber = Transcriber(str(model_path), resolved_arch)
        stream = transcriber.create_stream(args.update_interval)
    except Exception as exc:  # noqa: BLE001
        sink({"type": "error", "message": f"transcriber init failed: {exc}"})
        return 1

    partial_stable_s = (
        args.partial_stable_ms / 1000.0
        if args.partial_stable_ms
        else max(0.3, 1.2 * args.update_interval)
    )
    acc = TurnAccumulator(
        turn_end_s=args.turn_end_ms / 1000.0,
        max_utterance_s=args.max_utterance_ms / 1000.0,
        partial_stable_s=partial_stable_s,
        emit=sink,
    )

    vad_label = "rms-fallback"
    vad: SileroVad | RmsVad
    if args.no_vad:
        vad = RmsVad()
        vad_label = "rms-forced"
    else:
        asset = vad_asset_path(args.vad_model)
        if asset is None:
            sink.note("vad-unavailable", message="silero_vad.onnx not found")
            vad = RmsVad()
        else:
            try:
                vad = SileroVad(str(asset), enter=args.vad_enter, exit_=args.vad_exit)
                vad_label = "silero-v5"
            except VadLoadError as exc:
                sink.note("vad-unavailable", message=str(exc))
                vad = RmsVad()

    def listener(event) -> None:
        # Runs on whichever thread touched the stream, inside stream_lock.
        now = time.monotonic()
        line_id = int(getattr(getattr(event, "line", None), "line_id", 0) or 0)
        if isinstance(event, LineStarted):
            acc.on_line_started(line_id, now)
        elif isinstance(event, LineTextChanged):
            acc.on_line_text(line_id, getattr(event.line, "text", "") or "", now)
        elif isinstance(event, LineCompleted):
            acc.on_line_completed(line_id, getattr(event.line, "text", "") or "", now)
        elif isinstance(event, Error):
            acc.on_error(f"transcriber error: {event.error}")

    stream.add_listener(listener)
    stream.start()
    driver = MoonshineDriver(stream, stream_lock)
    sink({"type": "ready", "model": args.model, "vad": vad_label})

    stop = threading.Event()

    def scheduler() -> None:
        while not stop.is_set():
            deadline = acc.deadline()
            now = time.monotonic()
            wait = (
                SCHEDULER_TICK_S
                if deadline is None
                else max(0.01, min(SCHEDULER_TICK_S, deadline - now))
            )
            if stop.wait(wait):
                break
            action, reason = acc.due(time.monotonic())
            if action != "commit" or not reason:
                continue
            acc.commit(reason, time.monotonic())
            if acc.take_stream_reset():
                try:
                    driver.reset()
                except Exception as exc:  # noqa: BLE001
                    sink({"type": "error", "message": f"stream reset failed: {exc}"})

    threading.Thread(target=scheduler, daemon=True).start()

    stdin = sys.stdin.buffer
    try:
        while True:
            frame = read_frame(stdin)
            if frame is None:
                break
            frame_type, payload = frame
            now = time.monotonic()
            if frame_type == 0x01:
                samples = f32le_to_list(payload)
                if not samples:
                    continue
                acc.on_audio(now)
                try:
                    driver.add_audio(samples)
                except Exception as exc:  # noqa: BLE001
                    sink({"type": "error", "message": f"add_audio failed: {exc}"})
                    continue
                try:
                    edges = vad.feed(samples, now)
                except VadLoadError as exc:
                    sink.note("vad-unavailable", message=str(exc))
                    vad = RmsVad()
                    edges = vad.feed(samples, now)
                for kind, t in edges:
                    if kind == "start":
                        acc.on_speech_start(t)
                    else:
                        acc.on_speech_end(t)
            elif frame_type == 0x02:
                control = decode_control(payload)
                if control is None:
                    continue
                cmd = control.get("cmd")
                if cmd == "flush":
                    acc.commit("explicit-flush", time.monotonic())
                    if acc.take_stream_reset():
                        driver.reset()
                elif cmd in ("discard", "reset"):
                    acc.reset(discard=True)
                    driver.reset()
                    acc.finish_discard()
                else:
                    sink.note("unknown-cmd", message=str(cmd))
    finally:
        stop.set()
        try:
            driver.close()
            transcriber.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sani Moonshine streaming STT sidecar"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODEL_CHOICES))
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="print the authoritative supported-model catalog as JSON and exit",
    )
    parser.add_argument("--language", default="en")
    parser.add_argument("--update-interval", type=float, default=0.25)
    parser.add_argument(
        "--turn-end-ms",
        type=int,
        default=TURN_END_MS_DEFAULT,
        help="silence that closes a user turn; speech resuming inside this "
        "window cancels the commit and continues the same utterance",
    )
    parser.add_argument("--max-utterance-ms", type=int, default=MAX_UTTERANCE_MS_DEFAULT)
    parser.add_argument(
        "--partial-stable-ms",
        type=int,
        default=None,
        help="require the transcript to stop rewriting for this long before "
        "committing; defaults to max(300ms, 1.2 x update-interval)",
    )
    parser.add_argument("--vad-model", default=None, help="path to silero_vad.onnx")
    parser.add_argument("--vad-enter", type=float, default=VAD_ENTER)
    parser.add_argument("--vad-exit", type=float, default=VAD_EXIT)
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="endpoint on audio energy instead of the neural VAD (A/B and fallback)",
    )
    parser.add_argument(
        "--finalize-silence",
        type=float,
        default=None,
        help="deprecated alias for --turn-end-ms, expressed in seconds",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_models:
        # This deliberately happens before creating Sink or importing
        # moonshine_voice. Native callers can therefore inspect the supported
        # catalog even while no model is installed yet.
        print(
            json.dumps(
                {"models": list(SUPPORTED_MODELS), "default": DEFAULT_MODEL},
                separators=(",", ":"),
            )
        )
        return 0
    sink = Sink()
    if args.finalize_silence is not None:
        sink.note("deprecated-flag", message="--finalize-silence, use --turn-end-ms")
        args.turn_end_ms = int(args.finalize_silence * 1000)
    args.turn_end_ms = min(max(args.turn_end_ms, 600), 5000)
    return run_sidecar(args, sink)


if __name__ == "__main__":
    sys.exit(main())

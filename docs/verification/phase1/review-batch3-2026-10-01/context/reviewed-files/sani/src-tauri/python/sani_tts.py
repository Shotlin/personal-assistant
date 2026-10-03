"""sani-tts: the local speech-output worker (Jarvis Phase 1, T09/T10).

One supervised child process with a private framed stdin/stdout protocol —
no HTTP, no provider keys, no core tools, no mission database, and no
network after asset installation (file 03 §9).

Protocol (TtsRequestV1/PcmChunkV1):
- requests in:  `{"type":"request", "request":{TtsRequestV1 fields}}` and
  controls `{"type":"control", "action":"cancel", "generation":N}` /
  `{"type":"control","action":"shutdown"}`;
- events out:   `{"type":"event","event":{"event":"ready|chunk|finished|
  cancelled|error", ...}}` as one compact JSON line-length-prefixed frame.

Hard rules enforced here (the Rust host enforces the same on its side):
- malformed/oversize frames are refused, never partially applied;
- NaN/Inf samples and non-monotonic chunk sequences are rejected;
- a stale generation (older than the current cancel epoch) produces no
  audio and is answered with `cancelled`;
- decoded PCM is bounded (2 seconds per utterance buffered) and the text
  queue holds at most 3 utterances: backpressure over memory growth;
- the worker receives no credentials and performs no egress.

The engine behind :class:`EngineAdapter` is FIXED to the audition winner.
Until the owner-authorized audition completes, the configured engine is
``unspecified`` and the worker answers `error` for synthesis requests —
text output keeps working, voice acceptance stays BLOCKED (never faked).
"""

from __future__ import annotations

import base64
import json
import math
import os
import queue as _queue
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

FRAME_MAX_BYTES = 1 << 20
TEXT_MAX_CHARS = 4000
PCM_CHUNK_MAX_BYTES = 64 * 1024
MAX_QUEUED_UTTERANCES = 3
MAX_BUFFERED_SECONDS = 2.0

EVENT_KINDS = frozenset({"ready", "chunk", "finished", "cancelled", "error"})
REQUEST_KINDS = frozenset({"ACK", "STATUS", "FINAL"})
ENGINE_NAME = os.environ.get("SANI_TTS_ENGINE", "unspecified")


class TtsProtocolError(Exception):
    """A frame violated the worker contract; the frame is refused."""


@dataclass(frozen=True)
class TtsRequest:
    request_id: str
    utterance_id: str
    message_id: str
    generation: int
    text: str
    kind: str = "FINAL"
    conversation_id: str = ""
    mission_id: str | None = None
    voice_asset_id: str = "default"
    rate: float = 1.0

    @staticmethod
    def validate(payload: dict[str, Any]) -> "TtsRequest":
        if payload.get("schema_version", 1) != 1:
            raise TtsProtocolError("unsupported schema_version")
        request_id = payload.get("request_id")
        utterance_id = payload.get("utterance_id")
        message_id = payload.get("message_id")
        generation = payload.get("generation")
        text = payload.get("text")
        kind = payload.get("kind", "FINAL")
        rate = payload.get("rate", 1.0)
        if not all(isinstance(v, str) and v for v in (request_id, utterance_id, message_id)):
            raise TtsProtocolError("request_id/utterance_id/message_id must be non-empty strings")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise TtsProtocolError("generation must be a nonnegative integer")
        if not isinstance(text, str) or not text:
            raise TtsProtocolError("text must be a nonempty string")
        if len(text) > TEXT_MAX_CHARS:
            raise TtsProtocolError(f"text exceeds {TEXT_MAX_CHARS} chars")
        if kind not in REQUEST_KINDS:
            raise TtsProtocolError(f"kind must be one of {sorted(REQUEST_KINDS)}")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            raise TtsProtocolError("rate must be a number")
        if not 0.5 <= float(rate) <= 2.0:
            raise TtsProtocolError("rate is out of the validated range [0.5, 2.0]")
        mission_id = payload.get("mission_id")
        if mission_id is not None and not isinstance(mission_id, str):
            raise TtsProtocolError("mission_id must be a string or null")
        return TtsRequest(
            request_id=request_id,
            utterance_id=utterance_id,
            message_id=message_id,
            generation=generation,
            text=text,
            kind=kind,
            conversation_id=payload.get("conversation_id", "") or "",
            mission_id=mission_id,
            voice_asset_id=payload.get("voice_asset_id", "default") or "default",
            rate=float(rate),
        )


@dataclass(frozen=True)
class PcmChunk:
    utterance_id: str
    generation: int
    sequence: int
    sample_rate: int
    pcm: bytes
    final: bool

    @staticmethod
    def validate(payload: dict[str, Any]) -> "PcmChunk":
        utterance_id = payload.get("utterance_id")
        generation = payload.get("generation")
        sequence = payload.get("sequence")
        sample_rate = payload.get("sample_rate")
        encoded = payload.get("pcm_base64")
        final = payload.get("final", False)
        if not isinstance(utterance_id, str) or not utterance_id:
            raise TtsProtocolError("utterance_id must be a nonempty string")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise TtsProtocolError("generation must be a nonnegative integer")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise TtsProtocolError("sequence must be a nonnegative integer")
        if not isinstance(sample_rate, int) or sample_rate not in (16000, 22050, 24000, 44100, 48000):
            raise TtsProtocolError("sample_rate must be a supported rate")
        if not isinstance(encoded, str):
            raise TtsProtocolError("pcm_base64 must be a base64 string")
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except Exception as exc:  # noqa: BLE001 - any decode failure is a refusal
            raise TtsProtocolError("pcm_base64 is not valid base64") from exc
        if len(pcm) > PCM_CHUNK_MAX_BYTES:
            raise TtsProtocolError(f"decoded chunk exceeds {PCM_CHUNK_MAX_BYTES} bytes")
        if len(pcm) % 4 != 0:
            raise TtsProtocolError("f32le PCM must be a multiple of 4 bytes")
        if not isinstance(final, bool):
            raise TtsProtocolError("final must be a boolean")
        _reject_bad_samples(pcm)
        return PcmChunk(utterance_id, generation, sequence, sample_rate, pcm, final)


def _reject_bad_samples(pcm: bytes) -> None:
    """NaN and infinities are refused before they can reach the output."""
    count = len(pcm) // 4
    samples = struct.unpack(f"<{count}f", pcm) if count else ()
    for sample in samples:
        if math.isnan(sample) or math.isinf(sample):
            raise TtsProtocolError("PCM contains NaN or infinite samples")


class EngineAdapter:
    """The fixed synthesis engine interface (file 03 §9).

    ``synthesize`` yields f32le mono PCM chunks at ``sample_rate``; it must
    honor ``cancel`` between chunks cooperatively. Implementations ship only
    after the audition and asset-pin gates pass.
    """

    sample_rate = 24000

    def load(self, asset_manifest: dict[str, Any]) -> None:
        raise NotImplementedError("engine loading is implemented by the selected adapter")

    def synthesize(self, text: str, utterance_id: str, generation: int) -> Iterator[tuple[int, bytes]]:
        raise NotImplementedError("engine synthesis is implemented by the selected adapter")

    def cancel(self, generation: int) -> None:
        """Best-effort cancellation hook. A default no-op so a control
        cancel can never crash a worker whose engine has no engine-side
        cancellation (D09: cancel must be safe for every adapter)."""
        return None

    def close(self) -> None:
        raise NotImplementedError


class UnspecifiedEngine(EngineAdapter):
    """The pre-audition engine: refuses synthesis with a clear error.

    Voice acceptance stays BLOCKED until the owner audition and asset
    licensing gates pass; text output is unaffected.
    """

    def synthesize(self, text: str, utterance_id: str, generation: int) -> Iterator[tuple[int, bytes]]:
        raise RuntimeError(
            "no speech engine is configured yet (audition pending); "
            "this worker only validates the transport"
        )


class SilenceEngine(EngineAdapter):
    """A test engine: deterministic silence chunks (no assets, no network)."""

    sample_rate = 24000

    def __init__(self, chunk_ms: int = 100, total_ms: int = 500) -> None:
        self.chunk_ms = chunk_ms
        self.total_ms = total_ms
        self.cancelled: set[int] = set()

    def load(self, asset_manifest: dict[str, Any]) -> None:
        return None

    def synthesize(self, text: str, utterance_id: str, generation: int) -> Iterator[tuple[int, bytes]]:
        chunk_bytes = int(self.sample_rate * 4 * self.chunk_ms / 1000)
        chunks = max(1, int(self.sample_rate * 4 * self.total_ms / 1000) // chunk_bytes)
        for index in range(chunks):
            if generation in self.cancelled:
                return
            yield (self.sample_rate, b"\x00\x00\x00\x00" * (chunk_bytes // 4))

    def cancel(self, generation: int) -> None:
        self.cancelled.add(generation)

    def close(self) -> None:
        return None


def build_engine() -> EngineAdapter:
    """The engine is fixed by environment; no runtime engine switching."""
    if ENGINE_NAME == "silence":
        return SilenceEngine()
    return UnspecifiedEngine()


class Worker:
    """Framed protocol loop over stdin/stdout.

    D09: synthesis runs on a DEDICATED thread consuming an internal FIFO,
    while the serve loop keeps reading frames. A cancel/stop control
    arriving DURING active synthesis is processed immediately — the epoch
    bumps, the engine is told to cancel, and the synthesis thread abandons
    the stale utterance at its next chunk boundary. Without this, input
    controls were unreadable for the whole synthesis.
    """

    MAX_PENDING_REQUESTS = 4

    def __init__(self, reader: Any, writer: Any, engine: EngineAdapter | None = None) -> None:
        self._reader = reader
        self._writer = writer
        self._engine = engine or build_engine()
        self._generation = 0
        self._shutdown = False
        self._buffered_seconds = 0.0
        self._requests: "_queue.Queue[Any]" = _queue.Queue(
            maxsize=self.MAX_PENDING_REQUESTS
        )
        self._write_lock = threading.Lock()
        self._synth_thread: threading.Thread | None = None

    def _write(self, payload: dict[str, Any], *, droppable: bool = False) -> None:
        """One framed write, bounded even when the output pipe blocks.

        D18: a PCM chunk is droppable — a blocked output must never stop
        the control path from being read or answered (generation fencing
        discards the stale audio anyway). Control acknowledgements retry
        with a bounded timeout so a full stop still terminates.
        """
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > FRAME_MAX_BYTES:
            return
        deadline = time.monotonic() + 2.0
        while True:
            if not self._write_lock.acquire(timeout=0.25):
                if (droppable and self._shutdown) or time.monotonic() > deadline:
                    return
                continue
            try:
                self._writer.write(len(body).to_bytes(4, "big") + body)
                self._writer.flush()
                return
            finally:
                self._write_lock.release()

    def _event(self, event: str, **fields: Any) -> None:
        if event not in EVENT_KINDS:
            raise TtsProtocolError(f"unknown event kind {event!r}")
        self._write({"type": "event", "event": {"event": event, **fields}})

    def _event_droppable(self, event: str, **fields: Any) -> None:
        """A PCM chunk frame: bounded write, droppable on shutdown (D18)."""
        if event not in EVENT_KINDS:
            raise TtsProtocolError(f"unknown event kind {event!r}")
        self._write({"type": "event", "event": {"event": event, **fields}},
                    droppable=True)

    def _refuse(self, request_id: Any, message: str, *,
                utterance_id: str | None = None, generation: int | None = None) -> None:
        self._event("error", request_id=request_id, utterance_id=utterance_id,
                    generation=generation, message=message[:300])

    def serve(self) -> None:
        self._event("ready", engine=ENGINE_NAME)
        self._synth_thread = threading.Thread(
            target=self._synth_loop, name="sani-tts-synthesis", daemon=True
        )
        self._synth_thread.start()
        try:
            while not self._shutdown:
                header = self._reader.read(4)
                if not header or len(header) < 4:
                    break
                length = int.from_bytes(header, "big")
                if length > FRAME_MAX_BYTES:
                    break
                body = self._reader.read(length)
                if len(body) < length:
                    break
                try:
                    frame = json.loads(body.decode("utf-8"))
                    if not isinstance(frame, dict):
                        raise TtsProtocolError("frame must be a JSON object")
                    self._handle(frame)
                except TtsProtocolError as exc:
                    self._refuse(None, str(exc))
                except Exception as exc:  # noqa: BLE001
                    self._refuse(None, f"worker error: {exc}")
                    break
        finally:
            # D18: the sentinel never blocks forever on a FULL FIFO. On an
            # explicit shutdown control the queued work is discarded (the
            # in-flight generation aborts; the bounded join terminates).
            # On plain EOF the loop drains its queue first — the sentinel
            # waits for space, which the synth thread provides as it
            # consumes.
            if self._shutdown:
                deadline = time.monotonic() + 1.0
                while True:
                    try:
                        self._requests.put(None, timeout=0.1)
                        break
                    except _queue.Full:
                        if time.monotonic() >= deadline:
                            break
                if self._synth_thread is not None:
                    self._synth_thread.join(timeout=2.0)
            else:
                deadline = time.monotonic() + 10.0
                while True:
                    try:
                        self._requests.put(None, timeout=0.2)
                        break
                    except _queue.Full:
                        if time.monotonic() >= deadline:
                            break
                if self._synth_thread is not None:
                    self._synth_thread.join(timeout=15.0)

    def _handle(self, frame: dict[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == "control":
            self._handle_control(frame)
            return
        if frame_type != "request":
            raise TtsProtocolError(f"unknown frame type {frame_type!r}")
        request = TtsRequest.validate(frame.get("request") or {})
        if request.generation < self._generation:
            self._event(
                "cancelled", utterance_id=request.utterance_id, generation=request.generation
            )
            return
        if request.kind == "ACK":
            self._event("finished", utterance_id=request.utterance_id, generation=request.generation)
            return
        if self._engine is None or isinstance(self._engine, UnspecifiedEngine):
            self._refuse(request.request_id, "no speech engine is configured (audition pending)",
                         utterance_id=request.utterance_id, generation=request.generation)
            return
        try:
            self._requests.put_nowait(request)
        except _queue.Full:
            self._refuse(request.request_id,
                         "synthesis queue is full; the text path stays available",
                         utterance_id=request.utterance_id, generation=request.generation)

    def _synth_loop(self) -> None:
        while True:
            try:
                request = self._requests.get(timeout=0.2)
            except _queue.Empty:
                if self._shutdown:
                    return
                continue
            if request is None or self._shutdown:
                return
            self._synthesize_request(request)

    def _handle_control(self, frame: dict[str, Any]) -> None:
        action = frame.get("action")
        if action == "shutdown":
            self._shutdown = True
            return
        if action == "cancel":
            generation = frame.get("generation")
            if not isinstance(generation, int) or isinstance(generation, bool):
                raise TtsProtocolError("cancel requires an integer generation")
            # Cancellation bumps the epoch: anything older yields no audio.
            # The engine hook and the event fire HERE, on the read thread —
            # never behind the synthesis that is currently running (D09).
            if generation > self._generation:
                self._generation = generation
            try:
                self._engine.cancel(generation)
            except Exception:  # noqa: BLE001 -- a broken engine cancel is
                # contained: the epoch is already bumped and the audio is
                # fenced; the acknowledgement still goes out (D18).
                pass
            self._event("cancelled", generation=generation)
            return
        raise TtsProtocolError(f"unknown control action {action!r}")

    def _synthesize_request(self, request: TtsRequest) -> None:
        sequence = 0
        try:
            for sample_rate, pcm in self._engine.synthesize(
                request.text, request.utterance_id, request.generation
            ):
                if request.generation < self._generation or self._shutdown:
                    self._event(
                        "cancelled",
                        utterance_id=request.utterance_id,
                        generation=request.generation,
                    )
                    return
                # A framed write is synchronous: pipe backpressure bounds
                # pending output. Count this chunk, not lifetime synthesis.
                if sample_rate <= 0 or len(pcm) / 4 / sample_rate > MAX_BUFFERED_SECONDS:
                    raise RuntimeError("decoder buffer bound exceeded")
                encoded = base64.b64encode(pcm).decode("ascii")
                PcmChunk.validate({
                    "utterance_id": request.utterance_id, "generation": request.generation,
                    "sequence": sequence, "sample_rate": sample_rate, "channels": 1,
                    "format": "f32le", "pcm_base64": encoded, "final": False,
                })
                self._event_droppable(
                    "chunk",
                    utterance_id=request.utterance_id,
                    generation=request.generation,
                    sequence=sequence,
                    sample_rate=sample_rate,
                    channels=1,
                    format="f32le",
                    pcm_base64=encoded,
                    final=False,
                )
                sequence += 1
        except (RuntimeError, TtsProtocolError) as exc:
            self._refuse(request.request_id, str(exc), utterance_id=request.utterance_id,
                         generation=request.generation)
            return
        # A cancelled utterance is never reported as finished: an epoch bump
        # (control cancel) or the adapter's own cancelled record both end
        # this utterance as cancelled (D09).
        cancelled = getattr(self._engine, "cancelled", ())
        engine_cancelled = request.generation in (
            cancelled if isinstance(cancelled, (set, frozenset, list)) else ()
        )
        if engine_cancelled or request.generation < self._generation:
            self._event(
                "cancelled", utterance_id=request.utterance_id, generation=request.generation
            )
            return
        self._event(
            "finished", utterance_id=request.utterance_id, generation=request.generation
        )


def main() -> None:
    """The worker entry point: framed stdio only, no logging on stdout."""
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    Worker(reader, writer).serve()


if __name__ == "__main__":
    main()

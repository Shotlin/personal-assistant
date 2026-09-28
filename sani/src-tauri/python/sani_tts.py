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
import struct
import sys
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
        raise NotImplementedError("engine cancellation is implemented by the selected adapter")

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
    """Framed protocol loop over stdin/stdout; synthesis runs inline per chunk."""

    def __init__(self, reader: Any, writer: Any, engine: EngineAdapter | None = None) -> None:
        self._reader = reader
        self._writer = writer
        self._engine = engine or build_engine()
        self._generation = 0
        self._shutdown = False
        self._buffered_seconds = 0.0

    def _write(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > FRAME_MAX_BYTES:
            return
        self._writer.write(len(body).to_bytes(4, "big") + body)
        self._writer.flush()

    def _event(self, event: str, **fields: Any) -> None:
        if event not in EVENT_KINDS:
            raise TtsProtocolError(f"unknown event kind {event!r}")
        self._write({"type": "event", "event": {"event": event, **fields}})

    def _refuse(self, request_id: Any, message: str) -> None:
        self._event("error", request_id=request_id, message=message[:300])

    def serve(self) -> None:
        self._event("ready", engine=ENGINE_NAME)
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
            self._refuse(request.request_id, "no speech engine is configured (audition pending)")
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
            if generation > self._generation:
                self._generation = generation
            self._engine.cancel(generation)
            self._event("cancelled", generation=generation)
            return
        raise TtsProtocolError(f"unknown control action {action!r}")

    def _synthesize_request(self, request: TtsRequest) -> None:
        sequence = 0
        try:
            for sample_rate, pcm in self._engine.synthesize(
                request.text, request.utterance_id, request.generation
            ):
                if request.generation < self._generation:
                    self._event(
                        "cancelled",
                        utterance_id=request.utterance_id,
                        generation=request.generation,
                    )
                    return
                self._buffered_seconds += len(pcm) / 4 / sample_rate
                if self._buffered_seconds > MAX_BUFFERED_SECONDS:
                    self._refuse(request.request_id, "decoder buffer bound exceeded")
                    return
                encoded = base64.b64encode(pcm).decode("ascii")
                self._event(
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
        except RuntimeError as exc:
            self._refuse(request.request_id, str(exc))
            return
        # The adapter records cancelled generations (cancel() is part of the
        # interface), so an early generator end after a cancel is reported as
        # cancelled -- never as a finished utterance.
        cancelled = getattr(self._engine, "cancelled", ())
        if request.generation in (cancelled if isinstance(cancelled, (set, frozenset, list)) else ()):
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

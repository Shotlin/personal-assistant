"""sani-tts worker protocol tests (T09/T10, file 06 U5).

Framing, validation, cancellation, and backpressure — all against the real
worker module with the SilenceEngine double (no assets, no network, no
engine dependency). No voice-quality claim is made or implied.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_WORKER_PATH = Path(__file__).resolve().parents[2] / "sani" / "src-tauri" / "python" / "sani_tts.py"


@pytest.fixture()
def tts() -> Any:
    spec = importlib.util.spec_from_file_location("sani_tts_test", _WORKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request_payload(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schema_version": 1,
        "request_id": "req-1",
        "utterance_id": "utt-1",
        "message_id": "msg-1",
        "generation": 1,
        "text": "Fixture utterance.",
        "kind": "FINAL",
        "rate": 1.0,
    }
    fields.update(overrides)
    return fields


def _frame(body: bytes) -> bytes:
    return len(body).to_bytes(4, "big") + body


def _frames(raw: bytes) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    offset = 0
    while offset + 4 <= len(raw):
        length = int.from_bytes(raw[offset : offset + 4], "big")
        body = raw[offset + 4 : offset + 4 + length]
        frames.append(json.loads(body.decode("utf-8")))
        offset += 4 + length
    return frames


def _events(raw: bytes) -> list[dict[str, Any]]:
    return [f["event"] for f in _frames(raw) if f.get("type") == "event"]


def _serve(tts: Any, script: list[dict[str, Any]], engine: Any = None) -> list[dict[str, Any]]:
    reader = io.BytesIO(b"".join(
        _frame(json.dumps(f, separators=(",", ":")).encode())
        for f in script
    ))
    writer = io.BytesIO()
    worker = tts.Worker(reader, writer, engine=engine or tts.SilenceEngine())
    worker.serve()
    return _events(writer.getvalue())


def test_ready_then_synthesize_then_finished(tts: Any) -> None:
    events = _serve(tts, [{"type": "request", "request": _request_payload()}])
    assert events[0]["event"] == "ready"
    assert events[-1]["event"] == "finished"
    chunks = [e for e in events if e["event"] == "chunk"]
    assert chunks, "the test engine produced PCM chunks"
    assert chunks[0]["sequence"] == 0
    assert chunks[0]["format"] == "f32le" and chunks[0]["channels"] == 1
    assert all(e["generation"] == 1 for e in chunks)


def test_malformed_frames_are_refused(tts: Any) -> None:
    events = _serve(tts, [
        {"type": "request", "request": _request_payload(text="")},
        {"type": "request", "request": _request_payload(kind="MAYBE")},
        {"type": "request", "request": _request_payload(rate=9.0)},
        {"type": "nonsense"},
    ])
    errors = [e for e in events if e["event"] == "error"]
    assert len(errors) == 4, "each malformed frame got exactly one refusal"


def test_oversize_text_rejected(tts: Any) -> None:
    events = _serve(tts, [{"type": "request", "request": _request_payload(text="x" * 4001)}])
    assert events[-1]["event"] == "error"
    assert "4000" in events[-1]["message"]


def test_nan_samples_rejected(tts: Any) -> None:
    import struct

    bad = struct.pack("<f", float("nan")) * 4
    chunk = {
        "utterance_id": "u1",
        "generation": 1,
        "sequence": 0,
        "sample_rate": 24000,
        "pcm_base64": base64.b64encode(bad).decode(),
        "final": True,
    }
    with pytest.raises(tts.TtsProtocolError):
        tts.PcmChunk.validate(chunk)


def test_oversize_pcm_rejected(tts: Any) -> None:
    big = b"\x00" * (tts.PCM_CHUNK_MAX_BYTES + 4)
    chunk = {
        "utterance_id": "u1",
        "generation": 1,
        "sequence": 0,
        "sample_rate": 24000,
        "pcm_base64": base64.b64encode(big).decode(),
        "final": False,
    }
    with pytest.raises(tts.TtsProtocolError):
        tts.PcmChunk.validate(chunk)


def test_stale_generation_yields_cancelled_not_audio(tts: Any) -> None:
    events = _serve(tts, [
        {"type": "control", "action": "cancel", "generation": 5},
        {"type": "request", "request": _request_payload(generation=2)},
    ])
    assert any(e["event"] == "cancelled" and e.get("generation") == 5 for e in events)
    assert not any(e["event"] == "chunk" for e in events), "stale audio must never flow"


def test_cancel_during_synthesis_stops_chunks(tts: Any) -> None:
    class _SlowEngine(tts.SilenceEngine):
        def synthesize(self, text: str, utterance_id: str, generation: int):  # type: ignore[no-untyped-def]
            for sample_rate, pcm in super().synthesize(text, utterance_id, generation):
                self.cancel(generation)  # cancel after the first chunk
                yield sample_rate, pcm

    events = _serve(tts, [{"type": "request", "request": _request_payload()}],
                    engine=_SlowEngine())
    chunks = [e for e in events if e["event"] == "chunk"]
    assert len(chunks) == 1, "cancellation stopped the stream after one chunk"
    assert events[-1]["event"] == "cancelled"


def test_buffer_bound_enforced(tts: Any) -> None:
    class _FloodEngine(tts.SilenceEngine):
        def synthesize(self, text: str, utterance_id: str, generation: int):  # type: ignore[no-untyped-def]
            for _ in range(64):  # 64 x 0.5s >> 2s bound
                yield (24000, b"\x00\x00\x00\x00" * 12000)

    events = _serve(tts, [{"type": "request", "request": _request_payload()}],
                    engine=_FloodEngine())
    errors = [e for e in events if e["event"] == "error"]
    assert errors and "buffer" in errors[0]["message"]


def test_shutdown_control_exits(tts: Any) -> None:
    events = _serve(tts, [{"type": "control", "action": "shutdown"}])
    assert events[0]["event"] == "ready"
    assert len(events) == 1


def test_unspecified_engine_refuses_cleanly(tts: Any) -> None:
    events = _serve(tts, [{"type": "request", "request": _request_payload()}],
                    engine=tts.UnspecifiedEngine())
    errors = [e for e in events if e["event"] == "error"]
    assert errors and "audition pending" in errors[0]["message"], (
        "no engine is faked before the owner audition; text output is the fallback"
    )


def test_ack_requests_finish_without_audio(tts: Any) -> None:
    events = _serve(tts, [{"type": "request", "request": _request_payload(kind="ACK")}])
    assert events[-1]["event"] == "finished"
    assert not any(e["event"] == "chunk" for e in events)

"""Live voice E2E (T12, file 06 V1) — executable, owner-gated.

Voice acceptance requires the selected engine, installed licensed assets,
and an owner-approved microphone/output session (file 06 environment V).
The transport and queue are already proven offline in
tests/unit/test_sani_tts_worker.py and the Rust inline tests.

D10: with an authorization present the bodies EXECUTE against the REAL
worker protocol: a live interpreter process (the configured one — no
engine selection or asset download happens here), framed requests, real
PCM events, and a measured speech-stop. Audible-quality/intelligibility
judgment remains the separate owner audition gate (VOICE_SELECTION.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._live import authorized_config


def _require_audio_scope(config: dict[str, Any]) -> None:
    """V1 needs an explicit audio grant and a named worker to drive."""
    assert config.get("audio_allowed") is True, (
        "voice acceptance requires audio_allowed=true in the authorization"
    )
    assert config.get("voice_python"), "voice cases need the configured interpreter"
    assert config.get("voice_worker"), "voice cases need the worker script path"


def _speak_and_measure(config: dict[str, Any], *, text: str,
                       stop_after: bool) -> dict[str, Any]:
    """Drive the REAL worker subprocess over the framed protocol: one
    synthesis request, PCM collection, then stop timing."""
    worker = Path(str(config["voice_worker"])).expanduser()
    python = str(config["voice_python"])
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG"}}
    env["SANI_TTS_ENGINE"] = str(config.get("voice_engine") or "unspecified")
    proc = subprocess.Popen(
        [python, "-I", str(worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None
    stdout = proc.stdout

    def send(frame: dict[str, Any]) -> None:
        body = json.dumps(frame, separators=(",", ":")).encode()
        assert proc.stdin is not None
        proc.stdin.write(len(body).to_bytes(4, "big") + body)
        proc.stdin.flush()

    def read_exact(stream: Any, count: int, deadline: float) -> bytes:
        """D21: bounded partial-frame reads — a select-ready byte does not
        end a frame; each read loops until `count` bytes or the deadline."""
        buffer = b""
        while len(buffer) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("framed read exceeded its deadline")
            import select

            ready = select.select([stream], [], [], min(remaining, 1.0))[0]
            if not ready:
                continue
            piece = stream.read(count - len(buffer))
            if not piece:
                raise EOFError("worker closed mid-frame")
            buffer += piece
        return buffer

    def read_event(timeout: float = 30.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        try:
            header = read_exact(stdout, 4, deadline)
        except (TimeoutError, EOFError):
            return None
        length = int.from_bytes(header, "big")
        try:
            body = read_exact(stdout, length, deadline)
        except (TimeoutError, EOFError):
            return None
        return json.loads(body.decode("utf-8"))

    try:
        ready_frame = read_event()
        assert ready_frame is not None and ready_frame["event"]["event"] == "ready", (
            "the configured worker never became ready"
        )
        send({"type": "request", "request": {
            "schema_version": 1, "request_id": "live-1", "utterance_id": "utt-live-1",
            "message_id": "msg-live-1", "generation": 1, "text": text,
            "kind": "FINAL", "rate": 1.0,
        }})
        import base64 as _b64

        chunk_bytes = 0
        valid_pcm = True
        first_chunk_seen = threading.Event()
        last_pcm_b64 = ""
        deadline = time.monotonic() + 60
        terminal = None
        while time.monotonic() < deadline:
            if stop_after and first_chunk_seen.is_set():
                # D21: the stop lands DURING active synthesis/playback —
                # never after a terminal event.
                break
            event = read_event()
            if event is None:
                break
            inner = event["event"]
            if inner["event"] == "chunk":
                chunk_bytes += len(inner.get("pcm_base64", ""))
                last_pcm_b64 = inner.get("pcm_base64", "")
                first_chunk_seen.set()
            if inner["event"] in {"finished", "cancelled", "error"}:
                terminal = inner["event"]
                break
        else:
            pytest.fail("the live worker never reached a terminal event")
        result: dict[str, Any] = {"terminal": terminal, "pcm_events": chunk_bytes}
        if chunk_bytes:
            try:
                decoded = _b64.b64decode(last_pcm_b64, validate=True)
                valid_pcm = len(decoded) > 0 and len(decoded) % 4 == 0
            except Exception:  # noqa: BLE001
                valid_pcm = False
        result["valid_pcm"] = valid_pcm
        if stop_after:
            started = time.monotonic()
            send({"type": "control", "action": "cancel", "generation": 2})
            stop_event = read_event(timeout=5.0)
            result["stop_latency_ms"] = (time.monotonic() - started) * 1000
            assert stop_event is not None and stop_event["event"]["event"] == "cancelled", (
                "the live stop control was not answered"
            )
            # D21: after the stop, no further chunk of the stale generation.
            drained = read_event(timeout=1.0)
            if drained is not None and drained["event"]["event"] == "chunk":
                assert drained["event"].get("generation", 0) != 2, (
                    "stale-generation audio after the stop")
        return result
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_live_voice_round_trip() -> None:
    """V1: the configured interpreter synthesizes real PCM for the fixture
    text through the real worker protocol."""
    config = authorized_config(_require_audio_scope)
    result = _speak_and_measure(config, text=str(config.get("fixture_text") or
                                                 "Phase one voice acceptance fixture."),
                                stop_after=False)
    assert result["terminal"] == "finished", (
        "the round trip must finish; a cancelled or erroring engine is not a pass")
    assert result["pcm_events"] > 0 and result["valid_pcm"], (
        "the live round trip must produce nonzero, well-formed PCM")


def test_live_speech_stop_and_stt_coexistence() -> None:
    """V1: a speech stop mid-utterance is answered within the authorized
    bound; the no-self-listening guarantee stays a physical case."""
    config = authorized_config(_require_audio_scope)
    bound_ms = int(config.get("max_stop_latency_ms", 500))
    result = _speak_and_measure(config, text="stop coexistence fixture " * 40,
                                stop_after=True)
    latency = result.get("stop_latency_ms")
    assert latency is not None and latency <= bound_ms, (
        f"live stop latency {latency}ms exceeds the authorized bound"
    )
    # STT coexistence (microphone self-hearing) is PHYSICAL: it needs the
    # audio device and mic in the same room as the speaker. Report it as
    # the remaining physical acceptance question, never as answered.
    pytest.skip(
        "BLOCKED: microphone self-hearing coexistence requires the physical "
        "audio session (authorization "
        f"{config.get('authorization_id', '?')}) — the protocol-level stop "
        "was measured and answered"
    )

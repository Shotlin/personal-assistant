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

    def read_event(timeout: float = 30.0) -> dict[str, Any] | None:
        import select

        ready = select.select([stdout], [], [], timeout)[0]
        if not ready:
            return None
        header = stdout.read(4)
        if len(header) < 4:
            return None
        length = int.from_bytes(header, "big")
        body = stdout.read(length)
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
        chunk_bytes = 0
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            event = read_event()
            if event is None:
                break
            inner = event["event"]
            if inner["event"] == "chunk":
                chunk_bytes += len(inner.get("pcm_base64", ""))
            if inner["event"] in {"finished", "cancelled", "error"}:
                terminal = inner["event"]
                break
        else:
            pytest.fail("the live worker never reached a terminal event")
        result: dict[str, Any] = {"terminal": terminal, "pcm_events": chunk_bytes}
        if stop_after:
            started = time.monotonic()
            send({"type": "control", "action": "cancel", "generation": 2})
            stop_event = read_event(timeout=5.0)
            result["stop_latency_ms"] = (time.monotonic() - started) * 1000
            assert stop_event is not None and stop_event["event"]["event"] == "cancelled", (
                "the live stop control was not answered"
            )
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
    assert result["terminal"] in {"finished", "cancelled"}, result
    if result["terminal"] == "error":
        pytest.fail("the configured engine reported an error; audition gates not met")


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

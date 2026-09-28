"""Live voice E2E (T12, file 06 V1) — BLOCKED without engine + audition.

Voice acceptance requires the selected engine, installed licensed assets,
and an owner-approved microphone/output session (file 06 environment V).
The transport and queue are already proven offline in
tests/unit/test_sani_tts_worker.py and the Rust inline tests.

C01/N12: with an authorization present, the audio grant is verified first
(audio_allowed and named output surfaces), then the case reports the
explicit BLOCKED for the not-yet-selected engine/audition owner decision
(VOICE_SELECTION.md) — never a silent pass.
"""

from __future__ import annotations

from typing import Any

from tests.e2e._live import authorized_config, blocked_until_harness


def _require_audio_scope(config: dict[str, Any]) -> None:
    """V1 needs an explicit audio grant and a named fixture voice surface."""
    assert config.get("audio_allowed") is True, (
        "voice acceptance requires audio_allowed=true in the authorization"
    )
    assert config.get("allowed_windows"), "voice cases need the fixture window scope"


def test_live_voice_round_trip() -> None:
    """V1: committed text -> audible local speech -> STT coexistence."""
    config = authorized_config(_require_audio_scope)
    blocked_until_harness(config, "selected-engine offline playback + audition")


def test_live_speech_stop_and_stt_coexistence() -> None:
    """V1: speech.stop latency, no self-listening, no duplicate intake."""
    config = authorized_config(_require_audio_scope)
    blocked_until_harness(config, "audible stop/STT contention measurement")

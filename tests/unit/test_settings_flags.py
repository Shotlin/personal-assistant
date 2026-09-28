"""Phase 1 capability flags (file 03 §11): default off, RSI mode immutable."""

from __future__ import annotations

from assistant.settings import Settings


def test_phase1_capability_flags_default_off() -> None:
    settings = Settings(
        memory_backend="sqlite",
        sani_data_dir="/tmp/fixture-sani-data",
        cua_enabled=False,
        openrouter_api_key="fixture-not-a-secret",
    )
    assert settings.jarvis_missions_enabled is False
    assert settings.sani_tts_enabled is False


def test_rsi_mode_is_observation_only_and_not_env_switchable() -> None:
    """The experiments plane must not exist behind a settings flip."""
    settings = Settings(
        memory_backend="sqlite",
        sani_data_dir="/tmp/fixture-sani-data",
        cua_enabled=False,
        openrouter_api_key="fixture-not-a-secret",
    )
    assert settings.rsi_mode == "observation_only"

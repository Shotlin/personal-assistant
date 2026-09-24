"""CUA availability and manifest checks that need no live driver."""

import pytest

from assistant.settings import Settings
from assistant.tools.cua import _require_manifest, load_cua_tools


def test_require_manifest_rejects_missing_file() -> None:
    settings = Settings(
        cua_enabled=True,
        cua_capability_manifest_path="/nonexistent/path/cua-capabilities.yaml",
        openrouter_api_key="dummy",
    )
    with pytest.raises(RuntimeError, match="capability manifest not found"):
        _require_manifest(settings)


def test_require_manifest_rejects_relative_path() -> None:
    # model_construct bypasses Settings validation so the loader's own
    # defense-in-depth check is exercised directly.
    settings = Settings.model_construct(
        cua_enabled=True,
        cua_capability_manifest_path="relative/path.yaml",
    )
    with pytest.raises(RuntimeError, match="absolute"):
        _require_manifest(settings)


async def test_load_cua_tools_fails_without_manifest() -> None:
    settings = Settings(
        cua_enabled=True,
        cua_capability_manifest_path="/nonexistent/path/cua-capabilities.yaml",
        openrouter_api_key="dummy",
    )
    with pytest.raises(RuntimeError, match="capability manifest not found"):
        await load_cua_tools(settings)


def _dev_settings(**overrides) -> Settings:
    base = dict(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        openrouter_api_key="dummy",
        # The repository's .env may disable CUA; these tests are about the CUA
        # contract, so the subsystem is pinned on rather than inherited.
        cua_enabled=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_standard_mode_demands_no_manifest() -> None:
    """Architecture D1: the ceiling lives in Sani, so a policy file is not the
    artifact this mode may be asked for."""
    assert _require_manifest(_dev_settings(cua_permission_mode="standard")) is None
    assert _dev_settings(cua_permission_mode="standard").cua_enabled is True


def test_unrestricted_mode_is_still_rejected() -> None:
    from assistant.settings import SettingsError

    with pytest.raises(SettingsError, match="unrestricted mode is not allowed"):
        _dev_settings(cua_permission_mode="unrestricted")


def test_bounded_mode_still_requires_a_manifest_path() -> None:
    from assistant.settings import SettingsError

    with pytest.raises(SettingsError, match="CUA_CAPABILITY_MANIFEST_PATH"):
        _dev_settings(cua_permission_mode="bounded", cua_capability_manifest_path="")


def test_a_standard_daemon_passes_a_standard_expectation_and_fails_a_bounded_one() -> None:
    from assistant.tools.cua import _evaluate_daemon_status

    standard = (
        "Cua Driver daemon is running\n"
        "  permission mode: standard (trusted_startup_configuration)\n"
        "  capability manifest: configured=false, approved_at_startup=false, valid=true\n"
    )
    assert _evaluate_daemon_status(standard, "standard") is None
    reason = _evaluate_daemon_status(standard, "bounded")
    assert reason is not None and "not in bounded mode" in reason


def test_a_bounded_daemon_fails_a_standard_expectation() -> None:
    """The check is symmetric: it verifies the launched mode, never a preference."""
    from assistant.tools.cua import _evaluate_daemon_status

    bounded = (
        "Cua Driver daemon is running\n"
        "  permission mode: bounded (trusted_startup_configuration)\n"
        "  capability manifest: configured=true, approved_at_startup=true, valid=true\n"
    )
    reason = _evaluate_daemon_status(bounded, "standard")
    assert reason is not None and "not in standard mode" in reason

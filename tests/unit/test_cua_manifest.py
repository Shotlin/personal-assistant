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

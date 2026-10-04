"""ZCode's plans and models, read from the config file the ZCode app ships.

Plan names and which models each plan offers come from ``zcode-builtin.json``
(non-secret, inside the app). Live token balances come from a Z.ai web service
that needs ZCode's own encrypted sign-in token, so they are NOT available here
and are reported as such, never guessed. The one thing read from the user's own
config is the non-secret ``defaultModelSelection`` (which plan/model ZCode will
use), never anything else in that file.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any

_RELATIVE = Path("config") / "provider" / "zcode-builtin.json"
#: Plans in the order the ZCode app lists them (Z.ai first, then BigModel).
_FAMILY_ORDER = {"zai-family": 0, "bigmodel-family": 1}
_FAMILY_NAME = {"zai-family": "Z.ai", "bigmodel-family": "BigModel (China)"}


def _context_windows(rules: list[dict[str, Any]], model_id: str) -> tuple[int | None, int | None]:
    """Apply the shipped model rules in order: the last match for each field wins."""
    context: int | None = None
    output: int | None = None
    for rule in rules:
        pattern = rule.get("modelMatch")
        if not isinstance(pattern, str):
            continue
        with contextlib.suppress(re.error):
            if re.fullmatch(pattern, model_id, re.IGNORECASE) is None:
                continue
        config = rule.get("config") or {}
        window = (config.get("properties") or {}).get("contextWindow")
        if isinstance(window, int):
            context = window
        limit = ((config.get("optionSpecs") or {}).get("maxOutputTokens") or {}).get("max")
        if isinstance(limit, int):
            output = limit
    return context, output


def builtin_config_path(binary: Path | None) -> Path | None:
    if binary is None:
        return None
    candidate = binary.resolve().parent.parent / _RELATIVE
    return candidate if candidate.is_file() else None


def load_catalog(binary: Path | None) -> list[dict[str, Any]]:
    """Plans, each with its models, context size and output limit. Empty if unreadable."""
    path = builtin_config_path(binary)
    if path is None:
        return []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))["config"]
        providers = config["providerConfigRules"]["providerRules"]
        model_rules = config["modelConfigRules"]
        offered = model_rules["builtinProviderModelRules"]
        shape = model_rules["modelRules"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    plans: list[dict[str, Any]] = []
    for provider in providers:
        provider_id = str(provider.get("providerId") or "")
        family = str((provider.get("config") or {}).get("group") or "")
        models: list[dict[str, Any]] = []
        for rule in offered:
            if rule.get("providerId") != provider_id or not (rule.get("config") or {}).get(
                "enabled", True
            ):
                continue
            model_id = str(rule.get("modelId") or "")
            context, output = _context_windows(shape, model_id)
            models.append({"id": model_id, "context_window": context, "max_output": output})
        if provider_id and models:
            plans.append(
                {
                    "id": provider_id,
                    "name": str(provider.get("providerName") or provider_id),
                    "family": family,
                    "family_name": _FAMILY_NAME.get(family, family),
                    "models": models,
                }
            )
    plans.sort(key=lambda plan: _FAMILY_ORDER.get(plan["family"], 9))
    return plans


def zcode_default_selection() -> dict[str, str] | None:
    """Which plan/model ZCode will use by default (only that field of the user's config)."""
    home = Path(os.environ.get("ZCODE_HOME") or Path.home() / ".zcode").expanduser()
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        data = json.loads((home / "v2" / "provider_config.json").read_text(encoding="utf-8"))
        chosen = data["config"]["defaultModelSelection"]
        return {"provider": str(chosen["providerId"]), "model": str(chosen["modelId"])}
    return None

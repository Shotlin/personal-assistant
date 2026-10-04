"""How full the Deep Agent's own context window is, per conversation.

The number comes from what the provider reports for the latest model request
(input + output tokens = what the next request will carry at minimum). When a
provider reports nothing, a rough character-based estimate stands in and is
marked as such. The window size comes from OpenRouter's public model list
(the one host Sani already talks to), cached, with a conservative fallback.
Nothing here stores message text.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_FALLBACK_WINDOW = 128_000
_MODELS_URL = "https://openrouter.ai/api/v1/models"

_lock = threading.Lock()
_tokens: dict[str, tuple[int, bool]] = {}  # conversation -> (tokens, estimated)
_models: dict[str, str] = {}
_default_model = ""


def set_default_model(model: str) -> None:
    global _default_model
    _default_model = model


_windows: dict[str, int] = {}


def record(conversation: str, tokens: int, *, model: str = "", estimated: bool = False) -> None:
    if not conversation or tokens <= 0:
        return
    with _lock:
        _tokens[conversation] = (int(tokens), estimated)
        _models[conversation] = model


def estimate_add(conversation: str, text: str, model: str = "") -> None:
    """Grow a rough estimate by `text` (about 4 characters per token)."""
    if not conversation or not text:
        return
    with _lock:
        base, _ = _tokens.get(conversation, (0, True))
        _tokens[conversation] = (base + max(1, len(text) // 4), True)
        _models[conversation] = model


def _fetch_windows() -> dict[str, int]:
    try:
        request = urllib.request.Request(_MODELS_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed https URL
            data = json.load(response)
        return {
            str(item["id"]): int(item["context_length"])
            for item in data.get("data", [])
            if item.get("id") and item.get("context_length")
        }
    except Exception as exc:  # network is optional; the meter degrades, never fails
        logger.info("context window list unavailable: %s", exc)
        return {}


def window_for(model: str) -> int:
    with _lock:
        known = _windows.get(model)
        if known:
            return known
        loaded = bool(_windows)
    if not loaded:
        fetched = _fetch_windows()
        with _lock:
            _windows.update(fetched or {"": 0})
            known = _windows.get(model)
        if known:
            return known
    return _FALLBACK_WINDOW


def snapshot(conversation: str) -> dict[str, Any]:
    with _lock:
        entry = _tokens.get(conversation)
        model = _models.get(conversation, "")
    if entry is None:
        model = model or _default_model
        return {"known": False, "window": window_for(model) if model else None, "model": model}
    tokens, estimated = entry
    window = window_for(model)
    return {
        "known": True,
        "tokens": tokens,
        "window": window,
        "percent": min(100, round(100 * tokens / window)) if window else None,
        "estimated": estimated,
        "model": model,
    }

"""The eyes: one screenshot, one question, a bounded structured answer.

Used only when the accessibility tree cannot name the control (a canvas, an icon
without a label, an image-heavy page) or a remembered spot no longer matches.
The model never acts and never chats: it returns where a described control is,
as coordinates in the screenshot it was shown, or says it is not there. The
caller clicks, then verifies the screen changed like for any other action.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("assistant.velo.vision")

LOCATE_PROMPT = (
    "You see one screenshot of an application window. Find the single control the user "
    "means and answer with ONLY a JSON object: "
    '{"found": true|false, "label": "<its visible text or short description>", '
    '"x": <pixels from the left edge of THIS image to the control\'s centre>, '
    '"y": <pixels from the top edge of THIS image to the control\'s centre>, '
    '"confidence": <0..1>}. Pixel coordinates are for the image exactly as given. '
    'If the control is not visible, answer {"found": false}. If several match, choose '
    "the most prominent one in front (a modal dialog's button beats the page behind it). "
    "Never guess a position you cannot see."
)

CONTROLS_PROMPT = (
    "You see one screenshot of an application window. List the clickable controls "
    "(buttons, links, tabs, icons, menu entries, input boxes) that are clearly visible, "
    "at most 25, as ONLY a JSON array of "
    '{"label": "<visible text or short description>", "role": "button|link|tab|icon|input|menu", '
    '"x": <centre pixels from the left>, "y": <centre pixels from the top>}. '
    "Coordinates are for the image exactly as given. Do not include page text that is not "
    "clickable."
)


@dataclass(frozen=True)
class VisionHit:
    label: str
    x: float
    y: float
    confidence: float


def _json_in(text: str) -> Any:
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_hit(text: str, width: int, height: int) -> VisionHit | None:
    """A validated hit inside the image bounds, or ``None`` (never a guess)."""
    data = _json_in(text)
    if not isinstance(data, dict) or data.get("found") is not True:
        return None
    try:
        x, y = float(data["x"]), float(data["y"])
        confidence = float(data.get("confidence", 0.5))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= width and 0 <= y <= height):
        return None
    return VisionHit(str(data.get("label") or "")[:80], x, y, max(0.0, min(1.0, confidence)))


def parse_controls(text: str, width: int, height: int) -> list[VisionHit]:
    data = _json_in(text)
    hits: list[VisionHit] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            x, y = float(item["x"]), float(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(item.get("label") or "").strip()
        if label and 0 <= x <= width and 0 <= y <= height:
            hits.append(VisionHit(label[:80], x, y, 0.7))
    return hits[:25]


class VisionLocator:
    """One bounded model call over one image. Fails closed to ``None``."""

    def __init__(self, settings: Any, *, model: Any = None) -> None:
        self._settings = settings
        self._model = model

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "velo_vision_enabled", True))

    def _chat_model(self) -> Any:
        if self._model is None:
            from assistant.models import build_chat_model

            override = str(getattr(self._settings, "velo_vision_model", "") or "").strip()
            settings = self._settings
            if override:
                settings = self._settings.model_copy(update={"model_name": override})
            self._model = build_chat_model(settings)
        return self._model

    async def _ask(self, prompt: str, question: str, png: bytes) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        reply = await asyncio.wait_for(
            self._chat_model().ainvoke([
                SystemMessage(prompt),
                HumanMessage(content=[
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]),
            ]),
            timeout=40,
        )
        content = getattr(reply, "content", "")
        if isinstance(content, list):
            content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        return str(content)

    async def locate(self, png: bytes, target: str, width: int, height: int) -> VisionHit | None:
        if not self.enabled:
            return None
        try:
            text = await self._ask(LOCATE_PROMPT, f"Control to find: {target}", png)
        except Exception as exc:  # noqa: BLE001 -- vision is a bonus rung, never fatal
            logger.warning("vision_locate_failed: %s", exc)
            return None
        return parse_hit(text, width, height)

    async def controls(self, png: bytes, width: int, height: int) -> list[VisionHit]:
        if not self.enabled:
            return []
        try:
            text = await self._ask(CONTROLS_PROMPT, "List the clickable controls.", png)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision_controls_failed: %s", exc)
            return []
        return parse_controls(text, width, height)


__all__ = ["VisionHit", "VisionLocator", "parse_controls", "parse_hit"]

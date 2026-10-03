"""Find and click a control by sight, remembering what was learned.

Order, cheapest first: a remembered spot whose fingerprint still matches the
pixels there (no model), then ONE vision call. Every click is verified by the
screen actually changing; a click that changed nothing is never reported as done
and marks a remembered spot as bad.

Screenshots are read from a temporary file and deleted at once; nothing about a
screenshot is stored beyond a 64-bit hash of the clicked control.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.velo.contracts import OutcomeState, TaskState
from assistant.velo.uimemory import (
    MAX_HASH_DISTANCE,
    UiMemory,
    distance,
    fingerprint_of,
    scope_for,
)
from assistant.velo.vision import VisionLocator

logger = logging.getLogger("assistant.velo.sight")

#: Mean absolute difference (0..255) between two downsized screenshots that
#: counts as "the screen changed". Small enough to catch a popup closing, large
#: enough to ignore a cursor or a blinking caret.
CHANGE_THRESHOLD = 1.2


@dataclass
class SightKit:
    """What the recipes need for sight: eyes, memory, and where to look."""

    vision: VisionLocator | None
    memory: UiMemory | None


@dataclass(frozen=True)
class Shot:
    png: bytes
    width: int
    height: int
    win_w: int
    win_h: int


def guess_label(utterance: str) -> str:
    """The control name inside chatty dictation: the words just before "button"."""
    words = re.findall(r"[A-Za-z0-9']+", utterance)
    lowered = [w.lower() for w in words]
    for marker in ("button", "link", "icon", "tab"):
        if marker in lowered:
            end = lowered.index(marker)
            start = max(0, end - 3)
            chunk = [
                w for w in words[start:end]
                if w.lower() not in {"the", "a", "an", "this", "that", "here", "there", "you"}
            ]
            if chunk:
                return " ".join(chunk)
    return ""


def image_diff(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two screenshots at 32x32 (0 = identical)."""
    try:
        from PIL import Image

        def small(data: bytes) -> list[int]:
            image = Image.open(io.BytesIO(data)).convert("L").resize((32, 32))
            return list(image.getdata())

        pa, pb = small(a), small(b)
    except Exception:  # noqa: BLE001 -- unreadable means "cannot tell", i.e. changed=False
        return 0.0
    return sum(abs(x - y) for x, y in zip(pa, pb, strict=True)) / len(pa)


async def capture(task: TaskState, adapter: Any, pid: int, window_id: int) -> Shot | None:
    """One downsized window screenshot, read into memory and deleted from disk."""
    folder = Path(tempfile.mkdtemp(prefix="sani-shot-"))
    path = folder / "window.png"
    try:
        reply = await adapter.verification_call(
            task, "get_window_state", pid=pid, window_id=window_id,
            include_screenshot=False, include_accessibility_tree=False,
            max_dimension=1280, screenshot_out_file=str(path),
        )
        data = reply.structured or {}
        written = Path(str(data.get("screenshot_file_path") or path))
        if not written.is_file():
            return None
        png = written.read_bytes()
        bounds = data.get("window_bounds") or {}
        return Shot(
            png=png,
            width=int(data.get("screenshot_width") or 0),
            height=int(data.get("screenshot_height") or 0),
            win_w=int(bounds.get("width") or 0),
            win_h=int(bounds.get("height") or 0),
        )
    except Exception as exc:  # noqa: BLE001 -- sight is a bonus rung
        logger.info("sight_capture_failed: %s", exc)
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def _click_and_check(
    task: TaskState, adapter: Any, pid: int, window_id: int, shot: Shot, x: float, y: float,
) -> bool:
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, x=int(round(x)), y=int(round(y))
    )
    for attempt in range(4):
        await asyncio.sleep(0.4 if attempt == 0 else 0.5)
        after = await capture(task, adapter, pid, window_id)
        if after is not None and image_diff(shot.png, after.png) >= CHANGE_THRESHOLD:
            return True
    return False


async def find_and_click(
    task: TaskState, adapter: Any, *, pid: int, window_id: int, label: str,
    scope: str, description: str = "",
) -> tuple[OutcomeState, str] | None:
    """``(state, sentence)`` when sight found something to click, else ``None``."""
    kit: SightKit | None = getattr(adapter, "sight", None)
    if kit is None or not label.strip():
        return None
    shot = await capture(task, adapter, pid, window_id)
    if shot is None or shot.width <= 0 or shot.height <= 0:
        return None

    if kit.memory is not None:
        for spot in kit.memory.recall(scope, label, shot.win_w, shot.win_h):
            px, py = spot.rel_x * shot.width, spot.rel_y * shot.height
            if spot.fingerprint and distance(
                fingerprint_of(shot.png, px, py), spot.fingerprint
            ) > MAX_HASH_DISTANCE:
                continue  # the pixels there changed: not the same control any more
            if await _click_and_check(task, adapter, pid, window_id, shot, px, py):
                kit.memory.mark(spot, worked=True)
                return OutcomeState.CONFIRMED, f"Clicked {spot.label} (remembered its spot)."
            kit.memory.mark(spot, worked=False)

    if kit.vision is None or not kit.vision.enabled:
        return None
    hit = await kit.vision.locate(shot.png, description or label, shot.width, shot.height)
    if hit is None:
        return None
    if await _click_and_check(task, adapter, pid, window_id, shot, hit.x, hit.y):
        if kit.memory is not None:
            kit.memory.remember(
                scope, label, role="control", rel_x=hit.x / shot.width, rel_y=hit.y / shot.height,
                win_w=shot.win_w, win_h=shot.win_h,
                fingerprint=fingerprint_of(shot.png, hit.x, hit.y), source="vision",
            )
        return OutcomeState.CONFIRMED, f"Found {hit.label or label} by sight and clicked it."
    return (
        OutcomeState.UNKNOWN,
        f"I found {hit.label or label} by sight and clicked it, but the screen did not change.",
    )


async def learn_controls(
    task: TaskState, adapter: Any, *, pid: int, window_id: int, scope: str
) -> tuple[OutcomeState, str]:
    """Ask the vision model once for every visible control and remember them all."""
    kit: SightKit | None = getattr(adapter, "sight", None)
    if kit is None or kit.vision is None or not kit.vision.enabled or kit.memory is None:
        return OutcomeState.NO_EFFECT, (
            "Learning a screen needs the vision rung and its memory switched on."
        )
    shot = await capture(task, adapter, pid, window_id)
    if shot is None or shot.width <= 0:
        return OutcomeState.NO_EFFECT, "I couldn't take a picture of this window to learn from."
    controls = await kit.vision.controls(shot.png, shot.width, shot.height)
    for item in controls:
        kit.memory.remember(
            scope, item.label, role="control", rel_x=item.x / shot.width,
            rel_y=item.y / shot.height, win_w=shot.win_w, win_h=shot.win_h,
            fingerprint=fingerprint_of(shot.png, item.x, item.y), source="learned",
        )
    if not controls:
        return OutcomeState.UNKNOWN, "I looked at it but couldn't pick out any controls."
    return OutcomeState.CONFIRMED, (
        f"Learned {len(controls)} controls on {scope.split(':', 1)[1]}. "
        "Next time I can press them without looking again."
    )


def scope_from_state(app_name: str, state: dict[str, Any]) -> str:
    address = ""
    for element in state.get("elements") or []:
        if (
            isinstance(element, dict)
            and str(element.get("role") or "").lower().removeprefix("ax")
            in {"textfield", "combobox"}
            and "address" in str(element.get("label") or "").lower()
        ):
            address = str(element.get("value") or "")
            break
    return scope_for(app_name, address)


__all__ = [
    "Shot", "SightKit", "capture", "find_and_click", "guess_label", "image_diff",
    "learn_controls", "scope_from_state",
]

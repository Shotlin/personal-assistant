"""Which model ZCode should run, and whether Sani may switch it there (R3, R6, R9).

Sani's choice comes from its own settings (a model name, optionally a plan). It can only pick
something the ZCode app itself offers, and by default only a model on the user's Z.ai plan.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from assistant.coding_agents.zcode_model import is_zai_plan

#: ZCode's reasoning levels. Other words (medium, xhigh) are not guessed at.
REASONING_LEVELS = frozenset({"low", "high", "max"})


def pick_model(
    offered: list[dict[str, Any]], want_model: str, want_plan_id: str = "", *, allow_any: bool
) -> tuple[dict[str, Any] | None, str]:
    """``(entry, "")`` for the model to use, or ``(None, plain-words reason)``."""
    names = ", ".join(sorted({str(m["model"]) for m in offered})) or "none"
    matches = [
        m
        for m in offered
        if m["model"] == want_model and (not want_plan_id or m["plan_id"] == want_plan_id)
    ]
    if not matches:
        return None, f"ZCode does not offer {want_model} (it offers: {names}), so nothing was run."
    if not allow_any:
        matches = [m for m in matches if is_zai_plan(str(m["plan_id"]))]
        if not matches:
            return None, (
                f"{want_model} is not on your Z.ai plan in ZCode, so I did not run it and "
                "nothing was spent."
            )
    return matches[0], ""


def model_testid(entry: dict[str, Any]) -> str:
    """The picker item's test id, rebuilt the way the ZCode app writes it."""
    plan = quote(str(entry["plan_id"]), safe="")
    return f"chat-model-select-item-{entry['provider']}:{plan}:{entry['model']}"


def reasoning_level(effort: str) -> str:
    """The ZCode level for a requested effort, or empty when it is not one ZCode has."""
    return effort.strip().lower() if effort.strip().lower() in REASONING_LEVELS else ""

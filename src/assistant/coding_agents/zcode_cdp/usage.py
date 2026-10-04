"""Tokens a run used, from ZCode's own balance read before and after (R10).

Only a difference of two real reads counts. If either read failed, or a balance went up (a daily
reset in between), the answer is "unknown", never an estimate.
"""

from __future__ import annotations

from typing import Any


def _remaining(items: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    return {(str(i["plan"]), str(i["model"])): int(i["remaining"]) for i in items}


def tokens_used(
    before: list[dict[str, Any]] | None, after: list[dict[str, Any]] | None
) -> tuple[int | None, list[dict[str, Any]]]:
    """``(total, per plan-and-model)``; total is None when it cannot be known."""
    if not before or not after:
        return None, []
    was, now = _remaining(before), _remaining(after)
    used: list[dict[str, Any]] = []
    for key, left_before in was.items():
        if key not in now:
            return None, []  # a plan appeared or vanished mid-run
        spent = left_before - now[key]
        if spent < 0:
            return None, []  # the allowance went up (a reset): the difference means nothing
        if spent:
            used.append({"plan": key[0], "model": key[1], "tokens": spent})
    return sum(u["tokens"] for u in used), used


def describe(total: int | None, parts: list[dict[str, Any]]) -> str:
    if total is None:
        return (
            "Tokens used: unknown (ZCode's balance could not be read both before and after, "
            "or it reset in between)."
        )
    if total == 0:
        return "Tokens used: none that ZCode's balance shows (it may not update instantly)."
    where = "; ".join(f"{p['tokens']:,} on {p['model']} ({p['plan']})" for p in parts)
    return (
        f"Tokens used: {total:,} ({where}), from ZCode's own balance before and after. "
        "Anything else using the same plan at the same time is included."
    )

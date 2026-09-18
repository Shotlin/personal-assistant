"""Live-driver foreground verification contract (2026-09-18 incident).

get_desktop_state carries no foreground identity on the real driver, so
verify_foreground must derive evidence from list_apps' per-app ``active``
flag, and activate() must expose one honest bring_to_front step.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import StructuredTool

from assistant.runtime.recipe_errors import RecipeFailure
from assistant.runtime.recipe_executor import RecipeExecutor


def _mk(name: str, fn: Any) -> StructuredTool:
    return StructuredTool(
        name=name,
        description=name,
        args_schema={"type": "object", "properties": {}},
        coroutine=fn,
    )


def _fake_tools(apps: list[dict[str, Any]]) -> dict[str, StructuredTool]:
    async def list_apps(**kwargs: Any) -> Any:
        return {"apps": apps}

    async def bring_to_front(**kwargs: Any) -> Any:
        return {"status": "ok", "effect": "confirmed", "brought": kwargs.get("pid")}

    return {
        "list_apps": _mk("list_apps", list_apps),
        "bring_to_front": _mk("bring_to_front", bring_to_front),
    }


def test_verify_foreground_reports_active_app() -> None:
    tools = _fake_tools([{"name": "Calculator", "bundle_id": "com.apple.calculator",
                          "pid": 42, "active": True}])
    executor = RecipeExecutor(cua_tools_by_name=tools)
    outcome = asyncio.run(executor.verify_foreground("calculator"))
    assert outcome.status == "ok"
    assert outcome.structured["foreground_app"] == "calculator"


def test_verify_foreground_fails_when_app_backgrounded() -> None:
    tools = _fake_tools([{"name": "Calculator", "bundle_id": "com.apple.calculator",
                          "pid": 42, "active": False}])
    executor = RecipeExecutor(cua_tools_by_name=tools)
    outcome = asyncio.run(executor.verify_foreground("calculator"))
    assert outcome.status == "failed"
    assert outcome.structured["foreground_app"] is None


def test_verify_foreground_fails_when_app_absent() -> None:
    executor = RecipeExecutor(cua_tools_by_name=_fake_tools(
        [{"name": "Finder", "bundle_id": "com.apple.finder", "active": True}]))
    outcome = asyncio.run(executor.verify_foreground("calculator"))
    assert outcome.status == "failed"


def test_activate_uses_launch_pid() -> None:
    tools = _fake_tools([])
    executor = RecipeExecutor(cua_tools_by_name=tools)
    executor._launched_pid = 77
    outcome = asyncio.run(executor.activate("calculator"))
    assert outcome.structured["brought"] == 77


def test_activate_targets_window_candidate_on_multi_window_refusal() -> None:
    calls: list[dict[str, Any]] = []

    async def bring(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        if "window_id" not in kwargs:
            return {
                "status": "failed",
                "code": "ambiguous_window",
                "candidates": [{"window_id": 192}, {"window_id": 200}],
            }
        return {"status": "ok", "effect": "confirmed", "brought": kwargs["window_id"]}

    executor = RecipeExecutor(cua_tools_by_name={
        "bring_to_front": _mk("bring_to_front", bring),
    })
    executor._launched_pid = 14820
    outcome = asyncio.run(executor.activate("calculator"))
    assert outcome.status == "ok"
    assert calls == [{"pid": 14820}, {"pid": 14820, "window_id": 192}]


def test_activate_returns_refusal_without_candidates() -> None:
    async def refuse(**kwargs: Any) -> Any:
        return {"status": "failed", "code": "no_candidates"}

    executor = RecipeExecutor(cua_tools_by_name={
        "bring_to_front": _mk("bring_to_front", refuse),
    })
    executor._launched_pid = 5
    outcome = asyncio.run(executor.activate("calculator"))
    assert outcome.status == "failed"
    assert outcome.structured["code"] == "no_candidates"


def test_activate_fails_closed_without_pid() -> None:
    tools = _fake_tools([])
    executor = RecipeExecutor(cua_tools_by_name=tools)
    executor._launched_pid = None
    try:
        asyncio.run(executor.activate("calculator"))
    except RecipeFailure as exc:
        assert "pid" in str(exc)
    else:
        raise AssertionError("activate must fail closed without a pid")

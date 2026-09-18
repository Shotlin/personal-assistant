"""Trusted session binding at the model-facing tool boundary (master plan WP3 / F09).

The manager owns session ids; the model must not be able to override the
injected session, see lifecycle tools in its inventory, or sneak an action
past local cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from assistant.agent.context import RunBudget
from assistant.runtime.session import DesktopSessionManager
from assistant.tools.policy import (
    CUA_ALLOWED_TOOL_NAMES,
    SESSION_LIFECYCLE_TOOL_NAMES,
    apply_tool_policy,
    cua_desktop_run,
    cua_run_budget,
)
from tests.helpers.fake_driver import FakeDriver, call_names


class _ClickArgs(BaseModel):
    session: str | None = None
    element_token: str | None = None


@pytest.fixture
def _reset_policy_vars() -> Iterator[None]:
    yield
    cua_desktop_run.set(None)
    cua_run_budget.set(None)


def _capturing_tool(name: str, args_model: type[BaseModel], captured: list[dict]) -> StructuredTool:
    async def call(**kwargs: object) -> str:
        captured.append(dict(kwargs))  # type: ignore[arg-type]
        return f"{name}-ran"

    return StructuredTool(
        name=name, description=f"fake {name}", args_schema=args_model, coroutine=call
    )


def test_session_lifecycle_tools_are_not_model_visible() -> None:
    """Session start/end/motion stay controller-owned (never in the inventory)."""
    assert CUA_ALLOWED_TOOL_NAMES.isdisjoint(SESSION_LIFECYCLE_TOOL_NAMES)


async def test_trusted_session_overrides_model_supplied_session(
    _reset_policy_vars: None,
) -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    captured: list[dict] = []
    wrapped, _ = apply_tool_policy([_capturing_tool("click", _ClickArgs, captured)])
    async with manager.open("run-t") as session:
        cua_desktop_run.set(session)
        try:
            await wrapped[0].ainvoke({"session": "model-chosen", "element_token": "tok-1"})
        finally:
            cua_desktop_run.set(None)
    assert captured[0]["session"] == session.session_id
    assert captured[0]["session"] != "model-chosen"


async def test_first_action_activates_session_lazily(_reset_policy_vars: None) -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    captured: list[dict] = []
    wrapped, _ = apply_tool_policy(
        [_capturing_tool("get_window_state", _ClickArgs, captured)]
    )
    async with manager.open("run-lazy") as session:
        cua_desktop_run.set(session)
        try:
            await wrapped[0].ainvoke({})
        finally:
            cua_desktop_run.set(None)
    # exactly one session start, driven by the first desktop action
    assert call_names(driver).count("start_session") == 1
    assert captured[0]["session"] == session.session_id


async def test_cancelled_run_refuses_action_without_consuming_budget(
    _reset_policy_vars: None,
) -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    budget = RunBudget()
    cua_run_budget.set(budget)
    captured: list[dict] = []
    wrapped, _ = apply_tool_policy([_capturing_tool("click", _ClickArgs, captured)])
    async with manager.open("run-x") as session:
        cua_desktop_run.set(session)
        try:
            session.cancel()
            result = await wrapped[0].ainvoke({"element_token": "tok-1"})
        finally:
            cua_desktop_run.set(None)
    assert "cancel" in str(result).lower()
    assert captured == []  # the underlying tool never ran
    assert budget.used == 0  # a refused action costs no budget


async def test_queued_action_does_not_dispatch_after_stop(_reset_policy_vars: None) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def click(**kwargs: object) -> str:
        calls.append(str(kwargs.get("element_token")))
        entered.set()
        await release.wait()
        return "first effect occurred"

    tool = StructuredTool(name="click", description="fake", args_schema=_ClickArgs, coroutine=click)
    wrapped, _ = apply_tool_policy([tool])
    manager = DesktopSessionManager(FakeDriver())
    budget = RunBudget()
    budget_token = cua_run_budget.set(budget)
    try:
        async with manager.open("queued") as session:
            token = cua_desktop_run.set(session)
            try:
                first = asyncio.create_task(wrapped[0].ainvoke({"element_token": "first"}))
                await entered.wait()
                second = asyncio.create_task(wrapped[0].ainvoke({"element_token": "second"}))
                await asyncio.sleep(0)
                manager.cancel("queued")
                release.set()
                results = await asyncio.gather(first, second)
            finally:
                cua_desktop_run.reset(token)
    finally:
        cua_run_budget.reset(budget_token)
    assert calls == ["first"]
    assert "cancel" in str(results[1]).lower()
    assert budget.used == 1

"""WP4: action-ledger rows are written around REAL CUA dispatch.

Through the same wrapped tools the model invokes (wrap_tool_errors), a
mutating call must produce a 'planned' row BEFORE the native dispatch and
a terminal state after: 'confirmed' on success, 'failed' on tool error,
'unknown' when the outcome was never observed (timeout). Observations
never create ledger rows; a cancelled run records nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from assistant.runtime.runs import RunActionLedger, RunStore
from assistant.tools.policy import apply_tool_policy, cua_run_scope
from assistant.tools.result_normalizer import ToolOutcome

#: What an aimed action has to be aimed AT. Since Phase 4 an action carrying an
#: ``element_token`` is refused unless the same target was just observed, and a
#: refused action never reaches the ledger -- these tests are about the ledger.
PID, WINDOW_ID, SNAPSHOT = 4242, 7, "s00000001"
AIMED = {"pid": PID, "window_id": WINDOW_ID}


def _uid() -> str:
    return uuid.uuid4().hex[:12]


async def _store_and_ledger() -> tuple[RunStore, RunActionLedger, str]:
    store = await RunStore.connect(
        "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
    )
    await store.setup()
    run_id = f"run-{_uid()}"
    claim = await store.claim(
        user_id=_uid(),
        chat_id=_uid(),
        user_message_id=_uid(),
        request_digest="d",
        run_id=run_id,
    )
    assert claim.owned
    return store, RunActionLedger(store, run_id=run_id), run_id


def _mutating_tool(name: str = "click", behavior: str = "ok") -> StructuredTool:
    async def coro(**kwargs: Any) -> Any:
        if behavior == "error":
            raise RuntimeError("driver exploded")
        if behavior == "timeout":
            raise TimeoutError("no acknowledgement")
        return {"ok": True, "tool": name, "args": kwargs}

    return StructuredTool(
        name=name,
        description="",
        args_schema={"type": "object", "properties": {}},
        coroutine=coro,
    )


@pytest.fixture
async def ledger_env():
    store, ledger, run_id = await _store_and_ledger()
    try:
        yield store, ledger, run_id
    finally:
        await store.close()


def _observation_tool() -> StructuredTool:
    """A ``get_window_state`` answer in the shape the driver really reports."""

    async def coro(**kwargs: Any) -> ToolOutcome:
        return ToolOutcome(
            status="ok",
            effect="confirmed",
            text="observed",
            structured={"pid": PID, "window_id": WINDOW_ID, "snapshot_id": SNAPSHOT},
        )

    return StructuredTool(
        name="get_window_state",
        description="",
        args_schema={"type": "object", "properties": {}},
        coroutine=coro,
    )


async def _invoke_wrapped(tool: StructuredTool, ledger: RunActionLedger) -> Any:
    """One mutating action taken the way the loop takes it: observe, then aim."""
    wrapped, _ = apply_tool_policy([_observation_tool(), tool])
    async with cua_run_scope(budget=None, run=None, ledger=ledger):
        await wrapped[0].ainvoke({**AIMED})
        return await wrapped[1].ainvoke({**AIMED, "element_token": f"{SNAPSHOT}:0", "session": ""})


async def test_mutating_call_writes_planned_then_confirmed(ledger_env) -> None:
    store, ledger, run_id = ledger_env
    tool = _mutating_tool()
    await _invoke_wrapped(tool, ledger)
    actions = await store.run_actions(ledger.run_id)
    assert len(actions) == 1, "exactly one ledger row per native mutation"
    row = actions[0]
    assert row["tool_name"] == "click"
    assert row["state"] == "confirmed"
    assert row["args_digest"], "digest must be recorded"


async def test_tool_error_marks_row_failed_not_confirmed(ledger_env) -> None:
    store, ledger, _run_id = ledger_env
    tool = _mutating_tool(behavior="error")
    await _invoke_wrapped(tool, ledger)
    store2_actions = await store.run_actions(ledger.run_id)
    assert store2_actions[0]["state"] == "failed"


async def test_unobserved_outcome_marks_unknown(ledger_env) -> None:
    store, ledger, _run_id = ledger_env
    tool = _mutating_tool(behavior="timeout")
    await _invoke_wrapped(tool, ledger)
    actions = await store.run_actions(ledger.run_id)
    assert actions[0]["state"] == "unknown", (
        "crash window must be unknown_effect, never confirmed"
    )


async def test_observation_tools_do_not_create_ledger_rows(ledger_env) -> None:
    store, ledger, run_id = ledger_env
    tool = _mutating_tool(name="list_windows")
    await _invoke_wrapped(tool, ledger)
    assert await store.run_actions(run_id) == []


async def test_no_ledger_in_scope_means_no_rows(ledger_env) -> None:
    store, _ledger, run_id = ledger_env
    wrapped, _ = apply_tool_policy([_observation_tool(), _mutating_tool()])
    async with cua_run_scope(budget=None, run=None):
        await wrapped[0].ainvoke({**AIMED})
        dispatched = await wrapped[1].ainvoke({**AIMED, "element_token": f"{SNAPSHOT}:0"})
    assert "refused" not in str(dispatched), "the action must really have dispatched"
    assert await store.run_actions(run_id) == []


async def test_digest_differs_per_arguments(ledger_env) -> None:
    store, ledger, _run_id = ledger_env
    wrapped, _ = apply_tool_policy(
        [_observation_tool(), _mutating_tool(), _mutating_tool()]
    )

    async with cua_run_scope(budget=None, run=None, ledger=ledger):
        await wrapped[0].ainvoke({**AIMED})
        await wrapped[1].ainvoke({**AIMED, "element_token": f"{SNAPSHOT}:0"})
        await wrapped[2].ainvoke({**AIMED, "element_token": f"{SNAPSHOT}:1"})
    actions = await store.run_actions(ledger.run_id)
    assert len(actions) == 2
    assert actions[0]["args_digest"] != actions[1]["args_digest"]

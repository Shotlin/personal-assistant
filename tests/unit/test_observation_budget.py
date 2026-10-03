"""Observation budget tests (master plan WP2)."""


from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from assistant.tools.policy import (
    OBSERVATION_DEFAULTS,
    apply_tool_policy,
    cua_artifact_dir,
    cua_current_session,
    cua_run_budget,
)


class _EmptyArgs(BaseModel):
    pass


class _WindowArgs(BaseModel):
    pid: int | None = None
    window_id: int | None = None
    include_screenshot: bool | None = None
    screenshot_out_file: str | None = None
    max_elements: int | None = None


class _SessionArgs(BaseModel):
    session: str | None = None
    pid: int | None = None
    window_id: int | None = None


def _fake_tool(name: str, args_model: BaseModel | None = None, captured: list | None = None):
    async def call(**kwargs):
        if captured is not None:
            captured.append(dict(kwargs))
        return f"{name}-ok"

    schema = args_model if args_model is not None else _EmptyArgs
    return StructuredTool(name=name, description=f"fake {name}", args_schema=schema, coroutine=call)


async def test_window_state_defaults_applied():
    captured: list[dict] = []
    wrapped, _ = apply_tool_policy([_fake_tool("get_window_state", _WindowArgs, captured)])
    await wrapped[0].ainvoke({"pid": 1})
    call = captured[0]
    assert call["include_screenshot"] is False
    assert call["max_elements"] == 1500
    assert call["max_depth"] == 30


async def test_an_explicit_screenshot_request_goes_to_the_model(tmp_path) -> None:
    """D3: the picture is the recovery rung, so asking for it must deliver it.

    Diverting an explicitly requested capture to a file left the model holding a
    path it cannot look at, which is not a rung it can climb.
    """
    captured: list[dict] = []
    wrapped, _ = apply_tool_policy([_fake_tool("get_window_state", _WindowArgs, captured)])
    cua_artifact_dir.set(str(tmp_path))
    await wrapped[0].ainvoke({"pid": 1, "window_id": 2, "include_screenshot": True})
    call = captured[0]
    assert call["include_screenshot"] is True
    assert "screenshot_out_file" not in call or not call["screenshot_out_file"]


async def test_session_injected_when_accepting_session_arg():
    captured: list[dict] = []
    wrapped, _ = apply_tool_policy([_fake_tool("click", _SessionArgs, captured)])
    cua_current_session.set("run-abc")
    cua_run_budget.set(None)
    await wrapped[0].ainvoke({"pid": 42, "window_id": 7})
    assert captured[0]["session"] == "run-abc"
    cua_current_session.set(None)


def test_defaults_cannot_be_disabled_by_config_only():
    # The defaults always exist; explicit model args may raise limits later
    # (bounded by hard caps in WP7), but the text-first baseline is set.
    assert "include_screenshot" in OBSERVATION_DEFAULTS["get_window_state"]

"""Fake CUA tools and runtimes for the Velo test suites.

The fakes mimic the real policy-wrapped tool contract exactly where the
adapter depends on it: ``ainvoke`` records the call, publishes the structured
payload through the policy's indexed call log (as the real wrapper does), and
returns model-facing text. Nothing touches the driver, a provider, or macOS.
"""

from __future__ import annotations

import contextlib
from typing import Any

from assistant.agent.context import RunBudget
from assistant.tools.policy import STRUCTURED_CALLS, cua_run_scope, desktop_contexts


class FakeTool:
    """One wrapped CUA tool double."""

    def __init__(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        respond: Any = None,
    ) -> None:
        self.name = name
        # The declared argument schema the adapter validates against.
        self.args = args if args is not None else {}
        self.calls: list[dict[str, Any]] = []
        self._respond = respond

    async def ainvoke(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(dict(kwargs))
        text, structured = ("ok", {}) if self._respond is None else self._respond(kwargs)
        if structured:
            STRUCTURED_CALLS.record(self.name, structured)
        return text

    def text_of(self, call_index: int = -1) -> str:
        return str(self.calls[call_index])


def apps_payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"apps": entries}


def windows_payload(windows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"windows": windows}


def window_state_payload(
    elements: list[dict[str, Any]], *, pid: int = 1, window_id: int = 1
) -> dict[str, Any]:
    return {
        "pid": pid,
        "window_id": window_id,
        "snapshot_id": f"snapshot-{pid}-{window_id}",
        "elements": elements,
    }


def _window_state_response(kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The real wrapper records the snapshotted surface as the conversation
    focus; the fake does the same so carried targets behave in tests."""
    from assistant.tools.policy import cua_target_state

    state = SHARED_APPS["state"].get((kwargs.get("pid"), kwargs.get("window_id")), {})
    pid, window_id = state.get("pid"), state.get("window_id")
    if isinstance(pid, int) and isinstance(window_id, int):
        target_state = cua_target_state.get()
        if target_state is not None:
            target_state["focus"] = (pid, window_id)
            target_state.setdefault("snapshots", {})[f"{pid}:{window_id}"] = str(
                state.get("snapshot_id", "")
            )
    return "Read the window.", state


def app_entry(
    name: str,
    *,
    pid: int | None = None,
    bundle_id: str = "",
    running: bool = True,
    active: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "bundle_id": bundle_id or f"com.example.{name.lower()}"}
    if pid is not None:
        entry["pid"] = pid
        entry["running"] = running
        entry["active"] = active
    return entry


class FakeRuntime:
    """A SaniRuntime double: run_scope + the tool inventory the adapter sees."""

    def __init__(self, tools: dict[str, Any]) -> None:
        self.cua_tools = tools
        self.opened_scopes: list[str] = []

    async def runtime(self) -> "FakeRuntime":
        return self

    @contextlib.asynccontextmanager
    async def run_scope(self, session_name: str, conversation: str = ""):
        # The real policy context: carried focus and observed app identities
        # survive between scopes of the same conversation, exactly in
        # production.
        self.opened_scopes.append((session_name, conversation))
        budget = RunBudget()
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(
                cua_run_scope(
                    budget=budget, run=None, artifact_dir="", conversation=conversation
                )
            )
            yield budget


class EventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append((kind, data))


def standard_tools() -> dict[str, FakeTool]:
    """The wrapped inventory the controller's recipes dispatch through."""
    return {
        "list_apps": FakeTool(
            "list_apps",
            respond=lambda kwargs: ("Listed apps.", apps_payload(SHARED_APPS["list"])),
        ),
        "list_windows": FakeTool(
            "list_windows",
            respond=lambda kwargs: (
                "Listed windows.",
                windows_payload(SHARED_APPS["windows"].get(kwargs.get("pid"), [])),
            ),
        ),
        "get_window_state": FakeTool(
            "get_window_state",
            respond=_window_state_response,
        ),
        "launch_app": FakeTool(
            "launch_app",
            args={"bundle_id": {"type": "string"}, "name": {"type": "string"}},
            respond=lambda kwargs: (
                "Launched.",
                {"pid": SHARED_APPS["launch_pid"], "bundle_id": kwargs.get("bundle_id") or ""},
            ),
        ),
        "bring_to_front": FakeTool("bring_to_front", args={"pid": {"type": "integer"}}),
        "hotkey": FakeTool(
            "hotkey",
            args={
                "keys": {"type": "array"},
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "delivery_mode": {"type": "string"},
            },
        ),
        "type_text": FakeTool(
            "type_text",
            args={
                "text": {"type": "string"},
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "delivery_mode": {"type": "string"},
            },
        ),
        "set_value": FakeTool(
            "set_value",
            args={
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "element_token": {"type": "string"},
                "value": {"type": "string"},
            },
        ),
        "press_key": FakeTool(
            "press_key",
            args={
                "key": {"type": "string"},
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "delivery_mode": {"type": "string"},
            },
        ),
        "scroll": FakeTool(
            "scroll",
            args={
                "direction": {"type": "string"},
                "amount": {"type": "integer"},
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
            },
        ),
        "click": FakeTool(
            "click",
            args={
                "element_token": {"type": "string"},
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
            },
        ),
    }


#: Mutable per-test world state the standard tools read from.
SHARED_APPS: dict[str, Any] = {"list": [], "windows": {}, "state": {}, "launch_pid": 0}


def reset_world() -> None:
    SHARED_APPS["list"] = []
    SHARED_APPS["windows"] = {}
    SHARED_APPS["state"] = {}
    SHARED_APPS["launch_pid"] = 0

"""WP3: a permissions_pending driver failure must stay actionable.

Live evidence (2026-09-17, bounded daemon): with macOS Accessibility or
Screen Recording still pending, observation calls return text
'Error: permissions_pending: macOS Accessibility or Screen Recording
permission is still pending; no action started, retry after the
permission gate completes'. The gateway must surface that actionable
reason to the user (and the ledger), not flatten it to
'launch_app failed: DesktopDriverError'.
"""

from __future__ import annotations

import asyncio

import pytest

from assistant.runtime.recipe_errors import RecipeFailure
from assistant.runtime.recipe_executor import RecipeExecutor
from assistant.tools.result_normalizer import ToolOutcome

_PERMISSIONS_PENDING_TEXT = (
    "Error: permissions_pending: macOS Accessibility or Screen Recording "
    "permission is still pending; no action started, retry after the "
    "permission gate completes"
)


class _PendingDriverTool:
    """Mimics the real daemon chain: start_session raises DesktopDriverError.

    Production path (live log 2026-09-17): DesktopRun.ensure_started ->
    start_session fails with the pending-permission text; the executor's
    generic handler converted that to 'launch_app failed: DesktopDriverError',
    discarding the actionable reason.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.args: dict[str, object] = {}

    def get_input_schema(self) -> type:
        from pydantic import BaseModel, ConfigDict

        class S(BaseModel):
            model_config = ConfigDict(extra="forbid")
            bundle_id: str = ""
            session: str = ""

        return S

    async def ainvoke(self, arguments: object) -> ToolOutcome:
        await asyncio.sleep(0)
        return ToolOutcome("failed", "unverifiable", text=_PERMISSIONS_PENDING_TEXT)


class _DesktopDriverError(RuntimeError):
    """Mirror of assistant.runtime.session.DesktopDriverError."""


def _executor_with(tools: dict[str, object]) -> RecipeExecutor:
    return RecipeExecutor(cua_tools_by_name=tools, budget=None)  # type: ignore[arg-type]


async def test_permissions_pending_failure_names_the_permission_gate() -> None:
    from pydantic import BaseModel, ConfigDict

    class LaunchSchema(BaseModel):
        model_config = ConfigDict(extra="forbid")
        bundle_id: str = ""

    class SessionStartDenied:
        """DesktopRun.action() -> ensure_started raises before dispatch."""

        name = "launch_app"
        args = {"bundle_id": {"type": "string"}}

        def get_input_schema(self) -> type[BaseModel]:
            return LaunchSchema

        async def ainvoke(self, arguments: object) -> ToolOutcome:
            raise _DesktopDriverError(
                "start_session failed: Error: permissions_pending: macOS "
                "Accessibility or Screen Recording permission is still pending; "
                "no action started, retry after the permission gate completes"
            )

    executor = _executor_with({"launch_app": SessionStartDenied()})
    with pytest.raises(RecipeFailure) as excinfo:
        await executor.launch_app("chrome")
    message = str(excinfo.value)
    assert "permissions_pending" in message, (
        f"actionable permission detail was lost: {message!r}"
    )
    assert "Accessibility" in message or "Screen Recording" in message


async def test_recipe_failure_renders_permission_guidance_to_the_user() -> None:
    from assistant.runtime.recipe_result import render_result

    result = {
        "recipe_id": "open_app.v1",
        "ok": False,
        "reason": "launch_app failed: RecipeFailure('launch_app failed: "
        "permissions_pending: macOS Accessibility or Screen Recording "
        "permission is still pending; no action started, retry after the "
        "permission gate completes')",
        "app_id": "chrome",
        "query": "",
    }
    text = render_result(result)
    assert "could not verify" in text
    assert "permission" in text.lower(), (
        f"user-facing text lost the permission guidance: {text!r}"
    )

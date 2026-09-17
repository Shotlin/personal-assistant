"""Model-facing conversion of normalized MCP outcomes (WP2/WP3 boundary).

The raw caller returns ToolOutcome objects; the model-visible wrapper must
render bounded text (never a dataclass repr, never base64) while keeping
image payloads out of the prompt.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from assistant.tools.policy import apply_tool_policy
from assistant.tools.result_normalizer import ImageRef, ToolOutcome


class _EmptyArgs(BaseModel):
    pass


def _outcome_tool(outcome: ToolOutcome) -> StructuredTool:
    async def call(**kwargs: object) -> ToolOutcome:
        return outcome

    return StructuredTool(
        name="get_window_state", description="fake", args_schema=_EmptyArgs, coroutine=call
    )


async def test_tool_outcome_becomes_bounded_text_without_images() -> None:
    outcome = ToolOutcome(
        status="ok",
        effect="confirmed",
        text="Display is 42",
        structured={"display_value": "42"},
        images=[ImageRef(data_base64="AAAA")],
    )
    wrapped, _ = apply_tool_policy([_outcome_tool(outcome)])
    result = await wrapped[0].ainvoke({})
    assert isinstance(result, str)
    assert "Display is 42" in result
    assert "1 screenshot(s) retained locally" in result
    assert "42" in result  # structured evidence may inform text, not raw dumps
    assert "ImageRef" not in result and "ToolOutcome" not in result
    assert "data_base64" not in result


async def test_failed_outcome_reports_error_not_crash() -> None:
    outcome = ToolOutcome(status="failed", effect="unverifiable", text="element not found")
    wrapped, _ = apply_tool_policy([_outcome_tool(outcome)])
    result = await wrapped[0].ainvoke({})
    # A failed outcome renders its message as plain agent-visible text; the
    # contract is bounded text, not an exception and not a dataclass repr.
    assert result == "element not found"

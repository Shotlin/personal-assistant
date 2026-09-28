"""Structured mission submissions through the real Deep graph (R02, F01).

The Controller's PLAN/RECOVER/REVIEW roles require the model to answer
through a SCHEMA-BOUND submission tool — never through parsed free text:

- ``submit_mission_plan`` / ``submit_recovery_decision`` /
  ``submit_final_review`` are trusted tools bound into the existing graph
  when missions are enabled; each validates the ACTIVE role (invocation
  local, never shared state) and captures its structured payload;
- the production transport sets a fresh capture per invocation, runs the
  graph, and reads the capture back. A response without a valid submission
  is a rejection (one bounded repair is the caller's decision) — there is
  no raw-text fallback.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

from langchain_core.tools import StructuredTool

from assistant.missions.contracts import FinalReview, PlanProposal, RecoveryDecision
from assistant.tools.policy import controller_role_var

#: Invocation-local capture. The transport sets a fresh dict per call; the
#: submission tool writes into it; the transport reads and clears it.
submission_capture: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mission_submission_capture", default=None
)

#: role -> (tool name, validator model)
SUBMISSION_ROLES: dict[str, tuple[str, Any]] = {
    "PLAN": ("submit_mission_plan", PlanProposal),
    "RECOVER": ("submit_recovery_decision", RecoveryDecision),
    "REVIEW": ("submit_final_review", FinalReview),
}


def _submission_tool(role: str, tool_name: str, model: Any) -> StructuredTool:
    async def submit(payload: str) -> str:
        """Capture one structured submission for the active invocation."""
        active = controller_role_var.get()
        if active != role:
            return (
                f"Refused: the {tool_name} submission is only valid in the "
                f"{role} role (active role: {active or 'none'})."
            )
        capture = submission_capture.get()
        if capture is None:
            return "Refused: no mission submission invocation is active."
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            return f"Refused: payload is not valid JSON: {exc}"
        capture[role] = parsed
        return "Submitted."

    return StructuredTool.from_function(
        coroutine=submit,
        name=tool_name,
        description=f"Submit the structured {role} result for this mission step.",
    )


def build_submission_tools() -> list[StructuredTool]:
    """The three trusted submission tools, bound into the graph once."""
    return [
        _submission_tool(role, tool_name, model)
        for role, (tool_name, model) in SUBMISSION_ROLES.items()
    ]


__all__ = [
    "SUBMISSION_ROLES",
    "build_submission_tools",
    "submission_capture",
]

"""The Deep Controller: the existing Deep Agent in mission roles.

One graph, one provider, no second framework (file 03 §5). The Controller
wraps the SAME Deep Agent entry the shell already runs and constrains it by
role for the duration of one invocation:

- PLAN reads bounded context and proposes a :class:`PlanProposal` through a
  structured submission tool; it never sees raw desktop mutation tools;
- RECOVER answers an :class:`ExceptionPacket` with a :class:`RecoveryDecision`;
- REVIEW summarizes evidence into a :class:`FinalReview` that CANNOT set a
  terminal status -- the deterministic acceptance gate owns that;
- CHAT is the plain conversation path.

Role and authority travel in invocation-local runtime context, never by
mutating a shared tool list, so concurrent roles cannot leak into each
other. A raw CUA tool invoked from a Controller role raises even if a
reference to it was injected: denial is enforced at the tool boundary.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from assistant.missions.contracts import (
    ExceptionPacket,
    FinalReview,
    MissionSnapshot,
    PlanProposal,
    RecoveryDecision,
    RequestEnvelope,
)
from assistant.tools.policy import controller_role_var

logger = logging.getLogger("assistant.missions.controller")

ControllerRole = Literal["PLAN", "RECOVER", "REVIEW", "CHAT"]

class ControllerRoleViolation(RuntimeError):
    """A raw desktop tool was invoked from a Controller role."""


from contextlib import contextmanager  # noqa: E402


@contextmanager
def controller_role(role: str) -> Any:
    """Bind the active Controller role around one Deep invocation.

    The role lives in the policy module's contextvar so the REAL wrapped
    tool dispatch refuses desktop tools for Controller roles (F02/RP02).
    Invocation-local, never shared state.
    """
    token = controller_role_var.set(role)
    try:
        yield
    finally:
        controller_role_var.reset(token)


@dataclass
class ControllerContext:
    """Bounded context one Controller invocation may see."""

    role: ControllerRole
    scope_summary: str = ""
    remaining_budget: dict[str, Any] = field(default_factory=dict)
    context_text: str = ""  # <= 8 KiB, redacted
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ControllerResult:
    """What one Controller invocation produced, plus its cost accounting."""

    kind: str
    payload: Any
    deep_calls_used: int = 0
    role: ControllerRole = "CHAT"


class DeepController:
    """Role-scoped mission reasoning over the existing Deep Agent entry."""

    def __init__(self, deep_entry: Any, *, max_context_bytes: int = 8192) -> None:
        self._deep = deep_entry
        self._max_context = max_context_bytes

    # -- role enforcement ---------------------------------------------------------

    async def invoke_role_tool(self, tool_name: str, run_tool: Callable[..., Awaitable[Any]],
         **kwargs: Any) -> Any:
        """Run one tool under the active role's authority.

        The trusted submission tools route through here; anything that looks
        like a desktop mutation is refused before dispatch. Model binding may
        filter tools, but THIS check is what a forged reference hits.
        """
        role = controller_role_var.get()
        if role in {"PLAN", "RECOVER", "REVIEW"} and tool_name in _MUTATION_TOOLS:
            raise ControllerRoleViolation(
                f"role {role} may not invoke the desktop tool {tool_name!r}"
            )
        return await run_tool(**kwargs)

    # -- planning -------------------------------------------------------------------

    async def plan(
        self,
        request: RequestEnvelope,
        context: ControllerContext,
        *,
        invoke: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> ControllerResult:
        """One planning call. ``invoke`` is the injected Deep transport.

        In production the service injects the Deep Agent invocation; in
        tests it injects a scripted responder. Either way the role context
        and the structured-submission schema are the same.
        """
        prompt = self._plan_prompt(request, context)
        response = await self._call("PLAN", prompt, invoke)
        proposal = _validate_submission(response, PlanProposal, "plan")
        return ControllerResult(kind="plan", payload=proposal, deep_calls_used=1, role="PLAN")

    async def recover(
        self,
        mission: MissionSnapshot,
        exception: ExceptionPacket,
        *,
        invoke: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> ControllerResult:
        prompt = self._recover_prompt(mission, exception)
        response = await self._call("RECOVER", prompt, invoke)
        decision = _validate_submission(response, RecoveryDecision, "recovery")
        return ControllerResult(
            kind="recovery", payload=decision, deep_calls_used=1, role="RECOVER"
        )

    async def review(
        self,
        mission: MissionSnapshot,
        checks: list[Any],
        *,
        invoke: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> ControllerResult:
        prompt = self._review_prompt(mission, checks)
        response = await self._call("REVIEW", prompt, invoke)
        review = _validate_submission(response, FinalReview, "review")
        return ControllerResult(kind="review", payload=review, deep_calls_used=1, role="REVIEW")

    # -- chat passthrough ---------------------------------------------------------------

    async def chat(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Any,
        cancel_check: Any,
    ) -> dict[str, Any]:
        """The plain conversation path: the Deep Agent exactly as the shell runs it."""
        token = controller_role_var.set("CHAT")
        try:
            return await self._deep.run(
                text, thread_id=thread_id, on_event=on_event, cancel_check=cancel_check
            )
        finally:
            controller_role_var.reset(token)

    # -- prompts and transport -----------------------------------------------------------

    async def _call(
        self,
        role: ControllerRole,
        prompt: str,
        invoke: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None,
    ) -> dict[str, Any]:
        token = controller_role_var.set(role)
        try:
            if invoke is None:
                raise RuntimeError("no Deep transport is wired into the controller")
            from assistant.missions.submission import SUBMISSION_ROLES

            tool = SUBMISSION_ROLES[role][0]
            response = await invoke(role, tool, {"prompt": prompt})
            return response
        finally:
            controller_role_var.reset(token)

    def _plan_prompt(self, request: RequestEnvelope, context: ControllerContext) -> str:
        bounded = context.context_text[: self._max_context]
        return json.dumps(
            {
                "task": "plan",
                "goal": request.text[:2000],
                "scope_summary": context.scope_summary[:512],
                "budget_remaining": context.remaining_budget,
                "context": bounded,
                "schema": "PlanProposal",
            },
            ensure_ascii=False,
        )

    def _recover_prompt(self, mission: MissionSnapshot, exception: ExceptionPacket) -> str:
        return json.dumps(
            {
                "task": "recover",
                "mission_status": mission.status,
                "exception": exception.model_dump(),
            },
            ensure_ascii=False,
        )

    def _review_prompt(self, mission: MissionSnapshot, checks: list[Any]) -> str:
        return json.dumps(
            {
                "task": "review",
                "mission_status": mission.status,
                "checks": [c if isinstance(c, dict) else c.model_dump() for c in checks],
            },
            ensure_ascii=False,
        )


#: Tools a Controller role may never dispatch, by name (the allowlist in
#: tools/policy.py is the authoritative inventory; this is the role gate).
_MUTATION_TOOLS = frozenset(
    {
        "click", "double_click", "type_text", "scroll", "press_key", "hotkey",
        "set_value", "launch_app", "bring_to_front", "execute_javascript",
    }
)


def _validate_submission(response: dict[str, Any], model: Any, kind: str) -> Any:
    """Schema-validate a structured submission; truncation is a rejection."""
    try:
        return model.model_validate(response.get(kind) or response)
    except Exception as exc:  # noqa: BLE001 - one bounded repair is the caller's call
        raise ValueError(f"invalid {kind} submission: {exc}") from exc


__all__ = [
    "ControllerContext",
    "ControllerResult",
    "ControllerRole",
    "ControllerRoleViolation",
    "DeepController",
    "controller_role",
]

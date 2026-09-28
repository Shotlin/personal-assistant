"""DeepController unit tests (T06, file 06 U4): roles, submissions, bounds."""

from __future__ import annotations

import json
from typing import Any

import pytest

from assistant.missions.contracts import (
    BudgetLimits,
    ExceptionPacket,
    PlanProposal,
    RequestEnvelope,
    Scope,
    StepSpec,
    new_id,
)
from assistant.missions.controller import (
    ControllerContext,
    DeepController,
    controller_role,
)


def _request() -> RequestEnvelope:
    import time

    return RequestEnvelope(
        request_id=new_id(),
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture goal",
        submitted_at_ms=int(time.time() * 1000),
    )


def _step(step_id: str = "s1") -> StepSpec:
    return StepSpec(
        step_id=step_id,
        ordinal=1,
        objective="fixture",
        recipe_id="open_app",
        scope=Scope(owner_id="owner"),
        budget=BudgetLimits(),
    )


async def test_plan_invokes_deep_with_plan_role_and_validates() -> None:
    controller = DeepController(None)

    async def invoke(role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert role == "PLAN"
        assert tool == "submit_mission_plan"
        body = json.loads(payload["prompt"])
        assert body["task"] == "plan"
        assert body["goal"] == "fixture goal"
        return {
            "plan": {
                "steps": [_step().model_dump()],
                "explanation": "one local command",
            }
        }

    result = await controller.plan(_request(), ControllerContext(role="PLAN"), invoke=invoke)
    proposal: PlanProposal = result.payload
    assert result.deep_calls_used == 1
    assert proposal.steps[0].step_id == "s1"


async def test_truncated_plan_is_rejected_not_executed() -> None:
    """Output truncation becomes a rejection; one bounded repair is allowed."""
    controller = DeepController(None)

    async def truncated(role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"plan": {"steps": "not-a-list"}}

    with pytest.raises(ValueError):
        await controller.plan(_request(), ControllerContext(role="PLAN"), invoke=truncated)


async def test_recovery_decision_schema() -> None:
    controller = DeepController(None)
    from assistant.missions.contracts import MissionSnapshot

    snapshot = MissionSnapshot(
        mission_id=new_id(), status="RUNNING", plan_version=1, control_epoch=1,
        original_goal="fixture",
    )
    packet = ExceptionPacket(
        mission_id=snapshot.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=new_id(), attempt=1, category="NO_PROGRESS",
    )

    async def invoke(role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert role == "RECOVER"
        return {"recovery": {"decision": "ASK_OWNER", "reason": "ambiguous screen"}}

    result = await controller.recover(snapshot, packet, invoke=invoke)
    assert result.payload.decision == "ASK_OWNER"


async def test_review_cannot_set_terminal_status() -> None:
    """The FinalReview carries a summary only; no status field exists."""
    controller = DeepController(None)
    from assistant.missions.contracts import FinalReview, MissionSnapshot

    snapshot = MissionSnapshot(
        mission_id=new_id(), status="VERIFYING", plan_version=1, control_epoch=1,
        original_goal="fixture",
    )

    async def invoke(role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"review": {"supported_summary": "looks done"}}

    result = await controller.review(snapshot, [], invoke=invoke)
    review: FinalReview = result.payload
    assert review.supported_summary == "looks done"
    assert not hasattr(review, "status"), "reviews must not carry terminal authority"
    assert not hasattr(review, "set_status")


async def test_role_context_is_invocation_local() -> None:
    """Roles cannot leak between concurrent invocations."""
    controller = DeepController(None)
    seen: list[str] = []

    async def invoke(role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        from assistant.tools.policy import controller_role_var as _controller_role

        seen.append(_controller_role.get())
        return {"plan": {"steps": []}}

    import asyncio

    async def one() -> None:
        await controller.plan(
            _request(), ControllerContext(role="PLAN"), invoke=invoke
        )

    await asyncio.gather(one(), one())
    assert seen == ["PLAN", "PLAN"]
    from assistant.tools.policy import controller_role_var as _controller_role

    assert _controller_role.get() == "", "the role context resets after the call"


async def test_mutation_tool_allowed_in_chat_role_only() -> None:
    controller = DeepController(None)

    async def runner(**kwargs: Any) -> str:
        return "ok"

    with controller_role("CHAT"):
        assert await controller.invoke_role_tool("click", runner, pid=1) == "ok"

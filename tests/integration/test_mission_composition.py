"""Composed mission tests (R02+): production boundaries, not test doubles.

The twelve review probes become regressions HERE — through the real
resource graph, the real policy-wrapped tools, the real Deep graph (with a
scripted model at the provider boundary), and the real store. Doubles
replace external devices only, never the guard/composition layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from assistant.agent.build import build_agent
from assistant.missions.contracts import (
    BudgetLimits,
    Scope,
    StepSpec,
)
from assistant.missions.controller import DeepController
from assistant.missions.submission import build_submission_tools
from assistant.settings import Settings
from assistant.tools.policy import apply_tool_policy, controller_role_var
from tests.helpers.scripted_model import ScriptedChatModel

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "sani-data"),
        cua_enabled=False,
        openrouter_api_key="fixture-not-a-secret",
        jarvis_missions_enabled=True,
        model_provider="openrouter",
        model_name="fixture/model",
        agent_gateway_api_key="fixture-key",
    )


async def _returns(value: Any) -> Any:
    return value


async def _noop_event(kind: str, data: dict[str, Any]) -> None:
    return None


# -- RP01: the production planning adapter accepts a real structured plan -----


async def test_rp01_production_planning_adapter(tmp_path: Path) -> None:
    """RP01 regression: a Deep response containing a valid plan — delivered
    through the real graph and the real submission tool — validates."""
    settings = _settings(tmp_path)
    from assistant.memory.local import open_local_memory_resources

    async with open_local_memory_resources(settings.sani_db_path) as mem:
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "ordinal": 1,
                    "objective": "open safari",
                    "recipe_id": "open_app",
                    "recipe_args": {"app_name": "Safari"},
                    "scope": {"owner_id": "sani-local"},
                    "budget": {"max_wall_ms": 90000, "max_actions": 12},
                    "effect_class": "REPEATABLE_LOCAL",
                }
            ],
            "explanation": "one local command",
        }
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "submit_mission_plan",
                            "args": {"payload": json.dumps(plan)},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage("submitted"),
            ]
        )
        bundle = build_agent(
            model=model,
            checkpointer=mem.saver,
            store=mem.store,
            skills_root=SKILLS_ROOT,
            extra_tools=build_submission_tools(),
        )
        from assistant.core.agents import DeepAgentEntry, RuntimeProvider, _DeepInvoke

        deep = DeepAgentEntry(
            settings,
            provider=RuntimeProvider(settings, agent_builder=lambda: _returns(bundle.agent)),
        )
        transport = _DeepInvoke(deep, thread_id="conv-rp01")
        controller = DeepController(deep)
        import time

        from assistant.missions.contracts import RequestEnvelope
        from assistant.missions.controller import ControllerContext

        request = RequestEnvelope(
            request_id="req-rp01",
            conversation_id="conv-rp01",
            owner_id="sani-local",
            input_origin="typed_final",
            input_revision=1,
            text="open safari",
            submitted_at_ms=int(time.time() * 1000),
        )
        result = await controller.plan(
            request, ControllerContext(role="PLAN"), invoke=transport
        )
        assert result.payload.steps[0].step_id == "s1"
        assert result.deep_calls_used == 1


# -- RP02: PLAN role cannot dispatch through the REAL wrapped tool -------------


async def test_rp02_role_refusal_at_real_policy_boundary() -> None:
    """RP02 regression: within controller_role('PLAN') the real wrapped click
    refuses and the effect sink stays empty."""
    from tests.integration.test_mission_policy import _DriverWorld

    world = _DriverWorld()
    wrapped, _ = apply_tool_policy(world.tools())
    token = controller_role_var.set("PLAN")
    try:
        result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771, "element_token": "t1"})
    finally:
        controller_role_var.reset(token)
    assert "Refused" in result
    assert world.sink.count == 0, "a PLAN-role mutation must never dispatch"


async def test_chat_role_still_dispatches() -> None:
    """Legacy chat behavior is preserved: an empty/CHAT role acts as before."""
    from tests.integration.test_mission_policy import _DriverWorld

    world = _DriverWorld()
    wrapped, _ = apply_tool_policy(world.tools())
    result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771, "element_token": "t1"})
    assert result == "clicked"
    assert world.sink.count == 1


async def test_info_role_blocks_desktop_acquisition() -> None:
    """Information requests must not probe the desktop: the INFO role refuses
    even read-only observation tools."""
    from tests.integration.test_mission_policy import _DriverWorld

    world = _DriverWorld()
    wrapped, _ = apply_tool_policy(world.tools())
    token = controller_role_var.set("INFO")
    try:
        result = await wrapped[2].ainvoke({"pid": 4101, "window_id": 771})
    finally:
        controller_role_var.reset(token)
    assert "Refused" in result
    assert world.sink.count == 0


# -- submission tool role gating -----------------------------------------------


async def test_submission_tools_reject_wrong_role() -> None:
    tools = build_submission_tools()
    plan_tool = next(t for t in tools if t.name == "submit_mission_plan")
    result = await plan_tool.ainvoke({"payload": "{}"})
    assert "Refused" in result, "a submission outside its role must refuse"


async def test_submission_tools_accept_matching_role_with_capture() -> None:
    from assistant.missions.submission import submission_capture

    tools = build_submission_tools()
    plan_tool = next(t for t in tools if t.name == "submit_mission_plan")
    capture: dict[str, Any] = {}
    token = submission_capture.set(capture)
    role_token = controller_role_var.set("PLAN")
    try:
        plan = {"steps": [], "explanation": "empty but valid"}
        result = await plan_tool.ainvoke({"payload": json.dumps(plan)})
    finally:
        submission_capture.reset(token)
        controller_role_var.reset(role_token)
    assert result == "Submitted."
    assert capture["PLAN"]["explanation"] == "empty but valid"


# -- step spec and helper sanity for later repairs ------------------------------


def _step_spec() -> StepSpec:
    return StepSpec(
        step_id="s1",
        ordinal=1,
        objective="fixture",
        recipe_id="open_app",
        scope=Scope(owner_id="owner"),
        budget=BudgetLimits(),
    )


# -- R02/F08: one shared service lifetime; durable authority context -----------


async def test_core_resources_share_one_service(tmp_path: Path) -> None:
    """The registry entry and mission IPC share ONE service/store/authority."""
    from assistant.core.agents import build_core_resources

    settings = _settings(tmp_path)
    resources = build_core_resources(settings)
    service_a = await resources.mission_service()
    service_b = await resources.mission_service()
    assert service_a is service_b, "the resource graph must not duplicate services"
    await resources.aclose()


async def test_deep_selected_actions_route_through_mission_authority(
    tmp_path: Path,
) -> None:
    """NP11 regression (C02/N03): the PRODUCTION selectable Deep entry with a
    scripted model that actually ATTEMPTS the mutation — a real type_text
    tool call through the real wrapped policy — must produce zero effects.
    No guard is injected manually: the production run_scope installs the
    no-mission refusal guard whenever missions are enabled, because a
    selectable entry outside a mission owns no permit to spend."""
    from assistant.memory.local import open_local_memory_resources
    from tests.integration.test_mission_policy import _DriverWorld

    world = _DriverWorld()
    settings = _settings(tmp_path)
    assert settings.jarvis_missions_enabled
    async with open_local_memory_resources(settings.sani_db_path) as mem:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "type_text",
                            "args": {"pid": 4101, "window_id": 771, "text": "fixture only"},
                            "id": "fixture-type",
                        }
                    ],
                ),
                AIMessage("finished"),
            ]
        )
        bundle = build_agent(
            model=model,
            checkpointer=mem.saver,
            store=mem.store,
            skills_root=SKILLS_ROOT,
            extra_tools=apply_tool_policy(world.tools())[0],
        )
        deep = await _graph_entry(bundle.agent, settings)
        await deep.run(
            "click fixture",
            thread_id="conv-bypass",
            on_event=_noop_event,
            cancel_check=lambda: False,
        )
    assert world.sink.count == 0, (
        "a Deep-selected action outside any mission must not dispatch"
    )
    assert model.call_index >= 1, "the scripted model must have actually attempted the action"


async def _graph_entry(agent: Any, settings: Settings) -> Any:
    from assistant.core.agents import DeepAgentEntry, RuntimeProvider

    return DeepAgentEntry(
        settings, provider=RuntimeProvider(settings, agent_builder=lambda: _returns(agent))
    )



def _stub_item() -> Any:
    from assistant.missions.contracts import ActionScopeRecord, BoundedWorkItem

    scope = Scope(owner_id="owner")
    return BoundedWorkItem(
        mission_id="stub",
        plan_version=1,
        control_epoch=1,
        step_id="s1",
        execution_id="stub-exec",
        attempt=1,
        objective="stub",
        expected_scope=scope,
        allowed_action_scope=ActionScopeRecord(
            tool_ids=["click"], target_scope_hash=scope.scope_hash
        ),
        recipe_id="semantic_ui",
        deadline_at_ms=9_999_999_999_999,
        budget=BudgetLimits(),
    )


def _observed() -> Any:
    import time as time_module

    from assistant.missions.contracts import ScopeObservation

    return ScopeObservation(
        app_bundle="com.fixture.browser",
        pid=4101,
        window_id=771,
        captured_at_ms=int(time_module.time() * 1000),
    )


def _intent(kwargs: dict[str, Any], tool: str = "click") -> Any:
    from assistant.missions.contracts import ActionIntent

    return ActionIntent(tool=tool, args=dict(kwargs))


# -- C02/N04 (NP01): the production executor carries its real dependencies ----


async def test_np01_production_executor_dependencies(tmp_path: Path) -> None:
    """NP01 regression: build_core_resources -> service -> the REAL executor
    must hold its store and payload resolver, not None."""
    from assistant.core.agents import build_core_resources

    resources = build_core_resources(_settings(tmp_path))
    try:
        service = await resources.mission_service()
        assert service._executor._store is not None, (
            "the production executor must be bound to the shared mission store"
        )
        assert service._executor._payload_resolver is not None, (
            "the production executor must be bound to a payload resolver"
        )
        assert service._executor._store is resources._store
    finally:
        await resources.aclose()


async def test_np07_discovery_bootstrap(tmp_path: Path) -> None:
    """NP07 regression: the first inventory read of a REPEATABLE_LOCAL step
    is bounded discovery, not SCOPE_MISMATCH; a mutating call with no
    observation still refuses."""
    import time as time_module

    from assistant.missions.authority import MissionAuthority
    from assistant.missions.contracts import (
        BudgetLimits,
        CancellationToken,
        RequestEnvelope,
        Scope,
        StepSpec,
        new_id,
    )
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.store import MissionStore

    store = await MissionStore.connect(tmp_path / "np07.db")
    try:
        await store.setup()
        scope = Scope(
            owner_id="owner",
            allowed_apps=["com.fixture.allowed"],
            permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"},
        )
        request = RequestEnvelope(
            request_id=new_id(),
            conversation_id="np07",
            owner_id="owner",
            input_origin="typed_final",
            input_revision=1,
            text="fixture",
            submitted_at_ms=int(time_module.time() * 1000),
        )
        mission = await store.claim_request(
            request, "d" * 64, goal="fixture", scope=scope, limits=BudgetLimits()
        )
        mission = await store.commit_plan(
            mission.mission_id,
            0,
            [
                StepSpec(
                    step_id="s1",
                    ordinal=1,
                    objective="fixture",
                    recipe_id="semantic_ui",
                    scope=scope,
                    budget=BudgetLimits(),
                    effect_class="REPEATABLE_LOCAL",
                )
            ],
            [],
        )
        item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps", "click"])
        assert item is not None

        async def no_runtime() -> None:
            return None

        executor = VeloExecutor(
            Settings(cua_enabled=False),
            get_runtime=no_runtime,
            authority=MissionAuthority(store),
            store=store,
        )
        # First discovery read: permitted (bounded bootstrap authority).
        refusal = await executor._guard_dispatch(
            item, "list_apps", {}, CancellationToken()
        )
        assert refusal is None, f"discovery must bootstrap, refused with: {refusal}"
        # A mutation with no fresh observation still refuses (fail closed).
        denial = await executor._guard_dispatch(
            item, "click", {"pid": 4101, "window_id": 771}, CancellationToken()
        )
        assert denial is not None and "Refused" in denial
        assert "SCOPE_MISMATCH: effect READ_ONLY" not in denial
    finally:
        await store.close()


def test_scope_for_uses_trusted_settings(tmp_path: Path) -> None:
    """C02/N04: the mission scope derives from the trusted settings field —
    not from a nonexistent attribute, and never from model output."""
    from assistant.core.agents import build_core_resources

    settings = _settings(tmp_path)
    settings.mission_allowed_apps = ["com.fixture.allowed"]
    resources = build_core_resources(settings)
    import asyncio
    import time

    from assistant.missions.contracts import RequestEnvelope

    async def check() -> None:
        service = await resources.mission_service()
        scope = service._scope_for(
            RequestEnvelope(
                request_id="r",
                conversation_id="c",
                owner_id="sani-local",
                input_origin="typed_final",
                input_revision=1,
                text="fixture",
                submitted_at_ms=int(time.time() * 1000),
            )
        )
        assert scope.allowed_apps == ["com.fixture.allowed"]
        await resources.aclose()

    asyncio.run(check())

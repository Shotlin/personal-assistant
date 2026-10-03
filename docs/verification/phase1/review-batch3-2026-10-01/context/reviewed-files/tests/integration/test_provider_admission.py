"""Provider request admission at the real transport boundary (D11).

These tests use LangChain's actual model/callback machinery (BaseChatModel
subclass, callback config, streaming) with a local fake client — the same
path the independent reviewer proved failed open. Admission must refuse
BEFORE the provider is invoked, fail closed on accounting errors, isolate
invocation scopes, and never touch the outer deep_calls charge.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk

from assistant.missions.contracts import BudgetLimits, RequestEnvelope, Scope, new_id
from assistant.missions.store import MissionStore
from assistant.models.admission import (
    ProviderAdmissionController,
    ProviderRequestDenied,
    wrap_chat_model,
)


class _CountingFake(FakeListChatModel):
    """The real fake-chat machinery plus an invocation counter."""

    _calls: int = 0

    def _call(self, messages: list[BaseMessage], stop: Any = None,
              run_manager: Any = None, **kwargs: Any) -> str:  # type: ignore[override]
        self._calls += 1
        return str(super()._call(messages, stop=stop, run_manager=run_manager, **kwargs))


class _StreamingFake(SimpleChatModel):
    """A local streaming fake: three chunks per request."""

    _calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fixture-stream"

    def _call(self, messages: list[BaseMessage], stop: Any = None,
              run_manager: Any = None, **kwargs: Any) -> str:
        self._calls += 1
        return "streamed"

    def _stream(self, messages: list[BaseMessage], stop: Any = None,
                run_manager: Any = None, **kwargs: Any):  # type: ignore[override]
        self._calls += 1
        for piece in ("a", "b", "c"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))


class _FailingThenOkFake(FakeListChatModel):
    _calls: int = 0

    def _call(self, messages: list[BaseMessage], stop: Any = None,
              run_manager: Any = None, **kwargs: Any) -> str:  # type: ignore[override]
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("fixture transport error")
        return str(super()._call(messages, stop=stop, run_manager=run_manager, **kwargs))


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "admission.db")
    await value.setup()
    yield value
    await value.close()


async def _mission(store: MissionStore, *, max_requests: int) -> Any:
    scope = Scope(owner_id="owner", allowed_apps=[], permitted_effects={"READ_ONLY"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="admission",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=scope,
        limits=BudgetLimits(max_provider_requests=max_requests))
    return mission


def _messages() -> list[BaseMessage]:
    from langchain_core.messages import HumanMessage

    return [HumanMessage(content="fixture")]


def _rows(store: MissionStore, mission_id: str) -> dict[str, int]:
    rows = store._conn.execute(
        "SELECT state, COUNT(*) FROM mission_provider_requests "
        "WHERE mission_id=? GROUP BY state", (mission_id,)).fetchall()
    return {str(state): int(count) for state, count in rows}


async def test_limit_two_blocks_third_request_before_dispatch(
    store: MissionStore,
) -> None:
    """The reviewer's probe: limit 2 must not allow 5 responses. The third
    request is refused BEFORE the client is invoked."""
    mission = await _mission(store, max_requests=2)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _CountingFake(responses=[f"r{i}" for i in range(5)])
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        for _ in range(2):
            await model.ainvoke(_messages())
        with pytest.raises(ProviderRequestDenied):
            await model.ainvoke(_messages())
        with pytest.raises(ProviderRequestDenied):
            await model.ainvoke(_messages())
        with pytest.raises(ProviderRequestDenied):
            await model.ainvoke(_messages())
    finally:
        controller.pop_scope(scope)
    assert inner._calls == 2, "no outgoing request beyond the ceiling"
    assert _rows(store, mission.mission_id) == {
        "COMPLETED": 2, "ATTEMPTED": 3,
    }, "attempted-but-denied requests are durably accounted"


async def test_denial_survives_the_real_callback_manager(store: MissionStore) -> None:
    """The refusal raises through an explicit callback config — it is a
    pre-request denial at the transport, not a swallowed callback event."""
    mission = await _mission(store, max_requests=1)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _CountingFake(responses=["r0", "r1"])
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        await model.ainvoke(_messages(), config={"callbacks": []})
        with pytest.raises(ProviderRequestDenied):
            await model.ainvoke(_messages(), config={"callbacks": []})
    finally:
        controller.pop_scope(scope)
    assert inner._calls == 1


async def test_streaming_requests_are_admitted(store: MissionStore) -> None:
    """Streaming sub-calls pass the same pre-request admission."""
    mission = await _mission(store, max_requests=2)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _StreamingFake()
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        for _ in range(2):
            chunks = [chunk async for chunk in model.astream(_messages())]
            assert "".join(str(c.content) for c in chunks) == "abc"
        with pytest.raises(ProviderRequestDenied):
            async for _chunk in model.astream(_messages()):
                pass
    finally:
        controller.pop_scope(scope)
    assert inner._calls == 2


async def test_storage_failure_fails_closed(store: MissionStore) -> None:
    """Unavailable durable accounting denies the request — the provider is
    never invoked un-accounted."""
    mission = await _mission(store, max_requests=8)
    controller = ProviderAdmissionController()
    controller.bind_store(store)

    class _BrokenStore:
        def admit_provider_request_sync(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("storage unavailable")

        def settle_provider_request_sync(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("storage unavailable")

        async def get_mission(self, mission_id: str) -> Any:
            return await store.get_mission(mission_id)

    controller.bind_store(_BrokenStore())
    inner = _CountingFake(responses=["r0", "r1"])
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        with pytest.raises(ProviderRequestDenied):
            await model.ainvoke(_messages())
    finally:
        controller.pop_scope(scope)
    assert inner._calls == 0, "no un-accounted provider request"


async def test_invocation_scopes_are_isolated(store: MissionStore) -> None:
    """The ceiling is a per-INVOCATION reservation: a fresh scope admits
    again while every request stays durably accounted per mission."""
    mission = await _mission(store, max_requests=2)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _CountingFake(responses=[f"r{i}" for i in range(6)])
    model = wrap_chat_model(inner, controller)
    for _round in range(2):
        scope = await controller.open_scope(mission.mission_id, 1, "fixture")
        controller.push_scope(scope)
        try:
            await model.ainvoke(_messages())
            await model.ainvoke(_messages())
            with pytest.raises(ProviderRequestDenied):
                await model.ainvoke(_messages())
        finally:
            controller.pop_scope(scope)
    assert inner._calls == 4
    assert _rows(store, mission.mission_id) == {
        "COMPLETED": 4, "ATTEMPTED": 2,
    }


async def test_failed_requests_recorded_and_still_admitted(store: MissionStore) -> None:
    """A transport error records FAILED; the next attempt is admitted again
    (a failure never bypasses admission, and retries are metered)."""
    mission = await _mission(store, max_requests=8)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _FailingThenOkFake(responses=["recovered"])
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        with pytest.raises(RuntimeError, match="fixture transport error"):
            await model.ainvoke(_messages())
        out = await model.ainvoke(_messages())
        assert out.content == "recovered"
    finally:
        controller.pop_scope(scope)
    assert inner._calls == 2
    assert _rows(store, mission.mission_id) == {"FAILED": 1, "COMPLETED": 1}


async def test_provider_requests_never_double_charge_deep(
    store: MissionStore,
) -> None:
    """Provider-request accounting is transport evidence; the outer
    deep_calls budget is untouched by it."""
    mission = await _mission(store, max_requests=8)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _CountingFake(responses=[f"r{i}" for i in range(3)])
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        for _ in range(3):
            await model.ainvoke(_messages())
    finally:
        controller.pop_scope(scope)
    refreshed = await store.get_mission(mission.mission_id)
    assert refreshed is not None
    assert refreshed.budget_usage.consumed.get("deep_calls", 0) == 0


async def test_ceiling_comes_from_the_mission_limits(store: MissionStore) -> None:
    """No hardcoded completion count: the mission's own documented limit
    governs (1 here), and an unset/unknown allowance denies (fail closed)."""
    mission = await _mission(store, max_requests=1)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _CountingFake(responses=["r0", "r1"])
    model = wrap_chat_model(inner, controller)
    scope = await controller.open_scope(mission.mission_id, 1, "fixture")
    controller.push_scope(scope)
    try:
        await model.ainvoke(_messages())
        with pytest.raises(ProviderRequestDenied):
            await model.ainvoke(_messages())
    finally:
        controller.pop_scope(scope)
    assert inner._calls == 1


async def test_plain_chat_without_scope_passes_through(store: MissionStore) -> None:
    """Non-mission chat has no invocation scope and is not mission-metered."""
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    inner = _CountingFake(responses=["chat"])
    model = wrap_chat_model(inner, controller)
    out = await model.ainvoke(_messages())
    assert out.content == "chat"
    assert inner._calls == 1
    assert store._conn.execute(
        "SELECT COUNT(*) FROM mission_provider_requests").fetchone()[0] == 0

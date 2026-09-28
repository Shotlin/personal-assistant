"""Mission-policy integration (T03, file 06 I1): the guard meets the tools.

Real ``apply_tool_policy``-wrapped tools over scripted driver doubles, with
the mission context vars set exactly as the bounded executor will set them.
Every denial must leave the effect sink untouched.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from assistant.missions.authority import AuthorityDenied, MissionAuthority
from assistant.missions.contracts import (
    ActionIntent,
    ActionScopeRecord,
    Scope,
    ScopeObservation,
    new_id,
)
from assistant.missions.store import MissionStore
from assistant.tools.policy import (
    apply_tool_policy,
    cua_run_scope,
    mission_audit_strict,
    mission_dispatch_guard,
    mission_screenshots_withheld,
)
from tests.helpers.mission_fakes import SECRET_CANARIES, EffectSink


def _now() -> int:
    return int(time.time() * 1000)


class _DriverWorld:
    """Scripted driver effects + the tools the policy wraps."""

    def __init__(self) -> None:
        self.sink = EffectSink()
        self.screenshot_files: list[str] = []

    def tools(self) -> list[StructuredTool]:
        world = self

        async def click(
            pid: int = 0, window_id: int = 0, element_token: str = "", **_: Any
        ) -> str:
            """Scripted click effect."""
            world.sink.perform("click", f"{pid}:{window_id}:{element_token}")
            return "clicked"

        async def type_text(
            pid: int = 0, window_id: int = 0, text: str = "", **_: Any
        ) -> str:
            """Scripted typing effect."""
            world.sink.perform("type_text", f"{pid}:{window_id}", text)
            return "typed"

        async def get_window_state(
            pid: int = 0,
            window_id: int = 0,
            include_screenshot: bool = False,
            screenshot_out_file: str | None = None,
            **_: Any,
        ) -> str:
            """Scripted observation, recording any diverted screenshot file."""
            if screenshot_out_file:
                world.screenshot_files.append(screenshot_out_file)
            return "window read"

        return [
            StructuredTool.from_function(coroutine=click, name="click"),
            StructuredTool.from_function(coroutine=type_text, name="type_text"),
            StructuredTool.from_function(coroutine=get_window_state, name="get_window_state"),
        ]


@pytest.fixture()
async def store(tmp_path: Any) -> Any:
    s = await MissionStore.connect(tmp_path / "policy.db")
    await s.setup()
    yield s
    await s.close()


def _wrap(world: _DriverWorld) -> list[Any]:
    wrapped, _ = apply_tool_policy(world.tools())
    return wrapped


async def test_denied_mutation_leaves_sink_empty(store: MissionStore) -> None:
    world = _DriverWorld()
    wrapped = _wrap(world)
    authority = MissionAuthority(store)

    async def guard(tool: str, kwargs: dict[str, Any]) -> str | None:
        try:
            await authority.authorize(_item(), _intent(kwargs), _observed())
        except AuthorityDenied as exc:
            return f"Refused: {exc.category}: {exc.reason}"
        return None

    token = mission_dispatch_guard.set(guard)
    audit = mission_audit_strict.set(True)
    try:
        result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771, "element_token": "t1"})
    finally:
        mission_dispatch_guard.reset(token)
        mission_audit_strict.reset(audit)
    assert "Refused" in result
    assert world.sink.count == 0


async def test_permitted_mutation_performs_exactly_once(store: MissionStore) -> None:
    world = _DriverWorld()
    wrapped = _wrap(world)
    authority = MissionAuthority(store)
    item = _item()
    _commit_intent(store, item)

    async def guard(tool: str, kwargs: dict[str, Any]) -> str | None:
        try:
            await authority.authorize(item, _intent(kwargs, tool), _observed())
        except AuthorityDenied as exc:
            return f"Refused: {exc.category}: {exc.reason}"
        return None

    token = mission_dispatch_guard.set(guard)
    try:
        result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771, "element_token": "t1"})
    finally:
        mission_dispatch_guard.reset(token)
    assert result == "clicked"
    assert world.sink.count == 1


async def test_audit_failure_blocks_mission_mutation(store: MissionStore) -> None:
    world = _DriverWorld()
    wrapped = _wrap(world)

    class _BrokenLedger:  # type: ignore[type-arg]
        async def plan(self, **_: Any) -> int:
            raise OSError("disk full")

        async def observe(self, *_: Any) -> None:  # pragma: no cover
            return None

    from assistant.tools.policy import cua_action_ledger

    async def _allow(_tool: str, _kwargs: dict[str, Any]) -> str | None:
        return None

    guard_token = mission_dispatch_guard.set(_allow)
    audit_token = mission_audit_strict.set(True)
    ledger_token = cua_action_ledger.set(_BrokenLedger())  # type: ignore[arg-type]
    try:
        result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771, "element_token": "t1"})
    finally:
        mission_dispatch_guard.reset(guard_token)
        mission_audit_strict.reset(audit_token)
        cua_action_ledger.reset(ledger_token)
    assert "Refused" in result and "ledger" in result
    assert world.sink.count == 0


async def test_screenshots_withheld_in_mission_mode(store: MissionStore) -> None:
    world = _DriverWorld()
    wrapped = _wrap(world)
    withhold = mission_screenshots_withheld.set(True)
    try:
        await wrapped[2].ainvoke({"pid": 4101, "window_id": 771, "include_screenshot": True})
    finally:
        mission_screenshots_withheld.reset(withhold)
    assert world.screenshot_files == [], "no raw screenshot file may be written in mission mode"


async def test_secret_in_tool_error_is_redacted(store: MissionStore) -> None:
    secret = SECRET_CANARIES[0]

    async def failing_click(pid: int = 0, window_id: int = 0, **_: Any) -> str:
        """A driver failure that embeds a secret in its error text."""
        raise RuntimeError(f"request failed: bearer {secret}")

    wrapped, _ = apply_tool_policy(
        [StructuredTool.from_function(coroutine=failing_click, name="click")]
    )
    result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771})
    assert secret not in result
    assert "REDACTED" in result


def _item() -> Any:
    from assistant.missions.contracts import BoundedWorkItem, EvidenceRequirements

    scope = Scope(
        owner_id="owner",
        allowed_apps=["com.fixture.browser"],
        permitted_effects={"REPEATABLE_LOCAL"},
    )
    from assistant.missions.contracts import BudgetLimits

    return BoundedWorkItem(
        mission_id=new_id(),
        plan_version=1,
        control_epoch=1,
        step_id="s1",
        execution_id=new_id(),
        attempt=1,
        objective="fixture",
        expected_scope=scope,
        allowed_action_scope=ActionScopeRecord(
            tool_ids=["click", "type_text"],
            target_scope_hash=scope.scope_hash,
            permitted_effects={"REPEATABLE_LOCAL"},
        ),
        recipe_id="semantic_ui",
        deadline_at_ms=_now() + 90_000,
        budget=BudgetLimits(max_wall_ms=90_000),
        effect_class="REPEATABLE_LOCAL",
        evidence_requirements=EvidenceRequirements(),
    )


def _intent(kwargs: dict[str, Any], tool: str = "click") -> ActionIntent:
    return ActionIntent(tool=tool, args=dict(kwargs), effect_class="REPEATABLE_LOCAL")


def _observed() -> ScopeObservation:
    return ScopeObservation(
        app_bundle="com.fixture.browser",
        pid=4101,
        window_id=771,
        captured_at_ms=_now(),
    )


def _commit_intent(store: MissionStore, item: Any) -> None:
    store._conn.execute(
        """
        INSERT INTO mission_attempts (execution_id, mission_id, plan_version,
            step_id, attempt, control_epoch, packet_digest, dispatch_state,
            effect_class, created_at_ms, updated_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, 'd', 'INTENT_COMMITTED', 'REPEATABLE_LOCAL', ?, ?)
        """,
        (
            item.execution_id,
            item.mission_id,
            item.plan_version,
            item.step_id,
            item.attempt,
            item.control_epoch,
            _now(),
            _now(),
        ),
    )


def test_contextvars_default_clean() -> None:
    from assistant.tools.policy import (
        mission_audit_strict as audit,
    )
    from assistant.tools.policy import (
        mission_dispatch_guard as guard,
    )
    from assistant.tools.policy import (
        mission_screenshots_withheld as withheld,
    )

    assert guard.get() is None
    assert audit.get() is False
    assert withheld.get() is False


def test_run_scope_resets_mission_state() -> None:
    """Mission context vars set inside cua_run_scope do not leak out."""

    async def run() -> None:
        from assistant.tools.policy import (
            mission_audit_strict as audit,
        )
        from assistant.tools.policy import (
            mission_dispatch_guard as guard,
        )

        async def _guard(tool: str, kwargs: dict[str, Any]) -> str | None:
            return None

        async with cua_run_scope(budget=None, run=None):
            guard.set(_guard)
            audit.set(True)
        assert guard.get() is None
        assert audit.get() is False

    asyncio.run(run())


# -- R04/RP03: strict mode with NO ledger at all refuses before dispatch -------


async def test_rp03_strict_mode_without_any_ledger_refuses() -> None:
    """RP03 regression: strict mission audit with no bound ledger refuses
    the mutation before dispatch (previously it dispatched)."""
    from assistant.tools.policy import cua_action_ledger

    world = _DriverWorld()
    wrapped = _wrap(world)
    async def _allow(_tool: str, _kwargs: dict[str, Any]) -> str | None:
        return None

    guard_token = mission_dispatch_guard.set(_allow)
    audit_token = mission_audit_strict.set(True)
    ledger_token = cua_action_ledger.set(None)
    try:
        result = await wrapped[0].ainvoke({"pid": 4101, "window_id": 771, "element_token": "t1"})
    finally:
        mission_dispatch_guard.reset(guard_token)
        mission_audit_strict.reset(audit_token)
        cua_action_ledger.reset(ledger_token)
    assert "Refused" in result and "ledger" in result
    assert world.sink.count == 0

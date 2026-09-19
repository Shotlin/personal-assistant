"""WP7 integration: stale evidence cannot validate a recipe effect.

The executor stamps every observation with the current scene epoch and
observed_at; recipes verify effects against a fresh stamp. A cached
observation from an older epoch (or beyond the age bound) must FAIL the
evidence check even when foreground_app matches - an unchanged screenshot
from before a navigation change proves nothing about the current state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from assistant.runtime.recipe_executor import RecipeExecutor
from assistant.runtime.scene import SceneEntry
from assistant.tools.result_normalizer import ToolOutcome


class _FakeTool:
    def __init__(self, name: str, outcome: ToolOutcome) -> None:
        self.name = name
        self._outcome = outcome

    def get_input_schema(self) -> type:
        from pydantic import BaseModel, ConfigDict

        class S(BaseModel):
            model_config = ConfigDict(extra="forbid")
            session: str = ""

        return S

    async def ainvoke(self, arguments: object) -> ToolOutcome:
        return self._outcome


def _executor_with(outcome: ToolOutcome) -> RecipeExecutor:
    fake_tool: Any = _FakeTool("get_desktop_state", outcome)
    return RecipeExecutor(
        cua_tools_by_name={"get_desktop_state": fake_tool},
        action_ledger=None,
        run_id=None,
        budget=None,
        run=None,
    )


def test_scene_entry_carries_master_plan_shape() -> None:
    entry = SceneEntry(
        app_instance="chrome", window_id=1, navigation_epoch=1,
        observed_at=datetime.now(tz=UTC),
        semantic_fields={"foreground_app": "chrome"}, evidence_refs=("t",),
    )
    assert entry.semantic_fields["foreground_app"] == "chrome"


async def test_stale_observation_fails_evidence_check() -> None:
    # An observation older than the freshness bound (observed_at far in the
    # past, stamped by the driver as epoch 1) cannot validate a fresh
    # effect even though foreground_app matches.
    stale = ToolOutcome(
        "ok", "not_applicable",
        structured={
            "foreground_app": "chrome",
            "modal": False,
            "observed_at": "2020-01-01T00:00:00+00:00",
            "navigation_epoch": 1,
        },
    )
    executor = _executor_with(stale)
    outcome = await executor.verify_foreground("chrome")
    from assistant.runtime.recipes import foreground_matches
    assert foreground_matches(outcome, "chrome"), "fixture requires matching identity"
    from assistant.runtime.scene import observation_is_fresh
    assert not observation_is_fresh(outcome), (
        "stale observation must fail the freshness policy"
    )


async def test_fresh_observation_passes_evidence_check() -> None:
    fresh = ToolOutcome(
        "ok", "not_applicable",
        structured={
            "foreground_app": "chrome",
            "modal": False,
            "observed_at": datetime.now(tz=UTC).isoformat(),
            "navigation_epoch": 7,
        },
    )
    from assistant.runtime.scene import observation_is_fresh
    assert observation_is_fresh(fresh)

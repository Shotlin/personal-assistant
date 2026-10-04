"""The shared Sani runtime: proof that the Deep Agent gets a real tool world.

This is the parity guarantee for the sani-core cutover. Before it, the core
Deep Agent was built with no computer-control tools at all, so "Open Chrome"
worked over the legacy gateway and would have silently stopped working after
the cutover. These tests pin the wiring without touching a driver or provider.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from assistant.agent.context import RunBudget
from assistant.core import runtime as runtime_module
from assistant.core.runtime import SaniRuntime
from assistant.settings import Settings
from assistant.tools.policy import cua_desktop_run, cua_run_budget


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = ""


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the heavy edges of SaniRuntime.open and record what it passed on."""
    record: dict[str, Any] = {"build_agent_kwargs": None, "settings": None}

    fake_resources = SimpleNamespace(store="store", saver="saver")

    @contextlib.asynccontextmanager
    async def fake_local_memory(_db_path: str):  # noqa: ANN001
        yield fake_resources

    @contextlib.asynccontextmanager
    async def fake_cua_connection(settings: Settings):  # noqa: ANN001
        yield SimpleNamespace(
            tools=[_Tool("launch_app"), _Tool("observe")],
            tools_by_name={},
            lifecycle_tools_by_name={},
        )

    def fake_build_agent(**kwargs: Any) -> SimpleNamespace:
        record["build_agent_kwargs"] = kwargs
        return SimpleNamespace(agent=SimpleNamespace(name="built-agent"))

    class FakeDesktopSessions:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.opened: list[str] = []
            self.session_ids: list[str | None] = []
            self.closed = False
            record["created_sessions"] = self

        def open(self, name: str, *, session_id: str | None = None) -> Any:
            self.opened.append(name)
            self.session_ids.append(session_id)

            @contextlib.asynccontextmanager
            async def handle():  # noqa: ANN202
                yield SimpleNamespace(cancel=lambda: None)

            return handle()

        async def close_all(self) -> None:
            self.closed = True

    monkeypatch.setattr(runtime_module, "open_local_memory_resources", fake_local_memory)
    monkeypatch.setattr(runtime_module, "build_chat_model", lambda _s: SimpleNamespace())
    monkeypatch.setattr(runtime_module, "open_cua_connection", fake_cua_connection)
    monkeypatch.setattr(runtime_module, "build_agent", fake_build_agent)
    monkeypatch.setattr(runtime_module, "DesktopSessionManager", FakeDesktopSessions)
    return record


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "development",
        "model_provider": "generic_openai_compatible",
        "model_base_url": "http://127.0.0.1:1",
        "model_api_key": "k",
        "model_name": "m",
        "memory_backend": "sqlite",
        "sani_data_dir": "/tmp/sani-runtime-test",
        "cua_enabled": True,
        "cua_capability_manifest_path": "/tmp/sani-manifest.yaml",
        "cua_artifact_dir": "/tmp/sani-artifacts",
    }
    base.update(overrides)
    return Settings(**base)


async def test_runtime_hands_the_cua_inventory_to_the_agent(installed: dict[str, Any]) -> None:
    async with SaniRuntime.open(_settings()) as rt:
        names = sorted(
            getattr(t, "name", "?") for t in installed["build_agent_kwargs"]["extra_tools"]
        )
        # The whole point: the model can see computer-control tools.
        assert names == ["launch_app", "observe"]
        assert rt.cua_enabled is True
        assert rt.agent is not None
        assert installed["build_agent_kwargs"]["checkpointer"] == "saver"
        assert installed["build_agent_kwargs"]["store"] == "store"


async def test_runtime_with_cua_disabled_builds_no_desktop_handle(
    installed: dict[str, Any],
) -> None:
    async with SaniRuntime.open(_settings(cua_enabled=False)) as rt:
        assert installed["build_agent_kwargs"]["extra_tools"] == []
        assert rt.desktop_sessions is None
        async with rt.run_scope("core-test") as budget:
            assert isinstance(budget, RunBudget)
            assert cua_desktop_run.get() is None


async def test_run_scope_binds_the_desktop_run_and_resets_afterwards(
    installed: dict[str, Any],
) -> None:
    async with SaniRuntime.open(_settings()) as rt:
        assert cua_run_budget.get() is None
        async with rt.run_scope("core-42") as budget:
            assert cua_run_budget.get() is budget
            assert cua_desktop_run.get() is not None
            assert installed["created_sessions"].opened == ["core-42"]
            budget.consume("launch_app")
            assert budget.used == 1
        # Never leaks into the next run.
        assert cua_run_budget.get() is None
        assert cua_desktop_run.get() is None
    assert installed["created_sessions"].closed is True


async def test_open_with_agent_injects_without_touching_transport(
    installed: dict[str, Any],
) -> None:
    sentinel = SimpleNamespace(name="fake-agent")
    rt = SaniRuntime.open_with_agent(sentinel, _settings())
    assert rt.agent is sentinel
    assert rt.cua_enabled is False
    assert installed["build_agent_kwargs"] is None


async def test_deep_entry_uses_the_shared_runtime_not_a_private_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry must go through SaniRuntime, so there is one wiring to audit."""
    from assistant.core.agents import DeepAgentEntry

    opened: list[Settings] = []
    sentinel = SimpleNamespace(name="runtime")

    @contextlib.asynccontextmanager
    async def fake_open(settings: Settings):  # noqa: ANN001
        opened.append(settings)
        yield sentinel

    monkeypatch.setattr(
        "assistant.core.agents.SaniRuntime.open",
        fake_open,
    )
    entry = DeepAgentEntry(_settings())
    runtime = await entry._provider.runtime()  # noqa: SLF001 -- contract under test
    assert runtime is sentinel
    assert len(opened) == 1
    # Idempotent: one runtime for the process, not one per turn.
    assert await entry._provider.runtime() is sentinel  # noqa: SLF001
    assert len(opened) == 1


# --------------------------------------------- coding companions bound into the agent


def _zcode_toolkit(settings: Settings) -> Any:
    from assistant.claude_code.toolkit import ClaudeCodeToolkit
    from assistant.coding_agents.zcode import ZCodeBackend

    return ClaudeCodeToolkit(settings, backend=ZCodeBackend(settings.sani_data_dir))


async def test_zcode_cli_mode_binds_the_zcode_tool_and_its_guide(
    installed: dict[str, Any],
) -> None:
    settings = _settings(zcode_cli_enabled=True, zcode_mode="cli")
    async with SaniRuntime.open(settings, zcode=_zcode_toolkit(settings)) as rt:
        kwargs = installed["build_agent_kwargs"]
        names = sorted(t.name for t in kwargs["extra_tools"])
        assert names == ["launch_app", "observe", "zcode"]
        assert "`zcode`" in kwargs["system_prompt"] and "ZCode" in kwargs["system_prompt"]
        assert "zcode" not in rt.cua_tools  # not a computer-control tool
        assert rt.claude_code_enabled and not rt.zcode_app_enabled


async def test_zcode_app_mode_binds_the_helper_tools_and_the_app_guide(
    installed: dict[str, Any],
) -> None:
    settings = _settings(zcode_cli_enabled=True, zcode_mode="app")
    async with SaniRuntime.open(settings, zcode=_zcode_toolkit(settings)) as rt:
        kwargs = installed["build_agent_kwargs"]
        names = sorted(t.name for t in kwargs["extra_tools"])
        assert names == [
            "launch_app",
            "observe",
            "zcode_choice",
            "zcode_save_account",
            "zcode_save_balances",
        ]
        assert "dev.zcode.app" in kwargs["system_prompt"]
        assert set(rt.cua_tools) == {"launch_app", "observe"}
        assert rt.zcode_app_enabled


async def test_zcode_app_mode_needs_computer_control_so_it_binds_nothing_without_it(
    installed: dict[str, Any],
) -> None:
    settings = _settings(zcode_cli_enabled=True, zcode_mode="app", cua_enabled=False)
    async with SaniRuntime.open(settings, zcode=_zcode_toolkit(settings)) as rt:
        assert installed["build_agent_kwargs"]["extra_tools"] == []
        assert not rt.zcode_app_enabled


async def test_zcode_off_binds_nothing(installed: dict[str, Any]) -> None:
    settings = _settings(zcode_cli_enabled=False)
    async with SaniRuntime.open(settings, zcode=_zcode_toolkit(settings)):
        names = sorted(t.name for t in installed["build_agent_kwargs"]["extra_tools"])
        assert names == ["launch_app", "observe"]


async def test_claude_code_and_zcode_can_both_be_bound(installed: dict[str, Any]) -> None:
    from assistant.claude_code.toolkit import ClaudeCodeToolkit

    settings = _settings(claude_code_enabled=True, zcode_cli_enabled=True, zcode_mode="cli")
    async with SaniRuntime.open(
        settings, claude_code=ClaudeCodeToolkit(settings), zcode=_zcode_toolkit(settings)
    ):
        kwargs = installed["build_agent_kwargs"]
        names = sorted(t.name for t in kwargs["extra_tools"])
        assert names == ["claude_code", "launch_app", "observe", "zcode"]
        assert "`claude_code`" in kwargs["system_prompt"] and "`zcode`" in kwargs["system_prompt"]


async def test_coding_tools_raise_the_models_output_cap(
    installed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    caps: list[int] = []
    real = runtime_module.build_chat_model

    def spy(settings: Settings) -> Any:
        caps.append(settings.model_max_tokens)
        return real(settings)

    monkeypatch.setattr(runtime_module, "build_chat_model", spy)
    plain = _settings()
    async with SaniRuntime.open(plain):
        pass
    coding = _settings(zcode_cli_enabled=True, zcode_mode="cli")
    async with SaniRuntime.open(coding, zcode=_zcode_toolkit(coding)):
        pass
    explicit = _settings(zcode_cli_enabled=True, zcode_mode="cli", model_max_tokens=12000)
    async with SaniRuntime.open(explicit, zcode=_zcode_toolkit(explicit)):
        pass
    # Unchanged without a coding tool; raised with one; a bigger explicit choice is kept.
    assert caps == [2000, 8000, 12000]

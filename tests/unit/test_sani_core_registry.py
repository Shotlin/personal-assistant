"""Unit tests for the sani-core agent registry (Sani master doc 9)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from assistant.core.registry import AgentDescriptor, AgentRegistry


class _StubAgent:
    """Minimal structural AgentProtocol implementation for registry tests."""

    def __init__(self, agent_id: str, name: str, capabilities: tuple[str, ...]) -> None:
        self._descriptor = AgentDescriptor(id=agent_id, name=name, capabilities=capabilities)

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def cancel(self) -> None:
        return None


def test_register_and_get_return_the_same_agent() -> None:
    registry = AgentRegistry()
    agent = _StubAgent("deep", "Deep Agent", ("chat",))
    registry.register(agent)
    assert registry.get("deep") is agent


def test_get_unknown_agent_raises_key_error_with_clear_message() -> None:
    registry = AgentRegistry()
    with pytest.raises(KeyError, match="unknown agent id"):
        registry.get("nope")


def test_register_duplicate_id_raises_value_error() -> None:
    registry = AgentRegistry()
    registry.register(_StubAgent("deep", "Deep Agent", ("chat",)))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_StubAgent("deep", "Another", ("chat",)))


def test_list_returns_descriptors_sorted_by_id() -> None:
    registry = AgentRegistry()
    registry.register(_StubAgent("velo", "Velo", ("computer-use",)))
    registry.register(_StubAgent("deep", "Deep Agent", ("chat", "tools")))
    descriptors = registry.list()
    assert [d.id for d in descriptors] == ["deep", "velo"]
    assert descriptors[0].name == "Deep Agent"
    assert descriptors[0].capabilities == ("chat", "tools")

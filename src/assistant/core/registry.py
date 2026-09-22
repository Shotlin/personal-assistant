"""Small agent registry for the sani-core sidecar (Sani master doc 9).

Agents are identified, listed, run and cancelled -- nothing more. This is
deliberately not a workflow platform: no pipelines, no scheduling, and no
agent-to-agent wiring lives here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    id: str
    name: str
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "capabilities": list(self.capabilities)}


class AgentProtocol(Protocol):
    """What every sani-core agent must expose: identity, run, cooperative cancel."""

    @property
    def descriptor(self) -> AgentDescriptor: ...

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        """Run to completion, streaming progress via on_event(kind, data).

        `thread_id` is the caller's conversation identity, supplied so a
        stateful agent resumes the same thread across turns instead of
        starting a fresh one every run. Agents that hold no conversation
        state ignore it.

        Returns the final result payload dict. A cooperative agent checks
        cancel_check between steps and lets CancelledError propagate; the
        caller enforces cancellation with task.cancel() regardless.
        """
        ...

    async def cancel(self) -> None:
        """Ask the agent to stop cooperatively; best-effort, never blocking long."""
        ...


class AgentRegistry:
    """Id-keyed agent store: register, get, list. No workflow semantics."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentProtocol] = {}

    def register(self, agent: AgentProtocol) -> None:
        descriptor = agent.descriptor
        if descriptor.id in self._agents:
            raise ValueError(f"agent id already registered: {descriptor.id!r}")
        self._agents[descriptor.id] = agent

    def get(self, agent_id: str) -> AgentProtocol:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise KeyError(f"unknown agent id: {agent_id!r}") from None

    def list(self) -> list[AgentDescriptor]:
        return [self._agents[agent_id].descriptor for agent_id in sorted(self._agents)]

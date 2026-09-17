"""The only module that calls ``create_deep_agent`` (spec section 11.5).

Receives the prebuilt chat model, filtered CUA tools, the checkpointer,
the long-term store, and the read-only skill directory; returns one
initialized agent. Performs no HTTP work.

Filesystem layout behind the agent:
- ``/``            -> StateBackend (virtual scratch files in graph state)
- ``/memories/``   -> PolicyStoreBackend (Postgres store, user-scoped, gated)
- ``/skills/``     -> ReadOnlyFilesystemBackend (developer-authored skills)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import DeleteResult, EditResult, WriteResult
from deepagents.backends.state import StateBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from assistant.agent.context import AgentContext
from assistant.agent.observation_trim import ObservationTrimMiddleware
from assistant.agent.profiles import register_single_agent_profile
from assistant.agent.system_prompt import SYSTEM_PROMPT
from assistant.memory.namespaces import user_memory_namespace
from assistant.memory.policy import PolicyStoreBackend

SKILLS_ROUTE = "/skills/"
MEMORIES_ROUTE = "/memories/"
MEMORY_FILES = ["/memories/preferences.md", "/memories/profile.md"]

FORBIDDEN_MODEL_VISIBLE_TOOLS = frozenset({"task", "execute"})


class AgentBuildError(RuntimeError):
    """Raised when the assembled agent violates the single-agent contract."""


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """Filesystem backend that refuses every mutation.

    Used for the ``/skills/`` route: skills are developer-authored and
    read-only in Phase 1 (spec section 12 / security rule 9).
    """

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=f"Skills are read-only in Phase 1; refusing write to {file_path}")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=f"Skills are read-only in Phase 1; refusing write to {file_path}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return EditResult(error=f"Skills are read-only in Phase 1; refusing edit of {file_path}")

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return EditResult(error=f"Skills are read-only in Phase 1; refusing edit of {file_path}")

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=f"Skills are read-only; refusing delete of {file_path}")

    async def adelete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=f"Skills are read-only; refusing delete of {file_path}")


def _memory_namespace_factory() -> Any:
    """Build the per-request namespace factory for the memory store."""

    def factory(runtime: Any) -> tuple[str, ...]:
        context = getattr(runtime, "context", None)
        user_id = getattr(context, "user_id", None) or "anonymous"
        return user_memory_namespace(user_id)

    return factory


def build_backend(store: BaseStore, skills_root: Path) -> CompositeBackend:
    """Assemble the composite backend with virtual scratch, gated memories, read-only skills."""
    return CompositeBackend(
        default=StateBackend(),
        routes={
            MEMORIES_ROUTE: PolicyStoreBackend(store=store, namespace=_memory_namespace_factory()),
            SKILLS_ROUTE: ReadOnlyFilesystemBackend(root_dir=str(skills_root)),
        },
    )


def exposed_tool_names(agent: Any) -> set[str]:
    """Names of tools registered on the agent's tool node."""
    tools_node = agent.nodes.get("tools")
    if tools_node is None:
        return set()
    return set(getattr(tools_node.bound, "tools_by_name", {}).keys())


@dataclass
class AgentBundle:
    """The built agent plus its identity for logging."""

    agent: Any
    profile_keys: list[str]


def build_agent(
    *,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills_root: Path,
    extra_tools: Sequence[BaseTool] = (),
) -> AgentBundle:
    """Build exactly one Deep Agent (no subagents, no host shell)."""
    profile_keys = register_single_agent_profile(model)
    backend = build_backend(store, skills_root)

    trim_middleware: Any = ObservationTrimMiddleware()
    agent = create_deep_agent(
        model,
        tools=list(extra_tools) or None,
        backend=backend,
        skills=[SKILLS_ROUTE],
        memory=MEMORY_FILES,
        system_prompt=SYSTEM_PROMPT,
        middleware=[trim_middleware],
        checkpointer=checkpointer,
        store=store,
        context_schema=AgentContext,
    )

    names = exposed_tool_names(agent)
    # ``task`` must not exist on the node at all (subagents disabled).
    # ``execute`` may remain registered on the node but is filtered before
    # the model sees it and rejected at the tool-call boundary by the
    # harness profile; that binding is verified in profiles.py and by the
    # integration test capturing bind-time tool names.
    if "task" in names:
        raise AgentBuildError(
            f"Single-agent contract violated: 'task' tool is exposed (profile keys: {profile_keys})"
        )
    return AgentBundle(agent=agent, profile_keys=profile_keys)

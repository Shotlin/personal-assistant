"""Runtime compiler: graph revision -> ExecutionConfig (P5).

``compile_execution_config`` is pure: it turns a validated revision's
graph plus resolved dependencies into the normalized description of ONE
independent single-agent runtime. It never touches providers, MCP
servers, or the desktop (Validate/Prepare own processes; this owns
nothing but data).

Key invariants (frozen plan):
- NO subagent dispatch, NO host-shell execute — the trusted profile is
  re-asserted by every runtime build (agent/profiles.py).
- The effective capability set drives every execution path (A2): exact
  recipes, compact planner, direct tool dispatch, MCP and CUA.
- Knowledge retrieval mounts only when the adapter is EXECUTABLE (Fix 2);
  otherwise the node is inert (and validation already failed activation).
- Context defaults (R16): 12 recent turns, 32k estimated input,
  2048 output cap, 16 model attempts, 60 tool calls, 15-min run limit —
  proposed defaults within operator ceilings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from assistant.designer.connectors import ScopeKind
from assistant.designer.schemas import GraphDocument

# R16 defaults (proposed, operator ceilings applied downstream in P6).
CONTEXT_DEFAULTS = {
    "recent_turns": 12,
    "estimated_input_tokens": 32_000,
    "output_token_cap": 2_048,
    "max_model_attempts": 16,
    "max_tool_calls": 60,
    "run_wall_clock_seconds": 900,
}

#: Canonical capability ids. Disconnecting a node removes its id from the
#: effective set — every dispatch path re-checks it (A2).
CAP_CUA = "cua"
CAP_TERMINAL = "terminal"


def _capability_id_mcp(connector_id: str, tool: str) -> str:
    return f"mcp:{connector_id}:{tool}"


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model_id: str
    base_url: str = ""
    credential_ref: str = "operator-env"
    timeout_seconds: int = 120
    max_retries: int = 2


@dataclass(frozen=True)
class PromptConfig:
    """Immutable safety prefix (code-owned) + optional custom prompt."""

    safety_prefix_ref: str = "gateway:vion-safety-prefix"
    custom_text: str = ""
    resource_ref: str | None = None


@dataclass(frozen=True)
class SkillRef:
    source: str
    id: str
    revision_or_hash: str


@dataclass(frozen=True)
class MemoryPolicy:
    thread_recall: bool = True
    user_memory: bool = True
    retention_days: int | None = None


@dataclass(frozen=True)
class ContextPolicy:
    recent_turns: int = CONTEXT_DEFAULTS["recent_turns"]
    estimated_input_tokens: int = CONTEXT_DEFAULTS["estimated_input_tokens"]
    output_token_cap: int = CONTEXT_DEFAULTS["output_token_cap"]
    max_model_attempts: int = CONTEXT_DEFAULTS["max_model_attempts"]
    max_tool_calls: int = CONTEXT_DEFAULTS["max_tool_calls"]
    run_wall_clock_seconds: int = CONTEXT_DEFAULTS["run_wall_clock_seconds"]


@dataclass(frozen=True)
class ExecutionConfig:
    """The compiler's normalized runtime description (plan §2)."""

    agent_id: str
    revision_id: str
    model: ModelConfig
    prompt: PromptConfig
    skills: tuple[SkillRef, ...]
    memory: MemoryPolicy
    context: ContextPolicy
    capabilities: frozenset[str]
    knowledge_ids: tuple[str, ...] = ()
    terminal_enabled: bool = False

    def allows_native_dispatch(self) -> bool:
        """A2: native desktop actions require the CUA capability."""
        return CAP_CUA in self.capabilities

    def allows_tool(self, capability_id: str) -> bool:
        return capability_id in self.capabilities


def compile_execution_config(
    *,
    agent_id: str,
    revision_id: str,
    graph: GraphDocument,
    resolved_dependencies: dict[str, Any] | None = None,
) -> ExecutionConfig:
    """Pure graph -> ExecutionConfig. Raises on a disconnected model."""
    resolved = resolved_dependencies or {}
    connected_ids = {edge.target for edge in graph.edges}

    model_node = next(
        (n for n in graph.nodes if n.type == "model" and n.id in connected_ids and n.data.enabled),
        None,
    )
    if model_node is None:
        raise ValueError("execution config requires exactly one connected model node")

    model_cfg = dict(model_node.data.config)
    model = ModelConfig(
        provider=str(model_cfg.get("provider", "operator-env")),
        model_id=str(model_cfg.get("model_id", "")),
        base_url=str(model_cfg.get("base_url", "")),
        credential_ref=str(model_cfg.get("credential_ref", "operator-env")),
        timeout_seconds=int(model_cfg.get("timeout_seconds", 120)),
        max_retries=int(model_cfg.get("max_retries", 2)),
    )

    prompt_node = next(
        (n for n in graph.nodes if n.type == "prompt" and n.id in connected_ids), None
    )
    prompt = PromptConfig(
        custom_text=str(prompt_node.data.config.get("text", "") if prompt_node else ""),
        resource_ref=(
            prompt_node.data.config.get("resource_ref") if prompt_node else None
        ),
    )

    skills: list[SkillRef] = []
    for node in graph.nodes:
        if node.type == "skill" and node.id in connected_ids and node.data.enabled:
            skills.append(
                SkillRef(
                    source=str(node.data.config.get("source", "gateway")),
                    id=str(node.data.config.get("id", node.id)),
                    revision_or_hash=str(node.data.config.get("revision_or_hash", "")),
                )
            )

    memory_node = next(
        (n for n in graph.nodes if n.type == "memory" and n.id in connected_ids and n.data.enabled),
        None,
    )
    if memory_node is None:
        memory = MemoryPolicy(thread_recall=False, user_memory=False)
    else:
        memory = MemoryPolicy(
            thread_recall=bool(memory_node.data.config.get("thread_recall", True)),
            user_memory=bool(memory_node.data.config.get("user_memory", True)),
        )

    context_node = next(
        (n for n in graph.nodes if n.type == "context" and n.id in connected_ids), None
    )
    if context_node is None:
        context = ContextPolicy()
    else:
        cfg = context_node.data.config
        context = ContextPolicy(
            recent_turns=int(cfg.get("recent_turns", CONTEXT_DEFAULTS["recent_turns"])),
            estimated_input_tokens=int(
                cfg.get("estimated_input_tokens", CONTEXT_DEFAULTS["estimated_input_tokens"])
            ),
            output_token_cap=int(
                cfg.get("output_token_cap", CONTEXT_DEFAULTS["output_token_cap"])
            ),
            max_model_attempts=int(
                cfg.get("max_model_attempts", CONTEXT_DEFAULTS["max_model_attempts"])
            ),
            max_tool_calls=int(cfg.get("max_tool_calls", CONTEXT_DEFAULTS["max_tool_calls"])),
            run_wall_clock_seconds=int(
                cfg.get(
                    "run_wall_clock_seconds", CONTEXT_DEFAULTS["run_wall_clock_seconds"]
                )
            ),
        )

    capabilities: set[str] = set()
    knowledge_ids: list[str] = []
    cua_node = next(
        (n for n in graph.nodes if n.type == "cua" and n.id in connected_ids and n.data.enabled),
        None,
    )
    if cua_node is not None:
        capabilities.add(CAP_CUA)
    for node in graph.nodes:
        if node.type == "mcp" and node.id in connected_ids and node.data.enabled:
            connector_id = str(node.data.config.get("connector_id", node.id))
            for tool in node.data.config.get("selected_tool_ids", []):
                capabilities.add(_capability_id_mcp(connector_id, str(tool)))
        if node.type == "knowledge" and node.id in connected_ids and node.data.enabled:
            # Mounted at runtime ONLY when the adapter is EXECUTABLE (Fix 2);
            # validation already refuses activation while BLOCKED.
            adapter_status = str(
                resolved.get("knowledge_adapter_status") or "EXECUTABLE"
            )
            if adapter_status == "EXECUTABLE":
                knowledge_ids.append(str(node.data.config.get("knowledge_id", node.id)))

    return ExecutionConfig(
        agent_id=agent_id,
        revision_id=revision_id,
        model=model,
        prompt=prompt,
        skills=tuple(skills),
        memory=memory,
        context=context,
        capabilities=frozenset(capabilities),
        knowledge_ids=tuple(knowledge_ids),
    )


def config_hash(config: ExecutionConfig) -> str:
    material = {
        "model": vars(config.model),
        "prompt": vars(config.prompt),
        "skills": [vars(s) for s in config.skills],
        "memory": vars(config.memory),
        "context": vars(config.context),
        "capabilities": sorted(config.capabilities),
        "knowledge_ids": list(config.knowledge_ids),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Scope refusal (Fix 3, pinned by P4 tests): a user-scoped connector must
# never be compiled into a shared runtime.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeScopeRequest:
    """The scope a runtime is being compiled for + the connectors it
    would bind. Compilation refuses structurally unsafe combinations."""

    runtime_scope: ScopeKind
    connectors: dict[str, ScopeKind] | None = None

    def __post_init__(self) -> None:
        if self.connectors is None:
            object.__setattr__(self, "connectors", {})


def refuse_mismatched_scope(request: RuntimeScopeRequest) -> None:
    """Raise permission_denied when a user-scoped connector would land in
    a shared runtime — a guess is never made (Fix 3)."""
    from assistant.designer.connectors import ScopeKind

    if request.runtime_scope is not ScopeKind.SHARED:
        return
    offenders = sorted(
        name for name, scope in (request.connectors or {}).items()
        if scope is ScopeKind.USER_SCOPED
    )
    if offenders:
        from assistant.designer.errors import DesignerError

        raise DesignerError(
            "permission_denied",
            f"user-scoped connector(s) {offenders} cannot join a shared runtime; "
            "compile a user-scoped runtime instead",
        )

"""A scripted chat model for deterministic agent tests (no network, no spend).

Records every ``bind_tools`` call and every model request so tests can
assert which tools the model actually sees and what context was injected.
Pretends to be provider ``scripted`` so harness profiles bind like a real
provider integration would.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.base import LangSmithParams
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Returns pre-scripted messages in order and records what it receives."""

    responses: list[BaseMessage] = []
    seen: list[list[BaseMessage]] = []
    bound_tool_names: list[list[str]] = []
    call_index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        # deepagents resolves harness profiles via ls_provider.
        return LangSmithParams(ls_provider="scripted", ls_model_name="test-model")

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        names: list[str] = []
        for tool in tools or []:
            name = getattr(tool, "name", None) or (
                tool.get("name") if isinstance(tool, dict) else None
            )
            if name:
                names.append(str(name))
        self.bound_tool_names.append(names)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Sequence[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        message = self.responses[min(self.call_index, len(self.responses) - 1)]
        self.call_index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

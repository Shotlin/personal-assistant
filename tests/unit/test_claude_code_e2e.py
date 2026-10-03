"""The whole chain, minus the network: a real LangGraph agent loop calls the real
claude_code tool, which runs a fake `claude`; steps must reach the core's event
channel, and the agent must answer from the tool's result."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.prebuilt import create_react_agent

from assistant.claude_code.locate import ClaudeStatus
from assistant.claude_code.runner import ClaudeRunner
from assistant.claude_code.toolkit import ClaudeCodeToolkit
from assistant.core.agents import DeepAgentEntry
from assistant.core.protocol import AGENT_PROGRESS, AGENT_TOKEN
from assistant.settings import Settings
from tests.unit.test_claude_code import fake_claude  # noqa: F401


class ScriptedModel(BaseChatModel):
    """Calls claude_code once, then answers from the tool result."""

    project: str = ""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **_: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[BaseMessage], *_: Any, **__: Any) -> ChatResult:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            reply = AIMessage(content=f"Done. Claude Code said: {str(last.content)[:60]}")
        else:
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "claude_code",
                        "id": f"call-{uuid.uuid4().hex[:6]}",
                        "args": {
                            "task": "SCENARIO:ok fix the bug",
                            "project_dir": self.project,
                            "purpose": "Fix the bug",
                        },
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


async def test_agent_loop_tool_run_and_steps_reach_the_event_channel(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    project = tmp_path / "work" / "app"
    project.mkdir(parents=True)
    (tmp_path / "data").mkdir()
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "data"),
        claude_code_enabled=True,
        claude_code_dirs=str(tmp_path / "work"),
    )

    async def reader(_: Path | None) -> ClaudeStatus:
        return ClaudeStatus(installed=True, version="2.1.286", signed_in=True)

    toolkit = ClaudeCodeToolkit(
        settings,
        locator=lambda **_: fake_claude,
        status_reader=reader,
        runner_factory=lambda binary, version: ClaudeRunner(
            binary, version=version, tick_seconds=0.05, grace_seconds=2.0
        ),
    )
    agent = create_react_agent(ScriptedModel(project=str(project)), [toolkit.as_tool()])

    async def build() -> Any:
        return agent

    entry = DeepAgentEntry(settings, agent_builder=build)
    frames: list[tuple[str, dict[str, Any]]] = []

    async def on_event(kind: str, data: dict[str, Any]) -> None:
        frames.append((kind, data))

    result = await entry.run(
        "please fix the bug", thread_id="chat-1", on_event=on_event, cancel_check=lambda: False
    )

    steps = [d["step"] for k, d in frames if k == AGENT_PROGRESS and "step" in d]
    kinds = [s.get("kind") for s in steps]
    assert "round" in kinds and "prompt" in kinds and "reply" in kinds
    assert any(s["label"] == "Edited src/a.py" for s in steps)
    assert next(s for s in steps if s.get("kind") == "round")["label"] == "Fix the bug"
    assert any(k == AGENT_TOKEN for k, _ in frames)
    assert result["status"] == "done"
    assert "Claude Code said" in result["response"]
    # The session was remembered for this chat.
    assert await toolkit._store.get_session("chat-1", str(project)) == "sess-1"  # type: ignore[union-attr]  # noqa: SLF001

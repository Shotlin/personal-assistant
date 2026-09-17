"""Deep Agent core integration tests (Phase 1 Task 4, spec sections 11-12).

Runs the real agent graph with a scripted model against the real compose
PostgreSQL. Proves: single-agent tool contract, conversation streaming and
thread persistence, user-scoped memory recall across threads, and the
memory write policy inside the agent flow.
"""

import json
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from assistant.agent.build import build_agent
from assistant.agent.context import AgentContext
from assistant.memory.namespaces import thread_id_for, user_memory_namespace
from assistant.memory.postgres import open_memory_resources
from tests.helpers.scripted_model import ScriptedChatModel

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"


def make_agent(model: ScriptedChatModel, checkpointer, store):
    bundle = build_agent(
        model=model,
        checkpointer=checkpointer,
        store=store,
        skills_root=SKILLS_ROOT,
    )
    return bundle.agent


def exposed_tool_names(agent) -> set[str]:
    tools_node = agent.nodes.get("tools")
    assert tools_node is not None
    return set(getattr(tools_node.bound, "tools_by_name", {}).keys())


async def test_agent_has_no_task_or_execute_tools(require_postgres: None) -> None:
    async with open_memory_resources(POSTGRES_URL) as mem:
        model = ScriptedChatModel(responses=[AIMessage("hello")])
        agent = make_agent(model, mem.saver, mem.store)
        names = exposed_tool_names(agent)
        assert "task" not in names, f"task tool exposed: {sorted(names)}"
        # Run one turn so the middleware binds tools to the model.
        await agent.ainvoke(
            {"messages": [HumanMessage("hi")]},
            {"configurable": {"thread_id": thread_id_for("bindtest", "chat")}},
            context=AgentContext(user_id="bindtest", chat_id="chat"),
        )
        # execute may exist on the node but must never reach the model.
        assert model.bound_tool_names, "model never bound tools"
        for bound in model.bound_tool_names:
            assert "execute" not in bound, f"execute reached the model: {bound}"
            assert "task" not in bound, f"task reached the model: {bound}"


async def test_conversation_streams_and_persists_across_reconnect(require_postgres: None) -> None:
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    thread = thread_id_for(user_id, chat_id)
    config = {"configurable": {"thread_id": thread}}

    async with open_memory_resources(POSTGRES_URL) as mem:
        model = ScriptedChatModel(responses=[AIMessage("first reply"), AIMessage("second reply")])
        agent = make_agent(model, mem.saver, mem.store)

        streamed = []
        async for chunk in agent.astream(
            {"messages": [HumanMessage("turn one")]},
            config,
            context=AgentContext(user_id=user_id, chat_id=chat_id),
        ):
            streamed.append(chunk)
        assert streamed, "streaming produced no output"

        await agent.ainvoke(
            {"messages": [HumanMessage("turn two")]},
            config,
            context=AgentContext(user_id=user_id, chat_id=chat_id),
        )

    # Fresh "process": same thread must still hold the conversation.
    async with open_memory_resources(POSTGRES_URL) as mem2:
        model2 = ScriptedChatModel(responses=[AIMessage("third reply")])
        agent2 = make_agent(model2, mem2.saver, mem2.store)
        state = await agent2.aget_state(config)
        contents = [str(getattr(m, "text", None) or m.content) for m in state.values["messages"]]
        assert "turn one" in contents
        assert "second reply" in contents
        assert "turn two" in contents


async def test_memory_write_recalled_in_new_thread_and_isolated(require_postgres: None) -> None:
    user_id = f"memuser-{uuid.uuid4().hex[:8]}"
    preference = "Keep coding prompts concise but technically complete."

    async with open_memory_resources(POSTGRES_URL) as mem:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/memories/preferences.md",
                                "content": preference,
                            },
                            "id": "mem1",
                        }
                    ],
                ),
                AIMessage("Preference saved."),
            ]
        )
        agent = make_agent(model, mem.saver, mem.store)
        result = await agent.ainvoke(
            {"messages": [HumanMessage("Remember this preference")]},
            {"configurable": {"thread_id": thread_id_for(user_id, "chat-1")}},
            context=AgentContext(user_id=user_id, chat_id="chat-1"),
        )
        assert result["messages"][-1].content == "Preference saved."

        items = await mem.store.asearch(user_memory_namespace(user_id))
        assert items, "memory file was not persisted"
        assert any("preferences" in i.key for i in items)
        assert any(preference in json.dumps(i.value) for i in items)

    # New thread, same user: the preference must be injected as context.
    async with open_memory_resources(POSTGRES_URL) as mem2:
        model2 = ScriptedChatModel(responses=[AIMessage("done")])
        agent2 = make_agent(model2, mem2.saver, mem2.store)
        await agent2.ainvoke(
            {"messages": [HumanMessage("prepare a coding prompt")]},
            {"configurable": {"thread_id": thread_id_for(user_id, "chat-2")}},
            context=AgentContext(user_id=user_id, chat_id="chat-2"),
        )
        seen_text = " ".join(
            str(getattr(m, "text", None) or m.content) for m in model2.seen[-1]
        )
        assert "concise but technically complete" in seen_text

    # A different user's namespace stays empty.
    async with open_memory_resources(POSTGRES_URL) as mem3:
        other_ns = user_memory_namespace(f"other-{uuid.uuid4().hex[:8]}")
        assert await mem3.store.asearch(other_ns) == []


async def test_memory_policy_blocks_secret_writes_in_agent_flow(require_postgres: None) -> None:
    user_id = f"secret-{uuid.uuid4().hex[:8]}"

    async with open_memory_resources(POSTGRES_URL) as mem:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/memories/preferences.md",
                                "content": "api_key=sk-abcdefabcdefabcdef",
                            },
                            "id": "sec1",
                        }
                    ],
                ),
                AIMessage("I will not store that."),
            ]
        )
        agent = make_agent(model, mem.saver, mem.store)
        result = await agent.ainvoke(
            {"messages": [HumanMessage("Save my key so you remember it")]},
            {"configurable": {"thread_id": thread_id_for(user_id, "chat-sec")}},
            context=AgentContext(user_id=user_id, chat_id="chat-sec"),
        )
        tool_messages = [m for m in result["messages"] if getattr(m, "type", "") == "tool"]
        assert tool_messages, "no tool result observed"
        assert any("rejected" in str(m.content) for m in tool_messages)
        assert await mem.store.aget(user_memory_namespace(user_id), "/preferences.md") is None

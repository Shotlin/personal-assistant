"""Persistence and isolation tests against the real compose Postgres (Task 3).

Proves:
- thread state survives a full close/reconnect (process-restart shape),
- long-term store items persist across reconnects,
- namespaces isolate users (user A's items never surface for user B).
"""

import uuid
from typing import Any, TypedDict

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.postgres.aio import AsyncPostgresStore

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"


class _EchoState(TypedDict):
    value: str


def _make_graph(checkpointer: AsyncPostgresSaver) -> Any:
    def echo(state: _EchoState) -> dict[str, str]:
        return {"value": state["value"]}

    builder = StateGraph(_EchoState)
    builder.add_node("echo", echo)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder.compile(checkpointer=checkpointer)


async def test_thread_state_survives_reconnect(require_postgres: None) -> None:
    thread_id = f"owui:itest:{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}

    # "First process": run a turn and let the connection close.
    async with AsyncPostgresSaver.from_conn_string(POSTGRES_URL) as saver:
        await saver.setup()
        graph = _make_graph(saver)
        await graph.ainvoke({"value": "persist-me"}, config)

    # "Second process" (fresh pool): the same thread must still be there.
    async with AsyncPostgresSaver.from_conn_string(POSTGRES_URL) as saver2:
        await saver2.setup()
        graph2 = _make_graph(saver2)
        state = await graph2.aget_state(config)
        assert state.values["value"] == "persist-me"


async def test_store_items_survive_reconnect(require_postgres: None) -> None:
    namespace = ("users", f"persist-user-{uuid.uuid4().hex[:8]}", "assistant-memory")

    async with AsyncPostgresStore.from_conn_string(POSTGRES_URL) as store:
        await store.setup()
        await store.aput(namespace, "pref", {"text": "concise-but-complete"})

    async with AsyncPostgresStore.from_conn_string(POSTGRES_URL) as store2:
        await store2.setup()
        item = await store2.aget(namespace, "pref")
        assert item is not None
        assert item.value["text"] == "concise-but-complete"


async def test_user_namespaces_are_isolated(require_postgres: None) -> None:
    run = uuid.uuid4().hex[:8]
    user_a = ("users", f"user-a-{run}", "assistant-memory")
    user_b = ("users", f"user-b-{run}", "assistant-memory")

    async with AsyncPostgresStore.from_conn_string(POSTGRES_URL) as store:
        await store.setup()
        await store.aput(user_a, "pref", {"text": "A-secret-preference"})
        await store.aput(user_b, "pref", {"text": "B-secret-preference"})

    async with AsyncPostgresStore.from_conn_string(POSTGRES_URL) as store2:
        await store2.setup()

        # User B's namespace search must never surface user A's item.
        items_b = await store2.asearch(user_b)
        assert [i.value["text"] for i in items_b] == ["B-secret-preference"]

        # And user A's namespace search must never surface user B's item.
        items_a = await store2.asearch(user_a)
        assert [i.value["text"] for i in items_a] == ["A-secret-preference"]

        # A key lookup in B's namespace only ever returns B's own values.
        cross = await store2.aget(user_b, "pref")
        assert cross is not None and cross.value["text"] == "B-secret-preference"

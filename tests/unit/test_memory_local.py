"""Embedded SQLite memory backend tests (Sani master doc sections 2-5).

No Docker, no PostgreSQL: everything runs on one temporary sani.db.
"""

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.base import create_checkpoint, empty_checkpoint
from langgraph.store.base import ListNamespacesOp, MatchCondition

from assistant.memory.local import open_local_memory_resources
from assistant.memory.namespaces import user_memory_namespace
from assistant.memory.policy import PolicyStoreBackend

USER_NS = user_memory_namespace("user-a")


def make_policy_backend(store) -> PolicyStoreBackend:
    return PolicyStoreBackend(store=store, namespace=lambda _runtime: USER_NS)


async def test_store_roundtrip_and_namespace_isolation(tmp_path: Path) -> None:
    async with open_local_memory_resources(tmp_path / "sani.db") as resources:
        store = resources.store
        await store.aput(USER_NS, "profile", {"text": "Likes concise answers."})
        await store.aput(user_memory_namespace("user-b"), "profile", {"text": "Other user."})

        item = await store.aget(USER_NS, "profile")
        assert item is not None
        assert item.value == {"text": "Likes concise answers."}
        assert item.namespace == USER_NS

        # Isolation: user-b's memory is a different namespace entirely.
        other = await store.asearch(user_memory_namespace("user-b"))
        assert [hit.value["text"] for hit in other] == ["Other user."]


async def test_store_search_filter_and_delete(tmp_path: Path) -> None:
    async with open_local_memory_resources(tmp_path / "sani.db") as resources:
        store = resources.store
        await store.aput(USER_NS, "a", {"kind": "fact", "text": "one"})
        await store.aput(USER_NS, "b", {"kind": "preference", "text": "two"})

        facts = await store.asearch(USER_NS, filter={"kind": "fact"})
        assert [hit.key for hit in facts] == ["a"]

        await store.adelete(USER_NS, "a")
        assert await store.aget(USER_NS, "a") is None
        assert await store.aget(USER_NS, "b") is not None


async def test_store_list_namespaces_with_prefix_condition(tmp_path: Path) -> None:
    async with open_local_memory_resources(tmp_path / "sani.db") as resources:
        store = resources.store
        await store.aput(USER_NS, "k", {"v": 1})
        await store.aput(user_memory_namespace("user-b"), "k", {"v": 2})
        listed = await store.abatch(
            [
                ListNamespacesOp(
                    match_conditions=(MatchCondition(match_type="prefix", path=("users",)),)
                )
            ]
        )
        assert listed == [[USER_NS, user_memory_namespace("user-b")]]


async def test_persistence_across_reopen(tmp_path: Path) -> None:
    async with open_local_memory_resources(tmp_path / "sani.db") as resources:
        await resources.store.aput(USER_NS, "durable", {"text": "survives restart"})

    async with open_local_memory_resources(tmp_path / "sani.db") as reopened:
        hits = await reopened.store.asearch(USER_NS)
        assert [hit.value["text"] for hit in hits] == ["survives restart"]


async def test_checkpointer_roundtrip_on_the_same_db_file(tmp_path: Path) -> None:
    async with open_local_memory_resources(tmp_path / "sani.db") as resources:
        saver = resources.saver
        config: Any = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
        assert await saver.aget_tuple(config) is None

        first = empty_checkpoint()
        # CheckpointMetadata carries "writes" at runtime even though the
        # TypedDict omits it; Any annotations keep the test honest.
        metadata: Any = {"source": "input", "step": 1, "writes": {}}
        channels: Any = {"ch": "value"}
        await saver.aput(config, first, metadata, {})
        second = create_checkpoint(first, channels, 1)
        await saver.aput(config, second, {**metadata, "step": 2}, {})

        latest = await saver.aget_tuple(config)
        assert latest is not None
        steps = [c.metadata["step"] async for c in saver.alist(config, limit=10)]
        assert sorted(steps) == [1, 2]


async def test_secret_policy_gates_sqlite_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "sani.db"
    async with open_local_memory_resources(db_path) as resources:
        backend = make_policy_backend(resources.store)

        rejected = backend.write("/memories/leak.md", "api_key = sk-verysecretvalue123")
        assert rejected.error is not None
        assert "rejected" in rejected.error

        allowed = backend.write("/memories/prefs.md", "Prefers concise answers.")
        assert allowed.error is None

    # The secret bytes never reached the embedded database file.
    raw = db_path.read_bytes()
    assert b"sk-verysecretvalue123" not in raw
    assert b"concise answers" in raw


def test_sani_db_path_derivation(tmp_path: Path) -> None:
    from assistant.settings import Settings

    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path),
    )
    assert settings.sani_db_path == str(tmp_path / "sani.db")


def test_sqlite_backend_requires_sani_data_dir() -> None:
    from assistant.settings import Settings, SettingsError

    with pytest.raises(SettingsError, match="SANI_DATA_DIR"):
        Settings(
            app_env="development",
            model_provider="generic_openai_compatible",
            model_base_url="http://127.0.0.1:1",
            model_api_key="k",
            model_name="m",
            memory_backend="sqlite",
            sani_data_dir="",
        )

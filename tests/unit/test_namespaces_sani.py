"""Sani local identity namespaces (master doc section 25)."""

from assistant.memory.namespaces import (
    split_sani_thread_id,
    thread_id_for_sani,
)


def test_sani_thread_id_roundtrip() -> None:
    thread_id = thread_id_for_sani("conv-123")
    assert thread_id == "sani:conv-123"
    assert split_sani_thread_id(thread_id) == "conv-123"


def test_sani_thread_id_requires_conversation_id() -> None:
    import pytest

    with pytest.raises(ValueError):
        thread_id_for_sani("  ")
    assert split_sani_thread_id("owui:user:chat") is None
    assert split_sani_thread_id("sani:") is None

"""Unit tests for session mapping and turn resolution (spec 15.2, 16.4-16.5)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from assistant.api.identity import (
    CHAT_ID_HEADER,
    DEV_CHAT_ID_HEADER,
    DEV_USER_ID_HEADER,
    TASK_HEADER,
    USER_ID_HEADER,
    extract_identity,
    is_utility_task,
)
from assistant.api.schemas import ChatMessage, GatewayError
from assistant.api.turns import decide_turn, normalize_history
from assistant.memory.namespaces import split_thread_id, thread_id_for

FULL_HEADERS = {
    USER_ID_HEADER: "user-abc",
    CHAT_ID_HEADER: "chat-123",
    "X-OpenWebUI-Message-Id": "m1",
    "X-OpenWebUI-User-Message-Id": "um1",
    "X-OpenWebUI-User-Message-Parent-Id": "p1",
    TASK_HEADER: "",
}


def test_identity_maps_to_scoped_thread() -> None:
    identity = extract_identity(FULL_HEADERS, is_production=True)
    assert identity.user_id == "user-abc"
    assert identity.chat_id == "chat-123"
    assert identity.thread_id == "owui:user-abc:chat-123"
    assert identity.user_message_id == "um1"
    assert identity.user_message_parent_id == "p1"
    assert identity.is_utility is False
    assert identity.source == "openwebui"


def test_identity_missing_headers_rejected_in_production() -> None:
    with pytest.raises(GatewayError) as excinfo:
        extract_identity({}, is_production=True)
    assert excinfo.value.code == "missing_chat_identity"


def test_identity_missing_headers_rejected_in_development_without_dev_headers() -> None:
    with pytest.raises(GatewayError) as excinfo:
        extract_identity({}, is_production=False)
    assert excinfo.value.code == "missing_chat_identity"


def test_identity_dev_test_headers_in_development() -> None:
    headers = {DEV_USER_ID_HEADER: "dev-user", DEV_CHAT_ID_HEADER: "dev-chat"}
    identity = extract_identity(headers, is_production=False)
    assert identity.source == "dev-test"
    assert identity.thread_id == "owui:dev-user:dev-chat"


def test_identity_dev_test_headers_ignored_in_production() -> None:
    headers = {DEV_USER_ID_HEADER: "dev-user", DEV_CHAT_ID_HEADER: "dev-chat"}
    with pytest.raises(GatewayError):
        extract_identity(headers, is_production=True)


def test_utility_task_detection_is_fail_safe() -> None:
    assert is_utility_task(None) is False
    assert is_utility_task("") is False
    assert is_utility_task("  ") is False
    for task in ("title_generation", "tags_generation", "follow_up_generation", "anything-else"):
        assert is_utility_task(task) is True


def test_thread_id_round_trip() -> None:
    thread = thread_id_for("u1", "c1")
    assert thread == "owui:u1:c1"
    assert split_thread_id(thread) == ("u1", "c1")
    assert split_thread_id("bogus") is None


def test_normalize_history_maps_roles_and_drops_tool_messages() -> None:
    normalized = normalize_history(
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", content="a"),
            ChatMessage(role="tool", content="tool-result"),
        ]
    )
    assert isinstance(normalized[0], SystemMessage)
    assert isinstance(normalized[1], HumanMessage)
    assert isinstance(normalized[2], AIMessage)
    assert len(normalized) == 3


def test_decide_turn_initializes_empty_thread() -> None:
    incoming = [SystemMessage("s"), HumanMessage("hi")]
    decision = decide_turn(persisted=[], incoming=incoming)
    assert decision.mode == "initialize"
    assert decision.messages == incoming
    assert decision.fork is False


def test_decide_turn_new_turn_appends_only_latest_user_message() -> None:
    persisted = [HumanMessage("first"), AIMessage("reply one")]
    incoming = [HumanMessage("first"), AIMessage("reply one"), HumanMessage("second")]
    decision = decide_turn(persisted, incoming)
    assert decision.mode == "new_turn"
    assert decision.fork is False
    assert [m.content for m in decision.messages] == ["second"]


def test_decide_turn_regenerates_when_assistant_replied() -> None:
    persisted = [HumanMessage("same question"), AIMessage("old answer")]
    incoming = [HumanMessage("same question")]
    decision = decide_turn(persisted, incoming)
    assert decision.mode == "regenerate"
    assert decision.fork is True


def test_decide_turn_replays_retry_without_assistant_reply() -> None:
    persisted = [HumanMessage("same question")]  # turn failed; no reply persisted
    incoming = [HumanMessage("same question")]
    decision = decide_turn(persisted, incoming)
    assert decision.mode == "regenerate"  # uniform replay path (no mid-tool resume)
    assert decision.fork is True


def test_decide_turn_resumes_without_user_message() -> None:
    persisted = [HumanMessage("q"), AIMessage("a")]
    decision = decide_turn(persisted, incoming=[SystemMessage("sys")])
    assert decision.mode == "resume"
    assert decision.messages == []
    assert decision.fork is True

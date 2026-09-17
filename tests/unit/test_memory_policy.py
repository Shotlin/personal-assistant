"""Unit tests for the memory write policy (spec 14.3).

Uses langgraph's InMemoryStore so no database is required.
"""


from langgraph.store.memory import InMemoryStore

from assistant.memory.namespaces import user_memory_namespace
from assistant.memory.policy import PolicyStoreBackend, contains_secret

USER_NS = user_memory_namespace("user-a")


def make_backend() -> PolicyStoreBackend:
    """Build the policy backend over an in-memory store."""
    return PolicyStoreBackend(
        store=InMemoryStore(),
        namespace=lambda _runtime: USER_NS,
    )


def test_rejects_api_key_assignment() -> None:
    assert contains_secret("my api_key = sk-abc123def456ghi789") is not None


def test_rejects_password_assignment() -> None:
    assert contains_secret("password: hunter2") is not None


def test_rejects_bearer_token() -> None:
    assert contains_secret("use Bearer abcdefghij1234567890") is not None


def test_allows_normal_preference() -> None:
    assert contains_secret("Keep coding prompts concise but technically complete.") is None


def test_allows_the_word_password_without_assignment() -> None:
    assert contains_secret("Never store a password in memory files.") is None


def test_policy_backend_rejects_secret_write() -> None:
    backend = PolicyStoreBackend(store=InMemoryStore(), namespace=lambda _rt: USER_NS)
    result = backend.write("/memories/preferences.md", "api_key=super-secret-value")
    assert result.error is not None
    assert "rejected" in result.error


def test_policy_backend_allows_normal_write() -> None:
    backend = PolicyStoreBackend(store=InMemoryStore(), namespace=lambda _rt: USER_NS)
    result = backend.write("/memories/preferences.md", "Prefers concise coding prompts.")
    assert result.error is None
    assert result.path == "/memories/preferences.md"


def test_policy_backend_rejects_secret_edit() -> None:
    backend = PolicyStoreBackend(store=InMemoryStore(), namespace=lambda _rt: USER_NS)
    ok = backend.write("/memories/profile.md", "Likes short answers.")
    assert ok.error is None
    rejected = backend.edit("/memories/profile.md", "short", "api_key=abcdefgh12345678")
    assert rejected.error is not None
    assert "policy" in rejected.error


async def test_policy_backend_rejects_secret_awrite() -> None:
    backend = PolicyStoreBackend(store=InMemoryStore(), namespace=lambda _rt: USER_NS)
    result = await backend.awrite("/memories/profile.md", "otp = 123456")
    assert result.error is not None

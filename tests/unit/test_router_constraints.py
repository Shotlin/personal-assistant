"""Router contract for pre-approved app denials; memory loading is separate.

A denial narrows routing, including the implied Calculator/Chrome app
used by arithmetic/search. This is not an OS permission enforcement layer.
"""

from __future__ import annotations

import pytest

from assistant.runtime.router import match_local_command


def test_denied_app_is_never_routed() -> None:
    ctx = {"denied_apps": frozenset({"terminal"})}
    assert match_local_command("Open Terminal", ctx) is None
    assert match_local_command("open terminal", ctx) is None
    # Unrelated apps are unaffected: constraints narrow, never widen.
    assert match_local_command("Open Chrome", ctx) is not None


def test_router_without_context_still_matches() -> None:
    assert match_local_command("Open Terminal", None) is not None


def test_empty_denial_set_is_a_no_op() -> None:
    ctx: dict[str, object] = {"denied_apps": frozenset()}
    assert match_local_command("Open Terminal", ctx) is not None


@pytest.mark.parametrize("text, app", [
    ("calculate 6*7", "calculator"),
    ("search for weather", "chrome"),
])
def test_denied_implicit_app_is_not_routed(text: str, app: str) -> None:
    assert match_local_command(text, {"denied_apps": frozenset({app})}) is None
    assert match_local_command(text, {"denied_apps": frozenset({"terminal"})}) is not None


@pytest.mark.parametrize("denied", [None, "terminal", 1, [None], {"terminal": True}])
def test_malformed_denials_fail_closed(denied: object) -> None:
    assert match_local_command("Open Terminal", {"denied_apps": denied}) is None

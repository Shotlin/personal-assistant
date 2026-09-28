"""Shared live-acceptance gating for the Phase 1 E2E suites (C01/N12).

The launcher (scripts/verify_phase1.py) is the only caller of these suites.
The contract each case implements:

1. Without a launcher-propagated authorization the case is SKIPPED with an
   explicit reason; the launcher maps any skip in a live suite to BLOCKED,
   never PASS.
2. With an authorization present, the case FIRST asserts its real scope:
   the validated config must name the fixture surfaces this case needs and
   the fixture roots must exist. A wrong or incomplete authorization FAILS
   loudly instead of silently skipping.
3. Only then, if the physical harness this case needs (real driver, real
   audio device, installed bundle) is not available in this snapshot, the
   case skips as an explicit BLOCKED naming the missing capability and the
   authorization id. An acceptance question the harness cannot answer is
   never reported as answered.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def authorized_config(*scope_assertions: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Return the validated live config, or skip as an explicit BLOCKED.

    Each ``scope_assertions`` callable receives the parsed config and must
    raise AssertionError when the authorization does not actually cover the
    case's needs (wrong fixture app, no audio grant, missing bundle...).
    """
    if os.environ.get("PHASE1_LIVE_AUTHORIZED") != "1":
        pytest.skip(_LIVE_REASON)
    config_path = os.environ.get("PHASE1_APPROVED_TEST_CONFIG")
    if not config_path:
        pytest.skip("approved-test-config path missing from the runner environment")
    path = Path(config_path)
    if not path.exists():
        pytest.fail(f"authorized run points at a missing config: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"authorized config is not valid JSON: {exc}")
    if not isinstance(config, dict):
        pytest.fail("authorized config must be a JSON object")
    for assertion in scope_assertions:
        assertion(config)
    _fixture_roots_exist(config)
    return config


def blocked_until_harness(config: dict[str, Any], capability: str) -> None:
    """The honest BLOCKED when the physical harness is not connected yet.

    The authorization is valid and the scope is verified, but the physical
    acceptance action (real driver dispatch, audible playback, installed
    bundle launch) has no connected harness in this snapshot. Skipping here
    reports BLOCKED through the launcher; it never reports the case passed.
    """
    pytest.skip(
        f"BLOCKED: live harness for {capability} is not connected in this "
        f"snapshot; authorization {config.get('authorization_id', '?')!r} is "
        "valid but no physical acceptance action was performed"
    )


def _fixture_roots_exist(config: dict[str, Any]) -> None:
    for field in ("data_root", "artifact_root"):
        root = Path(str(config.get(field, ""))).expanduser()
        if not root.is_dir():
            pytest.fail(
                f"authorized config {field} is not an existing fixture "
                f"directory: {root}"
            )


_LIVE_REASON = (
    "live acceptance authorization required: owner-issued "
    "approved-test-config.json (exact fixture app/window/account scope, "
    "explicit call budget) plus --allow-live, via scripts/verify_phase1.py. "
    "No production accounts."
)

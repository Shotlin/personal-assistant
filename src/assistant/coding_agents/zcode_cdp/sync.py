"""Notice when the account in ZCode changes, and make Sani's picture follow it.

Identity is what ZCode shows: the signed-in display name and the set of plans. Signals, cheapest
first: (a) the names (never the values) and modification time of ZCode's credentials file,
(b) the account label in the window while Sani has it open, (c) a different set of plans after a
read. On a change Sani drops every cached account, balance, model and task mapping that belonged
to the old account, keeps a plain record for the banner ("Account changed: A -> B"), and writes
an audit line. The next run re-reads the window and re-confirms the plan guard anyway.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

IDENTITY_KEY = "zcode_identity"
CHANGE_KEY = "zcode_account_change"
AUDIT_KEY = "zcode_audit"
STALE_KEY = "zcode_stale"
#: What belonged to the previous account and must not be shown or reused after a change.
CACHED_KEYS = ("zcode_account", "zcode_balances", "zcode_models", "zcode_sessions")
AUDIT_LIMIT = 50


class SyncStore(Protocol):
    async def get_state(self, key: str) -> dict[str, Any]: ...

    async def put_state(self, key: str, value: dict[str, Any]) -> None: ...

    async def delete_state(self, key: str) -> None: ...

    async def forget_prefix(self, prefix: str) -> int: ...


def credentials_path() -> Path:
    home = os.environ.get("ZCODE_HOME") or Path.home() / ".zcode"
    return Path(home).expanduser() / "v2" / "credentials.json"


def credential_signal(path: Path | None = None) -> tuple[str, float]:
    """``(fingerprint, modified_time)`` from the credential NAMES and the file time only.

    Values are never kept: the file is parsed for its key names and the content dropped at once.
    ``("", 0)`` when the file is missing or unreadable.
    """
    target = path or credentials_path()
    try:
        modified = target.stat().st_mtime
        names = sorted(str(name) for name in json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return "", 0.0
    digest = hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]
    return f"{digest}:{int(modified)}", modified


class AccountSync:
    def __init__(self, store: SyncStore | None, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    async def _invalidate(self, *, keep_account: bool = False) -> None:
        if self._store is None:
            return
        for key in CACHED_KEYS:
            if keep_account and key == "zcode_account":
                continue
            await self._store.delete_state(key)
        await self._store.forget_prefix("zcode:")

    async def _audit(self, line: str) -> None:
        if self._store is None:
            return
        trail = list((await self._store.get_state(AUDIT_KEY)).get("lines", []))
        trail.append({"at": self._clock(), "line": line})
        await self._store.put_state(AUDIT_KEY, {"lines": trail[-AUDIT_LIMIT:]})

    async def observe(
        self,
        *,
        signed_in: bool,
        name: str,
        plans: list[str] | None,
        fingerprint: str = "",
        source: str = "read",
    ) -> dict[str, Any] | None:
        """Compare what ZCode shows now with what Sani last knew; return the change, if any.

        ``plans=None`` means the plans were not read this time (so they cannot count as changed).
        """
        if self._store is None:
            return None
        before = await self._store.get_state(IDENTITY_KEY)
        now = self._clock()
        plan_set = sorted(plans) if plans is not None else list(before.get("plans", []))
        current = {
            "signed_in": signed_in,
            "name": name if signed_in else "",
            "plans": plan_set if signed_in else [],
            "fingerprint": fingerprint or before.get("fingerprint", ""),
            "as_of": now,
        }
        change: dict[str, Any] | None = None
        if before:
            was_in = bool(before.get("signed_in"))
            if was_in and not signed_in:
                kind = "signed_out"
            elif not was_in and signed_in:
                kind = "signed_in"
            elif signed_in and before.get("name") != name:
                kind = "switched"
            elif signed_in and plans is not None and before.get("plans") != plan_set:
                kind = "plans_changed"
            else:
                kind = ""
            if kind:
                change = {
                    "kind": kind,
                    "from": before.get("name", ""),
                    "to": current["name"],
                    "plans_added": sorted(set(plan_set) - set(before.get("plans", []))),
                    "plans_removed": sorted(set(before.get("plans", [])) - set(plan_set)),
                    "at": now,
                    "source": source,
                    "seen": False,
                }
        if change is not None and change["kind"] == "plans_changed":
            # The plans of a just-noticed switch arrive with the read that follows it: fold them
            # into that change instead of replacing the banner.
            prior = await self._store.get_state(CHANGE_KEY)
            if (
                prior
                and not prior.get("seen")
                and prior.get("kind") in {"switched", "signed_in"}
                and prior.get("to") == name
            ):
                change = {
                    **prior,
                    "plans_added": change["plans_added"],
                    "plans_removed": change["plans_removed"],
                }
        if change is not None:
            await self._invalidate(keep_account=change["kind"] in {"plans_changed"})
            await self._store.put_state(CHANGE_KEY, change)
            await self._audit(_describe(change))
        await self._store.put_state(IDENTITY_KEY, current)
        await self._store.delete_state(STALE_KEY)
        return change

    # -- the cheap signal --------------------------------------------------------------

    async def signal(self, fingerprint: str, modified: float) -> bool:
        """Mark everything stale when the credentials file changed since the last read.

        True the first time a given change is noticed. Nothing is deleted here; the read that
        follows decides what really changed.
        """
        if self._store is None or not fingerprint:
            return False
        known = (await self._store.get_state(IDENTITY_KEY)).get("fingerprint", "")
        if not known or known == fingerprint:
            return False
        stale = await self._store.get_state(STALE_KEY)
        if stale.get("fingerprint") == fingerprint:
            return False
        await self._store.put_state(
            STALE_KEY, {"fingerprint": fingerprint, "since": self._clock(), "modified": modified}
        )
        await self._audit("ZCode's sign-in file changed; data marked out of date")
        return True

    async def stale(self) -> dict[str, Any]:
        if self._store is None:
            return {}
        return await self._store.get_state(STALE_KEY)

    async def view(self) -> dict[str, Any]:
        """For the status payload: the last change (until dismissed) and whether data is stale."""
        if self._store is None:
            return {"change": None, "stale": False}
        change = await self._store.get_state(CHANGE_KEY)
        stale = await self._store.get_state(STALE_KEY)
        return {
            "change": change if change and not change.get("seen") else None,
            "stale": bool(stale),
        }

    async def acknowledge(self) -> None:
        if self._store is None:
            return
        change = await self._store.get_state(CHANGE_KEY)
        if change:
            await self._store.put_state(CHANGE_KEY, {**change, "seen": True})


def _describe(change: dict[str, Any]) -> str:
    kind = change["kind"]
    if kind == "switched":
        return f"Account changed: {change['from'] or '?'} -> {change['to'] or '?'}"
    if kind == "signed_out":
        return f"Signed out in ZCode (was {change['from'] or '?'})"
    if kind == "signed_in":
        return f"Signed in to ZCode as {change['to'] or '?'}"
    added = ", ".join(change["plans_added"]) or "none"
    removed = ", ".join(change["plans_removed"]) or "none"
    return f"Plans changed (added: {added}; removed: {removed})"


def plan_names(balances: dict[str, Any]) -> list[str]:
    return sorted({str(i.get("plan", "")) for i in balances.get("items", []) if i.get("plan")})

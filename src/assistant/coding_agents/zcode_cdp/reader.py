"""Read ZCode's real state through its own window, then close the port again.

One call, one pass: start ZCode with a private port, check the contract, read account,
models, balances and sessions, always put ZCode back to a normal launch. Read-only (S2).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from assistant.coding_agents.zcode_cdp.client import CdpClient, CdpError, find_page_url
from assistant.coding_agents.zcode_cdp.contract import ContractResult, wait_for_contract
from assistant.coding_agents.zcode_cdp.launcher import ZCodeLauncher
from assistant.coding_agents.zcode_cdp.pages import PageAdapter


class StateStore(Protocol):
    async def put_state(self, key: str, value: dict[str, Any]) -> None: ...


OpenPage = Callable[[int], Awaitable[CdpClient]]


async def open_page(port: int) -> CdpClient:
    return await CdpClient.open(await find_page_url(port))


@dataclass
class Snapshot:
    as_of: float
    refusal: str = ""
    contract: dict[str, Any] = field(default_factory=dict)
    account: dict[str, Any] = field(default_factory=dict)
    models: dict[str, Any] = field(default_factory=dict)
    balances: dict[str, Any] = field(default_factory=dict)
    sessions: dict[str, Any] = field(default_factory=dict)
    port_closed: bool = False
    #: Parts that could not be read, each with a plain reason (never a guessed value).
    not_read: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "refusal": self.refusal,
            "contract": self.contract,
            "account": self.account,
            "models": self.models,
            "balances": self.balances,
            "sessions": self.sessions,
            "port_closed": self.port_closed,
            "not_read": self.not_read,
        }


async def read_with_adapter(adapter: PageAdapter, version: str, snap: Snapshot) -> Snapshot:
    """Contract check, then read account, models, balances and sessions from an open window."""
    contract: ContractResult = await wait_for_contract(adapter, version)
    snap.contract = contract.as_dict()
    snap.refusal = contract.refusal()
    if snap.refusal:
        return snap
    snap.account = await adapter.account()
    if not snap.account.get("signed_in"):
        snap.refusal = "ZCode is signed out. Open ZCode and sign in; Sani never types credentials."
        return snap
    for name, read in (
        ("models", adapter.models),
        ("balances", adapter.balances),
        ("sessions", adapter.sessions),
    ):
        try:
            setattr(snap, name, await read())
        except CdpError as exc:
            snap.not_read[name] = f"not read: {exc}"
    return snap


async def read_snapshot(
    launcher: ZCodeLauncher,
    *,
    consent: bool,
    opener: OpenPage = open_page,
    clock: Callable[[], float] = time.time,
) -> Snapshot:
    """Quit ZCode (if open), relaunch with a private port, read, relaunch normally.

    ``consent`` must be the user's yes to closing and reopening ZCode; without it nothing
    is touched. ZCode is always reopened normally, even when a read fails.
    """
    snap = Snapshot(as_of=clock())
    if not consent:
        snap.refusal = "Reading ZCode needs it to be closed and reopened; no go-ahead was given."
        return snap
    if not launcher.installed():
        snap.refusal = "ZCode is not installed."
        return snap
    was_running = await launcher.running()
    if was_running and not await launcher.quit():
        snap.refusal = (
            "ZCode did not close (it may be asking about unsaved work). Nothing was read."
        )
        return snap
    client: CdpClient | None = None
    try:
        port = await launcher.start_with_port()
        version = await launcher.version()
        client = await opener(port)
        await read_with_adapter(PageAdapter(client), version, snap)
    except CdpError as exc:
        snap.refusal = str(exc)
    finally:
        if client is not None:
            await client.close()
        if launcher.port or await launcher.running():
            snap.port_closed = await launcher.restore_normal()
        else:
            snap.port_closed = True
    return snap


async def save_snapshot(store: StateStore, snap: Snapshot) -> None:
    """Keep what was read in Sani's own state, each part with its own ``as_of``.

    Account and balances use the keys Settings already reads. A part that was not read is
    left as it was: stale is better labelled than overwritten with nothing.
    """
    await store.put_state("zcode_contract", {**snap.contract, "as_of": snap.as_of})
    if snap.account.get("signed_in"):
        await store.put_state(
            "zcode_account", {"name": snap.account["name"], "email": "", "as_of": snap.as_of}
        )
    if snap.balances.get("items"):
        await store.put_state(
            "zcode_balances",
            {
                "as_of": snap.as_of,
                "items": snap.balances["items"],
                "not_read": snap.balances.get("not_read", []),
            },
        )
    if snap.models.get("models"):
        await store.put_state("zcode_models", {**snap.models, "as_of": snap.as_of})
    if snap.sessions:
        await store.put_state("zcode_sessions", {**snap.sessions, "as_of": snap.as_of})


class StateReader(Protocol):
    async def get_state(self, key: str) -> dict[str, Any]: ...


async def saved_overview(store: StateReader | None) -> dict[str, Any] | None:
    """The stored result of the last read, for the status payload; None if never read.

    Every part carries its own ``as_of`` so the UI can say how old it is.
    """
    if store is None:
        return None
    contract = await store.get_state("zcode_contract")
    if not contract:
        return None
    models = await store.get_state("zcode_models")
    sessions = await store.get_state("zcode_sessions")
    last_run = await store.get_state("zcode_last_run")
    return {
        "contract": contract,
        "models": models or None,
        "sessions": sessions or None,
        "last_run": last_run or None,
    }

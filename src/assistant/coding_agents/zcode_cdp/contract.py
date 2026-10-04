"""The selector contract: what must be true of ZCode's page before Sani reads or clicks.

Run on every connect. A failing contract, or an app version nobody has verified, disables the
backend with a plain reason: Sani never clicks on a guess (N3). The result is stored with the
version so the user can see what was last confirmed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from assistant.coding_agents.zcode_cdp.pages import REQUIRED, PageAdapter

#: App versions whose markup was mapped by hand (docs/verification/zcode-cdp/discovery.md).
VERIFIED_VERSIONS = frozenset({"3.14.4"})


@dataclass
class ContractResult:
    version: str
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def missing(self) -> list[str]:
        return sorted(name for name, ok in self.checks.items() if not ok)

    @property
    def version_verified(self) -> bool:
        return self.version in VERIFIED_VERSIONS

    @property
    def ok(self) -> bool:
        return bool(self.checks) and not self.missing

    def refusal(self) -> str:
        """Plain words for why Sani must not use the page; empty when it may."""
        if not self.ok:
            parts = ", ".join(self.missing) or "everything"
            return (
                "ZCode's window does not look the way Sani expects "
                f"(missing: {parts}). It may be signed out, on another screen, or updated."
            )
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "version_verified": self.version_verified,
            "ok": self.ok,
            "missing": self.missing,
            "checks": self.checks,
        }


async def check_contract(adapter: PageAdapter, version: str) -> ContractResult:
    present = await adapter.present()
    return ContractResult(
        version=version, checks={name: bool(present.get(name)) for name in REQUIRED}
    )


async def wait_for_contract(
    adapter: PageAdapter,
    version: str,
    *,
    seconds: float = 40.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ContractResult:
    """The window exists before the app has drawn itself: re-check until it passes or time is up."""
    waited = 0.0
    while True:
        result = await check_contract(adapter, version)
        if result.ok or waited >= seconds:
            return result
        await sleep(1.0)
        waited += 1.0

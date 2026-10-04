"""ZCode through its own window: the backend for ``ZCODE_MODE=window``.

Same shape as ``ZCodeBackend`` (so the toolkit's folders, run limit, sessions and summary are
shared) but the work happens in the user's real ZCode app, signed in with their own Z.ai
account, driven through its debug port. See docs/zcode-cdp-integration-plan-2026-10-04.md.
"""

from __future__ import annotations

import plistlib
import time
from pathlib import Path
from typing import Any

from assistant.claude_code.locate import ClaudeStatus
from assistant.coding_agents.backend import Preflight
from assistant.coding_agents.guide import for_backend
from assistant.coding_agents.zcode import ZCodeBackend, app_signed_in
from assistant.coding_agents.zcode_cdp.choice import pick_model
from assistant.coding_agents.zcode_cdp.control import ControlRefused, ControlSession
from assistant.coding_agents.zcode_cdp.launcher import APP_PATH
from assistant.coding_agents.zcode_cdp.sync import STALE_KEY, AccountSync
from assistant.coding_agents.zcode_model import describe, is_zai_plan
from assistant.settings import Settings

WINDOW_GUIDE = """
Coding with {name} ({name} is the user's own {name} desktop app, on their own Z.ai plan):
- Call the `{tool}` tool for software work in a project folder the user allowed. Write ONE complete
  request: goal, where, constraints, and how to check it. It runs in ZCode's real window.
- The tool refuses, spending nothing, when ZCode control is off, ZCode is signed out, the model
  is not on the user's Z.ai plan, or the user is using ZCode. Report the reason in plain words.
- Sani decides every permission ZCode asks for by the user's run limit and never approves
  "always allow" or "full access". Commands only run when the limit is "run".
- The files listed as changed come from the disk, not from ZCode. A "Check:" line means ZCode's
  account of its work and the disk disagree: say so. Anything ZCode says it ran or tested without
  captured output is its own claim.
- Judge the result; at most one corrected follow-up. Never paste long output.
"""


class ZCodeWindowBackend(ZCodeBackend):
    sign_in_hint = (
        "ZCode isn't signed in in the ZCode app. Ask the user to open ZCode and sign in there; "
        "Sani never types credentials."
    )

    def __init__(self, data_dir: str = "", control: Any = None) -> None:
        super().__init__(data_dir)
        self.control = control or ControlSession()
        self._store: Any = None

    def bind_store(self, store: object) -> None:
        self.control.bind_store(store)  # type: ignore[arg-type]
        self._store = store
        if getattr(self.control, "on_account", None) is None and hasattr(
            self.control, "bind_store"
        ):
            self.control.on_account = self._on_account

    async def _on_account(self, account: dict[str, Any]) -> None:
        """The account label in the open window changed: record it and ask for a fresh read."""
        store: Any = getattr(self, "_store", None)
        if store is None:
            return
        change = await AccountSync(store).observe(
            signed_in=bool(account.get("signed_in")),
            name=str(account.get("name") or ""),
            plans=None,
            source="window",
        )
        if change is not None:
            await store.put_state(
                STALE_KEY, {"fingerprint": "window", "since": time.time(), "modified": 0}
            )

    def locate(self, override: str) -> Path | None:
        return APP_PATH if APP_PATH.exists() else None

    async def read_status(self, binary: Path | None) -> ClaudeStatus:
        if binary is None:
            return ClaudeStatus(installed=False, detail="ZCode isn't installed.")
        version = ""
        try:
            info = plistlib.loads((binary / "Contents" / "Info.plist").read_bytes())
            version = str(info.get("CFBundleShortVersionString", ""))
        except (OSError, plistlib.InvalidFileException):
            pass
        signed_in = app_signed_in()
        return ClaudeStatus(
            installed=True,
            path=str(binary),
            version=version,
            signed_in=signed_in,
            auth_method="z.ai (ZCode app)" if signed_in else "",
            detail="" if signed_in else self.sign_in_hint,
        )

    async def selection(self) -> dict[str, str]:
        """The plan and model the user picked in Sani's settings (may be empty)."""
        if self._store is None:
            return {}
        saved = await self._store.get_state("zcode_selection")
        return {k: str(v) for k, v in saved.items() if k in {"provider", "model"}}

    async def preflight(self, binary: Path, workspace: Path, settings: Settings) -> Preflight:
        """Before anything is spent: window readable, signed in, and on a Z.ai plan model."""
        try:
            async with self.control.session() as driver:
                info = await driver.model_info()
        except ControlRefused as refused:
            return Preflight(refusal=str(refused))
        except Exception as problem:  # the window failing must never become a guess
            return Preflight(refusal=f"I could not read ZCode's window ({type(problem).__name__}).")
        allow_any = settings.zcode_allowed_providers == "any"
        want, want_plan = settings.zcode_cli_model.strip(), ""
        if not want:
            chosen = await self.selection()
            want, want_plan = chosen.get("model", ""), chosen.get("provider", "")
        if want:  # Sani will switch ZCode to this model; it must be offered, and on the plan
            entry, error = pick_model(info["offered"], want, want_plan, allow_any=allow_any)
            if entry is None:
                return Preflight(refusal=error)
            model, plan_id, plan = entry["model"], entry["plan_id"], entry["plan"]
        else:
            model, plan_id, plan = info["model"], info["plan_id"], info["plan"]
        if not model or not plan_id:
            return Preflight(
                refusal=(
                    "I could not confirm which model and plan ZCode will use, so nothing was run."
                )
            )
        if not allow_any and not is_zai_plan(plan_id):
            return Preflight(
                refusal=(
                    f"ZCode is set to {model} on {plan_id}, {describe(plan_id)}, not your Z.ai "
                    "plan, so I did not run it and nothing was spent. Pick a Z.ai plan model in "
                    "the ZCode app, or allow custom providers on purpose with "
                    "ZCODE_ALLOWED_PROVIDERS=any."
                )
            )
        return Preflight(note=f"Model: {model} ({plan or plan_id}, {describe(plan_id)})")

    def guide(self) -> str:
        return for_backend(
            WINDOW_GUIDE.format(name=self.name, tool=self.tool_name), self.name, self.tool_name
        )

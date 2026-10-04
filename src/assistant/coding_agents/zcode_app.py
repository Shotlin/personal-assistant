"""Drive the ZCode desktop app with Sani's computer control.

ZCode's command line cannot see the user's Start Plan (only the desktop app can),
so the Deep Agent uses the ZCode *app* the way a person would, through the
bounded computer-control tools it already has (``dev.zcode.app`` is on the
allowlist in ``config/cua-capabilities.yaml``). This module adds only what a
screen-driving model cannot know by itself:

- ``zcode_choice``: the plan, model and run limit the user picked in Sani, and
  the folders ZCode may work in.
- ``zcode_save_balances``: where the agent stores the plan balances it read off
  ZCode's own screen, so Settings can show real numbers and when they were read.

Balances are only ever what ZCode displayed; Sani never calls Z.ai for them and
never touches ZCode's sign-in token.
"""

from __future__ import annotations

import re
import time
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from assistant.claude_code.redact import screen

BUNDLE_ID = "dev.zcode.app"
_MAX_ITEMS = 24
_ZCODE_MODE_NAMES = {
    "read": "Plan mode (look only, no edits)",
    "edit": "Edit automatically (no shell commands)",
    "run": "Full access (edit files and run commands)",
}

APP_GUIDE = """
Coding with ZCode (the user's ZCode desktop app and their Start Plan): there is no coding
command here. You drive the ZCode app on screen, like a person, with the computer-control tools.
- First call `zcode_choice` to get the plan and model the user picked, the most ZCode may do
  and the folders it may work in. Only open projects inside those folders.
- launch_app with bundle id dev.zcode.app, bring_to_front, then get_window_state to read it. If
  the window is not found or the app asks to sign in, stop and tell the user.
- Before sending, check the model picker under the prompt box shows the chosen model and set the
  permission mode to the one `zcode_choice` names. If the picker cannot show it, say so plainly.
- Send the task as ONE complete request (goal, where, constraints, how to check it). Type it in the
  prompt box (set_value, or click then type_text) and send it (Enter or the send button).
- ZCode is working while a Stop/Cancel control is visible. Poll with get_window_state about every
  5 seconds. It is done when that control is gone and the last assistant message stops changing.
  Do not poll forever: if nothing changes for about 3 minutes, say it looks stuck and stop.
- If ZCode asks a permission or a question, do not approve commands the user's limit does not
  allow; report what it asked. Never type secrets, tokens or API keys into ZCode.
- Read the final answer and report in plain words: what changed, whether it worked, what is next.
  Never paste long output.
- To find out which account ZCode is signed in as, look at ZCode's account menu or Settings
  (read-only), then call `zcode_save_account` with the name and email exactly as shown.
- When the user asks how many tokens are left, open the ZCode app's Settings -> providers, open the
  plan (for example Start Plan), read the "Today's balance" figures exactly as shown, then call
  `zcode_save_balances` with what you read. Report those numbers; never estimate any.
"""


class BalanceItem(BaseModel):
    plan: str = Field(description="Plan name exactly as ZCode shows it, e.g. 'Start Plan'.")
    model: str = Field(description="Model name as shown, e.g. 'GLM-5.3-Flash'.")
    remaining: int = Field(ge=0, description="Tokens left today, as a whole number.")
    total: int = Field(ge=0, description="Tokens in the daily allowance, as a whole number.")
    expires: str = Field(default="", description="Expiry text as shown, e.g. 'Oct 4, 21:29'.")
    family: str = Field(default="Z.ai", description="'Z.ai' or 'BigModel (China)'.")


class SaveAccountArgs(BaseModel):
    name: str = Field(default="", description="Display name shown for the signed-in account.")
    email: str = Field(default="", description="Email or username shown for the account.")


class SaveBalancesArgs(BaseModel):
    items: list[BalanceItem] = Field(description="Every balance figure read, one per model.")


def _clean_item(item: BalanceItem, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match the plan name to a catalog plan; a figure that cannot be placed is dropped."""
    if item.total <= 0 or item.remaining > item.total:
        return None
    wanted = item.plan.strip().lower()
    family = item.family.strip().lower()
    for plan in catalog:
        if str(plan["name"]).lower() != wanted:
            continue
        if family and family not in str(plan.get("family_name", "")).lower():
            continue
        if any(m["id"].lower() == item.model.strip().lower() for m in plan["models"]):
            model = next(
                m["id"] for m in plan["models"] if m["id"].lower() == item.model.strip().lower()
            )
            return {
                "provider": plan["id"],
                "plan": plan["name"],
                "model": model,
                "remaining": item.remaining,
                "total": item.total,
                "expires": screen(item.expires, limit=40),
            }
    return None


def build_tools(toolkit: Any) -> list[BaseTool]:
    """The two helper tools, bound to one ZCode toolkit (its store, folders, ceiling)."""

    async def zcode_choice() -> str:
        selection = await toolkit._selection()
        catalog = toolkit._backend.extra_status(
            toolkit._locator(override=toolkit._opts.binary)
        ).get("catalog", [])
        plan_name = ""
        for plan in catalog if isinstance(catalog, list) else []:
            if plan.get("id") == selection.get("provider"):
                plan_name = f"{plan['name']} ({plan.get('family_name', '')})"
        folders = [str(root) for root in toolkit.allowed_dirs()]
        ceiling = toolkit._opts.permission
        lines = [
            "Plan: "
            + (plan_name or "none picked in Sani yet (use whatever the ZCode app has selected)"),
            f"Model: {selection.get('model') or 'none picked in Sani yet'}",
            f"The most ZCode may do: {_ZCODE_MODE_NAMES.get(ceiling, ceiling)}",
            "Folders ZCode may work in: " + (", ".join(folders) if folders else "none set up yet"),
        ]
        if not folders:
            lines.append("Do not start: ask the user to add a project folder in Settings.")
        return "\n".join(lines)

    async def zcode_save_balances(items: list[BalanceItem]) -> str:
        if toolkit._store is None:
            return "Nowhere to save the balances."
        catalog = toolkit._backend.extra_status(
            toolkit._locator(override=toolkit._opts.binary)
        ).get("catalog", [])
        kept: list[dict[str, Any]] = []
        dropped = 0
        for item in items[:_MAX_ITEMS]:
            cleaned = _clean_item(item, catalog if isinstance(catalog, list) else [])
            if cleaned is None:
                dropped += 1
            else:
                kept.append(cleaned)
        if not kept:
            return "Nothing saved: none of those figures matched a known plan and model."
        await toolkit._store.put_state("zcode_balances", {"as_of": time.time(), "items": kept})
        note = f" ({dropped} figure(s) did not match a known plan and model and were skipped)"
        return f"Saved {len(kept)} balance figure(s)." + (note if dropped else "")

    async def zcode_save_account(name: str = "", email: str = "") -> str:
        if toolkit._store is None:
            return "Nowhere to save the account."
        name = screen(name.strip(), limit=80)
        shown = screen(email.strip(), limit=120)
        if not name and not shown:
            return "Nothing saved: no name or email was given."
        if shown and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+|[A-Za-z0-9._\-]{2,60}", shown):
            return "Nothing saved: that does not look like an email or username."
        await toolkit._store.put_state(
            "zcode_account", {"name": name, "email": shown, "as_of": time.time()}
        )
        return "Saved the account."

    return [
        StructuredTool.from_function(
            coroutine=zcode_save_account,
            name="zcode_save_account",
            description=(
                "Save the name and email (or username) of the account ZCode is signed in as, "
                "exactly as ZCode's own screen shows it, so Sani's Settings can display it."
            ),
            args_schema=SaveAccountArgs,
        ),
        StructuredTool.from_function(
            coroutine=zcode_choice,
            name="zcode_choice",
            description=(
                "The plan, model, run limit and project folders the user chose in Sani for ZCode. "
                "Call this before driving the ZCode app."
            ),
        ),
        StructuredTool.from_function(
            coroutine=zcode_save_balances,
            name="zcode_save_balances",
            description=(
                "Save the plan balances you just read on ZCode's own screen so Sani's Settings can "
                "show them. Only pass figures exactly as displayed."
            ),
            args_schema=SaveBalancesArgs,
        ),
    ]


async def saved_account(toolkit: Any) -> dict[str, Any] | None:
    """The account the agent last read off ZCode's screen, or None if never."""
    if toolkit._store is None:
        return None
    saved = await toolkit._store.get_state("zcode_account")
    if not (saved.get("name") or saved.get("email")):
        return None
    return {k: saved.get(k) for k in ("name", "email", "as_of")}


async def saved_balances(toolkit: Any) -> dict[str, Any] | None:
    """What the agent last read from ZCode's screen, or None if never."""
    if toolkit._store is None:
        return None
    saved = await toolkit._store.get_state("zcode_balances")
    if not saved.get("items"):
        return None
    return {"as_of": saved.get("as_of"), "items": saved["items"]}

"""Verbs that drive ZCode's window for a run: pick the project, set the mode, send, stop, answer.

Every verb checks its own effect on the page before returning (the mode really changed, the
prompt really became a conversation row, the card really went away). When it cannot confirm,
it raises and the run stops: Sani never clicks on a guess.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from assistant.coding_agents.zcode_cdp.choice import model_testid
from assistant.coding_agents.zcode_cdp.client import CdpError
from assistant.coding_agents.zcode_cdp.pages import (
    JS_MARK_SHOW_MORE,
    JS_SESSIONS,
    MODE_TRIGGER,
    MODEL_TRIGGER,
    MORE_SELECTOR,
    REASONING_TRIGGER,
    PageAdapter,
)
from assistant.coding_agents.zcode_cdp.rows import state_script


class Window(Protocol):
    async def evaluate(self, expression: str, *, timeout: float = ...) -> Any: ...

    async def click(self, selector: str) -> None: ...

    async def press(self, key: str) -> None: ...

    async def type_text(self, text: str) -> None: ...


NEW_TASK = "[data-testid=task-new-button]"
WORKSPACE_TRIGGER = "[data-testid=composer-workspace-trigger]"
COMPOSER = "[data-testid=v4-composer-input]"
SEND = "[data-testid=v4-composer-send]"
STOP = "[data-testid=v4-stop]"
CARD = "[role=listbox][aria-label='Permission required']"

#: The mode Sani always sets: every change or command then arrives as a card Sani answers by
#: the run limit. "Edit automatically" and "Full access" would skip Sani's own checks.
MODE_ID = "build"
MODE_LABEL = "Ask before changes"

JS_WATCH = """(()=>{if(!window.__saniWatch){window.__saniWatch={last:Date.now(),mine:0};
 for(const t of ['keydown','mousedown','wheel','touchstart'])
  window.addEventListener(t,()=>{if(!window.__saniWatch.mine)window.__saniWatch.last=Date.now()},true)}
 return Date.now()-window.__saniWatch.last})()"""
JS_MINE = "(()=>{if(window.__saniWatch)window.__saniWatch.mine+=%d;return 1})()"

JS_WORKSPACES = """(()=>[...document.querySelectorAll('[data-testid^=workspace-item-]')].map(e=>{
 const lines=(e.innerText||'').split('\\n').map(s=>s.trim()).filter(Boolean);
 const id=e.getAttribute('data-testid');
 return {path:id.slice('workspace-item-'.length),name:lines[0]||''}}))()"""

JS_TAG_WORKSPACE_MENU = """(()=>{const want=__NAME__;
 const items=[...document.querySelectorAll('[role=menu] [role=menuitemcheckbox]')]
  .filter(e=>(e.innerText||'').trim()===want);
 if(items.length!==1)return items.length;
 items[0].setAttribute('data-sani-ws','1');return 1})()"""

JS_COMPOSER = """(()=>{const i=document.querySelector('[data-testid=v4-composer-input]');
 return {text:i?(i.innerText||'').trim():null,
  workspace:(document.querySelector('[data-testid=composer-workspace-trigger]')||{}).innerText||'',
  mode:(document.querySelector('[data-testid=chat-mode-select-trigger]')||{}).innerText||'',
  model:(document.querySelector('[data-testid=chat-model-select-trigger]')||{}).innerText||'',
  reasoning:(document.querySelector('[data-testid=chat-thought-level-select-trigger]')||{})
   .innerText||''}})()"""


def _norm(text: str) -> str:
    return " ".join(text.split())


class WindowDriver:
    def __init__(
        self,
        window: Window,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        settle: float = 0.6,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._w = window
        self._sleep = sleep
        self._settle = settle
        self._clock = clock
        self.pages = PageAdapter(window, sleep=sleep, settle=settle)  # type: ignore[arg-type]

    # -- looking ---------------------------------------------------------------------

    async def state(self, after_row: int = 0) -> dict[str, Any]:
        return dict(await self._w.evaluate(state_script(after_row)) or {})

    async def composer(self) -> dict[str, Any]:
        return dict(await self._w.evaluate(JS_COMPOSER) or {})

    async def model_info(self) -> dict[str, Any]:
        """The model ZCode will use and the plan it belongs to, as the app's own picker shows."""
        info = await self.pages.models()
        current = [m for m in info["models"] if m["current"]]
        return {
            "model": str(info.get("current_model") or ""),
            "plan_id": current[0]["plan_id"] if len(current) == 1 else "",
            "plan": current[0]["plan"] if len(current) == 1 else "",
            "offered": info["models"],
        }

    # -- the person at the other keyboard (N4) -----------------------------------------

    async def _acting(self, delta: int) -> None:
        await self._w.evaluate(JS_MINE % delta)

    async def idle_for(self) -> float:
        """Seconds since a person last typed or clicked in ZCode (since Sani started watching)."""
        return float(await self._w.evaluate(JS_WATCH)) / 1000.0

    async def wait_until_idle(self, *, quiet: float = 5.0, max_wait: float = 60.0) -> bool:
        waited = 0.0
        while True:
            if await self.idle_for() >= quiet:
                return True
            if waited >= max_wait:
                return False
            await self._sleep(1.0)
            waited += 1.0

    async def _click(self, selector: str) -> None:
        await self._acting(1)
        try:
            await self._w.click(selector)
        finally:
            await self._acting(-1)

    # -- doing -------------------------------------------------------------------------

    async def workspace_for(self, project_path: str) -> tuple[str, str]:
        """``(ZCode's name for the project, its folder)`` that contains this path.

        A folder inside a ZCode project maps to that project (the run is then limited to the
        subfolder by Sani). Refuses names that are not unique.
        """
        listed = [dict(w) for w in await self._w.evaluate(JS_WORKSPACES) or []]
        holders = [
            w
            for w in listed
            if project_path == w["path"]
            or project_path.startswith(str(w["path"]).rstrip("/") + "/")
        ]
        if not holders:
            raise CdpError(
                "That folder is not inside a ZCode project yet. Add its project in the ZCode app "
                "(Open folder); Sani cannot use the folder chooser."
            )
        holder = max(holders, key=lambda w: len(str(w["path"])))
        name = str(holder["name"])
        if sum(1 for w in listed if w["name"] == name) != 1:
            raise CdpError(f"Two ZCode projects are both called “{name}”; Sani will not guess.")
        return name, str(holder["path"])

    async def new_task(self, project_name: str) -> None:
        await self._click(NEW_TASK)
        await self._sleep(self._settle * 2)
        composer = await self.composer()
        if composer.get("text") is None:
            raise CdpError("ZCode did not show an empty prompt box for a new task")
        if composer.get("workspace", "").strip() != project_name:
            await self._click(WORKSPACE_TRIGGER)
            await self._sleep(self._settle)
            found = await self._w.evaluate(
                JS_TAG_WORKSPACE_MENU.replace("__NAME__", json.dumps(project_name))
            )
            if found != 1:
                await self._w.press("Escape")
                raise CdpError(f"ZCode's project list does not have exactly one “{project_name}”")
            await self._click("[data-sani-ws='1']")
            await self._sleep(self._settle)
            composer = await self.composer()
            if composer.get("workspace", "").strip() != project_name:
                raise CdpError(f"Could not switch ZCode to the project “{project_name}”")

    async def _task_present(self, selector: str) -> bool:
        return bool(await self._w.evaluate(f"!!document.querySelector({json.dumps(selector)})"))

    async def open_task(self, session_id: str, project_path: str = "") -> bool:
        """Open an existing task (to continue it). False if it cannot be found or confirmed.

        ZCode hides the tasks of a collapsed project and cuts each list short, so if the task is
        not showing, open its project and press "Show more" before giving up.
        """
        selector = f'[data-testid="task-item-{session_id}"]'
        if not await self._task_present(selector) and project_path:
            for entry in await self.sidebar_projects():
                if entry.get("path") == project_path and entry.get("expanded") == "false":
                    await self._click(
                        f"[data-testid={json.dumps('workspace-item-' + project_path)}]"
                    )
                    await self._sleep(self._settle)
            for _ in range(5):
                if await self._task_present(selector):
                    break
                if not await self._w.evaluate(JS_MARK_SHOW_MORE):
                    break
                await self._click(MORE_SELECTOR)
                await self._sleep(self._settle)
        if not await self._task_present(selector):
            return False
        await self._click(selector)
        await self._sleep(self._settle * 2)
        state = await self.state(0)
        return state.get("session_id") == session_id

    async def set_mode(self) -> None:
        """Put ZCode in "Ask before changes" and confirm the app now says so."""
        if MODE_LABEL in str((await self.composer()).get("mode", "")):
            return
        await self._click(MODE_TRIGGER)
        await self._sleep(self._settle)
        try:
            await self._click(f"[data-testid=chat-mode-select-item-{MODE_ID}]")
        finally:
            await self._sleep(self._settle)
        if MODE_LABEL not in str((await self.composer()).get("mode", "")):
            raise CdpError("Could not switch ZCode to “Ask before changes”")

    async def set_model(self, entry: dict[str, Any]) -> None:
        """Pick a model from ZCode's own picker and confirm the app now shows it."""
        if str(entry["model"]) == str((await self.composer()).get("model", "")).strip():
            return
        await self._click(MODEL_TRIGGER)
        await self._sleep(self._settle)
        try:
            await self._click(f"[data-testid={json.dumps(model_testid(entry))}]")
        finally:
            await self._sleep(self._settle)
        if str(entry["model"]) != str((await self.composer()).get("model", "")).strip():
            raise CdpError(f"Could not switch ZCode to {entry['model']}")

    async def set_reasoning(self, level: str) -> None:
        """Set ZCode's reasoning level (low, high or max) and confirm the app shows it."""
        if level.lower() == str((await self.composer()).get("reasoning", "")).strip().lower():
            return
        await self._click(REASONING_TRIGGER)
        await self._sleep(self._settle)
        try:
            await self._click(f"[data-testid=chat-thought-level-select-item-{level}]")
        finally:
            await self._sleep(self._settle)
        if level.lower() != str((await self.composer()).get("reasoning", "")).strip().lower():
            raise CdpError(f"Could not set ZCode's reasoning level to {level}")

    async def send(self, prompt: str, *, baseline: int = 0) -> None:
        """Type the prompt, press Send, and wait until it shows up as the user's message."""
        await self._click(COMPOSER)
        await self._sleep(0.3)
        await self._acting(1)
        try:
            await self._w.type_text(prompt)
        finally:
            await self._acting(-1)
        await self._sleep(0.4)
        typed = _norm(str((await self.composer()).get("text") or ""))
        if _norm(prompt)[:200] != typed[:200]:
            raise CdpError("ZCode's prompt box did not take the request as written")
        await self._click(SEND)
        head = _norm(prompt)[:60]
        for _ in range(30):
            await self._sleep(0.5)
            state = await self.state(baseline)
            for row in state.get("rows") or []:
                if (
                    row.get("kind") == "userInput"
                    and _norm(str(row.get("text") or ""))[:60] == head
                ):
                    return
        raise CdpError("ZCode did not accept the request (no new message appeared)")

    async def stop(self) -> None:
        await self._click(STOP)

    async def answer(self, option: str, *, waiting_on: tuple[str, ...]) -> None:
        """Click one option on the permission card (only ever "Allow" or "Deny").

        Done when the card is gone or is now about different calls (ZCode may queue several).
        """
        if option not in {"Allow", "Deny"}:
            raise CdpError(f"Sani never chooses “{option}” on a permission card")
        await self._click(f"{CARD} button[aria-label='{option}']")
        for _ in range(10):
            await self._sleep(0.3)
            state = await self.state(10**12)
            now = tuple(str(p.get("call")) for p in state.get("pending") or [])
            if not state.get("card") or now != waiting_on:
                return
        raise CdpError("ZCode's permission card did not change after the answer")

    async def sidebar_projects(self) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._w.evaluate(JS_SESSIONS) or []]

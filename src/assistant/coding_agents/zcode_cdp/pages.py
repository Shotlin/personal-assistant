"""The PageAdapter: every ZCode selector in one file, read-only verbs only (S2).

Scripts only read the page; the few clicks open and close menus (and the Settings page) that
ZCode itself renders. Nothing here sends a prompt, changes a setting, or touches a token.
Figures are exactly what ZCode displays; a figure that cannot be read is absent, never guessed.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import unquote

from assistant.claude_code.redact import screen


class Page(Protocol):
    async def evaluate(self, expression: str, *, timeout: float = ...) -> Any: ...

    async def click(self, selector: str) -> None: ...

    async def press(self, key: str) -> None: ...


# -- selectors (the only place ZCode's markup is named) -------------------------------

SIDEBAR = "[data-testid=sidebar]"
ACCOUNT = "[data-testid=login-trigger]"
SETTINGS_BUTTON = "[data-testid=task-settings-button]"
SETTINGS_MODELS = "[data-testid=settings-section-nav-modelProvider]"
PROVIDER_NAV = "[data-testid^=model-provider-nav-item-]"
MODEL_TRIGGER = "[data-testid=chat-model-select-trigger]"
MODE_TRIGGER = "[data-testid=chat-mode-select-trigger]"
REASONING_TRIGGER = "[data-testid=chat-thought-level-select-trigger]"
BALANCE_TRIGGER = "[data-testid=chat-context-usage-trigger]"

#: Controls that must exist for the page to be one Sani understands.
REQUIRED = {
    "sidebar": SIDEBAR,
    "account": ACCOUNT,
    "settings_button": SETTINGS_BUTTON,
    "workspace_list": "[data-testid=workspace-list]",
    "composer_input": "[data-testid=v4-composer-input]",
    "model_trigger": MODEL_TRIGGER,
    "mode_trigger": MODE_TRIGGER,
    "reasoning_trigger": REASONING_TRIGGER,
    "balance_trigger": BALANCE_TRIGGER,
    "send": "[data-testid=v4-composer-send]",
}

JS_PRESENT = (
    "(()=>{const r={};for(const [k,s] of Object.entries(__SELECTORS__))"
    "r[k]=!!document.querySelector(s);return r})()"
)

JS_STATUS = """(()=>{
 const q=s=>document.querySelector(s);
 const a=q('__ACCOUNT__');
 const label=e=>e?((e.getAttribute('aria-label')||e.innerText||'').trim()):'';
 return {account:label(a),model:label(q('__MODEL__')),
  reasoning:label(q('__REASONING__')),mode:label(q('__MODE__')),
  balance_open:!!q('__BALANCE__')};
})()"""
JS_STATUS = (
    JS_STATUS.replace("__ACCOUNT__", ACCOUNT)
    .replace("__MODEL__", MODEL_TRIGGER)
    .replace("__REASONING__", REASONING_TRIGGER)
    .replace("__MODE__", MODE_TRIGGER)
    .replace("__BALANCE__", BALANCE_TRIGGER)
)

JS_MODEL_ITEMS = """(()=>[...document.querySelectorAll(
 '[role=menu] [role=menuitemradio][data-testid^=chat-model-select-item-]')].map(i=>{
  const g=i.closest('[role=group]');let label='';
  if(g){const id=g.getAttribute('aria-labelledby');const l=id&&document.getElementById(id);
   label=l?l.innerText:(g.previousElementSibling?g.previousElementSibling.innerText:'')}
  return {testid:i.getAttribute('data-testid'),checked:i.getAttribute('aria-checked')==='true',
   text:(i.innerText||'').trim(),group:(label||'').trim()}}))()"""

JS_MODE_ITEMS = """(()=>[...document.querySelectorAll(
 '[role=menu] [data-testid^=chat-mode-select-item-]')].map(i=>({
  testid:i.getAttribute('data-testid'),
  checked:i.getAttribute('aria-checked')==='true',
  text:(i.innerText||'').trim()})))()"""

JS_REASONING_ITEMS = """(()=>[...document.querySelectorAll(
 '[role=listbox] [data-testid^=chat-thought-level-select-item-]')].map(i=>({
  testid:i.getAttribute('data-testid'),
  checked:i.getAttribute('aria-checked')==='true'||i.getAttribute('aria-selected')==='true',
  text:(i.innerText||'').trim()})))()"""

JS_PROVIDER_NAV = """(()=>[...document.querySelectorAll('__NAV__')].map(e=>({
  testid:e.getAttribute('data-testid'),text:(e.innerText||'').trim()})))()""".replace(
    "__NAV__", PROVIDER_NAV
)

JS_PROVIDER_PANEL = (
    "(()=>{let e=document.querySelector('__NAV__');"
    "while(e&&!e.querySelector('[data-testid=model-provider-add-provider-button]'))"
    "e=e.parentElement;return e?e.innerText:null})()"
).replace("__NAV__", PROVIDER_NAV)

JS_BACK = (
    "(()=>{const b=[...document.querySelectorAll('button')].find(x=>"
    "/^\\s*(Back to workspace|返回工作区)\\s*$/.test(x.innerText||''));"
    "if(!b)return false;b.setAttribute('data-sani-back','1');return true})()"
)
BACK_SELECTOR = "[data-sani-back='1']"

JS_SESSIONS = """(()=>{
 const out=[];let cur=null;
 for(const e of document.querySelectorAll(
   '[data-testid^=workspace-item-],[data-testid^=task-item-]')){
  const t=e.getAttribute('data-testid');
  if(t.startsWith('workspace-item-')){
   const lines=(e.innerText||'').split('\\n').map(s=>s.trim()).filter(Boolean);
   cur={path:t.slice('workspace-item-'.length),name:lines[0]||'',
        expanded:e.getAttribute('aria-expanded'),tasks:[]};out.push(cur)}
  else{
   const lines=(e.innerText||'').split('\\n').map(s=>s.trim()).filter(Boolean);
   const row={id:t.slice('task-item-'.length),title:lines[0]||'',
              age:lines.length>1?lines[lines.length-1]:''};
   if(cur&&!cur.tasks.some(x=>x.id===row.id))cur.tasks.push(row);
   else if(!cur)out.push({path:'',name:'',expanded:null,tasks:[row]})}}
 return out})()"""

JS_MARK_SHOW_MORE = (
    "(()=>{const b=[...document.querySelectorAll('*')].find(x=>x.children.length===0&&"
    "(x.innerText||'').trim()==='Show more');"
    "if(!b)return false;b.setAttribute('data-sani-more','1');return true})()"
)
MORE_SELECTOR = "[data-sani-more='1']"

# -- parsing (pure, tested against saved text) ----------------------------------------

_PERCENT = re.compile(r"^(\d{1,3})%$")
_RESET = re.compile(r"^\d{1,2}:\d{2}$")
_RATIO = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_EXPIRES = re.compile(r"^(expires|expired|valid until)\b", re.IGNORECASE)
_NOISE = re.compile(r"^(upgrade|\d+%\s*quota|today.s balance)$", re.IGNORECASE)


def _num(text: str) -> int:
    return int(text.replace(",", ""))


def parse_balances(text: str) -> list[dict[str, Any]]:
    """Per-model balances from a ZCode provider page's text, exactly as displayed.

    A model card reads: name, ``100%``, reset time, ``left / total``. The plan name is the
    line above its ``Expires ...`` line. Anything incomplete is dropped, not completed.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[dict[str, Any]] = []
    plan = ""
    expires = ""
    model = ""
    percent: int | None = None
    reset = ""
    for index, line in enumerate(lines):
        if _EXPIRES.match(line):
            expires = line
            plan = lines[index - 1] if index else ""
            model, percent, reset = "", None, ""
            continue
        found = _PERCENT.match(line)
        if found:
            percent = int(found.group(1))
            model = lines[index - 1] if index else ""
            reset = ""
            continue
        if _RESET.match(line) and percent is not None:
            reset = line
            continue
        ratio = _RATIO.match(line)
        if ratio and model and percent is not None:
            remaining, total = _num(ratio.group(1)), _num(ratio.group(2))
            if total > 0 and remaining <= total:
                items.append(
                    {
                        "plan": screen(plan, limit=80),
                        "model": screen(model, limit=80),
                        "remaining": remaining,
                        "total": total,
                        "percent": percent,
                        "reset": reset,
                        "expires": screen(expires, limit=40),
                    }
                )
            model, percent, reset = "", None, ""
    return items


def parse_model_testid(testid: str) -> dict[str, str] | None:
    """``chat-model-select-item-custom:account%3Azai-start-plan:GLM-5.3`` -> parts."""
    prefix = "chat-model-select-item-"
    if not testid.startswith(prefix):
        return None
    parts = testid[len(prefix) :].split(":", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return {"provider": parts[0], "plan_id": unquote(parts[1]), "model": unquote(parts[2])}


def parse_models(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    for item in items:
        parts = parse_model_testid(str(item.get("testid", "")))
        if parts is None:
            continue
        group = str(item.get("group", "")).split("\n")[0].strip()
        models.append(
            {
                **parts,
                "plan": screen(group, limit=60),
                "current": bool(item.get("checked")),
            }
        )
    return models


def summarize_sessions(raw: list[dict[str, Any]]) -> dict[str, Any]:
    projects = []
    count = 0
    collapsed = 0
    for entry in raw:
        tasks = [
            {
                "id": str(t.get("id", "")),
                "title": screen(str(t.get("title", "")), limit=120),
                "age": str(t.get("age", "")),
            }
            for t in entry.get("tasks", [])
        ]
        count += len(tasks)
        if entry.get("expanded") == "false":
            collapsed += 1
        projects.append(
            {
                "path": str(entry.get("path", "")),
                "name": screen(str(entry.get("name", "")), limit=80),
                "tasks": tasks,
            }
        )
    #: True only if every project was seen open; a collapsed project hides its tasks.
    complete = collapsed == 0 and all(p["path"] for p in projects)
    return {"count": count, "projects": projects, "complete": complete}


# -- the adapter ------------------------------------------------------------------------


class PageAdapter:
    def __init__(
        self,
        page: Page,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        settle: float = 0.6,
    ) -> None:
        self._page = page
        self._sleep = sleep
        self._settle = settle

    async def present(self) -> dict[str, bool]:
        import json

        found = await self._page.evaluate(JS_PRESENT.replace("__SELECTORS__", json.dumps(REQUIRED)))
        return {str(k): bool(v) for k, v in dict(found or {}).items()}

    async def status(self) -> dict[str, str]:
        found = await self._page.evaluate(JS_STATUS)
        return {
            k: screen(str(v), limit=80) for k, v in dict(found or {}).items() if k != "balance_open"
        }

    async def account(self) -> dict[str, Any]:
        """The signed-in account as ZCode labels it: a display name (no email is shown)."""
        label = str((await self.status()).get("account", "")).strip()
        signed_out = re.search(r"\b(sign|log)\s?in\b", label, re.IGNORECASE) is not None
        return {
            "name": "" if signed_out or not label else label,
            "signed_in": bool(label) and not signed_out,
        }

    async def _menu(self, trigger: str, script: str) -> list[dict[str, Any]]:
        await self._page.click(trigger)
        try:
            await self._sleep(self._settle)
            return [dict(i) for i in (await self._page.evaluate(script)) or []]
        finally:
            await self._page.press("Escape")
            await self._sleep(0.2)

    async def models(self) -> dict[str, Any]:
        status = await self.status()
        items = parse_models(await self._menu(MODEL_TRIGGER, JS_MODEL_ITEMS))
        modes = [
            {
                "id": str(m["testid"]).removeprefix("chat-mode-select-item-"),
                "label": str(m["text"]).split("\n")[0],
                "current": bool(m["checked"]),
            }
            for m in await self._menu(MODE_TRIGGER, JS_MODE_ITEMS)
        ]
        levels = [
            {
                "id": str(m["testid"]).removeprefix("chat-thought-level-select-item-"),
                "label": str(m["text"]).split("\n")[0],
                "current": bool(m["checked"]),
            }
            for m in await self._menu(REASONING_TRIGGER, JS_REASONING_ITEMS)
        ]
        return {
            "models": items,
            "current_model": status.get("model", ""),
            "modes": modes,
            "reasoning": levels,
        }

    async def balances(self) -> dict[str, Any]:
        """Open Settings -> Model settings, read each plan page, come back."""
        await self._page.click(SETTINGS_BUTTON)
        await self._sleep(self._settle)
        try:
            await self._page.click(SETTINGS_MODELS)
            await self._sleep(self._settle)
            nav = [dict(n) for n in (await self._page.evaluate(JS_PROVIDER_NAV)) or []]
            items: list[dict[str, Any]] = []
            unread: list[str] = []
            for entry in nav:
                testid = str(entry["testid"])
                kind = testid.removeprefix("model-provider-nav-item-")
                if kind.startswith("custom:"):
                    continue
                selector = f"[data-testid={_css(testid)}]"
                await self._page.click(selector)
                await self._sleep(self._settle)
                panel = await self._page.evaluate(JS_PROVIDER_PANEL)
                found = parse_balances(str(panel or ""))
                provider = kind.split(":", 1)[1] if ":" in kind else kind
                if found:
                    items.extend({"provider": provider, **item} for item in found)
                else:
                    unread.append(_unread_note(str(entry.get("text", "")), str(panel or "")))
            return {"items": items, "not_read": unread}
        finally:
            await self.leave_settings()

    async def leave_settings(self) -> None:
        if await self._page.evaluate(JS_BACK):
            await self._page.click(BACK_SELECTOR)
            await self._sleep(self._settle)

    async def sessions(self, *, expand: bool = True) -> dict[str, Any]:
        """Every project's tasks. ZCode hides tasks of collapsed projects and cuts each list
        short ("Show more"), so open them, read, and close again what was opened.

        Opening a project also makes it the project of the new-task box (ZCode's behaviour).
        """
        opened: list[str] = []
        try:
            if expand:
                for entry in await self._page.evaluate(JS_SESSIONS) or []:
                    if entry.get("expanded") == "false" and entry.get("path"):
                        await self._page.click(
                            f"[data-testid={_css('workspace-item-' + entry['path'])}]"
                        )
                        opened.append(str(entry["path"]))
                        await self._sleep(self._settle)
                await self._sleep(self._settle)
                quiet = 0
                for _ in range(30):
                    if await self._page.evaluate(JS_MARK_SHOW_MORE):
                        quiet = 0
                        await self._page.click(MORE_SELECTOR)
                    else:
                        # Lists draw a moment after a click: only stop after two empty looks.
                        quiet += 1
                        if quiet == 2:
                            break
                    await self._sleep(self._settle)
            raw = await self._page.evaluate(JS_SESSIONS)
            result = summarize_sessions([dict(r) for r in raw or []])
            if await self._page.evaluate(JS_MARK_SHOW_MORE):
                result["complete"] = False
            return result
        finally:
            for path in reversed(opened):
                with contextlib.suppress(Exception):
                    await self._page.click(f"[data-testid={_css('workspace-item-' + path)}]")


def _unread_note(name: str, panel: str) -> str:
    """``Z.ai: Not subscribed...``: why a provider has no balance, as ZCode words it."""
    label = screen(name.split("\n")[0], limit=60)
    for line in panel.splitlines():
        if re.search(r"not subscribed", line, re.IGNORECASE):
            return f"{label}: {screen(line.strip(), limit=80)}"
    return label


def _css(testid: str) -> str:
    """A quoted attribute value for ids that contain ':' and '/'."""
    return '"' + testid.replace("\\", "\\\\").replace('"', '\\"') + '"'

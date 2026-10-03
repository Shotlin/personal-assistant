"""Ordinary-command recipes: short validated action sequences with checkpoints.

A recipe is the unit Route A executes and Route B's selected candidate names
(master plan sections 4-6). Its steps stay ordered and depend on each other,
so no model decision sits between them while their preconditions hold — that
is the whole latency argument. What sits at the end is a postcondition check,
because "the tool accepted my call" has never meant "the user's objective
happened".

Every recipe obeys one targeting rule without exception:

> Explicit user target -> resolved application identity -> maintained task
> target. Foreground state is evidence, not permission to substitute another
> application.

So "Open Safari" resolves to Safari or fails honestly, and a follow-up
"search YouTube" keeps acting on the surface the task already owns.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from assistant.velo import scene, sight
from assistant.velo import verify as outcome_check
from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import (
    AppIdentity,
    OutcomeState,
    Postcondition,
    PostconditionKind,
    Target,
    TargetOwnership,
    TaskState,
)
from assistant.velo.scene import SCENES

logger = logging.getLogger("assistant.velo.recipes")


@dataclass
class RecipeResult:
    """What a recipe reports: an honest sentence plus its evidence."""

    recipe: str
    state: OutcomeState
    answer: str
    verification: Verification | None = None
    #: Options Sani asked the user to pick from; the controller remembers them
    #: so "the first one" or a label answers the question.
    choices: tuple[str, ...] = ()


def _focused_editable(state: dict[str, Any]) -> dict[str, Any] | None:
    """The element the window says is focused and accepts text."""
    for element in state.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if not element.get("element_token"):
            continue
        role = str(element.get("role") or "").lower()
        if role not in {"axtextfield", "axtextarea", "axcombobox", "axsearchfield"}:
            continue
        if element.get("focused") or element.get("is_focused") or element.get("focus"):
            return element
    return None


def _address_field(state: dict[str, Any]) -> dict[str, Any] | None:
    """The browser's address/search field, by role and label heuristics."""
    for element in state.get("elements") or []:
        if not isinstance(element, dict) or not element.get("element_token"):
            continue
        role = str(element.get("role") or "").lower()
        if role not in {"axtextfield", "axcombobox", "axsearchfield"}:
            continue
        label = str(element.get("label") or "").lower()
        if any(word in label for word in ("address", "search", "url")):
            return element
    return None


async def _observe(task: TaskState, adapter: CuaAdapter, pid: int, window_id: int) -> dict[str, Any]:
    # The adapter digests every structured observation into the task, so
    # no-progress tracking sees exactly what each step saw.
    return await adapter.observe_window(task, pid, window_id)


async def _wait_foreground(
    adapter: CuaAdapter, task: TaskState, pid: int,
    *, attempts: int = 16, delay_seconds: float = 0.15,
) -> bool:
    """Wait until the driver reports ``pid`` active, bounded.

    Foregrounding is a precondition, not an assumption: bring_to_front is
    accepted by the driver in milliseconds while the actual activation takes
    longer (live 2026-09-26 03:45: cmd+L fired 11ms after bring_to_front and
    landed on whatever held focus). Confirmation is read from observation,
    and the caller still owns an honest postcondition check afterwards.
    """
    for attempt in range(attempts):
        if task.cancelled:
            return False
        # A bounded precondition poll, not decision churn: the reads do not
        # each register with the no-progress tracker.
        apps = await adapter.list_apps(task, for_verification=True)
        for app in apps:
            if app.pid == pid and app.active:
                return True
        if attempt < attempts - 1:
            await asyncio.sleep(delay_seconds)
    return False


async def _verify_patiently(
    adapter: CuaAdapter,
    task: TaskState,
    postcondition: Postcondition,
    *,
    expect_pid: int | None = None,
    expect_window_id: int | None = None,
    attempts: int = 12,
    delay_seconds: float = 0.4,
) -> Verification:
    """Check a postcondition, then keep checking while a result loads.

    A browser command's evidence appears when the page's accessibility tree
    settles -- seconds after Return, not instantly. One immediate check read
    every web navigation as "could not confirm" (live 2026-09-26 03:04). The
    poll is bounded here, and each read is a verification read, so the
    no-progress tracker sees one bounded wait rather than decision churn.
    """
    check = await outcome_check.check(
        adapter, task, postcondition,
        expect_pid=expect_pid, expect_window_id=expect_window_id,
    )
    tries = 1
    while not check.satisfied and tries < attempts:
        if task.cancelled:
            break
        await asyncio.sleep(delay_seconds)
        check = await outcome_check.check(
            adapter, task, postcondition,
            expect_pid=expect_pid, expect_window_id=expect_window_id,
        )
        tries += 1
    return check


async def _resolve_target(
    task: TaskState, adapter: CuaAdapter, *, wanted: str = ""
) -> Target | None:
    """Bind the task's application target once, and keep it.

    Resolution order: the name this instruction explicitly gave (a correction
    like "use Safari instead" must outrank anything the task held), then the
    target the conversation already owns, then nothing — the foreground app is
    looked at only as evidence and is never adopted as a substitute.
    """
    name = wanted or task.requested_app
    apps: list[AppIdentity] | None = None
    if name:
        apps = await adapter.list_apps(task)
        app, _note = CuaAdapter.resolve_app(name, apps)
        if app is None:
            return None
        running = app.running and app.pid
        identity = app if running else AppIdentity(name=app.name, bundle_id=app.bundle_id)
        task.resolved = Target(
            app=identity, ownership=TargetOwnership.REQUESTED, version=task.version
        )
        return task.resolved
    if task.resolved is not None and task.resolved.app.pid:
        # Validate the held target still exists; a dead pid re-resolves, and a
        # live one refreshes its identity from what the driver sees now.
        apps = await adapter.list_apps(task)
        for app in apps:
            if app.pid == task.resolved.app.pid and app.running:
                if app.name and app.name != task.resolved.app.name:
                    task.resolved = Target(
                        app=app,
                        window_id=task.resolved.window_id,
                        ownership=task.resolved.ownership,
                        version=task.version,
                    )
                return task.resolved
        task.resolved = None
    return None


async def _ensure_running_window(
    task: TaskState, adapter: CuaAdapter, target: Target, *, wanted_name: str
) -> tuple[int, int | None] | str:
    """The (pid, window_id) to act on, or an honest sentence about why not."""
    app = target.app
    pid = app.pid
    if not pid or not app.running:
        launch = await adapter.acting_call(
            task,
            "launch_app",
            bundle_id=app.bundle_id or None,
            name=app.name or None,
        )
        payload = launch.structured or {}
        pid = payload.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            detail = (launch.text or "no confirmation came back")[:160]
            return f"Tried to open {app.name}: {detail}"
        task.resolved = Target(
            app=AppIdentity(
                name=app.name, bundle_id=app.bundle_id, pid=pid, running=True
            ),
            ownership=TargetOwnership.REQUESTED,
            version=task.version,
        )
        app = task.resolved.app
    window_id = target.window_id
    if window_id is not None and not await adapter.window_is_on_screen(task, pid, window_id):
        # The carried id names a window the user has since closed or
        # minimized; re-resolve rather than typing into a dead surface.
        window_id = None
    if window_id is None:
        window_id = await adapter.front_window(task, pid)
    if window_id is None:
        bring = await adapter.acting_call(task, "bring_to_front", pid=pid)
        if bring.text.lower().startswith(("error", "refused")):
            return f"{app.name} has no open window I could bring forward."
        # Window creation and un-minimizing lag behind the foreground call
        # (live 2026-09-26 03:03: "Open safari" read no window that was about
        # to exist). A short bounded wait, not a polling loop.
        for _ in range(5):
            await asyncio.sleep(0.5)
            window_id = await adapter.front_window(task, pid)
            if window_id is not None:
                break
    if window_id is None:
        return f"{app.name} has no open window to act in."
    task.resolved = Target(
        app=app, window_id=window_id, ownership=target.ownership, version=task.version
    )
    return (pid, window_id)


async def open_app(
    task: TaskState, adapter: CuaAdapter, *, app_name: str
) -> RecipeResult:
    """Open or foreground the explicitly requested application."""
    target = await _resolve_target(task, adapter, wanted=app_name)
    if target is None:
        apps = await adapter.list_apps(task)
        near = CuaAdapter.closest_app(app_name, apps)
        if near is not None:
            note = f" I read {app_name!r} as {near.name}, the closest installed app."
            target = Target(app=near, ownership=TargetOwnership.RESOLVED, version=task.version)
        else:
            return RecipeResult(
                "open_app",
                OutcomeState.NO_EFFECT,
                f"I couldn't find an application called {app_name!r} to open.",
            )
    else:
        note = ""
    was_running = bool(target.app.running and target.app.pid)
    task.resolved = target
    surface = await _ensure_running_window(task, adapter, target, wanted_name=app_name)
    if isinstance(surface, str):
        return RecipeResult("open_app", OutcomeState.FAILED, surface)
    pid, _window_id = surface
    await adapter.acting_call(task, "bring_to_front", pid=pid)
    check = await outcome_check.check(
        adapter, task, Postcondition(kind=PostconditionKind.APP_RUNNING),
        expect_pid=pid,
    )
    app_name_actual = task.resolved.app.name
    verb = "Brought" if was_running else "Opened"
    if check.satisfied:
        return RecipeResult(
            "open_app",
            OutcomeState.CONFIRMED,
            f"{verb} {app_name_actual}.{note}",
            check,
        )
    return RecipeResult(
        "open_app",
        OutcomeState.UNKNOWN,
        f"I tried to open {app_name_actual}{note}, but I could not confirm it came up: "
        f"{check.detail}.",
        check,
    )


async def _browser_without_request(
    task: TaskState, adapter: CuaAdapter
) -> Target | None:
    """A browser target when the instruction named none.

    The literal category "browser" resolves to the one browser already
    running -- unambiguous evidence, not a substitution. Zero or several
    running browsers defers: the user decides.
    """
    apps = await adapter.list_apps(task)
    app, _note = CuaAdapter.resolve_app("browser", apps)
    if app is None:
        return None
    identity = AppIdentity(name=app.name, bundle_id=app.bundle_id, pid=app.pid, running=True)
    return Target(app=identity, ownership=TargetOwnership.OBSERVED, version=task.version)


async def navigate(
    task: TaskState, adapter: CuaAdapter, *, destination: str, app_name: str = ""
) -> RecipeResult:
    """Open a URL in the task's browser — exactly the browser the user owns."""
    url = destination if "://" in destination else f"https://{destination}"
    target = await _resolve_target(task, adapter, wanted=app_name)
    if target is None and not app_name:
        target = await _browser_without_request(task, adapter)
    if target is None:
        return RecipeResult(
            "navigate",
            OutcomeState.NO_EFFECT,
            "No browser is running and none was requested; tell me which one to launch.",
        )
    task.resolved = target
    surface = await _ensure_running_window(task, adapter, target, wanted_name=app_name)
    if isinstance(surface, str):
        return RecipeResult("navigate", OutcomeState.FAILED, surface)
    pid, window_id = surface
    # The keystroke sequence below lands on the FOCUSED application: if the
    # user was typing in Sani (or anywhere else), cmd+L would go there and
    # every later step would run blind (live 2026-09-26 03:36: the browser
    # never moved while Sani held focus). Foregrounding the task's own bound
    # window first makes the sequence deterministic.
    await adapter.acting_call(task, "bring_to_front", pid=pid)
    await _wait_foreground(adapter, task, pid)
    await adapter.acting_call(
        task, "hotkey", pid=pid, window_id=window_id, keys=["cmd", "l"], delivery_mode="foreground"
    )
    state = await _observe(task, adapter, pid, window_id)
    field = _focused_editable(state) or _address_field(state)
    if field is not None:
        # Semantic value-setting into a validated element: the browser PID
        # alone never guaranteed the composer-vs-address-bar distinction.
        set_reply = await adapter.acting_call(
            task,
            "set_value",
            pid=pid,
            window_id=window_id,
            element_token=field["element_token"],
            value=url,
        )
        if set_reply.text.lower().startswith(("error", "refused")):
            await adapter.acting_call(
                task, "type_text", pid=pid, window_id=window_id, text=url,
                delivery_mode="foreground",
            )
    else:
        await adapter.acting_call(
            task, "type_text", pid=pid, window_id=window_id, text=url,
            delivery_mode="foreground",
        )
    await adapter.acting_call(
        task, "press_key", pid=pid, window_id=window_id, key="Return", delivery_mode="foreground"
    )
    check = await _verify_patiently(
        adapter, task,
        Postcondition(kind=PostconditionKind.NAVIGATED, url=url),
        expect_pid=pid, expect_window_id=window_id,
    )
    app_name_actual = target.app.name
    if check.satisfied:
        return RecipeResult("navigate", OutcomeState.CONFIRMED, f"Opened {url} in {app_name_actual}.", check)
    return RecipeResult(
        "navigate",
        OutcomeState.UNKNOWN,
        f"I typed {url} into {app_name_actual} and pressed Return, but I could not "
        f"confirm the page loaded: {check.detail}.",
        check,
    )


async def search_browser(
    task: TaskState, adapter: CuaAdapter, *, query: str, site: str = "", app_name: str = ""
) -> RecipeResult:
    """Search the web (or a site) in the task's browser, query byte-exact.

    A named site scopes the search deterministically -- YouTube's own search
    URL -- so "search YouTube for jazz" cannot land on a generic web search,
    and the query is carried exactly as dictated.
    """
    site = (site or "").strip().lower()
    if site == "youtube":
        return await navigate(
            task,
            adapter,
            destination=f"https://www.youtube.com/results?search_query={quote_plus(query)}",
            app_name=app_name,
        )
    if site == "google":
        return await navigate(
            task,
            adapter,
            destination=f"https://www.google.com/search?q={quote_plus(query)}",
            app_name=app_name,
        )
    target = await _resolve_target(task, adapter, wanted=app_name)
    if target is None and not app_name:
        target = await _browser_without_request(task, adapter)
    if target is None:
        return RecipeResult(
            "search_browser",
            OutcomeState.NO_EFFECT,
            "No browser is running and none was requested; tell me which one to launch.",
        )
    task.resolved = target
    surface = await _ensure_running_window(task, adapter, target, wanted_name=app_name)
    if isinstance(surface, str):
        return RecipeResult("search_browser", OutcomeState.FAILED, surface)
    pid, window_id = surface
    # Same focus discipline as navigate: the sequence lands where the focus
    # is, so the task's window must be front before cmd+L.
    await adapter.acting_call(task, "bring_to_front", pid=pid)
    await _wait_foreground(adapter, task, pid)
    await adapter.acting_call(
        task, "hotkey", pid=pid, window_id=window_id, keys=["cmd", "l"], delivery_mode="foreground"
    )
    state = await _observe(task, adapter, pid, window_id)
    field = _focused_editable(state) or _address_field(state)
    if field is not None:
        set_reply = await adapter.acting_call(
            task,
            "set_value",
            pid=pid,
            window_id=window_id,
            element_token=field["element_token"],
            value=query,
        )
        if set_reply.text.lower().startswith(("error", "refused")):
            await adapter.acting_call(
                task, "type_text", pid=pid, window_id=window_id, text=query,
                delivery_mode="foreground",
            )
    else:
        await adapter.acting_call(
            task, "type_text", pid=pid, window_id=window_id, text=query,
            delivery_mode="foreground",
        )
    await adapter.acting_call(
        task, "press_key", pid=pid, window_id=window_id, key="Return", delivery_mode="foreground"
    )
    check = await _verify_patiently(
        adapter, task,
        Postcondition(kind=PostconditionKind.SEARCHED, query=query),
        expect_pid=pid, expect_window_id=window_id,
    )
    app_name_actual = target.app.name
    if check.satisfied:
        return RecipeResult(
            "search_browser",
            OutcomeState.CONFIRMED,
            f"Searched {app_name_actual} for {query!r}.",
            check,
        )
    return RecipeResult(
        "search_browser",
        OutcomeState.UNKNOWN,
        f"I searched {app_name_actual} for {query!r}, but I could not confirm the "
        f"search results loaded: {check.detail}.",
        check,
    )


async def scroll(
    task: TaskState, adapter: CuaAdapter, *, direction: str, amount: int = 3
) -> RecipeResult:
    """Scroll the bound surface, and prove the viewport moved."""
    # Re-validate the carried target and refresh its identity: the carried
    # seed carries only a pid, and an answer like "Scrolled down in ." (empty
    # name, live 2026-09-26) tells the user nothing.
    target = await _resolve_target(task, adapter)
    pid = target.app.pid if target else None
    window_id = target.window_id if target else None
    if pid is None or window_id is None:
        return RecipeResult(
            "scroll",
            OutcomeState.NO_EFFECT,
            "Nothing is bound to this task yet; name the app or window first.",
        )
    before_state = await _observe(task, adapter, pid, window_id)
    await adapter.acting_call(
        task, "scroll", pid=pid, window_id=window_id, direction=direction, amount=amount
    )
    check = await outcome_check.check(
        adapter, task,
        Postcondition(
            kind=PostconditionKind.VIEWPORT_CHANGED,
            direction=direction,
            before_digest=CuaAdapter.scene_digest(before_state),
        ),
        expect_pid=pid, expect_window_id=window_id,
    )
    app_name_actual = target.app.name if target else "the application"
    if check.satisfied:
        return RecipeResult(
            "scroll", OutcomeState.CONFIRMED,
            f"Scrolled {direction} in {app_name_actual}.", check,
        )
    return RecipeResult(
        "scroll",
        OutcomeState.NO_EFFECT,
        f"I scrolled {direction} in {app_name_actual}, but the view did not change "
        "(it may already be at the end).",
        check,
    )


async def type_text(
    task: TaskState, adapter: CuaAdapter, *, text: str
) -> RecipeResult:
    """Type exact dictated text into the bound surface's focused field."""
    target = await _resolve_target(task, adapter)
    pid = target.app.pid if target else None
    window_id = target.window_id if target else None
    if pid is None or window_id is None:
        return RecipeResult(
            "type_text",
            OutcomeState.NO_EFFECT,
            "Nothing is bound to this task yet; open the app first.",
        )
    state = await _observe(task, adapter, pid, window_id)
    field = _focused_editable(state)
    if field is not None:
        reply = await adapter.acting_call(
            task,
            "set_value",
            pid=pid,
            window_id=window_id,
            element_token=field["element_token"],
            value=text,
        )
        if reply.text.lower().startswith(("error", "refused")):
            await adapter.acting_call(
                task, "type_text", pid=pid, window_id=window_id, text=text,
                delivery_mode="foreground",
            )
    else:
        # No focused field was observable: type into the surface and say so.
        await adapter.acting_call(
            task, "type_text", pid=pid, window_id=window_id, text=text,
            delivery_mode="foreground",
        )
    check = await _verify_patiently(
        adapter, task,
        Postcondition(kind=PostconditionKind.TEXT_IN_FIELD, text=text),
        expect_pid=pid, expect_window_id=window_id, attempts=2, delay_seconds=0.8,
    )
    app_name_actual = target.app.name if target else "the application"
    if check.satisfied:
        return RecipeResult(
            "type_text", OutcomeState.CONFIRMED,
            f"Typed {text[:60]!r} into {app_name_actual}.", check,
        )
    return RecipeResult(
        "type_text",
        OutcomeState.UNKNOWN,
        f"I typed the text into {app_name_actual}, but the field does not expose its "
        "value, so I can't confirm what landed there.",
        check,
    )


_ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
}


def _ordinal_word(index: int) -> str:
    word = _ORDINAL_WORDS.get(index)
    if word:
        return word
    stem = 10 if index % 100 in (11, 12, 13) else index % 10
    return f"{index}{'st' if stem == 1 else 'nd' if stem == 2 else 'rd' if stem == 3 else 'th'}"


async def press_ordinal(
    task: TaskState, adapter: CuaAdapter, *, kind: str, index: int
) -> RecipeResult:
    """Press the Nth ``kind`` (link/button) in the bound surface's tree."""
    target = await _resolve_target(task, adapter)
    pid = target.app.pid if target else None
    window_id = target.window_id if target else None
    if pid is None or window_id is None:
        return RecipeResult(
            "press_ordinal",
            OutcomeState.NO_EFFECT,
            "Nothing is bound to this task yet; open the app first.",
        )
    before_state = await _observe(task, adapter, pid, window_id)
    wanted = kind.lower()
    candidates = [
        element
        for element in (before_state.get("elements") or [])
        if isinstance(element, dict)
        and str(element.get("role", "")).lower().removeprefix("ax") == wanted
        and element.get("element_token")
    ]
    if len(candidates) < index:
        return RecipeResult(
            "press_ordinal",
            OutcomeState.NO_EFFECT,
            f"I can read {len(candidates)} {wanted}{'' if len(candidates) == 1 else 's'} "
            f"in {target.app.name}'s current tree, so there is no "
            f"{_ordinal_word(index)} one to press.",
        )
    element = candidates[index - 1]
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, element_token=element["element_token"]
    )
    digest_before = CuaAdapter.scene_digest(before_state)
    after_state = await _observe(task, adapter, pid, window_id)
    changed = CuaAdapter.scene_digest(after_state) != digest_before
    label = str(element.get("label") or "unlabelled")[:40]
    ordinal = _ordinal_word(index)
    if changed:
        return RecipeResult(
            "press_ordinal",
            OutcomeState.CONFIRMED,
            f"Pressed the {ordinal} {wanted} ({label}) and the window changed.",
        )
    return RecipeResult(
        "press_ordinal",
        OutcomeState.UNKNOWN,
        f"Pressed the {ordinal} {wanted} ({label}); the window did not visibly change. "
        "Tell me if that was the wrong thing.",
    )


async def _scene_surface(
    task: TaskState, adapter: CuaAdapter
) -> tuple[Target, int, int] | str:
    """The bound (target, pid, window) to read, or an honest sentence."""
    target = await _resolve_target(task, adapter)
    pid = target.app.pid if target else None
    if target is None or pid is None:
        return "Nothing is bound yet; open the app first, then ask what I see."
    window_id = target.window_id or await adapter.front_window(task, pid)
    if window_id is None:
        return f"I can't find a window for {target.app.name or 'that app'} to look at."
    return target, pid, window_id


async def describe_screen(task: TaskState, adapter: CuaAdapter) -> RecipeResult:
    """Read the bound window and say what is on it, numbered, ads flagged."""
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("describe_screen", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    state = await adapter.observe_window(task, pid, window_id, max_elements=800, settle=True)
    app_name = target.app.name or "the app"
    title = scene.page_title(state) or str(state.get("window_title") or "").strip()
    items = scene.extract_items(state, fallback=False)
    SCENES.remember(task.conversation, items or scene.extract_items(state))
    if len(items) >= 2:
        return RecipeResult(
            "describe_screen", OutcomeState.CONFIRMED,
            scene.describe(items, app=app_name, title=title),
        )
    return RecipeResult(
        "describe_screen", OutcomeState.CONFIRMED,
        scene.describe_general(state, app=app_name, title=title),
    )


async def press_item(
    task: TaskState,
    adapter: CuaAdapter,
    *,
    index: int | None = None,
    title: str = "",
) -> RecipeResult:
    """Open the Nth (or named) item from what was just described.

    The page is re-read first -- tokens go stale -- and the remembered title
    at that number is re-matched, so "the second one" means the second item
    the user heard, even if the list shifted. Advertisements are never opened.
    """
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("press_item", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    before = await adapter.observe_window(task, pid, window_id, max_elements=800, settle=True)
    fresh = scene.extract_items(before)
    remembered = SCENES.recall(task.conversation)

    chosen: scene.SceneItem | None = None
    if title:
        chosen = scene.match_title(fresh, title)
    elif index is not None:
        if remembered and index <= len(remembered):
            chosen = scene.match_title(fresh, remembered[index - 1].title)
        if chosen is None and index <= len(fresh) and not remembered:
            chosen = fresh[index - 1]
    if chosen is None:
        what = f"the {_ordinal_word(index)} one" if index else f"'{title}'"
        return RecipeResult(
            "press_item",
            OutcomeState.NO_EFFECT,
            f"I can see {len(fresh)} item{'' if len(fresh) == 1 else 's'} here but could not "
            f"match {what}. Ask what I see, then pick a number.",
        )
    if chosen.is_ad:
        return RecipeResult(
            "press_item",
            OutcomeState.NO_EFFECT,
            f"That one is an advertisement ({chosen.title[:50]}), so I did not open it.",
        )
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, element_token=chosen.token
    )
    digest_before = CuaAdapter.scene_digest(before)
    wanted = scene._words(chosen.title)
    for attempt in range(8):
        if task.cancelled:
            break
        await asyncio.sleep(0.5 if attempt else 0.3)
        after = await adapter.observe_window(task, pid, window_id, for_verification=True)
        if CuaAdapter.scene_digest(after) == digest_before:
            continue
        seen = scene._words(f"{scene.page_title(after)} " + " ".join(
            str(e.get("label") or "") for e in (after.get("elements") or [])
            if isinstance(e, dict)
        ))
        if wanted and len(wanted & seen) / len(wanted) >= 0.5:
            return RecipeResult(
                "press_item",
                OutcomeState.CONFIRMED,
                f"Opened {chosen.title[:70]}.",
            )
    return RecipeResult(
        "press_item",
        OutcomeState.UNKNOWN,
        f"I clicked {chosen.title[:70]} but could not confirm it opened. Tell me if it did not.",
    )


async def click_named(task: TaskState, adapter: CuaAdapter, *, label: str) -> RecipeResult:
    """Click the visible control the user named, then confirm the screen moved."""
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("click_named", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    before = await adapter.observe_window(task, pid, window_id, max_elements=800, settle=True)
    element, alternatives = scene.find_by_label(before, label)
    if element is None:
        if alternatives:
            return RecipeResult(
                "click_named",
                OutcomeState.NO_EFFECT,
                f"More than one thing matches '{label}': " + "; ".join(alternatives)
                + ". Which one?",
                choices=tuple(alternatives),
            )
        seen = await sight.find_and_click(
            task, adapter, pid=pid, window_id=window_id, label=label,
            scope=sight.scope_from_state(target.app.name, before),
        )
        if seen is not None:
            return RecipeResult("click_named", seen[0], seen[1])
        return RecipeResult(
            "click_named",
            OutcomeState.NO_EFFECT,
            f"I can't see '{label}' on screen in {target.app.name or 'this window'}. "
            "Ask what I see, or scroll and try again.",
        )
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, element_token=element["element_token"]
    )
    digest_before = CuaAdapter.scene_digest(before)
    shown = " ".join(str(element.get("label") or label).split())[:50]
    for attempt in range(6):
        if task.cancelled:
            break
        await asyncio.sleep(0.25 if attempt == 0 else 0.4)
        after = await adapter.observe_window(
            task, pid, window_id, for_verification=True, max_elements=800
        )
        if CuaAdapter.scene_digest(after) != digest_before:
            return RecipeResult("click_named", OutcomeState.CONFIRMED, f"Clicked {shown}.")
    return RecipeResult(
        "click_named",
        OutcomeState.UNKNOWN,
        f"I clicked {shown} but the screen did not visibly change. Tell me if it did not work.",
    )


async def fill_field(
    task: TaskState, adapter: CuaAdapter, *, text: str, target: str = "", submit: bool = False
) -> RecipeResult:
    """Click the input box the user means, type the text, optionally press Enter."""
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("fill_field", OutcomeState.NO_EFFECT, surface)
    app, pid, window_id = surface
    state = await adapter.observe_window(task, pid, window_id, max_elements=800, settle=True)
    field = scene.find_field(state, target)
    if field is None:
        return RecipeResult(
            "fill_field",
            OutcomeState.NO_EFFECT,
            f"I don't see an input box in {app.app.name or 'this window'} right now. "
            "Which box do you mean, or should I click something first?",
        )
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, element_token=field["element_token"]
    )
    reply = await adapter.acting_call(
        task, "set_value", pid=pid, window_id=window_id,
        element_token=field["element_token"], value=text,
    )
    if reply.text.lower().startswith(("error", "refused")):
        await adapter.acting_call(
            task, "type_text", pid=pid, window_id=window_id, text=text,
            delivery_mode="foreground",
        )
    check = await _verify_patiently(
        adapter, task, Postcondition(kind=PostconditionKind.TEXT_IN_FIELD, text=text),
        expect_pid=pid, expect_window_id=window_id, attempts=3, delay_seconds=0.3,
    )
    if not check.satisfied and not reply.text.lower().startswith(("error", "refused")):
        # A web composer can accept set_value without its page noticing. Only
        # when the box visibly STILL reads empty is typing keystrokes safe;
        # if it exposes no value at all, typing again could duplicate the text.
        reread = await adapter.observe_window(
            task, pid, window_id, for_verification=True, max_elements=800
        )
        again = scene.find_field(reread, target)
        value = again.get("value") if again else None
        if isinstance(value, str) and not value.strip():
            await adapter.acting_call(
                task, "type_text", pid=pid, window_id=window_id, text=text,
                delivery_mode="foreground",
            )
            check = await _verify_patiently(
                adapter, task, Postcondition(kind=PostconditionKind.TEXT_IN_FIELD, text=text),
                expect_pid=pid, expect_window_id=window_id, attempts=3, delay_seconds=0.3,
            )
    shown = " ".join(str(field.get("label") or "the input box").split())[:40]
    if not submit:
        if check.satisfied:
            return RecipeResult("fill_field", OutcomeState.CONFIRMED, f"Typed it into {shown}.", check)
        return RecipeResult(
            "fill_field", OutcomeState.UNKNOWN,
            f"I typed it into {shown} but that box does not show its text, so I can't confirm it.",
            check,
        )
    before = CuaAdapter.scene_digest(state)
    await adapter.acting_call(
        task, "press_key", pid=pid, window_id=window_id, key="Return",
        delivery_mode="foreground",
    )
    for attempt in range(4):
        await asyncio.sleep(0.25 if attempt == 0 else 0.5)
        after = await adapter.observe_window(
            task, pid, window_id, for_verification=True, max_elements=800
        )
        if CuaAdapter.scene_digest(after) != before:
            return RecipeResult(
                "fill_field", OutcomeState.CONFIRMED, f"Typed it into {shown} and submitted."
            )
    return RecipeResult(
        "fill_field", OutcomeState.UNKNOWN,
        f"I typed it into {shown} and pressed Enter, but nothing on screen changed.",
    )


_DOWNLOAD_ALL = re.compile(r"\b(?:download|save)\s+\d+\s+images?|all\s+images?|series|every", re.I)
_SKIP_DOWNLOAD_LABELS = re.compile(r"\b(?:app|update|history)\b", re.I)
_DOWNLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".pdf", ".mp4", ".svg"}


def _downloads_dir() -> Path:
    return Path.home() / "Downloads"


def _download_snapshot() -> dict[str, int]:
    try:
        return {
            p.name: p.stat().st_size
            for p in _downloads_dir().iterdir()
            if p.suffix.lower() in _DOWNLOAD_SUFFIXES and not p.name.startswith(".")
        }
    except OSError:
        return {}


async def download_images(task: TaskState, adapter: CuaAdapter) -> RecipeResult:
    """Download the images on screen (e.g. everything ChatGPT just generated).

    Opens the first image if no viewer is open, opens the Download menu, takes
    the "download all / N images in this series" entry when there is one (else
    "Download image"), then confirms by watching the Downloads folder: a click
    on a download button proves nothing until a file lands.
    """
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("download_images", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    state = await adapter.observe_window(task, pid, window_id, max_elements=1500, settle=True)

    def pick(state: dict[str, Any], roles: set[str], rx: re.Pattern[str]) -> dict[str, Any] | None:
        strict = any(isinstance(e.get("frame"), dict) for e in state.get("elements") or [])
        for e in state.get("elements") or []:
            if (
                isinstance(e, dict) and scene._role(e) in roles and e.get("element_token")
                and scene._visible(e, strict=strict)
                and rx.search(str(e.get("label") or ""))
                and not _SKIP_DOWNLOAD_LABELS.search(str(e.get("label") or ""))
            ):
                return e
        return None

    async def click(element: dict[str, Any]) -> None:
        await adapter.acting_call(
            task, "click", pid=pid, window_id=window_id, element_token=element["element_token"]
        )

    async def look() -> dict[str, Any]:
        await asyncio.sleep(0.8)
        return await adapter.observe_window(
            task, pid, window_id, for_verification=True, max_elements=1500
        )

    download_btn = pick(state, {"popupbutton", "button"}, re.compile(r"^download\b", re.I))
    opened_viewer = False
    if download_btn is None:
        image = pick(state, {"button", "image"}, re.compile(r"^generated image\s*1\b|^image\s*1\b", re.I))
        if image is None:
            image = pick(state, {"button"}, re.compile(r"generated image|image \d", re.I))
        if image is None:
            return RecipeResult(
                "download_images", OutcomeState.NO_EFFECT,
                f"I don't see any images or a Download button in {target.app.name or 'this window'}. "
                "Which image do you want, or should I scroll to it first?",
            )
        await click(image)
        opened_viewer = True
        state = await look()
        download_btn = pick(state, {"popupbutton", "button"}, re.compile(r"^download\b", re.I))
        if download_btn is None:
            return RecipeResult(
                "download_images", OutcomeState.UNKNOWN,
                "I opened the image but can't find its Download button. "
                "Do you see one on screen? Tell me where it is.",
            )

    before = _download_snapshot()
    await click(download_btn)
    menu = await look()
    item = None
    for e in menu.get("elements") or []:
        if isinstance(e, dict) and scene._role(e) == "menuitem" and e.get("element_token") and (
            scene._visible(e, strict=True) if isinstance(e.get("frame"), dict) else True
        ) and str(e.get("label") or "").lower().startswith("download"):
            if _DOWNLOAD_ALL.search(str(e["label"])):
                item = e
                break
            item = item or e
    if item is not None:
        await click(item)
    # else: the button itself downloaded (no menu), which the folder check will show.

    seen = 0
    stable = 0
    for _ in range(40):
        await asyncio.sleep(0.5)
        now = _download_snapshot()
        new = [n for n in now if n not in before]
        if len(new) > seen:
            seen, stable = len(new), 0
        elif seen:
            stable += 1
            if stable >= 5:
                break
        if task.cancelled:
            break
    if opened_viewer:
        closer = pick(
            await adapter.observe_window(
                task, pid, window_id, for_verification=True, max_elements=1500
            ),
            {"button"}, re.compile(r"close viewer", re.I),
        )
        if closer is not None:
            await click(closer)
    if seen:
        return RecipeResult(
            "download_images", OutcomeState.CONFIRMED,
            f"Downloaded {seen} image{'s' if seen != 1 else ''} to your Downloads folder.",
        )
    return RecipeResult(
        "download_images", OutcomeState.UNKNOWN,
        "I clicked Download but no new file showed up in your Downloads folder. "
        "Is the browser asking where to save, or blocked the download?",
    )


async def click_in_utterance(
    task: TaskState, adapter: CuaAdapter, *, utterance: str
) -> RecipeResult:
    """Click the control named somewhere in loose dictation, then confirm the screen moved."""
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("click_in_utterance", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    before = await adapter.observe_window(task, pid, window_id, max_elements=1500, settle=True)
    element = scene.find_in_utterance(before, utterance)
    if element is None:
        label = sight.guess_label(utterance)
        seen = await sight.find_and_click(
            task, adapter, pid=pid, window_id=window_id, label=label,
            scope=sight.scope_from_state(target.app.name, before), description=utterance,
        ) if label else None
        if seen is not None:
            return RecipeResult("click_in_utterance", seen[0], seen[1])
        shown = scene.compact_observation(before) or ""
        names = [ln.split('"')[1] for ln in shown.splitlines() if '"' in ln and "Button" in ln][:8]
        return RecipeResult(
            "click_in_utterance", OutcomeState.NO_EFFECT,
            f"I can't see that button in {target.app.name or 'this window'}. "
            + (f"The buttons I can see include: {', '.join(names)}. Which one?" if names
               else "Tell me what it says or where it is."),
            choices=tuple(names),
        )
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, element_token=element["element_token"]
    )
    digest_before = CuaAdapter.scene_digest(before)
    shown = " ".join(str(element.get("label") or "").split())[:50]
    for attempt in range(6):
        await asyncio.sleep(0.3 if attempt == 0 else 0.5)
        after = await adapter.observe_window(
            task, pid, window_id, for_verification=True, max_elements=1500
        )
        if CuaAdapter.scene_digest(after) != digest_before:
            return RecipeResult("click_in_utterance", OutcomeState.CONFIRMED, f"Clicked {shown}.")
    return RecipeResult(
        "click_in_utterance", OutcomeState.UNKNOWN,
        f"I clicked {shown} but the screen did not visibly change. Tell me if it did not work.",
    )


_NEXT = re.compile(r"^(?:next|forward)(?:\s+(?:image|slide|photo|picture|page|item))?$", re.I)
_PREVIOUS = re.compile(r"^(?:previous|prev)(?:\s+(?:image|slide|photo|picture|page|item))?$", re.I)
_IMAGE_CURSOR: dict[str, int] = {}


async def step_item(task: TaskState, adapter: CuaAdapter, *, direction: str = "next") -> RecipeResult:
    """"Next" / "previous": the viewer's own control, else the next numbered image, else scroll."""
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("step_item", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    before = await adapter.observe_window(task, pid, window_id, max_elements=1500, settle=True)
    elements = [e for e in (before.get("elements") or []) if isinstance(e, dict)]
    strict = any(isinstance(e.get("frame"), dict) for e in elements)
    rx = _NEXT if direction == "next" else _PREVIOUS

    def visible_ctl(e: dict[str, Any]) -> bool:
        return bool(e.get("element_token")) and scene._visible(e, strict=strict)

    control = next(
        (e for e in elements if visible_ctl(e) and scene._role(e) in scene._ACTIONABLE
         and rx.match(" ".join(str(e.get("label") or "").split()))),
        None,
    )
    chosen = control
    label_shown = "that"
    if chosen is None:
        thumbs = []
        for e in elements:
            m = re.search(r"generated image\s*(\d+)", str(e.get("label") or ""), re.I)
            if m and visible_ctl(e) and scene._role(e) in {"checkbox", "button", "radiobutton"}:
                thumbs.append((int(m.group(1)), e))
        if thumbs:
            numbers = sorted({n for n, _ in thumbs})
            current = _IMAGE_CURSOR.get(task.conversation, numbers[0] if direction == "next" else 2)
            want = current + 1 if direction == "next" else current - 1
            if _IMAGE_CURSOR.get(task.conversation) is None and direction == "next":
                want = numbers[0]
            if want in numbers:
                chosen = next(e for n, e in thumbs if n == want)
                _IMAGE_CURSOR[task.conversation] = want
                label_shown = f"image {want}"
            else:
                return RecipeResult(
                    "step_item", OutcomeState.NO_EFFECT,
                    "That's the last image." if direction == "next" else "That's the first image.",
                )
    if chosen is None:
        return await scroll(
            task, adapter, direction="down" if direction == "next" else "up", amount=3
        )
    await adapter.acting_call(
        task, "click", pid=pid, window_id=window_id, element_token=chosen["element_token"]
    )
    digest_before = CuaAdapter.scene_digest(before)
    for attempt in range(5):
        await asyncio.sleep(0.3 if attempt == 0 else 0.5)
        after = await adapter.observe_window(
            task, pid, window_id, for_verification=True, max_elements=1500
        )
        if CuaAdapter.scene_digest(after) != digest_before:
            return RecipeResult("step_item", OutcomeState.CONFIRMED, f"Showing {label_shown}.")
    return RecipeResult(
        "step_item", OutcomeState.UNKNOWN, "I pressed it but the screen did not change."
    )


async def learn_screen(task: TaskState, adapter: CuaAdapter) -> RecipeResult:
    """Look at this window once and remember where every control is."""
    surface = await _scene_surface(task, adapter)
    if isinstance(surface, str):
        return RecipeResult("learn_screen", OutcomeState.NO_EFFECT, surface)
    target, pid, window_id = surface
    state = await adapter.observe_window(task, pid, window_id, max_elements=1500, settle=True)
    state_out, sentence = await sight.learn_controls(
        task, adapter, pid=pid, window_id=window_id,
        scope=sight.scope_from_state(target.app.name, state),
    )
    return RecipeResult("learn_screen", state_out, sentence)


#: The recipes a candidate may name. The mapping is the executor's authority:
#: a JEV-selected name outside it is a contract error, not a missing tool.
RECIPE_TABLE: dict[str, Any] = {
    "open_app": open_app,
    "navigate": navigate,
    "search_browser": search_browser,
    "scroll": scroll,
    "type_text": type_text,
    "press_ordinal": press_ordinal,
    "describe_screen": describe_screen,
    "press_item": press_item,
    "click_named": click_named,
    "fill_field": fill_field,
    "download_images": download_images,
    "click_in_utterance": click_in_utterance,
    "step_item": step_item,
    "learn_screen": learn_screen,
}


async def execute(
    recipe: str, task: TaskState, adapter: CuaAdapter, **kwargs: Any
) -> RecipeResult:
    """Dispatch one recipe by name; unknown names fail loudly."""
    runner = RECIPE_TABLE.get(recipe)
    if runner is None:
        raise ValueError(f"unknown recipe {recipe!r}")
    return await runner(task, adapter, **kwargs)


__all__ = [
    "RECIPE_TABLE",
    "RecipeResult",
    "execute",
    "navigate",
    "open_app",
    "press_ordinal",
    "click_in_utterance",
    "click_named",
    "describe_screen",
    "download_images",
    "learn_screen",
    "fill_field",
    "press_item",
    "scroll",
    "search_browser",
    "step_item",
    "type_text",
]

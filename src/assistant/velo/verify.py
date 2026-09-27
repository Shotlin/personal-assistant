"""Outcome verification: postconditions checked against fresh evidence.

A dispatched action is not a completed task (master plan section 8). Only a
``confirmed`` outcome supports a completion claim, and confirmation means the
postcondition the objective owes was observed on the screen afterwards: the
requested browser is the one running, the query reached the address field,
the video shows a Pause control, the viewport changed. A repeated action is
never the default response to uncertainty — a failed check is reported as
what it is.
"""

from __future__ import annotations

from urllib.parse import urlparse

from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import (
    AppIdentity,
    Postcondition,
    PostconditionKind,
    TaskState,
    Verification,
)


def _element_text(element: dict[str, object]) -> str:
    """Everything one element says, for substring evidence."""
    parts = (
        element.get("value"),
        element.get("label"),
        element.get("title"),
        element.get("description"),
    )
    return " ".join(str(part) for part in parts if part).lower()


def _window_elements(state: dict[str, object]) -> list[dict[str, object]]:
    elements = state.get("elements")
    if not isinstance(elements, list):
        return []
    return [element for element in elements if isinstance(element, dict)]


def _editable(element: dict[str, object]) -> bool:
    role = str(element.get("role") or "").lower()
    return role in {"axtextfield", "axtextarea", "axcombobox", "axsearchfield"}


async def _app_by_identity(
    adapter: CuaAdapter, task: TaskState, app: AppIdentity
) -> AppIdentity | None:
    """The app as the driver sees it *now*, matched on pid or bundle identity."""
    apps = await adapter.list_apps(task)
    for candidate in apps:
        if app.pid is not None and candidate.pid == app.pid:
            return candidate
        if app.bundle_id and candidate.bundle_id == app.bundle_id:
            return candidate
        if not app.bundle_id and app.name and candidate.name.lower() == app.name.lower():
            return candidate
    return None


def _has_usable_window(state: dict[str, object]) -> bool:
    """The requested app must show a surface, not merely appear in a list."""
    return bool(_window_elements(state))


async def check(
    adapter: CuaAdapter,
    task: TaskState,
    postcondition: Postcondition,
    *,
    expect_pid: int | None = None,
    expect_window_id: int | None = None,
) -> Verification:
    """One postcondition, one fresh observation, one honest answer."""
    kind = postcondition.kind
    if kind in (PostconditionKind.APP_RUNNING, PostconditionKind.APP_FOREGROUND):
        return await _check_app(adapter, task, postcondition, foreground=kind is PostconditionKind.APP_FOREGROUND)
    if kind is PostconditionKind.NAVIGATED:
        return await _check_text_evidence(
            adapter, task, postcondition, _destination_markers(postcondition.url),
            kind=kind, expect_pid=expect_pid, expect_window_id=expect_window_id,
        )
    if kind is PostconditionKind.SEARCHED:
        markers = _destination_markers(postcondition.url)
        query = postcondition.query.strip().lower()
        if query:
            markers = (query, *markers)
        return await _check_text_evidence(
            adapter, task, postcondition, markers,
            kind=kind, expect_pid=expect_pid, expect_window_id=expect_window_id,
        )
    if kind is PostconditionKind.TEXT_IN_FIELD:
        return await _check_text_evidence(
            adapter, task, postcondition, (postcondition.text.strip().lower(),),
            kind=kind, expect_pid=expect_pid, expect_window_id=expect_window_id,
            editable_only=True,
        )
    if kind is PostconditionKind.PLAYBACK_STARTED:
        return await _check_playback(adapter, task, expect_pid=expect_pid, expect_window_id=expect_window_id)
    if kind is PostconditionKind.VIEWPORT_CHANGED:
        return await _check_viewport(adapter, task, postcondition, expect_pid=expect_pid, expect_window_id=expect_window_id)
    return Verification(satisfied=False, kind=kind, detail=f"unknown postcondition {kind}")


def _destination_markers(destination: str) -> tuple[str, ...]:
    """What the screen must show for this destination: host, then full URL."""
    destination = destination.strip()
    if not destination:
        return ()
    markers = [destination.lower().removesuffix("/")]
    host = urlparse(destination if "://" in destination else f"https://{destination}").hostname
    if host and host.lower() not in markers:
        markers.append(host.lower())
    return tuple(markers)


async def _check_app(
    adapter: CuaAdapter, task: TaskState, postcondition: Postcondition, *, foreground: bool
) -> Verification:
    target = task.resolved
    if target is None:
        return Verification(False, postcondition.kind, detail="no resolved target to check")
    app = await _app_by_identity(adapter, task, target.app)
    if app is None or not app.running:
        return Verification(
            False,
            postcondition.kind,
            evidence={"wanted": target.app.name},
            detail=f"{target.app.name} is not running",
        )
    window_id = await adapter.front_window(task, app.pid) if app.pid else None
    state: dict[str, object] = {}
    if app.pid and window_id is not None:
        state = await adapter.observe_window(task, app.pid, window_id, for_verification=True)
    evidence = {"pid": app.pid, "window_id": window_id, "active": app.active}
    if not app.running or window_id is None:
        return Verification(False, postcondition.kind, evidence=evidence, detail="no on-screen window")
    if foreground and not app.active:
        return Verification(
            False, postcondition.kind, evidence=evidence, detail=f"{app.name} is not in the foreground"
        )
    if not _has_usable_window(state):
        return Verification(
            False, postcondition.kind, evidence=evidence, detail="window tree could not be read"
        )
    return Verification(True, postcondition.kind, evidence=evidence)


async def _check_text_evidence(
    adapter: CuaAdapter,
    task: TaskState,
    postcondition: Postcondition,
    markers: tuple[str, ...],
    *,
    kind: PostconditionKind,
    expect_pid: int | None,
    expect_window_id: int | None,
    editable_only: bool = False,
) -> Verification:
    """Marker text visible in the window's elements (address bar, field, page)."""
    pid = expect_pid if expect_pid is not None else (task.resolved.app.pid if task.resolved else None)
    window_id = expect_window_id if expect_window_id is not None else (task.resolved.window_id if task.resolved else None)
    if pid is None or window_id is None:
        return Verification(False, kind, detail="no bound surface to verify against")
    state = await adapter.observe_window(task, pid, window_id, for_verification=True)
    elements = _window_elements(state)
    if editable_only:
        elements = [element for element in elements if _editable(element)]
    for element in elements:
        text = _element_text(element)
        for marker in markers:
            if marker and marker in text:
                return Verification(
                    True,
                    kind,
                    evidence={
                        "pid": pid,
                        "window_id": window_id,
                        "element_role": str(element.get("role") or ""),
                        "marker": marker,
                    },
                )
    return Verification(
        False,
        kind,
        evidence={"pid": pid, "window_id": window_id, "markers": list(markers)},
        detail="none of the expected markers were observed in the window",
    )


async def _check_playback(
    adapter: CuaAdapter,
    task: TaskState,
    *,
    expect_pid: int | None,
    expect_window_id: int | None,
) -> Verification:
    """Playback evidence: a Pause control showing, or a play toggle already on."""
    pid = expect_pid if expect_pid is not None else (task.resolved.app.pid if task.resolved else None)
    window_id = expect_window_id if expect_window_id is not None else (task.resolved.window_id if task.resolved else None)
    if pid is None or window_id is None:
        return Verification(False, PostconditionKind.PLAYBACK_STARTED, detail="no bound surface")
    state = await adapter.observe_window(task, pid, window_id, for_verification=True)
    for element in _window_elements(state):
        text = _element_text(element)
        if "pause" in text:
            return Verification(
                True,
                PostconditionKind.PLAYBACK_STARTED,
                evidence={"element_label": str(element.get("label") or "")[:60]},
            )
    return Verification(
        False,
        PostconditionKind.PLAYBACK_STARTED,
        detail="no Pause control observed; playback could not be confirmed",
    )


async def _check_viewport(
    adapter: CuaAdapter,
    task: TaskState,
    postcondition: Postcondition,
    *,
    expect_pid: int | None,
    expect_window_id: int | None,
) -> Verification:
    """Scroll evidence: the surface's observed content differs from before."""
    pid = expect_pid if expect_pid is not None else (task.resolved.app.pid if task.resolved else None)
    window_id = expect_window_id if expect_window_id is not None else (task.resolved.window_id if task.resolved else None)
    if pid is None or window_id is None:
        return Verification(False, PostconditionKind.VIEWPORT_CHANGED, detail="no bound surface")
    state = await adapter.observe_window(task, pid, window_id, for_verification=True)
    digest = CuaAdapter.scene_digest(state)
    satisfied = bool(postcondition.before_digest) and digest != postcondition.before_digest
    return Verification(
        satisfied,
        PostconditionKind.VIEWPORT_CHANGED,
        evidence={"pid": pid, "window_id": window_id, "digest": digest},
        detail="" if satisfied else "the observed surface did not change",
    )


__all__ = ["check"]

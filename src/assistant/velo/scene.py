"""What is on screen, as a short numbered list the user can refer back to.

CUA's window tree is the source of truth. This module only *reads* it: it
picks the content items (video results, otherwise labelled links), flags
advertisements, and remembers the list per conversation so a follow-up such
as "play the second one" means the item that was just described. Tokens go
stale, so a follow-up always re-reads the page and re-matches by title.
Nothing here invents content or calls a model.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

MAX_ITEMS = 8
_MAX_CONVERSATIONS = 32

_VIDEO_SIGNAL = re.compile(
    r"\b(?:\d[\d,.]*\s*[KMB]?\s+(?:views?|watching)|\d+\s+(?:seconds?|minutes?|hours?)"
    r"|shorts?|live)\b",
    re.I,
)
_YT_LABEL = re.compile(
    r"^(?P<title>.+?)\s+by\s+(?P<channel>.+?)\s+"
    r"(?:\d[\d,.]*\s*[KMB]?\s+(?:views?|watching)|\d+\s+(?:seconds?|minutes?|hours?))",
    re.I,
)
_AD = re.compile(r"\b(?:sponsored|advertisement)\b|^ad\b|\bad\s*[·•-]|\bad\b\s*$", re.I)
_NAV = re.compile(
    r"^(?:home|shorts|subscriptions|you|history|menu|search|sign in|create|notifications|"
    r"skip|back|forward|reload|close|settings|guide)\b",
    re.I,
)


@dataclass(frozen=True)
class SceneItem:
    index: int
    title: str
    channel: str
    is_ad: bool
    token: str

    def spoken(self) -> str:
        prefix = "an advertisement, " if self.is_ad else ""
        by = f", by {self.channel}" if self.channel and not self.is_ad else ""
        return f"{prefix}{self.title}{by}"


def _link_like(role: str) -> bool:
    return role.lower().removeprefix("ax") in {"link", "button"}


_DURATION = re.compile(r"[\s,]+(?:\d+\s+(?:hours?|minutes?|seconds?)[,\s]*)+$", re.I)
_MARKER = re.compile(r"^(?:sponsored|ad)$", re.I)
_AD_BUTTONS = {"watch", "visit site", "learn more", "shop now", "install", "sign up"}
#: An element needs a real on-screen height; virtualised off-screen rows report ~1pt.
_MIN_VISIBLE_HEIGHT = 20.0


def _visible(element: dict[str, Any], *, strict: bool = False) -> bool:
    """On screen. ``strict``: the page reports frames, so a missing one is unknown."""
    frame = element.get("frame")
    if not isinstance(frame, dict):
        return not strict
    try:
        return float(frame.get("h", 0)) >= _MIN_VISIBLE_HEIGHT
    except (TypeError, ValueError):
        return False


def _reading_key(entry: tuple[int, dict[str, Any], str, str, bool]) -> tuple[int, int, int]:
    order, element, *_ = entry
    frame = element.get("frame")
    if isinstance(frame, dict):
        try:
            return (round(float(frame.get("y", 0)) / 40), round(float(frame.get("x", 0))), order)
        except (TypeError, ValueError):
            pass
    return (0, 0, order)


def extract_items(state: dict[str, Any], *, fallback: bool = True) -> list[SceneItem]:
    """On-screen content items in reading order, de-duplicated, ads flagged.

    Real YouTube structure (observed live): a video is an ``AXHeading`` with
    the title plus an ``AXLink`` labelled "<title> <duration>"; an ad is a
    link that directly precedes a "Sponsored" label and has no heading.
    Only elements with a real on-screen height count, so the list is what the
    user can actually see.
    """
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    strict = any(isinstance(e.get("frame"), dict) for e in elements)
    headings = {
        " ".join(str(e.get("label") or "").split())
        for e in elements
        if str(e.get("role") or "").lower().removeprefix("ax") == "heading"
    }
    entries: list[tuple[int, dict[str, Any], str, str, bool]] = []
    links = [
        (n, e) for n, e in enumerate(elements)
        if str(e.get("role") or "").lower().removeprefix("ax") == "link"
        and e.get("element_token")
    ]
    for n, element in links:
        label = " ".join(str(element.get("label") or "").split())
        if len(label) < 8 or not _visible(element, strict=strict):
            continue
        title = _DURATION.sub("", label).strip()
        listed = title != label or title in headings
        legacy = _VIDEO_SIGNAL.search(label) is not None
        if not (listed or legacy):
            continue
        match = _YT_LABEL.match(label) if legacy else None
        entries.append((
            n,
            element,
            match.group("title").strip() if match else title,
            match.group("channel").strip() if match else "",
            bool(_AD.search(label)),
        ))

    # Ads: the link just before a "Sponsored" marker (no heading, no duration).
    have = {id(entry[1]) for entry in entries}
    for n, element in enumerate(elements):
        text = " ".join(str(element.get("label") or element.get("value") or "").split())
        if not _MARKER.match(text):
            continue
        for back in range(n - 1, max(-1, n - 6), -1):
            cand = elements[back]
            label = " ".join(str(cand.get("label") or "").split())
            if (
                str(cand.get("role") or "").lower().removeprefix("ax") == "link"
                and cand.get("element_token")
                and len(label) >= 12
                and label.lower() not in _AD_BUTTONS
            ):
                if id(cand) not in have and _visible(cand, strict=strict):
                    entries.append((back, cand, label, "", True))
                    have.add(id(cand))
                break

    entries.sort(key=_reading_key)
    if not entries and fallback:
        # Not a video listing: fall back to the page's labelled links, minus chrome.
        for n, element in links:
            label = " ".join(str(element.get("label") or "").split())
            if len(label) >= 12 and _visible(element, strict=strict) and not _NAV.match(label):
                entries.append((n, element, label, "", bool(_AD.search(label))))
        entries.sort(key=_reading_key)

    items: list[SceneItem] = []
    seen: set[str] = set()
    for _n, element, title, channel, is_ad in entries:
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        token = str(element["element_token"])
        items.append(SceneItem(len(items) + 1, title, channel, is_ad, token))
        if len(items) >= MAX_ITEMS:
            break
    return items


def page_title(state: dict[str, Any]) -> str:
    """The window title without YouTube's unread-count prefix and site suffix."""
    for key in ("window_title", "title"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            title = re.sub(r"^\(\d+\)\s*", "", value.strip())
            title = re.sub(r"\s*[-–]\s*(?:YouTube|Google Chrome).*$", "", title).strip()
            return title if title.lower() != "youtube" else ""
    return ""


def describe(items: list[SceneItem], *, app: str, title: str) -> str:
    """The spoken/printed description; short on purpose (it is read aloud)."""
    where = f"{app}" + (f", {title}" if title else "")
    if not items:
        return (
            f"I looked at {where} but I can't pick out separate items on this page. "
            "Tell me the exact title or what to click."
        )
    listing = "; ".join(f"{item.index}, {item.spoken()}" for item in items)
    return f"I see in {where}: {listing.rstrip('.')}. Say the number or the title to play one."


_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 1}


def match_title(items: list[SceneItem], spoken: str) -> SceneItem | None:
    """The item whose title best matches ``spoken``; ``None`` when unsure.

    Needs at least half of the spoken words in the title and a clear winner,
    so "play music" never matches a video by accident.
    """
    want = _words(spoken)
    if not want:
        return None
    scored = sorted(
        ((len(want & _words(i.title)) / len(want), i) for i in items), key=lambda p: -p[0]
    )
    if not scored or scored[0][0] < 0.5:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


class SceneStore:
    """Bounded per-conversation memory of the last described screen."""

    def __init__(self) -> None:
        self._scenes: OrderedDict[str, list[SceneItem]] = OrderedDict()

    def remember(self, conversation: str, items: list[SceneItem]) -> None:
        if not conversation:
            return
        self._scenes[conversation] = items
        self._scenes.move_to_end(conversation)
        while len(self._scenes) > _MAX_CONVERSATIONS:
            self._scenes.popitem(last=False)

    def recall(self, conversation: str) -> list[SceneItem]:
        return list(self._scenes.get(conversation, ()))

    def clear(self) -> None:
        self._scenes.clear()


SCENES = SceneStore()


# ---------------------------------------------------------------------------
# Model-facing observation: what is on screen, compactly
# ---------------------------------------------------------------------------

#: Roles worth a line. Containers, images and layout rows are noise to a model
#: that only needs to know what it can read and press.
_ACTIONABLE = {
    "button", "link", "textfield", "textarea", "combobox", "searchfield", "checkbox",
    "radiobutton", "popupbutton", "menuitem", "menubaritem", "tab", "slider", "switch",
}
_TEXTY = {"heading", "statictext"}
MAX_OBSERVATION_LINES = 130


_TOKENISH = re.compile(
    r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{28,}\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b[A-Z][A-Z0-9_]{3,}\s*=\s*\S+"
)
HIDDEN = "[hidden: looks like a secret]"


def safe_text(text: str) -> str:
    """``text`` unless it looks like a credential, which is never read, shown or sent.

    Screens routinely contain keys, tokens and passwords (a note, a terminal, a
    settings page). Describing the screen aloud or handing it to a model must
    not become a way to leak them.
    """
    from assistant.observability.logging import redact

    if _TOKENISH.search(text) or redact(text) != text:
        return HIDDEN
    return text


def _role(element: dict[str, Any]) -> str:
    return str(element.get("role") or "").lower().removeprefix("ax")


def compact_observation(state: dict[str, Any]) -> str | None:
    """The visible part of a window as a short, ordered list; ``None`` to keep raw.

    The raw tree puts browser chrome first, includes thousands of off-screen
    virtualised rows (frame height ~1pt) and repeats every title three times
    (heading, link, static text); a model handed the truncated head never sees
    the control the user named. This lists only on-screen elements, one line
    each, with the token a click needs. Needs per-element frames; without them
    there is nothing to tell visible from hidden, so the raw text stays.
    """
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    if not elements or not any(isinstance(e.get("frame"), dict) for e in elements):
        return None
    visible = [e for e in elements if _visible(e, strict=True)]
    hidden = len(elements) - len(visible)

    rows: list[tuple[int, dict[str, Any], str]] = []
    claimed: set[str] = set()
    for n, element in enumerate(visible):
        role = _role(element)
        label = " ".join(str(element.get("label") or element.get("value") or "").split())
        if not label or (role not in _ACTIONABLE and role not in _TEXTY):
            continue
        base = _DURATION.sub("", label).strip().lower()
        if role in _ACTIONABLE:
            claimed.add(base)
            rows.append((n, element, label))
    for n, element in enumerate(visible):
        role = _role(element)
        label = " ".join(str(element.get("label") or element.get("value") or "").split())
        if not label or role not in _TEXTY:
            continue
        # A title already shown by its link/button is a duplicate, not new info.
        if _DURATION.sub("", label).strip().lower() in claimed:
            continue
        rows.append((n, element, label))
    rows.sort(key=lambda r: _reading_key((r[0], r[1], "", "", False)))

    head = (
        f'{state.get("app_name") or "Window"} - "{state.get("window_title") or ""}" '
        f"(snapshot {state.get('snapshot_id', '?')}): {len(rows)} visible items"
    )
    lines = [head]
    for _n, element, label in rows[:MAX_OBSERVATION_LINES]:
        token = element.get("element_token") or ""
        frame = element.get("frame") or {}
        where = f"@{round(float(frame.get('x', 0)))},{round(float(frame.get('y', 0)))}"
        role = str(element.get("role") or "")
        value = str(element.get("value") or "")
        extra = f' = "{safe_text(value[:60])}"' if value and value != label and _role(
            element) in {"textfield", "textarea", "combobox", "searchfield"} else ""
        handle = f"[{token}] " if token else ""
        lines.append(f'{handle}{role} "{safe_text(label[:110])}"{extra} {where}')
    if len(rows) > MAX_OBSERVATION_LINES:
        lines.append(f"... {len(rows) - MAX_OBSERVATION_LINES} more visible items omitted")
    if hidden:
        lines.append(
            f"({hidden} elements are off-screen; scroll to reveal them, or re-observe with a "
            "`query` for a specific label.)"
        )
    lines.append("Act with click(element_token=...) on the item whose label matches the request.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# "Click the Create button": resolve a spoken name to ONE visible element
# ---------------------------------------------------------------------------

_LABEL_NOISE = re.compile(
    r"\b(?:the|a|an|button|link|tab|section|icon|option|menu|item|on|please)\b", re.I
)


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(_LABEL_NOISE.sub(" ", text.lower())))


def find_by_label(state: dict[str, Any], wanted: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The visible actionable element a spoken name refers to.

    Returns ``(element, alternatives)``. ``element`` is ``None`` when nothing
    matches or the name is genuinely ambiguous (then ``alternatives`` lists the
    distinct labels so the user can choose). Exact label beats prefix beats
    word-subset; identical labels resolve to the first in reading order.
    """
    want = _norm(wanted)
    if not want:
        return None, []
    want_words = set(want.split())
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    strict = any(isinstance(e.get("frame"), dict) for e in elements)
    scored: list[tuple[int, int, dict[str, Any], str]] = []
    for n, element in enumerate(elements):
        role = _role(element)
        if role not in _ACTIONABLE or not element.get("element_token"):
            continue
        if not _visible(element, strict=strict):
            continue
        label = " ".join(str(element.get("label") or element.get("value") or "").split())
        # Only the spoken name loses filler words ("button", "section"); the
        # element's own label must stay intact ("Tab search" is not "search").
        have = " ".join(_WORD.findall(_DURATION.sub("", label).lower()))
        if not have:
            continue
        if have == want:
            score = 3
        elif have.startswith(want + " ") or have.endswith(" " + want):
            score = 2
        elif want_words <= set(have.split()) and len(want_words) >= 1:
            score = 1
        else:
            continue
        scored.append((score, n, element, label))
    if not scored:
        return None, []
    top = max(s[0] for s in scored)
    best = [s for s in scored if s[0] == top]
    best.sort(key=lambda s: _reading_key((s[1], s[2], "", "", False)))
    distinct = list(dict.fromkeys(s[3] for s in best))
    if len(distinct) > 1 and top < 3:
        return None, distinct[:4]
    return best[0][2], distinct[:4]


# ---------------------------------------------------------------------------
# "In the input box type ...": pick the field the user means
# ---------------------------------------------------------------------------

_FIELD_ROLES = {"textfield", "textarea", "combobox", "searchfield"}
_NOT_A_CHAT_FIELD = re.compile(r"address|url|search with|find in page|tab search", re.I)


def find_field(state: dict[str, Any], target: str = "") -> dict[str, Any] | None:
    """The visible editable field a request means, or ``None``.

    A named field ("search", "message") matches by label; otherwise the field
    that already has focus wins, then a multi-line text area, then the lowest
    one on screen (chat and prompt boxes sit at the bottom). The browser's
    address bar is never an implicit choice.
    """
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    strict = any(isinstance(e.get("frame"), dict) for e in elements)
    fields = [
        (n, e) for n, e in enumerate(elements)
        if _role(e) in _FIELD_ROLES and e.get("element_token") and _visible(e, strict=strict)
    ]
    if not fields:
        return None
    candidates = [
        (n, e) for n, e in fields
        if not _NOT_A_CHAT_FIELD.search(str(e.get("label") or ""))
    ] or fields
    want = set(_norm(target).split()) - {"input", "text", "box", "field", "bar", "area"}
    if want:
        named = [
            (n, e) for n, e in candidates
            if want <= set(_WORD.findall(str(e.get("label") or e.get("value") or "").lower()))
        ]
        if named:
            return named[0][1]

    def rank(entry: tuple[int, dict[str, Any]]) -> tuple[int, int, float]:
        _n, element = entry
        focused = bool(element.get("focused") or element.get("is_focused"))
        multiline = _role(element) == "textarea"
        frame = element.get("frame") if isinstance(element.get("frame"), dict) else {}
        y = float(frame.get("y", 0)) if frame else 0.0
        return (0 if focused else 1, 0 if multiline else 1, -y)

    return sorted(candidates, key=rank)[0][1]


class PendingChoice:
    """The options Sani just asked about, so a short answer resolves them."""

    def __init__(self) -> None:
        self._pending: OrderedDict[str, tuple[str, tuple[str, ...]]] = OrderedDict()

    def ask(self, conversation: str, recipe: str, options: tuple[str, ...]) -> None:
        if conversation and options:
            self._pending[conversation] = (recipe, options)
            while len(self._pending) > _MAX_CONVERSATIONS:
                self._pending.popitem(last=False)

    def take(self, conversation: str, answer: str) -> tuple[str, str] | None:
        """``(recipe, option)`` when ``answer`` picks one pending option, else ``None``."""
        entry = self._pending.get(conversation)
        if entry is None:
            return None
        recipe, options = entry
        said = " ".join(answer.lower().split()).strip(" .!?")
        ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1": 1, "2": 2, "3": 3,
                    "4": 4, "one": 1, "two": 2, "three": 3, "four": 4}
        words = said.replace("the ", "").replace(" one", "").strip()
        index = ordinals.get(words)
        chosen: str | None = options[index - 1] if index and index <= len(options) else None
        if chosen is None:
            hits = [o for o in options if said and said in o.lower()]
            if len(hits) == 1:
                chosen = hits[0]
        if chosen is None:
            return None
        self._pending.pop(conversation, None)
        return recipe, chosen

    def clear(self, conversation: str | None = None) -> None:
        if conversation is None:
            self._pending.clear()
        else:
            self._pending.pop(conversation, None)


PENDING = PendingChoice()


# ---------------------------------------------------------------------------
# Describe any screen, and notice an app that has not exposed its UI yet
# ---------------------------------------------------------------------------

_CHROME_ROLES = {"menubar", "menubaritem", "menu", "menuitem", "window"}


def looks_unhydrated(state: dict[str, Any]) -> bool:
    """True when only the menu bar and window frame came back.

    Chromium/Electron apps (Codex, Claude, VS Code, Slack) build their
    accessibility tree on the first request, so the first look shows nothing
    but the menu bar. Looking again a moment later returns the real UI.
    """
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    if not elements:
        return False
    content = [
        e for e in elements
        if _role(e) not in _CHROME_ROLES and _visible(e, strict=True) and (
            e.get("label") or e.get("value")
        )
    ]
    return len(content) < 3


def describe_general(state: dict[str, Any], *, app: str, title: str) -> str:
    """What is on any screen, in a few plain sentences: boxes, text, controls."""
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    strict = any(isinstance(e.get("frame"), dict) for e in elements)
    visible = [e for e in elements if _visible(e, strict=strict) and _role(e) not in _CHROME_ROLES]

    def text_of(e: dict[str, Any]) -> str:
        return " ".join(str(e.get("label") or e.get("value") or "").split())

    boxes, buttons, texts = [], [], []
    for e in sorted(visible, key=lambda e: _reading_key((0, e, "", "", False))):
        role, label = _role(e), text_of(e)
        if not label:
            continue
        if role in _FIELD_ROLES:
            # What is typed in a box is never read out: name the box, say if it has text.
            typed = " ".join(str(e.get("value") or "").split())
            has_text = bool(typed) and typed != label  # a placeholder echoes the label
            name = label if len(label) <= 40 and safe_text(label) != HIDDEN else "a text area"
            boxes.append(name + (" (has text)" if has_text else " (empty)"))
        elif role in _ACTIONABLE:
            buttons.append(safe_text(_DURATION.sub("", label)[:50]))
        elif role in _TEXTY and len(label) >= 20:
            texts.append(safe_text(label[:100]))
    # Repeated controls ("Pin chat" on every row) become one phrase with a count.
    counts: dict[str, int] = {}
    for label in buttons:
        counts[label] = counts.get(label, 0) + 1
    controls = [f"{k} ({v} times)" if v > 1 else k for k, v in list(counts.items())[:12]]

    where = app + (f", {title}" if title else "")
    parts = [f"This is {where}."]
    if boxes:
        parts.append("Input boxes: " + "; ".join(boxes[:3]) + ".")
    if texts:
        parts.append("Text: " + " | ".join(dict.fromkeys(texts))[:260] + ".")
    if controls:
        parts.append("Controls: " + ", ".join(controls) + ".")
    if len(parts) == 1:
        parts.append("I can't read anything on it yet; give it a moment and ask again.")
    return " ".join(parts)


def find_in_utterance(state: dict[str, Any], utterance: str) -> dict[str, Any] | None:
    """The visible control whose whole label appears among the spoken words.

    For dictation that wraps the name in chatter ("new project button here that
    button you click"): the label is whatever on-screen control has every one of
    its words in the sentence, preferring the longest label.
    """
    spoken = set(_WORD.findall(utterance.lower()))
    elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
    strict = any(isinstance(e.get("frame"), dict) for e in elements)
    best: tuple[int, int, dict[str, Any]] | None = None
    for n, element in enumerate(elements):
        if _role(element) not in _ACTIONABLE or not element.get("element_token"):
            continue
        if not _visible(element, strict=strict):
            continue
        words = _WORD.findall(_DURATION.sub("", str(element.get("label") or "")).lower())
        words = [w for w in words if w not in {"button", "link", "icon"}]
        if not words or len(" ".join(words)) < 4 or not set(words) <= spoken:
            continue
        key = (len(words), -n)
        if best is None or key > (best[0], -best[1]):
            best = (len(words), n, element)
    return best[2] if best else None

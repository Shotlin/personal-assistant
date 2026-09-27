"""Route A parsing: fully resolved ordinary commands, resolved mechanically.

The parser is deliberately narrow (the historical fast path's shapes, with two
defects the incident exposed removed):

- An explicit application name is an *identity*. "Safari" resolves to Safari;
  only the literal word "browser" asks for the browser category.
- Site-scoped search ("search YouTube for jazz") is one command with one exact
  payload -- it must not acquire an unrelated playback objective from
  conversation history, and the query is carried byte-exact.

Anything this module cannot resolve confidently returns ``None``: questions,
multi-step requests, and goal-shaped text belong to the interpretation route
or the general planner, never to a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class ParsedCommand:
    """One fully resolved imperative: a recipe and its exact arguments."""

    recipe: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    #: The application the instruction explicitly named, if any.
    requested_app: str = ""
    utterance: str = ""


_OPEN_APP = re.compile(r"^(?:open|launch|start|bring\s+up)\s+(?P<app>.+?)\s*$", re.I)
_OPEN_URL = re.compile(
    r"^(?:open|go\s+to|visit|take\s+me\s+to)\s+"
    r"(?P<url>[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?)\s*$",
    re.I,
)
#: "Use Safari instead" / "switch to Safari": a target correction, executed
#: against the named app, invalidating whatever the task held before.
_SWITCH_APP = re.compile(
    r"^(?:use|switch\s+to|go\s+to)\s+(?P<app>.+?)\s+instead\s*$", re.I
)
_SCROLL = re.compile(
    r"^scroll\s+(?P<direction>up|down|left|right)(?:\s+(?P<amount>\d+))?\s*$", re.I
)
_PRESS_ORDINAL = re.compile(
    r"^(?:press|click|hit|tap)\s+(?:the\s+)?(?P<ordinal>first|second|third|fourth|fifth|"
    r"sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+(?P<kind>link|button)s?\s*$",
    re.I,
)
#: Dictation word order: "first link click", "third link you press" -- the
#: ordinal comes first and the verb trails (live 2026-09-26 03:11: "First
#: Link Click." fell through to the reasoning loop and clicked around).
_PRESS_ORDINAL_TAIL = re.compile(
    r"^(?:the\s+)?(?P<ordinal>first|second|third|fourth|fifth|"
    r"sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+(?P<kind>link|button)s?"
    r"(?:\s+you)?\s*(?:press|click|hit|tap)s?\s*[.,!?]?\s*$",
    re.I,
)
#: "type" is dictation of exact text; "write X" is content generation and
#: belongs to the reasoning route.
_TYPE_TEXT = re.compile(r"^(?:type)\s+(?P<text>.+?)\s*$", re.I)
#: "Search YouTube for jazz" / "search for jazz" / "look up jazz".
_SEARCH = re.compile(
    r"^(?:search|look\s+up)(?:\s+(?:on\s+)?(?P<site>youtube|google|the\s+web))?\s+"
    r"(?:for\s+)?(?P<query>.+?)\s*$",
    re.I,
)

#: "Safari, not Chrome" / "open Safari not Chrome": negation is part of the
#: instruction, and the negated app must not be the one that opens.
_NEGATION = re.compile(r"^(?P<rest>.+?)\s*,?\s+not\s+.+$", re.I)

ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

#: Two commands in one utterance is a goal, and goals belong to Route C.
_MULTI_STEP = re.compile(r"\b(?:then|after that|and then)\b", re.I)

#: A second verb inside an open target: "open Chrome and search for music" is
#: a goal wearing an imperative's clothes.
_SECOND_VERB = re.compile(
    r"\b(?:and|then)\b.*\b(?:open|launch|start|search|scroll|press|click|type|visit|go)\b",
    re.I,
)

_TRIM = " \t.,;:!?"


def _clean(value: str) -> str:
    """Dictation ends sentences with punctuation; the app name does not."""
    return value.strip().strip(_TRIM).strip()


def parse(text: str) -> ParsedCommand | None:
    """The one imperative command in ``text``, or ``None`` to defer."""
    cleaned = " ".join((text or "").strip().split())
    if not cleaned or len(cleaned) > 160 or _MULTI_STEP.search(cleaned):
        return None
    if cleaned.endswith("?"):
        return None

    for pattern, builder in (
        (_SWITCH_APP, _build_switch),
        (_OPEN_URL, _build_open_url),
        (_SCROLL, _build_scroll),
        (_PRESS_ORDINAL, _build_press),
        (_PRESS_ORDINAL_TAIL, _build_press),
        (_SEARCH, _build_search),
        (_TYPE_TEXT, _build_type),
        (_OPEN_APP, _build_open_app),
    ):
        match = pattern.match(cleaned)
        if not match:
            continue
        command = builder(match, cleaned)
        if command is not None:
            return command
    return None


def _strip_negation(target: str) -> str | None:
    """``"Safari, not Chrome"`` -> ``"Safari"``; ``None`` when nothing remains."""
    match = _NEGATION.match(target.strip())
    return _clean(match.group("rest")) if match else _clean(target)


def _build_switch(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    app = _clean(match.group("app"))
    if not app or _SECOND_VERB.search(app):
        return None
    return ParsedCommand(
        recipe="open_app", kwargs={"app_name": app}, requested_app=app, utterance=utterance
    )


def _build_open_url(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    return ParsedCommand(
        recipe="navigate", kwargs={"destination": _clean(match.group("url"))},
        utterance=utterance,
    )


def _build_scroll(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    return ParsedCommand(
        recipe="scroll",
        kwargs={
            "direction": match.group("direction").lower(),
            "amount": int(match.group("amount") or 3),
        },
        utterance=utterance,
    )


def _build_press(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    word = match.group("ordinal").lower()
    index = ORDINALS.get(word) or int(re.sub(r"\D", "", word) or 0)
    if index < 1:
        return None
    return ParsedCommand(
        recipe="press_ordinal",
        kwargs={"kind": match.group("kind").lower(), "index": index},
        utterance=utterance,
    )


def _build_search(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    site = (match.group("site") or "").lower()
    query = _clean(match.group("query"))
    if not query:
        return None
    return ParsedCommand(
        recipe="search_browser",
        kwargs={"query": query, "site": site},
        utterance=utterance,
    )


def _build_type(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    text = _clean(match.group("text"))
    if not text:
        return None
    return ParsedCommand(
        recipe="type_text", kwargs={"text": text}, utterance=utterance
    )


def _build_open_app(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    target = match.group("app")
    if _SECOND_VERB.search(target):
        # Half of a goal is exactly what the planner is better at, so hand the
        # whole sentence over instead of doing the first half.
        return None
    app = _strip_negation(target)
    if not app:
        return None
    return ParsedCommand(
        recipe="open_app", kwargs={"app_name": app}, requested_app=app, utterance=utterance
    )


__all__ = ["ParsedCommand", "parse"]

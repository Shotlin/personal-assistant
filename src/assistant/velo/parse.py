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
#: "What do you see on the screen?" -- dictation is loose ("what are you see
#: right now in screen"), so match the shape: a what/which question about
#: seeing or the screen, or an imperative to describe/read the screen.
_DESCRIBE = re.compile(
    r"^(?:hey[, ]+|ok(?:ay)?[, ]+)?(?:"
    r"(?:what|which)\b.*\b(?:see|seeing|screen|showing)\b"
    r"|(?:describe|read|tell\s+me|show\s+me)\b.*\b(?:screen|what\s+you\s+see)\b"
    r")",
    re.I,
)
#: "Play the second video" / "open the 3rd one" / "play number 2".
_PLAY_ORDINAL = re.compile(
    r"^(?:play|open|click|select|choose|watch|press)\s+(?:the\s+|number\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|\d+(?:st|nd|rd|th)?)"
    r"(?:\s+(?:one|video|result|item))?\s*[.,!]?\s*$",
    re.I,
)
#: "Click the Create button" / "hit generate" / "go to the upload section".
#: One named, visible control; resolved against the live screen by the recipe.
_CLICK_NAMED = re.compile(
    r"^(?:click|press|tap|hit|select|choose)\s+(?:on\s+)?(?:the\s+)?"
    r"(?P<label>[^,.!?]{2,60}?)(?:\s+(?:button|link|tab|section|icon|option|menu))?\s*[.!]?$",
    re.I,
)
_GO_TO_LABEL = re.compile(
    r"^go\s+to\s+(?:the\s+)?(?P<label>[^,.!?]{2,60}?)"
    r"(?:\s+(?:section|tab|menu|page|option))?\s*[.!]?$",
    re.I,
)
#: "...codex input box inside you type X" / "in the search box write X and submit".
#: The box phrase can sit anywhere in dictated speech; what follows the verb is the text.
_BOX_THEN_TYPE = re.compile(
    r"(?:^|\b)(?:(?P<target>[a-z]+)\s+)?(?:input|text|prompt|message|chat|search)?\s*"
    r"(?:input\s+box|text\s+box|text\s+field|input\s+field|prompt\s+box|message\s+box|"
    r"chat\s+box|search\s+box|text\s+area|input|box|field)\s*,?\s*(?:inside|in|into)?\s*"
    r"(?:you\s+)?(?:type|write|enter|put)\s+(?P<text>.+)$",
    re.I,
)
_TYPE_THEN_BOX = re.compile(
    r"^(?:type|write|enter)\s+(?P<text>.+?)\s+(?:in|into|inside)\s+(?:the\s+)?"
    r"(?:(?P<target>[a-z]+)\s+)?(?:input\s+box|text\s+box|text\s+field|input\s+field|"
    r"prompt\s+box|message\s+box|chat\s+box|search\s+box|text\s+area|input|box|field)\s*$",
    re.I,
)
_SUBMIT_TAIL = re.compile(
    r"[\s,]*(?:and\s+)?(?:then\s+)?(?:submit(?:\s+it)?|send(?:\s+it)?|press\s+enter|"
    r"hit\s+enter|press\s+return|hit\s+return)\s*[.!]?$",
    re.I,
)
#: "make a prompt for a SaaS website and paste it in the input box": the TEXT is
#: written by a model (one short call), then typed by the same recipe as any
#: dictated text. Keys on the word "prompt" plus a write verb plus a box/paste cue.
_WRITE_VERB = re.compile(r"\b(?:make|made|write|create|generate|draft|prepare|compose)\b", re.I)
_PROMPT_WORD = re.compile(r"\bprompts?\b", re.I)
_BOX_CUE = re.compile(r"\b(?:input|box|field|paste|type|enter|put|submit|send|chat|composer)\b", re.I)
_SUBMIT_WORD = re.compile(r"\b(?:submit|send)\b|press\s+enter|hit\s+enter", re.I)
#: "download all the images ChatGPT generated" / "save all images".
_DOWNLOAD_IMAGES = re.compile(
    r"\b(?:download|save)\b[^.?!]{0,30}\b(?:images?|pictures?|photos?|mockups?)\b", re.I
)
#: "next" / "previous" while a viewer, carousel or gallery is open.
_STEP = re.compile(
    r"^(?P<d>next|previous|prev)(?:\s+(?:one|image|slide|mockup|picture|page|photo|item))?"
    r"\s*[.!]?$",
    re.I,
)
#: "open the first mockup" / "click the 2nd image".
_OPEN_NTH_IMAGE = re.compile(
    r"^(?:open|click|show|select|view)\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|\d+(?:st|nd|rd|th))\s+"
    r"(?:mockup|image|picture|photo|design)s?\s*[.!]?$",
    re.I,
)
#: Loose dictation that clearly asks for a click on a named button, e.g.
#: "now you create new project there new project button here that button you click".
_CLICK_INTENT = re.compile(r"\bclick(?:ed|ing)?\b|\bpress\b|\btap\b|\bhit\b", re.I)
_BUTTON_WORD = re.compile(r"\b(?:button|link|icon|tab)\b", re.I)
#: "learn this screen" / "remember where the buttons are on this website".
_LEARN = re.compile(
    r"\b(?:learn|remember|memori[sz]e|analy[sz]e|study)\b[^.?!]{0,40}"
    r"\b(?:screen|page|website|site|app|buttons?)\b",
    re.I,
)
_COMPOSE_BLOCK = re.compile(
    r"^(?:open|launch|start|scroll|click|press|tap|play|search|go|close|switch|use|watch|"
    r"select|choose)\b",
    re.I,
)
_KEY_WORDS = {"enter", "return", "escape", "esc", "tab", "space", "delete", "backspace",
              "it", "that", "this", "here", "there", "one"}
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
    r"\b(?:and|then|also)\b.*\b(?:open|launch|start|search|scroll|press|click|type|visit|go|"
    r"play|watch|download|write|enter|select|choose|find|create|send|paste|submit|upload|"
    r"attach|close|switch|read|show|make|generate)\b",
    re.I,
)

_TRIM = " \t.,;:!?"

#: Sites a person names instead of a URL. Only these resolve locally; any
#: other "open X" stays an app launch, so nothing is guessed.
KNOWN_SITES = {
    "youtube": "youtube.com",
    "google": "google.com",
    "gmail": "mail.google.com",
    "google maps": "maps.google.com",
    "maps": "maps.google.com",
    "github": "github.com",
    "whatsapp": "web.whatsapp.com",
    "whatsapp web": "web.whatsapp.com",
    "twitter": "x.com",
    "facebook": "facebook.com",
    "instagram": "instagram.com",
    "linkedin": "linkedin.com",
    "reddit": "reddit.com",
    "netflix": "netflix.com",
    "chatgpt": "chatgpt.com",
    "claude": "claude.ai",
    "openrouter": "openrouter.ai",
    "google flow": "flow.google.com",
    "flow": "flow.google.com",
    "gemini": "gemini.google.com",
    "google drive": "drive.google.com",
    "google docs": "docs.google.com",
    "google sheets": "sheets.google.com",
    "notion": "notion.so",
    "figma": "figma.com",
    "canva": "canva.com",
}
#: "open YouTube in Chrome" / "go to gmail" / "open youtube".
_OPEN_SITE = re.compile(
    r"^(?:open|go\s+to|visit|launch|start|take\s+me\s+to)\s+(?:the\s+)?"
    r"(?P<site>[a-z][a-z ]*?)(?:\s+(?:in|on|with|using)\s+(?P<app>[a-z][a-z ]*?))?\s*[.!]?$",
    re.I,
)

#: Dictation wraps commands in filler ("Now open Chrome", "okay, can you scroll
#: down please"); it carries no instruction, so it must not push a plain
#: command off the zero-model path.
_LEAD_FILLER = re.compile(
    r"^(?:(?:hey|hi|ok|okay|now|so|um|uh|and|just|please|also|alright|you|"
    r"can\s+you|could\s+you|will\s+you|would\s+you)\b[\s,.]*)+",
    re.I,
)
_TRAIL_FILLER = re.compile(r"[\s,]*\b(?:please|now|for\s+me)\b[\s.!]*$", re.I)


def _clean(value: str) -> str:
    """Dictation ends sentences with punctuation; the app name does not."""
    return value.strip().strip(_TRIM).strip()


_ACTION_START = re.compile(
    r"^(?:open|launch|start|scroll|click|press|tap|play|type|search|go|close|switch|use|"
    r"watch|select|choose|write|send)\b",
    re.I,
)
_SCREEN_WORDS = re.compile(r"\b(?:screen|title|titles|see|seeing|showing|visible|looking)\b", re.I)
_ASK_WORDS = re.compile(
    r"\b(?:what|which|tell|explain|explained|describe|read|list|show|currently|right\s+now)\b",
    re.I,
)


def _wants_screen_description(cleaned: str) -> bool:
    """Loose, dictation-tolerant: a question/ask about what is seen on screen.

    Speech-to-text mangles word order ("Currently explained my screen inside
    what videos are you see"), so this keys on the presence of a screen word
    and an ask word, never on a fixed phrase. A leading action verb ("open
    Chrome and tell me what you see") is a command, not a question.
    """
    if len(cleaned) > 240 or _ACTION_START.match(cleaned):
        return False
    return bool(_SCREEN_WORDS.search(cleaned) and _ASK_WORDS.search(cleaned))


def parse(text: str) -> ParsedCommand | None:
    """The one imperative command in ``text``, or ``None`` to defer."""
    cleaned = " ".join((text or "").strip().split())
    if len(cleaned) <= 160:
        stripped = _TRAIL_FILLER.sub("", _LEAD_FILLER.sub("", cleaned)).strip()
        # Keep the original when stripping would leave nothing to act on.
        cleaned = stripped or cleaned
    if cleaned and (_DESCRIBE.match(cleaned) or _wants_screen_description(cleaned)):
        return ParsedCommand(recipe="describe_screen", utterance=cleaned)
    step = _STEP.match(_TRAIL_FILLER.sub("", _LEAD_FILLER.sub("", cleaned)).strip()) if cleaned else None
    if step:
        return ParsedCommand(
            recipe="step_item",
            kwargs={"direction": "previous" if step.group("d").lower().startswith("prev") else "next"},
            utterance=cleaned,
        )
    nth = _OPEN_NTH_IMAGE.match(cleaned) if cleaned else None
    if nth:
        word = nth.group("ordinal").lower()
        index = ORDINALS.get(word) or int(re.sub(r"\D", "", word) or 0)
        if index >= 1:
            return ParsedCommand(
                recipe="click_named", kwargs={"label": f"generated image {index}"},
                utterance=cleaned,
            )
    if cleaned and len(cleaned) <= 160 and _LEARN.search(cleaned):
        return ParsedCommand(recipe="learn_screen", utterance=cleaned)
    if cleaned and len(cleaned) <= 200 and _DOWNLOAD_IMAGES.search(cleaned):
        return ParsedCommand(recipe="download_images", utterance=cleaned)
    if (
        len(cleaned) <= 400
        and _PROMPT_WORD.search(cleaned)
        and _WRITE_VERB.search(cleaned)
        and _BOX_CUE.search(cleaned)
        and not _COMPOSE_BLOCK.match(cleaned)
    ):
        return ParsedCommand(
            recipe="compose_fill",
            kwargs={"brief": cleaned, "submit": bool(_SUBMIT_WORD.search(cleaned))},
            utterance=cleaned,
        )
    if not cleaned or len(cleaned) > 160 or _MULTI_STEP.search(cleaned):
        return None
    if cleaned.endswith("?"):
        return None

    typed = _TYPE_THEN_BOX.match(cleaned) or _BOX_THEN_TYPE.search(cleaned)
    if typed is not None:
        command = _fill_command(typed.group("text"), typed.groupdict().get("target") or "", cleaned)
        if command is not None:
            return command

    for pattern, builder in (
        (_PLAY_ORDINAL, _build_play_ordinal),
        (_SWITCH_APP, _build_switch),
        (_OPEN_URL, _build_open_url),
        (_OPEN_SITE, _build_open_site),
        (_SCROLL, _build_scroll),
        (_PRESS_ORDINAL, _build_press),
        (_PRESS_ORDINAL_TAIL, _build_press),
        (_SEARCH, _build_search),
        (_CLICK_NAMED, _build_click_named),
        (_TYPE_TEXT, _build_type),
        (_OPEN_APP, _build_open_app),
        (_GO_TO_LABEL, _build_click_named),
    ):
        match = pattern.match(cleaned)
        if not match:
            continue
        command = builder(match, cleaned)
        if command is not None:
            return command
    if _CLICK_INTENT.search(cleaned) and _BUTTON_WORD.search(cleaned):
        # Chatty dictation around a named button: resolve the name on the screen itself.
        return ParsedCommand(
            recipe="click_in_utterance", kwargs={"utterance": cleaned}, utterance=cleaned
        )
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


def _build_open_site(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    site = " ".join(match.group("site").lower().split())
    host = KNOWN_SITES.get(site)
    if host is None:
        return None
    app = _clean(match.group("app") or "")
    return ParsedCommand(
        recipe="navigate",
        kwargs={"destination": host, **({"app_name": app} if app else {})},
        requested_app=app,
        utterance=utterance,
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


def _build_play_ordinal(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    word = match.group("ordinal").lower()
    index = ORDINALS.get(word) or int(re.sub(r"\D", "", word) or 0)
    if not 1 <= index <= 20:
        return None
    return ParsedCommand(recipe="press_item", kwargs={"index": index}, utterance=utterance)


def _build_click_named(match: re.Match[str], utterance: str) -> ParsedCommand | None:
    label = _clean(match.group("label"))
    if not label or label.lower() in _KEY_WORDS:
        return None
    if _SECOND_VERB.search(label) or len(label.split()) > 6:
        # "click the X and open Y" is a goal, not one button's name.
        return None
    return ParsedCommand(recipe="click_named", kwargs={"label": label}, utterance=utterance)


def _split_submit(text: str) -> tuple[str, bool]:
    match = _SUBMIT_TAIL.search(text)
    if not match:
        return text, False
    return text[: match.start()].strip(), True


def _fill_command(text: str, target: str, utterance: str) -> ParsedCommand | None:
    body, submit = _split_submit(_clean(text))
    body = _clean(body)
    if not body:
        return None
    return ParsedCommand(
        recipe="fill_field",
        kwargs={"text": body, "target": (target or "").strip(), "submit": submit},
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
    if _split_submit(text)[1]:
        return _fill_command(text, "", utterance)
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


_CHAIN_SPLIT = re.compile(
    r"\b(?:and\s+then|then|after\s+that|afterwards|next|finally)\b|[.;]", re.I
)
#: Dictation narrates ("then you're gonna click that"); none of it is instruction.
_CLAUSE_FILLER = re.compile(
    r"^(?:(?:and|you(?:'re|\s+are|\s+will|\s+gonna)?|gonna|going\s+to|need\s+to|have\s+to|"
    r"we|i|should|will|okay|ok|so|now|just|please|also|then)\b[\s,]*)+",
    re.I,
)
_WRITE_TEXT = re.compile(r"^(?:write|enter|input|fill(?:\s+in)?)\s+(?P<text>.+?)\s*$", re.I)
MAX_CHAIN_STEPS = 6


def parse_chain(text: str) -> list[ParsedCommand] | None:
    """Several local steps in one sentence, or ``None``.

    "Go to create, click Gemini, then write a girl walking, then hit generate"
    is four ordinary commands. It runs locally ONLY when every clause parses on
    its own; one unfamiliar clause hands the whole sentence to the reasoning
    route, so a half-understood plan never executes.
    """
    cleaned = " ".join((text or "").strip().split())
    if not cleaned or len(cleaned) > 400 or not _MULTI_STEP.search(cleaned):
        return None
    clauses = [c.strip(" ,") for c in _CHAIN_SPLIT.split(cleaned)]
    clauses = [_TRAIL_FILLER.sub("", _CLAUSE_FILLER.sub("", c)).strip() for c in clauses]
    clauses = [c for c in clauses if c]
    if not 2 <= len(clauses) <= MAX_CHAIN_STEPS:
        return None
    commands: list[ParsedCommand] = []
    for clause in clauses:
        write = _WRITE_TEXT.match(clause)
        command = (
            ParsedCommand(
                recipe="type_text", kwargs={"text": _clean(write.group("text"))},
                utterance=clause,
            )
            if write and _clean(write.group("text"))
            else parse(clause)
        )
        if command is None or command.recipe == "describe_screen":
            return None
        commands.append(command)
    return commands


__all__ = ["ParsedCommand", "parse", "parse_chain"]

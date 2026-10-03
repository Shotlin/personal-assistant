"""Turn a long request into a short list of TYPED steps; never act.

One model call reads what the user said and returns JSON naming steps drawn from
a CLOSED set of recipes with typed arguments. Anything outside that set, any
unknown argument, any wrong type, or more than ``MAX_STEPS`` steps is rejected,
so a hallucinated plan cannot execute: it falls back to the reasoning route or to
asking the user. The model writes the plan; the recipes (deterministic code)
resolve every step against the real screen and verify it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from assistant.velo.parse import ParsedCommand

logger = logging.getLogger("assistant.velo.planner")

MAX_STEPS = 8

#: recipe -> {argument: type}. The planner may use ONLY these.
STEP_SCHEMA: dict[str, dict[str, type]] = {
    "open_app": {"app_name": str},
    "navigate": {"destination": str, "app_name": str},
    "search_browser": {"query": str, "site": str},
    "click_named": {"label": str},
    "press_item": {"index": int, "title": str},
    "fill_field": {"text": str, "target": str, "submit": bool},
    "compose_fill": {"brief": str, "submit": bool},
    "type_text": {"text": str},
    "scroll": {"direction": str, "amount": int},
    "step_item": {"direction": str},
    "describe_screen": {},
    "download_images": {},
}
_REQUIRED: dict[str, set[str]] = {
    "open_app": {"app_name"}, "navigate": {"destination"}, "search_browser": {"query"},
    "click_named": {"label"}, "fill_field": {"text"}, "compose_fill": {"brief"},
    "type_text": {"text"}, "scroll": {"direction"}, "step_item": {"direction"},
}

PLANNER_PROMPT = """You plan computer tasks for a voice assistant. The user's words come from \
speech recognition and may be messy; infer what they meant. Reply with ONLY one JSON object:
{"kind": "plan" | "ask" | "chat", "steps": [...], "question": "..."}
- "plan": 1-8 steps, in order, each {"recipe": "<name>", "args": {...}}.
- "ask": a short question when a needed detail is missing (which app? which video? what text?).
- "chat": the user is chatting or asking for knowledge; no computer action is needed.
Allowed recipes and args (use ONLY these; omit args you do not need):
- open_app {app_name}                  open or switch to an application
- navigate {destination, app_name?}    open a website by name or address in a browser
- search_browser {query, site?}        search the web, or a site such as "youtube"
- click_named {label}                  click one visible button/link/tab by its on-screen name
- press_item {index? | title?}         open the Nth or the named item from a list just shown (videos, results)
- fill_field {text, target?, submit?}  click an input box and type EXACT text the user gave
- compose_fill {brief, submit?}        the user wants text WRITTEN for them (a prompt, a message): brief = what to write
- type_text {text}                     type into the focused field
- scroll {direction, amount?}          up/down/left/right
- step_item {direction}                next/previous image, slide or page
- describe_screen {}                   say what is on screen
- download_images {}                   download the images on screen
Rules: use exact words the user gave for names and text; never invent a button label you were \
not told; one action per step; search for a channel or topic with search_browser, then press_item \
to open a result. Do not add steps the user did not ask for."""

REPLAN_NOTE = """The earlier plan stopped. Completed steps: {done}. The step that failed: \
{failed} -> "{answer}". What is on screen now:
{screen}
Return a NEW plan for ONLY the remaining work (not the completed steps), or an "ask" if you \
need the user."""


@dataclass
class Plan:
    kind: str
    steps: list[ParsedCommand] = field(default_factory=list)
    question: str = ""


def _coerce(value: Any, kind: type) -> Any:
    if kind is bool:
        return value if isinstance(value, bool) else None
    if kind is int:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def validate_steps(raw: Any) -> list[ParsedCommand] | None:
    """Typed steps, or ``None`` when ANY step is outside the closed set."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
        return None
    steps: list[ParsedCommand] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        recipe = item.get("recipe")
        args = item.get("args") or {}
        schema = STEP_SCHEMA.get(recipe) if isinstance(recipe, str) else None
        if schema is None or not isinstance(args, dict):
            return None
        clean: dict[str, Any] = {}
        for key, value in args.items():
            if key not in schema:
                return None
            coerced = _coerce(value, schema[key])
            if coerced is None:
                if value in (None, "", False) and schema[key] is not bool:
                    continue
                if schema[key] is bool and value is False:
                    clean[key] = False
                    continue
                return None
            clean[key] = coerced
        if _REQUIRED.get(recipe, set()) - clean.keys():
            return None
        if recipe == "press_item" and not ({"index", "title"} & clean.keys()):
            return None
        if recipe in {"scroll", "step_item"} and clean["direction"].lower() not in {
            "up", "down", "left", "right", "next", "previous"
        }:
            return None
        if recipe in {"scroll", "step_item"}:
            clean["direction"] = clean["direction"].lower()
        steps.append(ParsedCommand(
            recipe=recipe, kwargs=clean,
            requested_app=str(clean.get("app_name", "")),
            utterance=f"{recipe} {json.dumps(clean, ensure_ascii=False)[:120]}",
        ))
    return steps


def parse_plan(text: str) -> Plan | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "").lower()
    if kind == "chat":
        return Plan("chat")
    if kind == "ask":
        question = str(data.get("question") or "").strip()
        return Plan("ask", question=question[:300]) if question else None
    if kind == "plan":
        steps = validate_steps(data.get("steps"))
        return Plan("plan", steps=steps) if steps else None
    return None


_ACTION_WORDS = re.compile(
    r"\b(?:open|go|search|find|click|press|type|write|enter|play|download|save|scroll|select|"
    r"choose|create|launch|start|watch|send|paste|submit|upload|attach|close|switch|visit|"
    r"navigate|look\s+up|make|generate|show|read)\b",
    re.I,
)


def looks_like_a_task(text: str) -> bool:
    """Cheap gate: chat ("hello", "what is 2+2") skips the planner call entirely."""
    return len(text.split()) >= 4 and bool(_ACTION_WORDS.search(text))


async def make_plan(
    model: Any, utterance: str, *, replan: dict[str, str] | None = None, timeout: float = 30
) -> Plan | None:
    """One bounded call. ``None`` on any failure: the caller keeps its fallback."""
    from langchain_core.messages import HumanMessage, SystemMessage

    system = PLANNER_PROMPT
    if replan:
        system += "\n\n" + REPLAN_NOTE.format(**replan)
    try:
        reply = await asyncio.wait_for(
            model.ainvoke([SystemMessage(system), HumanMessage(utterance)]), timeout=timeout
        )
    except Exception as exc:  # noqa: BLE001 -- planning is an optimisation, never fatal
        logger.warning("velo_plan_failed: %s", exc)
        return None
    content = getattr(reply, "content", "")
    if isinstance(content, list):
        content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return parse_plan(str(content))


__all__ = ["MAX_STEPS", "Plan", "STEP_SCHEMA", "looks_like_a_task", "make_plan", "parse_plan",
           "validate_steps"]

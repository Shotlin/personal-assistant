"""Sentence-first labels for Claude Code tool calls (DESIGN: tool activity).

"Edited src/app.ts", "Ran `npm test`", "Searched the code for \"login\"". Raw
input and output stay in the step's technical details, redacted and capped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant.claude_code.redact import screen

#: Tools that change files or run commands. Everything else only reads.
MUTATING_TOOLS = frozenset(
    {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "KillShell", "BashOutput"}
)
_SHELL_TOOLS = frozenset({"Bash"})
_EDIT_TOOLS = frozenset({"Edit", "MultiEdit"})


def _short(text: str, limit: int = 64) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def relative_path(raw: str, cwd: str) -> str:
    """Show a path the way a person would: relative to the project when inside it."""
    if not raw:
        return ""
    path = Path(raw)
    try:
        if cwd and path.is_absolute():
            return str(path.relative_to(cwd))
    except ValueError:
        pass
    try:
        return "~/" + str(path.relative_to(Path.home())) if path.is_absolute() else str(path)
    except ValueError:
        return str(path)


def _diff_snippet(old: str, new: str, limit_lines: int = 24) -> str:
    lines = [f"- {line}" for line in old.splitlines()[:limit_lines]]
    lines += [f"+ {line}" for line in new.splitlines()[:limit_lines]]
    return "\n".join(lines)


def describe_tool(name: str, tool_input: dict[str, Any], cwd: str) -> tuple[str, str, str]:
    """Return ``(label, detail, path)`` for one tool call."""
    path = relative_path(str(tool_input.get("file_path") or tool_input.get("path") or ""), cwd)
    if name in _SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        description = str(tool_input.get("description") or "").strip()
        label = description if description else f"Ran `{_short(command, 56)}`"
        return label, screen(command), ""
    if name == "Read":
        return f"Read {path or 'a file'}", path, path
    if name == "Write":
        content = str(tool_input.get("content") or "")
        return f"Wrote {path or 'a file'}", screen(content[:1200]), path
    if name in _EDIT_TOOLS:
        if name == "MultiEdit":
            edits = tool_input.get("edits") or []
            parts = [
                _diff_snippet(str(e.get("old_string", "")), str(e.get("new_string", "")), 12)
                for e in edits
                if isinstance(e, dict)
            ]
            return f"Edited {path or 'a file'}", screen("\n\n".join(parts)), path
        detail = _diff_snippet(
            str(tool_input.get("old_string") or ""), str(tool_input.get("new_string") or "")
        )
        return f"Edited {path or 'a file'}", screen(detail), path
    if name == "NotebookEdit":
        return f"Edited notebook {path}", "", path
    if name == "Glob":
        pattern = str(tool_input.get("pattern") or "")
        return f"Looked for files matching {_short(pattern, 40)}", pattern, ""
    if name == "Grep":
        pattern = str(tool_input.get("pattern") or "")
        return f"Searched the code for “{_short(pattern, 40)}”", pattern, ""
    if name == "LS":
        return f"Listed {path or 'a folder'}", path, path
    if name == "WebFetch":
        url = str(tool_input.get("url") or "")
        return f"Fetched {_short(url, 56)}", url, ""
    if name == "WebSearch":
        query = str(tool_input.get("query") or "")
        return f"Searched the web for “{_short(query, 40)}”", query, ""
    if name in {"Task", "Agent"}:
        description = str(tool_input.get("description") or "")
        label = f"Delegated: {_short(description, 48)}" if description else "Delegated a subtask"
        return label, "", ""
    if name == "TodoWrite":
        return "Updated its plan", "", ""
    if name == "ExitPlanMode":
        return "Finished planning", "", ""
    if name.startswith("mcp__"):
        _, server, *rest = name.split("__")
        tool = "__".join(rest) or server
        return f"Used {tool.replace('_', ' ')} ({server})", screen(json.dumps(tool_input)[:600]), ""
    return f"Used {name}", screen(json.dumps(tool_input, default=str)[:600]), path


def fingerprint(name: str, tool_input: dict[str, Any]) -> str:
    """Stable identity of a call, so a repeated one can be spotted."""
    try:
        body = json.dumps(tool_input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        body = repr(tool_input)
    return f"{name}:{body}"

"""What Sani lets ZCode do when it asks for permission, decided by the user's run limit.

ZCode is kept in "Ask before changes", so every file change or command arrives as a permission
card that Sani answers. Sani only ever picks "Allow" (this once) or "Deny": never "Always allow in
this project" and never "Full access". Inside "run", a denylist still refuses the worst commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ORDER = {"read": 0, "edit": 1, "run": 2}
_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "TodoWrite"})
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

#: Refused even in "run": privilege escalation, deleting the disk or home, publishing code,
#: piping downloads into a shell, and raw disk tools.
_DENIED_COMMANDS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(^|[\s;&|(])sudo\b",
        r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME|/\*|~/\*)(\s|$)",
        r"\bgit\s+push\b",
        r"\|\s*(sudo\s+)?(sh|bash|zsh)\b",
        r"\b(mkfs|dd\s+if=|diskutil\s+erase)",
        r">\s*/dev/(sd|disk|nvme)",
        r"\bchmod\s+-R\s+[0-7]*777\s+/",
    )
)


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reason: str = ""


def command_is_denied(command: str) -> bool:
    return any(pattern.search(command) for pattern in _DENIED_COMMANDS)


def _inside(raw: str, project: Path) -> bool:
    if not raw:
        return False
    path = Path(raw).expanduser()
    path = path if path.is_absolute() else project / path
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == project or project in resolved.parents


def decide(tool: str, tool_input: dict[str, Any], *, ceiling: str, project: Path) -> Decision:
    """Allow or deny one pending tool call. Unknown tools are denied."""
    project = project.resolve()
    level = _ORDER.get(ceiling, 0)
    if tool in _READ_TOOLS:
        raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if raw and not _inside(raw, project):
            return Decision(False, f"{tool} outside the project folder")
        return Decision(True)
    if tool in _WRITE_TOOLS:
        if level < _ORDER["edit"]:
            return Decision(False, f"{tool} is not allowed when the run limit is look only")
        raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if not _inside(raw, project):
            return Decision(False, f"{tool} outside the project folder")
        return Decision(True)
    if tool == "Bash":
        if level < _ORDER["run"]:
            return Decision(False, "running commands is not allowed by the run limit")
        command = str(tool_input.get("command") or "")
        if command_is_denied(command):
            return Decision(False, "that command is on Sani's never-run list")
        return Decision(True)
    return Decision(False, f"{tool} is not something Sani allows ZCode to do")

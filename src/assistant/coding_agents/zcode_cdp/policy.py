"""What Sani lets ZCode do when it asks for permission, decided by the user's run limit.

ZCode is kept in "Ask before changes", so every file change or command arrives as a permission
card that Sani answers. Sani only ever picks "Allow" (this once) or "Deny": never "Always allow in
this project" and never "Full access". Inside "run", a denylist still refuses the worst commands.
"""

from __future__ import annotations

import re
import shlex
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


_SHELL_META = re.compile(r"[;&|<>`$()\\\n]")


def is_safe_mkdir(command: str, project: Path, base: Path | None = None) -> bool:
    """``mkdir [-p] folder...`` with every folder inside the project, and nothing else in it.

    Making a folder is part of editing, so it is allowed at the "edit" limit; any shell
    trickery (pipes, substitution, redirects, chained commands) is not.
    """
    if _SHELL_META.search(command):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts or parts[0] != "mkdir":
        return False
    folders: list[str] = []
    for part in parts[1:]:
        if part in {"-p", "--parents"}:
            continue
        if part.startswith("-"):
            return False
        folders.append(part)
    return bool(folders) and all(_inside(f, project, base) for f in folders)


#: Commands that only look. ZCode's model likes to double-check its own work with them (ls, cat,
#: grep), and they change nothing, so they are fine at every run limit.
_READONLY = frozenset(
    {
        "ls", "cat", "head", "tail", "wc", "pwd", "stat", "file", "tree", "grep", "egrep",
        "rg", "find", "diff", "du", "basename", "dirname", "echo", "sort", "uniq", "which",
    }
)  # fmt: skip
_FIND_WRITES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
)
_OPERATOR_CHARS = frozenset(";&|<>()")


def _is_readonly_segment(words: list[str], project: Path, base: Path | None) -> bool:
    if not words:
        return False
    if words[0] == "cd":  # `cd folder && ls`: only into the project
        return len(words) == 2 and _inside(words[1], project, base)
    if words[0] not in _READONLY:
        return False
    if words[0] == "find" and any(word in _FIND_WRITES for word in words[1:]):
        return False
    for word in words[1:]:
        if word.startswith("-"):
            continue
        if word.startswith(("/", "~")) and not _inside(word, project, base):
            return False
        if ".." in Path(word).parts:
            return False
    return True


def is_readonly_command(command: str, project: Path, base: Path | None = None) -> bool:
    """One or more look-only commands joined by ``&&`` or ``|``, touching only the project.

    No redirects, no ``;``/``||``/background, no substitution, nothing that writes or runs
    another program.
    """
    if any(mark in command for mark in ("`", "$(", "${", "\n")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    segment: list[str] = []
    for token in tokens:
        if token and set(token) <= _OPERATOR_CHARS:
            if token not in {"&&", "|"} or not _is_readonly_segment(segment, project, base):
                return False
            segment = []
        else:
            segment.append(token)
    return _is_readonly_segment(segment, project, base)


def _inside(raw: str, project: Path, base: Path | None = None) -> bool:
    """Is ``raw`` inside ``project``? Relative paths mean relative to ``base`` (ZCode's folder)."""
    if not raw:
        return False
    path = Path(raw).expanduser()
    path = path if path.is_absolute() else (base or project) / path
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == project or project in resolved.parents


def decide(
    tool: str,
    tool_input: dict[str, Any],
    *,
    ceiling: str,
    project: Path,
    base: Path | None = None,
) -> Decision:
    """Allow or deny one pending tool call. Unknown tools are denied.

    ``base`` is the folder ZCode itself works from when that is above ``project``.
    """
    project = project.resolve()
    base = base.resolve() if base is not None else None
    level = _ORDER.get(ceiling, 0)
    if tool in _READ_TOOLS:
        raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if raw and not _inside(raw, project, base):
            return Decision(False, f"{tool} outside the project folder")
        return Decision(True)
    if tool in _WRITE_TOOLS:
        if level < _ORDER["edit"]:
            return Decision(False, f"{tool} is not allowed when the run limit is look only")
        raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if not _inside(raw, project, base):
            return Decision(False, f"{tool} outside the project folder")
        return Decision(True)
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        if is_readonly_command(command, project, base):
            return Decision(True)
        if level >= _ORDER["edit"] and is_safe_mkdir(command, project, base):
            return Decision(True)
        if level < _ORDER["run"]:
            return Decision(False, "running commands is not allowed by the run limit")
        if command_is_denied(command):
            return Decision(False, "that command is on Sani's never-run list")
        return Decision(True)
    return Decision(False, f"{tool} is not something Sani allows ZCode to do")

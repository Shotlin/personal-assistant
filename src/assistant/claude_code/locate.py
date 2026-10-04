"""Find the user's Claude Code program, its version, and whether it is signed in.

Sani never logs in on the user's behalf: signing in is an interactive browser
step the user does once with ``claude auth login``. This module only reports
the state so the UI can say so plainly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

#: Variables that would silently move Claude Code onto metered API billing or a
#: different account. The companion exists to use the user's own login, so they
#: are never passed to the child even if the host process has them.
_BLOCKED_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_BEARER_TOKEN_BEDROCK")
_BLOCKED_ENV_NAMES = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "OPENROUTER_API_KEY", "OPENAI_API_KEY"})

#: The minimum environment a login-based Claude Code needs to find its config
#: and credential store. Everything else is dropped.
_PASSTHROUGH_ENV = (
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "CLAUDE_CONFIG_DIR",
    "SSH_AUTH_SOCK",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
)

_DESKTOP_GLOB = (
    "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"
)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """A scrubbed environment for the Claude Code child process."""
    env = os.environ if source is None else source
    clean: dict[str, str] = {}
    for name in _PASSTHROUGH_ENV:
        value = env.get(name)
        if value:
            clean[name] = value
    # PATH is needed so the tools Claude Code runs (git, node, python) resolve.
    clean["PATH"] = env.get("PATH") or "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    for name in list(clean):
        if name in _BLOCKED_ENV_NAMES or name.startswith(_BLOCKED_ENV_PREFIXES):
            del clean[name]
    return clean


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _version_key(path: Path) -> tuple[int, int, int]:
    """Order desktop-bundled copies by the version directory in their path."""
    for part in reversed(path.parts):
        parsed = parse_version(part) if part.count(".") == 2 else None
        if parsed:
            return parsed
    return (0, 0, 0)


def find_claude_binary(
    *,
    override: str = "",
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """The best Claude Code executable on this Mac, or None.

    Order: an explicit setting, ``claude`` on PATH, the standard install
    locations, then the copy bundled inside the Claude desktop app (newest
    version wins).
    """
    if override.strip():
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    on_path = which("claude")
    if on_path:
        return Path(on_path)
    base = home or Path.home()
    for relative in (".local/bin/claude", ".claude/local/claude", ".npm-global/bin/claude"):
        candidate = base / relative
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    for absolute in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        candidate = Path(absolute)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    bundled = sorted(base.glob(_DESKTOP_GLOB), key=_version_key)
    bundled = [path for path in bundled if os.access(path, os.X_OK)]
    return bundled[-1] if bundled else None


@dataclass(frozen=True, slots=True)
class ClaudeStatus:
    """What the UI shows about the user's Claude Code, in plain facts."""

    installed: bool
    path: str = ""
    version: str = ""
    signed_in: bool = False
    auth_method: str = ""
    detail: str = ""
    # Who is signed in, for display only: name, email, plan. No tokens, ever.
    name: str = ""
    email: str = ""
    plan: str = ""
    org: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "path": self.path,
            "version": self.version,
            "signed_in": self.signed_in,
            "auth_method": self.auth_method,
            "detail": self.detail,
            "name": self.name,
            "email": self.email,
            "plan": self.plan,
            "org": self.org,
        }


async def _run_quiet(binary: Path, *args: str, timeout: float) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        str(binary),
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=child_environment(),
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return 124, ""
    return process.returncode or 0, out.decode("utf-8", errors="replace")


def _display_name(config_dir: str) -> str:
    """The profile name Claude Code stored for the signed-in account (name fields only)."""
    home = Path.home()
    base = Path(config_dir).expanduser() if config_dir else home / ".claude"
    candidate = home / ".claude.json" if base == home / ".claude" else base / ".claude.json"
    try:
        account = json.loads(candidate.read_text(encoding="utf-8")).get("oauthAccount") or {}
        return str(account.get("fullName") or account.get("displayName") or "")
    except (OSError, ValueError, AttributeError):
        return ""


async def read_status(binary: Path | None) -> ClaudeStatus:
    """Version and sign-in state. Never starts a model call."""
    if binary is None:
        return ClaudeStatus(
            installed=False,
            detail="Claude Code isn't installed. Install it, then sign in once.",
        )
    code, out = await _run_quiet(binary, "--version", timeout=15)
    if code != 0:
        return ClaudeStatus(installed=True, path=str(binary), detail="Claude Code didn't start.")
    version = ".".join(str(n) for n in (parse_version(out) or ())) if parse_version(out) else ""
    code, out = await _run_quiet(binary, "auth", "status", timeout=15)
    signed_in = False
    method = ""
    name = email = plan = org = ""
    if code == 0:
        try:
            data = json.loads(out)
            signed_in = bool(data.get("loggedIn"))
            method = str(data.get("authMethod") or "")
            email = str(data.get("email") or "")
            plan = str(data.get("subscriptionType") or "")
            org = str(data.get("orgName") or "")
            if signed_in:
                name = _display_name(str(data.get("configDirectory") or ""))
        except (json.JSONDecodeError, AttributeError):
            signed_in = False
    detail = "" if signed_in else "Not signed in. Run `claude auth login` once in Terminal."
    return ClaudeStatus(
        installed=True,
        path=str(binary),
        version=version,
        signed_in=signed_in,
        auth_method=method,
        detail=detail,
        name=name,
        email=email if signed_in else "",
        plan=plan if signed_in else "",
        org=org if signed_in else "",
    )

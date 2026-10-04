"""What one coding CLI has to tell Sani. Everything else is shared."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from assistant.claude_code.events import ClaudeEvent
    from assistant.claude_code.locate import ClaudeStatus
    from assistant.claude_code.runner import RunRequest
    from assistant.settings import Settings


class LineParser(Protocol):
    """Turns one output line of the child into zero or more typed events."""

    def feed(self, line: str) -> list[ClaudeEvent]: ...


@dataclass(frozen=True, slots=True)
class Preflight:
    """The answer to "may this run start?", asked before anything is spent."""

    #: Plain words for why it must not start; empty means go ahead.
    refusal: str = ""
    #: Shown in the run's result, e.g. which model will be billed.
    note: str = ""


@dataclass(frozen=True, slots=True)
class AgentOptions:
    """The user's settings for one backend, in one shape."""

    enabled: bool
    binary: str
    dirs: str
    #: The most a run may do: read | edit | run.
    permission: str
    model: str = ""
    effort: str = ""
    max_turns: int = 30
    run_seconds: int = 600


class Backend(Protocol):
    #: Stable id used in session keys and logs, e.g. "claude", "zcode".
    key: str
    #: Shown to the user and the Deep Agent, e.g. "Claude Code".
    name: str
    #: The LangChain tool name the Deep Agent calls.
    tool_name: str
    #: Plain-words line for "not signed in".
    sign_in_hint: str
    #: True when the prompt is written to stdin; False when ``build_args`` carries it.
    prompt_on_stdin: bool
    #: True when the CLI has no final "result" record, so a clean exit means success.
    final_from_exit: bool
    #: Prefix of this backend's host actions (``claude_code`` -> ``claude_code.login_start``).
    action_prefix: str
    #: Matches the sign-in link in the login command's output.
    login_url_pattern: str
    #: Live plan-usage numbers can be fetched with one tiny request (Claude Code only today).
    supports_usage: bool

    def options(self, settings: Settings) -> AgentOptions: ...

    def locate(self, override: str) -> Path | None: ...

    def command(self, binary: Path) -> list[str]:
        """The program and any leading arguments (e.g. ``node script.cjs``)."""
        ...

    def read_status(self, binary: Path | None) -> Awaitable[ClaudeStatus]: ...

    def extra_status(self, binary: Path | None) -> dict[str, Any]:
        """Extra facts for the UI (plans, models, the default in use). May be empty."""
        ...

    async def preflight(self, binary: Path, workspace: Path, settings: Settings) -> Preflight:
        """Checks to pass before a run starts (e.g. which account would be billed)."""
        ...

    def parse_account(self, login_output: str) -> dict[str, str] | None:
        """Name/email the login command printed on success, or None."""
        ...

    def login_args(self) -> list[str]:
        """Arguments (after ``command``) that start an interactive sign-in."""
        ...

    def logout_args(self) -> list[str]: ...

    def build_args(
        self, request: RunRequest, version: tuple[int, int, int] | None
    ) -> list[str]: ...

    def environment(self, settings: Settings | None = None) -> dict[str, str]: ...

    def make_parser(self, cwd: str) -> LineParser: ...

    def guide(self) -> str:
        """The paragraph added to the Deep Agent's system prompt."""
        ...


def session_key(backend: Backend, conversation: str) -> str:
    """Claude keeps its historical key; every other backend is namespaced."""
    return conversation if backend.key == "claude" else f"{backend.key}:{conversation}"


def describe(backend: Backend) -> dict[str, Any]:
    return {"key": backend.key, "name": backend.name, "tool": backend.tool_name}

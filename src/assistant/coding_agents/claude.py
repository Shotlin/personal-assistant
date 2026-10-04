"""Claude Code as a backend: a thin wrapper over the existing claude_code modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from assistant.claude_code.locate import (
    ClaudeStatus,
    child_environment,
    find_claude_binary,
    read_status,
)
from assistant.claude_code.parser import StreamParser
from assistant.coding_agents.backend import AgentOptions, LineParser, Preflight
from assistant.coding_agents.guide import GUIDE

if TYPE_CHECKING:
    from assistant.claude_code.runner import RunRequest
    from assistant.settings import Settings


class ClaudeBackend:
    key = "claude"
    name = "Claude Code"
    tool_name = "claude_code"
    sign_in_hint = (
        "Claude Code isn't signed in on this Mac. Ask the user to run `claude auth login` "
        "once in Terminal (it opens a browser); then try again."
    )
    prompt_on_stdin = True
    final_from_exit = False
    action_prefix = "claude_code"
    login_url_pattern = r"https://claude\.(?:com|ai)/\S+"
    supports_usage = True

    def options(self, settings: Settings) -> AgentOptions:
        return AgentOptions(
            enabled=settings.claude_code_enabled,
            binary=settings.claude_code_binary,
            dirs=settings.claude_code_dirs,
            permission=settings.claude_code_permission,
            model=settings.claude_code_model,
            effort=settings.claude_code_effort,
            max_turns=settings.claude_code_max_turns,
            run_seconds=settings.claude_code_run_seconds,
        )

    def locate(self, override: str) -> Path | None:
        return find_claude_binary(override=override)

    def command(self, binary: Path) -> list[str]:
        return [str(binary)]

    async def read_status(self, binary: Path | None) -> ClaudeStatus:
        return await read_status(binary)

    def extra_status(self, binary: Path | None) -> dict[str, Any]:
        return {}

    async def preflight(self, binary: Path, workspace: Path, settings: Settings) -> Preflight:
        return Preflight()

    def parse_account(self, login_output: str) -> dict[str, str] | None:
        return None

    def login_args(self) -> list[str]:
        return ["auth", "login", "--claudeai"]

    def logout_args(self) -> list[str]:
        return ["auth", "logout"]

    def build_args(self, request: RunRequest, version: tuple[int, int, int] | None) -> list[str]:
        from assistant.claude_code.runner import build_args

        return build_args(request, version=version)

    def environment(self, settings: Settings | None = None) -> dict[str, str]:
        return child_environment()

    def make_parser(self, cwd: str) -> LineParser:
        return StreamParser(cwd)

    def guide(self) -> str:
        return GUIDE

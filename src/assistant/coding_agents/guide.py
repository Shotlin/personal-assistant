"""The Deep Agent's instructions for a coding tool, one text for every backend."""

from __future__ import annotations

TOOL_DESCRIPTION = (
    "Do software work with the user's own Claude Code: write, change, debug, test or explain code "
    "in a project folder. Use it for any coding request, never for chat, desktop clicking or "
    "browsing. Give ONE complete, self-contained request (the goal, the files or area involved, "
    "constraints, and how to check it worked, for example 'run the tests'). Claude Code keeps this "
    "chat's session, so follow-ups continue where it left off. The result tells you what changed "
    "and whether it worked; read it before answering. If a run fails for a clear, fixable reason, "
    "make ONE corrected follow-up. If it stops, is blocked, or fails the same way twice, stop and "
    "tell the user plainly what happened and what you need, instead of trying again."
)

GUIDE = """
Coding with Claude Code: you have a `claude_code` tool that drives the user's own Claude Code.
- Use it for software work (build, edit, fix, test, explain a codebase). Do not use it for chat,
  the desktop, or the web.
- Write the request like a good engineer would: goal, where, constraints, how to verify.
- Pick mode "read" to only look, "edit" to change files, "run" to also run commands and tests.
- Judge each result. One corrected follow-up is fine; if it stops, is blocked or fails the same way
  twice, stop and explain in plain words what happened and what you need from the user.
- Never paste long output back. Say what changed, whether it worked, and what to do next.
- If the user attached an image, pass its path in `attachments`.
- Folder access and the limit on what a run may do (look, edit, or also run commands) are
  Sani's own settings: Settings → Claude Code → Project folders and → What a run may do. They
  are shared by every coding tool. If a folder is refused, or a run was limited so it could not
  run commands, tell the user to change that in Sani's Settings. Do not open the coding tool's
  app or look for its settings.
- Give every call a short `purpose` (under 8 words); the user sees it as the round's title.
- New project: pass `create_folder=true` and a project_dir inside an allowed folder.
- Claude Code cannot wait for an answer mid-run. If its reply asks you something with options,
  decide who answers. Answer it yourself (send a follow-up call) when the user's request, the chat
  so far, or sound engineering defaults settle it, and say in one line what you chose and why.
  Ask the user only for things that are theirs to decide (brand, taste, money, anything hard to
  undo). Ask by ending your message with a block like:
  [Options]
  - First choice
  - Second choice
  (2 to 4 short choices; the user can tap one or type their own).
"""


def for_backend(text: str, name: str, tool: str) -> str:
    """The same words for another CLI: swap its name and tool id."""
    # The settings screens keep the name "Claude Code" for every tool, so protect those words.
    kept = "Settings → Claude Code"
    swapped = text.replace(kept, "\x00")
    swapped = swapped.replace("`claude_code`", f"`{tool}`").replace("Claude Code", name)
    return swapped.replace("\x00", kept)

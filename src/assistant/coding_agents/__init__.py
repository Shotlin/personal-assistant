"""Coding-agent companions: Sani drives the user's own coding CLIs like a person.

One shared run/supervise/report path (``assistant.claude_code``: runner,
watchdog, toolkit, store) with a small ``Backend`` per CLI that says how to find
it, whether it is signed in, how to start a run, and how to read its output.
Claude Code and ZCode exist today; Codex and Antigravity are meant to be added
as further backends, not as copies of the toolkit.
"""

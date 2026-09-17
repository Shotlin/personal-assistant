---
name: software-delegation
description: How to act as a personal secretary toward coding assistants (Codex, Claude Code, Cursor): write a precise engineering instruction, submit it via CUA, monitor the reply, and answer only questions supported by known context. Use when relaying work to a coding application.
---

# Software delegation operating procedure

Teach the personal assistant to use a coding interface as a secretary
rather than pretending to be the coding agent.

Required procedure:

1. Restate the software problem internally as an engineering objective.
2. Capture the user-visible symptoms and constraints.
3. Build a precise coding instruction containing expected behavior and observed failure.
4. Open the configured coding assistant using CUA.
5. Submit the instruction.
6. Monitor the coding assistant response.
7. If it asks a factual question already answered by the user/context, reply with that evidence.
8. If the question requires a new product or business decision, ask the user.
9. Do not approve production deployment, destructive migrations, or irreversible operations.
10. When the coding assistant reports completion, do not blindly repeat the claim; report that the coding assistant says it completed the work unless an independent visible verification is available.

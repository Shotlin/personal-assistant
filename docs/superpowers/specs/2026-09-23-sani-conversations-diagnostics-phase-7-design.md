# Sani Phase 7 — Conversations and Diagnostics Design

## Outcome

Sani provides a real local Conversations page and optional owner-facing execution details without a new database or remote analytics.

## Architecture

`sani-history.db` remains authoritative. Existing conversations/messages are extended migration-safely with message preview and a local run/activity table containing only safe run metadata: ids, agent, timestamps, status, labels, bounded failure detail, and durations. React queries native commands and holds display state only.

## Experience and retention

Conversations list title, update time, preview, selection, and explicit deletion. Stored assistant attribution is rendered as-is. Execution Details is optional rather than a default developer console. Storage settings adds technical-activity retention (7/14/30 days) and explicit Clear technical logs; conversation history remains until explicit deletion and is not conflated with agent memory.

## Tests

Migration, restart persistence, list/switch/delete, attribution, run linkage, no secret columns/content, retention eligibility, and no silent chat/history deletion.

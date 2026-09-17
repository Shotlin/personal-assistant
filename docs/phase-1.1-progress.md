# Phase 1.1 implementation progress

## Current checkpoint

Branch: `phase-1.1-latency`. Committed through `9bd0a27`:
WP3 parts 1-3 (session manager core, stream-owned run scope + local Stop,
gateway wiring + stop endpoint + manifest review) and the newest-observation
hard cap (F03). Working tree clean. No push, no deployment.

## Live-verified this round (bounded, no desktop actions)

Gateway restarted from current source (`MODEL_NAME=stealth/union-alpha`):
`/healthz` + `/readyz` 200. Stop endpoint live checks: unknown run -> 404
`run_not_found`; missing key -> 401. One streaming smoke (1 model call,
`WP3-SMOKE-OK` streamed back in 2.91s total): `[working]` status chunk
appeared immediately after acceptance; final `[DONE]` framing present.
Log evidence for the pure-chat smoke: `run_usage` recorded
`model_calls=1, input_tokens=15774, output_tokens=45` and NO
`cua_session`/desktop events — session lifecycle tools absent from the
model inventory (startup `skipped` list). No live desktop action, no
cursor session, no denial-path probe.

Operational finding (pre-existing, logged live): deepagents skips
`/skills/computer-use/SKILL.md` — YAML frontmatter parse fails on the
colon in the description line. Skill content is currently not loaded.
Needs a front-matter fix + test (WP7).

## Measured (small-n, honest)

- Streaming smoke: 1 run, 2.91s end-to-end, 1 model call, 15,774 input /
  45 output tokens, model `stealth/union-alpha` via OpenRouter. Not a
  performance baseline (n=1, full-system latency).
- Unit/integration suite runs in ~2.9s wall clock; per-commit counts
  recorded in commit messages (136 -> 142 -> 145 -> 148 passed).

## Remaining WP3 work / known limitations

- Cursor-motion manifest entry added but daemon not restarted; motion
  behavior unverified live. idle_hide_ms=0 wiring exists only in the
  manager; no live >30s-wait or real Stop check yet.
- Durable cross-process desktop ownership remains WP4 scope; current
  lease is process-local.
- Utility path never opens desktop runs (by design); covered by absence
  of desktop events in the smoke log, not a dedicated test yet.
- Settings flags added to code; `.env.example` needs the new keys
  documented; README rollback section still pending (WP8).

## WP1/WP2 audit caveats (unchanged unless noted)

- Usage ledger counts provider responses, not underlying SDK retry
  attempts; cost stays unknown (recorded as unknown, not zero).
- Structured evidence now reaches the policy wrapper boundary
  (ToolOutcome preserved); per-window state shaping still open (WP7).
- Newest-observation hard cap DONE this round (12000 chars); per-window
  freshness still open.

## Model and live testing

The user changed the local configuration to `stealth/union-alpha` via
OpenRouter and requested a restart; the running gateway logs that model.
The master plan's model-specific work (GLM reasoning controls, provider
latency preference, GLM endpoint probes) is NOT applicable until the
owner reconciles the model constraint; no GLM-specific claims are made.
No claim is made that the smoke run reflects production performance.

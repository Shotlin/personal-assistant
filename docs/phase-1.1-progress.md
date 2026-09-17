# Phase 1.1 implementation progress

## Current checkpoint

Branch: `phase-1.1-latency`. Committed through `9d069be`:
- WP4 COMPLETE (parts 1-4): durable run registry + atomic claim
  (UNIQUE(user_id, user_message_id)), action ledger with unknown-effect
  state, gateway claim before external work, terminal marking on all
  paths, duplicate delivery observe-only (SSE + JSON), production wiring
  live-verified, and durable action-ledger rows written around REAL CUA
  dispatch (planned -> confirmed/failed/unknown; timeout/cancel = unknown,
  never blind replay). DesktopLease documented non-fencing, unwired.
- WP5 COMPLETE (parts 1-2): strict router (master-plan regressions
  verbatim), three local recipes with bounded arithmetic evaluator,
  RecipeExecutor (per-native-mutation budget + ledger, fail-closed
  schemas, no pixel coordinates), render_result with evidence recheck,
  gateway recipe route BEFORE the agent path, and an e2e gate proving
  'Open Chrome' -> 'Opened chrome.' with model call count 0.
Suite: 268 passed, 1 skipped; ruff/mypy clean (44 files). No push.

## Round-5 verification

- Live (production gateway, real model idle): 'Open Chrome' with the real
  cua-driver inventory -> honest failure text 'I could not verify that
  chrome opened: launch_app failed: DesktopDriverError.', usage 0 prompt /
  0 completion tokens, registry row completed. The zero-model claim and
  honest-failure rendering are LIVE-verified; the recipe's GUI effect is
  NOT (real driver dispatch is WP8 live scope; the daemon manifest for
  cursor motion is still stale and launch_app schema coverage must be
  confirmed against the real driver).
- test_recipe_gateway_e2e.py: production lifespan + fake CuaConnection
  with REAL argument schemas (executor validates; empty-schema fakes are
  rejected - pinned by an isolated executor probe) -> 'Opened chrome.',
  call_index == 0.
- WP4 ledger dispatch tests on real Postgres (6): planned->confirmed,
  failed, unknown-on-timeout, observations excluded, no-scope no-rows,
  per-args digest.
- Subagent WP5: 86 focused tests (router regressions, render contract,
  executor budget/ledger/modal/closed-session/macro accounting), full
  suite 268 green, ruff/mypy clean; fake-driver only.

## Round-4 live verification (production gateway, real Postgres, real model)

After restart from current source (`stealth/union-alpha` via OpenRouter):
- Non-streaming: first delivery executed (answer `WP4-LIVE-OK`, 2.65s, one
  OpenRouter call); duplicate delivery logged `run_duplicate_rejected
  reason=already_claimed claim_status=completed` and returned the
  observe-only reply; run_registry held EXACTLY ONE row, status
  `completed`. (Pre-repair baseline on the old process: HTTP 200 with
  ZERO registry rows — wiring failure was found live and fixed.)
- Streaming: duplicate delivery received observe-only SSE with proper
  framing ([DONE] present). No second model call.
- Log evidence source: the running process (job bash-15); var/gateway.log
  holds a stale pre-WP4 crash trace and is NOT current evidence.

## Round-3 evidence (all real-Postgres or live-gateway, no paid calls)

- tests/integration/test_run_ledger.py: 8 tests on the real compose DB —
  claim semantics (dup observes, conflict rejects, new id runs), status
  transitions, planned->confirmed ledger flow, unknown-not-replayed,
  desktop lease exclusivity + expiry steal.
- test_run_dedup.py: first version was VACUOUS (counter read the utility
  model); hardened to count agent model invocations and FAILED before
  wiring, then passed after claim wiring — it discriminates.
- Live gateway (restart pending for WP4 code): stop endpoint 404/401,
  one streaming smoke on stealth/union-alpha (2.91s, 1 call, 15774/45
  tokens, no desktop events). n=1, not a performance baseline.

## Remaining WP4 work

- Record actions in the ledger around real CUA dispatch (planned ->
  confirmed/unknown wiring in the policy wrapper/dispatch path).
- DesktopLease remains UNWIRED and non-fencing by design (documented in
  runs.py); production desktop dispatch must not rely on it for safety.
- Regenerate/retry interplay with claims (regeneration must not be
  swallowed as a duplicate); run_id collision on claim fails closed.
- .env.example docs for new flags; README rollback section (WP8).

## Operational findings (live log evidence)

- deepagents skips /skills/computer-use/SKILL.md: YAML frontmatter parse
  fails on the colon in the description line (seen in live gateway logs
  and integration runs). Fix front-matter + add test (WP7).

## Earlier rounds (summarized)

- WP3: DesktopSessionManager (lazy session, lease, sequential actions,
  local Stop before dispatch, cleanup on all paths), session lifecycle
  tools removed from model inventory, POST /v1/runs/{id}/stop, stream
  status events, run-scoped ContextVars, manifest reviewed.
- WP2/F03: observation defaults, ToolOutcome normalization + model-text
  conversion, newest-observation 12k hard cap.
- Live smoke on stealth/union-alpha confirmed status events + framing;
  model_calls=1 with no desktop events for pure chat.

## Model and live testing

User's configured model is stealth/union-alpha (changed from the plan's
z-ai/glm-5.3-flash); do not silently revert. GLM-specific plan items
(reasoning controls, provider latency probes) are not applicable until
the owner reconciles; no GLM claims made. No live desktop action, cursor
wait check, denial probe, or Open WebUI benchmark yet (WP8).

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

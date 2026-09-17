# Phase 1.1 implementation progress

## Round-12 checkpoint — planner outcome memory continuity

Planner-route success and honest failure responses now use the same
`_persist_recipe_turn` helper as exact recipes before returning JSON/SSE.
The thread receives user text + actual rendered outcome, not internal plan
JSON. Test `test_planner_outcome_reaches_checkpoint_and_followup` covers
success/failure crossed with stream false/true, checks terminal truth and
one initial planner call, reads the actual checkpoint, then routes a follow-up
through an unsupported planner decision to the general agent and checks its
received messages. The planner remains enabled during that follow-up.

Red evidence: before the fix, the first case had an empty checkpoint instead
of the expected user/assistant pair. After the fix all four cases pass; the
whole planner gateway file is 6 passed. Full gates: 318 passed, 1 skipped,
1 existing Starlette deprecation warning; ruff/mypy clean (45 files), diff
check clean. Tests use fake desktop tools/scripted model and real gateway
lifespan/checkpointer; no live desktop or provider calls were made this round.

Boundaries: persistence remains best-effort and cancellation branches still
skip it; this is not a guarantee of memory durability on storage failure or
client disconnect. Planner context/constraints remain unwired and its rollout
flag remains false by default. Next priorities remain actual stored-memory
constraints on both routes, cancellation/disconnect/wall-clock truth,
provider-wire coverage and WP7 compact window state. Full WP1-WP8 completion
and live latency improvement are not claimed. No model/permission changes.

## Round-11 checkpoint — router contract only

Implemented and verified supplied `approved_context['denied_apps']` in
`match_local_command`: explicit open-app, implicit Calculator arithmetic,
and implicit Chrome search all fall back on a denied app. Malformed denial
collections also fall back; absent/empty context retains existing routing.
TDD evidence: explicit Terminal denial failed first (recipe returned), then
passed; the two implicit-app cases failed first, then passed. Focused suite:
10 passed. Full gates: 314 passed, 1 skipped (Starlette deprecation warning),
ruff/mypy clean (45 source files), git diff --check clean.

This is NOT end-to-end standing-preference enforcement: the gateway still
passes `{}`; memory loading, conservative treatment of unfamiliar constraints,
and the planner's post-validation gate remain unwired. The speculative regex
memory extractor was removed rather than shipping an unverified assumption
about stored memory format. Returning None defers to the agent; it is not a
hard native authorization deny. Next integration must cover BOTH recipe and
planner paths and actual stored-memory schemas with production-path tests.

Correction to round 10: 'WP8 offline scope COMPLETE' and 'everything offline
verifiable ... pinned' were too broad and are retracted. Provider-wire,
constraints, disconnect/wall-clock, planner-memory parity and other offline
work remain. The negative tests lack a direct model-call assertion and the
Calculator test lacks a direct zero-model-call assertion; their existing
assertions are narrower than the previous final report described. No measured
live latency improvement is claimed. A permission probe earlier this round
returned PENDING; further live probing is paused. Model configuration and
permission policy were not changed.

## Round-10 checkpoint

Committed through `33875d8` — WP8 offline scope COMPLETE:
- test_calculator_gateway_e2e.py: production-lifespan calculator run
  ('calculate 6*7' -> '= 42') with display-state assertions (Escape
  first, Enter last, final display '42') and durable confirmed ledger
  rows. Red first (fixture lacked display_value evidence); production
  code untouched.
- test_negative_routing_e2e.py: master-plan negatives (negation,
  multi-clause, unmapped app, compound) reach the general agent; zero
  native actions across all negatives.
- README 'Feature flags & rollback' section + .env.example levers
  (planner/recipes/registry/cursor) with layer rollback paths.
Suite: 304 passed, 1 skipped; ruff/mypy clean. Live GUI verification
STILL PAUSED: one probe this round confirmed the macOS permission gate
remains pending. Remaining for the objective: live desktop verification
(permission grant), real provider-wire capture, constraints/preferences
gating of fast paths (approved_context), disconnect/wall-clock status
truth, Open WebUI auxiliary generation accounting, prompt-caching
decision (owner policy), planner-route persistence parity.

## Round-9 checkpoint

Committed through `eaefbd6`. The one offline-verifiable gap from the
live probes is fixed: with macOS permissions pending, the real chain
raises DesktopDriverError('start_session failed: Error:
permissions_pending: ...') and the executor discarded that text, so the
user saw only 'launch_app failed: DesktopDriverError'. RecipeFailure now
carries the driver's own message (300-char bound); pinned by
test_permissions_pending_error.py, which mirrors the production chain
(raising session-start denial) and was verified red against the pre-fix
executor via git stash. Suite: 297 passed, 1 skipped; ruff/mypy clean.
Cursor lifecycle ordering/idempotence was already covered and green
(test_desktop_session.py, 13 tests). Live desktop verification remains
paused pending the permission grant; once granted, failures (if any)
will now be self-explaining. Remaining: planner route persistence parity
(flag-gated rollout), constraints/preferences gating (approved_context),
provider-wire capture, disconnect/wall-clock truth, WP8 negative tests,
calculator e2e, rollback docs, WebUI auxiliary generation, caching decision.

## Round-8 checkpoint

Committed through `1e85bc7`. Two WP7 review items fixed, both TDD:
- SKILL.md YAML frontmatter (live-log bug): colon in an unquoted plain
  description broke yaml.safe_load, so deepagents DROPPED the skill.
  Descriptions now quoted (computer-use, general-assistant);
  tests/unit/test_skill_frontmatter.py pins the real files through
  deepagents' own parser + the quoting mechanism (red -> green).
- Fast-path recipe turns are now persisted to the agent thread
  (best-effort aupdate_state; P1-3). Pinned by
  test_recipe_checkpoint_continuity.py: checkpoint holds the exchange
  AND a following general-agent turn receives it in prompt history
  (was 0 messages).
Suite: 295 passed, 1 skipped; ruff/mypy clean. Live desktop verification
still paused (permission step below). Remaining review queue: strict
planner validation + its regressions; constraints/preferences gating of
fast paths (approved_context still empty); provider-wire capture;
disconnect/wall-clock status truth; WP8 negative tests, calculator e2e,
rollback docs; Open WebUI auxiliary generation; prompt caching decision.

## Round-7 checkpoint

Committed through `9e72f20`. Fast paths (exact recipe + planner route)
now return their own terminal status: failed recipe -> 'failed',
cancelled -> 'cancelled' (was always 'completed'); both the JSON finish
site and the SSE generator consume it. New e2e
(test_fast_path_cancellation.py, production lifespan): Stop during a
slow-but-finite recipe launch resolves 200 mid-flight and the registry
ends 'cancelled'; the tool is cancelled through the real DesktopRun
path. Suite: 292 passed, 1 skipped; ruff/mypy clean. Live desktop
verification remains paused pending the permission step below.

## Round-6 closeout — authoritative runtime and repair state

- Gates: 290 passed, 1 skipped; ruff, mypy (45 files), git diff --check clean.
- Planner usage now shares the gateway ledger; fake provider metadata
  (123 input / 24 output) is asserted through the production lifespan.
- Recipe executors now receive RunActionLedger; raw allowlisted tools are
  distinct from model-wrapped tools. Production-shaped ToolOutcome e2e
  reproduced evidence loss before the repair; now asserts exactly one
  confirmed launch row. Streamed agent scope also receives action_ledger.
- Fast responses return SSE/[DONE] when requested, and finalize the run
  in the generator. Early streaming, disconnect/cancellation status,
  timeout and accurate failed-recipe terminal status remain unfinished.
- Planner mixed/duplicate-key rejection and argument bounds improved,
  but strict discriminated validation and regression coverage remain open.
- Native launch lowering uses bundle_id (observed native schema); legacy
  fake app_id schemas remain supported explicitly. Foreground observation
  adaptation is NOT solved; do not infer focus from app/window presence.
- Tuple normalization tests: 3 red -> green for LangChain MCP adapter
  (content list, metadata with structured_content). Earlier statements
  that ClientSession.call_tool itself returns tuples were incorrect:
  load_cua_tools uses the adapter; production _caller uses ClientSession.
- Model remains stealth/union-alpha. Compact planner defaults OFF.

### Runtime incident and restoration

Repeated restart attempts were a mistake. The MCP adapter auto-started a
standard-mode daemon without the manifest; read-only status exposed this.
The Calculator launch/observation probes during that interval are NOT
valid evidence of bounded recipe success. Standard daemon and erroneous
launchctl job were stopped. Final status verified PID 96391, bounded
(trusted_startup_configuration), manifest configured/approved/valid,
sha256 107ff67558fbf72ca96690eea7f01de41cb4eaf8a2ff28f886b25ca91ba32eb7.
Managed daemon job bash-27 retains the permission gate; this launch
context reports Accessibility and Screen Recording missing. No TCC edits
or further bypass attempts are permitted. Human OS approval may be needed.
Gateway was stopped during restoration and has NOT been restarted.
Manual permission steps: System Settings -> Privacy & Security ->
Accessibility -> enable CuaDriver; then Screen & System Audio Recording
(or Screen Recording) -> enable CuaDriver. If already enabled, report
that rather than granting broader apps: the launch-context attribution
needs resolution. Do not edit TCC databases or disable the consent gate.
Live desktop testing is paused pending this human step; offline work can
continue. No further daemon checks until permission resolution.
Do not use MCP discovery that auto-starts standard mode while restoring.

Next: finish constraints/memory/checkpoint integration and strict validation
with planner disabled; repair real observation contracts and native schema
validation; add actual provider-wire and shared-budget tests; finish WP7/WP8.
Earlier COMPLETE claims below are historical and superseded.

## Current checkpoint

Branch: `phase-1.1-latency`. WP6 initial implementation committed at
`643ad09`; completion claims for WP4–WP6 are RETRACTED pending the
integration repairs below. Compact planning defaults OFF after review. WP6 adds runtime/planner.py
(plan_supported_task -> RecipeRequest | NeedsClarification | UnsupportedTask;
master-plan shell-payload regression verbatim; fail-closed validate_plan
rejecting any unknown executable field and truncated JSON; bounded
recovery = exactly one repair attempt, never open-ended), budget tests
(planner call consumes zero mutation budget), provider request contract
test (single compact message, NO tool bindings, settings-bound model),
and the gateway planner route between exact-match and the agent, behind
`compact_planner_enabled` (WP8 rollback lever). Suite: 286 passed,
1 skipped; ruff/mypy clean (45 files). No push.

## Round-6 live verification (production gateway)

Natural phrasing live smoke ('please launch chrome for me', real
stealth/union-alpha): one OpenRouter call (the planner decision), then
local recipe execution, final answer = honest failure text with usage
0 prompt / 0 completion tokens in the response — this was an ACCOUNTING
BUG, not evidence of free model use. Repaired with the shared usage ledger
callback; a discriminating e2e regression first failed (0 != 123), then
passed with 123 input / 24 output tokens from fake provider metadata.
The corrected accounting has NOT yet been live-tested. Log evidence (job
bash-21): run_started -> one openrouter POST -> desktop session attempt
-> run_finished ok in 3.39s. The GUI effect itself failed with
DesktopDriverError (expected: real-driver dispatch compatibility is WP8
live scope; the capability manifest also logged an idle-timeout policy
error on end_session — daemon restart still pending). E2E (production
lifespan + fake driver with real schemas): 'please launch chrome for me'
-> 'Opened chrome.' with call_index == 1 exactly.

## Integration repair queue (round-6 review)

- Recipe routes accept action_ledger but pass run_store=None: no durable
  recipe action rows. Existing zero-row live evidence exposed this gap.
- CuaConnection.tools_by_name contains policy-wrapped tools, whereas the
  executor requires raw normalized tools. Production evidence is flattened;
  fake dictionaries in current e2e tests do not expose this incompatibility.
- Streaming agent scope omits action_ledger; audit and regression required.
- Planner context argument is unused; clarification is discarded into agent
  fallback. Constraints/memory, strict decision shapes, length/arithmetic
  validation, streaming response format and cancellation need hardening.
- Budget tests with an unconnected local RunBudget do not prove shared
  gateway accounting. Provider contract test does not capture wire payload.
- Preserve current stealth/union-alpha selection and bounded permissions.
  Do not work around the expired capability manifest.

## WP6 NOT done (honest leftovers)

- Open WebUI auxiliary-generation accounting (untouched).
- Prompt caching/routing: NOT enabled; requires real metadata + owner
  price/privacy policy decision (master plan 15.2).
- Provider request contract is scripted-model based; a real
  stealth/union-alpha request capture is still pending (WP8 live scope).
- SKILL.md YAML frontmatter still breaks skill loading (WP7).

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

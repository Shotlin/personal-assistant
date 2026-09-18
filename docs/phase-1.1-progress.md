# Phase 1.1 implementation progress

## Round-17 checkpoint — live release: fast path proven live, token cuts, activation fix

Owner go-live round. All changes live-tested ONCE through the real chain
(no repeated UI testing; DB/log probes used for verification):

- LIVE (production gateway, real driver, real TCC): 'open calculator' ->
  'Opened calculator.' in 2.6s wall clock with ZERO model calls. This is
  the first honest live success of the exact recipe route (WP8 live
  scope); the macOS permission blocker was cleared earlier today
  (LaunchAgent + bounded daemon + posture guard).
- Live bug fixed (TDD, 7 new unit tests): verify_foreground derived
  identity from get_desktop_state, which carries NO foreground field on
  the real driver — every open_app recipe failed honestly but wrongly.
  Now derives foreground from list_apps' per-app active flag (live-
  verified payload), with the legacy read_state fallback for fakes.
- Live bug fixed: macOS launched apps in background; bring_to_front
  refuses pid-only when the app owns multiple top-level windows
  (code=ambiguous_window_target, candidates list). activate() now picks
  the best candidate (on-screen titled window preferred) and retries
  once with pid+window_id (verified live: active=true afterwards).
- Fixed recipe_turn_persist_failed (live: langgraph InvalidUpdateError
  'Ambiguous update, specify as_node' on the second turn of a chat):
  aupdate_state now passes as_node='model'.
- Token cuts (owner direction 'less, less, less token'):
  (a) compact tool descriptions: 19.8k chars -> ~1.6k chars (one-liners
  in policy.py; addressing contract kept on action tools);
  (b) compact system prompt (same 15 spec rules, ~40% shorter, added
  explicit concision rule); (c) COMPACT_PLANNER_ENABLED=true in .env
  (natural phrasing -> ONE compact call; flag documented for rollback);
  (d) Open WebUI auxiliary generation disabled via compose env
  (ENABLE_TITLE_GENERATION, ENABLE_TAGS_GENERATION,
  ENABLE_FOLLOW_UP_GENERATION, ENABLE_AUTOCOMPLETE_GENERATION=false —
  each fired a hidden model request per message).
- Full gates: 361 passed, 1 skipped (unit+integration); ruff/mypy clean
  (46 files). Model unchanged: z-ai/glm-5.3-flash via OpenRouter.

Open queue (unchanged truths): SceneEntry consumption by
executor/recipes, constraints wiring on both routes (still {}), real
p50/p95 benchmark (schema exists, no measured claim), Open WebUI
auxiliary-generation accounting parity now that generation is off,
prompt-caching decision (owner policy).

## Round-16 checkpoint — failed-turn retry (live incident) + model switch

LIVE INCIDENT (user screenshot, Open WebUI '2/2' regeneration): a turn
whose first run terminally FAILED could never be retried — the WP4 dedup
rejected any redelivery with the same user-message id, so the user was
stuck with '[This message was already received...; Current run status:
failed.]'. Fixed, TDD (4 new registry tests red first + 1 e2e red first):

- runs.claim(): when the existing run's status is TERMINAL ('failed' or
  'cancelled') and the request digest matches, a conditional
  `UPDATE ... WHERE status IN ('failed','cancelled')` performs the
  atomic takeover (exactly one concurrent retry winner — pinned), resets
  status to 'running' and clears failure_reason. Returns
  owned=True/reason='retry_after_terminal_failure' with the EXISTING
  run_id. Inflight and completed runs still dedup; identity conflicts
  (same id, different payload) still fail closed even after failure.
- chat_route: after an owned claim, the route adopts claim.run_id as the
  canonical id (the e2e caught this: a retry's fresh local run_id broke
  the action_ledger FK before the fix).
- Failure diagnostics (WP4, earlier this round): failed runs carry a
  bounded failure_reason (additive `failure_reason` column, finish()
  parameter, reason extracted from the fast path's honest reply, never
  user text) so the registry alone answers 'why did this fail'.
  test_run_store_safety's SELECT * was made column-explicit because the
  additive migration legitimately changes the tuple shape between
  snapshots.

Model switch (owner instruction): MODEL_NAME is now z-ai/glm-5.3-flash
via OpenRouter (session model changed accordingly); the provider
contract test pins the new identity. `.env` is owner-local (untracked).

Gateway RESTARTED (job bash-29) on :8787 — healthz/readyz OK — with the
retry fix and new model live. Full gates: 354 passed, 1 skipped;
ruff/mypy clean (46 files); diff check clean. Still open: constraints
wiring on both routes, cancellation/disconnect truth, provider-wire
capture, epoch management across recipes, live desktop verification
(permission gate still pending). No measured latency claims.

## Round-15 checkpoint — observation freshness policy wired

`scene.observation_is_fresh(outcome)` (TDD, red first): a normalized
ToolOutcome observation is fresh only when it carries an ISO `observed_at`
within the age bound AND a `navigation_epoch` consistent with the current
scene epoch. Unstamped or unparseable evidence is NOT fresh (must not
validate an effect); stale-stamped observations (old epoch or old
timestamp) fail even when `foreground_app` matches, closing the WP7
regression 'an unchanged unrelated screenshot does not prove current
input focus'. Recipe identity matching (`foreground_matches`) is
unchanged; freshness is the additional gate.

Red evidence: 2 failures (missing policy) before `observation_is_fresh`
existed; after, 8 scene/freshness tests pass. One test-side fixture fix
during the cycle: the fake tool's `get_input_schema` must return a
pydantic BaseModel subclass (the executor's real convention; a plain dict
raised `issubclass() arg 1 must be a class` — the same unfaithful-fixture
class of error as the round-7 keyword-expansion incident). Full gates:
347 passed, 1 skipped; ruff/mypy clean (46 files); diff check clean.

Scope note: unit-level policy wired into the observation contract; the
epoch is currently driver-stamped only when the driver provides it, and
no live desktop call was made (permission gate still pending, probing
paused). Recipes do not yet bump epochs themselves. Remaining:
constraints wiring on both routes, cancellation/disconnect truth,
provider-wire capture, epoch management across recipes, live desktop run.
WP1-WP8 full completion and measured latency improvement remain unclaimed.

## Round-14 checkpoint — WP7 scene freshness + fallback contract

Two WP7 pieces landed, both TDD:

- `src/assistant/runtime/scene.py` (new) + `tests/unit/test_scene_freshness.py`
  (5 tests, red first via missing module): the master-plan scene-entry shape
  (`app_instance, window_id, navigation_epoch, observed_at,
  semantic_fields, evidence_refs`), monotonic epoch progression, and one
  effect-aware freshness rule — evidence is fresh only when the epoch is
  current AND the observation is within the age bound. Pins the WP7
  regressions: a navigation bump invalidates prior evidence; an unchanged
  unrelated screenshot proves nothing about input focus; a failed recipe
  cannot validate against an older epoch (no re-executing a completed
  submission). NOTE: this is the contract module; executor/recipes do not
  consume SceneEntry yet (integration is future work).
- `tests/integration/test_general_fallback.py` (red first): pins the ACTUAL
  Phase 1.1 fallback contract — a failed fast-path recipe renders the
  honest failure with ZERO model calls (no automatic agent turn; the
  earlier draft wrongly asserted an agent reply), records terminal
  'failed', persists the exchange, and the NEXT general-agent turn
  receives both the original objective and the honest outcome in its
  prompt history (fallback decides without restarting completed steps).

Environment incident, resolved: the full suite caught
`MODEL_NAME=z-ai/glm-5.3-flash` in `.env` (modified 18 Sep 01:33, outside
this session's commits) disagreeing with the pinned
`stealth/union-alpha` provider contract test. Restored
`MODEL_NAME=stealth/union-alpha` in `.env`; the contract test passes. No
provider call was made; this only re-aligns local config with the
already-committed contract.

Full gates: 344 passed, 1 skipped (existing Starlette deprecation warning);
ruff/mypy clean (46 files); diff check clean. No live desktop or provider
calls this round; permission gate remains pending (probing paused per
round 11). Remaining: SceneEntry consumption by executor/recipes,
constraints wiring on both routes, cancellation/disconnect truth,
provider-wire capture, live desktop run. WP1-WP8 full completion and
measured latency improvement remain unclaimed.

## Round-13 checkpoint — planner wire-boundary hardening

`validate_plan` now enforces the wire contract before trusting any payload:
- size check first (`MAX_PLAN_CHARS`; oversized input raises before
  `json.loads` is ever reached, proven by a fail-loud monkeypatch),
- strict UTF-8 decode for bytes (`b"\xff"` raised UnicodeDecodeError out of
  json previously; now a domain `InvalidPlan`),
- decision plans must carry EXACTLY the menu shape (`clarification` with
  only `question`; `unsupported` with nothing else) and the decision string
  must be one of the two enumerated values; nulls, booleans, ints and
  unknown decisions are all invalid instead of silently mapping to
  UnsupportedTask,
- malformed context (`decision: True/42`, duplicate keys, deep nesting)
  still fail closed, and one bounded repair attempt survives for all of
  them (repairable oversized replies included).

Red evidence: 14 failures across the new `test_planner_boundaries.py`
before the change, including 8 accepted-but-should-reject decision shapes
and two oversized replies that validated as recipes. After the fix: 31
planner tests pass; one test-side regex mismatch (`match="size"` vs the
real message) was fixed in the test, not the code. Full gates: 338 passed,
1 skipped, ruff/mypy clean (45 files), diff check clean. No live desktop or
provider calls; the planner rollout flag stays false by default.

Scope note: this hardens planner INPUT validation only. It does not wire
constraints/memory into the planner (`context` is still ignored), does not
change execution permissions, and does not claim any live latency
improvement. Remaining: constraints on both routes, cancellation/disconnect
truth, provider-wire capture, WP7 compact window state, live desktop run.

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

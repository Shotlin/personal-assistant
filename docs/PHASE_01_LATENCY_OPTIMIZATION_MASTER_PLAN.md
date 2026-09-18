# Phase 1.1 - Low-Latency Personal Assistant: Master Plan

> **For implementation:** Execute the work packages sequentially in an isolated feature branch. Use test-driven changes and review each gate before continuing. Preserve one assistant identity and the existing model. This is a source-reviewed design and implementation specification, not a claim that a fix has been deployed or benchmarked.

**Prepared:** 2026-09-17  
**Repository:** `Shotlin/personal-assistant`  
**Reviewed revision:** `cca11e77b5119a2f4948d4c2106669f7ceca9f2c`  
**Model constraint:** keep `z-ai/glm-5.3-flash` through OpenRouter.  
**Architecture:** retain Open WebUI, the OpenAI-compatible FastAPI gateway, Deep Agents/LangGraph, PostgreSQL, and the persistent Cua Driver MCP connection. Add a small local action runtime so routine execution does not require a remote model decision for every mouse or keyboard event.  
**Spec:** this document, particularly Sections 4-12; implementation tasks are in Section 13.  
**Companion:** `IMPLEMENT_LATENCY_FIX_PROMPT.md`.

## 1. Executive decision

The main optimization is not another instruction saying "be faster." It is to change the unit of work given to the model.

**Current pattern:** model decides a small action -> tool -> model reads a large observation -> model decides the next small action.

**Proposed pattern:** recognize or plan a bounded task -> execute a tested local procedure -> verify its result locally -> return to the model only for a new decision, genuine ambiguity, or recovery.

This preserves the intelligence of the current model while taking it out of predictable mechanical steps. It is one assistant with multiple execution paths, not a new multi-agent organization.

### What this can reasonably achieve

- Known commands can have **zero LLM calls and zero inference tokens**, using an explicitly approved local recipe. Local CPU work and application execution still exist.
- A supported but naturally phrased request can often use **one planning call**, then several local actions, with no model call between them.
- The cursor can remain visibly associated with the active task without screenshot uploads or model-generated mouse motion.
- Novel tasks, fresh factual research, visual judgment, and complex editing still need model work. Network delay, cold application launches, website loading, and video rendering cannot be eliminated by a prompt.
- A 500 ms target is meaningful for local acknowledgement or warm dispatch. It is not an honest universal promise for completed arbitrary tasks.

### Global constraints

1. Do not change the user's model to a more expensive model, use `openrouter/auto`, or add model fallback aliases without approval.
2. Do not rebuild the application in Rust/C++/Go. CUA already has a native runtime; first remove unnecessary remote decisions.
3. Retain Deep Agents for general reasoning, memory, and unfamiliar tasks.
4. Retain the persistent MCP transport already implemented; do not regress to a per-action subprocess/session.
5. Preserve bounded CUA permissions, exact window targeting, owner control, and denial behavior.
6. No unrestricted host shell, generated-code `eval`, destructive operations, or silent permission expansion.
7. No new microservices, vector database, Kubernetes, or always-running video-analysis pipeline for this optimization.
8. Do not remove verification to make a benchmark look faster.
9. Do not fabricate activity, success, measured latency, or token savings.
10. Keep changes local to an implementation branch until reviewed; do not push or deploy from this planning exercise.

## 2. Evidence and confidence

### 2.1 What was inspected

The following application files were read at the revision above:

- `src/assistant/agent/build.py`
- `src/assistant/agent/observation_trim.py`
- `src/assistant/agent/system_prompt.py`
- `src/assistant/api/chat_route.py`
- `src/assistant/api/streaming.py`
- `src/assistant/api/turns.py`
- `src/assistant/main.py`
- `src/assistant/models/openrouter.py`
- `src/assistant/settings.py`
- `src/assistant/tools/cua.py`
- `src/assistant/tools/policy.py`
- `src/assistant/skills/computer-use/SKILL.md`
- `config/cua-capabilities.yaml`
- `tests/e2e/test_cua_calculator.py`

Also inspected: CUA's macOS cursor tool implementation and cursor motion defaults at **`cua-driver-rs-v0.28.2`**, the version recorded in the application's manifest. The older Phase 1 planning discussion mentioned 0.28.1; this audit follows the version recorded in the actual source, not that earlier assumption. See sources R1-R14 and C1-C2.

### 2.2 What the uploaded report supports

The supplied `Pasted markdown(20260917-153158).md` reports:

| Observation | Reported value | Evidence status |
|---|---:|---|
| Earlier Calculator run | 261 seconds | User-supplied report; not rerun here |
| Intermediate run after some changes | 553 seconds | User-supplied report; not rerun here |
| Latest reported Calculator run | 77.65 seconds | User-supplied report; not a gateway/UI benchmark |
| Approximate earlier execution count | 13 model calls and 9 actions | Report's count; complete timestamp trace unavailable |
| A direct observation probe | 632 ms, about 8.9k characters | One reported probe; not a distribution |
| Small model-call probes | 2.4-3.7 seconds | Different probe from the full workflow |

These results suggest improvement, but they do not establish p50/p95 latency, an exact bottleneck breakdown, or the cost of the real Open WebUI path. The report itself acknowledges missing timestamps and that the test bypassed the gateway. The report's initial diagnosis changed during the experiment; do not treat its first explanation as a proven exclusive cause.

### 2.3 What was not verified

- No live run on the user's Mac was performed in this review.
- No provider credentials, private `.env`, current account balance, or live OpenRouter generation records were read.
- The deployed process may differ from the reviewed commit. Record the actual running revision before implementation.
- The current endpoint's support for GLM reasoning-off/minimal, actual image billing, prompt caching, and price must be confirmed from provider metadata and small live tests.
- No source inspection can honestly prove that all future workflows are bug-free.

## 3. Source-backed findings

### F01 - The agent still makes too many remote decisions

**Verified in code:** `build.py` passes the CUA tool inventory into one general Deep Agent. There is no local recipe router/executor in the inspected path. Reducing each prompt does not eliminate the repeated model -> action -> model cycle. [R1, R9, R10]

**Interpretation:** this is the principal architectural opportunity, not a measured attribution of every slow second. It is unnecessary for familiar sequences such as opening an app or submitting a known search.

**Action:** add the three execution routes in Section 4; do not merely keep trimming tool descriptions indefinitely.

### F02 - Structured CUA results are discarded

**Verified in code:** `_caller` in `tools/cua.py` returns `result.content` and does not preserve `result.structuredContent`. Its custom wrapper does not use the standard adapter's multimodal/structured-content conversion. [R9]

**Risk:** useful result fields, window identity, or effect evidence may be lost to local verification. MCP content objects may also be serialized inefficiently. Whether raw base64 is actually becoming ordinary model text must be checked on the outgoing request; it is not proven just by this code.

**Action:** normalize MCP content and structured artifacts explicitly. Preserve machine-readable evidence locally. Send the model only a bounded summary and an image block when the task truly needs one. LangChain documents persistent sessions, structured artifacts, and multimodal conversion. [W1]

### F03 - Observation trimming is partial, not a complete context policy

**Verified in code:** `observation_trim.py` keeps one full observation globally, replaces older selected tool messages over 400 characters, and omits the `screenshot` tool from its observation-name set. It does not cap the newest result or trim every possible image-bearing output. [R2]

**Risk:** one large current payload can dominate; image output from another tool can remain; a useful state for one app can be erased when another app is observed. The stored transcript remains full, so database/checkpoint size also needs measurement.

**Action:** use task-scoped structured state, per-window freshness, explicit image retention, and final-wire payload inspection. Preserve authoritative evidence separately; do not destroy needed task constraints or valid tool call/result pairs.

### F04 - Observation instructions conflict

**Verified in code:** the tool description suffix requests another `get_window_state` after important actions, while the computer-use skill says one observation can cover several actions and unchanged screens should not be re-observed. [R10, R11]

**Action:** define one effect-aware rule: verify important outcomes, but perform routine checks in the local executor. A fresh model turn is not required to inspect every verified local transition. A transport acknowledgement alone is not proof of application success.

### F05 - Token reporting understates the run

**Verified in code:** `_usage_from` returns usage from the last message with usage metadata. It does not aggregate the current run's model calls. Streaming does not emit an aggregate usage result. [R4, R5]

**Action:** maintain a run-local usage ledger at the provider boundary, covering planner calls, general-agent calls, summaries, and utility requests. Record retries distinctly. Do not sum an entire persisted conversation and accidentally bill old turns again.

### F06 - Streaming timing ends before the work ends

**Verified in code:** `chat_completions` emits `run_finished` after `_run_agent_turn` returns a `StreamingResponse`; the generator runs afterward. Consequently the current duration is not the actual streamed task duration. [R4, R5]

**Action:** finalize timing/status in the stream lifecycle. A role-only first SSE chunk measures transport readiness, not useful response or first action.

### F07 - The current benchmark does not match the production path

**Verified in code:** `test_cua_calculator.py` builds the agent directly and calls `load_cua_tools`, the stateless loader. `main.py` uses `open_cua_connection`, the persistent transport. The test does not exercise the gateway's cursor setup or Open WebUI. [R6, R8, R9]

**Also verified:** the success assertions check that the final response contains `42` and that some mutating action happened. They do not independently prove the displayed calculator state. The denial test accepts a nonzero exit code, which can also reflect an invocation error. [R6]

**Action:** benchmark the same gateway path used by the UI. Use independent app-state evidence and discriminate a real policy denial from a bad command.

### F08 - Cursor disappearance has a concrete native setting to investigate

**Verified in exact-version CUA source:** v0.28.2 defaults to `idle_hide_ms = 20000`; `0` means no idle hiding. Its macOS `set_agent_cursor_motion` implementation supports that setting. [C1, C2]

**Interpretation:** a model wait longer than 20 seconds can explain idle disappearance even with a persistent MCP connection. This is a plausible contributor, not proof of the user's exact runtime cause.

**Action:** configure cursor motion once through a trusted control path, scoped to an active run; do not generate extra model calls or fake motion to keep it visible.

### F09 - Cursor/session lifecycle still needs hardening

**Verified in code:** the gateway starts/enables a CUA cursor session before every non-utility chat, including requests that may not need desktop control. No matching run-finalization cleanup appears in the inspected execution path. Session lifecycle tools are also exposed to the model; the wrapper permits a nonempty model-supplied session argument to override injection. [R4, R10]

**Action:** controller-owned, lazy, run-scoped sessions; forced trusted session binding; cleanup on every terminal path; single active owner of the desktop.

### F10 - Same-text turn handling can repeat expensive work

**Verified in code:** `decide_turn` compares text content rather than using the forwarded user-message ID as the execution key. Regeneration searches checkpoints and can reinvoke the model. A replay from a model node is not an external-action idempotency guarantee. [R4, R7]

**Action:** stable turn/run IDs, atomic claim of execution, action ledger, and explicit re-execution semantics. Duplicate delivery should return existing status/results, not click or submit again.

### F11 - Low reasoning is not the same as no billed reasoning

**Verified in code:** OpenRouter is configured with `reasoning={"effort": ..., "exclude": True}` and a default output cap of 2000. [R12, R13]

**Verified in provider documentation:** `exclude: true` hides reasoning from the response; it does not disable its generation or billing. Available reasoning controls differ by model/provider. [W2]

**Action:** retain low as the baseline, inspect actual supported controls, and A/B only supported alternatives on identical inputs. Track charged usage rather than visible response length.

### F12 - Soft defaults and generous retries do not bound total cost

**Verified in code:** observation limits use `setdefault`, so explicit tool arguments can override them. Model defaults allow 120 seconds and two retries. There is no inspected cumulative token/cost guard; the 2000-token setting is per call. [R10, R13]

**Interpretation:** retries could produce very long waits, but no retrieved trace proves that retries caused the reported run. Test timeout units against the installed SDK before changing them.

**Action:** hard task budgets, bounded repeated-error recovery, per-tool deadlines, accurate usage, and a cost-conscious interactive profile.

### F13 - Typed browser control cannot be silently added to this manifest

**Verified in repository:** the manifest records application-scoped generic input and explicitly notes that typed-browser and generic-input capabilities are separate. It currently permits Calculator, Chrome, and Terminal, not Premiere or arbitrary apps. [R14]

**Action:** use existing bounded CUA actions first. Introduce a typed-browser route only through a separately reviewed compatible configuration. App allowlisting does not establish URL-level restrictions or make Terminal harmless.

## 4. The proposed execution architecture

```text
Open WebUI
    |
    v
Gateway: authenticate, establish turn ID, claim run, acknowledge
    |
    v
Local deterministic router
    |
    +-- A. Exact approved command -> tested local recipe -> verified result
    |         No LLM request; no image sent to model
    |
    +-- B. Natural phrasing of supported task -> same GLM, compact plan
    |         Validate recipe/arguments -> local executor -> verified result
    |         Ask GLM again only for a genuinely new decision
    |
    +-- C. Unfamiliar/ambiguous task -> existing Deep Agent
              Compact semantic state -> targeted tools -> visual fallback

All routes share:
- authenticated user/project scope and durable run state
- policy/permission checks and action ledger
- existing persistent MCP transport
- a single desktop execution lease
- local result verification and cancellation
- accurate latency/cost events and evidence references
```

### Route A - Known local command

Examples: `Open Chrome`, `Open Calculator`, `Pause playback`, or `Play my saved lofi playlist` where the user has already configured one unambiguous playlist and the required operation is supported.

Implement a strict, small grammar with explicitly approved aliases. Do not use fuzzy semantic matching as authorization. Preserve query text and paths exactly where relevant. Ambiguous references, negation, mixed instructions, new account actions, or unknown arguments fall back to Route B/C without taking action.

Support tested English/Hindi/Bengali aliases as configuration, not a claim that a few regexes understand all language. Voice transcription remains a separate future subsystem; text routing works on its transcript later.

### Route B - One plan, local execution

The same `z-ai/glm-5.3-flash` model selects an approved recipe and provides its typed arguments. It does not produce a long sequence of raw mouse coordinates, shell commands, or source code to execute.

The executor validates the complete plan, resolves current targets, runs small sequential steps, and checks outcomes locally. A result renderer produces a short factual response from verified results. Do not automatically add another model call just to say "Done."

This is an intentional short path around the generic agent loop for supported operations. Merely adding a `run_recipe` tool to Deep Agents may still produce a final model call; measure it and implement the explicit gateway short-circuit when appropriate.

### Route C - General reasoning and unfamiliar interfaces

Keep the current Deep Agent for tasks that need research, uncertain interpretation, or visual decisions. Give it a small capability set for the current task rather than every driver tool at once. It may invoke the same local recipes whenever useful.

A failed recipe returns a bounded observation and exact partial-effect state to this route. It must not restart the task from the beginning or repeat completed writes.

### Why this is not blind batching

Local execution is still closed-loop. It checks relevant state before the next dependent action. The distinction is **local verification versus a cloud model round trip**, not verified execution versus unchecked execution.

- A new page, modal, changed focus, or stale element token interrupts the local sequence.
- Resolve a semantic target against the current snapshot; never reuse a stale index just because it worked previously.
- A batch stops at an approval boundary, uncertain result, or unsupported route.
- Use sequential actions on the same interface. Do not invoke multiple click/type mutations concurrently.
- Parallelize only independent read-only work with explicit resource isolation.

The architectural principle is consistent with published work on using local code to orchestrate MCP operations without carrying every intermediate result back through the model. This plan uses reviewed recipes instead of unrestricted generated host code. [W3]

## 5. First recipe library

Implement only the first three required recipes before expanding. The last two are optional follow-ons with separate acceptance gates.

| Recipe | Inputs | Local execution | Completion evidence |
|---|---|---|---|
| `open_app.v1` | approved application alias | resolve approved bundle ID, launch if necessary, wait for a usable window | matching process/window is present |
| `calculator.evaluate.v1` | bounded arithmetic expression | validate expression, open/resolve Calculator, enter with supported native controls, read result | independent displayed result readback matches an independently calculated expectation |
| `browser.search.v1` | query, approved search engine | resolve Chrome, navigate using a reviewed route, enter encoded query, wait for relevant state | current page/address or exposed search field matches the requested query |
| `media.play_saved.v1` | owner-approved playlist ID | open saved URL, resolve player, request playback, check player state | playback state or time advancement; otherwise report playback unverified |
| `coding.submit_task.v1` | approved project session and task text | resolve existing coding-assistant session, submit once, monitor state locally | text submitted to intended session and subsequent task/question state observed |

### Required restrictions

- `open_app.v1` only accepts registered aliases: initially `chrome` and `calculator`. Terminal requires the coding-session-specific route, not a generic command string.
- Arithmetic parsing must not use `eval`. Restrict operators and length; handle division by zero and unsupported expressions explicitly.
- URL creation uses a vetted encoder, not string interpolation into a shell. Restrict scheme/origins and handle redirects according to policy.
- Browser search completion is not playback completion. Do not claim music is playing just because a results page opened.
- "Latest music" requires a current result or explicitly selected service ordering, not a guessed or stale stored title.
- Consent popups, login, ads, changing page structure, and ambiguous media results may require fallback/owner interaction. Do not bypass authentication or anti-abuse controls.
- Coding tool questions are answered only from approved context. Account authorization, repository changes, and external submissions remain governed by existing policies.

### Example: what changes for music

Old route:

```text
GLM chooses Chrome -> launch -> GLM reads desktop -> GLM chooses address bar
-> click -> GLM reads screenshot -> type -> GLM reads screenshot -> Enter
-> GLM reads results -> choose video -> more observations and calls
```

New route:

```text
Known saved playlist: local match -> play_saved recipe -> local readback
New natural request: one GLM interpretation -> browser.search recipe
-> compact results only if a choice is needed -> local player action/readback
```

The second case still has a decision and external page latency. It should not contain a model call for each mechanical keystroke.

## 6. Result normalization and local state

### 6.1 Internal tool-result contract

Create a normalized internal record with these fields:

```json
{
  "tool_name": "get_window_state",
  "status": "ok",
  "effect": "not_applicable",
  "target": {"app_id": "calculator", "window_id": 101},
  "state_version": "observed-window-revision",
  "data": {"display_value": "42"},
  "evidence_refs": ["artifact:local-observation"],
  "model_summary": "Calculator display is 42.",
  "elapsed_ms": 632,
  "images_sent_to_model": 0
}
```

This is a proposed application contract; the values are an illustrative example, not another benchmark. Native CUA payload shapes must be mapped using the installed tool schema. Allowed statuses: `ok`, `denied`, `failed`, `unknown`. Effects: `confirmed`, `suspected_noop`, `unverifiable`, `not_applicable`.

Keep original evidence private. Do not append raw `structuredContent` twice as both a large text dump and an artifact. Keep structured artifacts accessible to the executor, not automatically injected into every model call.

### 6.2 Observation ladder

Use the cheapest sufficient observation:

1. Application/operation response with trustworthy effect evidence.
2. Targeted semantic readback: one field, current URL, button state, or matching controls.
3. Relevant accessibility subtree with a strict size limit and safety context.
4. A cropped or downscaled screenshot with coordinates/scale metadata.
5. Full-window/full-screen capture only when the narrower views cannot answer the question.

The ladder is an application policy, not a claim that every target exposes all five levels. Use the installed CUA action/effect semantics and refuse ambiguous actions rather than pretending an unavailable semantic route worked. [W4]

### 6.3 Hard context controls

- Preserve user constraints, approved permissions, current objective, and pending questions.
- Store bulky screenshots/trees in an artifact store, with scoped access and retention.
- Keep fresh state per application/window/navigation epoch, not just "the last tool message."
- Never let a recipe use a cached element token after its freshness has expired or relevant state has changed.
- A screenshot crop must retain the coordinate transform so the agent cannot click a crop-relative coordinate on the whole desktop.
- Keep full tool call/result pairing required by the model protocol. Preserve any provider-required reasoning/signature blocks; do not strip fields blindly.
- At the final provider boundary, count prompt bytes, estimated tokens, image blocks, and tool-schema bytes. Inspect this boundary to confirm trimming actually occurred.
- Enforce hard output size limits even when the model explicitly requests a larger accessibility tree. Return `truncated=true`, a targeted refinement mechanism, and protected modal/security information.
- Do not apply arbitrary truncation that can hide an approval dialog or make an incorrect target appear unique.

### 6.4 Images are not a continuous video feed to the model

Do not upload a screenshot for each cursor animation frame. Capture/compare locally when necessary; send an image only when a model decision requires its pixels.

Deduplicate identical images, but do not confuse identical pixels with identical semantic state: focus, document selection, invisible input state, and session identity can change without a visible pixel difference.

Image costs depend on the serving endpoint's encoding and accounting, not only JPEG byte size. Confirm that image bytes use supported multimodal blocks, rather than being converted to ordinary text by `str(...)`.

## 7. Cursor continuity and honest real-time feedback

### 7.1 Keep the working part

Keep `open_cua_connection` at application lifetime. This already avoids per-tool process/session setup in the production gateway. Fix the test path to match it; do not throw away this improvement. [R8, R9]

### 7.2 Session owner

Create a `DesktopSessionManager` in trusted application code:

- Start a run-scoped driver session lazily on first desktop operation.
- One active mutation owner for the desktop; reject/queue competing runs.
- Inject the trusted session into every operation, replacing or rejecting a model-supplied session.
- Do not expose start/end/motion configuration as normal model-selectable tools.
- Configure visibility once; no model polling to keep the cursor alive.
- Disable/end the session in `finally` on completed, failed, stopped, disconnected, or timed-out runs.
- Reset ContextVar tokens in the same execution scope that owns them. Do not rely on accidental task-context inheritance across the HTTP handler and async response generator.
- On restart, do not assume an old desktop lease/cursor session remains valid. Rebind and observe before continuing.

### 7.3 Native setting already available

CUA v0.28.2 contains `set_agent_cursor_motion` and documents `idle_hide_ms = 0` as never auto-hide. Its default is 20 seconds. [C1, C2]

After reviewing the manifest addition and validating the installed tool schema, use a trusted controller invocation equivalent to:

```json
{
  "session": "current-run-session",
  "idle_hide_ms": 0,
  "glide_duration_ms": 120,
  "dwell_after_click_ms": 40
}
```

The tool name is `set_agent_cursor_motion`. These are initial UX configuration proposals, not proven optimal settings. Keep this capability out of the general model-visible tool list. The current application manifest does not permit it yet; do not skip bounded-policy validation.

Set visibility back to idle/disabled when no run is active. "Never hide while this run is active" must not become "leave ghost cursors forever."

### 7.4 Distinguish work from waiting

Use local events such as:

`accepted`, `preparing`, `acting`, `waiting_for_app`, `waiting_for_model`, `needs_owner`, `paused`, `failed`, `completed`.

The visible cursor should reflect actual actions. A stationary waiting indicator is better than fake movement pretending work is occurring.

For Open WebUI, first use a compatible deterministic progress-text mechanism in the existing stream, at meaningful stage changes. Verify rendering with the pinned UI version. Generic OpenAI SSE extensions may be ignored; do not promise a custom status widget without a tested integration. SSE comments can keep a connection alive but do not provide visible acknowledgement.

A local status sentence costs no inference call, although it is still interface output. Keep status events out of model history or summarize them separately so they do not create future prompt bloat.

### 7.5 Stop must be local

Cancellation must set a local flag/lease revocation immediately and be checked before every action. Do not send "please stop" to GLM and wait for a cloud response before stopping new actions.

If a side effect already happened, report it. Cancellation is not undo. In-flight provider charges or already submitted external operations may not be reversible.

## 8. Keep GLM; optimize request shape and routing

### 8.1 Exact model preservation

The running configuration must resolve to `z-ai/glm-5.3-flash`. Log the model identifier and serving endpoint when available, never its API key. Do not silently substitute another model, a router alias, or a more expensive fallback.

Do not assert that this model is the globally cheapest or best without a current comparable benchmark. Its selection here is the user's explicit requirement.

### 8.2 Compact planner profile

For Route B, expose only a small, stable recipe schema and relevant task context. Start with:

- one normal planner call;
- one repair/clarification call if necessary;
- a hard maximum of three provider attempts, including retries;
- no model call per local action;
- no LLM-generated final sentence when a verified template suffices.

Use the existing general Deep Agent profile for work that genuinely requires broader reasoning. This does not create an autonomous second agent; it is a bounded model call inside the same assistant service.

### 8.3 Reasoning controls

Keep `low` as the measured baseline already reported. Inspect the model/endpoint's supported parameters and its actual wire behavior before adding `minimal`, `none`, or `enabled=false`.

Do not send unsupported knobs hoping they work. Some models require reasoning. `exclude=true` is a visibility control, not a cost-control guarantee. Reasoning usage must be included in the provider's charged output total without counting it twice. [W2]

Run a small controlled comparison on the SAME compact tool-planning input. A plain short chat response is not a substitute for measuring a tool-rich planning request.

### 8.4 Provider routing without changing models

OpenRouter supports provider sorting by latency, throughput, and price. Test latency preference for short interactive plans while retaining owner-approved endpoint, privacy, and price constraints. Throughput preference is not necessarily the best first-response preference. [W5]

Conceptual request fragment:

```json
{
  "model": "z-ai/glm-5.3-flash",
  "provider": {
    "sort": "latency",
    "require_parameters": true
  }
}
```

This is not a complete deployable configuration: apply the existing approved endpoint/price/data policies as well. Verify the installed `ChatOpenRouter` version's pass-through mechanism using a request-capture test. Do not invent constructor parameters.

`preferred_max_latency` is a preference, not a hard performance guarantee. Faster endpoints can cost more or reduce cache locality. If no compliant endpoint exists, return an explicit availability error rather than relaxing policy silently. [W5]

### 8.5 Caching

Cache stable knowledge, approved recipes, and unchanged document data with versioned keys. Use provider prompt caching only where supported and confirm actual cache-read metrics. Keep the stable system/schema prefix unchanged; place changing state afterward. Session affinity can improve cache reuse where supported. [W6]

Do not pad a small prompt merely to make it eligible for caching. Do not use cached model tool responses to replay clicks on a changed desktop. Prompt caching and caching a completed action decision are different things.

### 8.6 No minute-long invisible retry chain

Inspect actual retries and timeout units in the installed SDK. Preserve the existing timeout behavior until the capture test proves its semantics.

For the new interactive planner, start with an explicit 30-second provider deadline and no automatic retry by default. Recovery can use the remaining bounded attempts only after classifying the error and budget. This deadline is a safety ceiling, not the desired user-facing response time. Show a truthful waiting status much earlier.

Do not retry a GUI mutation after a timeout unless its outcome is known to be unapplied or the operation is safely idempotent. Read back unknown effects first.

## 9. Token and spending controls

### 9.1 Initial budgets, not universal model limits

These are proposed application limits for experimentation. Tune with accuracy/cost results; never silently truncate the task to fit them.

| Profile | Model calls/attempts | Prompt budget | Output budget | Images |
|---|---:|---|---|---:|
| Exact local recipe | 0 | 0 model tokens | 0 model tokens | 0 sent to model |
| Compact supported-task planner | normally 1; max 3 attempts | 6k estimated input per call; 12k cumulative input cap | 512 requested output per call; 1536 cumulative charged output ceiling where enforceable | 0 normally; at most 1 explicit visual escalation |
| General Deep Agent | preserve existing capability, add configured cumulative guard | report and cap per task category | preserve adequate room for valid tool output | demand-driven, not every action |

For models whose reasoning consumes the output allowance, a 512-token cap may be insufficient. Treat that as a measured profile incompatibility, not permission to execute truncated JSON. Adjust the bounded profile only with test evidence; do not claim a visible-token cap limits hidden billing universally.

Local token estimates are estimates. A hard dollar ceiling requires conservative reservation before a request using validated current prices/limits, plus reconciliation afterward. When pricing or usage is unknown, record it as unknown; do not display zero cost. A model-ID switch or provider price change invalidates the pricing snapshot.

### 9.2 What to count

For each run, record:

- unique model request/attempt ID and provider generation ID when available;
- input, output, reasoning, cached-input and cache-write tokens where reported;
- image blocks and request bytes;
- tool-schema bytes and selected capability set;
- provider-reported cost, currency, pricing snapshot date, and whether cost is final;
- failed/incomplete requests with known, estimated, or unknown usage;
- background title/tag/follow-up generation, separated from the task's main cost;
- tool actions, observations, application waits, retries, and recovery calls.

Use the provider's total output as the parent category; reasoning is usually a component of it, not another number to add on top. Cache categories must follow the endpoint's accounting rather than a universal assumed formula.

### 9.3 Open WebUI extras

For the low-cost pilot, disable automatic title, follow-up, and tag generation unless needed. Current Open WebUI docs list `ENABLE_TITLE_GENERATION`, `ENABLE_FOLLOW_UP_GENERATION`, and `ENABLE_TAGS_GENERATION`. Verify their effective persisted settings in the installed v0.11.3 deployment; changing an environment variable may not replace an existing saved setting. [W7]

Keep the utility route tool-free. Count any remaining utility request separately. Do not accidentally forward title generation into a computer-control task.

### 9.4 Simple cost model

```text
Total run cost = sum(actual billed cost of each model request)
              + separately priced tools/services
              + local/cloud infrastructure cost
```

An illustrative prompt-volume comparison, NOT a measured saving:

```text
13 calls x 6,000 input tokens = 78,000 total input tokens
 1 call  x 1,500 input tokens =  1,500 total input tokens
```

The example shows why reducing decision count can matter more than making a screenshot slightly smaller. Actual prices, cache discounts, reasoning, output tokens, and necessary extra decisions determine the real bill.

## 10. Measurable responsiveness targets

Do not put all latency into one number.

| Measurement | Initial target | Boundary |
|---|---|---|
| Local acknowledgement after gateway accepts task | p95 <= 150 ms | excludes browser-to-server network and cold process startup; must be visible text/event, not only an SSE role chunk |
| Warm known-command dispatch | p95 <= 500 ms | first approved native action submitted; not full application completion |
| Cursor feedback | no unexplained disappearance during active run | allow explicit paused/complete states; test with a model stall longer than 30 seconds |
| Warm Calculator recipe | stretch p95 <= 2 seconds | includes real display readback; measure actual native action costs before committing to SLA |
| New supported natural-language task | one model planning call in normal path | provider latency measured, not guaranteed below one second |
| Browser task completion | separately report page/service wait | a fast local submission does not mean YouTube/network/autoplay is instant |
| Stop acknowledgement | p95 <= 150 ms locally | no new mutation starts after cancellation is observed; disclose in-flight effects |

A single user-reported CUA observation of 632 ms already shows why "all tasks complete in 500 ms" is not established. Optimize what is on the critical path, and publish real distributions rather than substituting animation speed for task completion.

### Required latency decomposition

```text
request accepted
 -> identity/dedup/lock acquired
 -> context assembled
 -> model request sent
 -> first provider token
 -> validated plan ready
 -> native tool start/end
 -> app-ready / effect-confirmed
 -> final result visible
 -> resources released
```

Include time spent in PostgreSQL/checkpoint access and payload serialization. For parallel operations use the critical path, not an invalid sum of overlapping durations.

## 11. Durable execution, idempotency, and safe failure

Performance optimization must not increase the chance of typing into the wrong window or executing the same request twice.

### 11.1 Run identity

Use an authenticated user identity and a stable user-turn ID from the gateway integration. User text alone is not an execution key. Two intentional commands with the same text and different IDs are different turns; one repeated network delivery with the same ID is the same turn.

An atomic database claim decides which worker owns a run. Store `accepted`, `running`, `waiting`, `completed`, `failed`, `cancelled`, and `unknown_effect` states. Duplicates observe/replay the existing result stream or receive status; they do not start another desktop operation.

### 11.2 Action ledger

Before dispatch, record run ID, recipe version, step ID, target binding, parameters digest, policy version, and status. After dispatch, record outcome/evidence. Do not log credentials, full screenshots, or private text to ordinary telemetry.

After a crash between dispatch and acknowledgement, mark the action unknown and inspect state. Exactly-once arbitrary GUI execution cannot be guaranteed across that crash window. Do not advertise a transaction property the desktop does not provide.

### 11.3 Locking

Use one desktop mutation lease for this single-machine MVP. Per-thread locks prevent two requests racing the same conversation. Read-only requests that do not touch the desktop may proceed independently.

Never put a large model request inside a database transaction while holding row locks. Use leases with clear expiry/recovery behavior and trusted cancellation.

### 11.4 Permission boundaries

- Application scope is not authorization for all possible actions within that application.
- Chrome access must not silently mean arbitrary origin/upload access.
- Terminal access must not silently mean arbitrary host-shell execution.
- New typed-browser APIs or Premiere integration require a reviewed capability change and their own tests.
- A local recipe is trusted, reviewed code; model output supplies validated data, not executable authority.
- Enforce requested stops, owner questions, and denials locally. Do not rely on a prompt to enforce them.

## 12. File-level change map

Keep the existing layout; add small focused modules rather than restructuring the whole repository.

| File | Responsibility / change |
|---|---|
| `src/assistant/api/chat_route.py` | accepted/run lifecycle, routing, correct finalization, usage integration |
| `src/assistant/api/streaming.py` | real stream completion timing, local status, usage frame, cancellation cleanup |
| `src/assistant/api/turns.py` | ID-based execution semantics instead of text-based replay |
| `src/assistant/api/schemas.py` | compatible usage/status fields and bounded request validation |
| `src/assistant/settings.py` | feature flags, profile budgets, cursor options, provider preferences |
| `src/assistant/models/openrouter.py` | capability-checked reasoning/routing and request instrumentation |
| `src/assistant/tools/cua.py` | preserve MCP structured artifacts and multimodal content; retain persistent transport |
| `src/assistant/tools/policy.py` | hard limits, typed failures, controller-only session tools, trusted target injection |
| `src/assistant/tools/result_normalizer.py` (new) | small model summary plus locally retained structured evidence |
| `src/assistant/agent/observation_trim.py` | task/per-window context shaping including image-bearing results |
| `src/assistant/agent/build.py` | general-agent fallback and small selected capability sets |
| `src/assistant/agent/system_prompt.py` | consistent effect-aware verification and appropriate progress rules |
| `src/assistant/skills/computer-use/SKILL.md` | plan/recipe preference; no fresh model call for each mechanical action |
| `src/assistant/observability/usage.py` (new) | request-scoped usage ledger and cost reconciliation |
| `src/assistant/observability/timing.py` (new) | monotonic spans and truthful terminal events |
| `src/assistant/runtime/contracts.py` (new) | typed plans, results, events, budgets |
| `src/assistant/runtime/router.py` (new) | strict local command recognition with conservative fallback |
| `src/assistant/runtime/executor.py` (new) | sequential guarded recipe execution |
| `src/assistant/runtime/recipes.py` (new) | approved recipe registry and versions |
| `src/assistant/runtime/session.py` (new) | lazy CUA session and desktop lease ownership |
| `src/assistant/runtime/state.py` (new) | target freshness and minimal local app state |
| `src/assistant/runtime/runs.py` (new) | durable run/action ledger; atomic run claim |
| `src/assistant/runtime/planner.py` (new) | compact same-model plan parsing; no independent autonomous loop |
| `config/cua-capabilities.yaml` | reviewed cursor-motion permission only; other route expansion separate |
| `scripts/benchmark_latency.py` (new) | same-gateway-path comparative benchmark |
| `scripts/probe_provider.py` (new) | bounded model capability/usage test, never prints credentials |
| `tests/e2e/test_cua_calculator.py` | actual gateway path and independent display verification |
| `README.md` | measured results, limitations, flags and rollback instructions |

New directories also require normal Python package initializers according to the existing project packaging convention.

## 13. Ordered implementation work packages

Each work package follows: write failing regression tests -> implement the smallest targeted change -> run targeted tests -> run existing gates -> review diff -> commit locally. Do not combine all changes into one unmeasurable patch.

### WP1 - Truthful baseline and usage accounting

**Modify/create:** API routing/streaming, `observability/usage.py`, `observability/timing.py`, benchmark script.

**Contracts:**

- `UsageLedger.record(call_id, *, input_tokens, output_tokens, reasoning_tokens, cached_input_tokens, cost_usd) -> None`; missing provider values are `None`, not zero.
- `UsageLedger.snapshot() -> dict`; returned values are current-run totals, known/unknown counts, and non-additive detail counters.
- `RunTimeline.mark(event, *, monotonic_ns, metadata) -> None`; terminal event occurs once, after real completion/cleanup.

**Regression example for the proposed public interface:**

```python
from decimal import Decimal
from assistant.observability.usage import UsageLedger


def test_usage_counts_each_provider_call_once():
    ledger = UsageLedger()
    for call_id, input_tokens, output_tokens in [("a", 100, 20), ("b", 200, 30)]:
        ledger.record(
            call_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=5,
            cached_input_tokens=0,
            cost_usd=Decimal("0.001"),
        )
    ledger.record(
        "b", input_tokens=200, output_tokens=30, reasoning_tokens=5,
        cached_input_tokens=0, cost_usd=Decimal("0.001"),
    )
    totals = ledger.snapshot()
    assert totals["input_tokens"] == 300
    assert totals["output_tokens"] == 50
    assert totals["reasoning_tokens"] == 10
    assert totals["cost_usd"] == Decimal("0.002")
```

- [ ] Add `tests/unit/test_usage_ledger.py` with the example and unknown-usage cases.
- [ ] Add a streaming test with a controllable fake generator: `run_finished` must not exist before the generator terminates.
- [ ] Instrument serialization, provider calls, tool calls, and app waits without logging payload contents.
- [ ] Aggregate usage at the provider request boundary, not by rescanning old graph messages.
- [ ] Benchmark both direct gateway and actual Open WebUI path; record utility calls independently.
- [ ] Run `uv run pytest tests/unit/test_usage_ledger.py tests/integration/test_gateway_streaming.py` and existing lint/type gates.

**Gate:** a two-call fake run reports both calls; duplicate streamed usage is not counted twice; delayed stream has accurate elapsed time. Baseline report clearly marks no live measurements when live flags are off.

### WP2 - MCP result normalization and bounded observations

**Modify/create:** `tools/cua.py`, `tools/policy.py`, `tools/result_normalizer.py`, observation trimming.

**Contracts:**

- `normalize_mcp_result(result) -> ToolOutcome`; retain `structuredContent`, typed status, bounded text, and local image references.
- `ToolOutcome.model_content(*, allow_images: bool) -> list[dict]`; never return Python representations of MCP objects.
- `ToolOutcome.structured: dict`; machine-parseable evidence remains available locally.

**Regression example:**

```python
from types import SimpleNamespace
from assistant.tools.result_normalizer import normalize_mcp_result


def test_structured_evidence_survives_without_model_image_upload():
    result = SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(type="text", text="Display is 42")],
        structuredContent={"display_value": "42", "effect": "confirmed"},
    )
    outcome = normalize_mcp_result(result)
    assert outcome.structured["display_value"] == "42"
    assert outcome.effect == "confirmed"
    assert outcome.model_content(allow_images=False) == [
        {"type": "text", "text": "Display is 42"}
    ]
```

- [ ] Add tests for TextContent, ImageContent, mixed content, malformed structured data, native denial, timeout/unknown effect, and screenshot retention.
- [ ] Verify installed adapter APIs; either use its persistent-session conversion or preserve equivalent semantics in the custom wrapper.
- [ ] Replace unstructured `Error: ...` loss of classification with typed errors plus safe concise summaries.
- [ ] Hard-cap requested observations and protect safety-relevant modal state.
- [ ] Include screenshot/zoom and other image-bearing output in context control.
- [ ] Preserve original artifacts privately and verify the outgoing provider request contains no raw base64 text dump.
- [ ] Run `uv run pytest tests/unit/test_mcp_normalization.py tests/unit/test_observation_budget.py`.

**Gate:** structured evidence survives; no default image upload; a model cannot override hard limits; unavailable data is not treated as confirmed evidence.

### WP3 - Cursor/session lifecycle and local cancellation

**Modify/create:** `runtime/session.py`, route/stream lifecycle, policy wrappers, manifest.

**Contracts:**

- `DesktopSessionManager.open(run_id)` is an async context manager with lazy activation through `ensure_started()`.
- `DesktopSessionManager.cancel(run_id)` marks a local cancellation request.
- The manager owns session IDs and motion settings, never the model.

**Test behavior to encode in `tests/unit/test_desktop_session.py`:**

```python
async def test_non_desktop_request_does_not_start_a_driver_session(manager, fake_driver):
    async with manager.open("read-only-chat"):
        pass
    assert fake_driver.calls == []


async def test_session_is_closed_after_an_exception(manager, fake_driver):
    try:
        async with manager.open("desktop-run") as session:
            await session.ensure_started()
            raise RuntimeError("simulated interruption")
    except RuntimeError:
        pass
    names = [call.name for call in fake_driver.calls]
    assert names.count("start_session") == 1
    assert names.count("end_session") == 1
```

The test fixtures must create a fake driver, record calls, and perform no desktop actions. Add explicit tests for cancellation, client disconnect, timeout, reconnect, and attempted session override.

- [ ] Remove session lifecycle controls from the model-visible tool inventory.
- [ ] Review/validate `set_agent_cursor_motion` support and bounded manifest addition for installed v0.28.2.
- [ ] Set idle hiding to zero only during active work; configure short glide/dwell as a UX experiment.
- [ ] Ensure cleanup for streaming and non-streaming paths; retain one persistent MCP connection.
- [ ] Add a local waiting status; no repeated model call or fake motion for keepalive.
- [ ] Live-check >30-second model wait, explicit Stop, and no cursor leak into the next unrelated chat.
- [ ] Run `uv run pytest tests/unit/test_desktop_session.py tests/integration/test_gateway_streaming.py`.

**Gate:** active session remains identifiable through a long wait; Stop prevents new actions; pure chat creates no cursor; every run cleans up.

### WP4 - Exactly identified runs, without claiming exactly-once GUI effects

**Modify/create:** `api/turns.py`, `runtime/runs.py`, PostgreSQL schema migration/script, route/stream integration.

**Contracts:**

- `claim_run(user_id, chat_id, user_message_id, request_digest)` atomically returns either new execution ownership or the existing run.
- `record_action(run_id, step_id, state, evidence_ref)` records before/after/unknown outcomes.
- Same ID with a conflicting request digest rejects; same text with a new ID is allowed as an intentional new command.

**Integration test requirement:** two concurrent POSTs with the same ID produce one mutation; a retry after uncertain dispatch cannot resubmit until reconciliation.

```python
async def test_duplicate_delivery_executes_only_once(gateway_client, fake_driver, request_body):
    import asyncio
    headers = {"X-Test-User-Turn-Id": "same-user-turn"}
    await asyncio.gather(
        gateway_client.post("/v1/chat/completions", json=request_body, headers=headers),
        gateway_client.post("/v1/chat/completions", json=request_body, headers=headers),
    )
    assert fake_driver.mutation_count == 1
```

This uses an authenticated test fixture that maps `X-Test-User-Turn-Id` into the same trusted identity contract; do not enable such a test header in production.

- [ ] Use actual Open WebUI user-message lineage for production identity and test it end to end.
- [ ] Stop content-equality time travel from re-executing external actions.
- [ ] Add a desktop mutation lease and per-thread serialization.
- [ ] Test crash after dispatch/before acknowledgement -> `unknown_effect` -> readback, not blind replay.
- [ ] Define client disconnect as cancellation for this interactive MVP unless an explicitly detached job mode is later added.
- [ ] Run `uv run pytest tests/integration/test_run_dedup.py tests/integration/test_action_recovery.py`.

**Gate:** duplicate network delivery does not double-click, retype, relaunch a coding job, or spend a second model call unnecessarily.

### WP5 - Local recipes: the largest latency/token reduction

**Create:** runtime contracts, router, recipes, executor, state; integrate into gateway ahead of generic model execution.

**Contracts:**

- `match_local_command(text, approved_context) -> RecipeRequest | None`.
- `execute_recipe(request, runtime) -> RecipeResult`; runtime provides authorized CUA operations, state, cancellation, ledger, and evidence.
- `render_result(result) -> str`; produces only evidence-supported completion text.

**Router regression examples:**

```python
from assistant.runtime.router import match_local_command


def test_exact_known_command_selects_recipe():
    request = match_local_command("Open Chrome", approved_context={})
    assert request is not None
    assert request.recipe_id == "open_app.v1"
    assert request.arguments == {"app_id": "chrome"}


def test_negation_is_not_turned_into_an_action():
    assert match_local_command("Do not open Chrome", approved_context={}) is None


def test_unknown_project_or_side_effect_falls_back():
    text = "Open Terminal, delete the project, then open Chrome"
    assert match_local_command(text, approved_context={}) is None
```

- [ ] Start with exact `open_app.v1`, `calculator.evaluate.v1`, `browser.search.v1`.
- [ ] Resolve targets using fresh observations and perform checks within the local executor.
- [ ] Use approved keyboard shortcuts or semantic controls rather than model-decided individual keypresses.
- [ ] Do not hardcode pixel coordinates or static accessibility tokens.
- [ ] Test modal interruption, stale token, wrong foreground app, session expiry, and unsupported action.
- [ ] Count every native mutation inside a recipe against the same policy budget; one macro call is not one action for safety accounting.
- [ ] For exact matched commands, assert model-request count == 0 and images-sent-to-model == 0.
- [ ] Run `uv run pytest tests/unit/test_local_router.py tests/integration/test_recipe_executor.py`.

**Gate:** a verified known workflow finishes without calling the model. Failure pauses/escalates, never guesses a new target.

### WP6 - Compact same-model planning and budget enforcement

**Create/modify:** `runtime/planner.py`, models adapter, settings, agent capability selection.

**Contracts:**

- `plan_supported_task(text, context, model) -> RecipeRequest | NeedsClarification | UnsupportedTask`.
- Accept only an enumerated recipe ID and its validated arguments; reject extra executable fields.
- The complete plan must arrive and validate before executing; never execute a partially streamed tool argument.

**Validation regression example:**

```python
import pytest
from assistant.runtime.planner import validate_plan, InvalidPlan


def test_unknown_executable_payload_is_rejected():
    with pytest.raises(InvalidPlan):
        validate_plan({
            "recipe_id": "open_app.v1",
            "arguments": {"app_id": "chrome"},
            "shell": "unexpected executable payload",
        })
```

- [ ] Capture the provider request to verify exact model ID and supported reasoning/routing fields.
- [ ] Give the compact planner only the current objective, relevant preferences, supported recipe descriptions, and targeted state.
- [ ] Normally one planner call; no obligatory final model call after a verified result.
- [ ] Register bounded recovery, not an open-ended retry loop. Validate truncated output never executes.
- [ ] Enable prompt caching/routing only after checking real metadata and owner price/privacy policy.
- [ ] Disable or account for Open WebUI auxiliary generation.
- [ ] Run `uv run pytest tests/unit/test_compact_planner.py tests/unit/test_run_budgets.py tests/integration/test_provider_request_contract.py`.

**Gate:** natural phrasing of a supported task uses one model decision in normal conditions; constraints and unclear questions are not discarded to meet the budget.

### WP7 - Compact state and general-agent fallback

**Modify:** observation trimming, general-agent build/tools, computer-use skill and system prompt.

**Contracts:**

- A scene entry has `(app_instance, window_id, navigation_epoch, observed_at, semantic_fields, evidence_refs)`.
- A cached recipe contains selectors/procedure, never live element tokens.
- Fallback receives executed-step history and current verified state; it does not restart completed steps.

**Regression behavior:** a navigation change invalidates prior element tokens; an unchanged unrelated screenshot does not prove current input focus; a failed recipe cannot re-execute a completed submission.

- [ ] Replace conflicting observation rules with one effect-aware local verification policy.
- [ ] Choose a small task-relevant tool set; keep full native schemas in the executor.
- [ ] Keep provider-required message structures and protected instructions intact.
- [ ] Add targeted visual escalation with image count/payload budget and crop coordinate metadata.
- [ ] Test both semantic-only and canvas/visual tasks; do not declare screenshots permanently unnecessary.
- [ ] Test that instructions embedded in a web page, accessibility tree, or tool error cannot grant permission, change the run goal, or activate a new recipe.
- [ ] Run `uv run pytest tests/unit/test_scene_freshness.py tests/integration/test_general_fallback.py tests/unit/test_observation_budget.py`.

**Gate:** compact inputs improve efficiency without stale-target mistakes, cross-window confusion, or missing approval dialogs.

### WP8 - End-to-end release gate and rollback

**Modify/create:** real gateway/UI tests, benchmark runner, runbook and measured report.

- [ ] Replace the Calculator test's final-text-only success criterion with independent displayed-state verification.
- [ ] Verify policy denial from the native structured result, not just a nonzero command exit status.
- [ ] Run the full existing unit/integration suite and new regression cases.
- [ ] Benchmark local-only primitives before spending on model trials.
- [ ] Run a small fixed-cost live screen of candidates, then a larger owner-approved comparison for release evidence.
- [ ] Compare identical model, input, endpoint constraints, app state, and artifact/driver versions.
- [ ] Test actual Open WebUI request -> gateway -> persistent MCP -> application -> verified response.
- [ ] Test >30s model stall, cancellation, duplicate submission, permission refusal, browser modal, provider timeout, and restart after uncertain action.
- [ ] Record successes/failures, p50/p95, sample count, total model calls, total tokens/cost, and image count. Report confidence limitations for small samples.
- [ ] Document feature flags, known unsupported routes, and rollback; do not claim unrun live tests passed.

**Gate:** success quality is at least the accepted baseline on the tested suite, cost and latency improve on the intended tasks, and no safety gate regresses. If the stretch latency target is missed, report the component breakdown and next constrained experiment.

### Common verification commands

These are commands for the implementation environment, not claims they have been run during this review:

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest tests/unit tests/integration
```

Extend existing tool config rather than weakening type checks or deleting failing tests. Live tests must be explicit and capped; do not launch all paid/desktop tests by default on every save.

## 14. Benchmark protocol

### A. Measure the transport and native operations first

No model required. Benchmark persistent-session observation, app resolution, one permitted action, targeted readback, and cursor session lifecycle. Capture warm and cold cases separately. This tells us whether a local 500 ms action target is plausible before changing model settings.

### B. Measure the model with representative inputs

Use three fixed payloads: a small chat; a compact recipe-selection prompt; the current full tool-rich prompt. Record actual outbound attempts, requested reasoning, input/output/cache usage, and time to a complete usable plan. Do not extrapolate a two-second greeting into a two-second 20-tool decision.

### C. Compare architectures

Use the same fixed tasks:

1. Open Chrome when already running.
2. Open Chrome from a cold state.
3. Evaluate 6 x 7 with Calculator display verification.
4. Search for an exact music query in Chrome.
5. Play a preapproved saved playlist where that recipe has been implemented.
6. Handle an unexpected consent/modal screen.
7. Submit a bounded task to the existing coding session and stop at an unknown question.
8. Cancel during a model wait, duplicate a request, and recover after interruption.

Separate task phases: command acceptance, first action, page load, selection, playback confirmation. Do not hide web loading from the completion number, but report it separately.

### D. Sample counts and spending

Start with a few smoke trials and local mocks. For an initial comparative table, use at least 20 interleaved trials per selected simple task when the owner approves the cost. Mark p95 as preliminary with that sample size; use a larger sample for a real performance commitment. Record failures rather than dropping them from the statistics.

Do not repeatedly run a 9-minute model-heavy baseline just to build a large sample. Use a bounded initial baseline and replayable fixtures for development, then spend on a limited live validation.

### E. Output schema for each benchmark row

```json
{
  "task_id": "calculator-warm",
  "revision": "record-actual-running-commit",
  "model": "z-ai/glm-5.3-flash",
  "route": "local_recipe",
  "warm_state": true,
  "ack_ms": null,
  "first_action_ms": null,
  "verified_completion_ms": null,
  "model_requests": 0,
  "images_to_model": 0,
  "input_tokens": 0,
  "output_tokens": 0,
  "cost_usd": "0",
  "effect_verified": false,
  "failure_code": null
}
```

This is a schema example. Null timing and `effect_verified=false` deliberately avoid inventing benchmark results. For a zero-inference local route, model cost is zero; local machine/application costs are not.

## 15. How this scales to documents, spreadsheets, and video

These are extension directions, not a promise that Phase 1 already supports them.

### Documents and agreements

Use structured templates, content generation, direct file operations, and rendered review. Do not enter a long document one word at a time through the GUI. New long-form content still requires output tokens; saving those bytes into a file is not a way to make generation free.

### Excel and tabular work

Read/write tabular data with a controlled spreadsheet adapter. Use formulas/calculations and bulk operations, then verify the output. Visual UI automation is useful for final layout checks or application-only features, not for manually typing thousands of cells. File-writing support alone is not proof formulas were recalculated; test in the relevant calculation engine.

### Premiere Pro

Use a bounded editing instruction or edit-decision representation -> a reviewed Premiere adapter -> timeline/project operations -> selected visual checkpoints. Adobe documents UXP APIs for projects, sequences, clips, playback, effects, and export. Check installed Premiere/UXP version before relying on an operation. [W8]

Do not send every playback frame to GLM. Analyze relevant media samples when judgment needs them, then perform deterministic edit operations locally. Complex creative decisions and rendering remain real work. Browser-only or canvas-only features without an API may still need visual fallback.

### Coding assistants and video-generation websites

When a supported direct CLI/API interface exists, later add a typed adapter with bounded authority rather than endlessly screenshotting a chat window. For GUI-only integration, monitor locally with bounded polling or events and involve the model only when a new message/question matters. Do not claim a provider's generation/training/render wait was eliminated because your cursor moved sooner.

## 16. Benefits, trade-offs, and failure modes

| Change | Benefit | Trade-off / risk | Mitigation |
|---|---|---|---|
| Local recipes | zero inference on known tasks; rapid consecutive actions | only covers explicitly implemented operations | conservative fallback; grow library from measured demand |
| Compact planning | fewer calls and smaller prompts, same model | missing context can produce wrong interpretation | protect constraints; ask when ambiguous; enforce schemas |
| Semantic local verification | less image transmission and fewer model reads | some apps expose poor or stale accessibility data | fresh target checks and visual escalation |
| Stable active cursor | clearer continuity without inference cost | visibility alone can imply false progress | explicit waiting state; no fake motion; cleanup |
| Provider latency preference | may reduce interactive wait | possible price/cache/reliability differences | price/privacy constraints and measured comparison |
| Low/off reasoning where supported | can reduce overhead for routine plans | worse judgment on complex cases | per-task profiles and quality tests |
| Strict budgets | predictable maximum work and spending exposure | task may stop before completion | honest partial result and controlled escalation |
| Recipe/metadata caching | avoids repeated setup work | stale selectors, permissions, or settings | versioning and invalidation; never cache active element tokens |
| Fewer arbitrary GUI actions | smaller chance of wrong clicks | more engineering per common app workflow | begin with highest-frequency routines |

The strongest positive is that these changes target the multiplier: **the number of expensive model decisions per completed task**. They do not merely make a slow loop look prettier.

## 17. Rollout and rollback

Feature flags:

```text
LOCAL_RECIPES_ENABLED=false
COMPACT_PLANNER_ENABLED=false
STRICT_OBSERVATION_BUDGETS_ENABLED=false
ACTIVE_CURSOR_PERSISTENCE_ENABLED=false
```

These are proposed new settings, not existing environment variables. Add validation and tests before using them. Instrumentation and accurate billing are correctness fixes and should not be rolled back merely because they reveal higher usage.

Rollout order:

1. Instrument and record baseline; no workflow changes.
2. Normalize tool outcomes, harden lifecycle/dedup, and fix benchmark truthfulness.
3. Enable one approved local recipe for the owner's test session.
4. Add compact planning for that recipe, with a fallback.
5. Expand the library only after evidence.
6. Keep the original general agent as a controlled fallback, not an excuse to ignore local failures.

If a new recipe regresses, disable that recipe/version, retain evidence and safe run state, and return to the known fallback. Do not revert to unrestricted permissions or replay an unknown side effect.

## 18. Definition of done

- [ ] The exact requested model remains configured for every nonlocal model call.
- [ ] Actual provider attempts and full-run tokens/cost are accounted for without duplicates.
- [ ] Stream duration reflects completed execution rather than response-object creation.
- [ ] Known local commands use zero model calls, demonstrated by request capture.
- [ ] Supported natural-language commands use one normal compact planning call, verified by traces.
- [ ] Important effects are verified independently; Calculator success is not inferred from final prose.
- [ ] Images are sent only when needed and in proper multimodal form.
- [ ] The cursor remains coherent through a long wait and cleans up after completion/cancellation.
- [ ] Stop prevents further actions locally; duplicate delivery does not execute again.
- [ ] All reviewed permissions and current user/project isolation remain intact.
- [ ] Existing behavior is regression-tested, including unfamiliar-task fallback.
- [ ] Benchmark reports disclose sample size, failure cases, pricing uncertainty, and missed targets.
- [ ] No claim of "zero latency for everything" or "bug-free" replaces the measured report.

## 19. Sources

Repository links below are pinned to the inspected revision. The local user's report is separate evidence, not an upstream benchmark. Current web documentation was checked on 2026-09-17; implementation must verify behavior against installed versions.

### Application source

- **R1:** [Agent assembly](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/agent/build.py)
- **R2:** [Observation trimming](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/agent/observation_trim.py)
- **R3:** [System prompt](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/agent/system_prompt.py)
- **R4:** [Gateway and usage extraction](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/api/chat_route.py)
- **R5:** [Streaming](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/api/streaming.py)
- **R6:** [Calculator test](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/tests/e2e/test_cua_calculator.py)
- **R7:** [Turn resolution](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/api/turns.py)
- **R8:** [Application lifespan](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/main.py)
- **R9:** [Persistent and stateless CUA paths](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/tools/cua.py)
- **R10:** [Tool policy and descriptions](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/tools/policy.py)
- **R11:** [Computer-use skill](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/skills/computer-use/SKILL.md)
- **R12:** [OpenRouter adapter](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/models/openrouter.py)
- **R13:** [Settings](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/src/assistant/settings.py)
- **R14:** [Bounded manifest and recorded CUA version](https://github.com/Shotlin/personal-assistant/blob/cca11e77b5119a2f4948d4c2106669f7ceca9f2c/config/cua-capabilities.yaml)

### Exact-version CUA source

- **C1:** [macOS cursor controls, v0.28.2](https://github.com/trycua/cua/blob/cua-driver-rs-v0.28.2/libs/cua-driver/rust/crates/platform-macos/src/tools/cursor_tools.rs)
- **C2:** [Cursor motion defaults, v0.28.2](https://github.com/trycua/cua/blob/cua-driver-rs-v0.28.2/libs/cua-driver/rust/crates/cursor-overlay/src/motion.rs)

### Official documentation and engineering references

- **W1:** [LangChain MCP: sessions, structured artifacts, multimodal content](https://docs.langchain.com/oss/python/langchain/mcp)
- **W2:** [OpenRouter reasoning controls and billing](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- **W3:** [Anthropic: local code orchestration of MCP work](https://www.anthropic.com/engineering/code-execution-with-mcp)
- **W4:** [CUA action selection and effect verification](https://cua.ai/docs/reference/cua-driver/action-selection-policy)
- **W5:** [OpenRouter provider routing and price/latency preferences](https://openrouter.ai/docs/guides/routing/provider-selection)
- **W6:** [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
- **W7:** [Open WebUI generation settings](https://docs.openwebui.com/reference/env-configuration/)
- **W8:** [Adobe Premiere UXP APIs and version boundaries](https://developer.adobe.com/premiere-pro/uxp/resources/fundamentals/apis/)

## Final decision

Do not replace the current model or keep telling it to click faster. Fix measurement first, then add a guarded local action runtime. Preserve model intelligence for the decisions that need intelligence; execute familiar mechanics locally, with evidence and immediate owner control.

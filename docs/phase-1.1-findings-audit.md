# Phase 1.1 — findings audit against the current working tree

Reviewed plan: `docs/PHASE_01_LATENCY_OPTIMIZATION_MASTER_PLAN.md`
Plan's reviewed revision: `cca11e7` (matches this branch's HEAD at branch creation).
Audit date: 2026-09-17 (branch `phase-1.1-latency`).

Status legend: **confirmed** (still present), **fixed** (already resolved after
the reviewed revision), **partial** (present with nuance), **unverified**
(needs runtime evidence).

| Finding | Status | Notes |
|---|---|---|
| F01 too many remote decisions; no recipe router | **confirmed** | No local recipe path exists; every mechanical action is a model decision. |
| F02 `_caller` drops `structuredContent` | **confirmed** | `tools/cua.py` `_caller` returns `result.content` only; persistent-session wrapper has no multimodal conversion. |
| F03 observation trimming partial | **confirmed** | One global full observation kept; no screenshot tool in the name set (none exists yet); newest observation uncapped; transcript in checkpoints stays full. |
| F04 conflicting re-observe instructions | **partial** | Tool-description suffix still says "re-run get_window_state after important UI actions" while the skill minimizes re-observation. Needs one effect-aware rule (WP7). |
| F05 `_usage_from` last-message-only | **confirmed** | `api/chat_route.py::_usage_from` reads one message's `usage_metadata`; no run aggregation; streaming emits no usage. |
| F06 `run_finished` before stream ends | **confirmed** | Emitted when `_run_agent_turn` returns the `StreamingResponse`; generator runs afterwards. |
| F07 benchmark ≠ production path; weak assertions | **confirmed** | `tests/e2e/test_cua_calculator.py` uses the stateless `load_cua_tools` and asserts text contains "42" plus ≥1 mutation; no independent display readback; denial test accepts any nonzero exit. |
| F08 cursor idle-hide 20 s default (v0.28.2) | **unverified locally** | Per plan's C1/C2 source citations; will verify against the installed driver's `set_agent_cursor_motion` schema during WP3. |
| F09 cursor/session lifecycle hardening | **confirmed** | Session+cursor enabled for every non-utility turn (even chat-only); no end/cleanup on terminal paths; `start_session`/`end_session` are model-visible tools; model-supplied nonempty session overrides injection. |
| F10 content-based turn dedup | **confirmed** | `decide_turn` compares content; `X-OpenWebUI-User-Message-Id` is logged but not used as the execution key; regeneration can re-run the agent. |
| F11 `exclude=true` ≠ unbilled reasoning | **confirmed (documented)** | Behavior per OpenRouter docs; treat charged totals as authoritative (usage ledger, WP1). |
| F12 soft defaults; per-call 2000-token cap only | **confirmed** | `setdefault` lets model args override observation limits; no cumulative run budget. |
| F13 typed-browser not addable silently | **confirmed (by design)** | Manifest stays application-scoped; browser-* tools are filtered app-side and absent from the manifest. |

Additional local observations (not in the plan):

- The e2e Calculator test now uses `open_cua_connection`? No — see F07: it still
  imports `load_cua_tools`; the gateway path is persistent (`open_cua_connection`).
- `Model "" was not found` operational incident (gateway down → empty OpenAI
  registry) is documented in README; unrelated to these findings.

Disposition: WP1-WP8 proceed as ordered by the master plan. WP1 and WP2 also
close F05/F06/F02/F03; WP3 closes F08/F09; WP4 closes F10; WP7 closes F04.

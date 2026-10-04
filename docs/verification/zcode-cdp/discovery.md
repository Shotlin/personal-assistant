# S1 discovery: ZCode 3.14.4 via debug port (2026-10-04)

Method: quit ZCode, relaunched with `--remote-debugging-port=<random>` (24392), listener verified as the
ZCode pid on `127.0.0.1`, read-only CDP (`Runtime.evaluate`, `Page.captureScreenshot`, `Input.dispatchMouseEvent`).
No prompt was sent, no setting changed, no token/credential read. Opened existing task and menus only.
Fixtures (structure only, no message text) are in `fixtures/`. Account display name is masked in saved files.

## Findings that change the plan

1. **Synthetic `element.click()` does not open Radix menus; real `Input.dispatchMouseEvent` does.** The client must
   click by coordinates (rect from `getBoundingClientRect`), then Escape to close.
2. **A structured stream exists.** The timeline's React props carry `rows` (kind, toolName, status, input, output,
   startedAt/endedAt) and `sessionPhase` (`completedSuccess` seen). The pane exposes `data-projection-seq`
   (change counter), `data-session-id`. So `observer.py` need not scrape text: poll/observe `data-projection-seq`,
   then read `rows` via the fiber. DOM fallback: `v4-row-<n>` + `chat-tool-call-block-*` with `data-tool-name`/`data-status`.
   Fiber access is the fragile part: contract check must assert `rows` array and `sessionPhase` exist.
3. **Plan identity is in the model test id**: `chat-model-select-item-custom:account%3Azai-start-plan:GLM-5.3-Flash`.
   The plan guard (R9) can compare that id, not display text. A custom provider ("new-provider") also exists and
   sessions record "Model switched ..." markers, so the guard is needed.
4. **Two plans, both expiring today.** "ZCode Trust Build" (GLM-5.3-Flash 100,000,000) expires Oct 4 21:30 and
   "ZCode Start Plan" (GLM-5.3 3,000,000, Flash 5,000,000) expires Oct 4 21:29. After that the balances/models will change.
   This also shows R8 (expiry) will happen in practice.
5. **Mode is per session**, and includes `yolo` ("Full access"). One existing task was on Full access. Sani must set
   the mode explicitly per run, never inherit.
6. `window.zcode.activateOrSetWorkspace('<existing path>')` returned `{activated:false}` with no UI change: it is a
   window/shell call, not a project creator. New project stays behind the native folder dialog (`project-add`), not clicked.

## R1-R6 data sources

| Req | Source | Selector / call | Status |
|---|---|---|---|
| R1 account | Sidebar account button text | `[data-testid=login-trigger]` (text + aria-label) | Display name only. **No email in menu.** Menu has Disconnect (never click). Identity for R8 = display name + plan set. |
| R2 balances | Settings -> Model settings -> Start Plan page | nav `model-provider-nav-item-preset:account:zai-start-plan`; page text: plan name, "Expires", per model "left / total", %, reset time | Real figures read (see fixture). Composer popover (`chat-context-usage-trigger`) only gives % + reset, no plan name: use Settings page for tokens, popover for cheap polling. |
| R3 models/mode/reasoning | Composer menus | `chat-model-select-trigger`, `chat-mode-select-trigger`, `chat-thought-level-select-trigger` and their `-item-*` ids, `aria-checked` | Verified. Models listed in picker: Start Plan GLM-5.3-Flash (Vision), GLM-5.3. Trust Build model is **not** in the picker. |
| R4 sessions | Sidebar | `workspace-item-<abs path>`, `task-item-sess_<uuid>` (text = title, age) | Verified. Only expanded projects list tasks; need to expand each (or Group view) for a full count. Fiber/`window.zcode` alternative not explored. |
| R5 streaming | Timeline fiber `rows` + `data-projection-seq` | see finding 2 | Verified on a finished session. **Not yet seen mid-run** (needs S4 real run). |
| R6 control | Page | send `v4-composer-send`, input `v4-composer-input`, mode/model/reasoning ids above | Controls exist. Send, cancel and permission cards untested (would spend tokens / change state). Cancel and permission-card selectors: **unknown**, S4 must find them with a real run. |

## Not possible / fallbacks

- Email for the account: not exposed in the UI. Fallback: display name + plan names + a non-secret hash of the set of plans.
- Per-run tokens (R10): balance delta from the Settings page; popover % is too coarse for small runs.
- New project: native dialog. Fallback: only folders already in ZCode's project list, else ask the user to add it.
- Credentials-file mtime signal (R8a) not tested this session (reading file metadata only; deferred to S5).

## Still open for S4/S5

Permission card DOM, cancel/stop control, failed-turn structure, mid-run `sessionPhase` values, behavior when the user is
typing in ZCode, how model switching shows in rows (a "Model switched" marker row exists).

## Housekeeping

ZCode was relaunched normally after discovery (no debug port). Helper scripts were scratch only and are not shipped.

# ZCode integration through its own app (debug port): plan (2026-10-04)

> **Status (2026-10-04, end of day): S1-S7 implemented.** Evidence in `docs/verification/zcode-cdp/`.
> Still needs the user: (1) a REAL account switch inside ZCode to confirm the banner (simulated in tests),
> (2) N8 terms confirmation before window mode is made the default. Known limit: ZCode runs commands it
> judges safe without asking; Sani stops the run afterwards and reports it.


Scope: **ZCode only.** Other tools (Codex, Antigravity, Qoder, Claude Desktop) are parked;
see `coding-agents-infrastructure-plan-2026-10-04.md` (its S1 is replaced by this plan).
Claude Code CLI stays as it is.

Executed in **separate sessions** (section 7). Each session ends with evidence, not a claim.

## 1. Goal

The Deep Agent uses the user's real ZCode app, signed in with the user's own Z.ai account
and plan, as a coding agent, and Sani **shows the truth about it**: who is signed in, what
is left on each plan, which model will run, what sessions exist, what ZCode is doing as it
works, and what actually changed on disk. When the user signs in with a different account
inside ZCode, Sani notices and re-syncs on its own.

Why the app and not `zcode -p`: the command line cannot see the Start Plan, has no model
choice, returns only a final answer, and has no balance or account command (all verified
2026-10-04). The app has all of it on screen and is the only place the Start Plan works.

## 2. Requirements

Functional (R = requirement, each has an acceptance check in section 7):

- **R1 Account.** Settings and the sidebar show the signed-in ZCode account (name and
  email or username) with "as of" time. Never a token.
- **R2 Balances.** Per plan and per model: tokens left, daily total, percent, reset/expiry
  time, as ZCode displays them (e.g. "GLM-5.3-Flash 5,000,000 / 5,000,000"), with "as of".
  Never estimated. A figure that cannot be read says "not read", not a number.
- **R3 Models.** List the models the app offers (with the plan they belong to), the current
  model and reasoning level, and the current mode.
- **R4 Sessions.** Show how many ZCode tasks/sessions exist, per project, with title and
  last activity; map each Sani chat to its ZCode task; resume an existing one.
- **R5 Streaming.** While a run is going, the chat shows ZCode's steps as they happen
  (assistant text, tool calls, permission requests, file edits, errors), not only the end.
- **R6 Control.** Sani can start a task, set model, reasoning level and mode, send a prompt,
  wait for completion, cancel, and answer permission cards, all within the user's run limit
  (look / edit / run) and folder allowlist.
- **R7 Verified results.** Files changed are determined from the **disk** (hash/`git diff`
  of the project folder), compared with what ZCode claims, and mismatches are reported.
  A claim that something was "run" is marked unverified unless a real output was captured.
- **R8 Account change.** If the account in ZCode changes (sign out, sign in as someone else,
  plan upgrade or expiry), Sani detects it within a minute, invalidates all cached account,
  balance, model and session data, re-reads them, tells the user ("Account changed:
  A → B"), and re-runs the model guard before the next run.
- **R9 Plan guard.** A run starts only if the model shown in the app belongs to the user's
  Z.ai plan (default policy `zai`); otherwise it refuses and spends nothing. Every result
  names the model and plan that ran.
- **R10 Usage per run.** Tokens used by a run = balance before minus after, when both reads
  succeed; otherwise "unknown".

Non-functional and safety:

- **N1 No secrets.** Sani never reads, stores, logs or sends ZCode's tokens or encrypted
  credential store. Account/balance come from the page ZCode itself renders.
- **N2 Debug port hygiene.** The port has no password: random port, `127.0.0.1` only,
  verified listener owner, opened only for a "ZCode control" session, closed (app relaunched
  normally) when idle or on kill switch. The Settings screen says this in plain words. Off
  by default.
- **N3 Fail closed.** If selectors/contract checks fail, the app version changed and is not
  yet verified, the port is not ours, or the account/model cannot be confirmed: refuse with
  a plain-words reason. Never click on a guess.
- **N4 One at a time.** One run per project; a global lock for the ZCode window; if the user
  is typing/active in ZCode, wait or ask instead of clicking.
- **N5 Bounded.** Step, time and idle limits; cancel works; permission cards are answered
  by Sani's ceiling (never "allow everything"); a denylist (sudo, `rm -rf /`, `git push`,
  piping to a shell) is refused even in run mode.
- **N6 Evidence.** Every phase writes evidence under `docs/verification/zcode-cdp/`
  (commands, raw outputs with secrets redacted, screenshots if useful). A phase is not done
  without it.
- **N7 Ship rules.** Python-only changes ship with `update-core.sh` (keeps macOS
  permissions); renderer/Rust changes are batched into one full install; delete the stale
  `core-override` on every full install. Format only touched files. Full unit suite, ruff and
  mypy pass **before** shipping.
- **N8 Terms.** The user confirms Z.ai's terms allow driving the app on their plan before
  app-mode is switched on by default.

## 3. What is verified today (2026-10-04, ZCode 3.14.4, Electron 41.0.3)

- Debug-port fuse enabled; ASAR integrity off. Launching with `--remote-debugging-port=N`
  works **only if ZCode is not already running** (single instance: otherwise the flag is
  ignored). Port listens on `127.0.0.1`. Chrome 146, protocol 1.3.
- One page target: `app.asar/out/renderer/index.html`, plus workers.
- The page, signed in, has: composer (`role=textbox`, contenteditable) with a **Send**
  button; model picker button (showed `GLM-5.3-Flash`); a reasoning-level control (`Max`;
  options Low/High/Max); **Switch mode**; **Today's balance**; **Computer Use**; sidebar
  with projects and tasks.
- Stable `data-testid`s seen: `sidebar`, `task-new-button`, `conversation-new-task`,
  `conversation`, `conversation-column`, `workspace-list`, `workspace-item-<path>`,
  `task-list`, `task-item-sess_<id>`, `login-trigger`, `task-settings-button`,
  `terminal-toggle`, `side-pane-toggle`, `workspace-header`.
- `window.zcode` (preload bridge): 122 shell functions (workspaces, tabs, remote control,
  file pickers, OAuth, updates, settings sync). **None sends a prompt or sets a model**;
  `activateOrSetWorkspace` exists (behavior unknown). So prompts/model go through the page.
- Settings → providers shows, per plan, "Today's balance" bars per model with tokens and
  expiry (seen in the user's own screenshot); the composer's "Today's balance" button is
  the faster entry point (content not yet read).
- ZCode also has its own login: **two different sign-ins exist** (the app's Z.ai sign-in vs
  the CLI's Coding Plan key). In app mode the **app's** state is the source of truth; Sani's
  "Sign in" must open ZCode's own sign-in, not run `zcode login`.
- Billing endpoint `zcode.z.ai/.../billing/balance` needs ZCode's own encrypted token
  (401 without it). We do not call it and do not decrypt anything (N1).

Not verified yet (S1 answers these): how the page talks to the engine (React state,
WebSocket frames, MessagePort); whether streaming text can be tapped as structured events;
what the balance popover and account menu contain; how permission cards look; how "new
project" can be done without the native folder dialog; what a failed turn looks like.

## 4. Architecture

```
Deep Agent --tool `zcode` (app mode)--> ClaudeCodeToolkit (shared: folders, ceiling, store,
                                         reporter, watchdog, "unverified" notes)
                                              |
                                  ZCodeAppBackend (new)
                                   |            |             |
                              CdpSession   PageAdapter     DiskVerifier
                       (launch/port/WS)  (selectors+verbs)  (git/hash of project)
                                   |
                              ZCode.app (relaunched with 127.0.0.1 port)
```

Modules (Python, in `src/assistant/coding_agents/zcode_cdp/`):

- `launcher.py`: find ZCode, check not running, `open -a ZCode --args --remote-debugging-port=N`,
  wait for the port, verify the listener is ZCode, `close()` relaunches normally.
- `client.py`: minimal CDP over `websockets` (evaluate, bindings, screenshot, key/click,
  Page/Runtime/Network events). No Node, no third-party automation dependency.
- `observer.py`: injects a MutationObserver (via `Runtime.addBinding` +
  `Page.addScriptToEvaluateOnNewDocument`) that reports conversation changes as structured
  events; falls back to polling snapshots. Exact source chosen in S1.
- `pages.py`: the **PageAdapter**: selectors in one file, verbs `status()`, `account()`,
  `balances()`, `models()`, `sessions()`, `new_task(project)`, `set_model()`, `set_reasoning()`,
  `set_mode()`, `send()`, `stream()`, `answer_permission()`, `cancel()`.
- `contract.py`: the selector contract: a list of checks run on connect; result stored with
  the ZCode version. Failing contract → backend disabled with a reason.
- `verify.py`: DiskVerifier: snapshot (hash + `git status/diff`) of the project folder before
  and after a run; report changed files; compare with ZCode's claim.
- `sync.py`: account-change watcher (section 6).
- Backend plugs into the existing `Backend` protocol (`preflight` = port + contract +
  account + model guard); the toolkit already handles folders, ceiling, sessions, reporting.

Data Sani keeps (SQLite, existing `claude_code_state`): `zcode_account`, `zcode_balances`,
`zcode_models`, `zcode_sessions`, `zcode_contract` (version + result), each with `as_of`.

## 5. What the user sees

- **Settings → ZCode**: account card (name/email, as-of, "Refresh"); plan/balance table (per
  plan: name, expiry; per model: left/total, percent, reset); model, reasoning, mode; sessions
  (count and list); control status (port closed/open, contract ok, app version); the
  plain-words port warning; folders and run limit (shared) editable here too.
- **Chat**: round header "ZCode", live steps, "Model: <id> (your Z.ai plan)", files changed
  (from disk), tokens used (balance delta), and a clear "unverified" note when applicable.
- **Sidebar dot**: green only when installed + app signed in + control ready + contract ok
  + folder set; amber/red say what to fix; "Account changed" banner on switch.

## 6. Account change and sync (R8) in detail

Signals (cheapest first): (a) mtime/key-names of ZCode's credentials file (never contents);
(b) while control mode is open, the page's account menu/avatar text and `login-trigger`
state, polled every ~30 s and after every run; (c) a changed set of plans/models/balances.
On any change: mark all cached data stale → re-read account, plans, balances, models,
sessions → compare account identity → if different, show "Account changed: A → B", clear
Sani↔task mappings that belonged to A, force the plan guard to re-confirm, and write an
audit line. If ZCode is signed out: status "Signed out in ZCode", runs refused, "Open ZCode
to sign in" opens the app (Sani never types credentials). Same path covers plan expiry.
Acceptance includes a **real** account switch done by the user, and a simulated one in tests.

## 7. Sessions of work

Every session starts from the prompt in section 9. Order matters.

### S1: Discovery (read-only), evidence, decisions
Goal: replace the "not verified" list with facts. Steps: user confirms nothing important
runs in ZCode; relaunch with port; map the DOM (skeleton only, no message text); read the
account menu, composer "Today's balance" popover, model/reasoning/mode controls, the
permission card, a failed-turn view **structure**; find the page→engine channel (React fiber
state, WebSocket frames via Network domain, MessagePort/postMessage tap) and judge which
gives structured streaming; test `activateOrSetWorkspace`; record skeleton fixtures; relaunch
ZCode normally. Output: `docs/verification/zcode-cdp/discovery.md` + fixtures + a decision
note on data sources. **Acceptance:** each R1-R6 has a named data source and selector, or an
explicit "not possible, fallback X". No code shipped.

### S2: CDP core + read-only data (account, balances, models, sessions)
Build `launcher`, `client`, `pages` (read verbs), `contract`, store, `zcode.status` additions
(extend the status payload, no UI yet), fake-CDP unit tests + fixture contract tests.
**Acceptance:** from a real run, a script prints the true account, balances per model,
models, sessions count/list; contract passes; port closed afterwards; suite/ruff/mypy clean;
evidence file saved. Ship with `update-core.sh`.

### S3: Settings UI + chat labels (one full install, batch)
Rust passthroughs, renderer: ZCode page (R1-R4 views, control status, port warning, folders
and limit editable), fixed sign-out wording, "Sign in" opens ZCode, sidebar dot rules,
usage chip with tokens left; preview fixtures; remove stale override at install.
**Acceptance:** user opens the installed app and sees real account/balances/models/sessions;
screenshots saved. (Re-grant macOS permissions is **not** needed for this feature, but the
install resets them: tell the user first.)

### S4: Run engine (R5, R6, R7, R9)
`send`, streaming, wait-done, cancel, permission answers by ceiling and denylist, run lock,
user-activity check, DiskVerifier, plan guard in `preflight`, Deep Agent wiring (app mode,
tool name `zcode`), step events → existing rounds. **Acceptance:** real prompt round-trip on
the Start Plan: steps stream in the chat, files created/changed match disk, result states
the model and plan; a refused case (wrong model) spends nothing; cancel works; scripted-model
end-to-end test passes; long-request path tested.

### S5: Account sync (R8)
`sync.py`, signals, invalidation, banner, login-state handling, tests with a simulated
switch. **Acceptance:** the user signs out and in as another account (or the same account
again) inside ZCode; within a minute Sani shows the change, caches are fresh, the guard
re-confirmed; evidence recorded. Also: sign-out → refused-run message; sign-in → recovers.

### S6: Model, reasoning, mode, sessions, usage (R3, R4, R6, R10)
Change model/reasoning/mode from Sani (guarded to Z.ai plan models); list/resume sessions;
map chats to tasks; per-run tokens from balance delta; new project via the page or
`activateOrSetWorkspace` if S1 found a way, else document the native-dialog limit.
**Acceptance:** real model switch verified by re-reading the picker and by the next run's
result; resume continues the same task; per-run token figure matches the balance change.

### S7: Hardening and handover
App-update detection (version change → contract re-run, read-only until verified), kill
switch closing the port, diagnostics page (last run, account, model, redacted step log),
docs, cleanup (stale override, plan doc status), final end-to-end acceptance run with the
real landing-page prompt in a scratch folder. **Acceptance:** every R has evidence; the
user's checklist (section 8) is ticked.

## 8. Definition of done

1. R1-R10 each have saved evidence from a real run.
2. The landing-page prompt the user keeps using runs through the Deep Agent on the Start
   Plan, with streamed steps, correct model line, disk-verified files.
3. A real account switch inside ZCode is reflected in Sani within a minute.
4. With ZCode closed, signed out, on the wrong model or after an update, Sani refuses with
   a plain reason and spends nothing.
5. No token, key or encrypted credential content appears in Sani's logs, DB or screens.
6. Full unit suite, ruff, mypy pass; the installed app shows everything.

## 9. Starting prompt for each session

> Read `CLAUDE.md`, `docs/zcode-cdp-integration-plan-2026-10-04.md` and
> `docs/verification/zcode-cdp/` (what earlier sessions proved). Do session **S<n>** only.
> Verify with a real run before saying done and save the evidence. Never read, store or
> decrypt ZCode tokens. Ask before relaunching ZCode. Format only files you touch; run the
> full unit suite, ruff and mypy before shipping; Python-only changes ship with
> `update-core.sh`; batch renderer/Rust changes into one full install and tell me before it
> resets permissions.

## 10. Decisions needed from the user before S1/S4

1. Plan terms (N8): is driving the app on the Start Plan allowed? (user checks)
2. Relaunch policy: ask every time, or remember "yes" for ZCode control mode?
3. Should ZCode control mode stay open while ZCode is open (simpler, more exposed) or open
   only per run (safer, relaunches the app each time)? Default proposed: per session, with
   an idle timeout of 10 minutes.
4. May Sani take over the ZCode window briefly during a run, or must it wait while the user
   is active in ZCode? (Default: wait.)

# S5, S6, S7 evidence (2026-10-04, ZCode 3.14.4)

## S5: account change and sync (R8)
Built `zcode_cdp/sync.py`. Signals: the credential file's key NAMES and modified time (values are never kept;
a test plants a secret in a fake credentials file and asserts it is absent from the database), the account label
in the open window (polled every 30 s while the port is open), and a changed set of plans after a read.
On a change Sani drops cached account, balances, models, tasks and the chat-to-task links of the old account,
records "Account changed: A -> B" (and sign-out, sign-in, plan added/ended) for the banner, writes an audit line,
and, with the user's standing yes and once the credentials file has been still for 20 s, re-reads ZCode on its own
(at most once every 5 minutes, never while a run uses the window).

| Check | Result |
|---|---|
| Simulated switch A -> B through the read job: account B shown, only B's plans, A's task links gone, banner A -> B | Pass (tests) |
| Sign-out: refused in plain words, caches cleared; sign-in again recovers and is reported | Pass (tests) |
| Plan ends / added: reported, account kept, balances re-read | Pass (tests) |
| A switch's follow-up plan change folds into the same banner | Pass (test) |
| Auto re-read only with control on, after the file is still, not twice in 5 minutes | Pass (tests) |
| No credential value stored or logged | Pass (test) |
| Mutation: removing the task-link cleanup fails two tests | Done |
| **A REAL account switch inside ZCode** | **Not done: only the user can do it.** Steps below |

To confirm for real: turn ZCode control on (Settings -> ZCode), sign out and in as another account (or the same one)
inside ZCode, and within about a minute the amber banner "Account changed: A -> B" (or "Signed out of ZCode") should
appear, balances should refresh, and the sidebar dot should follow.

## S6: model, reasoning, tasks, tokens per run (R3, R4, R6, R10)
- Sani can switch ZCode's model and reasoning level before sending, each confirmed on the page; a model ZCode does not
  offer, or one not on the Z.ai plan, refuses before anything is sent (`choice.py`). Source: `ZCODE_CLI_MODEL`, the plan/model
  picked in Settings, and `ZCODE_CLI_EFFORT` (low, high, max).
- Tokens used = ZCode's balance before minus after (Settings page reads inside the window); unknown if either read fails or the
  allowance went up. The fresh balance is kept after every run.
- Resume opens a collapsed project and presses "Show more" to find a hidden task before giving up.
- **Real run G** (`s4-runs/run_g_model_and_tokens.txt`): chose GLM-5.3 + low; ZCode's picker read back GLM-5.3 / Low / Ask before
  changes; `Tokens used: 51,300`, and the kept balance went 3,000,000 -> 2,948,700 (matches).

## S7: hardening and handover
- App update: an unverified ZCode version is read-only (reads work, runs refuse). Selectors only in `pages.py`, `driver.py`, `rows.py`.
- Kill switch: Settings -> ZCode control off closes the port at once; the core also closes it on exit.
- Diagnostics: the last run (time, model and plan, tokens, files from disk, checks, step list, redacted) is in Settings -> ZCode.
- Chat: round header carries the model and plan, then "Files changed" and each check as steps.
- Docs: `CLAUDE.md` section, plan status updated.
- **Final acceptance run** (`s4-runs/final_landing_page.txt`, result in `s7-landing-page/index.html`): the landing-page prompt through the
  same toolkit the Deep Agent calls: 69 s, model GLM-5.3 on the Start Plan, `Files changed: index.html` (from disk), 56,734 tokens used,
  steps streamed. The page has the hero with a call to action, three feature cards, two plans, three FAQ items, a footer, a viewport tag,
  and no external links.
- Refusals (control off, signed out, off-plan or unknown model, unverified version) spend nothing: tested; control-off also run live (S4).

## Installed
One full install carried: the "ZCode control" switch, the window-mode setting (`ZCODE_MODE`) and reasoning choice, the account-change
banner, the last-run view, and the sidebar dot rules for window mode. The install resets Accessibility and Screen Recording.

## Honest limits
- Commands ZCode judges safe run without a permission card; Sani stops the run afterwards when the limit forbids commands.
- Wrong-model refusal is unit-tested; this ZCode offers only Start Plan models, so it could not be forced live.
- Terms (N8): the user has not confirmed that Z.ai allows driving the app on their plan; window mode is therefore NOT the default.

## Fix after the user's first website test (2026-10-04, evening)
The user pasted a website prompt into the installed Sani. Three things went wrong, all in Sani's engine:
1. ZCode's model reached for `mkdir` to make the new folder; the run limit was "Edit files", so Sani denied the shell command
   and ZCode stopped ("You declined the folder creation"). Fixed two ways: a folder-only `mkdir [-p]` inside the project is now
   allowed at the edit limit (any pipe, substitution, redirect or chained command is not), and every task now starts with
   Sani's rules (work only inside the folder, no shell commands at the edit limit, file tools create folders themselves, ask no questions).
2. The Deep Agent retried with the new subfolder as `project_dir`; ZCode does not know it as a project, so the run was refused.
   A folder inside a ZCode project now maps to that project; the run is limited to the subfolder, relative paths mean relative to
   ZCode's folder, and the task tells ZCode to work in `<subfolder>/`.
3. After the failure the Deep Agent wrote the files itself. The guide now says to report a stop or refusal and not do ZCode's job.
Also seen: Safari opened but Sani could not look at the screen ("could not create image from display"): that is the permission
reset from the full install (Screen Recording / Accessibility must be granted again), not a bug.

Real re-runs at the "Edit files" limit (`s4-runs/run_web_a.txt`, `run_web_b.txt`, files in `s4-runs/brew-a-website/`):
- A: new folder + index.html, style.css, script.js in one run: 3 steps, all three written inside `brew-a/`, checked on disk, 113,705 tokens.
  Site check: no external links, four anchor links, a media query, "Most popular" present, a click handler for the FAQ.
- B: `project_dir` = a brand-new subfolder (create_folder): ran in ZCode's project, wrote `index.html` there, 51,864 tokens.

## Second fix round: repeated prompt, "files missing", and the chat not updating (2026-10-04, 17:20)
What the user saw in the installed Sani: the same website prompt was sent to ZCode several times (about 290k tokens), the Deep Agent said
the folder was empty although ZCode had built the site, and after switching pages or minimizing the chat showed only "running" and never
the final answer. Facts from the saved last-run record and the disk (`brew-site` really had index.html, script.js, style.css):
1. **Repeated prompt.** After writing the files, ZCode's model ran `ls` / `grep` to double-check (the prompt said "do not say it worked
   unless the files exist"). Sani's "ZCode used a command without asking" rule stopped the run for that harmless look, the Deep Agent was
   told to retry, and it retried again. Fix: look-only commands (ls, cat, head, tail, wc, grep, find without -exec/-delete, `cd <inside>`,
   joined by `&&` or `|`, touching only the project) are allowed at every run limit; anything else a shell can do is still refused.
2. **"Files missing".** The second run re-wrote identical files, which was reported as "unchanged on disk"; and the Deep Agent's own `ls`
   looks at a private virtual copy, not the user's disk. Fix: the check now says "wrote X again with the same content (the files exist and are
   fine)" or "does not exist on disk", and every run lists "Verified on disk now: file (size)". The Deep Agent guide says its own file tools
   cannot see the disk and to trust those lines.
3. **Chat not updating.** The window can miss live events when it is minimized or covered (ZCode comes to the front while it works). Fix:
   `reconcileChat()` re-syncs from what the host saved when the window gets focus, becomes visible, the chat page is opened again, and every
   3 s while a run is live: a finished run replaces a stale "running" turn with the saved answer; a run still going gets the steps it missed.
   Proven in the preview with a scenario that delivers no live events at all (live steps appear within 4 s, the final answer at the end).

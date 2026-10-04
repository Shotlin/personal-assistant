# S3 evidence: Settings UI + chat labels (2026-10-04)

## Built
- Core: `zcode.read` action starts one background read (`zcode_cdp/job.py`), needs `confirm: true`; the status
  payload carries `cdp` = contract, models, sessions, plus the job (`idle|reading|done|refused`, message,
  `port_closed`). A read takes a minute or two, longer than a host request may wait, so the button starts it and
  the window polls (every 3 s while reading).
- Rust: `zcode_auth_cmd("read")` -> `zcode.read` (confirm added by the host after the window's dialog), new
  `open_zcode_cmd` (`open -a ZCode`; replaces "Sign in", because the app's sign-in lives in the app).
- Renderer: Settings -> ZCode shows account + "read <time>", Read now (with a plain-words close/reopen dialog),
  Open ZCode, control status (window check, ZCode version, debug port), per-plan balances (left/total, percent,
  reset, expiry), model/reasoning/mode, tasks per project (count and list), shared run limit and folders (now one
  shared component used by Claude Code and ZCode), the port warning in plain words. Sign out removed: it only ever
  signed out the command-line key, not the app.
- Sidebar: a ZCode row with a dot. Green only when installed, signed in inside ZCode, window contract ok, switched on,
  and a folder is set; amber/red/busy say what to fix (`zcodeHealth`).
- Chat: a "<n> left" ring chip beside the mic, tokens left for the model ZCode is on, summed over plans that include it,
  with a tooltip giving the exact figures and when they were read.
- Preview fixtures: real-shaped data (`app.html?preview`, `&zcode=never` for the unread state).

## Verified
- Preview (browser pane): unread state shows amber "Not read yet"; pressing Read now shows the dialog, then the busy
  dot ("Reading ZCode…", button "Reading…"), then green "Connected" with the account, 3 plan/model balances, models,
  modes and "Tasks in ZCode · 6"; chip text "103.8M left". Console errors seen are from the overlay window needing Tauri
  in a plain browser, not from these pages.
- `npx tsc` clean, `npm run build` ok, `cargo test`: 141 passed (2 ignored, as before).
- Python: full unit suite passes; ruff clean for touched files. New tests: read job (needs a yes, one at a time, refusals
  and crashes do not leak or save), the settings action and status payload.
- Install: `release-mac.sh` then `install-app.sh` succeeded; stale `core-override` deleted first; the installed app
  started and its sidecar started (log: `~/Library/Logs/app.sani.local/sani.log`). Previous build backed up under
  `~/Library/Caches/sani-app-backups/`.

## Not verified by me
- Real screenshots of the installed app: my shell has no Screen Recording permission, so `screencapture` failed.
  The acceptance step is yours: open the installed Sani -> ZCode, press Read now, and confirm the figures match
  ZCode (S2 read: Trust Build GLM-5.3-Flash 100,000,000; Start Plan GLM-5.3 3,000,000 and Flash 5,000,000).
- The installed app's own database holds no ZCode read yet (S2's real read was saved to a scratch database on purpose).
- The install reset Accessibility and Screen Recording for Sani; they must be granted again.

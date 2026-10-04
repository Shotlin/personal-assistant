# S2 evidence: CDP core + read-only data (2026-10-04, ZCode 3.14.4)

Command (real ZCode, user consented to close/reopen):

```
SANI_DATA_DIR=<scratch> uv run python scripts/zcode_read.py --yes --save
```

Redacted output: `s2-read-output.json` (display name, task titles/ids and home path masked).

## What the real run proved

- Contract passed: 10/10 controls, version 3.14.4 (verified list).
- Account: signed-in display name read from the sidebar (no email exists in the UI).
- Models: Start Plan GLM-5.3-Flash (current) and GLM-5.3, with plan id `account:zai-start-plan`; modes
  (plan/build/edit/yolo) and reasoning (low/high/max) with the current one marked.
- Balances per plan and model, as displayed: Trust Build GLM-5.3-Flash 100,000,000/100,000,000;
  Start Plan GLM-5.3 3,000,000/3,000,000 and GLM-5.3-Flash 5,000,000/5,000,000, with expiry and reset time.
  The unsubscribed Z.ai Coding Plan is listed under `not_read` with ZCode's own wording.
- Sessions: 17 across 5 projects, `complete: true` (collapsed projects opened and closed again, every
  "Show more" clicked). The first two real runs honestly reported `complete: false` / 14 until clicks were
  scrolled into view; the flag did its job.
- Port closed afterwards: the old port does not answer and the running ZCode has no
  `--remote-debugging-port` argument. ZCode is open and normal.
- Saved to a scratch `sani.db`: `zcode_contract`, `zcode_account`, `zcode_balances`, `zcode_models`,
  `zcode_sessions`, each with `as_of`; `status_report()` now carries `cdp` (contract, models, sessions).

## Things found while building that the plan did not know

- A real mouse click is needed, and the target must be scrolled into view first.
- The debug port answers before the window exists, and the window exists before the app has drawn:
  both need waiting (page target, then contract).
- Opening a project in the sidebar also selects it as the new-task project (read side effect, documented).
- The Z.ai "Coding Plan" nav item and "Start Plan" nav item have test ids `preset:` and `coding-plan:`
  (reversed relative to their labels); the reader uses what each page shows, not the id.

## Tests and checks

- `tests/unit/test_zcode_cdp.py`: 39 tests (fake CDP socket, fake launcher/system, scripted page, real captured
  page text in `tests/fixtures/zcode/provider-panels.json`). A mutation check (removing the port-owner
  verification) makes one fail.
- Full unit suite passes. Ruff is clean for every file touched or added.
- `mypy src tests` reports 128 errors in 17 files both at HEAD and now; none are in files touched here.
  (The repo rule says it should be clean; it already was not.)
- Shipped with `sani/scripts/update-core.sh` (permissions untouched); quit and reopen Sani to load it.

## Not done in S2 (by design)

No UI (S3), no sending/streaming/cancel (S4), no account-change watcher (S5), no model/mode changes (S6).

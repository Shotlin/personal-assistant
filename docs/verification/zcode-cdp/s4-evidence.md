# S4 evidence: run engine (2026-10-04, ZCode 3.14.4, Start Plan, model GLM-5.3-Flash)

Real runs used `scripts/zcode_run.py` (the same `ClaudeCodeToolkit.run` the Deep Agent calls, in
`ZCODE_MODE=window`) against the user's `sani_test` project. Transcripts: `s4-runs/`.

## Also fixed this session: "unknown method" on Read now
The core's method allowlist (`core/app.py` `_ZCODE_ACTIONS`) did not include `zcode.read`, so the installed
app rejected the button. Added, plus a test that reads the Rust host's action map and asserts every method it can
send is accepted by the core. Shipped with `update-core.sh`.

## What was discovered on the live app (new facts)
- Tool rows end `success`, `pendingApproval` (a card is waiting) or `cancelled` (the card was denied). Bash output
  arrives as `output.text`. Phases: `running`, `completedSuccess`, `completedInterrupted` (after Stop).
- Stop is `[data-testid=v4-stop]`; Send is replaced by it while running.
- The permission card is `[role=listbox][aria-label="Permission required"]` with `button[role=option]`
  `Allow`, `Always allow in this project`, `Full access`, `Deny`, `Tell the model…`. Clicking an option answers at once.
- New task: `task-new-button`, then the project by its **name** in the `composer-workspace-trigger` menu.
- **ZCode does not ask permission for commands it judges safe** (it ran `python3 --version` in "Ask before changes"
  with no card). So Sani's card policy cannot stop every command. Sani handles it by stopping the run the moment a
  call finishes that never showed a card and is not allowed by the run limit, and says so (see run C2).
- Several tool calls can be pending at once, and the card does not say which one it is for.

## Requirement checks (R5-R7, R9, N4, N5)
| Check | Result | Evidence |
|---|---|---|
| Real prompt round trip, steps stream into the chat, file created matches disk, result names model + plan | Pass | `s4-runs/run1.txt`: steps Wrote s4-run-1.txt, `Model: GLM-5.3-Flash (Start Plan, your Z.ai plan)`, `Files changed: s4-run-1.txt` (from disk), session saved |
| Resume continues the same ZCode task | Pass | chat B: same `Session:` id, file got its second line, `Check: Continued the earlier ZCode task.` |
| Outside-folder write is denied, inside write allowed | Pass | `s4-runs/run_c2.txt`: `/tmp/s4-outside.txt` was **not** created, `s4-run-3.txt` was; `Check: Denied Write: Write outside the project folder.` |
| Command at an "edit" limit is stopped | Pass, with a limit | ZCode ran it unasked; Sani stopped the run and reported it. The command itself had already run. |
| Command at the "run" limit works, output captured, disk change flagged as command-made | Pass | `s4-runs/run_d.txt`: 6,109-character request, `Check: Changed on disk without a matching edit step (likely by a command): s4-run-4.txt.` |
| Long request path | Pass | 6,109 characters through the real window; 15k characters in the agent-loop test |
| Cancel | Pass | cancel after 50 s: ZCode's task phase read back `completedInterrupted`, no file written |
| Control off: refused, spends nothing | Pass | "Not started. ZCode control is off…"; ZCode process unchanged, no file |
| Wrong model refuses and spends nothing | **Unit test only** | this ZCode offers only Start Plan models, so there is no non-Z.ai model to switch to; the guard is tested with a custom-provider fake |
| User active in ZCode: wait, then refuse | Unit test only | not exercised live |
| Scripted-model end to end through a real LangGraph loop | Pass | `test_a_real_agent_loop_calls_the_zcode_tool_with_a_long_request` |

## Safety behaviour in code
- ZCode is always put in "Ask before changes"; Sani answers only `Allow` or `Deny`, never "Always allow" or "Full access".
- Allow needs: tool within the run limit, path inside the project (symlinks resolved), and for Bash the command not on the
  never-run list (sudo, rm -rf of / or ~, git push, piping into a shell, mkfs/dd, chmod -R 777 /).
- With several cards waiting, Sani allows only if **every** waiting call is allowed; otherwise it denies.
- One run at a time (global window lock); the debug port opens only with the user's standing yes, stays up 10 minutes
  idle, then ZCode is reopened normally. The core also closes it on exit.

## Not done / needs the next batch
- The installed app cannot use window mode yet: the host passes only `ZCODE_CLI_ENABLED`, and there is no toggle for
  "ZCode control". Both need a Rust + renderer change, so they go in the next full install (S6/S7 batch).
  Until then the mode and control are driven by `scripts/zcode_run.py` (`ZCODE_MODE=window`, `--enable-control`).
- The ZCode tasks these runs created remain in ZCode's own list (Sani cannot delete tasks); the files they made were removed.
- Token use per run (R10) and model/mode changes (S6) are not implemented.

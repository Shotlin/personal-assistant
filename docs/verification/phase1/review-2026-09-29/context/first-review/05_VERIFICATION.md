# Independent verification: exact scope, commands, results and limits

All testing used the existing local dependencies; no installation/provider/desktop/audio/account action. Current working-tree source, including new untracked mission and test files, was copied into `/private/tmp/jarvis-p1-independent-review`. Production data/.env/generated core build were excluded. Existing `.venv`, node_modules and bundled resource files were reused read-only. Cargo/mypy outputs and all test data/logs went to task-owned temporary paths. Relevant source hashes appear in `evidence/review-manifest.json`.

## Results

| Check | Actual result | Interpretation |
|---|---|---|
| Existing Python unit + 7 mission integration files + performance | 605 cases: **592 passed,13 failed** in43.451s | Four Unix socket permission failures; nine launcher tests failed because copied source has no Git metadata. No hidden conversion to PASS. |
| Launcher-only recheck in actual Git workspace | **10 passed** in0.136s | Resolves the nine copied-workspace Git errors; one case also passed earlier. Not a clean full-suite rerun. |
| Rust offline/locked | **112 passed,1 failed,2 ignored**, compilation succeeded | Remaining failure: sandbox Unix socket bind. Actual source test count113 matches earlier gate's count, but current environment cannot certify the socket test. |
| Renderer first copy build | TypeScript passed; Vite failed missing copied `app.html` | Review-copy omission, not product defect. |
| Renderer after copying all original root HTML entrypoints | **PASS**, Vite337ms | Build only, not mission component mounting or desktop acceptance. |
| Ruff src/tests/launcher | **44 errors** | Same count as original planning baseline; review did not fix old debt. Handoff135 is not this command's result. |
| Mypy src/tests | **119 errors in22 files**,169 checked | Original91 errors remain; exact normalized error comparison shows **28 additions,0 removals**:17 existing test-double protocol errors and11 new mission-test errors. |
| Independent probes RP01–RP12 | **12 contradictory behaviors reproduced** | Synthetic effects/model responses only; details below. These are audit reproducers, not passed acceptance tests. |
| Real desktop/microphone/TTS/provider/installed bundle | **NOT RUN** | No live authorization/actions; independently missing implementations recorded separately. |

Do not add rerun counts together and claim all605 passed. Four Python and one Rust socket-dependent checks remain environment-blocked in this review. The green historical files may correctly represent their environment, but do not cover the additional failing boundary probes.

## Commands

Python source-copy check, cwd `/private/tmp/jarvis-p1-independent-review`:

```sh
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=/private/tmp/jarvis-p1-independent-review/src:/private/tmp/jarvis-p1-independent-review PYTHONDONTWRITEBYTECODE=1 OPENROUTER_API_KEY=fixture-not-a-secret CUA_ENABLED=false CUA_CAPABILITY_MANIFEST_PATH=/private/tmp/jarvis-p1-independent-review/config/cua-capabilities.yaml SANI_DATA_DIR=/private/tmp/jarvis-p1-independent-review/data /Users/sayan/Documents/personal-assistant/.venv/bin/python -m pytest tests/unit tests/integration/test_mission_sqlite.py tests/integration/test_mission_policy.py tests/integration/test_mission_core.py tests/integration/test_mission_ipc.py tests/integration/test_mission_restart.py tests/integration/test_mission_desktop_control.py tests/integration/test_mission_observability.py tests/performance -p no:cacheprovider --junitxml=/private/tmp/jarvis-p1-independent-review/review-tests.xml -q
```

Launcher fixture recheck, cwd actual repository (the tests inspect Git identity; no live resources):

```sh
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONDONTWRITEBYTECODE=1 /Users/sayan/Documents/personal-assistant/.venv/bin/python -m pytest tests/unit/test_verify_phase1_launcher.py -p no:cacheprovider -q --junitxml=/private/tmp/jarvis-p1-independent-review/launcher-recheck.xml
```

Rust, renderer and static checks in copied source:

```sh
CARGO_TARGET_DIR=/private/tmp/jarvis-p1-review-cargo cargo test --offline --locked --manifest-path sani/src-tauri/Cargo.toml
npm --prefix sani run build
/Users/sayan/Documents/personal-assistant/.venv/bin/ruff check src tests scripts/verify_phase1.py
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=/private/tmp/jarvis-p1-independent-review/src /Users/sayan/Documents/personal-assistant/.venv/bin/mypy --cache-dir=/private/tmp/jarvis-p1-review-mypy src tests
```

Probes ran with the same cleared Python fixture environment, CUA disabled, placeholder key, source-copy PYTHONPATH and no bytecode writes:

```sh
/Users/sayan/Documents/personal-assistant/.venv/bin/python review_probes.py
/Users/sayan/Documents/personal-assistant/.venv/bin/python review_extra_probes.py
```

Scripts are retained under evidence/ for inspection and conversion to regression tests. They reference the review temp directory and local repository helper modules; they are not intended as the finished production acceptance harness. First probe draft lacked a required StepSpec budget field and was corrected only in the temporary review script; product code was not changed.

## Probe evidence

| Probe | Expected | Actual |
|---|---|---|
| RP01 production planning adapter | accept valid structured plan | ValueError: invalid plan submission |
| RP02 Controller role at real policy boundary | zero effects from PLAN raw mutation | synthetic click executed once |
| RP03 strict audit with no ledger | refuse before effect | synthetic click executed once |
| RP04 semantic tool refuses | failed/blocked, never confirmed | COMPLETED/CONFIRMED, zero checks |
| RP05 copied scope hash | reject broadened app scope | plan accepted |
| RP06 empty stale observed app | deny permit | permit issued |
| RP07 pause/resume | reconcile or make valid next work ready | mission/step RUNNING, old attempt CANCELLED, no claimable work |
| RP08 secret-shaped request | sanitize before mission persistence | raw synthetic canary retained |
| RP09 tampered evidence | reject hash mismatch | verifier passed changed file |
| RP10 all-skipped voice acceptance | BLOCKED | launcher status PASS for `ss` output |
| RP11 zero Deep budget | no provider invocation | PLAN invoked; durable usage empty |
| RP12 concurrent desktop step claims | ownership prevents simultaneous active dispatch | two claimed active steps, both empty lease fences |

RP02/03 exercised real apply_tool_policy over inert synthetic tools. RP04 used the executor's documented tool contract with an explicit error reply. RP05/06/07/08/09/11/12 used actual mission contracts/store/service/authority/verifier with temporary SQLite/files. RP10 executed only the inspected unconditional skip tests; it did not exercise an audio device or bypass a live operation permission.

## Evidence integrity and review limitations

The existing gate refs still point to accessible `/private/tmp/jarvis-p1-final/evidence/*.log`; copies of those original JSON summaries are retained alongside this review. Their fingerprint differs from current tracked diff and never includes untracked files. This review supplies hashes for source actually copied/tested and for all review artifacts. It does not claim an installed bundle was built, signed, launched or equal to the reviewed source.

Call-site searches establish unused integration paths within the current repository; no claim is made about an unrelated installed binary or future changes. Targeted probes demonstrate counterexamples, not exhaustive absence of other bugs. This is enough to reject Phase1 acceptance and direct a concrete corrective plan. Future repair must rerun the full original gates and protected negative cases against its final source/bundle.

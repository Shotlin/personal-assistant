# Single prompt — corrective completion of Jarvis Phase 1

Use this prompt only after the owner explicitly approves starting the Phase1 corrections. Merely receiving or reading the review package is not that approval. Do not start Phase2 or Phase3.

Repository: `Shotlin/personal-assistant`
Local workspace: `/Users/sayan/Documents/personal-assistant`
Reviewed HEAD: `58dac9c88018674c2e780086953902f1ea135308`, branch `main`.
The implementation is UNCOMMITTED. Reviewed tracked diff hash: `353f3d045e1f98bf9557381205b5e2bd3f05e2821f1942668949a3b9feaa83b7`. New files are also part of the implementation; do not rely on that tracked diff hash alone.

Read these local files automatically; do not ask the owner to upload or paste them:

1. `/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-2026-09-27/00_VERDICT.md`
2. The same directory's `01_FINDINGS.md`, `02_COVERAGE_MATRIX.md`, `03_PHASE_1_CORRECTIVE_PLAN.md`, `05_VERIFICATION.md`, and `evidence/review-probes.json`.
3. `/Users/sayan/Documents/personal-assistant/docs/astra/jarvis-next-2026-09-27-58dac9c/05_PHASE_1_IMPLEMENTATION_AGENT_PROMPT.md`, plus `03_PHASE_1_TARGET_ARCHITECTURE.md`, `04_PHASE_1_IMPLEMENTATION_PLAN.md`, and `06_PHASE_1_TEST_AND_ACCEPTANCE_PLAN.md` in that package. Read original owner documents in `inputs/` and current repository instructions.
4. Existing `/Users/sayan/Documents/personal-assistant/docs/verification/phase1/HANDOFF.md`, `VOICE_SELECTION.md`, `BASELINE.md`, and `gates/`. Treat claims as evidence to verify, not as permission or established success.

Your assignment, once approved, is to repair and finish the ENTIRE original Phase1 foundation, T01–T12 and gates G0–G7. Do not stop at a prototype, helper modules, a passing subset, or requesting live authorization while independent code integration is still missing. The independent review found 13 groups of issues and reproduced 12 boundary failures. Preserve useful implementation and fix the root causes.

Before editing: inspect HEAD/status/current instructions and compare source to the reviewed snapshot. Preserve all unrelated work and existing uncommitted implementation. Never reset, clean, discard, replace wholesale or assume HEAD contains the new files. Investigate material drift before dependent changes. Keep the original planning package and historical gate evidence intact; write a new remediation evidence directory.

Execute the corrective plan R01–R12 in dependency order with test-first, reviewable changes. At minimum:

- Repair the acceptance runner so all-skipped/missing cases cannot PASS, config actually reaches the isolated harness, suites resolve correctly and source manifests include tracked/untracked code plus actual bundle/assets.
- Compose one shared runtime/service/store/authority. Wire the existing Deep graph to valid structured PLAN/RECOVER/REVIEW submissions with invocation-local capabilities, mission/version context and actual dispatcher enforcement. Explicit Deep-selected actions must not bypass missions; information questions must not acquire desktop.
- Recompute scope hashes, validate actual authority subsets and fresh app/window/account/origin/fence observations; unknown identity cannot grant access. Guard scope-sensitive reads as well as mutations. Preserve existing policy denies and current driver mode.
- Commit actual per-action intent and reserve all relevant resources before dispatch/provider calls. Absent ledger is a blocker. Respect zero and aggregate budgets, retries, cost uncertainty and restart-safe elapsed time.
- Connect desktop queue/session/action lock, real fences and host-local stop latch. Preserve human takeover, held-input release/reconciliation and no post-stop admission even with blocked IPC/model.
- Fix Velo refusal/unknown outcome handling, per-selected-step tool catalog and exact payload plumbing. Require complete trusted postconditions and independent acceptance before any completion claim, while preserving zero-model exact-command routing and structured JEV.
- Complete pause/resume/cancel/revise/restart and bounded recovery/wait scheduling. Retain uncertain effects; never replay blindly or leave RUNNING work silently stranded.
- Connect mission IPC/event replay/controls and the real renderer. Replace the shipping ASK_USER→completed behavior for missions. Carry actual voice/text origin and revisions through existing finalization protections.
- Sanitize every mission/evidence/log/model/observer sink; verify hashes, scope, ownership, expiry and real artifact content. Connect the read-only Observer to actual sanitized events, with separate recommendations and retention. No experiments or self-modification.
- Finish host TTS supervision, bounded PCM playback, speech.stop, device/error handling and STT/PTT interlock. Then obtain any necessary asset access/terms and owner audition, pin one licensed local engine/assets/lock, and validate offline output. No hosted fallback or STT replacement.
- Fix the28 introduced mypy errors and any new errors from remediation; identify unchanged baseline debt separately.
- Run meaningful composed offline tests, then the authorized isolated real desktop/voice/performance/packaging/rollback gates. Complete the requirement matrix and handoff with exact source-bound evidence.

The twelve review probes must become regression tests through real composition boundaries. Model/driver doubles may replace external effects; they must not bypass the role/policy/store/scheduler/host code being tested. Keep graders protected. Use negative controls to prove that disabling a guard causes a failure. Do not modify an oracle just to make the implementation pass.

Preserve the Sani desktop architecture, working local voice input/manual Finish&Send, existing Deep Agent/provider selection, structured JEV + CUA, useful Velo recipes/fast route, current native permissions and user data. Do not introduce a replacement general agent framework, public server, blanket shell access or new selected API providers.

Authorization limits remain: no commit, push, merge, deploy, production account action, installed production app replacement, paid generation, provider switch, RSI experiments or Phase2/3 work without separate explicit permission. Once Phase1 repair is approved, ordinary local implementation and isolated tests are authorized; ask only for genuinely missing live scope/access or consequential blockers, and continue independent work meanwhile. Do not accept gated model terms on the owner's behalf.

Return: changed-file list/diff, exact commands/results, retained JUnit/logs, full source/bundle/asset/evidence manifests, T01–T12 and G0–G7 decisions, JAR/TC/RSI coverage, measured baseline/candidate metrics, known defects/blockers, data-preserving rollback and a filled original Phase1 handoff template. Declare completion only when all required Phase1 gates pass. If they do not, say PARTIAL/BLOCKED and identify the exact remaining implementation/evidence—not “complete except tests.”

Stop after reporting Phase1 results. Phase2 requires a fresh re-plan from the accepted repository and the owner's separate go-ahead. Do not start it automatically.

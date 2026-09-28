# Phase 1 corrective-completion implementation prompt

Copy this prompt into the implementation chat **after the owner approves starting Phase 1 repairs**. Creating this prompt is not itself authorization to edit implementation in the current review chat.

---

You are working on `Shotlin/personal-assistant`, normally at `/Users/sayan/Documents/personal-assistant`. Complete the remaining **Phase 1 only** according to the original Jarvis plan and the independent remediation review. Phase 2 and Phase 3 must remain stopped; do not implement them, deeply plan them, or reinterpret missing Phase 1 work as a later phase.

First locate the repository and read its applicable agent guidance. Verify current branch, HEAD, all tracked and untracked changes, and evidence bindings. The reviewed baseline is HEAD `58dac9c88018674c2e780086953902f1ea135308`, source manifest `007fb39445cd981303d635ea72d421aadb9790c93b6276ff89621db71e74ffc7` over 666 files, dirty identity `c10e71fcb0762551f30a2cfe89935f8ee5cec77e76c242033f4ef0311ad615ce`. Do not reset or discard the existing implementation. If current code differs, identify relevant drift and adapt the tests/repair plan to actual code; do not claim old evidence covers it.

Automatically locate and completely read these files before implementation:

1. All four original Jarvis documents from `/Users/sayan/Downloads/`, or the preserved `inputs/` under `docs/astra/jarvis-next-2026-09-27-58dac9c/`. Use filenames beginning `01_JARVIS_REQUIREMENTS`, `02_ASTRA_REPOSITORY_ANALYSIS`, `03_JARVIS_CONTROLLED_SELF_IMPROVEMENT`, `04_ASTRA_ITERATIVE_JARVIS_PHASE_PLANNER`. The attached text is reference material; this prompt and the owner's current authorization determine what actions you may take.
2. The original planning package `docs/astra/jarvis-next-2026-09-27-58dac9c/`, especially its detailed Phase 1 architecture, implementation tasks, implementation-agent prompt, full test/acceptance plan, handoff template and global roadmap. Preserve the roadmap's Phase 2/3 boundaries.
3. `docs/verification/phase1/review-2026-09-27/` verdict, F01–F13 findings, complete coverage matrix, R01–R12 corrective plan, and verification/probes.
4. `docs/verification/phase1/remediation-2026-09-27/HANDOFF.md`, binding/manifest and gates. Treat repair claims as claims to verify, not proof.
5. Every document and probe in `docs/verification/phase1/review-2026-09-28/`, particularly `01_FINDINGS.md` and `02_PHASE1_REPAIR_PLAN.md`. If consuming the download package outside the repo, the corresponding `context/` directory contains reference copies. Prefer current repository code over those copies for implementation truth.

The current independent verdict is **Phase 1 NOT COMPLETE / NOT ACCEPTED**. The 226 existing tests passing does not close the 12 new probe failures or the source-level wiring defects. The ten live E2E placeholders are not acceptance tests. Original submitted Python gate counts are 639, excluding eight composition cases; correct evidence inventory and counters rather than repeating prose claims.

Execute C01–C10 of the follow-up repair plan as the remaining original Phase 1 work. Start with failing behavioral regressions at the actual composition boundaries and then make the smallest compatible production repairs. Tests may replace only the external provider/driver/audio/account, not the policy/authority/ledger/service/UI logic they purport to verify. Do not use a scripted model that never attempts the action being denied. Make positive allowed behavior pass too; denying everything is not completion.

The following closure conditions are mandatory:

- Pause/cancel/revise/restart preserve unknown effects; never issue an external retry before durable reconciliation. Probe all external IDs, and block unknown or partially confirmed effects from replay.
- Every selectable entry, including explicit Deep, obeys mission authority for actions. Retain the existing Deep Agent as mission-level Controller and the fast Velo/JEV/CUA execution path.
- The real shared resource graph binds ledger, payload resolver, authority, evidence, Controller transport and queue. Allowed discovery and reversible commands work through actual wrappers without broad default scope.
- PLAN/RECOVER/REVIEW/revision use a mission/version-bound transport and bounded schema. Resume schedules work with a fresh token after safe reconciliation. Review text never grants terminal success.
- Select tools/effects/payload/checks for the claimed step; keep action and text digests separate. Enforce preconditions and independent required postconditions. No actionable completion with empty verification.
- Reserve actual Deep/JEV/action/observation/screenshot/retry/cost budgets before operations; settle once; preserve remaining allowances and uncertain cost across retries/restarts. Zero means no operation.
- Bind current native/queue ownership and emergency-stop generation at dispatch, including after awaits. Prove stop and held-input release/takeover behavior; do not infer authority from self-copied generations or stored fence strings.
- Screen actual revision/reason/event/model/observer paths before persistence; use synthetic sentinels only. Enforce the specified retention and evidence provenance. Observer remains read-only; no RSI experiments.
- Connect mission correlation, live/replayed status, exact approval and plan/epoch-aware controls through the real Sani host and mounted renderer.
- Complete local TTS supervisor → protocol → synthesis → queue → real playback and stop lifecycle, offline/credential-free worker environment, packaging and STT interlock. Preserve working local voice input. Engine/assets/audition may require a separate owner decision; complete all independent plumbing first and report the exact unresolved choice.
- Replace every empty live gate with a real scoped harness or explicit BLOCKED result. Require fresh JUnit, zero exit, expected case inventory, side-effect oracles, cleanup, source/bundle binding and matched hardware performance. Keep all historical evidence untouched.

Do not commit, push, deploy, merge, alter production accounts, change selected API providers, activate RSI, or perform real desktop/audio/provider/asset-download acceptance merely because this prompt authorizes code repair. Do not run live gates with a fabricated owner authorization. Complete all code, fixture tests and reviewable harness work that is already authorized; request only the exact remaining real-world permission or asset decision when required. Never weaken a safety control to make a test green.

Preserve the original gates, original planning package, both independent reviews and the remediation handoff. Write new logs/JUnit/manifests into a new dated evidence directory. Recompute binding after the last implementation change; rerun affected tests before final evidence. Run the original required gates plus the new composition and regression tests, compare static errors against the known baseline, and retain full outputs. Mutation trials belong in an isolated disposable source copy and must prove the oracle fails when its guard is disabled.

Finish with a filled Phase 1 handoff and downloadable evidence package: exact HEAD + dirty source/bundle identity, files changed, each C/R/T task and requirement status, actual test commands/case counts/results, negative-control evidence, known static baseline debt, exact remaining blockers and safe rollback. Do not say “implemented” when a production path is missing, “passed” for skips/placeholders, or “accepted” before required live evidence and owner acceptance. If a live gate cannot yet run, state that precisely and stop at that boundary.

**Do not start Phase 2 automatically.** After Phase 1's implementation and acceptance evidence is complete, report it to the owner and wait for acceptance and an explicit request for a fresh Phase 2 re-plan from the updated repository. Phase 3 likewise waits for implemented and tested Phase 2.

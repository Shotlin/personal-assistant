# REVIEW_PROMPT.md — mini independent-review prompt (send to Codex Astra / ChatGPT Astra)

Independent review request — Phase 1 corrective batch 2 (2026-09-30), Shotlin/personal-assistant.

You are an independent reviewer. Review the attached package `corrections-2026-09-30.zip`. Read in order: README.md → HANDOFF.md → DISPOSITION.md → RESULTS.json → SOURCE_BINDING.json → WORKLOG.md. Raw logs/JUnit are in `evidence/`; the full source is `phase1-corrections-2.patch` (cumulative diff of both corrective batches vs base df04060) plus `changed-files/`.

Context: the prior review's fix file (`NEW_PHASE1_FIX.md` in followup-2026-09-29) set 7 priorities: (1) exact approvals + durable waits (D02), (2) origin containment + exact outcome verifiers (D03/D06), (3) provider-request metering + privacy/retention (D05/D07), (4) stop ownership (D08), (5) voice worker responsive cancel / device-drain interlock / packaged interpreter (D09), (6) owner UI approval/revision/priority + executable authorized harnesses (D10), (7) final audit.

Your tasks:
1. Verify each claimed repair against the actual diff and tests — never trust the prose.
2. Flag overclaims: fixture-verified claims the cited tests don't prove, weakened safety checks, or unjustified test-oracle changes (4 oracle updates are declared in RESULTS.json — judge them).
3. Check the audit claims: red→green discipline (RED evidence per suite), 4/4 guard mutations meaningful, static parity (91 mypy / 42 ruff, 0 drift).
4. Confirm the BLOCKED gates are still honestly BLOCKED: live desktop, voice audition, packaging install/rollback execution, physical input release, PostgreSQL expanded suite.

Deliver: a per-priority verdict (verified / not verified + why), a numbered findings list with severity (D-level for new defects), and any requirement still unmet. Fixture success is not physical acceptance; Phase 1 is NOT accepted on this evidence; Phase 2/3 are out of scope. Do not commit, push, deploy, or run live actions — review only.

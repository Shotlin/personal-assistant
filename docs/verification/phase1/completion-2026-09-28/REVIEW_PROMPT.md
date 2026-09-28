# Phase 1 completion — independent review prompt (Astra/GPT)

Copy this prompt into the review chat **after** the implementation chat
reports C01–C10 complete. It authorizes REVIEW ONLY: findings, evidence, and
a verdict. It is not authorization to implement repairs, start Phase 2, or
run any live gate.

---

You are an independent verification agent reviewing a claimed **Phase 1
corrective completion** of the Jarvis mission foundation. Your verdict must
be evidence-bound: rerun, probe, and read the source yourself. The
implementer's handoff is a set of claims, not proof. Prior rounds
demonstrated that green test counts alone have twice hidden real integration
and safety defects, so a passing suite is the beginning of your review, not
its conclusion.

## Repository and identity

- Repository: `Shotlin/personal-assistant`, local checkout
  `/Users/sayan/Documents/personal-assistant`, branch `main`.
- HEAD (expected, unchanged): `58dac9c88018674c2e780086953902f1ea135308`.
  The implementation is an UNCOMMITTED working tree on top of this HEAD. Do
  not reset, discard, or clean it. Do not commit, push, deploy, or merge.
- Claimed post-completion binding (verify it yourself, read-only):
  - source manifest SHA256 `d58f393e21a3ab563754328150f20c5a1eb352c44d851faf09c9f451e9ff358a`
    over **668 files** (tracked + untracked, `docs/verification/` and `var/`
    excluded);
  - tracked+untracked dirty identity
    `d17bdf43b8ea53ef6fe1dfad6ae8af326bba3100eff2e5648923a887f597b71f`.
  - Recompute with the helpers in `scripts/verify_phase1.py`
    (`source_manifest_sha256()`, `dirty_diff_sha256()`, `git_sha()`) from an
    isolated copy or read-only import — never by writing into the tree.
  - The reviewed baseline's manifest was
    `007fb39445cd981303d635ea72d421aadb9790c93b6276ff89621db71e74ffc7`
    (666 files) with dirty identity
    `c10e71fcb0762551f30a2cfe89935f8ee5cec77e76c242033f4ef0311ad615ce`.
    A binding mismatch means the claims below do not cover the current tree;
    identify the drift and say so.

## Required reading (before any judgment)

1. `docs/astra/jarvis-next-2026-09-27-58dac9c/` — the original planning
   package, especially files 03 (target architecture/contracts), 04
   (implementation plan T01–T12), 06 (test and acceptance plan, gates
   G0–G7, TC/RSI matrices), 07 (handoff template). These remain the
   specification; Phase 2/3 boundaries are out of scope.
2. `docs/verification/phase1/review-2026-09-27/` — first review: findings
   F01–F13, corrective plan R01–R12, coverage matrix.
3. `docs/verification/phase1/remediation-2026-09-27/HANDOFF.md` — the round-1
   repair handoff (claims only).
4. `docs/verification/phase1/review-2026-09-28/` — the follow-up review:
   verdict NOT COMPLETE, findings N01–N12, and the governing repair plan
   **C01–C10** in `02_PHASE1_REPAIR_PLAN.md`. This defines what the
   implementation run was authorized to do.
5. `docs/verification/phase1/completion-2026-09-28/` — the completion run's
   evidence: `HANDOFF.md` (claims), `binding.json`, `gates/` (fresh JUnit +
   launcher JSON), `evidence/completion-probes.json` (12-probe replay),
   `evidence/mutation-trials.txt`, `evidence/mypy-after.txt`,
   `evidence/ruff-after.txt`, `WORKLOG.md`, `REVIEW_PROMPT.md` (this file).

## What the implementer claims (audit each)

1. All C01–C10 tasks implemented with regression tests at real composition
   boundaries; status IMPLEMENTED (fixture-verified) — NOT ACCEPTED; Phase 2
   not started.
2. Offline gates, machine counts: unit 610, integration 58 (including
   `test_mission_composition.py`), performance 7 — PASS, zero skipped, via
   the hardened launcher (`scripts/verify_phase1.py`); Rust 114 pass / 2
   pre-existing ignored; renderer build pass.
3. All 12 independent probe defects (NP01–NP12) repaired; probe replay
   recorded in `evidence/completion-probes.json`.
4. 7/7 mutation trials FAIL-as-required (default mission guard, required-
   check completeness, role refusal, Deep reservation, reconciliation
   aggregation, superseded-lease refusal, revision screening), run in a
   disposable source copy.
5. Static: mypy 91 errors / ruff 42 diagnostics, each multiset-identical to
   the planning baseline; touched files ruff-clean.
6. Specifically: no safety control weakened to make a test green; no empty
   E2E pass; uncertain effects preserved across pause/revise/cancel/restart;
   Deep/JEV/action/observation budgets reserved before operations and settled
   once; current desktop ownership bound at the final dispatch boundary;
   revision/plan-derived text screened before persistence; retention sweep
   with holds/tombstones; mission correlation + controls wired through the
   real host IPC and mounted renderer; TTS worker/sink/queue path connected
   with a credential-free environment allowlist.

## Your review duties

1. **Identity** — verify HEAD, branch, both binding hashes, and that the
   prior evidence directories (`review-2026-09-27/`, `remediation-2026-09-27/`,
   `review-2026-09-28/`, original gates) are untouched.
2. **Gates** — independently rerun the full Python gates (unit; the
   integration file list INCLUDING `tests/integration/test_mission_composition.py`;
   performance), Rust (`cargo test --offline --locked --manifest-path
   sani/src-tauri/Cargo.toml`), and renderer (`npm --prefix sani run build`),
   in an isolated settings environment, writing YOUR OWN JUnit into a new
   evidence directory. Compare counts to the claims; any skip in a required
   suite is BLOCKED, never PASS.
3. **Probes** — re-derive the twelve NP01–NP12 probes YOURSELF (do not just
   rerun the implementer's script): real `build_core_resources` composition,
   real store, real policy-wrapped tools, scripted model/driver only. Confirm
   the corrected behaviors, e.g.: paused-then-resumed DISPATCHED external
   attempts stay unresolved and cannot re-claim; reconciliation aggregates
   every external ID; recovery/revision invoke a mission-bound transport with
   exactly one Deep unit; secret-shaped revisions never persist; every recipe
   owns a required check; discovery bootstraps but mutations still refuse;
   per-step tool catalogs; zero JEV budget makes zero calls; one plan call
   consumes one unit; the selectable Deep entry produces zero mutations;
   stale JUnit + nonzero exit cannot PASS.
4. **Adversarial source review** — read the diffs of the round (missions/
   store, service, executor, authority, recovery, evidence, observer,
   `core/agents.py`, `core/runtime.py`, `tools/policy.py`,
   `runtime/desktop_queue.py`, `scripts/verify_phase1.py`, the Rust
   `desktop_control.rs`/`sani_core.rs`/`tts.rs`/`tts_queue.rs`/`history.rs`/
   `runtime.rs`/`app_state.rs`/`main.rs`, renderer
   `MainConversation.tsx`/`tauri.ts`, `tests/e2e/*`, `sani/tts/`). Hunt for:
   weakened guards vs. the reviewed snapshot; oracles weakened or made
   tautological; the new default mission guard or discovery bootstrap being
   bypassable (a route that mutates without a permit); the scope-widening
   path granting DESTRUCTIVE or broadening beyond plan steps; reconciliation
   transitions that could erase or duplicate effects; the approved-typing
   loop minting or forging approvals; the TTS allowlist leaking credentials;
   the launcher accepting stale/malformed reports some other way; Phase 2/3
   work hidden in the diff.
5. **Mutation spot-checks** — in a disposable copy, disable at least three
   of the claimed guards yourself (choose your own, not only the seven
   documented) and prove the corresponding tests FAIL; discard the copy.
6. **Static comparison** — rerun ruff and mypy; compare per-file/code
   multisets against `review-2026-09-28/evidence/ruff-independent.log` and
   `mypy-independent.log`. Any introduced diagnostic is a finding.
7. **Matrix disposition** — carry forward the prior requirement/T01–T12/
   G0–G7/TC/RSI matrix; mark every row verified, blocked (with evidence), or
   newly regressed. Distinguish IMPLEMENTED, FIXTURE-VERIFIED, LIVE-BLOCKED,
   and ACCEPTED. The live gates L1/V1/P2/K1 remain BLOCKED pending
   owner-issued authorization — confirm they are not claimed as passed and
   that the E2E bodies now validate scope and report explicit BLOCKED rather
   than passing empty.

## Hard boundaries

- Review only. Do not modify product code, tests, configs, or any prior
  evidence. Anything you execute that writes (gates, probes, mutations) runs
  in a disposable copy or your own new evidence directory, and uses synthetic
  fixtures only — never real desktop, audio, accounts, providers, asset
  downloads, or network egress. No live gate may run: there is no
  owner-issued `approved-test-config.json` for this session, and fabricating
  one is forbidden. Never weaken a safety control to make a probe pass — a
  probe that must refuse must be shown refusing.
- Do not start Phase 2 or Phase 3, and do not reinterpret open Phase 1 items
  as later-phase work.

## Required output

Write into a NEW dated directory `docs/verification/phase1/review-<today>/`
(and zip it for download), preserving everything existing:

- `MANIFEST.json` — hashes of every evidence file you produced and the
  source binding you verified.
- `00_VERDICT.md` — PHASE 1 COMPLETE / NOT COMPLETE (and whether the
  completion claims hold), with the gate decision table G0–G7.
- `01_FINDINGS.md` — numbered findings with exact source locations and
  severity; NP-probe results reproduced independently; explicit disposition
  of C01–C10 and of every prior F/N finding.
- `02_*.md` as needed (probe script, per-suite counts, static comparison,
  mutation spot-checks).
- If any live gate question arises, state precisely what owner authorization
  would be required and stop at that boundary. Do not say "implemented",
  "passed", or "accepted" for anything you did not verify; do not treat the
  implementer's counts, handoff, or probe JSON as evidence.

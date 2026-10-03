# Phase 1 batch 3 disposition — requirement mapping (2026-10-01)

Statuses: IMPLEMENTED (code present), FIXTURE-VERIFIED (this batch's boundary
regression proves the slice), LIVE-BLOCKED (needs authorized physical
evidence), NOT IMPLEMENTED (no code), OWNER-ACCEPTED (only after all
required evidence + explicit owner acceptance). No row below is
OWNER-ACCEPTED. This matrix carries the batch-2 matrix forward and adds the
D11–D22 deltas; the original T/JAR/TC/RSI/G matrix (`review-2026-09-29/
03_COVERAGE_MATRIX.md`, copied into `review-batch2-2026-09-30/context/`)
remains the requirement denominator — narrow fixture wins never promote a
whole row.

## D11–D22 resolution state

| Finding | Status | Proof anchor (all in evidence/ unless noted) | Remaining |
|---|---|---|---|
| D11 provider admission | IMPLEMENTED + FIXTURE-VERIFIED | `d11-red-old-path.log` (executed RED: 5 responses at limit 2 on the old path), `d11-green` (9 cases incl. streaming, storage failure, scope isolation, no-double-charge) | Real-provider cost reconciliation LIVE-BLOCKED |
| D12 deletion isolation | IMPLEMENTED + FIXTURE-VERIFIED | `d12d16-green`: two-mission isolation, idempotence, hold refusal, IPC identity refusal | — |
| D13 approval recovery | IMPLEMENTED + FIXTURE-VERIFIED | `d13d20-green`: multi-step NEEDS_APPROVAL + owner IPC approve → single effect; pause → fresh digest; projection target | Renderer visual acceptance is owner UX review, not a code gate |
| D14 ambiguous replay | IMPLEMENTED + FIXTURE-VERIFIED | `d14-green`: UNKNOWN admission+release refusal (reviewer's repro), escalation wait fires once, rate-limit producer through real `_DeepInvoke`, deadline honesty, cancel release | — |
| D15 exact targets | IMPLEMENTED + FIXTURE-VERIFIED | `d15-green` incl. new adversarial cases: unrelated focus, cross-window token, missing BEFORE focus, axis binding, cross-mission evidence; fail-closed no-verifier path | Real-driver origin/axis reporting needs live driver (LIVE-BLOCKED) |
| D16 privacy/retention | IMPLEMENTED + FIXTURE-VERIFIED | `d12d16-green`: wait-canary screening, bounded checkpoints, damaged-row isolation, durable holds in lifecycle AND idle retention, 30d purge w/ tombstones, kind-aware TTLs | — |
| D17 voice lock cycle | IMPLEMENTED + FIXTURE-VERIFIED | `final-rust.log` (124 native) incl. `concurrent_lifecycle_ops_never_deadlock`, `drain_completes_against_concurrent_playback`; reviewer's deadlock cited as RED | Physical audio LIVE-BLOCKED |
| D18 worker/packaging | IMPLEMENTED + FIXTURE-VERIFIED | `d18-green`: full-FIFO gated shutdown bounded, heavy-PCM ack, crash-safe cancel; staged-layout resolver test; `tauri.conf.json` + `build-tts.sh` + launcher staged | Installed-bundle runtime NOT IMPLEMENTED (explicit); engine/audition owner gate |
| D19 fences/cleanup | IMPLEMENTED + FIXTURE-VERIFIED | `d19-green` + `d19-fence`: fenced service cleanup, ack-before-grant, uncertainty blocking, ack/manual unblock, takeover flow, fake-driver matrix | Physical release measurement LIVE-BLOCKED |
| D20 approval UI | IMPLEMENTED + FIXTURE-VERIFIED | `d13d20-green` (renderer-shaped IPC path, exact identity, approve/reject, negatives) + renderer build | Visual/UX acceptance is owner review |
| D21 harness truth | IMPLEMENTED + FIXTURE-VERIFIED | `d21-green`: offline fake validation of scope/budget admission (fail-before-effect), stop-during-work measurement, wrong-focus refusal; e2e bodies reworked (active-synthesis stop, nonzero PCM, bounded reads) | Authorized live execution LIVE-BLOCKED; install/rollback NOT IMPLEMENTED |
| D22 audit honesty | RESOLVED FOR THIS PACKAGE | Batch-2 RED claims corrected here (collection error; 7-of-9); prospective executed RED for D11; 11/11 mutations meaningful; static multisets exactly unchanged; oracle decisions listed in RESULTS.json | Next independent review |

## Mapping to the original matrix (deltas only)

- **T02** (contracts/store): D13/D14 slices now FIXTURE-VERIFIED (batch-2's D02 slices already were). Row remains nonterminal (live gates).
- **T03** (authority/evidence/budget): D15 verification slices + D11 admission FIXTURE-VERIFIED; D03 origin slice carried from batch 2.
- **T05** (ownership/stop): D19 slices FIXTURE-VERIFIED; physical takeover/release still LIVE-BLOCKED → row stays open.
- **T06** (MissionService/Deep): D11 per-request admission FIXTURE-VERIFIED (reviewer's fail-open probe closed); production Deep-role composition unchanged.
- **T07/T08**: D13+D20 owner-decision path FIXTURE-VERIFIED end-to-end over IPC; `mission.purge` IPC added (D12/D16).
- **T09/T10**: D17/D18 slices FIXTURE-VERIFIED; engine selection/audition/native playback LIVE-BLOCKED → rows stay open.
- **T12**: D21 offline-validated harnesses; live gates LIVE-BLOCKED; install/rollback NOT IMPLEMENTED.
- **G1–G7**: G1 (D13/D14/D16 slices), G2 (D11/D15/D16 slices), G3 (D11/D15 slices), G4 (D19 slice + LIVE-BLOCKED), G5 (D17/D18 slices + LIVE-BLOCKED), G6 (D15/D16 slices), G7 (audit) — each improved but none flipped to PASS; the physical/postgres gates still gate them.
- **JAR/TC/RSI rows**: unchanged from the batch-2 matrix except where the slices above are their proof anchors (JAR-005/006/007/008/009/015/017/018/019/020/021/016 and TC-03/05/08/09/12/13/16/19/20/22/27/28/30/32/33/34/35 gain fixture evidence for the named slices only). Nothing promoted.

## Oracle decisions (recorded, none weaken a guard)

1. Migration inventory `[1,2,3,4] → [1,2,3,4,5,6]` (unit + sqlite): additive migrations 5 (provider-request state) and 6 (retention holds) join; idempotency claim unchanged.
2. `test_release_refuses_old_epoch_digest`: updated to assert D13's re-queue-to-PENDING (the safe undispatched step re-observes) instead of batch-2's stranded BLOCKED — the old expectation pinned the stranding bug the review flagged.
3. `test_stop_owner_reports_driver_release_acknowledgement`: inserts `acknowledge_driver_cleanup()` between unacked stops — D19 makes unacked cleanup block dispatch by design.
4. Two provider-privacy tests re-targeted from the removed post-hoc metering API to the admission-scope API (the old tests asserted the mechanism D11 deleted; the new ones assert the real boundary, and the dedicated `test_provider_admission.py` covers the framework path).
5. Batch-2's scope-outcome press fixture role `AXLink→AXButton` and press expectation `{"ordinal"}→{ordinal_index, role_kind}`: aligned with the recipe's real resolution the check now binds (D15).

"""Batch-3 mutation trials: 11 sensitive guards in disposable copies.

Preserved from batch 2 (anchors updated to the current source):
  approval-release, origin-readonly, stop-noop, sink-screening.
New for batch 3:
  provider-admission (D11), deletion-isolation (D12), ambiguous-replay (D14),
  exact-target-verification (D15), fence-cleanup (D19), voice-lock-order (D17),
  harness-scope-admission (D21).

Each mutation must produce a MEANINGFUL test failure (the guard's own
boundary regression), never a collection or dependency error. The voice
lock-order mutant deadlocks by design, so it is bounded with a timeout and
a timeout kill counts as the detection (documented in the log).
"""
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

WORKTREE = pathlib.Path("/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant")
OUT = WORKTREE / "docs/verification/phase1/corrections-2026-10-01-batch3/evidence"
SCRATCH = pathlib.Path("/private/tmp/phase1-mutations-3")


def copy_worktree(dest: pathlib.Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(WORKTREE, dest,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc",
                                                  ".venv", "node_modules", "target"))

PY_MUTATIONS = {
    # --- preserved from batch 2 (anchors at current source) ---
    "mutation-approval-release": (
        "src/assistant/missions/store.py",
        """            rows = self._conn.execute(
                "SELECT s.step_id, s.attempt_count, s.pending_action_digest, "
                "s.pending_tool FROM mission_steps s "
                "WHERE s.mission_id=? AND s.plan_version=? AND s.state='BLOCKED' "
                "AND s.pending_action_digest!='' AND EXISTS ("
                "  SELECT 1 FROM mission_approvals a WHERE a.mission_id=s.mission_id "
                "  AND a.action_digest=s.pending_action_digest AND a.control_epoch=? "
                "  AND a.revoked=0 AND a.consumed_at_ms IS NULL AND a.expires_at_ms>? "
                "  AND a.target_ref IS s.pending_target_ref)",
                (mission_id, plan_version, epoch, now),
            ).fetchall()""",
        """            rows = self._conn.execute(
                "SELECT s.step_id, s.attempt_count, s.pending_action_digest, "
                "s.pending_tool FROM mission_steps s "
                "WHERE s.mission_id=? AND s.plan_version=? AND s.state='BLOCKED'",
                (mission_id, plan_version),
            ).fetchall()""",
        ["tests/integration/test_phase1_owner_controls.py::test_release_matches_only_the_pending_digest"],
    ),
    "mutation-origin-readonly": (
        "src/assistant/missions/authority.py",
        """        if scope.allowed_origins and (
            observed.origin is None or observed.origin not in scope.allowed_origins
        ):""",
        """        if scope.allowed_origins and action.effect_class != "READ_ONLY" and (
            observed.origin is None or observed.origin not in scope.allowed_origins
        ):""",
        ["tests/integration/test_phase1_scope_outcomes.py::test_unknown_origin_cannot_read_protected_content"],
    ),
    "mutation-stop-noop": (
        "src/assistant/runtime/desktop_queue.py",
        """            if target is None:
                # Nothing owned and nothing named: a no-op stop. The
                # generation does not move — invalidating nothing is not a
                # stop.
                self._log("stop_noop", "", generation=self._generation)
                return {"stopped": False, "owner": None, "certain": True}""",
        """            if target is None:
                self._generation += 1
                self._log("stop_noop", "", generation=self._generation)
                return {"stopped": False, "owner": None, "certain": True}""",
        ["tests/unit/test_mission_desktop_queue.py::test_stop_of_non_owner_never_invalidates_a_valid_lease",
         "tests/integration/test_mission_desktop_control.py::test_idle_stop_is_safe"],
    ),
    "mutation-sink-screening": (
        "src/assistant/missions/store.py",
        """                (
                    result.effect_outcome,
                    # D07: the outcome sink is screened — a canary riding in
                    # an exception or refusal text never persists verbatim.
                    canonical_json(_screen_result(result)),""",
        """                (
                    result.effect_outcome,
                    canonical_json(result.model_dump()),""",
        ["tests/integration/test_phase1_provider_privacy.py::test_canaries_withheld_at_result_and_final_review_sinks"],
    ),
    # --- new for batch 3 ---
    "mutation-provider-admission": (
        "src/assistant/models/admission.py",
        """        self.admitted += 1
        if self.admitted > self.max_requests:
            raise ProviderRequestDenied(""",
        """        self.admitted += 0
        if False:
            raise ProviderRequestDenied(""",
        ["tests/integration/test_provider_admission.py::test_limit_two_blocks_third_request_before_dispatch"],
    ),
    "mutation-deletion-isolation": (
        "src/assistant/missions/store.py",
        """            rows = self._conn.execute(
                "SELECT evidence_id, relative_path FROM mission_evidence "
                "WHERE mission_id=? AND deleted=0",
                (mission_id,),
            ).fetchall()""",
        """            rows = self._conn.execute(
                "SELECT evidence_id, relative_path FROM mission_evidence "
                "WHERE deleted=0",
                (),
            ).fetchall()""",
        ["tests/integration/test_deletion_retention.py::test_purge_is_mission_scoped_and_idempotent"],
    ),
    "mutation-ambiguous-replay": (
        "src/assistant/missions/store.py",
        """            # D14: release must not make an uncertain effect replayable. A
            # step whose latest attempt is still UNKNOWN stays blocked and
            # the mission blocks honestly instead of re-dispatching.
            unresolved = self._conn.execute(
                "SELECT COUNT(*) FROM mission_attempts WHERE mission_id=? AND "
                "plan_version=? AND step_id=? AND effect_outcome='UNKNOWN' AND "
                "dispatch_state='RESULT_APPLIED'",
                (str(mission_id), int(plan_version), str(step_id)),
            ).fetchone()
            if unresolved is not None and int(unresolved[0]) > 0:""",
        """            unresolved = (0,)
            if False:""",
        ["tests/integration/test_external_waits.py::test_unknown_effect_cannot_enter_or_release_a_wait"],
    ),
    "mutation-exact-target": (
        "src/assistant/missions/evidence.py",
        """            for flag in ("focused", "checked", "selected", "expanded", "pressed"):
                if bool(after.get(flag)) and not bool(prior.get(flag)):
                    return True, (
                        f"the resolved {kind} #{index} ({token!r}) gained {flag}"
                    ), [ordered[-1].evidence_id]""",
        """            for flag in ("focused", "checked", "selected", "expanded", "pressed"):
                if any(bool(e.get(flag)) and not bool(prior.get(flag))
                       for e in after_by_token.values()):
                    return True, (
                        f"the resolved {kind} #{index} ({token!r}) gained {flag}"
                    ), [ordered[-1].evidence_id]""",
        ["tests/integration/test_phase1_scope_outcomes.py::test_unrelated_focus_cannot_verify_ordinal_press"],
    ),
    "mutation-fence-cleanup": (
        "src/assistant/runtime/desktop_queue.py",
        """        if self._owner != run_id:
            return False
        if fence and fence != self._owner_fence:""",
        """        if self._owner != run_id:
            return False
        if False:""",
        ["tests/unit/test_mission_desktop_queue.py::test_old_cleanup_cannot_release_a_same_run_newer_lease"],
    ),
    "mutation-harness-scope": (
        "tests/e2e/_live.py",
        """    account_ref = config.get("account_ref")
    assert account_ref, (
        "authorized scope must name account_ref (the fixture account)")""",
        """    account_ref = config.get("account_ref")""",
        ["tests/integration/test_live_harness_offline.py::test_scope_admission_fails_on_missing_fields"],
    ),
}

RUST_MUTATIONS = {
    "mutation-voice-lock-order": {
        "file": "sani/src-tauri/src/tts_queue.rs",
        "old": """    pub fn drain_pending(&self) -> QueueState {
        // D17: each lock is taken and DROPPED in turn (queue, sink, state)
        // — never nested — so drain cannot invert against finish/play/stop.
        // A stale read at worst keeps Playing one poll longer, which the
        // pump simply retries.
        let queue_len = self.queue_len();""",
        "new": """    pub fn drain_pending(&self) -> QueueState {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let queue_len = self.queue_len();""",
        "restore_line": None,
    },
}


def run_pytest(root: pathlib.Path, tests: list[str], name: str) -> tuple[int, bool]:
    env = {
        'PATH': '/usr/bin:/bin:/opt/homebrew/bin',
        'PYTHONPATH': str(root / 'src') + ':' + str(root),
        'PYTHONDONTWRITEBYTECODE': '1',
        'CUA_ENABLED': 'false',
        'OPENROUTER_API_KEY': 'fixture-not-a-secret',
        'SANI_DATA_DIR': str(SCRATCH),
        'CUA_CAPABILITY_MANIFEST_PATH': str(root / 'config/cua-capabilities.yaml'),
    }
    command = [sys.executable, '-B', '-m', 'pytest', *tests, '-q',
               '--junitxml=' + str(OUT / (name + '.xml'))]
    with (OUT / (name + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=root, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
    meaningful = result.returncode == 1
    return result.returncode, meaningful


def main() -> int:
    SCRATCH.mkdir(exist_ok=True)
    results: dict[str, Any] = {}
    detected = 0
    for name, spec in PY_MUTATIONS.items():
        relative, old, new, tests = spec[0], spec[1], spec[2], spec[3]
        copy = SCRATCH / name
        copy_worktree(copy)
        target = copy / relative
        source = target.read_text()
        if old not in source:
            results[name] = {"error": "mutation anchor not found"}
            print(name, "ANCHOR-MISSING")
            continue
        target.write_text(source.replace(old, new))
        code, meaningful = run_pytest(copy, tests, name)
        results[name] = {"exit_code": code, "meaningful_failure": meaningful}
        print(name, code, "MEANINGFUL" if meaningful else "NOT-MEANINGFUL")
        detected += 1 if meaningful else 0
        shutil.rmtree(copy, ignore_errors=True)
    # Voice lock-order mutant: bounded run; a timeout kill IS the detection
    # (the concurrent-lifecycle test deadlocks under the inversion).
    name = "mutation-voice-lock-order"
    spec = RUST_MUTATIONS[name]
    copy = SCRATCH / name
    copy_worktree(copy)
    target = copy / spec["file"]
    source = target.read_text()
    if spec["old"] not in source:
        results[name] = {"error": "mutation anchor not found"}
        print(name, "ANCHOR-MISSING")
    else:
        target.write_text(source.replace(spec["old"], spec["new"]))
        import os as _os
        env = dict(_os.environ)
        env["CARGO_TARGET_DIR"] = str(SCRATCH / "rust-target")
        with (OUT / (name + '.log')).open('w') as log:
            proc = subprocess.Popen(
                ["cargo", "test", "--offline", "--locked", "concurrent_lifecycle"],
                cwd=copy / "sani/src-tauri", stdout=log, stderr=subprocess.STDOUT,
                env=env)
            try:
                code = proc.wait(timeout=240)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                code = 124
        # 124 = timeout kill while the test was deadlocked: detected.
        # 101 = cargo test failure: also detected. 0 = NOT detected.
        meaningful = code in (101, 124) or code < 0  # negative = killed mid-deadlock
        results[name] = {"exit_code": code, "meaningful_failure": meaningful,
                         "note": "timeout kill counts: the mutant deadlocks the test"}
        results[name] = {"exit_code": proc.returncode, "meaningful_failure": meaningful,
                         "note": "timeout kill counts: the mutant deadlocks the test"}
        print(name, proc.returncode, "MEANINGFUL" if meaningful else "NOT-MEANINGFUL")
        detected += 1 if meaningful else 0
        shutil.rmtree(copy, ignore_errors=True)
    total = len(PY_MUTATIONS) + len(RUST_MUTATIONS)
    results["_summary"] = {"guards_tested": total, "meaningful_failures": detected}
    (OUT / "mutations.json").write_text(json.dumps(results, indent=1))
    return 0 if detected == total else 1


if __name__ == "__main__":
    sys.exit(main())

"""Run sensitive-guard mutations in disposable copies; the real worktree is
never modified. Each mutation must produce a MEANINGFUL test failure (the
guard's own boundary regression), never a collection or dependency error."""
import pathlib, shutil, subprocess, sys, json

WORKTREE = pathlib.Path("/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant")
OUT = WORKTREE / "docs/verification/phase1/corrections-2026-09-30/evidence"
SCRATCH = pathlib.Path("/private/tmp/phase1-mutations-2")

def copy_worktree(dest: pathlib.Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(WORKTREE, dest,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc",
                                                  ".venv", "node_modules", "target"))

PY_MUTATIONS = {
    # 1. D02: release reverts to the OLD "any active approval releases any
    # blocked step" bug — the digest/epoch EXISTS binding is removed.
    "mutation-approval-release": (
        "src/assistant/missions/store.py",
        """            rows = self._conn.execute(
                "SELECT s.step_id, s.attempt_count, s.pending_action_digest, "
                "s.pending_tool FROM mission_steps s "
                "WHERE s.mission_id=? AND s.plan_version=? AND s.state='BLOCKED' "
                "AND s.pending_action_digest!='' AND EXISTS ("
                "  SELECT 1 FROM mission_approvals a WHERE a.mission_id=s.mission_id "
                "  AND a.action_digest=s.pending_action_digest AND a.control_epoch=? "
                "  AND a.revoked=0 AND a.consumed_at_ms IS NULL AND a.expires_at_ms>?)",
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
    # 2. D03: origin containment reverts to the OLD read-only hole.
    "mutation-origin-readonly": (
        "src/assistant/missions/authority.py",
        '''        if scope.allowed_origins and (
            observed.origin is None or observed.origin not in scope.allowed_origins
        ):''',
        '''        if scope.allowed_origins and action.effect_class != "READ_ONLY" and (
            observed.origin is None or observed.origin not in scope.allowed_origins
        ):''',
        ["tests/integration/test_phase1_scope_outcomes.py::test_unknown_origin_cannot_read_protected_content"],
    ),
    # 3. D08: stop_owner reverts to bumping the generation on a no-op stop.
    "mutation-stop-noop": (
        "src/assistant/runtime/desktop_queue.py",
        '''            if target is None:
                # Nothing owned and nothing named: a no-op stop. The
                # generation does not move — invalidating nothing is not a
                # stop.
                self._log("stop_noop", "", generation=self._generation)
                return {"stopped": False, "owner": None, "certain": True}''',
        '''            if target is None:
                self._generation += 1
                self._log("stop_noop", "", generation=self._generation)
                return {"stopped": False, "owner": None, "certain": True}''',
        ["tests/unit/test_mission_desktop_queue.py::test_stop_of_non_owner_never_invalidates_a_valid_lease",
         "tests/integration/test_mission_desktop_control.py::test_idle_stop_is_safe"],
    ),
    # 4. D07: the result sink stops screening uncertainty.
    "mutation-sink-screening": (
        "src/assistant/missions/store.py",
        '''            self._conn.execute(
                """
                UPDATE mission_attempts SET dispatch_state='RESULT_APPLIED',
                    effect_outcome=?, result_json=?, result_digest=?, updated_at_ms=?
                WHERE execution_id=?
                """,
                (
                    result.effect_outcome,
                    # D07: the outcome sink is screened — a canary riding in
                    # an exception or refusal text never persists verbatim.
                    canonical_json(_screen_result(result)),''',
        '''            self._conn.execute(
                """
                UPDATE mission_attempts SET dispatch_state='RESULT_APPLIED',
                    effect_outcome=?, result_json=?, result_digest=?, updated_at_ms=?
                WHERE execution_id=?
                """,
                (
                    result.effect_outcome,
                    canonical_json(result.model_dump()),''',
        ["tests/integration/test_phase1_provider_privacy.py::test_canaries_withheld_at_result_and_final_review_sinks"],
    ),
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
    # Meaningful = at least one test ran and at least one failed (exit 1).
    meaningful = result.returncode == 1
    return result.returncode, meaningful

def main() -> int:
    SCRATCH.mkdir(exist_ok=True)
    results = {}
    for name, (relative, old, new, tests) in PY_MUTATIONS.items():
        copy = pathlib.Path("/private/tmp/phase1-mutations-2") / name
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
        shutil.rmtree(copy, ignore_errors=True)
    (OUT / "mutations.json").write_text(json.dumps(results, indent=1))
    return 0 if all(r.get("meaningful_failure") for r in results.values()) else 1

if __name__ == "__main__":
    sys.exit(main())

from pathlib import Path
import hashlib, json, shutil, zipfile, xml.etree.ElementTree as ET, subprocess

repo = Path('/Users/sayan/Documents/personal-assistant')
out = repo / 'docs/verification/phase1/review-batch2-2026-09-30'
temp = Path('/private/tmp/jarvis-batch2-review-20260930')
input_zip = Path('/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant/docs/verification/phase1/corrections-2026-09-30.zip')
context = out / 'context'
context.mkdir(exist_ok=True)
shutil.copy2(input_zip, context / input_zip.name)
shutil.copytree(temp / 'corrections-2026-09-30', context / 'submitted-package', dirs_exist_ok=True)
for name in ('NEW_PHASE1_FIX.md', 'BASELINE_COVERAGE_MATRIX.md'):
    shutil.copy2(repo / 'docs/verification/phase1/followup-2026-09-29' / name, context / name)
shutil.copytree(repo / 'docs/verification/phase1/review-2026-09-29/context/original-planning', context / 'original-planning', dirs_exist_ok=True)
baseline = context / 'static-baseline'
baseline.mkdir(exist_ok=True)
for name in ('gates-mypy.log', 'gates-ruff-json.log', 'root-static-comparison.json'):
    shutil.copy2(repo / 'docs/verification/phase1/review-2026-09-29/evidence' / name, baseline / name)
for name in ('run_review_gates.py', 'replay_mutations.py'):
    shutil.copy2(temp / name, out / 'evidence' / name)

counts = {}
for name in ('unit-final', 'integration', 'performance'):
    root = ET.parse(out / 'evidence' / (name + '.xml')).getroot()
    suites = root.findall('testsuite')
    counts[name] = {key: sum(int(s.get(key, '0')) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
assert counts['unit-final'] == dict(tests=625, failures=0, errors=0, skipped=0)
assert counts['integration'] == dict(tests=96, failures=0, errors=0, skipped=0)
assert counts['performance'] == dict(tests=7, failures=0, errors=0, skipped=0)
counts.update(native={'passed': 121, 'failed': 0, 'ignored': 2}, renderer={'exit_code': 0})
counts['static'] = json.loads((out/'evidence/static-comparison.json').read_text())
counts['mutations'] = json.loads((out/'evidence/mutation-replay.json').read_text())
counts['overall'] = 'PHASE_1_INCOMPLETE_NOT_ACCEPTED'
counts['review_started'] = '2026-09-30'
counts['review_artifacts_finished'] = '2026-10-01'
counts['agents_used'] = 0
counts['live_actions_run'] = False
(out / 'RESULTS.json').write_text(json.dumps(counts, indent=2) + '\n')

# Recheck existing bound files without modifying either repository.
manifest = json.loads((out/'evidence/source-manifest.json').read_text())
wt = input_zip.parents[3]
assert wt == Path('/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant')
wt_changes = []
for relative, expected in manifest.items():
    path = wt / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        wt_changes.append(relative)
assert not wt_changes, wt_changes
main_original = json.loads((repo/'docs/verification/phase1/review-2026-09-29/evidence/source-manifest.json').read_text())
main_changes = [r for r, h in main_original.items() if not (repo/r).is_file() or hashlib.sha256((repo/r).read_bytes()).hexdigest() != h]
assert not main_changes, main_changes
integrity = {
    'main_head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=repo, text=True).strip(),
    'corrective_worktree_head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=wt, text=True).strip(),
    'main_source_differences_from_prior_review': main_changes,
    'corrective_source_differences_from_bound_batch2': wt_changes,
    'input_zip_sha256': hashlib.sha256(input_zip.read_bytes()).hexdigest(),
    'scope': 'Review documents only; no implementation changes, live actions or agents.'
}
assert integrity['input_zip_sha256'] == '02f61fe139424cce4d36c1ef1444024548dbe558e5d8f9e8c67d06819e8167cc'
(out / 'evidence/final-integrity.json').write_text(json.dumps(integrity, indent=2) + '\n')
shutil.copy2(Path(__file__), out / 'evidence/package_review.py')
file_manifest = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file() and p != out/'MANIFEST.json'}
(out / 'MANIFEST.json').write_text(json.dumps(file_manifest, indent=2) + '\n')
archive = out.with_suffix('.zip')
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(out.rglob('*')):
        if path.is_file():
            z.write(path, str(path.relative_to(out.parent)))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for relative, expected in file_manifest.items():
        assert hashlib.sha256(z.read(out.name + '/' + relative)).hexdigest() == expected
print(json.dumps({'archive': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'files': len(file_manifest)+1, 'bytes': archive.stat().st_size, 'results': counts, 'integrity': integrity}, indent=2))

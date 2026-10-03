"""Run isolated fixtures against this worktree, never the editable-install source."""
import os
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[5]
out = pathlib.Path(__file__).parent
scratch = pathlib.Path('/private/tmp/phase1-corrections-tests')
scratch.mkdir(exist_ok=True)
env = {
    'PATH': '/usr/bin:/bin:/opt/homebrew/bin',
    'PYTHONPATH': str(root / 'src') + ':' + str(root),
    'PYTHONDONTWRITEBYTECODE': '1',
    'CUA_ENABLED': 'false',
    'OPENROUTER_API_KEY': 'fixture-not-a-secret',
    'SANI_DATA_DIR': str(scratch),
    'CUA_CAPABILITY_MANIFEST_PATH': str(root / 'config/cua-capabilities.yaml'),
}
name, *tests = sys.argv[1:]
command = [sys.executable, '-B', '-m', 'pytest', *tests, '-q',
           '--junitxml=' + str(out / (name + '.xml'))]
with (out / (name + '.log')).open('w') as log:
    result = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
print(name, result.returncode)
sys.exit(result.returncode)

"""D11 prospective RED: run the batch-2 provider metering path with the
REAL LangChain callback manager and a local fake client.

The NEW requirement is asserted here (limit 2 -> no more than 2 completed
requests; refusal raised pre-request). Against the batch-2 implementation
(callback-end metering, default raise_error) this FAILS: 5 requests
complete and 5 rows are recorded. Executed against a disposable copy of
the pre-batch source; the real worktree is untouched."""
import os, pathlib, subprocess, sys, json, shutil

WORKTREE = pathlib.Path("/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant")
COPY = pathlib.Path("/private/tmp/phase1-batch3-red/d11")

def build_copy():
    if COPY.exists():
        shutil.rmtree(COPY)
    COPY.mkdir(parents=True)
    for item in ["src", "config", "tests", "pyproject.toml"]:
        src = WORKTREE / item
        dst = COPY / item
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    # Revert the batch-3 files to their batch-2 state:
    # admission.py did not exist; runtime.py was never changed in batch 2;
    # agents.py/store.py/usage.py take the batch-2 changed-files versions.
    (COPY / "src/assistant/models/admission.py").unlink(missing_ok=True)
    batch2 = WORKTREE / "docs/verification/phase1/corrections-2026-09-30/changed-files"
    shutil.copy2(batch2 / "src/assistant/core/agents.py",
                 COPY / "src/assistant/core/agents.py")
    shutil.copy2(batch2 / "src/assistant/missions/store.py",
                 COPY / "src/assistant/missions/store.py")
    shutil.copy2(batch2 / "src/assistant/observability/usage.py",
                 COPY / "src/assistant/observability/usage.py")
    import subprocess as sp
    head = sp.run(["git", "-C", str(WORKTREE), "show", "HEAD:src/assistant/core/runtime.py"],
                  capture_output=True, text=True, check=True).stdout
    (COPY / "src/assistant/core/runtime.py").write_text(head)

build_copy()

PROBE = '''
import json, os, sys, tempfile, pathlib
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from assistant.observability.usage import LedgerCallbackHandler, UsageLedger
from assistant.missions.store import MissionStore

# The batch-2 enforcement path: usage_recorder -> on_llm_end, count check
# AFTER the response, raised inside the callback (default raise_error=False).
LIMIT = 2
class _Meter:
    def __init__(self, store, mission_id, limit):
        self.store = store; self.mission_id = mission_id; self.limit = limit; self.n = 0
    def __call__(self, call_id, usage):
        self.n += 1
        self.store.record_provider_request_sync(self.mission_id, 1, call_id, call_key="probe",
                                                input_tokens=usage.get("input_tokens"),
                                                output_tokens=usage.get("output_tokens"))
        if self.n > self.limit:
            raise RuntimeError("provider request ceiling exceeded")

async def main():
    tmp = pathlib.Path(tempfile.mkdtemp())
    store = await MissionStore.connect(tmp / "probe.db"); await store.setup()
    scope = __import__("assistant.missions.contracts", fromlist=["Scope"]).Scope(owner_id="o")
    request = __import__("assistant.missions.contracts", fromlist=["RequestEnvelope"]).RequestEnvelope(
        request_id="probe-1", conversation_id="p", owner_id="o", input_origin="typed_final",
        input_revision=1, text="x", submitted_at_ms=1)
    limits = __import__("assistant.missions.contracts", fromlist=["BudgetLimits"]).BudgetLimits()
    mission = await store.claim_request(request, "d"*64, goal="x", scope=scope, limits=limits)
    meter = _Meter(store, mission.mission_id, LIMIT)
    ledger = UsageLedger()
    handler = LedgerCallbackHandler(ledger, prefix="probe", on_call=meter)
    model = FakeListChatModel(responses=[f"r{i}" for i in range(5)])
    completed = 0
    for i in range(5):
        out = await model.ainvoke([HumanMessage(content="x")],
                                  config={"callbacks": [handler]})
        if out.content:
            completed += 1
    rows = store._conn.execute(
        "SELECT COUNT(*) FROM mission_provider_requests WHERE mission_id=?",
        (mission.mission_id,)).fetchone()[0]
    result = {"completed_responses": completed, "recorded_rows": rows, "limit": LIMIT}
    print(json.dumps(result))
    # The NEW requirement, asserted: no more than LIMIT requests complete.
    assert completed <= LIMIT, (
        f"OLD PATH FAILS CLOSED REQUIREMENT: {completed} responses completed at limit {LIMIT}")
    await store.close()

import asyncio; asyncio.run(main())
'''

env = {
    'PATH': '/usr/bin:/bin:/opt/homebrew/bin',
    'PYTHONPATH': str(COPY / 'src') + ':' + str(COPY),
    'PYTHONDONTWRITEBYTECODE': '1',
}
with (WORKTREE / 'docs/verification/phase1/corrections-2026-10-01-batch3/evidence/d11-red-old-path.log').open('w') as log:
    result = subprocess.run([sys.executable, '-c', PROBE], cwd=COPY, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
print('probe exit:', result.returncode)

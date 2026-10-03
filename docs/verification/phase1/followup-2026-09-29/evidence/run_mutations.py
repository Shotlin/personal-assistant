import os,pathlib,subprocess,shutil,tempfile,json,sys
root=pathlib.Path('/Users/sayan/.codex/worktrees/phase1-corrections/personal-assistant')
out=root/'docs/verification/phase1/corrections-2026-09-29/evidence'
trials=[('stale-ledger','src/assistant/missions/store.py', 'if row[1] != row[3] or row[2] != row[4] or row[5] != "RUNNING":', 'if False:', 'test_invalidated_intent_cannot_append_dispatch_ledger'),('packet-budget','src/assistant/missions/store.py','if int(spent) + charge.amount > packet_ceiling:', 'if False:', 'test_packet_budget_counts_more_than_zero'),('final-stop','src/assistant/tools/policy.py','final_check = mission_final_dispatch_check.get()', 'final_check = None', 'test_stop_during_ledger_await_prevents_actual_effect')]
results=[]
for name,rel,old,new,test in trials:
    tmp=pathlib.Path(tempfile.mkdtemp(prefix='phase1-mutation-',dir='/private/tmp'))
    for folder in ('src','tests','config','scripts'):
        shutil.copytree(root/folder,tmp/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(root/'pyproject.toml',tmp/'pyproject.toml')
    p=tmp/rel;s=p.read_text();assert s.count(old)==1;s=s.replace(old,new);p.write_text(s)
    if name=='stale-ledger':
        # The row-state check and epoch check independently reject obsolete intent.
        s=p.read_text().replace('if row is None or str(row[0]) not in {"INTENT_COMMITTED", "DISPATCHED"}:','if row is None:');p.write_text(s)
    env={'PATH':'/usr/bin:/bin:/opt/homebrew/bin','PYTHONPATH':str(tmp/'src')+':'+str(tmp),'PYTHONDONTWRITEBYTECODE':'1','CUA_ENABLED':'false','OPENROUTER_API_KEY':'fixture-not-a-secret','SANI_DATA_DIR':str(tmp/'data'),'CUA_CAPABILITY_MANIFEST_PATH':str(tmp/'config/cua-capabilities.yaml')}
    command=[sys.executable,'-B','-m','pytest','-q','tests/integration/test_phase1_corrective_boundaries.py::'+test,'--junitxml='+str(out/('mutation-'+name+'.xml'))]
    with (out/('mutation-'+name+'.log')).open('w') as log: result=subprocess.run(command,cwd=tmp,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append({'name':name,'path':rel,'replacements': [{'old':old,'new':new}], 'test':test,'exit_code':result.returncode,'disposable_copy':str(tmp)})
(out/'mutations.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))

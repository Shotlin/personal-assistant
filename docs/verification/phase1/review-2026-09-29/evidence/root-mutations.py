import pathlib,subprocess,json,os
root=pathlib.Path('/private/tmp/jarvis-review29');out=pathlib.Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-2026-09-29/evidence');py='/Users/sayan/Documents/personal-assistant/.venv/bin/python'
env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(root/'src')+':'+str(root),'PYTHONDONTWRITEBYTECODE':'1','CUA_ENABLED':'false','OPENROUTER_API_KEY':'fixture-not-a-secret','SANI_DATA_DIR':str(root/'data'),'CUA_CAPABILITY_MANIFEST_PATH':str(root/'config/cua-capabilities.yaml')}
trials=[('surface','src/assistant/missions/authority.py','        self._check_surface(item, scope, action, observed)','        pass # review-only mutation','tests/unit/test_mission_authority.py::test_scope_sensitive_reads_are_guarded'),('budget','src/assistant/missions/store.py','if ceiling is not None and usage.total(charge.resource) + charge.amount > ceiling:','if False: # review-only mutation','tests/unit/test_mission_store.py::test_budget_reservation_idempotent_and_capped'),('voice','sani/src-tauri/python/sani_tts.py','if len(text) > TEXT_MAX_CHARS:','if False: # review-only mutation','tests/unit/test_sani_tts_worker.py')]
records=[]
for name,path,before,after,test in trials:
 p=root/path;original=p.read_text();assert original.count(before)==1
 def run(suffix):
  with (out/f'mutation-{name}-{suffix}.log').open('w') as f:return subprocess.run([py,'-m','pytest',test,'-q'],cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT).returncode
 baseline=run('baseline')
 try:p.write_text(original.replace(before,after));mutant=run('mutant')
 finally:p.write_text(original)
 records.append({'name':name,'path':path,'before':before,'after':after,'test':test,'baseline_exit':baseline,'mutant_exit':mutant,'restored':p.read_text()==original})
(out/'root-mutations.json').write_text(json.dumps(records,indent=2));print(json.dumps(records))
with (out/'root-probes.log').open('w') as f:print('probes',subprocess.run([py,str(out/'root-probes.py')],cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT).returncode)
with (out/'root-queue-recheck.json').open('w') as f:print('queue',subprocess.run([py,str(out/'gates-adversarial-queue.py')],cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT).returncode)

from pathlib import Path
import os,subprocess,sys
root=Path('/private/tmp/jarvis-batch3-review-20261001/source')
out=Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-batch3-2026-10-01/evidence')
env={'PATH':'/usr/bin:/bin:/opt/homebrew/bin','PYTHONPATH':str(root/'src')+':'+str(root),'PYTHONDONTWRITEBYTECODE':'1','CUA_ENABLED':'false','OPENROUTER_API_KEY':'fixture-not-a-secret','SANI_DATA_DIR':'/private/tmp/jarvis-batch3-review-20261001/data','CUA_CAPABILITY_MANIFEST_PATH':str(root/'config/cua-capabilities.yaml')}
unit=['tests/unit'];integration=['tests/integration/'+n+'.py' for n in ['test_mission_sqlite','test_mission_policy','test_mission_core','test_mission_ipc','test_mission_restart','test_mission_desktop_control','test_mission_observability','test_mission_composition','test_phase1_corrective_boundaries','test_phase1_owner_controls','test_phase1_scope_outcomes','test_phase1_provider_privacy','test_provider_admission','test_deletion_retention','test_approval_flows','test_external_waits','test_live_harness_offline']]
for name,tests in [('unit',unit),('integration',integration),('performance',['tests/performance'])]:
 with (out/(name+'.log')).open('w') as f:r=subprocess.run([sys.executable,'-B','-m','pytest',*tests,'--junitxml='+str(out/(name+'.xml'))],cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT)
 print(name,r.returncode,flush=True)

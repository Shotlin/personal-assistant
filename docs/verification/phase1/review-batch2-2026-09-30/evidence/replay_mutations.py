from pathlib import Path
import importlib.util,shutil,subprocess,sys,json,xml.etree.ElementTree as ET
base=Path('/private/tmp/jarvis-batch2-review-20260930');src=base/'source';pkg=base/'corrections-2026-09-30';out=Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-batch2-2026-09-30/evidence')
spec=importlib.util.spec_from_file_location('declared_mutations',pkg/'evidence/run_mutations.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
results={}
for name,(relative,old,new,tests) in mod.PY_MUTATIONS.items():
 target=base/('independent-'+name);target.mkdir(exist_ok=True)
 for folder in ('src','tests','config','scripts'):
  shutil.copytree(src/folder,target/folder,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
 shutil.copy2(src/'pyproject.toml',target/'pyproject.toml')
 p=target/relative;text=p.read_text();assert text.count(old)==1;p.write_text(text.replace(old,new))
 env={'PATH':'/usr/bin:/bin:/opt/homebrew/bin','PYTHONPATH':str(target/'src')+':'+str(target),'PYTHONDONTWRITEBYTECODE':'1','CUA_ENABLED':'false','OPENROUTER_API_KEY':'fixture-not-a-secret','SANI_DATA_DIR':str(target/'data'),'CUA_CAPABILITY_MANIFEST_PATH':str(target/'config/cua-capabilities.yaml')}
 with (out/(name+'.log')).open('w') as log:r=subprocess.run([sys.executable,'-B','-m','pytest',*tests,'--junitxml='+str(out/(name+'.xml'))],cwd=target,env=env,stdout=log,stderr=subprocess.STDOUT)
 xml=ET.parse(out/(name+'.xml')).getroot();suites=xml.findall('testsuite');results[name]={'exit_code':r.returncode,**{k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}}
 print(name,results[name],flush=True)
(out/'mutation-replay.json').write_text(json.dumps(results,indent=2)+'\n')

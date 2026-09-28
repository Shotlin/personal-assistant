import asyncio,json,importlib.util,tempfile,time
from pathlib import Path
from types import SimpleNamespace
from assistant.missions.contracts import *
from assistant.missions.store import MissionStore
from assistant.missions.service import MissionService
from assistant.missions.controller import DeepController
from assistant.missions.authority import MissionAuthority
from assistant.missions.evidence import EvidenceStore
OUT=Path('/private/tmp/jarvis-p1-independent-review')
async def main():
 results=json.loads((OUT/'review-probes.json').read_text())
 spec=importlib.util.spec_from_file_location('review_launcher','/Users/sayan/Documents/personal-assistant/scripts/verify_phase1.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 # Known source contains only unconditional skips; this executes no desktop/audio action.
 record=m.run_suite('skipped-voice-probe',['.venv/bin/python','-m','pytest','tests/e2e/test_sani_voice.py','-q','-p','no:cacheprovider'],environment_kind='V',evidence_dir=OUT)
 results.append({'id':'RP10','expected':'All-skipped acceptance must be BLOCKED, never PASS','actual':record})
 tmp=Path(tempfile.mkdtemp(prefix='jarvis-p1-more-probes-'));s=await MissionStore.connect(tmp/'d.sqlite');await s.setup()
 scope=Scope(owner_id='owner',allowed_apps=['com.fixture.app'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL'})
 req=RequestEnvelope(request_id=new_id(),conversation_id='review',owner_id='owner',input_origin='typed_final',input_revision=1,text='Prepare the fixture workflow',submitted_at_ms=int(time.time()*1000))
 mission=await s.claim_request(req,'digest',goal=req.text,scope=scope,limits=BudgetLimits(max_deep_calls=0))
 steps=[StepSpec(step_id='s'+str(i),ordinal=i,objective='fixture',recipe_id='semantic_ui',scope=scope,budget=BudgetLimits(),effect_class='REPEATABLE_LOCAL') for i in [1,2]]
 svc=MissionService(SimpleNamespace(),store=s,authority=MissionAuthority(s),evidence=EvidenceStore(tmp/'e',s),executor=None,controller=DeepController(None))
 calls=[]
 async def invoke(*args):calls.append(args[0]);return {'plan':{'steps':[v.model_dump() for v in steps],'success_criteria':[]}}
 await svc._plan(req,mission,invoke=invoke)
 results.append({'id':'RP11','expected':'Zero Deep-call budget forbids invocation at provider boundary','actual':{'calls':calls,'persisted_deep_usage':(await s.get_mission(mission.mission_id)).budget_usage.model_dump()}})
 mission=await s.commit_plan(mission.mission_id,0,steps,[])
 first=await s.claim_step(mission.mission_id,1,1,tool_ids=['click'])
 second=await s.claim_step(mission.mission_id,1,1,tool_ids=['click'])
 results.append({'id':'RP12','expected':'One active desktop step per mission/owner before prior attempt settles','actual':{'first_step':first.step_id,'second_step_while_first_active':second.step_id if second else None,'fence1':first.lease_fence,'fence2':second.lease_fence if second else None}})
 await s.close();(OUT/'review-probes.json').write_text(json.dumps(results,indent=2,default=str));print(json.dumps(results[-3:],indent=2,default=str))
asyncio.run(main())

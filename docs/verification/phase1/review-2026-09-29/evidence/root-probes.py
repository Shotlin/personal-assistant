import asyncio,json,tempfile,time,importlib.util,sys,io
from pathlib import Path
from types import SimpleNamespace
from assistant.missions.contracts import *
from assistant.missions.store import MissionStore,MissionActionLedger
from assistant.missions.authority import MissionAuthority
from assistant.missions.service import MissionService,_fast_checks_for
from assistant.missions.controller import DeepController
from assistant.missions.evidence import EvidenceStore
from assistant.missions.submission import SUBMISSION_ROLES
from assistant.missions.recovery import Reconciler
from tests.unit.test_mission_executor import _RecordingJev,_executor
from tests.unit.velo_fakes import standard_tools
from assistant.velo.contracts import JevDecision,DecisionStatus
OUT=Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-2026-09-29/evidence')
results={}
def req():return RequestEnvelope(request_id=new_id(),conversation_id='review',owner_id='owner',input_origin='typed_final',input_revision=1,text='fixture',submitted_at_ms=int(time.time()*1000))
async def main():
 root=Path(tempfile.mkdtemp(prefix='independent29-'));s=await MissionStore.connect(root/'db');await s.setup(); scope=Scope(owner_id='owner',allowed_apps=['com.example.safari'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL','EXTERNAL_WRITE'})
 async def mission(budget=None):
  m=await s.claim_request(req(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits())
  m=await s.commit_plan(m.mission_id,0,[StepSpec(step_id='s',ordinal=1,objective='fixture',recipe_id='semantic_ui',scope=scope,budget=budget or BudgetLimits(),effect_class='EXTERNAL_WRITE')],[])
  i=await s.claim_step(m.mission_id,1,1,tool_ids=['click','list_apps']);return m,i
 async def control(m,kind,epoch):return await s.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=epoch,kind=kind))
 m,i=await mission();ledger=MissionActionLedger(s,execution_id=i.execution_id,mission_id=m.mission_id)
 lid=await ledger.plan(tool_name='click',args_digest='synthetic');await ledger.observe(lid,'CONFIRMED')
 before=(await s.get_attempt(i.execution_id))['dispatch_state'];await control(m,'PAUSE',1);r=await control(m,'RESUME',2);second=await s.claim_step(m.mission_id,1,r.control_epoch,tool_ids=['click'])
 results['actual_ledger_pause']={'state_after_real_ledger':before,'old_state':(await s.get_attempt(i.execution_id))['dispatch_state'],'second_attempt':second.attempt if second else None,'note':'synthetic action ledger; no physical effect'}
 m,i=await mission();await s.mark_dispatched(i.execution_id);await control(m,'PAUSE',1);r=await control(m,'RESUME',2);second=await s.claim_step(m.mission_id,1,r.control_epoch,tool_ids=['click']);results['NP02_manually_marked']={'second_attempt':second.attempt if second else None,'state':(await s.get_attempt(i.execution_id))['dispatch_state']}
 m,i=await mission();await s.recover_inflight('review');s._conn.execute('UPDATE mission_attempts SET external_ids=? WHERE execution_id=?',('first,second',i.execution_id));calls=[]
 async def probe(op):calls.append(op);return 'NO_EFFECT' if op=='first' else 'CONFIRMED'
 r=await Reconciler(s,probe=probe).reconcile(i.execution_id);results['NP03_mixed']={'calls':calls,'result':str(r)}
 svc=MissionService(SimpleNamespace(sani_data_dir=str(root)),store=s,authority=MissionAuthority(s),evidence=EvidenceStore(root/'e',s),executor=None,controller=DeepController(None));scheduled=[];svc._schedule_resume=lambda mid:scheduled.append(mid)
 m,i=await mission();await control(m,'PAUSE',1);r=await svc.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=2,kind='RESUME'));results['normal_resume']={'status':r.status,'scheduled':scheduled}
 m,i=await mission(BudgetLimits(max_jev_calls=0));jev=_RecordingJev(JevDecision(status=DecisionStatus.ACT,selected_id='candidate'));ex,_=_executor(standard_tools(),jev=jev,authority=MissionAuthority(s));await ex._choose_candidate(i,[{'element_token':'candidate','role':'AXButton','label':'fixture','purpose':'primary'}]);results['NP09_real_authority']={'packet_limit':i.budget.max_jev_calls,'calls':len(jev.requests)}
 c=DeepController(None);calls=[]
 async def transport(role,tool,payload):calls.append({'role':role,'requested':tool,'available':SUBMISSION_ROLES[role][0]});return {}
 for role in ('PLAN','RECOVER','REVIEW'):await c._call(role,'{}',transport)
 results['NP04_role_tool_names']=calls
 results['NP06_checks']={n:[x.model_dump() for x in _fast_checks_for(n,{'app_name':'Safari','query':'AI'})] for n in ('open_app','navigate','search_browser','scroll','type_text','press_ordinal')}
 m=await s.claim_request(req(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits());calls=[]
 async def plan(*args):calls.append(args[0]);return {'plan':{'steps':[],'explanation':'fixture'}}
 await svc._plan(req(),m,invoke=plan);m=await s.get_mission(m.mission_id);results['NP10_accounting']={'calls':len(calls),'usage':m.budget_usage.model_dump()}
 sentinel='sk-review-synthetic-secret-12345678901234567890';m=await s.claim_request(req(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits());await s.commit_plan(m.mission_id,0,[StepSpec(step_id='s',ordinal=1,objective=sentinel,recipe_id='semantic_ui',scope=scope,budget=BudgetLimits())],[])
 found=[]
 for row in s._conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
  if any(sentinel in str(tuple(v)) for v in s._conn.execute('SELECT * FROM '+row[0])):found.append(row[0])
 results['plan_sink_canary']={'tables_with_synthetic_secret':found}
 await s.close()
 spec=importlib.util.spec_from_file_location('review_tts',Path.cwd()/'sani/src-tauri/python/sani_tts.py');mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
 from tests.unit.test_sani_tts_worker import _serve,_request_payload
 results['host_voice_request']=_serve(mod,[{'type':'request','request':_request_payload(message_id='')}])
 results['voice_error_correlation']=_serve(mod,[{'type':'request','request':_request_payload()}],engine=mod.UnspecifiedEngine())
 (OUT/'root-probes.json').write_text(json.dumps(results,indent=2,default=str));print(json.dumps(results,indent=2,default=str))
asyncio.run(main())

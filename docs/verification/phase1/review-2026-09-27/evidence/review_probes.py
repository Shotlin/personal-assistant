import asyncio,json,time,tempfile
from pathlib import Path
from types import SimpleNamespace
from assistant.missions.contracts import *
from assistant.missions.controller import DeepController,ControllerContext,controller_role
from assistant.core.agents import _DeepInvoke
from assistant.missions.store import MissionStore
from assistant.missions.authority import MissionAuthority
from assistant.missions.evidence import EvidenceStore
from assistant.missions.service import MissionService
from tests.integration.test_mission_policy import _DriverWorld,_wrap
from assistant.tools.policy import mission_audit_strict,mission_dispatch_guard
from tests.unit.test_mission_executor import _executor,_item,_safari_world
from tests.unit.velo_fakes import standard_tools,reset_world,FakeTool

results=[]
def report(id,expected,actual):results.append(dict(id=id,expected=expected,actual=actual))
def req(text='fixture'):
 return RequestEnvelope(request_id=new_id(),conversation_id='review',owner_id='owner',input_origin='typed_final',input_revision=1,text=text,submitted_at_ms=int(time.time()*1000))
async def main():
 tmp=Path(tempfile.mkdtemp(prefix='jarvis-p1-probes-'))
 async def event(*a):pass
 class FakeDeep:
  async def run(self,*a,**k):return {'response':json.dumps({'steps':[],'success_criteria':[],'explanation':'fixture'})}
 invoke=_DeepInvoke(FakeDeep(),thread_id='c',on_event=event,cancel_check=lambda:False)
 try:
  await DeepController(FakeDeep()).plan(req('Prepare fixture plan'),ControllerContext(role='PLAN'),invoke=invoke)
  actual='accepted'
 except Exception as e:actual=type(e).__name__+': '+str(e).splitlines()[0]
 report('RP01','Production Deep adapter accepts valid structured plan',actual)
 w=_DriverWorld(); tool=_wrap(w)[0]
 with controller_role('PLAN'):
  value=await tool.ainvoke({'pid':4101,'window_id':771,'element_token':'t1'})
 report('RP02','PLAN role cannot invoke wrapped mutation',{'effects':w.sink.count,'result':value})
 w=_DriverWorld(); tool=_wrap(w)[0]
 async def allow(*args):return None
 a=mission_audit_strict.set(True);g=mission_dispatch_guard.set(allow)
 try:value=await tool.ainvoke({'pid':4101,'window_id':771,'element_token':'t1'})
 finally:mission_audit_strict.reset(a);mission_dispatch_guard.reset(g)
 report('RP03','Strict mission mutation without action ledger is refused',{'effects':w.sink.count,'result':value})
 reset_world();_safari_world();tools=standard_tools();tools['click']._respond=lambda kw:('Error: simulated refusal',{'isError':True,'error':'simulated refusal'})
 ex,rt=_executor(tools)
 result=await ex.execute_work_item(_item(objective='press fixture-mark-go'),cancel=CancellationToken())
 report('RP04','Refused click cannot produce verified unit success',{'status':result.status,'effect_outcome':result.effect_outcome,'checks':len(result.postconditions)})
 reset_world()
 s=await MissionStore.connect(tmp/'db.sqlite');await s.setup()
 scope=Scope(owner_id='owner',allowed_apps=['com.fixture.allowed'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL'})
 mission=await s.claim_request(req(),'digest',goal='fixture',scope=scope,limits=BudgetLimits())
 forged=Scope(owner_id='owner',allowed_apps=['com.fixture.other'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL'},scope_hash=scope.scope_hash)
 svc=MissionService(SimpleNamespace(),store=s,authority=MissionAuthority(s),evidence=EvidenceStore(tmp/'e',s),executor=None,controller=DeepController(None))
 proposal=PlanProposal(steps=[StepSpec(step_id='s1',ordinal=1,objective='fixture',recipe_id='semantic_ui',scope=forged,budget=BudgetLimits())],success_criteria=[])
 try:svc._validate_proposal(proposal,mission);actual='ACCEPTED broadened app scope using copied hash'
 except Exception as e:actual=type(e).__name__
 report('RP05','Proposed plan cannot broaden scope by supplying its old hash',actual)
 spec=StepSpec(step_id='s1',ordinal=1,objective='fixture',recipe_id='semantic_ui',scope=scope,budget=BudgetLimits(),effect_class='REPEATABLE_LOCAL')
 mission=await s.commit_plan(mission.mission_id,0,[spec],[])
 item=await s.claim_step(mission.mission_id,1,1,tool_ids=['click'])
 # Unknown observed app must not gain permission merely by omitting identity.
 authority=MissionAuthority(s)
 try:
  permit=await authority.authorize(item,ActionIntent(tool='click',effect_class='REPEATABLE_LOCAL'),ScopeObservation(app_bundle='',captured_at_ms=0,driver_generation=''))
  actual='PERMIT ISSUED for unknown stale surface'
 except Exception as e:actual=type(e).__name__+': '+str(e)
 report('RP06','Unknown/stale app identity is denied',actual)
 paused=await s.control(MissionControl(control_id=new_id(),mission_id=mission.mission_id,expected_plan_version=1,expected_control_epoch=1,kind='PAUSE',reason='fixture'))
 resumed=await s.control(MissionControl(control_id=new_id(),mission_id=mission.mission_id,expected_plan_version=1,expected_control_epoch=paused.control_epoch,kind='RESUME',reason='fixture'))
 new_item=await s.claim_step(mission.mission_id,1,resumed.control_epoch,tool_ids=['click'])
 report('RP07','Pause/resume creates explicit reconciliation or a resumable ready step',{'mission_status':resumed.status,'step_states':await s.get_step_states(mission.mission_id,1),'next_item':new_item,'old_attempt':(await s.get_attempt(item.execution_id))['dispatch_state']})
 # Synthetic sentinel, never real credential.
 canary='sk-review-synthetic-secret-12345678901234567890'
 m=await s.claim_request(req('fixture '+canary),'digest2',goal='fixture '+canary,scope=scope,limits=BudgetLimits())
 stored=s._conn.execute('SELECT original_goal FROM missions WHERE mission_id=?',(m.mission_id,)).fetchone()[0]
 report('RP08','Secret-shaped raw goal is sanitized before mission persistence',{'raw_canary_retained':canary in stored})
 # Independent check cannot trust tampered evidence content.
 ev=EvidenceStore(tmp/'e',s)
 ref=await ev.put(EvidenceCandidate(kind='structured_facts',payload={'marker':'absent'},captured_at_ms=int(time.time()*1000)),mission_id=m.mission_id)
 ep=ev.root/ref.relative_path;data=json.loads(ep.read_text());data['payload']={'marker':'expected-secret-free-marker'};ep.write_text(json.dumps(data))
 check=CheckSpec(check_id='c1',verifier_id='page_state',verifier_version='1.0.0',expected={'markers':['expected-secret-free-marker']},target_scope_hash=scope.scope_hash)
 result=await ev.verify(check,SimpleNamespace(evidence_requirements=EvidenceRequirements()),refs=[ref])
 report('RP09','Changed evidence hash is rejected before verification',{'tampered_evidence_passed':result.passed})
 await s.close()
 Path('/private/tmp/jarvis-p1-independent-review/review-probes.json').write_text(json.dumps(results,indent=2,default=str))
 print(json.dumps(results,indent=2,default=str))
asyncio.run(main())

"""Independent synthetic probes; no desktop/provider/audio/production actions."""
import asyncio,importlib.util,json,time,tempfile,threading,io,struct
from pathlib import Path
from types import SimpleNamespace
from assistant.missions.store import MissionStore
from assistant.missions.contracts import *
from assistant.missions.contracts import PendingApprovalDigest
from assistant.missions.evidence import EvidenceStore
from assistant.missions.service import MissionService
from assistant.missions.controller import DeepController
from assistant.missions.authority import MissionAuthority
from assistant.core.agents import _DeepInvoke
from assistant.observability.usage import LedgerCallbackHandler,UsageLedger
from langchain_core.language_models.fake_chat_models import FakeListChatModel

root=Path(tempfile.mkdtemp(prefix='batch2-probes-',dir='/private/tmp'))
results={}
async def mission(store,steps=1):
 scope=Scope(owner_id='owner',allowed_apps=['com.fixture.safe'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL','EXTERNAL_WRITE'})
 req=RequestEnvelope(request_id=new_id(),conversation_id=new_id(),owner_id='owner',input_origin='typed_final',input_revision=1,text='fixture',submitted_at_ms=int(time.time()*1000))
 m=await store.claim_request(req,'d'*64,goal='fixture',scope=scope,limits=BudgetLimits())
 return await store.commit_plan(m.mission_id,0,[StepSpec(step_id='s'+str(n),ordinal=n,objective='fixture',recipe_id='semantic_ui',scope=scope,budget=BudgetLimits(),effect_class='EXTERNAL_WRITE',dependencies=['s'+str(n-1)] if n>1 else []) for n in range(1,steps+1)],[])
def svc(store,evidence):return MissionService(SimpleNamespace(sani_data_dir=str(root)),store=store,authority=MissionAuthority(store),evidence=evidence,executor=SimpleNamespace(),controller=DeepController(None))
async def main():
 store=await MissionStore.connect(root/'probe.db');await store.setup()
 evidence=EvidenceStore(root/'evidence',store)
 # Real callback dispatch, unlike the submission's direct callback double.
 m=await mission(store)
 transport=_DeepInvoke(None,thread_id='probe',mission_id=m.mission_id,plan_version=1,store=store,max_provider_requests=2)
 handler=LedgerCallbackHandler(UsageLedger(),'probe-actual',on_call=transport._meter_request)
 model=FakeListChatModel(responses=['fixture'])
 complete=0
 for _ in range(5):
  await model.ainvoke('fixture',config={'callbacks':[handler]});complete+=1
 results['provider_callback_ceiling']={'configured_limit':2,'completed_requests':complete,'durable_rows':len(await store.provider_requests(m.mission_id)),'callback_raise_error':handler.raise_error}
 original=store.record_provider_request_sync
 def broken(*a,**kw):raise RuntimeError('synthetic storage failure')
 store.record_provider_request_sync=broken
 await model.ainvoke('fixture',config={'callbacks':[handler]})
 results['provider_storage_failure']={'provider_response_returned':True}
 store.record_provider_request_sync=original
 # A different mission's evidence is deleted by a single-mission purge.
 a,b=await mission(store),await mission(store)
 refs=[]
 for current in (a,b):
  ref=await evidence.put(EvidenceCandidate(kind='structured_facts',payload={'mission_tag':current.mission_id},captured_at_ms=int(time.time()*1000)),mission_id=current.mission_id);refs.append(ref)
 deletion=await svc(store,evidence).purge_mission_derivatives(a.mission_id)
 results['cross_mission_deletion']={'target':a.mission_id,'other':b.mission_id,'target_file_exists':(evidence.root/refs[0].relative_path).exists(),'other_file_exists':(evidence.root/refs[1].relative_path).exists(),'other_row_deleted':store._conn.execute('SELECT deleted FROM mission_evidence WHERE evidence_id=?',(refs[1].evidence_id,)).fetchone()[0],'report':deletion}
 # Multi-step approval gate status; no synthetic tool effect is performed.
 m=await mission(store,2);item=await store.claim_step(m.mission_id,1,1,tool_ids=['click'])
 await store.apply_result(StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id=item.step_id,execution_id=item.execution_id,attempt=1,status='BLOCKED',effect_outcome='NOT_ATTEMPTED',failure_category='APPROVAL_REQUIRED',pending_approval=PendingApprovalDigest(step_id=item.step_id,tool='click',action_digest='a'*64,plan_version=1,control_epoch=1,target_ref='win-2')))
 refreshed=await store.get_mission(m.mission_id)
 try: await svc(store,evidence).control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=1,kind='RESUME'));resume='accepted'
 except Exception as exc:resume=str(exc)
 results['multi_step_approval']={'status':refreshed.status,'pending_target_ref':refreshed.pending_approvals[0].target_ref,'resume':resume}
 # Ordinal target remains unchanged; unrelated control gains focus.
 m=await mission(store);item=await store.claim_step(m.mission_id,1,1,tool_ids=['click']);now=int(time.time()*1000)
 before={'pid':9,'window_id':2,'elements':[{'element_token':'expected-third','focused':False},{'element_token':'unrelated-first','focused':False}]}
 after={**before,'elements':[{'element_token':'expected-third','focused':False},{'element_token':'unrelated-first','focused':True}]}
 rr=[]
 for n,payload in enumerate((before,after)):rr.append(await evidence.put(EvidenceCandidate(kind='structured_facts',payload=payload,captured_at_ms=now-100+n*100),mission_id=m.mission_id))
 r=await evidence.verify(CheckSpec(check_id='press',verifier_id='press_effect',verifier_version='1.0.0',expected={'ordinal':'third'},required=True),item,refs=rr)
 results['wrong_ordinal_verified']={'passed':r.passed,'reason':r.reason}
 # Same element token reused in another window must not be accepted.
 rr=[]
 for n,payload in enumerate(({'pid':9,'window_id':2,'elements':[{'element_token':'same','focused':True}]},{'pid':99,'window_id':200,'elements':[{'element_token':'same','value':'hello'}],'resolved_payloads':{'user_text_1':'hello'}})):
  rr.append(await evidence.put(EvidenceCandidate(kind='structured_facts',payload=payload,captured_at_ms=now-100+n*100),mission_id=m.mission_id))
 r=await evidence.verify(CheckSpec(check_id='field',verifier_id='field_value',verifier_version='1.0.0',expected={'payload_ref':'user_text_1'},required=True),item,refs=rr)
 results['wrong_window_field_verified']={'passed':r.passed,'reason':r.reason}
 # New wait sink persists private free text without screening.
 canary='sk-review-synthetic-canary-12345678901234567890'
 wait=await store.record_external_wait(m.mission_id,1,'s1',reason=canary,checkpoint={'detail':canary},retry_after_ms=100,deadline_ms=int(time.time()*1000)+1000)
 raw=store._conn.execute('SELECT reason,checkpoint_json FROM mission_external_waits WHERE wait_id=?',(wait,)).fetchone()
 results['wait_canary_persistence']={'synthetic_canary_in_row':canary in str(tuple(raw))}
 # Pause of approval-blocked single step clears pending digest but never requeues it.
 m=await mission(store);item=await store.claim_step(m.mission_id,1,1,tool_ids=['click'])
 await store.apply_result(StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id=item.step_id,execution_id=item.execution_id,attempt=1,status='BLOCKED',effect_outcome='NOT_ATTEMPTED',failure_category='APPROVAL_REQUIRED',pending_approval=PendingApprovalDigest(step_id=item.step_id,tool='click',action_digest='b'*64,plan_version=1,control_epoch=1)))
 await store.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=1,kind='PAUSE'))
 await svc(store,evidence).control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=2,kind='RESUME'))
 final=await store.get_mission(m.mission_id)
 results['approval_pause_resume_stranded']={'status':final.status,'states':await store.get_step_states(m.mission_id,1),'pending_count':len(final.pending_approvals)}
 # An UNKNOWN effect can be re-claimed when a wait releases a blocked step.
 m=await mission(store,2);item=await store.claim_step(m.mission_id,1,1,tool_ids=['click'])
 await store.mark_dispatched(item.execution_id)
 result=StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id=item.step_id,execution_id=item.execution_id,attempt=1,status='NEEDS_CONTROLLER',effect_outcome='UNKNOWN',failure_category='TRANSPORT_LOST',retry_after_ms=50)
 await store.apply_result(result)
 service=svc(store,evidence)
 service._schedule_wait_resume=lambda *args:None
 waiting=await service._enter_external_wait(m.mission_id,item,result)
 waits=await store.open_waits(m.mission_id);await store.release_wait(waits[0]['wait_id'])
 retried=await store.claim_step(m.mission_id,1,1,tool_ids=['click'])
 results['wait_reclaims_unknown_effect']={'waiting_status':waiting.status,'old_effect_outcome':(await store.get_attempt(item.execution_id))['effect_outcome'],'new_attempt':retried.attempt if retried else None}
 # The service finally releases by run ID rather than its acquisition fence.
 from assistant.runtime.desktop_queue import DesktopQueue
 q=DesktopQueue();old=await q.acquire('mission:duplicate')
 pending=asyncio.create_task(q.acquire('mission:duplicate'));await asyncio.sleep(0)
 await q.stop_owner('mission:duplicate');new=await pending
 await q.release('mission:duplicate')
 results['unfenced_old_cleanup']={'old_fence':old.fence,'new_fence':new.fence,'current_owner':q.current_owner,'new_lease_usable':q.check_usable(new)}
 await store.close()
asyncio.run(main())
# Bounded shutdown probe: slow synthetic engine, four queued requests, then shutdown.
path=Path('/private/tmp/jarvis-batch2-review-20260930/source/sani/src-tauri/python/sani_tts.py')
spec=importlib.util.spec_from_file_location('review_tts',path);mod=importlib.util.module_from_spec(spec)
import sys;sys.modules['review_tts']=mod;spec.loader.exec_module(mod)
entered=threading.Event();release=threading.Event()
class Engine(mod.EngineAdapter):
 def synthesize(self,*a):
  entered.set();release.wait(4);yield 24000,struct.pack('<f',0.0)*10
 def close(self):pass
class Reader:
 def __init__(self,data):self.reader=io.BytesIO(data);self.n=0
 def read(self,n):
  self.n+=1
  if self.n==3:entered.wait(1)
  return self.reader.read(n)
def frame(v):
 data=json.dumps(v).encode();return len(data).to_bytes(4,'big')+data
request={'type':'request','request':{'request_id':'r','utterance_id':'u','message_id':'m','generation':1,'text':'fixture','rate':1.0,'kind':'FINAL'}}
raw=frame(request)*5+frame({'type':'control','action':'shutdown'})
worker=mod.Worker(Reader(raw),io.BytesIO(),Engine())
t=threading.Thread(target=worker.serve,daemon=True);t.start();time.sleep(2.3)
results['worker_full_queue_shutdown']={'still_running_after_shutdown':t.is_alive(),'pending_queue_size':worker._requests.qsize(),'shutdown_flag':worker._shutdown}
release.set();t.join(.2)
results["worker_full_queue_shutdown"]["still_running_after_engine_release"] = t.is_alive()
worker._requests.get_nowait();t.join(1)
print(json.dumps(results,indent=2))
Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-batch2-2026-09-30/evidence/probes.json').write_text(json.dumps(results,indent=2)+'\n')

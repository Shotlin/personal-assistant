"""Independent offline batch-3 review probes. No network/device actions."""
import asyncio, importlib.util, json, tempfile, threading, time
from pathlib import Path
from types import SimpleNamespace
import httpx
from assistant.missions.store import MissionStore
from assistant.missions.contracts import (BudgetLimits, Scope, RequestEnvelope, StepSpec,
    StepResult, PendingApprovalDigest, ApprovalRecord, EvidenceCandidate, EvidenceRef,
    CheckSpec, CancellationToken, new_id)
from assistant.missions.service import MissionService
from assistant.missions.authority import MissionAuthority
from assistant.missions.controller import DeepController
from assistant.missions.evidence import EvidenceStore
from assistant.models.admission import ProviderAdmissionController, wrap_chat_model

ROOT=Path(tempfile.mkdtemp(prefix='batch3-review-probes-',dir='/private/tmp'))
SOURCE=Path('/private/tmp/jarvis-batch3-review-20261001/source')
OUT=Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-batch3-2026-10-01/evidence')
results={}

async def mission(store, count=1, max_requests=16):
    scope=Scope(owner_id='sani-local',allowed_apps=['com.fixture.safe'],permitted_effects={'READ_ONLY','EXTERNAL_WRITE','REPEATABLE_LOCAL'})
    req=RequestEnvelope(request_id=new_id(),conversation_id='review',owner_id='sani-local',input_origin='typed_final',input_revision=1,text='fixture',submitted_at_ms=int(time.time()*1000))
    m=await store.claim_request(req,'d'*64,goal='fixture',scope=scope,limits=BudgetLimits(max_provider_requests=max_requests))
    return await store.commit_plan(m.mission_id,0,[StepSpec(step_id=f's{i}',ordinal=i,objective='fixture',recipe_id='semantic_ui',scope=scope,budget=BudgetLimits(),effect_class='EXTERNAL_WRITE') for i in range(1,count+1)],[])

def service(store,evidence):
    return MissionService(SimpleNamespace(mission_allowed_apps=['com.fixture.safe']),store=store,authority=MissionAuthority(store),evidence=evidence,executor=SimpleNamespace(),controller=DeepController(None))

async def main():
    store=await MissionStore.connect(ROOT/'probe.db');await store.setup()
    ev=EvidenceStore(ROOT/'evidence',store)
    # Actual selected provider's SDK, with an in-memory HTTP transport only.
    from langchain_openrouter import ChatOpenRouter
    from openrouter import OpenRouter
    from openrouter.utils.retries import RetryConfig,BackoffStrategy
    m=await mission(store,max_requests=1);requests=[]
    def handle(req):
        requests.append(str(req.url))
        if len(requests)<3:
            return httpx.Response(503,json={'error':{'message':'fixture retry','code':503}})
        return httpx.Response(200,json={'id':'fixture-response','created':1,'model':'fixture','system_fingerprint':'fixture','object':'chat.completion','choices':[{'index':0,'message':{'role':'assistant','content':'fixture response'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})
    client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    sdk=OpenRouter(api_key='fixture-not-a-secret',async_client=client,retry_config=RetryConfig(strategy='backoff',backoff=BackoffStrategy(initial_interval=1,max_interval=5,exponent=1,max_elapsed_time=200),retry_connection_errors=True))
    inner=ChatOpenRouter(model_name='fixture',openrouter_api_key='fixture-not-a-secret',client=sdk)
    ctl=ProviderAdmissionController();ctl.bind_store(store)
    wrapped=wrap_chat_model(inner,ctl);scope=await ctl.open_scope(m.mission_id,1,'sdk-retry');ctl.push_scope(scope)
    try:
        response=await wrapped.ainvoke('fixture')
        rows=store._conn.execute('SELECT state,input_tokens,output_tokens FROM mission_provider_requests WHERE mission_id=?',(m.mission_id,)).fetchall()
        results['sdk_internal_retries']={'limit':1,'mock_http_requests':len(requests),'admitted':scope.admitted,'rows':rows,'response_usage':response.usage_metadata}
    except Exception as exc:results['sdk_internal_retries']={'probe_error':repr(exc),'mock_http_requests':len(requests)}
    finally:ctl.pop_scope(scope);await client.aclose()
    # Exact non-null pending target projected to UI, then current UI request shape.
    m=await mission(store);item=await store.claim_step(m.mission_id,1,1,tool_ids=['click'])
    await store.apply_result(StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id='s1',execution_id=item.execution_id,attempt=1,status='BLOCKED',effect_outcome='NOT_ATTEMPTED',failure_category='APPROVAL_REQUIRED',pending_approval=PendingApprovalDigest(step_id='s1',tool='click',action_digest='a'*64,plan_version=1,control_epoch=1,target_ref='win-7')))
    now=int(time.time()*1000)
    await store.record_approval(ApprovalRecord(approval_id=new_id(),mission_id=m.mission_id,plan_version=1,control_epoch=1,action_digest='a'*64,scope_hash=m.scope.scope_hash,effect_class='EXTERNAL_WRITE',issued_by='local_owner',issued_at_ms=now,expires_at_ms=now+300000))
    results['renderer_approval_target']={'pending_target':(await store.get_mission(m.mission_id)).pending_approvals[0].target_ref,'renderer_target_sent':None,'released_steps':await store.release_approved_blocked_steps(m.mission_id,1)}
    # New field guard still has a single-evidence fallback; focus alone proves press.
    m=await mission(store)
    async def put(payload,n):
        return await ev.put(EvidenceCandidate(kind='structured_facts',payload=payload,captured_at_ms=now+n),mission_id=m.mission_id)
    one=await put({'pid':9,'window_id':2,'resolved_payloads':{'p':'hello'},'elements':[{'element_token':'other','value':'hello','focused':False}]},0)
    c=CheckSpec(check_id='f',verifier_id='field_value',verifier_version='1.0.0',expected={'payload_ref':'p'})
    r=await ev.verify(c,SimpleNamespace(mission_id=m.mission_id),refs=[one]);results['single_capture_field']={'passed':r.passed,'reason':r.reason}
    before=await put({'pid':9,'window_id':2,'elements':[{'role':'AXButton','element_token':'b','focused':False}]},1)
    after=await put({'pid':9,'window_id':2,'elements':[{'role':'AXButton','element_token':'b','focused':True}]},2)
    r=await ev.verify(CheckSpec(check_id='p',verifier_id='press_effect',verifier_version='1.0.0',expected={'role_kind':'button','ordinal_index':1}),SimpleNamespace(mission_id=m.mission_id),refs=[before,after]);results['focus_without_activation']={'passed':r.passed,'reason':r.reason}
    # New screening covers values, not keys.
    m=await mission(store);canary='sk-review-synthetic-key-12345678901234567890'
    wid=await store.record_external_wait(m.mission_id,1,'s1',reason='fixture',checkpoint={canary:'safe'},retry_after_ms=100,deadline_ms=now+60000)
    raw=store._conn.execute('SELECT checkpoint_json FROM mission_external_waits WHERE wait_id=?',(wid,)).fetchone()[0]
    results['wait_secret_key']={'synthetic_canary_in_row':canary in raw}
    # Whole metadata purge leaves action ledger and fresh derivative files.
    m=await mission(store);ref=await ev.put(EvidenceCandidate(kind='structured_facts',payload={'fixture':'fresh'},captured_at_ms=now),mission_id=m.mission_id)
    store._conn.execute("INSERT INTO mission_action_ledger(execution_id,mission_id,tool_name,target_desc,created_at,updated_at) VALUES ('e',?,'fixture','fixture target',?,?)",(m.mission_id,now,now))
    store._conn.execute("UPDATE missions SET status='COMPLETED',updated_at_ms=? WHERE mission_id=?",(now-40*86400000,m.mission_id))
    sweep=await store.enforce_retention(now);ev.sweep_deleted_files(sweep['deleted_paths'])
    results['metadata_orphans']={'mission_purged':m.mission_id in sweep['purged_missions'],'ledger_rows':store._conn.execute('SELECT COUNT(*) FROM mission_action_ledger WHERE mission_id=?',(m.mission_id,)).fetchone()[0],'evidence_file_left':(ev.root/ref.relative_path).exists(),'evidence_rows':store._conn.execute('SELECT COUNT(*) FROM mission_evidence WHERE mission_id=?',(m.mission_id,)).fetchone()[0]}
    # Claimed PLAN rate-limit producer has no service wait consumer.
    from assistant.core.agents import _DeepInvoke
    class RateLimited:
        async def run(self,*a,**kw):raise RuntimeError('429 rate limit retry-after 1')
    svc=service(store,ev);req=RequestEnvelope(request_id=new_id(),conversation_id='rate-plan',owner_id='sani-local',input_origin='typed_final',input_revision=1,text='coordinate the fixture workflow',submitted_at_ms=now)
    try:await svc.submit(req,cancel=CancellationToken(),invoke=_DeepInvoke(RateLimited(),thread_id='fixture'))
    except Exception as exc:results['plan_rate_limit']={'exception':type(exc).__name__}
    recs=await store.list_missions();rec=next(x for x in recs if x.request_id==req.request_id)
    results.setdefault('plan_rate_limit',{}).update(status=rec.status,wait_count=len(await store.open_waits(rec.mission_id)))
    # Harness construction is offline; nonfast submission reaches missing transport before runtime.
    from tests.e2e._live import build_mission_harness,run_wrong_focus
    cfg={'data_root':str(ROOT),'allowed_apps':['com.fixture.safe'],'account_ref':'fixture','allowed_windows':[2],'max_deep_calls':1,'max_jev_calls':0,'max_actions':1}
    factory,close=build_mission_harness(cfg);harness,hstore=await factory()
    results['harness_transport']={'transport_factory_wired':harness._transport_factory is not None,'scope_has_windows':hasattr(harness._scope_for(req),'allowed_windows')}
    bad=await run_wrong_focus(harness,cfg,'coordinate the fixture workflow')
    results['harness_error_false_positive']=bad
    await close()
    # A writer blocking inside write() is not bounded by lock-acquisition timeout.
    spec=importlib.util.spec_from_file_location('review_worker',SOURCE/'sani/src-tauri/python/sani_tts.py')
    import sys
    worker=importlib.util.module_from_spec(spec);sys.modules[spec.name]=worker;spec.loader.exec_module(worker)
    entered=threading.Event();release=threading.Event();done=threading.Event()
    class Writer:
        def write(self,b):entered.set();release.wait(5);return len(b)
        def flush(self):pass
    w=worker.Worker(None,Writer());w._shutdown=True
    def writing():
        try:w._write({'type':'fixture'},droppable=True)
        finally:done.set()
    t=threading.Thread(target=writing);t.start();entered.wait(1);time.sleep(2.2)
    results['blocked_writer']={'finished_after_2_2_seconds':done.is_set(),'inside_write':entered.is_set()}
    release.set();t.join(2)
    await store.close()
    (OUT/'probes.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))

asyncio.run(main())

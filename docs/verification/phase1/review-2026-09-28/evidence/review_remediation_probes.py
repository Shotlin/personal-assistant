"""Independent review probes: synthetic stores, scripted models/devices only."""
import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict

from assistant.missions.contracts import *
from assistant.missions.store import MissionStore
from assistant.missions.authority import MissionAuthority
from assistant.missions.controller import DeepController
from assistant.missions.evidence import EvidenceStore
from assistant.missions.service import MissionService, _fast_checks_for
from assistant.missions.recovery import Reconciler

OUT = Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-2026-09-28/evidence')
records=[]
def record(case, expected, actual):
    records.append(dict(case=case, expected=expected, actual=actual))
def request(text='fixture'):
    return RequestEnvelope(request_id=new_id(),conversation_id='independent-review',owner_id='owner',input_origin='typed_final',input_revision=1,text=text,submitted_at_ms=int(time.time()*1000))

async def main():
    root=Path(tempfile.mkdtemp(prefix='jarvis-remediation-probes-'))
    from tests.integration.test_mission_composition import _settings
    from assistant.core.agents import build_core_resources
    resources=build_core_resources(_settings(root))
    service=await resources.mission_service()
    record('NP01-production-composition','Executor has store and payload resolver',{'store_bound':service._executor._store is not None,'payload_resolver_bound':service._executor._payload_resolver is not None})
    await resources.aclose()
    s=await MissionStore.connect(root/'probes.db'); await s.setup()
    scope=Scope(owner_id='owner',allowed_apps=['com.fixture.allowed'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL','EXTERNAL_WRITE'})
    async def mission(effect='EXTERNAL_WRITE',recipe='semantic_ui'):
        m=await s.claim_request(request(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits())
        m=await s.commit_plan(m.mission_id,0,[StepSpec(step_id='s1',ordinal=1,objective='fixture',recipe_id=recipe,scope=scope,budget=BudgetLimits(),effect_class=effect)],[])
        i=await s.claim_step(m.mission_id,1,1,tool_ids=['click','list_apps'])
        return m,i
    m,i=await mission()
    await s.mark_dispatched(i.execution_id)
    await s.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=1,kind='PAUSE'))
    resumed=await s.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=2,kind='RESUME'))
    second=await s.claim_step(m.mission_id,1,resumed.control_epoch,tool_ids=['click'])
    record('NP02-uncertain-retry','Dispatched external write remains blocked until proof of NO_EFFECT',{'second_attempt':second.attempt if second else None,'old_state':(await s.get_attempt(i.execution_id))['dispatch_state'],'reconciliation':asdict(await Reconciler(s).reconcile(i.execution_id))})
    m,i=await mission()
    await s.recover_inflight('review-generation')
    s._conn.execute('UPDATE mission_attempts SET external_ids=? WHERE execution_id=?',('first,second',i.execution_id))
    calls=[]
    async def effect_probe(op):
        calls.append(op);return 'NO_EFFECT' if op=='first' else 'CONFIRMED'
    recon=await Reconciler(s,probe=effect_probe).reconcile(i.execution_id)
    record('NP03-multiple-external-operations','Mixed NO_EFFECT and CONFIRMED cannot authorize retry',{'result':asdict(recon),'operations_probed':calls})
    svc=MissionService(SimpleNamespace(sani_data_dir=str(root)),store=s,authority=MissionAuthority(s),evidence=EvidenceStore(root/'e',s),executor=None,controller=DeepController(None))
    m,i=await mission('REPEATABLE_LOCAL')
    result=StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id='s1',execution_id=i.execution_id,attempt=1,status='NEEDS_CONTROLLER',effect_outcome='NOT_ATTEMPTED',failure_category='STALE_TARGET')
    await svc._escalate(m.mission_id,i,result)
    events=await s.get_events(m.mission_id)
    record('NP04-recovery-transport','Recovery invokes shared Deep transport with reserved budget',{'events':[e.model_dump() for e in events if e.kind=='control']})
    m,i=await mission('REPEATABLE_LOCAL')
    sentinel='sk-review-synthetic-secret-12345678901234567890'
    try:
        await svc.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=1,kind='REVISE',revision_request='fixture '+sentinel))
        error=None
    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    raw=s._conn.execute('SELECT payload_json FROM mission_events WHERE mission_id=?',(m.mission_id,)).fetchall()
    record('NP05-revision-transport-and-privacy','Revision safely persists screened intent and invokes versioned planner',{'error':error,'synthetic_secret_persisted':any(sentinel in r[0] for r in raw)})
    record('NP06-required-checks','Every mutating recipe has independent required outcome checks',{name:len(_fast_checks_for(name,{'app_name':'Safari'})) for name in ('open_app','navigate','search_browser','scroll','type_text','press_ordinal')})
    m,i=await mission('REPEATABLE_LOCAL')
    # Probe the actual policy-bound executor guard at the first discovery call.
    from assistant.missions.executor import VeloExecutor
    from assistant.tools.policy import cua_run_scope
    async def no_runtime():return None
    executor=VeloExecutor(SimpleNamespace(),get_runtime=no_runtime,authority=MissionAuthority(s),store=s)
    async with cua_run_scope(budget=None,run=None):
        denial=await executor._guard_dispatch(i,'list_apps',{},CancellationToken())
    record('NP07-discovery-bootstrap','Approved recipe may discover app before scoped action',{'refusal':denial})
    # Two-step proposal: the second step needs scroll, not the first app catalog.
    m=await s.claim_request(request(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits())
    m=await s.commit_plan(m.mission_id,0,[StepSpec(step_id='s1',ordinal=1,objective='open',recipe_id='open_app',scope=scope,budget=BudgetLimits()),StepSpec(step_id='s2',ordinal=2,objective='scroll',recipe_id='scroll',scope=scope,budget=BudgetLimits(),dependencies=['s1'])],[])
    first=await s.claim_step(m.mission_id,1,1,tool_ids=svc._tool_ids_for(m))
    await s.apply_result(StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id='s1',execution_id=first.execution_id,attempt=1,status='COMPLETED',effect_outcome='CONFIRMED'))
    m=await s.get_mission(m.mission_id)
    second=await s.claim_step(m.mission_id,1,1,tool_ids=svc._tool_ids_for(m))
    record('NP08-per-step-catalog','Second scroll step receives scroll permission',{'recipe':second.recipe_id,'tools':second.allowed_action_scope.tool_ids})
    # JEV boundary with zero JEV budget; no network is used.
    from tests.unit.test_mission_executor import _RecordingJev,_executor,_item
    from tests.unit.velo_fakes import standard_tools,reset_world
    from assistant.velo.contracts import JevDecision,DecisionStatus
    jev=_RecordingJev(JevDecision(status=DecisionStatus.ACT,selected_id='candidate'))
    ex,_=_executor(standard_tools(),jev=jev)
    await ex._choose_candidate(_item(budget=BudgetLimits(max_jev_calls=0)),[{'element_token':'candidate','role':'AXButton','label':'fixture','purpose':'primary'}])
    record('NP09-zero-JEV-budget','No JEV call when allowance is zero',{'calls':len(jev.requests)})
    reset_world()
    # Exactly one successful transport invocation is charged twice.
    m=await s.claim_request(request(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits())
    calls=[]
    async def invoke(*args):calls.append(args[0]);return {'plan':{'steps':[],'explanation':'fixture'}}
    await svc._plan(request('Prepare a fixture workflow'),m,invoke=invoke)
    m=await s.get_mission(m.mission_id)
    record('NP10-deep-accounting','One transport invocation consumes one unit',{'calls':len(calls),'usage':m.budget_usage.model_dump()})
    await s.close()
    # Real Deep graph, scripted provider and driver. No mission guard manually
    # injected: this is the selectable DeepAgentEntry production path.
    from tests.integration.test_mission_policy import _DriverWorld
    from tests.integration.test_mission_composition import _graph_entry,_noop_event,SKILLS_ROOT
    from assistant.agent.build import build_agent
    from assistant.memory.local import open_local_memory_resources
    from assistant.tools.policy import apply_tool_policy
    from langchain_core.messages import AIMessage
    from tests.helpers.scripted_model import ScriptedChatModel
    settings=_settings(root/'deep');world=_DriverWorld()
    async with open_local_memory_resources(settings.sani_db_path) as mem:
        model=ScriptedChatModel(responses=[AIMessage('',tool_calls=[{'name':'type_text','args':{'pid':4101,'window_id':771,'text':'fixture only'},'id':'fixture-type'}]),AIMessage('finished')])
        bundle=build_agent(model=model,checkpointer=mem.saver,store=mem.store,skills_root=SKILLS_ROOT,extra_tools=apply_tool_policy(world.tools())[0])
        deep=await _graph_entry(bundle.agent,settings)
        await deep.run('click fixture',thread_id='independent-deep',on_event=_noop_event,cancel_check=lambda:False)
    record('NP11-selectable-deep-bypass','Mission-enabled Deep route may not mutate outside a mission',{'synthetic_mutations':world.sink.count,'model_calls':model.call_index})
    # Harmless non-pytest child exits 7 and leaves stale JUnit untouched.
    # The launcher sees a literal pytest argument and treats it as pytest.
    import importlib.util,sys
    spec=importlib.util.spec_from_file_location('review_launcher','/Users/sayan/Documents/personal-assistant/scripts/verify_phase1.py')
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    stale=root/'stale-junit';stale.mkdir()
    (stale/'stale.xml').write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>')
    gate=launcher.run_suite('stale',[sys.executable,'-c','raise SystemExit(7)','pytest'],environment_kind='F',evidence_dir=stale,env={'PATH':'/usr/bin:/bin'})
    record('NP12-stale-JUnit','Nonzero process exit cannot pass by reading previous JUnit',{'status':gate['status'],'exit_code':gate['exit_code'],'observed':gate['observed']})
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'independent-probes.json').write_text(json.dumps(records,indent=2,default=str))
    print(json.dumps(records,indent=2,default=str))

asyncio.run(main())

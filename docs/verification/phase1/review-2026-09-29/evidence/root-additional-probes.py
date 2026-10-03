import asyncio,json,tempfile,time,sys,importlib.util
from pathlib import Path
from assistant.missions.contracts import *
from assistant.missions.store import MissionStore
from assistant.missions.service import RECIPE_TOOL_CATALOG
from assistant.missions.authority import MissionAuthority
from assistant.missions.executor import VeloExecutor
from assistant.tools.policy import cua_run_scope
from types import SimpleNamespace
OUT=Path('/Users/sayan/Documents/personal-assistant/docs/verification/phase1/review-2026-09-29/evidence')
async def main():
 r={};root=Path(tempfile.mkdtemp(prefix='review29-extra-'))
 from tests.integration.test_mission_composition import _settings,_graph_entry,_noop_event,SKILLS_ROOT
 from assistant.core.agents import build_core_resources
 resources=build_core_resources(_settings(root));svc=await resources.mission_service();r['NP01']={'store_bound':svc._executor._store is not None,'payload_bound':svc._executor._payload_resolver is not None};await resources.aclose()
 s=await MissionStore.connect(root/'extra.db');await s.setup();scope=Scope(owner_id='owner',allowed_apps=['com.fixture.allowed'],permitted_effects={'READ_ONLY','REPEATABLE_LOCAL'})
 def req():return RequestEnvelope(request_id=new_id(),conversation_id='extra',owner_id='owner',input_origin='typed_final',input_revision=1,text='fixture',submitted_at_ms=int(time.time()*1000))
 m=await s.claim_request(req(),'d'*64,goal='fixture',scope=scope,limits=BudgetLimits());m=await s.commit_plan(m.mission_id,0,[StepSpec(step_id='a',ordinal=1,objective='open',recipe_id='open_app',scope=scope,budget=BudgetLimits()),StepSpec(step_id='b',ordinal=2,objective='scroll',recipe_id='scroll',dependencies=['a'],scope=scope,budget=BudgetLimits())],[]);i=await s.claim_step(m.mission_id,1,1,tool_ids=[],tool_catalog=RECIPE_TOOL_CATALOG)
 async def no_runtime():return None
 ex=VeloExecutor(SimpleNamespace(),get_runtime=no_runtime,authority=MissionAuthority(s),store=s)
 async with cua_run_scope(budget=None,run=None):
  r['NP07']={'discovery_refusal':await ex._guard_dispatch(i,'list_apps',{},CancellationToken()),'mutation_refusal':await ex._guard_dispatch(i,'bring_to_front',{'pid':999},CancellationToken())}
 await s.apply_result(StepResult(mission_id=m.mission_id,plan_version=1,control_epoch=1,step_id='a',execution_id=i.execution_id,attempt=1,status='COMPLETED',effect_outcome='CONFIRMED'));i2=await s.claim_step(m.mission_id,1,1,tool_ids=[],tool_catalog=RECIPE_TOOL_CATALOG);r['NP08']={'recipe':i2.recipe_id,'tools':i2.allowed_action_scope.tool_ids}
 sentinel='sk-review-synthetic-secret-12345678901234567890'
 try:await s.control(MissionControl(control_id=new_id(),mission_id=m.mission_id,expected_plan_version=1,expected_control_epoch=1,kind='REVISE',revision_request=sentinel));refused=False
 except Exception as e:refused=True
 r['NP05']={'secret_revision_refused':refused,'persisted':any(sentinel in str(tuple(v)) for v in s._conn.execute('SELECT * FROM mission_events'))}
 from tests.unit.test_mission_authority import _item,_Intent,_action,_observed
 item=_item();item=item.model_copy(update={'allowed_action_scope':item.allowed_action_scope.model_copy(update={'tool_ids':['get_window_state']})});await _Intent(s).commit(item)
 try:p=await MissionAuthority(s).authorize(item,_action(tool='get_window_state',effect_class='READ_ONLY',args={'pid':999,'window_id':999}),_observed(app_bundle='',captured_at_ms=0));allowed=True
 except Exception:allowed=False
 r['targeted_discovery_unknown_identity']={'permit_issued':allowed}
 await s.close()
 from tests.integration.test_mission_policy import _DriverWorld
 from assistant.agent.build import build_agent
 from assistant.memory.local import open_local_memory_resources
 from assistant.tools.policy import apply_tool_policy
 from langchain_core.messages import AIMessage
 from tests.helpers.scripted_model import ScriptedChatModel
 settings=_settings(root/'deep');world=_DriverWorld()
 async with open_local_memory_resources(settings.sani_db_path) as mem:
  model=ScriptedChatModel(responses=[AIMessage('',tool_calls=[{'name':'type_text','args':{'pid':4101,'window_id':771,'text':'fixture only'},'id':'fixture-type'}]),AIMessage('finished')]);bundle=build_agent(model=model,checkpointer=mem.saver,store=mem.store,skills_root=SKILLS_ROOT,extra_tools=apply_tool_policy(world.tools())[0]);deep=await _graph_entry(bundle.agent,settings);await deep.run('click fixture',thread_id='independent29-deep',on_event=_noop_event,cancel_check=lambda:False)
 r['NP11']={'mutations':world.sink.count,'provider_calls':model.call_index}
 spec=importlib.util.spec_from_file_location('review_launcher',Path.cwd()/'scripts/verify_phase1.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);mod.REPO_ROOT=Path('/Users/sayan/Documents/personal-assistant');stale=root/'stale';stale.mkdir();(stale/'child.xml').write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>');g=mod.run_suite('child',[sys.executable,'-c','raise SystemExit(7)','pytest'],environment_kind='F',evidence_dir=stale,env={'PATH':'/usr/bin:/bin'});r['NP12']={'status':g['status'],'exit':g['exit_code']}
 (OUT/'root-additional-probes.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
asyncio.run(main())

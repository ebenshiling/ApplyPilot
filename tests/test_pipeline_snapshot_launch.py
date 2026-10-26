"""Parent/worker campaign identity parity, including a real no-network CLI launch."""
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest
import yaml

from applypilot import country_context as country
from test_country_foundation import foundation  # noqa: F401


@pytest.mark.parametrize('campaign',[None,'','   '])
def test_absent_campaign_has_one_canonical_context_and_snapshot_identity(foundation,campaign):
    root, _, cfg = foundation
    context = country.resolve(root,country_code='GB',config=cfg,campaign_id=campaign)
    assert context.campaign_id is None
    snapshot = country.export_run_snapshot(context,context.config)
    assert snapshot['campaign_id'] is None
    folder=root/'.country_runs';folder.mkdir()
    path=folder/'fixture.json';path.write_text(json.dumps(snapshot))
    restored=country.restore_run_snapshot(context,path)
    assert restored.campaign_id is None and restored.config==cfg


@pytest.mark.parametrize('legacy_campaign',['','   ',None])
def test_version_one_empty_campaign_manifest_restores_as_no_campaign(foundation,legacy_campaign):
    root, _, cfg=foundation
    context=country.resolve(root,country_code='GB',config=cfg)
    snapshot=country.export_run_snapshot(context,context.config)|{'campaign_id':legacy_campaign}
    folder=root/'.country_runs';folder.mkdir()
    path=folder/'legacy.json';path.write_text(json.dumps(snapshot))
    assert country.restore_run_snapshot(context,path).campaign_id is None


@pytest.mark.parametrize('field,value',[
    ('workspace','different-workspace'),('country_code','NZ'),('campaign_id','different-campaign'),('config_hash','wrong-hash')])
def test_actual_operation_mismatches_are_still_rejected(foundation,field,value):
    root, _, cfg=foundation
    context=country.resolve(root,country_code='GB',config=cfg)
    snapshot=country.export_run_snapshot(context,context.config)|{field:value}
    folder=root/'.country_runs';folder.mkdir()
    path=folder/'changed.json';path.write_text(json.dumps(snapshot))
    with pytest.raises(country.CountryError,match='does not match'):
        country.restore_run_snapshot(context,path)


@pytest.mark.parametrize('value',[False,True,0,[],{}])
def test_invalid_campaign_types_are_not_treated_as_an_absent_campaign(foundation,value):
    root, _, cfg=foundation
    with pytest.raises(country.CountryError,match='Campaign identifier'):
        country.resolve(root,country_code='GB',config=cfg,campaign_id=value)


CLI_PROBE = r'''
import json,runpy,socket,sys
import dotenv
original_dotenv=dotenv.load_dotenv
def workspace_dotenv_only(*args,**kwargs):
    return original_dotenv(*args,**kwargs) if args else False
dotenv.load_dotenv=workspace_dotenv_only
def deny_network(*args,**kwargs):
    raise AssertionError('CLI dry-run must not connect to a provider or employer')
socket.socket.connect=deny_network
from applypilot import country_context
original_restore=country_context.restore_run_snapshot
def observe_restore(context,path):
    restored=original_restore(context,path)
    print('RESTORED_SNAPSHOT='+json.dumps({'country':restored.country_code,'campaign':restored.campaign_id,
        'query':restored.config['queries'][0]['query'],'board_budget':restored.config.get('board_search_budget')}))
    return restored
country_context.restore_run_snapshot=observe_restore
sys.argv=['applypilot',*sys.argv[1:]]
runpy.run_module('applypilot',run_name='__main__')
'''


@pytest.mark.parametrize('campaign',['','uk-support'])
@pytest.mark.parametrize('bounded',[False,True])
def test_dashboard_find_and_assess_snapshot_survives_actual_cli_bootstrap(foundation,monkeypatch,campaign,bounded):
    from applypilot import dashboard_server as server
    from applypilot.ai_settings import workspace_context
    root, conn, cfg=foundation
    if campaign:
        cfg=cfg|{'campaigns':[{'id':'uk-support','name':'UK support','country_code':'GB','queries':[{'query':'Support Analyst','tier':1}]}]}
        (root/'searches.yaml').write_text(yaml.safe_dump(cfg))
    (root/'resume.txt').write_text('Fixture Candidate\nSUMMARY\nSynthetic support evidence')
    for key in ('APPLYPILOT_COUNTRY_SNAPSHOT','APPLYPILOT_CAMPAIGN_ID','APPLYPILOT_JOB_IDS','APPLYPILOT_REGENERATE_IDS'):
        monkeypatch.delenv(key,raising=False)
    for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','LLM_URL'):
        monkeypatch.setenv(key,'')
    monkeypatch.setenv('GEMINI_API_KEY','synthetic-no-network-key')
    monkeypatch.setenv('LLM_PROVIDER','gemini')
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    source=str(Path(__file__).resolve().parents[1]/'src')
    monkeypatch.setenv('PYTHONPATH',source)
    runner=server._PipelineRunner(root)
    payload={'stages':['discover','enrich','score'],'country_code':'GB','campaign_id':campaign,
        'workers':1,'min_score':7,'dry_run':True,'search_queries':['Application Support Analyst'],'board_search_budget':2}
    if bounded:
        from applypilot.pipeline_workspace import preview
        payload.update(scope_policy={'max_new_jobs':5,'results_per_search':3,'title_match':True,'processing_scope':'new'},smart_target_budget=2,workday_target_budget=2,
                       discover_skip_jobspy=False,discover_skip_workday=True,discover_skip_smarte=True,selected_only=False,stream=False)
        with workspace_context(root),country.operation(country.resolve_view(root,'GB',conn=conn)):
            payload['expected_preview_token']=preview(conn,root,payload)['preview_token']
    captured={};ready=threading.Event()
    def capture(cmd,**kwargs):
        captured.update(cmd=cmd,env=kwargs['env']);ready.set()
        raise OSError('Parent handoff captured for isolated CLI verification')
    with monkeypatch.context() as patch:
        patch.setattr(server.subprocess,'Popen',capture)
        with workspace_context(root),country.operation(country.resolve_view(root,'GB',conn=conn)):
            ok,info=runner.start(payload)
        assert ok,info
        assert ready.wait(10),'Parent did not reach worker launch'
        runner._thread.join(10)
        assert not runner._thread.is_alive()
    env=captured['env']
    snapshot=json.loads(Path(env['APPLYPILOT_COUNTRY_SNAPSHOT']).read_text())
    assert snapshot['campaign_id']==(campaign or None)
    assert env.get('APPLYPILOT_CAMPAIGN_ID')==(campaign or None)
    assert captured['cmd'][1:4]==['-u','-m','applypilot']
    result=subprocess.run([sys.executable,'-c',CLI_PROBE,*captured['cmd'][4:]],env=env,cwd=str(root),
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    marker=next(line.split('=',1)[1] for line in result.stdout.splitlines() if line.startswith('RESTORED_SNAPSHOT='))
    assert json.loads(marker)=={'country':'GB','campaign':campaign or None,'query':'Application Support Analyst','board_budget':2}
    assert 'DRY RUN' in result.stdout and 'No changes made.' in result.stdout
    assert 'CountryError' not in result.stdout+result.stderr

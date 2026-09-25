import shutil
import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
import server as server_module
from server import make_app

ROOT=Path(__file__).resolve().parents[1]
OWNER=Actor('dark','owner',{})


def free_port():
    s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close();return port


def warp_outbounds():
    return [
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'block','protocol':'blackhole','settings':{}},
        {'tag':'warp-us','protocol':'wireguard','settings':{'secretKey':'secret-us',
         'peers':[{'publicKey':'pub-us','endpoint':'1.1.1.1:2408'}],'address':['172.16.0.2/32']}},
        {'tag':'warp-de','protocol':'wireguard','settings':{'secretKey':'secret-de',
         'peers':[{'publicKey':'pub-de','endpoint':'1.0.0.1:2408'}],'address':['172.16.0.3/32']}},
    ]


def ready_nodes():
    return [
        {'id':'node-us','name':'USA','region':'USA','enabled':True,'online':True,'desired_state':{'pending':False},'health':{'core':{'state':'running'}}},
        {'id':'node-de','name':'Germany','region':'Germany','enabled':True,'online':True,'desired_state':{'pending':False},'health':{'core':{'state':'running'}}},
        {'id':'node-fr','name':'France','region':'France','enabled':True,'online':True,'desired_state':{'pending':False},'health':{'core':{'state':'running'}}},
        {'id':'node-uk','name':'UK','region':'UK','enabled':True,'online':True,'desired_state':{'pending':False},'health':{'core':{'state':'running'}}},
    ]


@pytest.fixture
def stage7_env(tmp_path,monkeypatch):
    fake=tmp_path/'fake-xray'
    shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);fake.chmod(0o755)
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(fake),xray_assets=str(tmp_path),xray_api_port=free_port(),
                  public_address='vpn.example.test',test_engine=True,core_autostart=False)
    engine=CoreEngine(config,store,tmp_path/'runtime')
    manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','Test!OnlyPassword123')
    manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
    engine.save_section('outbounds',warp_outbounds())
    engine.save_section('observatory',{'subjectSelector':['direct'],'probeURL':'https://example.test/204',
                                       'probeInterval':'30s','enableConcurrency':False})
    app=make_app(manager,auth,background=False)
    monkeypatch.setattr(app.state.nodes,'list',lambda: ready_nodes())
    monkeypatch.setattr(server_module,'scan_warp_outbounds',lambda binary,assets,outbounds,attempts=3,timeout=5.0:[
        {'tag':str(o.get('tag')),'ok':True,'latenciesMs':[70.0,75.0,72.0][:attempts],
         'lossPercent':0.0,'attempts':attempts,'successes':attempts,'failures':0,'error':'',
         'source':'test','probeUrl':'https://example.test/204','productionTrafficMutation':False}
        for o in outbounds])
    with TestClient(app,base_url=config.public_origin) as client:
        login=client.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
        assert login.status_code==200,login.text
        client.headers['X-Dark-CSRF']=login.json()['csrf']
        engine.apply(start=True)
        yield store,engine,client
    store.close()


def request_body():
    return {'warpAi':True,'adblock':True,'warpOutboundTags':['warp-us','warp-de'],
            'warpNodeIds':['node-us','node-de'],'adblockNodeIds':['node-fr','node-uk']}


def review(client):
    validated=client.post('/api/smart-routing/validate',json=request_body())
    assert validated.status_code==200,validated.text
    doc=validated.json()
    body=request_body()|{'baselineHash':doc['baselineHash'],'candidateHash':doc['candidateHash'],
                         'confirmation':'REVIEW SMART ROUTING'}
    response=client.post('/api/smart-routing/review',json=body)
    assert response.status_code==200,response.text
    return response.json()


def safety(client,revision_id):
    response=client.post('/api/smart-routing/safety-check',json={'revisionId':revision_id})
    assert response.status_code==200,response.text
    assert response.json()['safetyPassed'] is True,response.text
    return response.json()
def test_stage7_preview_redacts_wireguard_secrets_and_validate_does_not_mutate(stage7_env):
    _store,engine,client=stage7_env
    before_routing=engine.section('routing');before_obs=engine.section('observatory')
    preview=client.post('/api/smart-routing/preview',json=request_body())
    assert preview.status_code==200,preview.text
    assert 'outbounds' not in preview.json()
    assert 'secret-us' not in preview.text and 'secret-de' not in preview.text
    validated=client.post('/api/smart-routing/validate',json=request_body())
    assert validated.status_code==200,validated.text
    assert validated.json()['runtimeMutation'] is False
    assert engine.section('routing')==before_routing
    assert engine.section('observatory')==before_obs


def test_stage7_review_activate_and_rollback_round_trip(stage7_env):
    _store,engine,client=stage7_env
    before_routing=engine.section('routing');before_obs=engine.section('observatory')
    reviewed=review(client)
    assert reviewed['state']=='reviewed' and reviewed['rollbackAvailable'] is False
    assert engine.section('routing')==before_routing
    assert engine.section('observatory')==before_obs
    checked=safety(client,reviewed['revisionId'])
    assert checked['safetyReport']['productionTrafficMutation'] is False

    activated=client.post('/api/smart-routing/activate',json={
        'revisionId':reviewed['revisionId'],'confirmation':'APPLY SMART ROUTING'})
    assert activated.status_code==200,activated.text
    assert activated.json()['state']=='applied'
    routing=engine.section('routing');obs=engine.section('observatory')
    assert [r['ruleTag'] for r in routing['rules'][:2]]==['dark-smart-adblock','dark-smart-warp-ai']
    assert obs['subjectSelector']==['direct','warp-us','warp-de']
    assert obs['probeURL']=='https://example.test/204'

    rolled=client.post('/api/smart-routing/rollback',json={
        'revisionId':reviewed['revisionId'],'confirmation':'ROLLBACK SMART ROUTING'})
    assert rolled.status_code==200,rolled.text
    assert rolled.json()['state']=='rolled_back'
    assert engine.section('routing')==before_routing
    assert engine.section('observatory')==before_obs
def test_stage7_activate_refuses_stale_review_and_discard_is_non_mutating(stage7_env):
    _store,engine,client=stage7_env
    reviewed=review(client)
    changed={'domainStrategy':'AsIs','rules':[{'type':'field','domain':['domain:example.test'],
                                               'outboundTag':'direct'}]}
    engine.save_section('routing',changed)
    denied=client.post('/api/smart-routing/activate',json={
        'revisionId':reviewed['revisionId'],'confirmation':'APPLY SMART ROUTING'})
    assert denied.status_code==409,denied.text
    assert engine.section('routing')==changed

    discarded=client.post('/api/smart-routing/rollback',json={
        'revisionId':reviewed['revisionId'],'confirmation':'ROLLBACK SMART ROUTING'})
    assert discarded.status_code==200,discarded.text
    assert discarded.json()['state']=='discarded'
    assert discarded.json()['runtimeMutation'] is False
    assert engine.section('routing')==changed

def test_stage7_apply_requires_csrf_confirmation_and_writes_enabled(stage7_env):
    _store,engine,client=stage7_env
    reviewed=review(client)
    payload={'revisionId':reviewed['revisionId'],'confirmation':'APPLY SMART ROUTING'}

    bad_confirm=client.post('/api/smart-routing/activate',
                            json={**payload,'confirmation':'APPLY'})
    assert bad_confirm.status_code==400,bad_confirm.text

    bad_csrf=client.post('/api/smart-routing/activate',json=payload,
                         headers={'X-Dark-CSRF':'wrong'})
    assert bad_csrf.status_code==403,bad_csrf.text

    engine.config.writes_enabled=False
    try:
        readonly=client.post('/api/smart-routing/activate',json=payload)
        assert readonly.status_code==409,readonly.text
        assert reviewed['baselineHash']==client.get('/api/smart-routing/revisions').json()['items'][0]['baselineHash']
    finally:
        engine.config.writes_enabled=True

def test_stage7_review_applies_and_rolls_back_node_roles(stage7_env,monkeypatch):
    store,engine,client=stage7_env
    fake_nodes=[
        {'id':'node-us','name':'USA','region':'USA','enabled':True,'online':True},
        {'id':'node-de','name':'Germany','region':'Germany','enabled':True,'online':True},
        {'id':'node-fr','name':'France','region':'France','enabled':True,'online':True},
        {'id':'node-uk','name':'UK','region':'UK','enabled':True,'online':True},
    ]
    monkeypatch.setattr(client.app.state.nodes,'list',lambda: fake_nodes)
    body=request_body()|{'warpNodeIds':['node-us','node-de'],
                         'adblockNodeIds':['node-fr','node-uk']}
    validated=client.post('/api/smart-routing/validate',json=body)
    assert validated.status_code==200,validated.text
    assert validated.json()['nodeRoles']=={
        'warpNodeIds':['node-us','node-de'],'adblockNodeIds':['node-fr','node-uk']}
    review_body=body|{'baselineHash':validated.json()['baselineHash'],
                      'candidateHash':validated.json()['candidateHash'],
                      'confirmation':'REVIEW SMART ROUTING'}
    reviewed=client.post('/api/smart-routing/review',json=review_body)
    assert reviewed.status_code==200,reviewed.text
    revision=reviewed.json()
    assert revision['nodeRoles']==validated.json()['nodeRoles']
    safety(client,revision['revisionId'])

    applied=client.post('/api/smart-routing/activate',json={
        'revisionId':revision['revisionId'],'confirmation':'APPLY SMART ROUTING'})
    assert applied.status_code==200,applied.text
    with store.lock:
        roles=[tuple(r) for r in store.db.execute(
            'SELECT node_id,warp_ai,adblock FROM smart_routing_node_roles ORDER BY node_id')]
    assert roles==[
        ('node-de',1,0),('node-fr',0,1),('node-uk',0,1),('node-us',1,0)]

    rolled=client.post('/api/smart-routing/rollback',json={
        'revisionId':revision['revisionId'],'confirmation':'ROLLBACK SMART ROUTING'})
    assert rolled.status_code==200,rolled.text
    with store.lock:
        assert store.db.execute('SELECT COUNT(*) FROM smart_routing_node_roles').fetchone()[0]==0
    assert engine.section('routing').get('rules',[])==[]


def test_stage7_apply_is_locked_until_safety_gate_passes(stage7_env):
    _store,_engine,client=stage7_env
    reviewed=review(client)
    denied=client.post('/api/smart-routing/activate',json={
        'revisionId':reviewed['revisionId'],'confirmation':'APPLY SMART ROUTING'})
    assert denied.status_code==409,denied.text
    assert 'Safety Gate' in denied.text


def test_stage7_safety_gate_blocks_offline_selected_node(stage7_env,monkeypatch):
    _store,_engine,client=stage7_env
    reviewed=review(client)
    nodes=ready_nodes();nodes[1]['online']=False
    monkeypatch.setattr(client.app.state.nodes,'list',lambda: nodes)
    checked=client.post('/api/smart-routing/safety-check',json={'revisionId':reviewed['revisionId']})
    assert checked.status_code==200,checked.text
    doc=checked.json();assert doc['safetyPassed'] is False
    assert any('node-de' in x and 'offline' in x for x in doc['safetyReport']['issues'])


def test_stage7_safety_gate_expires_before_apply(stage7_env):
    store,_engine,client=stage7_env
    reviewed=review(client);safety(client,reviewed['revisionId'])
    with store.transaction() as db:
        db.execute('UPDATE smart_routing_revisions SET safety_checked_at=? WHERE id=?',
                   (time.time()-301,reviewed['revisionId']))
    denied=client.post('/api/smart-routing/activate',json={
        'revisionId':reviewed['revisionId'],'confirmation':'APPLY SMART ROUTING'})
    assert denied.status_code==409,denied.text
    assert 'expired' in denied.text

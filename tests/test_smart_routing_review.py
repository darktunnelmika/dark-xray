import hashlib
import json
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
    node_store={};counter={'n':0}
    monkeypatch.setattr(app.state.nodes,'list',lambda: ready_nodes())
    monkeypatch.setattr(app.state.nodes,'assignments',lambda node_id: [])
    def fake_set(node_id,value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(raw).hexdigest()
        old=node_store.get(node_id);counter['n']+=0 if old and old['hash']==digest else 1
        revision=(old or {}).get('revision',0)+(0 if old and old['hash']==digest else 1)
        node_store[node_id]={'node_id':node_id,'revision':revision,'hash':digest,'payload':value,'pending':True,
                             'applied_revision':(old or {}).get('applied_revision',0),'applied_hash':(old or {}).get('applied_hash',''),
                             'last_error':''}
        return node_store[node_id]
    def fake_desired(node_id,include_payload=True):
        row=dict(node_store.get(node_id,{'node_id':node_id,'revision':0,'hash':'','payload':{},'pending':False,
                                        'applied_revision':0,'applied_hash':'','last_error':''}))
        if not include_payload:row.pop('payload',None)
        return row
    def fake_sync(node_id,state,legacy_bundles=None):
        row=node_store[node_id];row['pending']=False;row['applied_revision']=state['revision'];row['applied_hash']=state['hash'];row['last_error']=''
        return {'desired_state_applied':True,'items':[],'desired_revision':state['revision'],'desired_hash':state['hash']}
    def fake_probe(node_id,timeout=8.0):
        return {'node':{'id':node_id},'latency_ms':12,'health':{'service':'DARK XRAY NODE','core':{'state':'running','dirty':False,'last_error':''}}}
    monkeypatch.setattr(app.state.nodes,'set_desired_state',fake_set)
    monkeypatch.setattr(app.state.nodes,'desired_state',fake_desired)
    monkeypatch.setattr(app.state.nodes,'sync_desired_state',fake_sync)
    monkeypatch.setattr(app.state.nodes,'probe',fake_probe)
    monkeypatch.setattr(app.state.nodes,'smart_warp_probe',lambda node_id,tags,attempts=2,timeout_seconds=5:{
        'node_id':node_id,'latency_ms':10,'productionTrafficMutation':False,
        'items':[{'tag':tag,'ok':True,'latencyMs':80.0,'lossPercent':0.0,'jitterMs':5.0} for tag in tags]})
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


def start_rollout(client,revision_id):
    response=client.post('/api/smart-routing/rollout/start',json={
        'revisionId':revision_id,'confirmation':'START STAGED ROLLOUT','observationSeconds':1})
    assert response.status_code==202,response.text
    assert response.json()['observationSeconds']==1
    rollout_id=response.json()['rolloutId']
    deadline=time.time()+8
    while time.time()<deadline:
        doc=client.get('/api/smart-routing/rollout/'+rollout_id).json()
        if doc['state']!='running':return doc
        time.sleep(.05)
    raise AssertionError('rollout did not finish')
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

    rollout=start_rollout(client,reviewed['revisionId'])
    assert rollout['state']=='completed',rollout
    assert rollout['phase']=='complete'
    assert all(x['state']=='completed' for x in rollout['items'])
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

def test_stage7_review_applies_and_rolls_back_node_roles(stage7_env):
    store,engine,client=stage7_env
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

    rollout=start_rollout(client,revision['revisionId'])
    assert rollout['state']=='completed',rollout
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


def test_stage7_rollout_is_locked_until_safety_gate_passes(stage7_env):
    _store,_engine,client=stage7_env
    reviewed=review(client)
    denied=client.post('/api/smart-routing/rollout/start',json={
        'revisionId':reviewed['revisionId'],'confirmation':'START STAGED ROLLOUT'})
    assert denied.status_code==409,denied.text
    assert 'Safety PASS' in denied.text


def test_stage7_safety_gate_blocks_offline_selected_node(stage7_env,monkeypatch):
    _store,_engine,client=stage7_env
    reviewed=review(client)
    nodes=ready_nodes();nodes[1]['online']=False
    monkeypatch.setattr(client.app.state.nodes,'list',lambda: nodes)
    checked=client.post('/api/smart-routing/safety-check',json={'revisionId':reviewed['revisionId']})
    assert checked.status_code==200,checked.text
    doc=checked.json();assert doc['safetyPassed'] is False
    assert any('node-de' in x and 'offline' in x for x in doc['safetyReport']['issues'])


def test_stage7_safety_gate_expires_before_rollout(stage7_env):
    store,_engine,client=stage7_env
    reviewed=review(client);safety(client,reviewed['revisionId'])
    with store.transaction() as db:
        db.execute('UPDATE smart_routing_revisions SET safety_checked_at=? WHERE id=?',
                   (time.time()-301,reviewed['revisionId']))
    denied=client.post('/api/smart-routing/rollout/start',json={
        'revisionId':reviewed['revisionId'],'confirmation':'START STAGED ROLLOUT'})
    assert denied.status_code==409,denied.text
    assert 'Safety PASS' in denied.text


def test_stage7_staged_rollout_auto_rolls_back_when_second_canary_fails(stage7_env,monkeypatch):
    store,engine,client=stage7_env
    before_routing=engine.section('routing');reviewed=review(client);safety(client,reviewed['revisionId'])
    counts={}
    def flaky_probe(node_id,timeout=8.0):
        counts[node_id]=counts.get(node_id,0)+1
        if node_id=='node-fr' and counts[node_id]==3:
            raise OSError('simulated failure inside observation window')
        return {'node':{'id':node_id},'latency_ms':12,
                'health':{'service':'DARK XRAY NODE','core':{'state':'running','dirty':False,'last_error':''}}}
    monkeypatch.setattr(client.app.state.nodes,'probe',flaky_probe)
    rollout=start_rollout(client,reviewed['revisionId'])
    assert rollout['state']=='rolled_back',rollout
    states={x['node_id']:x['state'] for x in rollout['items']}
    assert states['node-us']=='rolled_back'
    assert states['node-fr']=='rolled_back'
    assert engine.section('routing')==before_routing
    with store.lock:
        revision=store.db.execute('SELECT state FROM smart_routing_revisions WHERE id=?',(reviewed['revisionId'],)).fetchone()
    assert revision['state']=='reviewed'


def test_stage7_direct_apply_is_disabled_for_node_targeted_revision(stage7_env):
    _store,_engine,client=stage7_env
    reviewed=review(client);safety(client,reviewed['revisionId'])
    denied=client.post('/api/smart-routing/activate',json={
        'revisionId':reviewed['revisionId'],'confirmation':'APPLY SMART ROUTING'})
    assert denied.status_code==409,denied.text
    assert 'staged rollout' in denied.text


def test_simple_warp_api_create_status_and_modes(stage7_env,monkeypatch):
    _store,engine,client=stage7_env
    def fake_register(*,tag='warp'):
        return {'registered':True,'deviceId':'device-test','outbound':{
            'tag':tag,'protocol':'wireguard','settings':{
                'secretKey':'private-do-not-return','address':['172.16.50.2/32'],
                'reserved':[1,2,3],'peers':[{'publicKey':'peer-test','endpoint':'162.159.192.1:2408',
                                             'allowedIPs':['0.0.0.0/0','::/0'],'keepAlive':30}]},
            'streamSettings':{'sockopt':{}}}}
    monkeypatch.setattr(server_module,'register_cloudflare_warp',fake_register)

    created=client.post('/api/warp/create',json={'tag':'warp'})
    assert created.status_code==200,created.text
    doc=created.json()
    assert doc['registered'] is True and doc['created'] is True and doc['runtimeMutation'] is False
    assert doc['secretExposed'] is False and 'private-do-not-return' not in created.text

    status=client.get('/api/warp/status').json()
    assert status['mode']=='off' and status['endpoint']=='162.159.192.1:2408'

    def fake_probe(_binary,_assets,outbounds,*,tags=None,attempts=1,timeout=5.0,trace=False):
        selected=set(tags or [])
        rows=[]
        for i,out in enumerate(outbounds):
            tag=str(out.get('tag') or '')
            if selected and tag not in selected:continue
            endpoint=((out.get('settings') or {}).get('peers') or [{}])[0].get('endpoint','')
            rows.append({'tag':tag,'testable':True,'success':True,'delayMs':40.0,
                         'lossPercent':0.0,'jitterMs':3.0,'error':'',
                         'productionTrafficMutation':False,
                         'egress':{'ip':'104.28.1.1','country':'DE','colo':'FRA','warp':'on'} if trace else {},
                         'warpVerified':bool(trace)})
        return rows
    monkeypatch.setattr(server_module,'probe_outbounds',fake_probe)

    batch=client.post('/api/outbounds/test',json={'tags':['warp'],'attempts':1,'timeoutSeconds':5})
    assert batch.status_code==200,batch.text
    assert batch.json()['items'][0]['delayMs']==40.0

    paths=client.post('/api/warp/endpoints/scan',json={'tag':'warp'})
    assert paths.status_code==200,paths.text
    path_rows=paths.json()['items']
    assert len(path_rows)>=12
    assert len({x['endpoint'] for x in path_rows})==len(path_rows)
    assert all(x['ready'] for x in path_rows)
    chosen=next(x['endpoint'] for x in path_rows if x['endpoint']=='162.159.192.5:500')
    selected=client.post('/api/warp/endpoint',json={'tag':'warp','endpoint':chosen})
    assert selected.status_code==200,selected.text
    assert selected.json()['endpoint']==chosen
    assert next(x for x in engine.section('outbounds') if x['tag']=='warp')['settings']['peers'][0]['endpoint']==chosen

    scan=client.post('/api/warp/scan',json={'tag':'warp'})
    assert scan.status_code==200,scan.text
    assert scan.json()['passed'] is True and scan.json()['productionTrafficMutation'] is False

    ai=client.post('/api/warp/mode',json={'tag':'warp','mode':'ai','adblock':False})
    assert ai.status_code==200,ai.text
    assert ai.json()['mode']=='ai' and ai.json()['applied'] is True
    rules=engine.section('routing')['rules']
    assert rules[0]['ruleTag']=='dark-warp-ai' and rules[0]['outboundTag']=='warp'

    all_=client.post('/api/warp/mode',json={'tag':'warp','mode':'all','adblock':True})
    assert all_.status_code==200,all_.text
    assert all_.json()['mode']=='all' and all_.json()['adblock'] is True
    rules=engine.section('routing')['rules']
    assert [r['ruleTag'] for r in rules[:2]]==['dark-smart-adblock','dark-warp-all']

    off=client.post('/api/warp/mode',json={'tag':'warp','mode':'off','adblock':False})
    assert off.status_code==200,off.text
    assert off.json()['mode']=='off'
    assert not [r for r in engine.section('routing')['rules'] if r.get('ruleTag') in {'dark-warp-ai','dark-warp-all'}]

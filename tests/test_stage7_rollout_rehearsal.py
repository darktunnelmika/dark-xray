"""Stage 7.2 failure-injection rehearsal for Smart Routing staged rollout.

These tests use disposable SQLite/runtime directories and fake Xray. They never
read or write /opt/dark-xray, /opt/dark-xray-node, or the production database.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server as server_module
import nodes as nodes_module
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app
from test_smart_routing_review import (
    OWNER,ready_nodes,request_body,review,safety,start_rollout,stage7_env,warp_outbounds,
)

ROOT=Path(__file__).resolve().parents[1]


def test_rehearsal_canary_disconnect_inside_observation_rolls_back_and_never_touches_hub(stage7_env,monkeypatch):
    store,engine,client=stage7_env
    baseline=engine.section('routing')
    reviewed=review(client);safety(client,reviewed['revisionId'])
    counts={}
    original=client.app.state.nodes.probe

    def disconnecting_probe(node_id,timeout=8.0):
        counts[node_id]=counts.get(node_id,0)+1
        if node_id=='node-us' and counts[node_id]==3:
            raise OSError('injected canary disconnect during observation')
        return original(node_id,timeout=timeout)

    monkeypatch.setattr(client.app.state.nodes,'probe',disconnecting_probe)
    rollout=start_rollout(client,reviewed['revisionId'])
    assert rollout['state']=='rolled_back',rollout
    states={x['node_id']:x['state'] for x in rollout['items']}
    assert states['node-us']=='rolled_back'
    assert engine.section('routing')==baseline
    with store.lock:
        revision=store.db.execute('SELECT state FROM smart_routing_revisions WHERE id=?',(reviewed['revisionId'],)).fetchone()
    assert revision['state']=='reviewed'


def test_rehearsal_post_apply_warp_degradation_rolls_back_before_batch(stage7_env,monkeypatch):
    store,engine,client=stage7_env
    baseline=engine.section('routing')
    reviewed=review(client);safety(client,reviewed['revisionId'])
    original=client.app.state.nodes.smart_warp_probe

    def degraded(node_id,tags,attempts=2,timeout_seconds=5):
        if node_id=='node-us':
            return {'node_id':node_id,'latency_ms':10,'productionTrafficMutation':False,
                    'items':[{'tag':tag,'ok':True,'latencyMs':1800.0,'lossPercent':35.0,'jitterMs':500.0}
                             for tag in tags]}
        return original(node_id,tags,attempts=attempts,timeout_seconds=timeout_seconds)

    monkeypatch.setattr(client.app.state.nodes,'smart_warp_probe',degraded)
    rollout=start_rollout(client,reviewed['revisionId'])
    assert rollout['state']=='rolled_back',rollout
    first=rollout['items'][0]
    assert first['node_id']=='node-us' and first['state']=='rolled_back'
    assert all(x['state']=='pending' for x in rollout['items'][1:])
    assert engine.section('routing')==baseline
    with store.lock:
        revision=store.db.execute('SELECT state FROM smart_routing_revisions WHERE id=?',(reviewed['revisionId'],)).fetchone()
    assert revision['state']=='reviewed'


def test_rehearsal_success_orders_all_node_delivery_before_hub_apply(stage7_env,monkeypatch):
    _store,engine,client=stage7_env
    reviewed=review(client);safety(client,reviewed['revisionId'])
    events=[]
    registry=client.app.state.nodes
    original_sync=registry.sync_desired_state
    original_apply=engine.apply_config

    def traced_sync(node_id,state,legacy_bundles=None):
        events.append('node:'+node_id)
        return original_sync(node_id,state,legacy_bundles=legacy_bundles)

    def traced_apply(*args,**kwargs):
        events.append('hub')
        return original_apply(*args,**kwargs)

    monkeypatch.setattr(registry,'sync_desired_state',traced_sync)
    monkeypatch.setattr(engine,'apply_config',traced_apply)
    rollout=start_rollout(client,reviewed['revisionId'])
    assert rollout['state']=='completed',rollout
    assert events and events[-1]=='hub'
    node_events=[x for x in events if x.startswith('node:')]
    assert node_events==['node:node-us','node:node-fr','node:node-de','node:node-uk']
    assert events.index('hub')>max(events.index(x) for x in node_events)


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def _install_fake_registry(monkeypatch,state,events):
    def list_nodes(self):
        return ready_nodes()
    def assignments(self,node_id):
        return []
    def set_desired(self,node_id,value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        digest=hashlib.sha256(raw).hexdigest();old=state.get(node_id)
        revision=(old or {}).get('revision',0)+(0 if old and old['hash']==digest else 1)
        state[node_id]={'node_id':node_id,'revision':revision,'hash':digest,'payload':value,'pending':True,
                        'applied_revision':(old or {}).get('applied_revision',0),
                        'applied_hash':(old or {}).get('applied_hash',''),'last_error':''}
        return state[node_id]
    def desired(self,node_id,include_payload=True):
        row=dict(state.get(node_id,{'node_id':node_id,'revision':0,'hash':'','payload':{},'pending':False,
                                    'applied_revision':0,'applied_hash':'','last_error':''}))
        if not include_payload:row.pop('payload',None)
        return row
    def sync(self,node_id,envelope,legacy_bundles=None):
        row=state[node_id];row['pending']=False;row['applied_revision']=envelope['revision'];row['applied_hash']=envelope['hash']
        events.append('node:'+node_id)
        return {'desired_state_applied':True,'items':[],'desired_revision':envelope['revision'],'desired_hash':envelope['hash']}
    def probe(self,node_id,timeout=8.0):
        return {'node':{'id':node_id},'latency_ms':9,
                'health':{'service':'DARK XRAY NODE','core':{'state':'running','dirty':False,'last_error':''}}}
    def warp(self,node_id,tags,attempts=2,timeout_seconds=5):
        return {'node_id':node_id,'latency_ms':8,'productionTrafficMutation':False,
                'items':[{'tag':tag,'ok':True,'latencyMs':70.0,'lossPercent':0.0,'jitterMs':3.0} for tag in tags]}
    monkeypatch.setattr(nodes_module.NodeRegistry,'list',list_nodes)
    monkeypatch.setattr(nodes_module.NodeRegistry,'assignments',assignments)
    monkeypatch.setattr(nodes_module.NodeRegistry,'set_desired_state',set_desired)
    monkeypatch.setattr(nodes_module.NodeRegistry,'desired_state',desired)
    monkeypatch.setattr(nodes_module.NodeRegistry,'sync_desired_state',sync)
    monkeypatch.setattr(nodes_module.NodeRegistry,'probe',probe)
    monkeypatch.setattr(nodes_module.NodeRegistry,'smart_warp_probe',warp)


def _open_hub(root:Path,*,bootstrap:bool,background:bool,monkeypatch,state,events):
    root.mkdir(parents=True,exist_ok=True)
    fake=root/'fake-xray'
    if not fake.exists():
        shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);fake.chmod(0o755)
    store=Store(root/'dark.sqlite3')
    cfg=Config(xray_binary=str(fake),xray_assets=str(root),xray_api_port=_free_port(),
               public_address='vpn.example.test',test_engine=True,core_autostart=False,poll_seconds=60)
    engine=CoreEngine(cfg,store,root/'runtime')
    manager=Manager(store,engine);auth=Auth(store,root/'secret.key')
    if bootstrap:
        auth.bootstrap('dark','Test!OnlyPassword123')
        manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
        engine.save_section('outbounds',warp_outbounds())
        engine.save_section('observatory',{'subjectSelector':['direct'],'probeURL':'https://example.test/204',
                                           'probeInterval':'30s','enableConcurrency':False})
    _install_fake_registry(monkeypatch,state,events)
    monkeypatch.setattr(server_module,'scan_warp_outbounds',lambda binary,assets,outbounds,attempts=3,timeout=5.0:[
        {'tag':str(o.get('tag')),'ok':True,'latenciesMs':[55.0,60.0,58.0][:attempts],
         'lossPercent':0.0,'attempts':attempts,'successes':attempts,'failures':0,'error':'',
         'source':'test','probeUrl':'https://example.test/204','productionTrafficMutation':False}
        for o in outbounds])
    app=make_app(manager,auth,background=background)
    engine.apply(start=True)
    return store,engine,manager,auth,app


def test_rehearsal_persisted_mid_rollout_resumes_after_hub_restart(tmp_path,monkeypatch):
    root=tmp_path/'restart-rehearsal';state={};events=[]
    store,engine,manager,auth,app=_open_hub(root,bootstrap=True,background=False,monkeypatch=monkeypatch,state=state,events=events)
    with TestClient(app,base_url=engine.config.public_origin) as client:
        login=client.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
        client.headers['X-Dark-CSRF']=login.json()['csrf']
        reviewed=review(client);safety(client,reviewed['revisionId'])
        rollout_id='a'*32;now=time.time()
        with store.transaction() as db:
            db.execute('''INSERT INTO smart_routing_rollouts(
                id,revision_id,actor,created_at,state,phase,current_index,observation_seconds,detail,started_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (rollout_id,reviewed['revisionId'],'dark',now,'running','canary',0,1,'persisted before restart',now))
            for idx,(node_id,role) in enumerate([
                ('node-us','canary-warp'),('node-fr','canary-adblock'),('node-de','warp'),('node-uk','adblock')]):
                db.execute('''INSERT INTO smart_routing_rollout_nodes(
                    rollout_id,node_id,ord,role,state,updated_at) VALUES(?,?,?,?,?,?)''',
                    (rollout_id,node_id,idx,role,'pending',now))
    manager.close();engine.close();store.close()

    store2,engine2,manager2,auth2,app2=_open_hub(root,bootstrap=False,background=True,monkeypatch=monkeypatch,state=state,events=events)
    try:
        original_apply=engine2.apply_config
        def traced_apply(*args,**kwargs):
            events.append('hub');return original_apply(*args,**kwargs)
        monkeypatch.setattr(engine2,'apply_config',traced_apply)
        with TestClient(app2,base_url=engine2.config.public_origin) as client2:
            login=client2.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
            client2.headers['X-Dark-CSRF']=login.json()['csrf']
            deadline=time.time()+12;doc=None
            while time.time()<deadline:
                response=client2.get('/api/smart-routing/rollout/'+rollout_id)
                assert response.status_code==200,response.text
                doc=response.json()
                if doc['state']!='running':break
                time.sleep(.1)
            assert doc and doc['state']=='completed',doc
            assert events[-1]=='hub'
            assert [x for x in events if x.startswith('node:')]==[
                'node:node-us','node:node-fr','node:node-de','node:node-uk']
    finally:
        manager2.close();engine2.close();store2.close()

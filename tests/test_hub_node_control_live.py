"""Live Hub dispatcher/monitor contracts; real SQLite/API, in-process transport.

No provider WAN or installed-VPS acceptance is implied. Fake Xray is used only
in the explicitly paired Agent tests.
"""
from __future__ import annotations
import json
import time
import contextlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config, CoreEngine
from dark_policy import Store, PolicyError, Actor
from manager import Manager
from server import make_app
from node_commands import NodeCommands
from nodes import NodeRegistry
import nodes as node_module
from test_node_control_lifecycle import rebooted_agent
from test_node_hub_recovery import NODE, ORIGIN, TOKEN, payload, post_state


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(node_module, 'validate_origin', lambda x: x)
    root = tmp_path/'hub';root.mkdir()
    store = Store(root/'dark.sqlite3')
    config = Config(test_engine=True, core_autostart=False,
                    xray_binary=str(root/'missing'), xray_assets=str(root))
    engine = CoreEngine(config, store, root/'runtime');manager = Manager(store, engine)
    auth = Auth(store, root/'secret.key');auth.bootstrap('dark', 'Test!OnlyPassword123')
    manager.owner_put(Actor('dark', 'owner', {}), 'dark', name='DARK', allowed=[])
    app = make_app(manager, auth, background=False)
    app.state.nodes.put(NODE, 'Node', ORIGIN, TOKEN, True, [1])
    try:
        with TestClient(app, base_url=config.public_origin, raise_server_exceptions=False) as client:
            result = client.post('/api/auth/login', json={'username':'dark','password':'Test!OnlyPassword123'})
            client.headers['X-Dark-CSRF'] = result.json()['csrf']
            yield app.state.nodes, client, engine
    finally:
        engine.close();store.close()


def offline(*args, **kwargs):
    raise PolicyError('Injected network outage')


def health(node=NODE, supported=True):
    return {'service':'DARK XRAY NODE', 'node_id':node, 'agent_only':True,
            'capabilities':{'ordered_control':1} if supported else {},
            'core':{'state':'running', 'dirty':False}}


def ack(node_id, path, method='GET', body=None, timeout=8.0):
    if path == '/node/api/health':return health(node_id), 1
    assert path == '/node/api/v1/control'
    return {'service':'DARK XRAY NODE','node_id':node_id,
            'revision':body['revision'],'commandId':body['commandId'],
            'action':body['action'],'applied':True,
            'engine':{'state':'stopped' if body['action']=='stop' else 'running'}}, 1


def command(client, action):
    response=client.post(f'/api/nodes/{NODE}/core/{action}', json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_owner_stop_offline_is_durable_pending_not_completed(hub, monkeypatch):
    reg, client, _ = hub
    monkeypatch.setattr(reg, '_request', offline)
    result = command(client, 'stop')
    assert result['queued'] is True and result['executed'] is False
    current = NodeCommands(reg).status(NODE)
    assert current['pending'] and current['action']=='stop' and current['last_error']
    assert client.get('/api/nodes').json()[0]['control']['command_id']==current['command_id']
    assert command(client, 'stop')['control']['command_id']==current['command_id']


def test_dispatch_checks_capability_after_persisting_and_acknowledges_exact_result(hub, monkeypatch):
    reg, client, _ = hub;seen=[]
    def request(*a, **k):
        assert NodeCommands(reg).status(NODE)['pending']
        seen.append(a[1]);return ack(*a, **k)
    monkeypatch.setattr(reg, '_request', request)
    result=command(client, 'stop')
    assert seen==['/node/api/health','/node/api/v1/control']
    assert result['executed'] is True and result['queued'] is False
    assert result['control']['applied_revision']==result['control']['revision']==1


@pytest.mark.parametrize('mode', ['old', 'foreign', 'boolean-capability', 'disabled'])
def test_unsupported_or_disabled_nodes_do_not_receive_legacy_mutation(hub, monkeypatch, mode):
    reg, client, _ = hub;calls=[]
    if mode=='disabled':reg.set_enabled(NODE, False)
    def request(node, path, *args, **kwargs):
        calls.append(path);assert path=='/node/api/health'
        doc=health(node, supported=mode!='old')
        if mode=='foreign':doc['node_id']='other-node'
        if mode=='boolean-capability':doc['capabilities']['ordered_control']=True
        return doc, 1
    monkeypatch.setattr(reg, '_request', request)
    result=command(client, 'stop')
    assert result['queued'] and not result['executed'] and result['control']['last_error']
    assert result['delivery_state'] in {'unsupported_agent','identity_mismatch','disabled'}
    assert calls==([] if mode=='disabled' else ['/node/api/health'])


def test_pending_stop_excludes_subscription_even_with_healthy_cached_core(hub, monkeypatch):
    reg, client, _=hub
    with reg.store.transaction() as db:
        db.execute('UPDATE remote_nodes SET last_seen=?,last_health=? WHERE id=?',(time.time(),json.dumps(health()),NODE))
        db.execute('UPDATE remote_node_inbounds SET remote_inbound_id=1,last_sync=? WHERE node_id=?',(time.time(),NODE))
    monkeypatch.setattr(reg,'_client_inbounds',lambda email:[1])
    assert reg.failover_targets('alice')
    monkeypatch.setattr(reg,'_request',offline);command(client,'stop')
    node=reg.list()[0]
    assert node['online'] and node['control']['pending']
    assert not node['failover_ready'] and not reg.failover_targets('alice')


def test_disabled_node_retries_only_after_reenable(hub, monkeypatch):
    reg, client, _=hub;reg.set_enabled(NODE,False)
    monkeypatch.setattr(reg,'_request',lambda *a,**k:pytest.fail('Disabled Node contacted'))
    before=command(client,'stop')['control']['command_id']
    assert reg.deliver_pending_control(NODE)['queued']
    reg.set_enabled(NODE,True);monkeypatch.setattr(reg,'_request',ack)
    result=reg.deliver_pending_control(NODE)
    assert not result['queued'] and result['control']['command_id']==before


def test_stop_not_dependent_on_broken_configuration(hub, monkeypatch):
    reg,client,_=hub
    reg.set_desired_state(NODE, {'schema':1})
    with reg.store.transaction() as db:
        db.execute("UPDATE remote_node_desired_state SET desired_json='broken' WHERE node_id=?",(NODE,))
    monkeypatch.setattr(reg,'_request',ack)
    assert command(client,'stop')['executed']


def test_resume_waits_for_pending_configuration_instead_of_starting_stale_clients(hub, monkeypatch):
    reg, client, _=hub;reg.set_desired_state(NODE,{'schema':1})
    seen=[]
    monkeypatch.setattr(reg,'_request',lambda *a,**k:seen.append(a[1]) or ack(*a,**k))
    result=command(client,'start')
    assert result['queued'] and result['delivery_state']=='configuration_pending'
    assert '/node/api/v1/control' not in seen


def test_newer_command_does_not_receive_old_error_or_old_result(hub, monkeypatch):
    reg,client,_=hub
    def transport(node,path,*a,**k):
        if path.endswith('/health'):return health(node),1
        reg.commands.record(node,'start')
        return ack(node,path,*a,**k)
    monkeypatch.setattr(reg,'_request',transport)
    result=command(client,'stop')
    assert result['queued'] and not result['executed'] and result['result'] is None
    assert result['control']['action']=='start' and not result['control']['last_error']


def test_owner_auth_csrf_readonly_and_invalid_action_do_not_record(hub, monkeypatch):
    reg,client,engine=hub
    monkeypatch.setattr(reg,'_request',lambda *a,**k:pytest.fail('Unauthorized request sent'))
    for action,headers in [('stop',{'X-Dark-CSRF':''}),('shell',{})]:
        response=client.post(f'/api/nodes/{NODE}/core/{action}',json={},headers=headers)
        assert response.status_code in {400,403},response.text
    engine.config.writes_enabled=False
    assert client.post(f'/api/nodes/{NODE}/core/stop',json={}).status_code==409
    assert not NodeCommands(reg).status(NODE)['persisted']


def test_delete_cleans_control_register_without_resurrecting_inflight_ack(hub, monkeypatch):
    reg, client, _=hub
    def transport(node,path,*a,**k):
        if path.endswith('/health'):return health(node),1
        reg.delete(node);return ack(node,path,*a,**k)
    monkeypatch.setattr(reg,'_request',transport)
    result=command(client,'stop')
    assert not result['executed'] and result['result'] is None
    assert not NodeCommands(reg).status(NODE)['persisted']


def test_intent_changed_during_probe_cannot_bypass_resume_config_gate(hub, monkeypatch):
    reg,client,_=hub;reg.set_desired_state(NODE,{'schema':1});calls=[]
    def transport(node,path,*a,**k):
        calls.append(path)
        if path.endswith('/health'):
            reg.commands.record(node,'start');return health(node),1
        pytest.fail('New Start bypassed configuration gate through old Stop preflight')
    monkeypatch.setattr(reg,'_request',transport)
    result=command(client,'stop')
    assert result['queued'] and result['delivery_state']=='superseded'
    assert result['control']['action']=='start' and calls==['/node/api/health']


def bridge(reg, client, events=None):
    def request(node_id,path,method='GET',body=None,timeout=8.0):
        assert node_id==NODE
        if events is not None:events.append(path)
        response=client.request(method,path,json=body)
        if response.status_code!=200:raise PolicyError(f'Node HTTP {response.status_code}: {response.text}')
        reg._request_ok(node_id,1)
        return response.json(),1
    return request


@pytest.mark.parametrize('action',['stop','restart'])
def test_real_hub_api_lost_ack_survives_registry_recreation_then_monitor_recovers(hub, tmp_path, monkeypatch, action):
    reg,client,_=hub
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,node_client,_):
        post_state(node_client,payload(engine))
        transport=bridge(reg,node_client)
        def lost_ack(*a,**k):
            result=transport(*a,**k)
            if a[1]=='/node/api/v1/control':raise OSError('Injected lost response')
            return result
        monkeypatch.setattr(reg,'_request',lost_ack)
        result=command(client,action)
        assert result['queued'] and runtime.command_status()['phase']=='applied'
        identity=result['control']['command_id']
        # The background retry is the real NodeRegistry.start loop, not a direct
        # NodeCommands invocation. The reopened registry reads the same Hub DB.
        reg.close();replacement=NodeRegistry(reg.store,reg.cipher)
        transport=bridge(replacement,node_client)
        monkeypatch.setattr(replacement,'_request',transport)
        monkeypatch.setattr(runtime,'_command_locked',lambda *a,**k:pytest.fail('Lost ACK reexecuted command'))
        try:
            replacement.start(interval=.05,initial_delay=0)
            for _ in range(1000):
                if not replacement.commands.status(NODE)['pending']:break
                time.sleep(.01)
            assert not replacement.commands.status(NODE)['pending'], str(replacement.commands.status(NODE))
            assert replacement.commands.status(NODE)['command_id']==identity
            assert engine.running==(action=='restart')
        finally:replacement.close()


def test_monitor_delivers_stop_before_broken_compiler_or_traffic(hub, monkeypatch):
    reg,client,_=hub;order=[]
    monkeypatch.setattr(reg,'_request',offline);command(client,'stop')
    def transport(*a,**k):order.append(a[1]);return ack(*a,**k)
    monkeypatch.setattr(reg,'_request',transport)
    def broken(*a,**k):order.append('broken-config');raise PolicyError('Missing TLS file')
    monkeypatch.setattr(reg,'sync_traffic',offline)
    try:
        reg.start(interval=.05,initial_delay=0,desired_provider=broken)
        for _ in range(1000):
            if 'broken-config' in order:break
            time.sleep(.01)
    finally:reg.close()
    assert order.index('/node/api/v1/control')<order.index('broken-config')
    assert not reg.commands.status(NODE)['pending']


def test_monitor_stages_fresh_configuration_before_pending_start(hub, tmp_path, monkeypatch):
    reg,client,_=hub;events=[]
    with rebooted_agent(tmp_path/'node',autostart=False) as (_,engine,runtime,node_client,_):
        body=payload(engine)
        reg.set_desired_state(NODE,body)
        monkeypatch.setattr(reg,'_request',offline);command(client,'start')
        def desired(node):
            reg.set_desired_state(node,body);return reg.desired_state(node)
        transport=bridge(reg,node_client,events)
        def checked(node,path,method='GET',body=None,timeout=8.0):
            result=transport(node,path,method,body,timeout)
            if path.endswith('/state/apply'):
                assert body['payload']['desiredRunning'] is False
                assert not engine.running, 'Configuration implicitly executed pending Start'
            return result
        monkeypatch.setattr(reg,'_request',checked)
        try:
            reg.start(interval=10,initial_delay=0,desired_provider=desired)
            for _ in range(1000):
                if not reg.commands.status(NODE)['pending']:break
                time.sleep(.01)
            assert not reg.commands.status(NODE)['pending'] and engine.running, str((reg.commands.status(NODE),events))
            assert events.index('/node/api/v1/state/apply')<events.index('/node/api/v1/control')
        finally:reg.close()
        pid=engine.process.pid;revision=runtime.status()['appliedRevision']
        state=desired(NODE)
        assert state['revision']==revision, 'Acknowledging Start created a spurious config revision'
        # The command is acknowledged; future identical config cannot repeat it.
        monkeypatch.setattr(reg,'_request',transport)
        assert reg.sync_desired_state(NODE,state)['desired_state_applied']
        assert engine.process.pid==pid


def test_manual_sync_cannot_bypass_unsupported_pending_stop(hub,monkeypatch):
    reg,client,_=hub;calls=[]
    reg.set_desired_state(NODE,{'schema':1,'nodeId':NODE,'assignments':[]})
    def transport(node,path,*a,**k):
        calls.append(path)
        assert path=='/node/api/health'
        return health(node,supported=False),1
    monkeypatch.setattr(reg,'_request',transport);command(client,'stop')
    result=reg.sync_desired_state(NODE,reg.desired_state(NODE),legacy_bundles=[])
    assert result['queued'] and result['sync_deferred'] and not result['desired_state_applied']
    with pytest.raises(PolicyError,match='pending'):reg.sync_mirrors(NODE,[])
    assert all(path.endswith('/health') for path in calls)


def test_stopped_intent_overlays_compiler_and_old_persisted_payload(hub,monkeypatch):
    reg,client,_=hub
    source={'schema':1,'nodeId':NODE,'desiredRunning':True,'assignments':[]}
    reg.set_desired_state(NODE,source)
    monkeypatch.setattr(reg,'_request',ack);command(client,'stop')
    stored=reg.desired_state(NODE)
    assert stored['payload']['desiredRunning'] is False and stored['revision']==2
    reg.set_desired_state(NODE,source)
    assert reg.desired_state(NODE)['revision']==2 and source['desiredRunning'] is True


def test_validate_keeps_legacy_readonly_contract_without_record(hub,monkeypatch):
    reg,client,_=hub;paths=[]
    def request(node,path,*a,**k):
        paths.append(path);return {'engine':{'valid':True}},1
    monkeypatch.setattr(reg,'_request',request)
    assert command(client,'validate')['validated'] is True
    assert paths==['/node/api/core/validate'] and not reg.commands.status(NODE)['persisted']

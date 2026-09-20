"""Pre-update regressions. Isolated SQLite/API fixtures; never contact a VPS."""
from __future__ import annotations

import asyncio
import copy
import json
import time
import threading
from pathlib import Path

import pytest
from auth import Auth
from dark_policy import Store, PolicyError
import nodes as node_module
from nodes import NodeRegistry
from test_node_hub_recovery import agent

TOKEN = 'dkn_' + 'A' * 60
SHA = 'a' * 40

@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(node_module, 'validate_origin', lambda origin: origin)
    store = Store(tmp_path / 'dark.sqlite3')
    auth = Auth(store, tmp_path / 'secret.key')
    reg = NodeRegistry(store, auth.cipher)
    reg.put('n1', 'Node One', 'https://node.example.test', TOKEN, True, [1])
    try:
        yield reg
    finally:
        reg.close()
        store.close()


def desired():
    return {'schema': 1, 'nodeId': 'n1', 'assignments': [
        {'sourceInboundId': 1, 'inbound': {}, 'clients': []}
    ]}


def healthy(reg):
    health = {'service': 'DARK XRAY NODE', 'core': {'state': 'running', 'dirty': False}}
    with reg.store.transaction() as db:
        db.execute("UPDATE remote_nodes SET last_seen=?,last_health=? WHERE id='n1'", (time.time(), json.dumps(health)))
        db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=1,last_sync=? WHERE node_id='n1'", (time.time(),))


@pytest.mark.parametrize('operation', ['remote_update_check', 'remote_update_start'])
def test_hub_node_update_reaches_transport_for_exact_sha(registry, monkeypatch, operation):
    seen = []
    def request(node_id, path, method='GET', body=None, timeout=8.0):
        seen.append((path, body))
        return {'service': 'DARK XRAY NODE', 'update': {'state': 'ready'}}, 1
    monkeypatch.setattr(registry, '_request', request)
    assert getattr(registry, operation)('n1', SHA)['update']['state'] == 'ready'
    assert seen[0][1] == {'commit': SHA}
    with pytest.raises(PolicyError):
        getattr(registry, operation)('n1', 'main')
    assert len(seen) == 1


def test_unchanged_desired_state_keeps_diagnostic_and_timestamp(registry):
    state = registry.set_desired_state('n1', desired())
    registry.mark_desired_state('n1', state['revision'], state['hash'], error='TLS apply failed')
    before = registry.desired_state('n1', include_payload=False)
    repeated = registry.set_desired_state('n1', desired())
    after = registry.desired_state('n1', include_payload=False)
    assert repeated['changed'] is False
    assert after['last_error'] == 'TLS apply failed'
    assert after['updated_at'] == before['updated_at']
    assert after['pending'] is True


@pytest.mark.parametrize('core', [{'state': 'stopped'}, {'state': 'running', 'dirty': True}, {'state': 'running', 'last_error': 'reload failed'}])
def test_online_agent_is_not_a_ready_data_plane(registry, monkeypatch, core):
    healthy(registry)
    monkeypatch.setattr(registry, '_client_inbounds', lambda client: [1])
    with registry.store.transaction() as db:
        db.execute("UPDATE remote_nodes SET last_health=? WHERE id='n1'", (json.dumps({'core': core}),))
    node = registry.list()[0]
    assert node['online'] is True
    assert node['assignments'][0]['deployed'] is False
    assert node['failover_ready'] is False
    assert registry.failover_targets('alice') == []


def test_pending_credentials_are_not_published_using_old_deployment(registry, monkeypatch):
    healthy(registry)
    monkeypatch.setattr(registry, '_client_inbounds', lambda client: [1])
    one = registry.set_desired_state('n1', desired())
    registry.mark_desired_state('n1', one['revision'], one['hash'])
    assert registry.failover_targets('alice')
    newer = desired(); newer['generation'] = 'new client credential'
    registry.set_desired_state('n1', newer)
    row = registry.list()[0]
    assert row['desired_state']['pending'] is True
    assert row['assignments'][0]['deployed'] is False
    assert registry.failover_targets('alice') == []


@pytest.mark.parametrize('change', ['enabled', 'token'])
def test_metadata_edit_does_not_discard_traffic_baseline(registry, change):
    with registry.store.transaction() as db:
        db.execute("INSERT INTO remote_node_client_usage(node_id,client_id,raw_up,raw_down,initialized) VALUES('n1','alice',100,200,1)")
    registry.put('n1', 'Renamed', 'https://node.example.test', 'dkn_'+'B'*60 if change=='token' else TOKEN,
                 change != 'enabled', [1])
    with registry.store.lock:
        row = registry.store.db.execute("SELECT raw_up,raw_down,initialized FROM remote_node_client_usage WHERE node_id='n1'").fetchone()
    assert tuple(row) == (100, 200, 1)


def test_monitor_builds_payload_after_quota_callback(registry, monkeypatch):
    model = {'enable': True}; sent = []; done = threading.Event()
    def provider(node):
        value = desired(); value['client'] = copy.deepcopy(model)
        registry.set_desired_state(node, value)
        return registry.desired_state(node)
    def charge(node, result):
        model['enable'] = False
    def sync(node, state, legacy_bundles=None):
        sent.append(state['payload']['client']['enable']); done.set()
    monkeypatch.setattr(registry, 'probe', lambda *a, **k: {})
    monkeypatch.setattr(registry, 'sync_traffic', lambda *a: {'charged_bytes': 10})
    monkeypatch.setattr(registry, 'sync_desired_state', sync)
    registry.start(interval=60, initial_delay=0, desired_provider=provider, traffic_callback=charge)
    assert done.wait(2)
    registry.close()
    assert sent == [False]


@pytest.mark.parametrize('items', [[], [{'sourceInboundId': 1, 'remoteInboundId': 0}],
    [{'sourceInboundId': 1, 'remoteInboundId': 1}, {'sourceInboundId': 1, 'remoteInboundId': 2}],
    [{'sourceInboundId': 99, 'remoteInboundId': 1}], [{'sourceInboundId': 1, 'remoteInboundId': 1, 'error': 'apply failed'}]])
def test_partial_or_duplicate_ack_is_not_marked_applied(registry, monkeypatch, items):
    registry.set_desired_state('n1', desired()); state = registry.desired_state('n1')
    def request(*args, **kwargs):
        return {'service':'DARK XRAY NODE', 'appliedRevision':state['revision'], 'appliedHash':state['hash'],
                'items': items, 'core': {'state': 'running'}}, 1
    monkeypatch.setattr(registry, '_request', request)
    with pytest.raises(PolicyError):
        registry.sync_desired_state('n1', state)
    assert registry.desired_state('n1', include_payload=False)['pending'] is True


def test_process_logs_are_bounded_and_use_actual_core_file(tmp_path, monkeypatch):
    with agent(tmp_path / 'node') as (_, engine, _, client):
        path=engine.runtime/'process.log'
        path.write_text('x'*(1024*1024)+'\nXRAY PROCESS OUTPUT\n')
        original=Path.read_text
        def no_full_log_read(self,*args,**kwargs):
            if self==path:raise AssertionError('Must seek the log tail instead of reading the whole file')
            return original(self,*args,**kwargs)
        monkeypatch.setattr(Path,'read_text',no_full_log_read)
        response = client.get('/node/api/logs/process')
        assert response.status_code == 200
        assert response.json()['lines'][-1] == 'XRAY PROCESS OUTPUT'
        assert response.json()['truncated'] is True


def test_unauthenticated_invalid_json_is_rejected_before_parser(tmp_path):
    with agent(tmp_path / 'node') as (_, _, _, client):
        response = client.post('/node/api/v1/state/apply', content=b'not-json',
                               headers={'Authorization': '', 'Content-Type': 'application/json'})
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


def test_chunked_body_limit_is_independent_of_content_length(tmp_path):
    with agent(tmp_path / 'node') as (_, _, _, client):
        data = b'{' + b' ' * (8*1024*1024) + b'}'
        response = client.post('/node/api/v1/state/apply', content=iter([data]),
                               headers={'Content-Type': 'application/json'})
        assert response.status_code == 413


def test_stale_ack_cannot_partially_overwrite_new_assignments(registry, monkeypatch):
    healthy(registry)
    registry.set_desired_state('n1', desired()); state=registry.desired_state('n1')
    def request(*args, **kwargs):
        newer=desired(); newer['generation']='changed while request was in flight'
        registry.set_desired_state('n1', newer)
        return {'service':'DARK XRAY NODE','appliedRevision':state['revision'],'appliedHash':state['hash'],
                'items':[{'sourceInboundId':1,'remoteInboundId':99}], 'core':{'state':'running'}}, 1
    monkeypatch.setattr(registry,'_request',request)
    with pytest.raises(PolicyError,match='stale'):
        registry.sync_desired_state('n1',state)
    assert registry.assignments('n1')[0]['remote_inbound_id']==1
    assert registry.desired_state('n1',include_payload=False)['revision']==2
    assert registry.desired_state('n1',include_payload=False)['applied_revision']==0


def test_delayed_failure_does_not_relabel_new_desired_generation(registry):
    one=registry.set_desired_state('n1',desired())
    newer=desired(); newer['generation']=2
    two=registry.set_desired_state('n1',newer)
    with pytest.raises(PolicyError,match='stale'):
        registry.mark_desired_state('n1',one['revision'],one['hash'],error='old failure')
    current=registry.desired_state('n1',include_payload=False)
    assert current['revision']==two['revision'] and current['last_error']==''


def test_remote_stop_removes_endpoint_readiness_immediately(registry,monkeypatch):
    healthy(registry)
    monkeypatch.setattr(registry,'_client_inbounds',lambda client:[1])
    assert registry.failover_targets('alice')
    monkeypatch.setattr(registry,'_request',lambda *a,**k:({'engine':{'state':'stopped'},'node_agent':True},1))
    registry.remote_core('n1','stop')
    assert registry.list()[0]['online'] is True
    assert registry.failover_targets('alice')==[]


@pytest.mark.parametrize('value', [float('nan'),float('inf'),{1,2}])
def test_desired_state_rejects_non_finite_or_non_json(registry,value):
    with pytest.raises(PolicyError):
        registry.set_desired_state('n1',{'bad':value})
    assert registry.desired_state('n1')['revision']==0


def test_desired_state_limit_counts_utf8_bytes(registry):
    # Under 8 MiB characters, but over 8 MiB when actually transmitted as UTF-8.
    with pytest.raises(PolicyError,match='too large'):
        registry.set_desired_state('n1',{'unicode':'\u062f'*(5*1024*1024)})


# Exercise the actual Hub links and public subscription routes, not just a
# readiness helper. The existing fixture uses local FastAPI and test Xray.
from test_nodes_v2 import env as hub_env, _managed_client, _test_vless


def test_explicit_node_host_and_subscription_wait_for_current_revision(hub_env):
    store,engine,app,client=hub_env
    iid=client.post('/api/inbounds',json=_test_vless('NODE',24567,'target')).json()['id']
    _managed_client(client,'readiness-user',iid)
    reg=app.state.nodes
    reg.put('n1','Node One','https://node.example.test',TOKEN,True,[iid])
    engine.save_section('hosts',[{'inboundId':iid,'runtime':'node:n1','address':'node.example.test',
                                  'port':24567,'enable':True,'remark':'NODE'}])
    healthy(reg)
    # Test source IDs are generated by the Hub fixture.
    doc=desired(); doc['assignments'][0]['sourceInboundId']=iid
    one=reg.set_desired_state('n1',doc);reg.mark_desired_state('n1',one['revision'],one['hash'])
    response=client.get('/api/clients/readiness-user/links')
    assert response.status_code==200,response.text
    assert len(response.json()['engine']['links'])==1
    subscription=response.json()['subscription_url']+'?format=raw'
    sub=client.get(subscription)
    assert sub.status_code==200 and 'node.example.test' in sub.text
    doc['generation']=2;reg.set_desired_state('n1',doc)
    assert client.get('/api/clients/readiness-user/links').json()['engine']['links']==[]
    sub=client.get(subscription)
    assert sub.status_code==503 and 'node.example.test' not in sub.text
    two=reg.desired_state('n1');reg.mark_desired_state('n1',two['revision'],two['hash'])
    response=client.get('/api/clients/readiness-user/links')
    assert response.status_code==200,response.text
    assert len(response.json()['engine']['links'])==1


@pytest.mark.parametrize('deploy_local',[False,True])
def test_global_ip_limit_verifies_only_selected_runtime_sources(hub_env,deploy_local):
    store,engine,app,client=hub_env
    ib=_test_vless('REMOTE IP',24568,'remote-ip')
    ib['panelMeta']={'deployLocal':deploy_local}
    iid=client.post('/api/inbounds',json=ib).json()['id']
    _managed_client(client,'remote-ip-user',iid,{'limitIp':1})
    reg=app.state.nodes;now=time.time()
    for number,ip in enumerate(['203.0.113.10','203.0.113.11'],1):
        node='ip'+str(number)
        reg.put(node,node,'https://'+node+'.example.test',TOKEN,True,[iid])
        with store.transaction() as db:
            db.execute('UPDATE remote_node_inbounds SET remote_inbound_id=1 WHERE node_id=?',(node,))
            db.execute('INSERT INTO remote_node_security_state(node_id,source_verified,last_sync,last_error) VALUES(?,1,?,?)',(node,now,''))
            db.execute('INSERT INTO remote_node_ips(node_id,client_id,ip,first_seen,last_seen,verified) VALUES(?,?,?,?,?,1)',
                       (node,'remote-ip-user',ip,now,now))
    result=reg.reconcile_global_security(local_source_verified=False,now=now)
    item=next(x for x in result['items'] if x['client_id']=='remote-ip-user')
    assert item['ip_count']==2
    assert item['ip_enforceable'] is (not deploy_local)
    assert item['ip_blocked'] is (not deploy_local)

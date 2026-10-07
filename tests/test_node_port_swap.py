"""Port-scoped relay keeps shared logical inbound customers at their destination."""
import copy
import json
from dataclasses import replace

import pytest
from core import Config, CoreEngine
from dark_policy import Actor, PolicyError, Store
from node_runtime import NodeRuntime
from test_node_relay import relay_env, env, desired, create

BODY = {'name': 'Germany via Netherlands', 'sourceNodeId': 'nl', 'sourcePort': 26801,
        'exitNodeId': 'de', 'inboundId': 1, 'exitPort': 26802,
        'entryAddress': 'iran.example.test', 'entryPort': 6801}
ACK = lambda n: {'desired_state_applied': True, 'queued': False}


def setup_route(f, **changes):
    state, _, _ = f
    c = state[-1]
    r = c.post('/api/swap', json=BODY | changes)
    assert r.status_code == 200, r.text
    return c.app.state.node_port_swaps, r.json()['id']


def test_same_inbound_independent_ports_no_source_customer(relay_env, tmp_path, monkeypatch):
    state, _, _ = relay_env; store, engine, _, _, c = state
    create(c, 'swap-customer', extra={'totalGB': 1024**3})
    before = desired(c, 'nl'); old_meta = copy.deepcopy(engine.inbound(1)['panelMeta'])
    swap, rid = setup_route(relay_env)
    assert desired(c, 'nl') == before
    assert engine.inbound(1)['panelMeta'] == old_meta
    assert desired(c, 'de')['assignments'][0]['inbound']['panelMeta']['tunnelPorts']['local'] == 26802
    calls = []
    def sync(n):
        calls.append(n)
        payload = desired(c, n)
        if n == 'de': assert not swap.get(rid)['enabled']
        return ACK(n)
    swap.toggle(rid, True, sync)
    assert calls == ['de', 'nl']
    src, dst = desired(c, 'nl'), desired(c, 'de')
    assert src['assignments'][:-1] == before['assignments']
    assert src['assignments'][-1]['clients'] == []
    assert src['assignments'][-1]['inbound']['settings']['port'] == 26802
    assert src['sections']['routing']['rules'][1:] == before['sections']['routing']['rules']
    assert src['sections']['routing']['rules'][0]['inboundTag'] == ['dark-swap-'+str(rid)]
    assert not any(c['sourceEmail'].startswith('dark-swap-') for b in dst['assignments'] for c in b['clients'])
    monkeypatch.setattr(CoreEngine, 'validate', lambda self, config=None: {'validated': True, 'hash': self.config_hash(config or self.build_config())})
    for node, payload in [('nl', src), ('de', dst)]:
        db = Store(tmp_path/(node+'.sqlite3'))
        rt_engine = CoreEngine(Config(test_engine=True, xray_binary='/missing', xray_assets=str(tmp_path)), db, tmp_path/node)
        try:
            runtime = NodeRuntime(db, rt_engine, node)
            payload['desiredRunning'] = False
            _, digest = runtime._canonical(payload)
            runtime.apply({'revision': 1, 'hash': digest, 'payload': payload})
            built = rt_engine.build_config()
            if node == 'nl':
                forward = next(i for i in built['inbounds'] if i['protocol'] == 'dokodemo-door')
                assert forward['port'] == 26801 and not forward.get('sniffing', {}).get('enabled')
            else:
                ibs = [i for i in built['inbounds'] if i['port'] in (24443, 26802)]
                assert len(ibs) == 2
                assert ibs[0]['settings']['clients'] == ibs[1]['settings']['clients']
                assert ibs[0]['streamSettings'] == ibs[1]['streamSettings']
        finally: rt_engine.close(); db.close()


def test_live_rename_is_non_disruptive_and_keeps_route_enabled(relay_env):
    state, _, _ = relay_env; c = state[-1]
    swap, rid = setup_route(relay_env)
    swap.toggle(rid, True, ACK)
    before = swap.get(rid)
    r = c.patch(f'/api/swap/{rid}/name', json={'name': 'DE Premium via NL'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['name'] == 'DE Premium via NL'
    assert body['enabled'] is True and body['phase'] == 'enabled'
    assert body['accountingMode'] == 'destination-customer'
    after = swap.get(rid)
    assert after['source_node'] == before['source_node']
    assert after['source_port'] == before['source_port']
    assert after['exit_node'] == before['exit_node']
    assert after['exit_port'] == before['exit_port']
    assert after['inbound_id'] == before['inbound_id']


def test_swap_customer_usage_is_charged_at_destination_not_synthetic_relay(relay_env):
    state, nodes, _ = relay_env; c = state[-1]
    create(c, 'swap-customer', extra={'totalGB': 1024 ** 3})
    swap, rid = setup_route(relay_env)
    swap.toggle(rid, True, ACK)

    src = desired(c, 'nl')
    generated = next(x for x in src['assignments'] if x['sourceInboundId'] == swap.source_id(rid))
    assert generated['clients'] == []

    dst = desired(c, 'de')
    destination = next(x for x in dst['assignments'] if x['sourceInboundId'] == BODY['inboundId'])
    assert any(x['sourceEmail'] == 'swap-customer' for x in destination['clients'])

    baseline = nodes.apply_traffic_snapshot('de', [{'sourceEmail': 'swap-customer', 'up': 0, 'down': 0}])
    assert baseline['charged_bytes'] == 0
    charged = nodes.apply_traffic_snapshot('de', [{'sourceEmail': 'swap-customer', 'up': 31, 'down': 69}])
    assert charged['charged_bytes'] == 100

    fake_relay = nodes.apply_traffic_snapshot('nl', [
        {'sourceEmail': 'dark-swap-' + str(rid), 'up': 5000, 'down': 7000}
    ])
    assert fake_relay['charged_bytes'] == 0
    assert fake_relay['ignored_clients'] == 1


@pytest.mark.parametrize('changes', [
    {'sourcePort': 24443}, {'sourcePort': 24445}, {'sourcePort': 443},
    {'exitPort': 24443}, {'sourceNodeId': 'de'}, {'entryPort': 0}, {'entryAddress': 'https://bad.test'},
])
def test_reject_collisions_and_invalid_parameters(relay_env, changes):
    c = relay_env[0][-1]
    assert c.post('/api/swap', json=BODY | changes).status_code in (400, 422)
    assert not c.app.state.node_port_swaps.rows()


def test_existing_destination_tunnel_reused_and_delete_preserves_it(relay_env):
    state, _, _ = relay_env; c = state[-1]; engine = state[1]
    ib = engine.inbound(1); ib['panelMeta']['tunnelPorts']['node:de'] = 26802
    engine.save_inbound(ib, 1)
    assert c.post('/api/swap', json=BODY | {'exitPort': 26803}).status_code == 400
    swap, rid = setup_route(relay_env); swap.delete(rid, ACK)
    assert desired(c, 'de')['assignments'][0]['inbound']['panelMeta']['tunnelPorts']['local'] == 26802


def test_failed_ack_intent_is_retryable_and_blocks_unsafe_changes(relay_env):
    state, _, _ = relay_env; c = state[-1]; swap, rid = setup_route(relay_env)
    with pytest.raises(PolicyError, match='Destination'):
        swap.toggle(rid, True, lambda n: {})
    assert not swap.get(rid)['enabled']
    with pytest.raises(PolicyError, match='pending'):
        swap.toggle(rid, True, lambda n: ACK(n) if n == 'de' else {})
    assert swap.get(rid)['phase'] == 'enabling'
    with pytest.raises(PolicyError): swap.configure(BODY, rid)
    with pytest.raises(PolicyError): swap.delete(rid, ACK)
    ib = state[1].inbound(1); ib['port'] = 28000
    assert c.put('/api/inbounds/1', json=ib).status_code == 400
    assert c.delete('/api/nodes/de').status_code == 400
    assert c.put('/api/inbounds/1/deployments', json={'local': True, 'nodeIds': ['nl'], 'tunnelPorts': {}}).status_code == 400
    swap.toggle(rid, True, ACK)
    with pytest.raises(PolicyError): swap.toggle(rid, False, lambda n: {})
    assert swap.get(rid)['phase'] == 'disabling'
    swap.toggle(rid, False, ACK)
    assert not any(b['inbound']['protocol'] == 'dokodemo-door' for b in desired(c, 'nl')['assignments'])
    with pytest.raises(PolicyError): swap.delete(rid, lambda n: {})
    assert swap.get(rid)['phase'] == 'deleting'
    assert desired(c, 'de')['assignments'][0]['inbound'].get('panelMeta', {}).get('tunnelPorts', {}).get('local') is None
    swap.delete(rid, ACK)
    assert not swap.rows()


def test_disable_saved_nodes_does_not_break_ordinary_payload(relay_env):
    state, nodes, _ = relay_env; swap, rid = setup_route(relay_env)
    with state[0].transaction() as db: db.execute("UPDATE remote_nodes SET enabled=0 WHERE id='de'")
    assert desired(state[-1], 'nl')['assignments']
    assert desired(state[-1], 'de')['assignments']
    with pytest.raises(PolicyError): swap.toggle(rid, True, ACK)


def test_ports_reserved_through_inbound_api_and_owner_only(relay_env, monkeypatch):
    state, _, _ = relay_env; c = state[-1]; swap, rid = setup_route(relay_env)
    ib = state[1].inbound(2); ib['port'] = BODY['sourcePort']
    assert c.put('/api/inbounds/2', json=ib).status_code == 400
    assert c.delete('/api/inbounds/1').status_code == 400
    auth = state[3]; original = auth.current
    for principal in ('reseller', 'key'):
        monkeypatch.setattr(auth, 'current', lambda *a, **kw: replace(original(*a, **kw),
            **({'actor': Actor('seller','reseller',{})} if principal=='reseller' else {'key_id':'api-key'})))
        assert c.get('/api/swap').status_code == 403
        assert c.post('/api/swap', json=BODY).status_code == 403
        assert c.post(f'/api/swap/{rid}/state', json={'enabled': True}).status_code == 403
        assert c.delete(f'/api/swap/{rid}').status_code == 403


def test_subscriptions_destination_scoped_direct_preserved(relay_env, monkeypatch):
    state, nodes, _ = relay_env; c = state[-1]; engine = state[1]
    create(c, 'swap-customer', extra={'totalGB': 1024**3})
    engine.save_section('hosts', [{'inboundId':1,'runtime':'node:'+n,'address':n+'.example.test','port':24443,
        'remark':n,'enable':True} for n in ('nl','de')])
    swap, rid = setup_route(relay_env)
    monkeypatch.setattr(nodes, 'list', lambda: [nodes.get(n)|{'online': True,'desired_state': {'pending':False}, 'runtime_presence': {'state':'running'}} for n in ('nl','de','am')])
    monkeypatch.setattr(nodes, '_runtime_block_reason', lambda n:'')
    ready = {'node:nl':{1},'node:de':{1}}
    before = engine.links('swap-customer', runtime_ready=ready)['links']
    swap.toggle(rid, True, ACK)
    after = engine.links('swap-customer', runtime_ready=ready)['links']
    assert len(after) == len(before)+1
    assert {l['uri'] for l in before} <= {l['uri'] for l in after}
    assert sum('iran.example.test:6801' in link['uri'] for link in after) == 1
    assert all('iran.example.test' not in link['uri'] for link in engine.links('swap-customer', runtime_ready={'node:nl':{1}})['links'])
    swap.toggle(rid, False, ACK)
    assert engine.links('swap-customer', runtime_ready=ready)['links'] == before


def test_legacy_payload_fallback_forbidden(relay_env, monkeypatch):
    state,nodes,_=relay_env; c=state[-1];swap,rid=setup_route(relay_env);swap.toggle(rid,True,ACK)
    payload=c.get('/api/nodes/nl/desired').json()
    def reject(*a,**k): raise PolicyError('Node HTTP 404')
    monkeypatch.setattr(nodes,'_request',reject)
    monkeypatch.setattr(nodes,'sync_mirrors',lambda *a:pytest.fail('cannot bypass port forwarding'))
    with pytest.raises(PolicyError): nodes._sync_desired_state_locked('nl',payload,legacy_bundles=payload['payload']['assignments'])


def _acknowledge_payload(node_id, path, method, body, timeout):
    return {'service': 'DARK XRAY NODE', 'appliedRevision': body['revision'],
            'appliedHash': body['hash'], 'items': [
                {'sourceInboundId': item['sourceInboundId'], 'remoteInboundId': i+10}
                for i, item in enumerate(body['payload']['assignments'])]}, 1


def test_generated_relay_ack_preserves_real_assignments(relay_env):
    state, nodes, _ = relay_env; client = state[-1]
    swap, rid = setup_route(relay_env); swap.toggle(rid, True, ACK)
    before = {a['local_inbound_id'] for a in nodes.assignments('nl')}
    desired_state = client.get('/api/nodes/nl/desired').json()
    result = nodes._sync_desired_state_locked('nl', desired_state, _requester=_acknowledge_payload)
    assert result['desired_state_applied'] is True
    assert not nodes.desired_state('nl')['pending']
    assert {a['local_inbound_id'] for a in nodes.assignments('nl')} == before
    assert all(a['remote_inbound_id'] > 0 for a in nodes.assignments('nl'))
    assert state[0].db.execute('SELECT COUNT(*) FROM managed_clients').fetchone()[0] == 0


@pytest.mark.parametrize('mutation', ['disabled', 'deleted', 'other-node', 'unassigned-inbound', 'no-provider'])
def test_generated_relay_ack_does_not_bypass_assignment_guard(relay_env, mutation):
    state, nodes, _ = relay_env; store = state[0]; client = state[-1]
    swap, rid = setup_route(relay_env); swap.toggle(rid, True, ACK)
    desired_state = client.get('/api/nodes/nl/desired').json()
    def changed_while_applying(*args):
        with store.transaction() as db:
            if mutation == 'disabled':
                db.execute("UPDATE node_port_swaps SET enabled=0,phase='disabled' WHERE id=?", (rid,))
            elif mutation == 'deleted':
                db.execute('DELETE FROM node_port_swaps WHERE id=?', (rid,))
            elif mutation == 'other-node':
                db.execute("UPDATE node_port_swaps SET source_node='am' WHERE id=?", (rid,))
            elif mutation == 'unassigned-inbound':
                db.execute("DELETE FROM remote_node_inbounds WHERE node_id='nl' AND local_inbound_id=1")
            else:
                nodes.managed_assignment_sources = None
        return _acknowledge_payload(*args)
    with pytest.raises(PolicyError, match='Node assignments changed'):
        nodes._sync_desired_state_locked('nl', desired_state, _requester=changed_while_applying)
    assert nodes.desired_state('nl')['applied_revision'] != desired_state['revision']

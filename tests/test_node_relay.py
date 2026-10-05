import base64
import copy
import hashlib
import json
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

import nodes as nodes_module
from core import Config, CoreEngine
from dark_policy import PolicyError, Store
from node_runtime import NodeRuntime
from test_standalone import env, create, IB


@pytest.fixture
def relay_env(env, monkeypatch):
    store, engine, manager, auth, client = env
    private = X25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ib = copy.deepcopy(IB)
    ib.update(listen='0.0.0.0', tag='relay-ingress', port=24443,
              panelMeta={'tunnelPorts': {'node:nl': 24445}})
    ib['streamSettings'] = {'network': 'tcp', 'security': 'reality', 'realitySettings': {
        'dest': 'example.com:443', 'serverNames': ['example.com'], 'shortIds': ['ab12'],
        'privateKey': base64.urlsafe_b64encode(private).decode().rstrip('=')}}
    assert client.post('/api/inbounds', json=ib).status_code == 200
    ib['tag'] = 'relay-exit'; ib['port'] = 24444; ib['panelMeta'] = {}
    assert client.post('/api/inbounds', json=ib).status_code == 200
    monkeypatch.setattr(nodes_module.socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    nodes = client.app.state.nodes
    for name in ('nl', 'de', 'am'):
        nodes.put(name, name.upper(), 'https://' + name + '.example.test', 'dkn_' + 'A' * 60,
                  True, [1, 2], '93.184.216.34')
    return env, nodes, client.app.state.node_relays


def configured(f):
    env, nodes, relay = f
    c = env[-1]
    r = c.put('/api/nodes/nl/exits/1', json={'exitNodeId': 'de', 'exitInboundId': 2})
    assert r.status_code == 200, r.text
    return r.json()


def desired(c, node):
    r = c.get('/api/nodes/' + node + '/desired')
    assert r.status_code == 200, r.text
    return r.json()['payload']


def test_default_off_and_credentials_private(relay_env):
    env, nodes, relay = relay_env; store, engine, _, _, c = env
    before = desired(c, 'nl')
    public = configured(relay_env)
    assert public['enabled'] is False and public['phase'] == 'disabled'
    assert desired(c, 'nl') == before
    row = relay.get('nl', 1)
    secret = relay.cipher.decrypt(row['credential_enc'].encode()).decode()
    assert secret not in row['credential_enc']
    assert secret not in json.dumps(c.get('/api/nodes/nl/exits').json())
    assert row['identity'] not in json.dumps(public)
    assert store.db.execute('SELECT count(*) FROM managed_clients').fetchone()[0] == 0
    assert relay.cipher.decrypt(relay.get('nl', 1)['credential_enc'].encode()).decode() == secret


def test_exit_ack_before_source_and_failed_exit_not_enabled(relay_env, monkeypatch):
    env, nodes, relay = relay_env; c = env[-1]; configured(relay_env)
    calls = []
    def sync(node, state):
        calls.append((node, state['payload'], relay.get('nl', 1)['enabled']))
        return {'desired_state_applied': True, 'queued': False}
    monkeypatch.setattr(nodes, 'sync_desired_state', sync)
    result = c.post('/api/nodes/nl/exits/1', json={'enabled': True})
    assert result.status_code == 200, result.text
    assert [(n, enabled) for n, _, enabled in calls] == [('nl', 0), ('de', 0), ('nl', 1)]
    assert result.json()['phase'] == 'enabled'
    assert c.post('/api/nodes/nl/exits/1', json={'enabled': False}).status_code == 200
    monkeypatch.setattr(nodes, 'sync_desired_state', lambda *a: {'desired_state_applied': False, 'queued': True})
    assert c.post('/api/nodes/nl/exits/1', json={'enabled': True}).status_code == 400
    assert relay.get('nl', 1)['enabled'] == 0


def test_failed_source_disable_requires_ack_before_reconfiguration(relay_env):
    env, _, relay = relay_env; configured(relay_env)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    with pytest.raises(PolicyError, match='Source synchronization pending'):
        relay.toggle('nl', 1, False, lambda node: {'desired_state_applied': False})
    assert relay.get('nl', 1)['phase'] == 'disabling'
    with pytest.raises(PolicyError, match='Disable and synchronize'):
        relay.configure('nl', 1, 'am', 2)
    with pytest.raises(PolicyError, match='Disable and synchronize'):
        relay.check_node_change('de', deleting=True)
    bridge = desired(env[-1], 'de')['assignments'][1]['clients'][0]
    relay.toggle('nl', 1, False, lambda node: {'desired_state_applied': True})
    assert desired(env[-1], 'de')['assignments'][1]['clients'][0] == bridge
    relay.configure('nl', 1, 'am', 2)


def test_compile_encrypted_routing_shadow_and_exit_direct(relay_env, tmp_path, monkeypatch):
    monkeypatch.setattr(CoreEngine, 'validate', lambda self, config=None: {
        'validated': True, 'hash': self.config_hash(config or self.build_config())})
    env, _, relay = relay_env; store, engine, _, _, c = env; configured(relay_env)
    engine.save_section('outbounds', engine.section('outbounds') + [{'tag': 'warp', 'protocol': 'freedom', 'settings': {}}])
    engine.save_section('routing', {'domainStrategy': 'AsIs', 'rules': [
        {'type': 'field', 'domain': ['domain:blocked.example'], 'outboundTag': 'block'},
        {'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'warp'}]})
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    source = desired(c, 'nl'); exit_payload = desired(c, 'de')
    outbound = next(o for o in source['sections']['outbounds'] if o['protocol'] == 'vless')
    assert outbound['streamSettings']['security'] == 'reality'
    assert 'privateKey' not in json.dumps(outbound)
    assert source['sections']['routing']['rules'][0]['outboundTag'] == 'block'
    assert source['sections']['routing']['rules'][1]['outboundTag'] == outbound['tag']
    for node, payload in [('nl', source), ('de', exit_payload)]:
        db = Store(tmp_path/(node + '.sqlite3'))
        runtime_engine = CoreEngine(Config(test_engine=True, xray_binary='/missing', xray_assets=str(tmp_path)), db, tmp_path/node)
        runtime = NodeRuntime(db, runtime_engine, node)
        payload['desiredRunning'] = False
        _, digest = runtime._canonical(payload)
        runtime.apply({'revision': 1, 'hash': digest, 'payload': payload})
        config = runtime_engine.build_config()
        rules = config['routing']['rules']
        if node == 'nl':
            rule = next(r for r in rules if r.get('ruleTag') == outbound['tag'])
            assert len(rule['inboundTag']) == 2  # Includes real tunnel shadow listener.
        else:
            bridge = exit_payload['assignments'][1]['clients'][0]
            rule = next(r for r in rules if r.get('user') == [runtime.mirror_email(bridge['sourceEmail'])]
                        and r.get('outboundTag', '').endswith('-exit'))
            assert next(o for o in config['outbounds'] if o['tag'] == rule['outboundTag'])['protocol'] == 'freedom'
            assert rule['outboundTag'] != 'warp'
        runtime_engine.close(); db.close()


def test_missing_or_disabled_exit_fails_closed(relay_env):
    env, _, relay = relay_env; configured(relay_env)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    with env[0].transaction() as db: db.execute("UPDATE remote_nodes SET enabled=0 WHERE id='de'")
    payload = desired(env[-1], 'nl')
    rule = next(r for r in payload['sections']['routing']['rules'] if r.get('ruleTag', '').startswith('dark-relay-'))
    assert next(o for o in payload['sections']['outbounds'] if o['tag'] == rule['outboundTag'])['protocol'] == 'blackhole'


def test_reject_self_cycles_unsupported_and_unassigned(relay_env):
    env, nodes, relay = relay_env; configured(relay_env)
    for target in [('nl', 2), ('de', 999)]:
        with pytest.raises(PolicyError): relay.configure('nl', 1, *target)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    with pytest.raises(PolicyError, match='cycle'): relay.configure('de', 2, 'nl', 1)
    ib = env[1].inbound(2); ib['streamSettings']['security'] = 'none'
    ib['streamSettings'].pop('realitySettings'); env[1].save_inbound(ib, 2)
    with pytest.raises(PolicyError, match='VLESS Reality'): relay.configure('am', 1, 'de', 2)


def test_bridge_traffic_never_charged_twice_and_policy_remains(relay_env):
    env, nodes, relay = relay_env; configured(relay_env); c = env[-1]
    create(c, 'relay-customer', extra={'totalGB': 1024 * 1024, 'limitHwid': 2})
    row = relay.get('nl', 1)
    nodes.apply_traffic_snapshot('nl', [{'sourceEmail': 'relay-customer', 'up': 0, 'down': 0}])
    ingress = nodes.apply_traffic_snapshot('nl', [{'sourceEmail': 'relay-customer', 'up': 30, 'down': 70}])
    exit_result = nodes.apply_traffic_snapshot('de', [{'sourceEmail': row['identity'], 'up': 3000, 'down': 7000}])
    assert ingress['charged_bytes'] == 100
    assert exit_result['charged_bytes'] == 0 and exit_result['ignored_clients'] == 1
    payload = desired(c, 'nl')
    assert payload['security']['clients'][0]['sourceEmail'] == 'relay-customer'
    assert payload['security']['clients'][0]['limitHwid'] == 2
    assert all(x['sourceEmail'] != row['identity'] for x in desired(c, 'de')['security']['clients'])


def test_owner_session_required(relay_env, monkeypatch):
    env, _, _ = relay_env; c = env[-1]; configured(relay_env)
    auth = env[3]; original = auth.current
    from dataclasses import replace
    monkeypatch.setattr(auth, 'current', lambda *a, **kw: replace(original(*a, **kw), key_id='api-key'))
    assert c.get('/api/nodes/nl/exits').status_code == 403
    assert c.put('/api/nodes/nl/exits/1', json={'exitNodeId': 'de', 'exitInboundId': 2}).status_code == 403


def test_reseller_cannot_read_or_mutate_exits(relay_env, monkeypatch):
    from dataclasses import replace
    from dark_policy import Actor
    env, _, _ = relay_env; c = env[-1]
    auth = env[3]; original = auth.current
    monkeypatch.setattr(auth, 'current', lambda *a, **kw: replace(original(*a, **kw), actor=Actor('seller', 'reseller', {})))
    assert c.get('/api/nodes/nl/exits').status_code == 403
    assert c.post('/api/nodes/nl/exits/1', json={'enabled': True}).status_code == 403


def test_source_ack_loss_is_retryable_without_changing_credentials(relay_env):
    _, _, relay = relay_env; configured(relay_env)
    before = relay.get('nl', 1)['credential_enc']
    source_calls = 0
    def sync(node):
        nonlocal source_calls
        if node == 'nl':
            source_calls += 1
            if source_calls > 1: raise PolicyError('Injected lost source reply')
        return {'desired_state_applied': True}
    with pytest.raises(PolicyError, match='lost source'):
        relay.toggle('nl', 1, True, sync)
    assert relay.get('nl', 1)['phase'] == 'enabling'
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    assert relay.get('nl', 1)['phase'] == 'enabled'
    assert relay.get('nl', 1)['credential_enc'] == before


def test_legacy_source_cannot_publish_enabled_intent(relay_env):
    _, _, relay = relay_env; configured(relay_env)
    def sync(node):
        assert node == 'nl'
        raise PolicyError('Node HTTP 404')
    with pytest.raises(PolicyError, match='404'): relay.toggle('nl', 1, True, sync)
    assert relay.get('nl', 1)['enabled'] == 0
    assert relay.get('nl', 1)['phase'] == 'disabled'


def test_monitor_does_not_fall_back_to_mirrors_for_relay_payload(relay_env, monkeypatch):
    env, nodes, relay = relay_env; configured(relay_env)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    state = env[-1].get('/api/nodes/nl/desired').json()
    def reject(*a, **kw): raise PolicyError('Node HTTP 404')
    monkeypatch.setattr(nodes, '_request', reject)
    monkeypatch.setattr(nodes, 'sync_mirrors', lambda *a: pytest.fail('Mirror fallback would bypass the selected exit'))
    with pytest.raises(PolicyError, match='404'):
        nodes._sync_desired_state_locked('nl', state, legacy_bundles=state['payload']['assignments'])


def test_existing_allow_block_order_unchanged_for_other_customers(relay_env):
    env, _, relay = relay_env; configured(relay_env)
    rules = [
        {'type': 'field', 'domain': ['domain:exception.example'], 'outboundTag': 'direct'},
        {'type': 'field', 'domain': ['domain:example'], 'outboundTag': 'block'},
        {'type': 'field', 'inboundTag': ['relay-exit'], 'port': '25', 'outboundTag': 'block'},
        {'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'direct'}]
    env[1].save_section('routing', {'domainStrategy': 'AsIs', 'rules': rules})
    # Even a saved-but-disabled route must not reorder destination customers.
    exit_rules = desired(env[-1], 'de')['sections']['routing']['rules']
    assert exit_rules[-len(rules):] == rules
    assert all(r.get('user') for r in exit_rules[:-len(rules)])
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    source_rules = desired(env[-1], 'nl')['sections']['routing']['rules']
    assert source_rules[-len(rules):] == rules
    assert source_rules[0]['domain'] == ['domain:exception.example']
    assert source_rules[0]['outboundTag'].startswith('dark-relay-')
    assert source_rules[1]['outboundTag'] == 'block'
    assert all(set(r['inboundTag']) <= {'relay-ingress', 'dark-tunnel-1-24445'} for r in source_rules[:-len(rules)])
    assert not any(r.get('port') == '25' for r in source_rules[:-len(rules)])


def test_reserved_namespace_collision_rejected_before_persisting_route(relay_env):
    env, _, relay = relay_env
    env[1].save_section('outbounds', env[1].section('outbounds') + [
        {'tag': 'dark-relay-custom', 'protocol': 'freedom', 'settings': {}}])
    with pytest.raises(PolicyError, match='namespace is reserved'):
        relay.configure('nl', 1, 'de', 2)
    assert not relay.rows()


def test_bridge_terminates_before_exit_nodes_own_customer_route(relay_env):
    env, _, relay = relay_env; configured(relay_env)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    relay.configure('de', 2, 'am', 2)
    relay.toggle('de', 2, True, lambda node: {'desired_state_applied': True})
    rules = desired(env[-1], 'de')['sections']['routing']['rules']
    exit_index = next(i for i,r in enumerate(rules) if r.get('user') and r['outboundTag'].endswith('-exit'))
    source_index = next(i for i,r in enumerate(rules) if r.get('inboundTag') == ['relay-exit'])
    assert exit_index < source_index


def test_active_references_protected_through_upsert_and_deployment_api(relay_env):
    env, nodes, relay = relay_env; c = env[-1]; configured(relay_env)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    assert c.post('/api/nodes', json={'id': 'de', 'name': 'DE', 'origin': 'https://de.example.test',
        'token': 'dkn_' + 'A' * 60, 'enabled': False, 'inboundIds': [1, 2]}).status_code == 400
    assert bool(nodes.get('de')['enabled']) is True
    result = c.put('/api/inbounds/2/deployments', json={'local': True, 'nodeIds': ['nl', 'am'], 'tunnelPorts': {}})
    assert result.status_code == 400
    assert 2 in nodes.get('de')['inboundIds']


def test_removed_destination_assignment_compiles_blackhole(relay_env):
    env, nodes, relay = relay_env; c = env[-1]; configured(relay_env)
    relay.toggle('nl', 1, True, lambda node: {'desired_state_applied': True})
    with env[0].transaction() as db:
        db.execute("DELETE FROM remote_node_inbounds WHERE node_id='de' AND local_inbound_id=2")
    payload = desired(c, 'nl')
    assert any(o['protocol'] == 'blackhole' and o['tag'].startswith('dark-relay-') for o in payload['sections']['outbounds'])

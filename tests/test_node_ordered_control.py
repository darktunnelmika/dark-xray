"""Ordered Agent receipts: real SQLite/FastAPI, fake Xray; not WAN/power-loss proof.

The live Hub dispatcher is deliberately unchanged in this bounded checkpoint.
"""
from __future__ import annotations
import copy
import uuid

import pytest

from core import CoreError
from test_node_control_lifecycle import rebooted_agent, control
from test_node_hub_recovery import NODE, payload, post_state

ENDPOINT = '/node/api/v1/control'


def request(action='stop', revision=1, command_id=None):
    return {'nodeId': NODE, 'revision': revision,
            'commandId': command_id or uuid.uuid4().hex, 'action': action}


def send(client, body):
    response = client.post(ENDPOINT, json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('action', ['stop', 'start', 'restart'])
def test_exact_ack_and_duplicate_without_reexecution(tmp_path, monkeypatch, action):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        post_state(client, payload(engine))
        body = request(action)
        result = send(client, body)
        assert result['service'] == 'DARK XRAY NODE' and result['node_id'] == NODE
        assert result['revision'] == 1 and result['commandId'] == body['commandId']
        assert result['action'] == action and result['applied'] is True
        assert result['engine']['state'] == ('stopped' if action == 'stop' else 'running')
        pid = engine.process.pid if engine.running else None
        monkeypatch.setattr(engine, 'command', lambda *a, **k: pytest.fail('Duplicate executed Xray command'))
        again = send(client, body)
        assert again['duplicate'] is True and again['applied'] is True
        assert (engine.process.pid if engine.running else None) == pid
        assert runtime.command_status()['phase'] == 'applied'


@pytest.mark.parametrize('field,value', [
    ('revision', 0), ('revision', -1), ('revision', True), ('revision', 1.0),
    ('revision', '1'), ('revision', 2**63), ('commandId', ''),
    ('commandId', 'not-a-uuid'), ('nodeId', 'other-node'),
    ('action', 'validate'), ('action', []), ('extra', 'unexpected'),
])
def test_invalid_envelope_has_no_side_effect(tmp_path, monkeypatch, field, value):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        body = request();body[field] = value
        before = runtime.control_status()
        monkeypatch.setattr(engine, 'command', lambda *a, **k: pytest.fail('Invalid command executed'))
        response = client.post(ENDPOINT, json=body)
        assert response.status_code == 422, response.text
        assert not runtime.command_status()['persisted'] and runtime.control_status() == before


@pytest.mark.parametrize('conflict', ['older', 'identity', 'action', 'reused-identity'])
def test_older_or_conflicting_identity_cannot_change_control(tmp_path, conflict):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        body = request('stop', 4);send(client, body)
        other = copy.deepcopy(body)
        if conflict == 'older': other = request('start', 3)
        elif conflict == 'identity': other['commandId'] = uuid.uuid4().hex
        elif conflict == 'action': other['action'] = 'start'
        else: other['revision'] = 5
        receipt = runtime.command_status();before = runtime.control_status()
        response = client.post(ENDPOINT, json=other)
        assert response.status_code == 409, response.text
        assert runtime.command_status() == receipt and runtime.control_status() == before
        assert not engine.running


@pytest.mark.parametrize('action', ['start', 'restart'])
def test_failed_newer_resume_still_fences_old_command_and_can_retry(tmp_path, monkeypatch, action):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        post_state(client, payload(engine));old = request();send(client, old)
        body = request(action, 2);spawn = engine._spawn
        def fail():raise CoreError('Injected spawn failure', status=503)
        monkeypatch.setattr(engine, '_spawn', fail)
        response = client.post(ENDPOINT, json=body)
        assert response.status_code == 503, response.text
        receipt = runtime.command_status()
        assert receipt['revision'] == 2 and receipt['phase'] == 'failed' and receipt['last_error']
        assert runtime.control_status()['manual_stop'] and not engine.wants_running
        assert client.post(ENDPOINT, json=old).status_code == 409
        # A configuration revision cannot reinterpret the failed action as Start.
        post_state(client, payload(engine), 2)
        assert not engine.running
        monkeypatch.setattr(engine, '_spawn', spawn)
        assert send(client, body)['applied'] and engine.running


def test_failed_stop_keeps_traffic_safety_and_retries_same_identity(tmp_path, monkeypatch):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, loop):
        post_state(client, payload(engine));collect = engine.collect_stats;body = request()
        def fail(*a, **k):raise CoreError('Injected final-counter failure', status=503)
        monkeypatch.setattr(engine, 'collect_stats', fail)
        response = client.post(ENDPOINT, json=body)
        assert response.status_code == 503, response.text
        assert runtime.command_status()['phase'] == 'failed'
        assert runtime.control_status()['manual_stop'] and not engine.wants_running
        assert engine.running, 'Unsafe stop discarded unsaved traffic'
        monkeypatch.setattr(engine, 'collect_stats', collect)
        loop.tick();assert not engine.running
        result = send(client, body)
        assert result['applied'] and result['commandId'] == body['commandId']


@pytest.mark.parametrize('action', ['stop', 'restart'])
def test_committed_receipt_survives_agent_recreation(tmp_path, monkeypatch, action):
    root = tmp_path/'node';body = request(action)
    with rebooted_agent(root) as (_, engine, runtime, client, _):
        post_state(client, payload(engine));send(client, body)
        receipt = runtime.command_status()
    with rebooted_agent(root, background=True) as (_, engine, runtime, client, _):
        assert runtime.command_status() == receipt
        assert engine.running == (action != 'stop')
        with monkeypatch.context() as patch:
            patch.setattr(runtime, '_command_locked', lambda *a, **k: pytest.fail('Committed command replayed'))
            assert send(client, body)['duplicate']
        assert client.post(ENDPOINT, json=request('start', 0)).status_code == 422


@pytest.mark.parametrize('action,stale_running', [('start', False), ('stop', True)])
def test_config_cannot_reverse_ordered_run_intent(tmp_path, action, stale_running):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, loop):
        body = payload(engine);body['desiredRunning'] = stale_running
        post_state(client, body);command = request(action);send(client, command)
        update = copy.deepcopy(body);update['sections']['dns'] = {'servers': ['8.8.8.8']}
        post_state(client, update, 2);loop.tick()
        assert engine.running == (action == 'start')
        assert runtime.command_status()['command_id'] == command['commandId']
        post_state(client, update, 2)  # Duplicate desired-state delivery.
        assert engine.running == (action == 'start')


def test_failed_config_rollback_keeps_receipt_and_ordered_stop(tmp_path, monkeypatch):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        body = payload(engine);post_state(client, body);send(client, request())
        receipt = runtime.command_status();before = runtime.control_status()
        def fail(*a, **k):raise CoreError('Injected config/Guard failure', status=503)
        monkeypatch.setattr(runtime, '_sync_guard_ports', fail)
        update = copy.deepcopy(body);update['sections']['dns'] = {'servers': ['8.8.8.8']}
        from test_node_hub_recovery import envelope
        response = client.post('/node/api/v1/state/apply', json=envelope(update, 2))
        assert response.status_code == 422, response.text
        assert runtime.command_status() == receipt and runtime.control_status() == before
        assert not engine.running


def test_auth_readonly_and_health_boundaries(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        body = request();before = runtime.control_status()
        response = client.post(ENDPOINT, json=body, headers={'Authorization': ''})
        assert response.status_code == 401
        engine.config.writes_enabled = False
        assert client.post(ENDPOINT, json=body).status_code == 409
        assert not runtime.command_status()['persisted'] and runtime.control_status() == before
        engine.config.writes_enabled = True
        send(client, body)
        health = client.get('/node/api/health').json()
        state = client.get('/node/api/v1/state').json()
        assert health['capabilities']['ordered_control'] == 1
        assert health['control_receipt']['command_id'] == body['commandId']
        assert state['control_receipt'] == health['control_receipt']
        assert client.get('/node/api/v1/state', headers={'Authorization': ''}).status_code == 401


def test_legacy_control_remains_until_ordered_handoff_then_cannot_bypass_fence(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        post_state(client, payload(engine));control(client, 'stop');control(client, 'start')
        body = request();send(client, body);receipt = runtime.command_status()
        for action in ('start', 'stop', 'restart'):
            response = client.post('/node/api/core/'+action, json={})
            assert response.status_code == 409, response.text
        control(client, 'validate')
        assert runtime.command_status() == receipt and not engine.running
        send(client, request('start', 2));assert engine.running


def test_duplicate_receipt_does_not_report_running_when_child_is_down(tmp_path, monkeypatch):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, loop):
        post_state(client, payload(engine));body = request('restart');send(client, body)
        engine.process.kill();engine.process.wait(timeout=3)
        with monkeypatch.context() as patch:
            patch.setattr(runtime, '_command_locked', lambda *a, **k: pytest.fail('Duplicate replayed'))
            result = send(client, body)
        assert result['duplicate'] and result['applied'] is False and result['engine']['state'] == 'stopped'
        engine.last_start = 0;loop.tick();assert engine.running
        assert send(client, body)['applied']


def test_concurrent_duplicate_requests_execute_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        post_state(client, payload(engine));body = request('restart')
        execute = runtime._command_locked;calls = []
        def counted(action):calls.append(action);return execute(action)
        monkeypatch.setattr(runtime, '_command_locked', counted)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(runtime.ordered_command, [body, body, body]))
        assert calls == ['restart'] and sum(result['duplicate'] for result in results) == 2
        assert all(result['applied'] for result in results)


@pytest.mark.parametrize('action', ['stop', 'restart'])
def test_hub_component_retries_lost_ack_through_real_agent_api(tmp_path, monkeypatch, action):
    from auth import Auth
    from dark_policy import Store, PolicyError
    from nodes import NodeRegistry
    from node_commands import NodeCommands
    import nodes as node_module
    from test_node_hub_recovery import ORIGIN, TOKEN
    monkeypatch.setattr(node_module, 'validate_origin', lambda origin: origin)
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, _):
        post_state(client, payload(engine))
        root = tmp_path/'hub';root.mkdir()
        store = Store(root/'hub.sqlite3');auth = Auth(store, root/'secret.key')
        registry = NodeRegistry(store, auth.cipher);registry.put(NODE, 'Node', ORIGIN, TOKEN)
        commands = NodeCommands(registry);commands.record(NODE, action)
        try:
            def offline(*a, **k):raise PolicyError('Injected network outage')
            monkeypatch.setattr(registry, '_request', offline)
            assert commands.deliver(NODE)['queued'] and not runtime.command_status()['persisted']
            expected = commands.status(NODE)['command_id']
            registry.close();store.close()
            store = Store(root/'hub.sqlite3');auth = Auth(store, root/'secret.key')
            registry = NodeRegistry(store, auth.cipher);commands = NodeCommands(registry)
            assert commands.status(NODE)['command_id'] == expected
            def transport(node_id, path, method, body, timeout):
                response = client.request(method, path, json=body)
                if response.status_code != 200:raise PolicyError(response.text)
                return response.json(), 1
            def lost_reply(*a, **k):transport(*a, **k);raise OSError('Injected lost acknowledgement')
            monkeypatch.setattr(registry, '_request', lost_reply)
            assert commands.deliver(NODE)['queued']
            assert runtime.command_status()['phase'] == 'applied'
            with monkeypatch.context() as patch:
                patch.setattr(registry, '_request', transport)
                patch.setattr(runtime, '_command_locked', lambda *a, **k: pytest.fail('Lost ACK replayed command'))
                result = commands.deliver(NODE)
                assert not result['queued'] and result['control']['applied_revision'] == 1
        finally:
            registry.close();store.close()

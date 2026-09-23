"""Harness checks; none of these is a real-Xray acceptance claim."""
import contextlib

import httpx
import pytest
from fastapi import FastAPI

import node_automatic_lab as lab
from test_node_automatic_limits import eventually, serving, listener_socket


def test_http_serving_starts_and_stops_real_lifespan():
    events = []
    @contextlib.asynccontextmanager
    async def lifecycle(app):
        events.append('start')
        yield
        events.append('stop')
    app = FastAPI(lifespan=lifecycle)
    @app.get('/test')
    def endpoint():
        return {'observed':True}
    sock = listener_socket(); port = sock.getsockname()[1]
    with serving(app, sock):
        with httpx.Client(trust_env=False, timeout=2) as http:
            result = http.get(f'http://127.0.0.1:{port}/test')
        assert result.status_code == 200 and result.json() == {'observed':True}
        assert events == ['start']
    assert events == ['start', 'stop']


def test_wait_timeout_is_not_success():
    with pytest.raises(AssertionError, match='Automatic convergence timed out'):
        eventually(lambda: False, 'never-ready', .01)


def test_wait_does_not_swallow_programming_errors():
    def bad():
        raise ValueError('broken predicate')
    with pytest.raises(ValueError, match='broken predicate'):
        eventually(bad, 'bad predicate')


def test_bootstrap_refuses_host_before_any_command(monkeypatch):
    def reject():
        raise RuntimeError('Refusing host')
    monkeypatch.setattr(lab, 'check_namespace', reject)
    monkeypatch.setattr(lab.subprocess, 'run', lambda *a, **k: pytest.fail('command ran before namespace check'))
    with pytest.raises(RuntimeError, match='Refusing host'):
        lab.main()


def test_bootstrap_requires_root_before_any_command(monkeypatch):
    monkeypatch.setattr(lab, 'check_namespace', lambda: None)
    monkeypatch.setattr(lab.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(lab.subprocess, 'run', lambda *a, **k: pytest.fail('non-root command'))
    with pytest.raises(RuntimeError, match='requires root'):
        lab.main()


def test_bootstrap_preserves_failure_and_runs_only_fixed_suite(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, 'check_namespace', lambda: calls.append('checked'))
    monkeypatch.setattr(lab.os, 'geteuid', lambda: 0)
    def run(args, **kwargs):
        calls.append(args)
        return type('Result', (), {'returncode':7})()
    monkeypatch.setattr(lab.subprocess, 'run', run)
    assert lab.main() == 7
    assert calls[0] == 'checked'
    assert calls[1] == ['ip', 'link', 'set', 'lo', 'up']
    assert calls[2:4] == [['ip','address','add',ip+'/32','dev','lo'] for ip in lab.SOURCES]
    assert calls[4][1:4] == ['-m', 'pytest', 'tests/test_node_automatic_limits.py']
    assert len(calls) == 5


# These checks validate the observer; malformed/pending receipts cannot be
# promoted to success merely by accepting executed=False in the HTTP response.
import copy
import threading
from test_node_automatic_limits import stop_request_identity, stop_acknowledged
from test_hub_node_control_live import hub, bridge, command
from test_node_control_lifecycle import rebooted_agent
from test_node_hub_recovery import NODE, payload, post_state


def stop_documents(state='executed'):
    control = {'persisted':True, 'revision':1, 'command_id':'a'*32, 'action':'stop',
               'desired_running':False, 'pending':state == 'pending',
               'applied_revision':0 if state == 'pending' else 1, 'last_error':''}
    response = {'control':control, 'executed':state == 'executed',
                'queued':state == 'pending', 'delivery_state':state}
    if state == 'acknowledged':
        response['already_applied'] = True
    receipt = {'persisted':True, 'revision':1, 'command_id':'a'*32, 'action':'stop',
               'phase':'applied', 'pending':False, 'last_error':''}
    return response, receipt


@pytest.mark.parametrize('state', ['executed', 'acknowledged', 'pending'])
def test_stop_acceptance_does_not_confuse_pending_with_completion(state):
    response, receipt = stop_documents(state)
    expected = stop_request_identity(response)
    assert stop_acknowledged(expected, response['control'], receipt, False) is (state != 'pending')


@pytest.mark.parametrize('part,field,value', [
    ('response','delivery_state','superseded'), ('response','executed',False),
    ('response','queued',1), ('control','command_id',''),
    ('control','revision',True), ('control','action','start'),
    ('control','persisted',False), ('control','applied_revision',True),
    ('control','last_error','not acknowledged'),
])
def test_stop_request_rejects_incoherent_or_superseded_response(part, field, value):
    response, _ = stop_documents()
    (response if part == 'response' else response['control'])[field] = value
    with pytest.raises(AssertionError):
        stop_request_identity(response)


def test_acknowledged_response_requires_explicit_already_applied_marker():
    response, _ = stop_documents('acknowledged')
    response.pop('already_applied')
    with pytest.raises(AssertionError):
        stop_request_identity(response)


@pytest.mark.parametrize('part,field,value', [
    ('hub','command_id','b'*32), ('agent','command_id','b'*32),
    ('hub','revision',2), ('agent','revision',True),
    ('hub','pending',True), ('agent','pending',True),
    ('hub','last_error','failed'), ('agent','last_error','failed'),
    ('hub','applied_revision',0), ('hub','applied_revision',True),
    ('hub','desired_running',True), ('agent','phase','applying'),
    ('hub','persisted',False), ('agent','persisted',False),
    ('hub','action','start'), ('agent','action','start'),
])
def test_completion_requires_both_exact_successful_receipts(part, field, value):
    response, receipt = stop_documents()
    expected = stop_request_identity(response)
    (response['control'] if part == 'hub' else receipt)[field] = value
    assert not stop_acknowledged(expected, response['control'], receipt, False)


@pytest.mark.parametrize('running', [True, None, 0])
def test_historical_stop_receipt_does_not_prove_current_stopped_core(running):
    response, receipt = stop_documents()
    assert not stop_acknowledged(stop_request_identity(response), response['control'], receipt, running)


@pytest.mark.parametrize('mode', ['api-delivery', 'monitor-wins', 'lost-response'])
def test_stop_observation_with_real_monitor_and_agent_receipts(hub, tmp_path, monkeypatch, mode):
    """Real API/SQLite/monitor; deterministic scheduling and fixture Xray, not WAN."""
    reg, http, _ = hub
    with rebooted_agent(tmp_path/'stop-agent') as (_, engine, runtime, agent_http, _):
        post_state(agent_http, payload(engine))
        transport = bridge(reg, agent_http)
        control_posts = []; effects = []
        real_effect = runtime._command_locked
        def effect(action):
            effects.append(action)
            return real_effect(action)
        monkeypatch.setattr(runtime, '_command_locked', effect)
        def request(node, path, method='GET', body=None, timeout=8.0):
            result = transport(node, path, method, body, timeout)
            if path == '/node/api/v1/control':
                control_posts.append(copy.deepcopy(body))
                if mode == 'lost-response' and len(control_posts) == 1:
                    raise OSError('Injected response loss after Agent acknowledgement')
            return result
        monkeypatch.setattr(reg, '_request', request)
        reg.probe(NODE)
        record_calls = []
        real_record = reg.commands.record
        finished = threading.Event()
        real_deliver = reg.deliver_pending_control
        def deliver(node):
            result = real_deliver(node)
            if threading.current_thread() is reg.thread and result.get('executed'):
                finished.set()
            return result
        def record(node, action):
            saved = real_record(node, action); record_calls.append(saved['command_id'])
            if mode == 'monitor-wins':
                reg.start(interval=60, initial_delay=0)
                assert finished.wait(10), 'Monitor did not deliver the recorded command'
            return saved
        monkeypatch.setattr(reg, 'deliver_pending_control', deliver)
        monkeypatch.setattr(reg.commands, 'record', record)
        try:
            response = command(http, 'stop')
            expected = stop_request_identity(response)
            def completed():
                return stop_acknowledged(expected, reg.commands.status(NODE),
                                         runtime.command_status(), engine.running)
            if mode == 'monitor-wins':
                # The old E2E assertion fails here despite a completed Stop.
                assert response['executed'] is False
                assert response['already_applied'] is True and completed()
            elif mode == 'lost-response':
                assert response['queued'] is True and not completed()
                assert not engine.running, 'Receipt loss should happen after the actual Stop'
                reg.start(interval=60, initial_delay=0)
            else:
                assert response['executed'] is True and completed()
            eventually(completed, 'exact Stop receipts', 10)
            assert len(record_calls) == 1 and effects == ['stop']
            assert all(body['commandId'] == expected['command_id'] and
                       body['revision'] == expected['revision'] for body in control_posts)
            assert len(control_posts) == (2 if mode == 'lost-response' else 1)
        finally:
            reg.close()

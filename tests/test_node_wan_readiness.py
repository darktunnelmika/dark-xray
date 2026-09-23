"""WAN gate regressions: fixture reports are not real WAN or Xray evidence."""
import copy
import contextlib
import importlib.util
import threading
from pathlib import Path

import pytest
from dark_policy import PolicyError

ROOT = Path(__file__).resolve().parents[1]


def gate_module(path=None):
    spec = importlib.util.spec_from_file_location('wan_readiness_gate', path or ROOT/'tools/node-wan-gate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Registry:
    """Small explicit registry contract; integration cases use the real registry."""
    def __init__(self):
        self.store = type('Store', (), {'lock': threading.RLock()})()
        self.installations = type('Installations', (), {'operation': lambda self, node: contextlib.nullcontext()})()
        self.calls = []
        self.row = {
            'id': 'n1', 'name': 'Fixture', 'enabled': 1, 'origin': 'https://node.example.test',
            'failover_enabled': 1, 'failover_ready': True, 'data_address': 'node.example.test', 'priority': 10,
            'installation': {'binding_id': 'b'*32, 'generation': 1, 'agent_id': 'n1', 'installation_id': 'a'*32},
            'desired_state': {'revision': 2, 'desired_hash': 'c'*64, 'applied_revision': 2,
                              'applied_hash': 'c'*64, 'pending': False, 'last_error': ''},
            'control': {'persisted': False, 'revision': 0, 'command_id': '', 'action': '',
                        'pending': False, 'desired_running': True, 'last_error': '', 'applied_revision': 0},
            'assignments': [{'local_inbound_id': 1, 'remote_inbound_id': 7, 'last_error': ''}],
        }
        self.health = {'service': 'DARK XRAY NODE', 'agent_only': True, 'node_id': 'n1',
            'installation_id': 'a'*32, 'capabilities': {'installation_identity': 1},
            'core': {'state': 'running', 'dirty': False, 'last_error': ''},
            'maintenance': {'last_error': ''},
            'desired_state': {'appliedRevision': 2, 'appliedHash': 'c'*64, 'lastError': ''},
            'control_receipt': {'persisted': False},
            'run_control': {'manual_stop': False, 'effective_running': True}}
        self.traffic = {'items': [{'sourceEmail': 'alice', 'up': 1, 'down': 2}]}
        self.security = {'sourceVerified': True, 'items': [{'sourceEmail': 'alice', 'ips': [], 'devices': []}]}
        self.remote = {'items': [{'id': 7}]}
        self.after_request = lambda path: None

    def list(self):
        return [copy.deepcopy(self.row)]

    def probe(self, node_id, timeout=8.0):
        self.calls.append(('/node/api/health', 'GET'))
        self.after_request('/node/api/health')
        return {'latency_ms': 11, 'health': copy.deepcopy(self.health)}

    def _request(self, node_id, path, method='GET', body=None, timeout=8.0):
        self.calls.append((path, method)); self.after_request(path)
        if path == '/node/api/mirrors/traffic': return copy.deepcopy(self.traffic), 12
        if path == '/node/api/mirrors/security': return copy.deepcopy(self.security), 13
        if path == '/node/api/inbounds': return copy.deepcopy(self.remote['items']), 14
        raise AssertionError(path)

    def remote_inbounds(self, node_id):
        items, ms = self._request(node_id, '/node/api/inbounds')
        return {'items': items, 'latency_ms': ms}


def inspect(reg):
    return gate_module().inspect_node(reg, copy.deepcopy(reg.row), 5.0)


def test_ready_reports_pass_and_no_mutating_endpoint_is_called():
    reg = Registry(); result = inspect(reg)
    assert result['ok'] is True, result
    assert all(method == 'GET' for _, method in reg.calls)
    assert result['failover_ready'] is True


@pytest.mark.parametrize('section,value', [
    ('core', {'state': 'stopped', 'dirty': False, 'last_error': ''}),
    ('core', {'state': 'running', 'dirty': True, 'last_error': ''}),
    ('core', {'state': 'running', 'dirty': False, 'last_error': 'failed'}),
    ('core', {}), ('core', None),
    ('maintenance', {'last_error': 'guard reconciliation failed'}),
    ('run_control', {'manual_stop': True, 'effective_running': False}),
    ('desired_state', {'appliedRevision': 1, 'appliedHash': 'c'*64, 'lastError': ''}),
    ('desired_state', {'appliedRevision': 2, 'appliedHash': 'd'*64, 'lastError': ''}),
    ('desired_state', {'appliedRevision': 2, 'appliedHash': 'c'*64, 'lastError': 'failed'}),
])
def test_reachable_agent_is_not_sufficient(section, value):
    reg = Registry(); reg.health[section] = value
    assert inspect(reg)['ok'] is False


@pytest.mark.parametrize('field,value', [
    ('enabled', 0), ('assignments', []),
    ('assignments', [{'local_inbound_id': 1, 'remote_inbound_id': 0, 'last_error': ''}]),
    ('desired_state', {'revision': 2, 'desired_hash': 'c'*64, 'applied_revision': 1,
                       'applied_hash': 'c'*64, 'pending': True, 'last_error': ''}),
    ('control', {'persisted': True, 'pending': True, 'action': 'restart', 'desired_running': True}),
    ('control', {'persisted': True, 'pending': False, 'action': 'stop', 'desired_running': False}),
])
def test_hub_intent_and_assignments_must_be_ready(field, value):
    reg = Registry(); reg.row[field] = value
    assert inspect(reg)['ok'] is False


@pytest.mark.parametrize('field,value', [('installation_id', 'd'*32), ('node_id', 'other')])
def test_health_must_belong_to_the_pinned_installation(field, value):
    reg = Registry(); reg.health[field] = value
    assert inspect(reg)['ok'] is False


def test_report_does_not_copy_remote_exception_secrets():
    reg = Registry()
    def failure(path):
        raise PolicyError('Bearer dkn_PRIVATE_TOKEN response: secret.key password=secret')
    reg.after_request = failure
    result = inspect(reg)
    assert result['ok'] is False
    assert 'PRIVATE_TOKEN' not in str(result) and 'password=secret' not in str(result)


def test_old_assignment_must_still_exist_on_the_agent():
    reg = Registry(); reg.remote = {'items': [{'id': 8}]}
    assert inspect(reg)['ok'] is False


@pytest.mark.parametrize('field,value', [('enabled', 0), ('origin', 'https://new.example.test'),
    ('data_address', 'new.example.test'), ('failover_enabled', 0),
    ('desired_state', {'revision': 3, 'desired_hash': 'd'*64})])
def test_mid_probe_hub_change_invalidates_observation(field, value):
    reg = Registry()
    reg.after_request = lambda path: reg.row.update({field: value}) if path.endswith('/security') else None
    assert inspect(reg)['ok'] is False


def test_core_stopping_after_first_probe_is_not_reported_ready():
    reg = Registry()
    reg.after_request = lambda path: reg.health['core'].update(state='stopped') if path.endswith('/security') else None
    assert inspect(reg)['ok'] is False


def test_failover_flag_alone_is_not_failover_readiness():
    reg = Registry(); reg.row['failover_ready'] = False
    result = inspect(reg)
    assert result['ok'] is True
    assert result['failover_ready'] is False


@pytest.mark.parametrize('sequence,expected', [
    ([False, True], False), ([True, False, True], True),
    ([True, True], False), ([False, False], False), ([True, False], False)])
def test_recovery_requires_a_ready_baseline(sequence, expected):
    gate = gate_module()
    state = {'saw_ready_before': False, 'saw_down': False, 'saw_recovered': False, 'down_at': 0, 'recovered_at': 0}
    for at, ready in enumerate(sequence, 1): gate.advance_recovery(state, ready, at)
    assert state['saw_recovered'] is expected


@pytest.mark.parametrize('flag', ['--watch-seconds', '--interval', '--timeout'])
@pytest.mark.parametrize('value', ['nan', 'inf'])
def test_nonfinite_cli_times_rejected_before_opening_config(monkeypatch, flag, value):
    import sys
    gate = gate_module()
    monkeypatch.setattr(sys, 'argv', ['node-wan-gate', '--config', '/missing', '--data', '/missing', flag, value])
    with pytest.raises(SystemExit): gate.main()


def test_private_report_refuses_symlink_and_nonfinite_data(tmp_path):
    gate = gate_module(); target = tmp_path/'real'; target.write_text('preserve')
    link = tmp_path/'link'; link.symlink_to(target)
    with pytest.raises(PolicyError): gate.atomic_report(link, {'passed': True})
    assert target.read_text() == 'preserve'
    with pytest.raises(ValueError): gate.atomic_report(tmp_path/'nan', {'value': float('nan')})
    assert not (tmp_path/'nan').exists()


@pytest.mark.parametrize('mode', ['matching', 'old-id', 'boolean-revision', 'unknown-command'])
def test_ordered_control_receipt_is_checked(mode):
    reg = Registry()
    command = {'revision': 1, 'command_id': 'd'*32, 'action': 'start', 'persisted': True,
               'pending': False, 'desired_running': True, 'last_error': '', 'applied_revision': 1}
    reg.row['control'] = command
    reg.health['control_receipt'] = {**command, 'phase': 'applied'}
    if mode == 'old-id': reg.health['control_receipt']['command_id'] = 'e'*32
    if mode == 'boolean-revision': reg.health['control_receipt']['revision'] = True
    if mode == 'unknown-command': reg.row['control'].update(persisted=False)
    assert inspect(reg)['ok'] is (mode == 'matching')


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'invalid', 'source-unverified'])
def test_endpoint_observations_and_ip_guard_boundary(mode):
    reg = Registry()
    if mode == 'missing': reg.traffic = {}
    elif mode == 'duplicate': reg.remote = {'items': [{'id': 7}, {'id': 7}]}
    elif mode == 'invalid': reg.security = {'sourceVerified': 1, 'items': []}
    else: reg.security['sourceVerified'] = False
    result = inspect(reg)
    assert result['ok'] is (mode == 'source-unverified')
    if mode == 'source-unverified': assert result['source_verified'] is False


# Real Hub, Agent API handlers, SQLite, intent and counter storage. The socket
# transport and Xray fixture are not claims about an external network.
from test_hub_node_control_live import hub
from test_node_hub_recovery import NODE, agent, payload
from test_node_installations import http_transport, sql_rows, seed_account


@pytest.mark.parametrize('mode', ['ready', 'stopped', 'pending-config', 'retired'])
def test_gate_against_real_hub_and_agent_preserves_customer_state(hub, tmp_path, monkeypatch, mode):
    reg, owner, engine = hub
    seed_account(reg, owner, engine)
    with agent(tmp_path/'remote') as (remote_store, remote_engine, runtime, client):
        calls = http_transport(reg, client, monkeypatch)
        reg.probe(NODE)
        reg.set_desired_state(NODE, payload(engine))
        reg.sync_desired_state(NODE, reg.desired_state(NODE))
        if mode == 'stopped': runtime.command('stop')
        if mode == 'pending-config':
            newer = payload(engine); newer['sections']['dns'] = {'servers': ['8.8.8.8']}
            reg.set_desired_state(NODE, newer)
        baseline = reg.list()[0]
        tables = ('clients', 'managed_clients', 'remote_node_client_usage', 'remote_node_inbounds',
                  'remote_node_desired_state', 'remote_node_control')
        before = {table: sql_rows(reg, table) for table in tables}
        remote_before = {table: [tuple(x) for x in remote_store.db.execute('SELECT * FROM '+table)]
                         for table in ('core_clients', 'node_runtime_state', 'node_runtime_control')}
        pid = remote_engine.process.pid if remote_engine.process else None
        calls.clear()
        if mode == 'retired':
            # Retire the pinned identity between endpoint reads, without using
            # the gate to perform replacement. Its entire observation must fail.
            original = reg._request
            def request(node, path, *args, **kwargs):
                response = original(node, path, *args, **kwargs)
                if path.endswith('/security'):
                    with reg.store.transaction() as db:
                        db.execute('UPDATE remote_node_installations SET binding_id=? WHERE node_id=? AND retired_at=0', ('f'*32, NODE))
                return response
            monkeypatch.setattr(reg, '_request', request)
        result = gate_module().inspect_node(reg, baseline, 5.0)
        assert result['ok'] is (mode == 'ready'), result
        assert all(method == 'GET' for method, _, _ in calls)
        assert {table: sql_rows(reg, table) for table in tables} == before
        assert {table: [tuple(x) for x in remote_store.db.execute('SELECT * FROM '+table)]
                for table in remote_before} == remote_before
        assert (remote_engine.process.pid if remote_engine.process else None) == pid


@pytest.mark.parametrize('mode,passed', [('ready', True), ('flag-only', False), ('empty', False), ('fleet-change', False)])
def test_cli_pass_uses_verified_failover_and_explains_limits(tmp_path, monkeypatch, capsys, mode, passed):
    import json
    import sys
    gate = gate_module(); reg = Registry()
    reg.store.close = lambda: None
    reg.close = lambda: None
    if mode == 'flag-only': reg.row['failover_ready'] = False
    if mode == 'empty': reg.list = lambda: []
    if mode == 'fleet-change':
        original = reg.list
        def add_node(path):
            if path.endswith('/security'):
                reg.list = lambda: original()+[{**copy.deepcopy(reg.row), 'id': 'n2'}]
        reg.after_request = add_node
    data = tmp_path/'data'; data.mkdir()
    (data/'dark.sqlite3').touch(); (data/'secret.key').touch()
    monkeypatch.setattr(gate.Config, 'load', lambda path: None)
    monkeypatch.setattr(gate, 'Store', lambda path: reg.store)
    monkeypatch.setattr(gate, 'Auth', lambda *a: type('Auth', (), {'cipher': None})())
    monkeypatch.setattr(gate, 'NodeRegistry', lambda *a: reg)
    monkeypatch.setattr(sys, 'argv', ['wan', '--config', str(tmp_path/'config'), '--data', str(data),
                                    '--min-nodes', '1', '--json-only'])
    with pytest.raises(SystemExit) as done: gate.main()
    report = json.loads(capsys.readouterr().out)
    assert done.value.code == (0 if passed else 1)
    assert report['passed'] is passed
    assert report['customer_connection_tested'] is False
    assert report['global_ip_guard_tested'] is False
    assert report['network_outage_proven'] is False
    assert report['network_loss_injected_by_gate'] is False
    if mode == 'flag-only': assert report['failover_ready_nodes'] == 0
    if mode == 'empty': assert report['real_wan_requests'] is False
    if mode == 'fleet-change': assert report['fleet_changed'] is True
    assert (data/'qa/node-wan-gate.json').is_file()

import json
import os
import socket
import time

from fastapi.testclient import TestClient

from auth import Auth
from core import Config, CoreEngine
from dark_policy import Store
from node_agent import AgentToken, make_agent_app
from nodes import NodeRegistry
import nodes as nodes_mod


def _public_dns(monkeypatch):
    monkeypatch.setattr(
        nodes_mod.socket,
        'getaddrinfo',
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))],
    )


def _health(cpu=72.0):
    return {
        'service': 'DARK XRAY NODE',
        'version': '0.10.0-rc19',
        'installed_source': {'commit': 'abc', 'ref': 'abc'},
        'core': {'state': 'running', 'version': '26.3.27', 'dirty': False, 'last_error': ''},
        'system': {
            'cpu': cpu,
            'cpu_info': {'logical': 4},
            'loads': [3.5, 2.0, 1.0],
            'memory': {'used': 80, 'total': 100, 'percent': 80.0},
            'disk': {'used': 60, 'total': 100, 'free': 40, 'percent': 60.0},
            'network': {'sent': 10, 'recv': 20, 'up_bps': 30.0, 'down_bps': 40.0},
            'connections': {'open': 12, 'tcp': 10, 'udp': 2, 'available': True},
            'uptime': 1234,
        },
        'hub_lease': {'required': True, 'valid': True, 'state': 'active'},
        'maintenance': {'statistics_error': ''},
    }


def test_live_telemetry_uses_last_health_probe_not_generic_last_seen(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    store = Store(tmp_path / 'hub.sqlite3')
    auth = Auth(store, tmp_path / 'secret.key')
    registry = NodeRegistry(store, auth.cipher)
    registry.monitor_interval = 5
    registry.put('n1', 'Node One', 'https://node.example', 'dkn_' + ('A' * 60), True)

    now = time.time()
    with store.transaction() as db:
        db.execute(
            "UPDATE remote_nodes SET last_seen=?,last_health_at=?,last_health=?,last_error='' WHERE id='n1'",
            (now, now, json.dumps(_health())),
        )
    live = registry.list()[0]
    assert live['telemetry']['fresh'] is True
    assert live['telemetry']['system']['cpu'] == 72.0
    assert live['telemetry']['capacity'] == {'score': 87.5, 'status': 'busy'}

    # Logs/update/other successful Agent calls may refresh last_seen. That must
    # never resurrect an old resource sample as live.
    with store.transaction() as db:
        db.execute(
            "UPDATE remote_nodes SET last_seen=?,last_health_at=? WHERE id='n1'",
            (time.time(), time.time() - 20),
        )
    stale = registry.list()[0]
    assert stale['online'] is True  # routing compatibility remains unchanged
    assert stale['telemetry']['fresh'] is False
    assert stale['telemetry']['system'] is None
    assert stale['telemetry']['live_state'] == 'stale'
    assert any(x['code'] == 'telemetry_stale' for x in stale['telemetry']['alerts'])
    store.close()


def test_capacity_and_resource_alerts_only_use_fresh_health(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    store = Store(tmp_path / 'hub.sqlite3')
    auth = Auth(store, tmp_path / 'secret.key')
    registry = NodeRegistry(store, auth.cipher)
    registry.monitor_interval = 5
    registry.put('n1', 'Node One', 'https://node.example', 'dkn_' + ('B' * 60), True)
    now = time.time()
    with store.transaction() as db:
        db.execute(
            "UPDATE remote_nodes SET last_seen=?,last_health_at=?,last_health=? WHERE id='n1'",
            (now, now, json.dumps(_health(cpu=98.0))),
        )
    node = registry.list()[0]
    assert node['telemetry']['capacity']['status'] == 'overloaded'
    assert any(x['code'] == 'cpu_high' and x['severity'] == 'critical'
               for x in node['telemetry']['alerts'])
    store.close()


def test_agent_health_exports_full_system_and_diagnostics_skip_tunnel_health(tmp_path):
    token_value = 'dkn_' + ('C' * 60)
    token_path = tmp_path / 'token'
    token_path.write_text(token_value + '\n', encoding='utf-8')
    os.chmod(token_path, 0o600)
    store = Store(tmp_path / 'node.sqlite3')
    cfg = Config(
        xray_binary=str(tmp_path / 'missing'),
        xray_assets=str(tmp_path),
        test_engine=True,
        core_autostart=False,
    )
    engine = CoreEngine(cfg, store, tmp_path / 'runtime')
    app = make_agent_app(engine, store, AgentToken(token_path), 'node-live-01', background=False)
    with TestClient(app, base_url=cfg.public_origin) as client:
        headers = {'authorization': 'Bearer ' + token_value}
        response = client.get('/node/api/health', headers=headers)
        assert response.status_code == 200, response.text
        system = response.json()['system']
        for key in (
            'cpu', 'cpu_info', 'memory', 'disk', 'swap', 'uptime', 'loads',
            'network', 'connections', 'addresses', 'agent', 'xray',
        ):
            assert key in system

        response = client.get('/node/api/v1/diagnostics', headers=headers)
        assert response.status_code == 200, response.text
        doc = response.json()
        ids = {row['id'] for row in doc['checks']}
        expected = {
            'agent', 'xray', 'hub_lease', 'lease_watchdog',
            'accounting_checkpoint', 'cpu', 'memory', 'disk',
            'network', 'update_broker',
        }
        assert expected <= ids
        assert not any('tunnel' in value.lower() for value in ids)
        assert doc['boundary'].endswith('tunnel health is not tested')
    store.close()


def test_hub_diagnostics_proxy_validates_agent_contract(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    store = Store(tmp_path / 'hub.sqlite3')
    auth = Auth(store, tmp_path / 'secret.key')
    registry = NodeRegistry(store, auth.cipher)
    registry.put('n1', 'Node One', 'https://node.example', 'dkn_' + ('D' * 60), True)
    monkeypatch.setattr(
        registry,
        '_request',
        lambda *a, **k: (
            {
                'service': 'DARK XRAY NODE',
                'node_id': 'n1',
                'generated_at': time.time(),
                'checks': [
                    {'id': 'agent', 'label': 'Node Agent', 'status': 'pass', 'detail': 'ok', 'value': 'rc19'},
                ],
                'boundary': 'local Agent/Xray/accounting/system diagnostics; tunnel health is not tested',
            },
            7,
        ),
    )
    result = registry.diagnostics('n1')
    assert result['latency_ms'] == 7
    assert result['checks'][0]['id'] == 'agent'
    assert not any('tunnel' in row['id'].lower() for row in result['checks'])
    store.close()

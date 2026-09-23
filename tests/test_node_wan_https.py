"""Two real TLS listeners + production pinned HTTPS client + Agent handlers.

Only DNS fixture origins map to loopback and only the disposable test CA is
trusted. No TLS verification is disabled. Xray is a fixture, not customer traffic
or provider WAN acceptance. Fault injection closes the fixture's HTTPS sockets.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import shutil
import socket
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from datetime import datetime, timedelta, timezone

import nodes as nodes_module
from auth import Auth
from core import Config, CoreEngine
from dark_policy import PolicyError, Store, Actor
from node_agent import AgentToken, make_agent_app
from nodes import NodeRegistry
from manager import Manager
from test_node_hub_recovery import TOKEN, CLIENT_UUID, free_port, inbound, payload
from test_node_wan_readiness import gate_module

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def tls_material(tmp_path_factory):
    root = tmp_path_factory.mktemp('wan-tls'); now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'DARK disposable test CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), True)
          .sign(key, hashes.SHA256()))
    ca_path = root/'ca.pem'; ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths = []
    for number in (1, 2):
        host = f'node{number}.example.test'; private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
                .issuer_name(name).public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(private.public_key()), False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, None, None), True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
                .sign(key, hashes.SHA256()))
        cert_path, key_path = root/f'{number}.pem', root/f'{number}.key'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())); key_path.chmod(0o600)
        paths.append((host, cert_path, key_path))
    return ca_path, paths


class WireFaults:
    """Test-only ASGI adapter: faults happen on real network responses."""
    def __init__(self, app):
        self.app = app; self.down = False; self.bad_identity = False
        self.delay = 0.; self.calls = []

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        self.calls.append((scope['method'], scope['path']))
        if self.down:
            # Returning without HTTP response causes Uvicorn to close the socket.
            transport = scope.get('_fixture_transport')
            if transport is not None: transport.abort()
            return
        if self.delay: await asyncio.sleep(self.delay)
        async def wrapped(message):
            if self.bad_identity and message['type'] == 'http.response.start':
                message = dict(message)
                message['headers'] = [(k, b'wrong-install' if k.lower() == b'x-dark-installation-id' else v)
                                      for k, v in message.get('headers', [])]
            await send(message)
        await self.app(scope, receive, wrapped)


# Add a test-only transport handle to implement an actual TCP abort rather than
# synthesizing a successful/failed JSON health response.
from uvicorn.protocols.http.h11_impl import H11Protocol
class FaultProtocol(H11Protocol):
    def handle_events(self):
        original = self.app
        async def attach(scope, receive, send):
            scope['_fixture_transport'] = self.transport
            await original(scope, receive, send)
        self.app = attach
        try: return super().handle_events()
        finally: self.app = original


@contextlib.contextmanager
def tls_agent(root, node_id, material):
    root.mkdir(); host, cert, key = material
    listener = socket.socket(); listener.bind(('127.0.0.1', 0))
    origin = f'https://{host}:{listener.getsockname()[1]}'
    fake = root/'fake-xray'; shutil.copy2(ROOT/'tests/fixtures/fake_xray.py', fake); fake.chmod(0o755)
    cfg = Config(public_origin=origin, public_address=host, secure_cookie=True,
                 xray_binary=str(fake), xray_assets=str(root), xray_api_port=free_port(),
                 core_autostart=False, test_engine=True)
    store = Store(root/'node.sqlite3'); engine = CoreEngine(cfg, store, root/'runtime')
    token_path = root/'token'; token_path.write_text(TOKEN+'\n'); token_path.chmod(0o600)
    app = make_agent_app(engine, store, AgentToken(token_path), node_id, background=False)
    wire = WireFaults(app)
    server = uvicorn.Server(uvicorn.Config(wire, ssl_certfile=str(cert), ssl_keyfile=str(key),
        log_level='critical', access_log=False, ws='none', http=FaultProtocol, timeout_graceful_shutdown=2))
    errors = []
    def run():
        try: server.run(sockets=[listener])
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=run, name='fixture-'+node_id); thread.start()
    try:
        deadline = time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline: time.sleep(.01)
        assert server.started, errors
        yield SimpleNamespace(id=node_id, origin=origin, store=store, engine=engine,
                              runtime=app.state.runtime, wire=wire, server=server)
    finally:
        server.should_exit = True; thread.join(5); listener.close()
        engine.close(); store.close()
        assert not thread.is_alive() and not errors, errors


@pytest.fixture
def fleet(tmp_path, monkeypatch, tls_material):
    ca, materials = tls_material
    context = ssl.create_default_context(cafile=str(ca))
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    # Scope the disposable CA to this module reference, not process/global trust.
    monkeypatch.setattr(nodes_module, 'ssl', SimpleNamespace(create_default_context=lambda: context,
        SSLError=ssl.SSLError, SSLContext=ssl.SSLContext))
    with contextlib.ExitStack() as stack:
        agents = [stack.enter_context(tls_agent(tmp_path/f'agent{i}', f'wan-{i}', material))
                  for i, material in enumerate(materials, 1)]
        origins = {node.origin: node for node in agents}
        real_resolve = nodes_module.resolve_origin
        def resolve(origin):
            if origin not in origins: return real_resolve(origin)
            p = urlsplit(origin)
            return origin, p.hostname, p.port, ('127.0.0.1',)
        monkeypatch.setattr(nodes_module, 'resolve_origin', resolve)
        root = tmp_path/'hub'; root.mkdir(); store = Store(root/'dark.sqlite3')
        cfg = Config(test_engine=True, core_autostart=False, xray_binary=str(root/'missing'), xray_assets=str(root))
        config_path = root/'config.json'; config_path.write_text(json.dumps(dataclasses.asdict(cfg))); config_path.chmod(0o600)
        engine = CoreEngine(cfg, store, root/'runtime'); manager = Manager(store, engine)
        auth = Auth(store, root/'secret.key')
        reg = NodeRegistry(store, auth.cipher)
        try:
            iid = engine.save_inbound(inbound())['id']
            actor=Actor('dark','owner',{})
            manager.owner_put(actor,'dark',name='Fixture owner',allowed=[iid])
            with manager.lock:
                manager._stage_create_locked(actor,'dark',{'email':'alice','id':CLIENT_UUID,
                    'totalGB':100*1024**3,'expiryTime':2000000000000},[iid],set(),set())
            for node in agents:
                reg.put(node.id, node.id, node.origin, TOKEN, True, [iid], urlsplit(node.origin).hostname)
                reg.probe(node.id)
                body = payload(engine, source_id=iid); body['nodeId'] = node.id
                body['assignments'][0]['inbound']['port'] = free_port()
                reg.set_desired_state(node.id, body)
                result = reg.sync_desired_state(node.id, reg.desired_state(node.id))
                assert result['desired_state_applied'] is True, result
                reg.probe(node.id)
                reg.apply_traffic_snapshot(node.id,[{'sourceEmail':'alice','up':0,'down':0}])
                reg.apply_traffic_snapshot(node.id,[{'sourceEmail':'alice','up':10,'down':25}])
                with node.store.transaction() as db:
                    db.execute('UPDATE core_clients SET up=123,down=456')
                node.wire.calls.clear()
            yield SimpleNamespace(reg=reg, agents=agents, data=root, config=config_path, context=context)
        finally:
            reg.close(); engine.close(); store.close()


def snapshot(fleet):
    with fleet.reg.store.lock:
        hub_tables = ('remote_node_desired_state', 'remote_node_control', 'remote_node_inbounds',
                      'remote_node_client_usage', 'clients', 'managed_clients', 'traffic_ledger')
        result = {table: [tuple(r) for r in fleet.reg.store.db.execute('SELECT * FROM '+table)] for table in hub_tables}
    result['agents'] = []
    for node in fleet.agents:
        with node.store.lock:
            result['agents'].append({'pid': node.engine.process.pid,
                **{table: [tuple(r) for r in node.store.db.execute('SELECT * FROM '+table)]
                   for table in ('core_clients', 'node_runtime_control', 'node_runtime_state')}})
    return result


def test_two_tls_agents_ready_without_customer_or_runtime_mutation(fleet):
    gate = gate_module(); before = snapshot(fleet)
    results = [gate.inspect_node(fleet.reg, row, 5.0) for row in fleet.reg.list()]
    assert all(r['ok'] and r['failover_ready'] for r in results), (results, [(r['id'],r['last_error']) for r in fleet.reg.list()])
    assert snapshot(fleet) == before
    assert all(method == 'GET' for a in fleet.agents for method, _ in a.wire.calls)
    assert all(len(a.wire.calls)==5 for a in fleet.agents)
    assert results[0]['installation']['installation_id'] != results[1]['installation']['installation_id']


def test_tls_socket_outage_and_recovery_isolated_from_other_node(fleet):
    gate = gate_module(); before = snapshot(fleet); reg = fleet.reg; first, other = fleet.agents
    states = {a.id: {'saw_ready_before': False, 'saw_down': False, 'saw_recovered': False,
                    'down_at': 0, 'recovered_at': 0} for a in fleet.agents}
    for step in range(3):
        first.wire.down = step == 1
        results = [gate.inspect_node(reg, row, 5.0) for row in reg.list()]
        by_id = {r['id']: r for r in results}
        assert by_id[other.id]['ok'] is True
        assert by_id[first.id]['ok'] is (step != 1), by_id
        if step==1:
            assert by_id[first.id]['agent_reachable'] is False
            assert 'RemoteDisconnected' in next(r for r in reg.list() if r['id']==first.id)['last_error']
        for row in results: gate.advance_recovery(states[row['id']], row['ok'], step+1)
    assert states[first.id]['saw_recovered'] and not states[other.id]['saw_down']
    assert snapshot(fleet) == before
    assert all(method == 'GET' for a in fleet.agents for method, _ in a.wire.calls)


@pytest.mark.parametrize('failure', ['untrusted-ca', 'hostname', 'identity', 'wrong-token', 'slow-response'])
def test_https_rejection_never_marks_node_ready(fleet, monkeypatch, failure):
    gate = gate_module(); node = fleet.agents[0]; row = fleet.reg.list()[0]; before = snapshot(fleet)
    if failure == 'untrusted-ca':
        # A clean default trust store has no disposable CA.
        monkeypatch.setattr(nodes_module.ssl, 'create_default_context', ssl.create_default_context)
    elif failure == 'hostname':
        resolve = nodes_module.resolve_origin
        def mismatch(origin):
            origin, host, port, addresses = resolve(origin)
            return origin, 'wrong-host.example.test', port, addresses
        monkeypatch.setattr(nodes_module, 'resolve_origin', mismatch)
    elif failure == 'identity': node.wire.bad_identity = True
    elif failure == 'wrong-token':
        with fleet.reg.store.transaction() as db:
            db.execute('UPDATE remote_nodes SET token_enc=? WHERE id=?',
                (fleet.reg.cipher.encrypt(('dkn_'+'Z'*60).encode()).decode(), node.id))
    else: node.wire.delay = .8
    result = gate.inspect_node(fleet.reg, row, .2 if failure=='slow-response' else 5.0)
    assert result['ok'] is False and result['failover_ready'] is False, result
    assert 'dkn_' not in str(result)
    last_error=fleet.reg.list()[0]['last_error']
    if failure in ('untrusted-ca','hostname'): assert 'SSLCertVerificationError' in last_error
    elif failure in ('identity','wrong-token'): assert 'identity mismatch' in last_error
    elif failure=='slow-response': assert 'Timeout' in last_error
    assert snapshot(fleet) == before


def test_production_origin_policy_still_rejects_loopback_literals():
    # No fleet fixture or DNS substitution: exercise the unmodified production policy.
    with pytest.raises(PolicyError, match='globally routable'):
        nodes_module.resolve_origin('https://127.0.0.1:9443')


def test_child_watch_uses_real_https_recovery_with_stable_hub_intent(fleet, monkeypatch, capsys):
    import sys
    gate = gate_module(); first = fleet.agents[0]; before = snapshot(fleet)
    # Faults are triggered after full rounds, not by replacing inspect_node or HTTP.
    original = gate.fleet_signature; rounds = []
    def between_rounds(reg):
        current = original(reg); rounds.append(len(rounds))
        first.wire.down = len(rounds) == 1
        return current
    monkeypatch.setattr(gate, 'fleet_signature', between_rounds)
    monkeypatch.setattr(sys, 'argv', ['gate', '--config', str(fleet.config), '--data', str(fleet.data),
        '--min-nodes', '2', '--expected-node-count', '2', '--timeout', '5', '--watch-seconds', '8',
        '--interval', '.05', '--expect-outage', first.id, '--json-only'])
    with pytest.raises(SystemExit) as done: gate.main()
    doc = json.loads(capsys.readouterr().out)
    assert done.value.code == 0 and doc['passed'] is True, doc
    assert doc['observation_complete'] is True and doc['recovery'][first.id]['saw_recovered']
    assert doc['customer_connection_tested'] is False and doc['network_outage_proven'] is False
    assert snapshot(fleet) == before
    assert all(method == 'GET' for a in fleet.agents for method, _ in a.wire.calls)

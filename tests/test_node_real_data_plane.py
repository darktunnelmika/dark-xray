"""Opt-in real-Xray, two-Agent data-plane acceptance; never production hosts.

Run through node-real-data-plane.yml, or set DARK_REAL_XRAY_BINARY to a core
installed by tools/fetch-core.py. Hub HTTP handlers use TestClient; Agent traffic
uses the real pinned HTTPS client, and customer traffic uses real SOCKS/VLESS.
Only the two exact disposable HTTPS origins map to loopback. No fake Xray,
firewall changes, external target, real WAN, or transparent session-migration
claim is involved. The private VLESS data links intentionally have no TLS.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import http.server
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from fastapi.testclient import TestClient

import nodes as nodes_module
from auth import Auth
from core import Config, CoreEngine
from dark_policy import Actor, Store, PolicyError
from manager import Manager
from node_agent import AgentToken, make_agent_app
from server import make_app
from test_node_hub_recovery import TOKEN, CLIENT_UUID, free_port, inbound
from test_node_wan_https import tls_material, WireFaults, FaultProtocol

ROOT = Path(__file__).resolve().parents[1]
EMAIL = 'real-multinode-user'
PASSWORD = 'Only-Disposable-Fixture-Password!'
MARKER = b'DARK-MULTINODE-REAL-DATA:'
PADDING = b'x' * 65536


@pytest.fixture(scope='module')
def real_binary():
    supplied = os.environ.get('DARK_REAL_XRAY_BINARY', '')
    if not supplied:
        pytest.fail('This opt-in suite requires DARK_REAL_XRAY_BINARY; no fake/skip fallback')
    binary = Path(supplied).resolve(strict=True)
    assert binary.is_file() and os.access(binary, os.X_OK)
    provenance = json.loads((binary.parent/'DARK-CORE-PROVENANCE.json').read_text())
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert provenance['tag'] == 'v26.3.27'
    assert provenance['asset'] == 'Xray-linux-64.zip'
    assert provenance['origin'] == 'https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip'
    assert re.fullmatch(r'[0-9a-f]{64}', provenance['archive_sha256'])
    assert provenance['binary_sha256'] == digest
    version = subprocess.run([str(binary), 'version'], check=True, capture_output=True,
                             text=True, timeout=10).stdout.splitlines()[0]
    assert version.startswith('Xray ') and not any(x in version.upper() for x in ('FAKE', 'MOCK', 'TEST-DOUBLE'))
    return SimpleNamespace(path=binary, digest=digest, version=version, provenance=provenance)


@contextlib.contextmanager
def real_agent(root, node_id, material, binary):
    root.mkdir(mode=0o700)
    host, cert, key = material
    listener = socket.socket(); listener.bind(('127.0.0.1', 0))
    origin = f'https://{host}:{listener.getsockname()[1]}'
    cfg = Config(public_origin=origin, public_address=host, secure_cookie=True,
                 xray_binary=str(binary), xray_assets=str(binary.parent),
                 xray_api_port=free_port(), core_autostart=False, test_engine=False)
    store = Store(root/'node.sqlite3'); engine = CoreEngine(cfg, store, root/'runtime')
    token_path = root/'token'; token_path.write_text(TOKEN+'\n'); token_path.chmod(0o600)
    app = make_agent_app(engine, store, AgentToken(token_path), node_id, background=False)
    wire = WireFaults(app)
    server = uvicorn.Server(uvicorn.Config(wire, ssl_certfile=str(cert), ssl_keyfile=str(key),
        log_level='critical', access_log=False, ws='none', http=FaultProtocol, timeout_graceful_shutdown=2))
    errors = []
    def serve():
        try: server.run(sockets=[listener])
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=serve, name='real-data-'+node_id); thread.start()
    try:
        deadline = time.monotonic()+8
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.02)
        assert server.started, errors
        yield SimpleNamespace(id=node_id, origin=origin, engine=engine, store=store,
                              runtime=app.state.runtime, wire=wire)
    finally:
        server.should_exit = True; thread.join(8); listener.close()
        try: engine.close()
        finally: store.close()
        assert not thread.is_alive() and not errors, errors


class Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        nonce = self.path.removeprefix('/')
        if not re.fullmatch(r'[0-9a-f]{32}', nonce):
            self.send_error(400); return
        body = MARKER + nonce.encode() + PADDING
        with self.server.hit_lock: self.server.hits.append(nonce)
        self.send_response(200); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass


@contextlib.contextmanager
def target_server():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Target)
    server.hits = []; server.hit_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':.05})
    thread.start()
    try: yield server
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        assert not thread.is_alive()


class DataPlaneError(RuntimeError): pass


def exact(sock, count):
    data = b''
    while len(data) < count:
        chunk = sock.recv(count-len(data))
        if not chunk: raise DataPlaneError('unexpected SOCKS EOF')
        data += chunk
    return data


def transfer(socks_port, target, *, allowed=True):
    """Unique body and target-hit assertion prevent cached or bypass successes."""
    nonce = uuid.uuid4().hex
    expected = MARKER + nonce.encode() + PADDING
    success = False
    try:
        with socket.create_connection(('127.0.0.1', socks_port), timeout=3) as sock:
            sock.settimeout(3); sock.sendall(b'\x05\x01\x00')
            if exact(sock, 2) != b'\x05\x00': raise DataPlaneError('SOCKS negotiation failed')
            sock.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', target.server_port))
            reply = exact(sock, 4)
            if reply[:3] != b'\x05\x00\x00': raise DataPlaneError('SOCKS connect rejected')
            if reply[3] == 1: exact(sock, 6)
            elif reply[3] == 4: exact(sock, 18)
            elif reply[3] == 3: exact(sock, exact(sock, 1)[0]+2)
            else: raise DataPlaneError('invalid SOCKS address')
            sock.sendall(f'GET /{nonce} HTTP/1.1\r\nHost: fixture.test\r\nConnection: close\r\n\r\n'.encode())
            response = http.client.HTTPResponse(sock); response.begin()
            received = response.read(len(expected)+1)
            success = response.status == 200 and received == expected
    except (OSError, http.client.HTTPException, DataPlaneError):
        success = False
    with target.hit_lock: reached = nonce in target.hits
    if allowed:
        assert success and reached, 'real SOCKS -> VLESS -> HTTP transfer failed'
    else:
        assert not success and not reached, 'rejected client reached the HTTP target'
    return len(expected)


@contextlib.contextmanager
def xray_client(root, binary, uri, *, wrong_uuid=False):
    root.mkdir(mode=0o700)
    parsed = urlsplit(uri); query = parse_qs(parsed.query)
    assert parsed.scheme == 'vless' and parsed.hostname == '127.0.0.1'
    assert parsed.username == CLIENT_UUID
    assert query.get('security') == ['none'] and query.get('type') == ['tcp']
    port = free_port()
    config = {'log':{'loglevel':'warning'},
        'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'auth':'noauth','udp':False}}],
        'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':parsed.hostname,'port':parsed.port,
            'users':[{'id':str(uuid.uuid4()) if wrong_uuid else parsed.username,'encryption':'none'}]}]},
            'streamSettings':{'network':'tcp','security':'none'}}]}
    # There is deliberately no direct outbound, route fallback, or proxy environment use.
    path = root/'client.json'; path.write_text(json.dumps(config)); path.chmod(0o600)
    subprocess.run([str(binary), 'run', '-test', '-config', str(path)], capture_output=True, check=True, timeout=10)
    with (root/'client.log').open('wb') as log:
        proc = subprocess.Popen([str(binary), 'run', '-config', str(path)], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+8
            while True:
                assert proc.poll() is None, 'real Xray client exited'
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1): break
                except OSError:
                    if time.monotonic() >= deadline: raise
                    time.sleep(.02)
            yield SimpleNamespace(port=port, process=proc)
        finally:
            if proc.poll() is None: proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)


@pytest.fixture
def real_fleet(tmp_path, monkeypatch, tls_material, real_binary):
    ca, materials = tls_material
    trust = ssl.create_default_context(cafile=str(ca))
    assert trust.check_hostname and trust.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(nodes_module, 'ssl', SimpleNamespace(create_default_context=lambda:trust,
        SSLError=ssl.SSLError, SSLContext=ssl.SSLContext))
    with contextlib.ExitStack() as stack:
        agents = [stack.enter_context(real_agent(tmp_path/f'agent{i}', f'real-{i}', material, real_binary.path))
                  for i, material in enumerate(materials, 1)]
        origins = {node.origin for node in agents}; resolve = nodes_module.resolve_origin
        def fixture_resolve(origin):
            if origin not in origins: return resolve(origin)
            parsed = urlsplit(origin)
            return origin, parsed.hostname, parsed.port, ('127.0.0.1',)
        monkeypatch.setattr(nodes_module, 'resolve_origin', fixture_resolve)
        root = tmp_path/'hub'; root.mkdir(mode=0o700)
        store = Store(root/'dark.sqlite3'); stack.callback(store.close)
        cfg = Config(core_autostart=False, test_engine=False, xray_binary=str(real_binary.path),
                     xray_assets=str(real_binary.path.parent), xray_api_port=free_port())
        engine = CoreEngine(cfg, store, root/'runtime'); stack.callback(engine.close)
        manager = Manager(store, engine); stack.callback(manager.close)
        auth = Auth(store, root/'secret.key'); auth.bootstrap('dark', PASSWORD)
        app = make_app(manager, auth, background=False); reg = app.state.nodes
        http = stack.enter_context(TestClient(app, base_url=cfg.public_origin))
        def api(path, body=None, method=None):
            response = http.request(method or ('POST' if body is not None else 'GET'), path, json=body)
            assert 200 <= response.status_code < 300, (path, response.status_code, response.text[:600])
            return response.json()
        http.headers['X-Dark-CSRF'] = api('/api/auth/login', {'username':'dark','password':PASSWORD})['csrf']
        iids = []; hosts = []
        for index, node in enumerate(agents, 1):
            item = inbound(free_port()); item.update(tag=f'real-{index}', remark=f'REAL NODE {index}',
                                                     panelMeta={'deployLocal':False})
            iid = api('/api/inbounds', item)['id']; iids.append(iid); node.data_port = item['port']
            reg.put(node.id, node.id, node.origin, TOKEN, True, [iid], urlsplit(node.origin).hostname)
            reg.probe(node.id)
            hosts.append({'inboundId':iid,'address':'127.0.0.1','port':node.data_port,
                          'remark':node.id,'runtime':'node:'+node.id,'enable':True})
        manager.owner_put(Actor('dark','owner',{}), 'dark', name='Fixture', allowed=iids)
        api('/api/clients', {'owner':'dark','client':{'email':EMAIL,'id':CLIENT_UUID,'limitIp':0,
            'limitHwid':0,'totalGB':100*1024**3,'expiryTime':int((time.time()+86400*30)*1000)},'inboundIds':iids})
        api('/api/settings/hosts', {'value':hosts}, 'PUT')
        for node in agents:
            result = api('/api/nodes/'+node.id+'/sync', {})
            assert result['desired_state_applied'] is True, result
            reg.probe(node.id)
        link_doc = api('/api/clients/'+EMAIL+'/links')
        subscription = link_doc['subscription_url']
        def links():
            response = http.get(subscription+'?format=raw')
            assert response.status_code == 200, response.text
            return {urlsplit(line).port:line for line in response.text.splitlines() if line.strip()}
        raw = links(); assert set(raw) == {n.data_port for n in agents}, raw
        target = stack.enter_context(target_server())
        clients = [stack.enter_context(xray_client(tmp_path/f'client{i}', real_binary.path, raw[node.data_port]))
                   for i, node in enumerate(agents)]
        fixture = SimpleNamespace(agents=agents, clients=clients, reg=reg, store=store, engine=engine,
            api=api, links=links, target=target, root=tmp_path, binary=real_binary, uris=raw, evidence={})
        try: yield fixture
        finally:
            report = {'binary_sha256':real_binary.digest,'binary_version':real_binary.version,
                'real_xray':True,'agent_https':True,'hub_http':'in-process TestClient',
                'provider_wan_tested':False,'global_ip_guard_tested':False,
                'transparent_session_migration_tested':False,**fixture.evidence}
            out = ROOT/'qa/node-real-data-plane'; out.mkdir(parents=True, exist_ok=True)
            name = re.sub(r'[^a-zA-Z0-9_.-]', '_', os.environ.get('PYTEST_CURRENT_TEST','case').split(' ')[0])
            # Stage observations are not test success; JUnit is authoritative.
            (out/(name+'.json')).write_text(json.dumps(report, indent=2)+'\n')


def stable_identity(fleet):
    client = fleet.engine.client_detail(EMAIL)
    return {k:client['client'].get(k) for k in ('id','email','totalGB','expiryTime')} | {'inboundIds':client['inboundIds']}


def usage(fleet):
    with fleet.store.lock:
        total = fleet.store.db.execute('SELECT used_bytes FROM clients WHERE id=?', (EMAIL,)).fetchone()[0]
        rows = [tuple(r) for r in fleet.store.db.execute(
            'SELECT node_id,current_up,current_down FROM remote_node_client_usage WHERE client_id=? ORDER BY node_id', (EMAIL,))]
        ledger = sum(r[0] for r in fleet.store.db.execute('SELECT up_bytes+down_bytes FROM traffic_ledger WHERE client_id=?', (EMAIL,)))
    return int(total), rows, int(ledger)


def meter(fleet):
    for node in fleet.agents:
        node.engine.collect_stats(force=True, strict=True)
        fleet.api('/api/nodes/'+node.id+'/traffic', {})
    return usage(fleet)


def test_two_nodes_route_generated_links_and_meter_real_bytes(real_fleet):
    f = real_fleet; identity = stable_identity(f); initial = usage(f)
    assert initial[0] == 0 and len(initial[1]) == 2
    received = [transfer(client.port, f.target) for client in f.clients]
    total, rows, ledger = meter(f)
    assert total >= sum(received) and ledger == total
    assert all(up > 0 and down >= received[i] for i, (_, up, down) in enumerate(rows))
    assert total == sum(up+down for _, up, down in rows)
    assert meter(f) == (total, rows, ledger), 'same real counters charged twice'
    assert stable_identity(f) == identity
    f.evidence.update(transferred_bytes=sum(received), charged_bytes=total, duplicate_snapshot_unchanged=True)


@pytest.mark.parametrize('index', [0, 1])
def test_wrong_uuid_cannot_bypass_real_vless(real_fleet, index):
    f = real_fleet
    with xray_client(f.root/'wrong-client', f.binary.path, f.uris[f.agents[index].data_port], wrong_uuid=True) as bad:
        transfer(bad.port, f.target, allowed=False)
    transfer(f.clients[index].port, f.target)
    f.evidence['wrong_uuid_rejected_on'] = f.agents[index].id


def test_hub_stop_start_filters_links_and_preserves_real_usage(real_fleet):
    f = real_fleet; first, other = f.agents; identity = stable_identity(f)
    for client in f.clients: transfer(client.port, f.target)
    before = meter(f); other_pid = other.engine.process.pid
    assert f.api('/api/nodes/'+first.id+'/core/stop', {})['executed'] is True
    transfer(f.clients[0].port, f.target, allowed=False)
    transfer(f.clients[1].port, f.target)
    assert other.engine.process.pid == other_pid
    assert set(f.links()) == {other.data_port}
    assert f.api('/api/nodes/'+first.id+'/core/start', {})['executed'] is True
    f.reg.probe(first.id)
    assert set(f.links()) == {first.data_port, other.data_port}
    transfer(f.clients[0].port, f.target)
    after = meter(f)
    assert after[0] > before[0] and after[2] == after[0]
    old = {n:(up,down) for n,up,down in before[1]}
    assert all(up >= old[n][0] and down >= old[n][1] for n,up,down in after[1])
    assert stable_identity(f) == identity and other.engine.process.pid == other_pid
    f.evidence.update(hub_stop_start=True, charged_before=before[0], charged_after=after[0],
                      manual_new_connection_on_surviving_node=True)


def test_management_outage_is_distinct_from_customer_data_plane(real_fleet):
    f = real_fleet; first, other = f.agents
    before = stable_identity(f); pids = [a.engine.process.pid for a in f.agents]
    first.wire.down = True
    try:
        with pytest.raises(PolicyError, match='RemoteDisconnected'):
            f.reg.probe(first.id)
        f.reg.probe(other.id)
        assert set(f.links()) == {other.data_port}
        # Loss of management reachability is not a firewall/data-plane stop.
        for client in f.clients: transfer(client.port, f.target)
    finally: first.wire.down = False
    f.reg.probe(first.id)
    assert set(f.links()) == {a.data_port for a in f.agents}
    assert [a.engine.process.pid for a in f.agents] == pids
    assert stable_identity(f) == before
    f.evidence['management_outage_did_not_stop_customer_connections'] = True


def test_manual_client_disable_blocks_both_real_nodes(real_fleet):
    f = real_fleet; before = stable_identity(f)
    for client in f.clients: transfer(client.port, f.target)
    paid = meter(f)[0]
    f.api('/api/clients/'+EMAIL+'/action', {'action':'disable'})
    for node in f.agents: f.api('/api/nodes/'+node.id+'/sync', {})
    for client in f.clients: transfer(client.port, f.target, allowed=False)
    assert stable_identity(f) == before and usage(f)[0] >= paid
    f.evidence['explicit_client_disable_enforced_on_both_nodes'] = True


def test_same_configuration_sync_does_not_restart_either_real_core(real_fleet):
    f = real_fleet; pids = [a.engine.process.pid for a in f.agents]
    for client in f.clients: transfer(client.port, f.target)
    before = meter(f); identity = stable_identity(f)
    for node in f.agents: f.api('/api/nodes/'+node.id+'/sync', {})
    assert [a.engine.process.pid for a in f.agents] == pids
    assert usage(f) == before and stable_identity(f) == identity
    for client in f.clients: transfer(client.port, f.target)
    assert meter(f)[0] > before[0]
    f.evidence['unchanged_configuration_kept_both_pids_and_usage'] = True

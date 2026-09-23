"""Opt-in scheduler acceptance: real Hub HTTP, Agent HTTPS and Xray packets.

Run only through node_automatic_lab.py in a new loopback-only network namespace.
Normal lifespan starts all three production loops. No test calls tick, flush,
collect_stats, sync_traffic, sync_security or a manual synchronization endpoint.
This is eventual online convergence, NOT offline hard enforcement or an SLA.
"""
from __future__ import annotations

import contextlib
import json
import re
import socket
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn

import nodes as nodes_module
from auth import Auth
from core import Config, CoreEngine
from dark_policy import Actor, Store
from manager import Manager
from node_agent import AgentToken, make_agent_app
from server import make_app
from node_security_lab import SOURCES, check_namespace
from test_node_real_data_plane import (
    real_binary, tls_material, TOKEN, EMAIL, CLIENT_UUID, PASSWORD,
    free_port, inbound, target_server, xray_client, transfer, usage,
)
from test_node_real_limits import CONTROL_EMAIL, CONTROL_UUID, QUOTA, TOPUP
from test_node_real_security import bound_client
from test_node_wan_https import WireFaults, FaultProtocol

ROOT = Path(__file__).resolve().parents[1]


def eventually(predicate, description, timeout=45.0):
    """Bounded observation only: the predicate must not drive reconciliation."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError('Automatic convergence timed out: ' + description)
        time.sleep(.1)


@contextlib.contextmanager
def serving(app, listener, *, cert=None, key=None):
    errors = []
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False,
        ws='none', http=FaultProtocol, ssl_certfile=str(cert) if cert else None,
        ssl_keyfile=str(key) if key else None, timeout_graceful_shutdown=3))
    def run():
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            errors.append(type(exc).__name__)
    thread = threading.Thread(target=run, name='automatic-lab-http')
    thread.start()
    try:
        eventually(lambda: server.started or bool(errors), 'HTTP startup', 10)
        assert server.started and not errors, errors
        yield
    finally:
        server.should_exit = True
        thread.join(50)
        listener.close()
        assert not thread.is_alive() and not errors, errors


def listener_socket():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    return sock


@contextlib.contextmanager
def automatic_agent(root, number, material, binary):
    root.mkdir(mode=0o700)
    host, cert, key = material
    listener = listener_socket()
    origin = f'https://{host}:{listener.getsockname()[1]}'
    cfg = Config(public_origin=origin, public_address=host, secure_cookie=True,
        xray_binary=str(binary), xray_assets=str(binary.parent), xray_api_port=free_port(),
        poll_seconds=1, direct_source_verified=True, core_autostart=False, test_engine=False)
    store = Store(root/'node.sqlite3')
    engine = CoreEngine(cfg, store, root/'runtime')
    token = root/'token'; token.write_text(TOKEN+'\n'); token.chmod(0o600)
    node_id = f'automatic-{number}'
    app = make_agent_app(engine, store, AgentToken(token), node_id, background=True)
    wire = WireFaults(app)
    try:
        with serving(wire, listener, cert=cert, key=key):
            worker = app.state.loop.thread
            assert worker is not None and worker.is_alive()
            yield SimpleNamespace(id=node_id, origin=origin, engine=engine, store=store,
                runtime=app.state.runtime, wire=wire, loop=app.state.loop, worker=worker)
        assert not worker.is_alive(), 'Agent maintenance thread survived shutdown'
    finally:
        engine.close(); store.close()


def remote_enabled(node, email=EMAIL):
    # Read persisted Agent configuration only. CoreEngine.clients() can collect
    # statistics, so it is intentionally not used by convergence predicates.
    mirror = node.runtime.mirror_for_source(email)
    with node.store.lock:
        row = node.store.db.execute('SELECT body FROM core_clients WHERE email=?', (mirror,)).fetchone()
    return bool(row and json.loads(row[0]).get('enable'))


def settled(f, enabled=True):
    return (all(not f.reg.desired_state(n.id, include_payload=False)['pending'] for n in f.agents)
            and all(remote_enabled(n) is enabled for n in f.agents))


def seen_ip_count(f):
    with f.store.lock:
        return f.store.db.execute(
            'SELECT COUNT(DISTINCT ip) FROM remote_node_ips WHERE client_id=? AND verified=1 AND last_seen>?',
            (EMAIL, time.time()-120)).fetchone()[0]


def reasons(f):
    return f.store.client_reasons(EMAIL)


def identity(f):
    d = f.api('/api/clients/'+EMAIL)
    return {k:d[k] for k in ('email', 'owner', 'inboundIds', 'subscription_url')} | {'uuid':d['client']['id']}


def patch(f, **values):
    return f.api('/api/clients/'+EMAIL, {'client':values}, 'PATCH')


def packets(f, enabled):
    for c in f.clients:
        transfer(c.port, f.target, allowed=enabled)
    for c in f.controls:
        transfer(c.port, f.target)
    assert all(n.engine.running for n in f.agents)


def assert_healthy_loops(f):
    assert f.manager.thread.is_alive() and f.reg.thread.is_alive()
    assert not f.manager.last_error, f.manager.last_error
    assert all(n.worker.is_alive() and not n.loop.last_error for n in f.agents)


@pytest.fixture
def automatic_fleet(tmp_path, monkeypatch, tls_material, real_binary):
    check_namespace()
    ca, materials = tls_material
    trust = ssl.create_default_context(cafile=str(ca))
    assert trust.check_hostname and trust.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(nodes_module, 'ssl', SimpleNamespace(create_default_context=lambda:trust,
        SSLError=ssl.SSLError, SSLContext=ssl.SSLContext))
    with contextlib.ExitStack() as stack:
        agents = [stack.enter_context(automatic_agent(tmp_path/f'agent{i}', i, mat, real_binary.path))
                  for i, mat in enumerate(materials, 1)]
        origins = {n.origin for n in agents}; original = nodes_module.resolve_origin
        def resolve(origin):
            if origin not in origins:
                return original(origin)
            parsed = urlsplit(origin)
            return origin, parsed.hostname, parsed.port, ('127.0.0.1',)
        monkeypatch.setattr(nodes_module, 'resolve_origin', resolve)
        root = tmp_path/'hub'; root.mkdir(mode=0o700)
        listener = listener_socket(); port = listener.getsockname()[1]
        store = Store(root/'dark.sqlite3'); stack.callback(store.close)
        cfg = Config(public_origin=f'http://127.0.0.1:{port}', bind_port=port,
            xray_binary=str(real_binary.path), xray_assets=str(real_binary.path.parent),
            xray_api_port=free_port(), poll_seconds=1, test_engine=False)
        engine = CoreEngine(cfg, store, root/'runtime'); stack.callback(engine.close)
        manager = Manager(store, engine); stack.callback(manager.close)
        auth = Auth(store, root/'secret.key'); auth.bootstrap('dark', PASSWORD)
        app = make_app(manager, auth, background=True); reg = app.state.nodes
        stack.enter_context(serving(app, listener))
        hub_workers = [manager.thread, reg.thread]
        assert all(t is not None and t.is_alive() for t in hub_workers)
        http = stack.enter_context(httpx.Client(base_url=cfg.public_origin, timeout=15, trust_env=False))
        manual_calls = []
        def api(path, body=None, method=None):
            # A harness guard catches accidental manual workarounds immediately.
            assert path != '/api/sync'
            assert not re.fullmatch(r'/api/nodes/[^/]+/(sync|traffic|security|desired)', path)
            manual_calls.append((method or ('POST' if body is not None else 'GET'), path))
            r = http.request(method or ('POST' if body is not None else 'GET'), path, json=body)
            assert 200 <= r.status_code < 300, (path, r.status_code, r.text[:500])
            return r.json()
        http.headers['X-Dark-CSRF'] = api('/api/auth/login', {'username':'dark','password':PASSWORD})['csrf']
        ids = []; hosts = []
        for n in agents:
            item = inbound(free_port()); item.update(tag=n.id, panelMeta={'deployLocal':False})
            iid = api('/api/inbounds', item)['id']; ids.append(iid); n.data_port = item['port']
            # These tests start with registered nodes. Enrollment/replacement is
            # covered separately; no manual probe or synchronization follows put.
            reg.put(n.id, n.id, n.origin, TOKEN, True, [iid], urlsplit(n.origin).hostname)
            hosts.append({'inboundId':iid, 'address':'127.0.0.1', 'port':n.data_port,
                          'remark':n.id, 'runtime':'node:'+n.id, 'enable':True})
        manager.owner_put(Actor('dark','owner',{}), 'dark', name='Automatic lab', allowed=ids)
        for email, uid in ((EMAIL, CLIENT_UUID), (CONTROL_EMAIL, CONTROL_UUID)):
            api('/api/clients', {'owner':'dark','client':{'email':email,'id':uid,
                'totalGB':0,'expiryTime':0,'limitIp':0,'limitHwid':0}, 'inboundIds':ids})
        api('/api/settings/hosts', {'value':hosts}, 'PUT')
        f = SimpleNamespace(agents=agents, reg=reg, manager=manager, store=store, engine=engine,
            api=api, http=http, root=tmp_path, binary=real_binary, evidence={}, manual_calls=manual_calls)
        eventually(lambda: len(reg.list()) == 2 and all(r['failover_ready'] for r in reg.list()),
                   'initial deployment by background monitor')
        f.uris = {}; control_uris = {}
        for email, dest in ((EMAIL, f.uris), (CONTROL_EMAIL, control_uris)):
            url = api('/api/clients/'+email)['subscription_url']
            r = http.get(url+'?format=raw'); assert r.status_code == 200
            dest.update({urlsplit(line).port:line for line in r.text.splitlines() if line.strip()})
            assert set(dest) == {n.data_port for n in agents}
        f.target = stack.enter_context(target_server())
        f.clients = [stack.enter_context(xray_client(tmp_path/f'client{i}', real_binary.path, f.uris[n.data_port]))
                     for i,n in enumerate(agents)]
        f.controls = [stack.enter_context(xray_client(tmp_path/f'control{i}', real_binary.path,
            control_uris[n.data_port], expected_uuid=CONTROL_UUID)) for i,n in enumerate(agents)]
        try:
            yield f
        finally:
            # Recover the wire before shutdown, not through a reconciler call.
            for n in agents:
                n.wire.down = False
            report = {'real_xray':True, 'binary_sha256':real_binary.digest,
                'hub_http':'real loopback HTTP socket', 'agent_https':True,
                'production_lifespan_loops':True, 'explicit_sync_calls':0,
                'configured_poll_seconds':1, 'node_monitor_minimum_seconds':5,
                'provider_wan_tested':False, 'offline_hard_cutoff':False,
                'physical_device_attestation':False, 'kernel_firewall_tested':False,
                'client_request_count':len(manual_calls), **f.evidence}
            out = ROOT/'qa/node-automatic-limits'; out.mkdir(parents=True, exist_ok=True)
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', tmp_path.name)
            (out/(name+'.json')).write_text(json.dumps(report, indent=2)+'\n')
        # Save actual Thread objects: product close clears some attributes.
        stack.close()
        assert all(not t.is_alive() for t in hub_workers), 'Hub worker survived shutdown'


def test_automatic_combined_quota_and_topup(automatic_fleet):
    f = automatic_fleet; before = identity(f)
    patch(f, totalGB=QUOTA)
    eventually(lambda: settled(f), 'quota configuration')
    for c in f.clients:
        transfer(c.port, f.target)
    eventually(lambda: 'client_quota' in reasons(f) and settled(f, False), 'combined quota cutoff')
    total, rows, ledger = usage(f)
    assert len(rows) == 2 and all(0 < up+down < QUOTA for _,up,down in rows)
    assert total > QUOTA and total == ledger
    packets(f, False)
    patch(f, totalGB=TOPUP)
    eventually(lambda: not reasons(f) and settled(f), 'topup on both Agents')
    assert usage(f)[0] >= total and identity(f) == before
    packets(f, True)
    eventually(lambda: usage(f)[0] > total, 'new bytes after topup')
    assert_healthy_loops(f)
    f.evidence.update(automatic_quota=True, automatic_topup=True, recorded_at_cutoff=total)


@pytest.mark.parametrize('quota', [0, TOPUP], ids=['unlimited-volume','limited-volume'])
def test_wall_clock_expiry_and_extension_without_sync(automatic_fleet, quota):
    f = automatic_fleet; before = identity(f)
    expiry = int(time.time()+15)*1000
    patch(f, totalGB=quota, expiryTime=expiry)
    eventually(lambda: settled(f), 'future expiry configuration')
    assert time.time() < expiry/1000, 'fixture did not establish a valid pre-expiry baseline'
    packets(f, True)
    eventually(lambda: usage(f)[0] > 0, 'pre-expiry usage')
    charged = usage(f)[0]
    eventually(lambda: time.time() >= expiry/1000 and 'expired' in reasons(f) and settled(f, False),
               'real wall-clock expiry')
    packets(f, False)
    patch(f, expiryTime=int(time.time()+3600)*1000)
    eventually(lambda: not reasons(f) and settled(f), 'extension')
    packets(f, True)
    assert usage(f)[0] >= charged and identity(f) == before
    assert_healthy_loops(f)
    f.evidence.update(real_future_expiry_crossed=True, automatic_extension=True)


def test_management_outage_preserves_pending_disable_and_auto_recovers_usage(automatic_fleet):
    f = automatic_fleet; first, other = f.agents; before = identity(f)
    patch(f, totalGB=QUOTA)
    eventually(lambda: settled(f), 'quota setup')
    first.wire.down = True
    eventually(lambda: any(r['id'] == first.id and not r['online'] for r in f.reg.list()), 'offline observation')
    for _ in range(2):
        transfer(f.clients[1].port, f.target)
    eventually(lambda: 'client_quota' in reasons(f) and not remote_enabled(other), 'quota on reachable node')
    eventually(lambda: f.reg.desired_state(first.id, include_payload=False)['pending'], 'offline disable pending')
    charged = usage(f)[0]
    assert f.http.get(before['subscription_url']+'?format=raw').status_code == 403
    transfer(f.clients[1].port, f.target, allowed=False)
    # Explicit negative boundary: old authorization still works offline.
    transfer(f.clients[0].port, f.target)
    transfer(f.controls[1].port, f.target)
    first.wire.down = False
    eventually(lambda: settled(f, False) and usage(f)[0] > charged, 'reconnect without manual sync')
    packets(f, False)
    assert usage(f)[0] == usage(f)[2] and identity(f) == before
    assert_healthy_loops(f)
    f.evidence.update(offline_direct_access_observed=True, automatic_reconnect=True,
                      retained_counter_bytes_recovered=True)


def test_automatic_real_ip_union_and_cap_raise(automatic_fleet):
    f = automatic_fleet; before = identity(f)
    patch(f, limitIp=1)
    with contextlib.ExitStack() as stack:
        a = stack.enter_context(bound_client(f.root/'ip-a',f.binary.path,f.uris[f.agents[0].data_port],SOURCES[0]))
        b = stack.enter_context(bound_client(f.root/'ip-b',f.binary.path,f.uris[f.agents[1].data_port],SOURCES[0]))
        for c in (a,b):
            transfer(c.port, f.target)
        eventually(lambda: seen_ip_count(f) == 1, 'cross-node IP deduplication')
        assert 'global_ip_quota' not in reasons(f)
        c = stack.enter_context(bound_client(f.root/'ip-c',f.binary.path,f.uris[f.agents[1].data_port],SOURCES[1]))
        transfer(c.port, f.target)
        eventually(lambda: 'global_ip_quota' in reasons(f) and settled(f, False), 'global IP cutoff')
        charged = usage(f)[0]; packets(f, False)
        patch(f, limitIp=2)
        eventually(lambda: not reasons(f) and settled(f), 'IP cap raise')
        packets(f, True)
    assert identity(f) == before and usage(f)[0] >= charged
    assert_healthy_loops(f)
    f.evidence.update(automatic_ip_dedup=True, automatic_ip_block=True, automatic_ip_cap_raise=True)


def test_automatic_declared_device_cap_does_not_override_manual_disable(automatic_fleet):
    f = automatic_fleet; before = identity(f)
    patch(f, limitHwid=2)
    for hwid in ('automatic-device-A','automatic-device-B'):
        r = f.http.get(before['subscription_url']+'?format=raw', headers={'x-hwid':hwid})
        assert r.status_code == 200
    patch(f, limitHwid=1)
    eventually(lambda: 'global_device_quota' in reasons(f) and settled(f, False), 'global registered-device policy')
    packets(f, False)
    f.api('/api/clients/'+EMAIL+'/action', {'action':'disable'})
    patch(f, limitHwid=2)
    eventually(lambda: 'global_device_quota' not in reasons(f) and settled(f, False), 'independent manual disable')
    assert 'client_manual' in reasons(f)
    packets(f, False)
    assert identity(f) == before
    assert_healthy_loops(f)
    f.evidence.update(automatic_device_policy=True, manual_disable_preserved=True)


def test_unchanged_background_rounds_preserve_pid_and_metering(automatic_fleet):
    f = automatic_fleet
    received = [transfer(c.port, f.target) for c in f.clients]
    eventually(lambda: len(usage(f)[1]) == 2 and all(down >= size for (_,_,down),size in zip(usage(f)[1],received)), 'automatic collection')
    # Wait through two more actual rounds, observing loop progress, not invoking it.
    before = usage(f); pids = [n.engine.process.pid for n in f.agents]
    posts = [len(n.wire.calls) for n in f.agents]
    started = time.monotonic()
    eventually(lambda: time.monotonic()-started >= 12 and
        all(len(n.wire.calls) > count+10 for n,count in zip(f.agents,posts)), 'unchanged monitor rounds', 35)
    assert usage(f) == before, 'same cumulative counters charged twice'
    assert [n.engine.process.pid for n in f.agents] == pids
    assert_healthy_loops(f)
    f.evidence.update(repeated_background_snapshots_idempotent=True, stable_core_pids=True)


def stop_request_identity(response):
    """Validate acceptance, not completion; a monitor may already have delivered it."""
    assert isinstance(response, dict), 'Invalid Stop response'
    control = response.get('control')
    assert isinstance(control, dict), 'Stop response lacks durable control'
    assert control.get('persisted') is True and control.get('action') == 'stop'
    assert control.get('desired_running') is False
    assert type(control.get('revision')) is int and control['revision'] > 0
    assert isinstance(control.get('command_id'), str) and re.fullmatch(r'[0-9a-f]{32}', control['command_id'])
    state = response.get('delivery_state')
    assert state in {'executed', 'acknowledged', 'pending'}, 'Stop was not accepted'
    assert response.get('executed') is (state == 'executed')
    assert response.get('queued') is (state == 'pending')
    assert control.get('pending') is (state == 'pending')
    assert type(control.get('applied_revision')) is int
    if state == 'pending':
        assert 0 <= control['applied_revision'] < control['revision']
    else:
        assert control['applied_revision'] == control['revision'] and not control.get('last_error')
    if state == 'acknowledged':
        assert response.get('already_applied') is True
    return {k:control[k] for k in ('revision', 'command_id', 'action')}


def stop_acknowledged(expected, hub_control, agent_receipt, running):
    """Read-only completion: matching durable receipts AND a currently stopped core."""
    if running is not False:
        return False
    for doc in (hub_control, agent_receipt):
        if (not isinstance(doc, dict) or doc.get('persisted') is not True
            or type(doc.get('revision')) is not int
            or any(doc.get(k) != v for k, v in expected.items())
            or doc.get('pending') is not False or doc.get('last_error')):
            return False
    return (hub_control.get('desired_running') is False
            and type(hub_control.get('applied_revision')) is int
            and hub_control['applied_revision'] == expected['revision']
            and agent_receipt.get('phase') == 'applied')


def test_background_does_not_undo_explicit_node_stop(automatic_fleet):
    f = automatic_fleet; first, other = f.agents
    packets(f, True)
    eventually(lambda: usage(f)[0] > 0, 'pre-stop usage')
    charged = usage(f)[0]
    binding = f.reg.installations.public_status(first.id)
    response = f.api('/api/nodes/'+first.id+'/core/stop', {})
    f.evidence['stop_initial_response'] = {k:response.get(k) for k in
        ('executed', 'queued', 'already_applied', 'delivery_state')}
    expected = stop_request_identity(response)
    def completed():
        return (f.reg.installations.public_status(first.id) == binding
                and stop_acknowledged(expected, f.reg.commands.status(first.id),
                                      first.runtime.command_status(), first.engine.running))
    # Observe the SAME saved command. Never issue a second Stop or drive sync.
    eventually(completed, 'durable Stop acknowledgement and stopped core')
    f.evidence['stop_final_command'] = expected
    pid = other.engine.process.pid; start = time.monotonic()
    while time.monotonic()-start < 12:
        assert completed(), 'Stop receipt changed or the core resumed'
        transfer(f.clients[1].port, f.target)
        time.sleep(1)
    transfer(f.clients[0].port, f.target, allowed=False)
    assert completed()
    assert other.engine.process.pid == pid and usage(f)[0] >= charged
    assert_healthy_loops(f)
    f.evidence['explicit_stop_survived_background_rounds'] = True

"""Deterministic coordinator overlap tests; real SQLite, a protocol-only remote.

The authenticated remote is modelled here. These tests establish no TLS, WAN,
Xray, multi-process or arbitrary separate-Store serialization guarantee.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from node_credentials import NodeCredentials
from nodes import NodeHTTPError, NodeRegistry
import nodes as node_module
from test_hub_node_control_live import hub
from test_node_hub_recovery import NODE, TOKEN, ORIGIN

CANDIDATE = 'dkn_' + 'ConcurrentCandidateFixture' * 2
OTHER = 'another-node'


def descriptor(node, installation):
    return {'service': 'DARK XRAY NODE', 'agent_only': True, 'node_id': node,
            'installation_id': installation, 'writes_enabled': True,
            'capabilities': {'installation_identity': 1, 'credential_rotation': 1}}


def model_transport(reg, monkeypatch, *, hold_path):
    entered, release, second_io, second_started = (threading.Event() for _ in range(4))
    roles = threading.local()
    remote = {NODE: TOKEN, OTHER: TOKEN}
    documents = {NODE: descriptor(NODE, 'a' * 32), OTHER: descriptor(OTHER, 'b' * 32)}
    reg.installations.observe(NODE, documents[NODE])
    calls, gate = [], threading.Lock()
    held = False

    def transport(origin, credential, path, method='GET', body=None, timeout=12.0,
                  *, agent_id='', installation_id='', allow_auth_failure=False):
        nonlocal held
        role = getattr(roles, 'name', '')
        assert installation_id == documents[agent_id]['installation_id']
        assert allow_auth_failure is True
        with gate:
            calls.append((role, agent_id, method, path))
            if role == 'second':
                second_io.set()
            unauthorized = credential != remote[agent_id]
            if not unauthorized and path.endswith('/token/rotate'):
                assert method == 'POST' and set(body) == {'token'}
                remote[agent_id] = body['token']
                result = {'service': 'DARK XRAY NODE', 'rotated': True}
            else:
                result = documents[agent_id]
            block = role == 'first' and path == hold_path and not held
            if block:
                held = True
        if block:
            entered.set()
            assert release.wait(5), 'The test must release the held remote response'
        if unauthorized:
            raise NodeHTTPError(401)
        return result, 1

    monkeypatch.setattr(node_module, 'node_https_request', transport)

    def invoke(role, coordinator, node, attempt):
        roles.name = role
        if role == 'second':
            second_started.set()
        return coordinator.retry(node, attempt)

    return entered, release, second_io, second_started, remote, documents, calls, invoke


@pytest.mark.parametrize('hold_path', ['/node/api/health', '/node/api/v1/token/rotate'])
def test_same_store_coordinators_wait_for_inflight_handoff(hub, monkeypatch, hold_path):
    reg, _, _ = hub
    entered, release, second_io, started, remote, _, calls, invoke = model_transport(
        reg, monkeypatch, hold_path=hold_path)
    first = NodeCredentials(reg)
    second = NodeCredentials(NodeRegistry(reg.store, reg.cipher))
    saved = first.begin(NODE, CANDIDATE, binding_id=reg.installations.capture(NODE)['binding_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(invoke, 'first', first, NODE, saved['attempt_id'])
        try:
            assert entered.wait(3)
            # Holding a credential operation must not hold the SQLite lock.
            reg.set_enabled(NODE, False)
            b = pool.submit(invoke, 'second', second, NODE, saved['attempt_id'])
            assert started.wait(3)
            assert not second_io.wait(.25), 'Second coordinator overlapped the in-flight handoff'
        finally:
            release.set()
        assert a.result(timeout=5)['rotated'] is True
        assert b.result(timeout=5)['rotated'] is True
    assert remote[NODE] == reg.get(NODE, secret=True)['token'] == CANDIDATE
    assert not reg.get(NODE)['enabled']
    assert len([x for x in calls if x[2:] == ('POST', '/node/api/v1/token/rotate')]) == 1
    assert not any(x[0] == 'second' for x in calls), 'Completed retry should read the receipt only'


def test_handoff_lock_does_not_serialize_unrelated_nodes(hub, monkeypatch):
    reg, _, _ = hub
    entered, release, _, started, remote, documents, _, invoke = model_transport(
        reg, monkeypatch, hold_path='/node/api/health')
    reg.put(OTHER, 'Another Node', 'https://other.example.test', TOKEN)
    reg.installations.observe(OTHER, documents[OTHER])
    first = NodeCredentials(reg)
    second = NodeCredentials(NodeRegistry(reg.store, reg.cipher))
    a = first.begin(NODE, CANDIDATE, binding_id=reg.installations.capture(NODE)['binding_id'])
    b = second.begin(OTHER, CANDIDATE, binding_id=reg.installations.capture(OTHER)['binding_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(invoke, 'first', first, NODE, a['attempt_id'])
        try:
            assert entered.wait(3)
            other = pool.submit(invoke, 'second', second, OTHER, b['attempt_id'])
            assert started.wait(3)
            assert other.result(timeout=3)['rotated'] is True
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=5)['rotated'] is True
    assert remote[NODE] == remote[OTHER] == CANDIDATE

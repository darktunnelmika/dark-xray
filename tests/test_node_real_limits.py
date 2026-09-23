"""Opt-in two-node quota/expiry acceptance with real Xray and real Agent TLS.

Use the pinned binary fixture; never fake counters, clocks, cores or TLS results.
Hub HTTP handlers use TestClient. Propagation is deliberately explicit because
background schedulers are disabled. This proves enforcement after reconciliation,
not instantaneous distributed cut-off or an offline authorization lease. All
processes, credentials, databases and packet targets are disposable loopback.
"""
from __future__ import annotations

import contextlib
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from dark_policy import PolicyError
from test_node_real_data_plane import (
    real_binary, real_fleet, tls_material, EMAIL, CLIENT_UUID,
    transfer, xray_client, stable_identity, usage, meter,
)

CONTROL_EMAIL = 'quota-unaffected-user'
CONTROL_UUID = '22222222-2222-4222-8222-222222222222'
QUOTA = 110_000  # Each 64 KiB response is below this; the two-node sum is above it.
TOPUP = 4 * 1024 * 1024


def patch(fleet, **values):
    return fleet.api('/api/clients/'+EMAIL, {'client':values}, 'PATCH')


def detail(fleet):
    return fleet.api('/api/clients/'+EMAIL)


def sync_all(fleet):
    fleet.api('/api/sync', {})
    for node in fleet.agents:
        result = fleet.api('/api/nodes/'+node.id+'/sync', {})
        assert result['desired_state_applied'] is True, result
        fleet.reg.probe(node.id)
        assert fleet.reg.desired_state(node.id, include_payload=False)['pending'] is False


def immutable_customer(fleet):
    d = detail(fleet)
    return {'owner':d['owner'], 'uuid':d['client']['id'], 'email':d['email'],
            'inboundIds':d['inboundIds'], 'subscription_url':d['subscription_url']}


def assert_packet_policy(fixture, allowed):
    f = fixture.fleet
    for client in f.clients:
        transfer(client.port, f.target, allowed=allowed)
    # A second UUID uses the SAME logical inbounds, proving a per-customer block,
    # rather than a stopped/broken core, does not block every customer.
    for client in fixture.control_clients:
        transfer(client.port, f.target)
    assert not f.api('/api/clients/'+CONTROL_EMAIL)['block_reasons']
    assert all(a.engine.running for a in f.agents)
    response = f.http.get(detail(f)['subscription_url']+'?format=raw')
    assert response.status_code == (200 if allowed else 403), response.text


@pytest.fixture
def limit_fleet(real_fleet):
    f = real_fleet
    ids = stable_identity(f)['inboundIds']
    f.api('/api/clients', {'owner':'dark', 'client':{
        'email':CONTROL_EMAIL, 'id':CONTROL_UUID, 'limitIp':0, 'limitHwid':0,
        'totalGB':0, 'expiryTime':0}, 'inboundIds':ids})
    sync_all(f)
    url = f.api('/api/clients/'+CONTROL_EMAIL+'/links')['subscription_url']
    response = f.http.get(url+'?format=raw')
    assert response.status_code == 200
    raw = {urlsplit(line).port:line for line in response.text.splitlines() if line.strip()}
    assert set(raw) == {a.data_port for a in f.agents}
    with contextlib.ExitStack() as stack:
        controls = [stack.enter_context(xray_client(
            f.root/f'control-{i}', f.binary.path, raw[a.data_port], expected_uuid=CONTROL_UUID))
            for i, a in enumerate(f.agents)]
        f.evidence.update(limits_scope='quota-expiry-after-explicit-reconciliation',
            automatic_scheduler_tested=False, offline_hard_cutoff_tested=False,
            global_ip_guard_tested=False, device_limit_tested=False)
        yield SimpleNamespace(fleet=f, control_clients=controls, control_url=url)


def exhaust_combined_quota(fixture):
    f = fixture.fleet
    patch(f, totalGB=QUOTA, expiryTime=0)
    sync_all(f)
    assert usage(f)[0] == 0
    received = [transfer(c.port, f.target) for c in f.clients]
    total, rows, ledger = meter(f)
    assert len(rows) == 2 and ledger == total
    assert all(0 < up+down < QUOTA for _, up, down in rows), rows
    assert total >= sum(received) > QUOTA
    assert 'client_quota' in detail(f)['block_reasons']
    sync_all(f)
    assert meter(f) == (total, rows, ledger), 'duplicate snapshot charged twice'
    f.evidence.update(quota_bytes=QUOTA, exhausted_used_bytes=total,
        per_node_used_bytes={n:up+down for n, up, down in rows},
        combined_quota_enforced_after_reconciliation=True)
    return total, rows, ledger


def test_combined_quota_blocks_both_nodes_not_other_customer(limit_fleet):
    f = limit_fleet.fleet; identity = immutable_customer(f)
    charged = exhaust_combined_quota(limit_fleet)
    assert_packet_policy(limit_fleet, False)
    assert immutable_customer(f) == identity
    assert meter(f) == charged
    f.evidence['unaffected_customer_passed_on_both_shared_inbounds'] = True


def test_quota_topup_restores_same_links_without_refunding_usage(limit_fleet):
    f = limit_fleet.fleet; identity = immutable_customer(f)
    charged = exhaust_combined_quota(limit_fleet)
    assert_packet_policy(limit_fleet, False)
    patch(f, totalGB=TOPUP)
    sync_all(f)
    assert not detail(f)['block_reasons']
    assert usage(f) == charged and immutable_customer(f) == identity
    assert_packet_policy(limit_fleet, True)
    after = meter(f)
    assert after[0] > charged[0] and after[2] == after[0]
    assert meter(f) == after
    f.evidence.update(topup_without_usage_reset=True, used_after_topup_traffic=after[0])


@pytest.mark.parametrize('quota', [0, TOPUP], ids=['unlimited-volume', 'remaining-volume'])
def test_expiry_blocks_both_nodes_and_extension_restores_same_uuid(limit_fleet, quota):
    f = limit_fleet.fleet; identity = immutable_customer(f)
    patch(f, totalGB=quota, expiryTime=0)
    sync_all(f)
    assert_packet_policy(limit_fleet, True)
    charged = meter(f)
    expiry = int(time.time()-2)*1000
    patch(f, expiryTime=expiry)
    sync_all(f)
    assert detail(f)['block_reasons'] == ['expired']
    assert_packet_policy(limit_fleet, False)
    assert usage(f)[0] >= charged[0] and immutable_customer(f) == identity
    assert detail(f)['client']['totalGB'] == quota
    patch(f, expiryTime=int(time.time()+86400)*1000)
    sync_all(f)
    assert not detail(f)['block_reasons']
    assert_packet_policy(limit_fleet, True)
    after = meter(f)
    assert after[0] > charged[0] and after[2] == after[0]
    assert immutable_customer(f) == identity
    f.evidence.update(expiry_cutoff_after_reconciliation=True,
        expiry_extension_preserved_usage=True, expiry_quota_bytes=quota)


@pytest.mark.parametrize('block', ['client_manual', 'expired'])
def test_quota_topup_cannot_override_other_block(limit_fleet, block):
    f = limit_fleet.fleet; identity = immutable_customer(f)
    before = exhaust_combined_quota(limit_fleet)
    if block == 'client_manual': f.api('/api/clients/'+EMAIL+'/action', {'action':'disable'})
    else: patch(f, expiryTime=int(time.time()-2)*1000)
    patch(f, totalGB=TOPUP)
    sync_all(f)
    reasons = detail(f)['block_reasons']
    assert block in reasons and 'client_quota' not in reasons
    assert_packet_policy(limit_fleet, False)
    assert immutable_customer(f) == identity and meter(f) == before
    f.evidence['topup_preserved_block'] = block


def test_expiry_extension_cannot_override_exhausted_quota(limit_fleet):
    f = limit_fleet.fleet
    before = exhaust_combined_quota(limit_fleet)
    patch(f, expiryTime=int(time.time()-2)*1000)
    sync_all(f)
    assert set(detail(f)['block_reasons']) == {'expired', 'client_quota'}
    patch(f, expiryTime=int(time.time()+86400)*1000)
    sync_all(f)
    assert detail(f)['block_reasons'] == ['client_quota']
    assert_packet_policy(limit_fleet, False)
    assert meter(f) == before
    f.evidence['extension_preserved_quota_block'] = True


def test_zero_quota_and_expiry_mean_unlimited_not_disabled(limit_fleet):
    f = limit_fleet.fleet; identity = immutable_customer(f)
    patch(f, totalGB=0, expiryTime=0)
    sync_all(f)
    for _ in range(3):
        assert_packet_policy(limit_fleet, True)
        meter(f)
        sync_all(f)
    total, rows, ledger = usage(f)
    assert total > QUOTA and total == ledger
    assert all(up > 0 and down > 0 for _, up, down in rows)
    assert not detail(f)['block_reasons'] and immutable_customer(f) == identity
    assert meter(f) == (total, rows, ledger)
    f.evidence.update(zero_means_unlimited=True, unlimited_used_bytes=total)


def test_offline_node_keeps_old_authorization_then_converges_on_reconnect(limit_fleet):
    """Diagnostic boundary: a central quota is NOT an offline authorization lease."""
    f = limit_fleet.fleet; first, other = f.agents; identity = immutable_customer(f)
    patch(f, totalGB=QUOTA, expiryTime=0)
    sync_all(f)
    first.wire.down = True
    try:
        with pytest.raises(PolicyError, match='RemoteDisconnected'): f.reg.probe(first.id)
        for _ in range(2): transfer(f.clients[1].port, f.target)
        other.engine.collect_stats(force=True, strict=True)
        f.api('/api/nodes/'+other.id+'/traffic', {})
        before = usage(f)[0]
        assert before > QUOTA and 'client_quota' in detail(f)['block_reasons']
        f.api('/api/nodes/'+other.id+'/sync', {})
        transfer(f.clients[1].port, f.target, allowed=False)
        pending = f.api('/api/nodes/'+first.id+'/desired')
        assert pending['pending'] is True
        with pytest.raises(PolicyError): f.reg.sync_desired_state(first.id, pending)
        assert f.reg.desired_state(first.id, include_payload=False)['pending'] is True
        suspended = f.http.get(detail(f)['subscription_url']+'?format=raw')
        assert suspended.status_code == 403
        visible = f.http.get(limit_fleet.control_url+'?format=raw')
        assert visible.status_code == 200
        assert {urlsplit(line).port for line in visible.text.splitlines() if line.strip()} == {other.data_port}
        # Honest limit of the existing design: management loss is not firewall
        # enforcement. Old direct credentials can still pass traffic here.
        offline_bytes = transfer(f.clients[0].port, f.target)
        for client in limit_fleet.control_clients: transfer(client.port, f.target)
    finally:
        first.wire.down = False
    first.engine.collect_stats(force=True, strict=True)
    sync_all(f)
    assert_packet_policy(limit_fleet, False)
    after = meter(f)
    assert after[0] >= before+offline_bytes and after[2] == after[0]
    assert meter(f) == after and immutable_customer(f) == identity
    f.evidence.update(offline_old_authorization_observed=True,
        reconnect_enforced_quota=True, offline_reported_bytes_preserved=True,
        used_before_reconnect=before, used_after_reconnect=after[0])

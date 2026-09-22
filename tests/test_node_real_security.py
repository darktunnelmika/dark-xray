"""Real Xray source-IP aggregation and self-declared subscription HWID limits.

Run ONLY via node_security_lab.py in a separate loopback-only net namespace.
The two public-shaped source addresses are owned in that namespace, not routed
on any public network. No synthetic log lines, counters or device DB inserts.
No firewall, physical-device attestation, background-scheduler, opaque-tunnel
source recovery or atomic/offline cutoff claim is made by this acceptance suite.
"""
from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import time
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

import pytest
from node_security_lab import SOURCES, check_namespace
from test_node_real_data_plane import (
    real_binary, real_fleet, tls_material, EMAIL, transfer, xray_client,
    stable_identity, meter, usage, CLIENT_UUID, free_port,
)
from test_node_real_limits import (
    limit_fleet, CONTROL_EMAIL, CONTROL_UUID, patch, detail, sync_all, immutable_customer,
)


@pytest.fixture(autouse=True)
def private_network():
    check_namespace()


def security(f):
    return f.api('/api/clients/'+EMAIL+'/security-global')


def read_security(f):
    for node in f.agents:
        f.api('/api/nodes/'+node.id+'/security', {})
    return security(f)


def packets(fixture, allowed):
    f=fixture.fleet
    for client in f.clients: transfer(client.port, f.target, allowed=allowed)
    for client in fixture.control_clients: transfer(client.port, f.target)
    assert all(node.engine.running for node in f.agents)
    assert not f.api('/api/clients/'+CONTROL_EMAIL)['block_reasons']


@contextlib.contextmanager
def bound_client(root, binary, uri, source_ip, expected_uuid=CLIENT_UUID):
    """Additional test client; production endpoints and original helpers unchanged."""
    check_namespace()
    assert source_ip in SOURCES
    parsed=urlsplit(uri)
    assert parsed.scheme=='vless' and parsed.hostname=='127.0.0.1' and parsed.username==expected_uuid
    query=parse_qs(parsed.query)
    assert query.get('security')==['none'] and query.get('type')==['tcp']
    port=free_port();root.mkdir(mode=0o700)
    config={'log':{'loglevel':'warning'},
        'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks',
                     'settings':{'auth':'noauth','udp':False}}],
        'outbounds':[{'protocol':'vless','sendThrough':source_ip,
            'settings':{'vnext':[{'address':parsed.hostname,'port':parsed.port,
                'users':[{'id':parsed.username,'encryption':'none'}]}]},
            'streamSettings':{'network':'tcp','security':'none'}}]}
    path=root/'client.json';path.write_text(json.dumps(config));path.chmod(0o600)
    subprocess.run([str(binary),'run','-test','-config',str(path)],
                   check=True,capture_output=True,timeout=10)
    with (root/'client.log').open('wb') as log:
        proc=subprocess.Popen([str(binary),'run','-config',str(path)],stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+8
            while True:
                assert proc.poll() is None,'real bound-source Xray client exited'
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):break
                except OSError:
                    if time.monotonic()>=deadline:raise
                    time.sleep(.02)
            yield SimpleNamespace(port=port)
        finally:
            if proc.poll() is None:proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


@contextlib.contextmanager
def sources(f, addresses):
    with contextlib.ExitStack() as stack:
        clients=[stack.enter_context(bound_client(f.root/f'source-{i}', f.binary.path,
            f.uris[node.data_port], address))
            for i,(node,address) in enumerate(zip(f.agents,addresses,strict=True))]
        yield clients


def observed_sources(f):
    result=[]
    for node in f.agents:
        node.engine.read_ip_log()
        assert not node.engine.ip_error
        mirror=node.runtime.mirror_for_source(EMAIL)
        with node.store.lock:
            result.append({row[0] for row in node.store.db.execute(
                'SELECT ip FROM observations WHERE client_id=?', (mirror,))})
    return result


@pytest.fixture
def ip_fleet(limit_fleet):
    f=limit_fleet.fleet
    for node in f.agents:
        # Fixture operator knows the real local source addresses; no proxy/NAT.
        node.engine.config.direct_source_verified=True
        assert node.engine.section('ipguard')['mode']=='observe'
    patch(f,limitIp=1,limitHwid=0)
    sync_all(f)
    f.evidence.update(security_scope='verified-source-IP-and-declared-subscription-HWID',
        private_network_namespace=True, automatic_scheduler_tested=False,
        kernel_firewall_tested=False, physical_device_attestation=False)
    return limit_fleet


def exceed_ip(fixture):
    f=fixture.fleet
    with sources(f,SOURCES) as clients:
        for client in clients: transfer(client.port,f.target)
    assert observed_sources(f)==[{SOURCES[0]},{SOURCES[1]}]
    state=read_security(f)
    assert state['ip_enforceable'] and state['ip_count']==2 and state['ip_blocked']
    assert state['local_observation_required'] is False
    before=meter(f)
    sync_all(f)
    assert 'global_ip_quota' in detail(f)['block_reasons']
    packets(fixture,False)
    assert meter(f)==before
    f.evidence.update(real_ip_observations_per_node=[1,1],union_ip_count=2,
        global_ip_authorization_block_tested=True, kernel_firewall_tested=False)
    return before


def test_same_source_on_two_nodes_is_counted_once(ip_fleet):
    f=ip_fleet.fleet
    with sources(f,(SOURCES[0],SOURCES[0])) as clients:
        for client in clients: transfer(client.port,f.target)
        assert observed_sources(f)==[{SOURCES[0]},{SOURCES[0]}]
        state=read_security(f)
        assert state['ip_count']==1 and state['ip_enforceable'] and not state['ip_blocked']
        sync_all(f)
        for client in clients: transfer(client.port,f.target)
    packets(ip_fleet,True)
    total=meter(f)
    assert total[0]>0 and total[0]==total[2] and meter(f)==total
    f.evidence.update(same_source_deduplicated_across_nodes=True)


def test_combined_sources_block_only_customer_and_cap_raise_restores(ip_fleet):
    f=ip_fleet.fleet; identity=immutable_customer(f)
    before=exceed_ip(ip_fleet)
    response=f.http.get(detail(f)['subscription_url']+'?format=raw')
    assert response.status_code==403
    # A control UUID at the exact same source must still work: per-user removal,
    # NOT a source-address firewall ban that could affect other NAT customers.
    with bound_client(f.root/'same-source-control',f.binary.path,
            f.http.get(ip_fleet.control_url+'?format=raw').text.splitlines()[0],
            source_ip=SOURCES[0],expected_uuid=CONTROL_UUID) as control:
        transfer(control.port,f.target)
    patch(f,limitIp=2)
    sync_all(f)
    assert not security(f)['ip_blocked']
    assert usage(f)==before and immutable_customer(f)==identity
    packets(ip_fleet,True)
    after=meter(f)
    assert after[0]>before[0] and after[0]==after[2] and meter(f)==after
    f.evidence.update(ip_cap_raise_kept_usage=True, same_source_other_uuid_passed=True)


def test_unverified_source_cannot_create_new_global_ip_block(ip_fleet):
    f=ip_fleet.fleet
    # Make real observations first, then remove verification before importing.
    with sources(f,SOURCES) as clients:
        for client in clients: transfer(client.port,f.target)
    assert observed_sources(f)==[{SOURCES[0]},{SOURCES[1]}]
    f.agents[1].engine.config.direct_source_verified=False
    state=read_security(f)
    assert not state['ip_enforceable'] and not state['ip_blocked']
    sync_all(f); packets(ip_fleet,True)
    f.agents[1].engine.config.direct_source_verified=True
    state=read_security(f)
    assert state['ip_enforceable'] and state['ip_blocked']
    sync_all(f); packets(ip_fleet,False)
    f.evidence['unverified_source_not_treated_as_verified']=True


def test_loss_of_verification_does_not_clear_existing_global_block(ip_fleet):
    f=ip_fleet.fleet; before=exceed_ip(ip_fleet)
    f.agents[1].engine.config.direct_source_verified=False
    state=read_security(f)
    assert not state['ip_enforceable'] and state['ip_blocked']
    sync_all(f); packets(ip_fleet,False)
    assert meter(f)==before
    f.evidence['incomplete_observation_preserved_previous_block']=True


def test_real_ip_window_elapses_without_fake_clock_or_usage_reset(ip_fleet):
    f=ip_fleet.fleet
    f.api('/api/settings/ipguard',{'value':{'mode':'observe','window_seconds':10,
        'ban_seconds':1800,'exempt_ips':[]}},'PUT')
    sync_all(f)
    before=exceed_ip(ip_fleet)
    started=time.monotonic(); deadline=started+16
    # Wait for actual observation aging. No timestamp edits or mocked clock.
    while security(f)['ip_count']:
        assert time.monotonic()<deadline,'actual IP observation window did not expire'
        time.sleep(.1)
    state=read_security(f)
    assert state['ip_enforceable'] and not state['ip_blocked']
    sync_all(f)
    assert usage(f)==before
    packets(ip_fleet,True)
    assert meter(f)[0]>before[0]
    f.evidence.update(real_window_elapsed=True, wait_seconds=time.monotonic()-started)


@pytest.fixture
def device_fleet(limit_fleet):
    f=limit_fleet.fleet
    patch(f,limitHwid=1,limitIp=0)
    sync_all(f)
    f.evidence.update(security_scope='declared-subscription-HWID',
        private_network_namespace=True, device_limit_tested=True,
        physical_device_attestation=False, copied_uuid_hardware_binding=False,
        automatic_scheduler_tested=False, kernel_firewall_tested=False)
    return limit_fleet


def subscription(f,hwid=None):
    headers={} if hwid is None else {'x-hwid':hwid,'x-device-os':'fixture-os','x-device-model':'fixture-model'}
    return f.http.get(detail(f)['subscription_url']+'?format=raw',headers=headers)


@pytest.mark.parametrize('hwid',[None,'abc'],ids=['missing','too-short'])
def test_missing_or_invalid_hwid_denies_subscription_without_record(device_fleet,hwid):
    f=device_fleet.fleet
    assert subscription(f,hwid).status_code==403
    assert f.api('/api/clients/'+EMAIL+'/devices')==[]
    assert not detail(f)['block_reasons']
    f.evidence['invalid_hwid_denied_without_device_registration']=True


def test_same_declared_hwid_deduplicates_and_second_is_refused(device_fleet):
    f=device_fleet.fleet
    assert subscription(f,'device-A').status_code==200
    assert subscription(f,'device-A').status_code==200
    assert subscription(f,'device-B').status_code==403
    assert len(f.api('/api/clients/'+EMAIL+'/devices'))==1
    state=read_security(f)
    assert state['device_complete'] and state['device_count']==1 and not state['device_blocked']
    f.evidence['same_hwid_one_slot_second_hwid_denied']=True


def test_copied_uuid_is_not_bound_to_subscription_hwid(device_fleet):
    """Diagnostic only: an HWID header is not per-connection hardware proof."""
    f=device_fleet.fleet
    accepted=subscription(f,'device-A'); assert accepted.status_code==200
    assert subscription(f,'device-B').status_code==403
    # Fresh real process consumes a copied link despite its distinct HWID being
    # denied at the subscription endpoint. Do not call this anti-sharing proof.
    with xray_client(f.root/'copied-client',f.binary.path,accepted.text.splitlines()[0]) as copied:
        transfer(copied.port,f.target)
    assert subscription(f,'device-A').status_code==200  # self-declaration can also be repeated
    assert len(f.api('/api/clients/'+EMAIL+'/devices'))==1
    f.evidence.update(copied_uuid_usable_despite_second_hwid_denial=True,
        declared_hwid_not_hardware_attestation=True)


def lower_device_limit(f):
    patch(f,limitHwid=2);sync_all(f)
    assert subscription(f,'device-A').status_code==200
    assert subscription(f,'device-B').status_code==200
    assert len(f.api('/api/clients/'+EMAIL+'/devices'))==2
    patch(f,limitHwid=1);sync_all(f)
    state=read_security(f)
    assert state['device_complete'] and state['device_count']==2 and state['device_blocked']
    assert 'global_device_quota' in detail(f)['block_reasons']


def test_lower_device_cap_blocks_both_real_nodes_and_raise_restores(device_fleet):
    f=device_fleet.fleet;identity=immutable_customer(f)
    packets(device_fleet,True);before=meter(f)
    lower_device_limit(f);packets(device_fleet,False)
    assert usage(f)==before
    patch(f,limitHwid=2);sync_all(f)
    assert not security(f)['device_blocked']
    assert immutable_customer(f)==identity and usage(f)==before
    assert subscription(f,'device-A').status_code==200
    packets(device_fleet,True)
    after=meter(f);assert after[0]>before[0] and after[0]==after[2] and meter(f)==after
    f.evidence['lowered_hwid_cap_blocks_uuid_on_both_nodes']=True


@pytest.mark.parametrize('manual',[False,True],ids=['normal','manual-disable'])
def test_device_history_clear_preserves_usage_and_manual_disable(device_fleet,manual):
    f=device_fleet.fleet;identity=immutable_customer(f)
    packets(device_fleet,True);before=meter(f)
    lower_device_limit(f);packets(device_fleet,False)
    if manual: f.api('/api/clients/'+EMAIL+'/action',{'action':'disable'})
    f.api('/api/clients/'+EMAIL+'/devices',method='DELETE')
    sync_all(f)
    state=security(f)
    assert state['device_complete'] and not state['device_blocked'] and state['device_count']==0
    assert ('client_manual' in detail(f)['block_reasons']) is manual
    assert usage(f)==before and immutable_customer(f)==identity
    packets(device_fleet,not manual)
    f.evidence.update(device_history_clear_kept_usage=True, manual_disable_preserved=manual)

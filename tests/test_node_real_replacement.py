"""Opt-in complete replacement with real Xray and real pinned Agent HTTPS.

The old and replacement data listener reuse one loopback port. The test operator
explicitly stops the old owned core before activation; replacement does NOT stop
an old VPS, edit DNS, migrate open sessions or reconstruct unreported traffic.
Hub handlers use TestClient. Only the three disposable Agent origins are mapped
to loopback; TLS/hostname verification and all product guards stay enabled.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

import pytest
import nodes as nodes_module
from dark_policy import PolicyError
from node_installations import StaleInstallation
from test_node_real_data_plane import (
    real_binary, real_fleet, real_agent, tls_material, EMAIL, TOKEN,
    transfer, xray_client, stable_identity, usage, meter,
)


class LostCommittedReply:
    """Drop a real TLS socket only after the selected Agent handler has finished.

    Buffers the response so no successful header/body reaches the Hub. Product
    requests, authentication, runtime operations and HTTPS client are unchanged.
    Recorded evidence contains paths/statuses, never bodies or credentials.
    """
    def __init__(self, app):
        self.app = app
        self.next_path = None
        self.dropped = []

    async def __call__(self, scope, receive, send):
        selected = (scope['type'] == 'http' and scope['method'] == 'POST'
                    and scope['path'] == self.next_path)
        if not selected:
            return await self.app(scope, receive, send)
        self.next_path = None
        messages = []
        async def buffered(message):
            messages.append(message)
        await self.app(scope, receive, buffered)
        start = next(m for m in messages if m['type'] == 'http.response.start')
        # A failed handler is not a successful mutation with a lost response.
        assert start['status'] == 200, start['status']
        assert any(m['type'] == 'http.response.body' and not m.get('more_body') for m in messages)
        self.dropped.append({'path': scope['path'], 'status_before_drop': start['status']})
        scope['_fixture_transport'].abort()


@pytest.fixture
def replacement_lab(real_fleet, monkeypatch, tls_material):
    f = real_fleet
    old, survivor = f.agents
    # The third installation has a distinct HTTPS origin/port and identity. Its
    # certificate is valid for the same test hostname, not a new trust exception.
    with real_agent(f.root/'replacement', 'replacement-new', tls_material[1][0], f.binary.path) as new:
        resolve = nodes_module.resolve_origin
        def fixture_resolve(origin):
            if origin != new.origin:
                return resolve(origin)
            parsed = urlsplit(origin)
            return origin, parsed.hostname, parsed.port, ('127.0.0.1',)
        monkeypatch.setattr(nodes_module, 'resolve_origin', fixture_resolve)
        lost = LostCommittedReply(new.wire.app)
        new.wire.app = lost
        f.new, f.old, f.survivor, f.lost = new, old, survivor, lost
        f.old_binding = f.reg.installations.capture(old.id)
        f.survivor_pid = survivor.engine.process.pid
        f.evidence.update(scenario='full-replacement', old_core_stopped_by_test_operator=True,
            data_endpoint_reused_on_loopback=True, dns_or_tunnel_changed=False,
            real_server_replacement_tested=False, replacement_protocol_with_real_core_tested=True)
        try:
            yield f
        finally:
            f.evidence['committed_replies_dropped'] = lost.dropped


def contract(f):
    with f.store.lock:
        owner = f.store.db.execute('SELECT owner FROM clients WHERE id=?', (EMAIL,)).fetchone()[0]
    return {'client': stable_identity(f), 'owner': owner,
            'subscription': f.api('/api/clients/'+EMAIL+'/links')['subscription_url']}


def assert_survivor(f):
    assert f.survivor.engine.running
    assert f.survivor.engine.process.pid == f.survivor_pid
    transfer(f.clients[1].port, f.target)


def prefix(f, prepared=None):
    base = '/api/nodes/'+f.old.id+'/replacement'
    return base if prepared is None else base+'/'+prepared['attempt_id']


def prepare(f):
    body = {'schema':1, 'nodeId':f.new.id, 'name':'Isolated real replacement',
            'origin':f.new.origin, 'token':TOKEN, 'dataAddress':urlsplit(f.new.origin).hostname,
            'priority':100, 'failoverEnabled':True}
    code = 'DXN1.'+base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip('=')
    return f.api(prefix(f)+'/prepare', {'code':code})


def begin(f, *, drop_prepare=False, old_offline=False):
    """Real paid traffic first, then preparation, explicit old shutdown and commit."""
    before = contract(f)
    for client in f.clients:
        transfer(client.port, f.target)
    metered = meter(f)
    assert metered[0] > 0 and metered[2] == metered[0]
    old_pid = f.old.engine.process.pid
    if drop_prepare:
        f.lost.next_path = '/node/api/v1/replacement/rotate-token'
    prepared = prepare(f)
    if drop_prepare:
        assert not prepared['prepared'] and prepared['last_error'], prepared
        with f.store.lock:
            journal = dict(f.store.db.execute('SELECT * FROM remote_node_replacements').fetchone())
        token = (f.root/'replacement/token').read_text().strip()
        assert token != TOKEN and f.reg.cipher.decrypt(journal['candidate_enc'].encode()).decode() == token
        assert token not in json.dumps(prepared)
        prepared = f.api(prefix(f, prepared)+'/retry', {})
        assert (f.root/'replacement/token').read_text().strip() == token
        assert sum(path.endswith('/replacement/rotate-token') for method,path in f.new.wire.calls) == 1
    assert prepared['prepared'] and not f.new.engine.running, prepared
    assert f.reg.installations.capture(f.old.id)['binding_id'] == f.old_binding['binding_id']
    assert f.old.engine.process.pid == old_pid
    assert contract(f) == before and usage(f) == metered
    assert_survivor(f)
    # This explicit operator action frees the shared loopback data port. With a
    # real filtered VPS, stopping/fencing it and moving DNS are separate tasks.
    if old_offline:
        f.old.engine.command('stop')
        f.old.wire.down = True
        with pytest.raises(PolicyError, match='RemoteDisconnected'):
            f.reg.probe(f.old.id)
        # No additional old-node bytes are sent after its last sampled traffic.
        f.api('/api/nodes/'+f.survivor.id+'/traffic', {})
    else:
        assert f.api('/api/nodes/'+f.old.id+'/core/stop', {})['executed']
        meter(f)
    assert not f.old.engine.running
    paid = usage(f)
    receipt = f.api(prefix(f, prepared)+'/commit', {
        'sourceBindingId':prepared['source_binding_id'],
        'acceptUnconfirmedOldServer':True, 'acceptUnreportedTraffic':True})
    assert receipt['binding_committed'] and receipt['generation'] == 2, receipt
    assert receipt['old_stop_confirmed'] is False and receipt['traffic_tail_complete'] is False
    assert not f.reg.get(f.old.id)['enabled'] and not f.new.engine.running
    assert set(f.links()) == {f.survivor.data_port}
    assert usage(f) == paid and contract(f) == before
    binding = f.reg.installations.capture(f.old.id)
    assert binding['agent_id'] == f.new.id and binding['installation_id'] == f.new.runtime.installation_id
    assert binding['installation_id'] != f.old_binding['installation_id']
    history = f.reg.installations.history(f.old.id)
    assert len(history) == 2 and history[0]['retired_at'] > 0 and history[1]['retired_at'] == 0
    retirement = json.loads(history[0]['retirement_json'])
    assert sum(r['current_up']+r['current_down'] for r in retirement['usage']) > 0
    f.evidence.update(charged_before_replacement=paid[0], generations=len(history),
                      source_management_offline=old_offline)
    return prepared, receipt, before, paid


def stage(f, receipt):
    return f.api(prefix(f, receipt)+'/stage', {'bindingId':receipt['committed_binding_id']})


def review(f, receipt):
    result = f.api(prefix(f, receipt)+'/activation/review', {'bindingId':receipt['committed_binding_id']})
    assert result['review_ready'], result
    return result


def consent(reviewed):
    return {'bindingId':reviewed['binding_id'], 'reviewHash':reviewed['review_hash'],
            'confirmStart':True, 'acceptEndpointResponsibility':True,
            'acceptUnconfirmedOldServer':True, 'acceptUnreportedTraffic':True}


def activate(f, receipt, reviewed):
    return f.api(prefix(f, receipt)+'/activation/start', consent(reviewed))


def assert_staged(f, result):
    assert result['configuration_staged'] and result['activation_held'], result
    assert not f.new.engine.running and not f.reg.get(f.old.id)['enabled']
    assert f.new.runtime.command_status()['action'] == 'stop'
    assert f.new.runtime.status()['appliedRevision'] == result['desired_revision']
    assert f.new.runtime.status()['appliedHash'] == result['desired_hash']
    mirror = f.new.runtime.mirror_for_source(EMAIL)
    assert f.new.engine.client_detail(mirror)['client']['id'] == stable_identity(f)['id']
    assert all(row['traffic'] == {'up':0,'down':0} for row in f.new.engine.clients())
    transfer(f.clients[0].port, f.target, allowed=False)
    assert set(f.links()) == {f.survivor.data_port}


def meter_replaced(f):
    for node, physical in ((f.old.id, f.new), (f.survivor.id, f.survivor)):
        physical.engine.collect_stats(force=True, strict=True)
        f.api('/api/nodes/'+node+'/traffic', {})
    return usage(f)


def prove_new_traffic(f, receipt, reviewed, before, paid):
    result = activate(f, receipt, reviewed)
    assert result['activation_completed'] and result['service_activated'], result
    assert f.new.engine.running and not f.old.engine.running
    assert not result['network_verified'] and not result['dns_or_tunnel_changed']
    f.reg.probe(f.old.id)
    links = f.links()
    assert set(links) == {f.old.data_port, f.survivor.data_port}
    # Consume the NEW raw subscription, not a handcrafted UUID. No direct route.
    with xray_client(f.root/'replacement-client', f.binary.path, links[f.old.data_port]) as client:
        received = transfer(client.port, f.target)
    assert_survivor(f)
    after = meter_replaced(f)
    assert after[0] >= paid[0]+received and after[2] == after[0]
    old_usage = {node:(up,down) for node,up,down in paid[1]}
    assert all(up >= old_usage[node][0] and down >= old_usage[node][1] for node,up,down in after[1])
    assert meter_replaced(f) == after, 'replacement counters were charged twice'
    assert contract(f) == before
    f.evidence.update(charged_after_replacement=after[0], replacement_body_bytes=received,
        old_core_stopped=True, surviving_core_pid_unchanged=True, master_contract_preserved=True,
        duplicate_snapshot_unchanged=True)
    return result


@pytest.mark.parametrize('old_offline', [False, True])
def test_complete_real_replacement_keeps_account_and_surviving_node(replacement_lab, old_offline):
    f = replacement_lab
    prepared, receipt, before, paid = begin(f, old_offline=old_offline)
    assert_staged(f, stage(f, receipt))
    prove_new_traffic(f, receipt, review(f, receipt), before, paid)
    with xray_client(f.root/'wrong-replacement-client', f.binary.path,
                     f.links()[f.old.data_port], wrong_uuid=True) as bad:
        transfer(bad.port, f.target, allowed=False)
    f.evidence['wrong_uuid_rejected_on_replacement'] = True


@pytest.mark.parametrize('drop_path', [
    '/node/api/v1/replacement/rotate-token',
    '/node/api/v1/state/apply',
    '/node/api/v1/control/activate',
])
def test_lost_real_https_reply_resumes_same_replacement(replacement_lab, drop_path):
    f = replacement_lab
    _, receipt, before, paid = begin(f, drop_prepare=drop_path.endswith('rotate-token'))
    if drop_path.endswith('state/apply'):
        f.lost.next_path = drop_path
        failed = stage(f, receipt)
        assert not failed['configuration_staged'] and failed['activation_held'], failed
        assert not f.new.engine.running and not f.reg.get(f.old.id)['enabled']
        version = f.new.runtime.status()['appliedRevision']
        command = f.new.runtime.command_status()
        assert version > 0
        assert_staged(f, stage(f, receipt))
        assert f.new.runtime.status()['appliedRevision'] == version
        assert f.new.runtime.command_status() == command
    else:
        assert_staged(f, stage(f, receipt))
    reviewed = review(f, receipt)
    if drop_path.endswith('control/activate'):
        f.lost.next_path = drop_path
        failed = activate(f, receipt, reviewed)
        assert failed['requires_retry'] and failed['target_may_be_running'], failed
        assert not failed['service_activated'] and not f.reg.get(f.old.id)['enabled']
        assert set(f.links()) == {f.survivor.data_port}
        assert f.new.engine.running
        pid, command = f.new.engine.process.pid, f.new.runtime.command_status()
        # A known direct address can be used while the Hub still awaits the ACK.
        transferred = transfer(f.clients[0].port, f.target)
        f.new.engine.collect_stats(force=True, strict=True)
        assert activate(f, receipt, reviewed)['service_activated']
        assert usage(f)[0] >= paid[0]+transferred
        assert f.new.engine.process.pid == pid and f.new.runtime.command_status() == command
        f.evidence['pending_start_real_traffic_accounted'] = True
    prove_new_traffic(f, receipt, reviewed, before, paid)
    assert f.lost.dropped == [{'path':drop_path, 'status_before_drop':200}]
    f.evidence['lost_reply_recovered'] = drop_path


def test_completed_replacement_receipts_never_reexecute_or_reenable(replacement_lab):
    f = replacement_lab
    prepared, receipt, before, paid = begin(f)
    assert_staged(f, stage(f, receipt))
    reviewed = review(f, receipt)
    prove_new_traffic(f, receipt, reviewed, before, paid)
    pid = f.new.engine.process.pid; command = f.new.runtime.command_status()
    checkpoint = usage(f); calls = list(f.new.wire.calls)
    duplicate = f.api(prefix(f, prepared)+'/commit', {
        'sourceBindingId':prepared['source_binding_id'],
        'acceptUnconfirmedOldServer':True, 'acceptUnreportedTraffic':True})
    assert duplicate['committed_binding_id'] == receipt['committed_binding_id']
    assert activate(f, receipt, reviewed)['service_activated']
    assert f.new.wire.calls == calls and usage(f) == checkpoint
    assert f.new.engine.process.pid == pid and f.new.runtime.command_status() == command
    f.reg.set_enabled(f.old.id, False)
    assert activate(f, receipt, reviewed)['service_activated'] is False
    assert not f.reg.get(f.old.id)['enabled'] and f.new.wire.calls == calls
    assert len(f.reg.installations.history(f.old.id)) == 2
    f.evidence['terminal_retries_do_not_reactivate'] = True


def test_pause_after_lost_start_preserves_actual_bytes_then_reactivate(replacement_lab):
    f = replacement_lab
    _, receipt, before, paid = begin(f)
    assert_staged(f, stage(f, receipt)); reviewed = review(f, receipt)
    f.lost.next_path = '/node/api/v1/control/activate'
    assert activate(f, receipt, reviewed)['requires_retry']
    received = transfer(f.clients[0].port, f.target)
    first_command = f.new.runtime.command_status()['command_id']
    paused = f.api(prefix(f, receipt)+'/activation/pause', {
        'bindingId':receipt['committed_binding_id'], 'confirmStop':True})
    assert paused['phase'] == 'paused' and not paused['target_may_be_running'], paused
    assert not f.new.engine.running and not f.reg.get(f.old.id)['enabled']
    transfer(f.clients[0].port, f.target, allowed=False)
    assert usage(f)[0] >= paid[0]+received
    fresh = review(f, receipt)
    assert fresh['review_hash'] != reviewed['review_hash']
    prove_new_traffic(f, receipt, fresh, before, usage(f))
    assert f.new.runtime.command_status()['command_id'] != first_command
    f.evidence['pause_metered_real_tail_before_new_review'] = True


def test_old_installation_cannot_charge_buffered_real_sample_after_replacement(replacement_lab):
    f = replacement_lab
    for client in f.clients: transfer(client.port, f.target)
    meter(f)
    sample, _ = f.reg._request(f.old.id, '/node/api/mirrors/traffic')
    _, receipt, before, paid = begin(f)
    assert_staged(f, stage(f, receipt))
    prove_new_traffic(f, receipt, review(f, receipt), before, paid)
    checkpoint = usage(f)
    # Model only delayed completion's saved installation context, using a real
    # earlier HTTPS sample. No counter values or HTTP success are invented.
    token = f.reg.installations._contexts.set({f.old.id:f.old_binding})
    try:
        with pytest.raises(StaleInstallation):
            f.reg.apply_traffic_snapshot(f.old.id, sample['items'])
    finally:
        f.reg.installations._contexts.reset(token)
    assert usage(f) == checkpoint
    f.evidence['retired_context_real_sample_rejected'] = True

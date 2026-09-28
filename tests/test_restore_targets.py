import base64
import copy
import json
from collections import Counter
from urllib.parse import urlsplit
import pytest
from fastapi.testclient import TestClient
from test_standalone import env, IB, OWNER
from test_hosts_v3 import host


def setup(env, monkeypatch, count=2):
    store, engine, manager, auth, c = env
    ib = copy.deepcopy(IB)
    ib['panelMeta'] = {'deployLocal': True, 'tunnelPorts': {'local': 20111, 'node:n1': 20111, 'node:n2': 20112}}
    iid = c.post('/api/inbounds', json=ib).json()['id']
    restore = c.app.state.dark_restore
    nodes = [{'id': nid, 'name': name, 'data_address': address, 'online': True, 'enabled': True, 'last_error': '',
              'assignments': [{'local_inbound_id': iid, 'remote_inbound_id': 1, 'deployed': True, 'last_error': ''}]}
             for nid, name, address in [('n1', 'Armenia', 'am.example.test'), ('n2', 'Poland', 'pl.example.test'), ('n3', 'United Kingdom', 'uk.example.test')]]
    monkeypatch.setattr(restore.nodes, 'list', lambda: copy.deepcopy(nodes))
    monkeypatch.setattr(restore, '_scan', lambda url: {'status': 'verified', 'error': '', 'upload': 3000, 'download': 7000, 'total': 1000000, 'expire': 2000000000})
    urls = ['https://legacy.example/sub/test-' + str(i) for i in range(count)]
    r = c.post('/api/dark-restore/import', json={'urls': urls, 'inboundIds': [iid], 'groupName': 'Representative A', 'scan': True})
    assert r.status_code == 200, r.text
    gid = r.json()['group']['id']
    return store, engine, c, restore, iid, gid, nodes


def links(restore, gid, fmt='json'):
    r = restore.rows(gid)[0]
    body, headers = restore.subscription(r['public_token'], fmt, record_access=False)
    return (json.loads(body)['links'] if fmt == 'json' else body), headers


def payload(iid, **kw):
    return {'inboundIds': [iid], 'nodeIds': [], 'nodeMode': 'all', 'includeLocal': True, **kw}


def apply(c, gid, value):
    p = c.post('/api/dark-restore/groups/' + gid + '/mapping/preview', json=value)
    assert p.status_code == 200, p.text
    return c.put('/api/dark-restore/groups/' + gid + '/mapping', json={**value, 'expectedRevision': p.json()['revision']})


def identity_snapshot(store):
    queries = [
        'SELECT id,public_token,legacy_url,core_email,legacy_upload,legacy_download,legacy_total,legacy_expire,group_id,first_seen,last_seen FROM restore_subscriptions ORDER BY id',
        'SELECT email,body,up,down FROM core_clients ORDER BY email',
        'SELECT * FROM restore_usage ORDER BY restore_id,scope',
        'SELECT * FROM managed_clients ORDER BY email',
        'SELECT * FROM api_admins ORDER BY id',
    ]
    return [[tuple(r) for r in store.db.execute(q)] for q in queries]


def test_whole_inbound_import_includes_all_nodes_without_direct_hosts(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    rows = restore.rows(gid)
    assert all(r['node_mode'] == 'all' for r in rows)
    out, _ = links(restore, gid)
    assert Counter((x['runtime'], x['endpointType']) for x in out) == Counter({('local', 'direct'): 1, ('node:n1', 'direct'): 1, ('node:n2', 'direct'): 1, ('node:n3', 'direct'): 1})
    assert {urlsplit(x['uri']).port for x in out} == {IB['port']}
    assert engine.section('hosts') == []
    assert c.get('/api/clients').json() == []
    assert c.get('/api/unmanaged').json() == []


def test_multi_address_tunnels_and_explicit_direct_do_not_duplicate_directs(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    def h(runtime, kind, address, port, **kw):
        return host(iid, runtime=runtime, endpointType=kind, address=address, port=port, remark=runtime,
                    security='same', sni='', host='', path='', alpn='', fingerprint='', allowInsecure=False,
                    finalMask='', mihomoIpVersion='', **kw)
    hosts = [h('local', 'tunnel', 'ir1.example.test', 20111, addresses=['ir1.example.test', 'ir2.example.test']),
             h('node:n1', 'tunnel', 'ir3.example.test', 20111, addresses=['ir3.example.test', 'ir4.example.test']),
             h('node:n2', 'tunnel', 'ir5.example.test', 20112),
             h('node:n2', 'direct', 'explicit-pl.example.test', IB['port'])]
    r = c.put('/api/settings/hosts', json={'value': hosts}); assert r.status_code == 200, r.text
    before = copy.deepcopy(engine.section('hosts'))
    out, _ = links(restore, gid)
    directs = [x for x in out if x['endpointType'] == 'direct']
    assert len(out) == 9 and len(directs) == 4
    assert Counter(x['runtime'] for x in directs) == Counter({'local': 1, 'node:n1': 1, 'node:n2': 1, 'node:n3': 1})
    assert urlsplit(next(x for x in directs if x['runtime'] == 'node:n2')['uri']).hostname == 'explicit-pl.example.test'
    assert all(urlsplit(x['uri']).port == IB['port'] for x in directs)
    assert Counter(x['runtime'] for x in out if x['endpointType'] == 'tunnel') == Counter({'local': 2, 'node:n1': 2, 'node:n2': 1})
    assert engine.section('hosts') == before
    # The native generator is unchanged and still has its own failover pathway.
    native = engine.links(restore.rows(gid)[0]['core_email'], runtime_ready={'local': {iid}})
    assert {x['runtime'] for x in native['links']} == {'local'}


@pytest.mark.parametrize('fmt', ['raw', 'base64', 'json', 'clash'])
def test_every_subscription_format_contains_runtime_directs_and_legacy_headers(env, monkeypatch, fmt):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    result, headers = links(restore, gid, fmt)
    text = json.dumps(result) if fmt == 'json' else (base64.b64decode(result) if fmt == 'base64' else result).decode()
    for address in ['am.example.test', 'pl.example.test', 'uk.example.test']:
        assert address in text
    assert headers['subscription-userinfo'] == 'upload=3000; download=7000; total=1000000; expire=2000000000'
    assert all(r['first_seen'] == 0 for r in restore.rows(gid))


def test_offline_unassigned_and_pending_nodes_are_not_published(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    nodes[0]['online'] = False
    nodes[1]['assignments'][0]['deployed'] = False
    nodes[2]['assignments'][0]['local_inbound_id'] = 999
    out, _ = links(restore, gid)
    assert [(x['runtime'], x['endpointType']) for x in out] == [('local', 'direct')]


def test_node_only_and_explicit_hub_only_modes(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    r = apply(c, gid, payload(iid, nodeMode='selected', nodeIds=['n1'], includeLocal=False))
    assert r.status_code == 200, r.text
    assert [x['runtime'] for x in links(restore, gid)[0]] == ['node:n1']
    r = apply(c, gid, payload(iid, nodeMode='selected'))
    assert r.status_code == 200
    assert [x['runtime'] for x in links(restore, gid)[0]] == ['local']


def test_all_mode_follows_new_assignments_without_changing_identity(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    before = identity_snapshot(store)
    nodes.append({**copy.deepcopy(nodes[0]), 'id': 'n4', 'name': 'New Node', 'data_address': 'new.example.test'})
    assert len(links(restore, gid)[0]) == 5
    assert identity_snapshot(store) == before


def test_bulk_group_edit_preserves_every_identity_quota_usage_and_other_group(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch, 61)
    other = c.post('/api/dark-restore/import', json={'urls': ['https://other.example/sub/one'], 'inboundIds': [iid], 'groupName': 'Other', 'nodeMode': 'selected'})
    assert other.status_code == 200
    r0 = restore.rows(gid)[0]
    store.db.execute('UPDATE core_clients SET up=123,down=456 WHERE email=?', (r0['core_email'],))
    before = identity_snapshot(store)
    r = apply(c, gid, payload(iid, nodeMode='selected', nodeIds=['n1']))
    assert r.status_code == 200, r.text
    assert r.json()['updated'] == 61 and r.json()['core_changed'] is False
    assert identity_snapshot(store) == before
    assert len(links(restore, gid)[0]) == 2
    assert len(links(restore, other.json()['group']['id'])[0]) == 1
    again = apply(c, gid, payload(iid, nodeMode='selected', nodeIds=['n1']))
    assert again.json()['updated'] == 0


def test_group_preview_is_read_only_and_stale_revision_is_rejected(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    before = identity_snapshot(store)
    preview = c.post('/api/dark-restore/groups/' + gid + '/mapping/preview', json=payload(iid)).json()
    assert identity_snapshot(store) == before
    r0 = restore.rows(gid)[0]
    r = c.put('/api/dark-restore/' + r0['id'] + '/mapping', json=payload(iid, nodeMode='selected'))
    assert r.status_code == 200
    r = c.put('/api/dark-restore/groups/' + gid + '/mapping', json={**payload(iid), 'expectedRevision': preview['revision']})
    assert r.status_code == 409
    assert c.get('/api/dark-restore/groups/' + gid + '/mapping').json()['mixed'] is True


@pytest.mark.parametrize('changes', [{'nodeIds': ['unknown'], 'nodeMode': 'selected'}, {'inboundIds': [999]}, {'nodeMode': 'all', 'nodeIds': ['n1']}, {'includeLocal': False, 'nodeMode': 'selected'}])
def test_invalid_group_selection_never_partially_updates(env, monkeypatch, changes):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    before = identity_snapshot(store)
    r = c.post('/api/dark-restore/groups/' + gid + '/mapping/preview', json=payload(iid, **changes))
    assert r.status_code in (400, 422)
    assert identity_snapshot(store) == before


def test_bulk_inbound_change_is_atomic_and_updates_core_assignments_only(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    ib = {**IB, 'tag': 'second-inbound', 'port': 19444}
    iid2 = c.post('/api/inbounds', json=ib).json()['id']
    before = identity_snapshot(store)
    r = apply(c, gid, payload(iid2, nodeMode='selected'))
    assert r.status_code == 200 and r.json()['core_changed'] is True
    assert identity_snapshot(store) == before
    assert all(r['inbound_ids'] == [iid2] for r in restore.rows(gid))
    assert all(json.loads(r[0]) == [iid2] for r in store.db.execute('SELECT inbounds FROM core_clients'))


def test_missing_core_identity_rolls_back_whole_group(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    r0 = restore.rows(gid)[0]
    store.db.execute('DELETE FROM core_clients WHERE email=?', (r0['core_email'],))
    r = apply(c, gid, payload(iid, nodeMode='selected'))
    assert r.status_code == 409
    assert all(r['node_mode'] == 'all' for r in restore.rows(gid))


def test_group_mapping_is_owner_only_and_csrf_protected(env, monkeypatch):
    store, engine, c, restore, iid, gid, nodes = setup(env, monkeypatch)
    path = '/api/dark-restore/groups/' + gid + '/mapping'
    with TestClient(c.app, base_url=engine.config.public_origin) as anonymous:
        assert anonymous.get(path).status_code == 401
        assert anonymous.get('/api/dark-restore/targets').status_code == 401
    manager, auth = env[2], env[3]
    manager.owner_put(OWNER, 'rep', name='Rep', allowed=[iid])
    auth.admin_create(OWNER, 'rep', 'RepresentativePassword123', 'reseller', {})
    with TestClient(c.app, base_url=engine.config.public_origin) as rep:
        login = rep.post('/api/auth/login', json={'username': 'rep', 'password': 'RepresentativePassword123'})
        assert login.status_code == 200
        rep.headers['X-Dark-CSRF'] = login.json()['csrf']
        assert rep.get(path).status_code == 403
        assert rep.post(path + '/preview', json=payload(iid)).status_code == 403
    old = c.headers.pop('X-Dark-CSRF')
    try:
        assert c.post(path + '/preview', json=payload(iid)).status_code == 403
    finally:
        c.headers['X-Dark-CSRF'] = old

"""FastPath V2 read-only correctness, isolation and bounded error behavior."""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from auth import Auth, DEFAULTS
from core import Config, CoreEngine
from dashboard_read_model import DashboardReadModel, DashboardUnavailable
from dark_policy import Actor, Store
from manager import Manager
from server import make_app


OWNER = Actor('dark', 'owner', {})


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / 'dark.sqlite3')
    cfg = Config(public_origin='http://127.0.0.1:2087',
                 xray_binary=str(tmp_path / 'missing-xray'),
                 xray_assets=str(tmp_path), test_engine=True)
    engine = CoreEngine(cfg, store, tmp_path / 'runtime')
    manager = Manager(store, engine)
    auth = Auth(store, tmp_path / 'secret.key')
    auth.bootstrap('dark', 'OwnerPass88')
    manager.owner_put(OWNER, 'dark', name='Main', allowed=[])
    manager.owner_put(OWNER, 'seller', name='Seller', allowed=[])
    auth.admin_create(OWNER, 'seller', 'SellerPass88', 'reseller', DEFAULTS['reseller'])
    # make_app initializes the NodeRegistry schema used by this fixture.
    app = make_app(manager, auth, background=False)
    with store.transaction() as db:
        db.executemany(
            'INSERT INTO clients (id,owner,quota_bytes,used_bytes,manual,expires_at) VALUES (?,?,?,?,?,?)',
            [('active-1','dark',1000,250,0,0),
             ('exhausted','dark',500,500,0,0),
             ('disabled','dark',0,0,1,0),
             ('rep-1','seller',0,375,0,0)])
        db.execute('INSERT INTO core_inbounds(body) VALUES(?)',
                   (json.dumps({'remark':'Test','enable':True}),))
        db.execute('INSERT INTO remote_nodes (id,name,origin,token_enc,enabled,created_at,updated_at,last_seen) VALUES (?,?,?,?,?,?,?,?)',
                   ('test-node','Test Node','https://node.example.test','x',1,time.time(),time.time(),time.time()))
    try:
        yield store, app
    finally:
        manager.close()
        engine.close()
        store.close()


def test_read_model_uses_current_counters_and_never_scans_ledger(env):
    store, _ = env
    model = DashboardReadModel(store.path, ttl=0)
    before = store.db.total_changes
    started = time.monotonic()
    doc = model.get(role='owner', account='dark')
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, elapsed
    assert doc['read_only'] is True and doc['accounting_mutations'] is False
    assert doc['clients']['total'] == 4
    assert doc['clients']['policy_eligible'] == 2
    assert doc['clients']['policy_blocked'] == 2
    assert doc['clients']['current_stored_usage_bytes'] == 1125
    assert doc['clients']['presence_is_live'] is False
    assert doc['clients']['usage_scope'] == 'current-stored-counters-not-lifetime-ledger'
    assert doc['inbounds'] == {'total':1,'enabled':1}
    assert doc['representatives'] == {'total':1,'enabled':1}
    assert doc['nodes']['recent_heartbeat'] == 1
    assert doc['nodes']['status_source'] == 'cached-heartbeat-only-not-route-readiness'
    assert store.db.total_changes == before


def test_read_model_scopes_reseller_and_keeps_exact_large_counters(env):
    store, _ = env
    max_i64 = 2**63 - 1
    with store.transaction() as db:
        db.execute('UPDATE clients SET used_bytes=? WHERE id=?', (max_i64, 'active-1'))
        db.execute('UPDATE clients SET used_bytes=? WHERE id=?', (max_i64, 'rep-1'))
    model = DashboardReadModel(store.path, ttl=0)
    owner = model.get(role='owner', account='dark')
    reseller = model.get(role='reseller', account='seller')
    assert owner['clients']['current_stored_usage_bytes'] == 2*max_i64 + 500
    assert reseller['clients']['total'] == 1
    assert reseller['clients']['current_stored_usage_bytes'] == max_i64
    assert reseller['inbounds'] is None and reseller['nodes'] is None
    assert reseller['representatives'] is None
    with pytest.raises(PermissionError):
        model.get(role='readonly', account='unknown')


def test_read_model_returns_stale_only_with_an_actual_recent_snapshot(env, monkeypatch):
    store, _ = env
    model = DashboardReadModel(store.path, ttl=0, stale_seconds=90)
    good = model.get(role='owner', account='dark')
    def fail_read(*_args):
        raise sqlite3.OperationalError('database file: /secret/path is locked')
    monkeypatch.setattr(model, '_read', fail_read)
    stale = model.get(role='owner', account='dark')
    assert stale['freshness'] == 'stale'
    assert stale['generated_at'] == good['generated_at']
    assert stale['age_seconds'] >= 0
    cold = DashboardReadModel(store.path, ttl=0)
    monkeypatch.setattr(cold, '_read', fail_read)
    with pytest.raises(DashboardUnavailable, match='unavailable') as ex:
        cold.get(role='owner', account='dark')
    assert '/secret/path' not in str(ex.value)


def test_authenticated_dashboard_endpoint_is_permission_scoped(env):
    store, app = env
    with TestClient(app, base_url='http://127.0.0.1:2087') as owner:
        assert owner.get('/api/dashboard/summary').status_code == 401
        r = owner.post('/api/auth/login',
                       json={'username':'dark','password':'OwnerPass88','otp':''})
        assert r.status_code == 200
        summary = owner.get('/api/dashboard/summary')
        assert summary.status_code == 200, summary.text
        assert summary.json()['clients']['total'] == 4
        assert summary.json()['scope'] == 'owner'
    with TestClient(app, base_url='http://127.0.0.1:2087') as seller:
        r = seller.post('/api/auth/login',
                        json={'username':'seller','password':'SellerPass88','otp':''})
        assert r.status_code == 200
        summary = seller.get('/api/dashboard/summary')
        assert summary.status_code == 200, summary.text
        data = summary.json()
        assert data['scope'] == 'own-clients'
        assert data['clients']['total'] == 1
        assert data['representatives'] is None
        assert data['nodes'] is None

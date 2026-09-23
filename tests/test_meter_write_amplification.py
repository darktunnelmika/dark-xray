"""Unchanged samples must not manufacture durable traffic generations.

These use the real manager and SQLite, with the project's test engine. They are
accounting/write-amplification regressions, not a WAN or production SLA claim.
"""
import json
import sqlite3
import time

import pytest

from core import CoreError
from dark_policy import Actor, MAX_INT, PolicyError
from test_bulk_batching import make_env, inbound

OWNER = Actor('dark', 'owner', {})


@pytest.fixture
def fleet(tmp_path):
    store, engine, manager, client = make_env(tmp_path)
    try:
        number = client.post('/api/inbounds', json=inbound('meter', 19891)).json()['id']
        result = client.post('/api/clients/bulk-create', json={
            'owner': 'dark', 'prefix': 'meter-', 'postfix': '', 'first': 1,
            'quantity': 20, 'inboundIds': [number], 'client': {'limitIp': 0}})
        assert result.status_code == 200 and result.json()['created'] == 20
        yield store, engine, manager
    finally:
        client.__exit__(None, None, None)
        manager.close(); engine.close(); store.close()


def record(engine, up, down):
    return {**engine.client_detail('meter-1')['client'], 'traffic': {'up': up, 'down': down}}


def ledger(store):
    return [tuple(r) for r in store.db.execute('SELECT * FROM traffic_ledger ORDER BY event_id')]


def meter(fleet, up, down):
    store, engine, manager = fleet
    manager._charge_snapshot(manager.meta('meter-1'), record(engine, up, down))
    return manager.meta('meter-1')


def test_unchanged_full_tick_does_not_write_per_customer(fleet):
    store, engine, manager = fleet
    before = store.db.total_changes
    seq = [tuple(r) for r in store.db.execute('SELECT email,seq FROM managed_clients ORDER BY email')]
    manager.tick(); manager.tick()
    assert manager.last_error == ''
    assert store.db.total_changes == before
    assert [tuple(r) for r in store.db.execute('SELECT email,seq FROM managed_clients ORDER BY email')] == seq
    assert store.db.execute('PRAGMA synchronous').fetchone()[0] == 2  # FULL retained
    assert store.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'


def test_repeated_nonzero_sample_preserves_sequence_and_ledger(fleet):
    store, engine, manager = fleet
    first = meter(fleet, 100, 200); before = store.db.total_changes; events = ledger(store)
    for _ in range(5): assert meter(fleet, 100, 200)['seq'] == first['seq']
    assert ledger(store) == events and store.db.total_changes == before
    assert manager.detail(OWNER, 'meter-1')['used_bytes'] == 300
    assert meter(fleet, 120, 240)['seq'] == first['seq'] + 1
    assert sum(r[4] + r[5] for r in ledger(store)) == 360


@pytest.mark.parametrize('up,down,total', [(0, 0, 300), (10, 20, 330), (0, 250, 350), (110, 0, 310)])
def test_counter_decrease_and_new_epoch_are_still_metered(fleet, up, down, total):
    store, engine, manager = fleet
    first = meter(fleet, 100, 200)
    second = meter(fleet, up, down)
    assert second['seq'] == first['seq'] + 1
    assert manager.detail(OWNER, 'meter-1')['used_bytes'] == up + down
    assert sum(r[4] + r[5] for r in ledger(store)) == total
    stable = store.db.total_changes
    meter(fleet, up, down)
    assert store.db.total_changes == stable


def test_remote_only_change_is_not_hidden_by_stable_local_counters(fleet):
    store, engine, manager = fleet
    first = meter(fleet, 100, 200); events = ledger(store)
    with store.transaction() as db:
        db.execute('''INSERT INTO remote_node_client_usage(node_id,client_id,current_up,current_down)
                      VALUES('remote','meter-1',400,500)''')
    meter(fleet, 100, 200)
    assert manager.detail(OWNER, 'meter-1')['used_bytes'] == 1200
    assert manager.meta('meter-1')['seq'] == first['seq'] and ledger(store) == events
    with store.transaction() as db:
        db.execute('UPDATE remote_node_client_usage SET current_up=0,current_down=0')
    meter(fleet, 100, 200)
    assert manager.detail(OWNER, 'meter-1')['used_bytes'] == 300 and ledger(store) == events


def test_uninitialized_zero_sample_is_recorded_once(fleet):
    store, engine, manager = fleet
    with store.transaction() as db: db.execute("UPDATE managed_clients SET initialized=0 WHERE email='meter-1'")
    first = meter(fleet, 0, 0)
    assert first['initialized'] == 1
    before = store.db.total_changes
    meter(fleet, 0, 0)
    assert store.db.total_changes == before


def test_wrong_cached_policy_total_is_repaired_without_new_local_event(fleet):
    store, engine, manager = fleet
    first = meter(fleet, 100, 200); events = ledger(store)
    with store.transaction() as db: db.execute("UPDATE clients SET used_bytes=999 WHERE id='meter-1'")
    meter(fleet, 100, 200)
    assert manager.detail(OWNER, 'meter-1')['used_bytes'] == 300
    assert manager.meta('meter-1')['seq'] == first['seq'] and ledger(store) == events


@pytest.mark.parametrize('fault', ['missing-traffic', 'overflow', 'write-failure'])
def test_errors_do_not_advance_checkpoint_or_partial_ledger(fleet, fault):
    store, engine, manager = fleet
    before = meter(fleet, 100, 200); events = ledger(store)
    sample = record(engine, 120, 230)
    error = (CoreError, PolicyError, sqlite3.IntegrityError)
    if fault == 'missing-traffic': sample.pop('traffic')
    elif fault == 'overflow': sample['traffic'] = {'up': MAX_INT, 'down': 1}
    else:
        store.db.execute('''CREATE TEMP TRIGGER fail_meter BEFORE UPDATE OF last_up ON managed_clients
                            BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END''')
    with pytest.raises(error): manager._charge_snapshot(before, sample)
    assert manager.meta('meter-1') == before and ledger(store) == events
    assert manager.detail(OWNER, 'meter-1')['used_bytes'] == 300
    if fault == 'write-failure':
        store.db.execute('DROP TRIGGER fail_meter')
        meter(fleet, 120, 230)
        assert sum(r[4] + r[5] for r in ledger(store)) == 350


def test_idle_local_sample_still_checks_remote_overflow(fleet):
    store, engine, manager = fleet
    before = meter(fleet, 10, 0); events = ledger(store)
    with store.transaction() as db:
        db.execute("INSERT INTO remote_node_client_usage(node_id,client_id,current_up) VALUES('remote','meter-1',?)", (MAX_INT,))
    with pytest.raises(PolicyError, match='overflow'): meter(fleet, 10, 0)
    assert manager.meta('meter-1') == before and ledger(store) == events


@pytest.mark.parametrize('blocker', ['manual', 'global_ip_block', 'global_device_block', 'expiry', 'quota'])
def test_policy_still_applies_when_counters_have_not_changed(fleet, blocker):
    store, engine, manager = fleet
    patch = {}
    if blocker in {'manual', 'global_ip_block', 'global_device_block'}:
        with store.transaction() as db: db.execute(f"UPDATE clients SET {blocker}=1 WHERE id='meter-1'")
    elif blocker == 'expiry': patch['expiryTime'] = int((time.time()-30)*1000)
    else:
        patch['totalGB'] = 300
        with store.transaction() as db: db.execute("UPDATE core_clients SET up=100,down=200 WHERE email='meter-1'")
    if patch: manager.update(OWNER, 'meter-1', patch, reconcile=False)
    manager.tick()
    assert engine.client_detail('meter-1')['client']['enable'] is False
    assert engine.client_detail('meter-2')['client']['enable'] is True
    events = ledger(store)
    manager.tick()
    assert engine.client_detail('meter-1')['client']['enable'] is False
    assert ledger(store) == events


def test_patch_diagnostics_are_bounded_and_preserve_failures():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).with_name('load-scale-smoke.py')
    spec = importlib.util.spec_from_file_location('load_diagnostics', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    stats = module.PatchMetrics()
    assert stats.summary() == {'finished': 0, 'accepted': 0, 'max_seconds': 0, 'samples': []}
    stats.record(1, 45, 'timeout'); stats.record(0, .5, 'http_202')
    for i in range(2, 120): stats.record(i, 1, 'http_202')
    result = stats.summary()
    assert result['finished'] == 100 and result['accepted'] == 99
    assert result['max_seconds'] == 45
    assert result['samples'][1]['outcome'] == 'timeout'
    result['samples'].clear()
    assert stats.summary()['finished'] == 100

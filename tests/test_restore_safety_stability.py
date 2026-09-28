import contextlib
from test_restore_groups import env, prepare, add, row
from test_restore_safety import review_value


def test_idle_restore_reconciliation_has_no_write_transactions(env, monkeypatch):
    store, engine, c, restore, inbound = prepare(env, monkeypatch)
    add(c, inbound)
    engine.clients()
    transactions = 0
    original = store.transaction
    @contextlib.contextmanager
    def counted():
        nonlocal transactions
        transactions += 1
        with original() as db:
            yield db
    monkeypatch.setattr(store, 'transaction', counted)
    changes = store.db.total_changes
    for _ in range(5):
        engine.clients()
    assert transactions == 0
    assert store.db.total_changes == changes


def test_manual_review_frozen_on_repeat_import(env, monkeypatch):
    store, engine, c, restore, inbound = prepare(env, monkeypatch)
    first = add(c, inbound, scan=False)
    item = row(c)
    reviewed = c.put('/api/dark-restore/'+item['id']+'/metadata', json=review_value(item, total=50000, expire=2000000000))
    assert reviewed.status_code == 200
    def no_scan(url):
        raise AssertionError('Must not scan a manually reviewed migration again')
    monkeypatch.setattr(restore, '_scan', no_scan)
    old = row(c)
    add(c, inbound, group='', groupId=first['group']['id'])
    current = row(c)
    assert current['metadata_state'] == 'manual'
    for key in ('legacy_upload','legacy_download','legacy_total','legacy_expire','public_token','core_email','dark_used'):
        assert current[key] == old[key]

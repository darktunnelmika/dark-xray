"""Keep bot reads proportional to current clients, not traffic history size."""
from types import SimpleNamespace

import pytest

from core import Config, CoreEngine
from dark_policy import Actor, Store
from manager import Manager
from telegram_runtime import BotWorker
from test_standalone import env, create


def test_activity_reads_remain_bounded_with_long_client_history(tmp_path, monkeypatch):
    store = Store(tmp_path / 'dark.sqlite3')
    engine = CoreEngine(Config(test_engine=True), store, tmp_path / 'runtime')
    manager = Manager(store, engine)
    monkeypatch.setattr(engine, 'read_ip_log', lambda: None)
    with store.transaction() as db:
        db.executemany('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',
                       ((f'old-{n}', 'dark', 'alpha', 0, 1, 2, float(n))
                        for n in range(20000)))
        db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',
                   ('beta-event', 'dark', 'beta', 0, 7, 8, 25000.0))
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',
                   ('beta', '203.0.113.9', 'node', 24000.0, 26000.0, 1))
    changes = store.db.total_changes
    work = 0

    def budget():
        nonlocal work
        work += 100
        # No wall-clock assertion: fail if a read scans the 20,000-event history.
        return int(work > 20000)

    store.db.set_progress_handler(budget, 100)
    try:
        one = manager._activity_for('alpha')
        activity = manager._activity_map(['alpha', 'beta', 'missing'])
    finally:
        store.db.set_progress_handler(None, 0)
    assert one['activity_at'] == 19999.0
    assert activity['alpha']['activity_at'] == 19999.0
    assert activity['beta']['activity_at'] == 26000.0
    assert activity['beta']['presence_source'] == 'access'
    assert 'missing' not in activity
    assert store.db.total_changes == changes
    assert store.db.execute('SELECT COUNT(*),SUM(up_bytes+down_bytes) FROM traffic_ledger').fetchone()[:] == (20001, 60015)
    manager.close()
    store.close()


def test_existing_ledger_gets_activity_index_without_changing_history(tmp_path):
    path = tmp_path / 'dark.sqlite3'
    store = Store(path)
    with store.transaction() as db:
        db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',
                   ('retained-event', 'dark', 'retired-client', 2, 123, 456, 100.0))
        db.execute('DROP INDEX ledger_client_activity')
    store.close()
    reopened = Store(path)
    try:
        assert reopened.db.execute('SELECT * FROM traffic_ledger').fetchone()[:] == (
            'retained-event', 'dark', 'retired-client', 2, 123, 456, 100.0)
        plan = ' '.join(row[3] for row in reopened.db.execute(
            'EXPLAIN QUERY PLAN SELECT MAX(observed_at) FROM traffic_ledger WHERE client_id=?',
            ('retired-client',)))
        assert 'COVERING INDEX' in plan and 'client_id=?' in plan
    finally:
        reopened.close()


@pytest.mark.parametrize('read_many', [False, True])
def test_client_batch_checks_xray_once_and_sees_next_config_change(env, monkeypatch, read_many):
    _, engine, manager, _, client = env
    for name in ('alpha', 'beta', 'gamma'):
        create(client, name)
    # Represent an already running core without starting any real process.
    monkeypatch.setattr(engine, 'process', SimpleNamespace(poll=lambda: None, pid=12345))
    engine.applied_hash = engine.config_hash(engine.build_config())
    real_build = engine.build_config
    builds = []

    def build():
        builds.append(True)
        return real_build()

    monkeypatch.setattr(engine, 'build_config', build)
    actor = Actor('dark', 'owner', {})
    read = (lambda: list(manager.details_many(actor, ['alpha', 'beta', 'gamma'], credentials=False).values())) if read_many else (lambda: manager.list(actor))
    rows = read()
    assert len(builds) == 1
    assert all(row['data_plane_state'] == 'running' for row in rows)
    assert all('id' not in row['client'] and row['subscription_url'] is None for row in rows)
    engine.save_section('dns', {'servers': ['9.9.9.9']})
    builds.clear()
    assert all(row['data_plane_state'] == 'staged' for row in read())
    assert len(builds) == 1


def test_customer_home_uses_one_current_runtime_snapshot(env, monkeypatch):
    _, engine, manager, _, client = env
    for name, tid in [('mine-a', 555001), ('mine-b', 555001), ('other', 555002)]:
        create(client, name, extra={'tgId': tid})
    assert client.put('/api/telegram/settings', json={
        'enabled': False, 'admin_telegram_id': 777001}).status_code == 200
    monkeypatch.setattr(engine, 'process', SimpleNamespace(poll=lambda: None, pid=12345))
    engine.applied_hash = engine.config_hash(engine.build_config())
    real_build = engine.build_config
    builds = []
    monkeypatch.setattr(engine, 'build_config', lambda: builds.append(True) or real_build())
    worker = BotWorker(client.app.state.telegram_runtime, 'dark', 'test-only', 'test')
    sent = []
    monkeypatch.setattr(worker.api, 'send', lambda *args: sent.append(args))
    try:
        worker.send_home(555001, 555001)
    finally:
        worker.api.close()
    assert len(builds) == 1
    assert len(sent) == 1 and sent[0][0] == 555001
    assert '2 فعال از 2' in sent[0][1]
    assert not any(button.get('web_app') for row in sent[0][2]['keyboard'] for button in row)

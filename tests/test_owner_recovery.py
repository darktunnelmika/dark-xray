import fcntl

from auth import Auth
from core import Config, CoreEngine
from dark_policy import Store
from manager import Manager
from owner_recovery import reset_owner_password
from policy_auth import verify_password


def make_runtime(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    store = Store(data / 'dark.sqlite3')
    config = Config(
        xray_binary=str(tmp_path / 'missing-xray'),
        xray_assets=str(tmp_path),
        test_engine=True,
    )
    engine = CoreEngine(config, store, data / 'runtime')
    manager = Manager(store, engine)
    auth = Auth(store, data / 'secret.key')
    return data, store, engine, manager, auth


def close_runtime(store, engine, manager):
    manager.close()
    engine.close()
    store.close()


def test_live_safe_owner_reset_works_while_instance_lock_is_held(tmp_path):
    data, store, engine, manager, auth = make_runtime(tmp_path)
    auth.bootstrap('dark', 'OldPass88')
    auth.login('dark', 'OldPass88', '', '127.0.0.1')

    lock = (data / 'instance.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = reset_owner_password(data / 'dark.sqlite3', 'dark', 'NewPass88')
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    assert result == {'username': 'dark', 'sessions_revoked': True, 'totp_preserved': True}
    with store.lock:
        row = store.db.execute("SELECT password_hash FROM api_admins WHERE id='dark'").fetchone()
        sessions = store.db.execute("SELECT COUNT(*) FROM live_sessions WHERE admin_id='dark'").fetchone()[0]
    assert verify_password('NewPass88', row['password_hash'])
    assert not verify_password('OldPass88', row['password_hash'])
    assert sessions == 0
    close_runtime(store, engine, manager)


def test_owner_reset_rejects_non_owner(tmp_path):
    data, store, engine, manager, auth = make_runtime(tmp_path)
    auth.bootstrap('dark', 'OwnerPass8')
    with store.lock:
        owner_hash = store.db.execute("SELECT password_hash FROM api_admins WHERE id='dark'").fetchone()[0]
    with store.transaction() as db:
        db.execute(
            "INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)",
            ('viewer', 'readonly', owner_hash, '{}'),
        )
    try:
        try:
            reset_owner_password(data / 'dark.sqlite3', 'viewer', 'ViewerPass8')
            assert False, 'non-owner reset unexpectedly succeeded'
        except ValueError as exc:
            assert 'existing owner' in str(exc)
    finally:
        close_runtime(store, engine, manager)

import fcntl
import sqlite3

from auth import Auth
from dark_policy import Store
from owner_recovery import reset_owner_password
from policy_auth import verify_password


def test_live_safe_owner_reset_works_while_instance_lock_is_held(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    store = Store(data / 'dark.sqlite3')
    auth = Auth(store, data / 'secret.key')
    auth.bootstrap('dark', 'OldPass88')
    token, _ = auth.login('dark', 'OldPass88', '', '127.0.0.1')

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
    store.close()


def test_owner_reset_rejects_non_owner(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    store = Store(data / 'dark.sqlite3')
    auth = Auth(store, data / 'secret.key')
    auth.bootstrap('dark', 'OwnerPass8')
    with store.transaction() as db:
        db.execute("INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)",
                   ('viewer', 'readonly', row_hash := store.db.execute("SELECT password_hash FROM api_admins WHERE id='dark'").fetchone()[0], '{}'))
    try:
        try:
            reset_owner_password(data / 'dark.sqlite3', 'viewer', 'ViewerPass8')
            assert False, 'non-owner reset unexpectedly succeeded'
        except ValueError as exc:
            assert 'existing owner' in str(exc)
    finally:
        store.close()

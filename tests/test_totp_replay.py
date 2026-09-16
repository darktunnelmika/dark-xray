import auth as auth_module
import pytest

from auth import Auth,totp
from dark_policy import PermissionDenied,Store


def test_enrollment_records_the_actual_matched_totp_step(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3')
    auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','OwnerPass88')
    _,principal=auth.login('dark','OwnerPass88','','127.0.0.1',3600,'test')
    secret=auth.mfa_setup(principal,'OwnerPass88')

    base=1_700_000_000.0
    monkeypatch.setattr(auth_module.time,'time',lambda:base)
    step=int(base//30)
    future_code=totp(secret,step+1)
    recovery=auth.mfa_enable(principal,future_code)
    assert len(recovery)==8

    with store.lock:
        row=store.db.execute('SELECT * FROM mfa WHERE admin_id=?',('dark',)).fetchone()
        assert row['last_step']==step+1

    # When the clock reaches that accepted future step, the same code must not
    # become valid a second time.
    monkeypatch.setattr(auth_module.time,'time',lambda:base+30)
    with store.transaction() as db:
        row=db.execute('SELECT * FROM mfa WHERE admin_id=?',('dark',)).fetchone()
        with pytest.raises(PermissionDenied,match='already-used'):
            auth._verify_mfa(db,row,future_code,consume=True)
    store.close()

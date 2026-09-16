import auth as auth_module
import pytest

from auth import Auth,totp
from core import Config,CoreEngine
from dark_policy import PermissionDenied,Store
from manager import Manager


def test_enrollment_records_the_actual_matched_totp_step(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime')
    manager=Manager(store,engine)
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

    monkeypatch.setattr(auth_module.time,'time',lambda:base+30)
    with store.transaction() as db:
        row=db.execute('SELECT * FROM mfa WHERE admin_id=?',('dark',)).fetchone()
        with pytest.raises(PermissionDenied,match='already-used'):
            auth._verify_mfa(db,row,future_code,consume=True)

    manager.close();engine.close();store.close()

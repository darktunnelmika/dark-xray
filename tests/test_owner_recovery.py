import fcntl

from auth import Auth
from core import Config, CoreEngine
from dark_policy import Actor, Store
from manager import Manager
from owner_recovery import (
    create_owner_account,
    disable_owner_totp,
    owner_status,
    rename_owner_username,
    reset_owner_password,
    revoke_owner_api_keys,
    revoke_owner_sessions,
)
from policy_auth import verify_password


def make_runtime(tmp_path):
    data=tmp_path/'data';data.mkdir();store=Store(data/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,data/'runtime');manager=Manager(store,engine);auth=Auth(store,data/'secret.key')
    return data,store,engine,manager,auth


def close_runtime(store,engine,manager):manager.close();engine.close();store.close()


def test_live_safe_owner_reset_works_while_instance_lock_is_held(tmp_path):
    data,store,engine,manager,auth=make_runtime(tmp_path);auth.bootstrap('dark','OldPass88');auth.login('dark','OldPass88','','127.0.0.1')
    lock=(data/'instance.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:result=reset_owner_password(data/'dark.sqlite3','dark','NewPass88')
    finally:fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
    assert result=={'username':'dark','sessions_revoked':True,'totp_preserved':True,'password_verified':True}
    with store.lock:
        row=store.db.execute("SELECT password_hash FROM api_admins WHERE id='dark'").fetchone();sessions=store.db.execute("SELECT COUNT(*) FROM live_sessions WHERE admin_id='dark'").fetchone()[0]
    assert verify_password('NewPass88',row['password_hash']);assert not verify_password('OldPass88',row['password_hash']);assert sessions==0
    close_runtime(store,engine,manager)


def test_owner_reset_rejects_non_owner(tmp_path):
    data,store,engine,manager,auth=make_runtime(tmp_path);auth.bootstrap('dark','OwnerPass8')
    with store.lock:owner_hash=store.db.execute("SELECT password_hash FROM api_admins WHERE id='dark'").fetchone()[0]
    with store.transaction() as db:db.execute("INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)",('viewer','readonly',owner_hash,'{}'))
    try:
        try:reset_owner_password(data/'dark.sqlite3','viewer','ViewerPass8');assert False,'non-owner reset unexpectedly succeeded'
        except ValueError as exc:assert 'existing owner' in str(exc)
    finally:close_runtime(store,engine,manager)


def test_live_safe_owner_username_rename_moves_account_references(tmp_path):
    data,store,engine,manager,auth=make_runtime(tmp_path);auth.bootstrap('dark','OwnerPass8');_,principal=auth.login('dark','OwnerPass8','','127.0.0.1')
    _,api_key=auth.new_key(principal,'test-key',{},30);assert api_key.startswith('dkr_')
    with store.transaction() as db:
        db.execute("INSERT INTO clients(id,owner) VALUES('client@example.com','dark')")
        db.execute("INSERT INTO client_groups(owner,name,color,created_at,updated_at) VALUES('dark','VIP','#22d3ee',1,1)")
        db.execute("INSERT INTO mfa(admin_id,secret,pending,enabled,last_step,recovery) VALUES('dark','placeholder','',1,-1,'[]')")
    lock=(data/'instance.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:r=rename_owner_username(data/'dark.sqlite3','dark','night')
    finally:fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
    assert r['username']=='night' and r['sessions_revoked'] and r['totp_preserved'] and r['api_keys_preserved']
    with store.lock:
        assert store.db.execute("SELECT role FROM api_admins WHERE id='night'").fetchone()[0]=='owner'
        assert store.db.execute("SELECT 1 FROM api_admins WHERE id='dark'").fetchone() is None
        assert store.db.execute("SELECT owner FROM clients WHERE id='client@example.com'").fetchone()[0]=='night'
        assert store.db.execute("SELECT owner FROM client_groups WHERE name='VIP'").fetchone()[0]=='night'
        assert store.db.execute("SELECT COUNT(*) FROM robot_keys WHERE admin_id='night' AND revoked=0").fetchone()[0]==1
        assert store.db.execute("SELECT enabled FROM mfa WHERE admin_id='night'").fetchone()[0]==1
        assert store.db.execute("SELECT COUNT(*) FROM live_sessions WHERE admin_id IN ('dark','night')").fetchone()[0]==0
    close_runtime(store,engine,manager)


def test_owner_security_recovery_actions(tmp_path):
    data,store,engine,manager,auth=make_runtime(tmp_path);auth.bootstrap('dark','OwnerPass8');_,principal=auth.login('dark','OwnerPass8','','127.0.0.1');auth.new_key(principal,'one',{},30)
    with store.transaction() as db:db.execute("INSERT INTO mfa(admin_id,secret,pending,enabled,last_step,recovery) VALUES('dark','placeholder','',1,-1,'[]')")
    status=owner_status(data/'dark.sqlite3','dark');assert status['active_sessions']==1 and status['active_api_keys']==1 and status['totp_enabled']
    assert revoke_owner_sessions(data/'dark.sqlite3','dark')['revoked_sessions']==1
    assert revoke_owner_api_keys(data/'dark.sqlite3','dark')['revoked_api_keys']==1
    result=disable_owner_totp(data/'dark.sqlite3','dark');assert result['totp_disabled'] and result['sessions_revoked']
    status=owner_status(data/'dark.sqlite3','dark');assert status['active_sessions']==0 and status['active_api_keys']==0 and not status['totp_enabled']
    close_runtime(store,engine,manager)


def test_create_primary_owner_login_from_existing_profile_when_no_owner_exists(tmp_path):
    data,store,engine,manager,auth=make_runtime(tmp_path)
    manager.owner_put(Actor('bootstrap','owner',{}),'Mika',name='Mika',allowed=[])
    result=create_owner_account(data/'dark.sqlite3','Mika','MikaPass88')
    assert result['created'] is True and result['role']=='owner' and result['password_verified'] is True
    with store.lock:
        row=store.db.execute("SELECT role,password_hash FROM api_admins WHERE id='Mika'").fetchone()
    assert row['role']=='owner' and verify_password('MikaPass88',row['password_hash'])
    close_runtime(store,engine,manager)


def test_create_second_owner_is_rejected(tmp_path):
    from dark_policy import PolicyError
    data,store,engine,manager,auth=make_runtime(tmp_path)
    auth.bootstrap('dark','OwnerPass8')
    manager.owner_put(Actor('dark','owner',{}),'Mika',name='Mika',allowed=[])
    try:
        create_owner_account(data/'dark.sqlite3','Mika','MikaPass88');assert False
    except PolicyError as exc:
        assert 'one primary owner' in str(exc)
    with store.lock:
        owners=store.db.execute("SELECT id FROM api_admins WHERE role='owner' ORDER BY id").fetchall()
    assert [r['id'] for r in owners]==['dark']
    close_runtime(store,engine,manager)


def test_owner_status_excludes_expired_sessions_and_api_keys(tmp_path):
    import time
    data,store,engine,manager,auth=make_runtime(tmp_path);auth.bootstrap('dark','OwnerPass8')
    token,principal=auth.login('dark','OwnerPass8','','127.0.0.1');auth.new_key(principal,'active',{},30)
    with store.transaction() as db:
        db.execute("INSERT INTO live_sessions(digest,admin_id,csrf,expires_at,public_id,created_at,source,user_agent) VALUES(?,?,?,?,?,?,?,?)",('expired-digest','dark','x',time.time()-10,'expired-public-id-000000',time.time()-100,'old','ua'))
        db.execute("INSERT INTO robot_keys(id,digest,admin_id,name,permissions,expires_at,revoked,created_at) VALUES(?,?,?,?,?,?,0,?)",('expired-key','expired-digest-key','dark','expired','{}',time.time()-10,time.time()-100))
    status=owner_status(data/'dark.sqlite3','dark')
    assert status['active_sessions']==1 and status['active_api_keys']==1
    close_runtime(store,engine,manager)


def test_owner_rename_reports_existing_profile_collision_cleanly(tmp_path):
    from dark_policy import Actor,PolicyError
    data,store,engine,manager,auth=make_runtime(tmp_path);auth.bootstrap('dark','OwnerPass8')
    manager.owner_put(Actor('dark','owner',{}),'Mika',name='Mika',allowed=[])
    try:
        rename_owner_username(data/'dark.sqlite3','dark','Mika');assert False
    except PolicyError as exc:
        assert 'Target ownership profile already exists' in str(exc)
    with store.lock:
        assert store.db.execute("SELECT role FROM api_admins WHERE id='dark'").fetchone()[0]=='owner'
        assert store.db.execute("SELECT 1 FROM owner_profiles WHERE id='Mika'").fetchone()
    close_runtime(store,engine,manager)

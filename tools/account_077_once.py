#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:160]!r}')
    p.write_text(s.replace(old,new,1))

once('backend/owner_recovery.py',
"""import sqlite3
import sys
""",
"""import sqlite3
import sys
import time
""")

once('backend/owner_recovery.py',
"""        if db.execute('SELECT 1 FROM api_admins WHERE id=?',(new_username,)).fetchone():
            raise PolicyError('New username already exists')
        # Revoke interactive sessions first. API keys and TOTP remain attached to the
""",
"""        if db.execute('SELECT 1 FROM api_admins WHERE id=?',(new_username,)).fetchone():
            raise PolicyError('New username already exists')
        # A profile without a login is a separate ownership identity. Never merge it
        # implicitly during an auth rename: that could reassign clients/ledgers.
        for table in ('owners','owner_profiles'):
            if _table_exists(db,table) and _column_exists(db,table,'id') and db.execute(f'SELECT 1 FROM \"{table}\" WHERE id=?',(new_username,)).fetchone():
                raise PolicyError('Target owner profile already exists without this login; use Create owner login for that profile or choose another username')
        # Revoke interactive sessions first. API keys and TOTP remain attached to the
""")

old="""def owner_status(db_path: Path, username: str) -> dict:
    db=_connect(db_path)
    try:
        row=_owner(db,username);sessions=keys=0;totp=False
        if _table_exists(db,'live_sessions'):sessions+=db.execute('SELECT COUNT(*) FROM live_sessions WHERE admin_id=?',(username,)).fetchone()[0]
        if _table_exists(db,'sessions'):sessions+=db.execute('SELECT COUNT(*) FROM sessions WHERE admin_id=?',(username,)).fetchone()[0]
        if _table_exists(db,'robot_keys'):keys=db.execute('SELECT COUNT(*) FROM robot_keys WHERE admin_id=? AND revoked=0',(username,)).fetchone()[0]
        if _table_exists(db,'mfa'):
            r=db.execute('SELECT enabled FROM mfa WHERE admin_id=?',(username,)).fetchone();totp=bool(r and r[0])
        return {'username':username,'role':row['role'],'disabled':bool(row['disabled']),'active_sessions':sessions,'active_api_keys':keys,'totp_enabled':totp}
    finally:db.close()
"""
new="""def owner_status(db_path: Path, username: str) -> dict:
    db=_connect(db_path)
    try:
        row=_owner(db,username);sessions=keys=0;totp=False;now=time.time()
        if _table_exists(db,'live_sessions'):
            sessions+=db.execute('SELECT COUNT(*) FROM live_sessions WHERE admin_id=? AND expires_at>?',(username,now)).fetchone()[0]
        if _table_exists(db,'sessions'):
            sessions+=db.execute('SELECT COUNT(*) FROM sessions WHERE admin_id=? AND expires_at>?',(username,now)).fetchone()[0]
        if _table_exists(db,'robot_keys'):
            keys=db.execute('SELECT COUNT(*) FROM robot_keys WHERE admin_id=? AND revoked=0 AND expires_at>?',(username,now)).fetchone()[0]
        if _table_exists(db,'mfa'):
            r=db.execute('SELECT enabled FROM mfa WHERE admin_id=?',(username,)).fetchone();totp=bool(r and r[0])
        return {'username':username,'role':row['role'],'disabled':bool(row['disabled']),'active_sessions':sessions,'active_api_keys':keys,'totp_enabled':totp}
    finally:db.close()
"""
once('backend/owner_recovery.py',old,new)

p=Path('tests/test_owner_recovery.py');s=p.read_text();s += r'''


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
        assert 'Target owner profile already exists' in str(exc)
    with store.lock:
        assert store.db.execute("SELECT role FROM api_admins WHERE id='dark'").fetchone()[0]=='owner'
        assert store.db.execute("SELECT 1 FROM owner_profiles WHERE id='Mika'").fetchone()
    close_runtime(store,engine,manager)
''';p.write_text(s)

Path('VERSION').write_text('0.7.7-standalone-lab\n')
print('0.7.7 account consistency patch applied')

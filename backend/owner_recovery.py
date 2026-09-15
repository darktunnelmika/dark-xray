#!/usr/bin/env python3
"""Live-safe DARK XRAY owner account recovery and maintenance.

These actions intentionally do not construct CoreEngine/Manager and do not acquire
DARK's instance.lock. They perform narrowly-scoped SQLite authentication/account
transactions so the running panel/Xray process may remain online.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from getpass import getpass
from pathlib import Path

from dark_policy import NAME_RE, PolicyError
from policy_auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, password_hash, verify_password

_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _column_exists(db: sqlite3.Connection, table: str, column: str) -> bool:
    if not _IDENT.fullmatch(table):
        return False
    return any(row[1] == column for row in db.execute(f'PRAGMA table_info("{table}")'))


def _tables(db: sqlite3.Connection) -> list[str]:
    out=[]
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if isinstance(name,str) and _IDENT.fullmatch(name):out.append(name)
    return out


def _safe_db(db_path: Path) -> Path:
    db_path = Path(db_path)
    if db_path.is_symlink() or not db_path.is_file():
        raise PolicyError('DARK database is missing or is an unsafe symlink')
    return db_path


def _owner(db: sqlite3.Connection, username: str) -> sqlite3.Row:
    if not NAME_RE.fullmatch(username):raise PolicyError('Invalid owner username')
    row=db.execute('SELECT id,role,disabled FROM api_admins WHERE id=?',(username,)).fetchone()
    if not row or row['role']!='owner':raise PolicyError('Action only supports an existing owner account')
    return row


def _connect(db_path: Path) -> sqlite3.Connection:
    db=sqlite3.connect(str(_safe_db(db_path)),timeout=30,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    return db


def _revoke_sessions_tx(db: sqlite3.Connection, username: str) -> int:
    removed=0
    for table in ('live_sessions','sessions'):
        if _table_exists(db,table) and _column_exists(db,table,'admin_id'):
            cur=db.execute(f'DELETE FROM "{table}" WHERE admin_id=?',(username,));removed+=max(0,cur.rowcount)
    return removed


def create_owner_account(db_path: Path, username: str, password: str) -> dict:
    """Create a real login owner, optionally attaching an existing owner profile."""
    if not NAME_RE.fullmatch(username):raise PolicyError('Invalid owner username')
    hashed=password_hash(password)
    db=_connect(db_path)
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM api_admins WHERE id=?',(username,)).fetchone():raise PolicyError('A login account with this username already exists')
        db.execute('INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)',(username,'owner',hashed,'{}'))
        written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(username,)).fetchone()
        if not written or not verify_password(password,written['password_hash']):raise PolicyError('Owner password verification failed; transaction rolled back')
        if _table_exists(db,'owners'):db.execute('INSERT OR IGNORE INTO owners(id) VALUES(?)',(username,))
        if _table_exists(db,'owner_profiles'):
            row=db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(username,)).fetchone()
            if not row:
                allowed=[]
                if _table_exists(db,'core_inbounds'):allowed=[r[0] for r in db.execute('SELECT id FROM core_inbounds ORDER BY id')]
                db.execute('INSERT INTO owner_profiles(id,name,allowed) VALUES(?,?,?)',(username,username,json.dumps(allowed)))
        db.execute('COMMIT')
        return {'username':username,'created':True,'role':'owner','profile_attached':True,'password_verified':True}
    except BaseException:
        try:db.execute('ROLLBACK')
        except sqlite3.Error:pass
        raise
    finally:db.close()


def reset_owner_password(db_path: Path, username: str, password: str) -> dict:
    """Replace an existing owner's password and revoke interactive sessions."""
    hashed=password_hash(password)  # canonical policy validation before write lock
    db=_connect(db_path)
    try:
        db.execute('BEGIN IMMEDIATE');_owner(db,username)
        db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,username))
        written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(username,)).fetchone()
        if not written or not verify_password(password,written['password_hash']):raise PolicyError('Owner password verification failed; transaction rolled back')
        _revoke_sessions_tx(db,username);db.execute('COMMIT')
        return {'username':username,'sessions_revoked':True,'totp_preserved':True,'password_verified':True}
    except BaseException:
        try:db.execute('ROLLBACK')
        except sqlite3.Error:pass
        raise
    finally:db.close()


def rename_owner_username(db_path: Path, username: str, new_username: str) -> dict:
    """Rename an owner consistently across ownership/auth tables while live."""
    if not NAME_RE.fullmatch(new_username):raise PolicyError('Invalid new owner username')
    if username==new_username:raise PolicyError('New owner username is unchanged')
    db=_connect(db_path)
    try:
        db.execute('BEGIN IMMEDIATE');_owner(db,username)
        if db.execute('SELECT 1 FROM api_admins WHERE id=?',(new_username,)).fetchone():
            raise PolicyError('New username already exists')
        # A profile without a login is a separate ownership identity. Never merge it
        # implicitly during an auth rename: that could reassign clients/ledgers.
        for table in ('owners','owner_profiles'):
            if _table_exists(db,table) and _column_exists(db,table,'id') and db.execute(f'SELECT 1 FROM "{table}" WHERE id=?',(new_username,)).fetchone():
                raise PolicyError('Target owner profile already exists without this login; use Create owner login for that profile or choose another username')
        # Revoke interactive sessions first. API keys and TOTP remain attached to the
        # renamed owner by updating their admin_id references below.
        _revoke_sessions_tx(db,username)
        for table in _tables(db):
            if table not in {'live_sessions','sessions'} and _column_exists(db,table,'owner'):
                db.execute(f'UPDATE "{table}" SET owner=? WHERE owner=?',(new_username,username))
            if table not in {'live_sessions','sessions'} and _column_exists(db,table,'admin_id'):
                db.execute(f'UPDATE "{table}" SET admin_id=? WHERE admin_id=?',(new_username,username))
        for table in ('owners','owner_profiles','api_admins'):
            if _table_exists(db,table) and _column_exists(db,table,'id'):
                db.execute(f'UPDATE "{table}" SET id=? WHERE id=?',(new_username,username))
        db.execute('COMMIT')
        return {'old_username':username,'username':new_username,'sessions_revoked':True,'totp_preserved':True,'api_keys_preserved':True}
    except BaseException:
        try:db.execute('ROLLBACK')
        except sqlite3.Error:pass
        raise
    finally:db.close()


def revoke_owner_sessions(db_path: Path, username: str) -> dict:
    db=_connect(db_path)
    try:
        db.execute('BEGIN IMMEDIATE');_owner(db,username);count=_revoke_sessions_tx(db,username);db.execute('COMMIT')
        return {'username':username,'revoked_sessions':count}
    except BaseException:
        try:db.execute('ROLLBACK')
        except sqlite3.Error:pass
        raise
    finally:db.close()


def revoke_owner_api_keys(db_path: Path, username: str) -> dict:
    db=_connect(db_path)
    try:
        db.execute('BEGIN IMMEDIATE');_owner(db,username);count=0
        if _table_exists(db,'robot_keys'):
            cur=db.execute('UPDATE robot_keys SET revoked=1 WHERE admin_id=? AND revoked=0',(username,));count=max(0,cur.rowcount)
        db.execute('COMMIT');return {'username':username,'revoked_api_keys':count}
    except BaseException:
        try:db.execute('ROLLBACK')
        except sqlite3.Error:pass
        raise
    finally:db.close()


def disable_owner_totp(db_path: Path, username: str) -> dict:
    db=_connect(db_path)
    try:
        db.execute('BEGIN IMMEDIATE');_owner(db,username);removed=0
        if _table_exists(db,'mfa'):
            cur=db.execute('DELETE FROM mfa WHERE admin_id=?',(username,));removed=max(0,cur.rowcount)
        _revoke_sessions_tx(db,username);db.execute('COMMIT')
        return {'username':username,'totp_disabled':bool(removed),'sessions_revoked':True}
    except BaseException:
        try:db.execute('ROLLBACK')
        except sqlite3.Error:pass
        raise
    finally:db.close()


def owner_status(db_path: Path, username: str) -> dict:
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


def _read_password() -> str:
    if not sys.stdin.isatty():raise PolicyError('Interactive TTY is required for owner password recovery')
    first=getpass(f'New owner password ({PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters): ')
    second=getpass('Repeat new password: ')
    if first!=second:raise PolicyError('Passwords do not match')
    return first


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=Path(os.environ.get('DARK_DATA','./data')))
    p.add_argument('--username',required=True)
    p.add_argument('--action',choices=('create-owner','password','rename','revoke-sessions','revoke-api-keys','disable-totp','status'),default='password')
    p.add_argument('--new-username')
    args=p.parse_args();db=args.data/'dark.sqlite3'
    try:
        if args.action=='create-owner':result=create_owner_account(db,args.username,_read_password())
        elif args.action=='password':result=reset_owner_password(db,args.username,_read_password())
        elif args.action=='rename':
            if not args.new_username:raise PolicyError('--new-username is required for rename')
            result=rename_owner_username(db,args.username,args.new_username)
        elif args.action=='revoke-sessions':result=revoke_owner_sessions(db,args.username)
        elif args.action=='revoke-api-keys':result=revoke_owner_api_keys(db,args.username)
        elif args.action=='disable-totp':result=disable_owner_totp(db,args.username)
        else:result=owner_status(db,args.username)
    except (PolicyError,sqlite3.Error,OSError) as exc:
        print('ERROR:',exc,file=sys.stderr);raise SystemExit(2)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

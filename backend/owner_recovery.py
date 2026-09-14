#!/usr/bin/env python3
"""Live-safe DARK XRAY owner password recovery.

This module intentionally does not construct CoreEngine/Manager and does not acquire
DARK's instance.lock. It performs one narrowly-scoped SQLite authentication update,
so the running panel/Xray process may remain online while an owner password is reset.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from getpass import getpass
from pathlib import Path

from dark_policy import NAME_RE, PolicyError
from policy_auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, password_hash


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def reset_owner_password(db_path: Path, username: str, password: str) -> dict:
    """Replace an existing owner's password and revoke interactive sessions.

    Hashing is completed before BEGIN IMMEDIATE so the database write lock is held
    only for the small authentication transaction. API keys and TOTP are preserved.
    """
    db_path = Path(db_path)
    if not NAME_RE.fullmatch(username):
        raise PolicyError('Invalid owner username')
    if db_path.is_symlink() or not db_path.is_file():
        raise PolicyError('DARK database is missing or is an unsafe symlink')

    hashed = password_hash(password)
    db = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA busy_timeout=30000')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT role FROM api_admins WHERE id=?', (username,)).fetchone()
        if not row or row['role'] != 'owner':
            raise PolicyError('Password recovery only supports an existing owner account')
        db.execute('UPDATE api_admins SET password_hash=? WHERE id=?', (hashed, username))
        if _table_exists(db, 'live_sessions'):
            db.execute('DELETE FROM live_sessions WHERE admin_id=?', (username,))
        if _table_exists(db, 'sessions'):
            db.execute('DELETE FROM sessions WHERE admin_id=?', (username,))
        db.execute('COMMIT')
        return {'username': username, 'sessions_revoked': True, 'totp_preserved': True}
    except BaseException:
        try:
            db.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def _read_password() -> str:
    if not sys.stdin.isatty():
        raise PolicyError('Interactive TTY is required for owner password recovery')
    first = getpass(f'New owner password ({PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters): ')
    second = getpass('Repeat new password: ')
    if first != second:
        raise PolicyError('Passwords do not match')
    # password_hash performs the canonical length/policy validation.
    return first


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=Path(os.environ.get('DARK_DATA', './data')))
    p.add_argument('--username', required=True)
    args = p.parse_args()
    try:
        result = reset_owner_password(args.data / 'dark.sqlite3', args.username, _read_password())
    except (PolicyError, sqlite3.Error, OSError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        raise SystemExit(2)
    print(f"Owner password changed for {result['username']}; active sessions revoked. TOTP remains enabled.")


if __name__ == '__main__':
    main()

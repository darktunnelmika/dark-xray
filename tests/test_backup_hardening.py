import json
import os
import sqlite3
from pathlib import Path

import pytest

from backup import create_backup,restore_backup,project_version
from dark_policy import PolicyError

PASS='BackupPassphrase-123!'


def minimal_source(tmp_path:Path,*,sessions:bool=True):
    data=tmp_path/'data';data.mkdir()
    db=data/'dark.sqlite3'
    with sqlite3.connect(db) as con:
        con.executescript('''
        CREATE TABLE clients(id TEXT PRIMARY KEY);
        CREATE TABLE owners(id TEXT PRIMARY KEY);
        CREATE TABLE api_admins(id TEXT PRIMARY KEY);
        CREATE TABLE core_clients(email TEXT PRIMARY KEY);
        ''')
        if sessions:con.execute('CREATE TABLE live_sessions(digest TEXT PRIMARY KEY)')
        con.commit()
    (data/'secret.key').write_bytes(b'x'*44)
    os.chmod(data/'secret.key',0o600)
    config=tmp_path/'config.json';config.write_text(json.dumps({'public_origin':'http://127.0.0.1:2087','core_autostart':True}),encoding='utf-8')
    return data,config


def test_backup_manifest_uses_current_project_version_and_restore_reports_it(tmp_path):
    data,config=minimal_source(tmp_path)
    archive=tmp_path/'full.darkbackup'
    manifest=create_backup(data,config,archive,PASS)
    assert manifest['version']==project_version()
    restored=restore_backup(archive,tmp_path/'restored',PASS)
    assert restored['backup_version']==project_version()
    assert restored['core_autostart'] is False and restored['sessions_revoked'] is True
    with sqlite3.connect(tmp_path/'restored/data/dark.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM live_sessions').fetchone()[0]==0


def test_restore_accepts_older_standalone_snapshot_without_live_sessions(tmp_path):
    data,config=minimal_source(tmp_path,sessions=False)
    archive=tmp_path/'legacy.darkbackup'
    create_backup(data,config,archive,PASS)
    out=restore_backup(archive,tmp_path/'legacy-restored',PASS)
    assert out['restored'] is True
    with sqlite3.connect(tmp_path/'legacy-restored/data/dark.sqlite3') as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'core_clients' in tables and 'live_sessions' not in tables


def test_backup_refuses_config_symlink_before_resolving_it(tmp_path):
    data,config=minimal_source(tmp_path)
    target=tmp_path/'real-config.json';config.rename(target);config.symlink_to(target)
    with pytest.raises(PolicyError,match='symlinks'):
        create_backup(data,config,tmp_path/'bad.darkbackup',PASS)


def test_backup_refuses_database_symlink(tmp_path):
    data,config=minimal_source(tmp_path)
    db=data/'dark.sqlite3';real=data/'real.sqlite3';db.rename(real);db.symlink_to(real)
    with pytest.raises(PolicyError,match='non-symlink'):
        create_backup(data,config,tmp_path/'bad.darkbackup',PASS)


def test_backup_refuses_non_object_config(tmp_path):
    data,config=minimal_source(tmp_path)
    config.write_text('[]',encoding='utf-8')
    with pytest.raises(PolicyError,match='JSON object'):
        create_backup(data,config,tmp_path/'bad.darkbackup',PASS)


def test_full_backup_restores_telegram_business_state_but_resets_bot_identity(tmp_path):
    data,config=minimal_source(tmp_path)
    with sqlite3.connect(data/'dark.sqlite3') as db:
        db.executescript("""
        CREATE TABLE telegram_bots(
          owner TEXT PRIMARY KEY,enabled INTEGER NOT NULL,token_enc TEXT NOT NULL,
          admin_telegram_id INTEGER NOT NULL,updated_at REAL NOT NULL,update_offset INTEGER NOT NULL,
          bot_username TEXT NOT NULL,last_error TEXT NOT NULL,last_seen REAL NOT NULL,forum_prompted_at REAL NOT NULL);
        CREATE TABLE telegram_forums(
          owner TEXT PRIMARY KEY,chat_id INTEGER NOT NULL,title TEXT NOT NULL,enabled INTEGER NOT NULL,
          configured_at REAL NOT NULL,updated_at REAL NOT NULL,last_audit_id INTEGER NOT NULL,last_daily_key TEXT NOT NULL);
        CREATE TABLE telegram_forum_topics(
          owner TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,thread_id INTEGER NOT NULL,
          updated_at REAL NOT NULL,PRIMARY KEY(owner,kind));
        CREATE TABLE commerce_products(
          id TEXT NOT NULL,owner TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(owner,id));
        CREATE TABLE commerce_gateways(
          id TEXT NOT NULL,owner TEXT NOT NULL,kind TEXT NOT NULL,card_number TEXT NOT NULL,
          PRIMARY KEY(owner,id));
        CREATE TABLE commerce_orders(id TEXT PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL);
        """)
        db.execute("INSERT INTO telegram_bots VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ('dark',1,'OLD-ENCRYPTED-TOKEN',1906987468,1.0,9988,'old_bot','old timeout',123.0,44.0))
        db.execute("INSERT INTO telegram_forums VALUES(?,?,?,?,?,?,?,?)",
                   ('dark',-100777,'DARK Reports',1,1.0,2.0,55,'2026-09-24'))
        db.execute("INSERT INTO telegram_forum_topics VALUES(?,?,?,?,?)",
                   ('dark','sales','💰 فروش',101,1.0))
        db.execute("INSERT INTO commerce_products VALUES(?,?,?)",('vip','dark','VIP'))
        db.execute("INSERT INTO commerce_gateways VALUES(?,?,?,?)",('card','dark','manual','6037991234567890'))
        db.execute("INSERT INTO commerce_orders VALUES(?,?,?)",('ord1','dark','provisioned'))
        db.commit()
    archive=tmp_path/'telegram-full.darkbackup'
    manifest=create_backup(data,config,archive,PASS)
    assert manifest['telegram_disaster_recovery']['bot_token_reset_on_restore'] is True
    restored=tmp_path/'telegram-restored'
    result=restore_backup(archive,restored,PASS)
    assert result['new_bot_token_required'] is True
    assert result['forum_rebind_required'] is True
    with sqlite3.connect(restored/'data/dark.sqlite3') as db:
        db.row_factory=sqlite3.Row
        bot=db.execute("SELECT * FROM telegram_bots WHERE owner='dark'").fetchone()
        assert bot['admin_telegram_id']==1906987468
        assert bot['enabled']==0 and bot['token_enc']=='' and bot['update_offset']==0
        assert bot['bot_username']=='' and bot['last_error']=='' and bot['last_seen']==0
        forum=db.execute("SELECT * FROM telegram_forums WHERE owner='dark'").fetchone()
        assert forum['chat_id']==-100777 and forum['rebind_required']==1
        assert forum['rebind_reason']=='restored-backup'
        assert db.execute("SELECT thread_id FROM telegram_forum_topics WHERE owner='dark' AND kind='sales'").fetchone()[0]==101
        assert db.execute("SELECT name FROM commerce_products WHERE owner='dark' AND id='vip'").fetchone()[0]=='VIP'
        assert db.execute("SELECT card_number FROM commerce_gateways WHERE owner='dark' AND id='card'").fetchone()[0]=='6037991234567890'
        assert db.execute("SELECT status FROM commerce_orders WHERE id='ord1'").fetchone()[0]=='provisioned'

def test_full_backup_preserves_customer_wallet_referral_and_support_state(tmp_path):
    data,config=minimal_source(tmp_path)
    with sqlite3.connect(data/'dark.sqlite3') as db:
        db.executescript("""
        CREATE TABLE customer_wallets(
          owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,currency TEXT NOT NULL,
          balance_minor INTEGER NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(owner,telegram_id));
        CREATE TABLE customer_wallet_ledger(
          id TEXT PRIMARY KEY,owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,
          delta_minor INTEGER NOT NULL,currency TEXT NOT NULL,kind TEXT NOT NULL,
          reference TEXT NOT NULL,detail TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE customer_referrals(
          owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,code TEXT NOT NULL,
          referrer_telegram_id INTEGER NOT NULL,referred_at REAL NOT NULL,
          qualified_at REAL NOT NULL,reward_minor INTEGER NOT NULL,PRIMARY KEY(owner,telegram_id));
        CREATE TABLE customer_support_tickets(
          id TEXT PRIMARY KEY,owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,
          username TEXT NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL,
          created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE customer_support_messages(
          id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL,owner TEXT NOT NULL,
          sender_type TEXT NOT NULL,sender_telegram_id INTEGER NOT NULL,
          text TEXT NOT NULL,file_kind TEXT NOT NULL,file_id TEXT NOT NULL,created_at REAL NOT NULL);
        """)
        db.execute("INSERT INTO customer_wallets VALUES(?,?,?,?,?)",('dark',55,'IRT',700000,1.0))
        db.execute("INSERT INTO customer_wallet_ledger VALUES(?,?,?,?,?,?,?,?,?)",
                   ('w1','dark',55,700000,'IRT','topup','top1','approved',1.0))
        db.execute("INSERT INTO customer_referrals VALUES(?,?,?,?,?,?,?)",
                   ('dark',55,'REFCODE',0,0,0,0))
        db.execute("INSERT INTO customer_support_tickets VALUES(?,?,?,?,?,?,?,?)",
                   ('t1','dark',55,'user55','Need help','open',1.0,2.0))
        db.execute("INSERT INTO customer_support_messages VALUES(?,?,?,?,?,?,?,?,?)",
                   ('m1','t1','dark','customer',55,'hello','','',2.0))
        db.commit()
    archive=tmp_path/'customer-state.darkbackup'
    create_backup(data,config,archive,PASS)
    restored=tmp_path/'customer-restored'
    restore_backup(archive,restored,PASS)
    with sqlite3.connect(restored/'data/dark.sqlite3') as db:
        assert db.execute("SELECT balance_minor FROM customer_wallets WHERE owner='dark' AND telegram_id=55").fetchone()[0]==700000
        assert db.execute("SELECT COUNT(*) FROM customer_wallet_ledger WHERE owner='dark' AND telegram_id=55").fetchone()[0]==1
        assert db.execute("SELECT code FROM customer_referrals WHERE owner='dark' AND telegram_id=55").fetchone()[0]=='REFCODE'
        assert db.execute("SELECT status FROM customer_support_tickets WHERE id='t1'").fetchone()[0]=='open'
        assert db.execute("SELECT text FROM customer_support_messages WHERE id='m1'").fetchone()[0]=='hello'
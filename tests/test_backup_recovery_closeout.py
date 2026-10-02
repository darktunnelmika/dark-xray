"""Recovery readiness must describe the destination, not the old host."""
import json
import sqlite3
import sys
from pathlib import Path
import pytest
from backup import create_backup, restore_backup
from test_backup_hardening import minimal_source, PASS
from test_backup_cli import CLI


def source_with_domains(tmp_path):
    data, config = minimal_source(tmp_path)
    with sqlite3.connect(data/'dark.sqlite3') as db:
        db.executescript('''
          CREATE TABLE restore_domains(domain TEXT PRIMARY KEY, acme_email TEXT NOT NULL,
            dns_status TEXT NOT NULL, ssl_status TEXT NOT NULL, cert_path TEXT NOT NULL,
            key_path TEXT NOT NULL, last_checked REAL, created_at REAL, updated_at REAL);
          CREATE TABLE restore_subscriptions(id TEXT, public_token TEXT, legacy_url TEXT,
            legacy_upload INTEGER, legacy_download INTEGER, legacy_total INTEGER,
            legacy_expire INTEGER, inbound_ids TEXT, node_ids TEXT, promoted_at REAL);
          CREATE TABLE restore_groups(id TEXT, name TEXT);
        ''')
        db.execute('INSERT INTO restore_domains VALUES(?,?,?,?,?,?,?,?,?)',
            ('legacy.example','admin@example.test','ok','ready','/old/fullchain.pem','/old/key.pem',50,10,50))
        db.execute('INSERT INTO restore_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('restore-1','private-token','https://legacy.example/sub/private-token',73,47,99000,
             2000000000,'[1,2]','["node-a","node-b"]',0))
        db.execute('INSERT INTO restore_groups VALUES(?,?)',('group-1','Imported group'))
    return data, config


def test_restore_clears_only_host_readiness_not_customer_state(tmp_path):
    data, config=source_with_domains(tmp_path)
    archive=tmp_path/'full.darkbackup'; dest=tmp_path/'recovered'
    manifest=create_backup(data,config,archive,PASS)
    result=restore_backup(archive,dest,PASS)
    with sqlite3.connect(dest/'data/dark.sqlite3') as restored, sqlite3.connect(data/'dark.sqlite3') as source:
        row=restored.execute('SELECT * FROM restore_domains').fetchone()
        assert row[0:2]==('legacy.example','admin@example.test')
        assert row[2:7]==('unchecked','unchecked','','',0)
        assert row[7]==10
        assert source.execute('SELECT dns_status,ssl_status FROM restore_domains').fetchone()==('ok','ready')
        for table in ('restore_subscriptions','restore_groups'):
            assert restored.execute('SELECT * FROM '+table).fetchall()==source.execute('SELECT * FROM '+table).fetchall()
    recovery=result['dark_restore_recovery']
    assert recovery['frontend_rebuild_required'] is True
    assert recovery['domains']==['legacy.example'] and recovery['domain_rows_reset']==1
    assert result['live_activation_performed'] is False
    assert manifest['dark_restore_disaster_recovery']['frontend_rebuild_required_after_restore'] is True
    assert 'private-token' not in json.dumps(result)


def test_restore_old_backup_without_domains_remains_supported(tmp_path):
    data, config=minimal_source(tmp_path,sessions=False)
    archive=tmp_path/'old.darkbackup'
    create_backup(data,config,archive,PASS)
    result=restore_backup(archive,tmp_path/'old-recovered',PASS)
    assert result['dark_restore_recovery']=={'domain_rows_reset':0,'domains':[], 'frontend_rebuild_required':False}
    assert result['sessions_revoked'] is True and result['core_autostart'] is False


def test_verify_reports_recovery_actions_without_retaining_a_copy(tmp_path,monkeypatch,capsys):
    data,config=source_with_domains(tmp_path);archive=tmp_path/'verify.darkbackup'
    create_backup(data,config,archive,PASS)
    destinations=[];real=CLI.restore_backup
    def capture(archive,dest,password):
        destinations.append(Path(dest));return real(archive,dest,password)
    monkeypatch.setattr(CLI,'restore_backup',capture)
    monkeypatch.setattr(CLI,'secret',lambda prompt:PASS)
    monkeypatch.setattr(sys,'argv',['backup_cli.py','verify','--archive',str(archive)])
    assert CLI.main()==0
    report=json.loads(capsys.readouterr().out)
    assert report['dark_restore_recovery']['frontend_rebuild_required'] is True
    assert report['live_activation_performed'] is False
    assert destinations and all(not x.exists() for x in destinations)
    assert PASS not in json.dumps(report)


def test_bad_passphrase_and_nonempty_destination_still_refused(tmp_path):
    from dark_policy import PolicyError
    data,config=source_with_domains(tmp_path);archive=tmp_path/'secure.darkbackup'
    create_backup(data,config,archive,PASS)
    bad=tmp_path/'bad'
    with pytest.raises(PolicyError):restore_backup(archive,bad,'Definitely-Wrong-123!')
    assert not bad.exists()
    existing=tmp_path/'existing';existing.mkdir();(existing/'marker').write_text('keep')
    with pytest.raises(PolicyError):restore_backup(archive,existing,PASS)
    assert (existing/'marker').read_text()=='keep'

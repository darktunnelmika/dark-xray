"""Durable run-intent store only; not yet the live Hub API/monitor integration.

Real SQLite and encrypted backup. Remote acknowledgement is a test transport.
"""
from __future__ import annotations
import contextlib
import json
from dataclasses import asdict

import pytest
import nodes as node_module
from nodes import NodeRegistry
from node_commands import NodeCommands
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,PolicyError
from backup import create_backup,restore_backup

NODE='run-intent-node'
TOKEN='dkn_'+'A'*60

@contextlib.contextmanager
def fixture(root,monkeypatch):
    root.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr(node_module,'validate_origin',lambda origin:origin)
    store=Store(root/'dark.sqlite3');auth=Auth(store,root/'secret.key')
    registry=NodeRegistry(store,auth.cipher)
    if not store.db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(NODE,)).fetchone():
        registry.put(NODE,'Node','https://node.example.test',TOKEN)
    commands=NodeCommands(registry)
    try:yield registry,commands
    finally:registry.close();store.close()


def offline(*args,**kwargs):raise PolicyError('Test transport unavailable')


def acknowledge(node,path,method,body,timeout):
    assert path=='/node/api/v1/control' and method=='POST'
    return {'service':'DARK XRAY NODE','node_id':node,'revision':body['revision'],
            'commandId':body['commandId'],'action':body['action'],'applied':True,
            'engine':{'state':'stopped' if body['action']=='stop' else 'running'}},1


def test_persists_stop_before_network_and_survives_hub_recreation(tmp_path,monkeypatch):
    with fixture(tmp_path/'hub',monkeypatch) as (reg,commands):
        commands.record(NODE,'stop')
        monkeypatch.setattr(reg,'_request',offline)
        assert commands.deliver(NODE)['queued']
        expected=commands.status(NODE)
        assert expected['pending'] and not expected['desired_running']
    with fixture(tmp_path/'hub',monkeypatch) as (_,commands):assert commands.status(NODE)==expected


def test_newer_intent_supersedes_pending_old_action(tmp_path,monkeypatch):
    with fixture(tmp_path,monkeypatch) as (_,commands):
        commands.record(NODE,'stop');old=commands.status(NODE)
        commands.record(NODE,'start');new=commands.status(NODE)
        assert new['revision']==old['revision']+1 and new['command_id']!=old['command_id']
        assert new['pending'] and new['desired_running']


def test_retry_of_pending_action_reuses_identity(tmp_path,monkeypatch):
    with fixture(tmp_path,monkeypatch) as (_,commands):
        commands.record(NODE,'restart');old=commands.status(NODE)
        commands.record(NODE,'restart');assert commands.status(NODE)==old


@pytest.mark.parametrize('action',['start','stop','restart'])
def test_valid_ack_commits_only_exact_command(tmp_path,monkeypatch,action):
    with fixture(tmp_path,monkeypatch) as (reg,commands):
        commands.record(NODE,action);monkeypatch.setattr(reg,'_request',acknowledge)
        result=commands.deliver(NODE)
        assert not result['queued'] and result['control']['applied_revision']==1
        monkeypatch.setattr(reg,'_request',lambda *a,**k:pytest.fail('Completed command was resent'))
        assert commands.deliver(NODE)['already_applied']
        commands.record(NODE,action);assert commands.status(NODE)['revision']==2


@pytest.mark.parametrize('field,value',[('revision',True),('revision',99),('commandId','bad'),
                                        ('node_id','other'),('action','start'),('applied',False),
                                        ('engine',{'state':'running'})])
def test_bad_ack_is_pending_not_success(tmp_path,monkeypatch,field,value):
    with fixture(tmp_path,monkeypatch) as (reg,commands):
        commands.record(NODE,'stop')
        def bad(*a,**k):
            doc,ms=acknowledge(*a,**k);doc[field]=value;return doc,ms
        monkeypatch.setattr(reg,'_request',bad)
        assert commands.deliver(NODE)['queued']
        state=commands.status(NODE)
        assert state['applied_revision']==0 and state['last_error']


def test_delayed_ack_and_error_cannot_replace_newer_intent(tmp_path,monkeypatch):
    with fixture(tmp_path,monkeypatch) as (reg,commands):
        commands.record(NODE,'stop')
        def old_ack(*a,**k):commands.record(NODE,'start');return acknowledge(*a,**k)
        monkeypatch.setattr(reg,'_request',old_ack)
        assert commands.deliver(NODE)['queued']
        assert commands.status(NODE)['action']=='start' and commands.status(NODE)['applied_revision']==0
        def old_error(*a,**k):commands.record(NODE,'stop');raise PolicyError('Obsolete request failed')
        monkeypatch.setattr(reg,'_request',old_error);commands.deliver(NODE)
        assert commands.status(NODE)['last_error']==''


def test_record_does_not_read_broken_desired_configuration(tmp_path,monkeypatch):
    with fixture(tmp_path,monkeypatch) as (reg,commands):
        reg.set_desired_state(NODE,{'schema':1})
        with reg.store.transaction() as db:db.execute("UPDATE remote_node_desired_state SET desired_json='broken'")
        commands.record(NODE,'stop');assert commands.status(NODE)['pending']


def test_encrypted_hub_backup_contains_exact_pending_run_intent(tmp_path,monkeypatch):
    archive=tmp_path/'hub.darkbackup';password='Stage2B-Backup-12345!'
    with fixture(tmp_path/'hub',monkeypatch) as (reg,commands):
        cfg=tmp_path/'hub/config.json';cfg.write_text(json.dumps(asdict(Config(test_engine=True))));cfg.chmod(0o600)
        eng=CoreEngine(Config.load(cfg),reg.store,tmp_path/'hub/runtime')
        try:
            commands.record(NODE,'stop');expected=commands.status(NODE)
            create_backup(tmp_path/'hub',cfg,archive,password)
        finally:eng.close()
    restored=tmp_path/'restored';restore_backup(archive,restored,password)
    with fixture(restored/'data',monkeypatch) as (_,commands):assert commands.status(NODE)==expected


def test_record_rejects_unknown_node_or_action(tmp_path,monkeypatch):
    with fixture(tmp_path,monkeypatch) as (_,commands):
        with pytest.raises(PolicyError):commands.record('missing','stop')
        with pytest.raises(PolicyError):commands.record(NODE,'shell')
        assert not commands.status(NODE)['persisted']

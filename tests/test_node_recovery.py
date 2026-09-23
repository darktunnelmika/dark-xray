"""Explicit stale-Hub recovery. Real SQLite/FastAPI; fake Xray, in-process TLS fixture."""
import contextlib
import copy
import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest
from auth import Auth
from backup import create_backup, restore_backup
from dark_policy import PolicyError, Store
from node_recovery import NodeRecovery
from nodes import NodeRegistry
import nodes as nodes_module
from test_hub_node_control_live import hub
from test_node_control_lifecycle import rebooted_agent
from test_node_hub_recovery import NODE, TOKEN, payload, post_state
from test_node_installations import http_transport, seed_account, sql_rows

URL = f'/api/nodes/{NODE}/recovery'
REMOTE = '/node/api/v1/recovery'


def setup_stale(reg, owner, engine, remote_engine, runtime, client, monkeypatch):
    seed_account(reg, owner, engine)
    seen = http_transport(reg, client, monkeypatch)
    reg.probe(NODE)
    body = payload(remote_engine)
    reg.set_desired_state(NODE, body)
    local = reg.desired_state(NODE)
    reg.mark_desired_state(NODE, local['revision'], local['hash'])
    # Hub backup knows revision 1. Agent progressed beyond that backup.
    post_state(client, body, 9)
    response = client.post('/node/api/v1/control', json={'nodeId': NODE, 'revision': 12,
                           'commandId': uuid.uuid4().hex, 'action': 'restart'})
    assert response.status_code == 200, response.text
    with reg.store.transaction() as db:
        db.execute("INSERT INTO remote_node_control(node_id,revision,command_id,action,updated_at) VALUES(?,2,?,'restart',1)",
                   (NODE, uuid.uuid4().hex))
    return seen


def review(owner):
    response = owner.post(URL+'/review', json={})
    assert response.status_code == 200, response.text
    return response.json()


def consent(checked):
    return {'bindingId': checked['binding_id'], 'reviewHash': checked['review_hash'],
            'acknowledgeServiceInterruption': True, 'acknowledgeBackupMayBeStale': True}


def stop(owner, checked):
    response = owner.post(URL+'/stop', json=consent(checked))
    assert response.status_code == 200, response.text
    return response.json()


def test_review_and_current_are_read_only_and_show_exact_divergence(hub, tmp_path, monkeypatch):
    reg, owner, engine = hub
    with rebooted_agent(tmp_path/'node') as (_, eng, runtime, client, _):
        seen = setup_stale(reg, owner, engine, eng, runtime, client, monkeypatch)
        seen.clear();changes = reg.store.db.total_changes
        before = runtime.command_status();pid = eng.process.pid
        checked = review(owner)
        assert checked['recovery_needed']
        assert checked['reasons'] == ['agent_command_ahead','agent_configuration_ahead']
        assert checked['hub_command_revision'] == 2 and checked['agent_command_revision'] == 12
        assert checked['hub_configuration_revision'] == 1 and checked['agent_configuration_revision'] == 9
        assert [method for method, _, _ in seen] == ['GET']
        assert eng.process.pid == pid and runtime.command_status() == before
        seen.clear();found = owner.get(URL+'/current')
        assert found.status_code == 200 and found.json()['phase'] == 'not_started'
        assert not seen and reg.store.db.total_changes == changes
        assert TOKEN not in json.dumps(checked)


def test_recovery_advances_sequences_stops_and_preserves_master_data(hub, tmp_path, monkeypatch):
    reg, owner, engine = hub
    with rebooted_agent(tmp_path/'node') as (_, eng, runtime, client, loop):
        seen = setup_stale(reg, owner, engine, eng, runtime, client, monkeypatch)
        tables = ('clients','managed_clients','owners','core_clients','core_inbounds','core_sections',
                  'traffic_ledger','remote_node_client_usage')
        before = {table: sql_rows(reg, table) for table in tables}
        checked = review(owner);seen.clear();result = stop(owner, checked)
        assert result['recovery_completed'], result
        assert result['command_revision'] == 13 and result['rebased_revision'] == 10
        assert not eng.running and runtime.control_status()['manual_stop']
        loop.tick();assert not eng.running
        assert runtime.status()['appliedRevision'] == 9, 'Recovery must not apply old configuration'
        assert not reg.get(NODE)['enabled'] and reg.commands.status(NODE)['action'] == 'stop'
        assert not reg.commands.status(NODE)['pending']
        pending = reg.desired_state(NODE)
        assert pending['revision'] == 10 and pending['pending'] and pending['payload']['desiredRunning'] is False
        assert all(row['remote_inbound_id'] == 0 for row in reg.assignments(NODE))
        assert not reg.list()[0]['failover_ready']
        assert {table: sql_rows(reg, table) for table in tables} == before
        assert [path for method,path,_ in seen if method=='POST'] == [REMOTE+'/stop']
        assert not result['configuration_applied'] and not result['usage_reconciled']


def test_repaired_node_can_explicitly_sync_and_start_without_replaying_restart(hub, tmp_path, monkeypatch):
    reg, owner, engine = hub
    with rebooted_agent(tmp_path/'node') as (_, eng, runtime, client, _):
        setup_stale(reg, owner, engine, eng, runtime, client, monkeypatch)
        assert stop(owner, review(owner))['recovery_completed']
        reg.set_enabled(NODE, True)
        result = reg.sync_desired_state(NODE, reg.desired_state(NODE))
        assert result['desired_state_applied'] and not eng.running
        result = reg.remote_core(NODE, 'start')
        assert result['executed'] and eng.running
        assert runtime.command_status()['revision'] == 14


@pytest.mark.parametrize('where', ['before_stop', 'lost_stop_ack', 'lost_verify'])
def test_retry_same_stop_after_ambiguous_network_and_registry_recreation(hub, tmp_path, monkeypatch, where):
    reg, owner, engine = hub
    with rebooted_agent(tmp_path/'node') as (_, eng, runtime, client, _):
        setup_stale(reg, owner, engine, eng, runtime, client, monkeypatch)
        checked = review(owner);real = nodes_module.node_https_request;stop_sent = False
        def fault(origin, token, path, method='GET', body=None, *a, **kw):
            nonlocal stop_sent
            if where=='before_stop' and path==REMOTE+'/stop':raise OSError('untrusted '+TOKEN)
            if path==REMOTE+'/state' and stop_sent and where=='lost_verify':raise OSError('untrusted '+TOKEN)
            result=real(origin,token,path,method,body,*a,**kw)
            if path==REMOTE+'/stop':
                stop_sent=True
                if where=='lost_stop_ack':raise OSError('untrusted '+TOKEN)
            return result
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',fault)
            result=stop(owner,checked)
        assert result['phase']=='pending' and not result['recovery_completed']
        assert TOKEN not in json.dumps(result)
        assert not reg.get(NODE)['enabled']
        with pytest.raises(PolicyError):reg.set_enabled(NODE,True)
        with pytest.raises(PolicyError):reg.commands.record(NODE,'start')
        row=owner.app.state.node_recovery._row(NODE)
        rebuilt=NodeRegistry(reg.store,reg.cipher);controller=NodeRecovery(rebuilt)
        calls=[];execute=runtime._command_locked
        def count(action):calls.append(action);return execute(action)
        monkeypatch.setattr(runtime,'_command_locked',count)
        recovered=controller.retry(NODE,result['attempt_id'])
        assert recovered['recovery_completed'], recovered
        assert runtime.command_status()['command_id']==row['command_id']
        assert calls==(['stop'] if where=='before_stop' else [])


def test_completed_retry_does_not_disable_or_stop_later_running_node(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        checked=review(owner);result=stop(owner,checked)
        assert result['recovery_completed']
        reg.set_enabled(NODE,True);reg.sync_desired_state(NODE,reg.desired_state(NODE));reg.remote_core(NODE,'start')
        pid=eng.process.pid
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**kw:pytest.fail('Historical retry contacted Agent'))
        again=stop(owner,checked)
        assert again['recovery_completed'] and not again['live_state_verified']
        assert reg.get(NODE)['enabled'] and eng.process.pid==pid


@pytest.mark.parametrize('change', ['local_command','local_config','credential','origin','remote_command','remote_config'])
def test_stale_review_never_mutates_after_inputs_change(hub,tmp_path,monkeypatch,change):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);checked=review(owner)
        if change=='local_command':reg.commands.record(NODE,'stop')
        elif change=='local_config':
            body=payload(eng);body['sections']['dns']={'servers':['8.8.8.8']};reg.set_desired_state(NODE,body)
        elif change in ('credential','origin'):
            with reg.store.transaction() as db:
                if change=='credential':db.execute('UPDATE remote_nodes SET token_enc=? WHERE id=?',(reg.cipher.encrypt(('dkn_'+'B'*60).encode()).decode(),NODE))
                else:db.execute("UPDATE remote_nodes SET origin='https://different.example.test' WHERE id=?",(NODE,))
        elif change=='remote_command':runtime.ordered_command({'nodeId':NODE,'revision':13,'commandId':uuid.uuid4().hex,'action':'stop'})
        else:post_state(client,payload(eng),10)
        before=runtime.command_status();enabled=reg.get(NODE)['enabled']
        response=owner.post(URL+'/stop',json=consent(checked))
        assert response.status_code==400,response.text
        assert runtime.command_status()==before and reg.get(NODE)['enabled']==enabled
        assert not owner.app.state.node_recovery._row(NODE)


@pytest.mark.parametrize('mode',['false','string','extra','csrf','anonymous','reseller','key','readonly'])
def test_repair_owner_csrf_strict_consent_and_readonly(hub,tmp_path,monkeypatch,mode):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);body=consent(review(owner))
        if mode=='false':body['acknowledgeServiceInterruption']=False
        elif mode=='string':body['acknowledgeBackupMayBeStale']='true'
        elif mode=='extra':body['remoteHealth']={}
        elif mode=='csrf':owner.headers['X-Dark-CSRF']='invalid'
        elif mode=='anonymous':owner.cookies.clear()
        elif mode=='reseller':
            with reg.store.transaction() as db:db.execute("UPDATE api_admins SET role='reseller' WHERE id='dark'")
        elif mode=='key':
            response=owner.post('/api/keys',json={'name':'recovery','days':1,'permissions':{'clients.read':'all'}})
            owner.cookies.clear();owner.headers['Authorization']='Bearer '+response.json()['key']
        else:engine.config.writes_enabled=False
        before=runtime.command_status();response=owner.post(URL+'/stop',json=body)
        assert response.status_code=={'false':400,'string':422,'extra':422,'csrf':403,'anonymous':401,'reseller':403,'key':403,'readonly':409}[mode],response.text
        assert runtime.command_status()==before and reg.get(NODE)['enabled']
        assert not owner.app.state.node_recovery._row(NODE)


def test_same_number_conflicts_are_recoverable_not_accepted_as_ack(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        with reg.store.transaction() as db:
            db.execute('UPDATE remote_node_control SET revision=12 WHERE node_id=?',(NODE,))
            db.execute('UPDATE remote_node_desired_state SET revision=9 WHERE node_id=?',(NODE,))
        checked=review(owner)
        assert 'command_identity_conflict' in checked['reasons']
        # Equal config number and hash is valid; deliberately create a fork hash.
        with reg.store.transaction() as db:db.execute("UPDATE remote_node_desired_state SET desired_hash=? WHERE node_id=?",('b'*64,NODE))
        checked=review(owner);assert 'configuration_hash_conflict' in checked['reasons']
        assert stop(owner,checked)['recovery_completed']


def test_missing_pin_config_and_exhausted_counter_fail_before_mutation(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    assert owner.post(URL+'/review',json={}).status_code==400
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        with reg.store.transaction() as db:db.execute('UPDATE remote_node_control SET revision=? WHERE node_id=?',(2**63-1,NODE))
        assert owner.post(URL+'/review',json={}).status_code==400
        assert not owner.app.state.node_recovery._row(NODE)


def test_final_database_failure_preserves_journal_and_retries_without_second_stop(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);checked=review(owner)
        with reg.store.lock:
            reg.store.db.execute("CREATE TRIGGER fail_recovery BEFORE UPDATE OF phase ON remote_node_recoveries WHEN NEW.phase='recovered_stopped' BEGIN SELECT RAISE(ABORT,'injected'); END")
        result=stop(owner,checked)
        assert result['phase']=='pending' and not eng.running
        assert reg.desired_state(NODE,include_payload=False)['revision']==1
        with reg.store.lock:reg.store.db.execute('DROP TRIGGER fail_recovery')
        monkeypatch.setattr(runtime,'_command_locked',lambda *a,**k:pytest.fail('Stop repeated after committed Agent receipt'))
        response=owner.post(URL+'/'+result['attempt_id']+'/retry',json={})
        assert response.status_code==200 and response.json()['recovery_completed'],response.text


def test_agent_checkpoint_race_rejects_recovery_stop(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);checked=review(owner)
        real=nodes_module.node_https_request;new_id=uuid.uuid4().hex
        def race(origin,token,path,*a,**kw):
            if path==REMOTE+'/stop':runtime.ordered_command({'nodeId':NODE,'revision':13,'commandId':new_id,'action':'stop'})
            return real(origin,token,path,*a,**kw)
        monkeypatch.setattr(nodes_module,'node_https_request',race)
        result=stop(owner,checked)
        assert not result['recovery_completed'] and runtime.command_status()['command_id']==new_id
        assert not reg.get(NODE)['enabled']


def test_pending_recovery_journal_survives_real_encrypted_backup(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);checked=review(owner)
        real=nodes_module.node_https_request
        def lost(origin,token,path,*a,**kw):
            result=real(origin,token,path,*a,**kw)
            if path==REMOTE+'/stop':raise OSError('lost ACK')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',lost);result=stop(owner,checked)
        config=tmp_path/'config.json';config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
        archive=tmp_path/'hub.darkbackup';restored=tmp_path/'restored'
        create_backup(tmp_path/'hub',config,archive,'Recovery-Snapshot-Passphrase-123!')
        restore_backup(archive,restored,'Recovery-Snapshot-Passphrase-123!')
        store=Store(restored/'data/dark.sqlite3')
        try:
            auth=Auth(store,restored/'data/secret.key');rebuilt=NodeRegistry(store,auth.cipher);controller=NodeRecovery(rebuilt)
            monkeypatch.setattr(runtime,'_command_locked',lambda *a,**kw:pytest.fail('Restored journal replayed Stop'))
            assert controller.retry(NODE,result['attempt_id'])['recovery_completed']
            assert not rebuilt.get(NODE)['enabled']
        finally:store.close()


def test_install_and_update_ship_only_agent_recovery_protocol():
    root=Path(__file__).resolve().parents[1]
    for file in ('tools/provision_node.py','tools/update_node.py'):
        text=(root/file).read_text()
        assert "'node_recovery_protocol.py'" in text
        assert "'node_recovery.py'" not in text


@pytest.mark.parametrize('bad', ['bad_revision','boolean_revision','wrong_installation','malformed_control','legacy'])
def test_untrusted_or_unsupported_checkpoint_cannot_rebase_hub(hub,tmp_path,monkeypatch,bad):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        real=nodes_module.node_https_request
        def corrupt(*a,**kw):
            doc,ms=real(*a,**kw);doc=copy.deepcopy(doc)
            if bad=='bad_revision':doc['checkpoint']['configuration']['revision']=2**63
            elif bad=='boolean_revision':doc['checkpoint']['command']['revision']=True
            elif bad=='wrong_installation':doc['installation_id']='c'*32
            elif bad=='malformed_control':doc['run_control']=[]
            else:doc['protocol']=False
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',corrupt)
        before=reg.commands.status(NODE)
        response=owner.post(URL+'/review',json={})
        assert response.status_code==400,response.text
        assert reg.commands.status(NODE)==before and not owner.app.state.node_recovery._row(NODE)


def test_pending_replacement_cannot_be_bypassed_by_recovery(hub,tmp_path,monkeypatch):
    from test_node_replacement_prepare import code
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        seen=setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        owner.app.state.replacements.begin(NODE,code());seen.clear()
        response=owner.post(URL+'/review',json={})
        assert response.status_code==400 and 'replacement' in response.text
        assert not seen and not owner.app.state.node_recovery._row(NODE)


def test_readonly_diagnostics_and_agent_unauthorized_stop_do_not_mutate(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        engine.config.writes_enabled=False
        changes=reg.store.db.total_changes
        assert review(owner)['recovery_needed']
        assert owner.get(URL+'/current').status_code==200
        before=runtime.command_status()
        assert client.post(REMOTE+'/stop',json={},headers={'Authorization':''}).status_code==401
        assert runtime.command_status()==before and reg.store.db.total_changes==changes


def test_conditional_stop_rejects_delayed_old_request_before_any_effect(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        post_state(client,payload(eng),1)
        state=client.get(REMOTE+'/state').json()['checkpoint']
        request={'nodeId':NODE,'installationId':runtime.installation_id,'revision':1,
                 'commandId':uuid.uuid4().hex,'expected':state}
        newer={'nodeId':NODE,'revision':2,'commandId':uuid.uuid4().hex,'action':'stop'}
        runtime.ordered_command(newer);before=runtime.command_status()
        response=client.post(REMOTE+'/stop',json=request)
        assert response.status_code==409,response.text
        assert runtime.command_status()==before and not eng.running


def test_pending_checkpoint_retries_after_agent_recreation(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;root=tmp_path/'node'
    with rebooted_agent(root) as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);checked=review(owner)
        real=nodes_module.node_https_request
        def lost(origin,token,path,*a,**kw):
            result=real(origin,token,path,*a,**kw)
            if path==REMOTE+'/stop':raise OSError('lost')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',lost);result=stop(owner,checked)
        assert result['phase']=='pending'
    with rebooted_agent(root,background=True) as (_,eng,runtime,client,_):
        http_transport(reg,client,monkeypatch)
        assert not eng.running
        monkeypatch.setattr(runtime,'_command_locked',lambda *a,**kw:pytest.fail('Committed Stop replayed after reboot'))
        response=owner.post(URL+'/'+result['attempt_id']+'/retry',json={})
        assert response.status_code==200 and response.json()['recovery_completed'],response.text


def test_missing_configuration_snapshot_is_not_reconstructed_from_agent(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        seen=http_transport(reg,client,monkeypatch);reg.probe(NODE);seen.clear()
        response=owner.post(URL+'/review',json={})
        assert response.status_code==400 and 'configuration snapshot' in response.text
        assert not seen and not owner.app.state.node_recovery._row(NODE)


def test_changed_local_inputs_allow_explicit_fresh_review_instead_of_permanent_hold(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch);checked=review(owner)
        real=nodes_module.node_https_request
        def changed(origin,token,path,*a,**kw):
            result=real(origin,token,path,*a,**kw)
            if path==REMOTE+'/stop':
                # A local compiler completed while the network request was in flight.
                with reg.store.transaction() as db:
                    db.execute("UPDATE remote_node_desired_state SET revision=10,applied_revision=0 WHERE node_id=?",(NODE,))
            return result
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',changed);result=stop(owner,checked)
        assert result['phase']=='pending' and not eng.running
        again=review(owner)
        assert again['recovery_needed'] and 'recovery_incomplete' in again['reasons']
        assert stop(owner,again)['recovery_completed']


def test_ordinary_dispatch_stays_pending_until_explicit_recovery(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'node') as (_,eng,runtime,client,_):
        setup_stale(reg,owner,engine,eng,runtime,client,monkeypatch)
        before=runtime.command_status();pid=eng.process.pid
        result=reg.deliver_pending_control(NODE)
        assert result['queued'] and not result['executed']
        assert runtime.command_status()==before and eng.process.pid==pid
        with pytest.raises(PolicyError,match='Refusing stale desired-state revision'):
            reg.sync_desired_state(NODE,reg.desired_state(NODE))
        assert runtime.status()['appliedRevision']==9 and eng.process.pid==pid
        assert stop(owner,review(owner))['recovery_completed']

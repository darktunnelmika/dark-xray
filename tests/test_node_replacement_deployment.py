"""Stopped replacement deployment via real Hub/Agent APIs; fake Xray, no WAN proof."""
import json
import uuid

import pytest
import nodes as nodes_module
from dark_policy import PolicyError
from test_node_replacement_prepare import hub, candidate, prepare, NODE, TOKEN
from test_node_replacement_resolution import resolve
from test_node_installations import http_transport, seed_account, sql_rows


def stage(owner, receipt, **changes):
    body = {'bindingId':receipt['committed_binding_id']}
    body.update(changes)
    response = owner.post(f"/api/nodes/{NODE}/replacement/{receipt['attempt_id']}/stage", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def status(owner, receipt):
    response = owner.get(f"/api/nodes/{NODE}/replacement/{receipt['attempt_id']}/deployment")
    assert response.status_code == 200, response.text
    return response.json()


def test_stage_installs_current_users_and_policy_without_start_or_usage_reset(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,runtime,client,token):
        seen=http_transport(reg,client,monkeypatch)
        committed=resolve(owner,prepare(owner))
        tables=('clients','core_clients','core_inbounds','traffic_ledger','remote_node_client_usage')
        before={t:sql_rows(reg,t) for t in tables}
        meta_before=dict(reg.store.db.execute("SELECT * FROM managed_clients WHERE email='alice'").fetchone())
        seen.clear()
        result=stage(owner,committed)
        assert result['configuration_staged'] and result['snapshot_current'],result
        assert result['phase']=='staged' and result['activation_held']
        assert not result['service_activated'] and not result['cutover_performed']
        assert not result['old_stop_confirmed'] and not result['traffic_tail_complete']
        assert not reg.get(NODE)['enabled'] and not eng.running and not eng.wants_running
        assert runtime.control_status()['manual_stop']
        assert runtime.command_status()['action']=='stop'
        assert runtime.status()['appliedRevision']==result['desired_revision']
        assert runtime.status()['appliedHash']==result['desired_hash']
        mirror=runtime.mirror_for_source('alice');assert mirror
        assert eng.client_detail(mirror)['client']['id']==engine.client_detail('alice')['client']['id']
        assert eng.inbounds()[0]['tag']=='turkey'
        assert {t:sql_rows(reg,t) for t in tables}==before
        meta_after=dict(reg.store.db.execute("SELECT * FROM managed_clients WHERE email='alice'").fetchone())
        # Policy refresh legitimately increments the observation sequence, but
        # must not change user data, public token, usage, expiry or ownership.
        assert meta_after.pop('seq')>=meta_before.pop('seq')
        assert meta_after==meta_before
        assert eng.clients()[0]['traffic']=={'up':0,'down':0}
        assert '/node/api/core/start' not in [entry[1] for entry in seen]
        assert '/node/api/core/restart' not in [entry[1] for entry in seen]
        assert all(x[2].get('X-Dark-Expected-Installation-Id')==runtime.installation_id for x in seen)
        assert TOKEN not in json.dumps(result) and token.token not in json.dumps(result)
        assert status(owner,committed)['configuration_staged']


def test_repeat_stage_does_not_create_new_config_command_or_start(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,runtime,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        first=stage(owner,committed);assert first['configuration_staged'],first
        receipt=runtime.command_status();state=runtime.status();usage=sql_rows(reg,'remote_node_client_usage')
        monkeypatch.setattr(eng,'_spawn',lambda:pytest.fail('Staging attempted to start Xray'))
        second=stage(owner,committed)
        assert second['configuration_staged'],second
        assert second['desired_revision']==first['desired_revision']
        assert runtime.command_status()==receipt and runtime.status()==state
        assert sql_rows(reg,'remote_node_client_usage')==usage


@pytest.mark.parametrize('path',['/node/api/v1/control','/node/api/v1/state/apply','/node/api/core/validate','/node/api/mirrors/traffic'])
def test_lost_reply_keeps_hold_and_retry_uses_same_config_and_stop(hub,tmp_path,monkeypatch,path):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,runtime,client,token):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        real=nodes_module.node_https_request
        def lost(origin,credential,p,*a,**kw):
            result=real(origin,credential,p,*a,**kw)
            if p==path:raise TimeoutError('Sensitive target failure '+credential)
            return result
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',lost)
            failed=stage(owner,committed)
        assert not failed['configuration_staged'] and failed['phase']=='pending',failed
        assert failed['activation_held'] and not reg.get(NODE)['enabled'] and not eng.running
        assert token.token not in json.dumps(failed) and token.token not in json.dumps(sql_rows(reg,'remote_node_replacement_deployments'))
        command=reg.commands.status(NODE)['command_id'];revision=runtime.status()['appliedRevision']
        done=stage(owner,committed)
        assert done['configuration_staged'],done
        assert reg.commands.status(NODE)['command_id']==command
        if revision:assert runtime.status()['appliedRevision']==revision


@pytest.mark.parametrize('mode',['enable','edit-enable','start','restart','commands-start'])
def test_durable_stage_hold_blocks_normal_activation_paths(hub,tmp_path,monkeypatch,mode):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        real=nodes_module.node_https_request
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:(_ for _ in ()).throw(OSError('offline')))
        assert not stage(owner,committed)['configuration_staged']
        control=reg.commands.status(NODE)
        if mode=='enable':
            with pytest.raises(PolicyError,match='activation is held'):reg.set_enabled(NODE,True)
        elif mode=='edit-enable':
            n=reg.get(NODE,secret=True)
            with pytest.raises(PolicyError,match='activation is held'):
                reg.put(NODE,n['name'],n['origin'],n['token'],True,n['inboundIds'],n['data_address'])
        elif mode=='commands-start':
            with pytest.raises(PolicyError,match='activation is held'):reg.commands.record(NODE,'start')
        else:
            response=owner.post(f'/api/nodes/{NODE}/core/{mode}',json={})
            assert response.status_code==400 and 'activation is held' in response.text
        assert reg.commands.status(NODE)==control and not reg.get(NODE)['enabled']
        monkeypatch.setattr(nodes_module,'node_https_request',real)
        assert stage(owner,committed)['configuration_staged']
        with pytest.raises(PolicyError,match='activation is held'):reg.set_enabled(NODE,True)


@pytest.mark.parametrize('mode',['binding','install','credential','command','source-edit','desired-edit'])
def test_changed_state_during_io_cannot_publish_staged_success(hub,tmp_path,monkeypatch,mode):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,runtime,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        real=nodes_module.node_https_request;changed=[]
        def change(origin,credential,path,*a,**kw):
            result=real(origin,credential,path,*a,**kw)
            if path=='/node/api/core/validate' and not changed:
                changed.append(True)
                if mode in {'binding','install'}:
                    field='binding_id' if mode=='binding' else 'installation_id'
                    with reg.store.transaction() as db:
                        db.execute(f'UPDATE remote_node_installations SET {field}=? WHERE node_id=? AND retired_at=0',(uuid.uuid4().hex,NODE))
                elif mode=='credential':
                    with reg.store.transaction() as db:
                        db.execute('UPDATE remote_nodes SET token_enc=? WHERE id=?',(reg.cipher.encrypt(b'dkn_new').decode(),NODE))
                elif mode=='command':reg.commands.record(NODE,'stop')
                elif mode=='source-edit':engine.save_section('dns',{'servers':['8.8.8.8']})
                else:
                    state=reg.desired_state(NODE);state['payload']['sections']['dns']={'servers':['8.8.8.8']}
                    reg.set_desired_state(NODE,state['payload'])
            return result
        monkeypatch.setattr(nodes_module,'node_https_request',change)
        result=stage(owner,committed)
        assert changed and not result['configuration_staged'] and result['last_error'],result
        assert not reg.get(NODE)['enabled'] and not eng.running


@pytest.mark.parametrize('mode',['positive','negative','bool','missing','duplicate','foreign'])
def test_invalid_or_nonzero_target_counters_do_not_reset_central_usage(hub,tmp_path,monkeypatch,mode):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        usage=sql_rows(reg,'remote_node_client_usage');ledger=sql_rows(reg,'traffic_ledger')
        real=nodes_module.node_https_request
        def malformed(origin,credential,path,*a,**kw):
            doc,ms=real(origin,credential,path,*a,**kw)
            if path=='/node/api/mirrors/traffic':
                if mode=='missing':doc['items']=[]
                elif mode=='duplicate':doc['items']*=2
                elif mode=='foreign':doc['items'][0]['sourceEmail']='foreign'
                else:doc['items'][0]['up']={'positive':1,'negative':-1,'bool':False}[mode]
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',malformed)
        result=stage(owner,committed)
        assert not result['configuration_staged'] and result['last_error']=='unexpected_target_counters',result
        assert sql_rows(reg,'remote_node_client_usage')==usage and sql_rows(reg,'traffic_ledger')==ledger


def test_status_is_pure_read_and_local_edits_invalidate_snapshot(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        assert stage(owner,committed)['configuration_staged']
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Status made I/O'))
        changes=reg.store.db.total_changes
        assert status(owner,committed)['configuration_staged']
        assert reg.store.db.total_changes==changes
        engine.save_section('dns',{'servers':['8.8.8.8']})
        report=status(owner,committed)
        assert not report['snapshot_current'] and not report['configuration_staged']
        assert report['phase']=='staged' and report['requires_revalidation']


@pytest.mark.parametrize('field,value',[('bindingId','0'*32),('bindingId',True),('extra','unexpected')])
def test_stage_requires_explicit_binding_confirmation(hub,tmp_path,monkeypatch,field,value):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Invalid request contacted target'))
        body={'bindingId':committed['committed_binding_id']};body[field]=value
        response=owner.post(f"/api/nodes/{NODE}/replacement/{committed['attempt_id']}/stage",json=body)
        assert response.status_code in (400,422),response.text
        assert not sql_rows(reg,'remote_node_replacement_deployments')


def test_auth_csrf_and_readonly_boundaries(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        url=f"/api/nodes/{NODE}/replacement/{committed['attempt_id']}/stage"
        body={'bindingId':committed['committed_binding_id']}
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Unauthorized request made I/O'))
        assert owner.post(url,json=body,headers={'X-Dark-CSRF':''}).status_code==403
        engine.config.writes_enabled=False
        assert owner.post(url,json=body).status_code==409
        assert status(owner,committed)['phase']=='not_started'
        engine.config.writes_enabled=True
        owner.cookies.clear()
        assert owner.post(url,json=body).status_code==401
        assert owner.get(url.rsplit('/',1)[0]+'/deployment').status_code==401


def test_quota_exhausted_before_staging_never_enables_mirrored_client(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,runtime,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        with reg.store.transaction() as db:db.execute("UPDATE clients SET used_bytes=quota_bytes WHERE id='alice'")
        # Central recomputation uses accumulated remote usage, not just the last
        # UI counter. Model a fully charged period before refreshing policy.
        with reg.store.transaction() as db:
            db.execute('UPDATE remote_node_client_usage SET current_up=?,current_down=0 WHERE node_id=?',(100*1024**3,NODE))
        result=stage(owner,committed);assert result['configuration_staged'],result
        assert eng.client_detail(runtime.mirror_for_source('alice'))['client']['enable'] is False
        assert not eng.running


def test_agent_recreation_and_hub_backup_preserve_staging_hold_and_safe_retry(hub,tmp_path,monkeypatch):
    from auth import Auth
    from backup import create_backup, restore_backup
    from dark_policy import Store
    from nodes import NodeRegistry
    from node_replacement import NodeReplacement
    from node_replacement_deployment import ReplacementDeployment
    from dataclasses import asdict
    from pathlib import Path
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    target=tmp_path/'target'
    with candidate(target) as (_,runtime,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        first=stage(owner,committed);assert first['configuration_staged']
        source=reg.desired_state(NODE)['payload']
        config=tmp_path/'config.json';config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
        archive=tmp_path/'full.darkbackup'
        create_backup(Path(reg.store.path).parent,config,archive,'Recovery-Test-Only123!')
    dest=tmp_path/'restore';restore_backup(archive,dest,'Recovery-Test-Only123!')
    store=Store(dest/'data/dark.sqlite3')
    try:
        auth=Auth(store,dest/'data/secret.key');rebuilt=NodeRegistry(store,auth.cipher)
        deployment=ReplacementDeployment(rebuilt,NodeReplacement(rebuilt),lambda _:source)
        assert deployment.status(NODE,committed['attempt_id'])['configuration_staged']
        with pytest.raises(PolicyError,match='activation is held'):rebuilt.set_enabled(NODE,True)
        with candidate(target,background=True) as (eng,runtime,client,_):
            assert not eng.running and not eng.wants_running
            http_transport(rebuilt,client,monkeypatch)
            result=deployment.stage(NODE,committed['attempt_id'],binding_id=committed['committed_binding_id'])
            assert result['configuration_staged'] and not eng.running,result
    finally:store.close()


def test_remote_failure_text_never_enters_public_desired_state_or_journal(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,token):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        real=nodes_module.node_https_request
        def leak(origin,credential,path,*a,**kw):
            if path=='/node/api/v1/state/apply':raise nodes_module.NodeHTTPError(422,'secret-'+credential)
            return real(origin,credential,path,*a,**kw)
        monkeypatch.setattr(nodes_module,'node_https_request',leak)
        result=stage(owner,committed)
        assert not result['configuration_staged']
        assert token.token not in json.dumps(result)
        assert token.token not in json.dumps(reg.desired_state(NODE,include_payload=False))
        assert token.token not in json.dumps(sql_rows(reg,'remote_node_replacement_deployments'))


@pytest.mark.parametrize('mode',['bad-validation','bad-hash','incomplete-ack','bad-health-revision','target-running'])
def test_incomplete_validation_or_ack_never_reports_ready(hub,tmp_path,monkeypatch,mode):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        real=nodes_module.node_https_request;applied=[]
        def fault(origin,credential,path,*a,**kw):
            doc,ms=real(origin,credential,path,*a,**kw)
            if path=='/node/api/v1/state/apply':
                applied.append(True)
                if mode=='incomplete-ack':doc['items']=[]
            if path=='/node/api/core/validate':
                if mode=='bad-validation':doc['engine']['validated']=False
                if mode=='bad-hash':doc['engine']['hash']=''
            if path=='/node/api/health' and applied:
                if mode=='bad-health-revision':doc['desired_state']['appliedRevision']=True
                if mode=='target-running':doc['core']['state']='running'
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',fault)
        result=stage(owner,committed)
        assert not result['configuration_staged'] and result['last_error'],result
        assert result['activation_held'] and not reg.get(NODE)['enabled'] and not eng.running


def test_missing_tls_file_never_gets_false_staged_success(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;inbound_id=seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,_,client,_):
        http_transport(reg,client,monkeypatch);committed=resolve(owner,prepare(owner))
        # Persist a missing managed TLS dependency without bypassing any target
        # validation; this models loss of the Hub file after a restore.
        with reg.store.transaction() as db:
            raw=json.loads(db.execute('SELECT body FROM core_inbounds WHERE id=?',(inbound_id,)).fetchone()[0])
            raw['streamSettings']={'network':'tcp','security':'tls','tlsSettings':{'certificates':[
                {'certificateFile':str(tmp_path/'missing-cert'),'keyFile':str(tmp_path/'missing-key')} ]}}
            db.execute('UPDATE core_inbounds SET body=? WHERE id=?',(json.dumps(raw),inbound_id))
        result=stage(owner,committed)
        assert not result['configuration_staged'] and result['last_error']
        assert not eng.running and not eng.inbounds()

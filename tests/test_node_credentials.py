"""Registered token rotation: real SQLite/Agent API, fake Xray and socket bridge."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from auth import Auth
from backup import create_backup, restore_backup
from dark_policy import PolicyError, Store
from node_credentials import NodeCredentials
from nodes import NodeRegistry
import nodes as node_module
from test_hub_node_control_live import hub
from test_node_control_lifecycle import rebooted_agent
from test_node_installations import http_transport, sql_rows, seed_account
from test_node_hub_recovery import NODE, TOKEN, ORIGIN, payload, post_state

CANDIDATE='dkn_'+'R'*60
PATH=f'/api/nodes/{NODE}/credentials'


def body(reg, **changes):
    return {'bindingId':reg.installations.capture(NODE)['binding_id'], 'token':CANDIDATE,
            'confirmRotate':True, **changes}


def rotate(reg,owner,**changes):
    response=owner.post(PATH+'/rotate',json=body(reg,**changes))
    assert response.status_code==200,response.text
    return response.json()


def retry(owner,saved):
    response=owner.post(PATH+'/'+saved['attempt_id']+'/retry',json={'confirmRetry':True})
    assert response.status_code==200,response.text
    return response.json()


def wire(reg,client,monkeypatch,**kwargs):
    seen=http_transport(reg,client,monkeypatch,**kwargs)
    reg.probe(NODE);seen.clear()
    return seen


def saved_row(reg):
    with reg.store.lock:return dict(reg.store.db.execute('SELECT * FROM remote_node_credentials ORDER BY created_at DESC LIMIT 1').fetchone())


def test_live_registered_node_keeps_pid_configuration_accounts_and_usage(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with rebooted_agent(tmp_path/'agent') as (store,target,runtime,client,_):
        post_state(client,payload(target));seen=wire(reg,client,monkeypatch)
        before={t:sql_rows(reg,t) for t in ('clients','owners','managed_clients','core_clients','core_inbounds',
            'remote_node_client_usage','remote_node_inbounds','remote_node_control','remote_node_installations','traffic_ledger')}
        pid=target.process.pid;state=runtime.status();result=rotate(reg,owner)
        assert result['rotated'] is True and result['phase']=='completed' and result['credential_current'] is True
        assert reg.get(NODE,secret=True)['token']==CANDIDATE
        assert target.process.pid==pid and runtime.status()==state
        assert {t:sql_rows(reg,t) for t in before}==before
        assert [(m,p) for m,p,_ in seen if m!='GET']==[('POST','/node/api/v1/token/rotate')]
        row=saved_row(reg);assert row['original_enc']==row['candidate_enc']==''
        assert TOKEN not in json.dumps(result) and CANDIDATE not in json.dumps(result)
        seen.clear();reg.set_enabled(NODE,False)
        assert retry(owner,result)['rotated'] and seen==[] and not reg.get(NODE)['enabled']


@pytest.mark.parametrize('mode',['lost_reply','fake_ack','new_health_error','db_failure'])
def test_ambiguous_handoff_retains_credentials_then_retries_same_candidate(hub,tmp_path,monkeypatch,mode):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,target,_,client,_):
        seen=wire(reg,client,monkeypatch);original=node_module.node_https_request
        def transport(origin,credential,path,*args,**kwargs):
            if mode=='fake_ack' and path.endswith('/token/rotate'):
                return {'service':'DARK XRAY NODE','rotated':True},1
            answer=original(origin,credential,path,*args,**kwargs)
            if mode=='lost_reply' and path.endswith('/token/rotate'):raise OSError('Lost '+CANDIDATE)
            if mode=='new_health_error' and credential==CANDIDATE:raise OSError('Lost new health')
            return answer
        monkeypatch.setattr(node_module,'node_https_request',transport)
        if mode=='db_failure':
            with reg.store.lock:reg.store.db.execute("CREATE TRIGGER reject_rotation_commit BEFORE UPDATE OF token_enc ON remote_nodes BEGIN SELECT RAISE(ABORT,'Injected'); END")
        saved=rotate(reg,owner);row=saved_row(reg)
        assert saved['pending'] is True and saved['rotated'] is False
        assert row['candidate_enc'] and row['original_enc']
        assert reg.cipher.decrypt(row['candidate_enc'].encode()).decode()==CANDIDATE
        assert reg.get(NODE,secret=True)['token']==TOKEN
        assert CANDIDATE not in json.dumps(saved)
        assert CANDIDATE not in row['last_error']
        monkeypatch.setattr(node_module,'node_https_request',original)
        if mode=='db_failure':
            with reg.store.lock:reg.store.db.execute('DROP TRIGGER reject_rotation_commit')
        seen.clear();restored=NodeCredentials(NodeRegistry(reg.store,reg.cipher))
        result=restored.retry(NODE,saved['attempt_id'])
        assert result['rotated'] and not result['pending']
        if mode!='fake_ack':assert not any(m!='GET' for m,_,_ in seen)
        assert reg.get(NODE,secret=True)['token']==CANDIDATE


@pytest.mark.parametrize('mode',['tls','http403','bad_identity','unsupported'])
def test_candidate_verification_failure_never_falls_back_or_rotates(hub,tmp_path,monkeypatch,mode):
    from nodes import NodeHTTPError
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch);original=node_module.node_https_request;calls=[]
        def transport(origin,credential,path,*args,**kwargs):
            calls.append(credential)
            if mode=='tls':raise PolicyError('TLS error '+CANDIDATE)
            if mode=='http403':raise NodeHTTPError(403)
            doc=client.get('/node/api/health').json()
            if mode=='bad_identity':doc['installation_id']='b'*32
            else:doc['capabilities']['credential_rotation']=True
            return doc,1
        monkeypatch.setattr(node_module,'node_https_request',transport)
        result=rotate(reg,owner)
        assert result['pending'] and not result['rotated'] and calls==[CANDIDATE]
        assert seen==[] and reg.get(NODE,secret=True)['token']==TOKEN


def test_offline_journal_reservations_allow_stop_but_block_replacement_edit_delete(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        wire(reg,client,monkeypatch)
        monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k: (_ for _ in ()).throw(OSError('Offline')))
        saved=rotate(reg,owner);assert saved['pending']
        other=NodeRegistry(reg.store,reg.cipher)
        with pytest.raises(sqlite3.IntegrityError):other.put(NODE,'Edit',ORIGIN,TOKEN)
        with pytest.raises(sqlite3.IntegrityError):other.delete(NODE)
        with reg.store.lock:
            with pytest.raises(sqlite3.IntegrityError):reg.store.db.execute("UPDATE remote_node_installations SET retired_at=1 WHERE node_id=?",(NODE,))
        from test_node_replacement_prepare import code
        response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code()})
        assert response.status_code==409,response.text
        reg.set_enabled(NODE,False);reg.commands.record(NODE,'stop')
        assert not reg.get(NODE)['enabled']
        with pytest.raises(PolicyError):NodeCredentials(reg).begin(NODE,'dkn_'+'S'*60,binding_id=body(reg)['bindingId'])
        again=rotate(reg,owner);assert again['attempt_id']==saved['attempt_id']


@pytest.mark.parametrize('token',['bad','dkn_'+'A'*36+'\n','dkn_'+'آ'*40,'dkn_'+'A'*253,True])
def test_invalid_candidate_never_saved_or_contacted(hub,monkeypatch,token):
    reg,owner,_=hub
    monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('Contacted'))
    response=owner.post(PATH+'/rotate',json=body(reg,token=token))
    assert response.status_code in (400,422),response.text
    assert owner.get(PATH+'/current').json()['phase']=='not_started'


@pytest.mark.parametrize('change',[{'confirmRotate':False},{'confirmRotate':'true'},{'extra':1},{'bindingId':'a'*32}])
def test_strict_owner_confirmation_and_binding(hub,tmp_path,monkeypatch,change):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch)
        response=owner.post(PATH+'/rotate',json=body(reg,**change))
        assert response.status_code in (400,422),response.text
        assert seen==[] and owner.get(PATH+'/current').json()['phase']=='not_started'


def test_unknown_or_unpinned_node_rejected_before_contact(hub,monkeypatch):
    reg,owner,_=hub
    monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('Contacted'))
    assert owner.post(PATH+'/rotate',json=body(reg)).status_code==400
    assert owner.get(PATH.replace(NODE,'missing')+'/current').status_code==400


def test_status_is_readonly_nonsecret_and_cross_node_attempt_rejected(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        wire(reg,client,monkeypatch)
        monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k: (_ for _ in ()).throw(OSError('Offline')))
        saved=rotate(reg,owner)
        monkeypatch.setattr(reg.cipher,'decrypt',lambda *a:pytest.fail('Status decrypted a credential'))
        before=reg.store.db.total_changes
        status=owner.get(PATH+'/current');assert status.status_code==200
        assert reg.store.db.total_changes==before
        assert status.json()['attempt_id']==saved['attempt_id']
        assert all(k not in status.json() for k in ('candidate_enc','original_enc','candidate_hash','published_hash'))
        assert owner.post(PATH.replace(NODE,'other')+'/'+saved['attempt_id']+'/retry',json={'confirmRetry':True}).status_code==400


def test_encrypted_backup_and_agent_recreation_resume_the_same_handoff(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;root=tmp_path/'agent'
    with rebooted_agent(root) as (_,_,_,client,_):
        wire(reg,client,monkeypatch)
        original=node_module.node_https_request
        def lost(*a,**k):
            result=original(*a,**k)
            if a[2].endswith('/token/rotate'):raise OSError('Lost ACK')
            return result
        monkeypatch.setattr(node_module,'node_https_request',lost);saved=rotate(reg,owner)
        assert saved['pending']
    config=tmp_path/'config.json';config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
    archive=tmp_path/'backup.darkbackup';restored=tmp_path/'restored'
    create_backup(tmp_path/'hub',config,archive,'Credential-Backup-Test-123!')
    restore_backup(archive,restored,'Credential-Backup-Test-123!')
    store=Store(restored/'data/dark.sqlite3')
    try:
        auth=Auth(store,restored/'data/secret.key');rebuilt=NodeRegistry(store,auth.cipher)
        with rebooted_agent(root) as (_,_,_,client,_):
            seen=http_transport(rebuilt,client,monkeypatch)
            result=NodeCredentials(rebuilt).retry(NODE,saved['attempt_id'])
            assert result['rotated'] and not any(m!='GET' for m,_,_ in seen)
            assert rebuilt.get(NODE,secret=True)['token']==CANDIDATE
    finally:store.close()


def test_concurrent_retry_executes_one_remote_rotation(hub,tmp_path,monkeypatch):
    reg,_,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch);coordinator=NodeCredentials(reg)
        saved=coordinator.begin(NODE,CANDIDATE,binding_id=body(reg)['bindingId'])
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:coordinator.retry(NODE,saved['attempt_id']),range(3)))
        assert all(r['rotated'] for r in results)
        assert len([1 for m,p,_ in seen if p.endswith('/token/rotate') and m=='POST'])==1


@pytest.mark.parametrize('mode',['readonly','csrf','unauthenticated','reseller','key'])
def test_access_boundaries(hub,tmp_path,monkeypatch,mode):
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch);b=body(reg)
        if mode=='readonly':engine.config.writes_enabled=False
        if mode=='csrf':owner.headers['X-Dark-CSRF']='wrong'
        if mode=='unauthenticated':owner.cookies.clear()
        if mode=='reseller':
            with reg.store.transaction() as db:db.execute("UPDATE api_admins SET role='reseller' WHERE id='dark'")
        if mode=='key':
            key=owner.post('/api/keys',json={'name':'credentials','days':1,'permissions':{'clients.read':'all'}}).json()['key']
            owner.cookies.clear();owner.headers['Authorization']='Bearer '+key
        response=owner.post(PATH+'/rotate',json=b)
        if mode in ('readonly','csrf','unauthenticated','reseller','key'):
            assert response.status_code in (401,403,409);assert seen==[]


def edit_body(reg,**changes):
    n=reg.get(NODE)
    return {'name':n['name'],'origin':n['origin'],'enabled':bool(n['enabled']),'dataAddress':n['data_address'],
            'priority':n['priority'],'failoverEnabled':bool(n['failover_enabled']),'inboundIds':n['inboundIds'],
            'token':CANDIDATE,**changes}


@pytest.mark.parametrize('changes',[{'name':'Changed'},{'origin':'https://other.example.test'}, {'inboundIds':[999]}])
def test_invalid_or_mixed_settings_edit_never_rotates(hub,tmp_path,monkeypatch,changes):
    reg,owner,_=hub;reg.set_inbound_assignment(NODE,1,False)
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch)
        response=owner.patch('/api/nodes/'+NODE,json=edit_body(reg,**changes))
        assert response.status_code in (400,409),response.text
        assert seen==[] and owner.get(PATH+'/current').json()['phase']=='not_started'


def test_legacy_edit_reports_pending_without_saving_or_syncing_settings(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;reg.set_inbound_assignment(NODE,1,False)
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch);original=node_module.node_https_request
        def lost(*a,**k):
            result=original(*a,**k)
            if a[2].endswith('/token/rotate'):raise OSError('Lost')
            return result
        monkeypatch.setattr(node_module,'node_https_request',lost)
        response=owner.patch('/api/nodes/'+NODE,json=edit_body(reg))
        assert response.status_code==200,response.text
        result=response.json();assert result['pending'] and not result['rotated']
        assert reg.get(NODE)['name']=='Node' and reg.get(NODE,secret=True)['token']==TOKEN
        assert not any('state/apply' in path for _,path,_ in seen)


def test_completed_candidate_can_be_confirmed_after_remote_becomes_readonly(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,target,_,client,_):
        wire(reg,client,monkeypatch);original=node_module.node_https_request
        def after_rotation(*a,**k):
            result=original(*a,**k)
            if a[2].endswith('/token/rotate'):target.config.writes_enabled=False
            return result
        monkeypatch.setattr(node_module,'node_https_request',after_rotation)
        result=rotate(reg,owner);assert result['rotated'] and reg.get(NODE,secret=True)['token']==CANDIDATE


def test_disabled_node_rotation_does_not_enable_or_start(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent',autostart=False) as (_,target,_,client,_):
        seen=wire(reg,client,monkeypatch);reg.set_enabled(NODE,False)
        assert rotate(reg,owner)['rotated']
        assert not reg.get(NODE)['enabled'] and not target.running
        assert not any('/control' in p or 'state/apply' in p for _,p,_ in seen)


def test_known_retired_tokens_cannot_be_reused_and_completed_repeat_is_readonly(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch);first=rotate(reg,owner)
        second=rotate(reg,owner,token='dkn_'+'S'*60);seen.clear()
        for token in (TOKEN,CANDIDATE):
            r=owner.post(PATH+'/rotate',json=body(reg,token=token))
            assert r.status_code==400 and 'reuse' in r.text
        reg.set_enabled(NODE,False)
        repeat=rotate(reg,owner,token='dkn_'+'S'*60)
        assert repeat['attempt_id']==second['attempt_id'] and not reg.get(NODE)['enabled'] and seen==[]
        older=retry(owner,first)
        assert older['rotated'] and not older['credential_current'] and seen==[]


def test_conflicting_recovery_or_prepared_replacement_blocks_before_io(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch)
        from test_node_replacement_prepare import code
        owner.app.state.replacements.begin(NODE,code())
        seen.clear();r=owner.post(PATH+'/rotate',json=body(reg))
        assert r.status_code==400 and 'conflicting' in r.text and seen==[]


def test_separate_registries_share_durable_candidate_and_serialize_publication(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=wire(reg,client,monkeypatch)
        first=NodeCredentials(reg);second=NodeCredentials(NodeRegistry(reg.store,reg.cipher))
        saved=first.begin(NODE,CANDIDATE,binding_id=body(reg)['bindingId'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda c:c.retry(NODE,saved['attempt_id']),[first,second]))
        # The losing caller may observe pending, but it cannot overwrite the winner.
        final=first.retry(NODE,saved['attempt_id'])
        assert final['rotated'] and reg.get(NODE,secret=True)['token']==CANDIDATE
        assert len([1 for m,p,_ in seen if m=='POST' and p.endswith('/token/rotate')])<=1


def test_schema_reconstruction_preserves_pending_credential_and_reservations(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        wire(reg,client,monkeypatch)
        monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k: (_ for _ in ()).throw(OSError('Offline')))
        saved=rotate(reg,owner);before=saved_row(reg)
        reconstructed=NodeCredentials(NodeRegistry(reg.store,reg.cipher))
        assert reconstructed.status(NODE)['attempt_id']==saved['attempt_id'] and saved_row(reg)==before
        with pytest.raises(sqlite3.IntegrityError):reg.delete(NODE)


@pytest.mark.parametrize('first',['recovery','credentials'])
def test_recovery_and_rotation_cannot_compete_for_the_same_binding(hub,tmp_path,monkeypatch,first):
    from test_node_recovery import setup_stale, review, consent, URL
    reg,owner,engine=hub
    with rebooted_agent(tmp_path/'agent') as (_,target,runtime,client,_):
        setup_stale(reg,owner,engine,target,runtime,client,monkeypatch)
        reviewed=consent(review(owner));original=node_module.node_https_request;mutations=[]
        def offline_mutation(*a,**kw):
            if len(a)>3 and a[3]=='POST':mutations.append(a[2]);raise OSError('Offline')
            return original(*a,**kw)
        monkeypatch.setattr(node_module,'node_https_request',offline_mutation)
        if first=='recovery':
            response=owner.post(URL+'/stop',json=reviewed);assert response.status_code==200,response.text
            assert response.json()['phase']=='pending'
            mutations.clear();response=owner.post(PATH+'/rotate',json=body(reg))
            assert response.status_code==400 and 'conflicting' in response.text and not mutations
        else:
            coordinator=owner.app.state.node_credentials
            saved=coordinator.begin(NODE,CANDIDATE,binding_id=body(reg)['bindingId'])
            before=reg.commands.status(NODE)
            response=owner.post(URL+'/stop',json=reviewed)
            assert response.status_code==409,response.text
            assert reg.commands.status(NODE)==before and not mutations
            assert coordinator.status(NODE)['attempt_id']==saved['attempt_id']

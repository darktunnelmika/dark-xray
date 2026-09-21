"""Commit/discard recovery: real Hub/Agent API + SQLite, fake Xray, no WAN proof."""
import json
import uuid
from dataclasses import asdict

import pytest
import nodes as nodes_module
from auth import Auth
from backup import create_backup, restore_backup
from dark_policy import Store, PolicyError
from node_replacement import NodeReplacement
from test_node_replacement_prepare import (hub, candidate, prepare, code, retry, journal,
                                          NODE, TOKEN, TARGET_ORIGIN)
from test_node_installations import http_transport, sql_rows, seed_account
from test_node_hub_recovery import payload, post_state


def commit_body(result):
    return {'sourceBindingId':result['source_binding_id'],
            'acceptUnconfirmedOldServer':True,'acceptUnreportedTraffic':True}


def resolve(owner,result,action='commit',body=None):
    data=commit_body(result) if action=='commit' else {'discardCandidate':True}
    response=owner.post(f"/api/nodes/{NODE}/replacement/{result['attempt_id']}/{action}",
                        json=data if body is None else body)
    assert response.status_code==200,response.text
    return response.json()


def test_commit_preserves_accounts_and_retires_once_with_secret_free_receipt(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    tables=('clients','managed_clients','core_clients','core_inbounds','owners','traffic_ledger')
    before={table:sql_rows(reg,table) for table in tables}
    before_inbounds=reg.assignments(NODE)
    with candidate(tmp_path/'target') as (eng,runtime,client,token):
        http_transport(reg,client,monkeypatch)
        prepared=prepare(owner);assert prepared['prepared']
        old_binding=reg.installations.public_status(NODE)
        result=resolve(owner,prepared)
        assert result['phase']=='committed' and result['binding_committed'],result
        assert result['binding_current'] and result['generation']==2
        assert not result['service_activated'] and not result['cutover_performed']
        assert not result['old_stop_confirmed'] and not result['traffic_tail_complete']
        assert not reg.get(NODE)['enabled'] and not eng.running
        assert reg.get(NODE,secret=True)['token']==token.token and token.token!=TOKEN
        assert reg.installations.public_status(NODE)['installation_id']==runtime.installation_id
        assert reg.installations.history(NODE)[0]['binding_id']==old_binding['binding_id']
        assert reg.installations.history(NODE)[0]['retired_at']>0
        assert {table:sql_rows(reg,table) for table in tables}==before
        assert [x['local_inbound_id'] for x in reg.assignments(NODE)]==[x['local_inbound_id'] for x in before_inbounds]
        assert reg.commands.status(NODE)['action']=='stop' and reg.commands.status(NODE)['pending']
        assert not sql_rows(reg,'remote_node_replacements')
        assert token.token not in json.dumps(result) and TOKEN not in json.dumps(result)
        assert 'candidate_enc' not in json.dumps(sql_rows(reg,'remote_node_replacement_history'))
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Repeated commit made I/O'))
        assert resolve(owner,prepared)==result
        assert retry(owner,result['attempt_id'])==result
        assert len(reg.installations.history(NODE))==2


@pytest.mark.parametrize('field,value',[
    ('sourceBindingId','0'*32),('acceptUnconfirmedOldServer',False),('acceptUnreportedTraffic',False),
    ('acceptUnreportedTraffic',1),('acceptUnconfirmedOldServer','true'),('unexpected','extra'),
])
def test_explicit_commit_confirmation_required(hub,tmp_path,monkeypatch,field,value):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);result=prepare(owner);before=reg.get(NODE,secret=True)
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Unconfirmed commit contacted target'))
        body=commit_body(result);body[field]=value
        response=owner.post(f"/api/nodes/{NODE}/replacement/{result['attempt_id']}/commit",json=body)
        assert response.status_code in (400,422),response.text
        assert reg.get(NODE,secret=True)==before and journal(reg)['resolution']==''


@pytest.mark.parametrize('failure',['offline','nonempty','reinstalled','new-token-invalid','source-changed'])
def test_commit_revalidates_and_keeps_journal_on_failure(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (eng,runtime,client,token):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);before=reg.get(NODE,secret=True)
        if failure=='nonempty':
            body=payload(eng);body['nodeId']='new-turkey';client.headers['Authorization']='Bearer '+token.token
            post_state(client,body)
        if failure=='new-token-invalid':token.rotate('dkn_'+'Z'*60)
        if failure=='source-changed':
            with reg.store.transaction() as db:db.execute("UPDATE remote_nodes SET origin='https://changed.test' WHERE id=?",(NODE,))
        real=nodes_module.node_https_request
        def fail(*a,**kw):
            if failure=='offline':raise TimeoutError('token: '+token.token)
            doc,ms=real(*a,**kw)
            if failure=='reinstalled':doc['installation_id']=uuid.uuid4().hex
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',fail)
        response=owner.post(f"/api/nodes/{NODE}/replacement/{prepared['attempt_id']}/commit",json=commit_body(prepared))
        if failure=='source-changed':assert response.status_code==400,response.text
        else:
            assert response.status_code==200,response.text
            result=response.json();assert not result['binding_committed'] and result['last_error']
            assert reg.get(NODE,secret=True)==before
            assert token.token not in response.text
        assert len(reg.installations.history(NODE))==1 and journal(reg)['candidate_enc']


def test_atomic_failure_rolls_back_binding_receipt_and_credential_cleanup(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);result=prepare(owner);before=reg.get(NODE,secret=True)
        real=NodeReplacement._finish
        def fail(self,*a,**kw):real(self,*a,**kw);raise OSError('Injected pre-COMMIT failure')
        with monkeypatch.context() as patch:
            patch.setattr(NodeReplacement,'_finish',fail)
            failed=resolve(owner,result)
        assert not failed['binding_committed'] and failed['last_error']
        assert reg.get(NODE,secret=True)==before and len(reg.installations.history(NODE))==1
        assert journal(reg)['candidate_enc'] and not sql_rows(reg,'remote_node_replacement_history')
        assert retry(owner,result['attempt_id'])['binding_committed']


def test_cancel_disposes_candidate_only_and_erases_live_journal(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (eng,_,client,token):
        seen=http_transport(reg,client,monkeypatch);prepared=prepare(owner)
        before=reg.get(NODE,secret=True);active_token=token.token
        result=resolve(owner,prepared,'cancel')
        assert result['phase']=='cancelled' and result['candidate_discarded'],result
        assert result['candidate_requires_reinstall'] and not eng.running
        assert not result['binding_committed'] and reg.get(NODE,secret=True)==before
        assert token.token not in (TOKEN,active_token)
        assert not sql_rows(reg,'remote_node_replacements')
        for old in (TOKEN,active_token):
            assert client.get('/node/api/health',headers={'Authorization':'Bearer '+old}).status_code==401
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Cancelled operation contacted target'))
        assert resolve(owner,prepared,'cancel')==result
        assert retry(owner,result['attempt_id'])==result
        reg.put('spare','Spare',TARGET_ORIGIN,'dkn_'+'Z'*60,False,[])
        assert reg.get('spare')['origin']==TARGET_ORIGIN  # Reservation is now released.


@pytest.mark.parametrize('failure',['offline','lost-idle','lost-rotation','lost-new-health','nonempty'])
def test_cancel_ambiguous_or_unsafe_target_retains_credentials_and_reservation(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (eng,_,client,token):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);before=reg.get(NODE,secret=True)
        if failure=='lost-idle':eng.command('start')
        if failure=='nonempty':
            body=payload(eng);body['nodeId']='new-turkey';client.headers['Authorization']='Bearer '+token.token
            post_state(client,body)
        real=nodes_module.node_https_request;calls=[]
        def fail(origin,cred,path,*a,**kw):
            calls.append(path)
            if failure=='offline':raise TimeoutError('Secret '+cred)
            response=real(origin,cred,path,*a,**kw)
            if failure=='lost-idle' and path.endswith('/replacement/idle'):raise OSError('Lost idle reply')
            if failure=='lost-rotation' and path.endswith('/replacement/rotate-token'):raise OSError('Lost rotate reply')
            if failure=='lost-new-health' and path=='/node/api/health' and token.token==cred and cred not in (TOKEN,reg.cipher.decrypt(journal(reg)['candidate_enc'].encode()).decode()):
                raise OSError('Lost new-token proof')
            return response
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',fail)
            failed=resolve(owner,prepared,'cancel')
        assert failed['phase']=='cancelling' and failed['last_error'],failed
        saved=journal(reg);assert saved['candidate_enc'] and saved['resolution_token_enc']
        assert reg.get(NODE,secret=True)==before and not sql_rows(reg,'remote_node_replacement_history')
        with pytest.raises(PolicyError,match='reserved'):
            reg.put('spare','Spare',TARGET_ORIGIN,'dkn_'+'Z'*60,False,[])
        if failure!='nonempty':
            result=retry(owner,prepared['attempt_id']);assert result['candidate_discarded'],result


@pytest.mark.parametrize('action',['commit','cancel'])
@pytest.mark.parametrize('access',['anonymous','csrf','readonly'])
def test_resolution_access_boundaries_before_mutation(hub,tmp_path,monkeypatch,action,access):
    reg,owner,engine=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);before=journal(reg)
        if access=='anonymous':owner.cookies.clear()
        if access=='csrf':owner.headers['X-Dark-CSRF']='bad'
        if access=='readonly':engine.config.writes_enabled=False
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Unauthorized I/O'))
        body=commit_body(prepared) if action=='commit' else {'discardCandidate':True}
        response=owner.post(f"/api/nodes/{NODE}/replacement/{prepared['attempt_id']}/{action}",json=body)
        assert response.status_code in (401,403,409),response.text
        assert journal(reg)==before


def test_cancellation_requires_explicit_disposal_consent(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);before=journal(reg)
        for body in ({},{'discardCandidate':False},{'discardCandidate':1}):
            response=owner.post(f"/api/nodes/{NODE}/replacement/{prepared['attempt_id']}/cancel",json=body)
            assert response.status_code in (400,422)
        assert journal(reg)==before


def test_opposite_terminal_actions_are_rejected(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);resolve(owner,prepared)
        assert owner.post(f"/api/nodes/{NODE}/replacement/{prepared['attempt_id']}/cancel",json={'discardCandidate':True}).status_code==400


def test_cancellation_can_cleanup_after_source_changed(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner)
        with reg.store.transaction() as db:db.execute("UPDATE remote_nodes SET origin='https://changed.test' WHERE id=?",(NODE,))
        before=reg.get(NODE,secret=True)
        assert resolve(owner,prepared,'cancel')['candidate_discarded']
        assert reg.get(NODE,secret=True)==before


@pytest.mark.parametrize('resolution',['committing','cancelling'])
def test_pending_resolution_recovers_from_encrypted_backup(hub,tmp_path,monkeypatch,resolution):
    reg,owner,engine=hub;root=tmp_path/'target'
    with candidate(root) as (_,_,client,token):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner)
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',lambda *a,**k:(_ for _ in ()).throw(OSError('offline')))
            failed=resolve(owner,prepared,'commit' if resolution=='committing' else 'cancel')
        assert failed['phase']==resolution
        hub_root=engine.runtime.parent;cfg=hub_root/'config.json'
        cfg.write_text(json.dumps(asdict(engine.config)));cfg.chmod(0o600)
        archive=tmp_path/'saved.darkbackup';create_backup(hub_root,cfg,archive,'Test-Backup-Only!123')
        restored=tmp_path/'restored';restore_backup(archive,restored,'Test-Backup-Only!123')
        store=Store(restored/'data/dark.sqlite3')
        try:
            auth=Auth(store,restored/'data/secret.key');recovered=nodes_module.NodeRegistry(store,auth.cipher)
            controller=NodeReplacement(recovered)
            result=controller.resume(NODE,prepared['attempt_id'])
            assert result['phase']==('committed' if resolution=='committing' else 'cancelled'),result
        finally:store.close()


def test_disposal_receipt_failure_keeps_recovery_secret_then_retries_without_rotation(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,token):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);active=token.token
        real=NodeReplacement._finish
        def fail(self,*a,**kw):real(self,*a,**kw);raise OSError('Receipt transaction failed')
        with monkeypatch.context() as patch:
            patch.setattr(NodeReplacement,'_finish',fail)
            failed=resolve(owner,prepared,'cancel')
        assert failed['phase']=='cancelling' and failed['last_error']
        saved=journal(reg);assert reg.cipher.decrypt(saved['resolution_token_enc'].encode()).decode()==token.token!=active
        assert not sql_rows(reg,'remote_node_replacement_history')
        real_transport=nodes_module.node_https_request
        def no_rotation(origin,cred,path,*a,**kw):
            assert not path.endswith('/rotate-token'),'Disposal credential changed again'
            return real_transport(origin,cred,path,*a,**kw)
        monkeypatch.setattr(nodes_module,'node_https_request',no_rotation)
        assert retry(owner,prepared['attempt_id'])['candidate_discarded']


def test_cancel_from_another_registry_fences_inflight_commit(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);before=reg.get(NODE,secret=True)
        other=NodeReplacement(nodes_module.NodeRegistry(reg.store,reg.cipher))
        real=nodes_module.node_https_request;once=[]
        def cancel_during_health(*a,**kw):
            response=real(*a,**kw)
            if not once:
                once.append(True)
                assert other.cancel(NODE,prepared['attempt_id'],discard_candidate=True)['candidate_discarded']
            return response
        monkeypatch.setattr(nodes_module,'node_https_request',cancel_during_health)
        result=resolve(owner,prepared)
        assert result['phase']=='cancelled' and not result['binding_committed']
        assert reg.get(NODE,secret=True)==before and len(reg.installations.history(NODE))==1


@pytest.mark.parametrize('failure',['timeout','identity','redirect'])
def test_disposal_never_falls_back_on_ambiguous_new_token_health(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);calls=[]
        def fail(*a,**kw):
            calls.append(a)
            if failure=='timeout':raise TimeoutError('ambiguous')
            if failure=='identity':raise PolicyError('Identity mismatch')
            raise nodes_module.NodeHTTPError(302)
        monkeypatch.setattr(nodes_module,'node_https_request',fail)
        result=resolve(owner,prepared,'cancel')
        assert len(calls)==1 and result['phase']=='cancelling' and result['last_error']
        assert journal(reg)['resolution_token_enc']


def test_disposal_can_resolve_pending_before_first_agent_mutation(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;controller=NodeReplacement(reg);pending=controller.begin(NODE,code())
    assert pending['phase']=='pending' and not pending['target_installation_id']
    with candidate(tmp_path/'target',background=True) as (eng,_,client,token):
        http_transport(reg,client,monkeypatch)
        result=resolve(owner,pending,'cancel')
        assert result['candidate_discarded'] and not eng.running and token.token!=TOKEN
        assert reg.get(NODE,secret=True)['token']==TOKEN


def test_cancellation_intent_cannot_be_reversed_into_commit(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner)
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')))
            assert resolve(owner,prepared,'cancel')['phase']=='cancelling'
        response=owner.post(f"/api/nodes/{NODE}/replacement/{prepared['attempt_id']}/commit",json=commit_body(prepared))
        assert response.status_code==400,response.text
        assert journal(reg)['resolution']=='cancelling'


def test_internal_replacement_cannot_steal_reserved_target(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,token):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner)
        health=client.get('/node/api/health',headers={'Authorization':'Bearer '+token.token}).json()
        with pytest.raises(PolicyError,match='unresolved preparation'):
            reg.installations.replace_verified(NODE,expected_binding_id=prepared['source_binding_id'],
                health=health,origin=TARGET_ORIGIN,token=token.token,data_address='new-turkey.example.test')
        assert len(reg.installations.history(NODE))==1


@pytest.mark.parametrize('action',['commit','cancel'])
def test_resolution_rejects_other_node_and_unsupported_browser_descriptor(hub,tmp_path,monkeypatch,action):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner);before=journal(reg)
        body=commit_body(prepared) if action=='commit' else {'discardCandidate':True}
        assert owner.post(f"/api/nodes/other/replacement/{prepared['attempt_id']}/{action}",json=body).status_code==400
        body['health']={'service':'DARK XRAY NODE','node_id':'attacker'}
        assert owner.post(f"/api/nodes/{NODE}/replacement/{prepared['attempt_id']}/{action}",json=body).status_code==422
        assert journal(reg)==before


@pytest.mark.parametrize('path',['/node/api/v1/control','/node/api/core/stop','/node/api/v1/state/apply'])
def test_buffered_old_credential_cannot_mutate_after_revocation(tmp_path,monkeypatch,path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    with candidate(tmp_path/'target') as (engine,runtime,client,token):
        # The request passes middleware and auth, then waits for the engine lock.
        authenticated=threading.Event();calls=[];require=token.require
        def tracked(request):
            require(request);calls.append(True)
            if len(calls)==2:authenticated.set()
        monkeypatch.setattr(token,'require',tracked)
        if path.endswith('/control'):
            body={'nodeId':'new-turkey','revision':1,'commandId':uuid.uuid4().hex,'action':'stop'}
        elif path.endswith('/apply'):
            from test_node_hub_recovery import envelope
            data=payload(engine);data['nodeId']='new-turkey';body=envelope(data)
        else:body={}
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine.lock:
                future=pool.submit(client.post,path,json=body)
                assert authenticated.wait(3),'Request never reached the post-auth boundary'
                token.rotate('dkn_'+'Z'*60)
            response=future.result(timeout=8)
        assert response.status_code==401,response.text
        assert not runtime.command_status()['persisted'] and runtime.status()['appliedRevision']==0
        assert not runtime.control_status()['manual_stop']


@pytest.mark.parametrize('action',['commit','cancel'])
def test_another_registry_finishes_between_receipt_and_journal_read(hub,tmp_path,monkeypatch,action):
    reg,owner,_=hub
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);prepared=prepare(owner)
        controller=NodeReplacement(reg)
        other=NodeReplacement(nodes_module.NodeRegistry(reg.store,reg.cipher))
        params={'source_binding_id':prepared['source_binding_id'],
                'accept_unconfirmed_old_server':True,'accept_unreported_traffic':True} if action=='commit' else {'discard_candidate':True}
        original=controller._terminal;once=[]
        def finish_first(*a):
            prior=original(*a)
            if not once:
                once.append(True);getattr(other,action)(NODE,prepared['attempt_id'],**params)
            return prior  # Stale read immediately before the first journal lookup.
        monkeypatch.setattr(controller,'_terminal',finish_first)
        result=getattr(controller,action)(NODE,prepared['attempt_id'],**params)
        assert result['phase']==('committed' if action=='commit' else 'cancelled')
        assert len(sql_rows(reg,'remote_node_replacement_history'))==1

"""First enrollment disposal: real SQLite/FastAPI/files, fixture Agent transport and Xray."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from auth import Auth
from backup import create_backup, restore_backup
from dark_policy import PolicyError, Store
from nodes import NodeRegistry, NodeHTTPError
import nodes as nodes_module
from node_pairing import NodePairing
from test_hub_node_control_live import hub
from test_node_installations import http_transport, seed_account, sql_rows
from test_node_replacement_prepare import candidate, code, TARGET_ORIGIN
from test_node_pairing import NEW, PATH, PAIR, pending, pair
from test_node_hub_recovery import TOKEN, NODE, payload

CONSENT = {'confirmCancel': True, 'acknowledgeCredentialReset': True}
TABLES = ('remote_nodes','remote_node_installations','remote_node_inbounds','remote_node_client_usage',
          'clients','managed_clients','core_clients','core_inbounds','traffic_ledger','owners')


def cancel(owner, saved):
    response = owner.post(PATH+'/'+saved['attempt_id']+'/cancel', json=CONSENT)
    assert response.status_code == 200, response.text
    return response.json()


def interrupted(reg, owner, client, monkeypatch):
    def lost(response):
        if response.request.url.path.endswith('/replacement/rotate-token'):
            raise OSError('lost response '+TOKEN)
    http_transport(reg, client, monkeypatch, response_hook=lost)
    saved = pair(owner)
    assert not saved['paired'] and pending(reg)['phase'] == 'rotating'
    return saved


def assert_cancelled(doc, discarded):
    assert doc['phase'] == 'cancelled' and doc['cancelled'] is True and doc['paired'] is False
    assert doc['pair_code_consumed'] is discarded
    assert doc['candidate_requires_reinstall'] is discarded
    assert doc['credential_revocation_confirmed'] is discarded
    assert doc['remote_idle_confirmed'] is discarded
    assert doc['reservation_released'] is True and doc['credentials_retained_in_journal'] is False
    assert doc['status_scope'] == 'saved_pairing_cancellation_receipt'
    assert doc['live_state_verified'] is False and doc['registration_current'] is False


def test_never_dispatched_can_withdraw_offline_without_decryption_or_remote_changes(hub, monkeypatch):
    reg, owner, engine = hub; seed_account(reg, owner, engine)
    before = {t: sql_rows(reg,t) for t in TABLES}
    saved = owner.app.state.pairing.begin(code())
    monkeypatch.setattr(reg.cipher,'decrypt',lambda *a: pytest.fail('local cancellation decrypted a token'))
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k: pytest.fail('local cancellation contacted Agent'))
    result = cancel(owner,saved); assert_cancelled(result,False)
    assert pending(reg) is None and before == {t:sql_rows(reg,t) for t in TABLES}
    assert_cancelled(cancel(owner,saved),False)
    # Retrying an old cancelled ID is read-only, not a fresh enrollment.
    assert_cancelled(owner.app.state.pairing.retry(saved['attempt_id']),False)
    assert owner.get(PATH).json()['items'] == []


def test_withdrawn_code_can_be_reused_but_gets_a_new_attempt(hub,tmp_path,monkeypatch):
    reg,owner,_ = hub; old = owner.app.state.pairing.begin(code());cancel(owner,old)
    with candidate(tmp_path/'agent') as (_,_,client,token):
        seen=http_transport(reg,client,monkeypatch);new=pair(owner)
        assert new['paired'] and new['attempt_id']!=old['attempt_id']
        current=reg.get(NEW,secret=True);seen.clear()
        assert_cancelled(cancel(owner,old),False)
        assert reg.get(NEW,secret=True)==current and seen==[]


def test_after_rotation_disposal_revokes_both_tokens_and_preserves_registered_nodes(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    before={t:sql_rows(reg,t) for t in TABLES}
    with candidate(tmp_path/'agent',background=True) as (target,runtime,client,token):
        saved=interrupted(reg,owner,client,monkeypatch);old_candidate=token.token
        seen=http_transport(reg,client,monkeypatch);result=cancel(owner,saved)
        assert_cancelled(result,True);assert pending(reg) is None
        assert not target.running and runtime.control_status()['manual_stop']
        assert token.token not in (TOKEN,old_candidate)
        for secret in (TOKEN,old_candidate):
            assert client.get('/node/api/health',headers={'Authorization':'Bearer '+secret}).status_code==401
        assert [(m,p) for m,p,_ in seen if m=='POST']==[
            ('POST','/node/api/v1/replacement/rotate-token'),('POST','/node/api/v1/replacement/idle')]
        assert before=={t:sql_rows(reg,t) for t in TABLES}
        assert not runtime.command_status()['persisted'] and not runtime.status()['appliedRevision']
        for secret in (TOKEN,old_candidate,token.token):assert secret not in json.dumps(result)
        seen.clear();assert_cancelled(cancel(owner,saved),True);assert seen==[]


@pytest.mark.parametrize('failure',['lost-rotation','lost-idle','new-health-timeout','final-db'])
def test_cancel_ambiguity_retains_secrets_reservation_and_retries_same_disposal(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'agent',background=True) as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch);before=pending(reg)
        seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request
        def fail(origin,credential,path,*a,**k):
            doc,ms=real(origin,credential,path,*a,**k)
            disposal=reg.cipher.decrypt(pending(reg)['disposal_enc'].encode()).decode()
            if ((failure=='lost-rotation' and path.endswith('/replacement/rotate-token'))
                or (failure=='lost-idle' and path.endswith('/replacement/idle'))
                or (failure=='new-health-timeout' and path.endswith('/health') and credential==disposal)):
                raise OSError('response lost with '+credential)
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',fail)
        if failure=='final-db':
            reg.store.db.execute("CREATE TRIGGER fail_cancel BEFORE INSERT ON remote_node_pair_cancellations BEGIN SELECT RAISE(ABORT,'injected'); END")
        result=cancel(owner,saved);row=pending(reg)
        assert result['phase']=='cancelling' and not result['cancelled']
        assert row['candidate_enc']==before['candidate_enc'] and row['bootstrap_enc']==before['bootstrap_enc']
        disposal=reg.cipher.decrypt(row['disposal_enc'].encode()).decode();assert disposal==token.token
        assert disposal not in json.dumps(result) and TOKEN not in json.dumps(result)
        with pytest.raises(sqlite3.IntegrityError):reg.put(NEW,'Must stay reserved',TARGET_ORIGIN,TOKEN)
        assert owner.post(PATH+'/'+saved['attempt_id']+'/retry',json={'confirmRetry':True}).status_code==400
        with pytest.raises(PolicyError):owner.app.state.pairing._finish(row)
        if failure=='final-db':reg.store.db.execute('DROP TRIGGER fail_cancel')
        monkeypatch.setattr(nodes_module,'node_https_request',real)
        seen=http_transport(reg,client,monkeypatch)
        rebuilt=NodePairing(NodeRegistry(reg.store,reg.cipher))
        result=rebuilt.cancel(saved['attempt_id'],confirm_cancel=True,acknowledge_credential_reset=True)
        assert_cancelled(result,True);assert token.token==disposal
        assert not any(p.endswith('/replacement/rotate-token') for _,p,_ in seen)
        if failure in ('lost-idle','final-db'):assert all(m=='GET' for m,_,_ in seen)


@pytest.mark.parametrize('failure',['tls','timeout','identity','503'])
def test_cancel_never_falls_back_on_non_401_failure(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch);new=token.token;calls=[]
        def fail(*a,**k):
            calls.append(a)
            if failure=='503':raise NodeHTTPError(503)
            raise PolicyError(failure+' '+TOKEN)
        monkeypatch.setattr(nodes_module,'node_https_request',fail)
        doc=cancel(owner,saved)
        assert doc['phase']=='cancelling' and len(calls)==1 and token.token==new
        assert pending(reg)['disposal_enc'] and doc['last_error']=='pairing_cancellation_contact_or_verification_failed'


def test_ambiguous_before_dispatch_falls_back_only_after_verified_401s(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request
        def block(*a,**k):
            if a[2].endswith('/replacement/rotate-token'):raise OSError('before send')
            return real(*a,**k)
        monkeypatch.setattr(nodes_module,'node_https_request',block);saved=pair(owner)
        assert pending(reg)['phase']=='rotating' and token.token==TOKEN
        monkeypatch.setattr(nodes_module,'node_https_request',real)
        seen=http_transport(reg,client,monkeypatch);assert_cancelled(cancel(owner,saved),True)
        assert token.token!=TOKEN and len([1 for m,_,_ in seen if m=='POST'])==2


@pytest.mark.parametrize('dirty',['assigned','manual-inbound','installation','no-idle-proof','false-rotation','false-idle'])
def test_untrusted_or_used_target_is_not_reported_discarded(hub,tmp_path,monkeypatch,dirty):
    reg,owner,_=hub
    with candidate(tmp_path/'agent',background=True) as (target,runtime,client,token):
        saved=interrupted(reg,owner,client,monkeypatch);old=token.token
        seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request
        if dirty=='false-rotation':monkeypatch.setattr(token,'rotate',lambda *a,**k:None)
        if dirty=='false-idle':monkeypatch.setattr(runtime,'command',lambda *a,**k: {})
        def transport(*a,**k):
            if dirty=='manual-inbound' and a[2].endswith('/replacement/rotate-token'):
                target.save_inbound(payload(target)['assignments'][0]['inbound'])
            doc,ms=real(*a,**k)
            if a[2].endswith('/health'):
                if dirty=='assigned':doc['managed_clients']=1
                if dirty=='installation':doc['installation_id']='f'*32
                if dirty=='no-idle-proof':doc['run_control'].pop('manual_stop',None)
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        result=cancel(owner,saved)
        assert result['phase']=='cancelling' and not result['cancelled'] and pending(reg)
        if dirty in ('assigned','manual-inbound','installation','false-rotation'):assert token.token==old
        if dirty in ('assigned','installation'):assert all(m=='GET' for m,_,_ in seen)


@pytest.mark.parametrize('mode',['anonymous','csrf','readonly','reseller','api-key'])
def test_cancel_requires_owner_writes_and_csrf(hub,monkeypatch,mode):
    reg,owner,engine=hub;saved=owner.app.state.pairing.begin(code());before=pending(reg)
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('unauthorized'))
    headers={}
    if mode=='anonymous':headers['Cookie']=''
    elif mode=='csrf':headers['X-Dark-CSRF']='wrong'
    elif mode=='readonly':engine.config.writes_enabled=False
    elif mode=='reseller':
        with reg.store.transaction() as db:db.execute("UPDATE api_admins SET role='reseller' WHERE id='dark'")
    elif mode=='api-key':
        key=owner.post('/api/keys',json={'name':'canceltest','permissions':{'clients.read':'all'},'days':1})
        assert key.status_code==200,key.text
        headers['Authorization']='Bearer '+key.json()['key'];headers['Cookie']=''
    response=owner.post(PATH+'/'+saved['attempt_id']+'/cancel',json=CONSENT,headers=headers)
    assert response.status_code in (401,403,409),response.text
    assert pending(reg)==before


@pytest.mark.parametrize('body',[{}, {'confirmCancel':True}, {'confirmCancel':False,'acknowledgeCredentialReset':True},
    {'confirmCancel':True,'acknowledgeCredentialReset':False},{'confirmCancel':'true','acknowledgeCredentialReset':True},
    {'confirmCancel':1,'acknowledgeCredentialReset':True},{**CONSENT,'token':TOKEN}])
def test_cancel_needs_two_strict_confirmations(hub,body):
    reg,owner,_=hub;saved=owner.app.state.pairing.begin(code());before=pending(reg)
    response=owner.post(PATH+'/'+saved['attempt_id']+'/cancel',json=body)
    assert response.status_code in (400,422) and pending(reg)==before


def test_registered_node_can_never_be_cancelled_as_pending_pairing(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,_):
        seen=http_transport(reg,client,monkeypatch);saved=pair(owner);before={t:sql_rows(reg,t) for t in TABLES};seen.clear()
        response=owner.post(PATH+'/'+saved['attempt_id']+'/cancel',json=CONSENT)
        assert response.status_code==400 and seen==[] and before=={t:sql_rows(reg,t) for t in TABLES}


def test_local_receipt_failure_rolls_back_deletion_and_reservation(hub):
    reg,owner,_=hub;saved=owner.app.state.pairing.begin(code());before=pending(reg)
    reg.store.db.execute("CREATE TRIGGER fail_cancel BEFORE INSERT ON remote_node_pair_cancellations BEGIN SELECT RAISE(ABORT,'injected'); END")
    response=owner.post(PATH+'/'+saved['attempt_id']+'/cancel',json=CONSENT)
    assert response.status_code>=400 and pending(reg)==before


def test_cancellation_saved_reads_never_decrypt_contact_or_mutate(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,_):
        saved=interrupted(reg,owner,client,monkeypatch)
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:(_ for _ in ()).throw(OSError('offline')))
        doc=cancel(owner,saved);assert doc['phase']=='cancelling'
        monkeypatch.setattr(reg.cipher,'decrypt',lambda *a:pytest.fail('read decrypted'))
        queries=[];reg.store.db.set_trace_callback(queries.append)
        try:
            assert owner.get(PATH).json()['items'][0]['phase']=='cancelling'
            assert owner.get(PATH+'/'+saved['attempt_id']).json()['cancelled'] is False
        finally:reg.store.db.set_trace_callback(None)
        assert not any(q.split()[0].upper() in ('BEGIN','UPDATE','INSERT','DELETE','REPLACE') for q in queries)


def test_cancel_survives_hub_backup_and_agent_restart(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;root=tmp_path/'agent'
    with candidate(root) as (_,runtime,client,token):
        saved=interrupted(reg,owner,client,monkeypatch)
        # The same loss hook also loses the disposal rotation reply.
        doc=cancel(owner,saved);assert doc['phase']=='cancelling';secret=token.token;identity=runtime.installation_id
        hubroot=tmp_path/'hub';config=hubroot/'config.json'
        config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
        archive=tmp_path/'cancel.darkbackup';create_backup(hubroot,config,archive,'Cancel-Backup-Passphrase!123')
    restored=tmp_path/'restored';restore_backup(archive,restored,'Cancel-Backup-Passphrase!123')
    store=Store(restored/'data/dark.sqlite3')
    try:
        auth=Auth(store,restored/'data/secret.key');rebuilt=NodePairing(NodeRegistry(store,auth.cipher))
        with candidate(root) as (_,runtime,client,token):
            seen=http_transport(rebuilt.registry,client,monkeypatch)
            assert token.token==secret and runtime.installation_id==identity
            result=rebuilt.cancel(saved['attempt_id'],confirm_cancel=True,acknowledge_credential_reset=True)
            assert_cancelled(result,True)
            assert not any(p.endswith('/replacement/rotate-token') for _,p,_ in seen)
    finally:store.close()


def test_older_held_health_response_cannot_rotate_after_local_withdrawal(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;pairing=owner.app.state.pairing;saved=pairing.begin(code())
    with candidate(tmp_path/'agent') as (_,_,client,token):
        seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request;used=[]
        def transport(*a,**k):
            doc,ms=real(*a,**k)
            if not used:
                used.append(True)
                other=NodePairing(NodeRegistry(reg.store,reg.cipher))
                assert_cancelled(other.cancel(saved['attempt_id'],confirm_cancel=True,acknowledge_credential_reset=True),False)
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        assert_cancelled(pairing.retry(saved['attempt_id']),False)
        assert token.token==TOKEN and all(m=='GET' for m,_,_ in seen)


def test_two_registries_cancel_once_while_other_nodes_and_disable_can_progress(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent',background=True) as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch)
        seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request
        entered=threading.Event();release=threading.Event();second_started=threading.Event()
        def hold(*a,**k):
            result=real(*a,**k)
            if a[2].endswith('/replacement/rotate-token'):
                entered.set();assert release.wait(6)
            return result
        monkeypatch.setattr(nodes_module,'node_https_request',hold)
        a=owner.app.state.pairing;b=NodePairing(NodeRegistry(reg.store,reg.cipher))
        def second():
            second_started.set();return b.cancel(saved['attempt_id'],confirm_cancel=True,acknowledge_credential_reset=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(a.cancel,saved['attempt_id'],confirm_cancel=True,acknowledge_credential_reset=True)
            try:
                assert entered.wait(6);next_=pool.submit(second);assert second_started.wait(2)
                assert not next_.done()
                # Store lock is not held across network I/O or while waiting.
                other=a.begin(code(nodeId='other',origin='https://other.example.test:9443'))
                assert_cancelled(a.cancel(other['attempt_id'],confirm_cancel=True,acknowledge_credential_reset=True),False)
                reg.put(NODE,'Disabled',reg.get(NODE)['origin'],reg.get(NODE,secret=True)['token'],False,[])
            finally:release.set()
            assert_cancelled(first.result(8),True);assert_cancelled(next_.result(8),True)
        assert len([p for m,p,_ in seen if p.endswith('/replacement/rotate-token')])==1
        assert len(sql_rows(reg,'remote_node_pair_cancellations'))==2

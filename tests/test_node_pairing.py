"""Ordinary pairing uses real Hub/Agent APIs, SQLite and files; not WAN evidence."""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from auth import Auth
from backup import create_backup, restore_backup
from dark_policy import PolicyError, Store
from nodes import NodeRegistry
import nodes as nodes_module
from node_pairing import NodePairing
from test_hub_node_control_live import hub
from test_node_installations import http_transport, sql_rows, seed_account
from test_node_replacement_prepare import candidate, code, TARGET_ORIGIN
from test_node_hub_recovery import TOKEN, NODE, payload, post_state

NEW = 'new-turkey'
PAIR = '/api/nodes/pair'
PATH = '/api/nodes/pairings'


def pair(owner, value=None):
    response = owner.post(PAIR, json={'code':value or code()})
    assert response.status_code == 200, response.text
    return response.json()


def resume(owner, saved):
    response = owner.post(PATH+'/'+saved['attempt_id']+'/retry', json={'confirmRetry':True})
    assert response.status_code == 200, response.text
    return response.json()


def pending(reg):
    with reg.store.lock:
        row = reg.store.db.execute('SELECT * FROM remote_node_pairings').fetchone()
    return dict(row) if row else None


def test_ordinary_pair_verifies_new_token_before_atomic_registration(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    tables=('clients','managed_clients','core_clients','traffic_ledger','owners','core_inbounds')
    before={t:sql_rows(reg,t) for t in tables}
    with candidate(tmp_path/'agent',background=True) as (target,runtime,client,token):
        seen=http_transport(reg,client,monkeypatch)
        pid=target.process.pid
        result=pair(owner)
        assert result['paired'] is True and result['registration_current'] is True
        assert result['pair_code_consumed'] is True and pending(reg) is None
        assert reg.get(NEW,secret=True)['token']==token.token!=TOKEN
        assert reg.installations.capture(NEW)['installation_id']==runtime.installation_id
        assert target.process.pid==pid and not runtime.status()['appliedRevision']
        assert not runtime.command_status()['persisted']
        assert [(m,p) for m,p,_ in seen if m=='POST']==[('POST','/node/api/v1/replacement/rotate-token')]
        assert before=={t:sql_rows(reg,t) for t in tables}
        assert TOKEN not in json.dumps(result) and token.token not in json.dumps(result)
        assert reg.get(NEW)['inboundIds']==[]
        assert owner.post(PAIR,json={'code':code()}).status_code==409
        reg.put(NEW,'Disabled',TARGET_ORIGIN,token.token,False,[])
        seen.clear();again=resume(owner,result)
        assert again['paired'] and not again['node']['enabled'] and seen==[]


def test_offline_is_saved_and_reserves_target_without_registered_node(hub,monkeypatch):
    reg,owner,_=hub
    monkeypatch.setattr(nodes_module,'resolve_origin',lambda *a: (_ for _ in ()).throw(OSError('Offline')))
    result=pair(owner);row=pending(reg)
    assert result['paired'] is False and result['pair_code_consumed'] is None
    assert result['last_error']=='pairing_contact_or_verification_failed'
    assert row['installation_id']=='' and TOKEN not in row['bootstrap_enc']
    assert [n['id'] for n in reg.list()]==[NODE]
    again=pair(owner)
    assert again['attempt_id']==result['attempt_id']
    assert pending(reg)['candidate_enc']==row['candidate_enc']
    with pytest.raises(sqlite3.IntegrityError):reg.put(NEW,'New',TARGET_ORIGIN,TOKEN)
    with pytest.raises(sqlite3.IntegrityError):reg.put('alias','New',TARGET_ORIGIN,TOKEN)
    response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code()})
    assert response.status_code==409
    assert owner.get(PATH).json()['items'][0]['attempt_id']==result['attempt_id']


@pytest.mark.parametrize('failure',['lost-rotation','positive-reply-only','new-health-timeout','final-db'])
def test_ambiguous_outcome_keeps_same_encrypted_credential_for_retry(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,runtime,client,token):
        def hook(response):
            if str(response.request.url).endswith('/replacement/rotate-token') and failure=='lost-rotation':
                raise PolicyError('Injected lost rotation reply with '+TOKEN)
            if response.status_code==200 and str(response.request.url).endswith('/health') and token.token!=TOKEN and failure=='new-health-timeout':
                raise PolicyError('Injected lost new-credential health')
        seen=http_transport(reg,client,monkeypatch,response_hook=hook)
        if failure=='positive-reply-only':
            rotate=token.rotate
            monkeypatch.setattr(token,'rotate',lambda *a,**k:None)
        if failure=='final-db':
            reg.store.db.execute("CREATE TRIGGER fail_pair_receipt BEFORE INSERT ON remote_node_pair_history BEGIN SELECT RAISE(ABORT,'injected'); END")
        result=pair(owner);row=pending(reg)
        assert not result['paired'] and reg.store.db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(NEW,)).fetchone() is None
        assert row and row['installation_id']==runtime.installation_id
        credential=reg.cipher.decrypt(row['candidate_enc'].encode()).decode()
        assert credential not in json.dumps(result) and TOKEN not in json.dumps(result)
        if failure!='positive-reply-only':assert token.token==credential
        if failure=='positive-reply-only':monkeypatch.setattr(token,'rotate',rotate)
        if failure=='final-db':reg.store.db.execute('DROP TRIGGER fail_pair_receipt')
        seen=http_transport(reg,client,monkeypatch)
        # Reconstruction on the same persisted Store must not generate a token.
        reconstructed=NodePairing(NodeRegistry(reg.store,reg.cipher))
        final=reconstructed.retry(result['attempt_id'])
        assert final['paired'] and pending(reg) is None
        assert reg.get(NEW,secret=True)['token']==credential==token.token
        if failure!='positive-reply-only':assert all(method=='GET' for method,_,_ in seen)


def test_no_old_token_fallback_on_new_credential_transport_failure(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        http_transport(reg,client,monkeypatch)
        request=nodes_module.node_https_request;calls=[]
        def transport(origin,credential,*args,**kwargs):
            calls.append(credential)
            if credential!=TOKEN:raise PolicyError('TLS/identity failure')
            return request(origin,credential,*args,**kwargs)
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        result=pair(owner)
        assert not result['paired'] and len(calls)==2 and token.token==TOKEN


@pytest.mark.parametrize('change',['different-token','different-address','different-name','different-agent'])
def test_repeated_code_must_match_saved_operation(hub,monkeypatch,change):
    reg,owner,_=hub
    monkeypatch.setattr(nodes_module,'resolve_origin',lambda *a: (_ for _ in ()).throw(OSError('Offline')))
    pair(owner);before=pending(reg)
    variants={'different-token':{'token':'dkn_'+'Z'*60},'different-address':{'dataAddress':'other.example.test'},
              'different-name':{'name':'Other'},'different-agent':{'nodeId':'another'}}
    response=owner.post(PAIR,json={'code':code(**variants[change])})
    assert response.status_code==400 and pending(reg)==before


@pytest.mark.parametrize('change',['schema-bool','duplicate-json','invalid-base64','token-newline','unknown-field'])
def test_strict_pair_parser_no_write_or_contact(hub,monkeypatch,change):
    import base64
    reg,owner,_=hub
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Invalid code contacted Agent'))
    variants={'schema-bool':{'schema':True},'unknown-field':{'extra':'bad'},'token-newline':{'token':TOKEN+'\n'}}
    value=code(**variants.get(change,{}))
    if change=='invalid-base64':value='DXN1.'+'!'*40
    if change=='duplicate-json':
        raw=base64.urlsafe_b64decode(value[5:]+'='*(-len(value[5:])%4)).decode()
        value='DXN1.'+base64.urlsafe_b64encode((raw[:-1]+',"schema":1}').encode()).decode().rstrip('=')
    response=owner.post(PAIR,json={'code':value})
    assert response.status_code==400 and pending(reg) is None


@pytest.mark.parametrize('change',['nonfresh','legacy','wrong-agent','different-installation'])
def test_unsafe_target_never_rotated_or_published(hub,tmp_path,monkeypatch,change):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (engine,runtime,client,token):
        if change=='nonfresh':
            body=payload(engine);body['nodeId']=NEW;post_state(client,body)
        seen=http_transport(reg,client,monkeypatch)
        request=nodes_module.node_https_request
        def bad_health(*args,**kwargs):
            doc,ms=request(*args,**kwargs)
            if change=='legacy':doc['capabilities'].pop('replacement_prepare',None)
            if change=='wrong-agent':doc['node_id']='wrong'
            return doc,ms
        if change=='different-installation':
            state=owner.app.state.pairing.begin(code());row=pending(reg)
            reg.store.db.execute('UPDATE remote_node_pairings SET installation_id=?',('f'*32,))
            result=resume(owner,state)
        else:
            monkeypatch.setattr(nodes_module,'node_https_request',bad_health)
            result=pair(owner)
        assert not result['paired'] and token.token==TOKEN
        assert all(m=='GET' for m,_,_ in seen)


def test_guard_rechecks_freshness_at_rotation_after_successful_probe(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (engine,_,client,token):
        http_transport(reg,client,monkeypatch)
        request=nodes_module.node_https_request
        def changed(origin,credential,path,*args,**kwargs):
            if path.endswith('/replacement/rotate-token'):
                engine.save_inbound(payload(engine)['assignments'][0]['inbound'])
            return request(origin,credential,path,*args,**kwargs)
        monkeypatch.setattr(nodes_module,'node_https_request',changed)
        result=pair(owner)
        assert not result['paired'] and token.token==TOKEN


def test_read_status_has_no_writes_decryption_or_network(hub,monkeypatch):
    reg,owner,_=hub
    saved=owner.app.state.pairing.begin(code())
    queries=[];reg.store.db.set_trace_callback(queries.append)
    try:
        monkeypatch.setattr(reg.cipher,'decrypt',lambda *a:pytest.fail('GET decrypted credential'))
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('GET contacted Agent'))
        assert owner.get(PATH).json()['items'][0]['attempt_id']==saved['attempt_id']
        assert owner.get(PATH+'/'+saved['attempt_id']).json()['paired'] is False
    finally:reg.store.db.set_trace_callback(None)
    assert not any(q.strip().split()[0].upper() in ('INSERT','UPDATE','DELETE','BEGIN','REPLACE') for q in queries)


@pytest.mark.parametrize('mode',['anonymous','csrf','readonly','reseller','api-key'])
def test_owner_csrf_and_writes_boundaries(hub,monkeypatch,mode):
    reg,owner,engine=hub;saved=owner.app.state.pairing.begin(code())
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Unauthorized mutation'))
    headers={}
    if mode=='anonymous':headers['Cookie']=''
    elif mode=='csrf':headers['X-Dark-CSRF']='wrong'
    elif mode=='readonly':engine.config.writes_enabled=False
    elif mode=='reseller':
        with reg.store.transaction() as db:db.execute("UPDATE api_admins SET role='reseller' WHERE id='dark'")
    elif mode=='api-key':
        result=owner.post('/api/keys',json={'name':'pairtest','permissions':{'clients.read':'all'},'days':1})
        assert result.status_code==200,result.text
        headers['Authorization']='Bearer '+result.json()['key'];headers['Cookie']=''
    for path,body in [(PAIR,{'code':code()}),(PATH+'/'+saved['attempt_id']+'/retry',{'confirmRetry':True})]:
        response=owner.post(path,json=body,headers=headers)
        assert response.status_code in (401,403,409),response.text
    assert pending(reg)['operation_revision']==0


@pytest.mark.parametrize('body',[{}, {'confirmRetry':False},{'confirmRetry':'true'},{'confirmRetry':1},{'confirmRetry':True,'token':TOKEN}])
def test_retry_requires_strict_explicit_confirmation(hub,monkeypatch,body):
    _,owner,_=hub;saved=owner.app.state.pairing.begin(code())
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Invalid retry contacted Agent'))
    response=owner.post(PATH+'/'+saved['attempt_id']+'/retry',json=body)
    assert response.status_code in (400,422)


def test_concurrent_retries_register_once_and_rotate_at_most_once(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;saved=owner.app.state.pairing.begin(code())
    with candidate(tmp_path/'agent') as (_,_,client,_):
        seen=http_transport(reg,client,monkeypatch)
        with ThreadPoolExecutor(max_workers=3) as pool:
            result=list(pool.map(owner.app.state.pairing.retry,[saved['attempt_id']]*3))
        assert all(x['paired'] for x in result)
        assert reg.store.db.execute('SELECT COUNT(*) FROM remote_node_pair_history').fetchone()[0]==1
        assert len([1 for m,_,_ in seen if m=='POST'])==1


def test_pending_reconstructed_from_encrypted_backup_after_rotation(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        def lost(response):
            if str(response.request.url).endswith('/replacement/rotate-token'):raise PolicyError('Lost reply')
        http_transport(reg,client,monkeypatch,response_hook=lost)
        saved=pair(owner);row=pending(reg);candidate_token=token.token
        root=tmp_path/'hub';config=root/'config.json';config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
        archive=tmp_path/'pair.darkbackup'
        create_backup(root,config,archive,'Pair-Backup-Passphrase!123')
        restored=tmp_path/'restored';restore_backup(archive,restored,'Pair-Backup-Passphrase!123')
        store=Store(restored/'data/dark.sqlite3')
        try:
            auth=Auth(store,restored/'data/secret.key');registry=NodeRegistry(store,auth.cipher)
            handoff=NodePairing(registry);seen=http_transport(registry,client,monkeypatch)
            assert pending(registry)['candidate_enc']==row['candidate_enc']
            result=handoff.retry(saved['attempt_id'])
            assert result['paired'] and registry.get(NEW,secret=True)['token']==candidate_token
            assert all(m=='GET' for m,_,_ in seen)
        finally:store.close()


@pytest.mark.parametrize('direction',['replacement-first','pair-first-alias','known-installation'])
def test_installation_and_endpoint_reservations_are_bidirectional(hub,tmp_path,monkeypatch,direction):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,runtime,client,token):
        http_transport(reg,client,monkeypatch)
        if direction=='replacement-first':
            saved=owner.app.state.replacements.begin(NODE,code())
            response=owner.post(PAIR,json={'code':code()})
            assert response.status_code==400 and pending(reg) is None
        elif direction=='known-installation':
            reg.store.db.execute('UPDATE remote_node_installations SET installation_id=? WHERE node_id=?',
                                 (runtime.installation_id,NODE))
            result=pair(owner)
            assert not result['paired'] and result['last_error']=='pairing_installation_already_known'
        else:
            saved=owner.app.state.pairing.begin(code())
            reg.store.db.execute('UPDATE remote_node_pairings SET installation_id=?',(runtime.installation_id,))
            from node_replacement import NodeReplacement
            replacement=NodeReplacement(NodeRegistry(reg.store,reg.cipher))
            value=replacement.begin(NODE,code(origin='https://alias.example.test:9443'))
            # The shared HTTPS fixture follows the actual Agent, including Host validation.
            reg.store.db.execute('UPDATE remote_node_replacements SET target_origin=?',('https://alias.example.test:9443',))
            with pytest.raises(sqlite3.IntegrityError):
                reg.store.db.execute('UPDATE remote_node_replacements SET target_installation_id=?',(runtime.installation_id,))
            with pytest.raises(sqlite3.IntegrityError):
                reg.store.db.execute('UPDATE remote_node_installations SET installation_id=? WHERE node_id=?',
                                    (runtime.installation_id,NODE))
        assert token.token==TOKEN


def test_newer_retry_fences_older_completion_across_registry_instances(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;pairing=owner.app.state.pairing;saved=pairing.begin(code())
    with candidate(tmp_path/'agent') as (_,_,client,token):
        http_transport(reg,client,monkeypatch)
        request=nodes_module.node_https_request;other=NodePairing(NodeRegistry(reg.store,reg.cipher));intercept=[False]
        def transport(*args,**kwargs):
            doc,ms=request(*args,**kwargs)
            if not intercept[0] and args[1]!=TOKEN and isinstance(doc,dict) and doc.get('agent_only'):
                intercept[0]=True
                assert other.retry(saved['attempt_id'])['paired']
            return doc,ms
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        result=pairing.retry(saved['attempt_id'])
        assert result['paired'] and intercept[0]
        assert len(sql_rows(reg,'remote_node_pair_history'))==1
        assert reg.get(NEW,secret=True)['token']==token.token


def test_completed_saved_status_is_read_only_even_after_node_deleted(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);saved=pair(owner)
        reg.delete(NEW)
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('History contacted target'))
        queries=[];reg.store.db.set_trace_callback(queries.append)
        try:result=owner.get(PATH+'/'+saved['attempt_id']).json()
        finally:reg.store.db.set_trace_callback(None)
        assert result['paired'] and not result['registration_current'] and result['node'] is None
        assert not any(q.strip().split()[0].upper() in ('INSERT','UPDATE','DELETE','BEGIN') for q in queries)
        assert resume(owner,saved)['node'] is None


def test_agent_recreation_after_lost_rotation_uses_journaled_new_token(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;root=tmp_path/'agent'
    with candidate(root) as (_,runtime,client,token):
        def lost(response):
            if str(response.request.url).endswith('/replacement/rotate-token'):raise PolicyError('Lost reply')
        http_transport(reg,client,monkeypatch,response_hook=lost)
        saved=pair(owner);identity=runtime.installation_id;new=token.token
    with candidate(root) as (_,runtime,client,token):
        seen=http_transport(reg,client,monkeypatch)
        assert runtime.installation_id==identity and token.token==new
        assert resume(owner,saved)['paired']
        assert all(m=='GET' for m,_,_ in seen)

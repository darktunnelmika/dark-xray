"""Owner activation APIs/real SQLite/FastAPI. Fake Xray, no provider-WAN proof."""
import copy
import json
import uuid

import pytest
import nodes as node_module
from dark_policy import PolicyError
from test_node_replacement_prepare import hub, candidate, prepare, NODE
from test_node_replacement_resolution import resolve
from test_node_replacement_deployment import stage
from test_node_installations import seed_account, http_transport, sql_rows


def url(receipt, suffix=''):
    return f"/api/nodes/{NODE}/replacement/{receipt['attempt_id']}/activation"+suffix


def review(owner, receipt):
    response=owner.post(url(receipt,'/review'),json={'bindingId':receipt['committed_binding_id']})
    assert response.status_code==200,response.text
    result=response.json();assert result['review_ready'],result
    return result


def consent(reviewed):
    return {'bindingId':reviewed['binding_id'],'reviewHash':reviewed['review_hash'],
            'confirmStart':True,'acceptEndpointResponsibility':True,
            'acceptUnconfirmedOldServer':True,'acceptUnreportedTraffic':True}


def activate(owner,receipt,reviewed):
    response=owner.post(url(receipt,'/start'),json=consent(reviewed))
    assert response.status_code==200,response.text
    return response.json()


def pause(owner,receipt):
    response=owner.post(url(receipt,'/pause'),json={'bindingId':receipt['committed_binding_id'],'confirmStop':True})
    assert response.status_code==200,response.text
    return response.json()


def test_review_does_not_start_or_modify_addresses(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        seen=http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner))
        hosts=engine.section('hosts');address=reg.get(NODE)['data_address']
        result=review(owner,receipt)
        assert not eng.running and not reg.get(NODE)['enabled']
        assert result['endpoints']['hosts'][0]['address']=='turkey.example.test'
        assert result['endpoints']['direct_clients_may_connect_as_soon_as_start_is_sent']
        assert not result['network_verified'] and not result['old_stop_confirmed']
        assert not result['traffic_tail_complete'] and not result['service_activated']
        assert engine.section('hosts')==hosts and reg.get(NODE)['data_address']==address
        assert not any(p=='/node/api/v1/control/activate' for _,p,_ in seen)
        assert token.token not in json.dumps(result)


def test_activation_enables_after_exact_ack_and_preserves_master_accounts(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        tables=('clients','core_clients','core_inbounds','traffic_ledger')
        before={t:sql_rows(reg,t) for t in tables};hosts=engine.section('hosts')
        result=activate(owner,receipt,r)
        assert result['activation_completed'] and result['service_activated'],(result,reg.get(NODE),reg.commands.status(NODE))
        assert not result['activation_held'] and reg.get(NODE)['enabled'] and eng.running
        assert rt.command_status()['action']=='start' and not reg.commands.status(NODE)['pending']
        assert before=={t:sql_rows(reg,t) for t in tables} and engine.section('hosts')==hosts
        assert not result['network_verified'] and not result['dns_or_tunnel_changed']
        pid=eng.process.pid;command=rt.command_status()
        def no_request(*a,**k):pytest.fail('Completed activation contacted target again')
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',no_request)
            duplicate=activate(owner,receipt,r)
        assert duplicate['activation_completed'] and eng.process.pid==pid and rt.command_status()==command
        assert token.token not in json.dumps(result)
        reg.set_enabled(NODE,False)
        assert activate(owner,receipt,r)['service_activated'] is False
        assert not reg.get(NODE)['enabled'], 'Historical retry re-enabled an administratively disabled Node'


@pytest.mark.parametrize('lost_path',['/node/api/v1/control/activate','/node/api/mirrors/traffic','/node/api/health'])
def test_lost_reply_reuses_start_and_keeps_node_unpublished(hub,tmp_path,monkeypatch,lost_path):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request;sent=[]
        def lost(origin,cred,path,*a,**kw):
            result=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':sent.append(True)
            if sent and path==lost_path:raise OSError('Lost reply '+cred)
            return result
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',lost)
            failed=activate(owner,receipt,r)
        assert failed['phase']=='starting' and failed['target_may_be_running'] and failed['requires_retry'],failed
        assert not failed['service_activated'] and failed['activation_held'] and not reg.get(NODE)['enabled']
        assert eng.running;pid=eng.process.pid;command=rt.command_status()
        assert token.token not in json.dumps(failed)
        with monkeypatch.context() as patch:
            patch.setattr(rt,'_command_locked',lambda *a:pytest.fail('Duplicate Start reexecuted'))
            completed=activate(owner,receipt,r)
        assert completed['service_activated'],completed
        assert eng.process.pid==pid and rt.command_status()==command


@pytest.mark.parametrize('field,value', [('confirmStart',False),('acceptEndpointResponsibility',False),
    ('acceptUnconfirmedOldServer',False),('acceptUnreportedTraffic',False),('confirmStart','true'),('confirmStart',1)])
def test_confirmation_cannot_be_skipped_or_coerced(hub,tmp_path,monkeypatch,field,value):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        body=consent(r);body[field]=value
        response=owner.post(url(receipt,'/start'),json=body)
        assert response.status_code in (400,422),response.text
        assert not eng.running and rt.command_status()['action']=='stop' and not reg.get(NODE)['enabled']


@pytest.mark.parametrize('kind',['hosts','policy','data_address','binding'])
def test_changed_review_prevents_start(hub,tmp_path,monkeypatch,kind):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        with reg.store.transaction() as db:
            if kind=='binding':db.execute('UPDATE remote_node_installations SET installation_id=? WHERE node_id=? AND retired_at=0',(uuid.uuid4().hex,NODE))
            elif kind=='data_address':db.execute("UPDATE remote_nodes SET data_address='other.example.test' WHERE id=?",(NODE,))
            elif kind=='policy':db.execute("UPDATE clients SET limit_ip=3 WHERE id='alice'")
            else:
                hosts=engine.section('hosts');hosts[0]['address']='other.example.test'
                db.execute("UPDATE core_sections SET body=? WHERE name='hosts'",(json.dumps(hosts),))
        response=owner.post(url(receipt,'/start'),json=consent(r))
        assert response.status_code==400,response.text
        assert not eng.running and not reg.get(NODE)['enabled']


def test_expiry_refresh_requires_new_review_without_start(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        # Use the actual manager through API: expiry edits alter the reviewed model.
        body={'expiryTime':1000}
        response=owner.patch('/api/clients/alice',json={'client':body})
        assert response.status_code in (200,202),response.text
        response=owner.post(url(receipt,'/start'),json=consent(r))
        assert response.status_code==400,response.text
        assert not eng.running


def test_pause_after_lost_start_counts_bytes_and_allows_fresh_review(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request
        def lost(origin,cred,path,*a,**kw):
            result=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':raise OSError('Lost Start')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',lost)
            assert activate(owner,receipt,r)['requires_retry']
        assert eng.running
        mirror=rt.mirror_for_source('alice')
        with eng.store.transaction() as db:db.execute('UPDATE core_clients SET up=7,down=11 WHERE email=?',(mirror,))
        result=pause(owner,receipt)
        assert result['phase']=='paused' and not result['target_may_be_running'],result
        assert not eng.running and not reg.get(NODE)['enabled']
        assert reg.store.db.execute("SELECT used_bytes FROM clients WHERE id='alice'").fetchone()[0]==53
        new_review=review(owner,receipt)
        assert new_review['review_hash']!=r['review_hash']
        done=activate(owner,receipt,new_review)
        assert done['service_activated'],done
        assert reg.store.db.execute("SELECT used_bytes FROM clients WHERE id='alice'").fetchone()[0]==53


def test_status_is_read_only_and_hold_blocks_normal_paths_while_pending(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request
        def offline_start(origin,cred,path,*a,**kw):
            if path=='/node/api/v1/control/activate':raise OSError('offline')
            return original(origin,cred,path,*a,**kw)
        monkeypatch.setattr(node_module,'node_https_request',offline_start)
        pending=activate(owner,receipt,r);assert pending['phase']=='starting',pending
        with pytest.raises(PolicyError):reg.set_enabled(NODE,True)
        with pytest.raises(PolicyError):reg.commands.record(NODE,'start')
        with pytest.raises(PolicyError):reg.commands.record(NODE,'restart')
        response=owner.post(f"/api/nodes/{NODE}/replacement/{receipt['attempt_id']}/stage",json={'bindingId':r['binding_id']})
        assert response.status_code==400,response.text
        monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('GET contacted Agent'))
        before=sql_rows(reg,'remote_node_replacement_activations')
        assert owner.get(url(receipt)).status_code==200
        assert sql_rows(reg,'remote_node_replacement_activations')==before


@pytest.mark.parametrize('change',['hosts','stop'])
def test_change_after_start_is_compensated_by_stop_without_publishing(hub,tmp_path,monkeypatch,change):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request
        def edit(origin,cred,path,*a,**kw):
            result=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':
                if change=='stop':reg.commands.record(NODE,'stop')
                else:
                    hosts=engine.section('hosts');hosts[0]['address']='changed.example.test'
                    engine.save_section('hosts',hosts)
            return result
        monkeypatch.setattr(node_module,'node_https_request',edit)
        result=activate(owner,receipt,r)
        assert result['phase']=='paused',result
        assert not eng.running and not reg.get(NODE)['enabled'] and result['activation_held']
        assert rt.command_status()['action']=='stop' and not reg.commands.status(NODE)['pending']


@pytest.mark.parametrize('field,value',[('revision',True),('desiredRevision',True),('desiredHash','0'*64),
    ('validatedHash','0'*64),('nodeId','foreign'),('commandId','bad')])
def test_agent_conditional_start_rejects_invalid_or_changed_configuration(hub,tmp_path,monkeypatch,field,value):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        body={'nodeId':rt.scope,'revision':rt.command_status()['revision']+1,'commandId':uuid.uuid4().hex,
              'action':'start','desiredRevision':rt.status()['appliedRevision'],
              'desiredHash':rt.status()['appliedHash'],'validatedHash':eng.validate()['hash']}
        body[field]=value
        before=rt.command_status()
        response=client.post('/node/api/v1/control/activate',json=body,headers={'Authorization':'Bearer '+token.token})
        assert response.status_code in (409,422),response.text
        assert rt.command_status()==before and not eng.running


@pytest.mark.parametrize('failure',['ack','health','transaction'])
def test_bad_ack_health_or_publish_failure_does_not_release_hold(hub,tmp_path,monkeypatch,failure):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request;started=[]
        def invalid(origin,cred,path,*a,**kw):
            doc,ms=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':
                started.append(True)
                if failure=='ack':doc['revision']=True
            if failure=='health' and started and path=='/node/api/health':doc['core']['dirty']=True
            return doc,ms
        if failure=='transaction':
            reg.store.db.execute("CREATE TRIGGER fail_activation BEFORE UPDATE OF enabled ON remote_nodes WHEN NEW.enabled=1 BEGIN SELECT RAISE(ABORT,'injected'); END")
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',invalid)
            result=activate(owner,receipt,r)
        if failure=='transaction':reg.store.db.execute('DROP TRIGGER fail_activation')
        assert result['phase']=='starting' and not result['service_activated'],result
        assert eng.running and not reg.get(NODE)['enabled'] and result['activation_held']
        pid=eng.process.pid;identity=rt.command_status()['command_id']
        recovered=activate(owner,receipt,r)
        assert recovered['service_activated'],recovered
        assert eng.process.pid==pid and rt.command_status()['command_id']==identity


def test_lost_pause_reply_retains_superseding_stop_identity(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request
        def lose_start(origin,cred,path,*a,**kw):
            doc=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':raise OSError('Lost Start')
            return doc
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',lose_start)
            activate(owner,receipt,r)
        def lose_stop(origin,cred,path,*a,**kw):
            doc=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control':raise OSError('Lost Stop')
            return doc
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',lose_stop)
            pending=pause(owner,receipt)
        assert pending['phase']=='stopping' and pending['requires_retry'] and pending['target_may_be_running']
        assert not eng.running and not reg.get(NODE)['enabled']
        identity=rt.command_status()['command_id']
        done=pause(owner,receipt)
        assert done['phase']=='paused' and rt.command_status()['command_id']==identity
        assert reg.commands.status(NODE)['command_id']==identity


def test_owner_csrf_readonly_and_agent_auth_boundaries(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        assert owner.post(url(receipt,'/start'),json=consent(r),headers={'X-Dark-CSRF':''}).status_code==403
        engine.config.writes_enabled=False
        assert owner.post(url(receipt,'/start'),json=consent(r)).status_code==409
        engine.config.writes_enabled=True
        assert client.post('/node/api/v1/control/activate',json={},headers={'Authorization':''}).status_code==401
        key=owner.post('/api/keys',json={'name':'test','permissions':{},'days':1})
        assert key.status_code==200,key.text
        assert owner.post(url(receipt,'/start'),json=consent(r),
                          headers={'Authorization':'Bearer '+key.json()['key']}).status_code==403
        assert not eng.running and reg.commands.status(NODE)['action']=='stop'


def test_new_client_added_after_binding_counts_first_bytes(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner))
        response=owner.post('/api/clients',json={'owner':'dark','client':{'email':'bob',
            'id':'22222222-2222-4222-8222-222222222222','totalGB':10000000,'expiryTime':2000000000000},'inboundIds':[1]})
        assert response.status_code==202,response.text
        assert not reg.store.db.execute("SELECT 1 FROM remote_node_client_usage WHERE client_id='bob'").fetchone()
        r=review(owner,receipt);original=node_module.node_https_request
        def traffic(origin,cred,path,*a,**kw):
            result=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':
                with eng.store.transaction() as db:
                    db.execute('UPDATE core_clients SET up=5,down=4 WHERE email=?',(rt.mirror_for_source('bob'),))
            return result
        monkeypatch.setattr(node_module,'node_https_request',traffic)
        done=activate(owner,receipt,r)
        assert done['service_activated'],done
        assert reg.store.db.execute("SELECT used_bytes FROM clients WHERE id='bob'").fetchone()[0]==9
        assert reg.store.db.execute("SELECT used_bytes FROM clients WHERE id='alice'").fetchone()[0]==35


def test_backup_and_rebuilt_hub_retry_exact_pending_start(hub,tmp_path,monkeypatch):
    from dataclasses import asdict
    from pathlib import Path
    from auth import Auth
    from backup import create_backup,restore_backup
    from core import Config,CoreEngine
    from dark_policy import Store
    from manager import Manager
    from server import make_app
    from fastapi.testclient import TestClient
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request
        def lost(origin,cred,path,*a,**kw):
            result=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':raise OSError('lost')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',lost)
            pending=activate(owner,receipt,r)
        pid=eng.process.pid;identity=rt.command_status()['command_id']
        config=tmp_path/'config.json';config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
        backup=tmp_path/'hub.darkbackup';dest=tmp_path/'restored'
        create_backup(Path(reg.store.path).parent,config,backup,'Activation-test-backup-123!')
        restore_backup(backup,dest,'Activation-test-backup-123!')
        store=Store(dest/'data/dark.sqlite3');auth=Auth(store,dest/'data/secret.key')
        hub_engine=CoreEngine(Config.load(config),store,dest/'runtime')
        app=make_app(Manager(store,hub_engine),auth,background=False)
        try:
            with TestClient(app,base_url=engine.config.public_origin,raise_server_exceptions=False) as restored_owner:
                login=restored_owner.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
                restored_owner.headers['X-Dark-CSRF']=login.json()['csrf']
                saved=restored_owner.get(url(receipt)).json()
                assert saved['start_id']==pending['start_id'] and saved['requires_retry']
                with pytest.raises(PolicyError):app.state.nodes.set_enabled(NODE,True)
                result=activate(restored_owner,receipt,r)
                assert result['service_activated'],result
                assert rt.command_status()['command_id']==identity and eng.process.pid==pid
        finally:hub_engine.close();store.close()


def test_delayed_start_after_pause_is_rejected_by_agent_revision(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,token):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request;requests=[]
        def capture(origin,cred,path,method='GET',body=None,*a,**kw):
            if path=='/node/api/v1/control/activate':
                requests.append(copy.deepcopy(body));raise OSError('not sent')
            return original(origin,cred,path,method,body,*a,**kw)
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',capture)
            assert activate(owner,receipt,r)['requires_retry']
        assert pause(owner,receipt)['phase']=='paused'
        response=client.post('/node/api/v1/control/activate',json=requests[0],headers={'Authorization':'Bearer '+token.token})
        assert response.status_code==409,response.text
        assert not eng.running and rt.control_status()['manual_stop']


def test_concurrent_duplicate_activation_records_and_executes_one_start(hub,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        controller=owner.app.state.replacement_activation
        actual=rt._command_locked;calls=[]
        def count(action):
            if action=='start':calls.append(action)
            return actual(action)
        monkeypatch.setattr(rt,'_command_locked',count)
        def invoke(_):
            return controller.activate(NODE,receipt['attempt_id'],binding_id=r['binding_id'],review_hash=r['review_hash'],
                confirm_start=True,accept_endpoint_responsibility=True,accept_unconfirmed_old_server=True,accept_unreported_traffic=True)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(invoke,range(2)))
        assert calls==['start'] and all(x['service_activated'] for x in results),results
        assert len({x['start_id'] for x in results})==1 and eng.running


def test_agent_recreation_keeps_receipt_and_does_not_reissue_command(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine);target=tmp_path/'target'
    with candidate(target) as (eng,rt,client,_):
        http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner));r=review(owner,receipt)
        original=node_module.node_https_request
        def lost(origin,cred,path,*a,**kw):
            result=original(origin,cred,path,*a,**kw)
            if path=='/node/api/v1/control/activate':raise OSError('Lost Start')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(node_module,'node_https_request',lost)
            assert activate(owner,receipt,r)['requires_retry']
        retained=rt.command_status();api_port=eng.config.xray_api_port
    # A real installed Agent retains config.json. The helper normally allocates
    # a new test port each time, which would correctly invalidate the reviewed
    # compiled hash rather than model a restart of the same configuration.
    monkeypatch.setattr('test_node_replacement_prepare.free_port',lambda:api_port)
    with candidate(target,background=True) as (eng,rt,client,_):
        assert rt.command_status()==retained and eng.running
        http_transport(reg,client,monkeypatch)
        monkeypatch.setattr(rt,'_command_locked',lambda *a:pytest.fail('Committed command replayed after reboot'))
        result=activate(owner,receipt,r)
        assert result['service_activated'] and rt.command_status()==retained,result


def test_missing_activation_capability_has_no_legacy_start_fallback(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (eng,rt,client,_):
        seen=http_transport(reg,client,monkeypatch);receipt=resolve(owner,prepare(owner))
        original=node_module.node_https_request
        def legacy(origin,cred,path,*a,**kw):
            doc,ms=original(origin,cred,path,*a,**kw)
            if path=='/node/api/health':doc['capabilities'].pop('conditional_activation',None)
            return doc,ms
        monkeypatch.setattr(node_module,'node_https_request',legacy)
        response=owner.post(url(receipt,'/review'),json={'bindingId':receipt['committed_binding_id']})
        assert response.status_code==400 and 'conditional activation' in response.text
        assert not eng.running and not reg.get(NODE)['enabled']
        assert not any(path in ('/node/api/core/start','/node/api/core/restart','/node/api/v1/control/activate') for _,path,_ in seen)

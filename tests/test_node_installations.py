"""Logical node / installation boundary. SQLite/FastAPI are real; no WAN proof.

replace_verified is an INTERNAL transaction exercised with fixture-authenticated
health, not a ready enrollment/cutover API. Old-server stop is never assumed.
"""
from __future__ import annotations

import copy
import contextlib
import json
import uuid
from dataclasses import asdict

import pytest
from auth import Auth
from backup import create_backup, restore_backup
from core import Config, CoreEngine
from dark_policy import Store, PolicyError
from nodes import NodeRegistry
import nodes as nodes_module
from test_hub_node_control_live import hub
from test_node_control_lifecycle import rebooted_agent
from test_node_hub_recovery import NODE, TOKEN, ORIGIN, payload, post_state

OLD_ID = 'a'*32
NEW_ID = 'b'*32
NEW_AGENT = 'new-turkey-agent'
NEW_ORIGIN = 'https://new-turkey.example.test:9443'


def health(agent_id=NODE, installation_id=OLD_ID):
    return {'service':'DARK XRAY NODE','agent_only':True,'node_id':agent_id,
            'installation_id':installation_id,'capabilities':{'ordered_control':1,'installation_identity':1},
            'writes_enabled':True,'control_receipt':{'persisted':False},
            'core':{'state':'stopped'},'desired_state':{'appliedRevision':0},'inbounds':0,'managed_clients':0}


def pin(reg, monkeypatch, document=None):
    document=document or health()
    with monkeypatch.context() as patch:
        patch.setattr(reg,'_request',lambda *a,**k:(document,1))
        reg.probe(NODE)
    return reg.installations.public_status(NODE)


def replace(reg, **kwargs):
    return reg.installations.replace_verified(NODE,
        expected_binding_id=kwargs.pop('expected_binding_id',reg.installations.public_status(NODE)['binding_id']),
        health=kwargs.pop('health',health(NEW_AGENT,NEW_ID)),
        origin=kwargs.pop('origin',NEW_ORIGIN),token=kwargs.pop('token','dkn_'+'B'*60),
        data_address=kwargs.pop('data_address','new-turkey.example.test'),**kwargs)


def ack(body):
    return {'service':'DARK XRAY NODE','node_id':body['nodeId'],'revision':body['revision'],
            'commandId':body['commandId'],'action':body['action'],'applied':True,
            'engine':{'state':'stopped' if body['action']=='stop' else 'running'}}


def sql_rows(reg, table):
    with reg.store.lock:
        return [tuple(row) for row in reg.store.db.execute('SELECT * FROM '+table)]


def seed_account(reg, client, engine):
    # Owner, quota, expiry, client UUID, logical inbound and host all belong to Hub.
    created=client.post('/api/inbounds',json={'remark':'TR','listen':'127.0.0.1','port':24543,'protocol':'vless','enable':True,
        'tag':'turkey','settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'}})
    assert created.status_code==200,created.text
    inbound=created.json()
    reg.set_inbound_assignment(NODE,inbound['id'],True)
    response=client.post('/api/clients',json={'owner':'dark','client':{
        'email':'alice','id':'11111111-1111-4111-8111-111111111111',
        'totalGB':100*1024**3,'expiryTime':2000000000000},'inboundIds':[inbound['id']]})
    assert response.status_code==202,response.text
    engine.save_section('hosts',[{'inboundId':inbound['id'],'runtime':'node:'+NODE,'address':'turkey.example.test',
        'port':24543,'remark':'TR','security':'same','enable':True}])
    reg.apply_traffic_snapshot(NODE,[{'sourceEmail':'alice','up':0,'down':0}])
    reg.apply_traffic_snapshot(NODE,[{'sourceEmail':'alice','up':10,'down':25}])
    return inbound['id']


def test_migration_is_idempotent_preserves_existing_metadata_and_pending_command(hub):
    reg,_,_=hub
    reg.commands.record(NODE,'restart')
    expected=reg.installations.public_status(NODE)
    before={t:sql_rows(reg,t) for t in ('remote_nodes','remote_node_inbounds','remote_node_control')}
    rebuilt=NodeRegistry(reg.store,reg.cipher)
    assert rebuilt.installations.public_status(NODE)==expected
    assert before=={t:sql_rows(reg,t) for t in before}
    assert len(rebuilt.installations.history(NODE))==1


def test_first_health_pins_once_and_recreation_preserves_pin(hub,monkeypatch):
    reg,_,_=hub;first=pin(reg,monkeypatch)
    assert first['agent_id']==NODE and first['installation_id']==OLD_ID
    assert pin(reg,monkeypatch)==first
    rebuilt=NodeRegistry(reg.store,reg.cipher)
    assert rebuilt.installations.public_status(NODE)==first
    assert reg.list()[0]['installation']==first


@pytest.mark.parametrize('change',['new-install','new-agent','legacy','boolean-version','invalid-id'])
def test_changed_identity_never_auto_replaces_pinned_node(hub,monkeypatch,change):
    reg,_,_=hub;expected=pin(reg,monkeypatch);doc=health()
    if change=='new-install':doc['installation_id']=NEW_ID
    elif change=='new-agent':doc['node_id']=NEW_AGENT
    elif change=='legacy':doc.pop('capabilities')
    elif change=='boolean-version':doc['capabilities']['installation_identity']=True
    else:doc['installation_id']='invalid'
    before=sql_rows(reg,'remote_nodes')
    with pytest.raises(PolicyError):pin(reg,monkeypatch,doc)
    assert reg.installations.public_status(NODE)==expected
    assert sql_rows(reg,'remote_nodes')==before


def test_legacy_nodes_remain_explicitly_unpinned(hub,monkeypatch):
    reg,_,_=hub;doc=health();doc.pop('capabilities');doc.pop('installation_id')
    pin(reg,monkeypatch,doc)
    assert reg.installations.public_status(NODE)['installation_id']==''


def test_replacement_preserves_accounts_hosts_usage_ledger_and_logical_assignments(hub,monkeypatch):
    reg,client,engine=hub;seed_account(reg,client,engine);old=pin(reg,monkeypatch)
    state=payload(engine);state['nodeId']=NODE
    reg.set_desired_state(NODE,state);reg.commands.record(NODE,'restart')
    master_tables=('clients','managed_clients','owners','core_clients','core_inbounds','core_sections','traffic_ledger')
    before={t:sql_rows(reg,t) for t in master_tables}
    old_ledger=sql_rows(reg,'traffic_ledger');old_command=reg.commands.status(NODE)
    new=replace(reg)
    assert {t:sql_rows(reg,t) for t in master_tables}==before
    assert reg.get(NODE)['name']=='Node' and reg.get(NODE)['inboundIds']==[1]
    assert not reg.get(NODE)['enabled'] and not reg.list()[0]['failover_ready']
    assert new['generation']==old['generation']+1 and new['binding_id']!=old['binding_id']
    assert new['agent_id']==NEW_AGENT and new['installation_id']==NEW_ID
    control=reg.commands.status(NODE)
    assert control['pending'] and control['action']=='stop' and control['command_id']!=old_command['command_id']
    desired=reg.desired_state(NODE)
    assert desired['pending'] and desired['payload']['nodeId']==NEW_AGENT
    assert desired['payload']['desiredRunning'] is False and desired['applied_revision']==0
    history=reg.installations.history(NODE);retired=json.loads(history[0]['retirement_json'])
    assert len(history)==2 and history[0]['retired_at'] and not history[1]['retired_at']
    assert retired['usage'][0]['current_up']+retired['usage'][0]['current_down']==35
    assert retired['control']['command_id']==old_command['command_id']
    assert not retired['traffic_tail_complete'] and not retired['old_stop_confirmed']
    assert TOKEN not in json.dumps(history) and 'token_enc' not in json.dumps(history)
    result=reg.apply_traffic_snapshot(NODE,[{'sourceEmail':'alice','up':3,'down':4}])
    assert result['charged_bytes']==7  # Includes the very first new bytes.
    assert reg.store.db.execute("SELECT used_bytes FROM clients WHERE id='alice'").fetchone()[0]==42
    ledger=sql_rows(reg,'traffic_ledger');assert ledger[:-1]==old_ledger and len(ledger)==len(old_ledger)+1
    assert reg.apply_traffic_snapshot(NODE,[{'sourceEmail':'alice','up':3,'down':4}])['charged_bytes']==0


@pytest.mark.parametrize('failure',['not-fresh','running','reused','stale-preview','bad-token','bad-state','old-receipt','read-only'])
def test_invalid_replacement_rolls_back_all_tables(hub,monkeypatch,failure):
    reg,_,engine=hub;old=pin(reg,monkeypatch);reg.set_desired_state(NODE,payload(engine))
    options={}
    if failure=='not-fresh':doc=health(NEW_AGENT,NEW_ID);doc['managed_clients']=1;options['health']=doc
    elif failure=='running':doc=health(NEW_AGENT,NEW_ID);doc['core']['state']='running';options['health']=doc
    elif failure=='reused':options['health']=health(NODE,OLD_ID)
    elif failure=='stale-preview':options['expected_binding_id']='f'*32
    elif failure=='bad-token':options['token']='invalid'
    elif failure=='old-receipt':doc=health(NEW_AGENT,NEW_ID);doc['control_receipt']['persisted']=True;options['health']=doc
    elif failure=='read-only':doc=health(NEW_AGENT,NEW_ID);doc['writes_enabled']=False;options['health']=doc
    else:
        with reg.store.transaction() as db:db.execute("UPDATE remote_node_desired_state SET desired_json='broken'")
    tables=('remote_nodes','remote_node_installations','remote_node_desired_state','remote_node_control','remote_node_inbounds')
    before={t:sql_rows(reg,t) for t in tables}
    with pytest.raises((PolicyError,ValueError)):replace(reg,**options)
    assert {t:sql_rows(reg,t) for t in tables}==before
    assert reg.installations.public_status(NODE)==old


@pytest.mark.parametrize('endpoint',['traffic','security','health','control','config','rotation'])
def test_retired_response_cannot_publish_new_installation_state(hub,monkeypatch,endpoint):
    reg,client,engine=hub;seed_account(reg,client,engine);pin(reg,monkeypatch)
    reg.set_desired_state(NODE,payload(engine))
    original=reg.desired_state(NODE);captured=[]
    if endpoint=='control':reg.commands.record(NODE,'restart')
    def transport(node,path,method='GET',body=None,timeout=8.0):
        replace(reg)
        captured.append({t:sql_rows(reg,t) for t in ('remote_nodes','remote_node_control',
            'remote_node_desired_state','remote_node_inbounds','remote_node_client_usage','remote_node_security_state','clients')})
        if endpoint=='traffic':return {'items':[{'sourceEmail':'alice','up':999,'down':999}]},1
        if endpoint=='security':return {'sourceVerified':True,'items':[]},1
        if endpoint=='health':return health(),1
        if endpoint=='control':return ack(body),1
        if endpoint=='rotation':return {'service':'DARK XRAY NODE','rotated':True},1
        return {'service':'DARK XRAY NODE','appliedRevision':body['revision'],'appliedHash':body['hash'],
                'items':[{'sourceInboundId':1,'remoteInboundId':1}],'core':{'state':'running'}},1
    if endpoint=='rotation':
        # A durable handoff prevents retirement before remote credential effects.
        # Keep the same account/configuration preservation assertions.
        import sqlite3
        def credential_transport(*args,**kwargs):
            before={t:sql_rows(reg,t) for t in ('remote_nodes','remote_node_control',
                'remote_node_desired_state','remote_node_inbounds','remote_node_client_usage',
                'remote_node_security_state','clients')}
            with pytest.raises(sqlite3.IntegrityError):replace(reg)
            captured.append(before)
            raise PolicyError('Replacement conflicted with the pinned credential handoff')
        monkeypatch.setattr(nodes_module,'node_https_request',credential_transport)
        result=reg.rotate_token(NODE,'dkn_'+'C'*60)
        assert result['pending'] and not result['rotated'] and captured
        assert {t:sql_rows(reg,t) for t in captured[0]}==captured[0]
        return
    monkeypatch.setattr(reg,'_request',transport)
    with pytest.raises(PolicyError):
        if endpoint=='traffic':reg.sync_traffic(NODE)
        elif endpoint=='security':reg.sync_security(NODE)
        elif endpoint=='health':reg.probe(NODE)
        elif endpoint=='control':reg.commands.deliver(NODE)
        elif endpoint=='rotation':reg.rotate_token(NODE,'dkn_'+'C'*60)
        else:
            # Exercise the ACK publisher while holding the same production
            # operation context; bypass only unrelated pending-control dispatch.
            with reg.installations.operation(NODE):reg._sync_desired_state_locked(NODE,original)
    assert captured
    assert {t:sql_rows(reg,t) for t in captured[0]}==captured[0]


def test_pending_commands_address_new_agent_but_keep_logical_key(hub,monkeypatch):
    reg,_,engine=hub;pin(reg,monkeypatch);replace(reg);reg.set_enabled(NODE,True)
    seen=[]
    def transport(node,path,method='GET',body=None,timeout=8.0):
        assert node==NODE
        if path.endswith('/health'):return health(NEW_AGENT,NEW_ID),1
        seen.append(body);return ack(body),1
    monkeypatch.setattr(reg,'_request',transport)
    result=reg.deliver_pending_control(NODE)
    assert result['executed'] and seen[0]['nodeId']==NEW_AGENT
    assert reg.commands.status(NODE)['node_id']==NODE
    reg.set_desired_state(NODE,payload(engine))
    assert reg.desired_state(NODE)['payload']['nodeId']==NEW_AGENT


def test_history_and_active_binding_survive_encrypted_hub_backup(hub,monkeypatch,tmp_path):
    reg,client,engine=hub;seed_account(reg,client,engine);pin(reg,monkeypatch);replace(reg)
    config=tmp_path/'config.json';config.write_text(json.dumps(asdict(engine.config)));config.chmod(0o600)
    archive=tmp_path/'hub.darkbackup';restore=tmp_path/'restored'
    expected=reg.installations.history(NODE);master=sql_rows(reg,'clients')
    create_backup(tmp_path/'hub',config,archive,'Backup-Installations-123!')
    restore_backup(archive,restore,'Backup-Installations-123!')
    store=Store(restore/'data/dark.sqlite3')
    try:
        auth=Auth(store,restore/'data/secret.key');restored=NodeRegistry(store,auth.cipher)
        assert restored.installations.history(NODE)==expected
        assert sql_rows(restored,'clients')==master
        assert restored.commands.status(NODE)['action']=='stop'
        assert restored.get(NODE,secret=True)['token']=='dkn_'+'B'*60
    finally:store.close()


def test_delete_with_consumption_is_refused_instead_of_losing_usage(hub):
    reg,client,engine=hub;seed_account(reg,client,engine)
    with pytest.raises(PolicyError):reg.delete(NODE)
    assert reg.store.db.execute("SELECT used_bytes FROM clients WHERE id='alice'").fetchone()[0]==35
    assert reg.get(NODE)


def test_delete_retired_history_is_refused_but_empty_bootstrap_can_be_deleted(hub,monkeypatch):
    reg,_,_=hub;pin(reg,monkeypatch);replace(reg)
    with pytest.raises(PolicyError):reg.delete(NODE)
    reg.put('empty','Empty','https://empty.example.test',TOKEN)
    assert reg.delete('empty')['deleted']
    with pytest.raises(PolicyError):reg.remote_core('empty','stop')


def test_generic_endpoint_edit_does_not_change_pinned_installation_or_rebaseline_usage(hub,monkeypatch):
    reg,client,engine=hub;seed_account(reg,client,engine);old=pin(reg,monkeypatch)
    before=sql_rows(reg,'remote_node_client_usage')
    reg.put(NODE,'Node',NEW_ORIGIN,TOKEN,True,[1])
    assert reg.installations.public_status(NODE)==old
    assert sql_rows(reg,'remote_node_client_usage')==before
    with pytest.raises(PolicyError):pin(reg,monkeypatch,health(NODE,NEW_ID))


def test_old_endpoint_response_is_fenced_after_generic_address_edit(hub,monkeypatch):
    reg,client,engine=hub;seed_account(reg,client,engine);pin(reg,monkeypatch)
    before=sql_rows(reg,'remote_node_client_usage')
    def transport(*a,**k):
        reg.put(NODE,'Node',NEW_ORIGIN,TOKEN,True,[1])
        return {'items':[{'sourceEmail':'alice','up':999,'down':999}]},1
    monkeypatch.setattr(reg,'_request',transport)
    with pytest.raises(PolicyError):reg.sync_traffic(NODE)
    assert sql_rows(reg,'remote_node_client_usage')==before


def test_agent_identity_survives_restart_but_fresh_state_gets_new_identity(tmp_path):
    with rebooted_agent(tmp_path/'old') as (_,_,runtime,client,_):
        old=client.get('/node/api/health').json()
        assert old['capabilities']['installation_identity']==1
        assert old['installation_id']==runtime.installation_id
    with rebooted_agent(tmp_path/'old') as (_,_,runtime,client,_):
        assert client.get('/node/api/health').json()['installation_id']==old['installation_id']
    with rebooted_agent(tmp_path/'new') as (_,_,runtime,client,_):
        assert client.get('/node/api/health').json()['installation_id']!=old['installation_id']


@pytest.mark.parametrize('path',['/node/api/core/stop','/node/api/v1/control','/node/api/v1/state/apply',
                                 '/node/api/v1/token/rotate','/node/api/mirrors/traffic/reset'])
def test_stale_installation_request_is_rejected_before_body_parse_or_mutation(tmp_path,monkeypatch,path):
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,_):
        before=runtime.control_status()
        calls=[]
        with monkeypatch.context() as patch:
            patch.setattr(engine,'command',lambda *a,**k:calls.append(a) or {'state':'stopped'})
            response=client.post(path,content=b'not json',headers={'X-Dark-Expected-Installation-Id':OLD_ID})
        assert response.status_code==409,response.text
        assert calls==[]
        assert runtime.control_status()==before and not runtime.command_status()['persisted']


def test_agent_matching_headers_and_duplicate_header_rejection(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,_,runtime,client,_):
        headers={'X-Dark-Expected-Node-Id':NODE,'X-Dark-Expected-Installation-Id':runtime.installation_id}
        for path in ('/node/api/health','/node/api/inbounds','/node/api/mirrors/traffic'):
            response=client.get(path,headers=headers)
            assert response.status_code==200,response.text
            assert response.headers['x-dark-node-id']==NODE
            assert response.headers['x-dark-installation-id']==runtime.installation_id
        response=client.get('/node/api/health',headers=[('X-Dark-Expected-Installation-Id',runtime.installation_id),
                                                       ('X-Dark-Expected-Installation-Id',runtime.installation_id)])
        assert response.status_code==409
        assert client.get('/node/api/health',headers={'X-Dark-Expected-Node-Id':'foreign'}).status_code==409
        response=client.get('/node/api/health',headers={'Authorization':''})
        assert response.status_code==401 and 'x-dark-installation-id' not in response.headers


def http_transport(reg,client,monkeypatch,*,response_hook=None,header_hook=None):
    """Keep the real Hub request encoder, identity checks and Agent ASGI app.

    Replace the socket/TLS transport only: these tests are not TLS/WAN evidence.
    """
    import urllib.parse
    import io
    seen=[]
    class Connection:
        def __init__(self,*a,**k):pass
        def request(self,method,path,body=None,headers=None):
            seen.append((method,path,dict(headers or {})))
            self.response=client.request(method,path,content=body,headers=headers)
        def getresponse(self):
            response=self.response
            if response_hook:response_hook(response)
            class Reply:
                status=response.status_code
                def __init__(self):self.stream=io.BytesIO(response.content)
                def read(self,size):return self.stream.read(size)
                def getheader(self,key):
                    value=response.headers.get(key)
                    return header_hook(key,value) if header_hook else value
            return Reply()
        def close(self):pass
    monkeypatch.setattr(nodes_module,'_PinnedHTTPSConnection',Connection)
    def resolve(origin):
        parsed=urllib.parse.urlsplit(origin)
        return origin,parsed.hostname,parsed.port or 443,('93.184.216.34',)
    monkeypatch.setattr(nodes_module,'resolve_origin',resolve)
    return seen


def test_real_request_encoder_pins_identity_and_sends_expected_headers(hub,monkeypatch,tmp_path):
    reg,_,_=hub
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,_):
        seen=http_transport(reg,client,monkeypatch)
        reg.probe(NODE)
        assert reg.installations.public_status(NODE)['installation_id']==runtime.installation_id
        reg.sync_traffic(NODE)
        result=reg.remote_core(NODE,'stop')
        assert result['executed']
        assert 'X-Dark-Expected-Installation-Id' not in seen[0][2]
        for _,_,headers in seen[1:]:
            assert headers['X-Dark-Expected-Node-Id']==NODE
            assert headers['X-Dark-Expected-Installation-Id']==runtime.installation_id


@pytest.mark.parametrize('mode',['missing','wrong-installation','wrong-agent'])
def test_real_request_rejects_identity_headers_before_accepting_traffic(hub,monkeypatch,tmp_path,mode):
    reg,_,_=hub
    with rebooted_agent(tmp_path/'node') as (_,_,runtime,client,_):
        seen=http_transport(reg,client,monkeypatch);reg.probe(NODE)
        def corrupt(key,value):
            if mode=='missing':return None
            if mode=='wrong-installation' and key=='X-Dark-Installation-Id':return OLD_ID
            if mode=='wrong-agent' and key=='X-Dark-Node-Id':return 'other-node'
            return value
        http_transport(reg,client,monkeypatch,header_hook=corrupt)
        with pytest.raises(PolicyError,match='identity mismatch'):reg.sync_traffic(NODE)
        assert reg.get(NODE)['last_error'] and not reg.list()[0]['online']
        assert reg.installations.public_status(NODE)['installation_id']==runtime.installation_id


def test_real_transport_to_reinstalled_same_address_cannot_execute_old_mutation(hub,monkeypatch,tmp_path):
    reg,_,_=hub
    with rebooted_agent(tmp_path/'old') as (_,_,_,client,_):
        http_transport(reg,client,monkeypatch);reg.probe(NODE)
    with rebooted_agent(tmp_path/'new') as (_,engine,runtime,client,_):
        http_transport(reg,client,monkeypatch)
        before=runtime.control_status()
        with pytest.raises(PolicyError):reg._request(NODE,'/node/api/core/stop','POST',{})
        assert runtime.control_status()==before and not runtime.command_status()['persisted']
        assert 'identity mismatch' in reg.get(NODE)['last_error']


def test_actual_agent_rebuild_preserves_uuid_and_stop_then_explicit_resume(hub,monkeypatch,tmp_path):
    from node_agent import AgentToken,make_agent_app
    from fastapi.testclient import TestClient
    reg,_,engine=hub
    # Use real source/target Agent APIs. The fresh target has a different agent
    # identity while all Hub references retain the original logical Node key.
    with rebooted_agent(tmp_path/'old') as (_,old_engine,old_runtime,old_client,_):
        http_transport(reg,old_client,monkeypatch);reg.probe(NODE)
        reg.set_desired_state(NODE,payload(old_engine));reg.sync_desired_state(NODE,reg.desired_state(NODE))
        old_client_uuid=next(x['id'] for x in old_engine.clients() if x['email']==old_runtime.mirror_for_source('alice'))
    with rebooted_agent(tmp_path/'new') as (store,new_engine,_,unused,_):
        new_engine.config.public_origin=NEW_ORIGIN
        app=make_agent_app(new_engine,store,AgentToken(tmp_path/'new/token'),NEW_AGENT,background=False)
        with TestClient(app,base_url=NEW_ORIGIN) as client:
            client.headers['Authorization']='Bearer '+TOKEN
            descriptor=client.get('/node/api/health').json()
            replace(reg,health=descriptor,token=TOKEN)
            reg.set_enabled(NODE,True);http_transport(reg,client,monkeypatch)
            state=reg.desired_state(NODE)
            result=reg.sync_desired_state(NODE,state)
            assert result['desired_state_applied'] and not new_engine.running
            runtime=app.state.runtime
            assert next(x['id'] for x in new_engine.clients() if x['email']==runtime.mirror_for_source('alice'))==old_client_uuid
            assert reg.remote_core(NODE,'start')['executed'] and new_engine.running
            assert reg.installations.public_status(NODE)['agent_id']==NEW_AGENT


def test_restored_older_binding_is_refused_by_rebuilt_agent_without_replaying_command(hub,monkeypatch,tmp_path):
    reg,_,_=hub
    with rebooted_agent(tmp_path/'old') as (_,_,_,client,_):
        http_transport(reg,client,monkeypatch);reg.probe(NODE);reg.commands.record(NODE,'restart')
    # A real SQLite backup snapshots the old binding; no proposed auto-recovery
    # algorithm is exercised. An old restored Hub must fail closed, not re-pair.
    import sqlite3
    path=tmp_path/'old-hub.sqlite3'
    with contextlib.closing(sqlite3.connect(path)) as target:reg.store.db.backup(target)
    old_store=Store(path)
    try:
        restored=NodeRegistry(old_store,reg.cipher)
        with rebooted_agent(tmp_path/'new') as (_,engine,runtime,client,_):
            http_transport(restored,client,monkeypatch)
            result=restored.deliver_pending_control(NODE)
            assert result['queued'] and not result['executed']
            assert not runtime.command_status()['persisted'] and not engine.running
            assert restored.installations.public_status(NODE)==reg.installations.public_status(NODE)
    finally:old_store.close()


def test_database_invariant_initializes_binding_for_direct_node_insert(hub):
    reg,_,_=hub
    with reg.store.transaction() as db:
        token=reg.cipher.encrypt(TOKEN.encode()).decode()
        db.execute('INSERT INTO remote_nodes(id,name,origin,token_enc,created_at,updated_at) VALUES(?,?,?,?,1,1)',
                   ('direct','Direct','https://direct.example.test',token))
    assert reg.get('direct')['installation']['agent_id']=='direct'
    assert reg.installations.public_status('direct')['generation']==1
    assert reg.installations.public_status('direct')['installation_id']==''


def test_unknown_first_consumption_is_not_dropped_after_clean_replacement(hub,monkeypatch):
    reg,client,engine=hub;seed_account(reg,client,engine)
    response=client.post('/api/clients',json={'owner':'dark','client':{'email':'never-sampled'},'inboundIds':[1]})
    assert response.status_code==202,response.text
    assert not reg.store.db.execute("SELECT 1 FROM remote_node_client_usage WHERE client_id='never-sampled'").fetchone()
    pin(reg,monkeypatch);replace(reg)
    result=reg.apply_traffic_snapshot(NODE,[{'sourceEmail':'never-sampled','up':3,'down':5}])
    assert result['charged_bytes']==8

"""Replacement preparation: real Hub/Agent APIs and SQLite; no WAN acceptance."""
import base64
import json
import pytest
import nodes as nodes_module
from test_hub_node_control_live import hub
from test_node_hub_recovery import NODE, TOKEN

TARGET_ORIGIN='https://new-turkey.example.test:9443'


def code(**changes):
    body={'schema':1,'nodeId':'new-turkey','name':'Turkey replacement','origin':TARGET_ORIGIN,
          'token':TOKEN,'dataAddress':'new-turkey.example.test','priority':100,'failoverEnabled':True}
    body.update(changes)
    return 'DXN1.'+base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip('=')


def test_prepare_offline_is_persisted_without_changing_live_node(hub,monkeypatch):
    reg,client,_=hub
    before=reg.get(NODE,secret=True)
    monkeypatch.setattr(nodes_module,'resolve_origin',lambda *a: (_ for _ in ()).throw(OSError('Offline')))
    response=client.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code()})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['attempt_id'] and result['phase']=='pending' and not result['prepared']
    assert result['cutover_performed'] is False
    assert reg.get(NODE,secret=True)==before

import contextlib
from dataclasses import asdict
from pathlib import Path
import shutil
import uuid
from fastapi.testclient import TestClient
from core import Config,CoreEngine
from dark_policy import Store,PolicyError
from auth import Auth
from backup import create_backup,restore_backup
from node_agent import AgentToken,make_agent_app
from node_replacement import NodeReplacement,parse_pair_code
from test_node_installations import http_transport,sql_rows,seed_account
from test_node_hub_recovery import free_port,payload,post_state


@contextlib.contextmanager
def candidate(root, *, background=False):
    root.mkdir(parents=True,exist_ok=True)
    fake=root/'fake-xray'
    shutil.copy2(Path(__file__).parent/'fixtures/fake_xray.py',fake);fake.chmod(0o755)
    cfg=Config(public_origin=TARGET_ORIGIN,secure_cookie=True,xray_binary=str(fake),
               xray_assets=str(root),xray_api_port=free_port(),core_autostart=True,test_engine=True)
    store=Store(root/'node.sqlite3');engine=CoreEngine(cfg,store,root/'runtime')
    token_path=root/'token'
    if not token_path.exists():token_path.write_text(TOKEN+'\n');token_path.chmod(0o600)
    token=AgentToken(token_path)
    app=make_agent_app(engine,store,token,'new-turkey',background=background)
    try:
        with TestClient(app,base_url=TARGET_ORIGIN,raise_server_exceptions=False) as client:
            client.headers['Authorization']='Bearer '+token.token
            yield engine,app.state.runtime,client,token
    finally:
        engine.close();store.close()


def prepare(client, pair=None):
    response=client.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':pair or code()})
    assert response.status_code==200,response.text
    return response.json()


def retry(client, attempt_id):
    response=client.post(f'/api/nodes/{NODE}/replacement/{attempt_id}/retry',json={})
    assert response.status_code==200,response.text
    return response.json()


def journal(reg):
    with reg.store.lock:
        return dict(reg.store.db.execute('SELECT * FROM remote_node_replacements').fetchone())


def test_real_pair_preparation_rotates_once_and_keeps_live_accounts_unchanged(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    tables=('remote_nodes','remote_node_installations','remote_node_inbounds','remote_node_client_usage',
            'traffic_ledger','clients','managed_clients','core_clients','core_inbounds','core_sections','owners')
    before={name:sql_rows(reg,name) for name in tables}
    with candidate(tmp_path/'candidate',background=True) as (eng,runtime,client,token):
        assert eng.running
        seen=http_transport(reg,client,monkeypatch)
        result=prepare(owner)
        assert result['prepared'],result
        assert result['phase']=='prepared' and result['target_installation_id']==runtime.installation_id
        assert not result['cutover_performed'] and not eng.running
        assert runtime.control_status()['manual_stop'] and not runtime.command_status()['persisted']
        assert token.token!=TOKEN
        saved=journal(reg)
        assert reg.cipher.decrypt(saved['candidate_enc'].encode()).decode()==token.token
        assert reg.cipher.decrypt(saved['bootstrap_enc'].encode()).decode()==TOKEN
        assert TOKEN not in json.dumps(saved) and token.token not in json.dumps(saved)
        assert token.token not in json.dumps(result) and TOKEN not in json.dumps(result)
        assert before=={name:sql_rows(reg,name) for name in tables}
        assert prepare(owner)['attempt_id']==result['attempt_id']
        assert retry(owner,result['attempt_id'])['prepared']
        assert sum(path.endswith('/replacement/rotate-token') for _,path,_ in seen)==1
        assert sum(path.endswith('/replacement/idle') for _,path,_ in seen)==1
        assert client.get('/node/api/health',headers={'Authorization':'Bearer '+TOKEN}).status_code==401
        assert not any('core/' in path or 'state/apply' in path for _,path,_ in seen)


@pytest.mark.parametrize('lost_path',['/node/api/v1/replacement/rotate-token','/node/api/v1/replacement/idle'])
def test_lost_reply_recovers_from_same_encrypted_journal_after_registry_recreation(hub,tmp_path,monkeypatch,lost_path):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (eng,runtime,client,token):
        def lost(response):
            if response.request.url.path==lost_path:raise OSError('Injected lost response')
        http_transport(reg,client,monkeypatch,response_hook=lost)
        result=prepare(owner);assert not result['prepared'],result
        assert token.token!=TOKEN and result['last_error']
        original=journal(reg);before=reg.get(NODE,secret=True)
        rebuilt=nodes_module.NodeRegistry(reg.store,reg.cipher)
        controller=NodeReplacement(rebuilt)
        seen=http_transport(reg,client,monkeypatch)
        recovered=controller.resume(NODE,result['attempt_id'])
        assert recovered['prepared'],recovered
        assert journal(reg)['candidate_enc']==original['candidate_enc']
        assert reg.get(NODE,secret=True)==before
        assert not any(path.endswith('/replacement/rotate-token') for _,path,_ in seen)
        assert not eng.running and runtime.control_status()['manual_stop']


def test_rotation_not_delivered_retries_same_candidate_instead_of_making_a_new_one(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,_,client,token):
        http_transport(reg,client,monkeypatch)
        real=nodes_module.node_https_request
        def fail(origin,cred,path,*a,**kw):
            if path.endswith('/replacement/rotate-token'):raise OSError('Before dispatch')
            return real(origin,cred,path,*a,**kw)
        with monkeypatch.context() as patch:
            patch.setattr(nodes_module,'node_https_request',fail)
            result=prepare(owner)
        assert result['phase']=='rotating' and not result['prepared'] and token.token==TOKEN
        saved=journal(reg)['candidate_enc']
        recovered=retry(owner,result['attempt_id'])
        assert recovered['prepared'] and journal(reg)['candidate_enc']==saved
        assert reg.cipher.decrypt(saved.encode()).decode()==token.token


def test_rotation_ack_without_new_credential_proof_is_not_prepared(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,_,client,token):
        http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request
        def lie(origin,cred,path,*a,**kw):
            if path.endswith('/replacement/rotate-token'):return {'service':'DARK XRAY NODE','rotated':True},1
            return real(origin,cred,path,*a,**kw)
        monkeypatch.setattr(nodes_module,'node_https_request',lie)
        result=prepare(owner)
        assert not result['prepared'] and result['last_error'] and token.token==TOKEN


def test_preparation_survives_encrypted_backup_and_agent_recreation(hub,tmp_path,monkeypatch):
    reg,owner,hub_engine=hub;root=tmp_path/'candidate'
    with candidate(root) as (_,_,client,token):
        def lost(response):
            if response.request.url.path.endswith('/replacement/rotate-token'):raise OSError('Lost')
        http_transport(reg,client,monkeypatch,response_hook=lost)
        result=prepare(owner);assert not result['prepared'];new_token=token.token
        hub_root=hub_engine.runtime.parent;cfg=hub_root/'config.json'
        cfg.write_text(json.dumps(asdict(hub_engine.config)));cfg.chmod(0o600)
        archive=tmp_path/'hub.darkbackup'
        create_backup(hub_root,cfg,archive,'Only-Test-Backup-Password!')
    destination=tmp_path/'restore'
    restore_backup(archive,destination,'Only-Test-Backup-Password!')
    store=Store(destination/'data/dark.sqlite3')
    try:
        auth=Auth(store,destination/'data/secret.key');restored=nodes_module.NodeRegistry(store,auth.cipher)
        with candidate(root) as (_,_,client,token):
            assert token.token==new_token
            seen=http_transport(restored,client,monkeypatch)
            recovered=NodeReplacement(restored).resume(NODE,result['attempt_id'])
            assert recovered['prepared'],recovered
            assert not any(path.endswith('/replacement/rotate-token') for _,path,_ in seen)
            assert restored.installations.public_status(NODE)['generation']==1
    finally:store.close()


@pytest.mark.parametrize('change',[
    {'schema':True},{'priority':True},{'failoverEnabled':1},{'token':'dkn_'+('X'*40)+'\r\nBad'},
    {'nodeId':'bad/id'},{'extra':True},{'dataAddress':'https://bad.test/'},
])
def test_invalid_pair_code_never_creates_journal_or_contacts_candidate(hub,monkeypatch,change):
    reg,owner,_=hub
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Invalid candidate contacted'))
    response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code(**change)})
    assert response.status_code==400,response.text
    assert not sql_rows(reg,'remote_node_replacements')


@pytest.mark.parametrize('mode',['duplicate-json','invalid-base64','not-a-pair'])
def test_malformed_pair_code_rejected_without_leaking_content(hub,mode):
    _,owner,_=hub
    pair='DXN1.'+'!'*40 if mode=='invalid-base64' else 'WrongPrefix.'+'X'*30
    if mode=='duplicate-json':
        raw=b'{"schema":1,"schema":1}'
        pair='DXN1.'+base64.urlsafe_b64encode(raw).decode().rstrip('=')
    response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':pair})
    assert response.status_code==400 and pair not in response.text


@pytest.mark.parametrize('mode',['nonempty','wrong-agent','unsupported','already-pinned'])
def test_unsafe_target_rejected_before_rotation(hub,tmp_path,monkeypatch,mode):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (engine,runtime,client,token):
        pair=code()
        if mode=='nonempty':
            body=payload(engine);body['nodeId']='new-turkey';post_state(client,body)
        if mode=='wrong-agent':pair=code(nodeId='different-agent')
        if mode=='already-pinned':
            with reg.store.transaction() as db:
                db.execute('UPDATE remote_node_installations SET installation_id=?',(runtime.installation_id,))
        def alter(response):
            if mode=='unsupported' and response.status_code==200:
                body=response.json();body['capabilities'].pop('replacement_prepare',None)
                response._content=json.dumps(body).encode()
        seen=http_transport(reg,client,monkeypatch,response_hook=alter)
        result=prepare(owner,pair)
        assert not result['prepared'] and result['last_error'],result
        assert token.token==TOKEN and not any(method=='POST' for method,_,_ in seen)


def test_registered_origin_and_second_different_plan_are_rejected(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    assert owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code(origin=reg.get(NODE)['origin'])}).status_code==400
    monkeypatch.setattr(nodes_module,'resolve_origin',lambda *a:(_ for _ in ()).throw(OSError('offline')))
    first=prepare(owner)
    response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code(token='dkn_'+'Z'*60)})
    assert response.status_code==400
    assert journal(reg)['attempt_id']==first['attempt_id']


@pytest.mark.parametrize('mode',['csrf','readonly','anonymous'])
def test_owner_write_boundaries_apply_before_journaling(hub,mode):
    reg,owner,engine=hub
    if mode=='readonly':engine.config.writes_enabled=False
    if mode=='csrf':owner.headers['X-Dark-CSRF']='bad'
    if mode=='anonymous':owner.cookies.clear()
    response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code()})
    assert response.status_code in (401,403,409),response.text
    assert not sql_rows(reg,'remote_node_replacements')


def test_status_is_read_only_and_attempt_cannot_be_used_for_another_node(hub,monkeypatch):
    reg,owner,_=hub
    monkeypatch.setattr(nodes_module,'resolve_origin',lambda *a:(_ for _ in ()).throw(OSError('offline')))
    result=prepare(owner)
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('GET performed remote work'))
    status=owner.get(f"/api/nodes/{NODE}/replacement/{result['attempt_id']}")
    assert status.status_code==200 and status.json()==result
    assert owner.get(f"/api/nodes/other/replacement/{result['attempt_id']}").status_code==400
    assert owner.post(f"/api/nodes/other/replacement/{result['attempt_id']}/retry",json={}).status_code==400


def test_source_endpoint_change_fences_resume_without_remote_mutation(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,_,client,token):
        http_transport(reg,client,monkeypatch)
        controller=NodeReplacement(reg);result=controller.begin(NODE,code())
        with reg.store.transaction() as db:db.execute("UPDATE remote_nodes SET origin='https://changed.test' WHERE id=?",(NODE,))
        monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:pytest.fail('Obsolete preparation contacted target'))
        result=controller.resume(NODE,result['attempt_id'])
        assert result['last_error']=='source_changed' and not result['prepared'] and token.token==TOKEN


@pytest.mark.parametrize('mode',['timeout','identity','redirect'])
def test_no_bootstrap_fallback_on_ambiguous_candidate_probe(hub,tmp_path,monkeypatch,mode):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,_,client,token):
        http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request;calls=[]
        def failed(origin,cred,path,*a,**kw):
            calls.append((cred,path))
            if cred!=TOKEN:
                if mode=='timeout':raise TimeoutError('Injected')
                if mode=='identity':raise PolicyError('Node response installation identity mismatch')
                raise nodes_module.NodeHTTPError(302)
            return real(origin,cred,path,*a,**kw)
        monkeypatch.setattr(nodes_module,'node_https_request',failed)
        result=prepare(owner)
        assert not result['prepared'] and token.token==TOKEN
        assert len(calls)==2 and calls[0][0]==TOKEN and calls[1][0]!=TOKEN


def test_peer_error_is_redacted_in_status_and_database(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    monkeypatch.setattr(nodes_module,'node_https_request',lambda *a,**k:(_ for _ in ()).throw(PolicyError('REMOTE LEAK '+TOKEN)))
    result=prepare(owner)
    assert result['last_error']=='target_contact_or_verification_failed'
    assert TOKEN not in json.dumps(result) and TOKEN not in json.dumps(journal(reg))


def test_newer_preparation_result_is_not_overwritten_by_delayed_failure(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,_,client,token):
        http_transport(reg,client,monkeypatch)
        controller=NodeReplacement(reg);result=controller.begin(NODE,code())
        real=controller._exchange;once=[False]
        def supersede(row,*a,**kw):
            if not once[0]:
                once[0]=True
                with reg.store.transaction() as db:
                    db.execute("UPDATE remote_node_replacements SET operation_revision=operation_revision+1,last_error='newer-result' WHERE attempt_id=?",(row['attempt_id'],))
                raise OSError('Older request failed')
            return real(row,*a,**kw)
        monkeypatch.setattr(controller,'_exchange',supersede)
        assert controller.resume(NODE,result['attempt_id'])['last_error']=='newer-result'


def test_identity_changes_between_probes_cannot_rotate_or_prepare(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,_,client,token):
        calls=[0]
        def altered(response):
            calls[0]+=1
            if calls[0]>=3:
                response.headers['x-dark-installation-id']='f'*32
        seen=http_transport(reg,client,monkeypatch,response_hook=altered)
        result=prepare(owner)
        assert not result['prepared'] and token.token==TOKEN
        assert not any(method=='POST' for method,_,_ in seen)


def test_readonly_retry_and_reseller_cannot_run_preparation(hub,monkeypatch):
    reg,owner,engine=hub
    monkeypatch.setattr(nodes_module,'resolve_origin',lambda *a:(_ for _ in ()).throw(OSError('offline')))
    first=prepare(owner)
    before=journal(reg)
    engine.config.writes_enabled=False
    response=owner.post(f"/api/nodes/{NODE}/replacement/{first['attempt_id']}/retry",json={})
    assert response.status_code==409 and journal(reg)==before
    engine.config.writes_enabled=True
    # A non-owner session is denied by the same owner dependency.
    with reg.store.transaction() as db:db.execute("UPDATE api_admins SET role='reseller' WHERE id='dark'")
    response=owner.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code()})
    assert response.status_code in (401,403) and journal(reg)==before


def test_api_key_cannot_stage_candidate(hub):
    reg,client,_=hub
    response=client.post('/api/keys',json={'name':'replacement-test','days':1,'permissions':{'clients.read':'all'}})
    # The interactive-owner contract is also covered by existing API-key suites;
    # use Auth's actual key API when available, not a fabricated bearer.
    if response.status_code!=200:
        raise AssertionError(response.text)
    key=response.json()['key']
    client.cookies.clear();client.headers['Authorization']='Bearer '+key
    response=client.post(f'/api/nodes/{NODE}/replacement/prepare',json={'code':code()})
    assert response.status_code==403,response.text
    assert not sql_rows(reg,'remote_node_replacements')


def test_target_registered_during_last_probe_is_not_published_prepared(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (_,runtime,client,token):
        def register(response):
            if response.request.headers.get('Authorization')=='Bearer '+token.token and token.token!=TOKEN and response.request.url.path.endswith('/health'):
                with reg.store.transaction() as db:
                    db.execute('UPDATE remote_node_installations SET installation_id=?',(runtime.installation_id,))
        http_transport(reg,client,monkeypatch,response_hook=register)
        result=prepare(owner)
        assert not result['prepared'] and result['last_error']=='target_already_registered'
        assert journal(reg)['candidate_enc']


def test_candidate_gains_inbound_after_health_rotation_rechecks_before_touching_token(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'candidate') as (engine,runtime,client,token):
        http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request
        def changed(origin,cred,path,*a,**kw):
            if path.endswith('/replacement/rotate-token'):
                engine.save_inbound(payload(engine)['assignments'][0]['inbound'])
            return real(origin,cred,path,*a,**kw)
        monkeypatch.setattr(nodes_module,'node_https_request',changed)
        result=prepare(owner)
        assert not result['prepared'] and token.token==TOKEN and len(engine.inbounds())==1
        assert not runtime.control_status()['manual_stop']


@pytest.mark.parametrize('mode',['add','edit'])
def test_normal_node_registration_cannot_take_reserved_candidate(hub,monkeypatch,mode):
    reg,owner,_=hub
    controller=NodeReplacement(reg);controller.begin(NODE,code())
    before=sql_rows(reg,'remote_nodes')
    with pytest.raises(PolicyError,match='reserved'):
        reg.put('other-node' if mode=='add' else NODE,'Concurrent registration',TARGET_ORIGIN,TOKEN)
    assert sql_rows(reg,'remote_nodes')==before


def test_full_web_backup_preserves_preparation_journal(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    controller=NodeReplacement(reg);attempt=controller.begin(NODE,code())
    cfg=engine.runtime.parent/'config.json'
    cfg.write_text(json.dumps(asdict(engine.config)));cfg.chmod(0o600)
    engine.config._path=str(cfg)
    response=owner.post('/api/backup/full',json={'passphrase':'Web-Backup-Only-Password!'})
    assert response.status_code==200,response.text
    archive=tmp_path/'web.darkbackup';archive.write_bytes(response.content)
    destination=tmp_path/'web-restored'
    restore_backup(archive,destination,'Web-Backup-Only-Password!')
    store=Store(destination/'data/dark.sqlite3')
    try:
        auth=Auth(store,destination/'data/secret.key')
        registry=nodes_module.NodeRegistry(store,auth.cipher)
        restored=NodeReplacement(registry).status(NODE,attempt['attempt_id'])
        assert restored['attempt_id']==attempt['attempt_id'] and not restored['prepared']
        with store.lock:
            row=store.db.execute('SELECT candidate_enc FROM remote_node_replacements').fetchone()
        assert auth.cipher.decrypt(row['candidate_enc'].encode())==reg.cipher.decrypt(journal(reg)['candidate_enc'].encode())
    finally:store.close()

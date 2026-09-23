"""Hub/Agent integration regressions using real FastAPI/SQLite and fake Xray.

The transport is in-process TestClient, not a WAN/TLS proof. The separate
fresh-node-agent CI job checks installed services and verified HTTPS.
No test touches installed production paths or real customer databases.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import os
import shutil
import socket
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import nodes as nodes_module
from auth import Auth
from backup import create_backup,restore_backup
from core import Config,CoreEngine
from dark_policy import Store,PolicyError
from node_agent import AgentToken,make_agent_app
from node_runtime import STATE_SECTIONS
from nodes import NodeRegistry

NODE='node-recovery-1'
ORIGIN='https://node.example.test:9443'
TOKEN='dkn_'+('A'*60)
CLIENT_UUID='11111111-1111-4111-8111-111111111111'
ROOT=Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def inbound(port=24543):
    return {'remark':'RECOVERY','listen':'127.0.0.1','port':port,'protocol':'vless','enable':True,
            'tag':'recovery','settings':{'decryption':'none'},
            'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}}


@contextlib.contextmanager
def agent(root:Path,credential=TOKEN):
    root.mkdir(parents=True,exist_ok=True)
    fake=root/'fake-xray'
    shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);fake.chmod(0o755)
    cfg=Config(public_origin=ORIGIN,public_address='node.example.test',secure_cookie=True,
               xray_binary=str(fake),xray_assets=str(root),xray_api_port=free_port(),
               core_autostart=False,test_engine=True)
    store=Store(root/'node.sqlite3');engine=CoreEngine(cfg,store,root/'runtime')
    token_path=root/'token'
    if not token_path.exists():token_path.write_text(credential+'\n');token_path.chmod(0o600)
    token=AgentToken(token_path)
    app=make_agent_app(engine,store,token,NODE,background=False)
    try:
        with TestClient(app,base_url=ORIGIN,raise_server_exceptions=False) as client:
            client.headers['Authorization']='Bearer '+token.token
            yield store,engine,app.state.runtime,client
    finally:
        engine.close();store.close()


def payload(engine,source_id=1,tls=False):
    item=inbound();files=[]
    if tls:
        refs={}
        for field,kind,data in (('certificateFile','certificate',b'CERTIFICATE-FIXTURE'),
                                ('keyFile','private-key',b'PRIVATE-KEY-FIXTURE')):
            file_id=hashlib.sha256(field.encode()).hexdigest()
            files.append({'id':file_id,'kind':kind,'sha256':hashlib.sha256(data).hexdigest(),
                          'data':base64.b64encode(data).decode()})
            refs[field]='managed://'+file_id
        item['streamSettings']={'network':'tcp','security':'tls','tlsSettings':{'certificates':[refs]}}
    return {'schema':1,'nodeId':NODE,'desiredRunning':True,
            'sections':{name:engine.section(name) for name in STATE_SECTIONS},
            'assignments':[{'sourceInboundId':source_id,'inbound':item,
                            'clients':[{'sourceEmail':'alice','client':{'id':CLIENT_UUID,'enable':True}}]}],
            'security':{'clients':[{'sourceEmail':'alice','limitIp':2,'limitHwid':1,
                                     'globalIpBlocked':False,'globalDeviceBlocked':False}]},'files':files}


def envelope(body,revision=1):
    raw=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return {'revision':revision,'hash':hashlib.sha256(raw.encode()).hexdigest(),'payload':body}


def post_state(client,body,revision=1):
    value=envelope(body,revision)
    response=client.post('/node/api/v1/state/apply',json=value)
    assert response.status_code==200,response.text
    data=response.json()
    assert data['appliedRevision']==revision and data['appliedHash']==value['hash'],data
    return data


def test_authenticated_health_and_security_use_stable_identity_after_rotation(tmp_path):
    with agent(tmp_path/'node') as (store,engine,runtime,client):
        with store.transaction() as db:
            db.execute('INSERT INTO node_runtime_clients VALUES(?,?,?,?)',(NODE,'alice','nm_fixture',1))
        for path in ('/node/api/health','/node/api/mirrors/security','/node/api/v1/state'):
            response=client.get(path)
            assert response.status_code==200,response.text
        health=client.get('/node/api/health').json()
        assert health['node_id']==NODE and health['managed_clients']==1
        assert client.get('/node/api/mirrors/security').json()['items'][0]['sourceEmail']=='alice'
        assert client.get('/node/api/health',headers={'Authorization':''}).status_code==401
        for path in ('/','/api/auth/login','/assets/live.js','/docs','/openapi.json'):
            assert client.get(path).status_code==404
        new_token='dkn_'+('B'*60)
        response=client.post('/node/api/v1/token/rotate',json={'token':new_token})
        assert response.status_code==200,response.text
        assert client.get('/node/api/health').status_code==401
        client.headers['Authorization']='Bearer '+new_token
        assert client.get('/node/api/health').json()['managed_clients']==1
        assert runtime.scope==NODE
    with agent(tmp_path/'node') as (_,_,runtime,client):
        assert runtime.scope==NODE
        assert client.get('/node/api/health').json()['managed_clients']==1


def test_tls_desired_ack_is_envelope_hash_and_duplicate_apply_is_noop(tmp_path):
    with agent(tmp_path/'node') as (store,engine,runtime,client):
        body=payload(engine,tls=True)
        first=post_state(client,body)
        assert first['changed'] is True and first['items'][0]['remoteInboundId']==1
        pid=engine.process.pid
        same=post_state(client,body)
        assert same['changed'] is False and engine.process.pid==pid
        assert runtime.status()['appliedHash']==envelope(body)['hash']
        wrong=copy.deepcopy(body);wrong['sections']['dns']={'servers':['8.8.8.8']}
        response=client.post('/node/api/v1/state/apply',json=envelope(wrong,1))
        assert response.status_code==422,response.text
        assert engine.process.pid==pid


def test_config_update_preserves_counters_and_failed_apply_restores_previous_runtime(tmp_path):
    with agent(tmp_path/'node') as (store,engine,runtime,client):
        body=payload(engine)
        post_state(client,body)
        mirror=runtime.mirror_for_source('alice')
        with store.transaction() as db:
            db.execute('UPDATE core_clients SET up=123,down=456 WHERE email=?',(mirror,))
        update=copy.deepcopy(body);update['sections']['dns']={'servers':['8.8.8.8']}
        post_state(client,update,2)
        counters=client.get('/node/api/mirrors/traffic').json()['items']
        assert counters==[{'sourceEmail':'alice','up':123,'down':456}],counters
        failure=copy.deepcopy(update)
        failure['sections']['outbounds'][0]['settings']['failTestStartup']=True
        response=client.post('/node/api/v1/state/apply',json=envelope(failure,3))
        assert response.status_code==422,response.text
        assert runtime.status()['appliedRevision']==2
        assert engine.running and engine.section('dns')==update['sections']['dns']
        assert client.get('/node/api/mirrors/traffic').json()['items']==counters
        assert engine.section('outbounds')==update['sections']['outbounds']


def test_state_cannot_target_another_node_and_malformed_clients_do_not_touch_live_state(tmp_path):
    with agent(tmp_path/'node') as (_,engine,runtime,client):
        body=payload(engine)
        wrong=copy.deepcopy(body);wrong['nodeId']='another-node'
        assert client.post('/node/api/v1/state/apply',json=envelope(wrong)).status_code==422
        wrong=copy.deepcopy(body);wrong['assignments'][0]['clients']=None
        assert client.post('/node/api/v1/state/apply',json=envelope(wrong)).status_code==422
        assert runtime.status()['appliedRevision']==0 and not engine.inbounds()


def bridge(client,credential):
    def request(node_id,path,method='GET',body=None,timeout=8.0):
        assert node_id==NODE
        response=client.request(method,path,json=body,headers={'Authorization':'Bearer '+credential})
        if response.status_code!=200:raise PolicyError('Node HTTP '+str(response.status_code))
        return response.json(),1
    return request


def test_encrypted_hub_backup_rebuilds_fresh_agent_from_restored_desired_state(tmp_path,monkeypatch):
    # Do not use WAN or disable production SSRF validation: replace just origin
    # validation in this in-process transport fixture, tested elsewhere for real.
    monkeypatch.setattr(nodes_module,'validate_origin',lambda origin:origin)
    hub=tmp_path/'hub';hub.mkdir()
    store=Store(hub/'dark.sqlite3');auth=Auth(store,hub/'secret.key')
    cfg_path=hub/'config.json'
    cfg_path.write_text(json.dumps(asdict(Config(xray_binary=str(hub/'unused'),
        xray_assets=str(hub),test_engine=True,core_autostart=False))))
    cfg_path.chmod(0o600)
    engine=CoreEngine(Config.load(cfg_path),store,hub/'runtime')
    registry=NodeRegistry(store,auth.cipher)
    iid=engine.save_inbound(inbound())['id']
    registry.put(NODE,'Recovery Node',ORIGIN,TOKEN,True,[iid],'node.example.test')
    archive=tmp_path/'hub.darkbackup'
    try:
        with agent(tmp_path/'first-node') as (_,node_engine,runtime,client):
            body=payload(node_engine,source_id=iid,tls=True)
            state=registry.set_desired_state(NODE,body)
            registry._request=bridge(client,TOKEN)
            applied=registry.sync_desired_state(NODE,registry.desired_state(NODE))
            assert applied['desired_state_applied'] is True
            assert registry.desired_state(NODE,include_payload=False)['pending'] is False
            # Offline edits remain in the Hub without claiming to be applied.
            body['sections']['dns']={'servers':['8.8.8.8']}
            registry.set_desired_state(NODE,body)
            assert registry.desired_state(NODE,include_payload=False)['pending'] is True
            manifest=create_backup(hub,cfg_path,archive,'Recovery-Only-Passphrase-123!')
            assert manifest['schema']==2
    finally:
        engine.close();store.close()
    restored=tmp_path/'restored'
    restore_backup(archive,restored,'Recovery-Only-Passphrase-123!')
    restored_store=Store(restored/'data/dark.sqlite3')
    try:
        restored_auth=Auth(restored_store,restored/'data/secret.key')
        registry=NodeRegistry(restored_store,restored_auth.cipher)
        credential=registry.get(NODE,secret=True)['token']
        assert credential==TOKEN
        state=registry.desired_state(NODE)
        assert state['pending'] is True and state['revision']==2
        assert state['payload']['files'][1]['data']
        # New hardware/state directory with the same securely provisioned Node
        # identity+credential: this tests rebuild, not automatic re-pair enrolment.
        with agent(tmp_path/'rebuilt-node',credential) as (node_store,eng,runtime,client):
            registry._request=bridge(client,credential)
            result=registry.sync_desired_state(NODE,state)
            assert result['desired_state_applied'] is True
            assert runtime.status()['appliedRevision']==2
            assert eng.inbound(iid)['tag']=='recovery'
            assert eng.section('dns')=={'servers':['8.8.8.8']}
            assert runtime.mirror_for_source('alice')
            assert not registry.desired_state(NODE,include_payload=False)['pending']
            for item in eng.inbound(iid)['streamSettings']['tlsSettings']['certificates']:
                for field in ('certificateFile','keyFile'):
                    path=Path(item[field]);assert path.is_file()
                    assert path.stat().st_mode&0o077==0
            with node_store.lock:
                assert node_store.db.execute('SELECT COUNT(*) FROM api_admins').fetchone()[0]==0
    finally:restored_store.close()


def load_updater():
    import importlib.util
    spec=importlib.util.spec_from_file_location('node_update_transaction',ROOT/'tools/update_node.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def update_fixture(tmp_path,monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    updater=load_updater();app=tmp_path/'app';data=tmp_path/'data';src=tmp_path/'source'
    for root in (app,data,src):root.mkdir()
    monkeypatch.setattr(updater,'APP',app);monkeypatch.setattr(updater,'DATA',data)
    for name in updater.SOURCE_FILES:
        for root,prefix in ((app,'old-'),(src,'new-')):
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(prefix+name)
    (app/'VERSION').write_text('0.9.0-rc7');(src/'VERSION').write_text('0.9.0-rc8')
    for root,label in ((app/'.venv','old-interpreter'),(tmp_path/'candidate-venv','new-interpreter')):
        (root/'bin').mkdir(parents=True)
        executable=root/'bin/python';executable.write_text(label);executable.chmod(0o755)
    with contextlib.closing(sqlite3.connect(data/'node.sqlite3')) as db, db:
        db.execute('CREATE TABLE retained(value TEXT)');db.execute("INSERT INTO retained VALUES('before-update')")
    source={'commit':'a'*40,'version':'0.9.0-rc7','ref':'old','role':'node-agent'}
    (data/'installed-source.json').write_text(json.dumps(source));(data/'installed-source.json').chmod(0o640)
    transaction=tmp_path/'rollback';transaction.mkdir(mode=0o700)
    calls=[]
    def run(args,**kwargs):
        calls.append(tuple(map(str,args)));return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(updater,'run',run)
    monkeypatch.setattr(updater,'install_units',lambda:None)
    return updater,app,data,src,tmp_path/'candidate-venv',transaction,calls


def test_node_updater_run_supports_real_captured_subprocess(tmp_path):
    import sys
    updater=load_updater()
    result=updater.run([sys.executable,'-c','import sys;print("out");print("err",file=sys.stderr)'])
    assert result.returncode==0 and result.stdout.strip()=='out' and result.stderr.strip()=='err'
    output=tmp_path/'log'
    with output.open('w') as stream:
        updater.run([sys.executable,'-c','print("streamed")'],stdout=stream)
    assert output.read_text().strip()=='streamed'


def test_node_update_failure_before_venv_switch_never_deletes_old_environment(tmp_path,monkeypatch):
    updater,app,data,src,venv,transaction,calls=update_fixture(tmp_path,monkeypatch)
    def broken_copy(_src):raise OSError('injected copy failure before venv rename')
    monkeypatch.setattr(updater,'copy_source',broken_copy)
    monkeypatch.setattr(updater,'health_probe',lambda **kwargs:{'service':'DARK XRAY NODE'})
    with pytest.raises(RuntimeError,match='database restored'):
        updater.activate_candidate(src,venv,'b'*40,'0.9.0-rc8','b'*40,transaction)
    executable=app/'.venv/bin/python'
    assert executable.read_text()=='old-interpreter' and executable.stat().st_mode&0o111
    assert updater.current_source()['commit']=='a'*40


def test_node_update_failed_health_restores_source_database_and_venv(tmp_path,monkeypatch):
    import sqlite3
    updater,app,data,src,venv,transaction,calls=update_fixture(tmp_path,monkeypatch)
    def health(**kwargs):
        if kwargs.get('expected_version')=='0.9.0-rc8':
            with contextlib.closing(sqlite3.connect(data/'node.sqlite3')) as db, db:
                db.execute("UPDATE retained SET value='new-generation'")
                db.execute('CREATE TABLE injected_new_schema(value TEXT)')
            raise RuntimeError('injected post-start health failure')
        return {'service':'DARK XRAY NODE'}
    monkeypatch.setattr(updater,'health_probe',health)
    with pytest.raises(RuntimeError,match='database restored'):
        updater.activate_candidate(src,venv,'b'*40,'0.9.0-rc8','b'*40,transaction)
    assert (app/'VERSION').read_text()=='0.9.0-rc7'
    assert (app/'.venv/bin/python').read_text()=='old-interpreter'
    with contextlib.closing(sqlite3.connect(data/'node.sqlite3')) as db, db:
        assert db.execute('SELECT value FROM retained').fetchone()[0]=='before-update'
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='injected_new_schema'").fetchone()
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert updater.current_source()['commit']=='a'*40


def test_node_update_success_preserves_source_reader_permissions(tmp_path,monkeypatch):
    updater,app,data,src,venv,transaction,calls=update_fixture(tmp_path,monkeypatch)
    metadata=data/'installed-source.json';before=metadata.stat()
    monkeypatch.setattr(updater,'health_probe',lambda **kwargs:{'service':'DARK XRAY NODE'})
    result=updater.activate_candidate(src,venv,'b'*40,'0.9.0-rc8','b'*40,transaction)
    assert result['updated'] and updater.current_source()['commit']=='b'*40
    after=metadata.stat()
    assert (after.st_uid,after.st_gid)==(before.st_uid,before.st_gid)
    assert after.st_mode&0o777==0o640
    assert (app/'.venv/bin/python').read_text()=='new-interpreter'
    assert (app/'.venv/bin/python').stat().st_mode&0o111

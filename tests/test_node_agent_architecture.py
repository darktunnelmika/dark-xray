import base64
import contextlib
import hashlib
import json
import os
import socket
from pathlib import Path

import pytest

import nodes as nodes_mod
from auth import Auth
from backup import create_backup, restore_backup
from core import Config, CoreEngine
from dark_policy import Store
from node_runtime import NodeRuntime
from nodes import NodeRegistry

PASS='NodeArchitectureBackup-123!'


def _vless(port=24443,tag='node-test',panel_meta=None):
    return {
        'remark':'NODE TEST','listen':'0.0.0.0','port':port,'protocol':'vless','enable':True,'tag':tag,
        'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{},
        'panelMeta':panel_meta or {}
    }


def test_hub_desired_state_revision_is_stable_and_acknowledged(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'hub.sqlite3');auth=Auth(store,tmp_path/'secret.key');reg=NodeRegistry(store,auth.cipher)
    reg.put('n1','Node 1','https://node.example','dkn_'+('A'*60),True)
    one=reg.set_desired_state('n1',{'schema':1,'value':'A'})
    same=reg.set_desired_state('n1',{'schema':1,'value':'A'})
    two=reg.set_desired_state('n1',{'schema':1,'value':'B'})
    assert one['revision']==1 and one['changed'] is True
    assert same['revision']==1 and same['changed'] is False
    assert two['revision']==2 and two['changed'] is True and two['pending'] is True
    reg.mark_desired_state('n1',two['revision'],two['hash'])
    state=reg.desired_state('n1',include_payload=False)
    assert state['pending'] is False and state['applied_revision']==2 and state['applied_hash']==two['hash']
    store.close()


def test_local_deployment_scope_and_runtime_host_filtering(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    cfg=Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),public_address='main.example.test',test_engine=True)
    eng=CoreEngine(cfg,store,tmp_path/'runtime')
    remote=eng.save_inbound(_vless(24443,'remote-only',{'deployLocal':False,'deploymentTargets':['node:n1']}))
    local=eng.save_inbound(_vless(24444,'local-only',{'deployLocal':True,'deploymentTargets':['local']}))
    eng.create({'email':'runtime-user','id':'11111111-1111-4111-8111-111111111111','enable':True},[remote['id'],local['id']])
    eng.save_section('hosts',[
        {'inboundId':remote['id'],'runtime':'node:n1','address':'node.example.test','port':24443,'remark':'NODE',
         'security':'same','enable':True},
        {'inboundId':local['id'],'runtime':'local','address':'main.example.test','port':24444,'remark':'LOCAL',
         'security':'same','enable':True},
    ])
    compiled=eng.build_config()
    tags={x['tag'] for x in compiled['inbounds']}
    assert 'remote-only' not in tags and 'local-only' in tags
    ready=eng.links('runtime-user',runtime_ready={'local':{local['id']},'node:n1':{remote['id']}})
    assert {x['runtime'] for x in ready['links']}=={'local','node:n1'}
    node_down=eng.links('runtime-user',runtime_ready={'local':{local['id']},'node:n1':set()})
    assert [x['runtime'] for x in node_down['links']]==['local']
    eng.close();store.close()


def test_agent_managed_tls_is_hash_verified_materialized_and_private(tmp_path):
    store=Store(tmp_path/'node.sqlite3')
    cfg=Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),test_engine=True)
    eng=CoreEngine(cfg,store,tmp_path/'runtime');runtime=NodeRuntime(store,eng,'scope-1')
    cert=b'-----BEGIN CERTIFICATE-----\nNODE\n-----END CERTIFICATE-----\n'
    file_id=hashlib.sha256(b'file-id').hexdigest();digest=hashlib.sha256(cert).hexdigest()
    inbound=_vless(24445,'tls-node')
    inbound['streamSettings']={'network':'tcp','security':'tls','tlsSettings':{'certificates':[{
        'certificateFile':'managed://'+file_id,'keyFile':'managed://'+file_id
    }]}}
    payload={
        'schema':1,'nodeId':'n1','desiredRunning':True,
        'sections':{'outbounds':[{'tag':'direct','protocol':'freedom','settings':{}}],
                    'routing':{'domainStrategy':'AsIs','rules':[]},'dns':{'servers':['1.1.1.1']},
                    'policy':{},'observatory':{},'ipguard':{'mode':'observe'}},
        'assignments':[{'sourceInboundId':1,'inbound':inbound,'clients':[]}],
        'security':{'clients':[]},
        'files':[{'id':file_id,'kind':'certificate','sha256':digest,'data':base64.b64encode(cert).decode()}]
    }
    raw,digest_payload=runtime._canonical(payload)
    runtime._validate_envelope({'revision':1,'hash':digest_payload,'payload':payload})
    mapping=runtime._materialize_managed(runtime._managed_file_bytes(payload),tmp_path/'managed')
    rewritten=runtime._rewrite_managed_refs(inbound,mapping)
    path=Path(rewritten['streamSettings']['tlsSettings']['certificates'][0]['certificateFile'])
    assert path.read_bytes()==cert
    assert os.stat(path).st_mode & 0o077 == 0
    bad=json.loads(json.dumps(payload));bad['files'][0]['sha256']='0'*64
    _,bad_hash=runtime._canonical(bad)
    with pytest.raises(Exception,match='hash/size|hash'):
        runtime._validate_envelope({'revision':2,'hash':bad_hash,'payload':bad})
    eng.close();store.close()


def test_agent_reset_idempotency_history(tmp_path):
    store=Store(tmp_path/'node.sqlite3')
    cfg=Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),test_engine=True)
    eng=CoreEngine(cfg,store,tmp_path/'runtime');runtime=NodeRuntime(store,eng,'scope-reset')
    assert runtime.reset_result('reset-0001','alice') is None
    first=runtime.remember_reset('reset-0001','alice',123,45)
    second=runtime.remember_reset('reset-0001','alice',999,999)
    assert first['cached'] is False
    assert second['cached'] is True and second['up']==123 and second['down']==45
    with pytest.raises(Exception,match='different client'):
        runtime.reset_result('reset-0001','bob')
    eng.close();store.close()


def test_full_hub_backup_restores_node_state_secret_and_inbound_tls(tmp_path):
    data=tmp_path/'data';data.mkdir();db=data/'dark.sqlite3'
    cert=tmp_path/'inbound-cert.pem';key=tmp_path/'inbound-key.pem'
    cert.write_text('CERTIFICATE-DATA');key.write_text('PRIVATE-KEY-DATA')
    inbound=_vless(24446,'backup-tls')
    inbound['streamSettings']={'network':'tcp','security':'tls','tlsSettings':{'certificates':[{
        'certificateFile':str(cert),'keyFile':str(key)
    }]}}
    import sqlite3
    with contextlib.closing(sqlite3.connect(db)) as con, con:
        con.executescript('''
        CREATE TABLE clients(id TEXT PRIMARY KEY);
        CREATE TABLE owners(id TEXT PRIMARY KEY);
        CREATE TABLE api_admins(id TEXT PRIMARY KEY);
        CREATE TABLE core_clients(email TEXT PRIMARY KEY);
        CREATE TABLE core_inbounds(id INTEGER PRIMARY KEY,body TEXT NOT NULL);
        CREATE TABLE remote_node_desired_state(node_id TEXT PRIMARY KEY,revision INTEGER,desired_hash TEXT,desired_json TEXT,
          updated_at REAL,applied_revision INTEGER,applied_hash TEXT,applied_at REAL,last_error TEXT);
        ''')
        con.execute('INSERT INTO core_inbounds VALUES(1,?)',(json.dumps(inbound),))
        con.execute("INSERT INTO remote_node_desired_state VALUES('n1',7,'abc','{}',1,6,'old',1,'')")
        con.commit()
    (data/'secret.key').write_bytes(b's'*44);os.chmod(data/'secret.key',0o600)
    config=tmp_path/'config.json';config.write_text(json.dumps({'public_origin':'http://127.0.0.1:2087','core_autostart':True}))
    archive=tmp_path/'full.darkbackup'
    manifest=create_backup(data,config,archive,PASS)
    assert manifest['schema']==2 and len(manifest['external_files'])==2
    restored_dir=tmp_path/'restored'
    restore_backup(archive,restored_dir,PASS)
    with contextlib.closing(sqlite3.connect(restored_dir/'data/dark.sqlite3')) as con, con:
        body=json.loads(con.execute('SELECT body FROM core_inbounds WHERE id=1').fetchone()[0])
        row=con.execute("SELECT revision,applied_revision FROM remote_node_desired_state WHERE node_id='n1'").fetchone()
    tls=body['streamSettings']['tlsSettings']['certificates'][0]
    assert Path(tls['certificateFile']).read_text()=='CERTIFICATE-DATA'
    assert Path(tls['keyFile']).read_text()=='PRIVATE-KEY-DATA'
    assert row==(7,6)
    assert (restored_dir/'data/secret.key').read_bytes()==b's'*44


def test_node_install_contract_is_agent_only_and_hub_rebuildable():
    root=Path(__file__).resolve().parents[1]
    provision=(root/'tools/provision_node.py').read_text()
    online=(root/'install-node.sh').read_text()
    agent=(root/'backend/node_agent.py').read_text()
    assert "needed_backend=['node_agent.py','node_runtime.py'" in provision
    assert "'smart_routing.py','smart_warp_probe.py'" in provision
    updater=(root/'tools/update_node.py').read_text()
    inspector=(root/'backend/node_updated.py').read_text()
    assert "'smart_routing.py','smart_warp_probe.py'" in updater
    assert "'backend/smart_routing.py','backend/smart_warp_probe.py'" in inspector
    assert "copytree(ROOT/'web'" not in provision and "'server.py'" not in provision and "'auth.py'" not in provision
    assert 'dark-xray-node.service' in provision and 'dark-xray-node-guard.service' in provision and 'dark-xray-node-update.service' in provision
    assert 'DXN1.' in provision and 'PAIR CODE' in online
    assert 'No Web UI / Owner / Finance / Reseller database' in online
    assert '/node/api/v1/state/apply' in agent and '/node/api/v1/update/start' in agent


def test_node_runtime_scope_is_stable_across_token_rotation(tmp_path):
    from node_agent import AgentToken, node_identity
    token_path=tmp_path/'token';token_path.write_text('dkn_'+('A'*60)+'\n');os.chmod(token_path,0o600)
    node_id_path=tmp_path/'node-id';node_id_path.write_text('node-stable-01\n');os.chmod(node_id_path,0o600)
    pair_path=tmp_path/'pair.json';pair_path.write_text('{"pairCode":"DXN1.test"}');os.chmod(pair_path,0o600)
    token=AgentToken(token_path);identity=node_identity(node_id_path)
    token.rotate('dkn_'+('B'*60))
    assert node_identity(node_id_path)==identity=='node-stable-01'
    assert token_path.read_text().strip()=='dkn_'+('B'*60)
    assert not pair_path.exists() and (tmp_path/'pair-consumed').is_file()


def test_hub_desired_state_encrypts_managed_file_payload_at_rest(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'hub.sqlite3');auth=Auth(store,tmp_path/'secret.key');reg=NodeRegistry(store,auth.cipher)
    reg.put('n1','Node 1','https://node.example','dkn_'+('C'*60),True)
    secret=base64.b64encode(b'PRIVATE-NODE-TLS-MATERIAL').decode()
    payload={'schema':1,'files':[{'id':'a'*64,'kind':'private-key','sha256':'b'*64,'data':secret}]}
    reg.set_desired_state('n1',payload)
    with store.lock:raw=store.db.execute("SELECT desired_json FROM remote_node_desired_state WHERE node_id='n1'").fetchone()[0]
    assert secret not in raw and 'data_enc' in raw
    opened=reg.desired_state('n1')['payload']
    assert opened['files'][0]['data']==secret and 'data_enc' not in opened['files'][0]
    store.close()


def test_node_provisioner_accepts_normal_source_ref_and_rejects_only_newlines():
    root=Path(__file__).resolve().parents[1]
    provision=(root/'tools/provision_node.py').read_text()
    assert "or '\\r' in a.source_ref or '\\n' in a.source_ref" in provision
    assert "for c in a.source_ref for c in" not in provision

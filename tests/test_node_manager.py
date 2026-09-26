import base64
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('node_manager_under_test',ROOT/'tools/node_manager.py')
nm=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(nm)


def setup_manager(tmp_path,monkeypatch):
    app=tmp_path/'app';conf=tmp_path/'etc';data=tmp_path/'data'
    app.mkdir();conf.mkdir();data.mkdir();(conf/'tls').mkdir()
    values={
        'APP':app,'CONF':conf,'DATA':data,'CONFIG':conf/'config.json','GUARD':conf/'guard.json',
        'TOKEN':data/'token','NODE_ID':data/'node-id','PAIR':data/'pair.json',
        'PAIR_CONSUMED':data/'pair-consumed','PROFILE':data/'node-profile.json',
        'DB':data/'node.sqlite3','SOURCE':data/'installed-source.json',
    }
    for key,value in values.items():monkeypatch.setattr(nm,key,value)
    monkeypatch.setattr(nm,'account',lambda:(os.getuid(),os.getgid()))
    monkeypatch.setattr(nm,'root',lambda:None)
    monkeypatch.setattr(nm,'open_firewall',lambda port:None)
    monkeypatch.setattr(nm,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='',stderr=''))
    nm.CONFIG.write_text(json.dumps({
        'public_origin':'https://node.example.test:9443','public_address':'203.0.113.5',
        'bind_port':9443,'protected_ports':[22,9443,10085],
        'tls_certificate':str(conf/'tls/cert.pem'),'tls_private_key':str(conf/'tls/key.pem')
    }))
    nm.GUARD.write_text(json.dumps({'protected_ports':[22,9443,10085]}))
    nm.TOKEN.write_text('dkn_'+'A'*48+'\n');nm.NODE_ID.write_text('node-turkey-abcdef\n')
    payload={'schema':1,'nodeId':'node-turkey-abcdef','name':'turkey',
             'origin':'https://node.example.test:9443','token':'dkn_'+'A'*48,
             'dataAddress':'203.0.113.5','priority':100,'failoverEnabled':True}
    nm.PAIR.write_text(json.dumps({**payload,'pairCode':nm.encode_pair(payload)}))
    return nm


def decode(code):
    raw=code[5:];raw+='='*((-len(raw))%4)
    return json.loads(base64.urlsafe_b64decode(raw.encode()))


def test_new_pair_invalidates_old_token_and_preserves_identity(tmp_path,monkeypatch):
    m=setup_manager(tmp_path,monkeypatch)
    old=m.TOKEN.read_text().strip()
    code=m.issue_pair('turkey')
    doc=decode(code)
    assert code.startswith('DXN1.')
    assert doc['nodeId']=='node-turkey-abcdef'
    assert doc['origin']=='https://node.example.test:9443'
    assert doc['token']!=old
    assert m.TOKEN.read_text().strip()==doc['token']
    assert json.loads(m.PAIR.read_text())['pairCode']==code
    assert not m.PAIR_CONSUMED.exists()
    assert json.loads(m.PROFILE.read_text())['name']=='turkey'


def test_managed_state_blocks_pair_reset(tmp_path,monkeypatch):
    m=setup_manager(tmp_path,monkeypatch)
    db=sqlite3.connect(m.DB)
    db.execute('CREATE TABLE core_clients(id INTEGER)')
    db.execute('INSERT INTO core_clients VALUES(1)')
    db.commit();db.close()
    old=m.TOKEN.read_text()
    with pytest.raises(RuntimeError,match='managed state'):
        m.issue_pair('turkey')
    assert m.TOKEN.read_text()==old


def test_change_port_updates_config_guard_and_pair_code(tmp_path,monkeypatch):
    m=setup_manager(tmp_path,monkeypatch)
    code=m.change_port(8443)
    conf=json.loads(m.CONFIG.read_text());guard=json.loads(m.GUARD.read_text());doc=decode(code)
    assert conf['bind_port']==8443
    assert conf['public_origin']=='https://node.example.test:8443'
    assert 8443 in conf['protected_ports'] and 9443 not in conf['protected_ports']
    assert 8443 in guard['protected_ports'] and 9443 not in guard['protected_ports']
    assert doc['origin']=='https://node.example.test:8443'
    assert json.loads(m.PAIR.read_text())['pairCode']==code

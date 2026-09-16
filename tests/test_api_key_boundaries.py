import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth import Auth,digest
from core import Config,CoreEngine
from dark_policy import Store
from manager import Manager
from server import make_app


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime')
    manager=Manager(store,engine)
    auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','OwnerPass88')
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as c:
        r=c.post('/api/auth/login',json={'username':'dark','password':'OwnerPass88'})
        assert r.status_code==200
        c.headers['X-Dark-CSRF']=r.json()['csrf']
        yield store,auth,c
    store.close()


def test_new_robot_key_cannot_receive_key_management(env):
    _,_,c=env
    r=c.post('/api/keys',json={'name':'unsafe','permissions':{'api.manage':'all'},'days':30})
    assert r.status_code==400
    detail=r.json()['detail'].lower()
    assert 'robot keys' in detail and ('key lifecycle' in detail or 'interactive session' in detail)


def test_legacy_robot_key_loses_api_manage_at_authentication(env):
    store,auth,c=env
    raw='dkr_legacy-test-token-that-is-long-enough-for-the-boundary'
    with store.transaction() as db:
        db.execute(
            'INSERT INTO robot_keys(id,digest,admin_id,name,permissions,expires_at,revoked,created_at) VALUES(?,?,?,?,?,?,0,?)',
            ('legacy-key',digest(raw),'dark','legacy',json.dumps({'api.manage':'all','clients.read':'all'}),time.time()+3600,time.time())
        )
    principal=auth.current(None,'Bearer '+raw)
    assert principal.key_id=='legacy-key'
    assert principal.actor.permissions.get('clients.read')=='all'
    assert 'api.manage' not in principal.actor.permissions


def test_legacy_robot_key_cannot_list_or_revoke_keys(env):
    store,_,c=env
    created=c.post('/api/keys',json={'name':'normal','permissions':{'clients.read':'all'},'days':30})
    assert created.status_code==200
    normal_id=created.json()['id']

    raw='dkr_legacy-route-token-that-is-long-enough-for-the-boundary'
    with store.transaction() as db:
        db.execute(
            'INSERT INTO robot_keys(id,digest,admin_id,name,permissions,expires_at,revoked,created_at) VALUES(?,?,?,?,?,?,0,?)',
            ('legacy-route',digest(raw),'dark','legacy-route',json.dumps({'api.manage':'all'}),time.time()+3600,time.time())
        )
    headers={'Authorization':'Bearer '+raw}
    assert c.get('/api/keys',headers=headers).status_code==403
    assert c.delete('/api/keys/'+normal_id,headers=headers).status_code==403

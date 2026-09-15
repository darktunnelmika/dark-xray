from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store
from manager import Manager
from server import make_app


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3');config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key');auth.bootstrap('dark','OwnerPass88')
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as c:
        r=c.post('/api/auth/login',headers={'User-Agent':'DARK Browser A'},json={'username':'dark','password':'OwnerPass88'});assert r.status_code==200
        c.headers['X-Dark-CSRF']=r.json()['csrf'];yield store,engine,manager,auth,c
    store.close()


def test_session_inventory_and_individual_revoke(env):
    store,engine,manager,auth,c=env
    token,p=auth.login('dark','OwnerPass88','','10.0.0.2',3600,'DARK CLI B')
    rows=c.get('/api/auth/sessions').json();assert len(rows)==2 and sum(bool(x['current']) for x in rows)==1
    other=next(x for x in rows if not x['current']);assert other['source']=='10.0.0.2' and other['user_agent']=='DARK CLI B'
    r=c.delete('/api/auth/sessions/'+other['id']);assert r.status_code==200 and not r.json()['current']
    rows=c.get('/api/auth/sessions').json();assert len(rows)==1 and rows[0]['current']


def test_revoke_other_sessions_keeps_current(env):
    store,engine,manager,auth,c=env
    auth.login('dark','OwnerPass88','','10.0.0.2',3600,'Agent B');auth.login('dark','OwnerPass88','','10.0.0.3',3600,'Agent C')
    r=c.post('/api/auth/sessions/revoke-others',json={});assert r.status_code==200 and r.json()['revoked_others']==2
    rows=c.get('/api/auth/sessions').json();assert len(rows)==1 and rows[0]['current']
    assert c.get('/api/me').status_code==200


def test_session_metadata_migration_from_legacy_table(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    with store.transaction() as db:
        db.execute('CREATE TABLE live_sessions(digest TEXT PRIMARY KEY,admin_id TEXT NOT NULL,csrf TEXT NOT NULL,expires_at REAL NOT NULL)')
    auth=Auth(store,tmp_path/'secret.key')
    cols={r[1] for r in store.db.execute('PRAGMA table_info(live_sessions)')}
    assert {'public_id','created_at','source','user_agent'} <= cols
    store.close()

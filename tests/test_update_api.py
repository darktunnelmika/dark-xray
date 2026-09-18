import pytest
from fastapi.testclient import TestClient

import server as server_module
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app


class FakeUpdateBroker:
    def __init__(self,*a,**k):pass
    def status(self):
        return {'ok':True,'broker_ready':True,'state':'idle','current':{'version':'0.9.0-rc7','commit':'a'*40}}
    def check(self,channel,ref=''):
        return {'ok':True,'state':'ready','candidate':{'channel':channel,'ref':ref or 'main','commit':'b'*40,'version':'0.9.0-rc8','ready':True}}
    def start(self,commit):
        return {'ok':True,'state':'queued','job_id':'job1','candidate':{'commit':commit}}


@pytest.fixture
def env(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3')
    cfg=Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),public_address='vpn.test',test_engine=False)
    eng=CoreEngine(cfg,store,tmp_path/'runtime')
    manager=Manager(store,eng)
    auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','Test!OnlyPassword123')
    manager.owner_put(Actor('dark','owner',{}),'dark',name='DARK',allowed=[])
    monkeypatch.setattr(server_module,'UpdateBrokerClient',FakeUpdateBroker)
    with TestClient(make_app(manager,auth,background=False),base_url=cfg.public_origin) as c:
        r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
        assert r.status_code==200
        c.headers['X-Dark-CSRF']=r.json()['csrf']
        yield store,eng,manager,auth,c
    store.close()


def test_owner_can_check_and_start_verified_web_update(env):
    _,_,_,_,c=env
    r=c.get('/api/update/status');assert r.status_code==200 and r.json()['broker_ready'] is True
    r=c.post('/api/update/check',json={'channel':'main','ref':''})
    assert r.status_code==200 and r.json()['candidate']['commit']=='b'*40
    r=c.post('/api/update/start',json={'commit':'b'*40})
    assert r.status_code==202 and r.json()['state']=='queued'


def test_reseller_cannot_access_update_center_api(env):
    _,eng,manager,auth,c=env
    assert c.put('/api/owners/reseller',json={'name':'RESELLER','allowed':[]}).status_code==200
    assert c.post('/api/admins',json={'username':'reseller','password':'AnotherTestOnly123','role':'reseller'}).status_code==200
    token,p=auth.login('reseller','AnotherTestOnly123','','127.0.0.2')
    with TestClient(make_app(manager,auth,background=False),base_url=eng.config.public_origin) as x:
        x.cookies.set('dark_session',token)
        x.headers['X-Dark-CSRF']=p.csrf
        assert x.get('/api/update/status').status_code==403
        assert x.post('/api/update/check',json={'channel':'main','ref':''}).status_code==403
        assert x.post('/api/update/start',json={'commit':'b'*40}).status_code==403

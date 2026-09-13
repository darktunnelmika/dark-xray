import pytest
from fastapi.testclient import TestClient
import nodes as nodes_mod
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app
OWNER=Actor("dark","owner",{})
@pytest.fixture
def env(tmp_path,monkeypatch):
 monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
 store=Store(tmp_path/"d.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),test_engine=True);eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[]);app=make_app(m,auth,background=False)
 with TestClient(app,base_url=cfg.public_origin) as c:
  r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});c.headers['X-Dark-CSRF']=r.json()['csrf'];yield store,eng,app,c
 store.close()

def test_agent_token_hash_and_agent_health(env):
 store,_,_,c=env;r=c.post('/api/node-agent/tokens',json={'name':'central','days':10});assert r.status_code==200;token=r.json()['token'];assert token.startswith('dkn_')
 with store.lock:row=store.db.execute('SELECT digest FROM node_agent_tokens').fetchone();assert token not in row['digest']
 h=c.get('/node/api/health',headers={'authorization':'Bearer '+token});assert h.status_code==200 and h.json()['service']=='DARK XRAY NODE'
 assert c.get('/node/api/health',headers={'authorization':'Bearer dkn_badbadbadbadbadbadbadbadbadbadbadbadbad'}).status_code==401

def test_central_node_token_encrypted_and_probe(env,monkeypatch):
 store,_,app,c=env;token='dkn_'+('A'*60);r=c.post('/api/nodes',json={'id':'de1','name':'Germany','origin':'https://node.example.com','token':token,'enabled':True});assert r.status_code==200,r.text
 with store.lock:enc=store.db.execute('SELECT token_enc FROM remote_nodes WHERE id=?',('de1',)).fetchone()[0];assert token not in enc
 monkeypatch.setattr(app.state.nodes,'_request',lambda node_id,path,method='GET',body=None,timeout=8.0:({'service':'DARK XRAY NODE','core':{'state':'running'},'inbounds':3,'managed_clients':7},21))
 p=c.post('/api/nodes/de1/probe');assert p.status_code==200 and p.json()['latency_ms']==21
 rows=c.get('/api/nodes').json();assert rows[0]['online'] is True and rows[0]['health']['inbounds']==3 and 'token' not in rows[0]

def test_node_url_rejects_private_resolution(env,monkeypatch):
 _,_,_,c=env;monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
 r=c.post('/api/nodes',json={'id':'bad','name':'Bad','origin':'https://bad.example','token':'dkn_'+('B'*60),'enabled':True});assert r.status_code==400

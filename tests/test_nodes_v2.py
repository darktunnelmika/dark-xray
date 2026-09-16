import time
import pytest
from fastapi.testclient import TestClient
import nodes as nodes_mod
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor,PolicyError
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

def test_node_url_rejects_private_and_invalid_ports_and_normalizes_ipv6(env,monkeypatch):
 _,_,_,c=env
 monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
 r=c.post('/api/nodes',json={'id':'bad','name':'Bad','origin':'https://bad.example','token':'dkn_'+('B'*60),'enabled':True});assert r.status_code==400
 monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
 for origin in ('https://node.example.com:99999','https://node.example.com:0'):
  r=c.post('/api/nodes',json={'id':'badport','name':'Bad port','origin':origin,'token':'dkn_'+('B'*60),'enabled':True});assert r.status_code==400,r.text
 monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(10,1,6,'',('2001:4860:4860::8888',8443,0,0))])
 assert nodes_mod.validate_origin('https://[2001:4860:4860::8888]:8443/')=='https://[2001:4860:4860::8888]:8443'


def test_node_request_pins_validated_address_and_preserves_tls_hostname(env,monkeypatch):
 _,_,app,c=env;token='dkn_'+('C'*60)
 assert c.post('/api/nodes',json={'id':'safe','name':'Safe','origin':'https://node.example.com','token':token,'enabled':True}).status_code==200
 dns_calls=[]
 def resolve(*args,**kwargs):
  dns_calls.append((args,kwargs))
  if len(dns_calls)==1:return [(2,1,6,'',('93.184.216.34',443))]
  return [(2,1,6,'',('127.0.0.1',443))]
 monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',resolve)
 captured={}
 class Response:
  status=200
  def read(self,limit):return b'{"service":"DARK XRAY NODE"}'
 class Connection:
  def __init__(self,host,port,pinned_ip,**kw):captured.update(host=host,port=port,pinned_ip=pinned_ip,context=kw.get('context'))
  def request(self,method,path,body=None,headers=None):captured.update(method=method,path=path,headers=headers)
  def getresponse(self):return Response()
  def close(self):pass
 monkeypatch.setattr(nodes_mod,'_PinnedHTTPSConnection',Connection)
 doc,_=app.state.nodes._request('safe','/node/api/health')
 assert doc['service']=='DARK XRAY NODE'
 assert len(dns_calls)==1
 assert captured['host']=='node.example.com' and captured['pinned_ip']=='93.184.216.34' and captured['port']==443
 assert captured['path']=='/node/api/health' and captured['headers']['Authorization']=='Bearer '+token
 assert captured['context'].check_hostname is True and captured['context'].verify_mode!=nodes_mod.ssl.CERT_NONE
 node=app.state.nodes.list()[0];assert node['online'] is True and node['last_latency_ms']>=1 and node['last_error']==''


def test_node_redirect_is_rejected_without_followup_connection(env,monkeypatch):
 _,_,app,c=env;token='dkn_'+('R'*60)
 assert c.post('/api/nodes',json={'id':'redirect','name':'Redirect','origin':'https://node.example.com','token':token,'enabled':True}).status_code==200
 opened=[]
 class Response:
  status=302
  def read(self,limit):return b''
 class Connection:
  def __init__(self,*a,**k):opened.append(a)
  def request(self,*a,**k):pass
  def getresponse(self):return Response()
  def close(self):pass
 monkeypatch.setattr(nodes_mod,'_PinnedHTTPSConnection',Connection)
 with pytest.raises(PolicyError,match='Node HTTP 302'):
  app.state.nodes._request('redirect','/node/api/health')
 assert len(opened)==1
 node=app.state.nodes.list()[0];assert node['online'] is False and 'Node HTTP 302' in node['last_error']


def test_failed_remote_request_is_persisted_and_marks_node_offline(env,monkeypatch):
 _,_,app,c=env;token='dkn_'+('D'*60)
 assert c.post('/api/nodes',json={'id':'fail','name':'Fail','origin':'https://node.example.com','token':token,'enabled':True}).status_code==200
 class Connection:
  def __init__(self,*a,**k):pass
  def request(self,*a,**k):raise OSError('boom')
  def close(self):pass
 monkeypatch.setattr(nodes_mod,'_PinnedHTTPSConnection',Connection)
 with pytest.raises(PolicyError,match='Node connection failed'):
  app.state.nodes._request('fail','/node/api/health')
 node=app.state.nodes.list()[0];assert node['online'] is False and 'Node connection failed' in node['last_error']


def test_connection_identity_change_clears_stale_probe_state_but_name_change_does_not(env):
 store,_,app,c=env;token='dkn_'+('E'*60)
 assert c.post('/api/nodes',json={'id':'edit','name':'Before','origin':'https://node.example.com','token':token,'enabled':True}).status_code==200
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_latency_ms=17,last_error='',last_health=? WHERE id='edit'",(time.time(),'{"service":"DARK XRAY NODE","inbounds":4}'))
 r=c.patch('/api/nodes/edit',json={'name':'Renamed','origin':'https://node.example.com','keep_token':True,'enabled':True});assert r.status_code==200,r.text
 node=app.state.nodes.list()[0];assert node['online'] is True and node['health']['inbounds']==4
 r=c.patch('/api/nodes/edit',json={'name':'Renamed','origin':'https://new-node.example.com','keep_token':True,'enabled':True});assert r.status_code==200,r.text
 node=app.state.nodes.list()[0];assert node['online'] is False and node['last_seen']==0 and node['last_latency_ms']==0 and node['health']=={}


def test_invalid_remote_response_shape_is_recorded(env,monkeypatch):
 _,_,app,c=env;token='dkn_'+('F'*60)
 assert c.post('/api/nodes',json={'id':'shape','name':'Shape','origin':'https://node.example.com','token':token,'enabled':True}).status_code==200
 monkeypatch.setattr(app.state.nodes,'_request',lambda *a,**k:({'not':'a-list'},5))
 with pytest.raises(PolicyError,match='Invalid node inbound response'):
  app.state.nodes.remote_inbounds('shape')
 node=app.state.nodes.list()[0];assert 'Invalid node inbound response' in node['last_error']


def test_agent_can_stage_inbound_without_copying_clients(env):
 store,eng,_,c=env;r=c.post('/api/node-agent/tokens',json={'name':'central-stage','days':10});token=r.json()['token']
 ib={"remark":"REMOTE","listen":"127.0.0.1","port":19831,"protocol":"vless","enable":True,"tag":"remote-stage","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}
 out=c.post('/node/api/inbounds',json=ib,headers={'authorization':'Bearer '+token});assert out.status_code==200,out.text
 assert out.json()['id']==1 and eng.inbound(1)['tag']=='remote-stage'
 assert eng.runtime_state()['dirty'] is True

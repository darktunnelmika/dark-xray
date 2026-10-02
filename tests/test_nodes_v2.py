import time
import pytest
from fastapi.testclient import TestClient
import nodes as nodes_mod
from auth import Auth
from core import Config,CoreEngine,CoreError
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



def test_live_telemetry_freshness_preserves_system_snapshot(env,monkeypatch):
 store,_,app,c=env
 out=c.post('/api/nodes',json={'id':'live1','name':'live1','origin':'https://live1.example.com',
   'token':'dkn_'+('X'*60),'enabled':True,'inboundIds':[]})
 assert out.status_code==200,out.text
 def fake_request(node_id,path,method='GET',body=None,timeout=8.0):
  assert path=='/node/api/health'
  return {'service':'DARK XRAY NODE','agent_only':True,'node_id':node_id,
          'core':{'state':'running','version':'test','dirty':False,'last_error':''},
          'system':{'cpu':12.5,'memory_percent':34.0,'disk_percent':45.0,'uptime':3600,
                    'memory':{'used':340,'total':1000,'percent':34.0},
                    'disk':{'used':450,'total':1000,'free':550,'percent':45.0},
                    'network':{'sent':1000,'recv':2000,'up_bps':128.0,'down_bps':256.0},
                    'connections':{'open':7,'tcp':5,'udp':2,'available':True},
                    'addresses':[{'interface':'eth0','address':'203.0.113.8','family':4}]},
          'inbounds':0,'managed_clients':3},11
 monkeypatch.setattr(app.state.nodes,'_request',fake_request)
 result=app.state.nodes.probe('live1')
 assert result['latency_ms']==11
 fresh={x['id']:x for x in c.get('/api/nodes').json()}['live1']
 assert fresh['telemetry_state']=='fresh' and fresh['telemetry_age_seconds']<=2
 assert fresh['health']['system']['network']['down_bps']==256.0
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='' WHERE id='live1'",(time.time()-30,))
 stale={x['id']:x for x in c.get('/api/nodes').json()}['live1']
 assert stale['telemetry_state']=='stale' and stale['online'] is True and stale['telemetry_age_seconds']>=29
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='' WHERE id='live1'",(time.time()-181,))
 offline={x['id']:x for x in c.get('/api/nodes').json()}['live1']
 assert offline['telemetry_state']=='offline' and offline['online'] is False


def test_node_health_score_alerts_and_capacity_use_fresh_system_telemetry(env):
 store,_,app,c=env
 out=c.post('/api/nodes',json={'id':'health1','name':'health1','origin':'https://health1.example.com',
   'token':'dkn_'+('H'*60),'enabled':True,'inboundIds':[]})
 assert out.status_code==200,out.text
 fresh_health={
   'service':'DARK XRAY NODE','agent_only':True,'version':'0.10.0-rc20',
   'core':{'state':'running','version':'test','dirty':False,'last_error':''},
   'system':{'cpu':40.0,'memory_percent':50.0,'disk_percent':60.0,'uptime':1000,
             'cpu_info':{'logical':4},'loads':[2.0,1.5,1.0],
             'memory':{'used':500,'total':1000,'percent':50.0},
             'disk':{'used':600,'total':1000,'free':400,'percent':60.0}},
   'hub_lease':{'required':True,'valid':True},
   'maintenance':{'statistics_error':'','checkpoint_age_seconds':3.0}}
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_health=? WHERE id='health1'",
             (time.time(),__import__('json').dumps(fresh_health)))
 node={x['id']:x for x in c.get('/api/nodes').json()}['health1']
 ops=node['operational_health']
 assert ops['state']=='healthy' and ops['score']==100 and ops['alerts']==[]
 assert ops['capacity_percent']==46.0 and ops['capacity_state']=='healthy'

 load_warning=__import__('copy').deepcopy(fresh_health)
 load_warning['system']['loads']=[4.2,2.0,1.0]
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_health=? WHERE id='health1'",
             (time.time(),__import__('json').dumps(load_warning)))
 ops={x['id']:x for x in c.get('/api/nodes').json()}['health1']['operational_health']
 assert ops['state']=='warning' and ops['score']==90
 assert ops['alerts'][0]['code']=='load_high' and ops['alerts'][0]['threshold']==100.0

 warning=__import__('copy').deepcopy(fresh_health)
 warning['system']['cpu']=88.0
 warning['system']['disk']['percent']=87.0
 warning['system']['disk_percent']=87.0
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_health=? WHERE id='health1'",
             (time.time(),__import__('json').dumps(warning)))
 ops={x['id']:x for x in c.get('/api/nodes').json()}['health1']['operational_health']
 assert ops['state']=='warning' and ops['score']==80
 assert {x['code'] for x in ops['alerts']}=={'cpu_high','disk_high'}
 assert ops['capacity_percent']>60

 critical=__import__('copy').deepcopy(fresh_health)
 critical['system']['memory']['percent']=97.0
 critical['system']['memory_percent']=97.0
 critical['core']['state']='stopped'
 critical['hub_lease']['valid']=False
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_health=? WHERE id='health1'",
             (time.time(),__import__('json').dumps(critical)))
 ops={x['id']:x for x in c.get('/api/nodes').json()}['health1']['operational_health']
 assert ops['state']=='critical' and ops['score']==16
 assert {'memory_critical','xray_not_running','hub_lease_invalid'} <= {x['code'] for x in ops['alerts']}

 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='' WHERE id='health1'",(time.time()-30,))
 ops={x['id']:x for x in c.get('/api/nodes').json()}['health1']['operational_health']
 assert ops['state']=='warning' and ops['score']==55 and ops['capacity_percent'] is None
 assert ops['alerts'][0]['code']=='telemetry_stale'


def test_node_metric_history_is_bounded_bucketed_and_excludes_tunnel_probes(env):
 store,_,app,c=env
 out=c.post('/api/nodes',json={'id':'history1','name':'history1','origin':'https://history1.example.com',
   'token':'dkn_'+('J'*60),'enabled':True,'inboundIds':[]})
 assert out.status_code==200,out.text
 health={
   'service':'DARK XRAY NODE','agent_only':True,'version':'0.10.0-rc21','managed_clients':4,
   'core':{'state':'running','version':'test','dirty':False,'last_error':''},
   'system':{'cpu':20.0,'memory_percent':30.0,'disk_percent':40.0,'uptime':1000,
             'cpu_info':{'logical':4},'loads':[1.0,0.8,0.5],
             'memory':{'used':300,'total':1000,'percent':30.0},
             'disk':{'used':400,'total':1000,'free':600,'percent':40.0},
             'network':{'sent':1000,'recv':2000,'up_bps':100.0,'down_bps':200.0},
             'connections':{'open':5,'tcp':4,'udp':1,'available':True}},
   'hub_lease':{'required':True,'valid':True},
   'maintenance':{'statistics_error':'','checkpoint_age_seconds':2.0}}
 now=time.time()
 reg=app.state.nodes
 assert reg._record_metric('history1',health,10,captured_at=now-35,min_interval=0) is True
 health['system']['cpu']=40.0;health['system']['network']['down_bps']=400.0
 assert reg._record_metric('history1',health,20,captured_at=now-20,min_interval=0) is True
 health['system']['cpu']=60.0;health['system']['connections']['open']=9
 assert reg._record_metric('history1',health,30,captured_at=now-5,min_interval=0) is True
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_health=?,last_latency_ms=30 WHERE id='history1'",
             (time.time(),__import__('json').dumps(health)))
 live=c.get('/api/nodes/history1/metrics?window=live')
 assert live.status_code==200,live.text
 doc=live.json()
 assert doc['window']=='live' and doc['bucket_seconds']==10
 assert len(doc['points'])==3
 assert doc['points'][-1]['cpu']==60.0 and doc['points'][-1]['connections']==9.0
 assert 'Tunnel/WARP/path health is excluded' in doc['boundary']
 hour=c.get('/api/nodes/history1/metrics?window=1h')
 assert hour.status_code==200 and hour.json()['bucket_seconds']==30
 bad=c.get('/api/nodes/history1/metrics?window=7d')
 assert bad.status_code==422
 with store.lock:
  columns={r[1] for r in store.db.execute('PRAGMA table_info(remote_node_metrics)')}
 assert 'tunnel' not in {x.lower() for x in columns} and 'warp' not in {x.lower() for x in columns}


def test_node_maintenance_preserves_runtime_and_excludes_new_failover_routes(env):
 store,_,app,c=env
 inbound=c.post('/api/inbounds',json=_test_vless('MAINTENANCE',24410,'maintenance')).json()['id']
 _managed_client(c,'maintenance-user',inbound)
 out=c.post('/api/nodes',json={'id':'maint1','name':'Maintenance Node','origin':'https://maint1.example.com',
   'dataAddress':'maint-data.example.com','token':'dkn_'+('Q'*60),'enabled':True,'inboundIds':[inbound]})
 assert out.status_code==200,out.text
 health={'service':'DARK XRAY NODE','agent_only':True,'version':'0.10.0-rc22',
         'core':{'state':'running','version':'test','dirty':False,'last_error':''},
         'system':{'cpu':20.0,'memory_percent':30.0,'disk_percent':40.0,'uptime':1000,
                   'cpu_info':{'logical':4},'loads':[1.0],
                   'memory':{'used':300,'total':1000,'percent':30.0},
                   'disk':{'used':400,'total':1000,'free':600,'percent':40.0}},
         'hub_lease':{'required':True,'valid':True},'maintenance':{'statistics_error':'','checkpoint_age_seconds':2.0}}
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=77,last_sync=? WHERE node_id='maint1' AND local_inbound_id=?",(time.time(),inbound))
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_health=?,last_latency_ms=11 WHERE id='maint1'",
             (time.time(),__import__('json').dumps(health)))
 explicit_host={
   'inboundId':inbound,'runtime':'node:maint1','endpointType':'direct',
   'address':'maint-direct.example.com','port':24410,'remark':'MAINT DIRECT',
   'security':'same','sni':'','host':'','path':'','alpn':'','fingerprint':'','allowInsecure':False,
   'overrideSniFromAddress':False,'keepSniBlank':False,'finalMask':'','mihomoIpVersion':'',
   'excludeFromSubTypes':[],'enable':True}
 hosts=c.put('/api/settings/hosts',json={'value':[explicit_host]})
 assert hosts.status_code==200,hosts.text
 before_links=c.get('/api/clients/maintenance-user/links').json()['engine']['links']
 assert any(x.get('runtime')=='node:maint1' for x in before_links)
 before={x['id']:x for x in c.get('/api/nodes').json()}['maint1']
 assert before['enabled']==1 and before['online'] is True
 assert before['assignments'][0]['failover_ready'] is True
 last_seen=before['last_seen']
 enabled=c.post('/api/nodes/maint1/maintenance',json={'enabled':True,'note':'kernel work'})
 assert enabled.status_code==200,enabled.text
 node=enabled.json()
 assert node['maintenance']==1 and node['maintenance_note']=='kernel work' and node['enabled']==1
 assert node['last_seen']==last_seen and node['health']['core']['state']=='running'
 assert node['assignments'][0]['failover_ready'] is False
 assert node['assignments'][0]['failover_reason']=='node_maintenance'
 assert app.state.nodes.failover_targets('maintenance-user')==[]
 during_links=c.get('/api/clients/maintenance-user/links').json()['engine']['links']
 assert all(x.get('runtime')!='node:maint1' for x in during_links)
 disabled=c.post('/api/nodes/maint1/maintenance',json={'enabled':False,'note':''})
 assert disabled.status_code==200,disabled.text
 node=disabled.json()
 assert node['maintenance']==0 and node['maintenance_since']==0 and node['enabled']==1
 assert node['assignments'][0]['failover_ready'] is True
 assert len(app.state.nodes.failover_targets('maintenance-user'))==1
 after_links=c.get('/api/clients/maintenance-user/links').json()['engine']['links']
 assert any(x.get('runtime')=='node:maint1' for x in after_links)


def test_node_alert_lifecycle_tracks_first_and_last_observation(env):
 store,_,app,c=env
 out=c.post('/api/nodes',json={'id':'alert1','name':'Alert Node','origin':'https://alert1.example.com',
   'token':'dkn_'+('R'*60),'enabled':True,'inboundIds':[]})
 assert out.status_code==200,out.text
 now=time.time()
 health={'service':'DARK XRAY NODE','agent_only':True,'version':'0.10.0-rc22',
         'core':{'state':'running','version':'test','dirty':False,'last_error':''},
         'system':{'cpu':88.0,'memory_percent':30.0,'disk_percent':40.0,'uptime':1000,
                   'cpu_info':{'logical':4},'loads':[1.0],
                   'memory':{'used':300,'total':1000,'percent':30.0},
                   'disk':{'used':400,'total':1000,'free':600,'percent':40.0},
                   'network':{'sent':100,'recv':200,'up_bps':10.0,'down_bps':20.0},
                   'connections':{'open':2,'tcp':2,'udp':0,'available':True}},
         'hub_lease':{'required':True,'valid':True},'maintenance':{'statistics_error':'','checkpoint_age_seconds':2.0}}
 reg=app.state.nodes
 assert reg._record_metric('alert1',health,9,captured_at=now-30,min_interval=0)
 assert reg._record_metric('alert1',health,9,captured_at=now-5,min_interval=0)
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_health=? WHERE id='alert1'",
             (time.time(),__import__('json').dumps(health)))
 node={x['id']:x for x in c.get('/api/nodes').json()}['alert1']
 alert=next(x for x in node['operational_health']['alerts'] if x['code']=='cpu_high')
 assert abs(alert['started_at']-(now-30))<1
 assert abs(alert['last_observed_at']-(now-5))<1
 healthy=__import__('copy').deepcopy(health);healthy['system']['cpu']=20.0
 assert reg._record_metric('alert1',healthy,8,captured_at=now,min_interval=0)
 with store.lock:
  row=store.db.execute("SELECT active FROM remote_node_alerts WHERE node_id='alert1' AND code='cpu_high'").fetchone()
 assert row and row['active']==0

def test_pair_code_is_bootstrap_only_and_rotates_remote_credential(env,monkeypatch,tmp_path):
 # Keep the bootstrap/encryption/duplicate-registration contract, but use the
 # real Agent's pinned guarded handoff instead of a legacy unversioned mock.
 import json
 from test_node_replacement_prepare import candidate,code
 from test_node_installations import http_transport
 from test_node_hub_recovery import TOKEN
 store,_,app,c=env
 with candidate(tmp_path/'agent') as (_,runtime,client,token):
  calls=http_transport(app.state.nodes,client,monkeypatch)
  r=c.post('/api/nodes/pair',json={'code':code()});assert r.status_code==200,r.text
  out=r.json();assert out['paired'] is True and out['pair_code_consumed'] is True and 'token' not in json.dumps(out).lower()
  current=app.state.nodes.get('new-turkey',secret=True)['token']
  assert current!=TOKEN and current==token.token and current.startswith('dkn_')
  assert app.state.nodes.installations.capture('new-turkey')['installation_id']==runtime.installation_id
  assert [(m,p) for m,p,_ in calls if m=='POST']==[('POST','/node/api/v1/replacement/rotate-token')]
  assert calls[-1][0:2]==('GET','/node/api/health')
  assert calls[-1][2]['Authorization']=='Bearer '+current
  with store.lock:stored=store.db.execute("SELECT token_enc FROM remote_nodes WHERE id='new-turkey'").fetchone()[0]
  assert TOKEN not in stored and current not in stored
  repeat=c.post('/api/nodes/pair',json={'code':code()});assert repeat.status_code==409

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


def _test_vless(remark,port,tag):
 return {"remark":remark,"listen":"0.0.0.0","port":port,"protocol":"vless","enable":True,"tag":tag,
         "settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}


def test_node_assignment_sync_sends_only_selected_inbound_and_clients(env,monkeypatch):
 store,eng,app,c=env
 ib_a=_test_vless('NODE A',21001,'node-a')
 ib_a['panelMeta']={'deployLocal':False,'deploymentTargets':['node:tr1'],'tunnelPorts':{'node:tr1':21185}}
 a=c.post('/api/inbounds',json=ib_a).json()['id']
 b=c.post('/api/inbounds',json=_test_vless('LOCAL B',21002,'local-b')).json()['id']
 eng.create({'email':'alice','id':'11111111-1111-4111-8111-111111111111','enable':True},[a])
 eng.create({'email':'bob','id':'22222222-2222-4222-8222-222222222222','enable':True},[b])
 token='dkn_'+('N'*60)
 r=c.post('/api/nodes',json={'id':'tr1','name':'Turkey','origin':'https://node.example.com','token':token,'enabled':True,'inboundIds':[a]})
 assert r.status_code==200,r.text
 assert r.json()['inboundIds']==[a]
 captured={}
 def fake_request(node_id,path,method='GET',body=None,timeout=8.0):
  if path=='/node/api/mirrors/traffic':return {'items':[],'capturedAt':time.time()},11
  if path=='/node/api/mirrors/security':return {'sourceVerified':False,'items':[],'capturedAt':time.time()},12
  captured.update(node_id=node_id,path=path,method=method,body=body,timeout=timeout)
  if path=='/node/api/v1/state/apply':
   return {'service':'DARK XRAY NODE','appliedRevision':body['revision'],'appliedHash':body['hash'],
           'items':[{'sourceInboundId':a,'remoteInboundId':9,'clients':1}],'core':{'state':'running'}},17
  raise AssertionError(path)
 monkeypatch.setattr(app.state.nodes,'_request',fake_request)
 out=c.post('/api/nodes/tr1/sync')
 assert out.status_code==200,out.text
 assert captured['path']=='/node/api/v1/state/apply'
 desired=captured['body']
 assert desired['revision']>=1 and len(desired['hash'])==64
 assignments=desired['payload']['assignments']
 assert [x['sourceInboundId'] for x in assignments]==[a]
 assert assignments[0]['inbound']['tag']=='node-a'
 assert assignments[0]['inbound']['panelMeta']['tunnelPorts']=={'local':21185}
 assert 'deployLocal' not in assignments[0]['inbound']['panelMeta']
 assert 'deploymentTargets' not in assignments[0]['inbound']['panelMeta']
 assert [x['sourceEmail'] for x in assignments[0]['clients']]==['alice']
 assert all(x['sourceEmail']!='bob' for x in assignments[0]['clients'])
 node=c.get('/api/nodes').json()[0]
 assert node['assignments'][0]['remote_inbound_id']==9
 assert node['assignments'][0]['last_error']==''


def test_node_agent_mirror_sync_reconciles_inbound_and_credentials(env,monkeypatch):
 store,eng,_,c=env
 token=c.post('/api/node-agent/tokens',json={'name':'central-mirror','days':10}).json()['token']
 calls=[]
 monkeypatch.setattr(eng,'command',lambda action:(calls.append(action) or {'state':'running','action':action}))
 assignment={
   'sourceInboundId':77,
   'inbound':_test_vless('CENTRAL 77',22077,'central-77'),
   'clients':[{'sourceEmail':'mika','client':{'id':'33333333-3333-4333-8333-333333333333','enable':True}}]
 }
 r=c.post('/node/api/mirrors/sync',json={'assignments':[assignment]},headers={'authorization':'Bearer '+token})
 assert r.status_code==200,r.text
 doc=r.json();assert doc['mirrored']==1 and doc['clients']==1 and doc['core']['action']=='restart' and doc['changed'] is True
 rid=doc['items'][0]['remoteInboundId']
 remote=eng.inbound(rid)
 assert remote['port']==22077 and remote['tag'].startswith('nm-')
 with store.lock:
  row=store.db.execute('SELECT mirror_email FROM node_agent_mirror_clients').fetchone()
 assert row is not None
 mirrored=eng.client_detail(row['mirror_email'])
 assert mirrored['client']['id']=='33333333-3333-4333-8333-333333333333'
 assert mirrored['inboundIds']==[rid]
 # Identical background reconciliation must be a no-op and must not restart Xray.
 same=c.post('/node/api/mirrors/sync',json={'assignments':[assignment]},headers={'authorization':'Bearer '+token})
 assert same.status_code==200 and same.json()['changed'] is False
 assert calls==['restart']
 # Empty desired assignments reconcile/delete only this agent token's mirrors.
 r=c.post('/node/api/mirrors/sync',json={'assignments':[]},headers={'authorization':'Bearer '+token})
 assert r.status_code==200,r.text
 assert r.json()['mirrored']==0 and r.json()['changed'] is True
 assert calls==['restart','restart']
 with pytest.raises(CoreError):
  eng.inbound(rid)
 with store.lock:
  assert store.db.execute('SELECT COUNT(*) FROM node_agent_mirrors').fetchone()[0]==0
  assert store.db.execute('SELECT COUNT(*) FROM node_agent_mirror_clients').fetchone()[0]==0


def _managed_client(c,email,inbound_id,extra=None):
 r=c.post('/api/clients',json={'owner':'dark','client':{'email':email,**(extra or {})},'inboundIds':[inbound_id]})
 assert r.status_code==202,r.text
 return r.json()


def test_remote_traffic_is_baselined_then_counted_once_and_survives_counter_reset(env):
 store,eng,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('TRAFFIC',23001,'traffic-a')).json()['id']
 _managed_client(c,'alice',a)
 token='dkn_'+('T'*60)
 assert c.post('/api/nodes',json={'id':'n1','name':'Node 1','origin':'https://node.example.com','token':token,'enabled':True,'inboundIds':[a]}).status_code==200
 reg=app.state.nodes
 first=reg.apply_traffic_snapshot('n1',[{'sourceEmail':'alice','up':100,'down':50}],captured_at=1000)
 assert first['baselined']==1 and first['charged_bytes']==0
 second=reg.apply_traffic_snapshot('n1',[{'sourceEmail':'alice','up':160,'down':70}],captured_at=1010)
 assert second['charged_up']==60 and second['charged_down']==20 and second['charged_bytes']==80
 same=reg.apply_traffic_snapshot('n1',[{'sourceEmail':'alice','up':160,'down':70}],captured_at=1020)
 assert same['charged_bytes']==0
 reset=reg.apply_traffic_snapshot('n1',[{'sourceEmail':'alice','up':5,'down':7}],captured_at=1030)
 assert reset['counter_resets']==1 and reset['charged_bytes']==12
 with store.lock:
  client=store.db.execute("SELECT used_bytes FROM clients WHERE id='alice'").fetchone()
  node_rows=store.db.execute("SELECT COUNT(*) FROM traffic_ledger WHERE event_id LIKE 'node:%'").fetchone()[0]
  usage=store.db.execute("SELECT raw_up,raw_down,current_up,current_down,seq FROM remote_node_client_usage WHERE node_id='n1' AND client_id='alice'").fetchone()
 assert client['used_bytes']==92 and node_rows==2
 assert tuple(usage)==(5,7,65,27,2)


def test_two_nodes_aggregate_client_usage_without_duplicate_ledger_events(env):
 store,_,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('GLOBAL',23002,'global-a')).json()['id']
 _managed_client(c,'global-user',a)
 for node,ch in [('n1','U'),('n2','V')]:
  assert c.post('/api/nodes',json={'id':node,'name':node,'origin':'https://'+node+'.example.com',
    'token':'dkn_'+(ch*60),'enabled':True,'inboundIds':[a]}).status_code==200
 reg=app.state.nodes
 for node in ('n1','n2'):reg.apply_traffic_snapshot(node,[{'sourceEmail':'global-user','up':0,'down':0}],captured_at=1000)
 reg.apply_traffic_snapshot('n1',[{'sourceEmail':'global-user','up':10,'down':20}],captured_at=1010)
 reg.apply_traffic_snapshot('n2',[{'sourceEmail':'global-user','up':30,'down':40}],captured_at=1010)
 reg.apply_traffic_snapshot('n2',[{'sourceEmail':'global-user','up':30,'down':40}],captured_at=1020)
 with store.lock:
  used=store.db.execute("SELECT used_bytes FROM clients WHERE id='global-user'").fetchone()[0]
  rows=store.db.execute("SELECT COUNT(*),SUM(up_bytes+down_bytes) FROM traffic_ledger WHERE event_id LIKE 'node:%'").fetchone()
 assert used==100 and rows[0]==2 and rows[1]==100


def test_agent_traffic_snapshot_and_reset_are_idempotent(env,monkeypatch):
 store,eng,_,c=env
 token=c.post('/api/node-agent/tokens',json={'name':'central-traffic','days':10}).json()['token']
 monkeypatch.setattr(eng,'command',lambda action:{'state':'running','action':action})
 assignment={'sourceInboundId':88,'inbound':_test_vless('CENTRAL 88',23088,'central-88'),
             'clients':[{'sourceEmail':'mika','client':{'id':'44444444-4444-4444-8444-444444444444','enable':True}}]}
 r=c.post('/node/api/mirrors/sync',json={'assignments':[assignment]},headers={'authorization':'Bearer '+token})
 assert r.status_code==200,r.text
 with store.lock:
  mirror=store.db.execute("SELECT mirror_email FROM node_agent_mirror_clients WHERE source_email='mika'").fetchone()[0]
  store.db.execute('UPDATE core_clients SET up=123,down=45 WHERE email=?',(mirror,))
 snap=c.get('/node/api/mirrors/traffic',headers={'authorization':'Bearer '+token})
 assert snap.status_code==200 and snap.json()['items']==[{'sourceEmail':'mika','up':123,'down':45}]
 body={'sourceEmail':'mika','resetId':'reset-operation-0001'}
 first=c.post('/node/api/mirrors/traffic/reset',json=body,headers={'authorization':'Bearer '+token})
 second=c.post('/node/api/mirrors/traffic/reset',json=body,headers={'authorization':'Bearer '+token})
 assert first.status_code==200 and second.status_code==200
 assert first.json()['up']==123 and first.json()['down']==45 and first.json()['cached'] is False
 assert second.json()['up']==123 and second.json()['down']==45 and second.json()['cached'] is True
 with store.lock:
  row=store.db.execute('SELECT up,down FROM core_clients WHERE email=?',(mirror,)).fetchone()
  resets=store.db.execute('SELECT COUNT(*) FROM node_agent_traffic_resets').fetchone()[0]
 assert tuple(row)==(0,0) and resets==1


def test_remote_usage_participates_in_client_quota_enforcement(env):
 store,eng,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('QUOTA',23101,'quota-a')).json()['id']
 _managed_client(c,'quota-user',a,{'totalGB':50})
 assert c.post('/api/nodes',json={'id':'quota-node','name':'Quota','origin':'https://quota.example.com',
   'token':'dkn_'+('Q'*60),'enabled':True,'inboundIds':[a]}).status_code==200
 reg=app.state.nodes
 reg.apply_traffic_snapshot('quota-node',[{'sourceEmail':'quota-user','up':0,'down':0}],captured_at=1000)
 reg.apply_traffic_snapshot('quota-node',[{'sourceEmail':'quota-user','up':60,'down':0}],captured_at=1010)
 app.state.manager.tick(suppress=False)
 assert eng.client_detail('quota-user')['client']['enable'] is False
 row=c.get('/api/clients/quota-user').json()
 assert row['used_bytes']==60 and 'client_quota' in row['block_reasons']


def test_central_client_reset_reconciles_remote_final_counter_then_zeros_current_usage(env,monkeypatch):
 store,eng,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('RESET',23102,'reset-a')).json()['id']
 _managed_client(c,'reset-user',a)
 assert c.post('/api/nodes',json={'id':'reset-node','name':'Reset','origin':'https://reset.example.com',
   'token':'dkn_'+('Z'*60),'enabled':True,'inboundIds':[a]}).status_code==200
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=9 WHERE node_id='reset-node' AND local_inbound_id=?",(a,))
 reg=app.state.nodes
 reg.apply_traffic_snapshot('reset-node',[{'sourceEmail':'reset-user','up':10,'down':0}],captured_at=1000)
 calls=[]
 def fake_request(node_id,path,method='GET',body=None,timeout=8.0):
  calls.append((node_id,path,body))
  assert path=='/node/api/mirrors/traffic/reset'
  return {'sourceEmail':'reset-user','up':25,'down':5,'capturedAt':1010,'cached':False},8
 monkeypatch.setattr(reg,'_request',fake_request)
 out=c.post('/api/clients/reset-user/action',json={'action':'reset'})
 assert out.status_code==202,out.text
 with store.lock:
  policy=store.db.execute("SELECT used_bytes FROM clients WHERE id='reset-user'").fetchone()[0]
  remote=store.db.execute("SELECT raw_up,raw_down,current_up,current_down FROM remote_node_client_usage WHERE node_id='reset-node' AND client_id='reset-user'").fetchone()
  charged=store.db.execute("SELECT COALESCE(SUM(up_bytes+down_bytes),0) FROM traffic_ledger WHERE event_id LIKE 'node:%'").fetchone()[0]
 assert policy==0 and tuple(remote)==(0,0,0,0)
 assert charged==20
 assert len(calls)==1 and calls[0][2]['sourceEmail']=='reset-user' and len(calls[0][2]['resetId'])>=8


def test_deselected_stale_remote_traffic_is_ignored_so_cleanup_can_converge(env):
 store,_,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('STALE A',23201,'stale-a')).json()['id']
 b=c.post('/api/inbounds',json=_test_vless('KEEP B',23202,'keep-b')).json()['id']
 _managed_client(c,'old-user',a)
 _managed_client(c,'keep-user',b)
 token='dkn_'+('S'*60)
 assert c.post('/api/nodes',json={'id':'stale-node','name':'Stale','origin':'https://stale.example.com',
   'token':token,'enabled':True,'inboundIds':[a,b]}).status_code==200
 reg=app.state.nodes
 reg.apply_traffic_snapshot('stale-node',[
   {'sourceEmail':'old-user','up':0,'down':0},{'sourceEmail':'keep-user','up':0,'down':0}],captured_at=1000)
 # Simulate Central assignment removal before the remote agent has received mirror cleanup.
 assert c.patch('/api/nodes/stale-node',json={'name':'Stale','origin':'https://stale.example.com',
   'keep_token':True,'enabled':True,'inboundIds':[b]}).status_code==200
 out=reg.apply_traffic_snapshot('stale-node',[
   {'sourceEmail':'old-user','up':999,'down':999},{'sourceEmail':'keep-user','up':10,'down':5}],captured_at=1010)
 assert out['ignored_clients']==1 and out['charged_bytes']==15
 with store.lock:
  old=store.db.execute("SELECT used_bytes FROM clients WHERE id='old-user'").fetchone()[0]
  keep=store.db.execute("SELECT used_bytes FROM clients WHERE id='keep-user'").fetchone()[0]
 assert old==0 and keep==15


def test_client_delete_finalizes_remote_traffic_before_tombstone(env,monkeypatch):
 store,eng,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('DELETE',23103,'delete-a')).json()['id']
 _managed_client(c,'delete-user',a)
 assert c.post('/api/nodes',json={'id':'delete-node','name':'Delete','origin':'https://delete.example.com',
   'token':'dkn_'+('Y'*60),'enabled':True,'inboundIds':[a]}).status_code==200
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=12 WHERE node_id='delete-node' AND local_inbound_id=?",(a,))
 reg=app.state.nodes
 reg.apply_traffic_snapshot('delete-node',[{'sourceEmail':'delete-user','up':10,'down':0}],captured_at=1000)
 calls=[]
 def fake_request(node_id,path,method='GET',body=None,timeout=8.0):
  calls.append((node_id,path,body))
  assert path=='/node/api/mirrors/traffic/reset'
  return {'sourceEmail':'delete-user','up':25,'down':5,'capturedAt':1010,'cached':False},7
 monkeypatch.setattr(reg,'_request',fake_request)
 out=c.post('/api/clients/delete-user/action',json={'action':'delete'})
 assert out.status_code==202,out.text
 with store.lock:
  assert store.db.execute("SELECT 1 FROM clients WHERE id='delete-user'").fetchone() is None
  meta=store.db.execute("SELECT state,op_id FROM managed_clients WHERE email='delete-user'").fetchone()
  charged=store.db.execute("SELECT COALESCE(SUM(up_bytes+down_bytes),0) FROM traffic_ledger WHERE event_id LIKE 'node:%'").fetchone()[0]
 assert meta['state']=='deleted' and meta['op_id']==''
 assert charged==20 and len(calls)==1


def test_agent_security_snapshot_maps_mirror_identity_without_raw_hwid(env,monkeypatch):
 store,eng,_,c=env
 token=c.post('/api/node-agent/tokens',json={'name':'central-security','days':10}).json()['token']
 monkeypatch.setattr(eng,'command',lambda action:{'state':'running','action':action})
 assignment={'sourceInboundId':91,'inbound':_test_vless('SEC 91',24091,'sec-91'),
             'clients':[{'sourceEmail':'secure-user','client':{'id':'55555555-5555-4555-8555-555555555555','enable':True}}]}
 r=c.post('/node/api/mirrors/sync',json={'assignments':[assignment]},headers={'authorization':'Bearer '+token})
 assert r.status_code==200,r.text
 with store.transaction() as db:
  mirror=db.execute("SELECT mirror_email FROM node_agent_mirror_clients WHERE source_email='secure-user'").fetchone()[0]
  now=time.time()
  db.execute('INSERT INTO observations(client_id,ip,node,first_seen,last_seen,granted) VALUES(?,?,?,?,?,1)',
             (mirror,'198.51.100.44','local',now-10,now))
  db.execute('INSERT INTO core_devices(email,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?)',
             (mirror,'a'*64,'ios','iphone',now-20,now))
 snap=c.get('/node/api/mirrors/security',headers={'authorization':'Bearer '+token})
 assert snap.status_code==200,snap.text
 item=snap.json()['items'][0]
 assert item['sourceEmail']=='secure-user'
 assert item['ips'][0]['ip']=='198.51.100.44'
 assert item['devices'][0]['digest']=='a'*64
 assert 'hwid' not in snap.text.lower()
 clear=c.post('/node/api/mirrors/security/clear',json={'sourceEmail':'secure-user','kind':'all'},
              headers={'authorization':'Bearer '+token})
 assert clear.status_code==200 and clear.json()['ips']==1 and clear.json()['devices']==1


def test_global_security_reconcile_can_scope_to_one_client(env,monkeypatch):
 store,_,app,c=env
 inbound=c.post('/api/inbounds',json=_test_vless('SCOPED SECURITY',24100,'scoped-security')).json()['id']
 _managed_client(c,'scope-a',inbound,{'limitHwid':1})
 _managed_client(c,'scope-b',inbound,{'limitHwid':1})
 reg=app.state.nodes
 out=reg.reconcile_global_security(local_source_verified=True,client_ids=['scope-a'])
 assert out['clients']==1
 assert [x['client_id'] for x in out['items']]==['scope-a']

 calls=[]
 real=reg.reconcile_global_security
 def wrapped(**kwargs):
  calls.append(kwargs.get('client_ids'))
  return real(**kwargs)
 monkeypatch.setattr(reg,'reconcile_global_security',wrapped)
 r=c.patch('/api/clients/scope-b',json={'client':{'limitHwid':2}})
 assert r.status_code==202,r.text
 assert calls and calls[-1]==['scope-b']


def test_global_ip_guard_aggregates_nodes_and_preserves_block_while_telemetry_stale(env,monkeypatch):
 store,eng,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('GLOBAL IP',24101,'global-ip')).json()['id']
 _managed_client(c,'ip-user',a,{'limitIp':1})
 for node,ch in [('ipn1','I'),('ipn2','J')]:
  r=c.post('/api/nodes',json={'id':node,'name':node,'origin':'https://'+node+'.example.com',
    'token':'dkn_'+(ch*60),'enabled':True,'inboundIds':[a]})
  assert r.status_code==200,r.text
 with store.transaction() as db:
  db.execute('UPDATE remote_node_inbounds SET remote_inbound_id=7 WHERE local_inbound_id=?',(a,))
 for node in ('ipn1','ipn2'):
  desired=c.get('/api/nodes/'+node+'/desired');assert desired.status_code==200,desired.text
 reg=app.state.nodes
 def fake_request(node_id,path,method='GET',body=None,timeout=8.0):
  assert path=='/node/api/mirrors/security'
  ip='203.0.113.10' if node_id=='ipn1' else '203.0.113.11'
  return {'sourceVerified':True,'items':[{'sourceEmail':'ip-user','ips':[{'ip':ip,'firstSeen':1000.0,'lastSeen':1010.0}],'devices':[]}]},5
 monkeypatch.setattr(reg,'_request',fake_request)
 reg.sync_security('ipn1');reg.sync_security('ipn2')
 now=time.time()
 with store.transaction() as db:
  db.execute('UPDATE remote_node_ips SET first_seen=?,last_seen=?',(now-5,now))
 result=reg.reconcile_global_security(local_source_verified=True,now=now)
 item=next(x for x in result['items'] if x['client_id']=='ip-user')
 assert item['ip_enforceable'] is True and item['ip_count']==2 and item['ip_blocked'] is True
 app.state.manager.tick(suppress=False)
 assert eng.client_detail('ip-user')['client']['enable'] is False
 assert 'global_ip_quota' in c.get('/api/clients/ip-user').json()['block_reasons']

 # Telemetry becoming stale must not silently resurrect an already blocked client.
 with store.transaction() as db:
  db.execute("UPDATE remote_node_security_state SET last_sync=?,last_error='sync failed' WHERE node_id='ipn2'",(now,))
  db.execute("DELETE FROM remote_node_ips WHERE node_id='ipn2'")
 stale=reg.reconcile_global_security(local_source_verified=True,now=now+1)
 stale_item=next(x for x in stale['items'] if x['client_id']=='ip-user')
 assert stale_item['ip_enforceable'] is False and stale_item['ip_blocked'] is True

 # Once both nodes are fresh again and the distinct global IP set is within quota,
 # Central clears only the global blocker and Manager re-enables the client.
 with store.transaction() as db:
  db.execute("UPDATE remote_node_security_state SET source_verified=1,last_sync=?,last_error='' WHERE node_id='ipn2'",(now+2,))
  db.execute("UPDATE remote_node_ips SET ip=?,last_seen=? WHERE node_id='ipn1'",('203.0.113.10',now+2))
  db.execute("INSERT INTO remote_node_ips(node_id,client_id,ip,first_seen,last_seen,verified) VALUES(?,?,?,?,?,1)",
             ('ipn2','ip-user','203.0.113.10',now,now+2))
 fresh=reg.reconcile_global_security(local_source_verified=True,now=now+2)
 fresh_item=next(x for x in fresh['items'] if x['client_id']=='ip-user')
 assert fresh_item['ip_count']==1 and fresh_item['ip_blocked'] is False
 app.state.manager.tick(suppress=False)
 assert eng.client_detail('ip-user')['client']['enable'] is True



def test_verified_direct_excess_blocks_even_when_node_tunnel_coverage_is_opaque(env,monkeypatch):
 store,eng,app,c=env
 ib=_test_vless('PARTIAL IP',24109,'partial-ip')
 ib['panelMeta']={'deployLocal':False,'deploymentTargets':['node:partial-n1'],'tunnelPorts':{'node:partial-n1':25109}}
 inbound=c.post('/api/inbounds',json=ib).json()['id']
 _managed_client(c,'partial-user',inbound,{'limitIp':1})
 out=c.post('/api/nodes',json={'id':'partial-n1','name':'partial-n1','origin':'https://partial-n1.example.com',
   'token':'dkn_'+('P'*60),'enabled':True,'inboundIds':[inbound]})
 assert out.status_code==200,out.text
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=71 WHERE node_id='partial-n1' AND local_inbound_id=?",(inbound,))
 # Persist desired state so coverage inspection sees the opaque shadow listener.
 desired=c.get('/api/nodes/partial-n1/desired');assert desired.status_code==200,desired.text
 reg=app.state.nodes
 now=time.time()
 def security(node_id,path,method='GET',body=None,timeout=8.0):
  assert node_id=='partial-n1' and path=='/node/api/mirrors/security'
  return {'sourceVerified':True,'sourceScopeComplete':False,'opaqueTunnelPorts':[25109],
    'items':[{'sourceEmail':'partial-user','ips':[
      {'ip':'203.0.113.91','firstSeen':now-3,'lastSeen':now},
      {'ip':'203.0.113.92','firstSeen':now-2,'lastSeen':now}], 'devices':[]}]},3
 monkeypatch.setattr(reg,'_request',security)
 reg.sync_security('partial-n1')
 result=reg.reconcile_global_security(local_source_verified=False,local_source_complete=False,now=now)
 item=next(x for x in result['items'] if x['client_id']=='partial-user')
 assert item['ip_enforceable'] is True
 assert item['ip_coverage_complete'] is False
 assert item['remote_source_complete'] is False
 assert item['ip_count']==2 and item['ip_blocked'] is True
 # Incomplete coverage cannot clear a proven violation if one trusted IP later disappears.
 with store.transaction() as db:
  db.execute("DELETE FROM remote_node_ips WHERE node_id='partial-n1' AND ip='203.0.113.92'")
 held=reg.reconcile_global_security(local_source_verified=False,local_source_complete=False,now=now+1)
 held_item=next(x for x in held['items'] if x['client_id']=='partial-user')
 assert held_item['ip_count']==1 and held_item['ip_coverage_complete'] is False and held_item['ip_blocked'] is True

def test_global_device_hashes_from_two_nodes_enforce_hwid_limit(env,monkeypatch):
 store,eng,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('GLOBAL DEVICE',24102,'global-device')).json()['id']
 _managed_client(c,'device-user',a,{'limitHwid':1})
 for node,ch,digest in [('dev1','K','b'*64),('dev2','L','c'*64)]:
  r=c.post('/api/nodes',json={'id':node,'name':node,'origin':'https://'+node+'.example.com',
    'token':'dkn_'+(ch*60),'enabled':True,'inboundIds':[a]})
  assert r.status_code==200,r.text
 with store.transaction() as db:
  db.execute('UPDATE remote_node_inbounds SET remote_inbound_id=8 WHERE local_inbound_id=?',(a,))
 reg=app.state.nodes
 def fake_request(node_id,path,method='GET',body=None,timeout=8.0):
  digest='b'*64 if node_id=='dev1' else 'c'*64
  return {'sourceVerified':True,'items':[{'sourceEmail':'device-user','ips':[],
          'devices':[{'digest':digest,'deviceOs':'ios','model':node_id,'firstSeen':1000.0,'lastSeen':1010.0}]}]},4
 monkeypatch.setattr(reg,'_request',fake_request)
 reg.sync_security('dev1');reg.sync_security('dev2')
 result=reg.reconcile_global_security(local_source_verified=True)
 item=next(x for x in result['items'] if x['client_id']=='device-user')
 assert item['device_complete'] is True and item['device_count']==2 and item['device_blocked'] is True
 app.state.manager.tick(suppress=False)
 assert eng.client_detail('device-user')['client']['enable'] is False
 with store.transaction() as db:
  db.execute("UPDATE clients SET global_device_block=0 WHERE id='device-user'")
 detail=c.get('/api/clients/device-user/security-global').json()
 assert detail['device_count']==2 and detail['device_blocked'] is True and len(detail['remote_devices'])==2
 assert all('digest' not in x for x in detail['remote_devices'])
 with store.lock:
  assert store.db.execute("SELECT global_device_block FROM clients WHERE id='device-user'").fetchone()[0]==0


def test_failover_does_not_clone_local_tunnel_endpoint(env):
 store,_,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('TUNNEL FAILOVER',24102,'tunnel-failover')).json()['id']
 _managed_client(c,'tunnel-fail-user',a)
 dep=c.put(f'/api/inbounds/{a}/deployments',json={'local':True,'nodeIds':[],'tunnelPorts':{'local':20443}})
 assert dep.status_code==200,dep.text
 assert c.put('/api/settings/hosts',json={'value':[{
   'inboundId':a,'runtime':'local','endpointType':'tunnel',
   'address':'iran-tunnel.example.com','port':20443,'remark':'TUNNEL ONLY',
   'security':'same','sni':'','host':'','path':'','alpn':'','fingerprint':'','allowInsecure':False,
   'overrideSniFromAddress':False,'keepSniBlank':False,'finalMask':'','mihomoIpVersion':'',
   'excludeFromSubTypes':[],'enable':True}]}).status_code==200
 r=c.post('/api/nodes',json={'id':'edge-tunnel','name':'EDGE TUNNEL',
   'origin':'https://control-edge-tunnel.example.com','dataAddress':'data-edge-tunnel.example.com',
   'priority':10,'failoverEnabled':True,'token':'dkn_'+('N'*60),'enabled':True,'inboundIds':[a]})
 assert r.status_code==200,r.text
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=29 WHERE node_id='edge-tunnel' AND local_inbound_id=?",(a,))
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_latency_ms=13 WHERE id='edge-tunnel'",(time.time(),))
 links=c.get('/api/clients/tunnel-fail-user/links').json()['engine']
 assert [(x['endpointType'],x['runtime']) for x in links['links']]==[('direct','local'),('tunnel','local')]
 assert len(links['failover'])==1
 assert links['failover'][0]['endpointType']=='direct'
 assert 'data-edge-tunnel.example.com' in links['failover'][0]['uri']
 assert 'TUNNEL ONLY' not in links['failover'][0]['remark']
 assert 'EDGE TUNNEL' in links['failover'][0]['remark']
 assert '[edge-tunnel]' not in links['failover'][0]['remark']


def test_failover_subscription_uses_only_healthy_deployed_nodes(env):
 store,_,app,c=env
 a=c.post('/api/inbounds',json=_test_vless('FAILOVER',24103,'failover-a')).json()['id']
 client=_managed_client(c,'fail-user',a)
 assert c.put('/api/settings/hosts',json={'value':[{
   'inboundId':a,'address':'primary-tunnel.example.com','port':20443,'remark':'PRIMARY TUNNEL',
   'security':'same','sni':'','host':'','path':'','alpn':'','fingerprint':'','allowInsecure':False,
   'overrideSniFromAddress':False,'keepSniBlank':False,'finalMask':'','mihomoIpVersion':'',
   'excludeFromSubTypes':[],'enable':True}]}).status_code==200
 r=c.post('/api/nodes',json={'id':'edge1','name':'EDGE ONE','origin':'https://control-edge.example.com',
   'dataAddress':'data-edge.example.com','priority':10,'failoverEnabled':True,
   'token':'dkn_'+('M'*60),'enabled':True,'inboundIds':[a]})
 assert r.status_code==200,r.text
 assert r.json()['data_address']=='data-edge.example.com' and r.json()['priority']==10
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=19 WHERE node_id='edge1' AND local_inbound_id=?",(a,))
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_latency_ms=12 WHERE id='edge1'",(time.time(),))
 targets=app.state.nodes.failover_targets('fail-user')
 assert len(targets)==1 and targets[0]['address']=='data-edge.example.com'
 links=c.get('/api/clients/fail-user/links').json()['engine']
 assert links['links'] and ':20443' in links['links'][0]['uri']
 assert links['failover'] and 'data-edge.example.com' in links['failover'][0]['uri']
 assert ':24103' in links['failover'][0]['uri'] and ':20443' not in links['failover'][0]['uri']
 assert links['failover'][0]['failoverPort']==24103
 sub=c.get(client['subscription_url']+'?format=clash')
 assert sub.status_code==200,sub.text
 text=sub.text
 assert 'DARK FAILOVER' in text and 'data-edge.example.com' in text
 assert '24103' in text and sub.headers['x-dark-failover-nodes']=='1'

 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET last_error='mirror failed' WHERE node_id='edge1' AND local_inbound_id=?",(a,))
 assert app.state.nodes.failover_targets('fail-user')==[]
 orch=c.get('/api/nodes/orchestration').json()
 route=next(x for x in orch['inbounds'] if x['inbound_id']==a)['routes'][0]
 assert route['subscription_reason']=='sync_error' and route['subscription_included'] is False
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET last_error='' WHERE node_id='edge1' AND local_inbound_id=?",(a,))
 assert len(app.state.nodes.failover_targets('fail-user'))==1

 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_error='network down' WHERE id='edge1'")
 assert app.state.nodes.failover_targets('fail-user')==[]
 sub=c.get(client['subscription_url']+'?format=clash')
 assert sub.status_code==200 and 'data-edge.example.com' not in sub.text


def test_node_orchestration_explains_deployment_and_subscription_readiness(env):
 store,_,_,c=env
 a=c.post('/api/inbounds',json=_test_vless('ORCHESTRATE',24104,'orchestrate-a')).json()['id']
 assert c.post('/api/nodes',json={'id':'orch1','name':'ORCH ONE','origin':'https://orch.example.com',
   'dataAddress':'data-orch.example.com','priority':7,'failoverEnabled':True,
   'token':'dkn_'+('N'*60),'enabled':True,'inboundIds':[a]}).status_code==200

 doc=c.get('/api/nodes/orchestration').json()
 row=next(x for x in doc['inbounds'] if x['inbound_id']==a);route=row['routes'][0]
 assert route['deployment_state']=='pending'
 assert route['subscription_reason']=='not_deployed'
 assert route['subscription_included'] is False
 assert route['data_port']==24104

 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=44,last_sync=? WHERE node_id='orch1' AND local_inbound_id=?",(time.time(),a))
 doc=c.get('/api/nodes/orchestration').json();route=next(x for x in doc['inbounds'] if x['inbound_id']==a)['routes'][0]
 assert route['deployment_state']=='deployed'
 assert route['subscription_reason']=='node_offline'

 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='',last_latency_ms=9 WHERE id='orch1'",(time.time(),))
 doc=c.get('/api/nodes/orchestration').json();row=next(x for x in doc['inbounds'] if x['inbound_id']==a);route=row['routes'][0]
 assert route['subscription_included'] is True
 assert route['subscription_reason']=='ready'
 assert row['failover_count']==1
 assert doc['summary']['subscription_routes']>=1

 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET failover_enabled=0 WHERE id='orch1'")
 doc=c.get('/api/nodes/orchestration').json();route=next(x for x in doc['inbounds'] if x['inbound_id']==a)['routes'][0]
 assert route['subscription_included'] is False
 assert route['subscription_reason']=='failover_disabled'


def test_node_data_address_defaults_to_control_hostname(env):
 _,_,_,c=env
 r=c.post('/api/nodes',json={'id':'default-data','name':'Default data',
   'origin':'https://node-data.example.com','token':'dkn_'+('W'*60),'enabled':False,'inboundIds':[]})
 assert r.status_code==200,r.text
 assert r.json()['data_address']=='node-data.example.com'
 assert r.json()['priority']==100 and bool(r.json()['failover_enabled']) is True


def test_global_block_refreshes_all_node_desired_states_and_reports_offline_convergence(env,monkeypatch):
 store,_,app,c=env
 inbound=c.post('/api/inbounds',json=_test_vless('STAGE6 GLOBAL',24155,'stage6-global')).json()['id']
 # This service is Node-only: Hub has no packet source to verify for it.
 with store.transaction() as db:
  row=db.execute('SELECT body FROM core_inbounds WHERE id=?',(inbound,)).fetchone()
  body=__import__('json').loads(row['body']);body['panelMeta']={'deployLocal':False}
  db.execute('UPDATE core_inbounds SET body=? WHERE id=?',(__import__('json').dumps(body),inbound))
 _managed_client(c,'stage6-user',inbound,{'limitIp':1})
 for node,ch in [('stage6-n1','U'),('stage6-n2','V')]:
  out=c.post('/api/nodes',json={'id':node,'name':node,'origin':'https://'+node+'.example.com',
    'token':'dkn_'+(ch*60),'enabled':True,'inboundIds':[inbound]})
  assert out.status_code==200,out.text
 with store.transaction() as db:
  db.execute('UPDATE remote_node_inbounds SET remote_inbound_id=55 WHERE local_inbound_id=?',(inbound,))
 reg=app.state.nodes
 baseline={}
 for node in ('stage6-n1','stage6-n2'):
  state=c.get('/api/nodes/'+node+'/desired').json();baseline[node]=state['revision']
  reg.mark_desired_state(node,state['revision'],state['hash'])
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='' WHERE id='stage6-n1'",(time.time(),))
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='' WHERE id='stage6-n2'",(time.time()-999,))
 def security(node_id,path,method='GET',body=None,timeout=8.0):
  assert path=='/node/api/mirrors/security'
  ip='203.0.113.61' if node_id=='stage6-n1' else '203.0.113.62'
  now=time.time()
  return {'sourceVerified':True,'items':[{'sourceEmail':'stage6-user',
    'ips':[{'ip':ip,'firstSeen':now-2,'lastSeen':now}],'devices':[]}]},3
 monkeypatch.setattr(reg,'_request',security)
 reg.sync_security('stage6-n2')
 result=c.post('/api/nodes/stage6-n1/security')
 assert result.status_code==200,result.text
 refresh=result.json()['global']['desired_state_refresh']
 assert {x['node_id'] for x in refresh['nodes']}=={'stage6-n1','stage6-n2'} and not refresh['errors']
 for node in ('stage6-n1','stage6-n2'):
  state=reg.desired_state(node)
  assert state['revision']>baseline[node] and state['pending'] is True
  policy=next(x for x in state['payload']['security']['clients'] if x['sourceEmail']=='stage6-user')
  assert policy['globalIpBlocked'] is True
 n1=reg.desired_state('stage6-n1');reg.mark_desired_state('stage6-n1',n1['revision'],n1['hash'])
 detail=c.get('/api/clients/stage6-user/security-global').json()['convergence']
 assert detail['authorization_converged'] is False
 states={x['node_id']:x for x in detail['items']}
 assert states['stage6-n1']['status']=='converged' and states['stage6-n1']['authorization_applied'] is True
 assert states['stage6-n2']['status']=='offline_pending' and states['stage6-n2']['authorization_applied'] is False
 assert detail['pending_nodes']==['stage6-n2'] and detail['offline_nodes']==['stage6-n2']
 n2=reg.desired_state('stage6-n2');reg.mark_desired_state('stage6-n2',n2['revision'],n2['hash'])
 with store.transaction() as db:
  db.execute("UPDATE remote_nodes SET last_seen=?,last_error='' WHERE id='stage6-n2'",(time.time(),))
 settled=c.get('/api/clients/stage6-user/security-global').json()['convergence']
 assert settled['authorization_converged'] is True and not settled['pending_nodes'] and not settled['offline_nodes']
 assert settled['boundary'].startswith('authorization convergence')


def test_hub_observe_node_enforce_is_translated_only_for_agent_payload(env):
 store,eng,app,c=env
 guard=c.get('/api/settings/ipguard').json()['value'];guard['mode']='observe';guard['node_mode']='enforce'
 saved=c.put('/api/settings/ipguard',json={'value':guard});assert saved.status_code==200,saved.text
 assert eng.section('ipguard')['mode']=='observe' and eng.section('ipguard')['node_mode']=='enforce'
 inbound=c.post('/api/inbounds',json=_test_vless('NODE GUARD',24156,'node-guard')).json()['id']
 _managed_client(c,'node-guard-user',inbound,{'limitIp':1})
 out=c.post('/api/nodes',json={'id':'guard-node','name':'Guard Node','origin':'https://guard-node.example.com',
   'token':'dkn_'+('W'*60),'enabled':True,'inboundIds':[inbound]})
 assert out.status_code==200,out.text
 with store.transaction() as db:
  db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=56 WHERE node_id='guard-node' AND local_inbound_id=?",(inbound,))
 state=c.get('/api/nodes/guard-node/desired');assert state.status_code==200,state.text
 remote_guard=state.json()['payload']['sections']['ipguard']
 assert remote_guard['mode']=='observe' and 'node_mode' not in remote_guard
 with store.transaction() as db:
  db.execute("INSERT INTO remote_node_security_state(node_id,source_verified,last_sync,last_error) VALUES('guard-node',1,?,'') ON CONFLICT(node_id) DO UPDATE SET source_verified=1,last_sync=excluded.last_sync,last_error=''",(time.time(),))
 state=c.get('/api/nodes/guard-node/desired');assert state.status_code==200,state.text
 remote_guard=state.json()['payload']['sections']['ipguard']
 assert remote_guard['mode']=='enforce' and 'node_mode' not in remote_guard
 assert eng.section('ipguard')['mode']=='observe'
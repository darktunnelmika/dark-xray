import json
import pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app

OWNER=Actor("dark","owner",{})
IB={"remark":"MATRIX API","listen":"127.0.0.1","port":18569,"protocol":"vless","enable":True,"tag":"matrix-api",
    "settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{},
    "panelMeta":{"deployLocal":True,"deploymentTargets":["local"],"tunnelPorts":{"local":11185}}}

@pytest.fixture
def env(tmp_path):
 store=Store(tmp_path/"dark.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True)
 eng=CoreEngine(cfg,store,tmp_path/"runtime");manager=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key")
 auth.bootstrap("dark","Test!OnlyPassword123");manager.owner_put(OWNER,"dark",name="DARK",allowed=[])
 iid=eng.save_inbound(IB)["id"]
 eng.apply=lambda **kwargs:{"state":"running","dirty":False,"last_error":""}
 with TestClient(make_app(manager,auth,background=False),base_url=cfg.public_origin) as c:
  login=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=login.json()["csrf"]
  yield store,eng,c,iid
 store.close()

def test_matrix_api_exposes_direct_and_tunnel_rows(env):
 store,eng,c,iid=env
 r=c.get("/api/traffic-matrix",params={"inboundId":iid});assert r.status_code==200
 rows=r.json()["rows"];assert [(x["accessPath"],x["port"]) for x in rows]==[("direct",18569),("tunnel",11185)]

def test_matrix_api_applies_paths_independently(env):
 store,eng,c,iid=env
 r=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"normal"})
 assert r.status_code==200,r.text
 r=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"tunnel","policy":"adblock"})
 assert r.status_code==200,r.text
 with store.lock:
  rows=[tuple(x) for x in store.db.execute("SELECT access_path,policy FROM traffic_matrix WHERE inbound_id=? ORDER BY access_path",(iid,))]
 assert rows==[("direct","normal"),("tunnel","adblock")]
 routing=eng.routing_for_scope("hub")
 direct=[x for x in routing["rules"] if x.get("inboundTag")==["matrix-api"]]
 tunnel=[x for x in routing["rules"] if x.get("inboundTag")==["dark-tunnel-%s-11185"%iid]]
 assert [x["outboundTag"] for x in direct]==["direct"]
 assert [x["outboundTag"] for x in tunnel]==["block","direct"]

def test_warp_policy_requires_runtime_profile(env):
 store,eng,c,iid=env
 r=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"warp_ai"})
 assert r.status_code==409
 assert "Create WARP" in r.text

def test_custom_policy_delegates_to_advanced_routing(env):
 store,eng,c,iid=env
 r=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"custom"})
 assert r.status_code==200,r.text
 assert not [x for x in eng.routing_for_scope("hub")["rules"] if x.get("ruleTag","").startswith("dark-matrix-")]


def _seed_warp(store):
 profile={"tag":"warp","protocol":"wireguard","settings":{"secretKey":"secret","address":["172.16.0.2/32"],
          "peers":[{"publicKey":"peer","endpoint":"162.159.192.1:2408"}]}}
 with store.transaction() as db:
  db.execute("INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,1)",
             ("hub",json.dumps(profile),"test-device"))

def test_warp_policy_refuses_unverified_path(env,monkeypatch):
 import server
 store,eng,c,iid=env;_seed_warp(store);monkeypatch.setattr(eng,"_binary",lambda:"/bin/true")
 monkeypatch.setattr(server,"probe_outbounds",lambda *a,**k:[{"success":False,"warpVerified":False,"lossPercent":100.0}])
 r=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"warp_ai"})
 assert r.status_code==409
 with store.lock:
  assert store.db.execute("SELECT 1 FROM traffic_matrix WHERE inbound_id=?",(iid,)).fetchone() is None

def test_warp_policy_applies_only_after_verified_probe(env,monkeypatch):
 import server
 store,eng,c,iid=env;_seed_warp(store);monkeypatch.setattr(eng,"_binary",lambda:"/bin/true")
 monkeypatch.setattr(server,"probe_outbounds",lambda *a,**k:[{"success":True,"warpVerified":True,"delayMs":45.0,"lossPercent":0.0,"jitterMs":2.0}])
 r=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"warp_ai"})
 assert r.status_code==200,r.text
 with store.lock:
  row=store.db.execute("SELECT policy FROM traffic_matrix WHERE scope='hub' AND inbound_id=? AND access_path='direct'",(iid,)).fetchone()
 assert row and row["policy"]=="warp_ai"


def _registered_warp(endpoint='162.159.192.1:2408'):
    return {
        'outbound':{'tag':'warp','protocol':'wireguard','settings':{
            'secretKey':'pending-secret','address':['172.16.0.2/32'],
            'peers':[{'publicKey':'peer','endpoint':endpoint}]
        }},
        'deviceId':'pending-device'
    }


def test_warp_registration_is_pending_until_manual_selection(env,monkeypatch):
    import server
    store,eng,c,iid=env
    monkeypatch.setattr(server,'register_cloudflare_warp',lambda **k:_registered_warp())
    r=c.post('/api/traffic-matrix/warp/create',json={'server':'hub'})
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['pendingRegistration'] is True
    assert doc['manualSelectionRequired'] is True
    assert doc['productionTrafficMutation'] is False
    assert doc['applied'] is False
    with store.lock:
        assert store.db.execute("SELECT 1 FROM warp_profiles WHERE scope='hub'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM warp_pending_profiles WHERE scope='hub'").fetchone() is not None
    status=c.get('/api/traffic-matrix/warp',params={'server':'hub'}).json()
    assert status['registered'] is False and status['pendingRegistration'] is True


def test_warp_scan_is_read_only_and_manual_selection_activates_pending(env,monkeypatch):
    import server
    store,eng,c,iid=env
    monkeypatch.setattr(server,'register_cloudflare_warp',lambda **k:_registered_warp())
    assert c.post('/api/traffic-matrix/warp/create',json={'server':'hub'}).status_code==200

    def scan_probe(binary,assets,outbounds,*,tags=None,attempts=1,timeout=5.0,trace=False):
        return [{'tag':tag,'testable':True,'success':True,'delayMs':20.0+i,'lossPercent':0.0,
                 'jitterMs':1.0,'warpVerified':True,
                 'egress':{'ip':'198.51.100.10','country':'DE','colo':'FRA','warp':'on'}}
                for i,tag in enumerate(tags or [])]
    monkeypatch.setattr(server,'probe_outbounds',scan_probe)
    scanned=c.post('/api/traffic-matrix/warp/scan',json={'server':'hub'})
    assert scanned.status_code==200,scanned.text
    body=scanned.json()
    assert body['pendingRegistration'] is True
    assert body['productionTrafficMutation'] is False
    assert len(body['items'])>1
    assert body['items'][0]['ready'] is True
    with store.lock:
        assert store.db.execute("SELECT 1 FROM warp_profiles WHERE scope='hub'").fetchone() is None

    chosen=body['items'][1]['endpoint']
    selected=c.post('/api/traffic-matrix/warp/endpoint',json={'server':'hub','endpoint':chosen})
    assert selected.status_code==200,selected.text
    assert selected.json()['selected']==chosen
    with store.lock:
        active=store.db.execute("SELECT outbound_json FROM warp_profiles WHERE scope='hub'").fetchone()
        pending=store.db.execute("SELECT 1 FROM warp_pending_profiles WHERE scope='hub'").fetchone()
    assert active is not None and pending is None
    assert json.loads(active['outbound_json'])['settings']['peers'][0]['endpoint']==chosen


def test_new_pending_registration_does_not_replace_existing_active_warp(env,monkeypatch):
    import server
    store,eng,c,iid=env
    _seed_warp(store)
    with store.lock:
        before=store.db.execute("SELECT outbound_json FROM warp_profiles WHERE scope='hub'").fetchone()['outbound_json']
    monkeypatch.setattr(server,'register_cloudflare_warp',lambda **k:_registered_warp('162.159.192.5:2408'))
    r=c.post('/api/traffic-matrix/warp/create',json={'server':'hub'})
    assert r.status_code==200,r.text
    assert r.json()['activeEndpoint']=='162.159.192.1:2408'
    with store.lock:
        after=store.db.execute("SELECT outbound_json FROM warp_profiles WHERE scope='hub'").fetchone()['outbound_json']
        assert store.db.execute("SELECT 1 FROM warp_pending_profiles WHERE scope='hub'").fetchone() is not None
    assert after==before

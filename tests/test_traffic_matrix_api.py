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


def _registration_payload():
 return {"outbound":{"tag":"warp","protocol":"wireguard","settings":{"secretKey":"secret",
         "address":["172.16.0.2/32"],"peers":[{"publicKey":"peer","endpoint":"162.159.192.1:2408"}]}},
         "deviceId":"device-v5"}

def test_warp_create_requires_manual_endpoint_selection_before_policy(env,monkeypatch):
 import server
 store,eng,c,iid=env
 monkeypatch.setattr(server,"register_cloudflare_warp",lambda **k:_registration_payload())
 def scan_probe(*args,**kwargs):
  tags=kwargs.get("tags") or []
  return [{"tag":tag,"success":True,"warpVerified":True,"delayMs":20.0+i,"lossPercent":0.0,
           "jitterMs":1.0,"egress":{"country":"DE","colo":"FRA","ip":"198.51.100.1","warp":"on"}}
          for i,tag in enumerate(tags)]
 monkeypatch.setattr(server,"probe_outbounds",scan_probe)
 monkeypatch.setattr(eng,"_binary",lambda:"/bin/true")

 created=c.post("/api/traffic-matrix/warp/create",json={"server":"hub"})
 assert created.status_code==200,created.text
 doc=created.json()
 assert doc["selectionRequired"] is True and doc["selectionConfirmed"] is False
 assert len(doc["items"])>1
 with store.lock:
  row=store.db.execute("SELECT selection_confirmed FROM warp_profiles WHERE scope='hub'").fetchone()
 assert row and row["selection_confirmed"]==0

 status=c.get("/api/traffic-matrix/warp",params={"server":"hub"}).json()
 assert status["state"]=="awaiting_selection"
 assert status["endpoint"]==""
 assert status["candidateEndpoint"]=="162.159.192.1:2408"

 blocked=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"warp_ai"})
 assert blocked.status_code==409
 assert "Select and apply" in blocked.text

 scan=c.post("/api/traffic-matrix/warp/scan",json={"server":"hub"})
 assert scan.status_code==200,scan.text
 assert scan.json()["selectionConfirmed"] is False
 assert not any(x["selected"] for x in scan.json()["items"])

 selected=c.post("/api/traffic-matrix/warp/endpoint",json={"server":"hub","endpoint":"162.159.192.5:2408"})
 assert selected.status_code==200,selected.text
 assert selected.json()["selectionConfirmed"] is True
 with store.lock:
  row=store.db.execute("SELECT selection_confirmed,outbound_json FROM warp_profiles WHERE scope='hub'").fetchone()
 assert row["selection_confirmed"]==1
 assert json.loads(row["outbound_json"])["settings"]["peers"][0]["endpoint"]=="162.159.192.5:2408"

 applied=c.post("/api/traffic-matrix",json={"inboundId":iid,"server":"hub","accessPath":"direct","policy":"warp_ai"})
 assert applied.status_code==200,applied.text


def test_warp_auto_best_is_explicit_opt_in(env,monkeypatch):
 import server
 store,eng,c,iid=env
 monkeypatch.setattr(server,"register_cloudflare_warp",lambda **k:_registration_payload())
 monkeypatch.setattr(eng,"_binary",lambda:"/bin/true")
 def probe(*args,**kwargs):
  tags=kwargs.get("tags") or ["warp"]
  out=[]
  for i,tag in enumerate(tags):
   out.append({"tag":tag,"success":True,"warpVerified":True,
               "delayMs":80.0 if i==0 else 15.0+i,"lossPercent":0.0,
               "jitterMs":2.0,"egress":{"country":"NL","colo":"AMS","ip":"203.0.113.2","warp":"on"}})
  return out
 monkeypatch.setattr(server,"probe_outbounds",probe)
 assert c.post("/api/traffic-matrix/warp/create",json={"server":"hub"}).status_code==200
 auto=c.post("/api/traffic-matrix/warp/auto",json={"server":"hub"})
 assert auto.status_code==200,auto.text
 assert auto.json()["autoSelected"] is True and auto.json()["selectionConfirmed"] is True

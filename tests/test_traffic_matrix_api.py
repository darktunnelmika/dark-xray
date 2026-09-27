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

import pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app

OWNER=Actor("dark","owner",{})
IB={"remark":"CV2","listen":"127.0.0.1","port":19601,"protocol":"vless","enable":True,"tag":"cv2","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}

@pytest.fixture
def env(tmp_path):
 store=Store(tmp_path/"dark.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True)
 eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[])
 with TestClient(make_app(m,auth,background=False),base_url=cfg.public_origin) as c:
  r=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=r.json()["csrf"];yield store,eng,m,c
 store.close()

def seed(c):
 r=c.post("/api/inbounds",json=IB);assert r.status_code==200
 return r.json()["id"]

def test_group_crud_and_assignment(env):
 _,_,_,c=env;i=seed(c)
 assert c.post("/api/groups",json={"owner":"dark","name":"VIP","color":"#22d3ee"}).status_code==200
 r=c.post("/api/clients",json={"owner":"dark","client":{"email":"vip-1","group":"VIP","totalGB":1024},"inboundIds":[i]});assert r.status_code==202
 groups=c.get("/api/groups").json();g=next(x for x in groups if x["name"]=="VIP");assert g["client_count"]==1
 assert c.delete("/api/groups/dark/VIP").status_code==400

def test_bulk_create_adjust_and_inbounds(env):
 _,_,_,c=env;i=seed(c)
 r=c.post("/api/clients/bulk-create",json={"owner":"dark","prefix":"u-","postfix":"","first":1,"quantity":3,"inboundIds":[i],"client":{"totalGB":1073741824,"limitIp":1}});assert r.status_code==200 and r.json()["created"]==3
 emails=["u-1","u-2"]
 r=c.post("/api/clients/bulk-adjust",json={"emails":emails,"add_bytes":1073741824,"add_days":7,"group":"ECO","limit_hwid":2});assert r.status_code==200 and r.json()["changed"]==2
 d=c.get("/api/clients/u-1").json();assert d["client"]["totalGB"]==2147483648 and d["client"]["group"]=="ECO" and d["client"]["limitHwid"]==2
 assert any(x["name"]=="ECO" for x in c.get("/api/groups").json())
 r=c.post("/api/clients/bulk-inbounds",json={"emails":emails,"inboundIds":[i],"mode":"detach"});assert r.status_code==200 and r.json()["changed"]==0

def test_groups_are_owner_scoped(env):
 store,eng,m,c=env;i=seed(c);assert c.put("/api/owners/arda",json={"name":"ARDA","allowed":[i]}).status_code==200
 assert c.post("/api/admins",json={"username":"arda","password":"AnotherTestOnly123","role":"reseller"}).status_code==200
 assert c.post("/api/groups",json={"owner":"dark","name":"OWNER","color":""}).status_code==200
 from auth import Auth
 auth=c.app.state.auth;token,p=auth.login("arda","AnotherTestOnly123","","127.0.0.2")
 with TestClient(make_app(m,auth,background=False),base_url=eng.config.public_origin) as x:
  x.cookies.set("dark_session",token);x.headers["X-Dark-CSRF"]=p.csrf
  assert all(g["owner"]=="arda" for g in x.get("/api/groups").json())

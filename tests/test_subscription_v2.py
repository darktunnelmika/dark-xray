import base64,json,pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app
OWNER=Actor("dark","owner",{})
IB={"remark":"SUBV2","listen":"127.0.0.1","port":19701,"protocol":"vless","enable":True,"tag":"subv2","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}
@pytest.fixture
def env(tmp_path):
 store=Store(tmp_path/"d.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True);eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[])
 with TestClient(make_app(m,auth,background=False),base_url=cfg.public_origin) as c:
  r=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=r.json()["csrf"];ib=c.post("/api/inbounds",json=IB).json();u=c.post("/api/clients",json={"owner":"dark","client":{"email":"sub-v2"},"inboundIds":[ib["id"]]}).json();yield c,u["subscription_url"]
 store.close()

def test_formats_and_headers(env):
 c,url=env;s=c.get("/api/settings/subscription").json()["value"];s.update(default_format="raw",auto_detect=True,profile_title="DARK TEST",profile_url="https://profile.test",announce="hello");assert c.put("/api/settings/subscription",json={"value":s}).status_code==200
 raw=c.get(url);assert raw.status_code==200 and raw.content.startswith(b"vless://") and raw.headers["profile-title"]=="DARK TEST"
 links=c.get('/api/clients/sub-v2/links').json()['engine'];assert set(links['formats'])=={'raw','base64','json','clash'}
 b64=c.get(url+"?format=base64");assert base64.b64decode(b64.content).startswith(b"vless://")
 js=c.get(url+"?format=json");doc=js.json();assert doc["title"]=="DARK TEST" and doc["links"][0]["uri"].startswith("vless://")
 clash=c.get(url+"?format=clash");assert clash.status_code==200 and 'DARK AUTO' in clash.text and 'vless' in clash.text

def test_clash_user_agent_auto_detect(env):
 c,url=env;s=c.get("/api/settings/subscription").json()["value"];s["default_format"]="base64";s["auto_detect"]=True;assert c.put("/api/settings/subscription",json={"value":s}).status_code==200
 r=c.get(url,headers={"user-agent":"mihomo/1.19"});assert r.headers["content-type"].startswith("application/yaml") and 'DARK AUTO' in r.text

def test_invalid_format_is_not_silently_substituted(env):
 c,url=env;assert c.get(url+"?format=singbox").status_code==400

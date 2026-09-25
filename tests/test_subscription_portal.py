import base64,re,json
import pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app

OWNER=Actor("dark","owner",{})
IB={"remark":"PORTAL","listen":"127.0.0.1","port":19871,"protocol":"vless","enable":True,"tag":"portal","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}

@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/"d.sqlite3")
    cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True)
    engine=CoreEngine(cfg,store,tmp_path/"runtime");manager=Manager(store,engine);auth=Auth(store,tmp_path/"secret.key")
    auth.bootstrap("dark","Test!OnlyPassword123");manager.owner_put(OWNER,"dark",name="DARK",allowed=[])
    with TestClient(make_app(manager,auth,background=False),base_url=cfg.public_origin) as client:
        login=client.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"})
        client.headers["X-Dark-CSRF"]=login.json()["csrf"]
        inbound=client.post("/api/inbounds",json=IB).json()
        created=client.post("/api/clients",json={"owner":"dark","client":{"email":"portal-user","totalGB":5*1024**3},"inboundIds":[inbound["id"]]}).json()
        yield client,created["subscription_url"]
    store.close()

def portal_state(html):
    raw=re.search(r'data-state="([A-Za-z0-9_-]+)"',html).group(1)
    raw += "="*((4-len(raw)%4)%4)
    return json.loads(base64.urlsafe_b64decode(raw).decode())

def test_browser_portal_does_not_change_machine_feed(env):
    client,url=env
    page=client.get(url,headers={"accept":"text/html","user-agent":"Mozilla/5.0"})
    assert page.status_code==200 and page.headers["content-type"].startswith("text/html")
    assert "DARK XRAY // SUBSCRIPTION PORTAL" in page.text
    state=portal_state(page.text)
    assert state["status"]=="active"
    assert state["traffic"]["total"]==5*1024**3
    assert state["formats"]["base64"].endswith("?format=base64")

    raw=client.get(url,headers={"accept":"*/*","user-agent":"curl/8"})
    assert raw.status_code==200 and raw.headers["content-type"].startswith("text/plain")
    assert base64.b64decode(raw.content).startswith(b"vless://")

    forced=client.get(url+"?format=base64",headers={"accept":"text/html","user-agent":"Mozilla/5.0"})
    assert forced.status_code==200 and forced.headers["content-type"].startswith("text/plain")

def test_custom_subscription_path_serves_portal_assets(env):
    client,url=env
    sub=client.get("/api/settings/subscription").json()["value"];sub["path"]="/dark-feed"
    assert client.put("/api/settings/subscription",json={"value":sub}).status_code==200
    url=url.replace("/sub/","/dark-feed/")
    page=client.get(url,headers={"accept":"text/html","user-agent":"Mozilla/5.0"})
    assert page.status_code==200 and "/dark-feed/" in page.text
    assert client.get(url+"/portal.css").status_code==200
    assert client.get(url+"/portal.js").status_code==200
    assert client.get(url+"/vendor-qr.js").status_code==200

def test_suspended_service_has_portal_but_machine_feed_stays_blocked(env):
    client,url=env
    assert client.post("/api/clients/portal-user/action",json={"action":"disable"}).status_code==202
    page=client.get(url,headers={"accept":"text/html","user-agent":"Mozilla/5.0"})
    assert page.status_code==200 and portal_state(page.text)["status"]=="suspended"
    assert client.get(url,headers={"accept":"*/*","user-agent":"curl/8"}).status_code==403

def test_hwid_policy_is_not_bypassed_by_browser_portal(env):
    client,url=env
    assert client.patch("/api/clients/portal-user",json={"client":{"limitHwid":1}}).status_code==202
    assert client.get(url,headers={"accept":"text/html","user-agent":"Mozilla/5.0"}).status_code==200
    assert client.get(url+"?format=base64",headers={"accept":"*/*","user-agent":"curl/8"}).status_code==403


@pytest.mark.parametrize("user_agent",[
    "Clash.Meta/1.19","Mihomo/1.19","Hiddify/2.5","v2rayNG/1.10",
    "sing-box/1.12","Shadowrocket/2.2","NekoBox/1.4","Stash/2.6","Surge/5"
])
def test_known_subscription_clients_never_receive_html(env,user_agent):
    client,url=env
    response=client.get(url,headers={"accept":"text/html,*/*","user-agent":user_agent+" Mozilla/5.0"})
    assert response.status_code==200
    assert not response.headers["content-type"].startswith("text/html")

import base64,json,pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app

OWNER=Actor("dark","owner",{})
IB={"remark":"PORTAL","listen":"127.0.0.1","port":19721,"protocol":"vless","enable":True,"tag":"portal","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}

@pytest.fixture
def portal_env(tmp_path):
    store=Store(tmp_path/"d.sqlite3")
    cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True)
    eng=CoreEngine(cfg,store,tmp_path/"runtime");manager=Manager(store,eng)
    auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123")
    manager.owner_put(OWNER,"dark",name="DARK",allowed=[])
    with TestClient(make_app(manager,auth,background=False),base_url=cfg.public_origin) as c:
        login=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"})
        c.headers["X-Dark-CSRF"]=login.json()["csrf"]
        ib=c.post("/api/inbounds",json=IB).json()
        created=c.post("/api/clients",json={"owner":"dark","client":{"email":"portal-user","totalGB":5*1024**3},"inboundIds":[ib["id"]]}).json()
        yield c,created["subscription_url"]
    store.close()

def test_browser_gets_secure_portal(portal_env):
    c,url=portal_env
    r=c.get(url,headers={"accept":"text/html,application/xhtml+xml","user-agent":"Mozilla/5.0 Chrome/154 Safari/537.36"})
    assert r.status_code==200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"]=="no-store"
    assert r.headers["referrer-policy"]=="no-referrer"
    assert r.headers["x-content-type-options"]=="nosniff"
    assert r.headers["x-frame-options"]=="DENY"
    csp=r.headers["content-security-policy"]
    for directive in ("default-src 'self'","img-src 'self' data:","connect-src 'none'","frame-ancestors 'none'","form-action 'none'"):
        assert directive in csp
    assert "SECURE SUBSCRIPTION PORTAL" in r.text
    assert "portal-user" in r.text
    assert "/assets/sub-portal.css" in r.text and "/assets/sub-icons.js" in r.text
    assert "__SUB_DATA__" not in r.text

@pytest.mark.parametrize("ua",[
    "V2Box/4.0","HAPP/3.0","Streisand/1.6","Hiddify/2.5","v2rayN/7.15",
    "sing-box/1.12","Xray/26.3.27","NekoBox/1.3","Shadowrocket/2.2"
])
def test_native_apps_never_receive_portal_html(portal_env,ua):
    c,url=portal_env
    r=c.get(url,headers={"accept":"text/html,*/*","user-agent":ua})
    assert r.status_code==200,(ua,r.text)
    assert not r.headers["content-type"].startswith("text/html"),ua
    decoded=base64.b64decode(r.content)
    assert decoded.startswith(b"vless://"),ua

def test_clash_and_explicit_formats_still_win(portal_env):
    c,url=portal_env
    clash=c.get(url,headers={"accept":"text/html","user-agent":"mihomo/1.19"})
    assert clash.status_code==200 and clash.headers["content-type"].startswith("application/yaml")
    assert "DARK AUTO" in clash.text
    browser={"accept":"text/html","user-agent":"Mozilla/5.0 Chrome/154 Safari/537.36"}
    raw=c.get(url+"?format=raw",headers=browser)
    assert raw.status_code==200 and raw.content.startswith(b"vless://")
    assert not raw.headers["content-type"].startswith("text/html")
    js=c.get(url+"?format=json",headers=browser)
    assert js.status_code==200 and js.headers["content-type"].startswith("application/json")
    assert js.json()["client"]=="portal-user"

def test_portal_json_is_script_safe(portal_env):
    c,url=portal_env
    sub=c.get("/api/settings/subscription").json()["value"]
    sub["announce"]="</script><img src=x onerror=alert(1)>"
    assert c.put("/api/settings/subscription",json={"value":sub}).status_code==200
    r=c.get(url,headers={"accept":"text/html","user-agent":"Mozilla/5.0"})
    assert "</script><img" not in r.text
    assert "\\u003c/script\\u003e" in r.text

def test_portal_assets_are_local_and_available(portal_env):
    c,_=portal_env
    for path,ctype in [
        ("/assets/sub-portal.css","text/css"),
        ("/assets/sub-portal.js","javascript"),
        ("/assets/sub-icons.js","javascript"),
        ("/assets/vendor-qr.js","javascript"),
    ]:
        r=c.get(path)
        assert r.status_code==200,path
        assert ctype in r.headers["content-type"],(path,r.headers.get("content-type"))

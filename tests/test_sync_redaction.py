from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app

OWNER=Actor("dark","owner",{})

def test_sync_redacts_runtime_diagnostics_for_non_owner(tmp_path):
    store=Store(tmp_path/"dark.sqlite3")
    config=Config(xray_binary=str(tmp_path/"missing-xray"),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,tmp_path/"runtime");manager=Manager(store,engine);auth=Auth(store,tmp_path/"secret.key")
    auth.bootstrap("dark","OwnerPass88");manager.owner_put(OWNER,"seller",name="Seller",allowed=[])
    auth.admin_create(OWNER,"seller","SellerPass88","reseller",{"clients.read":"own","clients.ip":"own"})
    with store.transaction() as db:
        db.execute("UPDATE api_admins SET permissions=? WHERE id=?",('{"clients.read":"own","clients.ip":"own","ip.read":"own"}',"seller"))
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as c:
        r=c.post("/api/auth/login",json={"username":"seller","password":"SellerPass88"});assert r.status_code==200
        me=c.get("/api/me").json();assert me["permissions"].get("clients.ip")=="own" and "ip.read" not in me["permissions"]
        sync=c.get("/api/sync");assert sync.status_code==200
        runtime=sync.json()["runtime"]
        assert set(runtime)=={"state","running","dirty","desired_running"}
        assert not ({"pid","last_error","applied_hash","core_binary_present","last_exit_code"}&set(runtime))
        c.post("/api/auth/logout",json={})
        r=c.post("/api/auth/login",json={"username":"dark","password":"OwnerPass88"});assert r.status_code==200
        owner_runtime=c.get("/api/sync").json()["runtime"]
        assert {"pid","last_error","applied_hash","core_binary_present","last_exit_code"} <= set(owner_runtime)
    manager.close();engine.close();store.close()

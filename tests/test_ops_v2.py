import os,pytest
from fastapi.testclient import TestClient
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app
OWNER=Actor("dark","owner",{})
@pytest.fixture
def env(tmp_path):
 store=Store(tmp_path/"d.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),test_engine=True);eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[])
 with TestClient(make_app(m,auth,background=False),base_url=cfg.public_origin) as c:
  r=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=r.json()["csrf"];yield store,eng,c
 store.close()

def test_log_tail_is_local_and_bounded(env):
 _,eng,c=env;p=eng.runtime/"process.log";p.write_text("a\nb\nc\n")
 r=c.get("/api/logs/process?limit=2");assert r.status_code==200 and r.json()["lines"]==["b","c"]
 assert c.get("/api/logs/process?limit=0").status_code==400
 assert c.get("/api/logs/nope").status_code==422

def test_log_symlink_refused(env,tmp_path):
 _,eng,c=env;target=tmp_path/"target";target.write_text("secret");p=eng.runtime/"error.log";p.symlink_to(target)
 assert c.get("/api/logs/error").status_code==409

def test_backup_status_and_download(env):
 _,_,c=env;s=c.get("/api/backup/status");assert s.status_code==200;d=s.json();assert d["restore_isolated"] is True and d["database_bytes"]>0 and d["database_download"]=="/api/backup"
 r=c.get("/api/backup");assert r.status_code==200 and r.headers["content-type"].startswith("application/zip")

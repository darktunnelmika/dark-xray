#!/usr/bin/env python3
from pathlib import Path


def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:120])
    return s.replace(old,new,1)

p=Path('backend/server.py');s=p.read_text()
anchor="""    @app.get('/api/backup')
    def backup(p:Principal=Depends(owner)):
"""
insert="""    @app.get('/api/logs/{kind}')
    def logs(kind:Literal['process','error','access'],limit:int=400,p:Principal=Depends(owner)):
        if not 1<=limit<=2000:raise HTTPException(400,'Log line limit must be 1..2000')
        names={'process':'process.log','error':'error.log','access':'access.log'}
        path=engine.runtime/names[kind]
        if path.is_symlink():raise HTTPException(409,'Runtime log symlink refused')
        if not path.exists():return {'kind':kind,'exists':False,'lines':[]}
        try:
            size=path.stat().st_size
            # Read at most the newest 2 MiB; logs are operational surfaces, not bulk download endpoints.
            with path.open('rb') as f:
                if size>2*1024*1024:f.seek(size-2*1024*1024)
                raw=f.read(2*1024*1024)
            text=raw.decode('utf-8',errors='replace')
            lines=text.splitlines()[-limit:]
            return {'kind':kind,'exists':True,'size':size,'lines':lines}
        except OSError as ex:raise HTTPException(503,'Cannot read local runtime log: '+type(ex).__name__)

    @app.get('/api/backup/status')
    def backup_status(p:Principal=Depends(owner)):
        try:
            dbpath=Path(store.path) if store.path!=':memory:' else None
            database_bytes=dbpath.stat().st_size if dbpath and dbpath.is_file() and not dbpath.is_symlink() else 0
        except OSError:database_bytes=0
        with store.lock:
            managed=store.db.execute("SELECT COUNT(*) FROM managed_clients WHERE state!='deleted'").fetchone()[0]
            audits=store.db.execute('SELECT COUNT(*) FROM live_audit').fetchone()[0]
            groups=store.db.execute('SELECT COUNT(*) FROM client_groups').fetchone()[0] if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_groups'").fetchone() else 0
        return {'database_bytes':database_bytes,'managed_clients':managed,'audit_rows':audits,'groups':groups,
                'database_download':'/api/backup','full_backup_command':'sudo darkxray backup --output /root/dark-full.darkbackup',
                'restore_isolated':True}

"""
if insert not in s:s=once(s,anchor,insert+anchor)
p.write_text(s)

p=Path('tests/run-tests.sh');s=p.read_text();needle="python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml\n"
if 'test_ops_v2.py' not in s:s=s.replace(needle,needle+"python -m pytest tests/test_ops_v2.py -q --junitxml=qa/junit/ops-v2.xml\n")
p.write_text(s)
Path('tests/test_ops_v2.py').write_text('''import os,pytest\nfrom fastapi.testclient import TestClient\nfrom auth import Auth\nfrom core import Config,CoreEngine\nfrom dark_policy import Store,Actor\nfrom manager import Manager\nfrom server import make_app\nOWNER=Actor("dark","owner",{})\n@pytest.fixture\ndef env(tmp_path):\n store=Store(tmp_path/"d.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),test_engine=True);eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[])\n with TestClient(make_app(m,auth,background=False),base_url=cfg.public_origin) as c:\n  r=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=r.json()["csrf"];yield store,eng,c\n store.close()\n\ndef test_log_tail_is_local_and_bounded(env):\n _,eng,c=env;p=eng.runtime/"process.log";p.write_text("a\\nb\\nc\\n")\n r=c.get("/api/logs/process?limit=2");assert r.status_code==200 and r.json()["lines"]==["b","c"]\n assert c.get("/api/logs/process?limit=0").status_code==400\n assert c.get("/api/logs/nope").status_code==422\n\ndef test_log_symlink_refused(env,tmp_path):\n _,eng,c=env;target=tmp_path/"target";target.write_text("secret");p=eng.runtime/"error.log";p.symlink_to(target)\n assert c.get("/api/logs/error").status_code==409\n\ndef test_backup_status_and_download(env):\n _,_,c=env;s=c.get("/api/backup/status");assert s.status_code==200;d=s.json();assert d["restore_isolated"] is True and d["database_bytes"]>0 and d["database_download"]=="/api/backup"\n r=c.get("/api/backup");assert r.status_code==200 and r.headers["content-type"].startswith("application/zip")\n''')
print('Ops V2 backend patch applied')

#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text(encoding='utf-8')
    if new in s:return
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:100]!r}')
    p.write_text(s.replace(old,new,1),encoding='utf-8')

# Runtime RBAC uses clients.ip for IP/device surfaces. ip.read belongs only to
# the separate legacy policy API and must not be offered as a dead panel grant.
once('backend/auth.py',
" 'finance.refund','ip.read','system.read','audit.read','api.manage','inbounds.read'}",
" 'finance.refund','system.read','audit.read','api.manage','inbounds.read'}")
once('backend/auth.py',
"   'owners.read','finance.read','ip.read','system.read','audit.read','api.manage','inbounds.read'},",
"   'owners.read','finance.read','system.read','audit.read','api.manage','inbounds.read'},")
once('backend/auth.py',
" 'readonly':{'clients.read','owners.read','finance.read','ip.read','system.read','audit.read','inbounds.read'},",
" 'readonly':{'clients.read','owners.read','finance.read','system.read','audit.read','inbounds.read'},")
once('backend/auth.py',
"    'clients.credentials','clients.ip','clients.attach','owners.read','finance.read','ip.read','audit.read','api.manage','inbounds.read')},",
"    'clients.credentials','clients.ip','clients.attach','owners.read','finance.read','audit.read','api.manage','inbounds.read')},")

once('web/rbac-v2.js',
" reseller:['clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials','clients.ip','clients.attach','owners.read','finance.read','ip.read','system.read','audit.read','api.manage','inbounds.read'],",
" reseller:['clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials','clients.ip','clients.attach','owners.read','finance.read','system.read','audit.read','api.manage','inbounds.read'],")
once('web/rbac-v2.js',
" readonly:['clients.read','owners.read','finance.read','ip.read','system.read','audit.read','inbounds.read'],",
" readonly:['clients.read','owners.read','finance.read','system.read','audit.read','inbounds.read'],")
once('web/rbac-v2.js',
" reseller:{'clients.read':'own','clients.create':'own','clients.edit':'own','clients.delete':'own','clients.reset':'own','clients.credentials':'own','clients.ip':'own','clients.attach':'own','owners.read':'own','finance.read':'own','ip.read':'own','audit.read':'own','api.manage':'own','inbounds.read':'own'},",
" reseller:{'clients.read':'own','clients.create':'own','clients.edit':'own','clients.delete':'own','clients.reset':'own','clients.credentials':'own','clients.ip':'own','clients.attach':'own','owners.read':'own','finance.read':'own','audit.read':'own','api.manage':'own','inbounds.read':'own'},")
once('web/rbac-v2.js',
"const ORDER=['clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials','clients.ip','clients.attach','owners.read','finance.read','ip.read','system.read','audit.read','api.manage','inbounds.read'];",
"const ORDER=['clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials','clients.ip','clients.attach','owners.read','finance.read','system.read','audit.read','api.manage','inbounds.read'];")

old_sync="""    @app.get('/api/sync')
    def sync(p:Principal=Depends(current)):
        with store.lock:
            rows=[dict(r) for r in store.db.execute('SELECT m.email,m.op,m.state,m.error,m.updated_at,c.owner FROM managed_clients m LEFT JOIN clients c ON c.id=m.email ORDER BY m.updated_at DESC LIMIT 250')]
        return {'last_poll':manager.last_poll,'error':manager.last_error if p.actor.role=='owner' else ('CoreEngine synchronization unavailable' if manager.last_error else ''),
                'writes_enabled':config.writes_enabled,'engine_version':engine.version,'runtime':engine.runtime_state(),
                'items':[r for r in rows if p.actor.role=='owner' or p.actor.can('clients','read',r['owner'])]}
"""
new_sync="""    @app.get('/api/sync')
    def sync(p:Principal=Depends(current)):
        with store.lock:
            rows=[dict(r) for r in store.db.execute('SELECT m.email,m.op,m.state,m.error,m.updated_at,c.owner FROM managed_clients m LEFT JOIN clients c ON c.id=m.email ORDER BY m.updated_at DESC LIMIT 250')]
        runtime=engine.runtime_state()
        if p.actor.role!='owner':
            runtime={k:runtime.get(k) for k in ('state','running','dirty','desired_running')}
        return {'last_poll':manager.last_poll,'error':manager.last_error if p.actor.role=='owner' else ('CoreEngine synchronization unavailable' if manager.last_error else ''),
                'writes_enabled':config.writes_enabled,'engine_version':engine.version,'runtime':runtime,
                'items':[r for r in rows if p.actor.role=='owner' or p.actor.can('clients','read',r['owner'])]}
"""
once('backend/server.py',old_sync,new_sync)

test=Path('tests/test_sync_redaction.py')
test.write_text('''from fastapi.testclient import TestClient\n\nfrom auth import Auth\nfrom core import Config,CoreEngine\nfrom dark_policy import Actor,Store\nfrom manager import Manager\nfrom server import make_app\n\nOWNER=Actor("dark","owner",{})\n\ndef test_sync_redacts_runtime_diagnostics_for_non_owner(tmp_path):\n    store=Store(tmp_path/"dark.sqlite3")\n    config=Config(xray_binary=str(tmp_path/"missing-xray"),xray_assets=str(tmp_path),test_engine=True)\n    engine=CoreEngine(config,store,tmp_path/"runtime");manager=Manager(store,engine);auth=Auth(store,tmp_path/"secret.key")\n    auth.bootstrap("dark","OwnerPass88");manager.owner_put(OWNER,"seller",name="Seller",allowed=[])\n    auth.admin_create(OWNER,"seller","SellerPass88","reseller",{"clients.read":"own","clients.ip":"own"})\n    with store.transaction() as db:\n        db.execute("UPDATE api_admins SET permissions=? WHERE id=?",(\'{"clients.read":"own","clients.ip":"own","ip.read":"own"}\',"seller"))\n    app=make_app(manager,auth,background=False)\n    with TestClient(app,base_url=config.public_origin) as c:\n        r=c.post("/api/auth/login",json={"username":"seller","password":"SellerPass88"});assert r.status_code==200\n        me=c.get("/api/me").json();assert me["permissions"].get("clients.ip")=="own" and "ip.read" not in me["permissions"]\n        sync=c.get("/api/sync");assert sync.status_code==200\n        runtime=sync.json()["runtime"]\n        assert set(runtime)=={"state","running","dirty","desired_running"}\n        assert not ({"pid","last_error","applied_hash","core_binary_present","last_exit_code"}&set(runtime))\n        c.post("/api/auth/logout",json={})\n        r=c.post("/api/auth/login",json={"username":"dark","password":"OwnerPass88"});assert r.status_code==200\n        owner_runtime=c.get("/api/sync").json()["runtime"]\n        assert {"pid","last_error","applied_hash","core_binary_present","last_exit_code"} <= set(owner_runtime)\n    manager.close();engine.close();store.close()\n''',encoding='utf-8')

p=Path('tests/run-tests.sh');s=p.read_text(encoding='utf-8')
line="python -m pytest tests/test_rbac_hardening.py -q --junitxml=qa/junit/rbac-hardening.xml\n"
add=line+"python -m pytest tests/test_sync_redaction.py -q --junitxml=qa/junit/sync-redaction.xml\n"
if 'tests/test_sync_redaction.py' not in s:
    if line not in s:raise SystemExit('run-tests anchor missing')
    p.write_text(s.replace(line,add,1),encoding='utf-8')

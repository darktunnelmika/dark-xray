#!/usr/bin/env python3
from pathlib import Path


def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:120])
    return s.replace(old,new,1)

p=Path('backend/server.py');s=p.read_text()
s=once(s,"from reality_scan import RealityScanError,scan_target,search_targets\n","from reality_scan import RealityScanError,scan_target,search_targets\nfrom nodes import NodeRegistry,token_digest\n")
old="""class RealitySearch(Model):
    targets:list[str]=Field(default_factory=list,max_length=20)


def make_app(manager:Manager,auth:Auth,*,background:bool=True)->FastAPI:
"""
new="""class RealitySearch(Model):
    targets:list[str]=Field(default_factory=list,max_length=20)
class NodeCreate(Model):
    id:str=Field(min_length=1,max_length=128)
    name:str=Field(min_length=1,max_length=128)
    origin:str=Field(min_length=8,max_length=500)
    token:str=Field(min_length=40,max_length=256)
    enabled:bool=True
class NodePatch(Model):
    name:str=Field(min_length=1,max_length=128)
    origin:str=Field(min_length=8,max_length=500)
    token:str|None=Field(default=None,min_length=40,max_length=256)
    keep_token:bool=False
    enabled:bool=True
class NodeTokenCreate(Model):
    name:str=Field(min_length=1,max_length=64)
    days:StrictInt=Field(default=365,ge=1,le=3650)


def make_app(manager:Manager,auth:Auth,*,background:bool=True)->FastAPI:
"""
s=once(s,old,new)
old="""    config=manager.engine.config;store=manager.store;engine=manager.engine
"""
new="""    config=manager.engine.config;store=manager.store;engine=manager.engine;nodes=NodeRegistry(store,auth.cipher)
"""
s=once(s,old,new)
old="""    app.state.manager=manager;app.state.auth=auth;app.state.engine=engine
"""
new="""    app.state.manager=manager;app.state.auth=auth;app.state.engine=engine;app.state.nodes=nodes
"""
s=once(s,old,new)
anchor="""    @app.get('/api/inbounds')
    def inbounds(p:Principal=Depends(current)):
"""
insert="""    def node_agent(request:Request):
        header=request.headers.get('authorization','')
        if not header.startswith('Bearer dkn_') or len(header)>300:raise HTTPException(401,'DARK node token required')
        token=header[7:];now=time.time()
        with store.transaction() as db:
            row=db.execute('SELECT * FROM node_agent_tokens WHERE digest=? AND enabled=1 AND expires_at>?',(token_digest(token),now)).fetchone()
            if not row:raise HTTPException(401,'Node token expired or revoked')
            db.execute('UPDATE node_agent_tokens SET last_used=? WHERE id=?',(now,row['id']))
        return row['id']

    @app.get('/api/node-agent/tokens')
    def node_tokens(p:Principal=Depends(owner)):
        with store.lock:return [dict(r) for r in store.db.execute('SELECT id,name,enabled,expires_at,created_at,last_used FROM node_agent_tokens ORDER BY created_at DESC')]
    @app.post('/api/node-agent/tokens')
    def node_token_create(body:NodeTokenCreate,p:Principal=Depends(owner)):
        token='dkn_'+secrets.token_urlsafe(40);kid=secrets.token_hex(10);now=time.time()
        with store.transaction() as db:db.execute('INSERT INTO node_agent_tokens(id,name,digest,enabled,expires_at,created_at,last_used) VALUES(?,?,?,1,?,?,0)',(kid,body.name,token_digest(token),now+body.days*86400,now))
        manager.audit(p.actor,p.actor.id,'node_token.create',kid)
        return {'id':kid,'token':token,'displayed_once':True,'expires_at':now+body.days*86400}
    @app.delete('/api/node-agent/tokens/{token_id}')
    def node_token_revoke(token_id:str,p:Principal=Depends(owner)):
        with store.transaction() as db:
            cur=db.execute('UPDATE node_agent_tokens SET enabled=0 WHERE id=?',(token_id,))
            if not cur.rowcount:raise HTTPException(404,'Node token not found')
        manager.audit(p.actor,p.actor.id,'node_token.revoke',token_id);return {'revoked':True}

    @app.get('/node/api/health')
    def node_health(token_id:str=Depends(node_agent)):
        system=engine.system();runtime=engine.runtime_state()
        with store.lock:managed=store.db.execute("SELECT COUNT(*) FROM managed_clients WHERE state!='deleted'").fetchone()[0]
        return {'service':'DARK XRAY NODE','version':VERSION,'token_id':token_id,
                'core':{'state':runtime['state'],'version':runtime['version'],'dirty':runtime['dirty'],'last_error':runtime['last_error']},
                'system':{'cpu':system['cpu'],'memory_percent':100*system['mem']['current']/max(1,system['mem']['total']),'uptime':system['uptime']},
                'inbounds':len(engine.inbounds()),'managed_clients':managed,'writes_enabled':config.writes_enabled}
    @app.get('/node/api/inbounds')
    def node_inbounds(token_id:str=Depends(node_agent)):
        keys={'id','remark','protocol','port','listen','enable','tag'};out=[]
        for r in engine.inbounds():
            item={k:v for k,v in r.items() if k in keys};st=r.get('streamSettings',{}) if isinstance(r.get('streamSettings'),dict) else {}
            item['network']=st.get('network','tcp');item['security']=st.get('security','none');out.append(item)
        return out
    @app.post('/node/api/core/{action}')
    def node_core(action:str,token_id:str=Depends(node_agent)):
        writable()
        if action not in {'validate','restart','start','stop'}:raise HTTPException(404,'Unknown node core action')
        return {'engine':engine.command(action),'node_agent':True}

    @app.get('/api/nodes')
    def remote_nodes(p:Principal=Depends(owner)):return nodes.list()
    @app.post('/api/nodes')
    def remote_node_add(body:NodeCreate,p:Principal=Depends(owner)):
        writable();result=nodes.put(body.id,body.name,body.origin,body.token,body.enabled);manager.audit(p.actor,p.actor.id,'node.create',body.id);return result
    @app.patch('/api/nodes/{node_id}')
    def remote_node_edit(node_id:str,body:NodePatch,p:Principal=Depends(owner)):
        writable();token=body.token
        if not token:
            if not body.keep_token:raise HTTPException(400,'Provide a replacement token or keep_token=true')
            token=nodes.get(node_id,secret=True)['token']
        result=nodes.put(node_id,body.name,body.origin,token,body.enabled);manager.audit(p.actor,p.actor.id,'node.update',node_id);return result
    @app.delete('/api/nodes/{node_id}')
    def remote_node_delete(node_id:str,p:Principal=Depends(owner)):
        writable();result=nodes.delete(node_id);manager.audit(p.actor,p.actor.id,'node.delete',node_id);return result
    @app.post('/api/nodes/{node_id}/probe')
    def remote_node_probe(node_id:str,p:Principal=Depends(owner)):
        result=nodes.probe(node_id);manager.audit(p.actor,p.actor.id,'node.probe',node_id);return result
    @app.get('/api/nodes/{node_id}/inbounds')
    def remote_node_inbounds(node_id:str,p:Principal=Depends(owner)):return nodes.remote_inbounds(node_id)
    @app.post('/api/nodes/{node_id}/core/{action}')
    def remote_node_core(node_id:str,action:str,p:Principal=Depends(owner)):
        writable();result=nodes.remote_core(node_id,action);manager.audit(p.actor,p.actor.id,'node.core.'+action,node_id);return result

"""
if insert not in s:s=once(s,anchor,insert+anchor)
p.write_text(s)

# Load Nodes V2 UI assets.
p=Path('web/index.html');s=p.read_text()
s=s.replace('<link rel="stylesheet" href="/assets/ops-v2.css">','<link rel="stylesheet" href="/assets/ops-v2.css"><link rel="stylesheet" href="/assets/nodes-v2.css">')
s=s.replace('<script defer src="/assets/ops-v2.js"></script>','<script defer src="/assets/ops-v2.js"></script><script defer src="/assets/nodes-v2.js"></script>')
p.write_text(s)

p=Path('tests/run-tests.sh');s=p.read_text();needle="python -m pytest tests/test_ops_v2.py -q --junitxml=qa/junit/ops-v2.xml\n"
if 'test_nodes_v2.py' not in s:s=s.replace(needle,needle+"python -m pytest tests/test_nodes_v2.py -q --junitxml=qa/junit/nodes-v2.xml\n")
p.write_text(s)
Path('tests/test_nodes_v2.py').write_text('''import pytest\nfrom fastapi.testclient import TestClient\nimport nodes as nodes_mod\nfrom auth import Auth\nfrom core import Config,CoreEngine\nfrom dark_policy import Store,Actor\nfrom manager import Manager\nfrom server import make_app\nOWNER=Actor("dark","owner",{})\n@pytest.fixture\ndef env(tmp_path,monkeypatch):\n monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])\n store=Store(tmp_path/"d.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),test_engine=True);eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[]);app=make_app(m,auth,background=False)\n with TestClient(app,base_url=cfg.public_origin) as c:\n  r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});c.headers['X-Dark-CSRF']=r.json()['csrf'];yield store,eng,app,c\n store.close()\n\ndef test_agent_token_hash_and_agent_health(env):\n store,_,_,c=env;r=c.post('/api/node-agent/tokens',json={'name':'central','days':10});assert r.status_code==200;token=r.json()['token'];assert token.startswith('dkn_')\n with store.lock:row=store.db.execute('SELECT digest FROM node_agent_tokens').fetchone();assert token not in row['digest']\n h=c.get('/node/api/health',headers={'authorization':'Bearer '+token});assert h.status_code==200 and h.json()['service']=='DARK XRAY NODE'\n assert c.get('/node/api/health',headers={'authorization':'Bearer dkn_badbadbadbadbadbadbadbadbadbadbadbadbad'}).status_code==401\n\ndef test_central_node_token_encrypted_and_probe(env,monkeypatch):\n store,_,app,c=env;token='dkn_'+('A'*60);r=c.post('/api/nodes',json={'id':'de1','name':'Germany','origin':'https://node.example.com','token':token,'enabled':True});assert r.status_code==200,r.text\n with store.lock:enc=store.db.execute('SELECT token_enc FROM remote_nodes WHERE id=?',('de1',)).fetchone()[0];assert token not in enc\n monkeypatch.setattr(app.state.nodes,'_request',lambda node_id,path,method='GET',body=None,timeout=8.0:({'service':'DARK XRAY NODE','core':{'state':'running'},'inbounds':3,'managed_clients':7},21))\n p=c.post('/api/nodes/de1/probe');assert p.status_code==200 and p.json()['latency_ms']==21\n rows=c.get('/api/nodes').json();assert rows[0]['online'] is True and rows[0]['health']['inbounds']==3 and 'token' not in rows[0]\n\ndef test_node_url_rejects_private_resolution(env,monkeypatch):\n _,_,_,c=env;monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])\n r=c.post('/api/nodes',json={'id':'bad','name':'Bad','origin':'https://bad.example','token':'dkn_'+('B'*60),'enabled':True});assert r.status_code==400\n''')
print('Nodes V2 backend patch applied')

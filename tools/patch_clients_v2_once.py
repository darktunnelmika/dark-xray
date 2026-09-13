#!/usr/bin/env python3
from pathlib import Path


def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:120])
    return s.replace(old,new,1)

p=Path('backend/manager.py');s=p.read_text()
old="""            CREATE TABLE IF NOT EXISTS live_orders(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,email TEXT NOT NULL,kind TEXT NOT NULL,
              price INTEGER NOT NULL,at REAL NOT NULL);
            ''')
"""
new="""            CREATE TABLE IF NOT EXISTS live_orders(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,email TEXT NOT NULL,kind TEXT NOT NULL,
              price INTEGER NOT NULL,at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS client_groups(
              owner TEXT NOT NULL,name TEXT NOT NULL,color TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(owner,name));
            CREATE INDEX IF NOT EXISTS client_groups_owner ON client_groups(owner,name);
            ''')
"""
s=once(s,old,new)
anchor="""    def check_inbounds(self, actor: Actor, owner: str, ids: list[int]):
"""
insert="""    @staticmethod
    def _group_name(name: str) -> str:
        if not isinstance(name,str):raise PolicyError('Invalid group name')
        name=name.strip()
        if not 1<=len(name)<=64 or any(ord(ch)<32 or ch in '/\\' for ch in name):raise PolicyError('Invalid group name')
        return name

    def groups(self,actor: Actor) -> list[dict]:
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute('SELECT * FROM client_groups ORDER BY owner,name')]
            clients=[dict(r) for r in self.store.db.execute("SELECT c.id,c.owner,c.used_bytes,m.desired FROM clients c JOIN managed_clients m ON m.email=c.id WHERE m.state!='deleted'")]
        visible=[r for r in rows if actor.can('clients','read',r['owner'])]
        index={(r['owner'],r['name']):r for r in visible}
        for c in clients:
            if not actor.can('clients','read',c['owner']):continue
            try:name=json.loads(c['desired']).get('group','').strip()
            except Exception:name=''
            if name and (c['owner'],name) not in index:
                row={'owner':c['owner'],'name':name,'color':'','created_at':0,'updated_at':0,'implicit':True}
                visible.append(row);index[(c['owner'],name)]=row
        for r in visible:
            members=[]
            for c in clients:
                if c['owner']!=r['owner']:continue
                try:g=json.loads(c['desired']).get('group','').strip()
                except Exception:g=''
                if g==r['name']:members.append(c)
            r['client_count']=len(members);r['used_bytes']=sum(int(x['used_bytes']) for x in members)
            r.setdefault('implicit',False)
        return sorted(visible,key=lambda x:(x['owner'],x['name'].lower()))

    def group_put(self,actor: Actor,owner: str,name: str,color: str='') -> dict:
        actor.require('clients','edit',owner);name=self._group_name(name)
        if not isinstance(color,str) or color and not re.fullmatch(r'#[0-9A-Fa-f]{6}',color):raise PolicyError('Invalid group color')
        now=time.time()
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(owner,)).fetchone():raise PolicyError('Unknown owner')
            db.execute('INSERT INTO client_groups(owner,name,color,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(owner,name) DO UPDATE SET color=excluded.color,updated_at=excluded.updated_at',(owner,name,color,now,now))
        self.audit(actor,owner,'group.save',name)
        return {'owner':owner,'name':name,'color':color}

    def group_delete(self,actor: Actor,owner: str,name: str) -> dict:
        actor.require('clients','edit',owner);name=self._group_name(name)
        with self.store.lock:
            clients=self.store.db.execute("SELECT desired FROM managed_clients m JOIN clients c ON c.id=m.email WHERE c.owner=? AND m.state!='deleted'",(owner,)).fetchall()
        for r in clients:
            try:g=json.loads(r[0]).get('group','').strip()
            except Exception:g=''
            if g==name:raise PolicyError('Move clients out of the group before deleting it')
        with self.store.transaction() as db:db.execute('DELETE FROM client_groups WHERE owner=? AND name=?',(owner,name))
        self.audit(actor,owner,'group.delete',name);return {'deleted':True}

"""
if insert not in s:s=once(s,anchor,insert+anchor)
# Ensure explicit group values create a managed group record for compatibility.
old="""            desired.update(patch);desired['email']=email
            cap=self.profile(row['owner'])['max_client_ips']
"""
new="""            desired.update(patch);desired['email']=email
            if desired.get('group'):
                group=self._group_name(desired['group']);desired['group']=group
                now=time.time()
                with self.store.transaction() as db:db.execute('INSERT OR IGNORE INTO client_groups(owner,name,color,created_at,updated_at) VALUES(?,?,?,?,?)',(row['owner'],group,'',now,now))
            cap=self.profile(row['owner'])['max_client_ips']
"""
s=once(s,old,new)
old="""            data['subId']=secrets.token_hex(16)  # never accept a reseller's public credential
            for k,v in {'flow':'','security':'auto','limitIp':1,'limitHwid':0,'totalGB':0,
"""
new="""            data['subId']=secrets.token_hex(16)  # never accept a reseller's public credential
            if data.get('group'):
                group=self._group_name(data['group']);data['group']=group
                now=time.time()
                with self.store.transaction() as db:db.execute('INSERT OR IGNORE INTO client_groups(owner,name,color,created_at,updated_at) VALUES(?,?,?,?,?)',(owner,group,'',now,now))
            for k,v in {'flow':'','security':'auto','limitIp':1,'limitHwid':0,'totalGB':0,
"""
s=once(s,old,new)
p.write_text(s)

p=Path('backend/server.py');s=p.read_text()
old="""class Bulk(Model):
    emails:list[str]=Field(min_length=1,max_length=100)
    action:Literal['enable','disable','reset','delete']
"""
new="""class Bulk(Model):
    emails:list[str]=Field(min_length=1,max_length=500)
    action:Literal['enable','disable','reset','delete']
class GroupBody(Model):
    owner:str=Field(min_length=1,max_length=128)
    name:str=Field(min_length=1,max_length=64)
    color:str=Field(default='',max_length=16)
class BulkCreate(Model):
    owner:str=Field(min_length=1,max_length=128)
    prefix:str=Field(default='',max_length=64)
    postfix:str=Field(default='',max_length=64)
    first:StrictInt=Field(default=1,ge=0,le=999999)
    quantity:StrictInt=Field(ge=1,le=500)
    inboundIds:list[StrictInt]=Field(min_length=1,max_length=256)
    client:dict[str,Any]=Field(default_factory=dict)
class BulkAdjust(Model):
    emails:list[str]=Field(min_length=1,max_length=500)
    add_bytes:int=Field(default=0,ge=-((1<<63)-1),le=(1<<63)-1)
    add_days:StrictInt=Field(default=0,ge=-36500,le=36500)
    group:str|None=Field(default=None,max_length=64)
    limit_hwid:StrictInt|None=Field(default=None,ge=0,le=1000)
class BulkInbounds(Model):
    emails:list[str]=Field(min_length=1,max_length=500)
    inboundIds:list[StrictInt]=Field(min_length=1,max_length=256)
    mode:Literal['attach','detach']
"""
s=once(s,old,new)
anchor="""    @app.get('/api/clients/{email}')
    def client(email:str,p:Principal=Depends(current)):
"""
insert="""    @app.get('/api/groups')
    def groups(p:Principal=Depends(current)):
        return manager.groups(p.actor)
    @app.post('/api/groups')
    def group_put(body:GroupBody,p:Principal=Depends(current)):
        writable();return manager.group_put(p.actor,body.owner,body.name,body.color)
    @app.delete('/api/groups/{group_owner}/{group_name}')
    def group_delete(group_owner:str,group_name:str,p:Principal=Depends(current)):
        writable();return manager.group_delete(p.actor,group_owner,group_name)

    @app.post('/api/clients/bulk-create')
    def bulk_create(body:BulkCreate,p:Principal=Depends(current)):
        writable();out=[]
        for offset in range(body.quantity):
            email=f'{body.prefix}{body.first+offset}{body.postfix}'.strip().lower()
            payload=dict(body.client);payload['email']=email
            try:out.append({'email':email,'result':manager.create(p.actor,body.owner,payload,body.inboundIds)})
            except (PolicyError,CoreError) as ex:out.append({'email':email,'error':str(ex)[:300]})
        return {'requested':body.quantity,'created':sum('result' in x for x in out),'items':out}

    @app.post('/api/clients/bulk-adjust')
    def bulk_adjust(body:BulkAdjust,p:Principal=Depends(current)):
        writable();out=[];now_ms=int(time.time()*1000)
        for email in dict.fromkeys(body.emails):
            try:
                d=manager.detail(p.actor,email,credentials=False);c=d['client'];patch={}
                if body.add_bytes:
                    current=int(c.get('totalGB',0))
                    if current==0:raise PolicyError('Unlimited quota is unchanged by add-bytes; set a quota explicitly per client')
                    patch['totalGB']=max(0,min((1<<63)-1,current+body.add_bytes))
                if body.add_days:
                    current=int(c.get('expiryTime',0));base=current if current>now_ms else now_ms
                    patch['expiryTime']=max(0,base+body.add_days*86400000)
                if body.group is not None:patch['group']=body.group
                if body.limit_hwid is not None:patch['limitHwid']=body.limit_hwid
                if not patch:raise PolicyError('No bulk adjustment requested')
                out.append({'email':email,'result':manager.update(p.actor,email,patch)})
            except (PolicyError,CoreError) as ex:out.append({'email':email,'error':str(ex)[:300]})
        return {'changed':sum('result' in x for x in out),'items':out}

    @app.post('/api/clients/bulk-inbounds')
    def bulk_inbounds(body:BulkInbounds,p:Principal=Depends(current)):
        writable();out=[]
        for email in dict.fromkeys(body.emails):
            try:
                manager.own_row(p.actor,email,'attach');d=manager.detail(p.actor,email,credentials=False);current=set(d['inboundIds']);change=set(body.inboundIds)
                ids=sorted(current|change) if body.mode=='attach' else sorted(current-change)
                if not ids:raise PolicyError('A client must retain at least one inbound')
                out.append({'email':email,'result':manager.update(p.actor,email,{},ids)})
            except (PolicyError,CoreError) as ex:out.append({'email':email,'error':str(ex)[:300]})
        return {'changed':sum('result' in x for x in out),'items':out}

"""
if insert not in s:s=once(s,anchor,insert+anchor)
p.write_text(s)

p=Path('web/index.html');s=p.read_text()
s=s.replace('<link rel="stylesheet" href="/assets/settings-v2.css">','<link rel="stylesheet" href="/assets/settings-v2.css"><link rel="stylesheet" href="/assets/clients-v2.css">')
s=s.replace('<script defer src="/assets/settings-v2.js"></script>','<script defer src="/assets/settings-v2.js"></script><script defer src="/assets/clients-v2.js"></script>')
p.write_text(s)

p=Path('tests/run-tests.sh');s=p.read_text();needle="python -m pytest tests/test_settings_v2.py -q --junitxml=qa/junit/settings-v2.xml\n"
if 'test_clients_v2.py' not in s:s=s.replace(needle,needle+"python -m pytest tests/test_clients_v2.py -q --junitxml=qa/junit/clients-v2.xml\n")
p.write_text(s)

Path('tests/test_clients_v2.py').write_text('''import pytest\nfrom fastapi.testclient import TestClient\nfrom auth import Auth\nfrom core import Config,CoreEngine\nfrom dark_policy import Store,Actor\nfrom manager import Manager\nfrom server import make_app\n\nOWNER=Actor("dark","owner",{})\nIB={"remark":"CV2","listen":"127.0.0.1","port":19601,"protocol":"vless","enable":True,"tag":"cv2","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}\n\n@pytest.fixture\ndef env(tmp_path):\n store=Store(tmp_path/"dark.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True)\n eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[])\n with TestClient(make_app(m,auth,background=False),base_url=cfg.public_origin) as c:\n  r=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=r.json()["csrf"];yield store,eng,m,c\n store.close()\n\ndef seed(c):\n r=c.post("/api/inbounds",json=IB);assert r.status_code==200\n return r.json()["id"]\n\ndef test_group_crud_and_assignment(env):\n _,_,_,c=env;i=seed(c)\n assert c.post("/api/groups",json={"owner":"dark","name":"VIP","color":"#22d3ee"}).status_code==200\n r=c.post("/api/clients",json={"owner":"dark","client":{"email":"vip-1","group":"VIP","totalGB":1024},"inboundIds":[i]});assert r.status_code==202\n groups=c.get("/api/groups").json();g=next(x for x in groups if x["name"]=="VIP");assert g["client_count"]==1\n assert c.delete("/api/groups/dark/VIP").status_code==400\n\ndef test_bulk_create_adjust_and_inbounds(env):\n _,_,_,c=env;i=seed(c)\n r=c.post("/api/clients/bulk-create",json={"owner":"dark","prefix":"u-","postfix":"","first":1,"quantity":3,"inboundIds":[i],"client":{"totalGB":1073741824,"limitIp":1}});assert r.status_code==200 and r.json()["created"]==3\n emails=["u-1","u-2"]\n r=c.post("/api/clients/bulk-adjust",json={"emails":emails,"add_bytes":1073741824,"add_days":7,"group":"ECO","limit_hwid":2});assert r.status_code==200 and r.json()["changed"]==2\n d=c.get("/api/clients/u-1").json();assert d["client"]["totalGB"]==2147483648 and d["client"]["group"]=="ECO" and d["client"]["limitHwid"]==2\n assert any(x["name"]=="ECO" for x in c.get("/api/groups").json())\n r=c.post("/api/clients/bulk-inbounds",json={"emails":emails,"inboundIds":[i],"mode":"detach"});assert r.status_code==200 and r.json()["changed"]==0\n\ndef test_groups_are_owner_scoped(env):\n store,eng,m,c=env;i=seed(c);assert c.put("/api/owners/arda",json={"name":"ARDA","allowed":[i]}).status_code==200\n assert c.post("/api/admins",json={"username":"arda","password":"AnotherTestOnly123","role":"reseller"}).status_code==200\n assert c.post("/api/groups",json={"owner":"dark","name":"OWNER","color":""}).status_code==200\n from auth import Auth\n auth=c.app.state.auth;token,p=auth.login("arda","AnotherTestOnly123","","127.0.0.2")\n with TestClient(make_app(m,auth,background=False),base_url=eng.config.public_origin) as x:\n  x.cookies.set("dark_session",token);x.headers["X-Dark-CSRF"]=p.csrf\n  assert all(g["owner"]=="arda" for g in x.get("/api/groups").json())\n''')
print('Clients V2 backend patch applied')

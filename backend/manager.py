"""Persistent customer ownership and reconciliation for the local Xray runtime.

Only clients explicitly created/adopted here are controlled. Unknown engine
clients are never claimed by name prefix. CoreEngine shared inbounds are never
switched off by a reseller quota. Side effects happen outside DB transactions.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any

from dark_policy import Actor, Store, PolicyError, PermissionDenied, MAX_INT, integer, NAME_RE
from core import CoreEngine, CoreError, EMAIL_RE

SYSTEM = Actor('_dark_system', 'owner', {})
CLIENT_KEYS = {'id','password','auth','flow','security','reverse','limitIp','limitHwid',
               'totalGB','expiryTime','enable','tgId','subId','group','comment','reset',
               'resetDay','resetCount','resetTraffic','resetTrafficDay','encryption'}

class Manager:
    def __init__(self, store: Store, engine: CoreEngine):
        self.store, self.engine = store, engine
        self.lock = threading.RLock()
        self.last_poll = 0.0
        self.last_error = ''
        self.snapshot: dict[str,dict] = {}
        self.inbound_cache: list[dict] = []
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS live_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS owner_profiles(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,allowed TEXT NOT NULL DEFAULT '[]',
              prefix TEXT NOT NULL DEFAULT '',max_client_ips INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS managed_clients(
              email TEXT PRIMARY KEY,desired TEXT NOT NULL,inbounds TEXT NOT NULL,
              public_token TEXT UNIQUE NOT NULL,op TEXT NOT NULL DEFAULT 'upsert',
              state TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '',
              last_up INTEGER NOT NULL DEFAULT 0,last_down INTEGER NOT NULL DEFAULT 0,
              initialized INTEGER NOT NULL DEFAULT 0,seq INTEGER NOT NULL DEFAULT 0,
              expected_enable INTEGER,external_disabled INTEGER NOT NULL DEFAULT 0,
              retry_at REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,owner TEXT NOT NULL,
              action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL,at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS live_audit_owner ON live_audit(owner,id);
            CREATE TABLE IF NOT EXISTS client_cycles(
              email TEXT PRIMARY KEY,days INTEGER NOT NULL,next_at REAL NOT NULL,
              completed INTEGER NOT NULL DEFAULT 0,max_resets INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS live_orders(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,email TEXT NOT NULL,kind TEXT NOT NULL,
              price INTEGER NOT NULL,at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS client_groups(
              owner TEXT NOT NULL,name TEXT NOT NULL,color TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(owner,name));
            CREATE INDEX IF NOT EXISTS client_groups_owner ON client_groups(owner,name);
            ''')

    def audit(self, actor: Actor, owner: str, action: str, target: str, detail: str = ''):
        # Caller-controlled payloads/credentials are never copied into the audit log.
        with self.store.transaction() as db:
            db.execute('INSERT INTO live_audit(actor,owner,action,target,detail,at) VALUES(?,?,?,?,?,?)',
                       (actor.id,owner,action,target,detail[:500],time.time()))

    def own_row(self, actor: Actor, email: str, action: str = 'read') -> dict:
        if not EMAIL_RE.fullmatch(email): raise PolicyError('Invalid client identity')
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM clients WHERE id=?',(email,)).fetchone()
            if not row: raise PolicyError('Managed client not found')
            actor.require('clients',action,row['owner'])
            return dict(row)

    def profile(self, owner: str) -> dict:
        with self.store.lock:
            r = self.store.db.execute('SELECT * FROM owner_profiles WHERE id=?',(owner,)).fetchone()
        if not r: raise PolicyError('Owner profile not configured')
        return dict(r) | {'allowed':json.loads(r['allowed'])}

    def owner_put(self, actor: Actor, owner: str, *, name: str, allowed: list[int],
                  quota_bytes: int = 0,max_clients: int = 0,manual: bool | None = None,
                  prefix: str = '',max_client_ips: int = 0):
        if actor.role != 'owner': raise PermissionDenied('Only the primary owner may configure resellers')
        if not NAME_RE.fullmatch(owner) or not 1<=len(name)<=128: raise PolicyError('Invalid owner identity')
        if len(allowed)>4096 or any(type(i)is not int or i<1 for i in allowed): raise PolicyError('Invalid inbound assignments')
        integer(max_client_ips,0,1000)
        with self.lock:
            if allowed:
                known={r['id'] for r in self.engine.inbounds()}
                if not set(allowed)<=known: raise PolicyError('Assigned inbound does not exist')
            # Restriction changes may not strand already-owned clients silently.
            with self.store.lock:
                rows=self.store.db.execute('SELECT m.inbounds FROM managed_clients m JOIN clients c ON c.id=m.email WHERE c.owner=?',(owner,)).fetchall()
            if any(not set(json.loads(r['inbounds']))<=set(allowed) for r in rows):
                raise PolicyError('Detach or transfer affected clients before removing their inbound access')
            self.store.register_owner(actor,owner,quota_bytes,max_clients,manual)
            with self.store.transaction() as db:
                db.execute('INSERT INTO owner_profiles VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,allowed=excluded.allowed,prefix=excluded.prefix,max_client_ips=excluded.max_client_ips',
                           (owner,name,json.dumps(sorted(set(allowed))),prefix[:64],max_client_ips))
            self.audit(actor,owner,'owner.update',owner)
            # Reconcile synchronously when possible, otherwise the worker retries.
            self.tick(suppress=True)

    @staticmethod
    def _group_name(name: str) -> str:
        if not isinstance(name,str):raise PolicyError('Invalid group name')
        name=name.strip()
        if not 1<=len(name)<=64 or any(ord(ch)<32 or ch=='/' or ord(ch)==92 for ch in name):raise PolicyError('Invalid group name')
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

    def check_inbounds(self, actor: Actor, owner: str, ids: list[int]):
        if not ids or len(ids)>256 or any(type(i)is not int or i<1 for i in ids):
            raise PolicyError('Select 1..256 valid inbound IDs')
        if len(set(ids))!=len(ids): raise PolicyError('Duplicate inbound IDs')
        allowed=set(self.profile(owner)['allowed'])
        # The principal being the owner does not make a reseller's assignment unlimited.
        if not set(ids)<=allowed: raise PermissionDenied('Inbound is outside the client owner assignment')
        known={i['id'] for i in self.engine.inbounds()}
        if not set(ids)<=known: raise PolicyError('Inbound no longer exists')

    @staticmethod
    def validate_client(data: dict, *, partial: bool = False) -> dict:
        if not isinstance(data,dict) or set(data)-CLIENT_KEYS-{'email'}:
            raise PolicyError('Unknown client field')
        out=copy.deepcopy(data)
        for key in ('limitIp','limitHwid'):
            if key in out: integer(out[key],0,1000)
        for key in ('totalGB','tgId','reset'):
            if key in out: integer(out[key],0,MAX_INT)
        if 'reset' in out: integer(out['reset'],0,3650)
        if 'resetCount' in out: integer(out['resetCount'],0,100000)
        for key in ('resetDay','resetTraffic','resetTrafficDay'):
            if out.get(key): raise PolicyError('Only fixed-day quota reset cycles are supported in this release')
        if 'expiryTime' in out:
            if type(out['expiryTime'])is not int or not 0<=out['expiryTime']<=MAX_INT:
                raise PolicyError('Invalid expiryTime')
        if 'enable' in out and type(out['enable'])is not bool: raise PolicyError('enable must be boolean')
        for key in ('id','password','auth','subId','flow','security','group','comment','encryption'):
            if key in out and (not isinstance(out[key],str) or len(out[key])>4096): raise PolicyError('Invalid '+key)
        if 'flow' in out and out['flow'] not in ('','xtls-rprx-vision','xtls-rprx-vision-udp443'):
            raise PolicyError('Unknown flow')
        if 'id' in out and out['id']:
            try: uuid.UUID(out['id'])
            except ValueError: raise PolicyError('Invalid UUID')
        if not partial and not EMAIL_RE.fullmatch(str(out.get('email',''))): raise PolicyError('Invalid email/identity')
        return out

    def create(self, actor: Actor, owner: str, client: dict, ids: list[int]) -> dict:
        actor.require('clients','create',owner)
        if not self.engine.config.writes_enabled: raise PolicyError('CoreEngine writes are disabled')
        data=self.validate_client(client)
        email=data['email'].lower()
        with self.lock:
            self.check_inbounds(actor,owner,ids)
            existing={r['email'].lower() for r in self.engine.clients()}
            if email in existing: raise PolicyError('Client already exists in engine; adopt explicitly instead of overwriting')
            with self.store.lock:
                if self.store.db.execute('SELECT 1 FROM managed_clients WHERE email=?',(email,)).fetchone():
                    raise PolicyError('Identity is already reserved (including historical tombstones)')
            data['email']=email
            data['id']=data.get('id') or str(uuid.uuid4())
            data['password']=data.get('password') or secrets.token_urlsafe(24)
            data['auth']=data.get('auth') or secrets.token_urlsafe(24)
            data['subId']=secrets.token_hex(16)  # never accept a reseller's public credential
            if data.get('group'):
                group=self._group_name(data['group']);data['group']=group
                now=time.time()
                with self.store.transaction() as db:db.execute('INSERT OR IGNORE INTO client_groups(owner,name,color,created_at,updated_at) VALUES(?,?,?,?,?)',(owner,group,'',now,now))
            for k,v in {'flow':'','security':'auto','limitIp':1,'limitHwid':0,'totalGB':0,
                        'expiryTime':0,'enable':True,'tgId':0,'group':'','comment':'','reset':0}.items():data.setdefault(k,v)
            ceiling=self.profile(owner)['max_client_ips']
            # max_client_ips is the mandatory per-user IP cap; 0 means owner leaves choice open.
            if ceiling and (data['limitIp']==0 or data['limitIp']>ceiling):
                raise PolicyError('Requested IP limit exceeds the owner policy')
            self.store.register_client(actor,email,owner,data['limitIp'],data['totalGB'])
            try:
                self.store.edit_client(SYSTEM,email,manual=not data['enable'],expires_at=max(0,data['expiryTime']//1000))
                with self.store.transaction() as db:
                    db.execute('INSERT INTO managed_clients(email,desired,inbounds,public_token,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                               (email,json.dumps(data),json.dumps(ids),secrets.token_urlsafe(32),time.time(),time.time()))
            except Exception:
                self.store.delete_client(SYSTEM,email)
                raise
            self.audit(actor,owner,'client.create',email,'CoreEngine synchronization requested')
            self.tick(suppress=True)
            return self.detail(actor,email)

    def adopt(self, actor: Actor, owner: str, email: str) -> dict:
        if actor.role!='owner': raise PermissionDenied('Only the primary owner can adopt a engine client')
        with self.lock:
            detail=self.engine.client_detail(email)
            rec=detail['client'];ids=detail['inboundIds']
            self.check_inbounds(actor,owner,ids)
            data=CoreEngine.writable(rec);data['email']=email
            rows={r['email']:r for r in self.engine.clients()}
            if email not in rows: raise PolicyError('CoreEngine client disappeared')
            up,down=CoreEngine.counters(rows[email])
            self.store.register_client(actor,email,owner,int(data.get('limitIp',0)),int(data.get('totalGB',0)))
            try:
                with self.store.transaction() as db:
                    db.execute('UPDATE clients SET used_bytes=?,manual=?,expires_at=? WHERE id=?',
                        (up+down,int(not data.get('enable',True)),max(0,int(data.get('expiryTime',0))//1000),email))
                    db.execute('''INSERT INTO managed_clients(email,desired,inbounds,public_token,op,state,last_up,last_down,initialized,expected_enable,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(email,json.dumps(data),json.dumps(ids),secrets.token_urlsafe(32),'none','applied',up,down,1,int(bool(data.get('enable',True))),time.time(),time.time()))
            except Exception:
                self.store.delete_client(SYSTEM,email);raise
            self.audit(actor,owner,'client.adopt',email,'Historical engine bytes are a baseline, not a new reseller charge')
            self.tick(suppress=True)
            return self.detail(actor,email)

    def update(self, actor: Actor,email: str,patch: dict,ids: list[int]|None=None) -> dict:
        patch=self.validate_client(patch,partial=True)
        if 'email' in patch or 'subId' in patch: raise PolicyError('Identity/subId changes use a separate rotation workflow')
        with self.lock:
            row=self.own_row(actor,email,'edit')
            with self.store.lock:meta=dict(self.store.db.execute('SELECT * FROM managed_clients WHERE email=?',(email,)).fetchone())
            if meta['state']=='uncertain':raise PolicyError('Resolve the uncertain engine operation before editing')
            desired=json.loads(meta['desired'])
            if meta['op']=='none':
                desired=CoreEngine.writable(self.engine.client_detail(email)['client'])
            if ids is None:ids=json.loads(meta['inbounds'])
            self.check_inbounds(actor,row['owner'],ids)
            desired.update(patch);desired['email']=email
            if desired.get('group'):
                group=self._group_name(desired['group']);desired['group']=group
                now=time.time()
                with self.store.transaction() as db:db.execute('INSERT OR IGNORE INTO client_groups(owner,name,color,created_at,updated_at) VALUES(?,?,?,?,?)',(row['owner'],group,'',now,now))
            cap=self.profile(row['owner'])['max_client_ips']
            if cap and (desired.get('limitIp',0)==0 or desired['limitIp']>cap):raise PolicyError('IP cap exceeds owner policy')
            changes={}
            if 'limitIp' in patch:changes['limit_ip']=patch['limitIp']
            if 'totalGB' in patch:changes['quota_bytes']=patch['totalGB']
            if 'expiryTime' in patch:changes['expires_at']=max(0,patch['expiryTime']//1000)
            if 'enable' in patch:changes['manual']=not patch['enable']
            if changes:self.store.edit_client(SYSTEM,email,**changes)
            with self.store.transaction() as db:
                db.execute("UPDATE managed_clients SET desired=?,inbounds=?,op='upsert',state='pending',error='',retry_at=0,attempts=0,updated_at=?,external_disabled=CASE WHEN ? THEN 0 ELSE external_disabled END WHERE email=?",
                  (json.dumps(desired),json.dumps(ids),time.time(),'enable' in patch,email))
            self.audit(actor,row['owner'],'client.update',email,','.join(sorted(patch)))
            self.tick(suppress=True)
            return self.detail(actor,email)

    def action(self,actor: Actor,email: str,action: str) -> dict:
        if action in ('enable','disable'):return self.update(actor,email,{'enable':action=='enable'})
        if action not in ('reset','delete'):raise PolicyError('Unsupported action')
        with self.lock:
            row=self.own_row(actor,email,'delete' if action=='delete' else 'reset')
            # Meter the final observed delta before a destructive/reset operation.
            self.tick(suppress=False)
            with self.store.transaction() as db:
                meta=db.execute('SELECT * FROM managed_clients WHERE email=?',(email,)).fetchone()
                if meta['state']=='uncertain':raise PolicyError('Resolve the uncertain operation first')
                db.execute("UPDATE managed_clients SET op=?,state='pending',error='',retry_at=0,attempts=0,updated_at=? WHERE email=?",(action,time.time(),email))
            self.audit(actor,row['owner'],'client.'+action,email)
            self.tick(suppress=True)
            return {'email':email,'state':self.meta(email)['state']}

    def meta(self,email: str) -> dict:
        with self.store.lock:r=self.store.db.execute('SELECT * FROM managed_clients WHERE email=?',(email,)).fetchone()
        if not r:raise PolicyError('Managed metadata missing')
        return dict(r)

    def detail(self,actor: Actor,email: str,*,credentials: bool=True) -> dict:
        row=self.own_row(actor,email)
        meta=self.meta(email);engine=self.snapshot.get(email,{})
        desired=json.loads(meta['desired'])
        client=CoreEngine.writable(engine) if engine and meta['op']=='none' else desired
        if not credentials or not actor.can('clients','credentials',row['owner']):
            client={k:v for k,v in client.items() if k not in {'id','uuid','password','auth','subId','reverse','encryption'}}
        reasons=self.store.client_reasons(email)
        if meta['external_disabled']:reasons.append('engine_manual_or_external_disable')
        return {'email':email,'owner':row['owner'],'client':client,'inboundIds':json.loads(meta['inbounds']),
                'used_bytes':row['used_bytes'],'block_reasons':reasons,'state':meta['state'],
                'error':meta['error'],'observed_enable':engine.get('enable'),'last_seen_at':self.last_poll,
                'data_plane_state':'running' if self.engine.running and not self.engine.runtime_state()['dirty'] else 'staged',
                'subscription_url':self.engine.config.public_origin+'/sub/'+meta['public_token']
                     if credentials and actor.can('clients','credentials',row['owner']) else None}

    def list(self,actor: Actor) -> list[dict]:
        return [self.detail(actor,r['id'],credentials=False) for r in self.store.list_clients(actor)]

    def _charge_snapshot(self,meta: dict,record: dict):
        email=meta['email'];up,down=CoreEngine.counters(record)
        if up+down>MAX_INT:raise PolicyError('Counter overflow')
        if record.get('traffic') is None and (meta['last_up'] or meta['last_down']):
            raise CoreError('Missing engine traffic for a previously metered client; refusing to erase its baseline')
        decreased=up<meta['last_up'] or down<meta['last_down']
        du=up-meta['last_up'] if up>=meta['last_up'] else up
        dd=down-meta['last_down'] if down>=meta['last_down'] else down
        # Both the idempotent ledger and baseline advance under one transaction.
        with self.store.transaction() as db:
            user=db.execute('SELECT * FROM clients WHERE id=?',(email,)).fetchone()
            if not user:return
            owner=db.execute('SELECT * FROM owners WHERE id=?',(user['owner'],)).fetchone()
            seq=meta['seq']+1
            if du or dd:
                if du+dd>MAX_INT:raise PolicyError('Counter overflow')
                db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',
                    ('engine:'+hashlib.sha256(email.encode()).hexdigest()[:20]+':'+str(seq),user['owner'],email,owner['period'],du,dd,time.time()))
            # User's current-period meter comes from engine; reseller lifetime
            # consumption stays in the ledger, even after resets or deletion.
            db.execute('UPDATE clients SET used_bytes=? WHERE id=?',(up+down,email))
            db.execute('UPDATE managed_clients SET last_up=?,last_down=?,initialized=1,seq=? WHERE email=?',(up,down,seq,email))
        if decreased:self.audit(SYSTEM,user['owner'],'counter.reset_observed',email,'Ledger preserved; traffic lost between polling observations cannot be reconstructed')

    def _apply(self,meta: dict,records: dict[str,dict]):
        email=meta['email'];op=meta['op'];desired=json.loads(meta['desired']);ids=json.loads(meta['inbounds'])
        existing=records.get(email)
        if op=='delete':
            if existing:
                if existing.get('subId')!=desired.get('subId'):raise CoreError('Identity conflict: refusing to delete a different engine client',status=409)
                final=self.engine.delete(email)
                if final:self._charge_snapshot(self.meta(email),final)
            self.store.delete_client(SYSTEM,email)
            with self.store.transaction() as db:
                db.execute("UPDATE managed_clients SET op='none',state='deleted',desired='{}',error='',updated_at=? WHERE email=?",(time.time(),email))
            return
        if op=='reset':
            if not existing:raise CoreError('CoreEngine client missing; reset refused',status=409)
            with self.store.transaction() as db:db.execute("UPDATE managed_clients SET state='reset_inflight' WHERE email=?",(email,))
            final=self.engine.reset(email)
            if final:self._charge_snapshot(self.meta(email),final)
            with self.store.transaction() as db:
                db.execute('UPDATE clients SET used_bytes=0 WHERE id=?',(email,))
                db.execute('UPDATE managed_clients SET last_up=0,last_down=0 WHERE email=?',(email,))
            self._complete_cycle(email)
            # Reset does not remove manual, expiry or owner blockers.
        elif op=='upsert':
            enabled=not self.store.client_reasons(email) and not meta['external_disabled']
            desired['enable']=enabled
            if not existing:
                self.engine.create(desired,ids)
            else:
                if existing.get('subId')!=desired.get('subId'):
                    raise CoreError('Identity conflict: engine subId changed; explicit re-adoption is required',status=409)
                self.engine.update(email,desired)
                actual=set(existing.get('inboundIds',[]));want=set(ids)
                if want-actual:self.engine.attach(email,sorted(want-actual))
                if actual-want:self.engine.detach(email,sorted(actual-want))
            with self.store.transaction() as db:
                db.execute('UPDATE managed_clients SET expected_enable=? WHERE email=?',(int(enabled),email))
        with self.store.transaction() as db:
            db.execute("UPDATE managed_clients SET op='none',state='applied',error='',retry_at=0,attempts=0,updated_at=? WHERE email=?",(time.time(),email))

    def _schedule_cycles(self):
        """Schedule at most one reset for missed periods; persist the next deadline.

        Expiry and manual/owner blocks remain independent. Completion advances
        the schedule only after a successful reset; crash-uncertain resets are
        deliberately not replayed.
        """
        now=time.time()
        with self.store.transaction() as db:
            metas=db.execute("SELECT * FROM managed_clients WHERE state!='deleted'").fetchall()
            for meta in metas:
                desired=json.loads(meta['desired']);days=desired.get('reset',0)
                if not days:
                    db.execute('DELETE FROM client_cycles WHERE email=?',(meta['email'],));continue
                row=db.execute('SELECT * FROM client_cycles WHERE email=?',(meta['email'],)).fetchone()
                cap=desired.get('resetCount',0)
                if not row or row['days']!=days:
                    db.execute('INSERT INTO client_cycles(email,days,next_at,completed,max_resets) VALUES(?,?,?,0,?) ON CONFLICT(email) DO UPDATE SET days=excluded.days,next_at=excluded.next_at,completed=0,max_resets=excluded.max_resets',
                               (meta['email'],days,now+days*86400,cap));continue
                db.execute('UPDATE client_cycles SET max_resets=? WHERE email=?',(cap,meta['email']))
                if cap and row['completed']>=cap:continue
                if row['next_at']<=now and meta['op']=='none' and meta['state']=='applied':
                    db.execute("UPDATE managed_clients SET op='reset',state='pending',retry_at=0,attempts=0,error='' WHERE email=?",(meta['email'],))

    def _complete_cycle(self,email):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM client_cycles WHERE email=?',(email,)).fetchone()
            now=time.time()
            if row and row['next_at']<=now:
                period=row['days']*86400
                next_at=row['next_at']+(int((now-row['next_at'])//period)+1)*period
                db.execute('UPDATE client_cycles SET next_at=?,completed=completed+1 WHERE email=?',(next_at,email))

    def tick(self,*,suppress: bool=False):
        with self.lock:
            try:
                self._schedule_cycles()
                rows=self.engine.clients();records={r['email']:r for r in rows}
                with self.store.lock:metas=[dict(r) for r in self.store.db.execute("SELECT * FROM managed_clients WHERE state!='deleted'")]
                for meta in metas:
                    if meta['email'] in records:
                        rec=records[meta['email']]
                        if rec.get('subId')!=json.loads(meta['desired']).get('subId'):
                            with self.store.transaction() as db:
                                db.execute("UPDATE managed_clients SET state='conflict',error='CoreEngine identity changed; refusing to meter or modify another client' WHERE email=?",(meta['email'],))
                            meta['state']='conflict'
                        else:self._charge_snapshot(meta,rec)
                    elif meta['op']=='none':
                        with self.store.transaction() as db:
                            db.execute("UPDATE managed_clients SET state='missing',error='CoreEngine client missing; automatic recreation refused' WHERE email=?",(meta['email'],))
                        meta['state']='missing'

                changed=False
                for meta in metas:
                    if meta['op']=='none' or meta['retry_at']>time.time() or meta['state'] in ('uncertain','conflict'):continue
                    if not self.engine.config.writes_enabled:continue
                    try:self._apply(meta,records);changed=True
                    except (CoreError,PolicyError) as e:
                        uncertain=isinstance(e,CoreError) and e.uncertain and meta['op']=='reset'
                        conflict=isinstance(e,CoreError) and e.status==409
                        state='uncertain' if uncertain else 'conflict' if conflict else 'error'
                        attempts=meta['attempts']+1
                        with self.store.transaction() as db:
                            db.execute('UPDATE managed_clients SET state=?,error=?,attempts=?,retry_at=? WHERE email=?',
                                (state,str(e)[:400],attempts,time.time()+min(300,5*2**min(attempts,6)),meta['email']))
                if changed:
                    rows=self.engine.clients();records={r['email']:r for r in rows}
                    with self.store.lock:metas=[dict(r) for r in self.store.db.execute("SELECT * FROM managed_clients WHERE state!='deleted'")]
                self.snapshot=records
                for meta in metas:
                    rec=records.get(meta['email'])
                    if not rec or meta['op']!='none' or meta['state'] in ('conflict','uncertain'):continue
                    if meta['state']=='missing':
                        with self.store.transaction() as db:db.execute("UPDATE managed_clients SET state='applied',error='' WHERE email=?",(meta['email'],))
                    self._charge_snapshot(self.meta(meta['email']),rec)
                    with self.store.transaction() as db:
                        db.execute('UPDATE clients SET quota_bytes=?,expires_at=? WHERE id=?',
                            (int(rec.get('totalGB',0)),max(0,int(rec.get('expiryTime',0))//1000),meta['email']))
                    reasons=self.store.client_reasons(meta['email'])
                    # Preserve unexpected external disables. Never automatically
                    # resurrect a client whose enable flag changed outside DARK.
                    if rec.get('enable') is False and meta['expected_enable']==1 and not reasons:
                        with self.store.transaction() as db:db.execute('UPDATE managed_clients SET external_disabled=1 WHERE email=?',(meta['email'],))
                        meta['external_disabled']=1
                    enabled=not reasons and not meta['external_disabled']
                    if bool(rec.get('enable'))!=enabled and self.engine.config.writes_enabled:
                        payload=CoreEngine.writable(rec);payload['enable']=enabled
                        self.engine.update(meta['email'],payload)
                        rec['enable']=enabled
                    with self.store.transaction() as db:
                        db.execute('UPDATE managed_clients SET expected_enable=? WHERE email=?',(int(bool(rec.get('enable'))),meta['email']))
                self.engine.flush()
                self.last_poll=time.time();self.last_error='' 
            except Exception as e:
                self.last_error=(str(e) if isinstance(e,(CoreError,PolicyError)) else type(e).__name__)[:400]
                if not suppress:raise

    def resolve_reset(self,actor: Actor,email: str,confirmation: str) -> dict:
        if actor.role!='owner' or confirmation!=email:
            raise PermissionDenied('Owner and exact client identity confirmation required')
        with self.lock:
            meta=self.meta(email)
            if meta['state']!='uncertain' or meta['op']!='reset':
                raise PolicyError('There is no uncertain reset to resolve')
            records={r['email']:r for r in self.engine.clients()}
            rec=records.get(email)
            if not rec or rec.get('subId')!=json.loads(meta['desired']).get('subId'):
                raise PolicyError('CoreEngine client identity must match before recovery')
            # Accept the present counters. Never replay a destructive reset or
            # refund the historical ledger. Unobserved bytes remain unknown.
            self._charge_snapshot(meta,rec)
            with self.store.transaction() as db:
                db.execute("UPDATE managed_clients SET op='none',state='applied',error='',retry_at=0,attempts=0 WHERE email=?",(email,))
            row=self.own_row(actor,email)
            self.audit(actor,row['owner'],'reset.resolve_current',email,'Current engine counters accepted; destructive reset NOT replayed')
            self.tick(suppress=False)
            return self.detail(actor,email)

    def recover_resets(self):
        # A crash during a reset has an ambiguous remote outcome. Never replay it.
        with self.store.transaction() as db:
            db.execute("UPDATE managed_clients SET state='uncertain',error='Service restarted during a engine reset; automatic retry refused' WHERE state='reset_inflight'")

    def start(self):
        self.recover_resets()
        if self.thread:return
        def run():
            while not self.stop.is_set():
                self.tick(suppress=True)
                self.stop.wait(self.engine.config.poll_seconds)
        self.thread=threading.Thread(target=run,name='dark-engine-reconcile',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=35)

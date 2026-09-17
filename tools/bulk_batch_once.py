#!/usr/bin/env python3
from pathlib import Path

manager_path=Path('backend/manager.py')
server_path=Path('backend/server.py')
ms=manager_path.read_text(encoding='utf-8')
ss=server_path.read_text(encoding='utf-8')

create_start=ms.index('    def create(self, actor: Actor, owner: str, client: dict, ids: list[int]) -> dict:\n')
create_end=ms.index('\n    def adopt(self, actor: Actor, owner: str, email: str) -> dict:',create_start)
new_create='''    def _creation_sets_locked(self) -> tuple[set[str],set[str]]:
        """Snapshot identity reservations once for a create/bulk-create operation.

        The caller holds ``self.lock``. Managed tombstones intentionally remain
        reserved; policy-only rows are also treated as reserved so a partial old
        operation cannot become an unhandled SQLite UNIQUE failure.
        """
        existing={r['email'].lower() for r in self.engine.clients()}
        with self.store.lock:
            reserved={str(r[0]).lower() for r in self.store.db.execute('SELECT email FROM managed_clients')}
            reserved.update(str(r[0]).lower() for r in self.store.db.execute('SELECT id FROM clients'))
        return existing,reserved

    def _stage_create_locked(self,actor:Actor,owner:str,client:dict,ids:list[int],
                             existing:set[str],reserved:set[str],*,inbounds_checked:bool=False)->str:
        actor.require('clients','create',owner)
        if not self.engine.config.writes_enabled:raise PolicyError('CoreEngine writes are disabled')
        data=self.validate_client(client);email=data['email'].lower()
        if not inbounds_checked:self.check_inbounds(actor,owner,ids)
        if email in existing:raise PolicyError('Client already exists in engine; adopt explicitly instead of overwriting')
        if email in reserved:raise PolicyError('Identity is already reserved (including historical tombstones)')
        data['email']=email;data['id']=data.get('id') or str(uuid.uuid4());data['password']=data.get('password') or secrets.token_urlsafe(24)
        data['auth']=data.get('auth') or secrets.token_urlsafe(24);data['subId']=secrets.token_hex(16)
        if data.get('group'):
            group=self._group_name(data['group']);data['group']=group;now=time.time()
            with self.store.transaction() as db:db.execute('INSERT OR IGNORE INTO client_groups(owner,name,color,created_at,updated_at) VALUES(?,?,?,?,?)',(owner,group,'',now,now))
        for k,v in {'flow':'','security':'auto','limitIp':1,'limitHwid':0,'totalGB':0,
                    'expiryTime':0,'enable':True,'tgId':0,'group':'','comment':'','reset':0,
                    'resetTraffic':'never','resetTrafficDay':0,'resetCount':0}.items():data.setdefault(k,v)
        ceiling=self.profile(owner)['max_client_ips']
        if ceiling and (data['limitIp']==0 or data['limitIp']>ceiling):raise PolicyError('Requested IP limit exceeds the owner policy')
        try:self.store.register_client(actor,email,owner,data['limitIp'],data['totalGB'])
        except sqlite3.IntegrityError as ex:raise PolicyError('Identity is already reserved') from ex
        try:
            self.store.edit_client(SYSTEM,email,manual=not data['enable'],expires_at=max(0,data['expiryTime']//1000))
            with self.store.transaction() as db:
                db.execute('INSERT INTO managed_clients(email,desired,inbounds,public_token,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                           (email,json.dumps(data),json.dumps(ids),secrets.token_urlsafe(32),time.time(),time.time()))
        except Exception:
            self.store.delete_client(SYSTEM,email);raise
        self.audit(actor,owner,'client.create',email,'CoreEngine synchronization requested')
        existing.add(email);reserved.add(email)
        return email

    def create(self, actor: Actor, owner: str, client: dict, ids: list[int]) -> dict:
        with self.lock:
            self.check_inbounds(actor,owner,ids)
            existing,reserved=self._creation_sets_locked()
            email=self._stage_create_locked(actor,owner,client,ids,existing,reserved,inbounds_checked=True)
            self.tick(suppress=True)
            return self.detail(actor,email)

    def create_batch(self,actor:Actor,owner:str,clients:list[dict],ids:list[int])->dict:
        """Stage up to 500 independent creates, then reconcile the runtime once.

        This preserves per-item best-effort results while avoiding the previous
        O(N²)-like behavior where every item rescanned and reconciled all clients.
        """
        if not isinstance(clients,list) or not 1<=len(clients)<=500:raise PolicyError('Bulk create requires 1..500 clients')
        with self.lock:
            try:
                actor.require('clients','create',owner);self.check_inbounds(actor,owner,ids)
                existing,reserved=self._creation_sets_locked();preflight_error=''
            except (PolicyError,CoreError) as ex:
                existing,reserved=set(),set();preflight_error=str(ex)[:300]
            out=[];staged=[]
            for raw in clients:
                label=str(raw.get('email',''))[:128] if isinstance(raw,dict) else ''
                if preflight_error:
                    out.append({'email':label,'error':preflight_error});continue
                try:
                    email=self._stage_create_locked(actor,owner,raw,ids,existing,reserved,inbounds_checked=True)
                    staged.append(email);out.append({'email':email,'_staged':True})
                except (PolicyError,CoreError,sqlite3.IntegrityError) as ex:
                    out.append({'email':label,'error':str(ex)[:300]})
            if staged:self.tick(suppress=True)
            for item in out:
                if not item.pop('_staged',False):continue
                try:item['result']=self.detail(actor,item['email'])
                except (PolicyError,CoreError) as ex:
                    # The durable record was created. Surface reconciliation state
                    # rather than misreporting it as a failed identity reservation.
                    meta=self.meta(item['email']);item['result']={'email':item['email'],'state':meta['state'],'error':str(ex)[:300]}
            return {'requested':len(clients),'created':len(staged),'items':out}
'''
ms=ms[:create_start]+new_create+ms[create_end:]

update_start=ms.index('    def update(self, actor: Actor,email: str,patch: dict,ids: list[int]|None=None) -> dict:\n')
update_end=ms.index('\n    def action(self,actor: Actor,email: str,action: str) -> dict:',update_start)
old_update=ms[update_start:update_end]
if 'self.tick(suppress=True)\n            return self.detail(actor,email)' not in old_update:
    raise SystemExit('manager update anchor changed')
new_update=old_update.replace('    def update(self, actor: Actor,email: str,patch: dict,ids: list[int]|None=None) -> dict:',
                              '    def update(self, actor: Actor,email: str,patch: dict,ids: list[int]|None=None,*,reconcile:bool=True) -> dict:',1)
new_update=new_update.replace('            self.tick(suppress=True)\n            return self.detail(actor,email)',
                              '            if reconcile:self.tick(suppress=True)\n            return self.detail(actor,email)',1)
ms=ms[:update_start]+new_update+ms[update_end:]

bc_start=ss.index("    @app.post('/api/clients/bulk-create')\n")
ba_start=ss.index("    @app.post('/api/clients/bulk-adjust')\n",bc_start)
bi_start=ss.index("    @app.post('/api/clients/bulk-inbounds')\n",ba_start)
client_start=ss.index("    @app.get('/api/clients/{email}')\n",bi_start)
new_bc='''    @app.post('/api/clients/bulk-create')
    def bulk_create(body:BulkCreate,p:Principal=Depends(current)):
        writable();payloads=[]
        for offset in range(body.quantity):
            email=f'{body.prefix}{body.first+offset}{body.postfix}'.strip().lower()
            payload=dict(body.client);payload['email']=email;payloads.append(payload)
        return manager.create_batch(p.actor,body.owner,payloads,body.inboundIds)

'''
new_ba='''    @app.post('/api/clients/bulk-adjust')
    def bulk_adjust(body:BulkAdjust,p:Principal=Depends(current)):
        writable();out=[];now_ms=int(time.time()*1000);changed=[]
        for email in dict.fromkeys(body.emails):
            try:
                d=manager.detail(p.actor,email,credentials=False);c=d['client'];patch={}
                if body.add_bytes:
                    current=int(c.get('totalGB',0))
                    if current==0:raise PolicyError('Unlimited quota is unchanged by add-bytes; set a quota explicitly per client')
                    adjusted=current+body.add_bytes
                    if adjusted<=0:raise PolicyError('Bulk quota adjustment would become 0, but 0 means unlimited; choose a smaller reduction')
                    patch['totalGB']=min((1<<63)-1,adjusted)
                if body.add_days:
                    current=int(c.get('expiryTime',0))
                    if current==0 and body.add_days<0:raise PolicyError('A no-expiry client cannot be reduced with negative bulk days; set an explicit expiry or disable it instead')
                    base=current if current>now_ms else now_ms;patch['expiryTime']=max(1000,base+body.add_days*86400000)
                if body.group is not None:patch['group']=body.group
                if body.limit_hwid is not None:patch['limitHwid']=body.limit_hwid
                if not patch:raise PolicyError('No bulk adjustment requested')
                manager.update(p.actor,email,patch,reconcile=False);changed.append(email);out.append({'email':email,'_changed':True})
            except (PolicyError,CoreError) as ex:out.append({'email':email,'error':str(ex)[:300]})
        if changed:manager.tick(suppress=True)
        for item in out:
            if item.pop('_changed',False):item['result']=manager.detail(p.actor,item['email'])
        return {'changed':len(changed),'items':out}

'''
new_bi='''    @app.post('/api/clients/bulk-inbounds')
    def bulk_inbounds(body:BulkInbounds,p:Principal=Depends(current)):
        writable();out=[];changed=[]
        for email in dict.fromkeys(body.emails):
            try:
                manager.own_row(p.actor,email,'attach');d=manager.detail(p.actor,email,credentials=False);current=set(d['inboundIds']);change=set(body.inboundIds)
                ids=sorted(current|change) if body.mode=='attach' else sorted(current-change)
                if not ids:raise PolicyError('A client must retain at least one inbound')
                manager.update(p.actor,email,{},ids,reconcile=False);changed.append(email);out.append({'email':email,'_changed':True})
            except (PolicyError,CoreError) as ex:out.append({'email':email,'error':str(ex)[:300]})
        if changed:manager.tick(suppress=True)
        for item in out:
            if item.pop('_changed',False):item['result']=manager.detail(p.actor,item['email'])
        return {'changed':len(changed),'items':out}

'''
ss=ss[:bc_start]+new_bc+new_ba+new_bi+ss[client_start:]

manager_path.write_text(ms,encoding='utf-8')
server_path.write_text(ss,encoding='utf-8')

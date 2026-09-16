"""FastAPI routes for the standalone DARK policy/accounting development API."""
import hashlib, json, os, sqlite3, time, secrets
from typing import Annotated, Literal
import psutil
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from dark_policy import Actor, PermissionDenied, PolicyError, Store
from policy_auth import (CAPABILITIES, Login, AdminCreate, AdminEdit, OwnerEdit, ClientCreate, ClientEdit, Credit, Usage, Refund, create_admin, password_hash, verify_password, permissions_for, effective_permissions)

def create_app(store: Store) -> FastAPI:
    app=FastAPI(title='DARK XRAY Policy API',version='0.3.0-dev',
        description='Standalone development service, not an Xray panel backend. Interactive frontend is offline.',
        docs_url='/docs',redoc_url=None)
    dummy=password_hash('dummy-not-a-login-password')

    @app.middleware('http')
    async def harden(request: Request, call_next):
        # A browser on a different origin cannot use localhost as a write target.
        # No forwarded headers are trusted here.
        origin=request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Cross-origin requests are not accepted'},status_code=403)
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>65536):
            return JSONResponse({'detail':'Request body too large'},status_code=413)
        if request.method in {'POST','PUT','PATCH'}:
            if 'application/json' not in request.headers.get('content-type','') and length not in (None,'0'):
                return JSONResponse({'detail':'JSON required'},status_code=415)
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>65536:return JSONResponse({'detail':'Request body too large'},status_code=413)
            request._body=bytes(body)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        return response

    @app.exception_handler(PermissionDenied)
    async def permission_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=403)
    @app.exception_handler(PolicyError)
    async def policy_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)
    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request,exc):return JSONResponse({'detail':'Record already exists or conflicts with an invariant'},status_code=409)

    def token_value(authorization: str|None) -> str:
        if not authorization or not authorization.startswith('Bearer ') or len(authorization)>512:
            raise HTTPException(401,'Bearer token required',headers={'WWW-Authenticate':'Bearer'})
        token=authorization[7:]
        if not 32<=len(token)<=256:raise HTTPException(401,'Invalid session')
        return token

    def current(authorization: Annotated[str|None,Header()]=None) -> Actor:
        token=token_value(authorization);digest=hashlib.sha256(token.encode()).hexdigest()
        with store.lock:
            row=store.db.execute('''SELECT a.* FROM sessions s JOIN api_admins a ON a.id=s.admin_id
                WHERE s.digest=? AND s.expires_at>? AND a.disabled=0''',(digest,time.time())).fetchone()
        if not row:raise HTTPException(401,'Session expired or revoked',headers={'WWW-Authenticate':'Bearer'})
        return Actor(row['id'],row['role'],effective_permissions(row['role'],json.loads(row['permissions'])))

    def owner_only(actor: Actor):
        if actor.role!='owner':raise PermissionDenied('Owner access required')

    @app.get('/health')
    def health():return {'status':'development','capabilities':CAPABILITIES}

    @app.post('/v1/auth/login')
    def login(data: Login,request: Request):
        source=request.client.host if request.client else 'unknown';now=time.time()
        with store.transaction() as db:
            old=db.execute('SELECT * FROM login_attempts WHERE source=?',(source,)).fetchone()
            if old and old['start_at']>now-300 and old['attempts']>=5:
                raise HTTPException(429,'Too many attempts; retry after the current five-minute window',headers={'Retry-After':'300'})
            count=old['attempts']+1 if old and old['start_at']>now-300 else 1
            start=old['start_at'] if old and old['start_at']>now-300 else now
            db.execute('INSERT INTO login_attempts VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET attempts=excluded.attempts,start_at=excluded.start_at',(source,count,start))
            row=db.execute('SELECT * FROM api_admins WHERE id=?',(data.username,)).fetchone()
        valid=verify_password(data.password,row['password_hash'] if row else dummy)
        if not row or not valid or row['disabled']:raise HTTPException(401,'Invalid credentials')
        token=secrets.token_urlsafe(48);expiry=now+8*3600
        with store.transaction() as db:
            fresh=db.execute('SELECT * FROM api_admins WHERE id=?',(data.username,)).fetchone()
            if not fresh or fresh['disabled'] or fresh['password_hash']!=row['password_hash']:raise HTTPException(401,'Credentials changed; log in again')
            db.execute('DELETE FROM login_attempts WHERE source=?',(source,))
            db.execute('DELETE FROM sessions WHERE expires_at<=?',(now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],expiry))
        return {'access_token':token,'token_type':'bearer','expires_at':expiry,'admin':{'id':row['id'],'role':row['role']}}

    @app.post('/v1/auth/logout')
    def logout(actor: Annotated[Actor,Depends(current)],authorization: Annotated[str,Header()]):
        with store.transaction() as db:db.execute('DELETE FROM sessions WHERE digest=?',(hashlib.sha256(token_value(authorization).encode()).hexdigest(),))
        return {'revoked':True}

    @app.get('/v1/me')
    def me(actor: Annotated[Actor,Depends(current)]):return {'id':actor.id,'role':actor.role,'permissions':actor.permissions}

    @app.get('/v1/admins')
    def admins(actor: Annotated[Actor,Depends(current)]):
        owner_only(actor)
        with store.lock:return [dict(r) for r in store.db.execute('SELECT id,role,permissions,disabled FROM api_admins ORDER BY id')]

    @app.post('/v1/admins',status_code=201)
    def add_admin(data: AdminCreate,actor: Annotated[Actor,Depends(current)]):
        create_admin(store,actor,data.username,data.password,data.role,data.permissions)
        return {'id':data.username,'role':data.role}

    @app.patch('/v1/admins/{admin_id}')
    def edit_admin(admin_id: str,data: AdminEdit,actor: Annotated[Actor,Depends(current)]):
        owner_only(actor)
        hashed=password_hash(data.password) if data.password is not None else None
        with store.transaction() as db:
            row=db.execute('SELECT * FROM api_admins WHERE id=?',(admin_id,)).fetchone()
            if not row:raise HTTPException(404,'Admin not found')
            if data.disabled and row['role']=='owner' and db.execute("SELECT COUNT(*) FROM api_admins WHERE role='owner' AND disabled=0").fetchone()[0]<=1:
                raise PolicyError('Cannot disable the last enabled owner')
            changes={}
            if data.disabled is not None:changes['disabled']=int(data.disabled)
            if hashed:changes['password_hash']=hashed
            if data.permissions is not None:changes['permissions']=json.dumps(permissions_for(row['role'],data.permissions))
            if data.disabled is not None and row['role']=='reseller':
                db.execute('UPDATE owners SET account_disabled=? WHERE id=?',(int(data.disabled),admin_id))
            if changes:
                db.execute('UPDATE api_admins SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),admin_id))
                db.execute('DELETE FROM sessions WHERE admin_id=?',(admin_id,))
        return {'updated':True,'sessions_revoked':bool(changes)}

    @app.get('/v1/owners')
    def owners(actor: Annotated[Actor,Depends(current)]):
        with store.lock:ids=[r[0] for r in store.db.execute('SELECT id FROM owners ORDER BY id')]
        return [store.owner_stats(actor,r) for r in ids if actor.can('owners','read',r)]

    @app.put('/v1/owners/{owner_id}')
    def put_owner(owner_id: str,data: OwnerEdit,actor: Annotated[Actor,Depends(current)]):
        store.register_owner(actor,owner_id,**data.model_dump());return {'saved':True}

    @app.post('/v1/owners/{owner_id}/reset-period')
    def reset_owner(owner_id: str,actor: Annotated[Actor,Depends(current)]):
        store.reset_owner_period(actor,owner_id);return {'reset':True,'historical_ledger_preserved':True}

    @app.get('/v1/clients')
    def clients(actor: Annotated[Actor,Depends(current)]):
        return [r|{'block_reasons':store.client_reasons(r['id'])} for r in store.list_clients(actor)]

    @app.post('/v1/clients',status_code=201)
    def add_client(data: ClientCreate,actor: Annotated[Actor,Depends(current)]):
        if data.price or data.order_id:owner_only(actor)
        created=store.register_client(actor,data.id,data.owner,data.limit_ip,data.quota_bytes,data.price,data.order_id)
        return {'created':created,'note':'Policy record only; not an Xray credential/listener'}

    @app.patch('/v1/clients/{client_id}')
    def edit_client(client_id: str,data: ClientEdit,actor: Annotated[Actor,Depends(current)]):
        store.edit_client(actor,client_id,**data.model_dump());return {'updated':True}

    @app.delete('/v1/clients/{client_id}')
    def delete_client(client_id: str,actor: Annotated[Actor,Depends(current)]):
        store.delete_client(actor,client_id);return {'deleted':True,'historical_ledger_preserved':True}

    @app.post('/v1/clients/{client_id}/reset-usage')
    def reset_client(client_id: str,actor: Annotated[Actor,Depends(current)]):
        store.reset_client_usage(actor,client_id);return {'reset':True,'owner_traffic_not_refunded':True}

    @app.post('/v1/usage')
    def ingest_usage(data: Usage,actor: Annotated[Actor,Depends(current)]):
        owner_only(actor)
        return {'recorded':store.record_usage(data.event_id,data.client_id,data.up_bytes,data.down_bytes)}

    @app.post('/v1/owners/{owner_id}/credit')
    def credit_owner(owner_id: str,data: Credit,actor: Annotated[Actor,Depends(current)]):
        owner_only(actor)
        return {'recorded':store.credit(actor,owner_id,data.amount,data.event_id)}

    @app.post('/v1/refunds')
    def refund(data: Refund,actor: Annotated[Actor,Depends(current)]):
        owner_only(actor)
        return {'recorded':store.refund(actor,data.order_id,data.event_id)}

    @app.get('/v1/ledger/{kind}')
    def ledger(kind: Literal['traffic','money'],actor: Annotated[Actor,Depends(current)],limit: int=100):
        if not 1<=limit<=1000:raise PolicyError('Limit must be 1..1000')
        resource='finance' if kind=='money' else 'owners';table='money_ledger' if kind=='money' else 'traffic_ledger'
        all_allowed=actor.role=='owner' or actor.permissions.get(resource+'.read')=='all'
        own_allowed=actor.can(resource,'read',actor.id)
        if not (all_allowed or own_allowed):raise PermissionDenied('Ledger read is not allowed')
        with store.lock:
            sql=f'SELECT * FROM {table}'+('' if all_allowed else ' WHERE owner=?')+' ORDER BY rowid DESC LIMIT ?'
            return [dict(r) for r in store.db.execute(sql,(limit,) if all_allowed else (actor.id,limit))]

    @app.get('/v1/ip-events')
    def ip_events(actor: Annotated[Actor,Depends(current)],limit: int=100):
        if not 1<=limit<=1000:raise PolicyError('Limit must be 1..1000')
        all_allowed=actor.role=='owner' or actor.permissions.get('ip.read')=='all'
        if not all_allowed and not actor.can('ip','read',actor.id):raise PermissionDenied('IP read is not allowed')
        with store.lock:
            sql='SELECT * FROM events'+('' if all_allowed else ' WHERE owner=?')+' ORDER BY id DESC LIMIT ?'
            return {'live_firewall_verified':False,'events':[dict(r) for r in store.db.execute(sql,(limit,) if all_allowed else (actor.id,limit))]}

    @app.get('/v1/system')
    def system(actor: Annotated[Actor,Depends(current)]):
        actor.require('system','read')
        memory=psutil.virtual_memory();swap=psutil.swap_memory();disk=psutil.disk_usage('/');net=psutil.net_io_counters()
        return {'sample':False,'host':'this API host','cpu_percent':psutil.cpu_percent(interval=.05),
                'memory':{'total':memory.total,'used':memory.used,'available':memory.available,'percent':memory.percent},
                'swap':{'total':swap.total,'used':swap.used,'percent':swap.percent},
                'disk':{'total':disk.total,'used':disk.used,'free':disk.free,'percent':disk.percent},
                'uptime_seconds':int(time.time()-psutil.boot_time()),'load':list(os.getloadavg()),
                'network_counters':{'sent_bytes':net.bytes_sent,'received_bytes':net.bytes_recv},
                'xray_measured':False}
    return app


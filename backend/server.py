#!/usr/bin/env python3
"""DARK XRAY standalone server. Own database, API, UI and direct Xray process.
No proxy-panel installation or token is required. The default listener is loopback.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import sys
import time
import threading
import zipfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit,urlunsplit,quote

import psutil
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import JSONResponse,FileResponse,Response,RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field,StrictInt

from auth import Auth,Principal,DEFAULTS,PERMISSIONS,digest
from dark_policy import Store,Actor,PolicyError,PermissionDenied,MAX_INT
from manager import Manager,SYSTEM
from core import CoreEngine,CoreError,Config,SUB_RE
from reality_scan import RealityScanError,scan_target,search_targets
from nodes import NodeRegistry,token_digest
from update_bridge import UpdateBrokerClient,UpdateBrokerError

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
COOKIE='dark_session'
PASSWORD_MIN_LENGTH=8
PASSWORD_MAX_LENGTH=512

class UnbanIP(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    ip:str=Field(min_length=3,max_length=80)

class Model(BaseModel): model_config=ConfigDict(extra='forbid',strict=True)
class Login(Model):
    username:str=Field(min_length=1,max_length=128)
    password:str=Field(min_length=1,max_length=PASSWORD_MAX_LENGTH)
    otp:str=Field(default='',max_length=64)
class OwnerBody(Model):
    name:str=Field(min_length=1,max_length=128)
    allowed:list[StrictInt]=Field(default_factory=list,max_length=4096)
    quota_bytes:StrictInt=Field(default=0,ge=0,le=MAX_INT)
    max_clients:StrictInt=Field(default=0,ge=0,le=1000000)
    manual:bool|None=None
    prefix:str=Field(default='',max_length=64)
    max_client_ips:StrictInt=Field(default=0,ge=0,le=1000)
class ClientBody(Model):
    owner:str=Field(min_length=1,max_length=128)
    client:dict[str,Any]
    inboundIds:list[StrictInt]=Field(min_length=1,max_length=256)
class ClientPatch(Model):
    client:dict[str,Any]=Field(default_factory=dict)
    inboundIds:list[StrictInt]|None=Field(default=None,min_length=1,max_length=256)
class Adopt(Model):
    owner:str=Field(min_length=1,max_length=128)
    email:str=Field(min_length=1,max_length=128)
class AdminBody(Model):
    username:str=Field(min_length=1,max_length=128)
    password:str=Field(min_length=PASSWORD_MIN_LENGTH,max_length=PASSWORD_MAX_LENGTH)
    role:Literal['owner','reseller','readonly']='reseller'
    permissions:dict[str,str]|None=None
class AdminPatch(Model):
    disabled:bool|None=None
    password:str|None=Field(default=None,min_length=PASSWORD_MIN_LENGTH,max_length=PASSWORD_MAX_LENGTH)
    permissions:dict[str,str]|None=None
class ResolveReset(Model):confirmation:str=Field(min_length=1,max_length=128)
class Action(Model): action:Literal['enable','disable','reset','delete']
class Bulk(Model):
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
class Credit(Model):
    amount:StrictInt=Field(ge=1,le=MAX_INT)
    event_id:str=Field(min_length=16,max_length=256)
class KeyBody(Model):
    name:str=Field(min_length=1,max_length=128)
    permissions:dict[str,str]
    days:StrictInt=Field(default=30,ge=1,le=365)
class MFASetup(Model):password:str=Field(min_length=1,max_length=PASSWORD_MAX_LENGTH)
class Code(Model):code:str=Field(min_length=6,max_length=64)
class MFADisable(MFASetup):code:str=Field(min_length=6,max_length=64)
class Password(Model):
    old_password:str=Field(min_length=1,max_length=PASSWORD_MAX_LENGTH)
    new_password:str=Field(min_length=PASSWORD_MIN_LENGTH,max_length=PASSWORD_MAX_LENGTH)


class UpdateCheck(Model):
    channel:Literal['main','stable','rc','exact']='main'
    ref:str=Field(default='',max_length=128)
class UpdateStart(Model):
    commit:str=Field(min_length=40,max_length=40)

class RealityProbe(Model):
    target:str=Field(min_length=1,max_length=300)
class RealitySearch(Model):
    targets:list[str]=Field(default_factory=list,max_length=20)
class NodeCreate(Model):
    id:str=Field(min_length=1,max_length=128)
    name:str=Field(min_length=1,max_length=128)
    origin:str=Field(min_length=8,max_length=500)
    token:str=Field(min_length=40,max_length=256)
    enabled:bool=True
    dataAddress:str=Field(default='',max_length=253)
    priority:StrictInt=Field(default=100,ge=1,le=1000)
    failoverEnabled:bool=True
    inboundIds:list[StrictInt]=Field(default_factory=list,max_length=256)
class NodePatch(Model):
    name:str=Field(min_length=1,max_length=128)
    origin:str=Field(min_length=8,max_length=500)
    token:str|None=Field(default=None,min_length=40,max_length=256)
    keep_token:bool=False
    enabled:bool=True
    dataAddress:str=Field(default='',max_length=253)
    priority:StrictInt=Field(default=100,ge=1,le=1000)
    failoverEnabled:bool=True
    inboundIds:list[StrictInt]=Field(default_factory=list,max_length=256)
class NodeMirrorSync(Model):
    assignments:list[dict[str,Any]]=Field(default_factory=list,max_length=256)
class NodeMirrorTrafficReset(Model):
    sourceEmail:str=Field(min_length=1,max_length=128)
    resetId:str=Field(min_length=8,max_length=128)
class NodeMirrorSecurityClear(Model):
    sourceEmail:str=Field(min_length=1,max_length=128)
    kind:Literal['ips','devices','all']
class NodeTokenCreate(Model):
    name:str=Field(min_length=1,max_length=64)
    days:StrictInt=Field(default=365,ge=1,le=3650)


def make_app(manager:Manager,auth:Auth,*,background:bool=True)->FastAPI:
    config=manager.engine.config;store=manager.store;engine=manager.engine;nodes=NodeRegistry(store,auth.cipher)
    node_reset_lock=threading.RLock()
    manager.remote_reset=lambda email,reset_id:nodes.reset_client_traffic(email,reset_id)
    def apply_global_security(_node_id:str='',_result:dict|None=None):
        result=nodes.reconcile_global_security(local_source_verified=bool(config.direct_source_verified))
        if result.get('changed'):manager.tick(suppress=True)
        return result
    @contextlib.asynccontextmanager
    async def lifespan(app):
        if background:
            manager.start();nodes.start(interval=max(5.0,min(60.0,float(config.poll_seconds))),
                                      sync_provider=lambda node_id:build_node_bundles(node_id),
                                      traffic_callback=lambda node_id,result:manager.tick(suppress=True),
                                      security_callback=apply_global_security)
        yield
        nodes.close();manager.close();engine.close()
    app=FastAPI(title='DARK XRAY',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.manager=manager;app.state.auth=auth;app.state.engine=engine;app.state.nodes=nodes
    public=urlsplit(config.public_origin);panel_path=config.panel_path

    @app.middleware('http')
    async def security(request:Request,call_next):
        raw_path=request.scope.get('path','/') or '/'
        try:subscription_path=str(engine.section('subscription').get('path','/sub'))
        except Exception:subscription_path='/sub'
        subscription_request=raw_path.startswith(subscription_path+'/')
        if subscription_path!='/sub' and raw_path.startswith('/sub/'):
            return JSONResponse({'detail':'Not Found'},404)
        stable_public=(raw_path=='/health' or subscription_request or raw_path.startswith('/node/api/'))
        if subscription_request and subscription_path!='/sub':
            request.scope['path']='/sub'+raw_path[len(subscription_path):]
        if panel_path!='/' and not stable_public:
            if raw_path==panel_path:
                target=panel_path+'/'
                if request.url.query:target+='?'+request.url.query
                return RedirectResponse(target,status_code=307)
            if not raw_path.startswith(panel_path+'/'):
                return JSONResponse({'detail':'Not Found'},404)
            request.scope['path']=raw_path[len(panel_path):] or '/'
        if request.headers.get('host','').lower()!=public.netloc.lower():
            return JSONResponse({'detail':'Unexpected Host'},400)
        origin=request.headers.get('origin')
        if origin and origin!=config.public_origin:
            return JSONResponse({'detail':'Cross-origin request refused'},403)
        if request.method not in ('GET','HEAD','OPTIONS'):
            limit=2*1024*1024
            declared=request.headers.get('content-length')
            if declared and (not declared.isdigit() or int(declared)>limit):return JSONResponse({'detail':'Request too large'},413)
            data=bytearray()
            async for part in request.stream():
                data.extend(part)
                if len(data)>limit:return JSONResponse({'detail':'Request too large'},413)
            request._body=bytes(data)
            if request.url.path.startswith('/api/') and data and 'application/json' not in request.headers.get('content-type',''):
                return JSONResponse({'detail':'JSON content type required'},415)
        response=await call_next(request)
        response.headers.setdefault('Cache-Control','no-store')
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers.setdefault('X-Frame-Options','DENY')
        response.headers.setdefault('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        if config.secure_cookie:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    @app.exception_handler(PolicyError)
    async def policy_error(request,e):return JSONResponse({'detail':str(e)},400)
    @app.exception_handler(PermissionDenied)
    async def denied(request,e):return JSONResponse({'detail':str(e)},403)
    @app.exception_handler(CoreError)
    async def core_error(request,e):return JSONResponse({'detail':str(e),'uncertain':e.uncertain},e.status)
    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request,e):return JSONResponse({'detail':'Conflicting record or identity'},409)

    def current(request:Request)->Principal:
        try:p=auth.current(request.cookies.get(COOKIE),request.headers.get('authorization'))
        except PermissionDenied as e:raise HTTPException(401,str(e))
        if request.method not in ('GET','HEAD') and not p.key_id and request.url.path.startswith('/api/'):
            if not hmac.compare_digest(request.headers.get('x-dark-csrf',''),p.csrf):raise HTTPException(403,'Invalid CSRF token')
        return p
    def interactive(p:Principal=Depends(current))->Principal:
        if p.key_id:raise HTTPException(403,'Interactive session required')
        return p
    def owner(p:Principal=Depends(current))->Principal:
        if p.actor.role!='owner' or p.key_id:raise HTTPException(403,'Interactive owner access required')
        return p
    def writable():
        if not config.writes_enabled:raise HTTPException(409,'Local writes disabled by administrator')

    @app.get('/health')
    def health():return {'service':'DARK XRAY','version':VERSION,'mode':'standalone','test_engine':config.test_engine}

    def update_client()->UpdateBrokerClient:
        return UpdateBrokerClient(timeout=12)

    @app.get('/api/update/status')
    def update_status(p:Principal=Depends(owner)):
        try:return update_client().status()
        except UpdateBrokerError as ex:raise HTTPException(503,str(ex))

    @app.post('/api/update/check')
    def update_check(body:UpdateCheck,p:Principal=Depends(owner)):
        try:
            result=update_client().check(body.channel,body.ref)
            manager.audit(p.actor,p.actor.id,'system.update_check',str(result.get('candidate',{}).get('commit',''))[:40],body.channel)
            return result
        except UpdateBrokerError as ex:raise HTTPException(503,str(ex))

    @app.post('/api/update/start',status_code=202)
    def update_start(body:UpdateStart,p:Principal=Depends(owner)):
        if config.test_engine:raise HTTPException(409,'System update is disabled in test-engine mode')
        try:
            result=update_client().start(body.commit)
            manager.audit(p.actor,p.actor.id,'system.update_start',body.commit)
            return result
        except UpdateBrokerError as ex:raise HTTPException(503,str(ex))
    @app.post('/api/auth/login')
    def login(body:Login,request:Request):
        session_minutes=int(engine.section('panel').get('session_max_age_minutes',480))
        token,p=auth.login(body.username,body.password,body.otp,request.client.host if request.client else 'unknown',session_minutes*60,request.headers.get('user-agent',''))
        response=JSONResponse({'id':p.actor.id,'role':p.actor.role,'csrf':p.csrf,'permissions':p.actor.permissions})
        response.set_cookie(COOKIE,token,max_age=session_minutes*60,httponly=True,secure=config.secure_cookie,samesite='strict',path=panel_path)
        manager.audit(p.actor,p.actor.id,'auth.login',p.actor.id)
        return response
    @app.get('/api/me')
    def me(p:Principal=Depends(current)):
        with store.lock:mfa=store.db.execute('SELECT enabled FROM mfa WHERE admin_id=?',(p.actor.id,)).fetchone()
        return {'id':p.actor.id,'role':p.actor.role,'permissions':p.actor.permissions,'csrf':p.csrf,
            'totp_enabled':bool(mfa and mfa[0]),'version':VERSION,'writes_enabled':config.writes_enabled,
            'poll_seconds':config.poll_seconds,'engine_version':engine.version,'independent':True,'test_engine':config.test_engine,'panel_path':panel_path,
            'ui':engine.section('panel')}
    @app.post('/api/auth/logout')
    def logout(p:Principal=Depends(current)):
        with store.transaction() as db:db.execute('DELETE FROM live_sessions WHERE digest=?',(p.session_id,))
        response=JSONResponse({'revoked':True});response.delete_cookie(COOKIE,path=panel_path);return response
    @app.get('/api/auth/sessions')
    def sessions(p:Principal=Depends(interactive)):
        return auth.sessions(p)
    @app.delete('/api/auth/sessions/{session_id}')
    def revoke_session(session_id:str,p:Principal=Depends(interactive)):
        result=auth.revoke_session(p,session_id);manager.audit(p.actor,p.actor.id,'auth.session.revoke',session_id)
        response=JSONResponse(result)
        if result['current']:response.delete_cookie(COOKIE,path=panel_path)
        return response
    @app.post('/api/auth/sessions/revoke-others')
    def revoke_other_sessions(p:Principal=Depends(interactive)):
        result=auth.revoke_other_sessions(p);manager.audit(p.actor,p.actor.id,'auth.sessions.revoke_others',p.actor.id,str(result['revoked_others']))
        return result

    @app.post('/api/auth/password')
    def password(body:Password,p:Principal=Depends(current)):
        auth.change_password(p,body.old_password,body.new_password)
        return {'sessions_revoked':True}
    @app.post('/api/auth/totp/setup')
    def setup(body:MFASetup,p:Principal=Depends(current)):
        secret=auth.mfa_setup(p,body.password)
        from urllib.parse import quote
        return {'secret':secret,'uri':'otpauth://totp/DARK-XRAY:'+quote(p.actor.id)+'?secret='+secret+'&issuer=DARK-XRAY&digits=6&period=30'}
    @app.post('/api/auth/totp/enable')
    def enable_mfa(body:Code,p:Principal=Depends(current)):
        codes=auth.mfa_enable(p,body.code)
        return {'recovery_codes':codes,'sessions_revoked':True}
    @app.post('/api/auth/totp/disable')
    def disable_mfa(body:MFADisable,p:Principal=Depends(current)):
        auth.mfa_disable(p,body.password,body.code)
        return {'sessions_revoked':True}

    @app.get('/api/keys')
    def keys(p:Principal=Depends(current)):
        p.actor.require('api','manage',p.actor.id)
        with store.lock:return [dict(r) for r in store.db.execute('SELECT id,name,expires_at,revoked,permissions FROM robot_keys WHERE admin_id=?',(p.actor.id,))]
    @app.post('/api/keys')
    def new_key(body:KeyBody,p:Principal=Depends(current)):
        kid,key=auth.new_key(p,body.name,body.permissions,body.days)
        manager.audit(p.actor,p.actor.id,'api_key.create',kid)
        return {'id':kid,'key':key,'displayed_once':True}
    @app.delete('/api/keys/{key_id}')
    def del_key(key_id:str,p:Principal=Depends(current)):
        p.actor.require('api','manage',p.actor.id)
        with store.transaction() as db:
            db.execute('UPDATE robot_keys SET revoked=1 WHERE id=? AND admin_id=?',(key_id,p.actor.id))
        return {'revoked':True}

    @app.get('/api/admins')
    def admins(p:Principal=Depends(owner)):
        with store.lock:rows=[dict(r) for r in store.db.execute('SELECT id,role,permissions,disabled FROM api_admins ORDER BY id')]
        for r in rows:r['permissions']=json.loads(r['permissions'])
        return rows
    @app.post('/api/admins')
    def add_admin(body:AdminBody,p:Principal=Depends(owner)):
        auth.admin_create(p.actor,body.username,body.password,body.role,body.permissions)
        manager.audit(p.actor,body.username,'admin.create',body.username)
        return {'created':True}
    @app.patch('/api/admins/{username}')
    def edit_admin(username:str,body:AdminPatch,p:Principal=Depends(owner)):
        auth.admin_edit(p.actor,username,**body.model_dump())
        manager.audit(p.actor,username,'admin.update',username)
        manager.tick(suppress=True)
        return {'updated':True,'sessions_revoked':True}

    @app.get('/api/owners')
    def owners(p:Principal=Depends(current)):
        with store.lock:ids=[r[0] for r in store.db.execute('SELECT id FROM owner_profiles')]
        return [store.owner_stats(p.actor,i)|manager.profile(i) for i in ids if p.actor.can('owners','read',i)]
    @app.put('/api/owners/{owner_id}')
    def put_owner(owner_id:str,body:OwnerBody,p:Principal=Depends(owner)):
        manager.owner_put(p.actor,owner_id,**body.model_dump())
        return {'saved':True,'engine_error':manager.last_error or None}
    @app.post('/api/owners/{owner_id}/credit')
    def credit(owner_id:str,body:Credit,p:Principal=Depends(owner)):
        result=store.credit(p.actor,owner_id,body.amount,body.event_id)
        if result:
            manager.audit(p.actor,owner_id,'finance.credit',owner_id,f'amount={body.amount}; event={body.event_id}')
        return {'recorded':result}
    @app.post('/api/owners/{owner_id}/reset-period')
    def reset_period(owner_id:str,p:Principal=Depends(owner)):
        manager.tick(suppress=False);store.reset_owner_period(p.actor,owner_id)
        manager.audit(p.actor,owner_id,'owner.reset_period',owner_id,'Historical ledger preserved')
        manager.tick(suppress=True);return {'reset':True}

    @app.get('/api/clients')
    def clients(p:Principal=Depends(current)):return manager.list(p.actor)
    @app.post('/api/clients',status_code=202)
    def add_client(body:ClientBody,p:Principal=Depends(current)):
        writable();return manager.create(p.actor,body.owner,body.client,body.inboundIds)
    @app.post('/api/adopt')
    def adopt(body:Adopt,p:Principal=Depends(owner)):
        writable();return manager.adopt(p.actor,body.owner,body.email)
    @app.post('/api/clients/bulk')
    def bulk(body:Bulk,p:Principal=Depends(current)):
        writable()
        action='delete' if body.action=='delete' else 'reset' if body.action=='reset' else 'edit'
        for email in body.emails:manager.own_row(p.actor,email,action)
        out=[]
        for email in dict.fromkeys(body.emails):
            try:out.append({'email':email,'result':manager.action(p.actor,email,body.action)})
            except (PolicyError,CoreError) as e:out.append({'email':email,'error':str(e)[:300]})
        return out
    @app.get('/api/groups')
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
        writable();payloads=[]
        for offset in range(body.quantity):
            email=f'{body.prefix}{body.first+offset}{body.postfix}'.strip().lower()
            payload=dict(body.client);payload['email']=email;payloads.append(payload)
        return manager.create_batch(p.actor,body.owner,payloads,body.inboundIds)

    @app.post('/api/clients/bulk-adjust')
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

    @app.post('/api/clients/bulk-inbounds')
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

    @app.get('/api/clients/{email}')
    def client(email:str,p:Principal=Depends(current)):
        return manager.detail(p.actor,email,credentials=True)
    @app.patch('/api/clients/{email}',status_code=202)
    def patch_client(email:str,body:ClientPatch,p:Principal=Depends(current)):
        writable()
        if body.inboundIds is not None:manager.own_row(p.actor,email,'attach')
        result=manager.update(p.actor,email,body.client,body.inboundIds)
        apply_global_security()
        return manager.detail(p.actor,email)
    @app.post('/api/clients/{email}/action',status_code=202)
    def action(email:str,body:Action,p:Principal=Depends(current)):
        writable();return manager.action(p.actor,email,body.action)
    @app.post('/api/clients/{email}/resolve-reset')
    def resolve_reset(email:str,body:ResolveReset,p:Principal=Depends(owner)):
        writable();return manager.resolve_reset(p.actor,email,body.confirmation)


    def rewrite_failover_uri(uri:str,address:str,remark:str)->str:
        if uri.startswith('vmess://'):
            raw=uri[8:]
            try:
                doc=json.loads(base64.b64decode(raw+'='*((4-len(raw)%4)%4)).decode())
                doc['add']=address;doc['ps']=remark
                return 'vmess://'+base64.b64encode(json.dumps(doc,separators=(',',':'),ensure_ascii=False).encode()).decode()
            except Exception as ex:raise PolicyError('Cannot rewrite VMess failover link') from ex
        p=urlsplit(uri)
        if p.scheme not in {'vless','trojan','ss'}:raise PolicyError('Unsupported failover link protocol')
        userinfo=(p.netloc.rsplit('@',1)[0]+'@') if '@' in p.netloc else ''
        host='['+address+']' if ':' in address and not address.startswith('[') else address
        port=p.port
        netloc=userinfo+host+((':'+str(port)) if port else '')
        return urlunsplit((p.scheme,netloc,p.path,p.query,quote(remark)))

    def failover_links(email:str)->list[dict]:
        targets=nodes.failover_targets(email)
        if not targets:return []
        base=engine.links(email,'raw');out=[]
        for item in base['links']:
            inbound_id=int(item.get('inboundId') or 0)
            for target in targets:
                if inbound_id not in target['inbound_ids']:continue
                remark=str(item['remark'])+' · '+str(target['name'])+' ['+str(target['node_id'])+']'
                clone={k:json.loads(json.dumps(v)) for k,v in item.items() if k!='uri'}
                clone['remark']=remark
                clone['uri']=rewrite_failover_uri(item['uri'],target['address'],remark)
                clone['failoverNode']=target['node_id'];clone['failoverPriority']=target['priority']
                clone['failoverLatencyMs']=target['latency_ms'];out.append(clone)
        return out

    @app.get('/api/clients/{email}/links')
    def links(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'credentials')
        result=engine.links(email);result['failover']=failover_links(email)
        detail=manager.detail(p.actor,email)
        return {'engine':result,'subscription_url':detail['subscription_url']}
    @app.get('/api/clients/{email}/security-global')
    def global_security(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'ip')
        return nodes.global_security(email,local_source_verified=bool(config.direct_source_verified))
    @app.get('/api/clients/{email}/ips')
    def ips(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'ip')
        return engine.ips(email)
    @app.delete('/api/clients/{email}/ips')
    def clear_ips(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'ip');writable()
        remote=nodes.clear_remote_security(email,'ips');result=engine.clear_ips(email)
        global_state=apply_global_security()
        manager.audit(p.actor,manager.own_row(p.actor,email)['owner'],'ip.history_clear',email,'Global node history cleared; not a firewall unban')
        return {'engine':result,'remote':remote,'global':global_state,'firewall_unban':False}
    @app.get('/api/clients/{email}/devices')
    def devices(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'ip');return engine.devices(email)
    @app.delete('/api/clients/{email}/devices')
    def clear_devices(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'ip');writable()
        remote=nodes.clear_remote_security(email,'devices');result=engine.clear_devices(email)
        global_state=apply_global_security()
        return {'engine':result,'remote':remote,'global':global_state}
    @app.delete('/api/clients/{email}/devices/{device_id}')
    def del_device(email:str,device_id:int,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'ip');writable()
        if device_id<1:raise HTTPException(400,'Invalid device ID')
        return {'engine':engine.clear_devices(email,device_id)}

    def node_agent(request:Request):
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

    def mirror_email(token_id:str,source_email:str)->str:
        if not isinstance(source_email,str) or not source_email or len(source_email)>128:
            raise PolicyError('Invalid mirrored client identity')
        return 'nm_'+token_id[:8]+'_'+hashlib.sha256(source_email.encode()).hexdigest()[:20]

    def mirror_client_payload(raw:dict,mirror_id:str)->dict:
        if not isinstance(raw,dict):raise PolicyError('Invalid mirrored client payload')
        allowed={'id','password','flow','encryption','security','enable'}
        if set(raw)-allowed:raise PolicyError('Unexpected mirrored client field')
        out={k:v for k,v in raw.items() if k in allowed}
        out['email']=mirror_id
        out['enable']=bool(out.get('enable',True))
        if 'id' in out and (not isinstance(out['id'],str) or len(out['id'])>128):raise PolicyError('Invalid mirrored UUID')
        if 'password' in out and (not isinstance(out['password'],str) or len(out['password'])>512):raise PolicyError('Invalid mirrored password')
        if 'flow' in out and (not isinstance(out['flow'],str) or len(out['flow'])>80):raise PolicyError('Invalid mirrored flow')
        return out

    def build_node_bundles(node_id:str)->list[dict]:
        assignments=nodes.assignments(node_id);all_clients=engine.clients();bundles=[]
        for assignment in assignments:
            source=int(assignment['local_inbound_id']);ib=engine.inbound(source)
            inbound={k:json.loads(json.dumps(v)) for k,v in ib.items() if k not in {'id','applied'}}
            clients=[]
            for client in all_clients:
                if source not in client.get('inboundIds',[]):continue
                raw={k:client[k] for k in ('id','password','flow','encryption','security','enable') if k in client}
                clients.append({'sourceEmail':client['email'],'client':raw})
            bundles.append({'sourceInboundId':source,'inbound':inbound,'clients':clients})
        return bundles

    def sync_node_assignments(node_id:str)->dict:
        pre=nodes.sync_traffic(node_id)
        if pre.get('charged_bytes'):manager.tick(suppress=True)
        security_pre=None
        try:
            security_pre=nodes.sync_security(node_id);apply_global_security(node_id,security_pre)
        except PolicyError:
            pass
        bundles=build_node_bundles(node_id)
        result=nodes.sync_mirrors(node_id,bundles)
        post=nodes.sync_traffic(node_id)
        if post.get('charged_bytes'):manager.tick(suppress=True)
        security_post=None
        try:
            security_post=nodes.sync_security(node_id);apply_global_security(node_id,security_post)
        except PolicyError:
            pass
        result['traffic']={
            'charged_bytes':int(pre.get('charged_bytes',0))+int(post.get('charged_bytes',0)),
            'charged_up':int(pre.get('charged_up',0))+int(post.get('charged_up',0)),
            'charged_down':int(pre.get('charged_down',0))+int(post.get('charged_down',0)),
            'baselined':int(pre.get('baselined',0))+int(post.get('baselined',0)),
            'ignored_clients':int(pre.get('ignored_clients',0))+int(post.get('ignored_clients',0)),
            'pre':pre,'post':post,
        }
        result['security']={'pre':security_pre,'post':security_post}
        return result

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
    @app.post('/node/api/inbounds')
    def node_inbound_add(body:dict,token_id:str=Depends(node_agent)):
        writable()
        result=engine.save_inbound(body)
        return result
    @app.post('/node/api/mirrors/sync')
    def node_mirror_sync(body:NodeMirrorSync,token_id:str=Depends(node_agent)):
        writable();now=time.time();assignments=body.assignments
        seen=set();total_clients=0
        for item in assignments:
            if not isinstance(item,dict) or set(item)!={'sourceInboundId','inbound','clients'}:
                raise HTTPException(400,'Invalid node mirror assignment')
            source=item.get('sourceInboundId')
            if type(source)is not int or source<1 or source in seen:raise HTTPException(400,'Invalid/duplicate source inbound ID')
            seen.add(source)
            if not isinstance(item.get('inbound'),dict) or not isinstance(item.get('clients'),list):raise HTTPException(400,'Invalid node mirror payload')
            total_clients+=len(item['clients'])
        if total_clients>50000:raise HTTPException(413,'Too many mirrored clients')
        payload_hash=hashlib.sha256(json.dumps(assignments,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

        with store.lock:
            old_mirrors={int(r['source_inbound_id']):dict(r) for r in store.db.execute(
                'SELECT * FROM node_agent_mirrors WHERE token_id=?',(token_id,))}
            old_client_rows=[dict(r) for r in store.db.execute(
                'SELECT * FROM node_agent_mirror_clients WHERE token_id=?',(token_id,))]
            state_row=store.db.execute('SELECT payload_hash FROM node_agent_mirror_state WHERE token_id=?',(token_id,)).fetchone()
        if state_row and state_row['payload_hash']==payload_hash and len(old_mirrors)==len(assignments) and len(old_client_rows)==total_clients:
            healthy=True
            try:
                for row in old_mirrors.values():engine.inbound(int(row['remote_inbound_id']))
                for row in old_client_rows:engine.client_detail(row['mirror_email'])
            except CoreError:
                healthy=False
            if healthy:
                counts={}
                for row in old_client_rows:counts[int(row['source_inbound_id'])]=counts.get(int(row['source_inbound_id']),0)+1
                items=[{'sourceInboundId':source,'remoteInboundId':int(row['remote_inbound_id']),'clients':counts.get(source,0)}
                       for source,row in sorted(old_mirrors.items())]
                return {'mirrored':len(assignments),'clients':len({r['mirror_email'] for r in old_client_rows}),
                        'items':items,'core':engine.runtime_state(),'changed':False,'payloadHash':payload_hash}
        old_mirror_emails={r['mirror_email'] for r in old_client_rows}
        # Remove old mirrored credentials first. The running Xray process stays on
        # its previous active.json until the final validated restart succeeds.
        for email in sorted(old_mirror_emails):
            try:engine.delete(email)
            except CoreError as ex:
                if getattr(ex,'status',0)!=404:raise

        result_items=[];remote_ids={};desired_rows=[];desired_clients={}
        for item in assignments:
            source=int(item['sourceInboundId']);incoming=json.loads(json.dumps(item['inbound']))
            for key in ('id','applied','nodeId','up','down','total'):incoming.pop(key,None)
            settings=incoming.get('settings')
            if isinstance(settings,dict):
                settings.pop('clients',None);settings.pop('accounts',None)
            source_tag=str(incoming.get('tag') or ('source-'+str(source)))
            incoming['tag']='nm-'+token_id[:8]+'-'+str(source)+'-'+hashlib.sha256(source_tag.encode()).hexdigest()[:8]
            old=old_mirrors.get(source);remote_id=int(old['remote_inbound_id']) if old else None
            try:
                saved=engine.save_inbound(incoming,remote_id) if remote_id else engine.save_inbound(incoming)
                remote_id=int(saved['id']);remote_ids[source]=remote_id
                result_items.append({'sourceInboundId':source,'remoteInboundId':remote_id,'clients':len(item['clients'])})
            except CoreError as ex:
                result_items.append({'sourceInboundId':source,'remoteInboundId':remote_id or 0,'clients':0,'error':str(ex)[:300]})
                raise
            for entry in item['clients']:
                if not isinstance(entry,dict) or set(entry)!={'sourceEmail','client'}:raise HTTPException(400,'Invalid mirrored client entry')
                source_email=str(entry['sourceEmail'])
                mid=mirror_email(token_id,source_email);payload=mirror_client_payload(entry['client'],mid)
                row=desired_clients.setdefault(mid,{'payload':payload,'inbounds':set(),'sourceEmail':source_email})
                if row['payload']!=payload:raise HTTPException(409,'Conflicting mirrored credential payload')
                row['inbounds'].add(remote_id);desired_rows.append((token_id,source,mid,source_email))

        # Remove remote mirror inbounds deselected by Central, after their mirror
        # clients were removed above.
        removed=set(old_mirrors)-set(remote_ids)
        for source in sorted(removed):
            try:engine.delete_inbound(int(old_mirrors[source]['remote_inbound_id']))
            except CoreError as ex:
                if getattr(ex,'status',0)!=404:raise

        for mid,row in desired_clients.items():
            try:
                existing=engine.client_detail(mid)
            except CoreError as ex:
                if getattr(ex,'status',0)==404:existing=None
                else:raise
            if existing and mid not in old_mirror_emails:raise HTTPException(409,'Mirror identity collision on node')
            if existing:engine.delete(mid)
            engine.create(row['payload'],sorted(row['inbounds']))

        with store.transaction() as db:
            db.execute('DELETE FROM node_agent_mirror_clients WHERE token_id=?',(token_id,))
            db.execute('DELETE FROM node_agent_mirrors WHERE token_id=?',(token_id,))
            for item in assignments:
                source=int(item['sourceInboundId']);rid=remote_ids[source]
                db.execute('INSERT INTO node_agent_mirrors(token_id,source_inbound_id,remote_inbound_id,source_tag,updated_at) VALUES(?,?,?,?,?)',
                           (token_id,source,rid,str(item['inbound'].get('tag') or ''),now))
            db.executemany('INSERT INTO node_agent_mirror_clients(token_id,source_inbound_id,mirror_email,source_email) VALUES(?,?,?,?)',desired_rows)
            db.execute('''INSERT INTO node_agent_mirror_state(token_id,payload_hash,updated_at) VALUES(?,?,?)
                          ON CONFLICT(token_id) DO UPDATE SET payload_hash=excluded.payload_hash,updated_at=excluded.updated_at''',
                       (token_id,payload_hash,now))

        core_result=engine.command('restart')
        return {'mirrored':len(assignments),'clients':len(desired_clients),'items':result_items,'core':core_result,
                'changed':True,'payloadHash':payload_hash}



    @app.get('/node/api/mirrors/security')
    def node_mirror_security(token_id:str=Depends(node_agent)):
        engine.read_ip_log()
        with store.lock:
            mappings=[dict(r) for r in store.db.execute(
                'SELECT DISTINCT mirror_email,source_email FROM node_agent_mirror_clients WHERE token_id=? ORDER BY source_email',
                (token_id,))]
        items=[]
        for mapping in mappings:
            with store.lock:
                ips=[{'ip':r['ip'],'firstSeen':float(r['first_seen']),'lastSeen':float(r['last_seen'])}
                     for r in store.db.execute(
                        'SELECT ip,first_seen,last_seen FROM observations WHERE client_id=? ORDER BY last_seen DESC',
                        (mapping['mirror_email'],))]
                devices=[{'digest':r['digest'],'deviceOs':r['device_os'],'model':r['model'],
                          'firstSeen':float(r['first_seen']),'lastSeen':float(r['last_seen'])}
                         for r in store.db.execute(
                            'SELECT digest,device_os,model,first_seen,last_seen FROM core_devices WHERE email=? ORDER BY last_seen DESC',
                            (mapping['mirror_email'],))]
            items.append({'sourceEmail':mapping['source_email'],'ips':ips,'devices':devices})
        return {'sourceVerified':bool(config.direct_source_verified and not engine.ip_error),
                'items':items,'capturedAt':time.time()}

    @app.post('/node/api/mirrors/security/clear')
    def node_mirror_security_clear(body:NodeMirrorSecurityClear,token_id:str=Depends(node_agent)):
        writable()
        with store.lock:
            mirrors=[r[0] for r in store.db.execute(
                'SELECT DISTINCT mirror_email FROM node_agent_mirror_clients WHERE token_id=? AND source_email=?',
                (token_id,body.sourceEmail))]
        cleared_ips=cleared_devices=0
        with store.transaction() as db:
            for mirror in mirrors:
                if body.kind in {'ips','all'}:
                    cur=db.execute('DELETE FROM observations WHERE client_id=?',(mirror,));cleared_ips+=max(0,cur.rowcount)
                if body.kind in {'devices','all'}:
                    cur=db.execute('DELETE FROM core_devices WHERE email=?',(mirror,));cleared_devices+=max(0,cur.rowcount)
        return {'sourceEmail':body.sourceEmail,'kind':body.kind,'ips':cleared_ips,'devices':cleared_devices}

    @app.get('/node/api/mirrors/traffic')
    def node_mirror_traffic(token_id:str=Depends(node_agent)):
        rows=engine.clients();by_email={r['email']:r for r in rows}
        with store.lock:
            mappings=store.db.execute('''SELECT DISTINCT mirror_email,source_email FROM node_agent_mirror_clients
                                         WHERE token_id=? ORDER BY source_email''',(token_id,)).fetchall()
        items=[]
        for row in mappings:
            rec=by_email.get(row['mirror_email'])
            if not rec:continue
            up,down=CoreEngine.counters(rec)
            items.append({'sourceEmail':row['source_email'],'up':up,'down':down})
        return {'items':items,'capturedAt':time.time()}

    @app.post('/node/api/mirrors/traffic/reset')
    def node_mirror_traffic_reset(body:NodeMirrorTrafficReset,token_id:str=Depends(node_agent)):
        writable()
        with node_reset_lock:
            with store.lock:
                cached=store.db.execute('SELECT * FROM node_agent_traffic_resets WHERE token_id=? AND reset_id=?',
                                        (token_id,body.resetId)).fetchone()
            if cached:
                if cached['source_email']!=body.sourceEmail:raise HTTPException(409,'Traffic reset ID was reused for another client')
                return {'sourceEmail':cached['source_email'],'up':int(cached['up_bytes']),'down':int(cached['down_bytes']),
                        'capturedAt':float(cached['at']),'cached':True}
            with store.lock:
                mirrors=[r[0] for r in store.db.execute('''SELECT DISTINCT mirror_email FROM node_agent_mirror_clients
                    WHERE token_id=? AND source_email=?''',(token_id,body.sourceEmail))]
            if len(mirrors)!=1:raise HTTPException(404,'Mirrored client is not present for this node token')
            final=engine.reset(mirrors[0])
            if not final:raise HTTPException(404,'Mirrored client traffic state is missing')
            up,down=CoreEngine.counters(final);captured=time.time()
            with store.transaction() as db:
                db.execute('INSERT INTO node_agent_traffic_resets(token_id,reset_id,source_email,up_bytes,down_bytes,at) VALUES(?,?,?,?,?,?)',
                           (token_id,body.resetId,body.sourceEmail,up,down,captured))
            return {'sourceEmail':body.sourceEmail,'up':up,'down':down,'capturedAt':captured,'cached':False}

    @app.post('/node/api/core/{action}')
    def node_core(action:str,token_id:str=Depends(node_agent)):
        writable()
        if action not in {'validate','restart','start','stop'}:raise HTTPException(404,'Unknown node core action')
        return {'engine':engine.command(action),'node_agent':True}

    @app.get('/api/nodes')
    def remote_nodes(p:Principal=Depends(owner)):return nodes.list()
    @app.post('/api/nodes')
    def remote_node_add(body:NodeCreate,p:Principal=Depends(owner)):
        writable()
        known={i['id'] for i in engine.inbounds()}
        if not set(body.inboundIds)<=known:raise HTTPException(400,'Unknown inbound assignment')
        result=nodes.put(body.id,body.name,body.origin,body.token,body.enabled,body.inboundIds,
                         body.dataAddress,body.priority,body.failoverEnabled)
        manager.audit(p.actor,p.actor.id,'node.create',body.id)
        return result
    @app.patch('/api/nodes/{node_id}')
    def remote_node_edit(node_id:str,body:NodePatch,p:Principal=Depends(owner)):
        writable();token=body.token
        if not token:
            if not body.keep_token:raise HTTPException(400,'Provide a replacement token or keep_token=true')
            token=nodes.get(node_id,secret=True)['token']
        known={i['id'] for i in engine.inbounds()}
        if not set(body.inboundIds)<=known:raise HTTPException(400,'Unknown inbound assignment')
        result=nodes.put(node_id,body.name,body.origin,token,body.enabled,body.inboundIds,
                         body.dataAddress,body.priority,body.failoverEnabled)
        apply_global_security()
        manager.audit(p.actor,p.actor.id,'node.update',node_id);return result
    @app.delete('/api/nodes/{node_id}')
    def remote_node_delete(node_id:str,p:Principal=Depends(owner)):
        writable();result=nodes.delete(node_id);apply_global_security()
        manager.audit(p.actor,p.actor.id,'node.delete',node_id);return result
    @app.post('/api/nodes/{node_id}/probe')
    def remote_node_probe(node_id:str,p:Principal=Depends(owner)):
        result=nodes.probe(node_id);manager.audit(p.actor,p.actor.id,'node.probe',node_id);return result
    @app.post('/api/nodes/{node_id}/sync')
    def remote_node_sync(node_id:str,p:Principal=Depends(owner)):
        writable();result=sync_node_assignments(node_id)
        manager.audit(p.actor,p.actor.id,'node.sync',node_id,
                      'inbounds='+str(len(result.get('items',[])))+'; traffic='+str(result.get('traffic',{}).get('charged_bytes',0)))
        return result
    @app.post('/api/nodes/{node_id}/traffic')
    def remote_node_traffic(node_id:str,p:Principal=Depends(owner)):
        result=nodes.sync_traffic(node_id)
        if result.get('charged_bytes'):manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'node.traffic_sync',node_id,str(result.get('charged_bytes',0)))
        return result
    @app.post('/api/nodes/{node_id}/security')
    def remote_node_security(node_id:str,p:Principal=Depends(owner)):
        result=nodes.sync_security(node_id);global_state=apply_global_security(node_id,result)
        manager.audit(p.actor,p.actor.id,'node.security_sync',node_id,
                      'ips='+str(result.get('ips',0))+'; devices='+str(result.get('devices',0)))
        return {'node':result,'global':global_state}
    @app.get('/api/nodes/{node_id}/inbounds')
    def remote_node_inbounds(node_id:str,p:Principal=Depends(owner)):return nodes.remote_inbounds(node_id)
    @app.post('/api/nodes/{node_id}/inbounds')
    def remote_node_deploy_inbound(node_id:str,body:dict,p:Principal=Depends(owner)):
        writable();result=nodes.deploy_inbound(node_id,body)
        manager.audit(p.actor,p.actor.id,'node.inbound.deploy',node_id,str(result['inbound'].get('id','')))
        return result
    @app.post('/api/nodes/{node_id}/core/{action}')
    def remote_node_core(node_id:str,action:str,p:Principal=Depends(owner)):
        writable();result=nodes.remote_core(node_id,action);manager.audit(p.actor,p.actor.id,'node.core.'+action,node_id);return result

    @app.get('/api/inbounds')
    def inbounds(p:Principal=Depends(current)):
        rows=engine.inbounds();allowed=None
        if p.actor.role!='owner':
            p.actor.require('inbounds','read',p.actor.id)
            allowed=set(manager.profile(p.actor.id)['allowed']) if p.actor.permissions.get('inbounds.read')!='all' else None
        keys={'id','remark','protocol','port','listen','enable','tag','nodeId','up','down','total','expiryTime'}
        out=[]
        for r in rows:
            if allowed is not None and r['id'] not in allowed:continue
            item={k:v for k,v in r.items() if k in keys}
            stream=r.get('streamSettings',{}) if isinstance(r.get('streamSettings',{}),dict) else {}
            item['network']=stream.get('network','tcp')
            item['security']=stream.get('security','none')
            out.append(item)
        return out
    @app.get('/api/unmanaged')
    def unmanaged(p:Principal=Depends(owner)):
        with store.lock:managed={r[0] for r in store.db.execute('SELECT email FROM managed_clients')}
        return [{'email':r['email'],'inboundIds':r.get('inboundIds',[]),'enable':r.get('enable',False)} for r in engine.clients() if r['email'] not in managed]
    @app.get('/api/system')
    def system(p:Principal=Depends(current)):
        p.actor.require('system','read')
        return {'source':'DARK local host + Xray-core','sample':False,'engine':engine.system()}
    @app.get('/api/gateway-system')
    def gateway_system(p:Principal=Depends(owner)):
        mem=psutil.virtual_memory();disk=psutil.disk_usage('/');swap=psutil.swap_memory()
        return {'source':'DARK API host','sample':False,'cpu':psutil.cpu_percent(interval=.05),
                'memory_percent':mem.percent,'disk_percent':disk.percent,'swap_percent':swap.percent,
                'uptime':int(time.time()-psutil.boot_time())}
    @app.get('/api/ip-status')
    def ip_status(p:Principal=Depends(owner)):
        return {'source':'DARK IP Guard','engine':engine.ip_status(),
            'limit_unit':'recent distinct source IPs','global_multi_node_limit':False,'packet_test_performed_here':False}
    @app.get('/api/sync')
    def sync(p:Principal=Depends(current)):
        with store.lock:
            rows=[dict(r) for r in store.db.execute('SELECT m.email,m.op,m.state,m.error,m.updated_at,c.owner FROM managed_clients m LEFT JOIN clients c ON c.id=m.email ORDER BY m.updated_at DESC LIMIT 250')]
        runtime=engine.runtime_state()
        if p.actor.role!='owner':
            runtime={k:runtime.get(k) for k in ('state','running','dirty','desired_running')}
        return {'last_poll':manager.last_poll,'error':manager.last_error if p.actor.role=='owner' else ('CoreEngine synchronization unavailable' if manager.last_error else ''),
                'writes_enabled':config.writes_enabled,'engine_version':engine.version,'runtime':runtime,
                'items':[r for r in rows if p.actor.role=='owner' or p.actor.can('clients','read',r['owner'])]}
    @app.post('/api/sync')
    def force_sync(p:Principal=Depends(owner)):
        manager.tick(suppress=False);return {'last_poll':manager.last_poll}
    @app.post('/api/core/{action}')
    def core(action:str,p:Principal=Depends(owner)):
        writable()
        result=engine.command(action);manager.audit(p.actor,p.actor.id,'core.'+action,'local-xray')
        return {'engine':result,'local_process':True}
    @app.get('/api/audit')
    def audit(p:Principal=Depends(current)):
        p.actor.require('audit','read',p.actor.id)
        all_=p.actor.role=='owner' or p.actor.permissions.get('audit.read')=='all'
        with store.lock:return [dict(r) for r in store.db.execute('SELECT * FROM live_audit'+('' if all_ else ' WHERE owner=?')+' ORDER BY id DESC LIMIT 250',() if all_ else (p.actor.id,))]
    @app.get('/api/ledger/{kind}')
    def ledger(kind:Literal['traffic','money'],p:Principal=Depends(current)):
        resource='finance' if kind=='money' else 'owners';p.actor.require(resource,'read',p.actor.id)
        all_=p.actor.role=='owner' or p.actor.permissions.get(resource+'.read')=='all'
        with store.lock:return [dict(r) for r in store.db.execute('SELECT * FROM '+('money_ledger' if kind=='money' else 'traffic_ledger')+('' if all_ else ' WHERE owner=?')+' ORDER BY rowid DESC LIMIT 250',() if all_ else (p.actor.id,))]

    @app.get('/api/logs/{kind}')
    def logs(kind:Literal['process','error','access'],limit:int=400,p:Principal=Depends(owner)):
        if not 1<=limit<=2000:raise HTTPException(400,'Log line limit must be 1..2000')
        names={'process':'process.log','error':'error.log','access':'access.log'}
        path=engine.runtime/names[kind]
        if path.is_symlink():raise HTTPException(409,'Runtime log symlink refused')
        if not path.exists():return {'kind':kind,'exists':False,'lines':[]}
        try:
            size=path.stat().st_size
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
                'database_download':(config.panel_path if config.panel_path!='/' else '')+'/api/backup','full_backup_command':'sudo darkxray backup --output /root/dark-full.darkbackup',
                'restore_isolated':True}

    @app.get('/api/backup')
    def backup(p:Principal=Depends(owner)):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'dark.sqlite3';store.backup(path)
            data=path.read_bytes();stream=io.BytesIO()
            manifest={'version':VERSION,'scope':'standalone-dark-database','runtime_tables_included':True,
                'sha256':hashlib.sha256(data).hexdigest(),'requires_original_mfa_key':True}
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('dark.sqlite3',data);z.writestr('manifest.json',json.dumps(manifest,indent=2))
        manager.audit(p.actor,p.actor.id,'backup.database','dark','MFA encryption key, certificates, runtime files and config must be backed up separately')
        return Response(stream.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="DARK-standalone-backup.zip"'})

    @app.get('/sub/{public_token}')
    def subscription(public_token:str,request:Request):
        if not SUB_RE.fullmatch(public_token):raise HTTPException(404)
        with store.lock:
            row=store.db.execute("SELECT * FROM managed_clients WHERE public_token=? AND state!='deleted'",(public_token,)).fetchone()
        if not row:raise HTTPException(404)
        if store.client_reasons(row['email']) or row['external_disabled']:raise HTTPException(403,'Subscription suspended')
        if row['state']!='applied':raise HTTPException(503,'Customer configuration has not been saved to the runtime')
        engine.check_device(row['email'],request.headers.get('x-hwid',''),request.headers.get('x-device-os',''),request.headers.get('x-device-model',''))
        sub=engine.section('subscription')
        if not sub.get('enabled',True):raise HTTPException(404)
        fmt=request.query_params.get('format')
        if not fmt:
            ua=request.headers.get('user-agent','').lower()
            if sub.get('auto_detect',True) and any(x in ua for x in ('clash','mihomo')):fmt='clash'
            else:fmt=sub.get('default_format','base64')
        extra=failover_links(row['email'])
        body,headers=engine.subscription(row['email'],fmt,extra_links=extra)
        if extra:headers['x-dark-failover-nodes']=str(len({x['failoverNode'] for x in extra}))
        return Response(body,headers=headers)

    @app.get('/api/inbounds/{inbound_id}')
    def get_inbound(inbound_id:int,p:Principal=Depends(owner)):
        return engine.inbound(inbound_id)

    @app.post('/api/inbounds')
    def add_inbound(body:dict,p:Principal=Depends(owner)):
        writable();result=engine.save_inbound(body)
        try:profile=manager.profile(p.actor.id)
        except PolicyError:profile={'name':p.actor.id,'allowed':[]}
        with store.lock:stat=store.db.execute('SELECT * FROM owners WHERE id=?',(p.actor.id,)).fetchone()
        manager.owner_put(p.actor,p.actor.id,name=profile['name'],allowed=profile['allowed']+[result['id']],
            quota_bytes=stat['quota_bytes'] if stat else 0,max_clients=stat['max_clients'] if stat else 0)
        manager.audit(p.actor,p.actor.id,'inbound.create',str(result['id']))
        return result

    @app.put('/api/inbounds/{inbound_id}')
    def put_inbound(inbound_id:int,body:dict,p:Principal=Depends(owner)):
        writable();result=engine.save_inbound(body,inbound_id);manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'inbound.update',str(inbound_id));return result

    @app.delete('/api/inbounds/{inbound_id}')
    def delete_inbound(inbound_id:int,p:Principal=Depends(owner)):
        writable();result=engine.delete_inbound(inbound_id)
        with store.transaction() as db:
            for row in db.execute('SELECT id,allowed FROM owner_profiles').fetchall():
                db.execute('UPDATE owner_profiles SET allowed=? WHERE id=?',(json.dumps([i for i in json.loads(row['allowed']) if i!=inbound_id]),row['id']))
        manager.tick(suppress=True);manager.audit(p.actor,p.actor.id,'inbound.delete',str(inbound_id));return result

    @app.get('/api/settings/{section}')
    def get_setting(section:str,p:Principal=Depends(owner)):
        return {'section':section,'value':engine.section(section)}

    @app.put('/api/settings/{section}')
    def put_setting(section:str,body:dict,p:Principal=Depends(owner)):
        writable()
        if set(body)!={'value'}:raise HTTPException(400,'Expected one value field')
        result=engine.save_section(section,body['value'])
        if section in {'outbounds','routing','dns','policy','observatory','hosts','ipguard'}:manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'settings.update',section);return result

    @app.get('/api/runtime-config')
    def runtime_config(p:Principal=Depends(owner)):
        desired=engine.section('runtime');origin=urlsplit(config.public_origin);mode='domain_tls' if origin.scheme=='https' else 'ssh'
        actual={'access_mode':mode,'bind_host':config.bind_host,'bind_port':config.bind_port,'public_address':config.public_address,
                'public_origin':config.public_origin,'panel_path':config.panel_path,'panel_url':config.public_origin+(config.panel_path if config.panel_path!='/' else '')+'/',
                'poll_seconds':config.poll_seconds,'core_autostart':config.core_autostart,
                'domain':(origin.hostname or '') if mode=='domain_tls' else '','tls_enabled':bool(config.tls_certificate and config.tls_private_key),
                'tls_certificate':config.tls_certificate,'secure_cookie':config.secure_cookie,'xray_api_port':config.xray_api_port,
                'direct_source_verified':config.direct_source_verified,'guard_socket':config.guard_socket}
        compare=('access_mode','bind_port','public_address','panel_path','poll_seconds','core_autostart','domain')
        pending={k:{'from':actual.get(k),'to':desired.get(k)} for k in compare if actual.get(k)!=desired.get(k)}
        return {'actual':actual,'desired':desired,'pending':pending,'apply_command':'sudo darkxray settings-apply'}

    @app.get('/api/core/state')
    def core_state(p:Principal=Depends(owner)):return engine.runtime_state()

    @app.get('/api/core/config')
    def core_config(p:Principal=Depends(owner)):return engine.build_config()

    @app.post('/api/keys/x25519')
    def core_key(p:Principal=Depends(owner)):
        import base64
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        key=X25519PrivateKey.generate()
        return {'privateKey':base64.urlsafe_b64encode(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())).decode().rstrip('='),
            'publicKey':base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode().rstrip('='),
            'shortId':secrets.token_hex(8)}

    @app.post('/api/reality/scan')
    def reality_scan(body:RealityProbe,p:Principal=Depends(owner)):
        try:result=scan_target(body.target)
        except RealityScanError as ex:raise HTTPException(400,str(ex))
        manager.audit(p.actor,p.actor.id,'reality.scan',body.target[:300])
        return result

    @app.post('/api/reality/search')
    def reality_search(body:RealitySearch,p:Principal=Depends(owner)):
        try:items=search_targets(body.targets or None)
        except RealityScanError as ex:raise HTTPException(400,str(ex))
        manager.audit(p.actor,p.actor.id,'reality.search',str(len(body.targets or [])))
        return {'items':items,'source':'server-tls-probe','cidr_scan':False}

    @app.post('/api/ip/unban')
    def ip_unban(body:UnbanIP,p:Principal=Depends(owner)):
        writable();result=engine.unban_ip(body.ip)
        manager.audit(p.actor,p.actor.id,'ip.unban',body.ip,'Address/port effect can span accounts behind NAT')
        return result

    @app.get('/api/ip/events')
    def ip_events(p:Principal=Depends(current)):
        p.actor.require('clients','ip',p.actor.id)
        all_=p.actor.role=='owner' or p.actor.permissions.get('clients.ip')=='all'
        with store.lock:
            events=[dict(r) for r in store.db.execute('SELECT * FROM events'+('' if all_ else ' WHERE owner=?')+' ORDER BY id DESC LIMIT 200',() if all_ else (p.actor.id,))]
            bans=[dict(r) for r in store.db.execute("SELECT b.* FROM bans b JOIN clients c ON c.id=b.client_id WHERE b.expires_at>?"+('' if all_ else ' AND c.owner=?')+' ORDER BY expires_at DESC LIMIT 200',(time.time(),) if all_ else (time.time(),p.actor.id))]
        return {'events':events,'bans':bans}

    @app.get('/api/ip-policy')
    def export_ip_policy(p:Principal=Depends(owner)):
        from dataclasses import asdict
        policy=asdict(engine.ip_policy());policy['schema']=1
        return policy

    app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')
    @app.get('/')
    def index():return FileResponse(ROOT/'web'/'index.html',media_type='text/html')
    return app


def _read_password(prompt:str,repeat:str,*,stdin_mode:bool=False)->str:
    if stdin_mode:
        first=sys.stdin.readline().rstrip('\r\n');second=sys.stdin.readline().rstrip('\r\n')
        if not first or not second:raise PolicyError('Password input missing')
    else:
        from getpass import getpass
        first=getpass(prompt);second=getpass(repeat)
    if first!=second:raise PolicyError('Passwords differ')
    return first


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path('./config.json'))
    p.add_argument('--data',type=Path,default=Path('./data'))
    subs=p.add_subparsers(dest='command',required=True)
    init=subs.add_parser('init');init.add_argument('--username',default='dark');init.add_argument('--password-stdin',action='store_true',help=argparse.SUPPRESS)
    reset=subs.add_parser('reset-password');reset.add_argument('--username',required=True)
    serve=subs.add_parser('serve');serve.add_argument('--host',default=None);serve.add_argument('--port',type=int,default=None)
    subs.add_parser('check')
    b=subs.add_parser('backup');b.add_argument('--output',type=Path,required=True)
    r=subs.add_parser('restore');r.add_argument('--archive',type=Path,required=True);r.add_argument('--destination',type=Path,required=True)
    args=p.parse_args()
    if args.command in ('backup','restore'):
        from backup import create_backup,restore_backup
        from getpass import getpass
        pwd=getpass('Backup passphrase (at least 12 characters): ')
        if args.command=='backup':
            if pwd!=getpass('Repeat passphrase: '):raise SystemExit('Passphrases differ')
            result=create_backup(args.data,args.config,args.output,pwd)
        else: result=restore_backup(args.archive,args.destination,pwd)
        print(json.dumps(result,indent=2));return
    args.data.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    lockfile=(args.data/'instance.lock').open('a')
    try:fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another DARK instance is already using this data directory')
    store=Store(args.data/'dark.sqlite3')
    config=Config.load(args.config);engine=CoreEngine(config,store,args.data/'runtime');manager=Manager(store,engine);auth=Auth(store,args.data/'secret.key')
    try:
        if args.command=='init':
            pwd=_read_password(f'DARK owner password (at least {PASSWORD_MIN_LENGTH} characters): ','Repeat password: ',stdin_mode=args.password_stdin)
            auth.bootstrap(args.username,pwd)
            manager.owner_put(SYSTEM,args.username,name=args.username,allowed=[])
            print('Independent DARK owner initialized. No other panel is required.');return
        if args.command=='reset-password':
            pwd=_read_password(f'New owner password (at least {PASSWORD_MIN_LENGTH} characters): ','Repeat password: ')
            with store.lock:r=store.db.execute('SELECT role FROM api_admins WHERE id=?',(args.username,)).fetchone()
            if not r or r['role']!='owner':raise PolicyError('Offline recovery only supports existing owner accounts')
            auth.admin_edit(SYSTEM,args.username,password=pwd)
            print('Owner password changed; old sessions revoked. TOTP remains enabled.');return
        if args.command=='check':
            print('Core:',engine.version_info())
            print('Inbounds:',len(engine.inbounds()),'Clients:',len(engine.clients()))
            print('Writes enabled:',config.writes_enabled)
            print('IP Guard:',json.dumps(engine.ip_status(),ensure_ascii=False))
            return
        args.host=args.host or config.bind_host
        args.port=args.port or config.bind_port
        if not 1024<=args.port<=65535:raise PolicyError('Nonprivileged port required')
        if args.host not in ('127.0.0.1','::1') and not (config.secure_cookie and config.tls_certificate):
            raise PolicyError('A public listener requires a real TLS certificate/key; use loopback behind a reverse proxy otherwise')
        with store.lock:
            if not store.db.execute('SELECT 1 FROM api_admins').fetchone():raise PolicyError('Initialize the owner first')
        import uvicorn
        uvicorn.run(make_app(manager,auth),host=args.host,port=args.port,proxy_headers=False,access_log=False,workers=1,ws='none',ssl_certfile=config.tls_certificate or None,ssl_keyfile=config.tls_private_key or None)
    except (PolicyError,ValueError,CoreError) as e:raise SystemExit(str(e))
    finally:manager.close();engine.close();store.close();lockfile.close()

if __name__=='__main__':main()

[Reading 1000 lines from start (total: 2161 lines, 1161 remaining)]

#!/usr/bin/env python3
"""DARK XRAY standalone server. Own database, API, UI and direct Xray process.
No proxy-panel installation or token is required. The default listener is loopback.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
import copy
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import threading
import tempfile
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
from dark_policy import Store,Actor,PolicyError,PermissionDenied,MAX_INT,NAME_RE
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
    volume_credit_bytes:StrictInt|None=Field(default=None,ge=0,le=MAX_INT)
    unlimited_credit:StrictInt|None=Field(default=None,ge=0,le=1000000)
    quota_bytes:StrictInt|None=Field(default=None,ge=0,le=MAX_INT,description='Deprecated alias for volume_credit_bytes')
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
class RepresentativeBody(Model):
    name:str=Field(min_length=1,max_length=128)
    password:str|None=Field(default=None,min_length=PASSWORD_MIN_LENGTH,max_length=PASSWORD_MAX_LENGTH)
    enabled:bool=True
    allowed:list[StrictInt]=Field(default_factory=list,max_length=4096)
    volume_credit_bytes:StrictInt|None=Field(default=None,ge=0,le=MAX_INT)
    unlimited_credit:StrictInt|None=Field(default=None,ge=0,le=1000000)
    quota_bytes:StrictInt|None=Field(default=None,ge=0,le=MAX_INT,description='Deprecated alias for volume_credit_bytes')
    max_clients:StrictInt=Field(default=0,ge=0,le=1000000)
    prefix:str=Field(default='',max_length=64)
    max_client_ips:StrictInt=Field(default=0,ge=0,le=1000)
    max_client_hwid:StrictInt=Field(default=0,ge=0,le=1000)
class ReplacementCommit(Model):
    sourceBindingId:str=Field(pattern=r'^[0-9a-f]{32}$')
    acceptUnconfirmedOldServer:bool
    acceptUnreportedTraffic:bool
class ReplacementStage(Model):
    bindingId:str=Field(pattern=r'^[0-9a-f]{32}$')

class ReplacementActivate(ReplacementStage):
    reviewHash:str=Field(pattern=r'^[0-9a-f]{64}$')
    confirmStart:bool=Field(strict=True)
    acceptEndpointResponsibility:bool=Field(strict=True)
    acceptUnconfirmedOldServer:bool=Field(strict=True)
    acceptUnreportedTraffic:bool=Field(strict=True)

class ReplacementPause(ReplacementStage):
    confirmStop:bool=Field(strict=True)

class ReplacementCancel(Model):
    discardCandidate:bool

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
class ResourceCredit(Model):
    volume_bytes:int=Field(default=0,ge=-MAX_INT,le=MAX_INT)
    unlimited_units:int=Field(default=0,ge=-1000000,le=1000000)
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
class TrafficRoutePreview(Model):
    domain:str=Field(default='',max_length=2048)
    ip:str=Field(default='',max_length=80)
    port:StrictInt=Field(default=0,ge=0,le=65535)
    source_ip:str=Field(default='',max_length=80)
    source_port:StrictInt=Field(default=0,ge=0,le=65535)
    local_ip:str=Field(default='',max_length=80)
    local_port:StrictInt=Field(default=0,ge=0,le=65535)
    network:Literal['tcp','udp']='tcp'
    protocol:str=Field(default='',max_length=64)
    user:str=Field(default='',max_length=128)
    inbound_tag:str=Field(default='',max_length=128)
    process:str=Field(default='',max_length=1024)
    vless_route:StrictInt=Field(default=0,ge=0,le=65535)
    attrs:dict[str,str]=Field(default_factory=dict,max_length=64)
class FullBackupBody(Model):
    passphrase:str=Field(min_length=12,max_length=512)
class NodePair(Model):
    code:str=Field(min_length=16,max_length=4096)
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
class InboundDeployments(Model):
    local:bool=True
    nodeIds:list[str]=Field(default_factory=list,max_length=256)
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
    from node_replacement import NodeReplacement
    replacements=NodeReplacement(nodes)
    node_reset_lock=threading.RLock()
    manager.remote_reset=lambda email,reset_id:nodes.reset_client_traffic(email,reset_id)
    def apply_global_security(_node_id:str='',_result:dict|None=None):
        result=nodes.reconcile_global_security(local_source_verified=bool(config.direct_source_verified))
        changed=list(result.get('changed') or [])
        if changed:
            manager.tick(suppress=True)
            # Persist the new policy into every affected Node desired state now.
            # Delivery may still wait for an offline Node, but Hub state must never
            # pretend the old global blocker is the current desired policy.
            target_nodes=set()
            for email in changed:target_nodes.update(nodes._assigned_node_ids(email))
            refreshed=[];errors=[]
            for node_id in sorted(target_nodes):
                try:
                    state=ensure_node_desired_state(node_id)
                    refreshed.append({'node_id':node_id,'revision':state.get('revision',0),
                                      'pending':bool(state.get('pending'))})
                except (PolicyError,OSError,ValueError) as ex:
                    errors.append({'node_id':node_id,'error':str(ex)[:300]})
            result['desired_state_refresh']={'nodes':refreshed,'errors':errors}
        return result
    @contextlib.asynccontextmanager
    async def lifespan(app):
        bot_runtime=None
        if background:
            manager.start();nodes.start(interval=max(5.0,min(60.0,float(config.poll_seconds))),
                                      sync_provider=lambda node_id:build_node_bundles(node_id),
                                      desired_provider=lambda node_id:ensure_node_desired_state(node_id),
                                      traffic_callback=lambda node_id,result:manager.tick(suppress=True),
                                      security_callback=apply_global_security)
            bot_runtime=getattr(app.state,'telegram_runtime',None)
            if bot_runtime:bot_runtime.start()
        yield
        if bot_runtime:bot_runtime.close()
        nodes.close();manager.close();engine.close()
    app=FastAPI(title='DARK XRAY',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.replacements=replacements
    app.state.manager=manager;app.state.auth=auth;app.state.engine=engine;app.state.nodes=nodes
    from dark_restore import DarkRestore
    dark_restore=DarkRestore(store,engine,nodes)
    app.state.dark_restore=dark_restore
    public=urlsplit(config.public_origin);panel_path=config.panel_path

    @app.middleware('http')
    async def security(request:Request,call_next):
        raw_path=request.scope.get('path','/') or '/'
        try:subscription_path=str(engine.section('subscription').get('path','/sub'))
        except Exception:subscription_path='/sub'
        raw_query=(request.scope.get('query_string') or b'').decode('latin1')
        restore_match=dark_restore.match_request(request.headers.get('host',''),raw_path,raw_query)
        if restore_match:
            request.scope['path']='/restore/sub/'+restore_match['public_token']
            raw_path=request.scope['path']
        subscription_request=raw_path.startswith(subscription_path+'/') or raw_path.startswith('/restore/sub/')
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
        if not restore_match and request.headers.get('host','').lower()!=public.netloc.lower():
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

    from node_recovery import install_hub_recovery
    install_hub_recovery(app,nodes,owner,writable,manager.audit)

    from telegram_commerce import install_telegram_commerce
    install_telegram_commerce(app,store,auth,current,writable,manager.audit,manager)

    from dark_restore import install_dark_restore
    install_dark_restore(app,dark_restore,current,owner,writable,manager.audit)

    @app.get('/health')
    def health():return {'service':'DARK XRAY','version':VERSION,'mode':'standalone','test_engine':config.test_engine}

    def update_client()->UpdateBrokerClient:
        return UpdateBrokerClient(timeout=12)

    def hub_source_commit()->str:
        path=Path(store.path).parent/'installed-source.json'
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            commit=str(value.get('commit') or '').lower()
        except (OSError,ValueError,TypeError,AttributeError):commit=''
        if not re.fullmatch(r'[0-9a-f]{40}',commit):raise HTTPException(409,'Hub exact installed source commit is unavailable')
        return commit

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
        # Legacy compatibility endpoint. The interactive UI no longer exposes a
        # role/permission editor; new accounts are representatives only.
        with store.lock:rows=[dict(r) for r in store.db.execute("SELECT id,role,permissions,disabled FROM api_admins WHERE role IN ('owner','reseller') ORDER BY id")]
        for r in rows:r['permissions']=json.loads(r['permissions'])
        return rows
    @app.post('/api/admins')
    def add_admin(body:AdminBody,p:Principal=Depends(owner)):
        if body.role!='reseller':raise HTTPException(409,'DARK has one primary owner; only representative accounts can be created')
        auth.admin_create(p.actor,body.username,body.password,'reseller',DEFAULTS['reseller'])
        manager.audit(p.actor,body.username,'representative.login_create',body.username)
        return {'created':True,'role':'reseller'}
    @app.patch('/api/admins/{username}')
    def edit_admin(username:str,body:AdminPatch,p:Principal=Depends(owner)):
        with store.lock:row=store.db.execute('SELECT role FROM api_admins WHERE id=?',(username,)).fetchone()
        if not row:raise HTTPException(404,'Account not found')
        if row['role']!='reseller':raise HTTPException(409,'Primary owner account is managed only from Account & Security')
        auth.admin_edit(p.actor,username,disabled=body.disabled,password=body.password,permissions=DEFAULTS['reseller'])
        manager.audit(p.actor,username,'representative.login_update',username)
        manager.tick(suppress=True)
        return {'updated':True,'sessions_revoked':True}

    def representative_rows(p:Principal)->list[dict]:
        with store.lock:
            primary={r[0] for r in store.db.execute("SELECT id FROM api_admins WHERE role='owner'")}
            profiles=[dict(r) for r in store.db.execute('SELECT * FROM owner_profiles ORDER BY name,id')]
            accounts={r['id']:dict(r) for r in store.db.execute("SELECT id,role,disabled FROM api_admins WHERE role='reseller'")}
        out=[]
        for profile in profiles:
            rid=str(profile['id'])
            if rid in primary:continue
            try:stats=store.owner_stats(p.actor,rid)
            except PolicyError:continue
            account=accounts.get(rid)
            out.append({**stats,**manager.profile(rid),
                        'login_ready':bool(account),
                        'login_disabled':bool(account['disabled']) if account else True,
                        'enabled':bool(account and not account['disabled'] and not stats.get('manual') and not stats.get('account_disabled'))})
        return out

    @app.get('/api/resellers')
    def representatives(p:Principal=Depends(owner)):
        return representative_rows(p)

    @app.put('/api/resellers/{reseller_id}')
    def representative_put(reseller_id:str,body:RepresentativeBody,p:Principal=Depends(owner)):
        writable()
        if not NAME_RE.fullmatch(reseller_id):raise HTTPException(400,'Invalid representative ID')
        with store.lock:
            primary=store.db.execute("SELECT 1 FROM api_admins WHERE id=? AND role='owner'",(reseller_id,)).fetchone()
            account=store.db.execute('SELECT role FROM api_admins WHERE id=?',(reseller_id,)).fetchone()
        if primary:raise HTTPException(409,'Primary owner cannot be converted to a representative')
        if account and account['role']!='reseller':raise HTTPException(409,'Account ID belongs to a legacy non-representative role')
        if not account and not body.password:raise HTTPException(400,'Password is required when creating a representative')
        manager.owner_put(p.actor,reseller_id,name=body.name,allowed=body.allowed,
                          volume_credit_bytes=body.volume_credit_bytes,unlimited_credit=body.unlimited_credit,
                          quota_bytes=body.quota_bytes,max_clients=body.max_clients,manual=not body.enabled,
                          prefix=body.prefix,max_client_ips=body.max_client_ips,max_client_hwid=body.max_client_hwid)
        if not account:
            auth.admin_create(p.actor,reseller_id,body.password or '','reseller',DEFAULTS['reseller'])
        auth.admin_edit(p.actor,reseller_id,disabled=not body.enabled,password=body.password,
                        permissions=DEFAULTS['reseller'])
        manager.tick(suppress=True)
        manager.audit(p.actor,reseller_id,'representative.save',reseller_id,
                      'enabled='+str(body.enabled)+'; inbounds='+str(len(body.allowed))+
                      '; volume_credit_bytes='+str(body.volume_credit_bytes)+
                      '; unlimited_credit='+str(body.unlimited_credit))
        return next(r for r in representative_rows(p) if r['id']==reseller_id)

    @app.post('/api/resellers/{reseller_id}/credits')
    def representative_credit_adjust(reseller_id:str,body:ResourceCredit,p:Principal=Depends(owner)):
        writable()
        recorded=store.adjust_resource_credit(p.actor,reseller_id,body.volume_bytes,body.unlimited_units,body.event_id)
        if recorded:
            manager.audit(p.actor,reseller_id,'representative.credit_adjust',reseller_id,
                          f'volume_bytes={body.volume_bytes}; unlimited_units={body.unlimited_units}; event={body.event_id}')
        manager.tick(suppress=True)
        row=next((r for r in representative_rows(p) if r['id']==reseller_id),None)
        if not row:raise HTTPException(404,'Representative not found')
        return {'recorded':recorded,'representative':row}

    @app.delete('/api/resellers/{reseller_id}')
    def representative_delete(reseller_id:str,p:Principal=Depends(owner)):
        writable()
        with store.lock:
            primary=store.db.execute("SELECT 1 FROM api_admins WHERE id=? AND role='owner'",(reseller_id,)).fetchone()
            active=store.db.execute('SELECT COUNT(*) FROM clients WHERE owner=?',(reseller_id,)).fetchone()[0]
            account=store.db.execute('SELECT role FROM api_admins WHERE id=?',(reseller_id,)).fetchone()
        if primary:raise HTTPException(409,'Primary owner cannot be deleted')
        if active:raise HTTPException(409,'Move or delete representative clients before deleting the representative')
        if account and account['role']!='reseller':raise HTTPException(409,'Account is not a representative')
        with store.transaction() as db:
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(reseller_id,))
            db.execute('DELETE FROM robot_keys WHERE admin_id=?',(reseller_id,))
            db.execute('DELETE FROM mfa WHERE admin_id=?',(reseller_id,))
            db.execute("DELETE FROM api_admins WHERE id=? AND role='reseller'",(reseller_id,))
            db.execute('DELETE FROM client_groups WHERE owner=?',(reseller_id,))
            db.execute('DELETE FROM owner_profiles WHERE id=?',(reseller_id,))
            db.execute('DELETE FROM owners WHERE id=?',(reseller_id,))
        manager.audit(p.actor,reseller_id,'representative.delete',reseller_id,'Historical ledgers are preserved')
        return {'deleted':True}

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
        raise HTTPException(410,'Financial reseller credit is retired; use volume/unlimited resource credits')
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
        writable();now_ms=int(time.time()*1000);updates=[];slots=[]
        for email in dict.fromkeys(body.emails):
            try:
                d=manager.update_snapshot(p.actor,email);c=d['client'];patch={}
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
                updates.append({'email':email,'patch':patch});slots.append(None)
            except (PolicyError,CoreError) as ex:slots.append({'email':email,'error':str(ex)[:300]})
        batch=manager.update_batch(p.actor,updates) if updates else {'changed':0,'items':[]}
        results=iter(batch['items'])
        out=[next(results) if slot is None else slot for slot in slots]
        if batch['changed']:apply_global_security()
        return {'changed':batch['changed'],'items':out}

    @app.post('/api/clients/bulk-inbounds')
    def bulk_inbounds(body:BulkInbounds,p:Principal=Depends(current)):
        writable();updates=[];slots=[]
        for email in dict.fromkeys(body.emails):
            try:
                d=manager.update_snapshot(p.actor,email,action='attach');current=set(d['inboundIds']);change=set(body.inboundIds)
                ids=sorted(current|change) if body.mode=='attach' else sorted(current-change)
                if not ids:raise PolicyError('A client must retain at least one inbound')
                updates.append({'email':email,'patch':{},'inboundIds':ids});slots.append(None)
            except (PolicyError,CoreError) as ex:slots.append({'email':email,'error':str(ex)[:300]})
        batch=manager.update_batch(p.actor,updates,action='attach') if updates else {'changed':0,'items':[]}
        results=iter(batch['items'])
        out=[next(results) if slot is None else slot for slot in slots]
        if batch['changed']:apply_global_security()
        return {'changed':batch['changed'],'items':out}

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

    @app.post('/api/clients/{email}/sync-retry')
    def retry_client_sync(email:str,p:Principal=Depends(current)):
        writable();return manager.retry_operation(p.actor,email)

    @app.post('/api/clients/{email}/restore-missing')
    def restore_missing_client(email:str,body:ResolveReset,p:Principal=Depends(owner)):
        writable();return manager.restore_missing(p.actor,email,body.confirmation)


    def rewrite_failover_uri(uri:str,address:str,remark:str,port_override:int=0)->str:
        if port_override and not 1<=int(port_override)<=65535:raise PolicyError('Invalid failover data port')
        if uri.startswith('vmess://'):
            raw=uri[8:]
            try:
                doc=json.loads(base64.b64decode(raw+'='*((4-len(raw)%4)%4)).decode())
                doc['add']=address
                if port_override:doc['port']=str(int(port_override))
                doc['ps']=remark
                return 'vmess://'+base64.b64encode(json.dumps(doc,separators=(',',':'),ensure_ascii=False).encode()).decode()
            except Exception as ex:raise PolicyError('Cannot rewrite VMess failover link') from ex
        p=urlsplit(uri)
        if p.scheme not in {'vless','trojan','ss'}:raise PolicyError('Unsupported failover link protocol')
        userinfo=(p.netloc.rsplit('@',1)[0]+'@') if '@' in p.netloc else ''
        host='['+address+']' if ':' in address and not address.startswith('[') else address
        port=int(port_override or (p.port or 0))
        netloc=userinfo+host+((':'+str(port)) if port else '')
        return urlunsplit((p.scheme,netloc,p.path,p.query,quote(remark)))

    def runtime_ready_map(email:str)->dict[str,set[int]]:
        detail=engine.client_detail(email);ids={int(x) for x in detail.get('inboundIds',[])}
        ready={'local':set()}
        for inbound_id in ids:
            inbound=engine.inbound(inbound_id);meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
            if meta.get('deployLocal',True) is not False:ready['local'].add(inbound_id)
        for node in nodes.list():
            if not node.get('enabled') or not node.get('online') or node.get('last_error'):continue
            key='node:'+str(node['id'])
            for assignment in node.get('assignments',[]):
                inbound_id=int(assignment.get('local_inbound_id') or 0)
                if inbound_id in ids and assignment.get('deployed') and not assignment.get('last_error'):
                    ready.setdefault(key,set()).add(inbound_id)
        return ready

    def failover_links(email:str)->list[dict]:
        targets=nodes.failover_targets(email)
        if not targets:return []
        detail=engine.client_detail(email);base=engine.links(email,'raw',runtime_ready={'local':set(map(int,detail.get('inboundIds',[])))});out=[]
        for item in base['links']:
            inbound_id=int(item.get('inboundId') or 0)
            for target in targets:
                if inbound_id not in target['inbound_ids']:continue
                explicit_direct=any(int(h.get('inboundId') or 0)==inbound_id and h.get('enable',True)
                                    and h.get('runtime')=='node:'+str(target['node_id'])
                                    and (h.get('endpointType','direct') or 'direct')!='tunnel'
                                    for h in engine.section('hosts'))
                if explicit_direct:continue
                remark=str(item['remark'])+' · '+str(target['name'])+' ['+str(target['node_id'])+']'
                clone={k:json.loads(json.dumps(v)) for k,v in item.items() if k!='uri'}
                clone['remark']=remark
                source_port=int(engine.inbound(inbound_id)['port'])
                clone['uri']=rewrite_failover_uri(item['uri'],target['address'],remark,source_port)
                clone['endpointType']='direct'
                clone['failoverNode']=target['node_id'];clone['failoverPriority']=target['priority']
                clone['failoverPort']=source_port
                clone['failoverLatencyMs']=target['latency_ms'];out.append(clone)
        return out

    @app.get('/api/clients/{email}/links')
    def links(email:str,p:Principal=Depends(current)):
        manager.own_row(p.actor,email,'credentials')
        result=engine.links(email,runtime_ready=runtime_ready_map(email));result['failover']=failover_links(email)
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
            # Deployment scope belongs to the Hub. A selected remote Node must
            # run the logical inbound even when Local deployment is disabled.
            if isinstance(inbound.get('panelMeta'),dict):
                inbound['panelMeta'].pop('deployLocal',None);inbound['panelMeta'].pop('deploymentTargets',None)
                if not inbound['panelMeta']:inbound.pop('panelMeta',None)
            clients=[]
            for client in all_clients:
                if source not in client.get('inboundIds',[]):continue
                raw={k:client[k] for k in ('id','password','flow','encryption','security','enable') if k in client}
                clients.append({'sourceEmail':client['email'],'client':raw})
            bundles.append({'sourceInboundId':source,'inbound':inbound,'clients':clients})
        return bundles

    def node_managed_files(bundles:list[dict])->list[dict]:
        files={};path_ids={}
        for bundle in bundles:
            inbound=bundle.get('inbound',{}) if isinstance(bundle,dict) else {}
            st=inbound.get('streamSettings',{}) if isinstance(inbound,dict) else {}
            tls=st.get('tlsSettings',{}) if isinstance(st,dict) else {}
            certs=tls.get('certificates',[]) if isinstance(tls,dict) else []
            if not isinstance(certs,list):continue
            for cert in certs:
                if not isinstance(cert,dict):continue
                for key,kind in (('certificateFile','certificate'),('keyFile','private-key')):
                    raw=cert.get(key)
                    if not isinstance(raw,str) or not raw.strip():continue
                    original=raw.strip();path=Path(original)
                    if not path.is_absolute():raise PolicyError('Inbound TLS paths must be absolute before Node deployment')
                    try:resolved=path.resolve(strict=True)
                    except OSError as ex:raise PolicyError('Inbound TLS file is missing for Node deployment: '+original) from ex
                    if not resolved.is_file() or resolved.stat().st_size>1024*1024:
                        raise PolicyError('Inbound TLS file is unsafe or too large for Node deployment')
                    content=resolved.read_bytes();digest=hashlib.sha256(content).hexdigest()
                    identity=kind+'\0'+original+'\0'+digest
                    file_id=path_ids.get(identity)
                    if not file_id:
                        file_id=hashlib.sha256(identity.encode()).hexdigest()
                        path_ids[identity]=file_id
                        files[file_id]={'id':file_id,'kind':kind,'sha256':digest,
                                        'data':base64.b64encode(content).decode('ascii')}
                    cert[key]='managed://'+file_id
        total=sum(len(x['data']) for x in files.values())
        if total>4*1024*1024:raise PolicyError('Managed Node TLS payload exceeds 4 MiB')
        return [files[k] for k in sorted(files)]

    def build_node_desired_payload(node_id:str)->dict:
        bundles=build_node_bundles(node_id)
        managed_files=node_managed_files(bundles)
        assigned={int(x['sourceInboundId']) for x in bundles}
        with store.lock:
            policy_rows={str(r['id']):dict(r) for r in store.db.execute(
                'SELECT id,limit_ip,global_ip_block,global_device_block FROM clients')}
        policies=[]
        seen=set()
        for client in engine.clients():
            ids={int(x) for x in client.get('inboundIds',[])}
            if not ids.intersection(assigned):continue
            email=str(client.get('email',''))
            if not email or email in seen:continue
            seen.add(email);row=policy_rows.get(email,{})
            policies.append({'sourceEmail':email,'limitIp':int(row.get('limit_ip') or 0),
                             'limitHwid':int(client.get('limitHwid') or 0),
                             'globalIpBlocked':bool(row.get('global_ip_block')),
                             'globalDeviceBlocked':bool(row.get('global_device_block'))})
        sections={name:engine.section(name) for name in ('outbounds','routing','dns','policy','observatory','ipguard')}
        # Local and Node packet-source trust are separate boundaries. A Central
        # host behind Backhaul may have to remain Observe while direct-source
        # Nodes enforce through their own root-owned broker. Never send the
        # Central-only node_mode field to an Agent.
        node_guard=copy.deepcopy(sections['ipguard'])
        node_guard['mode']=node_guard.get('node_mode',node_guard.get('mode','observe'))
        node_guard.pop('node_mode',None);sections['ipguard']=node_guard
        return {'schema':1,'nodeId':node_id,'desiredRunning':True,'sections':sections,
                'assignments':bundles,'security':{'clients':policies},'files':managed_files}

    def ensure_node_desired_state(node_id:str)->dict:
        nodes.set_desired_state(node_id,build_node_desired_payload(node_id))
        return nodes.desired_state(node_id)

    def refresh_replacement_policy():
        manager.tick(suppress=False)
        apply_global_security()

    from node_replacement_deployment import ReplacementDeployment
    replacement_deployments=ReplacementDeployment(nodes,replacements,build_node_desired_payload,refresh_replacement_policy)
    app.state.replacement_deployments=replacement_deployments
    from node_replacement_activation import ReplacementActivation
    replacement_activation=ReplacementActivation(replacement_deployments)
    app.state.replacement_activation=replacement_activation

    def sync_node_assignments(node_id:str)->dict:
        pre=nodes.sync_traffic(node_id)
        if pre.get('charged_bytes'):manager.tick(suppress=True)
        security_pre=None
        try:
            security_pre=nodes.sync_security(node_id);apply_global_security(node_id,security_pre)
        except PolicyError:
            pass
        bundles=build_node_bundles(node_id)
        state=ensure_node_desired_state(node_id)
        result=nodes.sync_desired_state(node_id,state,legacy_bundles=bundles)
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

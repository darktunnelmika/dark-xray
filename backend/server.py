#!/usr/bin/env python3
"""DARK XRAY standalone server. Own database, API, UI and direct Xray process.
No proxy-panel installation or token is required. The default listener is loopback.
"""
from __future__ import annotations

from node_lease_sync import renew_accounting_lease
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
import socket
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
from outbound_probe import OutboundProbeError,probe_outbounds
from warp_cloudflare import WarpRegistrationError,register_cloudflare_warp,validate_warp_endpoint
from warp_paths import warp_endpoint_candidates, warp_scan_results
from traffic_matrix import POLICIES as MATRIX_POLICIES,ACCESS_PATHS as MATRIX_ACCESS_PATHS,policy_parts as matrix_policy_parts
from nodes import NodeRegistry,token_digest
from node_relay import NodeRelay
from node_port_swap import NodePortSwap
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
class RelayBody(Model):
    exitNodeId:str=Field(min_length=1,max_length=128)
    exitInboundId:StrictInt=Field(ge=1)
class RelayToggle(Model):
    enabled:bool
class PortSwapBody(Model):
    sourceNodeId:str=Field(min_length=1,max_length=128)
    sourcePort:StrictInt=Field(ge=1,le=65535)
    exitNodeId:str=Field(min_length=1,max_length=128)
    inboundId:StrictInt=Field(ge=1)
    exitPort:StrictInt=Field(ge=1,le=65535)
    entryAddress:str=Field(min_length=1,max_length=253)
    entryPort:StrictInt=Field(ge=1,le=65535)
    name:str=Field(min_length=1,max_length=160)
class PortSwapRename(Model):
    name:str=Field(min_length=1,max_length=160)
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
class TrafficMatrixSet(Model):
    inboundId:StrictInt=Field(ge=1)
    server:str=Field(default='hub',min_length=1,max_length=160)
    accessPath:Literal['direct','tunnel']='direct'
    policy:str=Field(default='normal',min_length=1,max_length=40)
class TrafficMatrixBatch(Model):
    inboundId:StrictInt=Field(ge=1)
    server:str=Field(default='hub',min_length=1,max_length=160)
    accessPaths:list[Literal['direct','tunnel']]=Field(min_length=1,max_length=2)
    policy:str=Field(default='normal',min_length=1,max_length=40)
class TrafficMatrixProbe(Model):
    inboundId:StrictInt=Field(ge=1)
    server:str=Field(default='hub',min_length=1,max_length=160)
    accessPath:Literal['direct','tunnel']='direct'
    attempts:StrictInt=Field(default=2,ge=1,le=3)
class TrafficOutboundProbe(Model):
    server:str=Field(default='hub',min_length=1,max_length=160)
    tag:str=Field(min_length=1,max_length=128)
    attempts:StrictInt=Field(default=2,ge=1,le=3)
class WarpCreate(Model):
    server:str=Field(default='hub',min_length=1,max_length=160)
class WarpEndpointScan(Model):
    server:str=Field(default='hub',min_length=1,max_length=160)
class WarpEndpointSelect(Model):
    server:str=Field(default='hub',min_length=1,max_length=160)
    endpoint:str=Field(min_length=3,max_length=160)

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
class NodeMaintenance(Model):
    enabled:bool
    note:str=Field(default='',max_length=300)
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
    tunnelPorts:dict[str,StrictInt]=Field(default_factory=dict,max_length=257)
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
    node_relays=NodeRelay(store,auth.cipher,nodes,engine)
    node_port_swaps=NodePortSwap(store,nodes,engine)
    nodes.managed_assignment_sources=node_port_swaps.assignment_sources
    engine.swap_hosts_provider=node_port_swaps.hosts
    from licensing import LicenseClient
    license_client=LicenseClient(Path(store.path).resolve().parent if store.path!=':memory:' else Path('/tmp/dark-xray-test-license'))
    from node_replacement import NodeReplacement
    replacements=NodeReplacement(nodes)
    node_reset_lock=threading.RLock()
    manager.remote_reset=lambda email,reset_id:nodes.reset_client_traffic(email,reset_id)
    def _runtime_targets()->list[dict]:
        hub_name=str(os.environ.get('DARK_HUB_NAME','HUB')).strip() or 'HUB'
        rows=[{'id':'hub','kind':'hub','name':hub_name,'address':str(config.public_address),'online':True,'latencyMs':0}]
        for node in nodes.list():
            rows.append({'id':'node:'+str(node.get('id')),'nodeId':str(node.get('id')),'kind':'node',
                         'name':str(node.get('name') or node.get('id') or 'Node'),
                         'address':str(node.get('data_address') or ''),'online':bool(node.get('online')),
                         'latencyMs':int(node.get('last_latency_ms') or 0)})
        return rows

    def _runtime_target(scope:str,*,require_online:bool=False)->dict:
        value=str(scope or 'hub').strip() or 'hub'
        row=next((x for x in _runtime_targets() if x['id']==value),None)
        if not row:raise HTTPException(400,'Unknown runtime server target')
        if require_online and not row.get('online'):raise HTTPException(409,'Selected Node is offline')
        return row

    def _settings_apply_state(scope:str)->dict:
        # Read desired/applied runtime state only. No health probes or mutation.
        if scope=='hub':
            runtime=engine.runtime_state()
            error=str(runtime.get('last_error') or '')[:300]
            status='error' if error else 'pending' if runtime.get('dirty') or runtime.get('state')!='running' else 'applied'
            return {'status':status,'error':error,'source':'hub-runtime'}
        node=next((n for n in nodes.list() if 'node:'+str(n['id'])==scope),{})
        desired=node.get('desired_state') or {};control=node.get('control') or {}
        runtime=(node.get('health') or {}).get('core') or {}
        error=str(desired.get('last_error') or runtime.get('last_error') or '')[:300]
        pending=(not node.get('online') or desired.get('pending') or control.get('pending') or
                 runtime.get('dirty') or runtime.get('state')!='running' or not desired.get('applied_revision'))
        return {'status':'error' if error else 'pending' if pending else 'applied',
                'error':error,'source':'node-acknowledgement','revision':desired.get('revision',0),
                'appliedRevision':desired.get('applied_revision',0)}

    def _warp_profile(scope:str)->dict|None:
        return engine.warp_profile(str(scope or 'hub'))

    def _warp_profile_save(scope:str,outbound:dict,device_id:str='')->dict:
        value=str(scope or 'hub').strip() or 'hub';body=copy.deepcopy(outbound);body['tag']='warp'
        with store.transaction() as db:
            db.execute("""INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,?)
                          ON CONFLICT(scope) DO UPDATE SET outbound_json=excluded.outbound_json,
                          device_id=excluded.device_id,updated_at=excluded.updated_at""",
                       (value,json.dumps(body,separators=(',',':')),str(device_id or '')[:256],time.time()))
        return body

    def _warp_profile_delete(scope:str):
        with store.transaction() as db:db.execute('DELETE FROM warp_profiles WHERE scope=?',(str(scope or 'hub'),))

    def _warp_pending(scope:str)->dict|None:
        value=str(scope or 'hub').strip() or 'hub'
        with store.lock:row=store.db.execute('SELECT outbound_json FROM warp_pending_profiles WHERE scope=?',(value,)).fetchone()
        if not row:return None
        try:out=json.loads(row['outbound_json'])
        except (TypeError,ValueError):return None
        return copy.deepcopy(out) if isinstance(out,dict) else None

    def _warp_pending_save(scope:str,outbound:dict,device_id:str='')->dict:
        value=str(scope or 'hub').strip() or 'hub';body=copy.deepcopy(outbound);body['tag']='warp';now=time.time()
        with store.transaction() as db:
            db.execute("""INSERT INTO warp_pending_profiles(scope,outbound_json,device_id,created_at,updated_at)
                          VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET outbound_json=excluded.outbound_json,
                          device_id=excluded.device_id,updated_at=excluded.updated_at""",
                       (value,json.dumps(body,separators=(',',':')),str(device_id or '')[:256],now,now))
        return body

    def _warp_pending_delete(scope:str):
        with store.transaction() as db:db.execute('DELETE FROM warp_pending_profiles WHERE scope=?',(str(scope or 'hub'),))

    def _matrix_deployments(inbound_id:int)->tuple[dict,list[str],dict]:
        inbound=engine.inbound(inbound_id);meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
        scopes=[]
        if meta.get('deployLocal',True) is not False:scopes.append('hub')
        for node_id in sorted(nodes.inbound_assignments(inbound_id)):scopes.append('node:'+str(node_id))
        raw=meta.get('tunnelPorts',{}) if isinstance(meta.get('tunnelPorts'),dict) else {}
        ports={str(k):int(v) for k,v in raw.items() if isinstance(k,str) and type(v)is int and 1<=v<=65535}
        return inbound,scopes,ports

    def _matrix_port(scope:str,inbound:dict,ports:dict,access_path:str)->int:
        if access_path=='direct':return int(inbound.get('port') or 0)
        key='local' if scope=='hub' else scope
        return int(ports.get(key) or 0)

    def _matrix_doc(inbound_id:int)->dict:
        inbound,scopes,ports=_matrix_deployments(inbound_id)
        with store.lock:
            saved={(str(r['scope']),str(r['access_path'])):str(r['policy']) for r in store.db.execute(
                'SELECT scope,access_path,policy FROM traffic_matrix WHERE inbound_id=?',(inbound_id,))}
        block=next((x for x in engine.section('outbounds') if isinstance(x,dict) and x.get('tag')=='block'),None)
        rows=[]
        for scope in scopes:
            target=_runtime_target(scope,require_online=False);warp=bool(_warp_profile(scope));warp_pending=bool(_warp_pending(scope))
            apply_state=_settings_apply_state(scope)
            for path in ('direct','tunnel'):
                port=_matrix_port(scope,inbound,ports,path)
                if path=='tunnel' and not port:continue
                rows.append({'server':target,'serverId':scope,'inboundId':inbound_id,'accessPath':path,'port':port,
                             'policy':saved.get((scope,path),'normal'),'warpReady':warp,'warpPending':warp_pending,
                             'adblockReady':bool(block and str(block.get('protocol','')).lower()=='blackhole'),
                             'applyState':apply_state})
        return {'inboundId':inbound_id,'remark':str(inbound.get('remark') or inbound.get('tag') or inbound_id),
                'tag':str(inbound.get('tag') or ''),'rows':rows,'policies':sorted(MATRIX_POLICIES)}

    def _ensure_matrix_base_outbounds(policy:str):
        if policy not in MATRIX_POLICIES:raise HTTPException(400,'Unsupported Traffic Matrix policy')
        adblock,warp=matrix_policy_parts(policy);outs=engine.section('outbounds');changed=False
        direct=next((x for x in outs if isinstance(x,dict) and x.get('tag')=='direct'),None)
        if direct is None:outs.append({'tag':'direct','protocol':'freedom','settings':{}});changed=True
        elif str(direct.get('protocol','')).lower()!='freedom':raise HTTPException(409,"Traffic Matrix requires freedom outbound 'direct'")
        if adblock:
            block=next((x for x in outs if isinstance(x,dict) and x.get('tag')=='block'),None)
            if block is None:outs.append({'tag':'block','protocol':'blackhole','settings':{}});changed=True
            elif str(block.get('protocol','')).lower()!='blackhole':raise HTTPException(409,"Traffic Matrix Adblock requires blackhole outbound 'block'")
        if changed:engine.save_section('outbounds',outs)
        return warp

    def _listener_ready(port:int)->bool:
        if not 1<=int(port or 0)<=65535:return False
        try:
            with socket.create_connection(('127.0.0.1',int(port)),timeout=.5):return True
        except OSError:return False

    def _warp_current_probe(scope:str,outbound:dict,*,attempts:int=2)->dict:
        target=_runtime_target(scope,require_online=True)
        peers=((outbound.get('settings') or {}).get('peers') or [{}])
        current=str(peers[0].get('endpoint') or '') if isinstance(peers,list) and peers and isinstance(peers[0],dict) else ''
        try:
            if target['kind']=='hub':
                row=probe_outbounds(engine._binary(),config.xray_assets,[outbound],tags=['warp'],
                                    attempts=attempts,timeout=5.0,trace=True)[0]
                row['endpoint']=current
            else:
                remote=nodes.warp_endpoint_probe(target['nodeId'],'warp',[current],attempts=attempts,timeout_seconds=5)
                row=remote['items'][0] if remote.get('items') else {}
        except (OutboundProbeError,PolicyError) as ex:
            raise HTTPException(409,'WARP safety probe failed: '+str(ex))
        if not row.get('success') or not row.get('warpVerified'):
            raise HTTPException(409,'WARP path is not usable on '+str(scope)+'; scan/select a working path first')
        return row

    def _apply_matrix_scope(scope:str)->dict:
        target=_runtime_target(scope,require_online=True)
        if target['kind']=='hub':return {'server':target,'runtime':engine.apply(start=True)}
        result=sync_node_assignments(target['nodeId'])
        return {'server':target,'nodeSync':result}

    def apply_global_security(_node_id:str='',_result:dict|None=None,*,client_ids=None):
        coverage=engine.ip_source_coverage()
        result=nodes.reconcile_global_security(local_source_verified=bool(coverage['direct_source_verified']),
                                               local_source_complete=bool(coverage['source_scope_complete']),
                                               client_ids=client_ids)
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
    def renew_node_lease(node_id,traffic):
        return renew_accounting_lease(nodes,manager,engine,node_id,traffic,ensure_node_desired_state,build_node_bundles)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        bot_runtime=None
        if background:
            manager.start();nodes.start(interval=max(5.0,min(60.0,float(config.poll_seconds))),
                                      sync_provider=lambda node_id:build_node_bundles(node_id),
                                      desired_provider=lambda node_id:ensure_node_desired_state(node_id),
                                      traffic_callback=lambda node_id,result:manager.tick(suppress=True),
                                      security_callback=apply_global_security,
                                      lease_callback=renew_node_lease)
            bot_runtime=getattr(app.state,'telegram_runtime',None)
            if bot_runtime:bot_runtime.start()
            license_client.start(config.public_origin)
        yield
        if bot_runtime:bot_runtime.close()
        license_client.close()
        nodes.close();manager.close();engine.close()
    app=FastAPI(title='DARK XRAY',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.replacements=replacements
    app.state.renew_node_lease=renew_node_lease
    app.state.manager=manager;app.state.auth=auth;app.state.engine=engine;app.state.nodes=nodes;app.state.license=license_client
    from dark_restore import DarkRestore
    dark_restore=DarkRestore(store,engine,nodes,manager)
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
        mini_app_html=request.scope.get('path','') in ('/assets/telegram-miniapp.html','/assets/telegram-customer.html')
        response=await call_next(request)
        response.headers.setdefault('Cache-Control','no-store')
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        if not mini_app_html:response.headers.setdefault('X-Frame-Options','DENY')
        csp=("default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; frame-src 'self'; frame-ancestors https://telegram.org https://*.telegram.org; object-src 'none'; base-uri 'none'; form-action 'self'"
             if mini_app_html else
             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        response.headers.setdefault('Content-Security-Policy',csp)
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
    def local_writable():
        if not config.writes_enabled:raise HTTPException(409,'Local writes disabled by administrator')
    def writable():
        local_writable()
        license_client.refresh(config.public_origin)
        status=license_client.status()
        if not status['writes_allowed']:raise HTTPException(402,'DARK license is not active; panel is read-only')

    from licensing import install_licensing
    install_licensing(app,license_client,current,owner,local_writable,manager.audit,config.public_origin)

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
        # Resource credit gates future allocation. Existing clients do not derive
        # runtime enablement from remaining credit, and reductions below current
        # allocation are rejected transactionally by Store.adjust_resource_credit.
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

    @app.post('/api/unlimited-credit/quote')
    def unlimited_credit_quote(body:dict,p:Principal=Depends(current)):
        if set(body)-{'owner','email','client'}:raise HTTPException(400,'Unknown quote field')
        oid=body.get('owner') or p.actor.id;email=body.get('email') or ''
        patch=body.get('client') or {}
        if not isinstance(oid,str) or not isinstance(email,str) or not isinstance(patch,dict):
            raise HTTPException(400,'Invalid credit quote')
        return manager.unlimited_quote(p.actor,oid,patch,email=email)

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
        apply_global_security(client_ids=[email])
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
            # A single transient transport/telemetry failure must not make
            # a persisted deployed Node vanish from subscriptions. The API's
            # online signal now has strict 3-failure/identity/TLS hysteresis;
            # assignment.deployed additionally requires the actual reported
            # Xray runtime and Hub accounting lease to be healthy.
            if (not node.get('enabled') or not node.get('online')
                    or node.get('maintenance')):continue
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
            # Failover entries are direct Node routes. Never clone a local
            # Tunnel/CDN endpoint onto a Node data address; doing so creates a
            # misleading tunnel-labelled link that actually bypasses the tunnel.
            if (item.get('endpointType','direct') or 'direct') in ('tunnel','swap'):continue
            inbound_id=int(item.get('inboundId') or 0)
            for target in targets:
                if inbound_id not in target['inbound_ids']:continue
                explicit_direct=any(int(h.get('inboundId') or 0)==inbound_id and h.get('enable',True)
                                    and h.get('runtime')=='node:'+str(target['node_id'])
                                    and (h.get('endpointType','direct') or 'direct')=='direct'
                                    for h in engine.section('hosts'))
                if explicit_direct:continue
                # Failover is a remote Direct connection, not a Hub-local
                # one. Never inherit the customer's email from the Hub URI.
                remark=str(target['name']).strip()
                if 'مستقیم' not in remark:remark+=' ⚡ مستقیم'
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
        return nodes.global_security(email,local_source_verified=bool(engine.ip_source_coverage()['direct_source_verified']),local_source_complete=bool(engine.ip_source_coverage()['source_scope_complete']))
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
                meta=inbound['panelMeta']
                raw_ports=meta.get('tunnelPorts',{}) if isinstance(meta.get('tunnelPorts',{}),dict) else {}
                tunnel_port=raw_ports.get('node:'+str(node_id))
                meta.pop('deployLocal',None);meta.pop('deploymentTargets',None)
                if type(tunnel_port)is int and 1<=tunnel_port<=65535:meta['tunnelPorts']={'local':int(tunnel_port)}
                else:meta.pop('tunnelPorts',None)
                if not meta:inbound.pop('panelMeta',None)
            clients=[]
            for client in all_clients:
                if source not in client.get('inboundIds',[]):continue
                raw={k:client[k] for k in ('id','password','flow','encryption','security','enable') if k in client}
                clients.append({'sourceEmail':client['email'],'client':raw})
            bundles.append({'sourceInboundId':source,'inbound':inbound,'clients':clients})
        return node_relays.enrich_bundles(node_id,bundles)

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
        sections['outbounds']=engine.runtime_outbounds('node:'+str(node_id))
        sections['routing']=engine.routing_for_scope('node:'+str(node_id),sections['routing'])
        sections=node_relays.compile(node_id,sections)
        bundles,sections=node_port_swaps.enrich(node_id,bundles,sections)
        managed_files=node_managed_files(bundles)
        # Local and Node packet-source trust are separate boundaries. A Central
        # host behind Backhaul may have to remain Observe while direct-source
        # Nodes enforce through their own root-owned broker. Never send the
        # Central-only node_mode field to an Agent.
        node_guard=copy.deepcopy(sections['ipguard'])
        requested_node_mode=node_guard.get('node_mode',node_guard.get('mode','observe'))
        effective_node_mode=requested_node_mode
        if requested_node_mode=='enforce':
            with store.lock:
                verified=store.db.execute(
                    'SELECT source_verified,last_error FROM remote_node_security_state WHERE node_id=?',(node_id,)).fetchone()
            if not verified or not bool(verified['source_verified']) or str(verified['last_error'] or ''):
                effective_node_mode='observe'
        node_guard['mode']=effective_node_mode
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
    from node_lease_sync import grant_stopped_replacement_lease
    def replacement_lease_provider(row,binding,traffic,request):
        state=nodes.desired_state(row['node_id'],include_payload=False)
        if (state['revision']!=row['desired_revision'] or state['hash']!=row['desired_hash']
                or state.get('pending') or state.get('last_error')):
            raise PolicyError('Replacement desired policy is no longer current')
        return grant_stopped_replacement_lease(
            nodes,manager,engine,row['node_id'],traffic,state,row['start_revision'],request)
    replacement_activation=ReplacementActivation(replacement_deployments,lease_provider=replacement_lease_provider)
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
        guard=engine.ip_status()
        return {'sourceVerified':bool(guard.get('source_verified') and not engine.ip_error),
                'sourceScopeComplete':bool(guard.get('source_scope_complete') and not engine.ip_error),
                'directSourceVerified':bool(guard.get('source_verified')),
                'opaqueTunnelPorts':list(guard.get('opaque_tunnel_ports') or []),
                'items':items,'capturedAt':time.time(),'guard':guard}

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

    def node_orchestration_doc():
        node_rows=nodes.list();inbounds=engine.inbounds();hosts=engine.section('hosts');clients=engine.clients()
        client_counts={int(i['id']):0 for i in inbounds}
        for client in clients:
            for inbound_id in client.get('inboundIds',[]):
                if inbound_id in client_counts:client_counts[inbound_id]+=1
        rows=[];ready_routes=deployed_routes=assigned_routes=0
        for inbound in inbounds:
            inbound_id=int(inbound['id'])
            configured=[h for h in hosts if int(h.get('inboundId') or 0)==inbound_id and h.get('enable',True)]
            primary=[{'address':h['address'],'port':int(h['port']),'remark':h.get('remark') or inbound.get('remark') or inbound.get('tag'),
                      'endpoint_type':h.get('endpointType','direct') or 'direct','runtime':h.get('runtime','local') or 'local'}
                     for h in configured]
            if not primary:
                primary=[{'address':config.public_address,'port':int(inbound['port']),'remark':inbound.get('remark') or inbound.get('tag')}]
            routes=[]
            for node in node_rows:
                assignment=next((a for a in node.get('assignments',[]) if int(a['local_inbound_id'])==inbound_id),None)
                if not assignment:continue
                assigned_routes+=1
                if assignment.get('deployed'):deployed_routes+=1
                if assignment.get('failover_ready'):ready_routes+=1
                routes.append({
                    'node_id':node['id'],'name':node['name'],'origin':node['origin'],
                    'data_address':node.get('data_address',''),'data_port':int(inbound['port']),
                    'priority':int(node.get('priority') or 100),'latency_ms':int(node.get('last_latency_ms') or 0),
                    'enabled':bool(node.get('enabled')),'online':bool(node.get('online')),
                    'maintenance':bool(node.get('maintenance')),'maintenance_since':float(node.get('maintenance_since') or 0),
                    'failover_enabled':bool(node.get('failover_enabled')),
                    'remote_inbound_id':int(assignment.get('remote_inbound_id') or 0),
                    'deployment_state':assignment.get('deployment_state','pending'),
                    'subscription_included':bool(assignment.get('failover_ready')),
                    'subscription_reason':assignment.get('failover_reason','not_deployed'),
                    'last_sync':float(assignment.get('last_sync') or 0),
                    'last_error':assignment.get('last_error','')
                })
            routes.sort(key=lambda x:(x['priority'],x['latency_ms'] or 10**9,x['name'],x['node_id']))
            stream=inbound.get('streamSettings',{}) if isinstance(inbound.get('streamSettings'),dict) else {}
            rows.append({'inbound_id':inbound_id,'remark':inbound.get('remark') or inbound.get('tag'),
                         'protocol':inbound.get('protocol'),'network':stream.get('network','tcp'),
                         'security':stream.get('security','none'),'listen_port':int(inbound['port']),
                         'client_count':client_counts.get(inbound_id,0),'primary_endpoints':primary,
                         'routes':routes,'failover_count':sum(1 for x in routes if x['subscription_included'])})
        return {'generated_at':time.time(),'summary':{'nodes':len(node_rows),'inbounds':len(inbounds),
                'assigned_routes':assigned_routes,'deployed_routes':deployed_routes,'subscription_routes':ready_routes},
                'inbounds':rows}

    @app.get('/api/nodes/orchestration')
    def remote_node_orchestration(p:Principal=Depends(owner)):return node_orchestration_doc()

    @app.get('/api/nodes')
    def remote_nodes(p:Principal=Depends(owner)):return nodes.list()

    @app.get('/api/nodes/{node_id}/metrics')
    def remote_node_metrics(node_id:str,window:Literal['live','1h','24h']='live',p:Principal=Depends(owner)):
        doc=nodes.metrics_history(node_id,window)
        source={'version':VERSION,'commit':''}
        source_path=engine.runtime.parent/'installed-source.json'
        try:
            if source_path.is_file() and not source_path.is_symlink() and source_path.stat().st_size<65536:
                raw=json.loads(source_path.read_text(encoding='utf-8'))
                if isinstance(raw,dict):
                    source={'version':str(raw.get('version') or VERSION),'commit':str(raw.get('commit') or '')}
        except (OSError,ValueError):pass
        doc['hub_source']=source
        return doc

    @app.post('/api/nodes/{node_id}/maintenance')
    def remote_node_maintenance(node_id:str,body:NodeMaintenance,p:Principal=Depends(owner)):
        writable()
        result=nodes.set_maintenance(node_id,body.enabled,body.note)
        manager.audit(p.actor,p.actor.id,'node.maintenance',node_id,
                      ('enabled' if body.enabled else 'disabled')+(('; '+body.note[:200]) if body.note else ''))
        return result

    @app.post('/api/nodes/{node_id}/replacement/prepare')
    def prepare_node_replacement(node_id:str,body:NodePair,p:Principal=Depends(owner)):
        writable()
        attempt=replacements.begin(node_id,body.code)
        manager.audit(p.actor,p.actor.id,'node.replacement.prepare',node_id,'attempt='+attempt['attempt_id'])
        return replacements.resume(node_id,attempt['attempt_id'])

    @app.get('/api/nodes/{node_id}/replacement/{attempt_id}')
    def node_replacement_status(node_id:str,attempt_id:str,p:Principal=Depends(owner)):
        return replacements.status(node_id,attempt_id)

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/retry')
    def retry_node_replacement(node_id:str,attempt_id:str,p:Principal=Depends(owner)):
        writable()
        replacements.status(node_id,attempt_id)
        manager.audit(p.actor,p.actor.id,'node.replacement.retry',node_id,'attempt='+attempt_id)
        return replacements.resume(node_id,attempt_id)

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/commit')
    def commit_node_replacement(node_id:str,attempt_id:str,body:ReplacementCommit,p:Principal=Depends(owner)):
        writable()
        result=replacements.commit(node_id,attempt_id,source_binding_id=body.sourceBindingId,
            accept_unconfirmed_old_server=body.acceptUnconfirmedOldServer,
            accept_unreported_traffic=body.acceptUnreportedTraffic)
        manager.audit(p.actor,p.actor.id,'node.replacement.commit',node_id,
                      'attempt='+attempt_id+'; phase='+result['phase'])
        return result

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/cancel')
    def cancel_node_replacement(node_id:str,attempt_id:str,body:ReplacementCancel,p:Principal=Depends(owner)):
        writable()
        result=replacements.cancel(node_id,attempt_id,discard_candidate=body.discardCandidate)
        manager.audit(p.actor,p.actor.id,'node.replacement.cancel',node_id,
                      'attempt='+attempt_id+'; phase='+result['phase'])
        return result

    @app.get('/api/nodes/{node_id}/replacement/{attempt_id}/deployment')
    def node_replacement_deployment_status(node_id:str,attempt_id:str,p:Principal=Depends(owner)):
        return replacement_deployments.status(node_id,attempt_id)

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/stage')
    def stage_node_replacement(node_id:str,attempt_id:str,body:ReplacementStage,p:Principal=Depends(owner)):
        writable()
        result=replacement_deployments.stage(node_id,attempt_id,binding_id=body.bindingId)
        manager.audit(p.actor,p.actor.id,'node.replacement.stage',node_id,
                      'attempt='+attempt_id+'; phase='+result['phase'])
        return result

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/activation/review')
    def review_replacement_activation(node_id:str,attempt_id:str,body:ReplacementStage,p:Principal=Depends(owner)):
        writable()
        result=replacement_activation.review(node_id,attempt_id,binding_id=body.bindingId)
        manager.audit(p.actor,p.actor.id,'node.replacement.activation.review',node_id,'attempt='+attempt_id)
        return result

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/activation/start')
    def activate_replacement(node_id:str,attempt_id:str,body:ReplacementActivate,p:Principal=Depends(owner)):
        writable()
        result=replacement_activation.activate(node_id,attempt_id,binding_id=body.bindingId,
            review_hash=body.reviewHash,confirm_start=body.confirmStart,
            accept_endpoint_responsibility=body.acceptEndpointResponsibility,
            accept_unconfirmed_old_server=body.acceptUnconfirmedOldServer,
            accept_unreported_traffic=body.acceptUnreportedTraffic)
        manager.audit(p.actor,p.actor.id,'node.replacement.activation.start',node_id,
                      'attempt='+attempt_id+'; phase='+result['phase'])
        return result

    @app.post('/api/nodes/{node_id}/replacement/{attempt_id}/activation/pause')
    def pause_replacement_activation(node_id:str,attempt_id:str,body:ReplacementPause,p:Principal=Depends(owner)):
        writable()
        result=replacement_activation.pause(node_id,attempt_id,binding_id=body.bindingId,confirm_stop=body.confirmStop)
        manager.audit(p.actor,p.actor.id,'node.replacement.activation.pause',node_id,
                      'attempt='+attempt_id+'; phase='+result['phase'])
        return result

    @app.get('/api/nodes/{node_id}/replacement/{attempt_id}/activation')
    def replacement_activation_status(node_id:str,attempt_id:str,p:Principal=Depends(owner)):
        return replacement_activation.status(node_id,attempt_id)

    from node_pairing import install_hub_pairing
    install_hub_pairing(app,nodes,owner,writable,manager.audit)
    from node_credentials import install_hub_credentials
    install_hub_credentials(app,nodes,owner,writable,manager.audit)

    def relay_synchronize(target):
        return nodes.sync_desired_state(target,ensure_node_desired_state(target))

    @app.get('/api/swap')
    def swap_workspace(p:Principal=Depends(owner)):
        listed=nodes.list(); by_id={n['id']:n for n in listed}
        routes=[]
        for row in node_port_swaps.rows():
            item=node_port_swaps.public(row)
            try:node_port_swaps.validate(row);item['configurationError']=''
            except PolicyError as ex:item['configurationError']=str(ex)
            item['sourceState']=by_id.get(row['source_node'],{}).get('desired_state',{})
            item['exitState']=by_id.get(row['exit_node'],{}).get('desired_state',{})
            routes.append(item)
        return {'model':'dedicated-tcp-port','routes':routes,'legacyRoutes':[node_relays.public(r) for r in node_relays.rows()], 'nodes':listed,'inbounds':[
            {k:i.get(k) for k in ('id','tag','remark','protocol','enable','listen','port')} |
            {'network':i.get('streamSettings',{}).get('network','tcp'),
             'security':i.get('streamSettings',{}).get('security','none'),
             'tunnelPorts':i.get('panelMeta',{}).get('tunnelPorts',{})}
            for i in engine.inbounds()]}

    @app.post('/api/swap')
    def swap_create(body:PortSwapBody,p:Principal=Depends(owner)):
        writable();result=node_port_swaps.configure(body.model_dump())
        manager.audit(p.actor,p.actor.id,'swap.port.create',str(result['id']),result['name'])
        return result

    @app.put('/api/swap/{route_id}')
    def swap_edit(route_id:int,body:PortSwapBody,p:Principal=Depends(owner)):
        writable();result=node_port_swaps.configure(body.model_dump(),route_id,relay_synchronize)
        manager.audit(p.actor,p.actor.id,'swap.port.edit',str(route_id),result['name'])
        return result

    @app.patch('/api/swap/{route_id}/name')
    def swap_rename(route_id:int,body:PortSwapRename,p:Principal=Depends(owner)):
        writable();result=node_port_swaps.rename(route_id,body.name)
        manager.audit(p.actor,p.actor.id,'swap.port.rename',str(route_id),result['name'])
        return result

    @app.post('/api/swap/{route_id}/state')
    def swap_toggle(route_id:int,body:RelayToggle,p:Principal=Depends(owner)):
        writable();result=node_port_swaps.toggle(route_id,body.enabled,relay_synchronize)
        manager.audit(p.actor,p.actor.id,'swap.port.enable' if body.enabled else 'swap.port.disable',str(route_id),'')
        return result

    @app.delete('/api/swap/{route_id}')
    def swap_delete(route_id:int,p:Principal=Depends(owner)):
        writable();result=node_port_swaps.delete(route_id,relay_synchronize)
        manager.audit(p.actor,p.actor.id,'swap.port.delete',str(route_id),'')
        return result

    @app.post('/api/swap/{route_id}/diagnostics')
    def swap_diagnostics(route_id:int,p:Principal=Depends(owner)):
        writable();row=node_port_swaps.get(route_id)
        if not row['enabled'] or row['phase']!='enabled':raise PolicyError('Enable and synchronize the route first')
        node_port_swaps.validate(row)
        listener=nodes.traffic_matrix_probe(row['source_node'],row['source_port'],'dark-swap-'+str(route_id),attempts=1,timeout_seconds=3)
        direct=next((o['tag'] for o in engine.runtime_outbounds('node:'+row['exit_node']) if o.get('protocol')=='freedom'),None)
        if not direct:raise PolicyError('Destination has no direct outbound to diagnose')
        probe=nodes.outbound_probe(row['exit_node'],direct,attempts=1,timeout_seconds=3)['probe']
        egress=probe.get('egress',{})
        result={'relayListenerReady':bool(listener['listenerReady']), 'destinationInternet':{'success':bool(probe.get('success')), 'delayMs':probe.get('delayMs'),
                'egress':{k:egress[k] for k in ('ip','country') if k in egress}},
                'endToEndVerified':False,'scope':'relay listener and destination internet separately; test the generated customer link for end-to-end', 'checkedAt':time.time()}
        manager.audit(p.actor,p.actor.id,'swap.port.diagnostics',str(route_id),'')
        return result

    @app.delete('/api/nodes/{node_id}/exits/{inbound_id}')
    def remote_node_exit_delete(node_id:str,inbound_id:int,p:Principal=Depends(owner)):
        writable()
        result=node_relays.delete(node_id,inbound_id,relay_synchronize)
        manager.audit(p.actor,p.actor.id,'node.exit.delete',node_id,'inbound='+str(inbound_id))
        return result

    @app.post('/api/nodes/{node_id}/exits/{inbound_id}/probe')
    def remote_node_exit_probe(node_id:str,inbound_id:int,p:Principal=Depends(owner)):
        writable()
        result=node_relays.probe(node_id,inbound_id)
        manager.audit(p.actor,p.actor.id,'node.exit.probe',node_id,'inbound='+str(inbound_id)+'; success='+str(result['success']))
        return result

    @app.get('/api/nodes/{node_id}/exits')
    def remote_node_exits(node_id:str,p:Principal=Depends(owner)):
        return node_relays.list(node_id)

    @app.put('/api/nodes/{node_id}/exits/{inbound_id}')
    def remote_node_exit_configure(node_id:str,inbound_id:int,body:RelayBody,p:Principal=Depends(owner)):
        writable()
        result=node_relays.configure(node_id,inbound_id,body.exitNodeId,body.exitInboundId)
        manager.audit(p.actor,p.actor.id,'node.exit.configure',node_id,
                      'inbound='+str(inbound_id)+'; exit='+body.exitNodeId+'; exit_inbound='+str(body.exitInboundId))
        return result

    @app.post('/api/nodes/{node_id}/exits/{inbound_id}')
    def remote_node_exit_toggle(node_id:str,inbound_id:int,body:RelayToggle,p:Principal=Depends(owner)):
        writable()
        result=node_relays.toggle(node_id,inbound_id,body.enabled,relay_synchronize)
        manager.audit(p.actor,p.actor.id,'node.exit.enable' if body.enabled else 'node.exit.disable',node_id,
                      'inbound='+str(inbound_id))
        return result

    app.state.node_relays=node_relays
    app.state.node_port_swaps=node_port_swaps

    @app.post('/api/nodes')
    def remote_node_add(body:NodeCreate,p:Principal=Depends(owner)):
        writable()
        known={i['id'] for i in engine.inbounds()}
        if not set(body.inboundIds)<=known:raise HTTPException(400,'Unknown inbound assignment')
        node_relays.check_node_change(body.id,enabled=body.enabled,inbound_ids=body.inboundIds,origin=body.origin)
        node_port_swaps.check_node_change(body.id,enabled=body.enabled,inbound_ids=body.inboundIds,origin=body.origin)
        result=nodes.put(body.id,body.name,body.origin,body.token,body.enabled,body.inboundIds,
                         body.dataAddress,body.priority,body.failoverEnabled)
        manager.audit(p.actor,p.actor.id,'node.create',body.id)
        return result
    @app.patch('/api/nodes/{node_id}')
    def remote_node_edit(node_id:str,body:NodePatch,p:Principal=Depends(owner)):
        writable()
        known={i['id'] for i in engine.inbounds()}
        if not set(body.inboundIds)<=known:raise HTTPException(400,'Unknown inbound assignment')
        if body.token:
            # Credential handoff is independent of configuration. Do not rotate
            # first, then discover that the requested metadata is invalid.
            current_node=nodes.get(node_id)
            expected={'name':current_node['name'],'origin':current_node['origin'],
                'dataAddress':current_node['data_address'],'enabled':current_node['enabled'],
                'priority':current_node['priority'],'failoverEnabled':current_node['failover_enabled'],
                'inboundIds':sorted(current_node['inboundIds'])}
            supplied=body.model_dump(include=set(expected));supplied['inboundIds']=sorted(set(body.inboundIds))
            if supplied!=expected:raise HTTPException(409,'Save settings separately from token rotation')
            result=nodes.rotate_token(node_id,body.token)
            manager.audit(p.actor,p.actor.id,'node.credential.rotate',node_id,'phase='+result['phase'])
            return result
        if not body.keep_token:raise HTTPException(400,'Provide a replacement token or keep_token=true')
        node_relays.check_node_change(node_id,enabled=body.enabled,inbound_ids=body.inboundIds,origin=body.origin)
        node_port_swaps.check_node_change(node_id,enabled=body.enabled,inbound_ids=body.inboundIds,origin=body.origin)
        token=nodes.get(node_id,secret=True)['token']
        result=nodes.put(node_id,body.name,body.origin,token,body.enabled,body.inboundIds,
                         body.dataAddress,body.priority,body.failoverEnabled)
        for target in {node_id}:ensure_node_desired_state(target)
        apply_global_security()
        manager.audit(p.actor,p.actor.id,'node.update',node_id);return result
    @app.delete('/api/nodes/{node_id}')
    def remote_node_delete(node_id:str,p:Principal=Depends(owner)):
        writable()
        node_relays.check_node_change(node_id,deleting=True)
        node_port_swaps.check_node_change(node_id,deleting=True)
        refs=[h for h in engine.section('hosts') if h.get('runtime')=='node:'+node_id]
        if refs:raise HTTPException(409,'Move or delete Public Endpoints that use this Node before deleting it')
        result=nodes.delete(node_id)
        scope='node:'+str(node_id)
        with store.transaction() as db:
            db.execute('DELETE FROM traffic_matrix WHERE scope=?',(scope,))
            db.execute('DELETE FROM warp_profiles WHERE scope=?',(scope,))
            db.execute('DELETE FROM warp_pending_profiles WHERE scope=?',(scope,))
        apply_global_security()
        manager.audit(p.actor,p.actor.id,'node.delete',node_id);return result
    @app.post('/api/nodes/{node_id}/probe')
    def remote_node_probe(node_id:str,p:Principal=Depends(owner)):
        result=nodes.probe(node_id);manager.audit(p.actor,p.actor.id,'node.probe',node_id);return result

    @app.get('/api/nodes/{node_id}/logs/{kind}')
    def remote_node_logs(node_id:str,kind:Literal['process','error','access'],limit:int=300,p:Principal=Depends(owner)):
        return nodes.remote_logs(node_id,kind,max(1,min(1000,limit)))

    @app.get('/api/nodes/{node_id}/update')
    def remote_node_update_status(node_id:str,p:Principal=Depends(owner)):
        result=nodes.remote_update_status(node_id);result['hub_commit']=hub_source_commit();return result

    @app.post('/api/nodes/{node_id}/update/check')
    def remote_node_update_check(node_id:str,p:Principal=Depends(owner)):
        commit=hub_source_commit();result=nodes.remote_update_check(node_id,commit)
        result['hub_commit']=commit;manager.audit(p.actor,p.actor.id,'node.update_check',node_id,commit);return result

    @app.post('/api/nodes/{node_id}/update/start',status_code=202)
    def remote_node_update_start(node_id:str,p:Principal=Depends(owner)):
        if config.test_engine:raise HTTPException(409,'Node update is disabled in test-engine mode')
        commit=hub_source_commit();result=nodes.remote_update_start(node_id,commit)
        result['hub_commit']=commit;manager.audit(p.actor,p.actor.id,'node.update_start',node_id,commit);return result
    @app.get('/api/nodes/{node_id}/desired')
    def remote_node_desired(node_id:str,p:Principal=Depends(owner)):
        return ensure_node_desired_state(node_id)

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

    @app.get('/api/inbounds/{inbound_id}/deployments')
    def inbound_deployments(inbound_id:int,p:Principal=Depends(owner)):
        inbound=engine.inbound(inbound_id);meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
        selected=set(nodes.inbound_assignments(inbound_id));fleet=nodes.list()
        raw_ports=meta.get('tunnelPorts',{}) if isinstance(meta.get('tunnelPorts',{}),dict) else {}
        tunnel_ports={str(k):int(v) for k,v in raw_ports.items()
                      if isinstance(k,str) and type(v)is int and 1<=v<=65535}
        hosts=engine.section('hosts')
        def tunnel_route(runtime:str)->dict:
            port=int(tunnel_ports.get(runtime) or 0)
            configured=[h for h in hosts if int(h.get('inboundId') or 0)==inbound_id
                        and h.get('enable',True)
                        and (h.get('runtime','local') or 'local')==runtime
                        and (h.get('endpointType','direct') or 'direct')=='tunnel']
            matching=[h for h in configured if port and int(h.get('port') or 0)==port]
            if not port:state='off'
            elif matching:state='active'
            elif configured:state='port_mismatch'
            else:state='waiting_host'
            return {'runtime':runtime,'port':port,'enabled':bool(port),'state':state,
                    'matchingHosts':len(matching),'configuredHosts':len(configured)}
        routes={'local':tunnel_route('local')}
        for n in fleet:routes['node:'+str(n['id'])]=tunnel_route('node:'+str(n['id']))
        swaps=[node_port_swaps.public(r) for r in node_port_swaps.rows() if r['inbound_id']==inbound_id]
        return {'inboundId':inbound_id,'swapRoutes':swaps,'local':meta.get('deployLocal',True) is not False,
                'nodeIds':sorted(selected),'tunnelPorts':tunnel_ports,'tunnelRoutes':routes,
                'targets':[{'id':n['id'],'name':n['name'],'online':bool(n.get('online')),'enabled':bool(n.get('enabled')),
                            'selected':n['id'] in selected,'pending':bool(n.get('desired_state',{}).get('pending')),
                            'runtime':'node:'+str(n['id']),'tunnel':routes['node:'+str(n['id'])]}
                           for n in fleet]}

    @app.put('/api/inbounds/{inbound_id}/deployments')
    def inbound_deployments_put(inbound_id:int,body:InboundDeployments,p:Principal=Depends(owner)):
        writable();inbound=engine.inbound(inbound_id);fleet=nodes.list();known={str(n['id']) for n in fleet}
        requested=list(dict.fromkeys(str(x) for x in body.nodeIds))
        if any(not NAME_RE.fullmatch(x) for x in requested) or not set(requested)<=known:
            raise HTTPException(400,'Unknown node deployment target')
        for node in fleet:
            remaining=[i for i in node['inboundIds'] if i!=inbound_id]
            if node['id'] in requested:remaining.append(inbound_id)
            node_relays.check_node_change(node['id'],inbound_ids=remaining)
            node_port_swaps.check_node_change(node['id'],inbound_ids=remaining)
        allowed_runtimes=({'local'} if body.local else set())|{'node:'+x for x in requested}
        tunnel_ports={}
        for runtime,port in body.tunnelPorts.items():
            runtime=str(runtime)
            if runtime not in allowed_runtimes:
                raise HTTPException(400,'Tunnel Port requires the same inbound deployment target')
            if type(port)is not int or not 1<=port<=65535:
                raise HTTPException(400,'Tunnel Port must be between 1 and 65535')
            tunnel_ports[runtime]=int(port)
        meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
        inbound=copy.deepcopy(inbound);inbound.pop('id',None);inbound.pop('applied',None)
        meta=copy.deepcopy(meta);meta['deployLocal']=bool(body.local);meta['deploymentTargets']=['local']*int(bool(body.local))+requested
        meta['tunnelPorts']=dict(sorted(tunnel_ports.items()))
        inbound['panelMeta']=meta;node_port_swaps.check_inbound(inbound,inbound_id);engine.save_inbound(inbound,inbound_id)
        # Tunnel Port is a real listener contract. Apply the new local Xray
        # generation immediately; CoreEngine validates and rolls back the
        # previous running generation if the shadow listener cannot start.
        manager.tick(suppress=False)
        before=set(nodes.inbound_assignments(inbound_id));after=set(requested)
        for node_id in sorted(before|after):
            nodes.set_inbound_assignment(node_id,inbound_id,node_id in after)
            # Persist the new Hub desired revision immediately even when a node
            # is offline. The monitor will apply it when connectivity returns.
            ensure_node_desired_state(node_id)
        valid_scopes=({'hub'} if body.local else set())|{'node:'+x for x in after}
        with store.transaction() as db:
            for row in db.execute('SELECT scope,access_path FROM traffic_matrix WHERE inbound_id=?',(inbound_id,)).fetchall():
                scope=str(row['scope']);path=str(row['access_path'])
                runtime='local' if scope=='hub' else scope
                if scope not in valid_scopes or (path=='tunnel' and runtime not in tunnel_ports):
                    db.execute('DELETE FROM traffic_matrix WHERE scope=? AND inbound_id=? AND access_path=?',
                               (scope,inbound_id,path))
        manager.audit(p.actor,p.actor.id,'inbound.deployments',str(inbound_id),
                      'local='+str(bool(body.local))+'; nodes='+','.join(sorted(after))+
                      '; tunnel_ports='+','.join(k+':'+str(v) for k,v in sorted(tunnel_ports.items())))
        return inbound_deployments(inbound_id,p)

    @app.get('/api/traffic-matrix')
    def traffic_matrix(inboundId:int,p:Principal=Depends(owner)):
        return _matrix_doc(inboundId)

    def _matrix_write(rows:list[tuple[str,int,str,str]],p:Principal)->dict:
        writable()
        if not rows:raise HTTPException(400,'Traffic Matrix update is empty')
        inbound_id=int(rows[0][1]);doc=_matrix_doc(inbound_id)
        allowed={(str(x['serverId']),str(x['accessPath'])) for x in doc['rows']}
        scopes=set()
        old={}
        warp_scopes=set()
        for scope,iid,path,policy in rows:
            if iid!=inbound_id or (scope,path) not in allowed:raise HTTPException(400,'Traffic Matrix path is not deployed')
            if policy not in MATRIX_POLICIES:raise HTTPException(400,'Unsupported Traffic Matrix policy')
            _ensure_matrix_base_outbounds(policy)
            if policy.startswith('warp_'):
                profile=_warp_profile(scope)
                if profile is None:raise HTTPException(409,'Create WARP on this runtime before selecting a WARP policy')
                warp_scopes.add(scope)
        for scope in sorted(warp_scopes):_warp_current_probe(scope,_warp_profile(scope),attempts=2)
        with store.lock:
            for scope,iid,path,policy in rows:
                row=store.db.execute('SELECT policy,updated_at FROM traffic_matrix WHERE scope=? AND inbound_id=? AND access_path=?',
                                     (scope,iid,path)).fetchone()
                old[(scope,iid,path)]=dict(row) if row else None;scopes.add(scope)
        with store.transaction() as db:
            for scope,iid,path,policy in rows:
                db.execute("""INSERT INTO traffic_matrix(scope,inbound_id,access_path,policy,updated_at) VALUES(?,?,?,?,?)
                              ON CONFLICT(scope,inbound_id,access_path) DO UPDATE SET policy=excluded.policy,updated_at=excluded.updated_at""",
                           (scope,iid,path,policy,time.time()))
        try:
            applied={scope:_apply_matrix_scope(scope) for scope in sorted(scopes)}
        except Exception as ex:
            with store.transaction() as db:
                for key,prev in old.items():
                    scope,iid,path=key
                    if prev is None:db.execute('DELETE FROM traffic_matrix WHERE scope=? AND inbound_id=? AND access_path=?',key)
                    else:db.execute('UPDATE traffic_matrix SET policy=?,updated_at=? WHERE scope=? AND inbound_id=? AND access_path=?',
                                    (prev['policy'],prev['updated_at'],scope,iid,path))
            for scope in sorted(scopes):
                try:_apply_matrix_scope(scope)
                except Exception:pass
            raise HTTPException(409,'Traffic Matrix apply failed and was rolled back: '+str(ex)[:300])
        manager.audit(p.actor,p.actor.id,'traffic_matrix.apply',str(inbound_id),
                      '; '.join(scope+'/'+path+'='+policy for scope,_iid,path,policy in rows))
        return _matrix_doc(inbound_id)|{'applied':applied}

    @app.post('/api/traffic-matrix')
    def traffic_matrix_set(body:TrafficMatrixSet,p:Principal=Depends(owner)):
        return _matrix_write([(str(body.server),int(body.inboundId),str(body.accessPath),str(body.policy))],p)

    @app.post('/api/traffic-matrix/batch')
    def traffic_matrix_batch(body:TrafficMatrixBatch,p:Principal=Depends(owner)):
        return _matrix_write([(str(body.server),int(body.inboundId),str(path),str(body.policy)) for path in dict.fromkeys(body.accessPaths)],p)

    @app.post('/api/traffic-matrix/probe')
    def traffic_matrix_probe(body:TrafficMatrixProbe,p:Principal=Depends(owner)):
        doc=_matrix_doc(int(body.inboundId));row=next((x for x in doc['rows']
            if x['serverId']==str(body.server) and x['accessPath']==str(body.accessPath)),None)
        if not row:raise HTTPException(404,'Traffic Matrix path not found')
        target=_runtime_target(str(body.server),require_online=True);policy=str(row['policy']);warp=policy.startswith('warp_')
        outbound_tag='warp' if warp else 'direct'
        if warp and _warp_profile(str(body.server)) is None:raise HTTPException(409,'WARP profile is missing on this runtime')
        try:
            if target['kind']=='hub':
                listener=_listener_ready(int(row['port']))
                items=probe_outbounds(engine._binary(),config.xray_assets,engine.runtime_outbounds(str(body.server)),
                                      tags=[outbound_tag],attempts=int(body.attempts),timeout=5.0,trace=warp)
                result=items[0] if items else {}
            else:
                remote=nodes.traffic_matrix_probe(target['nodeId'],int(row['port']),outbound_tag,
                                                  attempts=int(body.attempts),timeout_seconds=5)
                listener=bool(remote.get('listenerReady'));result=remote.get('probe') or {}
        except (OutboundProbeError,PolicyError) as ex:raise HTTPException(409,'Traffic Matrix probe failed: '+str(ex))
        warp_verified=bool(result.get('warpVerified')) if warp else True
        ok=listener and bool(result.get('success')) and warp_verified
        return {'ok':ok,'server':target,'accessPath':row['accessPath'],'policy':policy,'port':row['port'],
                'listenerReady':listener,'delayMs':result.get('delayMs'),'lossPercent':result.get('lossPercent'),
                'jitterMs':result.get('jitterMs'),'warpVerified':warp_verified,'egress':result.get('egress',{}),
                'adblockReady':bool(row.get('adblockReady')),'productionTrafficMutation':False}

    @app.get('/api/xray-settings/servers')
    def xray_settings_servers(p:Principal=Depends(owner)):
        return {'items':[target|{'applyState':_settings_apply_state(target['id'])}
                         for target in _runtime_targets()], 'productionTrafficMutation':False}

    @app.get('/api/traffic-matrix/warp')
    def traffic_matrix_warp_status(server:str='hub',p:Principal=Depends(owner)):
        target=_runtime_target(server,require_online=False);profile=_warp_profile(server);pending=_warp_pending(server)
        def endpoint_of(value):
            if not value:return ''
            peers=((value.get('settings') or {}).get('peers') or [{}])
            return str(peers[0].get('endpoint') or '') if isinstance(peers,list) and peers and isinstance(peers[0],dict) else ''
        return {'server':target,'registered':bool(profile),'endpoint':endpoint_of(profile),
                'pendingRegistration':bool(pending),'pendingEndpoint':endpoint_of(pending),
                'manualSelectionRequired':bool(pending),'autoBestDefault':False,
                'applyState':_settings_apply_state(server)}

    @app.post('/api/traffic-matrix/warp/create')
    def traffic_matrix_warp_create(body:WarpCreate,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server,require_online=True)
        try:registered=register_cloudflare_warp(tag='warp')
        except WarpRegistrationError as ex:raise HTTPException(502,str(ex))
        candidate=copy.deepcopy(registered['outbound']);candidate['tag']='warp'
        peers=((candidate.get('settings') or {}).get('peers') or [{}])
        default_endpoint=str(peers[0].get('endpoint') or '') if peers else ''
        _warp_pending_save(body.server,candidate,registered.get('deviceId',''))
        active=_warp_profile(body.server);active_endpoint=''
        if active:
            active_peers=((active.get('settings') or {}).get('peers') or [{}])
            if isinstance(active_peers,list) and active_peers and isinstance(active_peers[0],dict):
                active_endpoint=str(active_peers[0].get('endpoint') or '')
        manager.audit(p.actor,p.actor.id,'traffic_matrix.warp.register',body.server,
                      'pending registration created; manual endpoint selection required')
        return {'registered':False,'pendingRegistration':True,'server':target,
                'activeEndpoint':active_endpoint,'candidateEndpoint':default_endpoint,
                'selected':active_endpoint,'requiresScan':True,'manualSelectionRequired':True,
                'productionTrafficMutation':False,'applied':False}

    @app.post('/api/traffic-matrix/warp/scan')
    def traffic_matrix_warp_scan(body:WarpEndpointScan,p:Principal=Depends(owner)):
        target=_runtime_target(body.server,require_online=True);active=_warp_profile(body.server);pending=_warp_pending(body.server)
        outbound=pending or active
        if not outbound:raise HTTPException(409,'Register WARP on this runtime before scanning paths')
        peers=((outbound.get('settings') or {}).get('peers') or [{}]);candidate_current=str(peers[0].get('endpoint') or '') if peers else ''
        active_current=''
        if active:
            active_peers=((active.get('settings') or {}).get('peers') or [{}])
            if isinstance(active_peers,list) and active_peers and isinstance(active_peers[0],dict):
                active_current=str(active_peers[0].get('endpoint') or '')
        endpoints=list(dict.fromkeys(([active_current] if active_current else [])+warp_endpoint_candidates(candidate_current)))
        raw=[]
        # Bounded batches release the existing per-node operation lock between
        # requests, letting rc29 accounting/lease renewal continue during scans.
        # A failed batch is visible per endpoint and cannot erase other results.
        for offset in range(0,len(endpoints),4):
            batch=endpoints[offset:offset+4]
            try:
                if target['kind']=='hub':
                    clones=[];mapping={}
                    for idx,endpoint in enumerate(batch):
                        item=copy.deepcopy(outbound);tag='dark-warp-path-'+str(offset+idx);item['tag']=tag
                        item['settings']['peers'][0]['endpoint']=endpoint;clones.append(item);mapping[tag]=endpoint
                    results=probe_outbounds(engine._binary(),config.xray_assets,clones,tags=[x['tag'] for x in clones],
                                            attempts=2,timeout=4.0,trace=True)
                    for result in results:result['endpoint']=mapping.get(str(result.get('tag') or ''),'')
                else:
                    remote=nodes.warp_endpoint_probe(target['nodeId'],'warp',batch,attempts=2,timeout_seconds=4,
                                                     outbound=outbound if pending else None)
                    results=remote['items']
                raw.extend(results)
            except (OutboundProbeError,PolicyError) as ex:
                raw.extend({'endpoint':endpoint,'success':False,'warpVerified':False,
                            'error':str(ex)[:300]} for endpoint in batch)
        items=warp_scan_results(endpoints,raw,active_current,candidate_current if pending else '')
        return {'server':target,'selected':active_current,'pendingRegistration':bool(pending),
                'candidateEndpoint':candidate_current,'manualSelectionRequired':bool(pending),
                'items':items,'resultCount':len(items),'scannedAt':time.time(),
                'productionTrafficMutation':False}

    @app.post('/api/traffic-matrix/warp/endpoint')
    def traffic_matrix_warp_endpoint(body:WarpEndpointSelect,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server,require_online=True);old=_warp_profile(body.server);pending=_warp_pending(body.server)
        source=pending or old
        if not source:raise HTTPException(409,'Register WARP on this runtime before selecting a path')
        try:endpoint=validate_warp_endpoint(body.endpoint)
        except WarpRegistrationError as ex:raise HTTPException(400,str(ex))
        candidate=copy.deepcopy(source);candidate['tag']='warp';candidate['settings']['peers'][0]['endpoint']=endpoint
        try:
            if target['kind']=='hub':
                checked=probe_outbounds(engine._binary(),config.xray_assets,[candidate],tags=['warp'],attempts=2,timeout=5.0,trace=True)[0]
            else:
                checked=nodes.warp_endpoint_probe(target['nodeId'],'warp',[endpoint],attempts=2,timeout_seconds=5,
                                                  outbound=candidate if pending else None)['items'][0]
        except (OutboundProbeError,PolicyError) as ex:raise HTTPException(409,'Selected WARP path test failed: '+str(ex))
        if not checked.get('success') or not checked.get('warpVerified'):
            raise HTTPException(409,'Selected WARP path did not verify Cloudflare warp=on')
        _warp_profile_save(body.server,candidate,'manual-endpoint-selection')
        try:applied=_apply_matrix_scope(body.server)
        except Exception as ex:
            if old is None:_warp_profile_delete(body.server)
            else:_warp_profile_save(body.server,old,'rollback')
            try:_apply_matrix_scope(body.server)
            except Exception:pass
            raise HTTPException(409,'WARP endpoint apply failed and was rolled back: '+str(ex)[:300])
        if pending:_warp_pending_delete(body.server)
        manager.audit(p.actor,p.actor.id,'traffic_matrix.warp.endpoint',body.server,'manual endpoint='+endpoint)
        return {'server':target,'selected':endpoint,'test':checked,'applied':applied,
                'pendingRegistration':False,'manualSelectionRequired':False,
                'applyState':_settings_apply_state(body.server)}

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
            meta=r.get('panelMeta',{}) if isinstance(r.get('panelMeta',{}),dict) else {}
            raw_ports=meta.get('tunnelPorts',{}) if isinstance(meta.get('tunnelPorts',{}),dict) else {}
            item['tunnelPorts']={str(k):int(v) for k,v in raw_ports.items()
                                 if isinstance(k,str) and type(v)is int and 1<=v<=65535}
            stream=r.get('streamSettings',{}) if isinstance(r.get('streamSettings',{}),dict) else {}
            item['network']=stream.get('network','tcp')
            item['security']=stream.get('security','none')
            out.append(item)
        return out
    @app.get('/api/unmanaged')
    def unmanaged(p:Principal=Depends(owner)):
        with store.lock:managed={r[0] for r in store.db.execute('SELECT email FROM managed_clients')}
        return [{'email':r['email'],'inboundIds':r.get('inboundIds',[]),'enable':r.get('enable',False)} for r in engine.clients() if r['email'] not in managed and not str(r['email']).endswith('@dark.restore')]
    @app.get('/api/system')
    def system(p:Principal=Depends(current)):
        p.actor.require('system','read')
        return {'source':'DARK local host + Xray-core','sample':False,'engine':engine.system()}
    @app.get('/api/gateway-system')
    def gateway_system(p:Principal=Depends(owner)):
        mem=psutil.virtual_memory();disk=psutil.disk_usage('/');swap=psutil.swap_memory()
        return {'source':'DARK API host','sample':False,'cpu':round(float(psutil.cpu_percent(interval=.2)),1),
                'memory_percent':mem.percent,'disk_percent':disk.percent,'swap_percent':swap.percent,
                'uptime':int(time.time()-psutil.boot_time())}
    def security_center_payload(p:Principal):
        p.actor.require('clients','ip',p.actor.id)
        rows=manager.list(p.actor);now=time.time();settings=engine.section('ipguard');window=int(settings.get('window_seconds',120))
        allowed={r['email'] for r in rows}
        with store.lock:
            policy_rows={str(r['id']):dict(r) for r in store.db.execute(
                'SELECT id,limit_ip,global_ip_block,global_device_block FROM clients')}
            local_ip_values={}
            for r in store.db.execute('SELECT client_id,ip FROM observations WHERE last_seen>?',(now-window,)):
                local_ip_values.setdefault(str(r['client_id']),set()).add(str(r['ip']))
            local_device_values={}
            for r in store.db.execute('SELECT email,digest FROM core_devices'):
                local_device_values.setdefault(str(r['email']),set()).add(str(r['digest']))
            remote_ip_values={}
            if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_ips'").fetchone():
                for r in store.db.execute('SELECT client_id,ip FROM remote_node_ips WHERE verified=1 AND last_seen>?',(now-window,)):
                    remote_ip_values.setdefault(str(r['client_id']),set()).add(str(r['ip']))
            remote_device_values={}
            if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_devices'").fetchone():
                for r in store.db.execute('SELECT client_id,digest FROM remote_node_devices'):
                    remote_device_values.setdefault(str(r['client_id']),set()).add(str(r['digest']))
            active_bans=[dict(r) for r in store.db.execute(
                "SELECT b.ip,b.client_id,b.node,b.expires_at,b.state FROM bans b JOIN clients c ON c.id=b.client_id "
                "WHERE b.state='applied' AND b.expires_at>? ORDER BY b.expires_at DESC",(now,)) if str(r['client_id']) in allowed]
            events=[dict(r) for r in store.db.execute(
                'SELECT id,kind,owner,client_id,ip,node,detail,at FROM events ORDER BY id DESC LIMIT 250')
                if str(r['client_id']) in allowed]
        client_rows=[]
        for item in rows:
            email=item['email'];client=item.get('client') or {};policy=policy_rows.get(email,{})
            local_ip_set=local_ip_values.get(email,set());remote_ip_set=remote_ip_values.get(email,set())
            local_device_set=local_device_values.get(email,set());remote_device_set=remote_device_values.get(email,set())
            client_rows.append({'email':email,'owner':item.get('owner',''),'limit_ip':int(client.get('limitIp') or 0),
                'limit_hwid':int(client.get('limitHwid') or 0),'local_ip_count':len(local_ip_set),'remote_ip_count':len(remote_ip_set),
                'global_ip_count':len(local_ip_set|remote_ip_set),'local_device_count':len(local_device_set),'remote_device_count':len(remote_device_set),
                'global_device_count':len(local_device_set|remote_device_set),'global_ip_block':bool(policy.get('global_ip_block')),
                'global_device_block':bool(policy.get('global_device_block')),
                'block_reasons':item.get('block_reasons',[]),'presence_state':item.get('presence_state','offline'),
                'last_seen_at':item.get('last_seen_at',0)})
        node_rows=nodes.list() if p.actor.role=='owner' else []
        active_node_rows=[n for n in node_rows if n.get('enabled')]
        fresh_nodes=sum(1 for n in active_node_rows if n.get('security',{}).get('last_sync') and
                        now-float(n['security']['last_sync'])<180 and not n['security'].get('last_error'))
        verified_nodes=sum(1 for n in active_node_rows if n.get('security',{}).get('source_verified') and
                           n.get('security',{}).get('last_sync') and now-float(n['security']['last_sync'])<180 and
                           not n['security'].get('last_error'))
        complete_nodes=sum(1 for n in active_node_rows if nodes._node_source_scope_complete(str(n.get('id') or '')))
        node_guard_enforce=0;node_guard_ready=0;node_policy_pending=0;offline_nodes=0
        for n in active_node_rows:
            health=n.get('health') if isinstance(n.get('health'),dict) else {}
            remote_guard=health.get('guard') if isinstance(health.get('guard'),dict) else {}
            if remote_guard.get('requested_mode')=='enforce':node_guard_enforce+=1
            if (remote_guard.get('requested_mode')=='enforce' and remote_guard.get('state')=='applied'
                and remote_guard.get('applied') is True and remote_guard.get('source_verified') is True):
                node_guard_ready+=1
            if n.get('desired_state',{}).get('pending'):node_policy_pending+=1
            if not n.get('online'):offline_nodes+=1
        guard=engine.ip_status()
        summary={'clients':len(client_rows),'ip_limited':sum(1 for x in client_rows if x['limit_ip']>0),
            'hwid_limited':sum(1 for x in client_rows if x['limit_hwid']>0),
            'ip_blocked':sum(1 for x in client_rows if x['global_ip_block']),
            'device_blocked':sum(1 for x in client_rows if x['global_device_block']),
            'active_local_bans':len(active_bans),'recent_violations':sum(1 for x in events if x['kind']=='violation' and now-float(x['at'])<=window)}
        return {'source':'DARK Native Security Center','guard':guard,'settings':settings,'summary':summary,
            'clients':client_rows,'events':events,'bans':active_bans,
            'nodes':{'total':len(active_node_rows),'security_fresh':fresh_nodes,'source_verified':verified_nodes,
                     'source_scope_complete':complete_nodes,
                     'guard_enforce':node_guard_enforce,'guard_ready':node_guard_ready,
                     'policy_pending':node_policy_pending,'offline':offline_nodes} if p.actor.role=='owner' else None,
            'architecture':{'local_observer':'Xray access log source-IP observation',
                'local_enforcer':'root-owned DARK nftables broker','local_firewall_scope':'this host data ports only',
                'global_policy':'central Local + Node verified-source aggregation',
                'global_action':'client service block across synchronized DARK runtime',
                'global_remote_firewall_ban':False}}

    def traffic_engine_doc():
        outbounds=engine.section('outbounds');routing=engine.section('routing');observatory=engine.section('observatory') or {}
        tags=[str(o.get('tag','')) for o in outbounds];rules=routing.get('rules',[]) if isinstance(routing,dict) else []
        balancers=routing.get('balancers',[]) if isinstance(routing,dict) else []
        obs_selectors=observatory.get('subjectSelector',[]) if isinstance(observatory,dict) else []
        rows=[]
        for index,out in enumerate(outbounds):
            tag=str(out.get('tag',''));stream=out.get('streamSettings',{}) if isinstance(out.get('streamSettings'),dict) else {}
            sock=stream.get('sockopt',{}) if isinstance(stream.get('sockopt'),dict) else {}
            rule_refs=[i+1 for i,r in enumerate(rules) if isinstance(r,dict) and r.get('outboundTag')==tag]
            bal_refs=[str(b.get('tag')) for b in balancers if isinstance(b,dict) and
                      any(tag.startswith(str(sel)) for sel in b.get('selector',[]) if isinstance(sel,str))]
            fallback_refs=[str(b.get('tag')) for b in balancers if isinstance(b,dict) and b.get('fallbackTag')==tag]
            dialed_by=[]
            for other in outbounds:
                st=other.get('streamSettings',{}) if isinstance(other.get('streamSettings'),dict) else {}
                so=st.get('sockopt',{}) if isinstance(st.get('sockopt'),dict) else {}
                if so.get('dialerProxy')==tag:dialed_by.append(str(other.get('tag','')))
            rows.append({'tag':tag,'protocol':out.get('protocol',''),'default':index==0,
                         'dials_via':str(sock.get('dialerProxy') or ''),'dialed_by':dialed_by,
                         'rule_refs':rule_refs,'balancer_refs':bal_refs,'fallback_refs':fallback_refs,
                         'observed':any(tag.startswith(str(sel)) for sel in obs_selectors if isinstance(sel,str))})
        bal_rows=[];warnings=[]
        for b in balancers:
            if not isinstance(b,dict):continue
            selectors=[str(x) for x in b.get('selector',[]) if isinstance(x,str)]
            candidates=[tag for tag in tags if any(tag.startswith(sel) for sel in selectors)]
            observed=[tag for tag in candidates if any(tag.startswith(str(sel)) for sel in obs_selectors if isinstance(sel,str))]
            strategy=(b.get('strategy') or {}).get('type','random') if isinstance(b.get('strategy',{}),dict) else 'random'
            bal_rows.append({'tag':b.get('tag',''),'strategy':strategy,'selectors':selectors,'candidates':candidates,
                             'observed_candidates':observed,'fallback_tag':b.get('fallbackTag',''),
                             'rule_refs':[i+1 for i,r in enumerate(rules) if isinstance(r,dict) and r.get('balancerTag')==b.get('tag')]})
            if not candidates:warnings.append('Balancer '+str(b.get('tag',''))+' has no outbound candidate')
            if strategy=='leastPing' and set(candidates)-set(observed):
                warnings.append('leastPing balancer '+str(b.get('tag',''))+' has candidates outside Observatory selectors')
        return {'outbounds':rows,'balancers':bal_rows,'routing':{'domain_strategy':routing.get('domainStrategy','AsIs'),
                'rule_count':len(rules),'default_outbound':tags[0] if tags else ''},
                'observatory':{'enabled':bool(observatory),'selectors':obs_selectors,
                               'probe_url':observatory.get('probeURL',observatory.get('probeUrl','')) if isinstance(observatory,dict) else '',
                               'probe_interval':observatory.get('probeInterval','') if isinstance(observatory,dict) else ''},
                'servers':_runtime_targets(),'warnings':warnings,'preview_mode':'saved_config_static',
                'live_route_api':False,'live_outbound_probe':True}

    def _port_match(expr,value:int):
        if expr in (None,''):return True
        if not value:return False
        parts=[str(expr)] if type(expr)is int else str(expr).split(',')
        for raw in parts:
            part=raw.strip()
            if '-' in part:
                try:lo,hi=map(int,part.split('-',1))
                except ValueError:continue
                if lo<=value<=hi:return True
            else:
                try:
                    if int(part)==value:return True
                except ValueError:continue
        return False

    def _domain_match(patterns,domain:str):
        if not patterns:return True
        if not domain:return False
        domain_l=domain.lower().rstrip('.');unknown=False
        for raw in patterns:
            p=str(raw)
            if p.startswith(('geosite:','ext:')):unknown=True;continue
            if p.startswith('full:'):
                if domain_l==p[5:].lower().rstrip('.'):return True
            elif p.startswith('domain:'):
                base=p[7:].lower().rstrip('.')
                if domain_l==base or domain_l.endswith('.'+base):return True
            elif p.startswith('regexp:'):
                try:
                    if re.search(p[7:],domain):return True
                except re.error:unknown=True
            elif p.startswith('keyword:'):
                if p[8:].lower() in domain_l:return True
            elif p.startswith('dotless:'):
                if '.' not in domain and p[8:] in domain:return True
            elif p.lower() in domain_l:return True
        return None if unknown else False

    def _ip_item(raw:str,address:ipaddress._BaseAddress):
        inverse=raw.startswith('!');token=raw[1:] if inverse else raw
        if token.startswith(('geoip:','ext:')):return None
        try:matched=address in ipaddress.ip_network(token,strict=False)
        except ValueError:return None
        return (not matched) if inverse else matched

    def _ip_match(patterns,raw_ip:str,*,dns_indeterminate:bool=False):
        if not patterns:return True
        if not raw_ip:return None if dns_indeterminate else False
        try:address=ipaddress.ip_address(raw_ip)
        except ValueError:return False
        positives=[];inverses=[];unknown=False
        for raw in patterns:
            raw=str(raw);v=_ip_item(raw,address)
            if v is None:unknown=True;continue
            (inverses if raw.startswith('!') else positives).append(v)
        if any(positives):return True
        inverse_ok=bool(inverses) and all(inverses)
        if inverse_ok:return True
        if unknown:return None
        return False

    def _string_list_match(values,current:str,*,regex_allowed:bool=False,process:bool=False):
        if not values:return True
        if not current:return False
        unknown=False
        for raw in values:
            token=str(raw)
            if process and token.startswith(('self/','xray/')):unknown=True;continue
            if regex_allowed and token.startswith('regexp:'):
                try:
                    if re.search(token[7:],current):return True
                except re.error:unknown=True
            elif token==current:return True
        return None if unknown else False

    def _rule_eval(rule:dict,body:TrafficRoutePreview,domain_strategy:str):
        checks=[]
        if rule.get('domain'):checks.append(_domain_match(rule['domain'],body.domain))
        if rule.get('ip'):checks.append(_ip_match(rule['ip'],body.ip,dns_indeterminate=bool(body.domain and domain_strategy!='AsIs')))
        if rule.get('port') not in (None,''):checks.append(_port_match(rule.get('port'),body.port))
        if rule.get('sourcePort') not in (None,''):checks.append(_port_match(rule.get('sourcePort'),body.source_port))
        if rule.get('localPort') not in (None,''):checks.append(_port_match(rule.get('localPort'),body.local_port))
        if rule.get('vlessRoute') not in (None,''):checks.append(_port_match(rule.get('vlessRoute'),body.vless_route))
        source=rule.get('sourceIP',rule.get('source',[]));local_ip=rule.get('localIP',[])
        if source:checks.append(_ip_match(source,body.source_ip))
        if local_ip:checks.append(_ip_match(local_ip,body.local_ip))
        if rule.get('network'):checks.append(body.network in str(rule['network']).split(','))
        if rule.get('protocol'):
            vals=rule['protocol'] if isinstance(rule['protocol'],list) else [x.strip() for x in str(rule['protocol']).split(',') if x.strip()]
            checks.append(_string_list_match(vals,body.protocol))
        if rule.get('inboundTag'):checks.append(_string_list_match(rule['inboundTag'],body.inbound_tag))
        if rule.get('user'):checks.append(_string_list_match(rule['user'],body.user,regex_allowed=True))
        if rule.get('process'):checks.append(_string_list_match(rule['process'],body.process,process=True))
        if rule.get('attrs'):
            if not body.attrs:checks.append(False)
            else:
                attr_ok=True;attr_unknown=False
                lowered={str(k).lower():str(v) for k,v in body.attrs.items()}
                for key,pattern in rule['attrs'].items():
                    value=lowered.get(str(key).lower())
                    if value is None:attr_ok=False;break
                    try:
                        if not re.search(str(pattern),value):attr_ok=False;break
                    except re.error:attr_unknown=True
                checks.append(None if attr_unknown and attr_ok else attr_ok)
        if any(x is False for x in checks):return False
        if any(x is None for x in checks):return None
        return True

    def _outbound_chain(tag:str,outbounds:list[dict]):
        by={str(o.get('tag','')):o for o in outbounds};chain=[];seen=set();current=tag
        while current and current in by and current not in seen:
            seen.add(current);chain.append(current)
            st=by[current].get('streamSettings',{}) if isinstance(by[current].get('streamSettings'),dict) else {}
            so=st.get('sockopt',{}) if isinstance(st.get('sockopt'),dict) else {}
            current=str(so.get('dialerProxy') or '')
        return chain

    def traffic_preview(body:TrafficRoutePreview):
        if body.ip:
            try:ipaddress.ip_address(body.ip)
            except ValueError:raise HTTPException(400,'Invalid target IP')
        for raw,label in ((body.source_ip,'source IP'),(body.local_ip,'local IP')):
            if raw:
                try:ipaddress.ip_address(raw)
                except ValueError:raise HTTPException(400,'Invalid '+label)
        outbounds=engine.section('outbounds');routing=engine.section('routing');rules=routing.get('rules',[])
        balancers={str(b.get('tag','')):b for b in routing.get('balancers',[]) if isinstance(b,dict)}
        domain_strategy=str(routing.get('domainStrategy','AsIs'));trace=[]
        for index,rule in enumerate(rules):
            result=_rule_eval(rule,body,domain_strategy)
            trace.append({'index':index+1,'rule_tag':rule.get('ruleTag',''),'result':'match' if result is True else 'indeterminate' if result is None else 'no_match',
                          'target':rule.get('outboundTag') or rule.get('balancerTag') or ''})
            if result is None:
                return {'result':'indeterminate','rule_index':index+1,'rule_tag':rule.get('ruleTag',''),
                        'reason':'This earlier rule needs DNS/geodata/process information that DARK cannot safely infer offline.',
                        'trace':trace,'preview_mode':'saved_config_static','live_core_verified':False}
            if result is not True:continue
            if rule.get('outboundTag'):
                tag=str(rule['outboundTag'])
                return {'result':'matched','rule_index':index+1,'rule_tag':rule.get('ruleTag',''),
                        'target_type':'outbound','target':tag,'selected_outbound':tag,'chain':_outbound_chain(tag,outbounds),
                        'trace':trace,'preview_mode':'saved_config_static','live_core_verified':False}
            tag=str(rule.get('balancerTag',''));b=balancers.get(tag,{})
            selectors=[str(x) for x in b.get('selector',[]) if isinstance(x,str)]
            candidates=[str(o.get('tag','')) for o in outbounds if any(str(o.get('tag','')).startswith(s) for s in selectors)]
            selected=candidates[0] if len(candidates)==1 else ''
            return {'result':'matched','rule_index':index+1,'rule_tag':rule.get('ruleTag',''),
                    'target_type':'balancer','target':tag,'balancer_strategy':(b.get('strategy') or {}).get('type','random'),
                    'candidates':candidates,'selected_outbound':selected,'chain':_outbound_chain(selected,outbounds) if selected else [],
                    'reason':'' if selected else 'Balancer strategy/live Observatory decides the final outbound at runtime.',
                    'trace':trace,'preview_mode':'saved_config_static','live_core_verified':False}
        default=str(outbounds[0].get('tag','')) if outbounds else ''
        return {'result':'default','rule_index':0,'target_type':'outbound','target':default,'selected_outbound':default,
                'chain':_outbound_chain(default,outbounds),'trace':trace,'preview_mode':'saved_config_static','live_core_verified':False}

    @app.get('/api/traffic-engine')
    def traffic_engine(p:Principal=Depends(owner)):return traffic_engine_doc()

    @app.post('/api/traffic-engine/outbound/probe')
    def traffic_engine_outbound_probe(body:TrafficOutboundProbe,p:Principal=Depends(owner)):
        target=_runtime_target(body.server,require_online=True)
        try:
            if target['kind']=='hub':
                outbounds=engine.runtime_outbounds(body.server)
                if not any(isinstance(x,dict) and str(x.get('tag') or '')==body.tag for x in outbounds):
                    raise HTTPException(404,'Outbound not found on selected runtime')
                row=probe_outbounds(engine._binary(),config.xray_assets,outbounds,tags=[body.tag],
                                    attempts=int(body.attempts),timeout=5.0,trace=True)[0]
            else:
                row=nodes.outbound_probe(target['nodeId'],body.tag,attempts=int(body.attempts),timeout_seconds=5)['probe']
        except (OutboundProbeError,PolicyError) as ex:
            raise HTTPException(409,'Outbound probe failed: '+str(ex))
        egress=row.get('egress') if isinstance(row.get('egress'),dict) else {}
        return {'server':target,'tag':body.tag,'success':bool(row.get('success')),'testable':bool(row.get('testable',True)),
                'delayMs':row.get('delayMs'),'lossPercent':row.get('lossPercent'),'jitterMs':row.get('jitterMs'),
                'egress':egress,'error':row.get('error',''),'productionTrafficMutation':False}

    @app.post('/api/traffic-engine/preview')
    def traffic_engine_preview(body:TrafficRoutePreview,p:Principal=Depends(owner)):return traffic_preview(body)

    @app.get('/api/security-center')
    def security_center(p:Principal=Depends(current)):return security_center_payload(p)

    @app.get('/api/ip-status')
    def ip_status(p:Principal=Depends(owner)):
        return {'source':'DARK Native IP Guard','engine':engine.ip_status(),
            'limit_unit':'recent distinct verified source IPs',
            'global_multi_node_limit':False,
            'global_account_policy':True,
            'global_policy_note':'Central can block a client from verified Local + remote Node observations; nftables bans remain local to the host where they are applied.',
            'packet_test_performed_here':False}
    def sync_status_doc(p:Principal):
        now=time.time()
        with store.lock:
            raw=[dict(r) for r in store.db.execute('''SELECT m.email,m.op,m.op_id,m.state,m.error,m.updated_at,m.retry_at,
                    m.attempts,m.expected_enable,m.external_disabled,c.owner
                    FROM managed_clients m LEFT JOIN clients c ON c.id=m.email
                    WHERE m.state!='deleted' ORDER BY m.updated_at DESC''')]
        visible=[r for r in raw if p.actor.role=='owner' or (r.get('owner') and p.actor.can('clients','read',r['owner']))]

        def classify(r):
            state=str(r.get('state') or '');op=str(r.get('op') or 'none');external=bool(r.get('external_disabled'))
            retry_in=max(0,int(float(r.get('retry_at') or 0)-now))
            code='clean';severity='ok';automatic=False;action='none'
            if state=='uncertain':
                code='uncertain_reset' if op=='reset' else 'uncertain_operation';severity='error'
                action='resolve_reset' if p.actor.role=='owner' and op=='reset' else 'inspect'
            elif state=='conflict':
                code='identity_conflict';severity='error';action='inspect'
            elif state=='missing':
                code='runtime_missing';severity='error';action='restore_missing' if p.actor.role=='owner' else 'inspect'
            elif state=='reset_inflight':
                code='reset_inflight';severity='warn';automatic=False;action='none'
            elif state=='error':
                code='retry_wait' if retry_in else 'operation_error';severity='warn';automatic=op!='none'
                owner_id=r.get('owner')
                can_retry=bool(owner_id and p.actor.can('clients','delete' if op=='delete' else 'reset' if op=='reset' else 'edit',owner_id))
                action='retry_now' if op!='none' and can_retry else 'inspect'
            elif state=='pending':
                code='queued';severity='info';automatic=op!='none';action='none'
            elif external:
                code='external_disabled';severity='warn'
                owner_id=r.get('owner');action='restore_control' if owner_id and p.actor.can('clients','edit',owner_id) else 'inspect'
            elif state!='applied':
                code='state_'+state;severity='warn';action='inspect'
            out=dict(r);out['reason_code']=code;out['severity']=severity;out['automatic_retry']=automatic
            out['retry_in_seconds']=retry_in;out['next_action']=action
            out['drift']=code in {'identity_conflict','runtime_missing','external_disabled'}
            out['operation_pending']=op!='none'
            out.pop('op_id',None)
            return out

        items=[classify(r) for r in visible]
        rank={'error':0,'warn':1,'info':2,'ok':3}
        items.sort(key=lambda x:(rank.get(x['severity'],9),-float(x.get('updated_at') or 0),x['email']))
        summary={'total':len(items)}
        for key in ('clean','queued','retry_wait','operation_error','uncertain_reset','uncertain_operation',
                    'identity_conflict','runtime_missing','external_disabled','reset_inflight'):
            summary[key]=sum(1 for x in items if x['reason_code']==key)
        summary['action_required']=sum(1 for x in items if x['severity']=='error')
        summary['warnings']=sum(1 for x in items if x['severity']=='warn')
        summary['automatic_retry']=sum(1 for x in items if x['automatic_retry'])

        runtime=engine.runtime_state()
        if runtime.get('last_error'):generation='runtime_error'
        elif runtime.get('running') and not runtime.get('dirty'):generation='running_clean'
        elif runtime.get('running') and runtime.get('dirty'):generation='running_dirty'
        elif not runtime.get('running') and runtime.get('desired_running'):generation='stopped_unexpected'
        elif runtime.get('dirty'):generation='stopped_staged'
        else:generation='stopped_clean'
        runtime['generation_state']=generation
        runtime['next_action']='restart_apply' if generation=='running_dirty' else 'start_apply' if generation in {'stopped_staged','stopped_unexpected','runtime_error'} else 'none'
        if p.actor.role!='owner':
            runtime={k:runtime.get(k) for k in ('state','running','dirty','desired_running')}

        poll_age=None if not manager.last_poll else max(0,int(now-manager.last_poll))
        poll_stale=manager.last_poll==0 or poll_age>max(15,int(config.poll_seconds)*3)
        manager_state='error' if manager.last_error else 'stale' if poll_stale else 'healthy'
        node_summary=None
        if p.actor.role=='owner':
            node_rows=nodes.list();assignments=[a for n in node_rows for a in n.get('assignments',[])]
            node_summary={'total':len(node_rows),'online':sum(1 for n in node_rows if n.get('online')),
                'offline':sum(1 for n in node_rows if n.get('enabled') and not n.get('online')),
                'assignment_errors':sum(1 for a in assignments if a.get('deployment_state')=='sync_error'),
                'pending_deploys':sum(1 for a in assignments if a.get('deployment_state')=='pending'),
                'deployed':sum(1 for a in assignments if a.get('deployment_state')=='deployed'),
                'subscription_ready':sum(1 for a in assignments if a.get('failover_ready'))}

        limited=items[:500]
        return {'last_poll':manager.last_poll,'poll_age_seconds':poll_age,'poll_stale':poll_stale,
                'manager_state':manager_state,
                'error':manager.last_error if p.actor.role=='owner' else ('CoreEngine synchronization unavailable' if manager.last_error else ''),
                'writes_enabled':config.writes_enabled,'engine_version':engine.version,'runtime':runtime,
                'summary':summary,'nodes':node_summary,'items':limited,'items_total':len(items),'truncated':len(items)>len(limited)}

    @app.get('/api/sync')
    def sync(p:Principal=Depends(current)):return sync_status_doc(p)

    @app.post('/api/sync')
    def force_sync(p:Principal=Depends(owner)):
        manager.tick(suppress=False);return sync_status_doc(p)
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
    def ledger(kind:Literal['traffic','credits'],p:Principal=Depends(current)):
        resource='finance' if kind=='credits' else 'owners';p.actor.require(resource,'read',p.actor.id)
        all_=p.actor.role=='owner' or p.actor.permissions.get(resource+'.read')=='all'
        table='resource_credit_ledger' if kind=='credits' else 'traffic_ledger'
        with store.lock:return [dict(r) for r in store.db.execute('SELECT * FROM '+table+('' if all_ else ' WHERE owner=?')+' ORDER BY rowid DESC LIMIT 250',() if all_ else (p.actor.id,))]

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
                'database_download':(config.panel_path if config.panel_path!='/' else '')+'/api/backup',
                'full_backup_download':(config.panel_path if config.panel_path!='/' else '')+'/api/backup/full',
                'full_backup_command':'sudo darkxray backup --output /root/dark-full.darkbackup',
                'central_node_state_included':True,'restore_isolated':True}

    @app.post('/api/backup/full')
    def backup_full(body:FullBackupBody,p:Principal=Depends(owner)):
        from backup import create_backup
        config_path=Path(str(getattr(config,'_path','')))
        if not str(getattr(config,'_path','')) or config_path.is_symlink() or not config_path.is_file():
            raise HTTPException(409,'Full Web backup requires a file-backed DARK configuration')
        with tempfile.TemporaryDirectory(prefix='dark-web-backup.') as td:
            path=Path(td)/'dark-xray-full.darkbackup'
            manifest=create_backup(Path(store.path).parent,config_path,path,body.passphrase)
            raw=path.read_bytes()
        manager.audit(p.actor,p.actor.id,'backup.full','dark','Encrypted Hub backup created; passphrase was not persisted')
        stamp=time.strftime('%Y%m%d-%H%M%S');filename=f'DARK-XRAY-full-{stamp}.darkbackup'
        runtime=getattr(app.state,'telegram_runtime',None)
        if runtime:
            try:
                sha=hashlib.sha256(raw).hexdigest()
                caption=f"DARK XRAY encrypted backup\nSchema: {manifest.get('schema',0)}\nBytes: {len(raw)}\nSHA256: {sha}"
                if runtime.send_backup(p.actor.id,filename,raw,caption):
                    manager.audit(p.actor,p.actor.id,'backup.telegram_sent','dark',filename+'; sha256='+sha)
            except Exception as ex:
                manager.audit(p.actor,p.actor.id,'backup.telegram_error','dark',type(ex).__name__+': '+str(ex)[:300])
        return Response(raw,media_type='application/octet-stream',
                        headers={'Content-Disposition':f'attachment; filename="{filename}"',
                                 'X-DARK-Backup-Schema':str(manifest.get('schema',0))})

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
        sub=engine.section('subscription')
        if not sub.get('enabled',True):raise HTTPException(404)
        with store.lock:
            row=store.db.execute("SELECT * FROM managed_clients WHERE public_token=? AND state!='deleted'",(public_token,)).fetchone()
        if not row:raise HTTPException(404)
        if store.client_reasons(row['email']) or row['external_disabled']:raise HTTPException(403,'Subscription suspended')
        if row['state']!='applied':raise HTTPException(503,'Customer configuration has not been saved to the runtime')
        fmt=request.query_params.get('format')
        ua=request.headers.get('user-agent','').lower()
        accept=request.headers.get('accept','').lower()
        native_clients=('clash','mihomo','sing-box','singbox','xray','v2ray','v2box','happ','hiddify','nekobox','shadowrocket','streisand')
        wants_portal=(request.query_params.get('portal')=='1' or
                      (not fmt and 'text/html' in accept and not any(x in ua for x in native_clients)))
        if wants_portal:
            with store.lock:
                core_row=store.db.execute('SELECT body,up,down FROM core_clients WHERE email=?',(row['email'],)).fetchone()
            client=json.loads(core_row['body']) if core_row else {}
            used=(int(core_row['up'])+int(core_row['down'])) if core_row else (int(row['last_up'])+int(row['last_down']))
            total=max(0,int(client.get('totalGB',0) or 0))
            expiry=max(0,int(client.get('expiryTime',0) or 0)//1000)
            activation_pending=False;duration_days=0
            if not expiry:
                with store.lock:
                    order=store.db.execute("""SELECT o.status,o.duration_days,o.activation_mode
                      FROM commerce_orders o JOIN clients c ON c.id=o.client_id AND c.owner=o.owner
                      WHERE o.client_id=? AND o.status IN ('provisioned','provisioned_waiting_activation')
                      ORDER BY o.updated_at DESC,o.created_at DESC LIMIT 1""",(row['email'],)).fetchone()
                if order and order['status']=='provisioned_waiting_activation' and order['activation_mode']=='first_connection':
                    activation_pending=True;duration_days=max(1,int(order['duration_days']))
            public_url=config.public_origin.rstrip('/')+str(sub.get('path','/sub'))+'/'+public_token
            portal_data={'title':sub.get('profile_title','DARK XRAY'),'client':row['email'],'url':public_url,
                         'used':used,'total':total,'expiry':expiry,
                         'activation_pending':activation_pending,'duration_days':duration_days,
                         'update_hours':int(sub.get('profile_update_interval_hours',6)),
                         'announce':sub.get('announce',''),'support_url':sub.get('support_url','')}
            safe=json.dumps(portal_data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
            page=(ROOT/'web/sub-portal.html').read_text(encoding='utf-8').replace('__SUB_DATA__',safe)
            return Response(page,media_type='text/html; charset=utf-8',
                            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                                     'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
                                     'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})
        engine.check_device(row['email'],request.headers.get('x-hwid',''),request.headers.get('x-device-os',''),request.headers.get('x-device-model',''))
        if not fmt:
            if sub.get('auto_detect',True) and any(x in ua for x in ('clash','mihomo')):fmt='clash'
            else:fmt=sub.get('default_format','base64')
        extra=failover_links(row['email'])
        body,headers=engine.subscription(row['email'],fmt,extra_links=extra,runtime_ready=runtime_ready_map(row['email']))
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
            volume_credit_bytes=stat['volume_credit_bytes'] if stat else None,
            unlimited_credit=stat['unlimited_credit'] if stat else None,
            max_clients=stat['max_clients'] if stat else 0)
        manager.audit(p.actor,p.actor.id,'inbound.create',str(result['id']))
        return result

    @app.put('/api/inbounds/{inbound_id}')
    def put_inbound(inbound_id:int,body:dict,p:Principal=Depends(owner)):
        writable();node_port_swaps.check_inbound(body,inbound_id);result=engine.save_inbound(body,inbound_id);manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'inbound.update',str(inbound_id));return result

    @app.delete('/api/inbounds/{inbound_id}')
    def delete_inbound(inbound_id:int,p:Principal=Depends(owner)):
        writable();node_port_swaps.check_inbound({},inbound_id,deleting=True);result=engine.delete_inbound(inbound_id)
        with store.transaction() as db:
            for row in db.execute('SELECT id,allowed FROM owner_profiles').fetchall():
                db.execute('UPDATE owner_profiles SET allowed=? WHERE id=?',(json.dumps([i for i in json.loads(row['allowed']) if i!=inbound_id]),row['id']))
            db.execute('DELETE FROM traffic_matrix WHERE inbound_id=?',(inbound_id,))
        manager.tick(suppress=True);manager.audit(p.actor,p.actor.id,'inbound.delete',str(inbound_id));return result

    @app.get('/api/subscription/status')
    def subscription_status(p:Principal=Depends(owner)):
        sub=engine.section('subscription')
        with store.lock:
            managed=int(store.db.execute("SELECT COUNT(*) FROM managed_clients WHERE state!='deleted'").fetchone()[0])
            hwid_clients=int(store.db.execute("SELECT COUNT(*) FROM managed_clients m JOIN core_clients c ON c.email=m.email WHERE m.state!='deleted' AND CAST(json_extract(c.body,'$.limitHwid') AS INTEGER)>0").fetchone()[0])
            devices=int(store.db.execute("SELECT COUNT(*) FROM core_devices").fetchone()[0])
            at_limit=int(store.db.execute("""SELECT COUNT(*) FROM core_clients c
                WHERE CAST(json_extract(c.body,'$.limitHwid') AS INTEGER)>0
                  AND (SELECT COUNT(*) FROM core_devices d WHERE d.email=c.email)>=CAST(json_extract(c.body,'$.limitHwid') AS INTEGER)""").fetchone()[0])
        tmpl=sub.get('remark_template','{remark} | {email}')
        preview=tmpl.replace('{remark}','TURKEY FAST').replace('{email}','customer@example').replace('{protocol}','VLESS')
        base=config.public_origin.rstrip('/')+str(sub.get('path','/sub'))+'/<token>'
        return {'enabled':bool(sub.get('enabled',True)),'base_url':base,
                'default_format':sub.get('default_format','base64'),'auto_detect':bool(sub.get('auto_detect',True)),
                'supported_formats':['base64','raw','clash','json'],
                'profile_update_interval_hours':int(sub.get('profile_update_interval_hours',6)),
                'profile_title':sub.get('profile_title','DARK XRAY'),
                'profile_url':sub.get('profile_url',''),'support_url':sub.get('support_url',''),
                'remark_preview':preview,'traffic_scope':'local_plus_remote_nodes',
                'device_policy':{'managed_clients':managed,'clients_with_hwid_limit':hwid_clients,
                                 'registered_devices':devices,'clients_at_device_limit':at_limit,
                                 'required_header':'x-hwid','optional_headers':['x-device-os','x-device-model']},
                'auto_detect_rules':[{'contains':'clash','format':'clash'},{'contains':'mihomo','format':'clash'}]}

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

    @app.get('/api/operations/overview')
    def operations_overview(p:Principal=Depends(owner)):
        from operations_overview import snapshot
        return snapshot(store,engine,nodes,getattr(app.state,'telegram_runtime',None))

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

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
from smart_routing import (AI_DOMAIN_MATCHERS,DEFAULT_SAFETY_THRESHOLDS,STAGE7_SAFETY_TTL_SECONDS,SmartRoutingError,
                           build_stage7_candidate_config,build_stage7_patch,build_stage7_plan,
                           evaluate_stage7_node_readiness,evaluate_warp_safety,filter_stage7_routing_for_node,
                           rank_warp_paths,stage7_state_hash)
from smart_warp_probe import SmartWarpProbeError,scan_warp_outbounds
from outbound_probe import OutboundProbeError,probe_outbounds
from warp_cloudflare import (WarpRegistrationError,register_cloudflare_warp,
                             validate_warp_endpoint,warp_endpoint_candidates)
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
class SmartRoutingPreview(Model):
    warpAi:bool=True
    adblock:bool=True
    warpOutboundTags:list[str]=Field(default_factory=list,max_length=32)
    warpNodeIds:list[str]|None=Field(default=None,max_length=64)
    adblockNodeIds:list[str]|None=Field(default=None,max_length=64)
class SmartRoutingReview(SmartRoutingPreview):
    baselineHash:str=Field(min_length=64,max_length=64,pattern=r'^[0-9a-f]{64}$')
    candidateHash:str=Field(min_length=64,max_length=64,pattern=r'^[0-9a-f]{64}$')
    confirmation:str=Field(min_length=1,max_length=64)
class SmartRoutingRevisionAction(Model):
    revisionId:str=Field(min_length=16,max_length=64,pattern=r'^[0-9a-f]+$')
    confirmation:str=Field(min_length=1,max_length=64)
class SmartRoutingSafetyCheck(Model):
    revisionId:str=Field(min_length=16,max_length=64,pattern=r'^[0-9a-f]+$')
    attempts:StrictInt=Field(default=3,ge=1,le=3)
    timeoutSeconds:StrictInt=Field(default=5,ge=1,le=10)
    maxLossPercent:StrictInt=Field(default=int(DEFAULT_SAFETY_THRESHOLDS['maxLossPercent']),ge=0,le=50)
    maxLatencyMs:StrictInt=Field(default=int(DEFAULT_SAFETY_THRESHOLDS['maxLatencyMs']),ge=50,le=3000)
    maxJitterMs:StrictInt=Field(default=int(DEFAULT_SAFETY_THRESHOLDS['maxJitterMs']),ge=0,le=1500)
class SmartRoutingRolloutStart(Model):
    revisionId:str=Field(min_length=16,max_length=64,pattern=r'^[0-9a-f]+$')
    confirmation:str=Field(min_length=1,max_length=64)
    observationSeconds:StrictInt=Field(default=5,ge=1,le=30)
class SmartRoutingRolloutAction(Model):
    confirmation:str=Field(min_length=1,max_length=64)
class SmartWarpRank(Model):
    observations:list[dict[str,Any]]=Field(default_factory=list,max_length=256)
class SmartWarpScan(Model):
    outboundTags:list[str]=Field(min_length=1,max_length=8)
    attempts:StrictInt=Field(default=3,ge=1,le=3)
    timeoutSeconds:StrictInt=Field(default=5,ge=1,le=10)
class WarpCreate(Model):
    tag:str=Field(default='warp',min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    server:str=Field(default='hub',min_length=1,max_length=160)
class WarpMode(Model):
    tag:str=Field(default='warp',min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    mode:Literal['off','ai','all']='ai'
    adblock:bool=False
    server:str=Field(default='hub',min_length=1,max_length=160)
    inboundIds:list[StrictInt]=Field(default_factory=list,max_length=256)
class AdblockMode(Model):
    enabled:bool=True
    server:str=Field(default='hub',min_length=1,max_length=160)
    inboundIds:list[StrictInt]=Field(default_factory=list,max_length=256)
class WarpProbeRequest(Model):
    tag:str=Field(default='warp',min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    server:str=Field(default='hub',min_length=1,max_length=160)
class WarpEndpointSelect(Model):
    tag:str=Field(default='warp',min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    endpoint:str=Field(min_length=3,max_length=160)
    server:str=Field(default='hub',min_length=1,max_length=160)
class OutboundProbeRequest(Model):
    tags:list[str]=Field(default_factory=list,max_length=32)
    attempts:StrictInt=Field(default=1,ge=1,le=3)
    timeoutSeconds:StrictInt=Field(default=5,ge=1,le=10)
    server:str=Field(default='hub',min_length=1,max_length=160)
class RoutingScopeBody(Model):
    ruleTag:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    scope:str=Field(default='all',min_length=1,max_length=160)

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
    location:str=Field(default='',max_length=80)
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
    location:str=Field(default='',max_length=80)
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
    stage7_rollout_lock=threading.RLock();stage7_rollout_threads={}
    manager.remote_reset=lambda email,reset_id:nodes.reset_client_traffic(email,reset_id)
    def _warp_profile(scope:str)->dict|None:
        value=str(scope or 'hub').strip()
        with store.lock:
            row=store.db.execute('SELECT outbound_json FROM warp_profiles WHERE scope=?',(value,)).fetchone()
        if not row:return None
        try:outbound=json.loads(row['outbound_json'])
        except Exception:return None
        return outbound if isinstance(outbound,dict) else None

    def _warp_profile_save(scope:str,outbound:dict,device_id:str=''):
        value=str(scope or 'hub').strip();now=time.time()
        body=copy.deepcopy(outbound);body['tag']='warp'
        with store.transaction() as db:
            db.execute('''INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,?)
                          ON CONFLICT(scope) DO UPDATE SET outbound_json=excluded.outbound_json,
                          device_id=excluded.device_id,updated_at=excluded.updated_at''',
                       (value,json.dumps(body,separators=(',',':')),str(device_id or '')[:256],now))
        return body

    def _warp_assignment(scope:str)->dict:
        value=str(scope or 'hub').strip()
        with store.lock:row=store.db.execute('SELECT * FROM warp_assignments WHERE scope=?',(value,)).fetchone()
        if not row:return {'scope':value,'mode':'off','adblock':False,'inboundIds':[],'updatedAt':0.0}
        try:ids=[int(x) for x in json.loads(row['inbound_ids'] or '[]')]
        except Exception:ids=[]
        return {'scope':value,'mode':str(row['mode'] or 'off'),'adblock':bool(row['adblock']),
                'inboundIds':ids,'updatedAt':float(row['updated_at'] or 0)}

    def _warp_assignments()->list[dict]:
        with store.lock:rows=store.db.execute('SELECT * FROM warp_assignments ORDER BY scope').fetchall()
        out=[]
        for row in rows:
            try:ids=[int(x) for x in json.loads(row['inbound_ids'] or '[]')]
            except Exception:ids=[]
            out.append({'scope':str(row['scope']),'mode':str(row['mode'] or 'off'),
                        'adblock':bool(row['adblock']),'inboundIds':ids,'updatedAt':float(row['updated_at'] or 0)})
        return out

    def _warp_assignment_save(scope:str,mode:str,adblock:bool,inbound_ids:list[int]):
        value=str(scope or 'hub').strip();ids=list(dict.fromkeys(int(x) for x in inbound_ids))
        with store.transaction() as db:
            db.execute('''INSERT INTO warp_assignments(scope,mode,adblock,inbound_ids,updated_at) VALUES(?,?,?,?,?)
                          ON CONFLICT(scope) DO UPDATE SET mode=excluded.mode,adblock=excluded.adblock,
                          inbound_ids=excluded.inbound_ids,updated_at=excluded.updated_at''',
                       (value,str(mode),int(bool(adblock)),json.dumps(ids),time.time()))
        return _warp_assignment(value)

    def _adblock_assignment(scope:str)->dict:
        value=str(scope or 'hub').strip()
        with store.lock:row=store.db.execute('SELECT * FROM adblock_assignments WHERE scope=?',(value,)).fetchone()
        if not row:return {'scope':value,'enabled':False,'inboundIds':[],'updatedAt':0.0}
        try:ids=[int(x) for x in json.loads(row['inbound_ids'] or '[]')]
        except Exception:ids=[]
        return {'scope':value,'enabled':bool(row['enabled']),'inboundIds':ids,
                'updatedAt':float(row['updated_at'] or 0)}

    def _adblock_assignments()->list[dict]:
        with store.lock:rows=store.db.execute('SELECT * FROM adblock_assignments ORDER BY scope').fetchall()
        out=[]
        for row in rows:
            try:ids=[int(x) for x in json.loads(row['inbound_ids'] or '[]')]
            except Exception:ids=[]
            out.append({'scope':str(row['scope']),'enabled':bool(row['enabled']),
                        'inboundIds':ids,'updatedAt':float(row['updated_at'] or 0)})
        return out

    def _adblock_assignment_save(scope:str,enabled:bool,inbound_ids:list[int]):
        value=str(scope or 'hub').strip();ids=list(dict.fromkeys(int(x) for x in inbound_ids))
        with store.transaction() as db:
            db.execute('''INSERT INTO adblock_assignments(scope,enabled,inbound_ids,updated_at) VALUES(?,?,?,?)
                          ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled,
                          inbound_ids=excluded.inbound_ids,updated_at=excluded.updated_at''',
                       (value,int(bool(enabled)),json.dumps(ids),time.time()))
        return _adblock_assignment(value)

    def _warp_rule_tag(kind:str,scope:str)->str:
        digest=hashlib.sha256(str(scope).encode()).hexdigest()[:10]
        return 'dark-warp-'+kind+'-'+digest if kind in {'ai','all'} else 'dark-smart-adblock-'+digest

    def _warp_migrate_legacy():
        hub_profile=_warp_profile('hub')
        global_warp=next((x for x in engine.section('outbounds') if isinstance(x,dict) and x.get('tag')=='warp'
                          and str(x.get('protocol','')).lower()=='wireguard'),None)
        if hub_profile is None and global_warp is not None:
            _warp_profile_save('hub',global_warp,'legacy-hub')
        routing=copy.deepcopy(engine.section('routing'));rules=routing.get('rules',[]) if isinstance(routing,dict) else []
        if not isinstance(rules,list):rules=[]
        def unscoped_legacy_warp(rule):
            if not isinstance(rule,dict) or str(rule.get('ruleTag') or '')!='warp' or str(rule.get('outboundTag') or '')!='warp':
                return False
            match_keys=('domain','ip','inboundTag','port','sourcePort','localPort','network','protocol','user','process','attrs')
            return not any(rule.get(key) not in (None,'',[],{}) for key in match_keys)
        cleaned=[rule for rule in rules if not unscoped_legacy_warp(rule)]
        if len(cleaned)!=len(rules):
            routing['rules']=cleaned
            engine.save_section('routing',routing)
            with store.transaction() as db:db.execute("DELETE FROM routing_rule_scopes WHERE rule_tag='warp'")
            rules=cleaned
        with store.lock:
            count=store.db.execute('SELECT COUNT(*) FROM warp_assignments').fetchone()[0]
        if count:return
        by_tag={str(x.get('tag') or ''):int(x['id']) for x in engine.inbounds() if isinstance(x,dict) and x.get('tag')}
        merged={}
        for rule in rules if isinstance(rules,list) else []:
            if not isinstance(rule,dict):continue
            rt=str(rule.get('ruleTag') or '')
            if rt not in {'dark-warp-ai','dark-warp-all','dark-smart-warp-ai','dark-smart-adblock'}:continue
            with store.lock:sr=store.db.execute('SELECT scope FROM routing_rule_scopes WHERE rule_tag=?',(rt,)).fetchone()
            scope=str(sr['scope']) if sr else 'hub'
            row=merged.setdefault(scope,{'mode':'off','adblock':False,'inboundIds':[]})
            if rt=='dark-warp-all':row['mode']='all'
            elif rt in {'dark-warp-ai','dark-smart-warp-ai'} and row['mode']!='all':row['mode']='ai'
            elif rt=='dark-smart-adblock':row['adblock']=True
            for tag in rule.get('inboundTag',[]) if isinstance(rule.get('inboundTag'),list) else []:
                if str(tag) in by_tag and by_tag[str(tag)] not in row['inboundIds']:row['inboundIds'].append(by_tag[str(tag)])
        for scope,row in merged.items():
            _warp_assignment_save(scope,row['mode'],row['adblock'],row['inboundIds'])
            if scope.startswith('node:') and _warp_profile(scope) is None and global_warp is not None:
                _warp_profile_save(scope,global_warp,'legacy-clone')
    _warp_migrate_legacy()
    def _adblock_migrate_legacy():
        # Older releases stored Adblock inside warp_assignments. Split it once,
        # preserving the exact server + inbound scope, then clear the legacy bit.
        with store.transaction() as db:
            rows=db.execute("SELECT scope,adblock,inbound_ids FROM warp_assignments WHERE adblock!=0").fetchall()
            for row in rows:
                existing=db.execute("SELECT 1 FROM adblock_assignments WHERE scope=?",(str(row['scope']),)).fetchone()
                if not existing:
                    db.execute("INSERT INTO adblock_assignments(scope,enabled,inbound_ids,updated_at) VALUES(?,?,?,?)",
                               (str(row['scope']),1,str(row['inbound_ids'] or '[]'),time.time()))
                db.execute("UPDATE warp_assignments SET adblock=0 WHERE scope=?",(str(row['scope']),))
        # Also recover a managed legacy Adblock rule if it predates the assignment row.
        routing=engine.section('routing');rules=routing.get('rules',[]) if isinstance(routing,dict) else []
        by_tag={str(x.get('tag') or ''):int(x['id']) for x in engine.inbounds() if isinstance(x,dict) and x.get('tag')}
        for rule in rules if isinstance(rules,list) else []:
            if not isinstance(rule,dict):continue
            rt=str(rule.get('ruleTag') or '')
            if not (rt=='dark-smart-adblock' or rt.startswith('dark-smart-adblock-')):continue
            with store.lock:
                sr=store.db.execute('SELECT scope FROM routing_rule_scopes WHERE rule_tag=?',(rt,)).fetchone()
            scope=str(sr['scope']) if sr else 'hub'
            ids=[]
            for tag in rule.get('inboundTag',[]) if isinstance(rule.get('inboundTag'),list) else []:
                if str(tag) in by_tag and by_tag[str(tag)] not in ids:ids.append(by_tag[str(tag)])
            with store.lock:
                exists=store.db.execute('SELECT 1 FROM adblock_assignments WHERE scope=?',(scope,)).fetchone()
            if not exists and ids:_adblock_assignment_save(scope,True,ids)

    _adblock_migrate_legacy()

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
            with store.lock:pending_rollouts=[r[0] for r in store.db.execute(
                "SELECT id FROM smart_routing_rollouts WHERE state='running' ORDER BY created_at").fetchall()]
            for rollout_id in pending_rollouts:_stage7_start_worker(rollout_id)
        yield
        if bot_runtime:bot_runtime.close()
        nodes.close();manager.close();engine.close()
    app=FastAPI(title='DARK XRAY',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.replacements=replacements
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

    from node_recovery import install_hub_recovery
    install_hub_recovery(app,nodes,owner,writable,manager.audit)

    from telegram_commerce import install_telegram_commerce
    install_telegram_commerce(app,store,auth,current,writable,manager.audit,manager)

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
            if not node.get('enabled') or not node.get('online'):continue
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

    def stage7_node_roles()->dict:
        with store.lock:rows=[dict(r) for r in store.db.execute(
            'SELECT node_id,warp_ai,adblock FROM smart_routing_node_roles ORDER BY node_id').fetchall()]
        return {'warpNodeIds':[r['node_id'] for r in rows if r['warp_ai']],
                'adblockNodeIds':[r['node_id'] for r in rows if r['adblock']]}

    def _stage7_rollout_override(node_id:str)->dict|None:
        with store.lock:
            row=store.db.execute('''SELECT n.state node_state,r.state rollout_state,r.revision_id,v.after_routing,
                v.after_observatory,v.after_node_roles FROM smart_routing_rollout_nodes n
                JOIN smart_routing_rollouts r ON r.id=n.rollout_id
                JOIN smart_routing_revisions v ON v.id=r.revision_id
                WHERE n.node_id=? AND r.state='running' ORDER BY r.created_at DESC LIMIT 1''',(node_id,)).fetchone()
        if not row or row['node_state'] not in {'applying','verifying','healthy'}:return None
        roles=json.loads(row['after_node_roles'] or '{}')
        return {'routing':json.loads(row['after_routing']),'observatory':json.loads(row['after_observatory']),
                'warp_ai':node_id in set(roles.get('warpNodeIds',[])),
                'adblock':node_id in set(roles.get('adblockNodeIds',[]))}

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
        sections['outbounds']=[copy.deepcopy(x) for x in sections['outbounds']
                               if not (isinstance(x,dict) and str(x.get('tag') or '')=='warp')]
        node_warp=_warp_profile('node:'+node_id)
        if node_warp is not None:sections['outbounds'].append(copy.deepcopy(node_warp))
        override=_stage7_rollout_override(node_id)
        roles=stage7_node_roles();warp_nodes=set(roles['warpNodeIds']);adblock_nodes=set(roles['adblockNodeIds'])
        if override:
            sections['routing']=copy.deepcopy(override['routing'])
            sections['observatory']=copy.deepcopy(override['observatory'])
            warp_nodes={node_id} if override['warp_ai'] else set()
            adblock_nodes={node_id} if override['adblock'] else set()
        sections['routing']=engine.filter_routing_for_scope(sections['routing'],'node:'+node_id)
        sections['routing']=filter_stage7_routing_for_node(
            sections['routing'],warp_ai=node_id in warp_nodes,adblock=node_id in adblock_nodes)
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

    @app.post('/api/nodes')
    def remote_node_add(body:NodeCreate,p:Principal=Depends(owner)):
        writable()
        known={i['id'] for i in engine.inbounds()}
        if not set(body.inboundIds)<=known:raise HTTPException(400,'Unknown inbound assignment')
        result=nodes.put(body.id,body.name,body.origin,body.token,body.enabled,body.inboundIds,
                         body.dataAddress,body.priority,body.failoverEnabled,body.location)
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
                'dataAddress':current_node['data_address'],'location':current_node.get('location',''),'enabled':current_node['enabled'],
                'priority':current_node['priority'],'failoverEnabled':current_node['failover_enabled'],
                'inboundIds':sorted(current_node['inboundIds'])}
            supplied=body.model_dump(include=set(expected));supplied['inboundIds']=sorted(set(body.inboundIds))
            if supplied!=expected:raise HTTPException(409,'Save settings separately from token rotation')
            result=nodes.rotate_token(node_id,body.token)
            manager.audit(p.actor,p.actor.id,'node.credential.rotate',node_id,'phase='+result['phase'])
            return result
        if not body.keep_token:raise HTTPException(400,'Provide a replacement token or keep_token=true')
        token=nodes.get(node_id,secret=True)['token']
        result=nodes.put(node_id,body.name,body.origin,token,body.enabled,body.inboundIds,
                         body.dataAddress,body.priority,body.failoverEnabled,body.location)
        for target in {node_id}:ensure_node_desired_state(target)
        apply_global_security()
        manager.audit(p.actor,p.actor.id,'node.update',node_id);return result
    @app.delete('/api/nodes/{node_id}')
    def remote_node_delete(node_id:str,p:Principal=Depends(owner)):
        writable()
        refs=[h for h in engine.section('hosts') if h.get('runtime')=='node:'+node_id]
        if refs:raise HTTPException(409,'Move or delete Public Endpoints that use this Node before deleting it')
        result=nodes.delete(node_id);apply_global_security()
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
        return {'inboundId':inbound_id,'local':meta.get('deployLocal',True) is not False,
                'nodeIds':sorted(selected),
                'targets':[{'id':n['id'],'name':n['name'],'online':bool(n.get('online')),'enabled':bool(n.get('enabled')),
                            'selected':n['id'] in selected,'pending':bool(n.get('desired_state',{}).get('pending'))}
                           for n in fleet]}

    @app.put('/api/inbounds/{inbound_id}/deployments')
    def inbound_deployments_put(inbound_id:int,body:InboundDeployments,p:Principal=Depends(owner)):
        writable();inbound=engine.inbound(inbound_id);fleet=nodes.list();known={str(n['id']) for n in fleet}
        requested=list(dict.fromkeys(str(x) for x in body.nodeIds))
        if any(not NAME_RE.fullmatch(x) for x in requested) or not set(requested)<=known:
            raise HTTPException(400,'Unknown node deployment target')
        meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
        inbound=copy.deepcopy(inbound);inbound.pop('id',None);inbound.pop('applied',None)
        meta=copy.deepcopy(meta);meta['deployLocal']=bool(body.local);meta['deploymentTargets']=['local']*int(bool(body.local))+requested
        inbound['panelMeta']=meta;engine.save_inbound(inbound,inbound_id)
        before=set(nodes.inbound_assignments(inbound_id));after=set(requested)
        for node_id in sorted(before|after):
            nodes.set_inbound_assignment(node_id,inbound_id,node_id in after)
            # Persist the new Hub desired revision immediately even when a node
            # is offline. The monitor will apply it when connectivity returns.
            ensure_node_desired_state(node_id)
        manager.audit(p.actor,p.actor.id,'inbound.deployments',str(inbound_id),
                      'local='+str(bool(body.local))+'; nodes='+','.join(sorted(after)))
        return inbound_deployments(inbound_id,p)

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
        fresh_nodes=sum(1 for n in node_rows if n.get('security',{}).get('last_sync') and
                        now-float(n['security']['last_sync'])<180 and not n['security'].get('last_error'))
        verified_nodes=sum(1 for n in node_rows if n.get('security',{}).get('source_verified') and
                           n.get('security',{}).get('last_sync') and now-float(n['security']['last_sync'])<180 and
                           not n['security'].get('last_error'))
        node_guard_enforce=0;node_guard_ready=0;node_policy_pending=0;offline_nodes=0
        for n in node_rows:
            health=n.get('health') if isinstance(n.get('health'),dict) else {}
            remote_guard=health.get('guard') if isinstance(health.get('guard'),dict) else {}
            if remote_guard.get('requested_mode')=='enforce':node_guard_enforce+=1
            if remote_guard.get('requested_mode')=='enforce' and remote_guard.get('state')=='applied' and remote_guard.get('applied') is True:
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
            'nodes':{'total':len(node_rows),'security_fresh':fresh_nodes,'source_verified':verified_nodes,
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
                'warnings':warnings,'preview_mode':'saved_config_static',
                'live_route_api':False}

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

    @app.post('/api/traffic-engine/preview')
    def traffic_engine_preview(body:TrafficRoutePreview,p:Principal=Depends(owner)):return traffic_preview(body)

    def _runtime_targets()->list[dict]:
        hub_location=str(os.environ.get('DARK_HUB_LOCATION','')).strip()[:80]
        rows=[{'id':'hub','kind':'hub','name':'HUB','location':hub_location,'address':str(config.public_address),
               'online':True,'latencyMs':0}]
        for node in nodes.list():
            rows.append({'id':'node:'+str(node.get('id')),'nodeId':str(node.get('id')),'kind':'node',
                         'name':str(node.get('name') or node.get('id') or 'Node'),
                         'location':str(node.get('location') or ''),
                         'address':str(node.get('data_address') or ''),
                         'online':bool(node.get('online')),
                         'latencyMs':int(node.get('last_latency_ms') or 0)})
        return rows

    def _runtime_target(scope:str,*,require_online:bool=True)->dict:
        value=str(scope or 'hub').strip()
        row=next((x for x in _runtime_targets() if x['id']==value),None)
        if not row:raise HTTPException(400,'Unknown runtime server target')
        if require_online and not row.get('online'):raise HTTPException(409,'Selected Node is offline')
        return row

    @app.get('/api/runtime-targets')
    def runtime_targets(p:Principal=Depends(owner)):
        return {'items':_runtime_targets()}

    def _runtime_inbound_access_paths(scope:str,inbound_id:int,deployed:list[str])->list[str]:
        hosts=engine.section('hosts')
        def runtime_key(raw):
            value=str(raw or 'local')
            return 'hub' if value=='local' else value
        if scope=='all':
            scopes=set(deployed)
        else:
            scopes={str(scope)}
        kinds=set()
        for host in hosts if isinstance(hosts,list) else []:
            if not isinstance(host,dict) or not host.get('enable',True):continue
            if int(host.get('inboundId') or 0)!=int(inbound_id):continue
            if runtime_key(host.get('runtime')) not in scopes:continue
            kind=str(host.get('endpointType') or 'direct').lower()
            if kind in {'direct','tunnel'}:kinds.add(kind)
        if not kinds and scopes.intersection(set(deployed)):
            # A deployed runtime without an explicit Public Endpoint still has
            # the Node/Hub data-address path used by direct/failover links.
            kinds.add('direct')
        return [x for x in ('direct','tunnel') if x in kinds]

    def _runtime_inbound_rows(scope:str)->list[dict]:
        value=str(scope or 'hub').strip()
        target=None if value=='all' else _runtime_target(value,require_online=False)
        fleet=nodes.list()
        node_ids=[str(n.get('id')) for n in fleet if n.get('id')]
        rows=[]
        for inbound in engine.inbounds():
            if not inbound.get('enable',True):continue
            inbound_id=int(inbound['id']);meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
            deployed=[]
            if meta.get('deployLocal',True) is not False:deployed.append('hub')
            assigned=set(nodes.inbound_assignments(inbound_id))
            deployed.extend('node:'+node_id for node_id in node_ids if node_id in assigned)
            if target is not None and target['id'] not in deployed:continue
            if target is None and not deployed:continue
            access_paths=_runtime_inbound_access_paths(value,inbound_id,deployed)
            rows.append({'id':inbound_id,'tag':str(inbound.get('tag') or ''),
                         'remark':str(inbound.get('remark') or inbound.get('tag') or ('Inbound '+str(inbound_id))),
                         'protocol':str(inbound.get('protocol') or ''),
                         'port':int(inbound.get('port') or 0),
                         'servers':deployed,'accessPaths':access_paths})
        return rows

    @app.get('/api/runtime-inbounds')
    def runtime_inbounds(server:str='hub',p:Principal=Depends(owner)):
        value=str(server or 'hub').strip()
        if value!='all':target=_runtime_target(value,require_online=False)
        else:target={'id':'all','kind':'all','name':'All servers','location':'All','online':True}
        return {'server':target,'items':_runtime_inbound_rows(value)}

    @app.get('/api/routing/scopes')
    def routing_scopes(p:Principal=Depends(owner)):
        with store.lock:
            rows={str(r['rule_tag']):str(r['scope']) for r in store.db.execute('SELECT rule_tag,scope FROM routing_rule_scopes')}
        return {'items':rows,'targets':[{'id':'all','kind':'all','name':'All servers','location':'All'}]+_runtime_targets()}

    @app.post('/api/routing/scopes')
    def routing_scope_set(body:RoutingScopeBody,p:Principal=Depends(owner)):
        writable();scope=str(body.scope)
        if scope!='all':_runtime_target(scope,require_online=False)
        with store.transaction() as db:
            db.execute('INSERT INTO routing_rule_scopes(rule_tag,scope,updated_at) VALUES(?,?,?) '
                       'ON CONFLICT(rule_tag) DO UPDATE SET scope=excluded.scope,updated_at=excluded.updated_at',
                       (body.ruleTag,scope,time.time()))
        for node in nodes.list():
            try:ensure_node_desired_state(str(node['id']))
            except Exception:pass
        manager.audit(p.actor,p.actor.id,'routing.scope',body.ruleTag,scope)
        return {'ruleTag':body.ruleTag,'scope':scope,'targets':_runtime_targets()}

    @app.post('/api/outbounds/test')
    def outbound_test(body:OutboundProbeRequest,p:Principal=Depends(owner)):
        target=_runtime_target(body.server)
        outbounds=engine.section('outbounds')
        tags=body.tags or [str(o.get('tag')) for o in outbounds if isinstance(o,dict) and o.get('tag')]
        try:
            if target['kind']=='hub':
                items=probe_outbounds(engine._binary(),config.xray_assets,outbounds,tags=tags,
                                      attempts=int(body.attempts),timeout=float(body.timeoutSeconds))
            else:
                remote=nodes.outbound_probe(target['nodeId'],tags,attempts=int(body.attempts),timeout_seconds=int(body.timeoutSeconds))
                items=remote['items']
        except (OutboundProbeError,PolicyError) as ex:
            raise HTTPException(409,'Outbound test failed: '+str(ex))
        for item in items:
            if isinstance(item,dict):item['server']=target
        manager.audit(p.actor,p.actor.id,'outbound.test',','.join(tags[:16]),
                      'server='+target['id']+'; isolated temporary Xray; production traffic unchanged')
        return {'items':items,'server':target,'productionTrafficMutation':False}

    def _warp_inbound_scope(target:dict,inbound_ids:list[int])->tuple[list[int],list[str]]:
        rows=_runtime_inbound_rows(str(target['id']))
        if not rows:raise HTTPException(409,'Selected server has no enabled deployed inbounds')
        by_id={int(row['id']):row for row in rows}
        clean=[]
        for raw in inbound_ids or []:
            inbound_id=int(raw)
            if inbound_id not in clean:clean.append(inbound_id)
        if clean:
            missing=[x for x in clean if x not in by_id]
            if missing:raise HTTPException(409,'Selected inbound is not deployed on the selected server: '+','.join(map(str,missing)))
            selected=[by_id[x] for x in clean]
        else:
            selected=rows
            clean=[int(row['id']) for row in selected]
        tags=[str(row['tag']) for row in selected if str(row.get('tag') or '')]
        if not tags:raise HTTPException(409,'Selected server inbounds have no Xray tags')
        return clean,tags

    def _warp_managed_tag(tag:str)->bool:
        value=str(tag or '')
        return (value in {'dark-warp-ai','dark-warp-all','dark-smart-warp-ai','dark-smart-adblock'}
                or value.startswith('dark-warp-ai-') or value.startswith('dark-warp-all-')
                or value.startswith('dark-smart-adblock-'))

    def _warp_assignment_rules(warp_override:dict|None=None,adblock_override:dict|None=None)->tuple[dict,dict]:
        warp_rows={str(x['scope']):x for x in _warp_assignments()}
        ad_rows={str(x['scope']):x for x in _adblock_assignments()}
        if warp_override is not None:warp_rows[str(warp_override['scope'])]=copy.deepcopy(warp_override)
        if adblock_override is not None:ad_rows[str(adblock_override['scope'])]=copy.deepcopy(adblock_override)
        routing=copy.deepcopy(engine.section('routing'));rules=routing.get('rules',[])
        if not isinstance(rules,list):rules=[]
        base=[r for r in rules if not (isinstance(r,dict) and _warp_managed_tag(str(r.get('ruleTag') or '')))]
        generated=[];scopes={}
        from smart_routing import ADBLOCK_DOMAIN_MATCHERS
        for scope,row in sorted(ad_rows.items()):
            if not bool(row.get('enabled')):continue
            available={int(x['id']):x for x in _runtime_inbound_rows(scope)}
            ids=[int(x) for x in row.get('inboundIds',[]) if int(x) in available]
            tags=[str(available[x].get('tag') or '') for x in ids if str(available[x].get('tag') or '')]
            if not tags:continue
            rt=_warp_rule_tag('adblock',scope)
            generated.append({'type':'field','ruleTag':rt,'domain':ADBLOCK_DOMAIN_MATCHERS[:],
                              'inboundTag':tags[:],'outboundTag':'block'});scopes[rt]=scope
        for scope,row in sorted(warp_rows.items()):
            mode=str(row.get('mode') or 'off')
            if mode=='off':continue
            available={int(x['id']):x for x in _runtime_inbound_rows(scope)}
            ids=[int(x) for x in row.get('inboundIds',[]) if int(x) in available]
            tags=[str(available[x].get('tag') or '') for x in ids if str(available[x].get('tag') or '')]
            if not tags:continue
            if mode=='ai':
                rt=_warp_rule_tag('ai',scope)
                generated.append({'type':'field','ruleTag':rt,'domain':AI_DOMAIN_MATCHERS[:],
                                  'inboundTag':tags[:],'outboundTag':'warp'});scopes[rt]=scope
            elif mode=='all':
                rt=_warp_rule_tag('all',scope)
                generated.append({'type':'field','ruleTag':rt,'network':'tcp,udp',
                                  'inboundTag':tags[:],'outboundTag':'warp'});scopes[rt]=scope
        routing['rules']=generated+base
        return routing,scopes

    def _commit_managed_routing(db,routing:dict,scopes:dict):
        db.execute("INSERT INTO core_sections(name,body) VALUES('routing',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",
                   (json.dumps(routing),))
        for old in db.execute('SELECT rule_tag FROM routing_rule_scopes').fetchall():
            if _warp_managed_tag(str(old['rule_tag'])):
                db.execute('DELETE FROM routing_rule_scopes WHERE rule_tag=?',(old['rule_tag'],))
        for tag,scope in scopes.items():
            db.execute('INSERT INTO routing_rule_scopes(rule_tag,scope,updated_at) VALUES(?,?,?)',
                       (tag,scope,time.time()))

    def _warp_commit_assignment(row:dict,routing:dict,scopes:dict):
        with store.transaction() as db:
            db.execute("""INSERT INTO warp_assignments(scope,mode,adblock,inbound_ids,updated_at)
                          VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                          mode=excluded.mode,adblock=0,
                          inbound_ids=excluded.inbound_ids,updated_at=excluded.updated_at""",
                       (str(row['scope']),str(row.get('mode') or 'off'),0,
                        json.dumps([int(x) for x in row.get('inboundIds',[])]),time.time()))
            _commit_managed_routing(db,routing,scopes)

    def _adblock_commit_assignment(row:dict,routing:dict,scopes:dict):
        with store.transaction() as db:
            db.execute("""INSERT INTO adblock_assignments(scope,enabled,inbound_ids,updated_at)
                          VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                          enabled=excluded.enabled,inbound_ids=excluded.inbound_ids,updated_at=excluded.updated_at""",
                       (str(row['scope']),int(bool(row.get('enabled'))),
                        json.dumps([int(x) for x in row.get('inboundIds',[])]),time.time()))
            _commit_managed_routing(db,routing,scopes)

    def _warp_profile_public(scope:str)->dict:
        target=_runtime_target(scope,require_online=False)
        outbound=_warp_profile(scope);assignment=_warp_assignment(scope)
        settings=outbound.get('settings',{}) if isinstance(outbound,dict) and isinstance(outbound.get('settings'),dict) else {}
        peers=settings.get('peers') if isinstance(settings,dict) else []
        peer=peers[0] if isinstance(peers,list) and peers and isinstance(peers[0],dict) else {}
        available=_runtime_inbound_rows(scope)
        available_ids=[int(x['id']) for x in available]
        selected=[int(x) for x in assignment.get('inboundIds',[])]
        coverage_rows=[x for x in available if not selected or int(x['id']) in set(selected)]
        access_paths=[]
        for row in coverage_rows:
            for kind in row.get('accessPaths',[]):
                if kind not in access_paths:access_paths.append(kind)
        return {'registered':bool(outbound and str(outbound.get('protocol','')).lower()=='wireguard'),
                'tag':'warp','mode':str(assignment.get('mode') or 'off'),'adblock':bool(assignment.get('adblock')),
                'server':target,'serverId':scope,'inboundIds':selected,
                'allInbounds':bool(available_ids and set(selected)==set(available_ids)),
                'endpoint':str(peer.get('endpoint') or ''),
                'addresses':[str(x) for x in settings.get('address',[]) if isinstance(x,str)],
                'availableInbounds':available,'accessPaths':access_paths,'secretExposed':False}

    def _warp_profiles_public()->list[dict]:
        return [_warp_profile_public(str(t['id'])) for t in _runtime_targets()]

    def _warp_install_profile(scope:str,outbound:dict,device_id:str='')->dict:
        target=_runtime_target(scope);profile=copy.deepcopy(outbound);profile['tag']='warp'
        if target['kind']=='hub':
            current=engine.section('outbounds')
            updated=[copy.deepcopy(x) for x in current if not (isinstance(x,dict) and str(x.get('tag') or '')=='warp')]
            updated.append(profile)
            candidate=engine.build_config();candidate['outbounds']=copy.deepcopy(updated);engine.validate(candidate)
            def commit():
                with store.transaction() as db:
                    db.execute("""INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,?)
                                  ON CONFLICT(scope) DO UPDATE SET outbound_json=excluded.outbound_json,
                                  device_id=excluded.device_id,updated_at=excluded.updated_at""",
                               ('hub',json.dumps(profile,separators=(',',':')),str(device_id or '')[:256],time.time()))
                    db.execute("INSERT INTO core_sections(name,body) VALUES('outbounds',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",
                               (json.dumps(updated),))
            runtime=engine.apply_config(candidate,start=True,force=True,after_success=commit)
            state=engine.runtime_state()
            if state.get('state')!='running' or state.get('dirty') or state.get('last_error'):
                raise HTTPException(409,'Hub WARP profile installed but runtime verification failed')
            return {'server':target,'runtime':runtime,'verification':{'ok':True,'state':'running','dirty':False}}
        old=_warp_profile(scope)
        _warp_profile_save(scope,profile,device_id)
        try:
            state=ensure_node_desired_state(target['nodeId'])
            sync=sync_node_assignments(target['nodeId'])
            desired=nodes.desired_state(target['nodeId'],include_payload=False)
            remote=nodes.probe(target['nodeId'],timeout=8.0);core=(remote.get('health') or {}).get('core') or {}
            ok=not desired.get('pending') and not desired.get('last_error') and core.get('state')=='running' and not core.get('dirty')
            if not ok:raise PolicyError('Node WARP profile verification failed')
            return {'server':target,'nodeSync':sync,'verification':{'ok':True,'pending':False,
                    'coreState':'running','coreDirty':False,'latencyMs':remote.get('latency_ms')}}
        except Exception as ex:
            try:
                if old is None:
                    with store.transaction() as db:db.execute('DELETE FROM warp_profiles WHERE scope=?',(scope,))
                else:_warp_profile_save(scope,old,'rollback')
                ensure_node_desired_state(target['nodeId']);sync_node_assignments(target['nodeId'])
            except Exception:pass
            if isinstance(ex,HTTPException):raise
            raise HTTPException(409,'Node WARP profile install failed: '+str(ex)) from ex

    def _warp_status(tag:str='warp',server:str='hub')->dict:
        if tag!='warp':raise HTTPException(400,'Managed WARP tag must be warp')
        return _warp_profile_public(str(server or 'hub'))

    @app.get('/api/warp/status')
    def warp_status(tag:str='warp',server:str='hub',p:Principal=Depends(owner)):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',tag):raise HTTPException(400,'Invalid WARP tag')
        return _warp_status(tag,server)

    @app.get('/api/warp/profiles')
    def warp_profiles(p:Principal=Depends(owner)):
        return {'items':_warp_profiles_public()}

    @app.post('/api/warp/create')
    def warp_create(body:WarpCreate,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server);before=bool(_warp_profile(target['id']))
        try:registered=register_cloudflare_warp(tag='warp')
        except WarpRegistrationError as ex:raise HTTPException(502,str(ex))
        installed=_warp_install_profile(target['id'],registered['outbound'],registered['deviceId'])
        manager.audit(p.actor,p.actor.id,'warp.rotate' if before else 'warp.create',target['id'],
                      'Independent Cloudflare WARP profile installed on selected runtime')
        return _warp_profile_public(target['id'])|{'created':not before,'rotated':before,
               'deviceId':registered['deviceId'],'runtimeMutation':True,'installation':installed}

    @app.post('/api/warp/rotate')
    def warp_rotate(body:WarpCreate,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server)
        if not _warp_profile(target['id']):raise HTTPException(404,'WARP profile not found on selected server')
        try:registered=register_cloudflare_warp(tag='warp')
        except WarpRegistrationError as ex:raise HTTPException(502,str(ex))
        installed=_warp_install_profile(target['id'],registered['outbound'],registered['deviceId'])
        manager.audit(p.actor,p.actor.id,'warp.rotate',target['id'],'Independent WARP profile rotated on selected runtime')
        return _warp_profile_public(target['id'])|{'rotated':True,'deviceId':registered['deviceId'],
                                                   'runtimeMutation':True,'installation':installed}

    @app.post('/api/warp/scan')
    def warp_scan(body:WarpProbeRequest,p:Principal=Depends(owner)):
        target=_runtime_target(body.server);outbound=_warp_profile(target['id'])
        if not outbound or str(outbound.get('protocol','')).lower()!='wireguard':
            raise HTTPException(409,'Create the WARP profile on the selected server first')
        settings=outbound.get('settings') if isinstance(outbound.get('settings'),dict) else {}
        peers=settings.get('peers') if isinstance(settings,dict) else []
        current=str(peers[0].get('endpoint') or '') if isinstance(peers,list) and peers and isinstance(peers[0],dict) else ''
        try:
            if target['kind']=='hub':
                raw=probe_outbounds(engine._binary(),config.xray_assets,[outbound],
                                    tags=['warp'],attempts=3,timeout=5.0,trace=True)[0]
            else:
                remote=nodes.warp_endpoint_probe(target['nodeId'],'warp',[current],attempts=3,timeout_seconds=5)
                raw=remote['items'][0]
        except (OutboundProbeError,PolicyError) as ex:
            raise HTTPException(409,'WARP ping failed: '+str(ex))
        egress=raw.get('egress') if isinstance(raw.get('egress'),dict) else {}
        normalized={'tag':'warp','ok':bool(raw.get('success')) and bool(raw.get('warpVerified')),
                    'latencyMs':raw.get('delayMs'),'lossPercent':raw.get('lossPercent'),
                    'jitterMs':raw.get('jitterMs'),'error':raw.get('error',''),
                    'egress':{'ip':egress.get('ip',''),'country':egress.get('country',''),
                              'colo':egress.get('colo',''),'warp':egress.get('warp','')}}
        safety=evaluate_warp_safety([normalized],['warp'],
            max_loss_percent=float(DEFAULT_SAFETY_THRESHOLDS['maxLossPercent']),
            max_latency_ms=float(DEFAULT_SAFETY_THRESHOLDS['maxLatencyMs']),
            max_jitter_ms=float(DEFAULT_SAFETY_THRESHOLDS['maxJitterMs']))
        if not raw.get('warpVerified'):
            safety['passed']=False;safety.setdefault('issues',[]).append('Cloudflare trace did not verify warp=on')
        manager.audit(p.actor,p.actor.id,'warp.scan',target['id'],'current endpoint trace; production traffic unchanged')
        return {'passed':safety['passed'],'items':[normalized],'issues':safety['issues'],'server':target,
                'egress':normalized['egress'],'thresholds':safety['thresholds'],'productionTrafficMutation':False}

    @app.post('/api/warp/endpoints/scan')
    def warp_endpoint_scan(body:WarpProbeRequest,p:Principal=Depends(owner)):
        target=_runtime_target(body.server);outbound=_warp_profile(target['id'])
        if not outbound or str(outbound.get('protocol','')).lower()!='wireguard':
            raise HTTPException(409,'Create the WARP outbound first')
        settings=outbound.get('settings') if isinstance(outbound.get('settings'),dict) else {}
        peers=settings.get('peers') if isinstance(settings,dict) else []
        local_current=str(peers[0].get('endpoint') or '') if isinstance(peers,list) and peers and isinstance(peers[0],dict) else ''
        try:
            if target['kind']=='hub':
                current=local_current;endpoints=warp_endpoint_candidates(current);clones=[];tag_to_endpoint={}
                for index,endpoint in enumerate(endpoints):
                    candidate=copy.deepcopy(outbound);probe_tag=f'dark-warp-path-{index}'
                    candidate['tag']=probe_tag;candidate['settings']['peers'][0]['endpoint']=endpoint
                    clones.append(candidate);tag_to_endpoint[probe_tag]=endpoint
                raw=probe_outbounds(engine._binary(),config.xray_assets,clones,
                                    tags=[x['tag'] for x in clones],attempts=2,timeout=4.0,trace=True)
                for row in raw:row['endpoint']=tag_to_endpoint.get(str(row.get('tag') or ''),'')
            else:
                remote=nodes.warp_endpoint_probe(target['nodeId'],body.tag,None,attempts=2,timeout_seconds=4)
                current=str(remote.get('current') or local_current);raw=remote['items']
        except (OutboundProbeError,PolicyError) as ex:
            raise HTTPException(409,'WARP endpoint scan failed: '+str(ex))
        items=[]
        max_loss=float(DEFAULT_SAFETY_THRESHOLDS['maxLossPercent'])
        max_latency=float(DEFAULT_SAFETY_THRESHOLDS['maxLatencyMs'])
        max_jitter=float(DEFAULT_SAFETY_THRESHOLDS['maxJitterMs'])
        for row in raw:
            endpoint=str(row.get('endpoint') or '')
            egress=row.get('egress') if isinstance(row.get('egress'),dict) else {}
            loss=float(row['lossPercent']) if row.get('lossPercent') is not None else 100.0
            latency=float(row['delayMs']) if row.get('delayMs') is not None else 10**9
            jitter=float(row['jitterMs']) if row.get('jitterMs') is not None else 10**9
            ready=bool(row.get('success')) and bool(row.get('warpVerified')) and loss<=max_loss and latency<=max_latency and jitter<=max_jitter
            items.append({'endpoint':endpoint,'ready':ready,'delayMs':row.get('delayMs'),
                          'lossPercent':row.get('lossPercent'),'jitterMs':row.get('jitterMs'),
                          'country':egress.get('country',''),'colo':egress.get('colo',''),
                          'egressIp':egress.get('ip',''),'warp':egress.get('warp',''),
                          'error':row.get('error',''),'selected':endpoint==current})
        items.sort(key=lambda x:(not x['ready'],
                                 float(x['lossPercent']) if x['lossPercent'] is not None else 100.0,
                                 float(x['delayMs']) if x['delayMs'] is not None else 10**9,
                                 float(x['jitterMs']) if x['jitterMs'] is not None else 10**9))
        manager.audit(p.actor,p.actor.id,'warp.endpoint_scan',body.tag,
                      f"server={target['id']}; {len(items)} Cloudflare consumer WARP paths tested; production unchanged")
        return {'selected':current,'items':items,'server':target,'productionTrafficMutation':False}

    @app.post('/api/warp/endpoint')
    def warp_endpoint_select(body:WarpEndpointSelect,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server);outbound=_warp_profile(target['id'])
        if not outbound or str(outbound.get('protocol','')).lower()!='wireguard':
            raise HTTPException(409,'Create the WARP outbound first')
        try:endpoint=validate_warp_endpoint(body.endpoint)
        except WarpRegistrationError as ex:raise HTTPException(400,str(ex))
        target=_runtime_target(body.server);candidate_out=copy.deepcopy(outbound)
        candidate_out['settings']['peers'][0]['endpoint']=endpoint
        try:
            if target['kind']=='hub':
                checked=probe_outbounds(engine._binary(),config.xray_assets,[candidate_out],
                                        tags=[body.tag],attempts=2,timeout=5.0,trace=True)[0]
            else:
                remote=nodes.warp_endpoint_probe(target['nodeId'],body.tag,[endpoint],attempts=2,timeout_seconds=5)
                checked=remote['items'][0]
        except (OutboundProbeError,PolicyError) as ex:
            raise HTTPException(409,'Selected WARP endpoint test failed: '+str(ex))
        loss=float(checked['lossPercent']) if checked.get('lossPercent') is not None else 100.0
        latency=float(checked['delayMs']) if checked.get('delayMs') is not None else 10**9
        jitter=float(checked['jitterMs']) if checked.get('jitterMs') is not None else 10**9
        if (not checked.get('success') or not checked.get('warpVerified')
                or loss>float(DEFAULT_SAFETY_THRESHOLDS['maxLossPercent'])
                or latency>float(DEFAULT_SAFETY_THRESHOLDS['maxLatencyMs'])
                or jitter>float(DEFAULT_SAFETY_THRESHOLDS['maxJitterMs'])):
            raise HTTPException(409,'Selected endpoint did not pass the WARP safety gate')
        installed=_warp_install_profile(target['id'],candidate_out,'endpoint-change')
        manager.audit(p.actor,p.actor.id,'warp.endpoint',target['id'],
                      endpoint+'; verified and installed only on selected runtime')
        return _warp_profile_public(target['id'])|{'selected':endpoint,'test':checked,
               'server':target,'installation':installed,'runtimeMutation':True}

    @app.post('/api/warp/mode')
    def warp_mode(body:WarpMode,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server);scope=str(target['id']);outbound=_warp_profile(scope)
        if (body.mode!='off') and (not outbound or str(outbound.get('protocol','')).lower()!='wireguard'):
            raise HTTPException(409,'Create the WARP profile on the selected server first')
        with store.lock:
            active=store.db.execute("SELECT id FROM smart_routing_rollouts WHERE state='running' LIMIT 1").fetchone()
        if active:raise HTTPException(409,'A Smart Routing rollout is running; finish or abort it first')
        selected_inbound_ids=[];inbound_tags=[];safety=None
        if body.mode!='off' or body.adblock:
            selected_inbound_ids,inbound_tags=_warp_inbound_scope(target,body.inboundIds)
        if body.adblock:
            block=next((x for x in engine.section('outbounds') if isinstance(x,dict) and x.get('tag')=='block'),None)
            if not block or str(block.get('protocol','')).lower()!='blackhole':
                raise HTTPException(409,"Smart Adblock requires blackhole outbound 'block'")
        if body.mode!='off':
            try:
                if target['kind']=='hub':
                    observations=scan_warp_outbounds(engine._binary(),config.xray_assets,[outbound],attempts=3,timeout=5.0)
                    ranked=rank_warp_paths(observations,max_results=1)
                else:
                    ranked=nodes.smart_warp_probe(target['nodeId'],['warp'],attempts=3,timeout_seconds=5)['items']
            except (SmartWarpProbeError,PolicyError) as ex:
                raise HTTPException(409,'WARP safety scan failed: '+str(ex))
            safety=evaluate_warp_safety(ranked,['warp'],
                max_loss_percent=float(DEFAULT_SAFETY_THRESHOLDS['maxLossPercent']),
                max_latency_ms=float(DEFAULT_SAFETY_THRESHOLDS['maxLatencyMs']),
                max_jitter_ms=float(DEFAULT_SAFETY_THRESHOLDS['maxJitterMs']))
            if not safety['passed']:
                raise HTTPException(409,'WARP safety scan blocked activation: '+'; '.join(safety['issues']))
        assignment={'scope':scope,'mode':body.mode,'adblock':bool(body.adblock),
                    'inboundIds':selected_inbound_ids,'updatedAt':time.time()}
        routing,scopes=_warp_assignment_rules(assignment)
        hub_candidate=engine.build_config();hub_candidate['routing']=engine.filter_routing_for_scope(routing,'hub')
        def commit():_warp_commit_assignment(assignment,routing,scopes)
        runtime=engine.apply_config(hub_candidate,start=True,force=False,after_success=commit)
        sync_result={};verification={'hub':None,'nodes':{}}
        hub_state=engine.runtime_state();hub_ok=hub_state.get('state')=='running' and not hub_state.get('dirty') and not hub_state.get('last_error')
        verification['hub']={'ok':hub_ok,'state':hub_state.get('state'),'dirty':bool(hub_state.get('dirty')),
                             'lastError':str(hub_state.get('last_error') or '')}
        if target['kind']=='hub' and not hub_ok:raise HTTPException(409,'WARP routing did not verify on Hub')
        if target['kind']=='node':
            try:
                ensure_node_desired_state(target['nodeId'])
                sync_result[target['nodeId']]=sync_node_assignments(target['nodeId'])
                desired=nodes.desired_state(target['nodeId'],include_payload=False)
                remote=nodes.probe(target['nodeId'],timeout=8.0);core=(remote.get('health') or {}).get('core') or {}
            except (PolicyError,OSError) as ex:
                raise HTTPException(409,'WARP routing saved but Node sync/verification failed: '+str(ex))
            ok=not desired.get('pending') and not desired.get('last_error') and core.get('state')=='running' and not core.get('dirty')
            verification['nodes'][target['nodeId']]={'ok':ok,'pending':bool(desired.get('pending')),
                'lastError':str(desired.get('last_error') or ''),'coreState':str(core.get('state') or ''),
                'coreDirty':bool(core.get('dirty')),'latencyMs':remote.get('latency_ms')}
            if not ok:raise HTTPException(409,'WARP routing did not verify on Node '+target['nodeId'])
        manager.audit(p.actor,p.actor.id,'warp.mode',scope,
                      'mode='+body.mode+'; adblock='+str(body.adblock)+'; inboundIds='+','.join(map(str,selected_inbound_ids)))
        status=_warp_profile_public(scope)
        if (body.mode!='off' or body.adblock) and set(status.get('inboundIds') or [])!=set(selected_inbound_ids):
            raise HTTPException(409,'WARP routing saved but inbound scope verification failed')
        return status|{'applied':True,'safety':safety,'runtime':runtime,
                       'nodeSync':sync_result,'server':target,'verification':verification}

    def _adblock_profile_public(scope:str)->dict:
        target=_runtime_target(scope,require_online=False);assignment=_adblock_assignment(scope)
        available=_runtime_inbound_rows(scope);available_ids=[int(x['id']) for x in available]
        selected=[int(x) for x in assignment.get('inboundIds',[])]
        rows=[x for x in available if not selected or int(x['id']) in set(selected)]
        access=[]
        for row in rows:
            for kind in row.get('accessPaths',[]):
                if kind not in access:access.append(kind)
        return {'enabled':bool(assignment.get('enabled')),'server':target,'serverId':scope,
                'inboundIds':selected,'allInbounds':bool(available_ids and set(selected)==set(available_ids)),
                'availableInbounds':available,'accessPaths':access,
                'blockReady':any(isinstance(x,dict) and x.get('tag')=='block' and str(x.get('protocol','')).lower()=='blackhole'
                                 for x in engine.section('outbounds'))}

    @app.get('/api/adblock/profiles')
    def adblock_profiles(p:Principal=Depends(owner)):
        return {'items':[_adblock_profile_public(str(t['id'])) for t in _runtime_targets()]}

    @app.get('/api/adblock/status')
    def adblock_status(server:str='hub',p:Principal=Depends(owner)):
        return _adblock_profile_public(str(server or 'hub'))

    @app.post('/api/adblock/mode')
    def adblock_mode(body:AdblockMode,p:Principal=Depends(owner)):
        writable();target=_runtime_target(body.server);scope=str(target['id'])
        block=next((x for x in engine.section('outbounds') if isinstance(x,dict) and x.get('tag')=='block'),None)
        if body.enabled and (not block or str(block.get('protocol','')).lower()!='blackhole'):
            raise HTTPException(409,"Smart Adblock requires blackhole outbound 'block'")
        with store.lock:
            active=store.db.execute("SELECT id FROM smart_routing_rollouts WHERE state='running' LIMIT 1").fetchone()
        if active:raise HTTPException(409,'A Smart Routing rollout is running; finish or abort it first')
        ids=[]
        if body.enabled:ids,_=_warp_inbound_scope(target,body.inboundIds)
        assignment={'scope':scope,'enabled':bool(body.enabled),'inboundIds':ids,'updatedAt':time.time()}
        routing,scopes=_warp_assignment_rules(adblock_override=assignment)
        hub_candidate=engine.build_config();hub_candidate['routing']=engine.filter_routing_for_scope(routing,'hub')
        def commit():_adblock_commit_assignment(assignment,routing,scopes)
        runtime=engine.apply_config(hub_candidate,start=True,force=False,after_success=commit)
        sync_result={};verification={'hub':None,'nodes':{}}
        hub_state=engine.runtime_state();hub_ok=hub_state.get('state')=='running' and not hub_state.get('dirty') and not hub_state.get('last_error')
        verification['hub']={'ok':hub_ok,'state':hub_state.get('state'),'dirty':bool(hub_state.get('dirty')),
                             'lastError':str(hub_state.get('last_error') or '')}
        if target['kind']=='hub' and not hub_ok:raise HTTPException(409,'Adblock routing did not verify on Hub')
        if target['kind']=='node':
            try:
                ensure_node_desired_state(target['nodeId']);sync_result[target['nodeId']]=sync_node_assignments(target['nodeId'])
                desired=nodes.desired_state(target['nodeId'],include_payload=False)
                remote=nodes.probe(target['nodeId'],timeout=8.0);core=(remote.get('health') or {}).get('core') or {}
            except (PolicyError,OSError) as ex:raise HTTPException(409,'Adblock Node sync/verification failed: '+str(ex))
            ok=not desired.get('pending') and not desired.get('last_error') and core.get('state')=='running' and not core.get('dirty')
            verification['nodes'][target['nodeId']]={'ok':ok,'pending':bool(desired.get('pending')),
                'lastError':str(desired.get('last_error') or ''),'coreState':str(core.get('state') or ''),
                'coreDirty':bool(core.get('dirty')),'latencyMs':remote.get('latency_ms')}
            if not ok:raise HTTPException(409,'Adblock routing did not verify on Node '+target['nodeId'])
        manager.audit(p.actor,p.actor.id,'adblock.mode',scope,'enabled='+str(body.enabled)+'; inboundIds='+','.join(map(str,ids)))
        status=_adblock_profile_public(scope)
        if body.enabled and set(status.get('inboundIds') or [])!=set(ids):
            raise HTTPException(409,'Adblock routing saved but inbound scope verification failed')
        return status|{'applied':True,'runtime':runtime,'nodeSync':sync_result,'verification':verification}

    @app.get('/api/smart-routing/plan')
    def smart_routing_plan(p:Principal=Depends(owner)):
        plan=build_stage7_plan(nodes.list(),engine.section('outbounds'),engine.section('routing'))
        plan['nodeRoles']=stage7_node_roles()
        return plan

    def _stage7_material(body:SmartRoutingPreview):
        outbounds=engine.section('outbounds');routing=engine.section('routing')
        observatory=engine.section('observatory') or {};current_roles=stage7_node_roles()
        node_rows=nodes.list();known_nodes={str(n.get('id')) for n in node_rows if n.get('id')}
        def role_ids(raw,current,label):
            values=current if raw is None else raw
            clean=[]
            for item in values:
                node_id=str(item).strip()
                if not node_id or node_id in clean:continue
                if len(node_id)>128 or node_id not in known_nodes:
                    raise HTTPException(400,'Unknown '+label+' Node: '+node_id)
                clean.append(node_id)
            return clean
        after_roles={
            'warpNodeIds':role_ids(body.warpNodeIds,current_roles.get('warpNodeIds',[]),'Smart WARP'),
            'adblockNodeIds':role_ids(body.adblockNodeIds,current_roles.get('adblockNodeIds',[]),'Smart Adblock'),
        }
        if not body.warpAi:after_roles['warpNodeIds']=[]
        if not body.adblock:after_roles['adblockNodeIds']=[]
        try:
            patch=build_stage7_patch(outbounds,routing,current_observatory=observatory,
                                     warp_outbound_tags=body.warpOutboundTags,
                                     enable_warp_ai=body.warpAi,enable_adblock=body.adblock)
        except SmartRoutingError as ex:
            raise HTTPException(400,str(ex))
        if body.warpAi and not after_roles['warpNodeIds']:
            patch.setdefault('warnings',[]).append('Smart WARP AI has no assigned Node role yet.')
        if body.adblock and not after_roles['adblockNodeIds']:
            patch.setdefault('warnings',[]).append('Smart Adblock has no assigned Node role yet.')
        candidate=build_stage7_candidate_config(engine.build_config(),patch)
        baseline_hash=stage7_state_hash(outbounds,routing,observatory,current_roles)
        candidate_hash=stage7_state_hash(outbounds,patch['routing'],patch.get('observatory') or {},after_roles)
        return outbounds,routing,observatory,current_roles,after_roles,patch,candidate,baseline_hash,candidate_hash

    def _stage7_revision(revision_id:str)->dict:
        with store.lock:row=store.db.execute('SELECT * FROM smart_routing_revisions WHERE id=?',(revision_id,)).fetchone()
        if not row:raise HTTPException(404,'Smart Routing revision not found')
        return dict(row)

    def _stage7_public(row:dict)->dict:
        request=json.loads(row['request_body']);roles=json.loads(row.get('after_node_roles') or '{}')
        try:safety=json.loads(row.get('safety_report') or '{}')
        except Exception:safety={}
        checked=float(row.get('safety_checked_at') or 0);recorded=bool(row.get('safety_passed'))
        fresh=bool(recorded and checked and time.time()-checked<=STAGE7_SAFETY_TTL_SECONDS
                   and safety.get('candidateHash')==row['candidate_hash'])
        return {'revisionId':row['id'],'actor':row['actor'],'createdAt':row['created_at'],
                'baselineHash':row['baseline_hash'],'candidateHash':row['candidate_hash'],
                'state':row['state'],'detail':row['detail'],'appliedAt':row['applied_at'],
                'rolledBackAt':row['rolled_back_at'],'request':request,'nodeRoles':roles,
                'safetyPassed':fresh,'safetyRecordedPassed':recorded,'safetyCheckedAt':checked,
                'safetyExpiresAt':checked+STAGE7_SAFETY_TTL_SECONDS if checked else 0,
                'safetyHash':row.get('safety_hash') or '','safetyReport':safety,
                'rollbackAvailable':row['state']=='applied'}

    def _stage7_write_sections(db,routing:dict,observatory:dict,node_roles:dict):
        for name,value in (('routing',routing),('observatory',observatory)):
            db.execute('INSERT INTO core_sections(name,body) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET body=excluded.body',
                       (name,json.dumps(value)))
        db.execute('DELETE FROM smart_routing_node_roles')
        warp=set(node_roles.get('warpNodeIds',[]));ads=set(node_roles.get('adblockNodeIds',[]));now=time.time()
        for node_id in sorted(warp|ads):
            db.execute('INSERT INTO smart_routing_node_roles(node_id,warp_ai,adblock,updated_at) VALUES(?,?,?,?)',
                       (node_id,int(node_id in warp),int(node_id in ads),now))

    def _stage7_rollout_public(rollout_id:str,*,include_timeline:bool=True)->dict:
        with store.lock:
            row=store.db.execute('SELECT * FROM smart_routing_rollouts WHERE id=?',(rollout_id,)).fetchone()
            items=[dict(x) for x in store.db.execute(
                'SELECT * FROM smart_routing_rollout_nodes WHERE rollout_id=? ORDER BY ord,node_id',(rollout_id,)).fetchall()]
            events=[dict(x) for x in store.db.execute(
                'SELECT * FROM smart_routing_rollout_events WHERE rollout_id=? ORDER BY id DESC LIMIT 100',(rollout_id,)).fetchall()] if include_timeline else []
        if not row:raise HTTPException(404,'Smart Routing rollout not found')
        for event in events:
            try:event['metrics']=json.loads(event.get('metrics') or '{}')
            except Exception:event['metrics']={}
        events.reverse();doc=dict(row);total=len(items);healthy=sum(1 for x in items if x['state'] in {'healthy','completed'})
        doc.update({'rolloutId':doc.pop('id'),'revisionId':doc.pop('revision_id'),'currentIndex':doc.pop('current_index'),
                    'observationSeconds':doc.pop('observation_seconds',5),'controlState':doc.pop('control_state','run'),
                    'createdAt':doc.pop('created_at'),'startedAt':doc.pop('started_at'),'pausedAt':doc.pop('paused_at',0),
                    'resumedAt':doc.pop('resumed_at',0),'abortedAt':doc.pop('aborted_at',0),
                    'completedAt':doc.pop('completed_at'),'rolledBackAt':doc.pop('rolled_back_at'),
                    'items':items,'timeline':events,'totalNodes':total,'healthyNodes':healthy,
                    'progressPercent':100 if doc['state']=='completed' else int(100*healthy/max(1,total+1))})
        return doc

    def _stage7_rollout_event(rollout_id:str,kind:str,detail:str='',*,node_id:str='',phase:str='',state_name:str='',metrics:dict|None=None):
        payload=json.dumps(metrics or {},sort_keys=True,separators=(',',':'),ensure_ascii=False)
        with store.transaction() as db:
            db.execute('''INSERT INTO smart_routing_rollout_events(rollout_id,node_id,phase,state,kind,detail,metrics,at)
                          VALUES(?,?,?,?,?,?,?,?)''',
                       (rollout_id,node_id,phase,state_name,kind,detail[:500],payload,time.time()))

    def _stage7_rollout_order(before_roles:dict,after_roles:dict)->list[tuple[str,str]]:
        bw=[str(x) for x in before_roles.get('warpNodeIds',[])];ba=[str(x) for x in before_roles.get('adblockNodeIds',[])]
        aw=[str(x) for x in after_roles.get('warpNodeIds',[])];aa=[str(x) for x in after_roles.get('adblockNodeIds',[])]
        order=[];seen=set()
        def add(node_id,role):
            if node_id and node_id not in seen:order.append((node_id,role));seen.add(node_id)
        if aw:add(aw[0],'canary-warp')
        if aa:add(aa[0],'canary-adblock' if aa[0] not in seen else 'canary-warp+adblock')
        for node_id in aw:add(node_id,'warp')
        for node_id in aa:add(node_id,'adblock' if node_id not in set(aw) else 'warp+adblock')
        for node_id in bw+ba:
            if node_id not in set(aw+aa):add(node_id,'remove')
        return order

    def _stage7_rollout_mark(rollout_id:str,node_id:str,state_name:str,detail:str='',desired:dict|None=None,metrics:dict|None=None):
        now=time.time();revision=int((desired or {}).get('revision') or 0);digest=str((desired or {}).get('hash') or '')
        with store.transaction() as db:
            db.execute('''UPDATE smart_routing_rollout_nodes SET state=?,detail=?,desired_revision=?,desired_hash=?,
                          verified_at=CASE WHEN ?='healthy' THEN ? ELSE verified_at END,updated_at=?
                          WHERE rollout_id=? AND node_id=?''',
                       (state_name,detail[:500],revision,digest,state_name,now,now,rollout_id,node_id))
        _stage7_rollout_event(rollout_id,'node_state',detail,node_id=node_id,state_name=state_name,metrics=metrics)

    def _stage7_rollout_control(rollout_id:str,*,node_id:str='')->str:
        announced=False
        while True:
            with store.lock:row=store.db.execute('SELECT state,phase,control_state FROM smart_routing_rollouts WHERE id=?',(rollout_id,)).fetchone()
            if not row or row['state']!='running':return 'stop'
            control=str(row['control_state'] or 'run')
            if control=='abort_requested':return 'abort'
            if control in {'pause_requested','paused'}:
                if control=='pause_requested':
                    with store.transaction() as db:
                        db.execute("UPDATE smart_routing_rollouts SET control_state='paused',paused_at=?,detail=? WHERE id=? AND state='running'",
                                   (time.time(),'Paused by owner',rollout_id))
                if not announced:
                    _stage7_rollout_event(rollout_id,'paused','Rollout paused by owner',node_id=node_id,phase=str(row['phase']),state_name='paused')
                    announced=True
                time.sleep(.25);continue
            if announced:
                _stage7_rollout_event(rollout_id,'resumed','Rollout resumed',node_id=node_id,phase=str(row['phase']),state_name='running')
            return 'run'

    def _stage7_verify_rollout_node(node_id:str,desired:dict)->dict:
        probe=nodes.probe(node_id,timeout=8.0);state=nodes.desired_state(node_id,include_payload=False)
        health=probe.get('health') if isinstance(probe.get('health'),dict) else {}
        core=health.get('core') if isinstance(health.get('core'),dict) else {}
        reasons=[]
        if state.get('pending'):reasons.append('desired_state_pending')
        if state.get('last_error'):reasons.append('desired_state_error')
        if int(state.get('applied_revision') or 0)!=int(desired.get('revision') or 0):reasons.append('revision_not_applied')
        if str(state.get('applied_hash') or '')!=str(desired.get('hash') or ''):reasons.append('hash_not_applied')
        if core.get('state')!='running':reasons.append('core_not_running')
        if core.get('dirty') is True:reasons.append('runtime_dirty')
        if core.get('last_error'):reasons.append('core_error')
        if reasons:raise PolicyError('Node verification failed: '+', '.join(reasons))
        return {'latencyMs':probe.get('latency_ms',0),'appliedRevision':state.get('applied_revision'),
                'appliedHash':state.get('applied_hash'),'coreState':core.get('state')}

    def _stage7_rollback_rollout_nodes(rollout_id:str,reason:str,*,final_state:str='rolled_back')->bool:
        with store.lock:rows=[dict(x) for x in store.db.execute(
            "SELECT * FROM smart_routing_rollout_nodes WHERE rollout_id=? AND state IN ('applying','verifying','healthy','failed') ORDER BY ord DESC",
            (rollout_id,)).fetchall()]
        ok=True
        for row in rows:
            node_id=row['node_id'];_stage7_rollout_mark(rollout_id,node_id,'rolling_back',reason)
            try:
                desired=ensure_node_desired_state(node_id)
                nodes.sync_desired_state(node_id,desired,legacy_bundles=build_node_bundles(node_id))
                _stage7_verify_rollout_node(node_id,desired)
                _stage7_rollout_mark(rollout_id,node_id,'rolled_back','baseline restored',desired)
            except Exception as ex:
                ok=False;_stage7_rollout_mark(rollout_id,node_id,'rollback_failed',str(ex))
        terminal=(final_state if ok else 'failed')
        with store.transaction() as db:
            db.execute('UPDATE smart_routing_rollouts SET state=?,phase=?,control_state=?,detail=?,rolled_back_at=? WHERE id=?',
                       (terminal,'rollback','run',reason[:500],time.time(),rollout_id))
        _stage7_rollout_event(rollout_id,'rollback_complete',reason,phase='rollback',state_name=terminal,
                              metrics={'nodes':len(rows),'successful':ok})
        return ok

    def _stage7_rollout_worker(rollout_id:str):
        try:
            with store.lock:
                rollout=store.db.execute('SELECT * FROM smart_routing_rollouts WHERE id=?',(rollout_id,)).fetchone()
                rows=[dict(x) for x in store.db.execute(
                    'SELECT * FROM smart_routing_rollout_nodes WHERE rollout_id=? ORDER BY ord,node_id',(rollout_id,)).fetchall()]
            if not rollout or rollout['state']!='running':return
            revision=_stage7_revision(rollout['revision_id'])
            request=json.loads(revision['request_body']);after_roles=json.loads(revision.get('after_node_roles') or '{}')
            warp_nodes=set(after_roles.get('warpNodeIds',[]));warp_tags=[str(x) for x in request.get('warpOutboundTags',[]) if str(x)]
            try:safety_report=json.loads(revision.get('safety_report') or '{}')
            except Exception:safety_report={}
            thresholds=(safety_report.get('warpSafety') or {}).get('thresholds') or DEFAULT_SAFETY_THRESHOLDS
            _stage7_rollout_event(rollout_id,'worker_started','Rollout worker active',phase=str(rollout['phase']),state_name='running')
            for index,row in enumerate(rows):
                if row['state'] in {'healthy','completed'}:continue
                control=_stage7_rollout_control(rollout_id,node_id=row['node_id'])
                if control=='abort':
                    _stage7_rollout_event(rollout_id,'abort_ack','Abort acknowledged before Node apply',node_id=row['node_id'],state_name='aborting')
                    _stage7_rollback_rollout_nodes(rollout_id,'Aborted by owner',final_state='aborted');return
                if control=='stop':return
                node_id=row['node_id']
                with store.transaction() as db:
                    db.execute('UPDATE smart_routing_rollouts SET current_index=?,phase=?,detail=? WHERE id=?',
                               (index,'canary' if str(row['role']).startswith('canary') else 'batch','Applying '+node_id,rollout_id))
                _stage7_rollout_mark(rollout_id,node_id,'applying','candidate desired state')
                try:
                    nodes.probe(node_id,timeout=8.0)
                    desired=ensure_node_desired_state(node_id)
                    nodes.sync_desired_state(node_id,desired,legacy_bundles=build_node_bundles(node_id))
                    _stage7_rollout_mark(rollout_id,node_id,'verifying','candidate delivered',desired)
                    check=_stage7_verify_rollout_node(node_id,desired)
                    _stage7_rollout_event(rollout_id,'health_sample','Initial post-apply health sample',
                                          node_id=node_id,state_name='verifying',metrics=check)
                    observation=max(1.0,min(30.0,float(rollout['observation_seconds'] or 5)))
                    deadline=time.monotonic()+observation;check2=check
                    while True:
                        control=_stage7_rollout_control(rollout_id,node_id=node_id)
                        if control=='abort':
                            _stage7_rollout_event(rollout_id,'abort_ack','Abort acknowledged during observation',
                                                  node_id=node_id,state_name='aborting')
                            _stage7_rollback_rollout_nodes(rollout_id,'Aborted by owner',final_state='aborted');return
                        if control=='stop':return
                        remaining=deadline-time.monotonic()
                        if remaining<=0:break
                        time.sleep(min(1.0,remaining))
                        check2=_stage7_verify_rollout_node(node_id,desired)
                        _stage7_rollout_event(rollout_id,'health_sample','Observation health sample',
                                              node_id=node_id,state_name='verifying',metrics=check2)
                    warp_note=''
                    if node_id in warp_nodes and warp_tags:
                        remote=nodes.smart_warp_probe(node_id,warp_tags,attempts=2,timeout_seconds=5)
                        gate=evaluate_warp_safety(remote.get('items',[]),warp_tags,
                            max_loss_percent=float(thresholds.get('maxLossPercent',20)),
                            max_latency_ms=float(thresholds.get('maxLatencyMs',1200)),
                            max_jitter_ms=float(thresholds.get('maxJitterMs',350)))
                        _stage7_rollout_event(rollout_id,'warp_probe','Post-apply WARP verification',
                                              node_id=node_id,state_name='verifying',metrics={
                                                  'passed':gate['passed'],'items':gate.get('items',[]),
                                                  'thresholds':gate.get('thresholds',{})})
                        if not gate['passed']:raise PolicyError('Post-apply WARP verification failed: '+'; '.join(gate['issues']))
                        warp_note='; WARP verified'
                    _stage7_rollout_mark(rollout_id,node_id,'healthy',
                        'observed '+str(int(observation))+'s; '+str(check2.get('latencyMs',0))+'ms'+warp_note,desired,
                        metrics={'observationSeconds':observation,'health':check2,'warpVerified':bool(warp_note)})
                except Exception as ex:
                    _stage7_rollout_mark(rollout_id,node_id,'failed',str(ex))
                    _stage7_rollback_rollout_nodes(rollout_id,'Node '+node_id+' failed: '+str(ex))
                    return
            control=_stage7_rollout_control(rollout_id)
            if control=='abort':
                _stage7_rollout_event(rollout_id,'abort_ack','Abort acknowledged before Hub apply',phase='hub',state_name='aborting')
                _stage7_rollback_rollout_nodes(rollout_id,'Aborted by owner',final_state='aborted');return
            if control=='stop':return
            with store.transaction() as db:
                db.execute("UPDATE smart_routing_rollouts SET phase='hub',detail='All Nodes healthy; applying Hub last' WHERE id=?",(rollout_id,))
            _stage7_rollout_event(rollout_id,'hub_phase','All Nodes healthy; Hub apply starting',phase='hub',state_name='running')
            outbounds=engine.section('outbounds')
            current=stage7_state_hash(outbounds,engine.section('routing'),engine.section('observatory') or {},stage7_node_roles())
            if current!=revision['baseline_hash']:
                _stage7_rollback_rollout_nodes(rollout_id,'Hub baseline changed before final apply');return
            after_routing=json.loads(revision['after_routing']);after_observatory=json.loads(revision['after_observatory'])
            after_roles=json.loads(revision.get('after_node_roles') or '{}')
            candidate=build_stage7_candidate_config(engine.build_config(),
                {'outbounds':outbounds,'routing':after_routing,'observatory':after_observatory})
            engine.validate(candidate)
            def commit():
                with store.transaction() as db:
                    _stage7_write_sections(db,after_routing,after_observatory,after_roles)
                    db.execute('UPDATE smart_routing_revisions SET state=?,detail=?,applied_at=? WHERE id=? AND state=?',
                               ('applied','Staged rollout completed; Hub applied last',time.time(),revision['id'],'reviewed'))
                    db.execute("UPDATE smart_routing_rollout_nodes SET state='completed',updated_at=? WHERE rollout_id=? AND state='healthy'",
                               (time.time(),rollout_id))
                    db.execute("UPDATE smart_routing_rollouts SET state='completed',phase='complete',detail=?,completed_at=? WHERE id=?",
                               ('Canary and batch verified; Hub applied last',time.time(),rollout_id))
            engine.apply_config(candidate,force=True,after_success=commit)
            _stage7_rollout_event(rollout_id,'completed','Canary and Batch healthy; Hub applied last',
                                  phase='complete',state_name='completed',metrics={'nodes':len(rows)})
            try:manager.audit(Actor(rollout['actor'],'owner',{}),rollout['actor'],'smart.routing.rollout.complete',rollout_id,
                              'all nodes verified; hub applied last')
            except Exception:pass
        except Exception as ex:
            try:_stage7_rollout_event(rollout_id,'worker_error',str(ex),phase='rollback',state_name='failed')
            except Exception:pass
            try:_stage7_rollback_rollout_nodes(rollout_id,'Rollout worker failed: '+str(ex))
            except Exception:
                with store.transaction() as db:
                    db.execute("UPDATE smart_routing_rollouts SET state='failed',phase='rollback',detail=? WHERE id=?",
                               (str(ex)[:500],rollout_id))
        finally:
            with stage7_rollout_lock:stage7_rollout_threads.pop(rollout_id,None)

    def _stage7_start_worker(rollout_id:str):
        with stage7_rollout_lock:
            current=stage7_rollout_threads.get(rollout_id)
            if current and current.is_alive():return False
            thread=threading.Thread(target=_stage7_rollout_worker,args=(rollout_id,),
                                    name='stage7-rollout-'+rollout_id[:8],daemon=True)
            stage7_rollout_threads[rollout_id]=thread;thread.start();return True

    @app.post('/api/smart-routing/preview')
    def smart_routing_preview(body:SmartRoutingPreview,p:Principal=Depends(owner)):
        _o,_r,_obs,_before_roles,after_roles,patch,_candidate,baseline_hash,candidate_hash=_stage7_material(body)
        return {k:v for k,v in patch.items() if k!='outbounds'}|{
            'baselineHash':baseline_hash,'candidateHash':candidate_hash,'nodeRoles':after_roles}

    @app.post('/api/smart-routing/validate')
    def smart_routing_validate(body:SmartRoutingPreview,p:Principal=Depends(owner)):
        _o,_r,_obs,_before_roles,after_roles,patch,candidate,baseline_hash,candidate_hash=_stage7_material(body)
        validated=engine.validate(candidate)
        manager.audit(p.actor,p.actor.id,'smart.routing.validate',candidate_hash[:16],
                      'candidate validated only; production traffic unchanged')
        return {'validated':True,'baselineHash':baseline_hash,'candidateHash':candidate_hash,
                'candidateConfigHash':validated['hash'],'warnings':patch.get('warnings',[]),'nodeRoles':after_roles,
                'runtimeMutation':False,'saveMutation':False,'rollbackPrepared':False}

    @app.post('/api/smart-routing/review')
    def smart_routing_review(body:SmartRoutingReview,p:Principal=Depends(owner)):
        writable()
        _o,routing,observatory,before_roles,after_roles,patch,candidate,baseline_hash,candidate_hash=_stage7_material(body)
        if body.confirmation!='REVIEW SMART ROUTING':raise HTTPException(400,'Review confirmation text is invalid')
        if body.baselineHash!=baseline_hash:raise HTTPException(409,'Smart Routing baseline changed; validate again')
        if body.candidateHash!=candidate_hash:raise HTTPException(409,'Smart Routing candidate changed; validate again')
        engine.validate(candidate)
        revision_id=secrets.token_hex(16);now=time.time()
        request={'warpAi':body.warpAi,'adblock':body.adblock,'warpOutboundTags':body.warpOutboundTags,
                 'warpNodeIds':after_roles['warpNodeIds'],'adblockNodeIds':after_roles['adblockNodeIds']}
        with store.transaction() as db:
            db.execute('''INSERT INTO smart_routing_revisions(
                id,actor,created_at,baseline_hash,candidate_hash,before_routing,before_observatory,
                after_routing,after_observatory,before_node_roles,after_node_roles,request_body,state,detail)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (revision_id,p.actor.id,now,baseline_hash,candidate_hash,json.dumps(routing),json.dumps(observatory),
                 json.dumps(patch['routing']),json.dumps(patch.get('observatory') or {}),json.dumps(before_roles),
                 json.dumps(after_roles),json.dumps(request),'reviewed',
                 'Validated and reviewed; no settings or runtime mutation'))
        manager.audit(p.actor,p.actor.id,'smart.routing.review',revision_id,'rollback snapshot prepared; not applied')
        return _stage7_public(_stage7_revision(revision_id))|{'runtimeMutation':False,'saveMutation':False}

    @app.get('/api/smart-routing/revisions')
    def smart_routing_revisions(p:Principal=Depends(owner)):
        with store.lock:rows=[dict(x) for x in store.db.execute(
            'SELECT * FROM smart_routing_revisions ORDER BY created_at DESC LIMIT 30').fetchall()]
        return {'items':[_stage7_public(x) for x in rows]}

    @app.get('/api/smart-routing/rollouts')
    def smart_routing_rollouts(p:Principal=Depends(owner)):
        with store.lock:ids=[r[0] for r in store.db.execute(
            'SELECT id FROM smart_routing_rollouts ORDER BY created_at DESC LIMIT 20').fetchall()]
        return {'items':[_stage7_rollout_public(x,include_timeline=False) for x in ids]}

    @app.get('/api/smart-routing/rollout/{rollout_id}')
    def smart_routing_rollout_get(rollout_id:str,p:Principal=Depends(owner)):
        return _stage7_rollout_public(rollout_id)

    @app.post('/api/smart-routing/rollout/start',status_code=202)
    def smart_routing_rollout_start(body:SmartRoutingRolloutStart,p:Principal=Depends(owner)):
        writable()
        if body.confirmation!='START STAGED ROLLOUT':raise HTTPException(400,'Staged rollout confirmation text is invalid')
        revision=_stage7_revision(body.revisionId)
        if revision['state']!='reviewed':raise HTTPException(409,'Only a reviewed Smart Routing revision can start rollout')
        public=_stage7_public(revision)
        if not public['safetyPassed']:raise HTTPException(409,'Fresh Safety PASS is required before staged rollout')
        outbounds=engine.section('outbounds')
        if stage7_state_hash(outbounds,engine.section('routing'),engine.section('observatory') or {},stage7_node_roles())!=revision['baseline_hash']:
            raise HTTPException(409,'Smart Routing baseline changed after Safety Gate; create a new review')
        before_roles=json.loads(revision.get('before_node_roles') or '{}');after_roles=json.loads(revision.get('after_node_roles') or '{}')
        order=_stage7_rollout_order(before_roles,after_roles);target_ids=[x[0] for x in order]
        readiness=evaluate_stage7_node_readiness(nodes.list(),warp_node_ids=target_ids,adblock_node_ids=[])
        if not readiness['passed']:
            raise HTTPException(409,'Staged rollout target is not ready: '+'; '.join(readiness['issues'])[:600])
        with store.lock:active=store.db.execute("SELECT id FROM smart_routing_rollouts WHERE state='running' LIMIT 1").fetchone()
        if active:raise HTTPException(409,'Another Smart Routing rollout is already running')
        rollout_id=secrets.token_hex(16);now=time.time()
        with store.transaction() as db:
            db.execute('''INSERT INTO smart_routing_rollouts(id,revision_id,actor,created_at,state,phase,current_index,
                          observation_seconds,detail,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (rollout_id,revision['id'],p.actor.id,now,'running','canary',0,body.observationSeconds,
                        'Canary rollout starting',now))
            for idx,(node_id,role) in enumerate(order):
                db.execute('''INSERT INTO smart_routing_rollout_nodes(rollout_id,node_id,ord,role,state,updated_at)
                              VALUES(?,?,?,?,?,?)''',(rollout_id,node_id,idx,role,'pending',now))
        _stage7_rollout_event(rollout_id,'started','Staged rollout created',phase='canary',state_name='running',
                              metrics={'nodes':len(order),'observationSeconds':body.observationSeconds})
        _stage7_start_worker(rollout_id)
        manager.audit(p.actor,p.actor.id,'smart.routing.rollout.start',rollout_id,
                      'nodes='+str(len(order))+'; hub last')
        return _stage7_rollout_public(rollout_id)

    @app.get('/api/smart-routing/rollout/{rollout_id}/timeline')
    def smart_routing_rollout_timeline(rollout_id:str,p:Principal=Depends(owner)):
        return {'rolloutId':rollout_id,'items':_stage7_rollout_public(rollout_id).get('timeline',[])}

    @app.post('/api/smart-routing/rollout/{rollout_id}/pause',status_code=202)
    def smart_routing_rollout_pause(rollout_id:str,body:SmartRoutingRolloutAction,p:Principal=Depends(owner)):
        writable()
        if body.confirmation!='PAUSE STAGED ROLLOUT':raise HTTPException(400,'Pause confirmation text is invalid')
        doc=_stage7_rollout_public(rollout_id,include_timeline=False)
        if doc['state']!='running':raise HTTPException(409,'Only a running rollout can be paused')
        if doc['controlState'] in {'pause_requested','paused'}:return _stage7_rollout_public(rollout_id)
        with store.transaction() as db:
            db.execute("UPDATE smart_routing_rollouts SET control_state='pause_requested',detail=? WHERE id=? AND state='running'",
                       ('Pause requested by owner',rollout_id))
        _stage7_rollout_event(rollout_id,'pause_requested','Pause requested by owner',phase=str(doc.get('phase') or ''),state_name='running')
        manager.audit(p.actor,p.actor.id,'smart.routing.rollout.pause',rollout_id,'pause requested')
        return _stage7_rollout_public(rollout_id)

    @app.post('/api/smart-routing/rollout/{rollout_id}/resume',status_code=202)
    def smart_routing_rollout_resume(rollout_id:str,body:SmartRoutingRolloutAction,p:Principal=Depends(owner)):
        writable()
        if body.confirmation!='RESUME STAGED ROLLOUT':raise HTTPException(400,'Resume confirmation text is invalid')
        doc=_stage7_rollout_public(rollout_id,include_timeline=False)
        if doc['state']!='running':raise HTTPException(409,'Only a running rollout can be resumed')
        with store.transaction() as db:
            db.execute("UPDATE smart_routing_rollouts SET control_state='run',resumed_at=?,detail=? WHERE id=? AND state='running'",
                       (time.time(),'Resumed by owner',rollout_id))
        _stage7_rollout_event(rollout_id,'resume_requested','Resume requested by owner',phase=str(doc.get('phase') or ''),state_name='running')
        _stage7_start_worker(rollout_id)
        manager.audit(p.actor,p.actor.id,'smart.routing.rollout.resume',rollout_id,'resumed')
        return _stage7_rollout_public(rollout_id)

    @app.post('/api/smart-routing/rollout/{rollout_id}/abort',status_code=202)
    def smart_routing_rollout_abort(rollout_id:str,body:SmartRoutingRolloutAction,p:Principal=Depends(owner)):
        writable()
        if body.confirmation!='ABORT STAGED ROLLOUT':raise HTTPException(400,'Abort confirmation text is invalid')
        doc=_stage7_rollout_public(rollout_id,include_timeline=False)
        if doc['state']!='running':raise HTTPException(409,'Only a running rollout can be aborted')
        with store.transaction() as db:
            db.execute("UPDATE smart_routing_rollouts SET control_state='abort_requested',aborted_at=?,detail=? WHERE id=? AND state='running'",
                       (time.time(),'Abort requested by owner',rollout_id))
        _stage7_rollout_event(rollout_id,'abort_requested','Abort requested by owner',phase=str(doc.get('phase') or ''),state_name='aborting')
        _stage7_start_worker(rollout_id)
        manager.audit(p.actor,p.actor.id,'smart.routing.rollout.abort',rollout_id,'abort requested')
        return _stage7_rollout_public(rollout_id)

    @app.post('/api/smart-routing/safety-check')
    def smart_routing_safety_check(body:SmartRoutingSafetyCheck,p:Principal=Depends(owner)):
        writable();row=_stage7_revision(body.revisionId)
        if row['state']!='reviewed':raise HTTPException(409,'Safety Gate requires a reviewed Smart Routing revision')
        request=json.loads(row['request_body']);roles=json.loads(row.get('after_node_roles') or '{}')
        outbounds=engine.section('outbounds');routing=engine.section('routing');observatory=engine.section('observatory') or {}
        current_hash=stage7_state_hash(outbounds,routing,observatory,stage7_node_roles())
        if current_hash!=row['baseline_hash']:
            raise HTTPException(409,'Smart Routing baseline changed; create a new review before Safety Gate')
        issues=[];observations=[];ranked=[]
        if request.get('warpAi') and not roles.get('warpNodeIds'):
            issues.append('Smart WARP AI requires at least one assigned Node')
        if request.get('adblock') and not roles.get('adblockNodeIds'):
            issues.append('Smart Adblock requires at least one assigned Node')
        by_tag={str(o.get('tag','')):o for o in outbounds if isinstance(o,dict)}
        if request.get('adblock'):
            block=by_tag.get('block')
            if not block or str(block.get('protocol','')).lower()!='blackhole':
                issues.append("Smart Adblock requires blackhole outbound 'block'")
        warp_gate={'passed':True,'items':[],'issues':[],'thresholds':{
            'maxLossPercent':body.maxLossPercent,'maxLatencyMs':body.maxLatencyMs,'maxJitterMs':body.maxJitterMs}}
        warp_tags=[str(x) for x in request.get('warpOutboundTags',[]) if str(x)]
        if request.get('warpAi'):
            selected=[]
            for tag in warp_tags:
                outbound=by_tag.get(tag)
                if not outbound or str(outbound.get('protocol','')).lower()!='wireguard':
                    issues.append('Smart WARP path unavailable or not WireGuard: '+tag);continue
                selected.append(outbound)
            if not selected:
                issues.append('Smart WARP AI requires at least one valid WireGuard path')
            else:
                try:
                    observations=scan_warp_outbounds(engine._binary(),config.xray_assets,selected,
                        attempts=body.attempts,timeout=float(body.timeoutSeconds))
                except SmartWarpProbeError as ex:
                    observations=[];issues.append('WARP probe failed: '+str(ex))
                ranked=rank_warp_paths(observations,max_results=len(selected)) if observations else []
                warp_gate=evaluate_warp_safety(ranked,[str(x.get('tag')) for x in selected],
                    max_loss_percent=body.maxLossPercent,max_latency_ms=body.maxLatencyMs,
                    max_jitter_ms=body.maxJitterMs)
                issues.extend(warp_gate['issues'])
        node_gate=evaluate_stage7_node_readiness(nodes.list(),
            warp_node_ids=roles.get('warpNodeIds',[]) if request.get('warpAi') else [],
            adblock_node_ids=roles.get('adblockNodeIds',[]) if request.get('adblock') else [])
        issues.extend(node_gate['issues'])
        latest_hash=stage7_state_hash(engine.section('outbounds'),engine.section('routing'),
            engine.section('observatory') or {},stage7_node_roles())
        if latest_hash!=row['baseline_hash']:
            raise HTTPException(409,'Smart Routing baseline changed during Safety Gate; run review again')
        checked=time.time();passed=not issues
        report={'safetyPassed':passed,'candidateHash':row['candidate_hash'],'checkedAt':checked,
                'expiresAt':checked+STAGE7_SAFETY_TTL_SECONDS,'ttlSeconds':STAGE7_SAFETY_TTL_SECONDS,
                'nodeReadiness':node_gate,'warpSafety':warp_gate,'observations':observations,
                'issues':issues,'productionTrafficMutation':False,'applyMutation':False}
        raw=json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        safety_hash=hashlib.sha256(raw.encode()).hexdigest()
        with store.transaction() as db:
            live=db.execute('SELECT state,candidate_hash FROM smart_routing_revisions WHERE id=?',(body.revisionId,)).fetchone()
            if not live or live['state']!='reviewed' or live['candidate_hash']!=row['candidate_hash']:
                raise CoreError('Smart Routing revision changed during Safety Gate',status=409)
            db.execute('UPDATE smart_routing_revisions SET safety_report=?,safety_hash=?,safety_checked_at=?,safety_passed=? WHERE id=?',
                       (raw,safety_hash,checked,int(passed),body.revisionId))
        manager.audit(p.actor,p.actor.id,'smart.routing.safety',body.revisionId,
                      'PASS' if passed else 'BLOCKED: '+('; '.join(issues)[:500]))
        return _stage7_public(_stage7_revision(body.revisionId))

    @app.post('/api/smart-routing/activate')
    def smart_routing_activate(body:SmartRoutingRevisionAction,p:Principal=Depends(owner)):
        writable()
        if body.confirmation!='APPLY SMART ROUTING':raise HTTPException(400,'Apply confirmation text is invalid')
        row=_stage7_revision(body.revisionId)
        if row['state']!='reviewed':raise HTTPException(409,'Only a reviewed Smart Routing revision can be applied')
        before_routing=json.loads(row['before_routing']);before_observatory=json.loads(row['before_observatory'])
        after_routing=json.loads(row['after_routing']);after_observatory=json.loads(row['after_observatory'])
        before_roles=json.loads(row.get('before_node_roles') or '{}');after_roles=json.loads(row.get('after_node_roles') or '{}')
        if set(before_roles.get('warpNodeIds',[])+before_roles.get('adblockNodeIds',[])+after_roles.get('warpNodeIds',[])+after_roles.get('adblockNodeIds',[])):
            raise HTTPException(409,'Node-targeted Smart Routing must use staged rollout; direct apply is disabled')
        outbounds=engine.section('outbounds')
        if stage7_state_hash(outbounds,engine.section('routing'),engine.section('observatory') or {},stage7_node_roles())!=row['baseline_hash']:
            raise HTTPException(409,'Smart Routing baseline changed after review; create a new review')
        safety=_stage7_public(row)
        if not safety['safetyPassed']:
            raise HTTPException(409,'Smart Routing Safety Gate is missing, blocked, or expired; run Safety Gate again')
        request=json.loads(row['request_body'])
        if request.get('warpAi') and not after_roles.get('warpNodeIds'):
            raise HTTPException(409,'Smart WARP AI has no assigned Node; Safety Gate refused apply')
        if request.get('adblock') and not after_roles.get('adblockNodeIds'):
            raise HTTPException(409,'Smart Adblock has no assigned Node; Safety Gate refused apply')
        live_nodes=evaluate_stage7_node_readiness(nodes.list(),
            warp_node_ids=after_roles.get('warpNodeIds',[]) if request.get('warpAi') else [],
            adblock_node_ids=after_roles.get('adblockNodeIds',[]) if request.get('adblock') else [])
        if not live_nodes['passed']:
            raise HTTPException(409,'Smart Routing Node readiness changed after Safety Gate: '+'; '.join(live_nodes['issues'])[:600])
        patch={'outbounds':outbounds,'routing':after_routing,'observatory':after_observatory}
        candidate=build_stage7_candidate_config(engine.build_config(),patch)
        engine.validate(candidate)
        def commit():
            with store.transaction() as db:
                current=stage7_state_hash(engine.section('outbounds'),engine.section('routing'),engine.section('observatory') or {},stage7_node_roles())
                live=db.execute('SELECT state FROM smart_routing_revisions WHERE id=?',(body.revisionId,)).fetchone()
                if not live or live['state']!='reviewed' or current!=row['baseline_hash']:
                    raise CoreError('Smart Routing review became stale during apply',status=409)
                _stage7_write_sections(db,after_routing,after_observatory,after_roles)
                db.execute('UPDATE smart_routing_revisions SET state=?,detail=?,applied_at=? WHERE id=?',
                           ('applied','Runtime accepted candidate and reviewed settings committed',time.time(),body.revisionId))
        runtime=engine.apply_config(candidate,force=True,after_success=commit)
        desired=[]
        for node in nodes.list():
            node_id=str(node.get('id') or '')
            if not node_id:continue
            try:
                state=ensure_node_desired_state(node_id);desired.append({'nodeId':node_id,'pending':bool(state.get('pending'))})
            except (PolicyError,OSError,ValueError) as ex:
                desired.append({'nodeId':node_id,'pending':True,'error':str(ex)[:200]})
        manager.audit(p.actor,p.actor.id,'smart.routing.apply',body.revisionId,'reviewed candidate applied with rollback snapshot')
        return _stage7_public(_stage7_revision(body.revisionId))|{'runtime':runtime,'applied':True,'nodeDesired':desired}

    @app.post('/api/smart-routing/rollback')
    def smart_routing_rollback(body:SmartRoutingRevisionAction,p:Principal=Depends(owner)):
        writable();row=_stage7_revision(body.revisionId)
        if body.confirmation!='ROLLBACK SMART ROUTING':raise HTTPException(400,'Rollback confirmation text is invalid')
        if row['state']=='reviewed':
            with store.transaction() as db:
                db.execute('UPDATE smart_routing_revisions SET state=?,detail=?,rolled_back_at=? WHERE id=? AND state=?',
                           ('discarded','Reviewed revision discarded before apply',time.time(),body.revisionId,'reviewed'))
            manager.audit(p.actor,p.actor.id,'smart.routing.discard',body.revisionId,'reviewed change discarded; runtime unchanged')
            return _stage7_public(_stage7_revision(body.revisionId))|{'runtimeMutation':False}
        if row['state']!='applied':raise HTTPException(409,'Only an applied or reviewed Smart Routing revision can be rolled back')
        before_routing=json.loads(row['before_routing']);before_observatory=json.loads(row['before_observatory'])
        before_roles=json.loads(row.get('before_node_roles') or '{}')
        outbounds=engine.section('outbounds')
        current_hash=stage7_state_hash(outbounds,engine.section('routing'),engine.section('observatory') or {},stage7_node_roles())
        if current_hash!=row['candidate_hash']:raise HTTPException(409,'Routing or Node roles changed after this revision; automatic rollback refused')
        previous=build_stage7_candidate_config(engine.build_config(),
            {'outbounds':outbounds,'routing':before_routing,'observatory':before_observatory})
        engine.validate(previous)
        def commit():
            with store.transaction() as db:
                current=stage7_state_hash(engine.section('outbounds'),engine.section('routing'),engine.section('observatory') or {},stage7_node_roles())
                live=db.execute('SELECT state FROM smart_routing_revisions WHERE id=?',(body.revisionId,)).fetchone()
                if not live or live['state']!='applied' or current!=row['candidate_hash']:
                    raise CoreError('Smart Routing revision changed during rollback',status=409)
                _stage7_write_sections(db,before_routing,before_observatory,before_roles)
                db.execute('UPDATE smart_routing_revisions SET state=?,detail=?,rolled_back_at=? WHERE id=?',
                           ('rolled_back','Previous routing, Observatory and Node roles restored after runtime validation',time.time(),body.revisionId))
        runtime=engine.apply_config(previous,force=True,after_success=commit)
        desired=[]
        for node in nodes.list():
            node_id=str(node.get('id') or '')
            if not node_id:continue
            try:
                state=ensure_node_desired_state(node_id);desired.append({'nodeId':node_id,'pending':bool(state.get('pending'))})
            except (PolicyError,OSError,ValueError) as ex:
                desired.append({'nodeId':node_id,'pending':True,'error':str(ex)[:200]})
        manager.audit(p.actor,p.actor.id,'smart.routing.rollback',body.revisionId,'previous reviewed snapshot restored')
        return _stage7_public(_stage7_revision(body.revisionId))|{'runtime':runtime,'rolledBack':True,'nodeDesired':desired}

    @app.post('/api/smart-routing/warp-rank')
    def smart_routing_warp_rank(body:SmartWarpRank,p:Principal=Depends(owner)):
        return {'items':rank_warp_paths(body.observations),'previewOnly':True}

    @app.post('/api/smart-routing/warp-scan')
    def smart_routing_warp_scan(body:SmartWarpScan,p:Principal=Depends(owner)):
        outbounds=engine.section('outbounds')
        by_tag={str(o.get('tag','')):o for o in outbounds if isinstance(o,dict)}
        tags=[]
        for raw in body.outboundTags:
            tag=str(raw).strip()
            if not tag or tag in tags:continue
            if tag not in by_tag:raise HTTPException(400,'Unknown WARP outbound tag: '+tag)
            if str(by_tag[tag].get('protocol','')).lower()!='wireguard':
                raise HTTPException(400,'WARP scan requires WireGuard outbound: '+tag)
            tags.append(tag)
        if not tags:raise HTTPException(400,'Select at least one WARP outbound')
        try:
            observations=scan_warp_outbounds(engine._binary(),config.xray_assets,
                [by_tag[tag] for tag in tags],attempts=body.attempts,timeout=float(body.timeoutSeconds))
        except SmartWarpProbeError as ex:
            raise HTTPException(400,str(ex))
        ranked=rank_warp_paths(observations,max_results=len(tags))
        meta={x.get('tag'):x for x in build_stage7_plan([],outbounds,{}).get('warpCandidates',[])
              if isinstance(x,dict) and x.get('tag')}
        for row in ranked:
            info=meta.get(row.get('tag'),{})
            row['node']=info.get('node') or row.get('tag') or '—'
            row['region']=info.get('region') or '—'
        manager.audit(p.actor,p.actor.id,'smart.warp.scan',str(len(tags)),
                      'isolated temporary Xray probes; production traffic unchanged')
        return {'items':ranked,'observations':observations,'previewOnly':True,
                'productionTrafficMutation':False,'selectedTags':tags,
                'scanMode':'isolated-temporary-xray'}

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
            public_url=config.public_origin.rstrip('/')+str(sub.get('path','/sub'))+'/'+public_token
            portal_data={'title':sub.get('profile_title','DARK XRAY'),'client':row['email'],'url':public_url,
                         'used':used,'total':total,'expiry':expiry,
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
        writable();result=engine.save_inbound(body,inbound_id);manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'inbound.update',str(inbound_id));return result

    @app.delete('/api/inbounds/{inbound_id}')
    def delete_inbound(inbound_id:int,p:Principal=Depends(owner)):
        writable();result=engine.delete_inbound(inbound_id)
        with store.transaction() as db:
            for row in db.execute('SELECT id,allowed FROM owner_profiles').fetchall():
                db.execute('UPDATE owner_profiles SET allowed=? WHERE id=?',(json.dumps([i for i in json.loads(row['allowed']) if i!=inbound_id]),row['id']))
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
        if section=='routing':
            tags={str(r.get('ruleTag')) for r in body['value'].get('rules',[]) if isinstance(r,dict) and r.get('ruleTag')}
            with store.transaction() as db:
                for row in db.execute('SELECT rule_tag FROM routing_rule_scopes').fetchall():
                    if str(row['rule_tag']) not in tags:db.execute('DELETE FROM routing_rule_scopes WHERE rule_tag=?',(row['rule_tag'],))
        if section in {'outbounds','routing','dns','policy','observatory','hosts','ipguard'}:manager.tick(suppress=True)
        if section in {'outbounds','routing','dns','policy','observatory','ipguard'}:
            for node in nodes.list():
                try:ensure_node_desired_state(str(node['id']))
                except Exception:pass
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

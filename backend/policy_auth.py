#!/usr/bin/env python3
"""Authenticated development API for DARK policy + accounting, NOT the full panel.

Loopback only by default. No CORS, no cookies, no eval/shell or arbitrary file APIs.
It does not run Xray, collect Xray traffic, synchronize nodes or serve VPN subscriptions.
Use dark_policy.py separately for explicitly approved Fail2ban integration.
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import psutil
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from dark_policy import Actor, MAX_INT, NAME_RE, PermissionDenied, PolicyError, Store

CAPABILITIES={
 'auth':'password + expiring revocable bearer sessions',
 'ownership':'server-side per-client ownership and explicit permission scopes',
 'ledger':'atomic immutable traffic / financial entries + idempotency',
 'ip':'read IP observations from the separate guard database',
 'system':'actual OS metrics on THIS API host',
 'xray':False,'subscriptions':False,'node_sync':False,'frontend_api_binding':False,
 'production_ready':False,
}
ALLOWED_PERMISSIONS={
 'clients.read','clients.create','clients.edit','clients.delete','clients.reset',
 'owners.read','owners.edit','owners.reset','finance.read','finance.credit','finance.refund',
 'ip.read','system.read',
}
DEFAULT_PERMISSIONS={
 'reseller':{f'clients.{p}':'own' for p in ('read','create','edit','delete','reset')}|{'owners.read':'own','finance.read':'own','ip.read':'own'},
 'readonly':{'clients.read':'all','owners.read':'all','finance.read':'all','ip.read':'all'},
 'owner':{},
}

PASSWORD_MIN_LENGTH=8
PASSWORD_MAX_LENGTH=512

def password_hash(password: str, salt: bytes | None=None) -> str:
    if not isinstance(password,str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise PolicyError(f'Password must be between {PASSWORD_MIN_LENGTH} and {PASSWORD_MAX_LENGTH} characters')
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return f'scrypt$16384$8$1${salt.hex()}${digest.hex()}'

def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password,str) or len(password)>PASSWORD_MAX_LENGTH:return False
    try:
        typ,n,r,p,salt,target=encoded.split('$')
        if (typ,n,r,p)!=('scrypt','16384','8','1'):return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
        return hmac.compare_digest(actual,bytes.fromhex(target))
    except (ValueError,TypeError):return False

def permissions_for(role: str, overrides: dict[str,str] | None) -> dict[str,str]:
    result=dict(DEFAULT_PERMISSIONS[role] if overrides is None else overrides)
    if any(k not in ALLOWED_PERMISSIONS or v not in {'none','own','all'} for k,v in result.items()):
        raise PolicyError('Unrecognized permission or scope')
    return result

def create_admin(store: Store, actor: Actor, username: str, password: str,
                 role: str='reseller', permissions: dict[str,str] | None=None) -> None:
    if actor.role!='owner':raise PermissionDenied('Only the owner can create administrators')
    if not NAME_RE.fullmatch(username) or role not in DEFAULT_PERMISSIONS:raise PolicyError('Invalid admin identity or role')
    perms=permissions_for(role,permissions);hashed=password_hash(password)
    with store.transaction() as db:
        if role=='reseller' and not db.execute('SELECT id FROM owners WHERE id=?',(username,)).fetchone():
            raise PolicyError('Create the matching reseller owner record before its login')
        db.execute('INSERT INTO api_admins(id,role,password_hash,permissions) VALUES(?,?,?,?)',(username,role,hashed,json.dumps(perms)))

def bootstrap(store: Store, username: str, password: str) -> None:
    hashed=password_hash(password)
    if not NAME_RE.fullmatch(username):raise PolicyError('Invalid username')
    with store.transaction() as db:
        if db.execute('SELECT 1 FROM api_admins').fetchone():raise PolicyError('Already initialized; no default account was overwritten')
        db.execute('INSERT INTO api_admins(id,role,password_hash,permissions) VALUES(?,?,?,?)',(username,'owner',hashed,'{}'))

class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class Login(StrictModel):
    username: str=Field(min_length=1,max_length=128)
    password: str=Field(min_length=1,max_length=PASSWORD_MAX_LENGTH)

class AdminCreate(Login):
    role: Literal['reseller','readonly','owner']='reseller'
    permissions: dict[str,str]|None=None

class AdminEdit(StrictModel):
    disabled: bool|None=None
    password: str|None=Field(default=None,min_length=PASSWORD_MIN_LENGTH,max_length=PASSWORD_MAX_LENGTH)
    permissions: dict[str,str]|None=None

class OwnerEdit(StrictModel):
    quota_bytes: StrictInt=Field(default=0,ge=0,le=MAX_INT)
    max_clients: StrictInt=Field(default=0,ge=0,le=1000000)
    manual: bool|None=None

class ClientCreate(StrictModel):
    id: str=Field(min_length=1,max_length=128)
    owner: str=Field(min_length=1,max_length=128)
    limit_ip: StrictInt=Field(default=1,ge=0,le=1000)
    quota_bytes: StrictInt=Field(default=0,ge=0,le=MAX_INT)
    price: StrictInt=Field(default=0,ge=0,le=MAX_INT)
    order_id: str|None=Field(default=None,min_length=1,max_length=256)

class ClientEdit(StrictModel):
    limit_ip: StrictInt|None=Field(default=None,ge=0,le=1000)
    quota_bytes: StrictInt|None=Field(default=None,ge=0,le=MAX_INT)
    manual: bool|None=None
    expires_at: StrictInt|None=Field(default=None,ge=0,le=MAX_INT)

class Credit(StrictModel):
    amount: StrictInt=Field(ge=1,le=MAX_INT)
    event_id: str=Field(min_length=1,max_length=256)

class Usage(StrictModel):
    event_id: str=Field(min_length=1,max_length=256)
    client_id: str=Field(min_length=1,max_length=128)
    up_bytes: StrictInt=Field(ge=0,le=MAX_INT)
    down_bytes: StrictInt=Field(ge=0,le=MAX_INT)

class Refund(StrictModel):
    order_id: str=Field(min_length=1,max_length=256)
    event_id: str=Field(min_length=1,max_length=256)


"""Server-side sessions, optional TOTP and scoped per-administrator robot keys."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from cryptography.fernet import Fernet
from dark_policy import Actor,Store,PolicyError,PermissionDenied,NAME_RE
from policy_api import password_hash,verify_password

PERMISSIONS={
 'clients.read','clients.create','clients.edit','clients.delete','clients.reset','clients.credentials',
 'clients.ip','clients.attach','owners.read','owners.edit','owners.reset','finance.read','finance.credit',
 'finance.refund','ip.read','system.read','audit.read','api.manage','inbounds.read'}
DEFAULTS={
 'owner':{},
 'reseller':{k:'own' for k in ('clients.read','clients.create','clients.edit','clients.delete','clients.reset',
    'clients.credentials','clients.ip','clients.attach','owners.read','finance.read','ip.read','audit.read','api.manage','inbounds.read')},
 'readonly':{'clients.read':'all','owners.read':'all','inbounds.read':'all','system.read':'all','audit.read':'all'} }


def digest(value: str)->str:return hashlib.sha256(value.encode()).hexdigest()

def totp(secret: str,step: int)->str:
    key=base64.b32decode(secret+'='*((8-len(secret)%8)%8))
    mac=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest();offset=mac[-1]&15
    return str((struct.unpack('>I',mac[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)


def valid_permissions(role: str,values: dict|None)->dict:
    if role not in DEFAULTS:raise PolicyError('Unknown role')
    if role=='owner':return {}
    p=DEFAULTS[role].copy() if values is None else values.copy()
    if any(k not in PERMISSIONS or v not in ('none','own','all') for k,v in p.items()):raise PolicyError('Invalid permission/scope')
    return p


@dataclass
class Principal:
    actor: Actor
    session_id: str|None=None
    csrf: str=''
    key_id: str|None=None

class Auth:
    def __init__(self,store: Store,key_path: Path):
        self.store=store
        key_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if key_path.is_symlink():raise PolicyError('Encryption key symlink refused')
        if not key_path.exists():
            import os
            fd=os.open(key_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
        if key_path.stat().st_mode&0o077:raise PolicyError('Encryption key must have mode 600')
        self.cipher=Fernet(key_path.read_bytes())
        self.dummy=password_hash('not-a-real-account-password')
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS live_sessions(digest TEXT PRIMARY KEY,admin_id TEXT NOT NULL,
              csrf TEXT NOT NULL,expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS robot_keys(id TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,
              admin_id TEXT NOT NULL,name TEXT NOT NULL,permissions TEXT NOT NULL,
              expires_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS mfa(admin_id TEXT PRIMARY KEY,secret TEXT NOT NULL,
              pending TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 0,
              last_step INTEGER NOT NULL DEFAULT -1,recovery TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT PRIMARY KEY,start REAL NOT NULL,count INTEGER NOT NULL);
            ''')

    def bootstrap(self,username: str,password: str):
        if not NAME_RE.fullmatch(username):raise PolicyError('Invalid username')
        hashed=password_hash(password)
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM api_admins').fetchone():raise PolicyError('An admin already exists; bootstrap refused')
            db.execute('INSERT INTO api_admins VALUES(?,?,?,?,0)',(username,'owner',hashed,'{}'))
            db.execute('INSERT OR IGNORE INTO owners(id) VALUES(?)',(username,))
            db.execute("INSERT INTO owner_profiles(id,name) VALUES(?,?)",(username,'DARK OWNER'))

    def login(self,username: str,password: str,otp: str,source: str)->tuple[str,Principal]:
        now=time.time();bucket=digest(source)
        with self.store.transaction() as db:
            r=db.execute('SELECT * FROM auth_attempts WHERE bucket=?',(bucket,)).fetchone()
            if r and r['start']>now-300 and r['count']>=8:raise PermissionDenied('Too many login attempts; retry after five minutes')
            count=r['count']+1 if r and r['start']>now-300 else 1
            start=r['start'] if r and r['start']>now-300 else now
            db.execute('INSERT INTO auth_attempts VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET start=excluded.start,count=excluded.count',(bucket,start,count))
            row=db.execute('SELECT * FROM api_admins WHERE id=?',(username,)).fetchone()
        if not verify_password(password,row['password_hash'] if row else self.dummy) or not row or row['disabled']:
            raise PermissionDenied('Invalid credentials')
        with self.store.transaction() as db:
            fresh=db.execute('SELECT * FROM api_admins WHERE id=?',(username,)).fetchone()
            if not fresh or fresh['disabled'] or fresh['password_hash']!=row['password_hash']:raise PermissionDenied('Credentials changed')
            mfa=db.execute('SELECT * FROM mfa WHERE admin_id=?',(username,)).fetchone()
            if mfa and mfa['enabled']:self._verify_mfa(db,mfa,otp,consume=True)
            token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32)
            db.execute('DELETE FROM live_sessions WHERE expires_at<=?',(now,))
            db.execute('INSERT INTO live_sessions VALUES(?,?,?,?)',(digest(token),username,csrf,now+8*3600))
            db.execute('DELETE FROM auth_attempts WHERE bucket=?',(bucket,))
        return token,Principal(Actor(row['id'],row['role'],json.loads(row['permissions'])),digest(token),csrf)

    def current(self,cookie: str|None,authorization: str|None)->Principal:
        with self.store.lock:
            if authorization:
                if not authorization.startswith('Bearer dkr_') or len(authorization)>512:raise PermissionDenied('Invalid API key')
                r=self.store.db.execute('''SELECT k.*,a.role,a.permissions AS admin_permissions,a.disabled FROM robot_keys k
                    JOIN api_admins a ON a.id=k.admin_id WHERE k.digest=? AND k.revoked=0 AND k.expires_at>?''',
                    (digest(authorization[7:]),time.time())).fetchone()
                if not r or r['disabled']:raise PermissionDenied('API key expired or revoked')
                admin=Actor(r['admin_id'],r['role'],json.loads(r['admin_permissions']))
                requested=json.loads(r['permissions']);effective={}
                for key,val in requested.items():
                    if val=='none':continue
                    grant='all' if admin.role=='owner' else admin.permissions.get(key,'none')
                    if grant=='none':continue
                    effective[key]='own' if 'own' in (grant,val) else 'all'
                # A robot key is NEVER an owner bypass; explicit permissions apply.
                return Principal(Actor(admin.id,'token',effective),key_id=r['id'])
            if not cookie or len(cookie)>256:raise PermissionDenied('Authentication required')
            r=self.store.db.execute('''SELECT a.*,s.csrf FROM live_sessions s JOIN api_admins a ON a.id=s.admin_id
                WHERE s.digest=? AND s.expires_at>? AND a.disabled=0''',(digest(cookie),time.time())).fetchone()
            if not r:raise PermissionDenied('Session expired or revoked')
        return Principal(Actor(r['id'],r['role'],json.loads(r['permissions'])),digest(cookie),r['csrf'])

    def admin_create(self,actor: Actor,username: str,password: str,role: str,permissions: dict|None):
        if actor.role!='owner':raise PermissionDenied('Owner required')
        if not NAME_RE.fullmatch(username):raise PolicyError('Invalid username')
        perms=valid_permissions(role,permissions);hashed=password_hash(password)
        with self.store.transaction() as db:
            if role=='reseller' and not db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(username,)).fetchone():
                raise PolicyError('Create a matching reseller profile first')
            db.execute('INSERT INTO api_admins VALUES(?,?,?,?,0)',(username,role,hashed,json.dumps(perms)))

    def admin_edit(self,actor: Actor,username: str,*,disabled: bool|None=None,password: str|None=None,permissions: dict|None=None):
        if actor.role!='owner':raise PermissionDenied('Owner required')
        hashed=password_hash(password) if password else None
        with self.store.transaction() as db:
            r=db.execute('SELECT * FROM api_admins WHERE id=?',(username,)).fetchone()
            if not r:raise PolicyError('Admin not found')
            if disabled and r['role']=='owner' and db.execute("SELECT COUNT(*) FROM api_admins WHERE role='owner' AND disabled=0").fetchone()[0]<=1:
                raise PolicyError('Cannot disable the last owner')
            if permissions is not None:db.execute('UPDATE api_admins SET permissions=? WHERE id=?',(json.dumps(valid_permissions(r['role'],permissions)),username))
            if disabled is not None:
                db.execute('UPDATE api_admins SET disabled=? WHERE id=?',(int(disabled),username))
                db.execute('UPDATE owners SET account_disabled=? WHERE id=?',(int(disabled),username))
            if hashed:db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,username))
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(username,))

    def change_password(self,p: Principal,old: str,new: str):
        if p.key_id:raise PermissionDenied('Interactive session required')
        with self.store.lock:r=self.store.db.execute('SELECT password_hash FROM api_admins WHERE id=?',(p.actor.id,)).fetchone()
        if not r or not verify_password(old,r[0]):raise PermissionDenied('Current password incorrect')
        hashed=password_hash(new)
        with self.store.transaction() as db:
            db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,p.actor.id))
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(p.actor.id,))

    def new_key(self,p: Principal,name: str,permissions: dict,days: int)->tuple[str,str]:
        p.actor.require('api','manage',p.actor.id)
        if p.key_id:raise PermissionDenied('Robot keys cannot mint other keys')
        if not 1<=days<=365 or not 1<=len(name)<=128:raise PolicyError('Invalid key metadata')
        if any(k not in PERMISSIONS or v not in ('none','own','all') for k,v in permissions.items()):raise PolicyError('Invalid key permissions')
        for k,v in permissions.items():
            grant='all' if p.actor.role=='owner' else p.actor.permissions.get(k,'none')
            if v!='none' and (grant=='none' or grant=='own' and v=='all'):raise PermissionDenied('A key cannot exceed its administrator permissions')
        key='dkr_'+secrets.token_urlsafe(40);kid=secrets.token_hex(10)
        with self.store.transaction() as db:
            db.execute('INSERT INTO robot_keys VALUES(?,?,?,?,?,?,0,?)',
                (kid,digest(key),p.actor.id,name,json.dumps(permissions),time.time()+days*86400,time.time()))
        return kid,key

    def _verify_mfa(self,db,row,code: str,*,consume: bool):
        secret=self.cipher.decrypt(row['secret'].encode()).decode()
        step=int(time.time()//30)
        for counter in (step-1,step,step+1):
            if counter>row['last_step'] and hmac.compare_digest(totp(secret,counter),code):
                if consume:db.execute('UPDATE mfa SET last_step=? WHERE admin_id=?',(counter,row['admin_id']))
                return
        codes=json.loads(row['recovery']);value=digest(code)
        if value in codes:
            if consume:
                codes.remove(value);db.execute('UPDATE mfa SET recovery=? WHERE admin_id=?',(json.dumps(codes),row['admin_id']))
            return
        raise PermissionDenied('Invalid or already-used two-factor code')

    def mfa_setup(self,p: Principal,password: str)->str:
        if p.key_id:raise PermissionDenied('Interactive session required')
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM api_admins WHERE id=?',(p.actor.id,)).fetchone()
            old=self.store.db.execute('SELECT enabled FROM mfa WHERE admin_id=?',(p.actor.id,)).fetchone()
        if not verify_password(password,row['password_hash']):raise PermissionDenied('Password incorrect')
        if old and old['enabled']:raise PolicyError('Disable existing TOTP before replacing its secret')
        secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=');encrypted=self.cipher.encrypt(secret.encode()).decode()
        with self.store.transaction() as db:
            db.execute("INSERT INTO mfa(admin_id,secret,pending) VALUES(?,?,?) ON CONFLICT(admin_id) DO UPDATE SET pending=excluded.pending",(p.actor.id,encrypted,encrypted))
        return secret

    def mfa_enable(self,p: Principal,code: str)->list[str]:
        if p.key_id:raise PermissionDenied('Interactive session required')
        with self.store.transaction() as db:
            r=db.execute('SELECT * FROM mfa WHERE admin_id=?',(p.actor.id,)).fetchone()
            if not r or not r['pending'] or r['enabled']:raise PolicyError('No pending TOTP enrollment')
            secret=self.cipher.decrypt(r['pending'].encode()).decode();step=int(time.time()//30)
            if not any(hmac.compare_digest(totp(secret,s),code) for s in (step-1,step,step+1)):
                raise PermissionDenied('Invalid enrollment code')
            codes=[secrets.token_hex(8) for _ in range(8)]
            db.execute("UPDATE mfa SET secret=pending,pending='',enabled=1,last_step=?,recovery=? WHERE admin_id=?",(step,json.dumps([digest(c) for c in codes]),p.actor.id))
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(p.actor.id,))
        return codes

    def mfa_disable(self,p: Principal,password: str,code: str):
        if p.key_id:raise PermissionDenied('Interactive session required')
        with self.store.lock:r=self.store.db.execute('SELECT password_hash FROM api_admins WHERE id=?',(p.actor.id,)).fetchone()
        if not verify_password(password,r[0]):raise PermissionDenied('Password incorrect')
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM mfa WHERE admin_id=?',(p.actor.id,)).fetchone()
            if not row or not row['enabled']:raise PolicyError('TOTP is not enabled')
            self._verify_mfa(db,row,code,consume=True)
            db.execute('DELETE FROM mfa WHERE admin_id=?',(p.actor.id,))
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(p.actor.id,))

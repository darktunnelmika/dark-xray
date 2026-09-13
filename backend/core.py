"""DARK XRAY standalone storage and direct Xray-core process supervisor.

No other panel, HTTP adapter, external panel token, or remote UI is used.
The Xray binary is administrator-selected. All process calls use argument lists,
never a shell. DB writes are distinct from applied data-plane generations.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlencode, quote
import base64
from functools import wraps
import copy
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
import psutil
from dark_policy import Store, PolicyError, Policy, Guard, parse_access_line, render_fail2ban

EMAIL_RE = re.compile(r'^[A-Za-z0-9_.@+-]{1,128}$')
SUB_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
TAG_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')
PROTOCOLS = {'vless','vmess','trojan','shadowsocks','socks','http','dokodemo-door','tunnel'}
SECTIONS = {'outbounds','routing','dns','policy','observatory','hosts','panel','runtime','subscription','ipguard'}

class CoreError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool=False, status: int=422):
        super().__init__(message)
        self.uncertain, self.status = uncertain, status

@dataclass
class Config:
    public_origin: str = 'http://127.0.0.1:2087'
    xray_binary: str = '/usr/local/lib/dark-xray/xray'
    xray_assets: str = '/usr/local/lib/dark-xray'
    xray_api_port: int = 10085
    public_address: str = '127.0.0.1'
    writes_enabled: bool = True
    secure_cookie: bool = False
    poll_seconds: int = 5
    core_autostart: bool = False
    ip_window_seconds: int = 120
    direct_source_verified: bool = False
    protected_ports: list[int] = field(default_factory=lambda:[22,2087,10085])
    test_engine: bool = False
    bind_host: str = '127.0.0.1'
    bind_port: int = 2087
    tls_certificate: str = ''
    tls_private_key: str = ''
    guard_socket: str = '/run/dark-xray-guard/control.sock'
    ip_ban_seconds: int = 1800
    ip_exempt_ips: list[str] = field(default_factory=list)

    def __post_init__(self):
        p=urlsplit(self.public_origin)
        if p.scheme not in ('http','https') or not p.hostname or p.path not in ('','/') or p.query or p.fragment or p.username or p.password:
            raise ValueError('public_origin must be an origin without credentials or path')
        port=p.port or (443 if p.scheme=='https' else 80)
        if not 1<=port<=65535: raise ValueError('Invalid panel port')
        self.public_origin=self.public_origin.rstrip('/')
        if p.scheme=='https' and not self.secure_cookie: raise ValueError('HTTPS requires secure_cookie=true')
        if p.scheme=='http':
            try: local=ipaddress.ip_address(p.hostname).is_loopback
            except ValueError: local=p.hostname=='localhost'
            if not local: raise ValueError('Plain HTTP is only allowed on loopback')
        for key in ('writes_enabled','secure_cookie','core_autostart','direct_source_verified','test_engine'):
            if type(getattr(self,key)) is not bool: raise ValueError(key+' must be boolean')
        for key,low,high in [('poll_seconds',1,3600),('xray_api_port',1024,65535),('ip_window_seconds',10,3600),('ip_ban_seconds',10,86400),('bind_port',1024,65535)]:
            if type(getattr(self,key)) is not int or not low<=getattr(self,key)<=high: raise ValueError('Invalid '+key)
        if not Path(self.xray_binary).is_absolute() or not Path(self.xray_assets).is_absolute():
            raise ValueError('Core binary and asset paths must be absolute')
        if not self.public_address or any(c in self.public_address for c in '/?#@ \r\n'):
            raise ValueError('public_address must be an IP or DNS name without URL/port')
        if not isinstance(self.protected_ports,list) or any(type(x)is not int or not 1<=x<=65535 for x in self.protected_ports):
            raise ValueError('Invalid protected ports')
        self.protected_ports=sorted(set(self.protected_ports+[22,port,self.bind_port,self.xray_api_port]))
        try: ipaddress.ip_address(self.bind_host)
        except ValueError: raise ValueError('bind_host must be an IP literal')
        if bool(self.tls_certificate) != bool(self.tls_private_key):
            raise ValueError('Both TLS certificate and private key are required')
        for path in (self.tls_certificate,self.tls_private_key):
            if path and not Path(path).is_absolute(): raise ValueError('TLS paths must be absolute')
        if self.tls_certificate and p.scheme != 'https': raise ValueError('TLS listener requires HTTPS public_origin')
        if not Path(self.guard_socket).is_absolute(): raise ValueError('Guard socket must be absolute')
        if not isinstance(self.ip_exempt_ips,list) or len(self.ip_exempt_ips)>512: raise ValueError('Invalid IP exemptions')
        for item in self.ip_exempt_ips: ipaddress.ip_network(item,strict=False)

    @classmethod
    def load(cls,path:Path)->'Config':
        if path.is_symlink(): raise ValueError('Configuration symlinks refused')
        info=path.stat()
        if info.st_mode&0o027 or info.st_uid not in (0,os.geteuid()):
            raise ValueError('Use private config 0600, or root-owned 0640 for the service group')
        return cls(**json.loads(path.read_text()))  # unknown legacy upstream fields fail closed

def serialized(fn):
    @wraps(fn)
    def wrapped(self,*args,**kwargs):
        with self.lock:
            return fn(self,*args,**kwargs)
    return wrapped

class CoreEngine:
    def __init__(self,config:Config,store:Store,runtime:Path):
        self.config,self.store=config,store
        self.runtime=runtime.resolve();self.runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=threading.RLock();self.process:subprocess.Popen|None=None;self.log_handle=None
        self.version='';self.last_error='';self.stats_error='';self.applied_hash=''
        self.wants_running=config.core_autostart;self.last_samples:dict[str,tuple[int,int]]={}
        self.last_stats=0.;self.last_apply=0.;self.last_start=0.
        self._access_inode=None;self._access_position=0;self._access_fragment=''
        access=self.runtime/'access.log'
        if access.exists():
            info=access.stat();self._access_inode=(info.st_dev,info.st_ino);self._access_position=info.st_size
        self._net_sample=None
        self.ip_error=''
        self._guard_status={'state':'pending','requested_mode':'observe','applied':False,'checked_at':0}
        self._guard_boot=''
        self._guard_executor=None
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS core_inbounds(id INTEGER PRIMARY KEY AUTOINCREMENT,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS core_clients(email TEXT PRIMARY KEY,body TEXT NOT NULL,inbounds TEXT NOT NULL,
              up INTEGER NOT NULL DEFAULT 0,down INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS core_sections(name TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS core_devices(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,
              digest TEXT NOT NULL,device_os TEXT NOT NULL,model TEXT NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,
              UNIQUE(email,digest));
            ''')
        self.validate_schema_only=True

    def _write(self):
        if not self.config.writes_enabled: raise CoreError('Local writes disabled',status=409)

    def _binary(self)->str:
        path=Path(self.config.xray_binary)
        if not path.is_file() or not os.access(path,os.X_OK):
            raise CoreError('Xray-core executable is missing. Configure/install the core itself; no panel installation is needed.',status=503)
        return str(path)

    def version_info(self)->str:
        result=subprocess.run([self._binary(),'version'],capture_output=True,text=True,timeout=10,check=False)
        if result.returncode or not result.stdout.lstrip().startswith('Xray '): raise CoreError('Invalid Xray version response')
        self.version=result.stdout.splitlines()[0][:250];return self.version

    def section(self,name:str):
        if name not in SECTIONS: raise CoreError('Unknown settings section',status=404)
        origin=urlsplit(self.config.public_origin);access_mode='domain_tls' if origin.scheme=='https' else 'ssh'
        defaults={'outbounds':[{'tag':'direct','protocol':'freedom','settings':{}},{'tag':'block','protocol':'blackhole','settings':{}}],
                  'routing':{'domainStrategy':'AsIs','rules':[]},'dns':{'servers':['1.1.1.1']},'policy':{},
                  'observatory':{},'hosts':[],
                  'panel':{'title':'DARK XRAY','support_url':'','language':'en','timezone':'UTC','page_size':50,
                           'session_max_age_minutes':480,'datepicker':'gregorian','density':'comfortable','reduced_motion':False},
                  'runtime':{'access_mode':access_mode,'bind_port':self.config.bind_port,'public_address':self.config.public_address,
                             'poll_seconds':self.config.poll_seconds,'core_autostart':self.config.core_autostart,
                             'domain':(origin.hostname or '') if access_mode=='domain_tls' else '','acme_email':''},
                  'subscription':{'enabled':True,'default_format':'base64','auto_detect':True,'profile_update_interval_hours':6,
                                  'remark_template':'{remark} | {email}','support_url':'','profile_title':'DARK XRAY',
                                  'profile_url':'','announce':''},
                  'ipguard':{'mode':'observe','window_seconds':self.config.ip_window_seconds,
                             'ban_seconds':self.config.ip_ban_seconds,'exempt_ips':self.config.ip_exempt_ips}}
        with self.store.lock:r=self.store.db.execute('SELECT body FROM core_sections WHERE name=?',(name,)).fetchone()
        if not r:return copy.deepcopy(defaults[name])
        saved=json.loads(r[0])
        if name in {'panel','runtime','subscription','ipguard'} and isinstance(saved,dict):
            base=copy.deepcopy(defaults[name]);base.update(saved);return base
        return saved

    @serialized
    def save_section(self,name:str,value:Any):
        self._write()
        if name not in SECTIONS: raise CoreError('Unknown settings section',status=404)
        if not isinstance(value,list if name in ('hosts','outbounds') else dict): raise CoreError('Wrong settings shape')
        if len(json.dumps(value))>500000: raise CoreError('Settings too large')
        if name=='panel':
            allowed={'title','support_url','language','timezone','page_size','session_max_age_minutes','datepicker','density','reduced_motion'}
            if set(value)!=allowed:raise CoreError('Panel settings shape is incomplete or contains unknown fields')
            if not isinstance(value['title'],str) or not 1<=len(value['title'])<=80:raise CoreError('Invalid panel title')
            support=value['support_url']
            if not isinstance(support,str) or len(support)>500:raise CoreError('Invalid support URL')
            if support:
                u=urlsplit(support)
                if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Support URL must be http/https without credentials')
            if value['language'] not in ('en','fa'):raise CoreError('Unsupported panel language')
            if value['datepicker'] not in ('gregorian','jalalian'):raise CoreError('Unsupported calendar')
            if value['density'] not in ('comfortable','compact') or type(value['reduced_motion']) is not bool:raise CoreError('Invalid appearance settings')
            for key,low,high in [('page_size',10,1000),('session_max_age_minutes',60,525600)]:
                if type(value[key]) is not int or not low<=value[key]<=high:raise CoreError('Invalid '+key)
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(value['timezone'])
            except Exception:raise CoreError('Invalid IANA timezone')
        if name=='runtime':
            allowed={'access_mode','bind_port','public_address','poll_seconds','core_autostart','domain','acme_email'}
            if set(value)!=allowed:raise CoreError('Runtime settings shape is incomplete or contains unknown fields')
            if value['access_mode'] not in ('ssh','domain_tls'):raise CoreError('Invalid access mode')
            for key,low,high in [('bind_port',1024,65535),('poll_seconds',1,3600)]:
                if type(value[key]) is not int or not low<=value[key]<=high:raise CoreError('Invalid '+key)
            if type(value['core_autostart']) is not bool:raise CoreError('core_autostart must be boolean')
            addr=value['public_address']
            if not isinstance(addr,str) or not addr or len(addr)>253 or any(c in addr for c in '/?#@ \r\n\t'):raise CoreError('Invalid public proxy address')
            reserved=(set(self.config.protected_ports)-{self.config.bind_port})|{22,self.config.xray_api_port}
            if value['bind_port'] in reserved or any(i['port']==value['bind_port'] for i in self.inbounds()):raise CoreError('Panel port collides with a protected or data port')
            domain=value['domain'];email=value['acme_email']
            if not isinstance(domain,str) or len(domain)>253 or not isinstance(email,str) or len(email)>254:raise CoreError('Invalid domain/ACME values')
            if value['access_mode']=='domain_tls':
                if not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}',domain):raise CoreError('Domain + TLS mode requires a valid ASCII domain')
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise CoreError('Domain + TLS mode requires a valid ACME email')
            elif domain or email:raise CoreError('Domain and ACME email must be empty in SSH mode')
        if name=='subscription':
            allowed={'enabled','default_format','auto_detect','profile_update_interval_hours','remark_template','support_url','profile_title','profile_url','announce'}
            if set(value)!=allowed:raise CoreError('Subscription settings shape is incomplete or contains unknown fields')
            if type(value['enabled']) is not bool or type(value['auto_detect']) is not bool or value['default_format'] not in ('raw','base64','json','clash'):raise CoreError('Invalid subscription mode')
            if type(value['profile_update_interval_hours']) is not int or not 1<=value['profile_update_interval_hours']<=168:raise CoreError('Invalid subscription update interval')
            tmpl=value['remark_template']
            if not isinstance(tmpl,str) or not 1<=len(tmpl)<=200:raise CoreError('Invalid remark template')
            if any(x not in {'remark','email','protocol'} for x in re.findall(r'{([^{}]+)}',tmpl)):raise CoreError('Unknown remark-template variable')
            for key in ('support_url','profile_url'):
                val=value[key]
                if not isinstance(val,str) or len(val)>500:raise CoreError('Invalid subscription URL')
                if val:
                    u=urlsplit(val)
                    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Subscription URL must be http/https without credentials')
                    try:val.encode('ascii')
                    except UnicodeEncodeError:raise CoreError('Subscription URL must be ASCII/punycode')
            if not isinstance(value['profile_title'],str) or not 1<=len(value['profile_title'])<=120:raise CoreError('Invalid subscription profile title')
            if not isinstance(value['announce'],str) or len(value['announce'])>2000:raise CoreError('Invalid subscription announcement')
        if name=='ipguard':
            if set(value)-{'mode','window_seconds','ban_seconds','exempt_ips'}: raise CoreError('Unknown IP Guard setting')
            if value.get('mode') not in ('observe','enforce'): raise CoreError('Invalid IP Guard mode')
            for key,low,high in [('window_seconds',10,3600),('ban_seconds',10,86400)]:
                if type(value.get(key))is not int or not low<=value[key]<=high: raise CoreError('Invalid '+key)
            exempt=value.get('exempt_ips',[])
            if not isinstance(exempt,list) or len(exempt)>512: raise CoreError('Invalid exemption list')
            try:
                for item in exempt: ipaddress.ip_network(item,strict=False)
            except (ValueError,TypeError): raise CoreError('Invalid exempt CIDR')
            if value['mode']=='enforce' and not self.config.direct_source_verified:
                raise CoreError('Administrator must verify DIRECT packet sources in root-owned configuration before enforcement')
            # Disabling is not successful until existing DARK address bans are released.
            if value['mode']=='observe' and self.section('ipguard').get('mode')=='enforce':
                from guard_bridge import BrokerClient
                BrokerClient(self.config.guard_socket).clear()
                with self.store.transaction() as db: db.execute("UPDATE bans SET state='released',expires_at=? WHERE state='applied'",(time.time(),))
        if name=='outbounds':
            if not value: raise CoreError('At least one outbound is required')
            tags=[]
            for out in value:
                if not isinstance(out,dict) or not TAG_RE.fullmatch(str(out.get('tag',''))) or not out.get('protocol'):
                    raise CoreError('Every outbound requires tag and protocol')
                if out['tag']=='dark-api': raise CoreError('Reserved API tag')
                tags.append(out['tag'])
            if len(set(tags))!=len(tags): raise CoreError('Duplicate outbound tags')
            links={}
            for out in value:
                stream=out.get('streamSettings',{})
                if not isinstance(stream,dict) or not isinstance(stream.get('sockopt',{}),dict):raise CoreError('Invalid outbound streamSettings/sockopt')
                via=stream.get('sockopt',{}).get('dialerProxy')
                if via:
                    if not isinstance(via,str) or via not in tags:raise CoreError('Unknown chained outbound')
                    links[out['tag']]=via
            for tag in links:
                seen=set();current=tag
                while current in links:
                    if current in seen:raise CoreError('Outbound chaining cycle')
                    seen.add(current);current=links[current]
            for r in self.section('routing').get('rules',[]):
                if r.get('outboundTag') and r['outboundTag'] not in tags: raise CoreError('Routing still refers to an outbound being removed')
        if name=='routing':
            if not isinstance(value.get('rules',[]),list): raise CoreError('rules must be a list')
            tags={o['tag'] for o in self.section('outbounds')}
            balancers=value.get('balancers',[])
            if not isinstance(balancers,list):raise CoreError('balancers must be a list')
            btags=set()
            for b in balancers:
                if not isinstance(b,dict) or not isinstance(b.get('tag'),str) or not TAG_RE.fullmatch(b['tag']) or b['tag'] in btags:raise CoreError('Invalid or duplicate balancer tag')
                if not isinstance(b.get('selector'),list) or not b['selector'] or any(not isinstance(x,str) or not x for x in b['selector']):raise CoreError('Balancer requires nonempty tag selectors')
                btags.add(b['tag'])
            for rule in value.get('rules',[]):
                if not isinstance(rule,dict): raise CoreError('Every rule must be an object')
                if rule.get('outboundTag') and rule['outboundTag'] not in tags: raise CoreError('Unknown routing outbound')
                if bool(rule.get('outboundTag'))==bool(rule.get('balancerTag')): raise CoreError('Rule needs exactly one outbound or balancer tag')
                if rule.get('balancerTag') and rule['balancerTag'] not in btags:raise CoreError('Unknown routing balancer')
                if rule.get('type','field')!='field': raise CoreError('Unsupported routing rule type')
        if name=='hosts':
            known={i['id'] for i in self.inbounds()}
            for host in value:
                if not isinstance(host,dict) or type(host.get('inboundId'))is not int or host['inboundId'] not in known:
                    raise CoreError('Host requires an existing inboundId')
                addr=host.get('address','')
                if not isinstance(addr,str) or not addr or any(c in addr for c in '/?#@ \r\n'):
                    raise CoreError('Host address must be a plain IP/domain')
                if type(host.get('port',0))is not int or not 1<=host['port']<=65535: raise CoreError('Invalid host port')
                if 'enable' in host and type(host['enable'])is not bool:raise CoreError('Host enable must be boolean')
                for key in ('remark','sni','host','path','alpn','fingerprint'):
                    if key in host and (not isinstance(host[key],str) or len(host[key])>4096):raise CoreError('Invalid host '+key)
        with self.store.transaction() as db:
            db.execute('INSERT INTO core_sections VALUES(?,?) ON CONFLICT(name) DO UPDATE SET body=excluded.body',(name,json.dumps(value)))
        result={'saved':True,'applied':False,'runtime':self.runtime_state()}
        if name=='runtime':result.update(requires_root_apply=True,apply_command='sudo darkxray settings-apply')
        return result

    def inbounds(self)->list[dict]:
        with self.store.lock:rows=self.store.db.execute('SELECT id,body FROM core_inbounds ORDER BY id').fetchall()
        return [json.loads(r['body'])|{'id':r['id']} for r in rows]

    def inbound(self,i:int)->dict:
        for r in self.inbounds():
            if r['id']==i:return r
        raise CoreError('Inbound not found',status=404)

    @serialized
    def save_inbound(self,data:dict,i:int|None=None)->dict:
        self._write();v=copy.deepcopy(data)
        allowed={'remark','protocol','listen','port','enable','tag','settings','streamSettings','sniffing','panelMeta','id'}
        if not isinstance(v,dict) or set(v)-allowed: raise CoreError('Unknown inbound field')
        v.pop('id',None)
        if v.get('protocol') not in PROTOCOLS: raise CoreError('This standalone editor does not yet support that protocol')
        if type(v.get('port'))is not int or not 1<=v['port']<=65535: raise CoreError('Port must be between 1 and 65535')
        if v['port'] in self.config.protected_ports: raise CoreError('Port overlaps panel, core API, or protected management ports')
        listen=v.get('listen') or '0.0.0.0'
        try:ipaddress.ip_address(listen)
        except ValueError:raise CoreError('Listen must be an IPv4 or IPv6 address')
        v['listen']=listen;v.setdefault('enable',True);v.setdefault('remark','DARK inbound')
        if type(v['enable'])is not bool or not isinstance(v['remark'],str) or len(v['remark'])>200:raise CoreError('Invalid inbound fields')
        v.setdefault('tag','dark-in-'+uuid.uuid4().hex[:12])
        if not isinstance(v['tag'],str) or not TAG_RE.fullmatch(v['tag']) or v['tag']=='dark-api':raise CoreError('Invalid/reserved inbound tag')
        for key in ('settings','streamSettings','sniffing'):
            v.setdefault(key,{});
            if not isinstance(v[key],dict):raise CoreError(key+' must be an object')
        v.setdefault('panelMeta',{})
        if not isinstance(v['panelMeta'],dict):raise CoreError('panelMeta must be an object')
        if len(json.dumps(v['panelMeta']))>20000:raise CoreError('panelMeta too large')
        if v['settings'].get('clients') or v['settings'].get('accounts'):
            raise CoreError('Credentials must be managed through DARK clients, not hidden in inbound JSON')
        v['settings'].pop('clients',None);v['settings'].pop('accounts',None)
        if v['protocol']=='vless': v['settings'].setdefault('decryption','none')
        if len(json.dumps(v))>200000:raise CoreError('Inbound too large')
        self._validate_transport(v)
        current=self.inbounds()
        if i is not None:
            old=self.inbound(i)
            if old['protocol']!=v['protocol'] and any(i in c['inboundIds'] for c in self.clients()):
                raise CoreError('Detach clients before switching an inbound protocol')
        for r in current:
            if r['id']==i:continue
            if r['tag']==v['tag']:raise CoreError('Duplicate inbound tag')
            if r['port']==v['port']:raise CoreError('Duplicate port; this release uses conservative collision checks')
        with self.store.transaction() as db:
            if i is None:i=db.execute('INSERT INTO core_inbounds(body) VALUES(?)',(json.dumps(v),)).lastrowid
            else:db.execute('UPDATE core_inbounds SET body=? WHERE id=?',(json.dumps(v),i))
        return v|{'id':i,'applied':False}

    @staticmethod
    def _validate_transport(v:dict):
        """Common form errors are caught before reaching the real core validator."""
        proto=v['protocol'];st=v['streamSettings'];net=st.get('network','tcp');sec=st.get('security','none')
        for key in ('realitySettings','tlsSettings','grpcSettings','wsSettings','xhttpSettings','httpupgradeSettings','kcpSettings','sockopt'):
            if key in st and not isinstance(st[key],dict): raise CoreError(key+' must be an object')
        if net not in ('tcp','raw','ws','grpc','httpupgrade','xhttp','kcp'): raise CoreError('Unsupported transport')
        if sec not in ('none','tls','reality'): raise CoreError('Unsupported security layer')
        if sec=='reality' and (proto not in ('vless','trojan') or net not in ('tcp','raw','grpc','xhttp')):
            raise CoreError('REALITY requires VLESS/Trojan with TCP, gRPC or XHTTP')
        if net=='kcp' and sec!='none': raise CoreError('mKCP has no separate TLS/REALITY layer')
        if sec=='reality':
            rt=st.get('realitySettings',{})
            try:
                key=rt.get('privateKey','')
                if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',key):raise ValueError()
                names=rt.get('serverNames');ids=rt.get('shortIds')
                if not isinstance(names,list) or not names or any(not isinstance(n,str) or not n for n in names):raise ValueError()
                if not isinstance(ids,list) or not ids:raise ValueError()
                if not isinstance(rt.get('target') or rt.get('dest'),str):raise ValueError()
                raw=base64.urlsafe_b64decode(key+'='*((4-len(key)%4)%4))
                if len(raw)!=32 or not rt.get('serverNames') or not (rt.get('target') or rt.get('dest')): raise ValueError()
                if any(not isinstance(x,str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){0,8}',x) for x in rt.get('shortIds',[])): raise ValueError()
            except (ValueError,TypeError): raise CoreError('REALITY needs a 32-byte private key, target, server names and hexadecimal short IDs')
        if sec=='tls':
            certs=st.get('tlsSettings',{}).get('certificates',[])
            if not isinstance(certs,list) or not certs or any(not isinstance(c,dict) for c in certs):raise CoreError('TLS inbound requires a certificate list')
        # URI generation currently supports only classic Shadowsocks ciphers.
        if proto=='shadowsocks' and not v['settings'].get('method'):
            v['settings']['method']='aes-128-gcm'

    @serialized
    def delete_inbound(self,i:int):
        self._write();self.inbound(i)
        with self.store.lock:
            # Pending and failed managed operations also reserve their inbounds.
            exists=self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='managed_clients'").fetchone()
            managed=self.store.db.execute("SELECT inbounds FROM managed_clients WHERE state!='deleted'").fetchall() if exists else []
        if any(i in json.loads(r[0]) for r in managed) or any(i in c['inboundIds'] for c in self.clients()):
            raise CoreError('Detach all customers, including pending ones, before deleting an inbound')
        if any(h['inboundId']==i for h in self.section('hosts')):raise CoreError('Remove dependent hosts first')
        tag=self.inbound(i)['tag']
        if any(tag in r.get('inboundTag',[]) for r in self.section('routing').get('rules',[])):raise CoreError('Remove dependent routing rules first')
        with self.store.transaction() as db:db.execute('DELETE FROM core_inbounds WHERE id=?',(i,))
        return {'deleted':True}

    def clients(self)->list[dict]:
        with self.lock:
            self.collect_stats()
            with self.store.lock:rows=self.store.db.execute('SELECT * FROM core_clients ORDER BY email').fetchall()
            return [json.loads(r['body'])|{'inboundIds':json.loads(r['inbounds']),'traffic':{'up':r['up'],'down':r['down']}} for r in rows]

    def client_detail(self,email:str)->dict:
        with self.store.lock:r=self.store.db.execute('SELECT * FROM core_clients WHERE email=?',(email,)).fetchone()
        if not r:raise CoreError('Core client not found',status=404)
        return {'client':json.loads(r['body']),'inboundIds':json.loads(r['inbounds'])}

    @serialized
    def create(self,client:dict,inbounds:list[int]):
        self._write()
        if not EMAIL_RE.fullmatch(client.get('email','')):raise CoreError('Invalid identity')
        if not set(inbounds)<={r['id'] for r in self.inbounds()}:raise CoreError('Unknown inbound')
        with self.store.transaction() as db:db.execute('INSERT INTO core_clients(email,body,inbounds) VALUES(?,?,?)',(client['email'],json.dumps(client),json.dumps(inbounds)))

    @serialized
    def update(self,email:str,client:dict):
        self._write();old=self.client_detail(email)['client']
        if client.get('email',email)!=email:raise CoreError('Identity cannot change')
        old.update(self.writable(client));old['email']=email
        with self.store.transaction() as db:db.execute('UPDATE core_clients SET body=? WHERE email=?',(json.dumps(old),email))

    @serialized
    def delete(self,email:str):
        self._write();self.collect_stats(force=True)
        with self.store.lock:
            r=self.store.db.execute('SELECT body,inbounds,up,down FROM core_clients WHERE email=?',(email,)).fetchone()
            final=(json.loads(r['body'])|{'inboundIds':json.loads(r['inbounds']),'traffic':{'up':r['up'],'down':r['down']}}) if r else None
        with self.store.transaction() as db:
            db.execute('DELETE FROM core_clients WHERE email=?',(email,));db.execute('DELETE FROM core_devices WHERE email=?',(email,))
        # Usernames remain reserved in managed_clients tombstones.
        return final

    @serialized
    def attach(self,email:str,ids:list[int]):
        self._write();d=self.client_detail(email);vals=sorted(set(d['inboundIds'])|set(ids))
        if not set(vals)<={r['id'] for r in self.inbounds()}:raise CoreError('Unknown inbound')
        with self.store.transaction() as db:db.execute('UPDATE core_clients SET inbounds=? WHERE email=?',(json.dumps(vals),email))

    @serialized
    def detach(self,email:str,ids:list[int]):
        self._write();d=self.client_detail(email);vals=sorted(set(d['inboundIds'])-set(ids))
        if not vals:raise CoreError('A customer must retain an inbound')
        with self.store.transaction() as db:db.execute('UPDATE core_clients SET inbounds=? WHERE email=?',(json.dumps(vals),email))

    def reset(self,email:str):
        self._write()
        with self.lock:
            self.collect_stats(force=True,strict=self.running)
            with self.store.transaction() as db:
                r=db.execute('SELECT body,inbounds,up,down FROM core_clients WHERE email=?',(email,)).fetchone()
                final=(json.loads(r['body'])|{'inboundIds':json.loads(r['inbounds']),'traffic':{'up':r['up'],'down':r['down']}}) if r else None
                db.execute('UPDATE core_clients SET up=0,down=0 WHERE email=?',(email,))
            return final
            # last_samples stays at the last observed core counter: no replay of old usage.

    @staticmethod
    def writable(record:dict)->dict:
        return {k:copy.deepcopy(v) for k,v in record.items() if k not in {'inboundIds','traffic','usedTraffic','owner','engineStatus'}}

    @staticmethod
    def counters(record:dict)->tuple[int,int]:
        t=record.get('traffic') or {};a,b=t.get('up',0),t.get('down',0)
        if any(type(v)is not int or v<0 or v>2**63-1 for v in (a,b)):raise CoreError('Invalid core counters')
        return a,b

    @property
    def running(self):return bool(self.process is not None and self.process.poll() is None)

    def build_config(self)->dict:
        # Read local tables directly; compiling never queries an external panel.
        with self.store.lock:rows=self.store.db.execute('SELECT body,inbounds FROM core_clients').fetchall()
        cs=[(json.loads(r[0]),set(json.loads(r[1]))) for r in rows]
        result=[]
        for r in self.inbounds():
            if not r['enable']:continue
            proto=r['protocol'];settings=copy.deepcopy(r['settings']);users=[]
            for c,ids in cs:
                if r['id'] not in ids or not c.get('enable',True):continue
                p={'email':c['email'],'level':0}
                if proto in ('vless','vmess'):
                    p['id']=c['id']
                    if proto=='vless' and c.get('flow'):p['flow']=c['flow']
                elif proto in ('trojan','shadowsocks'):
                    p['password']=c['password']
                    if proto=='shadowsocks' and not settings.get('method','').startswith('2022-'):
                        p['method']=settings.get('method','aes-128-gcm')
                elif proto in ('socks','http'):p={'user':c['email'],'pass':c['password']}
                else:continue
                users.append(p)
            if proto in ('vless','vmess','trojan','shadowsocks'):settings['clients']=users
            elif proto in ('socks','http'):
                settings['accounts']=users
                if proto=='socks':settings['auth']='password'
                if not users:continue  # never turn a no-user customer inbound into an open proxy
            result.append({'tag':r['tag'],'listen':r['listen'],'port':r['port'],'protocol':proto,
                           'settings':settings,'streamSettings':copy.deepcopy(r['streamSettings']),
                           'sniffing':copy.deepcopy(r['sniffing'])})
        policy=self.section('policy');levels=policy.setdefault('levels',{});level=levels.setdefault('0',{})
        level.update({'statsUserUplink':True,'statsUserDownlink':True})
        policy.setdefault('system',{}).update({'statsInboundUplink':True,'statsInboundDownlink':True})
        cfg={'log':{'access':str(self.runtime/'access.log'),'error':str(self.runtime/'error.log'),'loglevel':'warning'},
             'api':{'tag':'dark-api','listen':'127.0.0.1:'+str(self.config.xray_api_port),'services':['StatsService','HandlerService','LoggerService']},
             'stats':{},'policy':policy,'inbounds':result,'outbounds':self.section('outbounds'),
             'routing':self.section('routing'),'dns':self.section('dns')}
        if self.section('observatory'):cfg['observatory']=self.section('observatory')
        return cfg

    def config_hash(self,cfg:dict)->str:return hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()

    def _atomic(self,path:Path,text:str):
        if path.is_symlink():raise CoreError('Runtime symlink refused')
        temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
            os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)

    def validate(self,cfg:dict|None=None)->dict:
        with self.lock:
            cfg=cfg or self.build_config();path=self.runtime/'candidate.json'
            self._atomic(path,json.dumps(cfg,indent=2))
            env=os.environ.copy();env['XRAY_LOCATION_ASSET']=self.config.xray_assets
            try:r=subprocess.run([self._binary(),'run','-test','-config',str(path)],env=env,capture_output=True,text=True,timeout=20,check=False)
            except (OSError,subprocess.TimeoutExpired) as ex:raise CoreError('Core validation could not run: '+type(ex).__name__,status=503)
            if r.returncode:raise CoreError('Xray rejected the candidate configuration; current process unchanged: '+(r.stderr or r.stdout)[-1500:])
            return {'validated':True,'hash':self.config_hash(cfg),'applied':False}

    def _spawn(self):
        # Only a child started by this object may be stopped; no system-wide pkill.
        if self.process is not None and self.process.poll() is not None:self._stop_child()
        log=self.runtime/'process.log'
        if log.is_symlink():raise CoreError('Log symlink refused')
        if log.exists() and log.stat().st_size>8*1024*1024:os.replace(log,self.runtime/'process.previous.log')
        fd=os.open(log,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600);self.log_handle=os.fdopen(fd,'ab',buffering=0)
        env=os.environ.copy();env['XRAY_LOCATION_ASSET']=self.config.xray_assets
        self.last_samples={};self.last_stats=0
        self.process=subprocess.Popen([self._binary(),'run','-config',str(self.runtime/'active.json')],
            stdin=subprocess.DEVNULL,stdout=self.log_handle,stderr=subprocess.STDOUT,env=env,cwd=self.runtime,start_new_session=True)
        self.last_start=time.time()
        if not self.version:
            try:self.version_info()
            except CoreError:pass
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            if self.process.poll()is not None:
                raise CoreError('Xray exited during startup; inspect the local process log',status=503)
            try:
                with socket.create_connection(('127.0.0.1',self.config.xray_api_port),timeout=.15):return
            except OSError:time.sleep(.1)
        self._stop_child();raise CoreError('Xray API did not become ready',status=503)

    def _stop_child(self):
        p=self.process
        if p and p.poll() is None:
            p.terminate()
            try:p.wait(timeout=8)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
        self.process=None
        if self.log_handle:self.log_handle.close();self.log_handle=None

    def apply(self,*,start:bool=False,force:bool=False)->dict:
        self._write()
        with self.lock:
            cfg=self.build_config();newhash=self.config_hash(cfg)
            if not force and self.running and newhash==self.applied_hash:return self.runtime_state()
            self.validate(cfg)
            old=(self.runtime/'active.json').read_text() if (self.runtime/'active.json').exists() else None
            oldhash=self.applied_hash;was=self.running
            self.collect_stats(force=True,strict=was)
            if was:self._stop_child()
            else:
                # Avoid attaching to an unrelated process on the control API port.
                s=socket.socket()
                try:s.bind(('127.0.0.1',self.config.xray_api_port))
                except OSError:raise CoreError('Core API port already occupied by another process',status=409)
                finally:s.close()
            if old:self._atomic(self.runtime/'previous.json',old)
            self._atomic(self.runtime/'active.json',json.dumps(cfg,indent=2))
            try:
                if start or was or self.wants_running:self._spawn()
            except Exception as e:
                self._stop_child()
                rollback='no previous running generation'
                if old:
                    self._atomic(self.runtime/'active.json',old)
                    if was:
                        try:self._spawn();rollback='previous process configuration restored'
                        except Exception:rollback='previous configuration written, restart failed'
                self.applied_hash=oldhash if self.running else '';self.last_error=str(e)+'; '+rollback
                raise CoreError(self.last_error,status=503)
            self.applied_hash=newhash if self.running else ''
            self.last_apply=time.time();self.last_error='';self.wants_running=self.running
            return self.runtime_state()

    def command(self,action:str)->dict:
        self._write()
        if action=='validate':return self.validate()
        if action=='restart':return self.apply(start=True,force=True)
        if action in ('start','apply'):return self.apply(start=True)
        if action=='stop':
            with self.lock:
                self.collect_stats(force=True,strict=self.running);self.wants_running=False;self._stop_child();self.applied_hash=''
            return self.runtime_state()
        raise CoreError('Unknown core action',status=404)

    def flush(self):
        self.read_ip_log()
        if not self.wants_running:return
        if not self.running and time.time()-self.last_start<5:return
        try:self.apply(start=True)
        except Exception as e:self.last_error=str(e)[:1500]

    def collect_stats(self,*,force:bool=False,strict:bool=False):
        with self.lock:
            if not self.running:return
            if not force and time.monotonic()-self.last_stats<1:return
            try:
                r=subprocess.run([self._binary(),'api','statsquery','--server=127.0.0.1:'+str(self.config.xray_api_port),
                                  '-pattern=user>>>','-reset=false'],capture_output=True,text=True,timeout=8,check=False)
                if r.returncode:raise CoreError('Xray statistics query failed')
                doc=json.loads(r.stdout);entries=doc.get('stat',[]);samples={}
                if not isinstance(entries,list):raise CoreError('Unexpected stats response')
                for item in entries:
                    m=re.fullmatch(r'user>>>(.+)>>>traffic>>>(uplink|downlink)',item.get('name',''))
                    if not m:continue
                    n=int(item.get('value',0))
                    if n<0 or n>2**63-1:raise CoreError('Invalid core statistic')
                    samples.setdefault(m[1],[0,0])[0 if m[2]=='uplink' else 1]=n
                with self.store.transaction() as db:
                    for email,pair in samples.items():
                        old=self.last_samples.get(email,(0,0));delta=[pair[i]-old[i] if pair[i]>=old[i] else pair[i] for i in range(2)]
                        db.execute('UPDATE core_clients SET up=up+?,down=down+? WHERE email=?',(*delta,email))
                self.last_samples.update({k:tuple(v) for k,v in samples.items()})
                self.last_stats=time.monotonic();self.stats_error=''
            except (ValueError,OSError,subprocess.SubprocessError,CoreError) as ex:
                self.stats_error=str(ex)[:500]
                if strict:raise CoreError('Cannot safely snapshot traffic before stopping/resetting: '+self.stats_error,status=503)

    def runtime_state(self)->dict:
        try:dirty=self.config_hash(self.build_config())!=self.applied_hash
        except Exception:dirty=True
        return {'running':self.running,'pid':self.process.pid if self.running else None,'core_binary_present':Path(self.config.xray_binary).is_file(),
                'version':self.version,'dirty':dirty,'state':'running' if self.running else 'stopped','last_error':self.last_error,
                'statistics_error':self.stats_error,'applied_hash':self.applied_hash,'last_apply':self.last_apply,
                'independent':True,'restart_disconnects_existing_sessions':True}

    def system(self)->dict:
        vm=psutil.virtual_memory();disk=psutil.disk_usage(self.runtime);swap=psutil.swap_memory();net=psutil.net_io_counters()
        now=time.monotonic();rates={}
        if self._net_sample:
            then,up,down=self._net_sample;elapsed=max(.001,now-then)
            rates={'up':max(0,net.bytes_sent-up)/elapsed,'down':max(0,net.bytes_recv-down)/elapsed}
        self._net_sample=(now,net.bytes_sent,net.bytes_recv)
        core_mem=0
        if self.running:
            try:core_mem=psutil.Process(self.process.pid).memory_info().rss
            except psutil.Error:pass
        return {'cpu':psutil.cpu_percent(interval=.05),'mem':{'current':vm.used,'total':vm.total},
                'disk':{'current':disk.used,'total':disk.total},'swap':{'current':swap.used,'total':swap.total},
                'uptime':int(time.time()-psutil.boot_time()),'loads':list(os.getloadavg()),
                'netTraffic':{'sent':net.bytes_sent,'recv':net.bytes_recv},'netIO':rates,
                'xray':{'state':'running' if self.running else 'stopped','version':self.version,'mem':core_mem},
                'runtime':self.runtime_state()}

    def ip_policy(self,enforce:bool|None=None)->Policy:
        clients={};ibs={i['id']:i for i in self.inbounds() if i['enable']}
        settings=self.section('ipguard')
        if enforce is None:enforce=settings['mode']=='enforce'
        with self.store.lock:
            rows=self.store.db.execute('SELECT id,owner,limit_ip FROM clients').fetchall()
            cs={r['email']:json.loads(r['inbounds']) for r in self.store.db.execute('SELECT email,inbounds FROM core_clients')}
        for r in rows:
            ports=sorted({ibs[i]['port'] for i in cs.get(r['id'],[]) if i in ibs})
            if ports:clients[r['id']]={'owner':r['owner'],'limit_ip':r['limit_ip'],'ports':ports}
        return Policy.from_dict({'schema':1,'clients':clients,'enforce':enforce,
            'source_mode':'direct' if self.config.direct_source_verified else 'opaque',
            'original_ip_verified':self.config.direct_source_verified,'window_seconds':settings['window_seconds'],
            'ban_seconds':settings['ban_seconds'],'exempt_ips':settings.get('exempt_ips',[]),
            'protected_ports':self.config.protected_ports})

    def sync_ip_guard(self):
        from dataclasses import asdict
        from guard_bridge import BrokerClient,BrokerExecutor
        policy=self.ip_policy()
        generation=self.config_hash(asdict(policy));self._guard_executor=None
        status={'state':'observing','requested_mode':'enforce' if policy.enforce else 'observe',
                'policy_hash':generation,'loaded_policy_hash':generation,'applied':False,
                'checked_at':time.time(),'limited_clients':sum(c.limit_ip>0 for c in policy.clients.values()),
                'error':''}
        if policy.enforce:
            try:
                executor=BrokerExecutor(policy,BrokerClient(self.config.guard_socket));info=executor.check()
                # The broker resets only its own temporary sets on restart. Never
                # claim that old SQLite ban records are still enforced afterward.
                with self.store.transaction() as db:
                    db.execute("CREATE TABLE IF NOT EXISTS core_guard_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                    old=db.execute("SELECT value FROM core_guard_meta WHERE key='boot_id'").fetchone()
                    if not old or old[0]!=info['boot_id']:
                        db.execute("UPDATE bans SET state='released',expires_at=? WHERE state='applied'",(time.time(),))
                    db.execute("INSERT INTO core_guard_meta VALUES('boot_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(info['boot_id'],))
                self._guard_executor=executor;status.update(state='applied',applied=True,broker=info)
            except (PolicyError,OSError) as exc:
                status.update(state='error',error=str(exc)[:500],loaded_policy_hash='')
        elif not policy.original_ip_verified:
            status['source_warning']='IP sources are not verified; no automatic ban or concurrency claim'
        self._guard_status=status
        return policy

    def read_ip_log(self):
        try:policy=self.sync_ip_guard()
        except (PolicyError,CoreError) as exc:
            self.ip_error=str(exc);return
        path=self.runtime/'access.log'
        if not path.exists():return
        try:
            if path.is_symlink():raise PolicyError('Access log symlink refused')
            info=path.stat();identity=(info.st_dev,info.st_ino)
            if self._access_inode is None:
                self._access_inode=identity;self._access_position=0
            if identity!=self._access_inode or info.st_size<self._access_position:
                self._access_position=0;self._access_fragment='';self._access_inode=identity
            with path.open('rb') as f:
                f.seek(self._access_position);data=f.read(1024*1024);self._access_position=f.tell()
            text=self._access_fragment+data.decode('utf-8',errors='replace');lines=text.split('\n');self._access_fragment=lines.pop()[-16384:]
            guard=Guard(policy,self.store,self._guard_executor)
            for line in lines:
                obs=parse_access_line(line)
                if obs:
                    result=guard.observe(obs.email,obs.ip)
                    if result.get('decision')=='enforcement_failed':
                        self._guard_status.update(state='error',applied=False,error=result.get('reason','Enforcement failed'))
            self.ip_error=''
        except (OSError,PolicyError) as ex:self.ip_error=str(ex)[:500]

    def ips(self,email:str)->list[dict]:
        self.read_ip_log()
        with self.store.lock:return [dict(r) for r in self.store.db.execute('SELECT ip,node,first_seen,last_seen,granted FROM observations WHERE client_id=? ORDER BY last_seen DESC',(email,))]

    def clear_ips(self,email:str):
        with self.store.transaction() as db:db.execute('DELETE FROM observations WHERE client_id=?',(email,))
        return {'cleared':True,'firewall_unban':False}

    def unban_ip(self,ip:str)->dict:
        from guard_bridge import BrokerClient
        from dark_policy import normalize_ip
        ip=normalize_ip(ip);response=BrokerClient(self.config.guard_socket).release(ip)
        with self.store.transaction() as db:
            db.execute("UPDATE bans SET state='released',expires_at=? WHERE ip=?",(time.time(),ip))
            db.execute('DELETE FROM observations WHERE ip=? AND granted=0',(ip,))
        return response

    def ip_status(self)->dict:
        return {'mode':self.section('ipguard')['mode'],'source_verified':self.config.direct_source_verified,
                'window_seconds':self.section('ipguard')['window_seconds'],
                'limiter':'independent DARK Guard','error':self.ip_error or self._guard_status.get('error',''),
                'enforcement':'narrow root-owned Unix/nftables broker; panel remains unprivileged',
                'global_multi_node_limit':False,'packet_block_tested':False,**self._guard_status,
                **({'error':self.ip_error,'state':'error','applied':False} if self.ip_error else {})}

    def devices(self,email:str)->list[dict]:
        with self.store.lock:return [dict(r) for r in self.store.db.execute('SELECT id,device_os,model,first_seen,last_seen FROM core_devices WHERE email=?',(email,))]

    def clear_devices(self,email:str,device_id:int|None=None):
        with self.store.transaction() as db:
            db.execute('DELETE FROM core_devices WHERE email=?'+(' AND id=?' if device_id is not None else ''),(email,device_id) if device_id is not None else (email,))
        return {'cleared':True}

    @serialized
    def check_device(self,email:str,hwid:str,os_name:str='',model:str=''):
        client=self.client_detail(email)['client'];limit=client.get('limitHwid',0)
        if not limit:return
        if not isinstance(hwid,str) or not 4<=len(hwid)<=512:raise CoreError('A supported client must send x-hwid for this subscription',status=403)
        fingerprint=hashlib.sha256(hwid.encode()).hexdigest();now=time.time()
        with self.store.transaction() as db:
            r=db.execute('SELECT id FROM core_devices WHERE email=? AND digest=?',(email,fingerprint)).fetchone()
            if r:db.execute('UPDATE core_devices SET last_seen=? WHERE id=?',(now,r[0]));return
            if db.execute('SELECT COUNT(*) FROM core_devices WHERE email=?',(email,)).fetchone()[0]>=limit:raise CoreError('Subscription device limit reached',status=403)
            db.execute('INSERT INTO core_devices(email,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?)',(email,fingerprint,os_name[:80],model[:120],now,now))

    def links(self,email:str)->dict:
        d=self.client_detail(email);c=d['client'];links=[];warnings=[]
        for i in d['inboundIds']:
            ib=self.inbound(i)
            if not ib['enable']:continue
            configured=[h for h in self.section('hosts') if h['inboundId']==i]
            hs=[h for h in configured if h.get('enable',True)] if configured else [{}]
            for host in hs:
                address=host.get('address',self.config.public_address);port=host.get('port',ib['port'])
                proto=ib['protocol'];sub=self.section('subscription');base_remark=host.get('remark',ib['remark'])
                label=sub.get('remark_template','{remark} | {email}').replace('{remark}',base_remark).replace('{email}',email).replace('{protocol}',proto.upper())
                st=ib['streamSettings'];net=st.get('network','tcp');sec=st.get('security','none')
                q={'type':net,'security':sec};security=st.get('realitySettings' if sec=='reality' else 'tlsSettings',{})
                sni=host.get('sni') or security.get('serverName') or next(iter(security.get('serverNames',[])),'')
                if sni:q['sni']=sni
                if sec=='reality':
                    key=security.get('privateKey','')
                    if not key: warnings.append('REALITY private key missing for '+ib['tag']);continue
                    try:
                        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                        from cryptography.hazmat.primitives import serialization
                        pub=X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(key+'='*((4-len(key)%4)%4))).public_key()
                        q['pbk']=base64.urlsafe_b64encode(pub.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode().rstrip('=')
                    except (ValueError,TypeError):warnings.append('Invalid REALITY key for '+ib['tag']);continue
                    q['sid']=next(iter(security.get('shortIds',[])),'')
                    meta=ib.get('panelMeta',{}).get('reality',{}) if isinstance(ib.get('panelMeta',{}),dict) else {}
                    q['fp']=host.get('fingerprint') or meta.get('fingerprint','chrome')
                    spider=meta.get('spiderX','')
                    if spider:q['spx']=spider
                if sec=='tls' and host.get('alpn'):q['alpn']=host['alpn']
                if net in ('ws','httpupgrade','xhttp'):
                    ns=st.get(net+'Settings',{});q['path']=host.get('path',ns.get('path','/'))
                    q['host']=host.get('host',ns.get('host',ns.get('headers',{}).get('Host','')))
                    if net=='xhttp':q['mode']=ns.get('mode','auto')
                if net=='grpc':q['serviceName']=st.get('grpcSettings',{}).get('serviceName','')
                hp=('['+address+']' if ':' in address else address)+':'+str(port)
                if proto=='vless':
                    q['encryption']=c.get('encryption') or 'none'
                    if c.get('flow'):q['flow']=c['flow']
                    uri='vless://'+c['id']+'@'+hp+'?'+urlencode(q)+'#'+quote(label)
                elif proto=='trojan':uri='trojan://'+quote(c['password'],safe='')+'@'+hp+'?'+urlencode(q)+'#'+quote(label)
                elif proto=='vmess':
                    ob={'v':'2','ps':label,'add':address,'port':str(port),'id':c['id'],'aid':'0','scy':c.get('security','auto'),
                        'net':net,'type':'none','host':q.get('host',''),'path':q.get('serviceName',q.get('path','')),'tls':sec if sec!='none' else '','sni':sni}
                    uri='vmess://'+base64.b64encode(json.dumps(ob,separators=(',',':'),ensure_ascii=False).encode()).decode()
                elif proto=='shadowsocks':
                    method=ib['settings'].get('method','aes-128-gcm')
                    if method.startswith('2022-') or net not in ('tcp','raw') or sec!='none':
                        warnings.append('Shadowsocks transport/2022 export not yet supported');continue
                    user=base64.urlsafe_b64encode((method+':'+c['password']).encode()).decode().rstrip('=');uri='ss://'+user+'@'+hp+'#'+quote(label)
                else:warnings.append('No subscription generator for '+proto);continue
                links.append({'inboundId':i,'remark':label,'uri':uri})
        return {'links':links,'warnings':warnings,'formats':['raw','base64']}

    @staticmethod
    def _clash_proxy(uri:str,name:str)->dict:
        from urllib.parse import urlsplit,parse_qs,unquote
        if uri.startswith('vmess://'):
            raw=uri[8:];doc=json.loads(base64.b64decode(raw+'='*((4-len(raw)%4)%4)).decode())
            out={'name':name,'type':'vmess','server':doc['add'],'port':int(doc['port']),'uuid':doc['id'],'alterId':int(doc.get('aid',0)),'cipher':doc.get('scy','auto'),'udp':True}
            net=doc.get('net','tcp');out['network']=net
            if doc.get('tls'):out['tls']=True
            if doc.get('sni'):out['servername']=doc['sni']
            if net=='ws':out['ws-opts']={'path':doc.get('path','/'),'headers':{'Host':doc.get('host','')}}
            if net=='grpc':out['grpc-opts']={'grpc-service-name':doc.get('path','')}
            return out
        p=urlsplit(uri);q={k:v[-1] for k,v in parse_qs(p.query).items()};proto=p.scheme
        if proto=='ss':
            user=p.username or ''
            try:creds=base64.urlsafe_b64decode(user+'='*((4-len(user)%4)%4)).decode();method,password=creds.split(':',1)
            except Exception:raise CoreError('Cannot convert Shadowsocks link to Clash')
            return {'name':name,'type':'ss','server':p.hostname,'port':p.port,'cipher':method,'password':password,'udp':True}
        if proto not in ('vless','trojan'):raise CoreError('Unsupported Clash proxy protocol')
        out={'name':name,'type':proto,'server':p.hostname,'port':p.port,'udp':True}
        if proto=='vless':out['uuid']=unquote(p.username or '')
        else:out['password']=unquote(p.username or '')
        net=q.get('type','tcp');sec=q.get('security','none');out['network']=net
        if sec in ('tls','reality'):out['tls']=True
        if q.get('sni'):out['servername']=q['sni']
        if q.get('flow'):out['flow']=q['flow']
        if q.get('fp'):out['client-fingerprint']=q['fp']
        if sec=='reality':out['reality-opts']={'public-key':q.get('pbk',''),'short-id':q.get('sid','')}
        if net=='ws':out['ws-opts']={'path':q.get('path','/'),'headers':{'Host':q.get('host','')}}
        if net=='grpc':out['grpc-opts']={'grpc-service-name':q.get('serviceName','')}
        if net=='xhttp':out['xhttp-opts']={'path':q.get('path','/'),'mode':q.get('mode','auto')}
        return out

    @staticmethod
    def _yaml(value,level:int=0)->str:
        pad='  '*level
        if isinstance(value,dict):
            lines=[]
            for k,v in value.items():
                key=json.dumps(str(k),ensure_ascii=False)
                if isinstance(v,(dict,list)):lines.append(f'{pad}{key}:\n'+CoreEngine._yaml(v,level+1))
                else:lines.append(f'{pad}{key}: {CoreEngine._yaml(v,0).strip()}')
            return '\n'.join(lines)
        if isinstance(value,list):
            lines=[]
            for v in value:
                if isinstance(v,(dict,list)):
                    nested=CoreEngine._yaml(v,level+1).splitlines();lines.append(pad+'- '+nested[0].lstrip());lines.extend(nested[1:])
                else:lines.append(pad+'- '+CoreEngine._yaml(v,0).strip())
            return '\n'.join(lines)
        if value is True:return 'true'
        if value is False:return 'false'
        if value is None:return 'null'
        if isinstance(value,(int,float)):return str(value)
        return json.dumps(str(value),ensure_ascii=False)

    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:
        if fmt not in ('raw','base64','json','clash'):raise CoreError('Unsupported subscription format',status=400)
        settings=self.section('subscription');result=self.links(email)
        if result['warnings']:raise CoreError('Subscription would be incomplete: '+'; '.join(result['warnings']),status=422)
        links=[r['uri'] for r in result['links']]
        if not links:raise CoreError('No enabled supported connection',status=503)
        content_type='text/plain; charset=utf-8'
        if fmt in ('raw','base64'):
            body='\n'.join(links).encode();body=base64.b64encode(body) if fmt=='base64' else body
        elif fmt=='json':
            body=json.dumps({'version':1,'title':settings.get('profile_title','DARK XRAY'),'client':email,'announce':settings.get('announce',''),'links':result['links']},ensure_ascii=False,indent=2).encode();content_type='application/json; charset=utf-8'
        else:
            proxies=[]
            for item in result['links']:proxies.append(self._clash_proxy(item['uri'],item['remark']))
            names=[p['name'] for p in proxies];doc={'proxies':proxies,'proxy-groups':[{'name':'DARK AUTO','type':'select','proxies':names}], 'rules':['MATCH,DARK AUTO']}
            body=(self._yaml(doc)+'\n').encode();content_type='application/yaml; charset=utf-8'
        with self.store.lock:r=self.store.db.execute('SELECT * FROM core_clients WHERE email=?',(email,)).fetchone()
        c=json.loads(r['body']);headers={'Content-Type':content_type,'profile-update-interval':str(settings.get('profile_update_interval_hours',6)),
            'profile-title':settings.get('profile_title','DARK XRAY'),
            'subscription-userinfo':f"upload={r['up']}; download={r['down']}; total={c.get('totalGB',0)}; expire={max(0,c.get('expiryTime',0)//1000)}"}
        support=settings.get('support_url','');profile=settings.get('profile_url','')
        if support:headers['support-url']=support
        if profile:headers['profile-web-page-url']=profile
        return body,headers

    def close(self):
        with self.lock:
            try:self.collect_stats(force=True)
            finally:self._stop_child()

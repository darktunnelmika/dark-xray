"""DARK XRAY remote node registry.

Central node credentials are encrypted at rest with the existing DARK Fernet key.
Remote node requests require HTTPS and resolve only to globally routable addresses
so a panel operator cannot accidentally turn the node feature into a private-network
SSRF primitive. Remote agents authenticate dedicated dkn_ tokens, never owner sessions.
"""
from __future__ import annotations
import hashlib
import ipaddress
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dark_policy import PolicyError, NAME_RE, Store


def token_digest(value:str)->str:
    return hashlib.sha256(value.encode()).hexdigest()


def validate_origin(raw:str)->str:
    if not isinstance(raw,str) or len(raw)>500:raise PolicyError('Invalid node URL')
    p=urllib.parse.urlsplit(raw.strip())
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise PolicyError('Node URL must be an HTTPS origin without credentials/path/query')
    host=p.hostname
    try:
        infos=socket.getaddrinfo(host,p.port or 443,type=socket.SOCK_STREAM)
    except OSError as ex:raise PolicyError('Node hostname does not resolve') from ex
    addresses={x[4][0].split('%')[0] for x in infos}
    if not addresses:raise PolicyError('Node hostname has no usable address')
    for raw_ip in addresses:
        try:ip=ipaddress.ip_address(raw_ip)
        except ValueError:raise PolicyError('Node DNS returned an invalid address')
        if not ip.is_global:raise PolicyError('Node must resolve only to globally routable addresses')
    port=p.port
    return f'https://{host}' + (f':{port}' if port and port!=443 else '')


class NodeRegistry:
    def __init__(self,store:Store,cipher):
        self.store,self.cipher=store,cipher
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS remote_nodes(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,origin TEXT NOT NULL UNIQUE,
              token_enc TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL,updated_at REAL NOT NULL,last_seen REAL NOT NULL DEFAULT 0,
              last_latency_ms INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
              last_health TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS node_agent_tokens(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL DEFAULT 1,expires_at REAL NOT NULL,created_at REAL NOT NULL,
              last_used REAL NOT NULL DEFAULT 0);
            ''')

    def list(self)->list[dict]:
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute('SELECT * FROM remote_nodes ORDER BY name,id')]
        for r in rows:
            r.pop('token_enc',None)
            try:r['health']=json.loads(r.pop('last_health','{}'))
            except Exception:r['health']={}
            r['online']=bool(r['last_seen'] and time.time()-r['last_seen']<180 and not r['last_error'])
        return rows

    def get(self,node_id:str,*,secret:bool=False)->dict:
        if not NAME_RE.fullmatch(node_id):raise PolicyError('Invalid node ID')
        with self.store.lock:r=self.store.db.execute('SELECT * FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
        if not r:raise PolicyError('Node not found')
        out=dict(r)
        if secret:
            try:out['token']=self.cipher.decrypt(out['token_enc'].encode()).decode()
            except Exception as ex:raise PolicyError('Node credential cannot be decrypted') from ex
        out.pop('token_enc',None)
        return out

    def put(self,node_id:str,name:str,origin:str,token:str,enabled:bool=True)->dict:
        if not NAME_RE.fullmatch(node_id) or not isinstance(name,str) or not 1<=len(name)<=128:raise PolicyError('Invalid node identity')
        origin=validate_origin(origin)
        if not isinstance(token,str) or not token.startswith('dkn_') or not 40<=len(token)<=256:raise PolicyError('Invalid DARK node token')
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
        enc=self.cipher.encrypt(token.encode()).decode();now=time.time()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO remote_nodes(id,name,origin,token_enc,enabled,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,origin=excluded.origin,
              token_enc=excluded.token_enc,enabled=excluded.enabled,updated_at=excluded.updated_at''',
              (node_id,name,origin,enc,int(enabled),now,now))
        return self.get(node_id)

    def set_enabled(self,node_id:str,enabled:bool)->dict:
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(node_id,)).fetchone():raise PolicyError('Node not found')
            db.execute('UPDATE remote_nodes SET enabled=?,updated_at=? WHERE id=?',(int(enabled),time.time(),node_id))
        return self.get(node_id)

    def delete(self,node_id:str)->dict:
        with self.store.transaction() as db:
            cur=db.execute('DELETE FROM remote_nodes WHERE id=?',(node_id,))
            if not cur.rowcount:raise PolicyError('Node not found')
        return {'deleted':True}

    def _request(self,node_id:str,path:str,method:str='GET',body:dict|None=None,timeout:float=8.0)->tuple[dict,int]:
        node=self.get(node_id,secret=True)
        if not node['enabled']:raise PolicyError('Node is disabled')
        # Resolve again immediately before every connection to reduce DNS-rebinding risk.
        origin=validate_origin(node['origin'])
        data=None if body is None else json.dumps(body).encode()
        req=urllib.request.Request(origin+path,data=data,method=method,headers={
            'Accept':'application/json','Authorization':'Bearer '+node['token'],
            **({'Content-Type':'application/json'} if data is not None else {})})
        start=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=timeout,context=ssl.create_default_context()) as res:
                raw=res.read(1024*1024)
                if res.status<200 or res.status>=300:raise PolicyError('Node returned HTTP '+str(res.status))
        except urllib.error.HTTPError as ex:
            try:detail=json.loads(ex.read(65536).decode()).get('detail','')
            except Exception:detail=''
            raise PolicyError(f'Node HTTP {ex.code}'+(': '+str(detail)[:200] if detail else '')) from ex
        except (urllib.error.URLError,TimeoutError,OSError) as ex:raise PolicyError('Node connection failed: '+type(ex).__name__) from ex
        elapsed=max(1,int((time.monotonic()-start)*1000))
        try:doc=json.loads(raw.decode())
        except Exception as ex:raise PolicyError('Node returned invalid JSON') from ex
        if not isinstance(doc,dict) and not isinstance(doc,list):raise PolicyError('Unexpected node response shape')
        return doc,elapsed

    def probe(self,node_id:str)->dict:
        now=time.time()
        try:
            health,ms=self._request(node_id,'/node/api/health')
            if not isinstance(health,dict) or health.get('service')!='DARK XRAY NODE':raise PolicyError('Remote endpoint is not a DARK node agent')
            with self.store.transaction() as db:db.execute('UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error=?,last_health=?,updated_at=? WHERE id=?',(now,ms,'',json.dumps(health),now,node_id))
            return {'node':self.get(node_id),'latency_ms':ms,'health':health}
        except PolicyError as ex:
            with self.store.transaction() as db:db.execute('UPDATE remote_nodes SET last_error=?,updated_at=? WHERE id=?',(str(ex)[:300],now,node_id))
            raise

    def remote_core(self,node_id:str,action:str)->dict:
        if action not in {'validate','restart','start','stop'}:raise PolicyError('Unsupported remote core action')
        doc,ms=self._request(node_id,'/node/api/core/'+action,'POST',{})
        return {'latency_ms':ms,'result':doc}

    def remote_inbounds(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/inbounds')
        if not isinstance(doc,list):raise PolicyError('Invalid node inbound response')
        return {'latency_ms':ms,'items':doc}

    def deploy_inbound(self,node_id:str,payload:dict)->dict:
        if not isinstance(payload,dict):raise PolicyError('Inbound payload must be an object')
        doc,ms=self._request(node_id,'/node/api/inbounds','POST',payload,12.0)
        if not isinstance(doc,dict) or type(doc.get('id')) is not int:raise PolicyError('Invalid node inbound deploy response')
        return {'latency_ms':ms,'inbound':doc,'applied':False,'next':'validate/restart remote Xray'}

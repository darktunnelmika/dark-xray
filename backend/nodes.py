"""DARK XRAY remote node registry.

Central node credentials are encrypted at rest with the existing DARK Fernet key.
Remote node requests require HTTPS and resolve only to globally routable addresses.
Connections are pinned to the exact address set that passed validation while TLS
still verifies the original hostname, closing redirect/proxy/DNS-rebinding SSRF paths.
Remote agents authenticate dedicated dkn_ tokens, never owner sessions.
"""
from __future__ import annotations
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import time
import urllib.parse
from typing import Any

from dark_policy import PolicyError, NAME_RE, Store


def token_digest(value:str)->str:
    return hashlib.sha256(value.encode()).hexdigest()


def resolve_origin(raw:str)->tuple[str,str,int,tuple[str,...]]:
    if not isinstance(raw,str) or len(raw)>500:raise PolicyError('Invalid node URL')
    try:p=urllib.parse.urlsplit(raw.strip())
    except ValueError as ex:raise PolicyError('Invalid node URL') from ex
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise PolicyError('Node URL must be an HTTPS origin without credentials/path/query')
    try:port=p.port or 443
    except ValueError as ex:raise PolicyError('Invalid node URL port') from ex
    if not 1<=port<=65535:raise PolicyError('Invalid node URL port')
    host=p.hostname
    try:
        literal=ipaddress.ip_address(host)
        host=literal.compressed
    except ValueError:
        try:host=host.encode('idna').decode('ascii').lower()
        except UnicodeError as ex:raise PolicyError('Invalid node hostname') from ex
    try:infos=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except OSError as ex:raise PolicyError('Node hostname does not resolve') from ex
    addresses=[]
    for info in infos:
        raw_ip=info[4][0].split('%')[0]
        try:ip=ipaddress.ip_address(raw_ip)
        except ValueError:raise PolicyError('Node DNS returned an invalid address')
        if not ip.is_global:raise PolicyError('Node must resolve only to globally routable addresses')
        canonical=ip.compressed
        if canonical not in addresses:addresses.append(canonical)
    if not addresses:raise PolicyError('Node hostname has no usable address')
    rendered='['+host+']' if ':' in host else host
    origin=f'https://{rendered}' + (f':{port}' if port!=443 else '')
    return origin,host,port,tuple(addresses)


def validate_origin(raw:str)->str:
    return resolve_origin(raw)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that never resolves the hostname after policy validation."""
    def __init__(self,host:str,port:int,pinned_ip:str,*,timeout:float,context:ssl.SSLContext):
        super().__init__(host,port=port,timeout=timeout,context=context)
        self.pinned_ip=pinned_ip

    def connect(self):
        ip=ipaddress.ip_address(self.pinned_ip)
        family=socket.AF_INET6 if ip.version==6 else socket.AF_INET
        sock=socket.socket(family,socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            if self.source_address:sock.bind(self.source_address)
            target=(ip.compressed,self.port,0,0) if ip.version==6 else (ip.compressed,self.port)
            sock.connect(target)
            self.sock=sock
            if self._tunnel_host:self._tunnel()
            server_hostname=self._tunnel_host or self.host
            self.sock=self._context.wrap_socket(self.sock,server_hostname=server_hostname)
        except BaseException:
            try:sock.close()
            except Exception:pass
            self.sock=None
            raise


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
            r['online']=bool(r['enabled'] and r['last_seen'] and time.time()-r['last_seen']<180 and not r['last_error'])
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
        reset_probe=True
        with self.store.lock:
            old=self.store.db.execute('SELECT origin,token_enc,enabled FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            if old:
                try:old_token=self.cipher.decrypt(old['token_enc'].encode()).decode()
                except Exception:old_token=None
                reset_probe=old['origin']!=origin or old_token!=token or bool(old['enabled'])!=enabled
        enc=self.cipher.encrypt(token.encode()).decode();now=time.time()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO remote_nodes(id,name,origin,token_enc,enabled,created_at,updated_at,last_seen,last_latency_ms,last_error,last_health)
              VALUES(?,?,?,?,?,?,?,0,0,'','{}') ON CONFLICT(id) DO UPDATE SET name=excluded.name,origin=excluded.origin,
              token_enc=excluded.token_enc,enabled=excluded.enabled,updated_at=excluded.updated_at,
              last_seen=CASE WHEN ? THEN 0 ELSE remote_nodes.last_seen END,
              last_latency_ms=CASE WHEN ? THEN 0 ELSE remote_nodes.last_latency_ms END,
              last_error=CASE WHEN ? THEN '' ELSE remote_nodes.last_error END,
              last_health=CASE WHEN ? THEN '{}' ELSE remote_nodes.last_health END''',
              (node_id,name,origin,enc,int(enabled),now,now,int(reset_probe),int(reset_probe),int(reset_probe),int(reset_probe)))
        return self.get(node_id)

    def set_enabled(self,node_id:str,enabled:bool)->dict:
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(node_id,)).fetchone():raise PolicyError('Node not found')
            db.execute("UPDATE remote_nodes SET enabled=?,updated_at=?,last_seen=0,last_latency_ms=0,last_error='',last_health='{}' WHERE id=?",(int(enabled),time.time(),node_id))
        return self.get(node_id)

    def delete(self,node_id:str)->dict:
        with self.store.transaction() as db:
            cur=db.execute('DELETE FROM remote_nodes WHERE id=?',(node_id,))
            if not cur.rowcount:raise PolicyError('Node not found')
        return {'deleted':True}

    def _request_ok(self,node_id:str,latency_ms:int):
        now=time.time()
        with self.store.transaction() as db:
            db.execute("UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error='',updated_at=? WHERE id=?",
                       (now,max(1,int(latency_ms)),now,node_id))

    def _request_failed(self,node_id:str,error:str):
        now=time.time()
        with self.store.transaction() as db:
            db.execute('UPDATE remote_nodes SET last_error=?,updated_at=? WHERE id=?',(str(error)[:300],now,node_id))

    @staticmethod
    def _response_error(status:int,raw:bytes)->PolicyError:
        detail=''
        try:
            parsed=json.loads(raw[:65536].decode())
            if isinstance(parsed,dict):detail=str(parsed.get('detail',''))[:200]
        except Exception:pass
        return PolicyError(f'Node HTTP {status}'+(': '+detail if detail else ''))

    def _request(self,node_id:str,path:str,method:str='GET',body:dict|None=None,timeout:float=8.0)->tuple[dict,int]:
        node=self.get(node_id,secret=True)
        if not node['enabled']:raise PolicyError('Node is disabled')
        if not isinstance(path,str) or not path.startswith('/node/api/') or any(ch in path for ch in '\r\n?#'):
            raise PolicyError('Invalid node API path')
        _origin,host,port,addresses=resolve_origin(node['origin'])
        data=None if body is None else json.dumps(body,separators=(',',':')).encode()
        if data is not None and len(data)>2*1024*1024:raise PolicyError('Node request exceeds 2 MiB limit')
        headers={'Accept':'application/json','Authorization':'Bearer '+node['token']}
        if data is not None:headers['Content-Type']='application/json'
        context=ssl.create_default_context();last_error=None;start=time.monotonic()
        for address in addresses:
            conn=_PinnedHTTPSConnection(host,port,address,timeout=timeout,context=context)
            try:
                conn.request(method,path,body=data,headers=headers)
                res=conn.getresponse();raw=res.read(1024*1024+1)
                if len(raw)>1024*1024:raise PolicyError('Node response exceeds 1 MiB limit')
                if res.status<200 or res.status>=300:raise self._response_error(res.status,raw)
                try:doc=json.loads(raw.decode())
                except Exception as ex:raise PolicyError('Node returned invalid JSON') from ex
                if not isinstance(doc,(dict,list)):raise PolicyError('Unexpected node response shape')
                elapsed=max(1,int((time.monotonic()-start)*1000));self._request_ok(node_id,elapsed)
                return doc,elapsed
            except PolicyError as ex:
                self._request_failed(node_id,str(ex));raise
            except (ssl.SSLError,http.client.HTTPException,TimeoutError,OSError) as ex:
                last_error=ex
            finally:
                try:conn.close()
                except Exception:pass
        err=PolicyError('Node connection failed: '+(type(last_error).__name__ if last_error else 'No validated address'))
        self._request_failed(node_id,str(err));raise err from last_error

    def probe(self,node_id:str)->dict:
        now=time.time()
        try:
            health,ms=self._request(node_id,'/node/api/health')
            if not isinstance(health,dict) or health.get('service')!='DARK XRAY NODE':raise PolicyError('Remote endpoint is not a DARK node agent')
            with self.store.transaction() as db:db.execute('UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error=?,last_health=?,updated_at=? WHERE id=?',(now,ms,'',json.dumps(health),now,node_id))
            return {'node':self.get(node_id),'latency_ms':ms,'health':health}
        except PolicyError as ex:
            self._request_failed(node_id,str(ex));raise

    def remote_core(self,node_id:str,action:str)->dict:
        if action not in {'validate','restart','start','stop'}:raise PolicyError('Unsupported remote core action')
        doc,ms=self._request(node_id,'/node/api/core/'+action,'POST',{})
        if not isinstance(doc,dict) or 'engine' not in doc:
            self._request_failed(node_id,'Invalid remote core response');raise PolicyError('Invalid remote core response')
        return {'latency_ms':ms,'result':doc}

    def remote_inbounds(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/inbounds')
        if not isinstance(doc,list):
            self._request_failed(node_id,'Invalid node inbound response');raise PolicyError('Invalid node inbound response')
        return {'latency_ms':ms,'items':doc}

    def deploy_inbound(self,node_id:str,payload:dict)->dict:
        if not isinstance(payload,dict):raise PolicyError('Inbound payload must be an object')
        doc,ms=self._request(node_id,'/node/api/inbounds','POST',payload,12.0)
        if not isinstance(doc,dict) or type(doc.get('id')) is not int:
            self._request_failed(node_id,'Invalid node inbound deploy response');raise PolicyError('Invalid node inbound deploy response')
        return {'latency_ms':ms,'inbound':doc,'applied':False,'next':'validate/restart remote Xray'}

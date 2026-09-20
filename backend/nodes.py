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
import re
import socket
import ssl
import threading
import time
import urllib.parse
from typing import Any

from dark_policy import PolicyError, NAME_RE, Store, normalize_ip


def token_digest(value:str)->str:
    return hashlib.sha256(value.encode()).hexdigest()


def resolve_origin(raw:str)->tuple[str,str,int,tuple[str,...]]:
    if not isinstance(raw,str) or len(raw)>500:raise PolicyError('Invalid node URL')
    try:p=urllib.parse.urlsplit(raw.strip())
    except ValueError as ex:raise PolicyError('Invalid node URL') from ex
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise PolicyError('Node URL must be an HTTPS origin without credentials/path/query')
    try:parsed_port=p.port
    except ValueError as ex:raise PolicyError('Invalid node URL port') from ex
    port=443 if parsed_port is None else parsed_port
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


def validate_data_address(raw:str,origin:str)->str:
    value=str(raw or '').strip()
    if not value:
        try:value=urllib.parse.urlsplit(origin).hostname or ''
        except ValueError:value=''
    if not value or len(value)>253 or any(ch in value for ch in '/?#@'):
        raise PolicyError('Invalid node data address')
    try:return ipaddress.ip_address(value.strip('[]')).compressed
    except ValueError:pass
    try:value=value.encode('idna').decode('ascii').lower()
    except UnicodeError as ex:raise PolicyError('Invalid node data hostname') from ex
    labels=value.rstrip('.').split('.')
    if not labels or any(not part or len(part)>63 or part[0]=='-' or part[-1]=='-' or
                         any(not (ch.isalnum() or ch=='-') for ch in part) for part in labels):
        raise PolicyError('Invalid node data hostname')
    return value.rstrip('.')


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
        self.stop=threading.Event();self.thread:threading.Thread|None=None
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS remote_nodes(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,origin TEXT NOT NULL UNIQUE,
              token_enc TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL,updated_at REAL NOT NULL,last_seen REAL NOT NULL DEFAULT 0,
              last_latency_ms INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
              last_health TEXT NOT NULL DEFAULT '{}',failure_count INTEGER NOT NULL DEFAULT 0,
              recovery_count INTEGER NOT NULL DEFAULT 0,last_offline_at REAL NOT NULL DEFAULT 0,
              last_recovered_at REAL NOT NULL DEFAULT 0,
              data_address TEXT NOT NULL DEFAULT '',priority INTEGER NOT NULL DEFAULT 100,
              failover_enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS node_agent_tokens(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL DEFAULT 1,expires_at REAL NOT NULL,created_at REAL NOT NULL,
              last_used REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS remote_node_inbounds(
              node_id TEXT NOT NULL,local_inbound_id INTEGER NOT NULL,remote_inbound_id INTEGER NOT NULL DEFAULT 0,
              updated_at REAL NOT NULL,last_sync REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(node_id,local_inbound_id));
            CREATE TABLE IF NOT EXISTS node_agent_mirrors(
              token_id TEXT NOT NULL,source_inbound_id INTEGER NOT NULL,remote_inbound_id INTEGER NOT NULL,
              source_tag TEXT NOT NULL,updated_at REAL NOT NULL,
              PRIMARY KEY(token_id,source_inbound_id));
            CREATE TABLE IF NOT EXISTS node_agent_mirror_clients(
              token_id TEXT NOT NULL,source_inbound_id INTEGER NOT NULL,mirror_email TEXT NOT NULL,source_email TEXT NOT NULL,
              PRIMARY KEY(token_id,source_inbound_id,mirror_email));
            CREATE TABLE IF NOT EXISTS node_agent_mirror_state(
              token_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS remote_node_client_usage(
              node_id TEXT NOT NULL,client_id TEXT NOT NULL,raw_up INTEGER NOT NULL DEFAULT 0,
              raw_down INTEGER NOT NULL DEFAULT 0,current_up INTEGER NOT NULL DEFAULT 0,
              current_down INTEGER NOT NULL DEFAULT 0,seq INTEGER NOT NULL DEFAULT 0,
              initialized INTEGER NOT NULL DEFAULT 0,last_seen REAL NOT NULL DEFAULT 0,
              PRIMARY KEY(node_id,client_id));
            CREATE INDEX IF NOT EXISTS remote_node_usage_client ON remote_node_client_usage(client_id);
            CREATE TABLE IF NOT EXISTS node_agent_traffic_resets(
              token_id TEXT NOT NULL,reset_id TEXT NOT NULL,source_email TEXT NOT NULL,
              up_bytes INTEGER NOT NULL,down_bytes INTEGER NOT NULL,at REAL NOT NULL,
              PRIMARY KEY(token_id,reset_id));
            CREATE TABLE IF NOT EXISTS remote_node_ips(
              node_id TEXT NOT NULL,client_id TEXT NOT NULL,ip TEXT NOT NULL,
              first_seen REAL NOT NULL,last_seen REAL NOT NULL,verified INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(node_id,client_id,ip));
            CREATE INDEX IF NOT EXISTS remote_node_ips_client ON remote_node_ips(client_id,last_seen);
            CREATE TABLE IF NOT EXISTS remote_node_devices(
              node_id TEXT NOT NULL,client_id TEXT NOT NULL,digest TEXT NOT NULL,
              device_os TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '',
              first_seen REAL NOT NULL,last_seen REAL NOT NULL,
              PRIMARY KEY(node_id,client_id,digest));
            CREATE INDEX IF NOT EXISTS remote_node_devices_client ON remote_node_devices(client_id,last_seen);
            CREATE TABLE IF NOT EXISTS remote_node_security_state(
              node_id TEXT PRIMARY KEY,source_verified INTEGER NOT NULL DEFAULT 0,
              last_sync REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS remote_node_desired_state(
              node_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0,
              desired_hash TEXT NOT NULL DEFAULT '',desired_json TEXT NOT NULL DEFAULT '{}',
              updated_at REAL NOT NULL DEFAULT 0,applied_revision INTEGER NOT NULL DEFAULT 0,
              applied_hash TEXT NOT NULL DEFAULT '',applied_at REAL NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '');
            ''')
            node_cols={r[1] for r in store.db.execute('PRAGMA table_info(remote_nodes)')}
            for name,ddl in (
                ('failure_count',"ALTER TABLE remote_nodes ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"),
                ('recovery_count',"ALTER TABLE remote_nodes ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"),
                ('last_offline_at',"ALTER TABLE remote_nodes ADD COLUMN last_offline_at REAL NOT NULL DEFAULT 0"),
                ('last_recovered_at',"ALTER TABLE remote_nodes ADD COLUMN last_recovered_at REAL NOT NULL DEFAULT 0"),
                ('data_address',"ALTER TABLE remote_nodes ADD COLUMN data_address TEXT NOT NULL DEFAULT ''"),
                ('priority',"ALTER TABLE remote_nodes ADD COLUMN priority INTEGER NOT NULL DEFAULT 100"),
                ('failover_enabled',"ALTER TABLE remote_nodes ADD COLUMN failover_enabled INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in node_cols:store.db.execute(ddl)
            # Existing Node V3 records predate data_address. Preserve their
            # behavior without requiring an Edit/Save round trip after upgrade.
            for row in store.db.execute("SELECT id,origin,data_address FROM remote_nodes WHERE data_address=''").fetchall():
                try:address=validate_data_address('',str(row['origin']))
                except PolicyError:continue
                store.db.execute('UPDATE remote_nodes SET data_address=? WHERE id=?',(address,row['id']))

    @staticmethod
    def _runtime_block_reason(node:dict)->str:
        desired=node.get('desired_state') or {}
        if desired.get('last_error'):return 'desired_state_error'
        if desired.get('pending'):return 'desired_state_pending'
        health=node.get('health') or {}
        if not isinstance(health,dict):return 'invalid_health'
        core=health.get('core') or {}
        if not isinstance(core,dict):return 'invalid_health'
        if core.get('state') and core['state']!='running':return 'core_stopped'
        if core.get('dirty') is True:return 'runtime_dirty'
        if core.get('last_error'):return 'core_error'
        if health.get('agent_only') is True and core.get('state')!='running':return 'core_unverified'
        # Older full-panel nodes do not report revisions. Keep their legacy
        # assignment contract, but never ignore an explicit unhealthy report.
        return ''

    @staticmethod
    def _assignment_state(node:dict,assignment:dict,*,now:float|None=None)->dict:
        now=time.time() if now is None else float(now)
        remote_id=int(assignment.get('remote_inbound_id') or 0)
        sync_error=str(assignment.get('last_error') or '')
        runtime_block=NodeRegistry._runtime_block_reason(node)
        deployed=bool(remote_id and not sync_error and not runtime_block)
        online=bool(node.get('enabled') and node.get('last_seen') and now-float(node.get('last_seen') or 0)<180 and not node.get('last_error'))
        if sync_error:deployment_state='sync_error'
        elif runtime_block:deployment_state=runtime_block
        elif remote_id:deployment_state='deployed'
        else:deployment_state='pending'
        if not node.get('enabled'):reason='node_disabled'
        elif sync_error:reason='sync_error'
        elif not remote_id:reason='not_deployed'
        elif runtime_block:reason=runtime_block
        elif not node.get('failover_enabled'):reason='failover_disabled'
        elif not node.get('data_address'):reason='data_address_missing'
        elif not online:reason='node_offline'
        else:reason='ready'
        return {**assignment,'remote_inbound_id':remote_id,'deployment_state':deployment_state,
                'deployed':deployed,'failover_ready':reason=='ready','failover_reason':reason}

    def list(self)->list[dict]:
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute('SELECT * FROM remote_nodes ORDER BY name,id')]
        now=time.time()
        for r in rows:
            r.pop('token_enc',None)
            try:r['health']=json.loads(r.pop('last_health','{}'))
            except Exception:r['health']={}
            with self.store.lock:
                assigned=[dict(x) for x in self.store.db.execute(
                    'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(r['id'],))]
            r['online']=bool(r['enabled'] and r['last_seen'] and now-r['last_seen']<180 and not r['last_error'])
            with self.store.lock:
                ds=self.store.db.execute('SELECT revision,desired_hash,updated_at,applied_revision,applied_hash,applied_at,last_error FROM remote_node_desired_state WHERE node_id=?',(r['id'],)).fetchone()
            desired=dict(ds) if ds else {'revision':0,'desired_hash':'','updated_at':0,'applied_revision':0,'applied_hash':'','applied_at':0,'last_error':''}
            desired['pending']=bool(desired['revision'] and (desired['revision']!=desired['applied_revision'] or desired['desired_hash']!=desired['applied_hash']))
            r['desired_state']=desired
            assigned=[self._assignment_state(r,x,now=now) for x in assigned]
            r['inboundIds']=[int(x['local_inbound_id']) for x in assigned]
            r['assignments']=assigned
            with self.store.lock:
                usage=self.store.db.execute('''SELECT COUNT(*) clients,COALESCE(SUM(current_up+current_down),0) bytes,
                    COALESCE(MAX(last_seen),0) last_sync FROM remote_node_client_usage WHERE node_id=?''',(r['id'],)).fetchone()
            r['traffic_clients']=int(usage['clients'] or 0)
            r['traffic_current_bytes']=int(usage['bytes'] or 0)
            r['traffic_last_sync']=float(usage['last_sync'] or 0)
            with self.store.lock:
                sec=self.store.db.execute('SELECT source_verified,last_sync,last_error FROM remote_node_security_state WHERE node_id=?',(r['id'],)).fetchone()
            r['security']={'source_verified':bool(sec['source_verified']),'last_sync':float(sec['last_sync']),'last_error':sec['last_error']} if sec else {'source_verified':False,'last_sync':0,'last_error':''}
            r['failover_ready']=any(bool(x['failover_ready']) for x in assigned)
            if r['failover_ready']:r['failover_reason']='ready'
            elif not assigned:r['failover_reason']='no_assignments'
            else:r['failover_reason']=next((x['failover_reason'] for x in assigned if x['failover_reason']!='ready'),'not_deployed')
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
        with self.store.lock:
            assigned=[dict(x) for x in self.store.db.execute(
                'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(node_id,))]
        out['inboundIds']=[int(x['local_inbound_id']) for x in assigned]
        out['assignments']=assigned
        return out

    def put(self,node_id:str,name:str,origin:str,token:str,enabled:bool=True,inbound_ids:list[int]|None=None,
            data_address:str='',priority:int=100,failover_enabled:bool=True)->dict:
        if not NAME_RE.fullmatch(node_id) or not isinstance(name,str) or not 1<=len(name)<=128:raise PolicyError('Invalid node identity')
        origin=validate_origin(origin);data_address=validate_data_address(data_address,origin)
        if not isinstance(token,str) or not token.startswith('dkn_') or not 40<=len(token)<=256:raise PolicyError('Invalid DARK node token')
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
        if type(failover_enabled)is not bool:raise PolicyError('failover_enabled must be boolean')
        if type(priority)is not int or not 1<=priority<=1000:raise PolicyError('Invalid node failover priority')
        inbound_ids=[] if inbound_ids is None else inbound_ids
        if not isinstance(inbound_ids,list) or len(inbound_ids)>256 or any(type(i)is not int or i<1 for i in inbound_ids):
            raise PolicyError('Invalid node inbound assignment')
        inbound_ids=sorted(set(inbound_ids))
        reset_probe=True
        with self.store.lock:
            old=self.store.db.execute('SELECT origin,token_enc,enabled FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            if old:
                try:old_token=self.cipher.decrypt(old['token_enc'].encode()).decode()
                except Exception:old_token=None
                reset_probe=old['origin']!=origin or old_token!=token or bool(old['enabled'])!=enabled
        enc=self.cipher.encrypt(token.encode()).decode();now=time.time()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO remote_nodes(id,name,origin,token_enc,enabled,created_at,updated_at,last_seen,last_latency_ms,last_error,last_health,data_address,priority,failover_enabled)
              VALUES(?,?,?,?,?,?,?,0,0,'','{}',?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,origin=excluded.origin,
              token_enc=excluded.token_enc,enabled=excluded.enabled,updated_at=excluded.updated_at,
              data_address=excluded.data_address,priority=excluded.priority,failover_enabled=excluded.failover_enabled,
              last_seen=CASE WHEN ? THEN 0 ELSE remote_nodes.last_seen END,
              last_latency_ms=CASE WHEN ? THEN 0 ELSE remote_nodes.last_latency_ms END,
              last_error=CASE WHEN ? THEN '' ELSE remote_nodes.last_error END,
              last_health=CASE WHEN ? THEN '{}' ELSE remote_nodes.last_health END''',
              (node_id,name,origin,enc,int(enabled),now,now,data_address,priority,int(failover_enabled),
               int(reset_probe),int(reset_probe),int(reset_probe),int(reset_probe)))
            if old and old['origin']!=origin:
                db.execute('UPDATE remote_node_client_usage SET raw_up=0,raw_down=0,initialized=0 WHERE node_id=?',(node_id,))
            old_ids={int(r[0]) for r in db.execute('SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=?',(node_id,))}
            for inbound_id in inbound_ids:
                db.execute('''INSERT INTO remote_node_inbounds(node_id,local_inbound_id,updated_at)
                              VALUES(?,?,?) ON CONFLICT(node_id,local_inbound_id) DO UPDATE SET updated_at=excluded.updated_at''',
                           (node_id,inbound_id,now))
            removed=old_ids-set(inbound_ids)
            if removed:
                db.executemany('DELETE FROM remote_node_inbounds WHERE node_id=? AND local_inbound_id=?',[(node_id,x) for x in removed])
        return self.get(node_id)

    def set_inbound_assignment(self,node_id:str,inbound_id:int,assigned:bool)->dict:
        if type(inbound_id)is not int or inbound_id<1 or type(assigned)is not bool:raise PolicyError('Invalid inbound deployment assignment')
        self.get(node_id);now=time.time()
        with self.store.transaction() as db:
            if assigned:
                db.execute('''INSERT INTO remote_node_inbounds(node_id,local_inbound_id,updated_at)
                              VALUES(?,?,?) ON CONFLICT(node_id,local_inbound_id) DO UPDATE SET updated_at=excluded.updated_at''',
                           (node_id,inbound_id,now))
            else:
                db.execute('DELETE FROM remote_node_inbounds WHERE node_id=? AND local_inbound_id=?',(node_id,inbound_id))
        return {'node_id':node_id,'inbound_id':inbound_id,'assigned':assigned,'updated_at':now}

    def inbound_assignments(self,inbound_id:int)->list[str]:
        if type(inbound_id)is not int or inbound_id<1:raise PolicyError('Invalid inbound ID')
        with self.store.lock:
            return [str(r[0]) for r in self.store.db.execute(
                'SELECT node_id FROM remote_node_inbounds WHERE local_inbound_id=? ORDER BY node_id',(inbound_id,))]

    @staticmethod
    def _desired_payload(value:dict)->tuple[str,str]:
        if not isinstance(value,dict):raise PolicyError('Node desired state must be an object')
        try:raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        except (TypeError,ValueError) as ex:raise PolicyError('Node desired state must be finite JSON') from ex
        encoded=raw.encode()
        if len(encoded)>8*1024*1024:raise PolicyError('Node desired state is too large')
        return raw,hashlib.sha256(encoded).hexdigest()

    def _seal_desired_payload(self,value:dict)->str:
        sealed=json.loads(json.dumps(value,ensure_ascii=False))
        files=sealed.get('files',[])
        if isinstance(files,list):
            for item in files:
                if not isinstance(item,dict) or 'data' not in item:continue
                raw=item.pop('data')
                if not isinstance(raw,str):raise PolicyError('Invalid managed Node file payload')
                item['data_enc']=self.cipher.encrypt(raw.encode()).decode()
        return json.dumps(sealed,sort_keys=True,separators=(',',':'),ensure_ascii=False)

    def _open_desired_payload(self,raw:str)->dict:
        try:value=json.loads(raw)
        except Exception as ex:raise PolicyError('Persisted Node desired state is invalid') from ex
        if not isinstance(value,dict):raise PolicyError('Persisted Node desired state is invalid')
        files=value.get('files',[])
        if isinstance(files,list):
            for item in files:
                if not isinstance(item,dict):continue
                if 'data_enc' in item:
                    enc=item.pop('data_enc')
                    if not isinstance(enc,str):raise PolicyError('Persisted Node file secret is invalid')
                    try:item['data']=self.cipher.decrypt(enc.encode()).decode()
                    except Exception as ex:raise PolicyError('Persisted Node file secret cannot be decrypted') from ex
        return value

    def set_desired_state(self,node_id:str,value:dict)->dict:
        self.get(node_id)
        _raw,digest=self._desired_payload(value);now=time.time()
        with self.store.transaction() as db:
            old=db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
            if old and old['desired_hash']==digest:
                return {'node_id':node_id,'revision':int(old['revision']),'hash':digest,'changed':False,
                        'pending':bool(old['last_error'] or old['revision']!=old['applied_revision'] or digest!=old['applied_hash']),
                        'updated_at':float(old['updated_at'])}
            sealed_raw=self._seal_desired_payload(value)
            revision=int(old['revision'] if old else 0)+1
            applied_revision=int(old['applied_revision']) if old else 0
            applied_hash=str(old['applied_hash']) if old else ''
            applied_at=float(old['applied_at']) if old else 0.
            db.execute('''INSERT INTO remote_node_desired_state(node_id,revision,desired_hash,desired_json,updated_at,applied_revision,applied_hash,applied_at,last_error)
                          VALUES(?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(node_id) DO UPDATE SET revision=excluded.revision,desired_hash=excluded.desired_hash,
                            desired_json=excluded.desired_json,updated_at=excluded.updated_at,last_error=excluded.last_error''',
                       (node_id,revision,digest,sealed_raw,now,applied_revision,applied_hash,applied_at,''))
        return {'node_id':node_id,'revision':revision,'hash':digest,'changed':not old or old['desired_hash']!=digest,
                'pending':revision!=applied_revision or digest!=applied_hash,'updated_at':now}

    def desired_state(self,node_id:str,*,include_payload:bool=True)->dict:
        self.get(node_id)
        with self.store.lock:r=self.store.db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
        if not r:return {'node_id':node_id,'revision':0,'hash':'','payload':{} if include_payload else None,'pending':False}
        out={'node_id':node_id,'revision':int(r['revision']),'hash':r['desired_hash'],'updated_at':float(r['updated_at']),
             'applied_revision':int(r['applied_revision']),'applied_hash':r['applied_hash'],'applied_at':float(r['applied_at']),
             'last_error':r['last_error'],'pending':int(r['revision'])!=int(r['applied_revision']) or r['desired_hash']!=r['applied_hash']}
        if include_payload:out['payload']=self._open_desired_payload(r['desired_json'])
        return out

    def mark_desired_state(self,node_id:str,revision:int,digest:str,*,error:str='')->dict:
        if type(revision)is not int or revision<0 or not isinstance(digest,str) or len(digest)>128:raise PolicyError('Invalid node apply acknowledgement')
        self.get(node_id);now=time.time()
        with self.store.transaction() as db:
            row=db.execute('SELECT revision,desired_hash FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
            if not row:raise PolicyError('Node desired state is missing')
            if revision!=int(row['revision']) or digest!=row['desired_hash']:raise PolicyError('Node acknowledged a stale desired state')
            if error:
                db.execute('UPDATE remote_node_desired_state SET last_error=? WHERE node_id=?',(str(error)[:500],node_id))
            else:
                if revision!=int(row['revision']) or digest!=row['desired_hash']:raise PolicyError('Node acknowledged a stale desired state')
                db.execute('''UPDATE remote_node_desired_state SET applied_revision=?,applied_hash=?,applied_at=?,last_error='' WHERE node_id=?''',
                           (revision,digest,now,node_id))
        return self.desired_state(node_id,include_payload=False)

    def set_enabled(self,node_id:str,enabled:bool)->dict:
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(node_id,)).fetchone():raise PolicyError('Node not found')
            db.execute("UPDATE remote_nodes SET enabled=?,updated_at=?,last_seen=0,last_latency_ms=0,last_error='',last_health='{}' WHERE id=?",(int(enabled),time.time(),node_id))
        return self.get(node_id)

    def delete(self,node_id:str)->dict:
        with self.store.transaction() as db:
            db.execute('DELETE FROM remote_node_inbounds WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_client_usage WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_ips WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_devices WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_security_state WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_desired_state WHERE node_id=?',(node_id,))
            cur=db.execute('DELETE FROM remote_nodes WHERE id=?',(node_id,))
            if not cur.rowcount:raise PolicyError('Node not found')
        return {'deleted':True}

    def _request_ok(self,node_id:str,latency_ms:int):
        now=time.time()
        with self.store.transaction() as db:
            old=db.execute('SELECT last_error FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            recovered=bool(old and old['last_error'])
            db.execute('''UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error='',updated_at=?,
                       failure_count=0,recovery_count=recovery_count+?,last_recovered_at=CASE WHEN ? THEN ? ELSE last_recovered_at END
                       WHERE id=?''',
                       (now,max(1,int(latency_ms)),now,int(recovered),int(recovered),now,node_id))

    def _request_failed(self,node_id:str,error:str):
        now=time.time()
        with self.store.transaction() as db:
            old=db.execute('SELECT last_error FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            first=bool(old and not old['last_error'])
            db.execute('''UPDATE remote_nodes SET last_error=?,updated_at=?,failure_count=failure_count+1,
                          last_offline_at=CASE WHEN ? THEN ? ELSE last_offline_at END WHERE id=?''',
                       (str(error)[:300],now,int(first),now,node_id))

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
        if not .2<=timeout<=30:raise PolicyError('Invalid node timeout')
        _origin,host,port,addresses=resolve_origin(node['origin'])
        data=None if body is None else json.dumps(body,separators=(',',':')).encode()
        if data is not None and len(data)>8*1024*1024:raise PolicyError('Node request exceeds 8 MiB limit')
        headers={'Accept':'application/json','Authorization':'Bearer '+node['token']}
        if data is not None:headers['Content-Type']='application/json'
        context=ssl.create_default_context();last_error=None;start=time.monotonic();deadline=start+timeout
        for address in addresses:
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            conn=_PinnedHTTPSConnection(host,port,address,timeout=max(.2,remaining),context=context)
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
        err=PolicyError('Node connection failed: '+(type(last_error).__name__ if last_error else 'Timeout'))
        self._request_failed(node_id,str(err));raise err from last_error

    def probe(self,node_id:str,*,timeout:float=8.0)->dict:
        now=time.time()
        health,ms=self._request(node_id,'/node/api/health',timeout=timeout)
        if not isinstance(health,dict) or health.get('service')!='DARK XRAY NODE':
            self._request_failed(node_id,'Remote endpoint is not a DARK node agent')
            raise PolicyError('Remote endpoint is not a DARK node agent')
        with self.store.transaction() as db:db.execute('UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error=?,last_health=?,updated_at=? WHERE id=?',(now,ms,'',json.dumps(health),now,node_id))
        return {'node':self.get(node_id),'latency_ms':ms,'health':health}


    def _allowed_traffic_clients(self,node_id:str)->set[str]:
        with self.store.lock:
            assigned={int(r[0]) for r in self.store.db.execute(
                'SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=?',(node_id,))}
            rows=self.store.db.execute("SELECT email,inbounds FROM managed_clients WHERE state!='deleted'").fetchall()
        allowed=set()
        for row in rows:
            try:ids={int(x) for x in json.loads(row['inbounds'])}
            except Exception:continue
            if ids & assigned:allowed.add(str(row['email']))
        return allowed

    @staticmethod
    def _usage_event_id(node_id:str,client_id:str,seq:int)->str:
        key=hashlib.sha256((node_id+'\0'+client_id).encode()).hexdigest()[:24]
        return 'node:'+key+':'+str(seq)

    @staticmethod
    def _recompute_client_usage(db,client_id:str):
        meta=db.execute('SELECT last_up,last_down FROM managed_clients WHERE email=?',(client_id,)).fetchone()
        if not meta:return
        remote=int(db.execute('SELECT COALESCE(SUM(current_up+current_down),0) FROM remote_node_client_usage WHERE client_id=?',(client_id,)).fetchone()[0])
        total=int(meta['last_up'])+int(meta['last_down'])+remote
        if total<0 or total>(1<<63)-1:raise PolicyError('Global client traffic counter overflow')
        db.execute('UPDATE clients SET used_bytes=? WHERE id=?',(total,client_id))

    def apply_traffic_snapshot(self,node_id:str,items:list[dict],*,captured_at:float|None=None)->dict:
        if not isinstance(items,list) or len(items)>100000:raise PolicyError('Invalid node traffic snapshot')
        allowed=self._allowed_traffic_clients(node_id);now=time.time() if captured_at is None else float(captured_at)
        seen=set();charged_up=charged_down=0;baselined=0;resets=0;ignored=0;changed=[]
        with self.store.transaction() as db:
            for item in items:
                if not isinstance(item,dict) or set(item)-{'sourceEmail','up','down'}:raise PolicyError('Invalid node traffic item')
                email=item.get('sourceEmail');up=item.get('up');down=item.get('down')
                if not isinstance(email,str):raise PolicyError('Invalid node traffic client')
                if email not in allowed:
                    ignored+=1;continue
                if email in seen:raise PolicyError('Duplicate node traffic client')
                if type(up)is not int or type(down)is not int or up<0 or down<0 or up>(1<<63)-1 or down>(1<<63)-1 or up+down>(1<<63)-1:
                    raise PolicyError('Invalid node traffic counter')
                seen.add(email)
                row=db.execute('SELECT * FROM remote_node_client_usage WHERE node_id=? AND client_id=?',(node_id,email)).fetchone()
                if not row:
                    db.execute('''INSERT INTO remote_node_client_usage(node_id,client_id,raw_up,raw_down,initialized,last_seen)
                                  VALUES(?,?,?,?,1,?)''',(node_id,email,up,down,now))
                    baselined+=1;changed.append(email);self._recompute_client_usage(db,email);continue
                if not row['initialized']:
                    db.execute('''UPDATE remote_node_client_usage SET raw_up=?,raw_down=?,initialized=1,last_seen=?
                                  WHERE node_id=? AND client_id=?''',(up,down,now,node_id,email))
                    baselined+=1;changed.append(email);self._recompute_client_usage(db,email);continue
                du=up-row['raw_up'] if up>=row['raw_up'] else up
                dd=down-row['raw_down'] if down>=row['raw_down'] else down
                if up<row['raw_up'] or down<row['raw_down']:resets+=1
                seq=int(row['seq'])
                if du or dd:
                    if du+dd>(1<<63)-1:raise PolicyError('Node traffic delta overflow')
                    user=db.execute('SELECT owner FROM clients WHERE id=?',(email,)).fetchone()
                    if not user:raise PolicyError('Node traffic client is not managed')
                    owner=db.execute('SELECT period FROM owners WHERE id=?',(user['owner'],)).fetchone()
                    if not owner:raise PolicyError('Node traffic owner is missing')
                    seq+=1
                    db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',
                               (self._usage_event_id(node_id,email,seq),user['owner'],email,owner['period'],du,dd,now))
                    charged_up+=du;charged_down+=dd
                new_up=int(row['current_up'])+du;new_down=int(row['current_down'])+dd
                if new_up+new_down>(1<<63)-1:raise PolicyError('Node current traffic overflow')
                db.execute('''UPDATE remote_node_client_usage SET raw_up=?,raw_down=?,current_up=?,current_down=?,
                              seq=?,initialized=1,last_seen=? WHERE node_id=? AND client_id=?''',
                           (up,down,new_up,new_down,seq,now,node_id,email))
                changed.append(email);self._recompute_client_usage(db,email)
        return {'clients':len(seen),'ignored_clients':ignored,'baselined':baselined,'charged_up':charged_up,'charged_down':charged_down,
                'charged_bytes':charged_up+charged_down,'counter_resets':resets,'captured_at':now,'changed_clients':changed}

    def sync_traffic(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/mirrors/traffic',timeout=12.0)
        if not isinstance(doc,dict) or not isinstance(doc.get('items'),list):
            self._request_failed(node_id,'Invalid node traffic response');raise PolicyError('Invalid node traffic response')
        result=self.apply_traffic_snapshot(node_id,doc['items'],captured_at=time.time())
        return {'latency_ms':ms,**result}


    def _client_inbounds(self,client_id:str)->list[int]:
        with self.store.lock:
            row=self.store.db.execute("SELECT inbounds FROM managed_clients WHERE email=? AND state!='deleted'",(client_id,)).fetchone()
        if not row:return []
        try:return sorted({int(x) for x in json.loads(row['inbounds']) if int(x)>0})
        except Exception:raise PolicyError('Invalid persisted client inbound assignment')

    def _assigned_node_ids(self,client_id:str)->list[str]:
        inbound_ids=self._client_inbounds(client_id)
        if not inbound_ids:return []
        marks=','.join('?' for _ in inbound_ids)
        with self.store.lock:
            rows=self.store.db.execute(
                'SELECT DISTINCT r.node_id FROM remote_node_inbounds r JOIN remote_nodes n ON n.id=r.node_id '
                'WHERE n.enabled=1 AND r.remote_inbound_id>0 AND r.local_inbound_id IN ('+marks+') ORDER BY r.node_id',
                tuple(inbound_ids)).fetchall()
        return [str(r[0]) for r in rows]

    @staticmethod
    def _security_stamp(value)->float:
        if isinstance(value,bool) or not isinstance(value,(int,float)):raise PolicyError('Invalid node security timestamp')
        value=float(value)
        if not 0<=value<1e15:raise PolicyError('Invalid node security timestamp')
        return value

    def sync_security(self,node_id:str)->dict:
        try:
            doc,ms=self._request(node_id,'/node/api/mirrors/security',timeout=12.0)
            if not isinstance(doc,dict) or type(doc.get('sourceVerified')) is not bool or not isinstance(doc.get('items'),list):
                raise PolicyError('Invalid node security response')
            if len(doc['items'])>100000:raise PolicyError('Node security response is too large')
            allowed=self._allowed_traffic_clients(node_id);ips=[];devices=[];ignored=0;seen=set()
            for item in doc['items']:
                if not isinstance(item,dict) or set(item)-{'sourceEmail','ips','devices'}:
                    raise PolicyError('Invalid node security item')
                email=item.get('sourceEmail')
                if not isinstance(email,str) or len(email)>128:raise PolicyError('Invalid node security client')
                if email not in allowed:
                    ignored+=1;continue
                if email in seen:raise PolicyError('Duplicate node security client')
                seen.add(email)
                raw_ips=item.get('ips',[]);raw_devices=item.get('devices',[])
                if not isinstance(raw_ips,list) or not isinstance(raw_devices,list) or len(raw_ips)>10000 or len(raw_devices)>10000:
                    raise PolicyError('Invalid node security collection')
                for row in raw_ips:
                    if not isinstance(row,dict) or set(row)!={'ip','firstSeen','lastSeen'}:raise PolicyError('Invalid node IP observation')
                    ip=normalize_ip(row.get('ip',''));first=self._security_stamp(row.get('firstSeen'));last=self._security_stamp(row.get('lastSeen'))
                    if first>last:raise PolicyError('Invalid node IP observation time')
                    ips.append((node_id,email,ip,first,last,int(doc['sourceVerified'])))
                for row in raw_devices:
                    if not isinstance(row,dict) or set(row)!={'digest','deviceOs','model','firstSeen','lastSeen'}:
                        raise PolicyError('Invalid node device observation')
                    digest=str(row.get('digest','')).lower()
                    if len(digest)!=64 or any(ch not in '0123456789abcdef' for ch in digest):
                        raise PolicyError('Invalid node device digest')
                    first=self._security_stamp(row.get('firstSeen'));last=self._security_stamp(row.get('lastSeen'))
                    if first>last:raise PolicyError('Invalid node device observation time')
                    os_name=str(row.get('deviceOs',''))[:80];model=str(row.get('model',''))[:120]
                    devices.append((node_id,email,digest,os_name,model,first,last))
            now=time.time()
            with self.store.transaction() as db:
                db.execute('DELETE FROM remote_node_ips WHERE node_id=?',(node_id,))
                db.execute('DELETE FROM remote_node_devices WHERE node_id=?',(node_id,))
                if ips:db.executemany('INSERT INTO remote_node_ips(node_id,client_id,ip,first_seen,last_seen,verified) VALUES(?,?,?,?,?,?)',ips)
                if devices:db.executemany('INSERT INTO remote_node_devices(node_id,client_id,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)',devices)
                db.execute("""INSERT INTO remote_node_security_state(node_id,source_verified,last_sync,last_error) VALUES(?,?,?,'')
                              ON CONFLICT(node_id) DO UPDATE SET source_verified=excluded.source_verified,last_sync=excluded.last_sync,last_error=''""",
                           (node_id,int(doc['sourceVerified']),now))
            return {'latency_ms':ms,'clients':len(seen),'ips':len(ips),'devices':len(devices),
                    'ignored_clients':ignored,'source_verified':bool(doc['sourceVerified']),'synced_at':now}
        except PolicyError as ex:
            with self.store.transaction() as db:
                db.execute('''INSERT INTO remote_node_security_state(node_id,source_verified,last_sync,last_error) VALUES(?,0,0,?)
                              ON CONFLICT(node_id) DO UPDATE SET source_verified=0,last_error=excluded.last_error''',
                           (node_id,str(ex)[:300]))
            raise

    def reconcile_global_security(self,*,local_source_verified:bool,now:float|None=None,persist:bool=True)->dict:
        if type(local_source_verified)is not bool:raise PolicyError('local_source_verified must be boolean')
        if type(persist)is not bool:raise PolicyError('persist must be boolean')
        now=time.time() if now is None else float(now)
        with self.store.lock:
            table=self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_clients'").fetchone()
            if not table:return {'clients':0,'changed':[],'items':[]}
            row=self.store.db.execute("SELECT body FROM core_sections WHERE name='ipguard'").fetchone()
            try:window=max(10,int(json.loads(row[0]).get('window_seconds',120))) if row else 120
            except Exception:window=120
            remote_only=set()
            for inbound in self.store.db.execute('SELECT id,body FROM core_inbounds'):
                try:
                    body=json.loads(inbound['body']);meta=body.get('panelMeta',{})
                    if isinstance(meta,dict) and meta.get('deployLocal') is False:remote_only.add(int(inbound['id']))
                except (ValueError,TypeError,AttributeError):
                    pass  # Unknown scope still requires Local verification.
            metas=[dict(r) for r in self.store.db.execute('''SELECT c.id,c.limit_ip,m.desired,m.inbounds,
                       c.global_ip_block,c.global_device_block FROM clients c
                       JOIN managed_clients m ON m.email=c.id WHERE m.state!='deleted' ORDER BY c.id''')]
        changed=[];items=[]
        for meta in metas:
            client_id=str(meta['id']);assigned=self._assigned_node_ids(client_id)
            try:local_required=any(int(i) not in remote_only for i in json.loads(meta['inbounds']))
            except (ValueError,TypeError):local_required=True
            states={}
            if assigned:
                marks=','.join('?' for _ in assigned)
                with self.store.lock:
                    states={str(r['node_id']):dict(r) for r in self.store.db.execute(
                        'SELECT * FROM remote_node_security_state WHERE node_id IN ('+marks+')',tuple(assigned))}
            fresh=all(n in states and states[n]['last_sync'] and now-float(states[n]['last_sync'])<180
                      and not states[n]['last_error'] for n in assigned)
            verified=fresh and all(bool(states[n]['source_verified']) for n in assigned)
            ip_values=set()
            if local_source_verified and local_required:
                with self.store.lock:
                    ip_values.update(str(r[0]) for r in self.store.db.execute(
                        'SELECT DISTINCT ip FROM observations WHERE client_id=? AND last_seen>?',(client_id,now-window)))
            if assigned:
                marks=','.join('?' for _ in assigned)
                with self.store.lock:
                    ip_values.update(str(r[0]) for r in self.store.db.execute(
                        'SELECT DISTINCT ip FROM remote_node_ips WHERE client_id=? AND verified=1 AND last_seen>? '
                        'AND node_id IN ('+marks+')',(client_id,now-window,*assigned)))
            ip_complete=bool(assigned) and (not local_required or local_source_verified) and verified
            limit_ip=int(meta['limit_ip'] or 0)
            if not limit_ip or not assigned:
                ip_block=False
            elif not ip_complete:
                ip_block=bool(meta['global_ip_block'])
            else:
                ip_block=len(ip_values)>limit_ip

            try:limit_hwid=int(json.loads(meta['desired']).get('limitHwid',0) or 0)
            except Exception:limit_hwid=0
            device_values=set()
            with self.store.lock:
                device_table=self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_devices'").fetchone()
                if device_table:
                    device_values.update(str(r[0]) for r in self.store.db.execute('SELECT digest FROM core_devices WHERE email=?',(client_id,)))
            if assigned:
                marks=','.join('?' for _ in assigned)
                with self.store.lock:
                    device_values.update(str(r[0]) for r in self.store.db.execute(
                        'SELECT DISTINCT digest FROM remote_node_devices WHERE client_id=? AND node_id IN ('+marks+')',(client_id,*assigned)))
            device_complete=bool(assigned) and fresh
            if not limit_hwid or not assigned:
                device_block=False
            elif not device_complete:
                device_block=bool(meta['global_device_block'])
            else:
                device_block=len(device_values)>limit_hwid
            if bool(meta['global_ip_block'])!=ip_block or bool(meta['global_device_block'])!=device_block:
                if persist:
                    with self.store.transaction() as db:
                        db.execute('UPDATE clients SET global_ip_block=?,global_device_block=? WHERE id=?',
                                   (int(ip_block),int(device_block),client_id))
                    changed.append(client_id)
            items.append({'client_id':client_id,'nodes':assigned,'ip_count':len(ip_values),'limit_ip':limit_ip,
                          'ip_enforceable':ip_complete,'local_observation_required':local_required,'ip_blocked':ip_block,'device_count':len(device_values),
                          'limit_hwid':limit_hwid,'device_complete':device_complete,'device_blocked':device_block})
        return {'clients':len(items),'changed':changed,'items':items,'window_seconds':window}

    def global_security(self,client_id:str,*,local_source_verified:bool)->dict:
        result=self.reconcile_global_security(local_source_verified=local_source_verified,persist=False)
        item=next((x for x in result['items'] if x['client_id']==client_id),None)
        if item is None:raise PolicyError('Managed client not found')
        now=time.time();window=result['window_seconds'];assigned=item['nodes']
        with self.store.lock:
            local_ips=[dict(r) for r in self.store.db.execute(
                'SELECT ip,node,first_seen,last_seen,granted FROM observations WHERE client_id=? AND last_seen>? ORDER BY last_seen DESC',
                (client_id,now-window))]
            local_devices=[dict(r) for r in self.store.db.execute(
                'SELECT id,device_os,model,first_seen,last_seen FROM core_devices WHERE email=? ORDER BY last_seen DESC',(client_id,))]
            remote_ips=[dict(r) for r in self.store.db.execute(
                'SELECT node_id,ip,first_seen,last_seen,verified FROM remote_node_ips WHERE client_id=? ORDER BY last_seen DESC',(client_id,))]
            remote_devices=[dict(r) for r in self.store.db.execute(
                'SELECT node_id,digest,device_os,model,first_seen,last_seen FROM remote_node_devices WHERE client_id=? ORDER BY last_seen DESC',(client_id,))]
        for row in remote_devices:
            row['device_id']='node:'+row['node_id']+':'+row.pop('digest')[:16]
        return {**item,'local_ips':local_ips,'remote_ips':remote_ips,
                'local_devices':local_devices,'remote_devices':remote_devices}

    def failover_targets(self,client_id:str)->list[dict]:
        inbound_ids=set(self._client_inbounds(client_id))
        if not inbound_ids:return []
        targets=[]
        for node in self.list():
            ids=[int(a['local_inbound_id']) for a in node['assignments']
                 if int(a['local_inbound_id']) in inbound_ids and a['failover_ready']]
            if not ids:continue
            targets.append({'node_id':node['id'],'name':node['name'],'address':node['data_address'],
                'priority':int(node['priority']),'latency_ms':int(node['last_latency_ms'] or 0),
                'recovery_count':int(node['recovery_count'] or 0),'inbound_ids':ids})
        return sorted(targets,key=lambda x:(x['priority'],x['latency_ms'] or 10**9,x['name'],x['node_id']))

    def clear_remote_security(self,client_id:str,kind:str)->dict:
        if kind not in {'ips','devices','all'}:raise PolicyError('Invalid node security clear kind')
        node_ids=self._assigned_node_ids(client_id);items=[]
        for node_id in node_ids:
            doc,ms=self._request(node_id,'/node/api/mirrors/security/clear','POST',
                                 {'sourceEmail':client_id,'kind':kind},12.0)
            if not isinstance(doc,dict) or doc.get('sourceEmail')!=client_id:
                raise PolicyError('Invalid node security clear response')
            items.append({'node_id':node_id,'latency_ms':ms,'result':doc})
        with self.store.transaction() as db:
            if kind in {'ips','all'}:db.execute('DELETE FROM remote_node_ips WHERE client_id=?',(client_id,))
            if kind in {'devices','all'}:db.execute('DELETE FROM remote_node_devices WHERE client_id=?',(client_id,))
        return {'nodes':len(items),'items':items,'kind':kind}

    def reset_client_traffic(self,client_id:str,reset_id:str)->dict:
        if not isinstance(reset_id,str) or not 8<=len(reset_id)<=128:raise PolicyError('Invalid remote reset ID')
        with self.store.lock:
            meta=self.store.db.execute("SELECT inbounds FROM managed_clients WHERE email=? AND state!='deleted'",(client_id,)).fetchone()
            if not meta:return {'nodes':0,'reset':True}
            inbound_ids=sorted({int(x) for x in json.loads(meta['inbounds'])})
            if not inbound_ids:return {'nodes':0,'reset':True}
            marks=','.join('?' for _ in inbound_ids)
            node_ids=[r[0] for r in self.store.db.execute(
                'SELECT DISTINCT node_id FROM remote_node_inbounds WHERE remote_inbound_id>0 AND local_inbound_id IN ('+marks+') ORDER BY node_id',
                tuple(inbound_ids))]
        results=[]
        for node_id in node_ids:
            doc,ms=self._request(node_id,'/node/api/mirrors/traffic/reset','POST',
                                  {'sourceEmail':client_id,'resetId':reset_id},12.0)
            if not isinstance(doc,dict) or doc.get('sourceEmail')!=client_id or type(doc.get('up')) is not int or type(doc.get('down')) is not int:
                raise PolicyError('Invalid node traffic reset response')
            snap=self.apply_traffic_snapshot(node_id,[{'sourceEmail':client_id,'up':doc['up'],'down':doc['down']}],
                                             captured_at=time.time())
            with self.store.transaction() as db:
                db.execute('''UPDATE remote_node_client_usage SET raw_up=0,raw_down=0,current_up=0,current_down=0,
                              initialized=1,last_seen=? WHERE node_id=? AND client_id=?''',(time.time(),node_id,client_id))
                self._recompute_client_usage(db,client_id)
            results.append({'node_id':node_id,'latency_ms':ms,'snapshot':snap,'cached':bool(doc.get('cached'))})
        return {'nodes':len(results),'items':results,'reset':True}

    def start(self,*,interval:float=60.0,initial_delay:float=5.0,sync_provider=None,desired_provider=None,traffic_callback=None,security_callback=None):
        if self.thread and self.thread.is_alive():return
        if interval<=0 or initial_delay<0:raise ValueError('Invalid node monitor interval')
        if sync_provider is not None and not callable(sync_provider):raise ValueError('sync_provider must be callable')
        if desired_provider is not None and not callable(desired_provider):raise ValueError('desired_provider must be callable')
        if traffic_callback is not None and not callable(traffic_callback):raise ValueError('traffic_callback must be callable')
        if security_callback is not None and not callable(security_callback):raise ValueError('security_callback must be callable')
        self.stop.clear()
        def run():
            if self.stop.wait(initial_delay):return
            while not self.stop.is_set():
                with self.store.lock:ids=[r[0] for r in self.store.db.execute('SELECT id FROM remote_nodes WHERE enabled=1 ORDER BY id')]
                for node_id in ids:
                    if self.stop.is_set():return
                    desired_state=None
                    if desired_provider is not None:
                        try:desired_state=desired_provider(node_id)
                        except (PolicyError,OSError,ValueError):desired_state=None
                    try:
                        self.probe(node_id,timeout=5.0)
                        traffic=self.sync_traffic(node_id)
                        if traffic_callback is not None and traffic.get('charged_bytes'):traffic_callback(node_id,traffic)
                        if security_callback is not None:
                            try:
                                security=self.sync_security(node_id);security_callback(node_id,security)
                            except (PolicyError,OSError,ValueError):
                                pass
                        if desired_provider is not None:
                            legacy_bundles=sync_provider(node_id) if sync_provider is not None else None
                            # Traffic/security callbacks may just have disabled a client.
                            # Persist before probing for offline visibility, but rebuild here
                            # so this cycle never sends the pre-quota/pre-block payload.
                            desired_state=desired_provider(node_id)
                            self.sync_desired_state(node_id,desired_state,legacy_bundles=legacy_bundles)
                            post=self.sync_traffic(node_id)
                            if traffic_callback is not None and post.get('charged_bytes'):traffic_callback(node_id,post)
                            if security_callback is not None:
                                try:
                                    security=self.sync_security(node_id);security_callback(node_id,security)
                                except (PolicyError,OSError,ValueError):
                                    pass
                        elif sync_provider is not None:
                            self.sync_mirrors(node_id,sync_provider(node_id))
                            post=self.sync_traffic(node_id)
                            if traffic_callback is not None and post.get('charged_bytes'):traffic_callback(node_id,post)
                            if security_callback is not None:
                                try:
                                    security=self.sync_security(node_id);security_callback(node_id,security)
                                except (PolicyError,OSError,ValueError):
                                    pass
                    except (PolicyError,OSError,ValueError):
                        pass
                if self.stop.wait(interval):return
        self.thread=threading.Thread(target=run,name='dark-node-health',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=6.0)
        self.thread=None

    def remote_logs(self,node_id:str,kind:str='process',limit:int=300)->dict:
        if kind not in {'process','error','access'}:raise PolicyError('Unknown Node log kind')
        if type(limit)is not int or not 1<=limit<=1000:raise PolicyError('Invalid Node log limit')
        doc,ms=self._request(node_id,'/node/api/logs/'+kind,timeout=12.0)
        if not isinstance(doc,dict) or doc.get('kind')!=kind or not isinstance(doc.get('lines'),list):
            raise PolicyError('Invalid Node log response')
        return {'latency_ms':ms,'kind':kind,'lines':[str(x)[:2000] for x in doc['lines'][-limit:]]}

    def remote_update_status(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/v1/update/status',timeout=12.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('update'),dict):
            raise PolicyError('Invalid Node update status response')
        return {'latency_ms':ms,'update':doc['update']}

    def remote_update_check(self,node_id:str,commit:str)->dict:
        if not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit):raise PolicyError('Exact Hub commit required')
        doc,ms=self._request(node_id,'/node/api/v1/update/check','POST',{'commit':commit},30.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('update'),dict):
            raise PolicyError('Invalid Node update check response')
        return {'latency_ms':ms,'update':doc['update']}

    def remote_update_start(self,node_id:str,commit:str)->dict:
        if not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit):raise PolicyError('Exact Hub commit required')
        doc,ms=self._request(node_id,'/node/api/v1/update/start','POST',{'commit':commit},30.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('update'),dict):
            raise PolicyError('Invalid Node update start response')
        return {'latency_ms':ms,'update':doc['update']}

    def rotate_token(self,node_id:str,new_token:str)->dict:
        if not isinstance(new_token,str) or not new_token.startswith('dkn_') or not 40<=len(new_token)<=256:
            raise PolicyError('Invalid replacement DARK node token')
        doc,ms=self._request(node_id,'/node/api/v1/token/rotate','POST',{'token':new_token},12.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or doc.get('rotated') is not True:
            raise PolicyError('Node did not confirm token rotation')
        enc=self.cipher.encrypt(new_token.encode()).decode()
        with self.store.transaction() as db:db.execute('UPDATE remote_nodes SET token_enc=?,updated_at=? WHERE id=?',(enc,time.time(),node_id))
        return {'rotated':True,'latency_ms':ms}

    def remote_core(self,node_id:str,action:str)->dict:
        if action not in {'validate','restart','start','stop'}:raise PolicyError('Unsupported remote core action')
        doc,ms=self._request(node_id,'/node/api/core/'+action,'POST',{})
        if not isinstance(doc,dict) or 'engine' not in doc:
            self._request_failed(node_id,'Invalid remote core response');raise PolicyError('Invalid remote core response')
        if action!='validate' and isinstance(doc.get('engine'),dict):
            with self.store.transaction() as db:
                row=db.execute('SELECT last_health FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
                try:health=json.loads(row['last_health'])
                except (ValueError,TypeError):health={}
                if not isinstance(health,dict):health={}
                health['core']=doc['engine']
                db.execute('UPDATE remote_nodes SET last_health=? WHERE id=?',(json.dumps(health),node_id))
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

    def assignments(self,node_id:str)->list[dict]:
        self.get(node_id)
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute(
                'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(node_id,))]

    def sync_desired_state(self,node_id:str,state:dict,*,legacy_bundles:list[dict]|None=None)->dict:
        if not isinstance(state,dict) or type(state.get('revision')) is not int or not isinstance(state.get('hash'),str) or not isinstance(state.get('payload'),dict):
            raise PolicyError('Invalid Hub desired-state envelope')
        body={'revision':state['revision'],'hash':state['hash'],'payload':state['payload']}
        try:
            doc,ms=self._request(node_id,'/node/api/v1/state/apply','POST',body,30.0)
        except PolicyError as ex:
            if legacy_bundles is not None and str(ex).startswith('Node HTTP 404'):
                legacy=self.sync_mirrors(node_id,legacy_bundles)
                # Legacy full-panel nodes cannot truthfully acknowledge sections
                # that only the lightweight Node Agent can own.
                self.mark_desired_state(node_id,state['revision'],state['hash'],error='legacy node: inbound/client mirror only; upgrade to agent-only runtime')
                return {**legacy,'legacy':True,'desired_revision':state['revision'],'desired_hash':state['hash'],
                        'desired_state_applied':False}
            self.mark_desired_state(node_id,state['revision'],state['hash'],error=str(ex))
            raise
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or doc.get('appliedRevision')!=state['revision'] or doc.get('appliedHash')!=state['hash']:
            error='Node returned an invalid desired-state acknowledgement'
            self.mark_desired_state(node_id,state['revision'],state['hash'],error=error)
            self._request_failed(node_id,error);raise PolicyError(error)
        items=doc.get('items')
        desired_sources={x['sourceInboundId'] for x in state['payload'].get('assignments',[])
                         if isinstance(x,dict) and type(x.get('sourceInboundId')) is int}
        by_source={}
        valid=isinstance(items,list)
        for item in items if valid else []:
            if (not isinstance(item,dict) or type(item.get('sourceInboundId')) is not int
                    or item['sourceInboundId'] not in desired_sources or item['sourceInboundId'] in by_source
                    or type(item.get('remoteInboundId')) is not int or item['remoteInboundId']<1 or item.get('error')):
                valid=False;break
            by_source[item['sourceInboundId']]=item
        if not valid or set(by_source)!=desired_sources:
            error='Node returned incomplete or invalid assignment acknowledgements'
            self.mark_desired_state(node_id,state['revision'],state['hash'],error=error)
            self._request_failed(node_id,error);raise PolicyError(error)
        now=time.time()
        with self.store.transaction() as db:
            current=db.execute('SELECT revision,desired_hash FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
            if not current or int(current['revision'])!=state['revision'] or current['desired_hash']!=state['hash']:
                raise PolicyError('Node acknowledged a stale desired state')
            assigned={int(r[0]) for r in db.execute(
                'SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=?',(node_id,))}
            if not desired_sources<=assigned:raise PolicyError('Node assignments changed while applying desired state')
            for source in assigned:
                item=by_source.get(source)
                db.execute("""UPDATE remote_node_inbounds SET remote_inbound_id=?,last_sync=?,last_error=?,updated_at=?
                              WHERE node_id=? AND local_inbound_id=?""",
                           (item['remoteInboundId'] if item else 0,now,
                            '' if item else 'not present in desired state',now,node_id,source))
            # The assignment list and revision acknowledgement commit together.
            db.execute("""UPDATE remote_node_desired_state SET applied_revision=?,applied_hash=?,applied_at=?,last_error=''
                          WHERE node_id=?""",(state['revision'],state['hash'],now,node_id))
            row=db.execute('SELECT last_health FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            try:health=json.loads(row['last_health'])
            except (ValueError,TypeError):health={}
            if not isinstance(health,dict):health={}
            if isinstance(doc.get('core'),dict):health['core']=doc['core']
            db.execute('UPDATE remote_nodes SET last_health=? WHERE id=?',(json.dumps(health),node_id))
        status=self.desired_state(node_id,include_payload=False)
        return {'latency_ms':ms,'legacy':False,'desired_state_applied':True,'desired_state':status,
                'items':items,'core':doc.get('core',{}),'agent':doc,'synced_at':now}

    def sync_mirrors(self,node_id:str,bundles:list[dict])->dict:
        if not isinstance(bundles,list) or len(bundles)>256:raise PolicyError('Invalid node mirror bundle')
        doc,ms=self._request(node_id,'/node/api/mirrors/sync','POST',{'assignments':bundles},30.0)
        if not isinstance(doc,dict) or not isinstance(doc.get('items'),list):
            self._request_failed(node_id,'Invalid node mirror sync response');raise PolicyError('Invalid node mirror sync response')
        now=time.time()
        by_source={int(x.get('sourceInboundId')):x for x in doc['items'] if isinstance(x,dict) and type(x.get('sourceInboundId')) is int}
        with self.store.transaction() as db:
            for bundle in bundles:
                source=int(bundle['sourceInboundId']);item=by_source.get(source,{})
                db.execute('''UPDATE remote_node_inbounds SET remote_inbound_id=?,last_sync=?,last_error=?,updated_at=?
                              WHERE node_id=? AND local_inbound_id=?''',
                           (int(item.get('remoteInboundId') or 0),now,str(item.get('error') or '')[:300],now,node_id,source))
        return {'latency_ms':ms,'items':doc['items'],'core':doc.get('core',{}),'synced_at':now}


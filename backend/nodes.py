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
import threading
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
              last_recovered_at REAL NOT NULL DEFAULT 0);
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
            ''')
            node_cols={r[1] for r in store.db.execute('PRAGMA table_info(remote_nodes)')}
            for name,ddl in (
                ('failure_count',"ALTER TABLE remote_nodes ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"),
                ('recovery_count',"ALTER TABLE remote_nodes ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"),
                ('last_offline_at',"ALTER TABLE remote_nodes ADD COLUMN last_offline_at REAL NOT NULL DEFAULT 0"),
                ('last_recovered_at',"ALTER TABLE remote_nodes ADD COLUMN last_recovered_at REAL NOT NULL DEFAULT 0"),
            ):
                if name not in node_cols:store.db.execute(ddl)

    def list(self)->list[dict]:
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute('SELECT * FROM remote_nodes ORDER BY name,id')]
        for r in rows:
            r.pop('token_enc',None)
            try:r['health']=json.loads(r.pop('last_health','{}'))
            except Exception:r['health']={}
            with self.store.lock:
                assigned=[dict(x) for x in self.store.db.execute(
                    'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(r['id'],))]
            r['inboundIds']=[int(x['local_inbound_id']) for x in assigned]
            r['assignments']=assigned
            with self.store.lock:
                usage=self.store.db.execute('''SELECT COUNT(*) clients,COALESCE(SUM(current_up+current_down),0) bytes,
                    COALESCE(MAX(last_seen),0) last_sync FROM remote_node_client_usage WHERE node_id=?''',(r['id'],)).fetchone()
            r['traffic_clients']=int(usage['clients'] or 0)
            r['traffic_current_bytes']=int(usage['bytes'] or 0)
            r['traffic_last_sync']=float(usage['last_sync'] or 0)
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
        with self.store.lock:
            assigned=[dict(x) for x in self.store.db.execute(
                'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(node_id,))]
        out['inboundIds']=[int(x['local_inbound_id']) for x in assigned]
        out['assignments']=assigned
        return out

    def put(self,node_id:str,name:str,origin:str,token:str,enabled:bool=True,inbound_ids:list[int]|None=None)->dict:
        if not NAME_RE.fullmatch(node_id) or not isinstance(name,str) or not 1<=len(name)<=128:raise PolicyError('Invalid node identity')
        origin=validate_origin(origin)
        if not isinstance(token,str) or not token.startswith('dkn_') or not 40<=len(token)<=256:raise PolicyError('Invalid DARK node token')
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
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
            db.execute('''INSERT INTO remote_nodes(id,name,origin,token_enc,enabled,created_at,updated_at,last_seen,last_latency_ms,last_error,last_health)
              VALUES(?,?,?,?,?,?,?,0,0,'','{}') ON CONFLICT(id) DO UPDATE SET name=excluded.name,origin=excluded.origin,
              token_enc=excluded.token_enc,enabled=excluded.enabled,updated_at=excluded.updated_at,
              last_seen=CASE WHEN ? THEN 0 ELSE remote_nodes.last_seen END,
              last_latency_ms=CASE WHEN ? THEN 0 ELSE remote_nodes.last_latency_ms END,
              last_error=CASE WHEN ? THEN '' ELSE remote_nodes.last_error END,
              last_health=CASE WHEN ? THEN '{}' ELSE remote_nodes.last_health END''',
              (node_id,name,origin,enc,int(enabled),now,now,int(reset_probe),int(reset_probe),int(reset_probe),int(reset_probe)))
            if reset_probe:
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

    def set_enabled(self,node_id:str,enabled:bool)->dict:
        if type(enabled)is not bool:raise PolicyError('enabled must be boolean')
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(node_id,)).fetchone():raise PolicyError('Node not found')
            db.execute("UPDATE remote_nodes SET enabled=?,updated_at=?,last_seen=0,last_latency_ms=0,last_error='',last_health='{}' WHERE id=?",(int(enabled),time.time(),node_id))
        return self.get(node_id)

    def delete(self,node_id:str)->dict:
        with self.store.transaction() as db:
            db.execute('DELETE FROM remote_node_inbounds WHERE node_id=?',(node_id,))
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
        if data is not None and len(data)>2*1024*1024:raise PolicyError('Node request exceeds 2 MiB limit')
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
        try:
            health,ms=self._request(node_id,'/node/api/health',timeout=timeout)
            if not isinstance(health,dict) or health.get('service')!='DARK XRAY NODE':raise PolicyError('Remote endpoint is not a DARK node agent')
            with self.store.transaction() as db:db.execute('UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error=?,last_health=?,updated_at=? WHERE id=?',(now,ms,'',json.dumps(health),now,node_id))
            return {'node':self.get(node_id),'latency_ms':ms,'health':health}
        except PolicyError as ex:
            self._request_failed(node_id,str(ex));raise


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
        seen=set();charged_up=charged_down=0;baselined=0;resets=0;changed=[]
        with self.store.transaction() as db:
            for item in items:
                if not isinstance(item,dict) or set(item)-{'sourceEmail','up','down'}:raise PolicyError('Invalid node traffic item')
                email=item.get('sourceEmail');up=item.get('up');down=item.get('down')
                if not isinstance(email,str) or email not in allowed or email in seen:raise PolicyError('Unexpected node traffic client')
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
        return {'clients':len(seen),'baselined':baselined,'charged_up':charged_up,'charged_down':charged_down,
                'charged_bytes':charged_up+charged_down,'counter_resets':resets,'captured_at':now,'changed_clients':changed}

    def sync_traffic(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/mirrors/traffic',timeout=12.0)
        if not isinstance(doc,dict) or not isinstance(doc.get('items'),list):
            self._request_failed(node_id,'Invalid node traffic response');raise PolicyError('Invalid node traffic response')
        result=self.apply_traffic_snapshot(node_id,doc['items'],captured_at=time.time())
        return {'latency_ms':ms,**result}

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

    def start(self,*,interval:float=60.0,initial_delay:float=5.0,sync_provider=None,traffic_callback=None):
        if self.thread and self.thread.is_alive():return
        if interval<=0 or initial_delay<0:raise ValueError('Invalid node monitor interval')
        if sync_provider is not None and not callable(sync_provider):raise ValueError('sync_provider must be callable')
        if traffic_callback is not None and not callable(traffic_callback):raise ValueError('traffic_callback must be callable')
        self.stop.clear()
        def run():
            if self.stop.wait(initial_delay):return
            while not self.stop.is_set():
                with self.store.lock:ids=[r[0] for r in self.store.db.execute('SELECT id FROM remote_nodes WHERE enabled=1 ORDER BY id')]
                for node_id in ids:
                    if self.stop.is_set():return
                    try:
                        self.probe(node_id,timeout=5.0)
                        traffic=self.sync_traffic(node_id)
                        if traffic_callback is not None and traffic.get('charged_bytes'):traffic_callback(node_id,traffic)
                        if sync_provider is not None:self.sync_mirrors(node_id,sync_provider(node_id))
                    except (PolicyError,OSError,ValueError):
                        pass
                if self.stop.wait(interval):return
        self.thread=threading.Thread(target=run,name='dark-node-health',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=6.0)
        self.thread=None

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

    def assignments(self,node_id:str)->list[dict]:
        self.get(node_id)
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute(
                'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(node_id,))]

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


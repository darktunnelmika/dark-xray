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
from node_commands import NodeCommands
from node_installations import NodeInstallations, StaleInstallation, installation_operation


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


class NodeHTTPError(PolicyError):
    """Typed HTTP rejection; callers must not infer 401 from an error string."""
    def __init__(self, status:int, detail:str=''):
        self.status=status
        super().__init__(f'Node HTTP {status}'+(': '+detail if detail else ''))


def node_https_request(origin:str, token:str, path:str, method:str='GET', body:dict|None=None,
                       timeout:float=8.0, *, agent_id:str='', installation_id:str='',
                       allow_auth_failure:bool=False)->tuple[dict,int]:
    """Same pinned HTTPS transport for registered Nodes and isolated candidates.

    No database writes. Candidate probes may distinguish a TLS-verified 401
    (which has no authenticated identity headers) to try the journaled bootstrap
    credential. No other identity/TLS/status failure permits that fallback.
    """
    if not isinstance(path,str) or not path.startswith('/node/api/') or any(ch in path for ch in '\r\n?#'):
        raise PolicyError('Invalid node API path')
    if not .2<=timeout<=30:raise PolicyError('Invalid node timeout')
    _origin,host,port,addresses=resolve_origin(origin)
    data=None if body is None else json.dumps(body,separators=(',',':'),allow_nan=False).encode()
    if data is not None and len(data)>8*1024*1024:raise PolicyError('Node request exceeds 8 MiB limit')
    headers={'Accept':'application/json','Authorization':'Bearer '+token}
    if installation_id:
        headers['X-Dark-Expected-Node-Id']=agent_id
        headers['X-Dark-Expected-Installation-Id']=installation_id
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
            if allow_auth_failure and res.status==401:raise NodeHTTPError(401)
            if installation_id and (res.getheader('X-Dark-Node-Id')!=agent_id or
                    res.getheader('X-Dark-Installation-Id')!=installation_id):
                raise PolicyError('Node response installation identity mismatch')
            if res.status<200 or res.status>=300:raise NodeRegistry._response_error(res.status,raw)
            try:doc=json.loads(raw.decode())
            except Exception as ex:raise PolicyError('Node returned invalid JSON') from ex
            if not isinstance(doc,(dict,list)):raise PolicyError('Unexpected node response shape')
            return doc,max(1,int((time.monotonic()-start)*1000))
        except PolicyError:raise
        except (ssl.SSLError,http.client.HTTPException,TimeoutError,OSError) as ex:last_error=ex
        finally:
            try:conn.close()
            except Exception:pass
    raise PolicyError('Node connection failed: '+(type(last_error).__name__ if last_error else 'Timeout')) from last_error


class NodeRegistry:
    def __init__(self,store:Store,cipher):
        self.store,self.cipher=store,cipher
        self.managed_assignment_sources = None
        self.stop=threading.Event();self.thread:threading.Thread|None=None
        self._monitor_lock=threading.RLock();self._monitor_state={}
        self._operation_locks={};self._operation_locks_guard=threading.Lock()
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
              failover_enabled INTEGER NOT NULL DEFAULT 1,
              maintenance INTEGER NOT NULL DEFAULT 0,maintenance_since REAL NOT NULL DEFAULT 0,
              maintenance_note TEXT NOT NULL DEFAULT '');
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
            CREATE TABLE IF NOT EXISTS remote_node_metrics(
              node_id TEXT NOT NULL,captured_at REAL NOT NULL,
              cpu REAL,memory_percent REAL,disk_percent REAL,load1 REAL,
              rx_bps REAL,tx_bps REAL,connections INTEGER,latency_ms INTEGER,
              health_score INTEGER,capacity_percent REAL,xray_running INTEGER,
              managed_clients INTEGER,
              PRIMARY KEY(node_id,captured_at));
            CREATE INDEX IF NOT EXISTS remote_node_metrics_time ON remote_node_metrics(node_id,captured_at);
            CREATE TABLE IF NOT EXISTS remote_node_alerts(
              node_id TEXT NOT NULL,code TEXT NOT NULL,severity TEXT NOT NULL,
              first_seen REAL NOT NULL,last_seen REAL NOT NULL,value REAL,threshold REAL,
              active INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY(node_id,code));
            CREATE INDEX IF NOT EXISTS remote_node_alerts_active ON remote_node_alerts(node_id,active,last_seen);
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
                ('maintenance',"ALTER TABLE remote_nodes ADD COLUMN maintenance INTEGER NOT NULL DEFAULT 0"),
                ('maintenance_since',"ALTER TABLE remote_nodes ADD COLUMN maintenance_since REAL NOT NULL DEFAULT 0"),
                ('maintenance_note',"ALTER TABLE remote_nodes ADD COLUMN maintenance_note TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in node_cols:store.db.execute(ddl)
            # Existing Node V3 records predate data_address. Preserve their
            # behavior without requiring an Edit/Save round trip after upgrade.
            for row in store.db.execute("SELECT id,origin,data_address FROM remote_nodes WHERE data_address='' ").fetchall():
                try:address=validate_data_address('',str(row['origin']))
                except PolicyError:continue
                store.db.execute('UPDATE remote_nodes SET data_address=? WHERE id=?',(address,row['id']))

        self.commands=NodeCommands(self)
        self.installations=NodeInstallations(self)

    def _node_transaction(self,node_id:str):
        return self.installations.transaction(node_id)


    def _node_operation(self,node_id:str):
        # Serialize config/control/probe effects per Node, never under SQLite.
        # Recording newer intent deliberately does NOT wait for this lock.
        with self._operation_locks_guard:
            return self._operation_locks.setdefault(node_id,threading.RLock())

    @staticmethod
    def _runtime_block_reason(node:dict)->str:
        control=node.get('control') or {}
        if control.get('pending'):return 'control_pending'
        if control.get('persisted') and not control.get('desired_running'):return 'control_stopped'
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
        telemetry_fresh=bool(node.get('last_seen') and now-float(node.get('last_seen') or 0)<180)
        # Subscription/failover routing must not flap on a single short control-plane
        # timeout. The Node monitor already tracks consecutive transport failures and
        # clears failure_count on recovery. Keep a previously healthy deployed route
        # through two transient failures; three consecutive failures (or stale
        # telemetry) remove it. Explicit maintenance/config/runtime failures remain
        # immediate below.
        transport_stable=bool(not node.get('last_error') or int(node.get('failure_count') or 0)<3)
        online=bool(node.get('enabled') and telemetry_fresh and transport_stable)
        if sync_error:deployment_state='sync_error'
        elif runtime_block:deployment_state=runtime_block
        elif remote_id:deployment_state='deployed'
        else:deployment_state='pending'
        if not node.get('enabled'):reason='node_disabled'
        elif node.get('maintenance'):reason='node_maintenance'
        elif sync_error:reason='sync_error'
        elif not remote_id:reason='not_deployed'
        elif runtime_block:reason=runtime_block
        elif not node.get('failover_enabled'):reason='failover_disabled'
        elif not node.get('data_address'):reason='data_address_missing'
        elif not online:reason='node_offline'
        else:reason='ready'
        return {**assignment,'remote_inbound_id':remote_id,'deployment_state':deployment_state,
                'deployed':deployed,'failover_ready':reason=='ready','failover_reason':reason}

    @staticmethod
    def _operations_health(node:dict)->dict:
        """Summarize fresh Node telemetry without probing any data path.

        Capacity is a utilization estimate from CPU, RAM and normalized 1m load.
        Disk, Xray, Hub lease and accounting health affect the Health score only.
        Tunnel/WARP/routing health is intentionally outside this calculation.
        """
        def number(value):
            try:
                out=float(value)
                return out if out==out and abs(out)!=float('inf') else None
            except (TypeError,ValueError):return None
        def percent(value):
            value=number(value)
            return None if value is None else max(0.0,min(100.0,value))
        def add(items,severity,code,value=None,threshold=None):
            item={'severity':severity,'code':code}
            if value is not None:item['value']=round(float(value),1)
            if threshold is not None:item['threshold']=round(float(threshold),1)
            items.append(item)

        enabled=bool(node.get('enabled'));telemetry=str(node.get('telemetry_state') or 'offline')
        if not enabled:
            return {'score':None,'state':'disabled','capacity_percent':None,'capacity_state':'unknown','alerts':[]}
        if telemetry=='offline':
            return {'score':0,'state':'critical','capacity_percent':None,'capacity_state':'unknown',
                    'alerts':[{'severity':'critical','code':'telemetry_offline'}]}
        if telemetry!='fresh':
            age=number(node.get('telemetry_age_seconds'))
            item={'severity':'warning','code':'telemetry_stale'}
            if age is not None:item['value']=round(age,1)
            return {'score':55,'state':'warning','capacity_percent':None,'capacity_state':'unknown','alerts':[item]}

        health=node.get('health') if isinstance(node.get('health'),dict) else {}
        system=health.get('system') if isinstance(health.get('system'),dict) else {}
        core=health.get('core') if isinstance(health.get('core'),dict) else {}
        memory=system.get('memory') if isinstance(system.get('memory'),dict) else {}
        disk=system.get('disk') if isinstance(system.get('disk'),dict) else {}
        cpu_info=system.get('cpu_info') if isinstance(system.get('cpu_info'),dict) else {}
        maintenance=health.get('maintenance') if isinstance(health.get('maintenance'),dict) else {}
        lease=health.get('hub_lease') if isinstance(health.get('hub_lease'),dict) else {}

        cpu=percent(system.get('cpu'))
        mem=percent(memory.get('percent',system.get('memory_percent')))
        disk_pct=percent(disk.get('percent',system.get('disk_percent')))
        loads=system.get('loads') if isinstance(system.get('loads'),list) else []
        load1=number(loads[0]) if loads else None
        logical=number(cpu_info.get('logical'))
        load_raw_pct=max(0.0,100.0*load1/logical) if load1 is not None and logical and logical>0 else None
        load_capacity_pct=min(100.0,load_raw_pct) if load_raw_pct is not None else None

        weighted=[(cpu,.40),(mem,.35),(load_capacity_pct,.25)]
        present=[(value,weight) for value,weight in weighted if value is not None]
        capacity=round(sum(value*weight for value,weight in present)/sum(weight for _,weight in present),1) if present else None
        capacity_state='unknown' if capacity is None else ('overloaded' if capacity>=90 else 'busy' if capacity>=70 else 'healthy')

        alerts=[]
        for value,warn,critical,wcode,ccode in (
            (cpu,80,95,'cpu_high','cpu_critical'),
            (mem,85,95,'memory_high','memory_critical'),
            (disk_pct,85,95,'disk_high','disk_critical'),
        ):
            if value is None:continue
            if value>=critical:add(alerts,'critical',ccode,value,critical)
            elif value>=warn:add(alerts,'warning',wcode,value,warn)
        if load_raw_pct is not None:
            if load_raw_pct>=150:add(alerts,'critical','load_critical',load_raw_pct,150)
            elif load_raw_pct>=100:add(alerts,'warning','load_high',load_raw_pct,100)

        core_state=str(core.get('state') or '')
        if core_state and core_state!='running':add(alerts,'critical','xray_not_running')
        if core.get('last_error'):add(alerts,'critical','xray_error')
        if lease.get('required') is True and lease.get('valid') is not True:add(alerts,'critical','hub_lease_invalid')
        if maintenance.get('statistics_error'):add(alerts,'critical','accounting_checkpoint_error')
        checkpoint=number(maintenance.get('checkpoint_age_seconds'))
        if checkpoint is not None and checkpoint>15:add(alerts,'warning','accounting_checkpoint_stale',checkpoint,15)
        if node.get('last_error') and int(node.get('failure_count') or 0)>=3:
            add(alerts,'critical','node_error')

        penalty=sum(28 if x['severity']=='critical' else 10 for x in alerts)
        score=max(0,100-min(100,penalty))
        state='critical' if any(x['severity']=='critical' for x in alerts) else ('warning' if alerts else 'healthy')
        return {'score':int(score),'state':state,'capacity_percent':capacity,'capacity_state':capacity_state,'alerts':alerts}

    def _sync_active_alerts(self,db,node_id:str,alerts:list[dict],now:float):
        active_codes=set()
        for alert in alerts:
            code=str(alert.get('code') or '')[:96]
            severity=str(alert.get('severity') or 'warning')[:16]
            if not code:continue
            active_codes.add(code)
            value=self._metric_number(alert.get('value'));threshold=self._metric_number(alert.get('threshold'))
            db.execute('''INSERT INTO remote_node_alerts(node_id,code,severity,first_seen,last_seen,value,threshold,active)
                          VALUES(?,?,?,?,?,?,?,1)
                          ON CONFLICT(node_id,code) DO UPDATE SET
                            severity=excluded.severity,
                            first_seen=CASE WHEN remote_node_alerts.active=1 THEN remote_node_alerts.first_seen ELSE excluded.first_seen END,
                            last_seen=excluded.last_seen,value=excluded.value,threshold=excluded.threshold,active=1''',
                       (node_id,code,severity,now,now,value,threshold))
        if active_codes:
            marks=','.join('?' for _ in active_codes)
            db.execute('UPDATE remote_node_alerts SET active=0 WHERE node_id=? AND active=1 AND code NOT IN ('+marks+')',
                       (node_id,*sorted(active_codes)))
        else:
            db.execute('UPDATE remote_node_alerts SET active=0 WHERE node_id=? AND active=1',(node_id,))

    def _annotate_alert_times(self,node:dict,alerts:list[dict],now:float)->list[dict]:
        with self.store.lock:
            rows={str(r['code']):dict(r) for r in self.store.db.execute(
                'SELECT code,severity,first_seen,last_seen,value,threshold,active FROM remote_node_alerts WHERE node_id=? AND active=1',
                (node['id'],))}
        out=[]
        for source in alerts:
            item=dict(source);code=str(item.get('code') or '')
            stored=rows.get(code)
            if stored:
                item['started_at']=float(stored['first_seen']);item['last_observed_at']=float(stored['last_seen'])
            elif code=='telemetry_stale':
                item['started_at']=float(node.get('last_seen') or now)+20.0;item['last_observed_at']=now
            elif code in {'telemetry_offline','node_error'}:
                item['started_at']=float(node.get('last_offline_at') or node.get('last_seen') or now);item['last_observed_at']=now
            else:
                item['started_at']=float(node.get('last_seen') or now);item['last_observed_at']=float(node.get('last_seen') or now)
            out.append(item)
        return out

    def list(self)->list[dict]:
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute('SELECT * FROM remote_nodes ORDER BY name,id')]
        now=time.time()
        for r in rows:
            r.pop('token_enc',None)
            with self._monitor_lock:r['monitor']=dict(self._monitor_state.get(r['id'],{}))
            try:r['health']=json.loads(r.pop('last_health','{}'))
            except Exception:r['health']={}
            with self.store.lock:
                assigned=[dict(x) for x in self.store.db.execute(
                    'SELECT local_inbound_id,remote_inbound_id,last_sync,last_error FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id',(r['id'],))]
            age=max(0.0,now-float(r['last_seen'] or 0)) if r['last_seen'] else None
            r['telemetry_age_seconds']=round(age,1) if age is not None else None
            # Monitoring is observational: one transient request error
            # must not flip LIVE/STALE or affect the saved Node assignment.
            # Lease validity remains enforced independently by the Agent.
            stable=not r['last_error'] or int(r.get('failure_count') or 0)<3
            r['telemetry_state']='fresh' if r['enabled'] and age is not None and age<=45 and stable else ('stale' if r['enabled'] and age is not None and age<180 else 'offline')
            r['online']=bool(r['enabled'] and r['last_seen'] and age is not None and age<180 and stable)
            r['operational_health']=self._operations_health(r)
            r['operational_health']['alerts']=self._annotate_alert_times(r,r['operational_health'].get('alerts',[]),now)
            with self.store.lock:
                ds=self.store.db.execute('SELECT revision,desired_hash,updated_at,applied_revision,applied_hash,applied_at,last_error FROM remote_node_desired_state WHERE node_id=?',(r['id'],)).fetchone()
            desired=dict(ds) if ds else {'revision':0,'desired_hash':'','updated_at':0,'applied_revision':0,'applied_hash':'','applied_at':0,'last_error':''}
            desired['pending']=bool(desired['revision'] and (desired['revision']!=desired['applied_revision'] or desired['desired_hash']!=desired['applied_hash']))
            r['desired_state']=desired
            r['control']=self.commands.status(r['id'])
            r['installation']=self.installations.public_status(r['id'])
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
        out['installation']=self.installations.public_status(node_id)
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
            from node_replacement_deployment import assert_deployment_allows
            if enabled:assert_deployment_allows(db,node_id,'enable')
            # A pending handoff owns its target endpoint. Normal Add/Edit/Pair
            # must not register that candidate while its credential is changing.
            if (db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_replacements'").fetchone()
                    and db.execute('SELECT 1 FROM remote_node_replacements WHERE target_origin=?',(origin,)).fetchone()):
                raise PolicyError('Node endpoint is reserved by a replacement preparation')
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
            binding=self.installations.ensure(db,node_id)
            if old and old['origin']!=origin and not binding['installation_id']:
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
        now=time.time()
        with self._node_transaction(node_id) as db:
            if isinstance(value,dict) and 'nodeId' in value:
                value={**value,'nodeId':self.installations.current(node_id)['agent_id']}
            if isinstance(value,dict) and self.commands.status(node_id)['persisted']:
                value={**value,'desiredRunning':self.commands.config_running(node_id)}
            _raw,digest=self._desired_payload(value)
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
        with self.store.lock:
            return self._desired_state_locked(node_id,include_payload=include_payload)

    def _desired_state_locked(self,node_id:str,*,include_payload:bool=True)->dict:
        self.get(node_id)
        with self.store.lock:r=self.store.db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
        if not r:return {'node_id':node_id,'revision':0,'hash':'','payload':{} if include_payload else None,'pending':False}
        out={'node_id':node_id,'revision':int(r['revision']),'hash':r['desired_hash'],'updated_at':float(r['updated_at']),
             'applied_revision':int(r['applied_revision']),'applied_hash':r['applied_hash'],'applied_at':float(r['applied_at']),
             'last_error':r['last_error'],'pending':int(r['revision'])!=int(r['applied_revision']) or r['desired_hash']!=r['applied_hash']}
        if include_payload:
            value=self._open_desired_payload(r['desired_json'])
            control=self.commands.status(node_id)
            if control['persisted'] and value.get('desiredRunning',True)!=self.commands.config_running(node_id):
                self.set_desired_state(node_id,value)
                return self._desired_state_locked(node_id,include_payload=True)
            out['payload']=value
        return out

    def mark_desired_state(self,node_id:str,revision:int,digest:str,*,error:str='')->dict:
        if type(revision)is not int or revision<0 or not isinstance(digest,str) or len(digest)>128:raise PolicyError('Invalid node apply acknowledgement')
        self.get(node_id);now=time.time()
        with self._node_transaction(node_id) as db:
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
            from node_replacement_deployment import assert_deployment_allows
            if enabled:assert_deployment_allows(db,node_id,'enable')
            if not db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(node_id,)).fetchone():raise PolicyError('Node not found')
            db.execute("UPDATE remote_nodes SET enabled=?,updated_at=?,last_seen=0,last_latency_ms=0,last_error='',last_health='{}' WHERE id=?",(int(enabled),time.time(),node_id))
        return self.get(node_id)

    def set_maintenance(self,node_id:str,enabled:bool,note:str='')->dict:
        if type(enabled)is not bool:raise PolicyError('maintenance enabled must be boolean')
        if not isinstance(note,str) or len(note)>300:raise PolicyError('Invalid maintenance note')
        now=time.time()
        with self._node_transaction(node_id) as db:
            row=db.execute('SELECT maintenance,maintenance_since FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            if not row:raise PolicyError('Node not found')
            since=(float(row['maintenance_since']) if row['maintenance'] and enabled else now if enabled else 0.0)
            db.execute('UPDATE remote_nodes SET maintenance=?,maintenance_since=?,maintenance_note=?,updated_at=? WHERE id=?',
                       (int(enabled),since,note.strip() if enabled else '',now,node_id))
        return next(x for x in self.list() if x['id']==node_id)

    def delete(self,node_id:str)->dict:
        with self.store.transaction() as db:
            history=db.execute('SELECT 1 FROM remote_node_installations WHERE node_id=? AND retired_at>0',(node_id,)).fetchone()
            usage=db.execute('SELECT 1 FROM remote_node_client_usage WHERE node_id=? AND (current_up>0 OR current_down>0 OR seq>0)',(node_id,)).fetchone()
            if history or usage:
                raise PolicyError('Node has retained installation/usage history; disable it instead of deleting it')
            db.execute('DELETE FROM remote_node_installations WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_inbounds WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_client_usage WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_ips WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_devices WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_security_state WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_desired_state WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_control WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_metrics WHERE node_id=?',(node_id,))
            db.execute('DELETE FROM remote_node_alerts WHERE node_id=?',(node_id,))
            cur=db.execute('DELETE FROM remote_nodes WHERE id=?',(node_id,))
            if not cur.rowcount:raise PolicyError('Node not found')
        return {'deleted':True}

    def _request_ok(self,node_id:str,latency_ms:int):
        now=time.time()
        with self._node_transaction(node_id) as db:
            old=db.execute('SELECT last_error FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            recovered=bool(old and old['last_error'])
            db.execute('''UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error='',updated_at=?,
                       failure_count=0,recovery_count=recovery_count+?,last_recovered_at=CASE WHEN ? THEN ? ELSE last_recovered_at END
                       WHERE id=?''',
                       (now,max(1,int(latency_ms)),now,int(recovered),int(recovered),now,node_id))

    def _request_failed(self,node_id:str,error:str):
        now=time.time()
        with self._node_transaction(node_id) as db:
            old=db.execute('SELECT last_error,failure_count FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
            first=bool(old and not old['last_error'])
            failures=int(old['failure_count'] or 0)+1 if old else 1
            db.execute('''UPDATE remote_nodes SET last_error=?,updated_at=?,failure_count=failure_count+1,
                          last_offline_at=CASE WHEN ? THEN ? ELSE last_offline_at END WHERE id=?''',
                       (str(error)[:300],now,int(first),now,node_id))
            # A single failed HTTP request is not an offline Node. Align
            # alerts with the three-failure routing hysteresis.
            if failures>=3:
                self._sync_active_alerts(db,node_id,[{'severity':'critical','code':'telemetry_offline'},
                                                    {'severity':'critical','code':'node_error'}],now)

    @staticmethod
    def _response_error(status:int,raw:bytes)->PolicyError:
        detail=''
        try:
            parsed=json.loads(raw[:65536].decode())
            if isinstance(parsed,dict):detail=str(parsed.get('detail',''))[:200]
        except Exception:pass
        return NodeHTTPError(status,detail)

    @installation_operation
    def _request(self,node_id:str,path:str,method:str='GET',body:dict|None=None,timeout:float=8.0)->tuple[dict,int]:
        node=self.installations.current(node_id)
        with self.store.lock:self.installations.assert_current(self.store.db,node)
        try:token=self.cipher.decrypt(node['token_enc'].encode()).decode()
        except Exception as ex:raise PolicyError('Node credential cannot be decrypted') from ex
        if not node['enabled']:raise PolicyError('Node is disabled')
        try:
            doc,elapsed=node_https_request(node['origin'],token,path,method,body,timeout,
                                          agent_id=node['agent_id'],installation_id=node['installation_id'])
        except PolicyError as ex:
            self._request_failed(node_id,str(ex));raise
        self._request_ok(node_id,elapsed)
        return doc,elapsed

    @installation_operation
    def probe(self,node_id:str,*,timeout:float=8.0)->dict:
        with self._node_operation(node_id):
            return self._probe_locked(node_id,timeout=timeout)

    @staticmethod
    def _metric_number(value):
        try:
            out=float(value)
            return out if out==out and abs(out)!=float('inf') else None
        except (TypeError,ValueError):return None

    def _record_metric(self,node_id:str,health:dict,latency_ms:int,*,captured_at:float|None=None,min_interval:float=15.0):
        """Persist a bounded lightweight system sample from an already-completed Health probe.

        This never performs its own network request and intentionally excludes Tunnel/WARP/path health.
        """
        now=time.time() if captured_at is None else float(captured_at)
        system=health.get('system') if isinstance(health.get('system'),dict) else {}
        memory=system.get('memory') if isinstance(system.get('memory'),dict) else {}
        disk=system.get('disk') if isinstance(system.get('disk'),dict) else {}
        network=system.get('network') if isinstance(system.get('network'),dict) else {}
        connections=system.get('connections') if isinstance(system.get('connections'),dict) else {}
        loads=system.get('loads') if isinstance(system.get('loads'),list) else []
        core=health.get('core') if isinstance(health.get('core'),dict) else {}
        ops=self._operations_health({'enabled':True,'telemetry_state':'fresh','telemetry_age_seconds':0,
                                     'health':health,'last_error':''})
        row=(node_id,now,self._metric_number(system.get('cpu')),
             self._metric_number(memory.get('percent',system.get('memory_percent'))),
             self._metric_number(disk.get('percent',system.get('disk_percent'))),
             self._metric_number(loads[0]) if loads else None,
             self._metric_number(network.get('down_bps')),self._metric_number(network.get('up_bps')),
             int(connections.get('open')) if type(connections.get('open')) is int else None,
             int(latency_ms),int(ops['score']) if type(ops.get('score')) is int else None,
             self._metric_number(ops.get('capacity_percent')),
             1 if core.get('state')=='running' else 0,
             int(health.get('managed_clients')) if type(health.get('managed_clients')) is int else None)
        with self.store.transaction() as db:
            latest=db.execute('SELECT MAX(captured_at) FROM remote_node_metrics WHERE node_id=?',(node_id,)).fetchone()[0]
            if latest and now-float(latest)<min_interval:return False
            db.execute('''INSERT OR REPLACE INTO remote_node_metrics(
              node_id,captured_at,cpu,memory_percent,disk_percent,load1,rx_bps,tx_bps,connections,latency_ms,
              health_score,capacity_percent,xray_running,managed_clients) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',row)
            # Keep enough headroom for the 24h view while bounding database growth.
            db.execute('DELETE FROM remote_node_metrics WHERE captured_at<?',(now-93600,))
            self._sync_active_alerts(db,node_id,ops.get('alerts',[]),now)
        return True

    @staticmethod
    def _metric_average(values):
        values=[float(x) for x in values if x is not None]
        return round(sum(values)/len(values),2) if values else None

    def metrics_history(self,node_id:str,window:str='live')->dict:
        if window not in {'live','1h','24h'}:raise PolicyError('Invalid Node metric window')
        # Confirm the node exists without exposing its credential.
        current=next((x for x in self.list() if x['id']==node_id),None)
        if current is None:raise PolicyError('Node not found')
        seconds={'live':900,'1h':3600,'24h':86400}[window]
        bucket={'live':10,'1h':30,'24h':600}[window]
        now=time.time();start=now-seconds
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute(
                '''SELECT captured_at,cpu,memory_percent,disk_percent,load1,rx_bps,tx_bps,connections,
                          latency_ms,health_score,capacity_percent,xray_running,managed_clients
                   FROM remote_node_metrics WHERE node_id=? AND captured_at>=? ORDER BY captured_at''',
                (node_id,start))]
        groups={}
        for row in rows:
            key=int((float(row['captured_at'])-start)//bucket)
            groups.setdefault(key,[]).append(row)
        points=[]
        avg_fields=('cpu','memory_percent','disk_percent','load1','rx_bps','tx_bps','connections',
                    'latency_ms','health_score','capacity_percent','managed_clients')
        for key in sorted(groups):
            chunk=groups[key]
            item={'at':round(sum(float(x['captured_at']) for x in chunk)/len(chunk),3)}
            for name in avg_fields:item[name]=self._metric_average([x.get(name) for x in chunk])
            # One stopped sample makes the bucket reflect the runtime interruption.
            states=[x.get('xray_running') for x in chunk if x.get('xray_running') is not None]
            item['xray_running']=min(states) if states else None
            points.append(item)
        return {'node':current,'window':window,'from':start,'to':now,'bucket_seconds':bucket,
                'retention_seconds':93600,'points':points,
                'boundary':'Hub-stored Agent/Xray/system metrics only; Tunnel/WARP/path health is excluded'}

    def _probe_locked(self,node_id:str,*,timeout:float=8.0)->dict:
        now=time.time()
        health,ms=self._request(node_id,'/node/api/health',timeout=timeout)
        if not isinstance(health,dict) or health.get('service')!='DARK XRAY NODE':
            self._request_failed(node_id,'Remote endpoint is not a DARK node agent')
            raise PolicyError('Remote endpoint is not a DARK node agent')
        self.installations.observe(node_id,health)
        with self._node_transaction(node_id) as db:db.execute('UPDATE remote_nodes SET last_seen=?,last_latency_ms=?,last_error=?,last_health=?,updated_at=? WHERE id=?',(now,ms,'',json.dumps(health),now,node_id))
        self._record_metric(node_id,health,ms,captured_at=now)
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

    def apply_traffic_snapshot(self,node_id:str,items:list[dict],*,captured_at:float|None=None,initialize_absent:bool=False)->dict:
        if not isinstance(items,list) or len(items)>100000:raise PolicyError('Invalid node traffic snapshot')
        allowed=self._allowed_traffic_clients(node_id);now=time.time() if captured_at is None else float(captured_at)
        seen=set();charged_up=charged_down=0;baselined=0;resets=0;ignored=0;seeded=0;changed=[]
        with self._node_transaction(node_id) as db:
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
            # A complete fresh Agent snapshot proves an absent new mirror has
            # consumed zero. Seed it BEFORE config apply so its first bytes are
            # not silently discarded as a later nonzero adoption baseline.
            if initialize_absent:
                for email in allowed-seen:
                    cur=db.execute('''INSERT OR IGNORE INTO remote_node_client_usage
                        (node_id,client_id,raw_up,raw_down,initialized,last_seen) VALUES(?,?,0,0,1,?)''',
                        (node_id,email,now))
                    if cur.rowcount:seeded+=1
        return {'seeded_zero_baselines':seeded,'clients':len(seen),'ignored_clients':ignored,'baselined':baselined,'charged_up':charged_up,'charged_down':charged_down,
                'charged_bytes':charged_up+charged_down,'counter_resets':resets,'captured_at':now,'changed_clients':changed}

    @installation_operation
    def sync_traffic(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/mirrors/traffic',timeout=12.0)
        if not isinstance(doc,dict) or not isinstance(doc.get('items'),list):
            self._request_failed(node_id,'Invalid node traffic response');raise PolicyError('Invalid node traffic response')
        result=self.apply_traffic_snapshot(node_id,doc['items'],captured_at=time.time(),
                                          initialize_absent=isinstance(doc.get('accountingLease'),str))
        return {'latency_ms':ms,**result,'accounting_lease':doc.get('accountingLease')}


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

    def _node_source_scope_complete(self,node_id:str)->bool:
        """True only when every desired customer listener on the Node exposes an end-user source.

        A shadow tunnel listener is intentionally treated as opaque until a
        future edge-attribution transport can prove the original source.
        """
        with self.store.lock:
            row=self.store.db.execute('SELECT desired_json FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
        if not row:return False
        try:payload=json.loads(row['desired_json'] or '{}')
        except Exception:return False
        assignments=payload.get('assignments',[]) if isinstance(payload,dict) else []
        if not isinstance(assignments,list):return False
        for item in assignments:
            inbound=item.get('inbound',{}) if isinstance(item,dict) else {}
            if not isinstance(inbound,dict) or not inbound.get('enable',True):continue
            meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta',{}),dict) else {}
            raw=meta.get('tunnelPorts',{}) if isinstance(meta.get('tunnelPorts',{}),dict) else {}
            shadow=raw.get('local')
            if type(shadow)is int and 1<=shadow<=65535:return False
        return True

    @staticmethod
    def _security_stamp(value)->float:
        if isinstance(value,bool) or not isinstance(value,(int,float)):raise PolicyError('Invalid node security timestamp')
        value=float(value)
        if not 0<=value<1e15:raise PolicyError('Invalid node security timestamp')
        return value

    @installation_operation
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
            with self._node_transaction(node_id) as db:
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
            with self._node_transaction(node_id) as db:
                db.execute('''INSERT INTO remote_node_security_state(node_id,source_verified,last_sync,last_error) VALUES(?,0,0,?)
                              ON CONFLICT(node_id) DO UPDATE SET source_verified=0,last_error=excluded.last_error''',
                           (node_id,str(ex)[:300]))
            raise

    def reconcile_global_security(self,*,local_source_verified:bool,local_source_complete:bool|None=None,
                                  now:float|None=None,persist:bool=True,
                                  client_ids:list[str]|tuple[str,...]|set[str]|None=None)->dict:
        if type(local_source_verified)is not bool:raise PolicyError('local_source_verified must be boolean')
        if local_source_complete is None:local_source_complete=local_source_verified
        if type(local_source_complete)is not bool:raise PolicyError('local_source_complete must be boolean')
        if type(persist)is not bool:raise PolicyError('persist must be boolean')
        scoped=None
        if client_ids is not None:
            if not isinstance(client_ids,(list,tuple,set)):raise PolicyError('client_ids must be a collection')
            raw=list(client_ids)
            if any(not isinstance(x,str) or not x or len(x)>128 for x in raw):
                raise PolicyError('Invalid client security scope')
            scoped=sorted(set(raw))
            if not scoped:return {'clients':0,'changed':[],'items':[],'window_seconds':120}
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
            sql='''SELECT c.id,c.limit_ip,m.desired,m.inbounds,
                       c.global_ip_block,c.global_device_block FROM clients c
                       JOIN managed_clients m ON m.email=c.id WHERE m.state!='deleted' '''
            args=()
            if scoped is not None:
                sql+='AND c.id IN ('+','.join('?' for _ in scoped)+') '
                args=tuple(scoped)
            sql+='ORDER BY c.id'
            metas=[dict(r) for r in self.store.db.execute(sql,args)]
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
            remote_scope_complete=fresh and all(self._node_source_scope_complete(n) for n in assigned)
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
            # Trusted Direct observations are monotonic evidence: seeing more
            # distinct verified sources than the cap is sufficient to block even
            # when an opaque tunnel also exists. Incomplete path coverage may
            # hide additional sources, so it is never sufficient to CLEAR an
            # existing block or to claim complete accounting.
            evidence_ready=bool(assigned) and (not local_required or local_source_verified) and verified
            ip_complete=bool(evidence_ready and (not local_required or local_source_complete) and remote_scope_complete)
            limit_ip=int(meta['limit_ip'] or 0)
            if not limit_ip or not assigned:
                ip_block=False
            elif evidence_ready and len(ip_values)>limit_ip:
                ip_block=True
            elif not ip_complete:
                ip_block=bool(meta['global_ip_block'])
            else:
                ip_block=False

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
                          'ip_enforceable':evidence_ready,'ip_coverage_complete':ip_complete,
                          'local_observation_required':local_required,
                          'local_source_complete':bool(not local_required or local_source_complete),
                          'remote_source_complete':remote_scope_complete,'ip_blocked':ip_block,'device_count':len(device_values),
                          'limit_hwid':limit_hwid,'device_complete':device_complete,'device_blocked':device_block})
        return {'clients':len(items),'changed':changed,'items':items,'window_seconds':window}

    def global_security(self,client_id:str,*,local_source_verified:bool,local_source_complete:bool|None=None)->dict:
        result=self.reconcile_global_security(local_source_verified=local_source_verified,
                                              local_source_complete=local_source_complete,persist=False)
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
                'local_devices':local_devices,'remote_devices':remote_devices,
                'convergence':self.policy_convergence(client_id)}

    def policy_convergence(self,client_id:str)->dict:
        """Report whether each assigned Node applied the current client policy.

        This is authorization convergence, not a distributed-firewall claim.
        A Node can be offline with a newer desired policy safely persisted on the
        Hub while its last applied runtime remains stale until it reconnects.
        """
        assigned=self._assigned_node_ids(client_id)
        with self.store.lock:
            row=self.store.db.execute(
                'SELECT global_ip_block,global_device_block FROM clients WHERE id=?',(client_id,)).fetchone()
        if not row:raise PolicyError('Managed client not found')
        expected_ip=bool(row['global_ip_block']);expected_device=bool(row['global_device_block'])
        listed={str(n['id']):n for n in self.list() if str(n['id']) in assigned}
        items=[]
        for node_id in assigned:
            node=listed.get(node_id,{})
            try:state=self.desired_state(node_id,include_payload=True)
            except PolicyError:
                state={'revision':0,'applied_revision':0,'hash':'','applied_hash':'','pending':True,
                       'last_error':'desired state unavailable','payload':{}}
            payload=state.get('payload') if isinstance(state.get('payload'),dict) else {}
            security=payload.get('security') if isinstance(payload.get('security'),dict) else {}
            policies=security.get('clients') if isinstance(security.get('clients'),list) else []
            policy=next((x for x in policies if isinstance(x,dict) and str(x.get('sourceEmail',''))==client_id),None)
            desired_matches=bool(policy is not None
                and bool(policy.get('globalIpBlocked'))==expected_ip
                and bool(policy.get('globalDeviceBlocked'))==expected_device)
            pending=bool(state.get('pending') or state.get('last_error') or not desired_matches)
            applied=bool(desired_matches and not pending)
            online=bool(node.get('online'))
            health=node.get('health') if isinstance(node.get('health'),dict) else {}
            guard=health.get('guard') if isinstance(health.get('guard'),dict) else {}
            ipguard=payload.get('sections',{}).get('ipguard',{}) if isinstance(payload.get('sections'),dict) else {}
            guard_mode=str(ipguard.get('mode','observe')) if isinstance(ipguard,dict) else 'observe'
            guard_required=guard_mode=='enforce'
            guard_ready=bool(not guard_required or (guard.get('applied') is True and guard.get('state')=='applied'
                                                     and guard.get('source_verified') is True))
            status='converged' if applied else ('offline_pending' if not online else ('error' if state.get('last_error') else 'pending'))
            items.append({'node_id':node_id,'name':node.get('name',node_id),'online':online,'status':status,
                          'desired_revision':int(state.get('revision') or 0),'applied_revision':int(state.get('applied_revision') or 0),
                          'desired_matches_policy':desired_matches,'authorization_applied':applied,
                          'last_error':str(state.get('last_error') or '')[:300],
                          'guard_mode':guard_mode,'guard_ready':guard_ready,'guard_state':str(guard.get('state') or 'unknown'),
                          'direct_source_verified':bool(health.get('direct_source_verified'))})
        pending_nodes=[x['node_id'] for x in items if not x['authorization_applied']]
        offline_nodes=[x['node_id'] for x in items if not x['online']]
        return {'global_ip_block':expected_ip,'global_device_block':expected_device,'assigned_nodes':len(items),
                'authorization_converged':all(x['authorization_applied'] for x in items),
                'packet_guard_ready':all(x['guard_ready'] for x in items),
                'pending_nodes':pending_nodes,'offline_nodes':offline_nodes,'items':items,
                'boundary':'authorization convergence only; nftables remains host-local'}

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
        stamps=[]
        for node_id in node_ids:
            with self.installations.operation(node_id):
                doc,ms=self._request(node_id,'/node/api/mirrors/security/clear','POST',
                                     {'sourceEmail':client_id,'kind':kind},12.0)
                if not isinstance(doc,dict) or doc.get('sourceEmail')!=client_id:
                    raise PolicyError('Invalid node security clear response')
                stamps.append(self.installations.current(node_id))
                items.append({'node_id':node_id,'latency_ms':ms,'result':doc})
        with self.store.transaction() as db:
            for stamp in stamps:self.installations.assert_current(db,stamp)
            for stamp in stamps:
                if kind in {'ips','all'}:db.execute('DELETE FROM remote_node_ips WHERE client_id=? AND node_id=?',(client_id,stamp['node_id']))
                if kind in {'devices','all'}:db.execute('DELETE FROM remote_node_devices WHERE client_id=? AND node_id=?',(client_id,stamp['node_id']))
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
            with self.installations.operation(node_id):
                doc,ms=self._request(node_id,'/node/api/mirrors/traffic/reset','POST',
                                      {'sourceEmail':client_id,'resetId':reset_id},12.0)
                if not isinstance(doc,dict) or doc.get('sourceEmail')!=client_id or type(doc.get('up')) is not int or type(doc.get('down')) is not int:
                    raise PolicyError('Invalid node traffic reset response')
                snap=self.apply_traffic_snapshot(node_id,[{'sourceEmail':client_id,'up':doc['up'],'down':doc['down']}],
                                                 captured_at=time.time())
                with self._node_transaction(node_id) as db:
                    db.execute('''UPDATE remote_node_client_usage SET raw_up=0,raw_down=0,current_up=0,current_down=0,
                                  initialized=1,last_seen=? WHERE node_id=? AND client_id=?''',(time.time(),node_id,client_id))
                    self._recompute_client_usage(db,client_id)
                results.append({'node_id':node_id,'latency_ms':ms,'snapshot':snap,'cached':bool(doc.get('cached'))})
        return {'nodes':len(results),'items':results,'reset':True}

    def start(self,*,interval:float=60.0,initial_delay:float=5.0,sync_provider=None,desired_provider=None,traffic_callback=None,security_callback=None,lease_callback=None):
        import logging
        import math
        if self.thread and self.thread.is_alive():return
        if not math.isfinite(interval) or not math.isfinite(initial_delay) or interval<=0 or initial_delay<0:
            raise ValueError('Invalid node monitor interval')
        callbacks={'sync_provider':sync_provider,'desired_provider':desired_provider,
                   'traffic_callback':traffic_callback,'security_callback':security_callback,'lease_callback':lease_callback}
        for name,callback in callbacks.items():
            if callback is not None and not callable(callback):raise ValueError(name+' must be callable')
        # A lease renewer cannot inherit the legacy 60s telemetry interval.
        # Each node owns its cadence; no fleet-wide barrier or queued duplicates.
        cadence=min(float(interval),5.0) if lease_callback is not None else float(interval)
        accounting_first=lease_callback is not None
        stop=self.stop;stop.clear();workers={}
        logger=logging.getLogger(__name__)
        class Cancelled(Exception):pass

        def cycle(node_id,retired):
            def checkpoint():
                if stop.is_set() or retired.is_set():raise Cancelled()
                with self.store.lock:
                    row=self.store.db.execute('SELECT enabled FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
                if not row or not row[0]:raise Cancelled()
            def record(**values):
                with self._monitor_lock:self._monitor_state.setdefault(node_id,{}).update(values)
            def step(stage,function,*args,**kwargs):
                checkpoint();record(stage=stage)
                return function(*args,**kwargs)
            def security():
                if security_callback is None:return
                try:
                    result=step('security',self.sync_security,node_id)
                    step('security_policy',security_callback,node_id,result)
                    record(security_error='')
                except (PolicyError,OSError,ValueError) as exc:
                    record(security_error=str(exc)[:400])
            started=time.monotonic();record(started_at=time.time(),cadence_seconds=cadence)
            try:
                # Pin this cycle to one installation, including its final grant.
                with self.installations.operation(node_id):
                    command=step('control',self.commands.status,node_id)
                    if command['pending'] and command['action']=='stop':
                        step('stop',self.deliver_pending_control,node_id)
                    if desired_provider is not None:
                        # Preserve pending/offline desired-state visibility.
                        try:step('desired',desired_provider,node_id)
                        except (PolicyError,OSError,ValueError):pass
                    if not accounting_first:
                        step('probe',self.probe,node_id,timeout=5.0)
                    traffic=step('traffic',self.sync_traffic,node_id)
                    if traffic_callback is not None and traffic.get('charged_bytes'):
                        step('policy',traffic_callback,node_id,traffic)
                    if not accounting_first:security()
                    if desired_provider is not None:
                        bundles=step('bundles',sync_provider,node_id) if sync_provider is not None else None
                        state=step('desired',desired_provider,node_id)
                        step('apply',self.sync_desired_state,node_id,state,legacy_bundles=bundles)
                    elif sync_provider is not None:
                        bundles=step('bundles',sync_provider,node_id)
                        step('apply',self.sync_mirrors,node_id,bundles)
                    if desired_provider is not None or sync_provider is not None:
                        traffic=step('post_traffic',self.sync_traffic,node_id)
                        if traffic_callback is not None and traffic.get('charged_bytes'):
                            step('post_policy',traffic_callback,node_id,traffic)
                        if not accounting_first:security()
                    if lease_callback is not None:
                        lease=step('lease',lease_callback,node_id,traffic)
                        record(last_lease_at=time.time(),lease_remaining_seconds=(lease or {}).get('remaining_seconds'))
                    step('control',self.deliver_pending_control,node_id)
                    if accounting_first:
                        # Meter/import/apply/grant before optional health and
                        # security reads. Probe failure cannot undo a valid grant.
                        security()
                        with self._monitor_lock:
                            last_probe=self._monitor_state.get(node_id,{}).get('last_probe_at',0)
                        if time.time()-last_probe>=15:
                            try:
                                step('probe',self.probe,node_id,timeout=5.0)
                                record(last_probe_at=time.time(),probe_error='')
                            except (PolicyError,OSError,ValueError) as exc:
                                record(probe_error=str(exc)[:400],last_probe_at=time.time())
                record(stage='complete',last_success_at=time.time(),last_error='',consecutive_failures=0)
            except Cancelled:
                record(stage='cancelled')
            except Exception as exc:
                # Keep failures separate from transport health: a successful GET
                # must not erase a refused accounting grant or kill the worker.
                with self._monitor_lock:
                    state=self._monitor_state.setdefault(node_id,{})
                    state.update(last_error=type(exc).__name__+': '+str(exc)[:400],
                                 last_error_at=time.time(),failed_stage=state.get('stage',''),
                                 consecutive_failures=state.get('consecutive_failures',0)+1)
                    stage=state.get('stage','')
                # Include bounded diagnostic context (never the bearer token)
                # so accounting PolicyErrors can be distinguished from transport.
                detail=re.sub(r'dkn_[A-Za-z0-9_-]{12,256}','dkn_[REDACTED]',str(exc)).replace('\\n',' ')[:200]
                logger.warning('Node monitor cycle failed for %s at %s (%s): %s',node_id,stage,type(exc).__name__,detail)
            finally:record(cycle_seconds=round(time.monotonic()-started,3))

        def worker(node_id,retired):
            while not stop.is_set() and not retired.is_set():
                started=time.monotonic();cycle(node_id,retired)
                # Fixed start-to-start cadence; slow work does not add another
                # full sleep. A tiny floor prevents a retry/busy-loop storm.
                wait=max(.01,cadence-(time.monotonic()-started))
                if retired.wait(wait):return

        def run():
            try:
                if stop.wait(initial_delay):return
                while not stop.is_set():
                    with self.store.lock:
                        ids={r[0] for r in self.store.db.execute('SELECT id FROM remote_nodes WHERE enabled=1')}
                    for node_id,(thread,retired) in list(workers.items()):
                        if node_id not in ids:retired.set()
                        if not thread.is_alive():
                            workers.pop(node_id)
                            if node_id not in ids:
                                with self._monitor_lock:self._monitor_state.pop(node_id,None)
                    for node_id in sorted(ids):
                        if stop.is_set():break
                        if node_id in workers:continue
                        retired=threading.Event()
                        thread=threading.Thread(target=worker,args=(node_id,retired),
                                                name='dark-node-monitor-'+node_id,daemon=True)
                        workers[node_id]=(thread,retired);thread.start()
                    if stop.wait(min(1.0,cadence)):return
            finally:
                for thread,retired in workers.values():retired.set()
                # Keep the supervisor alive until workers drain, preventing a
                # start/close/start race from resurrecting an old generation.
                for thread,_retired in workers.values():thread.join()
        self.thread=threading.Thread(target=run,name='dark-node-health',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6.0)
            if not self.thread.is_alive():self.thread=None

    @installation_operation
    def remote_logs(self,node_id:str,kind:str='process',limit:int=300)->dict:
        if kind not in {'process','error','access'}:raise PolicyError('Unknown Node log kind')
        if type(limit)is not int or not 1<=limit<=1000:raise PolicyError('Invalid Node log limit')
        doc,ms=self._request(node_id,'/node/api/logs/'+kind,timeout=12.0)
        if not isinstance(doc,dict) or doc.get('kind')!=kind or not isinstance(doc.get('lines'),list):
            raise PolicyError('Invalid Node log response')
        return {'latency_ms':ms,'kind':kind,'lines':[str(x)[:2000] for x in doc['lines'][-limit:]]}

    @installation_operation
    def remote_update_status(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/v1/update/status',timeout=12.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('update'),dict):
            raise PolicyError('Invalid Node update status response')
        return {'latency_ms':ms,'update':doc['update']}

    @installation_operation
    def remote_update_check(self,node_id:str,commit:str)->dict:
        if not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit):raise PolicyError('Exact Hub commit required')
        doc,ms=self._request(node_id,'/node/api/v1/update/check','POST',{'commit':commit},30.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('update'),dict):
            raise PolicyError('Invalid Node update check response')
        return {'latency_ms':ms,'update':doc['update']}

    @installation_operation
    def remote_update_start(self,node_id:str,commit:str)->dict:
        if not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit):raise PolicyError('Exact Hub commit required')
        doc,ms=self._request(node_id,'/node/api/v1/update/start','POST',{'commit':commit},30.0)
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('update'),dict):
            raise PolicyError('Invalid Node update start response')
        return {'latency_ms':ms,'update':doc['update']}

    def rotate_token(self,node_id:str,new_token:str)->dict:
        from node_credentials import NodeCredentials
        coordinator=NodeCredentials(self)
        binding=self.installations.capture(node_id)
        saved=coordinator.begin(node_id,new_token,binding_id=binding['binding_id'])
        return coordinator.retry(node_id,saved['attempt_id'])

    def remote_core(self,node_id:str,action:str)->dict:
        self.get(node_id)  # Unknown IDs are errors, not superseded operations.
        try:
            with self.installations.operation(node_id):
                return self._remote_core_operation(node_id,action)
        except StaleInstallation:
            # Preserve the public pending/executed contract even when a Node
            # is deleted/replaced mid-flight. Never return the obsolete result.
            control=self.commands.status(node_id)
            return {'queued':bool(control['pending']),'executed':False,'delivery_state':'superseded',
                    'control':control,'result':None}

    def _remote_core_operation(self,node_id:str,action:str)->dict:
        if not isinstance(action,str) or action not in {'validate','restart','start','stop'}:
            raise PolicyError('Unsupported remote core action')
        if action=='validate':
            doc,ms=self._request(node_id,'/node/api/core/validate','POST',{})
            if not isinstance(doc,dict) or not isinstance(doc.get('engine'),dict):
                self._request_failed(node_id,'Invalid remote core response')
                raise PolicyError('Invalid remote core response')
            return {'latency_ms':ms,'result':doc,'queued':False,'validated':True}
        self.commands.record(node_id,action)
        return self.deliver_pending_control(node_id)

    @installation_operation
    def deliver_pending_control(self,node_id:str)->dict:
        with self._node_operation(node_id):
            command=self.commands.status(node_id)
            if not command['pending']:return self.commands.deliver(node_id)
            try:
                node=self.get(node_id)
                if not node['enabled']:
                    return self.commands.defer(node_id,command,'Node is disabled; enable it to deliver the pending command','disabled')
                result=self.probe(node_id,timeout=5.0);health=result['health']
                if health.get('agent_only') is True and health.get('node_id')!=self.installations.current(node_id)['agent_id']:
                    return self.commands.defer(node_id,command,'Agent identity mismatch; verify Node enrolment','identity_mismatch')
                capabilities=health.get('capabilities')
                version=capabilities.get('ordered_control') if isinstance(capabilities,dict) else None
                if health.get('agent_only') is not True or type(version) is not int or version!=1:
                    return self.commands.defer(node_id,command,'Agent lacks ordered control v1; update the Node Agent. No legacy command was sent','unsupported_agent')
                # A resumed core must not run a known-outdated configuration.
                desired=self.desired_state(node_id,include_payload=False)
                if command['action']!='stop' and (desired.get('pending') or desired.get('last_error')):
                    return self.commands.defer(node_id,command,'Waiting for desired configuration acknowledgement before resume','configuration_pending')
            except (PolicyError,OSError,ValueError) as exc:
                return self.commands.defer(node_id,command,str(exc),'pending')
            return self.commands.deliver(node_id,expected_command_id=command['command_id'])

    @installation_operation
    def remote_inbounds(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/inbounds')
        if not isinstance(doc,list):
            self._request_failed(node_id,'Invalid node inbound response');raise PolicyError('Invalid node inbound response')
        return {'latency_ms':ms,'items':doc}

    @installation_operation
    def traffic_matrix_probe(self,node_id:str,port:int,outbound_tag:str,*,attempts:int=2,timeout_seconds:int=5)->dict:
        if type(port)is not int or not 1<=port<=65535 or not isinstance(outbound_tag,str) or not outbound_tag:
            raise PolicyError('Invalid Traffic Matrix probe request')
        if type(attempts)is not int or not 1<=attempts<=3 or type(timeout_seconds)is not int or not 1<=timeout_seconds<=10:
            raise PolicyError('Invalid Node probe limits')
        payload={'port':port,'outboundTag':outbound_tag,'attempts':attempts,'timeoutSeconds':timeout_seconds}
        doc,ms=self._request(node_id,'/node/api/v1/traffic-matrix/probe','POST',payload,
                             min(30.0,float(attempts*timeout_seconds+8)))
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('probe'),dict):
            raise PolicyError('Invalid Node Traffic Matrix probe response')
        return {'node_id':node_id,'latency_ms':ms,'listenerReady':bool(doc.get('listenerReady')),
                'probe':doc['probe'],'productionTrafficMutation':False}

    @installation_operation
    def outbound_probe(self,node_id:str,tag:str,*,attempts:int=2,timeout_seconds:int=5)->dict:
        if not isinstance(tag,str) or not tag or len(tag)>128:
            raise PolicyError('Invalid outbound probe tag')
        if type(attempts)is not int or not 1<=attempts<=3 or type(timeout_seconds)is not int or not 1<=timeout_seconds<=10:
            raise PolicyError('Invalid Node outbound probe limits')
        payload={'tag':tag,'attempts':attempts,'timeoutSeconds':timeout_seconds}
        doc,ms=self._request(node_id,'/node/api/v1/outbounds/probe','POST',payload,
                             min(30.0,float(attempts*timeout_seconds+8)))
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('probe'),dict):
            raise PolicyError('Invalid Node outbound probe response')
        return {'node_id':node_id,'latency_ms':ms,'probe':doc['probe'],'productionTrafficMutation':False}

    @installation_operation
    def warp_endpoint_probe(self,node_id:str,tag:str,endpoints:list[str]|None=None,*,attempts:int=2,timeout_seconds:int=4,
                            outbound:dict|None=None)->dict:
        if type(attempts)is not int or not 1<=attempts<=3 or type(timeout_seconds)is not int or not 1<=timeout_seconds<=10:
            raise PolicyError('Invalid Node WARP probe limits')
        if endpoints is not None and (not isinstance(endpoints,list) or not 1<=len(endpoints)<=20):
            raise PolicyError('Select 1-20 WARP endpoints')
        payload={'tag':str(tag or 'warp'),'attempts':attempts,'timeoutSeconds':timeout_seconds}
        if endpoints is not None:payload['endpoints']=endpoints
        if outbound is not None:payload['outbound']=outbound
        count=len(endpoints) if isinstance(endpoints,list) else 15
        doc,ms=self._request(node_id,'/node/api/v1/warp/endpoints/probe','POST',payload,
                             min(30.0,max(8.0,float(max(1,count)*timeout_seconds+8))))
        if not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or not isinstance(doc.get('items'),list):
            raise PolicyError('Invalid Node WARP endpoint probe response')
        return {'node_id':node_id,'latency_ms':ms,'current':doc.get('current',''),'items':doc['items'],
                'productionTrafficMutation':False}

    @installation_operation
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

    @installation_operation
    def sync_desired_state(self,node_id:str,state:dict,*,legacy_bundles:list[dict]|None=None)->dict:
        if not isinstance(state,dict) or type(state.get('revision')) is not int or not isinstance(state.get('hash'),str) or not isinstance(state.get('payload'),dict):
            raise PolicyError('Invalid Hub desired-state envelope')
        with self._node_operation(node_id):
            control=self.deliver_pending_control(node_id)
            # A gated Start must not deadlock configuration -> accounting -> lease.
            node_health=json.loads(self.get(node_id).get('last_health') or '{}')
            lease_start=(control['control'].get('action') in ('start','restart') and
                         (node_health.get('capabilities') or {}).get('accounting_lease')==1)
            if control['queued'] and control['delivery_state']!='configuration_pending' and not lease_start:
                return {**control,'desired_state_applied':False,'sync_deferred':True,'items':[]}
            current=self.desired_state(node_id)
            if state.get('revision')!=current['revision'] or state.get('hash')!=current['hash']:
                raise PolicyError('Desired state changed before delivery; retry synchronization')
            result=self._sync_desired_state_locked(node_id,state,legacy_bundles=legacy_bundles)
            control=self.deliver_pending_control(node_id)
            result['control']=control['control'];result['queued']=control['queued']
            if control.get('executed'):result['core']=control['result']['engine']
            return result

    def _sync_desired_state_locked(self,node_id:str,state:dict,*,legacy_bundles:list[dict]|None=None,_requester=None)->dict:
        if not isinstance(state,dict) or type(state.get('revision')) is not int or not isinstance(state.get('hash'),str) or not isinstance(state.get('payload'),dict):
            raise PolicyError('Invalid Hub desired-state envelope')
        body={'revision':state['revision'],'hash':state['hash'],'payload':state['payload']}
        try:
            doc,ms=(_requester or self._request)(node_id,'/node/api/v1/state/apply','POST',body,30.0)
        except PolicyError as ex:
            relay_required=any(str(o.get('tag') or '').startswith(('dark-relay-','dark-swap-'))
                               for o in state['payload'].get('sections',{}).get('outbounds',[]) if isinstance(o,dict))
            if legacy_bundles is not None and not relay_required and str(ex).startswith('Node HTTP 404'):
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
        with self._node_transaction(node_id) as db:
            current=db.execute('SELECT revision,desired_hash FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
            if not current or int(current['revision'])!=state['revision'] or current['desired_hash']!=state['hash']:
                raise PolicyError('Node acknowledged a stale desired state')
            assigned={int(r[0]) for r in db.execute(
                'SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=?',(node_id,))}
            # Hub-generated relay listeners intentionally have no customer
            # assignment row. Recheck their authoritative owner-scoped intent
            # at ACK time, without exempting arbitrary high IDs or real inbounds.
            generated = (set(self.managed_assignment_sources(node_id))
                         if self.managed_assignment_sources is not None else set())
            if not desired_sources <= assigned | generated:
                raise PolicyError('Node assignments changed while applying desired state')
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

    @installation_operation
    def sync_mirrors(self,node_id:str,bundles:list[dict])->dict:
        if self.commands.status(node_id)['pending']:
            raise PolicyError('Ordered Node control is pending; legacy mirror synchronization is deferred')
        if not isinstance(bundles,list) or len(bundles)>256:raise PolicyError('Invalid node mirror bundle')
        doc,ms=self._request(node_id,'/node/api/mirrors/sync','POST',{'assignments':bundles},30.0)
        if not isinstance(doc,dict) or not isinstance(doc.get('items'),list):
            self._request_failed(node_id,'Invalid node mirror sync response');raise PolicyError('Invalid node mirror sync response')
        now=time.time()
        by_source={int(x.get('sourceInboundId')):x for x in doc['items'] if isinstance(x,dict) and type(x.get('sourceInboundId')) is int}
        with self._node_transaction(node_id) as db:
            for bundle in bundles:
                source=int(bundle['sourceInboundId']);item=by_source.get(source,{})
                db.execute('''UPDATE remote_node_inbounds SET remote_inbound_id=?,last_sync=?,last_error=?,updated_at=?
                              WHERE node_id=? AND local_inbound_id=?''',
                           (int(item.get('remoteInboundId') or 0),now,str(item.get('error') or '')[:300],now,node_id,source))
        return {'latency_ms':ms,'items':doc['items'],'core':doc.get('core',{}),'synced_at':now}

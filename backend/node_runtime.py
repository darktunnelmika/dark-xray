"""Agent-only desired-state runtime for DARK XRAY nodes.

The Hub owns configuration and policy. This runtime retains only executable
state, observations, cumulative counters and revision acknowledgements.
"""
from __future__ import annotations

from pathlib import Path
import base64
import copy
import hashlib
import json
import os
import re
import tempfile
import time
import uuid

from core import CoreEngine,CoreError
from dark_policy import Store,PolicyError
from guard_bridge import BrokerClient

STATE_SECTIONS=('outbounds','routing','dns','policy','observatory','ipguard')


class NodeRuntime:
    def __init__(self,store:Store,engine:CoreEngine,scope:str):
        self.store,self.engine,self.scope=store,engine,str(scope)
        if not self.scope or len(self.scope)>128:raise PolicyError('Invalid node runtime scope')
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS node_runtime_identity(
              scope TEXT PRIMARY KEY,installation_id TEXT NOT NULL UNIQUE,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS node_runtime_state(
              scope TEXT PRIMARY KEY,applied_revision INTEGER NOT NULL DEFAULT 0,
              applied_hash TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS node_runtime_inbounds(
              scope TEXT NOT NULL,source_inbound_id INTEGER NOT NULL,local_inbound_id INTEGER NOT NULL,
              source_tag TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL,
              PRIMARY KEY(scope,source_inbound_id));
            CREATE TABLE IF NOT EXISTS node_runtime_clients(
              scope TEXT NOT NULL,source_email TEXT NOT NULL,mirror_email TEXT NOT NULL,
              updated_at REAL NOT NULL,PRIMARY KEY(scope,source_email),UNIQUE(scope,mirror_email));
            CREATE TABLE IF NOT EXISTS node_runtime_traffic_resets(
              scope TEXT NOT NULL,reset_id TEXT NOT NULL,source_email TEXT NOT NULL,
              up_bytes INTEGER NOT NULL,down_bytes INTEGER NOT NULL,at REAL NOT NULL,
              PRIMARY KEY(scope,reset_id));
            CREATE TABLE IF NOT EXISTS node_runtime_commands(
              scope TEXT PRIMARY KEY,revision INTEGER NOT NULL,
              command_id TEXT NOT NULL,action TEXT NOT NULL,
              phase TEXT NOT NULL CHECK(phase IN ('pending','applied','failed')),
              updated_at REAL NOT NULL,applied_at REAL NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS node_runtime_control(
              scope TEXT PRIMARY KEY,
              desired_running INTEGER NOT NULL CHECK(desired_running IN (0,1)),
              manual_stop INTEGER NOT NULL CHECK(manual_stop IN (0,1)),
              updated_at REAL NOT NULL);
            ''')
        # The identity belongs to this durable Agent state, not its address,
        # token or Python process. Fresh state gets a fresh identity; cloning an
        # entire state directory deliberately clones it (not hardware attestation).
        with store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO node_runtime_identity VALUES(?,?,?)',
                       (self.scope,uuid.uuid4().hex,time.time()))
            self.installation_id=db.execute(
                'SELECT installation_id FROM node_runtime_identity WHERE scope=?',(self.scope,)).fetchone()[0]
        if not isinstance(self.installation_id,str) or not re.fullmatch(r'[0-9a-f]{32}',self.installation_id):
            raise PolicyError('Invalid persisted Node installation identity')
        self.engine.wants_running=self.control_status()['effective_running']

    def control_status(self)->dict:
        with self.store.lock:
            row=self.store.db.execute(
                'SELECT desired_running,manual_stop,updated_at FROM node_runtime_control WHERE scope=?',
                (self.scope,)).fetchone()
        desired=bool(row['desired_running']) if row else bool(self.engine.config.core_autostart)
        paused=bool(row['manual_stop']) if row else False
        return {'desired_running':desired,'manual_stop':paused,'effective_running':desired and not paused,
                'persisted':row is not None,'updated_at':float(row['updated_at']) if row else 0.0}

    def _write_control(self,db,desired:bool,paused:bool)->None:
        db.execute('''INSERT INTO node_runtime_control(scope,desired_running,manual_stop,updated_at)
                      VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                      desired_running=excluded.desired_running,manual_stop=excluded.manual_stop,
                      updated_at=excluded.updated_at''',
                   (self.scope,int(desired),int(paused),time.time()))

    def command_status(self)->dict:
        """Latest accepted control identity; `phase` is historical, not liveness."""
        with self.store.lock:
            row=self.store.db.execute(
                'SELECT * FROM node_runtime_commands WHERE scope=?',(self.scope,)).fetchone()
        if not row:
            return {'persisted':False,'revision':0,'command_id':'','action':'',
                    'phase':'','pending':False,'last_error':''}
        return {**dict(row),'persisted':True,'pending':row['phase']!='applied'}

    def ordered_command(self,body:dict)->dict:
        """Apply a Hub identity under the same lock as desired-state changes.

        Persist the high-water mark BEFORE touching Xray. A rejected/failed newer
        command must still fence delayed older commands. A committed receipt
        makes a retry after a lost HTTP response a read, not another Restart.
        A crash between process effects and receipt commit remains ambiguous:
        pending/failed commands may be retried (no physical exactly-once claim).
        """
        if not isinstance(body,dict) or set(body)!={'nodeId','revision','commandId','action'}:
            raise PolicyError('Invalid ordered Node command envelope')
        revision=body['revision'];identity=body['commandId'];action=body['action']
        if body['nodeId']!=self.scope:raise PolicyError('Command targets a different Node')
        if type(revision) is not int or not 0<revision<2**63:raise PolicyError('Invalid command revision')
        if not isinstance(identity,str) or not re.fullmatch(r'[0-9a-f]{32}',identity):
            raise PolicyError('Invalid command identity')
        if not isinstance(action,str) or action not in {'start','stop','restart'}:
            raise PolicyError('Unsupported ordered Node action')
        with self.engine.lock:
            self.engine._write()
            previous=self.command_status()
            if previous['persisted']:
                if revision<previous['revision']:
                    raise CoreError('Refusing stale Node command revision',status=409)
                if revision==previous['revision']:
                    if identity!=previous['command_id'] or action!=previous['action']:
                        raise CoreError('Command revision belongs to a different identity/action',status=409)
                    if previous['phase']=='applied':return self._command_ack(previous,duplicate=True)
                elif identity==previous['command_id']:
                    raise CoreError('Command identity already belongs to a different revision',status=409)
            with self.store.transaction() as db:
                db.execute("""INSERT INTO node_runtime_commands
                              (scope,revision,command_id,action,phase,updated_at)
                              VALUES(?,?,?,?,'pending',?) ON CONFLICT(scope) DO UPDATE SET
                              revision=excluded.revision,command_id=excluded.command_id,
                              action=excluded.action,phase='pending',updated_at=excluded.updated_at,
                              applied_at=0,last_error=''""",
                           (self.scope,revision,identity,action,time.time()))
            try:
                result=self._command_locked(action)
                expected='stopped' if action=='stop' else 'running'
                if not isinstance(result,dict) or result.get('state')!=expected:
                    raise CoreError('Xray did not reach the commanded state',status=503)
                with self.store.transaction() as db:
                    db.execute("""UPDATE node_runtime_commands SET phase='applied',applied_at=?,last_error=''
                                  WHERE scope=? AND revision=? AND command_id=?""",
                               (time.time(),self.scope,revision,identity))
            except Exception as exc:
                with self.store.transaction() as db:
                    db.execute("""UPDATE node_runtime_commands SET phase='failed',last_error=?
                                  WHERE scope=? AND revision=? AND command_id=?""",
                               (str(exc)[:500],self.scope,revision,identity))
                raise
            return self._command_ack(self.command_status(),duplicate=False)

    def _command_ack(self,receipt:dict,*,duplicate:bool)->dict:
        core=self.engine.runtime_state()
        expected='stopped' if receipt['action']=='stop' else 'running'
        return {'service':'DARK XRAY NODE','node_id':self.scope,'revision':receipt['revision'],
                'commandId':receipt['command_id'],'action':receipt['action'],
                'applied':receipt['phase']=='applied' and core.get('state')==expected,
                'duplicate':duplicate,'engine':core,'run_control':self.control_status(),
                'control_receipt':receipt}

    def command(self,action:str)->dict:
        with self.engine.lock:
            # Keep the old Hub contract until ordered control is first used.
            # Afterwards, an unversioned request cannot bypass the receipt fence.
            if action!='validate' and self.command_status()['persisted']:
                raise CoreError('Ordered Node control is active; use /node/api/v1/control',status=409)
            return self._command_locked(action)

    def _command_locked(self,action:str)->dict:
        # An authenticated Stop is a durable pause, not a temporary process kill.
        # Caller holds engine.lock; ordinary configuration cannot undo this pause.
        if action=='validate':return self.engine.command(action)
        if action not in {'start','restart','stop'}:raise PolicyError('Unsupported Node core action')
        self.engine._write()
        previous=self.control_status()
        if action=='stop':
            with self.store.transaction() as db:
                self._write_control(db,previous['desired_running'],True)
            # Even a failed final traffic snapshot must not cause an automatic
            # restart. Maintenance retries the safe stop later.
            self.engine.wants_running=False
            return self.engine.command('stop')
        with self.store.transaction() as db:self._write_control(db,True,False)
        try:return self.engine.command(action)
        except Exception:
            with self.store.transaction() as db:
                self._write_control(db,previous['desired_running'],previous['manual_stop'])
            self.engine.wants_running=previous['effective_running']
            raise

    def reconcile_control(self)->bool:
        # Restore the last accepted control intent from local SQLite, with no
        # Hub connectivity needed. Never start a child here: flush/start owns it.
        with self.engine.lock:
            self.engine.wants_running=False
            wanted=self.control_status()['effective_running']
            self.engine.wants_running=wanted
            if not wanted and self.engine.running:self.engine.command('stop')
            return wanted

    def mirror_email(self,source_email:str)->str:
        if not isinstance(source_email,str) or not source_email or len(source_email)>128:
            raise PolicyError('Invalid mirrored client identity')
        digest=hashlib.sha256((self.scope+'\0'+source_email).encode()).hexdigest()[:24]
        return 'nm_'+digest

    @staticmethod
    def _canonical(payload:dict)->tuple[str,str]:
        if not isinstance(payload,dict):raise PolicyError('Desired state payload must be an object')
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        encoded=raw.encode()
        if len(encoded)>8*1024*1024:raise PolicyError('Desired state payload exceeds 8 MiB')
        return raw,hashlib.sha256(encoded).hexdigest()

    def status(self)->dict:
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM node_runtime_state WHERE scope=?',(self.scope,)).fetchone()
        if not row:return {'appliedRevision':0,'appliedHash':'','updatedAt':0,'lastError':''}
        return {'appliedRevision':int(row['applied_revision']),'appliedHash':str(row['applied_hash']),
                'updatedAt':float(row['updated_at']),'lastError':str(row['last_error'])}

    def _validate_envelope(self,envelope:dict)->tuple[int,str,dict]:
        if not isinstance(envelope,dict) or set(envelope)!={'revision','hash','payload'}:
            raise PolicyError('Invalid desired-state envelope')
        revision=envelope['revision'];digest=envelope['hash'];payload=envelope['payload']
        if type(revision)is not int or revision<1:raise PolicyError('Desired-state revision must be positive')
        if not isinstance(digest,str) or len(digest)!=64:raise PolicyError('Invalid desired-state hash')
        _raw,actual=self._canonical(payload)
        if actual!=digest:raise PolicyError('Desired-state hash mismatch')
        if payload.get('schema')!=1 or not isinstance(payload.get('nodeId'),str):
            raise PolicyError('Unsupported desired-state schema')
        sections=payload.get('sections')
        if not isinstance(sections,dict) or set(sections)!=set(STATE_SECTIONS):
            raise PolicyError('Desired-state sections are incomplete')
        assignments=payload.get('assignments')
        if not isinstance(assignments,list) or len(assignments)>256:raise PolicyError('Invalid desired-state assignments')
        for item in assignments:
            if not isinstance(item,dict) or set(item)!={'sourceInboundId','inbound','clients'}:
                raise PolicyError('Invalid node assignment')
            if not isinstance(item['clients'],list) or len(item['clients'])>100000:
                raise PolicyError('Invalid node assignment clients')
        security=payload.get('security')
        if not isinstance(security,dict) or not isinstance(security.get('clients',[]),list):
            raise PolicyError('Invalid desired-state security policy')
        files=payload.get('files',[])
        if not isinstance(files,list) or len(files)>512:raise PolicyError('Invalid managed Node file set')
        total=0;ids=set()
        for item in files:
            if not isinstance(item,dict) or set(item)!={'id','kind','sha256','data'}:raise PolicyError('Invalid managed Node file')
            file_id=item.get('id');kind=item.get('kind');file_digest=item.get('sha256');data=item.get('data')
            if not isinstance(file_id,str) or len(file_id)!=64 or any(c not in '0123456789abcdef' for c in file_id) or file_id in ids:
                raise PolicyError('Invalid or duplicate managed Node file ID')
            if kind not in {'certificate','private-key'} or not isinstance(file_digest,str) or len(file_digest)!=64 or not isinstance(data,str):
                raise PolicyError('Invalid managed Node TLS metadata')
            if len(data)>1400000:raise PolicyError('Managed Node TLS content exceeds the file limit')
            try:raw=base64.b64decode(data,validate=True)
            except Exception as ex:raise PolicyError('Managed Node TLS content is not valid base64') from ex
            if not raw or len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=file_digest:
                raise PolicyError('Managed Node TLS hash/size validation failed')
            total+=len(raw);ids.add(file_id)
        if total>4*1024*1024:raise PolicyError('Managed Node TLS payload exceeds 4 MiB')
        if type(payload.get('desiredRunning',True)) is not bool:raise PolicyError('Invalid desiredRunning')
        # A TLS file digest is not the digest of the entire desired state.
        return revision,digest,payload

    def _policy_map(self,payload:dict)->dict[str,dict]:
        result={}
        for item in payload.get('security',{}).get('clients',[]):
            if not isinstance(item,dict):raise PolicyError('Invalid node client policy')
            email=item.get('sourceEmail')
            if not isinstance(email,str) or not email or len(email)>128 or email in result:
                raise PolicyError('Invalid or duplicate node client policy')
            limit_ip=item.get('limitIp',0);limit_hwid=item.get('limitHwid',0)
            if type(limit_ip)is not int or not 0<=limit_ip<=1000 or type(limit_hwid)is not int or not 0<=limit_hwid<=1000:
                raise PolicyError('Invalid node IP/HWID limit')
            for key in ('globalIpBlocked','globalDeviceBlocked'):
                if key in item and type(item[key]) is not bool:raise PolicyError('Invalid global node block state')
            result[email]=copy.deepcopy(item)
        return result

    @staticmethod
    def _managed_file_bytes(payload:dict)->dict[str,bytes]:
        out={}
        for item in payload.get('files',[]):
            raw=base64.b64decode(item['data'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise PolicyError('Managed Node TLS hash mismatch')
            out[item['id']]=raw
        return out

    @staticmethod
    def _materialize_managed(files:dict[str,bytes],root:Path)->dict[str,str]:
        if root.is_symlink():raise PolicyError('Managed Node TLS directory cannot be a symlink')
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not root.is_dir() or root.stat().st_mode&0o077:raise PolicyError('Managed Node TLS directory must be private')
        mapping={}
        for file_id,raw in files.items():
            if len(file_id)!=64 or any(c not in '0123456789abcdef' for c in file_id):
                raise PolicyError('Invalid managed Node file ID')
            path=root/(file_id+'.pem')
            if path.is_symlink():raise PolicyError('Managed Node TLS symlink refused')
            if path.exists():
                mode=path.stat().st_mode
                if not path.is_file() or mode&0o077 or hashlib.sha256(path.read_bytes()).hexdigest()!=hashlib.sha256(raw).hexdigest():
                    raise PolicyError('Existing managed Node TLS file is unsafe')
            else:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
            mapping['managed://'+file_id]=str(path)
        return mapping

    @staticmethod
    def _rewrite_managed_refs(inbound:dict,mapping:dict[str,str])->dict:
        inbound=copy.deepcopy(inbound);st=inbound.get('streamSettings',{})
        tls=st.get('tlsSettings',{}) if isinstance(st,dict) else {}
        certs=tls.get('certificates',[]) if isinstance(tls,dict) else []
        if isinstance(certs,list):
            for cert in certs:
                if not isinstance(cert,dict):continue
                for key in ('certificateFile','keyFile'):
                    value=cert.get(key)
                    if isinstance(value,str) and value.startswith('managed://'):
                        if value not in mapping:raise PolicyError('Inbound references unknown managed Node TLS file')
                        cert[key]=mapping[value]
        return inbound

    def _validated_model(self,payload:dict)->dict:
        policies=self._policy_map(payload);assignments=payload['assignments']
        seen=set();normalized=[];managed=self._managed_file_bytes(payload)
        with tempfile.TemporaryDirectory(prefix='dark-node-validate.') as td:
            root=Path(td);stage_store=Store(root/'stage.sqlite3')
            stage_engine=CoreEngine(self.engine.config,stage_store,root/'runtime')
            try:
                managed_map=self._materialize_managed(managed,root/'managed-tls')
                for item in assignments:
                    source=item['sourceInboundId']
                    if type(source)is not int or source<1 or source in seen:raise PolicyError('Invalid/duplicate source inbound')
                    seen.add(source)
                    inbound=copy.deepcopy(item['inbound'])
                    if not isinstance(inbound,dict):raise PolicyError('Invalid node inbound payload')
                    inbound.pop('id',None);inbound.pop('applied',None)
                    validated_inbound=self._rewrite_managed_refs(inbound,managed_map)
                    saved=stage_engine.save_inbound(validated_inbound);local_id=int(saved['id'])
                    clients=[]
                    for entry in item['clients']:
                        if not isinstance(entry,dict) or set(entry)!={'sourceEmail','client'}:
                            raise PolicyError('Invalid node client entry')
                        source_email=entry['sourceEmail']
                        if not isinstance(source_email,str) or not source_email or len(source_email)>128:
                            raise PolicyError('Invalid node source client')
                        raw=copy.deepcopy(entry['client'])
                        if not isinstance(raw,dict):raise PolicyError('Invalid node client payload')
                        mirror=self.mirror_email(source_email);policy=policies.get(source_email,{})
                        raw['email']=mirror;raw['limitHwid']=int(policy.get('limitHwid',0) or 0)
                        if policy.get('globalIpBlocked') or policy.get('globalDeviceBlocked'):raw['enable']=False
                        clients.append((source_email,mirror,raw))
                    normalized.append({'sourceInboundId':source,'localInboundId':local_id,
                                       'sourceTag':str(inbound.get('tag') or ''),'inbound':inbound,'clients':clients})
                merged={}
                for item in normalized:
                    for source_email,mirror,raw in item['clients']:
                        row=merged.setdefault(source_email,{'mirror':mirror,'raw':raw,'ids':[]})
                        if row['raw']!=raw:raise PolicyError('Conflicting node client payload')
                        row['ids'].append(item['localInboundId'])
                for source_email,row in merged.items():
                    stage_engine.create(row['raw'],sorted(set(row['ids'])))
                    policy=policies.get(source_email,{})
                    with stage_store.transaction() as db:
                        db.execute("INSERT OR IGNORE INTO owners(id) VALUES('_hub')")
                        db.execute('''INSERT INTO clients(id,owner,limit_ip,global_ip_block,global_device_block)
                                      VALUES(?,?,?,?,?)''',
                                   (row['mirror'],'_hub',int(policy.get('limitIp',0) or 0),
                                    int(bool(policy.get('globalIpBlocked'))),int(bool(policy.get('globalDeviceBlocked')))))
                for name in STATE_SECTIONS:
                    stage_engine.save_section(name,copy.deepcopy(payload['sections'][name]))
                validated=stage_engine.validate()
                return {'assignments':normalized,'policies':policies,'validated':validated}
            finally:
                stage_engine.close();stage_store.close()

    def _snapshot(self)->dict:
        names=('core_inbounds','core_clients','core_sections','clients','owners','observations','bans','core_devices',
               'node_runtime_inbounds','node_runtime_clients','node_runtime_state','node_runtime_traffic_resets','node_runtime_control')
        out={}
        with self.store.lock:
            for name in names:
                exists=self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
                if not exists:continue
                cols=[r[1] for r in self.store.db.execute('PRAGMA table_info('+name+')')]
                rows=[tuple(r) for r in self.store.db.execute('SELECT * FROM '+name)]
                out[name]=(cols,rows)
        return out

    def _restore_snapshot(self,snap:dict):
        with self.store.transaction() as db:
            for name,(cols,rows) in snap.items():
                db.execute('DELETE FROM '+name)
                if rows:
                    marks=','.join('?' for _ in cols)
                    db.executemany('INSERT INTO '+name+'('+','.join(cols)+') VALUES('+marks+')',rows)

    def _sync_guard_ports(self,payload:dict)->tuple[BrokerClient|None,list[int]|None]:
        mode=str(payload.get('sections',{}).get('ipguard',{}).get('mode','observe'))
        ports=sorted({int(a['inbound'].get('port') or 0) for a in payload['assignments']
                      if a['inbound'].get('enable',True) and int(a['inbound'].get('port') or 0)>0})
        client=BrokerClient(self.engine.config.guard_socket)
        try:status=client.status()
        except PolicyError:
            if mode=='enforce':raise
            return None,None
        old=[int(x) for x in status.get('allowed_ports',[])]
        # Reject before modifying the allowlist, so a rejection cannot strand
        # old Guard ports outside the rollback tuple.
        if mode=='enforce' and not status.get('direct_source_verified'):
            raise PolicyError('Node Guard has not verified direct packet sources')
        if status.get('runtime_port_updates'):
            if set(ports)!=set(old):client.set_ports(ports)
        elif set(ports)-set(old):
            if mode=='enforce':raise PolicyError('Node Guard root approval is missing for desired Xray ports')
        return client,old

    def reconcile_guard(self)->None:
        """Recover volatile broker ports from applied local state, even offline.

        No new revision, inbound edit or Xray restart is needed. The root
        broker continues to validate protected ports and direct-source approval.
        An unpaired/never-applied Node must not change the broker allowlist.
        """
        with self.engine.lock:
            if self.status()['appliedRevision']<1:return
            with self.store.lock:
                rows=self.store.db.execute(
                    'SELECT c.body FROM core_inbounds c JOIN node_runtime_inbounds n '
                    'ON c.id=n.local_inbound_id WHERE n.scope=?',(self.scope,)).fetchall()
            local={'sections':{'ipguard':self.engine.section('ipguard')},
                   'assignments':[{'inbound':json.loads(row['body'])} for row in rows]}
            self._sync_guard_ports(local)

    def apply(self,envelope:dict)->dict:
        # Validation, revision comparison, snapshot and commit share one lock.
        # A concurrent older request cannot overwrite a newer acknowledgement.
        with self.engine.lock:
            return self._apply_locked(copy.deepcopy(envelope))

    def _apply_locked(self,envelope:dict)->dict:
        revision,digest,payload=self._validate_envelope(envelope)
        if payload['nodeId']!=self.scope:raise PolicyError('Desired state targets a different Node')
        current=self.status()
        if revision<current['appliedRevision']:raise PolicyError('Refusing stale desired-state revision')
        if revision==current['appliedRevision']:
            if digest!=current['appliedHash']:raise PolicyError('Revision already belongs to a different desired state')
            if not current['lastError']:
                self.reconcile_control()
                self.reconcile_guard()
                return {'changed':False,'appliedRevision':revision,'appliedHash':digest,
                        'items':self.assignment_status(),'core':self.engine.runtime_state()}
        model=self._validated_model(payload);now=time.time()
        control=self.control_status()
        # Once ordered control is established, configuration snapshots (including
        # an in-flight old desiredRunning=False) cannot reverse accepted control.
        # Failed Start/Restart leaves the previous effective intent authoritative.
        desired_running=(control['desired_running'] if self.command_status()['persisted']
                         else payload.get('desiredRunning',True))
        effective_running=desired_running and not control['manual_stop']
        was_running=self.engine.running;wanted_running=self.engine.wants_running
        guard=None;old_guard_ports=None;snap=None
        try:
            # Stop/flush the OLD generation before replacing its rows. The
            # final counters belong to that generation, not to newly inserted
            # zero-valued client rows.
            self.engine.command('stop')
            snap=self._snapshot()
            with self.store.lock:
                counters={str(r['email']):(int(r['up']),int(r['down']))
                          for r in self.store.db.execute('SELECT email,up,down FROM core_clients')}
                old_mirrors={str(r[0]) for r in self.store.db.execute(
                    'SELECT mirror_email FROM node_runtime_clients WHERE scope=?',(self.scope,))}
            guard,old_guard_ports=self._sync_guard_ports(payload)
            managed_root=self.engine.runtime.parent/'managed-tls'
            managed_map=self._materialize_managed(self._managed_file_bytes(payload),managed_root)
            policies=model['policies'];assignments=model['assignments']
            with self.store.transaction() as db:
                db.execute('DELETE FROM core_clients');db.execute('DELETE FROM core_inbounds')
                for name in STATE_SECTIONS:
                    db.execute('''INSERT INTO core_sections(name,body) VALUES(?,?)
                                  ON CONFLICT(name) DO UPDATE SET body=excluded.body''',
                               (name,json.dumps(payload['sections'][name])))
                db.execute("INSERT OR IGNORE INTO owners(id) VALUES('_hub')")
                db.execute("DELETE FROM clients WHERE owner='_hub'")
                db.execute('DELETE FROM node_runtime_inbounds WHERE scope=?',(self.scope,))
                db.execute('DELETE FROM node_runtime_clients WHERE scope=?',(self.scope,))
                merged={}
                for item in assignments:
                    source=int(item['sourceInboundId']);body=copy.deepcopy(item['inbound'])
                    body.pop('id',None);body.pop('applied',None)
                    body=self._rewrite_managed_refs(body,managed_map)
                    db.execute('INSERT INTO core_inbounds(id,body) VALUES(?,?)',(source,json.dumps(body)))
                    db.execute('INSERT INTO node_runtime_inbounds(scope,source_inbound_id,local_inbound_id,source_tag,updated_at) VALUES(?,?,?,?,?)',
                               (self.scope,source,source,str(item.get('sourceTag') or ''),now))
                    for source_email,mirror,raw in item['clients']:
                        row=merged.setdefault(source_email,{'mirror':mirror,'raw':raw,'ids':[]})
                        row['ids'].append(source)
                for source_email,row in merged.items():
                    up,down=counters.get(row['mirror'],(0,0))
                    db.execute('INSERT INTO core_clients(email,body,inbounds,up,down) VALUES(?,?,?,?,?)',
                               (row['mirror'],json.dumps(row['raw']),json.dumps(sorted(set(row['ids']))),up,down))
                    db.execute('INSERT INTO node_runtime_clients(scope,source_email,mirror_email,updated_at) VALUES(?,?,?,?)',
                               (self.scope,source_email,row['mirror'],now))
                    policy=policies.get(source_email,{})
                    db.execute('''INSERT INTO clients(id,owner,limit_ip,global_ip_block,global_device_block)
                                  VALUES(?,?,?,?,?)''',
                               (row['mirror'],'_hub',int(policy.get('limitIp',0) or 0),
                                int(bool(policy.get('globalIpBlocked'))),int(bool(policy.get('globalDeviceBlocked')))))
                stale=old_mirrors-{v['mirror'] for v in merged.values()}
                for mirror in stale:
                    db.execute('DELETE FROM observations WHERE client_id=?',(mirror,))
                    db.execute('DELETE FROM core_devices WHERE email=?',(mirror,))
            if effective_running:
                core=self.engine.command('start')
            else:
                self.engine.validate();core=self.engine.runtime_state()
            with self.store.transaction() as db:
                self._write_control(db,desired_running,control['manual_stop'])
                db.execute('''INSERT INTO node_runtime_state(scope,applied_revision,applied_hash,updated_at,last_error)
                              VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                              applied_revision=excluded.applied_revision,applied_hash=excluded.applied_hash,
                              updated_at=excluded.updated_at,last_error=excluded.last_error''',
                           (self.scope,revision,digest,time.time(),''))
            # Keep previous managed TLS files for rollback/recovery; a successful
            # start alone is not permission to delete the old generation's keys.
            return {'changed':True,'appliedRevision':revision,'appliedHash':digest,
                    'items':self.assignment_status(),'core':core,'validatedHash':model['validated']['hash']}
        except Exception as ex:
            rollback_errors=[]
            if snap is not None:
                try:self._restore_snapshot(snap)
                except Exception as rollback:rollback_errors.append('state:'+type(rollback).__name__)
            if guard is not None and old_guard_ports is not None:
                try:guard.set_ports(old_guard_ports)
                except Exception as rollback:rollback_errors.append('guard:'+type(rollback).__name__)
            try:
                self.engine.command('start' if was_running else 'stop')
                self.engine.wants_running=wanted_running
            except Exception as rollback:rollback_errors.append('core:'+type(rollback).__name__)
            detail=str(ex)[:400]
            if rollback_errors:detail+='; rollback failed: '+','.join(rollback_errors)
            with self.store.transaction() as db:
                db.execute('''INSERT INTO node_runtime_state(scope,applied_revision,applied_hash,updated_at,last_error)
                              VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                              updated_at=excluded.updated_at,last_error=excluded.last_error''',
                           (self.scope,current['appliedRevision'],current['appliedHash'],time.time(),detail))
            if rollback_errors:raise CoreError(detail,status=503) from ex
            raise

    def assignment_status(self)->list[dict]:
        with self.store.lock:
            rows=self.store.db.execute('SELECT source_inbound_id,local_inbound_id FROM node_runtime_inbounds WHERE scope=? ORDER BY source_inbound_id',(self.scope,)).fetchall()
            counts={int(r[0]):0 for r in rows}
            for row in self.store.db.execute('SELECT source_email,mirror_email FROM node_runtime_clients WHERE scope=?',(self.scope,)):
                detail=self.store.db.execute('SELECT inbounds FROM core_clients WHERE email=?',(row['mirror_email'],)).fetchone()
                if not detail:continue
                for inbound_id in json.loads(detail['inbounds']):
                    if int(inbound_id) in counts:counts[int(inbound_id)]+=1
        return [{'sourceInboundId':int(row['source_inbound_id']),'remoteInboundId':int(row['local_inbound_id']),
                 'clients':counts.get(int(row['source_inbound_id']),0)} for row in rows]

    def reset_result(self,reset_id:str,source_email:str)->dict|None:
        if not isinstance(reset_id,str) or not 8<=len(reset_id)<=128 or not isinstance(source_email,str) or not source_email:
            raise PolicyError('Invalid node traffic reset identity')
        with self.store.lock:
            row=self.store.db.execute('SELECT source_email,up_bytes,down_bytes,at FROM node_runtime_traffic_resets WHERE scope=? AND reset_id=?',
                                      (self.scope,reset_id)).fetchone()
        if not row:return None
        if str(row['source_email'])!=source_email:raise PolicyError('Reset ID belongs to a different client')
        return {'sourceEmail':source_email,'up':int(row['up_bytes']),'down':int(row['down_bytes']),
                'capturedAt':float(row['at']),'cached':True}

    def remember_reset(self,reset_id:str,source_email:str,up:int,down:int)->dict:
        if type(up)is not int or type(down)is not int or up<0 or down<0:raise PolicyError('Invalid reset counters')
        now=time.time()
        with self.store.transaction() as db:
            existing=db.execute('SELECT source_email,up_bytes,down_bytes,at FROM node_runtime_traffic_resets WHERE scope=? AND reset_id=?',
                                (self.scope,reset_id)).fetchone()
            if existing:
                if str(existing['source_email'])!=source_email:raise PolicyError('Reset ID belongs to a different client')
                return {'sourceEmail':source_email,'up':int(existing['up_bytes']),'down':int(existing['down_bytes']),
                        'capturedAt':float(existing['at']),'cached':True}
            db.execute('INSERT INTO node_runtime_traffic_resets(scope,reset_id,source_email,up_bytes,down_bytes,at) VALUES(?,?,?,?,?,?)',
                       (self.scope,reset_id,source_email,up,down,now))
            db.execute('''DELETE FROM node_runtime_traffic_resets WHERE scope=? AND reset_id IN (
                          SELECT reset_id FROM node_runtime_traffic_resets WHERE scope=? ORDER BY at DESC LIMIT -1 OFFSET 5000)''',
                       (self.scope,self.scope))
        return {'sourceEmail':source_email,'up':up,'down':down,'capturedAt':now,'cached':False}

    def source_for_mirror(self,mirror:str)->str|None:
        with self.store.lock:
            row=self.store.db.execute('SELECT source_email FROM node_runtime_clients WHERE scope=? AND mirror_email=?',(self.scope,mirror)).fetchone()
        return str(row[0]) if row else None

    def mirror_for_source(self,source:str)->str|None:
        with self.store.lock:
            row=self.store.db.execute('SELECT mirror_email FROM node_runtime_clients WHERE scope=? AND source_email=?',(self.scope,source)).fetchone()
        return str(row[0]) if row else None

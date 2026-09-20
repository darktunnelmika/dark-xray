"""Agent-only desired-state runtime for DARK XRAY nodes.

The Hub is authoritative. A node keeps only the minimum local state needed to
run Xray, observe traffic/security, and acknowledge an applied Hub revision.
No owner account, reseller data, panel UI, finance data, or panel settings are
required on an agent-only node.
"""
from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
import tempfile
import time

from core import CoreEngine, CoreError
from dark_policy import Store, PolicyError
from guard_bridge import BrokerClient


STATE_SECTIONS=('outbounds','routing','dns','policy','observatory','ipguard')


class NodeRuntime:
    def __init__(self,store:Store,engine:CoreEngine,scope:str):
        self.store,self.engine,self.scope=store,engine,str(scope)
        if not self.scope or len(self.scope)>128:raise PolicyError('Invalid node runtime scope')
        with store.lock:
            store.db.executescript('''
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
            ''')

    def mirror_email(self,source_email:str)->str:
        if not isinstance(source_email,str) or not source_email or len(source_email)>128:
            raise PolicyError('Invalid mirrored client identity')
        digest=hashlib.sha256((self.scope+'\0'+source_email).encode()).hexdigest()[:24]
        return 'nm_'+digest

    @staticmethod
    def _canonical(payload:dict)->tuple[str,str]:
        if not isinstance(payload,dict):raise PolicyError('Desired state payload must be an object')
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        if len(raw)>8*1024*1024:raise PolicyError('Desired state payload exceeds 8 MiB')
        return raw,hashlib.sha256(raw.encode()).hexdigest()

    def status(self)->dict:
        with self.store.lock:r=self.store.db.execute(
            'SELECT * FROM node_runtime_state WHERE scope=?',(self.scope,)).fetchone()
        if not r:return {'appliedRevision':0,'appliedHash':'','updatedAt':0,'lastError':''}
        return {'appliedRevision':int(r['applied_revision']),'appliedHash':str(r['applied_hash']),
                'updatedAt':float(r['updated_at']),'lastError':str(r['last_error'])}

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
        security=payload.get('security')
        if not isinstance(security,dict) or not isinstance(security.get('clients',[]),list):
            raise PolicyError('Invalid desired-state security policy')
        if type(payload.get('desiredRunning',True)) is not bool:raise PolicyError('Invalid desiredRunning')
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

    def _validated_model(self,payload:dict)->dict:
        policies=self._policy_map(payload);assignments=payload['assignments']
        seen=set();normalized=[];all_sources=set()
        with tempfile.TemporaryDirectory(prefix='dark-node-validate.') as td:
            root=Path(td);stage_store=Store(root/'stage.sqlite3')
            stage_engine=CoreEngine(self.engine.config,stage_store,root/'runtime')
            try:
                for name in STATE_SECTIONS:
                    stage_engine.save_section(name,copy.deepcopy(payload['sections'][name]))
                source_to_local={}
                for item in assignments:
                    if not isinstance(item,dict) or set(item)!={'sourceInboundId','inbound','clients'}:
                        raise PolicyError('Invalid node assignment')
                    source=item['sourceInboundId']
                    if type(source)is not int or source<1 or source in seen:raise PolicyError('Invalid/duplicate source inbound')
                    seen.add(source)
                    inbound=copy.deepcopy(item['inbound'])
                    if not isinstance(inbound,dict):raise PolicyError('Invalid node inbound payload')
                    inbound.pop('id',None);inbound.pop('applied',None)
                    saved=stage_engine.save_inbound(inbound);local_id=int(saved['id']);source_to_local[source]=local_id
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
                        all_sources.add(source_email)
                    normalized.append({'sourceInboundId':source,'localInboundId':local_id,
                                       'sourceTag':str(inbound.get('tag') or ''),'inbound':inbound,'clients':clients})
                # One mirrored identity may be attached to multiple desired inbounds.
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
                validated=stage_engine.validate()
                # Persisted model keeps Hub source IDs; local IDs are re-created
                # deterministically as the source IDs on the real agent.
                return {'assignments':normalized,'policies':policies,'validated':validated}
            finally:
                stage_engine.close();stage_store.close()

    def _snapshot(self)->dict:
        names=('core_inbounds','core_clients','core_sections','clients','owners',
               'node_runtime_inbounds','node_runtime_clients','node_runtime_state')
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
        ports=sorted({int(a.get('inbound',{}).get('port') or 0) for a in payload.get('assignments',[])
                      if isinstance(a,dict) and isinstance(a.get('inbound'),dict) and int(a['inbound'].get('port') or 0)>0})
        client=BrokerClient(self.engine.config.guard_socket)
        try:status=client.status()
        except PolicyError:
            if mode=='enforce':raise
            return None,None
        old=[int(x) for x in status.get('allowed_ports',[])]
        if status.get('runtime_port_updates'):
            client.set_ports(ports)
        elif set(ports)-set(old):
            if mode=='enforce':raise PolicyError('Node Guard root approval is missing for desired Xray ports')
        if mode=='enforce' and not status.get('direct_source_verified'):
            raise PolicyError('Node Guard has not verified direct packet sources')
        return client,old

    def apply(self,envelope:dict)->dict:
        revision,digest,payload=self._validate_envelope(envelope)
        current=self.status()
        if current['appliedRevision']==revision and current['appliedHash']==digest and not current['lastError']:
            return {'changed':False,'appliedRevision':revision,'appliedHash':digest,'items':self.assignment_status(),
                    'core':self.engine.runtime_state()}
        if revision<current['appliedRevision']:
            raise PolicyError('Refusing stale desired-state revision')
        model=self._validated_model(payload);snap=self._snapshot();now=time.time()
        guard=None;old_guard_ports=None
        try:
            guard,old_guard_ports=self._sync_guard_ports(payload)
            policies=model['policies'];assignments=model['assignments']
            with self.store.lock:
                old_mirrors={str(r[0]) for r in self.store.db.execute(
                    'SELECT mirror_email FROM node_runtime_clients WHERE scope=?',(self.scope,))}
            # The agent is an owned runtime: the Hub model replaces all Xray
            # config tables, while observations/traffic history remain local.
            with self.store.transaction() as db:
                db.execute('DELETE FROM core_clients');db.execute('DELETE FROM core_inbounds')
                for name in STATE_SECTIONS:
                    db.execute('''INSERT INTO core_sections(name,body) VALUES(?,?)
                                  ON CONFLICT(name) DO UPDATE SET body=excluded.body''',
                               (name,json.dumps(payload['sections'][name])))
                db.execute("INSERT OR IGNORE INTO owners(id) VALUES('_hub')")
                # Agent-only nodes never sell/manage accounts locally.
                db.execute("DELETE FROM clients WHERE owner='_hub'")
                db.execute('DELETE FROM node_runtime_inbounds WHERE scope=?',(self.scope,))
                db.execute('DELETE FROM node_runtime_clients WHERE scope=?',(self.scope,))
                merged={}
                for item in assignments:
                    source=int(item['sourceInboundId']);body=copy.deepcopy(item['inbound']);body.pop('id',None);body.pop('applied',None)
                    db.execute('INSERT INTO core_inbounds(id,body) VALUES(?,?)',(source,json.dumps(body)))
                    db.execute('INSERT INTO node_runtime_inbounds(scope,source_inbound_id,local_inbound_id,source_tag,updated_at) VALUES(?,?,?,?,?)',
                               (self.scope,source,source,str(item.get('sourceTag') or ''),now))
                    for source_email,mirror,raw in item['clients']:
                        row=merged.setdefault(source_email,{'mirror':mirror,'raw':raw,'ids':[]})
                        row['ids'].append(source)
                for source_email,row in merged.items():
                    db.execute('INSERT INTO core_clients(email,body,inbounds) VALUES(?,?,?)',
                               (row['mirror'],json.dumps(row['raw']),json.dumps(sorted(set(row['ids'])))))
                    db.execute('INSERT INTO node_runtime_clients(scope,source_email,mirror_email,updated_at) VALUES(?,?,?,?)',
                               (self.scope,source_email,row['mirror'],now))
                    policy=policies.get(source_email,{})
                    db.execute('''INSERT INTO clients(id,owner,limit_ip,global_ip_block,global_device_block)
                                  VALUES(?,?,?,?,?)''',
                               (row['mirror'],'_hub',int(policy.get('limitIp',0) or 0),
                                int(bool(policy.get('globalIpBlocked'))),int(bool(policy.get('globalDeviceBlocked')))))
                # Remove stale observations/devices belonging to mirror identities
                # no longer controlled by the Hub.
                stale=old_mirrors-{v['mirror'] for v in merged.values()}
                for mirror in stale:
                    db.execute('DELETE FROM observations WHERE client_id=?',(mirror,))
                    db.execute('DELETE FROM core_devices WHERE email=?',(mirror,))
            core=self.engine.command('restart' if payload.get('desiredRunning',True) else 'stop')
            with self.store.transaction() as db:
                db.execute('''INSERT INTO node_runtime_state(scope,applied_revision,applied_hash,updated_at,last_error)
                              VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                              applied_revision=excluded.applied_revision,applied_hash=excluded.applied_hash,
                              updated_at=excluded.updated_at,last_error=excluded.last_error''',
                           (self.scope,revision,digest,time.time(),''))
            return {'changed':True,'appliedRevision':revision,'appliedHash':digest,'items':self.assignment_status(),'core':core,
                    'validatedHash':model['validated']['hash']}
        except Exception as ex:
            self._restore_snapshot(snap)
            if guard is not None and old_guard_ports is not None:
                try:guard.set_ports(old_guard_ports)
                except Exception:pass
            try:self.engine.command('restart' if self.engine.config.core_autostart else 'stop')
            except Exception:pass
            with self.store.transaction() as db:
                db.execute('''INSERT INTO node_runtime_state(scope,applied_revision,applied_hash,updated_at,last_error)
                              VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET updated_at=excluded.updated_at,last_error=excluded.last_error''',
                           (self.scope,current['appliedRevision'],current['appliedHash'],time.time(),str(ex)[:500]))
            raise

    def assignment_status(self)->list[dict]:
        with self.store.lock:
            rows=self.store.db.execute('SELECT source_inbound_id,local_inbound_id FROM node_runtime_inbounds WHERE scope=? ORDER BY source_inbound_id',(self.scope,)).fetchall()
            counts={int(r[0]):0 for r in rows}
            for r in self.store.db.execute('SELECT source_email,mirror_email FROM node_runtime_clients WHERE scope=?',(self.scope,)):
                detail=self.store.db.execute('SELECT inbounds FROM core_clients WHERE email=?',(r['mirror_email'],)).fetchone()
                if not detail:continue
                for inbound_id in json.loads(detail['inbounds']):
                    if int(inbound_id) in counts:counts[int(inbound_id)]+=1
        return [{'sourceInboundId':int(r['source_inbound_id']),'remoteInboundId':int(r['local_inbound_id']),
                 'clients':counts.get(int(r['source_inbound_id']),0)} for r in rows]

    def source_for_mirror(self,mirror:str)->str|None:
        with self.store.lock:r=self.store.db.execute(
            'SELECT source_email FROM node_runtime_clients WHERE scope=? AND mirror_email=?',(self.scope,mirror)).fetchone()
        return str(r[0]) if r else None

    def mirror_for_source(self,source:str)->str|None:
        with self.store.lock:r=self.store.db.execute(
            'SELECT mirror_email FROM node_runtime_clients WHERE scope=? AND source_email=?',(self.scope,source)).fetchone()
        return str(r[0]) if r else None

"""Durable Hub-owned run intent; transport retries reuse the same command identity.

Latest intent wins. Superseded, undelivered actions are not replayed as a FIFO.
Never keep a SQLite transaction open while calling a remote Node.
The registry dispatches this register through the authenticated ordered Agent
API. Historical receipt state must never be confused with current liveness.
"""
from __future__ import annotations
import json
import time
import uuid
from dark_policy import PolicyError
from node_installations import installation_operation


class NodeCommands:
    def __init__(self,registry):
        self.registry=registry;self.store=registry.store
        with self.store.lock:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS remote_node_control(
                node_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,command_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('start','stop','restart')),
                applied_revision INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,applied_at REAL NOT NULL DEFAULT 0)''')

    def status(self,node_id:str)->dict:
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM remote_node_control WHERE node_id=?',(node_id,)).fetchone()
        if not row:
            return {'revision':0,'command_id':'','action':'','desired_running':True,
                    'pending':False,'persisted':False,'last_error':'','applied_revision':0}
        result=dict(row)
        result.update(desired_running=row['action']!='stop',pending=row['revision']!=row['applied_revision'],persisted=True)
        return result

    def record(self,node_id:str,action:str)->dict:
        if not isinstance(action,str) or action not in {'start','stop','restart'}:raise PolicyError('Unsupported durable Node action')
        self.registry.get(node_id)
        with self.registry._node_transaction(node_id) as db:
            from node_replacement_deployment import assert_deployment_allows
            assert_deployment_allows(db,node_id,action)
            if not db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(node_id,)).fetchone():
                raise PolicyError('Node no longer exists')
            old=db.execute('SELECT * FROM remote_node_control WHERE node_id=?',(node_id,)).fetchone()
            # Retries of the same pending operation reuse its identity. A new
            # explicit request after success gets a new ordered identity.
            if old and old['action']==action and old['revision']!=old['applied_revision']:
                return self.status(node_id)
            revision=int(old['revision'])+1 if old else 1
            if revision>=2**63:raise PolicyError('Node command sequence exhausted')
            db.execute('''INSERT INTO remote_node_control(node_id,revision,command_id,action,updated_at)
                          VALUES(?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET
                          revision=excluded.revision,command_id=excluded.command_id,action=excluded.action,
                          updated_at=excluded.updated_at,last_error=''
                          ''',
                       (node_id,revision,uuid.uuid4().hex,action,time.time()))
            # The register is authoritative and included in Hub backups. Do not
            # make an emergency Stop depend on compiling/decrypting a config.
            # The desired-state getter/compiler reconciles it before delivery.
        return self.status(node_id)

    def defer(self,node_id:str,command:dict,error:str,reason:str)->dict:
        """Attach diagnostics only to the still-current pending identity."""
        with self.registry._node_transaction(node_id) as db:
            cur=db.execute('''UPDATE remote_node_control SET last_error=?
                              WHERE node_id=? AND revision=? AND command_id=? AND applied_revision<>revision''',
                           (str(error)[:500],node_id,command['revision'],command['command_id']))
        current=self.status(node_id)
        return {'queued':bool(current['pending']),'executed':False,'control':current,'result':None,
                'delivery_state':reason if cur.rowcount else 'superseded'}

    def config_running(self,node_id:str,default:bool=True)->bool:
        control=self.status(node_id)
        if not control['persisted']:return default
        # Once ordered control is used, config has a stopped fallback. Only a
        # durable Agent command receipt may grant running intent. Keep this bit
        # stable after acknowledgement: toggling it would create an unnecessary
        # config revision/restart after a successfully staged Start. A rebuilt
        # Agent without receipts stays stopped until explicit recovery/Start.
        return False

    @installation_operation
    def deliver(self,node_id:str,*,expected_command_id:str|None=None)->dict:
        command=self.status(node_id)
        if expected_command_id is not None and command['command_id']!=expected_command_id:
            return {'queued':bool(command['pending']),'executed':False,'delivery_state':'superseded',
                    'control':command,'result':None}
        if not command['persisted']:
            return {'queued':False,'executed':False,'delivery_state':'idle','control':command,'result':None}
        if not command['pending']:
            return {'queued':False,'executed':False,'delivery_state':'acknowledged','control':command,'result':None,'already_applied':True}
        agent_id=self.registry.installations.current(node_id)['agent_id']
        body={'nodeId':agent_id,'revision':command['revision'],'commandId':command['command_id'],'action':command['action']}
        try:
            doc,ms=self.registry._request(node_id,'/node/api/v1/control','POST',body,12.0)
            expected='stopped' if command['action']=='stop' else 'running'
            if (not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE' or doc.get('node_id')!=agent_id
                or type(doc.get('revision')) is not int or doc['revision']!=command['revision']
                or doc.get('commandId')!=command['command_id'] or doc.get('action')!=command['action']
                or doc.get('applied') is not True or not isinstance(doc.get('engine'),dict)
                or doc['engine'].get('state')!=expected):
                raise PolicyError('Node did not acknowledge the exact run command and resulting state')
        except (PolicyError,OSError,ValueError) as exc:
            return self.defer(node_id,command,str(exc),'pending')
        with self.registry._node_transaction(node_id) as db:
            cur=db.execute('''UPDATE remote_node_control SET applied_revision=?,applied_at=?,last_error=''
                              WHERE node_id=? AND revision=? AND command_id=?''',
                           (command['revision'],time.time(),node_id,command['revision'],command['command_id']))
            # An acknowledgement of an older in-flight command cannot make the
            # latest command appear applied, or replace its health snapshot.
            if cur.rowcount:
                row=db.execute('SELECT last_health FROM remote_nodes WHERE id=?',(node_id,)).fetchone()
                if row:
                    try:health=json.loads(row['last_health'])
                    except (ValueError,TypeError):health={}
                    if not isinstance(health,dict):health={}
                    health['core']=doc['engine'];health['run_control']=doc.get('run_control',{})
                    db.execute('UPDATE remote_nodes SET last_health=? WHERE id=?',(json.dumps(health),node_id))
        current=self.status(node_id)
        accepted=bool(cur.rowcount and current['command_id']==command['command_id'] and not current['pending'])
        return {'queued':bool(current['pending']),'executed':accepted,'control':current,'latency_ms':ms,
                'delivery_state':'executed' if accepted else 'superseded','result':doc if accepted else None}

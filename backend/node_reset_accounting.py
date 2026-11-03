"""Crash-recoverable, exactly-once Hub accounting for Node traffic resets.

Do not replay an Agent's cached *pre-reset* counters as a fresh observation.
A PREPARED Hub receipt is committed before the remote mutation; its APPLIED
transition, any missed usage delta and local reset are one SQLite transaction.
"""
import json
import time
from dark_policy import PolicyError

MAX_INT=(1<<63)-1


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS remote_node_reset_receipts(
        node_id TEXT NOT NULL, reset_id TEXT NOT NULL, client_id TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('prepared','applied')),
        pre_raw_up INTEGER NOT NULL, pre_raw_down INTEGER NOT NULL,
        pre_seq INTEGER NOT NULL,
        response_up INTEGER NOT NULL DEFAULT 0, response_down INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, applied_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(node_id,reset_id))""")


def reset_client_traffic(registry, client_id: str, reset_id: str) -> dict:
    if not isinstance(reset_id,str) or not 8<=len(reset_id)<=128:
        raise PolicyError('Invalid remote reset ID')
    if not isinstance(client_id,str) or not client_id:
        raise PolicyError('Invalid remote client identity')
    with registry.store.transaction() as db:
        ensure_schema(db)
        meta=db.execute("SELECT inbounds FROM managed_clients WHERE email=? AND state!='deleted'",
                        (client_id,)).fetchone()
        if not meta:return {'nodes':0,'reset':True}
        inbound_ids=sorted({int(x) for x in json.loads(meta['inbounds']) if int(x)>0})
        if not inbound_ids:return {'nodes':0,'reset':True}
        marks=','.join('?' for _ in inbound_ids)
        rows=db.execute(
            'SELECT DISTINCT r.node_id,n.enabled,n.maintenance '
            'FROM remote_node_inbounds r JOIN remote_nodes n ON n.id=r.node_id '
            'WHERE r.remote_inbound_id>0 AND r.local_inbound_id IN ('+marks+') ORDER BY r.node_id',
            tuple(inbound_ids)).fetchall()
        # A temporarily disabled Node still holds customer state. Only explicit
        # retirement (maintenance=1 + disabled) may exclude a former target.
        if any(bool(r['enabled']) == bool(r['maintenance']) for r in rows):
            # Both disabled-unretired and enabled-in-maintenance are unsafe.
            # A maintenance flag alone never proves traffic has stopped.
            raise PolicyError('Remote reset target has inconsistent retirement/maintenance state')
        node_ids=[r['node_id'] for r in rows if bool(r['enabled']) and not bool(r['maintenance'])]
    items=[]
    for node_id in node_ids:
        fresh_intent=False
        with registry.installations.operation(node_id):
            with registry.store.lock:
                prior=registry.store.db.execute(
                    'SELECT * FROM remote_node_reset_receipts WHERE node_id=? AND reset_id=?',
                    (node_id,reset_id)).fetchone()
            if prior is not None:
                if prior['client_id']!=client_id:
                    raise PolicyError('Node reset identity/idempotency key conflict')
                if prior['phase']=='applied':
                    items.append({'node_id':node_id,'cached':True,'accounted_once':True})
                    continue
            else:
                # Persist the most recent Hub-metered Node baseline BEFORE the
                # remote mutation. The Agent's final pre-reset receipt supplies
                # any unmetered delta since this saved checkpoint.
                with registry._node_transaction(node_id) as db:
                    existing=db.execute(
                        'SELECT * FROM remote_node_reset_receipts WHERE node_id=? AND reset_id=?',
                        (node_id,reset_id)).fetchone()
                    if existing:
                        if existing['client_id']!=client_id:
                            raise PolicyError('Node reset identity/idempotency key conflict')
                    else:
                        baseline=db.execute(
                            'SELECT raw_up,raw_down,seq,initialized FROM remote_node_client_usage '
                            'WHERE node_id=? AND client_id=?',(node_id,client_id)).fetchone()
                        if not baseline or not baseline['initialized']:
                            raise PolicyError('Node reset lacks a durable metered pre-reset baseline')
                        fresh_intent=True
                        db.execute('INSERT INTO remote_node_reset_receipts '
                                   '(node_id,reset_id,client_id,phase,pre_raw_up,pre_raw_down,pre_seq,created_at) '
                                   "VALUES(?,?,?,'prepared',?,?,?,?)",
                                   (node_id,reset_id,client_id,baseline['raw_up'],baseline['raw_down'],
                                    baseline['seq'],time.time()))
            # Same idempotency ID for all retries; cached Agent response safe
            # because the Hub has committed the initial PREPARED baseline.
            doc,latency=registry._request(node_id,'/node/api/mirrors/traffic/reset','POST',
                {'sourceEmail':client_id,'resetId':reset_id},12.0)
            if (not isinstance(doc,dict) or doc.get('sourceEmail')!=client_id or
                type(doc.get('up')) is not int or type(doc.get('down')) is not int or
                doc['up']<0 or doc['down']<0 or doc['up']+doc['down']>MAX_INT):
                raise PolicyError('Invalid durable Node reset receipt')
            if doc.get('cached') is True and fresh_intent:
                # An old Agent reset predating the Hub receipt cannot be replayed.
                raise PolicyError('Cached Node reset predates durable Hub intent; manual reconciliation required')
            with registry._node_transaction(node_id) as db:
                receipt=db.execute(
                    'SELECT * FROM remote_node_reset_receipts WHERE node_id=? AND reset_id=?',
                    (node_id,reset_id)).fetchone()
                if not receipt or receipt['client_id']!=client_id:
                    raise PolicyError('Node reset intent disappeared or changed')
                if receipt['phase']=='applied':
                    items.append({'node_id':node_id,'cached':True,'accounted_once':True})
                    continue
                state=db.execute(
                    'SELECT raw_up,raw_down,current_up,current_down,seq,initialized '
                    'FROM remote_node_client_usage WHERE node_id=? AND client_id=?',
                    (node_id,client_id)).fetchone()
                if not state or not state['initialized']:
                    raise PolicyError('Node reset accounting baseline disappeared')
                if state['seq']!=receipt['pre_seq']:
                    raise PolicyError('Concurrent metered Node event during remote reset; manual review required')
                base_up=max(state['raw_up'],receipt['pre_raw_up'])
                base_down=max(state['raw_down'],receipt['pre_raw_down'])
                if doc['up']<base_up or doc['down']<base_down:
                    raise PolicyError('Node reset receipt lower than metered baseline; manual review required')
                du=doc['up']-base_up;dd=doc['down']-base_down
                if du or dd:
                    user=db.execute('SELECT owner FROM clients WHERE id=?',(client_id,)).fetchone()
                    if not user:raise PolicyError('Cannot charge reset delta for unknown client')
                    owner=db.execute('SELECT period FROM owners WHERE id=?',(user['owner'],)).fetchone()
                    if not owner:raise PolicyError('Cannot charge reset delta for unknown owner')
                    event_id=registry._usage_event_id(node_id,client_id,int(state['seq'])+1)
                    db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',
                        (event_id,user['owner'],client_id,owner['period'],du,dd,time.time()))
                db.execute('UPDATE remote_node_client_usage SET raw_up=0,raw_down=0,'
                           'current_up=0,current_down=0,seq=?,initialized=1,last_seen=? '
                           'WHERE node_id=? AND client_id=?',
                           (int(state['seq'])+int(bool(du or dd)),time.time(),node_id,client_id))
                registry._recompute_client_usage(db,client_id)
                db.execute('UPDATE remote_node_reset_receipts SET phase=?,response_up=?,response_down=?,applied_at=? '
                           'WHERE node_id=? AND reset_id=? AND phase=?',
                           ('applied',doc['up'],doc['down'],time.time(),node_id,reset_id,'prepared'))
            items.append({'node_id':node_id,'latency_ms':latency,'cached':bool(doc.get('cached')),
                          'accounted_once':True})
    return {'nodes':len(items),'items':items,'reset':True}

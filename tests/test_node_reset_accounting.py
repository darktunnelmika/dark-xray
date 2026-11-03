import contextlib
import hashlib
import json
import sqlite3
import threading
import time
import unittest

from node_reset_accounting import reset_client_traffic
from dark_policy import PolicyError


class Store:
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.executescript("""
        CREATE TABLE remote_nodes(id TEXT PRIMARY KEY,enabled INT,maintenance INT);
        CREATE TABLE remote_node_inbounds(node_id TEXT,local_inbound_id INT,remote_inbound_id INT);
        CREATE TABLE managed_clients(email TEXT PRIMARY KEY,inbounds TEXT,state TEXT);
        CREATE TABLE remote_node_client_usage(node_id TEXT,client_id TEXT,raw_up INT,raw_down INT,
        current_up INT,current_down INT,seq INT,initialized INT,last_seen REAL,
        PRIMARY KEY(node_id,client_id));
        CREATE TABLE clients(id TEXT PRIMARY KEY,owner TEXT,used_bytes INT);
        CREATE TABLE owners(id TEXT PRIMARY KEY,period INT);
        CREATE TABLE traffic_ledger(event_id TEXT PRIMARY KEY,owner TEXT,client_id TEXT,
        period INT,up_bytes INT,down_bytes INT,observed_at REAL);
        INSERT INTO owners VALUES('owner',0);
        INSERT INTO clients VALUES('alice','owner',250);
        INSERT INTO managed_clients VALUES('alice','[1]','applied');
        INSERT INTO remote_nodes VALUES('de',1,0);
        INSERT INTO remote_node_inbounds VALUES('de',1,1);
        INSERT INTO remote_node_client_usage VALUES('de','alice',100,150,100,150,5,1,0);
        """)
    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.commit()
            except:
                self.db.rollback()
                raise


class Agent:
    def __init__(self, up=120,down=200):
        self.current=(up,down)
        self.receipts={}
        self.calls=0
        self.drop_first=False

    def request(self,node_id,path,method,body,timeout):
        self.calls+=1
        rid=body['resetId']
        cached=rid in self.receipts
        if not cached:
            self.receipts[rid]=self.current
            self.current=(0,0)
        u,d=self.receipts[rid]
        if self.drop_first and self.calls==1:
            raise TimeoutError('simulated response lost after remote mutation')
        return {'sourceEmail':body['sourceEmail'],'up':u,'down':d,'cached':cached},3


class Registry:
    def __init__(self,agent=None):
        self.store=Store()
        self.installations=type('Installations',(),{'operation':lambda _,node:contextlib.nullcontext()})()
        self.agent=agent or Agent()
        self.synced=0
    def sync_traffic(self,node):
        self.synced+=1
        return {}
    def _node_transaction(self,node):
        return self.store.transaction()
    def _request(self,*args):
        return self.agent.request(*args)
    def _usage_event_id(self,node,client,seq):
        return 'node:'+hashlib.sha256((node+'\0'+client).encode()).hexdigest()[:24]+':'+str(seq)
    @staticmethod
    def _recompute_client_usage(db,client):
        cur=db.execute('SELECT sum(current_up+current_down) FROM remote_node_client_usage WHERE client_id=?',(client,)).fetchone()[0]
        db.execute('UPDATE clients SET used_bytes=? WHERE id=?',(cur,client))


class TestResetOnce(unittest.TestCase):
    def test_receipt_is_exactly_once(self):
        r=Registry()
        a=reset_client_traffic(r,'alice','idempotent-1')
        self.assertTrue(a['reset'])
        self.assertEqual(r.store.db.execute('select up_bytes,down_bytes from traffic_ledger').fetchone()[:],(20,50))
        self.assertEqual(r.store.db.execute('select raw_up,raw_down,current_up,current_down from remote_node_client_usage').fetchone()[:],(0,0,0,0))
        self.assertEqual(r.store.db.execute('select used_bytes from clients').fetchone()[0],0)
        reset_client_traffic(r,'alice','idempotent-1')
        self.assertEqual(r.agent.calls,1)
        self.assertEqual(r.store.db.execute('select count(*) from traffic_ledger').fetchone()[0],1)

    def test_lost_response_replays_cached_agent_receipt_once(self):
        a=Agent()
        a.drop_first=True
        r=Registry(a)
        with self.assertRaises(TimeoutError):
            reset_client_traffic(r,'alice','idempotent-2')
        self.assertEqual(r.store.db.execute('select phase from remote_node_reset_receipts').fetchone()[0],'prepared')
        reset_client_traffic(r,'alice','idempotent-2')
        self.assertEqual(r.agent.calls,2)
        self.assertEqual(r.store.db.execute('select count(*) from traffic_ledger').fetchone()[0],1)
        self.assertEqual(r.store.db.execute('select up_bytes+down_bytes from traffic_ledger').fetchone()[0],70)
        reset_client_traffic(r,'alice','idempotent-2')
        self.assertEqual(r.agent.calls,2)

    def test_disabled_retired_node_is_excluded(self):
        r=Registry()
        r.store.db.execute("insert into remote_nodes values('nl',0,1)")
        r.store.db.execute("insert into remote_node_inbounds values('nl',1,1)")
        r.store.db.commit()
        reset_client_traffic(r,'alice','idempotent-3')
        self.assertEqual(r.agent.calls,1)

    def test_unretired_disabled_node_fails_closed(self):
        r=Registry()
        r.store.db.execute("insert into remote_nodes values('nl',0,0)")
        r.store.db.execute("insert into remote_node_inbounds values('nl',1,1)")
        r.store.db.commit()
        with self.assertRaises(PolicyError):
            reset_client_traffic(r,'alice','idempotent-4')
        self.assertEqual(r.agent.calls,0)

    def test_missing_baseline_fails_closed(self):
        r=Registry()
        r.store.db.execute("delete from remote_node_client_usage")
        r.store.db.commit()
        with self.assertRaises(PolicyError):
            reset_client_traffic(r,'alice','idempotent-5')
        self.assertEqual(r.agent.calls,0)

    def test_cached_reset_without_prior_hub_intent_fails_closed(self):
        agent=Agent()
        agent.receipts['idempotent-7']=(120,200)
        agent.current=(0,0)
        r=Registry(agent)
        with self.assertRaises(PolicyError):
            reset_client_traffic(r,'alice','idempotent-7')
        self.assertEqual(r.store.db.execute('select count(*) from traffic_ledger').fetchone()[0],0)
        self.assertEqual(r.store.db.execute('select phase from remote_node_reset_receipts').fetchone()[0],'prepared')

    def test_sequence_concurrency_fails_closed(self):
        r=Registry()
        original=r._request
        def bump(*a):
            response=original(*a)
            r.store.db.execute("update remote_node_client_usage set seq=seq+1")
            r.store.db.commit()
            return response
        r._request=bump
        with self.assertRaises(PolicyError):
            reset_client_traffic(r,'alice','idempotent-6')
        self.assertEqual(r.store.db.execute('select phase from remote_node_reset_receipts').fetchone()[0],'prepared')


if __name__=='__main__':
    unittest.main(verbosity=2)

[executed on device: dark-tr-01 (6a9f9955-05e2-4f3b-b6eb-ebd7dcbfe215)]

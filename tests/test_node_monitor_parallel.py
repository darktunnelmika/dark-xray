"""Regression: a slow node must not consume another node's accounting lease.

Only disposable registries and fake I/O are used. No production service/tunnel.
"""
import threading
import time
from contextlib import contextmanager

import pytest
from auth import Auth
from dark_policy import Store, PolicyError
from nodes import NodeRegistry
import nodes as nodes_mod


@contextmanager
def registry_fixture(tmp_path,monkeypatch,names=('a-slow','z-fast')):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'db');auth=Auth(store,tmp_path/'key');registry=NodeRegistry(store,auth.cipher)
    for name in names:registry.put(name,name,'https://'+name+'.example','dkn_'+'A'*60,True)
    monkeypatch.setattr(registry,'probe',lambda *a,**kw:{})
    monkeypatch.setattr(registry,'sync_traffic',lambda _:{'charged_bytes':0})
    try:yield registry
    finally:registry.close();store.close()


def eventually(predicate,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.005)
    assert predicate()


def test_slow_node_does_not_delay_healthy_node_lease(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch) as r:
        entered=threading.Event();release=threading.Event();grants=[]
        def probe(node_id,**kw):
            if node_id=='a-slow':entered.set();assert release.wait(3)
            return {}
        monkeypatch.setattr(r,'probe',probe)
        def renew(node_id,traffic):grants.append(node_id);return {'remaining_seconds':59}
        r.start(interval=.02,initial_delay=0,lease_callback=renew)
        try:
            assert entered.wait(1)
            eventually(lambda:grants.count('z-fast')>=3)
            assert 'a-slow' not in grants
        finally:release.set()


def test_multiple_blocked_nodes_do_not_form_a_fleet_barrier(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('a-slow','b-slow','z-fast')) as r:
        release=threading.Event();entered=[];grants=[]
        def probe(node_id,**kw):
            if node_id!='z-fast':entered.append(node_id);assert release.wait(3)
            return {}
        monkeypatch.setattr(r,'probe',probe)
        r.start(interval=.02,initial_delay=0,lease_callback=lambda n,t:grants.append(n))
        try:
            eventually(lambda:len(entered)==2 and grants.count('z-fast')>=3)
            assert set(grants)=={'z-fast'}
        finally:release.set()


def test_slow_cycle_never_overlaps_itself_or_builds_a_queue(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        active=[0];peak=[0];calls=[]
        def probe(node_id,**kw):
            active[0]+=1;peak[0]=max(peak[0],active[0]);calls.append(time.monotonic())
            time.sleep(.035);active[0]-=1
            return {}
        monkeypatch.setattr(r,'probe',probe)
        r.start(interval=.01,initial_delay=0)
        eventually(lambda:len(calls)>=4)
        r.close();count=len(calls);time.sleep(.04)
        assert peak[0]==1 and len(calls)==count and r.thread is None


def test_policy_failure_withholds_grant_but_worker_recovers(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        calls=[];grants=[]
        def traffic(node_id):
            calls.append(node_id)
            if len(calls)==1:raise RuntimeError('ledger unavailable')
            return {'charged_bytes':0}
        monkeypatch.setattr(r,'sync_traffic',traffic)
        r.start(interval=.15,initial_delay=0,lease_callback=lambda n,t:grants.append(n))
        eventually(lambda:bool(r.list()[0]['monitor'].get('last_error')))
        monitor=r.list()[0]['monitor']
        assert monitor['failed_stage']=='traffic' and 'ledger unavailable' in monitor['last_error']
        assert not grants
        r._request_ok('n',1)
        assert r.list()[0]['monitor']['last_error']==monitor['last_error']
        eventually(lambda:bool(grants))
        eventually(lambda:r.list()[0]['monitor'].get('last_error')=='')


def test_close_during_network_wait_never_grants_after_return(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        entered=threading.Event();release=threading.Event();closed=threading.Event();grants=[]
        def probe(*a,**kw):entered.set();assert release.wait(3);return {}
        monkeypatch.setattr(r,'probe',probe)
        r.start(interval=.02,initial_delay=0,lease_callback=lambda n,t:grants.append(n))
        assert entered.wait(1)
        closer=threading.Thread(target=lambda:(r.close(),closed.set()));closer.start()
        try:
            eventually(lambda:r.stop.is_set());old=r.thread
            r.start(interval=.02,initial_delay=0)
            assert r.thread is old
        finally:release.set();closer.join(2)
        assert closed.is_set() and r.thread is None and not grants


def test_disabling_node_cancels_inflight_cycle_before_lease(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        entered=threading.Event();release=threading.Event();grants=[]
        def probe(*a,**kw):entered.set();assert release.wait(3);return {}
        monkeypatch.setattr(r,'probe',probe)
        r.start(interval=.02,initial_delay=0,lease_callback=lambda n,t:grants.append(n))
        try:
            assert entered.wait(1)
            with r.store.transaction() as db:db.execute('UPDATE remote_nodes SET enabled=0 WHERE id=?',('n',))
        finally:release.set()
        time.sleep(.08);r.close();assert not grants


def test_enabled_nodes_are_discovered_after_monitor_start(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        with r.store.transaction() as db:db.execute('UPDATE remote_nodes SET enabled=0')
        grants=[];r.start(interval=.02,initial_delay=0,lease_callback=lambda n,t:grants.append(n))
        time.sleep(.04);assert not grants
        with r.store.transaction() as db:db.execute('UPDATE remote_nodes SET enabled=1')
        eventually(lambda:bool(grants))
        r.put('new','New','https://new.example','dkn_'+'B'*60,True)
        eventually(lambda:'new' in grants)


def test_lease_is_after_durable_traffic_policy_and_post_apply(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        order=[];done=threading.Event()
        monkeypatch.setattr(r,'probe',lambda *a,**kw:order.append('probe') or {})
        monkeypatch.setattr(r,'sync_traffic',lambda n:order.append('traffic') or {'charged_bytes':1})
        monkeypatch.setattr(r,'sync_mirrors',lambda *a:order.append('apply'))
        def renew(n,t):order.append('lease');done.set();return {'remaining_seconds':59}
        r.start(interval=60,initial_delay=0,sync_provider=lambda n:[],
                traffic_callback=lambda n,t:order.append('policy'),lease_callback=renew)
        assert done.wait(1);r.close()
        assert order==['probe','traffic','policy','apply','traffic','policy','lease']


@pytest.mark.parametrize('interval,delay',[(0,0),(-1,0),(float('inf'),0),(float('nan'),0),(1,-1),(1,float('inf'))])
def test_nonfinite_or_invalid_cadence_rejected(tmp_path,monkeypatch,interval,delay):
    with registry_fixture(tmp_path,monkeypatch,()) as r:
        with pytest.raises(ValueError):r.start(interval=interval,initial_delay=delay)


def test_lease_monitor_caps_legacy_poll_interval(tmp_path,monkeypatch):
    with registry_fixture(tmp_path,monkeypatch,('n',)) as r:
        r.start(interval=60,initial_delay=0,lease_callback=lambda n,t:{'remaining_seconds':59})
        eventually(lambda:r.list()[0]['monitor'].get('last_success_at',0)>0)
        assert r.list()[0]['monitor']['cadence_seconds']==5.0

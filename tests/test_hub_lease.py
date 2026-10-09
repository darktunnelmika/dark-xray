"""Lease protocol, independent checkpoints and failsafe regression tests.

Fake Xray is used here; real packet tests live in test_hub_lease_real.py.
All databases and child processes are disposable, never installed services.
"""
from __future__ import annotations

import contextlib
import copy
import json
import shutil
import socket
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from core import Config, CoreEngine, CoreError
from dark_policy import Store, PolicyError
from node_runtime import HubLease, SystemdWatchdog
from node_agent import AgentToken, make_agent_app
from node_lease_sync import renew_accounting_lease
from test_node_hub_recovery import NODE, ORIGIN, TOKEN, ROOT, free_port, payload, post_state

STATE={'appliedRevision':1,'appliedHash':'a'*64,'lastError':''}


def ack(lease, state=STATE, control=0):
    return {'challenge':lease.challenge(),'revision':state['appliedRevision'],
            'hash':state['appliedHash'],'controlRevision':control}


def test_lease_expiry_duplicate_replay_and_durable_latch(tmp_path):
    clock=[100.0]
    store=Store(tmp_path/'db')
    try:
        lease=HubLease(store,NODE,clock=lambda:clock[0])
        assert lease.allowed and not lease.required
        body=ack(lease);assert lease.renew(body,STATE,0)['valid']
        assert lease.required and lease.deadline==160.0
        clock[0]=125
        assert lease.renew(body,STATE,0)['duplicate'] and lease.deadline==160.0
        restarted=HubLease(store,NODE,clock=lambda:clock[0])
        assert restarted.required and not restarted.allowed
        with pytest.raises(PolicyError):restarted.renew(body,STATE,0)
        clock[0]=160
        assert not lease.allowed and not lease.renew(body,STATE,0)['valid']
    finally:store.close()


@pytest.mark.parametrize('key,value',[('revision',2),('revision',True),('hash','b'*64),
                                    ('controlRevision',1),('controlRevision',False),('challenge','x')])
def test_stale_or_malformed_ack_never_arms(tmp_path,key,value):
    store=Store(tmp_path/'db')
    try:
        lease=HubLease(store,NODE,required=True)
        body=ack(lease);body[key]=value
        with pytest.raises(PolicyError):lease.renew(body,STATE,0)
        assert not lease.allowed
    finally:store.close()


def test_delayed_or_out_of_order_ack_does_not_extend(tmp_path):
    clock=[100.0];store=Store(tmp_path/'db')
    try:
        lease=HubLease(store,NODE,required=True,clock=lambda:clock[0])
        old=ack(lease);clock[0]=102;new=ack(lease)
        lease.renew(new,STATE,0)
        with pytest.raises(PolicyError):lease.renew(old,STATE,0)
        clock[0]=104;late=ack(lease);clock[0]=135
        with pytest.raises(PolicyError):lease.renew(late,STATE,0)
        assert lease.deadline==162
        for _ in range(100):lease.challenge()
        assert len(lease.pending)<=64
    finally:store.close()


@contextlib.contextmanager
def guarded_agent(root):
    root.mkdir(parents=True,exist_ok=True)
    fake=root/'fake-xray';shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);fake.chmod(0o755)
    cfg=Config(public_origin=ORIGIN,public_address='node.example.test',secure_cookie=True,
               xray_binary=str(fake),xray_assets=str(root),xray_api_port=free_port(),
               core_autostart=True,test_engine=True,hub_lease_required=True)
    store=Store(root/'node.sqlite3');engine=CoreEngine(cfg,store,root/'runtime')
    token=root/'token';token.write_text(TOKEN+'\n');token.chmod(0o600)
    app=make_agent_app(engine,store,AgentToken(token),NODE,background=False)
    clock=[100.0];app.state.runtime.hub_lease.clock=lambda:clock[0]
    try:
        with TestClient(app,base_url=ORIGIN,raise_server_exceptions=False) as client:
            client.headers['Authorization']='Bearer '+TOKEN
            yield SimpleNamespace(store=store,engine=engine,app=app,api=client,
                                  runtime=app.state.runtime,clock=clock)
    finally:engine.close();store.close()


def grant(f):
    response=f.api.get('/node/api/mirrors/traffic');assert response.status_code==200,response.text
    state=f.runtime.status()
    body={'challenge':response.json()['accountingLease'],'revision':state['appliedRevision'],
          'hash':state['appliedHash'],'controlRevision':f.runtime.command_status()['revision']}
    response=f.api.post('/node/api/v1/accounting/lease',json=body)
    assert response.status_code==200,response.text
    return response.json()


def test_fresh_node_stays_closed_until_metered_ack_and_recovers(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        body=payload(f.engine);post_state(f.api,body)
        assert not f.engine.running
        for _ in range(3):
            assert f.api.get('/node/api/health').status_code==200
            f.app.state.loop.tick()
        assert not f.engine.running and not f.runtime.hub_lease.allowed
        assert grant(f)['lease']['valid'] and f.engine.running
        mirror=f.runtime.mirror_for_source('alice')
        f.clock[0]+=61;f.app.state.lease_guard.tick()
        assert not f.engine.running and not f.engine.wants_running
        assert f.runtime.status()['appliedRevision']==1
        assert not f.runtime.control_status()['manual_stop']
        assert f.api.get('/node/api/health').status_code==200
        post_state(f.api,body);f.app.state.loop.tick();assert not f.engine.running
        grant(f);assert f.engine.running and f.runtime.mirror_for_source('alice')==mirror


def test_manual_stop_is_preserved_after_lease_recovery(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        assert f.api.post('/node/api/core/stop',json={}).status_code==200
        f.clock[0]+=61;f.app.state.lease_guard.tick();grant(f)
        assert not f.engine.running and f.runtime.control_status()['manual_stop']


def test_config_health_start_and_restart_cannot_bypass_expiry(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        body=payload(f.engine);post_state(f.api,body);grant(f)
        f.clock[0]+=61;f.app.state.lease_guard.tick()
        for action in ('start','restart'):
            assert f.api.post('/node/api/core/'+action,json={}).status_code==503
        post_state(f.api,body)
        assert not f.engine.running
        with pytest.raises(CoreError):f.engine.command('start')
        assert not f.engine.running


def test_expiry_stops_even_when_final_statistics_fail(tmp_path,monkeypatch):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        f.clock[0]+=61
        def broken(**kw):raise CoreError('injected stats outage',status=503)
        monkeypatch.setattr(f.engine,'collect_stats',broken)
        f.app.state.lease_guard.tick()
        assert not f.engine.running
        assert f.runtime.hub_lease.status()['trips']==1
        assert 'injected stats outage' in f.runtime.hub_lease.status()['last_error']


def test_stats_endpoint_failure_cannot_issue_ack_token(tmp_path,monkeypatch):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        def broken(**kw):raise CoreError('injected stats outage',status=503)
        monkeypatch.setattr(f.engine,'collect_stats',broken)
        response=f.api.get('/node/api/mirrors/traffic')
        assert response.status_code==503 and 'accountingLease' not in response.text


def test_unchanged_node_loop_persists_traffic_without_hub_poll(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        mirror=f.runtime.mirror_for_source('alice');pid=f.engine.process.pid
        def sample(up,down):
            Path('stats-fixture.json').write_text(json.dumps({'stat':[
                {'name':f'user>>>{mirror}>>>traffic>>>uplink','value':up},
                {'name':f'user>>>{mirror}>>>traffic>>>downlink','value':down}]}))
            f.engine.last_stats=0;f.app.state.loop.tick()
        sample(123,456);sample(123,456)
        row=f.store.db.execute('SELECT up,down FROM core_clients WHERE email=?',(mirror,)).fetchone()
        assert tuple(row)==(123,456)
        sample(223,656)
        row=f.store.db.execute('SELECT up,down FROM core_clients WHERE email=?',(mirror,)).fetchone()
        assert tuple(row)==(223,656) and f.engine.process.pid==pid
        assert f.runtime.hub_lease.deadline==160


def test_authentication_required_for_lease_and_health_does_not_grant(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine))
        response=f.api.post('/node/api/v1/accounting/lease',json={},headers={'Authorization':''})
        assert response.status_code==401
        assert f.api.get('/node/api/health').json()['hub_lease']['state']=='awaiting_hub'
        assert not f.runtime.hub_lease.allowed


def test_watchdog_socket_is_main_process_only(tmp_path,monkeypatch):
    path=str(tmp_path/'notify.sock')
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as receiver:
        receiver.bind(path);receiver.settimeout(1)
        monkeypatch.setenv('NOTIFY_SOCKET',path);monkeypatch.setenv('WATCHDOG_USEC','30000000')
        monkeypatch.delenv('WATCHDOG_PID',raising=False)
        watch=SystemdWatchdog();assert watch.enabled;watch.notify()
        assert receiver.recv(100)==b'WATCHDOG=1'
        monkeypatch.setenv('WATCHDOG_PID','999999999')
        assert not SystemdWatchdog().enabled


def test_guard_keeps_watchdog_alive_for_every_valid_lease_second(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        watcher=f.app.state.lease_guard.watchdog
        watcher.notify=Mock();watcher.trigger=Mock()
        f.app.state.lease_guard.tick()
        first=watcher.notify.call_count
        f.clock[0]=135;f.app.state.lease_guard.tick()
        assert watcher.notify.call_count>first and f.engine.running
        before=watcher.notify.call_count
        f.clock[0]=161;f.app.state.lease_guard.tick()
        assert not f.engine.running and watcher.notify.call_count>before
        watcher.trigger.assert_not_called()


def test_busy_engine_preserves_valid_grant_but_fences_on_actual_expiry(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        guard=f.app.state.lease_guard;watcher=guard.watchdog
        watcher.notify=Mock();watcher.trigger=Mock()
        entered=threading.Event();release=threading.Event()
        def busy():
            with f.engine.lock:entered.set();release.wait(3)
        thread=threading.Thread(target=busy);thread.start()
        try:
            assert entered.wait(1)
            guard.tick()
            count=watcher.notify.call_count
            assert count>=1 and f.engine.running
            assert guard.last_error=='engine_busy_with_valid_lease'
            f.clock[0]=135;guard.tick()
            assert watcher.notify.call_count>count and f.engine.running
            f.clock[0]=161;guard.tick()
            watcher.trigger.assert_called_once()
            assert guard.last_error=='lease_expired_engine_busy'
            assert f.runtime.hub_lease.deadline==160
        finally:release.set();thread.join(2)
        guard.tick();assert not f.engine.running


def test_transient_statistics_delay_does_not_revoke_valid_grant(tmp_path):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        pid=f.engine.process.pid;guard=f.app.state.lease_guard
        guard.watchdog.seconds=30;guard.watchdog.notify=Mock()
        f.engine.last_stats=time.monotonic()-20;f.engine.stats_error='temporary statistics timeout'
        guard.tick()
        assert guard.watchdog.notify.call_count==1 and f.engine.process.pid==pid
        assert f.runtime.hub_lease.deadline==160
        response=f.api.post('/node/api/v1/accounting/lease',json=ack(f.runtime.hub_lease,
            f.runtime.status(),f.runtime.command_status()['revision']))
        assert response.status_code==409
        f.clock[0]=161;guard.tick();assert not f.engine.running


def test_healthy_lease_renewal_does_not_reapply_running_core(tmp_path,monkeypatch):
    with guarded_agent(tmp_path/'node') as f:
        post_state(f.api,payload(f.engine));grant(f)
        pid=f.engine.process.pid;command=Mock(side_effect=AssertionError('unnecessary core apply'))
        monkeypatch.setattr(f.engine,'command',command)
        grant(f)
        command.assert_not_called();assert f.engine.process.pid==pid


def test_quota_metadata_only_revision_reuses_exact_running_validation(tmp_path,monkeypatch):
    with guarded_agent(tmp_path/'node') as f:
        body=payload(f.engine);post_state(f.api,body);grant(f)
        pid=f.engine.process.pid;body=copy.deepcopy(body)
        raw=body['assignments'][0]['clients'][0]['client']
        raw['totalGB']=int(raw.get('totalGB',0))+1024*1024
        monkeypatch.setattr(CoreEngine,'validate',Mock(side_effect=AssertionError('duplicate validation process')))
        post_state(f.api,body,revision=2)
        assert f.engine.process.pid==pid and f.runtime.status()['appliedRevision']==2


def test_offline_other_node_delete_error_does_not_poison_healthy_node_lease():
    class Rows:
        def __init__(self, rows): self.rows=rows
        def fetchall(self): return self.rows
    row={'email':'alice','state':'error','op':'delete','error':'Node connection failed: ConnectionRefusedError'}
    store=SimpleNamespace(lock=contextlib.nullcontext(),
        db=SimpleNamespace(execute=lambda *_args,**_kw:Rows([row])))
    manager=SimpleNamespace(store=store,tick=Mock(),last_error='')
    engine=SimpleNamespace(collect_stats=Mock(),stats_error='',config=SimpleNamespace(writes_enabled=True))
    nodes=SimpleNamespace(
        installations=SimpleNamespace(operation=lambda _node:contextlib.nullcontext()),
        _allowed_traffic_clients=lambda _node:{'alice'},
        sync_desired_state=Mock(),
        desired_state=Mock(return_value={'pending':False,'last_error':'','revision':7}),
        commands=SimpleNamespace(status=Mock(return_value={'revision':0})),
        _request=Mock(return_value=({'lease':{'valid':True,'remaining_seconds':60}},1)),
    )
    desired=lambda _node:{'revision':7,'hash':'a'*64}
    result=renew_accounting_lease(nodes,manager,engine,'healthy',
        {'accounting_lease':'b'*64},desired,lambda _node:[])
    assert result['valid'] is True
    nodes._request.assert_called_once()

    row.update(state='error',op='upsert',error='policy write failed')
    with pytest.raises(PolicyError,match='unresolved'):
        renew_accounting_lease(nodes,manager,engine,'healthy',
            {'accounting_lease':'c'*64},desired,lambda _node:[])


def test_hub_failure_or_ignored_identity_never_renews():
    nodes=SimpleNamespace(installations=SimpleNamespace(operation=lambda _:contextlib.nullcontext()),_request=Mock())
    manager=SimpleNamespace(tick=Mock(side_effect=RuntimeError('ledger unavailable')))
    with pytest.raises(PolicyError,match='withheld'):
        renew_accounting_lease(nodes,manager,SimpleNamespace(collect_stats=Mock()),'n',{'accounting_lease':'a'*64},None,None)
    with pytest.raises(PolicyError,match='unmanaged'):
        renew_accounting_lease(nodes,manager,None,'n',{'accounting_lease':'a'*64,'ignored_clients':1},None,None)
    nodes._request.assert_not_called()


def test_new_installer_and_watchdog_are_part_of_release():
    provision=(ROOT/'tools/provision_node.py').read_text()
    assert "'hub_lease_required':True" in provision
    for name in ('provision_node.py','update_node.py'):
        assert "'node_runtime.py'" in (ROOT/'tools'/name).read_text()
    unit=(ROOT/'deploy/dark-xray-node.service').read_text()
    assert 'WatchdogSec=30' in unit and 'WatchdogSignal=SIGKILL' in unit
    assert 'KillMode=control-group' in unit and 'NotifyAccess=main' in unit


def test_absent_new_mirror_gets_zero_baseline_before_its_first_bytes(tmp_path):
    from nodes import NodeRegistry
    store=Store(tmp_path/'hub.sqlite3')
    try:
        with store.transaction() as db:
            db.execute("INSERT INTO owners(id) VALUES('owner')")
            db.execute("INSERT INTO clients(id,owner) VALUES('alice','owner')")
            db.execute('CREATE TABLE managed_clients(email TEXT PRIMARY KEY,last_up INTEGER,last_down INTEGER)')
            db.execute("INSERT INTO managed_clients VALUES('alice',0,0)")
            db.execute('''CREATE TABLE remote_node_client_usage(node_id TEXT,client_id TEXT,
                raw_up INTEGER DEFAULT 0,raw_down INTEGER DEFAULT 0,current_up INTEGER DEFAULT 0,
                current_down INTEGER DEFAULT 0,seq INTEGER DEFAULT 0,initialized INTEGER DEFAULT 0,
                last_seen REAL DEFAULT 0,PRIMARY KEY(node_id,client_id))''')
        reg=NodeRegistry.__new__(NodeRegistry);reg.store=store
        reg._allowed_traffic_clients=lambda _: {'alice'}
        reg._node_transaction=lambda _:store.transaction()
        pre=reg.apply_traffic_snapshot('node',[],initialize_absent=True)
        assert pre['seeded_zero_baselines']==1
        post=reg.apply_traffic_snapshot('node',[{'sourceEmail':'alice','up':123,'down':456}],initialize_absent=True)
        assert post['charged_bytes']==579 and post['baselined']==0
        again=reg.apply_traffic_snapshot('node',[{'sourceEmail':'alice','up':123,'down':456}],initialize_absent=True)
        assert again['charged_bytes']==0
    finally:store.close()

"""P1 regression: fully metered, bounded renewal of stale accounting challenges.

Disposable fake Node and quota engine; never touches a live service or DB.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import node_lease_sync as leases
from dark_policy import PolicyError
from nodes import NodeHTTPError


class EmptyRows:
    def fetchall(self):
        return []


def scenario(monkeypatch, *, initial_policy_seconds=0, new_bytes=0, post_error=None):
    time_value=[100.0]
    monkeypatch.setattr(leases,'time',SimpleNamespace(monotonic=lambda:time_value[0]))
    digest='a'*64
    current={'revision':7,'hash':digest,'applied_revision':7,
             'applied_hash':digest,'pending':False,'last_error':''}
    requests=[]
    captures=[]
    syncs=[]
    manager_calls=[]
    expired=[False]
    def tick(*,suppress=False):
        manager_calls.append(suppress)
        if len(manager_calls)==1:
            time_value[0]+=initial_policy_seconds
    manager=SimpleNamespace(
        last_error='',
        store=SimpleNamespace(lock=contextlib.nullcontext(),
                              db=SimpleNamespace(execute=lambda *a:EmptyRows())),
        tick=tick)
    engine=SimpleNamespace(collect_stats=Mock(),stats_error='',
                           config=SimpleNamespace(writes_enabled=True))

    def request(node,path,method,body,timeout):
        requests.append((path,method,body))
        if post_error and not expired[0]:
            expired[0]=True
            raise NodeHTTPError(post_error[0],post_error[1])
        return {'lease':{'valid':True,'remaining_seconds':60}},1

    def capture(node):
        token=('b' if not syncs else 'c')*64
        snapshot={'accounting_lease':token,
                  'accounting_challenge_requested_at':time_value[0],
                  'charged_bytes':new_bytes,'ignored_clients':0,
                  'restore_usage':{'clients':0}}
        syncs.append(snapshot)
        captures.append(time_value[0])
        return snapshot

    nodes=SimpleNamespace(
        installations=SimpleNamespace(operation=lambda node:contextlib.nullcontext()),
        _allowed_traffic_clients=lambda node:set(),
        desired_state=lambda node,**kw:dict(current),
        commands=SimpleNamespace(status=lambda node:{'revision':3}),
        sync_desired_state=lambda node,state,**kw:current.update(
            applied_revision=state['revision'],applied_hash=state['hash'],pending=False),
        sync_traffic=capture,
        _request=request)
    desired=lambda node:{'revision':7,'hash':digest}
    first={'accounting_lease':'a'*64,
           'accounting_challenge_requested_at':time_value[0],
           'charged_bytes':0,'ignored_clients':0,'restore_usage':{'clients':0}}
    return SimpleNamespace(clock=time_value,nodes=nodes,manager=manager,engine=engine,
                           first=first,desired=desired,requests=requests,syncs=syncs,
                           manager_calls=manager_calls)


def grant(s):
    return leases.renew_accounting_lease(
        s.nodes,s.manager,s.engine,'node-ci',s.first,s.desired,lambda node:[])


def test_long_policy_invalidates_old_token_and_uses_new_meted_snapshot(monkeypatch,caplog):
    s=scenario(monkeypatch,initial_policy_seconds=24)
    doc=grant(s)
    assert doc['valid'] and len(s.syncs)==1
    assert len(s.requests)==1
    assert s.requests[0][2]['challenge']=='b'*64
    assert s.requests[0][2]['revision']==7 and s.requests[0][2]['controlRevision']==3
    assert s.manager_calls==[False]
    assert 'reason=slow-pregrant' in caplog.text
    assert 'a'*64 not in caplog.text and 'b'*64 not in caplog.text


def test_stale_token_before_ack_rechecks_policy_when_new_bytes_are_meted(monkeypatch):
    s=scenario(monkeypatch,initial_policy_seconds=21,new_bytes=42)
    doc=grant(s)
    assert doc['valid'] and len(s.syncs)==1
    assert s.manager_calls==[False,False]
    assert s.engine.collect_stats.call_count==2
    assert s.requests[0][2]['challenge']=='b'*64


def test_new_snapshot_with_unmanaged_traffic_must_not_grant(monkeypatch):
    s=scenario(monkeypatch,initial_policy_seconds=21)
    original=s.nodes.sync_traffic
    def insecure(node):
        d=original(node);d['ignored_clients']=1
        return d
    s.nodes.sync_traffic=insecure
    with pytest.raises(PolicyError,match='unmanaged traffic identities'):
        grant(s)
    assert not s.requests


def test_retry_only_node_expired_stale_unknown_challenge_409(monkeypatch,caplog):
    s=scenario(monkeypatch,initial_policy_seconds=1,
               post_error=(409,'Accounting lease challenge is expired, stale or unknown'))
    assert grant(s)['valid']
    assert len(s.syncs)==1 and len(s.requests)==2
    assert s.requests[0][2]['challenge']=='a'*64
    assert s.requests[1][2]['challenge']=='b'*64
    assert 'node-rejected-old-challenge' in caplog.text
    assert 'a'*64 not in caplog.text and 'b'*64 not in caplog.text


@pytest.mark.parametrize('error',[(401,'Invalid token'),(409,'Control revision changed'),
                                  (409,'Wrong installation'),(403,'Forbidden')])
def test_auth_control_and_other_errors_never_trigger_retry(monkeypatch,error):
    s=scenario(monkeypatch,post_error=error)
    with pytest.raises(PolicyError,match='Accounting lease withheld'):
        grant(s)
    assert not s.syncs and len(s.requests)==1


def test_bounded_refresh_refuses_unbounded_slow_policy_without_grant(monkeypatch):
    s=scenario(monkeypatch,initial_policy_seconds=23,new_bytes=2)
    original=s.manager.tick
    def always_slow(*,suppress):
        original(suppress=suppress)
        if len(s.manager_calls)>1:
            s.clock[0]+=23
    s.manager.tick=always_slow
    with pytest.raises(PolicyError,match='remained too old after bounded refresh'):
        grant(s)
    assert len(s.syncs)==2 and not s.requests
    assert s.manager_calls==[False,False,False]


def test_refreshed_snapshot_policy_error_fail_closed(monkeypatch):
    s=scenario(monkeypatch,initial_policy_seconds=23,new_bytes=42)
    original=s.manager.tick
    def fails_on_new_bytes(*,suppress):
        original(suppress=suppress)
        if len(s.manager_calls)==2:
            s.manager.last_error='synthetic policy failure'
    s.manager.tick=fails_on_new_bytes
    with pytest.raises(PolicyError,match='not healthy and writable'):
        grant(s)
    assert not s.requests


def test_node_snapshot_missing_fresh_challenge_denies(monkeypatch):
    s=scenario(monkeypatch,initial_policy_seconds=22)
    def no_token(node):
        return {'accounting_lease':None,'ignored_clients':0,
                'restore_usage':{'clients':0},'accounting_challenge_requested_at':s.clock[0]}
    s.nodes.sync_traffic=no_token
    with pytest.raises(PolicyError,match='Fresh accounted Node challenge missing'):
        grant(s)
    assert not s.requests


def test_legacy_metered_caller_without_age_metadata_keeps_strict_check(monkeypatch):
    s=scenario(monkeypatch,initial_policy_seconds=23)
    s.first.pop('accounting_challenge_requested_at')
    assert grant(s)['valid']
    assert len(s.syncs)==0 and len(s.requests)==1
    assert s.manager_calls==[False]


def test_bad_monotonic_clock_never_grants(monkeypatch):
    s=scenario(monkeypatch)
    s.first['accounting_challenge_requested_at']=s.clock[0]+1
    with pytest.raises(PolicyError,match='monotonic challenge clock invalid'):
        grant(s)
    assert not s.requests


def test_traffic_checkpoint_records_get_start_as_challenge_age_bound(monkeypatch):
    from nodes import NodeRegistry
    import nodes as node_module
    current=[50.0]
    monkeypatch.setattr(node_module.time,'monotonic',lambda:current[0])
    seen=[]
    def request(*args,**kwargs):
        seen.append(current[0])
        current[0]+=7
        return {'items':[],'accountingLease':'e'*64},7010
    stub=SimpleNamespace(
        installations=SimpleNamespace(operation=lambda _node:contextlib.nullcontext()),
        _request=request,
        apply_traffic_snapshot=lambda *a,**kw:{
            'ignored_clients':0,'restore_usage':{'clients':0},
            'charged_bytes':0,'captured_at':100.0})
    snap=NodeRegistry.sync_traffic(stub,'node-ci')
    assert seen==[50.0]
    assert snap['accounting_lease']=='e'*64
    assert snap['accounting_challenge_requested_at']==50.0
    assert current==[57.0]

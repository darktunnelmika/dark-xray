"""No unnecessary Node full Apply or second traffic poll on a durable ACK.

Disposable SQLite registry; no network services or production runtime.
"""
import time
import threading
from types import SimpleNamespace

from auth import Auth
from dark_policy import Store
from nodes import NodeRegistry
import nodes as nodes_module


def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_module.socket,'getaddrinfo',
                        lambda *a,**kw:[(2,1,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'hub.sqlite3')
    auth=Auth(store,tmp_path/'key')
    registry=NodeRegistry(store,auth.cipher)
    registry.put('n','Node','https://n.example','dkn_'+'A'*60,True)
    payload={'schema':1,'generation':1}
    desired=registry.set_desired_state('n',payload)
    monkeypatch.setattr(registry,'probe',lambda *a,**kw:{})
    monkeypatch.setattr(registry,'deliver_pending_control',lambda node:{'queued':False})
    return store,registry,payload,desired


def test_helper_never_skips_pending_control_or_bad_revision_hash():
    state={'revision':8,'hash':'a'*64}
    good={'revision':8,'hash':'a'*64,'applied_revision':8,
          'applied_hash':'a'*64,'pending':False,'last_error':''}
    assert NodeRegistry._monitor_desired_already_applied(state,good,{'pending':False})
    assert not NodeRegistry._monitor_desired_already_applied(state,good,{'pending':True})
    for changed in ({'pending':True},{'last_error':'apply failed'},
                    {'applied_revision':7},{'applied_hash':'b'*64},
                    {'hash':'c'*64},{'revision':9}):
        d={**good,**changed}
        assert not NodeRegistry._monitor_desired_already_applied(state,d,{'pending':False})
    assert not NodeRegistry._monitor_desired_already_applied(
        {'revision':0,'hash':''},good,{'pending':False})


def test_already_acknowledged_node_imports_once_per_grant_and_never_reapplies(tmp_path,monkeypatch):
    store,reg,payload,desired=setup(tmp_path,monkeypatch)
    reg.mark_desired_state('n',desired['revision'],desired['hash'])
    calls=[]
    grants=[]
    finished=threading.Event()

    def provider(node):
        reg.set_desired_state(node,payload) # same revision every time
        return reg.desired_state(node)

    def traffic(node):
        calls.append('traffic')
        return {'charged_bytes':0,'ignored_clients':0,'accounting_lease':'a'*64}

    def no_reapply(*a,**kw):
        calls.append('INVALID_APPLY')
        raise AssertionError('Redundant full Node Apply on already durable ACK')

    def grant(node,snapshot):
        assert snapshot['accounting_lease']=='a'*64
        assert calls.count('traffic')==len(grants)+1
        grants.append(1)
        if len(grants)>=3:finished.set()
        return {'remaining_seconds':59}

    monkeypatch.setattr(reg,'sync_traffic',traffic)
    monkeypatch.setattr(reg,'sync_desired_state',no_reapply)
    try:
        reg.start(interval=.03,initial_delay=0,
                  desired_provider=provider,sync_provider=lambda n:[],
                  lease_callback=grant)
        assert finished.wait(3)
        assert grants and 'INVALID_APPLY' not in calls
        assert reg.desired_state('n',include_payload=False)['pending'] is False
    finally:
        reg.close();store.close()


def test_changed_desired_requires_apply_and_second_checkpoint_before_lease(tmp_path,monkeypatch):
    store,reg,payload,desired=setup(tmp_path,monkeypatch)
    order=[]
    finished=threading.Event()
    def provider(node):
        return reg.desired_state(node)
    def apply(node,state,**kw):
        order.append('apply')
        reg.mark_desired_state(node,state['revision'],state['hash'])
        return {'applied':True}
    def traffic(node):
        order.append('traffic')
        return {'charged_bytes':0,'ignored_clients':0}
    def lease(node,snapshot):
        order.append('lease');finished.set();return {'remaining_seconds':59}
    monkeypatch.setattr(reg,'sync_traffic',traffic)
    monkeypatch.setattr(reg,'sync_desired_state',apply)
    try:
        reg.start(interval=.15,initial_delay=0,desired_provider=provider,
                  sync_provider=lambda n:[],lease_callback=lease)
        assert finished.wait(3)
        assert order[:4]==['traffic','apply','traffic','lease']
        assert reg.desired_state('n',include_payload=False)['pending'] is False
    finally:
        reg.close();store.close()

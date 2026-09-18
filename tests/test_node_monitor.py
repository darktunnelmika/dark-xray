import time

from auth import Auth
from dark_policy import Store
from nodes import NodeRegistry
import nodes as nodes_mod


def test_monitor_probes_only_enabled_nodes_and_stops_cleanly(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'dark.sqlite3');auth=Auth(store,tmp_path/'secret.key');registry=NodeRegistry(store,auth.cipher)
    registry.put('on','Online','https://on.example','dkn_'+('A'*60),True)
    registry.put('off','Disabled','https://off.example','dkn_'+('B'*60),False)
    calls=[]
    def probe(node_id,*,timeout=8.0):
        calls.append((node_id,timeout));return {'node':registry.get(node_id),'latency_ms':1,'health':{}}
    monkeypatch.setattr(registry,'probe',probe)
    monkeypatch.setattr(registry,'sync_traffic',lambda node_id:{'charged_bytes':0,'clients':0})
    registry.start(interval=.02,initial_delay=0)
    time.sleep(.07);registry.close()
    assert calls and {x[0] for x in calls}=={'on'}
    assert all(x[1]==5.0 for x in calls)
    count=len(calls);time.sleep(.04);assert len(calls)==count
    store.close()


def test_monitor_start_is_idempotent(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3');auth=Auth(store,tmp_path/'secret.key');registry=NodeRegistry(store,auth.cipher)
    monkeypatch.setattr(registry,'probe',lambda *a,**k:None)
    monkeypatch.setattr(registry,'sync_traffic',lambda node_id:{'charged_bytes':0})
    registry.start(interval=.05,initial_delay=.05);first=registry.thread
    registry.start(interval=.01,initial_delay=0)
    assert registry.thread is first
    registry.close();assert registry.thread is None
    store.close()


def test_monitor_orders_probe_traffic_policy_callback_then_mirror_sync(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'dark.sqlite3');auth=Auth(store,tmp_path/'secret.key');registry=NodeRegistry(store,auth.cipher)
    registry.put('on','Online','https://on.example','dkn_'+('A'*60),True)
    order=[]
    monkeypatch.setattr(registry,'probe',lambda node_id,timeout=8.0:order.append('probe') or {})
    monkeypatch.setattr(registry,'sync_traffic',lambda node_id:order.append('traffic') or {'charged_bytes':7})
    monkeypatch.setattr(registry,'sync_mirrors',lambda node_id,bundles:order.append('mirror') or {})
    def provider(node_id):
        order.append('provider');return []
    def callback(node_id,result):
        assert result['charged_bytes']==7;order.append('policy')
    registry.start(interval=10,initial_delay=0,sync_provider=provider,traffic_callback=callback)
    for _ in range(50):
        if 'mirror' in order:break
        time.sleep(.01)
    registry.close()
    assert order[:5]==['probe','traffic','policy','provider','mirror']
    store.close()


def test_request_failure_then_success_records_recovery(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'dark.sqlite3');auth=Auth(store,tmp_path/'secret.key');registry=NodeRegistry(store,auth.cipher)
    registry.put('n1','Node','https://node.example','dkn_'+('R'*60),True)
    registry._request_failed('n1','network down')
    down=registry.list()[0]
    assert down['failure_count']==1 and down['last_offline_at']>0 and down['online'] is False
    registry._request_ok('n1',15)
    up=registry.list()[0]
    assert up['failure_count']==0 and up['recovery_count']==1 and up['last_recovered_at']>0
    assert up['last_error']=='' and up['online'] is True
    store.close()

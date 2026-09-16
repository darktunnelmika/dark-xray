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
    registry.start(interval=.02,initial_delay=0)
    time.sleep(.07);registry.close()
    assert calls and {x[0] for x in calls}=={'on'}
    assert all(x[1]==5.0 for x in calls)
    count=len(calls);time.sleep(.04);assert len(calls)==count
    store.close()


def test_monitor_start_is_idempotent(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3');auth=Auth(store,tmp_path/'secret.key');registry=NodeRegistry(store,auth.cipher)
    monkeypatch.setattr(registry,'probe',lambda *a,**k:None)
    registry.start(interval=.05,initial_delay=.05);first=registry.thread
    registry.start(interval=.01,initial_delay=0)
    assert registry.thread is first
    registry.close();assert registry.thread is None
    store.close()

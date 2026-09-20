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
    monkeypatch.setattr(registry,'sync_security',lambda node_id:order.append('security') or {'ips':1,'devices':0})
    monkeypatch.setattr(registry,'sync_mirrors',lambda node_id,bundles:order.append('mirror') or {})
    def provider(node_id):
        order.append('provider');return []
    def callback(node_id,result):
        assert result['charged_bytes']==7;order.append('policy')
    def security_callback(node_id,result):
        assert result['ips']==1;order.append('security-policy')
    registry.start(interval=10,initial_delay=0,sync_provider=provider,traffic_callback=callback,security_callback=security_callback)
    for _ in range(50):
        if order.count('security-policy')>=2:break
        time.sleep(.01)
    registry.close()
    assert order[:11]==['probe','traffic','policy','security','security-policy','provider','mirror','traffic','policy','security','security-policy']
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


def test_monitor_records_desired_state_before_failed_probe(tmp_path,monkeypatch):
    monkeypatch.setattr(nodes_mod.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    store=Store(tmp_path/'dark.sqlite3');auth=Auth(store,tmp_path/'secret.key');registry=NodeRegistry(store,auth.cipher)
    registry.put('n1','Node 1','https://node.example','dkn_'+('D'*60),True)
    calls=[]
    def desired(node_id):
        calls.append(('desired',node_id))
        registry.set_desired_state(node_id,{'schema':1,'generation':len(calls)})
        return registry.desired_state(node_id)
    def fail_probe(node_id,*,timeout=8.0):
        calls.append(('probe',node_id))
        raise nodes_mod.PolicyError('offline')
    monkeypatch.setattr(registry,'probe',fail_probe)
    monkeypatch.setattr(registry,'sync_traffic',lambda node_id:{'charged_bytes':0})
    registry.start(interval=.05,initial_delay=0,desired_provider=desired)
    for _ in range(50):
        if any(x[0]=='probe' for x in calls):break
        time.sleep(.01)
    registry.close()
    assert calls[:2]==[('desired','n1'),('probe','n1')]
    state=registry.desired_state('n1',include_payload=False)
    assert state['revision']>=1 and state['pending'] is True
    store.close()

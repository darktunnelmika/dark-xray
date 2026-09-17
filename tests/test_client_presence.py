import pytest
import manager as manager_module

from core import Config,CoreEngine
from dark_policy import Store,PolicyError
from manager import Manager


def test_activity_map_uses_real_client_activity_without_exposing_ip(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3')
    engine=CoreEngine(Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),test_engine=True),store,tmp_path/'runtime')
    manager=Manager(store,engine)
    monkeypatch.setattr(engine,'read_ip_log',lambda:None)
    monkeypatch.setattr(manager_module.time,'time',lambda:1000.0)
    with store.transaction() as db:
        db.execute("INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)",('e-a','owner','alpha',0,1,1,970.0))
        db.execute("INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)",('e-b','owner','beta',0,1,1,800.0))
        db.execute("INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)",('e-c','owner','gamma',0,1,1,500.0))
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?)",('beta','203.0.113.9','node',100.0,850.0,1))
    activity=manager._activity_map()
    assert activity['alpha']['presence_state']=='online'
    assert activity['alpha']['presence_age_seconds']==30
    assert activity['beta']['presence_state']=='idle'
    assert activity['beta']['activity_at']==850.0
    assert activity['gamma']['presence_state']=='offline'
    assert all('ip' not in row for row in activity.values())
    manager.close();store.close()


def test_vision_flow_rejected_for_grpc_or_xhttp(tmp_path,monkeypatch):
    store=Store(tmp_path/'dark.sqlite3')
    engine=CoreEngine(Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),test_engine=True),store,tmp_path/'runtime')
    manager=Manager(store,engine)
    monkeypatch.setattr(engine,'inbounds',lambda:[
        {'id':1,'protocol':'vless','remark':'gRPC REALITY','streamSettings':{'network':'grpc','security':'reality'}},
        {'id':2,'protocol':'vless','remark':'TCP REALITY','streamSettings':{'network':'tcp','security':'reality'}},
    ])
    with pytest.raises(PolicyError,match='XTLS Vision flow is only valid'):
        manager.validate_client_transport({'flow':'xtls-rprx-vision'},[1])
    manager.validate_client_transport({'flow':'xtls-rprx-vision'},[2])
    manager.validate_client_transport({'flow':''},[1])
    manager.close();store.close()

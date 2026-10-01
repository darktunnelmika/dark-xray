import pytest

from auth import Auth
from dark_policy import PolicyError, Store
from nodes import NodeRegistry


def registry(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    auth=Auth(store,tmp_path/'secret.key')
    return store,NodeRegistry(store,auth.cipher)


def test_node_warp_probe_rejects_invalid_timeout_before_network(tmp_path,monkeypatch):
    store,reg=registry(tmp_path)
    try:
        monkeypatch.setattr(reg,'_request',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network must not run')))
        with pytest.raises(PolicyError,match='Invalid Node WARP probe limits'):
            NodeRegistry.warp_endpoint_probe.__wrapped__(reg,'node-1','warp',None,attempts=2,timeout_seconds=0)
        with pytest.raises(PolicyError,match='Invalid Node WARP probe limits'):
            NodeRegistry.warp_endpoint_probe.__wrapped__(reg,'node-1','warp',None,attempts=4,timeout_seconds=4)
    finally:
        reg.close();store.close()


def test_node_warp_probe_rejects_invalid_endpoint_count_before_network(tmp_path,monkeypatch):
    store,reg=registry(tmp_path)
    try:
        monkeypatch.setattr(reg,'_request',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network must not run')))
        with pytest.raises(PolicyError,match='Select 1-20 WARP endpoints'):
            NodeRegistry.warp_endpoint_probe.__wrapped__(reg,'node-1','warp',[],attempts=2,timeout_seconds=4)
        with pytest.raises(PolicyError,match='Select 1-20 WARP endpoints'):
            NodeRegistry.warp_endpoint_probe.__wrapped__(reg,'node-1','warp',['162.159.192.1:2408']*21,attempts=2,timeout_seconds=4)
    finally:
        reg.close();store.close()


def test_node_traffic_matrix_probe_rejects_invalid_limits_before_network(tmp_path,monkeypatch):
    store,reg=registry(tmp_path)
    try:
        monkeypatch.setattr(reg,'_request',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network must not run')))
        with pytest.raises(PolicyError,match='Invalid Node probe limits'):
            NodeRegistry.traffic_matrix_probe.__wrapped__(reg,'node-1',443,'direct',attempts=2,timeout_seconds=11)
    finally:
        reg.close();store.close()

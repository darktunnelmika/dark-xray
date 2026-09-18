import importlib.util
import json
import os
from pathlib import Path

from dark_policy import PolicyError

ROOT=Path(__file__).resolve().parents[1]


def load_gate():
    path=ROOT/'tools/node-wan-gate.py'
    spec=importlib.util.spec_from_file_location('dark_node_wan_gate',path)
    mod=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class HealthyRegistry:
    def probe(self,node_id,timeout=8.0):
        return {'latency_ms':11,'health':{'core':{'state':'running'}}}

    def _request(self,node_id,path,method='GET',body=None,timeout=8.0):
        if path=='/node/api/mirrors/traffic':
            return {'items':[{'sourceEmail':'alice','up':1,'down':2}]},12
        if path=='/node/api/mirrors/security':
            return {'sourceVerified':True,'items':[{'sourceEmail':'alice','ips':[],'devices':[]}]},13
        raise AssertionError(path)

    def remote_inbounds(self,node_id):
        return {'latency_ms':14,'items':[{'id':1},{'id':2}]}


class BrokenRegistry(HealthyRegistry):
    def probe(self,node_id,timeout=8.0):
        raise PolicyError('network down')


def test_inspect_node_requires_real_agent_endpoints():
    gate=load_gate()
    node={'id':'de1','name':'Germany','failover_enabled':1,'data_address':'de.example.com','priority':10}
    result=gate.inspect_node(HealthyRegistry(),node,5)
    assert result['ok'] is True
    assert result['latency_ms']==11
    assert result['traffic_latency_ms']==12
    assert result['security_latency_ms']==13
    assert result['source_verified'] is True
    assert result['mirrored_traffic_clients']==1
    assert result['mirrored_security_clients']==1
    assert result['remote_inbounds']==2
    assert result['core_state']=='running'


def test_inspect_node_records_connection_failure_without_fake_success():
    gate=load_gate()
    node={'id':'tr1','name':'Turkey','failover_enabled':1,'data_address':'tr.example.com','priority':20}
    result=gate.inspect_node(BrokenRegistry(),node,5)
    assert result['ok'] is False
    assert 'network down' in result['error']
    assert 'latency_ms' not in result


def test_atomic_wan_report_is_private(tmp_path):
    gate=load_gate();path=tmp_path/'qa'/'node-wan-gate.json'
    gate.atomic_report(path,{'passed':False,'real_wan_requests':True})
    doc=json.loads(path.read_text())
    assert doc['real_wan_requests'] is True
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_wan_gate_does_not_claim_to_inject_outages():
    source=(ROOT/'tools/node-wan-gate.py').read_text(encoding='utf-8')
    assert "'network_loss_injected_by_gate':False" in source
    assert "--expect-outage" in source
    assert "saw_down" in source and "saw_recovered" in source

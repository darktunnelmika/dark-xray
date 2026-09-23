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


from test_node_wan_readiness import Registry


class HealthyRegistry(Registry):
    def __init__(self):
        super().__init__()
        self.remote = {'items': [{'id': 7}, {'id': 8}]}


class BrokenRegistry(HealthyRegistry):
    def probe(self,node_id,timeout=8.0):
        raise PolicyError('network down')


def test_inspect_node_requires_real_agent_endpoints():
    gate=load_gate()
    registry=HealthyRegistry()
    result=gate.inspect_node(registry,registry.list()[0],5)
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
    registry=BrokenRegistry()
    result=gate.inspect_node(registry,registry.list()[0],5)
    assert result['ok'] is False
    assert result['error']=='node_observation_failed'  # remote exception text is never exported
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


def test_source_expectation_parser_accepts_ipv4_and_ipv6_without_logging_raw_failures():
    gate=load_gate()
    parsed=gate.parse_source_expectations([
        'node-a,user@example.test,203.0.113.7',
        'node-a,user@example.test,2001:db8::7',
    ])
    assert parsed=={'node-a':[('user@example.test','203.0.113.7'),('user@example.test','2001:db8::7')]}


def test_fresh_source_evidence_requires_verified_recent_exact_pair():
    gate=load_gate();now=1000.0
    security={'sourceVerified':True,'items':[{
        'sourceEmail':'user@example.test',
        'ips':[{'ip':'203.0.113.7','lastSeen':999.0},{'ip':'203.0.113.8','lastSeen':700.0}],
    }]}
    evidence=gate.security_source_evidence(security,[('user@example.test','203.0.113.7')],900.0)
    assert evidence['required'] is True and evidence['matched']==1
    for bad in (
        {'sourceVerified':False,'items':security['items']},
        {'sourceVerified':True,'items':[{'sourceEmail':'user@example.test','ips':[{'ip':'203.0.113.7','lastSeen':800.0}]}]},
        {'sourceVerified':True,'items':[{'sourceEmail':'other@example.test','ips':[{'ip':'203.0.113.7','lastSeen':999.0}]}]},
    ):
        try:gate.security_source_evidence(bad,[('user@example.test','203.0.113.7')],900.0)
        except gate.GateRejected:pass
        else:raise AssertionError('unverified, stale or wrong-client source evidence must fail')

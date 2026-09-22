#!/usr/bin/env python3
"""DARK XRAY real-WAN node validation gate.

This gate uses the configured Central node registry and the same pinned HTTPS/TLS
client as production. It never changes customer configuration, Xray config or
firewall rules. It may update normal node health metadata (last_seen/error) while
probing. Traffic/security endpoints are read without importing usage into Central.

For a real outage/recovery rehearsal, run with --watch-seconds and one or more
--expect-outage NODE_ID values, then interrupt and restore those node networks
outside this process. PASS requires observing ready -> unavailable -> ready with stable Hub intent.
It verifies reported runtime/configuration and failover eligibility, NOT a real
customer connection, successful global IP enforcement or the cause of an outage.
Pinned lightweight Agents and an acknowledged desired configuration are required.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

from auth import Auth
from core import Config
from dark_policy import PolicyError,Store
from nodes import NodeRegistry
from node_installations import NodeInstallations
from node_gate_budget import node_budget


def atomic_report(path:Path,doc:dict)->None:
    if path.is_symlink():raise PolicyError('Unsafe report path')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    data=(json.dumps(doc,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        try:
            if tmp.exists():tmp.unlink()
        except OSError:pass


class GateRejected(PolicyError):
    """Fixed diagnostic codes only; never copy an Agent response into a report."""


def node_signature(node:dict)->dict:
    """Intent/topology only. Normal health timestamps do not invalidate a round."""
    return {
        'node': {k:node.get(k) for k in ('id','origin','enabled','data_address','priority','failover_enabled')},
        'installation': node.get('installation'),
        'desired': {k:(node.get('desired_state') or {}).get(k) for k in
                    ('revision','desired_hash','applied_revision','applied_hash','last_error','pending')},
        'control': {k:(node.get('control') or {}).get(k) for k in
                    ('revision','command_id','action','applied_revision','last_error','pending','persisted','desired_running')},
        'assignments': sorted(
            ((a.get('local_inbound_id'),a.get('remote_inbound_id'),a.get('last_error'))
             for a in node.get('assignments',[])), key=lambda a:str(a[0])),
    }


def current_node(registry:NodeRegistry,node_id:str)->dict:
    # list() uses the same reentrant Store lock. Never hold it during I/O.
    with registry.store.lock:
        rows=[r for r in registry.list() if r['id']==node_id]
        if len(rows)!=1:raise GateRejected('node_not_registered')
        return rows[0]


def require_ready_health(node:dict,health:dict)->None:
    if not node.get('enabled'):raise GateRejected('node_disabled')
    if not isinstance(health,dict):raise GateRejected('invalid_health')
    try:agent,installation=NodeInstallations.descriptor(health)
    except PolicyError:raise GateRejected('installation_unverified') from None
    bound=node.get('installation') or {}
    if agent!=bound.get('agent_id') or installation!=bound.get('installation_id'):
        raise GateRejected('installation_mismatch')
    core=health.get('core')
    if (not isinstance(core,dict) or core.get('state')!='running'
        or core.get('dirty') is not False or core.get('last_error')):
        raise GateRejected('core_not_ready')
    maintenance=health.get('maintenance')
    if not isinstance(maintenance,dict) or maintenance.get('last_error'):
        raise GateRejected('maintenance_unverified')
    run=health.get('run_control')
    if not isinstance(run,dict) or run.get('manual_stop') is not False or run.get('effective_running') is not True:
        raise GateRejected('run_intent_not_ready')
    desired=node.get('desired_state') or {}
    revision=desired.get('revision');digest=desired.get('desired_hash')
    if (type(revision) is not int or revision<1 or not isinstance(digest,str)
        or not re.fullmatch(r'[0-9a-f]{64}',digest) or desired.get('pending') is not False
        or desired.get('last_error') or desired.get('applied_revision')!=revision
        or desired.get('applied_hash')!=digest):
        raise GateRejected('hub_configuration_not_ready')
    remote=health.get('desired_state')
    if (not isinstance(remote,dict) or type(remote.get('appliedRevision')) is not int
        or remote['appliedRevision']!=revision or remote.get('appliedHash')!=digest or remote.get('lastError')):
        raise GateRejected('agent_configuration_mismatch')
    control=node.get('control') or {};receipt=health.get('control_receipt')
    if (control.get('pending') is not False or control.get('last_error')
        or control.get('desired_running') is not True or not isinstance(receipt,dict)):
        raise GateRejected('control_not_ready')
    if control.get('persisted') is True:
        if (receipt.get('persisted') is not True or type(receipt.get('revision')) is not int
            or receipt.get('phase')!='applied'
            or receipt.get('last_error') or receipt.get('pending') is not False
            or any(receipt.get(k)!=control.get(k) for k in ('revision','command_id','action'))):
            raise GateRejected('control_receipt_mismatch')
    elif receipt.get('persisted') is not False:
        raise GateRejected('unrecognized_agent_command')
    assignments=node.get('assignments')
    if (not isinstance(assignments,list) or not assignments
        or any(type(a.get('remote_inbound_id')) is not int or a['remote_inbound_id']<1
               or a.get('last_error') for a in assignments)):
        raise GateRejected('assignments_not_ready')


def inspect_node(registry:NodeRegistry,node:dict,timeout:float)->dict:
    """A read-only runtime observation, NOT an actual customer connection test."""
    node_id=str(node['id']);started=time.monotonic()
    out={'id':node_id,'name':str(node.get('name') or node_id),'ok':False,
         'agent_reachable':False,'failover_ready':False,
         'failover_enabled':bool(node.get('failover_enabled')),
         'data_address':str(node.get('data_address') or ''),'priority':int(node.get('priority') or 100)}
    try:
        # One installation context fences the entire set, not just each HTTP read.
        with registry.installations.operation(node_id):
            before=current_node(registry,node_id)
            if node_signature(before)!=node_signature(node):raise GateRejected('node_state_changed')
            probe=registry.probe(node_id,timeout=timeout)
            out['agent_reachable']=True
            require_ready_health(before,probe.get('health'))
            traffic,traffic_ms=registry._request(node_id,'/node/api/mirrors/traffic',timeout=timeout)
            security,security_ms=registry._request(node_id,'/node/api/mirrors/security',timeout=timeout)
            remote,_=registry._request(node_id,'/node/api/inbounds',timeout=timeout)
            if not isinstance(traffic,dict) or not isinstance(traffic.get('items'),list):
                raise GateRejected('invalid_traffic_response')
            if (not isinstance(security,dict) or type(security.get('sourceVerified')) is not bool
                or not isinstance(security.get('items'),list)):
                raise GateRejected('invalid_security_response')
            if (not isinstance(remote,list) or any(not isinstance(a,dict) or type(a.get('id')) is not int
                                                   or a['id']<1 for a in remote)):
                raise GateRejected('invalid_inbound_response')
            remote_ids={a['id'] for a in remote}
            if (len(remote_ids)!=len(remote)
                or not {a['remote_inbound_id'] for a in before['assignments']}<=remote_ids):
                raise GateRejected('remote_assignments_missing')
            # The first health can be obsolete by the time the other reads finish.
            final_probe=registry.probe(node_id,timeout=timeout)
            after=current_node(registry,node_id)
            if node_signature(before)!=node_signature(after):raise GateRejected('node_state_changed')
            require_ready_health(after,final_probe.get('health'))
            verified={'latency_ms':int(final_probe['latency_ms']),
                      'traffic_latency_ms':int(traffic_ms),'security_latency_ms':int(security_ms),
                      'source_verified':security['sourceVerified'],
                      'mirrored_traffic_clients':len(traffic['items']),
                      'mirrored_security_clients':len(security['items']),
                      'remote_inbounds':len(remote),'core_state':'running',
                      'installation':after['installation'],
                      'configuration_revision':after['desired_state']['revision'],
                      'failover_ready':after.get('failover_ready') is True}
        # A final installation-fence failure must not leave ok=True in the report.
        out.update(ok=True,**verified)
    except Exception as ex:
        error=str(ex) if isinstance(ex,GateRejected) else 'node_observation_failed'
        out.update(ok=False,failover_ready=False,error=error)
    out['elapsed_ms']=max(1,int((time.monotonic()-started)*1000))
    return out


def advance_recovery(state:dict,ready:bool,at:float)->None:
    """Observe ready -> unavailable -> ready; not proof of a network-only outage."""
    if ready and not state['saw_down']:
        state['saw_ready_before']=True
    elif not ready and state['saw_ready_before'] and not state['saw_down']:
        state['saw_down']=True;state['down_at']=at
    elif ready and state['saw_down'] and not state['saw_recovered']:
        state['saw_recovered']=True;state['recovered_at']=at


def fleet_signature(registry:NodeRegistry)->list:
    with registry.store.lock:
        return sorted((node_signature(n) for n in registry.list() if n.get('enabled')),
                      key=lambda n:n['node']['id'])


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--min-nodes',type=int,default=2)
    ap.add_argument('--expected-node-count',type=int,default=None,help='Parent budget snapshot; mismatch fails before probes')
    ap.add_argument('--timeout',type=float,default=8.0)
    ap.add_argument('--watch-seconds',type=float,default=0.0)
    ap.add_argument('--interval',type=float,default=5.0)
    ap.add_argument('--expect-outage',action='append',default=[],metavar='NODE_ID')
    ap.add_argument('--report',type=Path)
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    if a.min_nodes<1:raise SystemExit('--min-nodes must be >= 1')
    if not .2<=a.timeout<=30:raise SystemExit('--timeout must be between 0.2 and 30 seconds')
    if not all(math.isfinite(x) for x in (a.timeout,a.watch_seconds,a.interval)):
        raise SystemExit('timing values must be finite')
    if a.watch_seconds<0 or a.interval<=0:raise SystemExit('watch/interval values are invalid')
    if a.expect_outage and a.watch_seconds<=0:raise SystemExit('--expect-outage requires --watch-seconds')

    if a.expected_node_count is not None and a.expected_node_count<0:
        raise SystemExit('--expected-node-count must be >= 0')

    cfg=Config.load(a.config)
    db_path=a.data/'dark.sqlite3';secret=a.data/'secret.key'
    if not db_path.is_file() or db_path.is_symlink():raise SystemExit('DARK database is missing or unsafe')
    if not secret.is_file() or secret.is_symlink():raise SystemExit('DARK secret.key is missing or unsafe')

    store=Store(db_path);registry=None
    started=time.time()
    try:
        auth=Auth(store,secret);registry=NodeRegistry(store,auth.cipher)
        nodes=[n for n in registry.list() if n.get('enabled')]
        baseline=sorted((node_signature(n) for n in nodes),key=lambda n:n['node']['id'])
        fleet_changed=False
        ids={str(n['id']) for n in nodes}
        unknown=sorted(set(a.expect_outage)-ids)
        transitions={node_id:{'saw_ready_before':False,'saw_down':False,'saw_recovered':False,'down_at':0,'recovered_at':0}
                     for node_id in a.expect_outage}
        rounds=[]

        def one_round()->list[dict]:
            nonlocal fleet_changed
            rows=[inspect_node(registry,n,a.timeout) for n in nodes]
            fleet_changed=fleet_changed or fleet_signature(registry)!=baseline
            now=time.time()
            for row in rows:
                state=transitions.get(row['id'])
                if state is None:continue
                advance_recovery(state,row['ok'] is True,now)
            rounds.append({'at':now,'nodes':rows})
            return rows

        budget=node_budget(len(nodes),a.timeout,a.watch_seconds)
        budget_matches=a.expected_node_count is None or a.expected_node_count==len(nodes)
        final=one_round() if budget_matches else []
        if a.watch_seconds>0 and budget_matches:
            deadline=time.monotonic()+a.watch_seconds
            while time.monotonic()<deadline:
                time.sleep(min(a.interval,max(0,deadline-time.monotonic())))
                final=one_round()

        enabled_count=len(nodes);healthy=sum(bool(x['ok']) for x in final)
        failover_ready=sum(bool(x['ok'] and x['failover_ready']) for x in final)
        recovery_ok=all(v['saw_down'] and v['saw_recovered'] for v in transitions.values()) and not unknown
        passed=(budget_matches and not fleet_changed and enabled_count>=a.min_nodes and healthy==enabled_count and failover_ready>=min(a.min_nodes,enabled_count)
                and (recovery_ok if a.expect_outage else True))
        report={'version':(ROOT/'VERSION').read_text(encoding='utf-8').strip(),
                'passed':passed,'started_at':started,'finished_at':time.time(),
                'configured_enabled_nodes':enabled_count,'minimum_nodes':a.min_nodes,
                'healthy_nodes':healthy,'failover_ready_nodes':failover_ready,
                'expected_outages':a.expect_outage,'unknown_expected_nodes':unknown,
                'recovery':transitions,'final':final,
                'round_count':len(rounds),'real_wan_requests':bool(nodes and budget_matches),
                'observation_complete':budget_matches,'budget_matches_fleet':budget_matches,
                'expected_node_count':a.expected_node_count,'timing_budget':budget,
                'fleet_changed':fleet_changed,
                'observation_basis':'authenticated Agent reports and Hub intent; not customer traffic',
                'customer_connection_tested':False,'global_ip_guard_tested':False,
                'network_outage_proven':False,
                'network_loss_injected_by_gate':False,
                'changes_made':'normal probe health/identity metadata and private report only; no customer/Xray/firewall mutation'}
        if not budget_matches:report['error']='node_count_changed_since_budget'
        if a.watch_seconds>0:report['rounds']=rounds
        report_path=a.report or (a.data/'qa/node-wan-gate.json')
        atomic_report(report_path,report);report['report_path']=str(report_path)
        if not a.json_only:
            print('DARK XRAY NODE WAN GATE',report['version'])
            print('PASS' if passed else 'FAIL')
            print(' Enabled nodes       :',enabled_count)
            print(' Healthy final       :',healthy)
            print(' Failover ready final:',failover_ready)
            if a.expect_outage:
                for node_id,state in transitions.items():
                    print(' Recovery',node_id,':','PASS' if state['saw_down'] and state['saw_recovered'] else 'FAIL')
            print(' Report:',report_path)
            print('\nJSON RESULT')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        raise SystemExit(0 if passed else 1)
    finally:
        if registry:registry.close()
        store.close()


if __name__=='__main__':main()

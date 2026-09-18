#!/usr/bin/env python3
"""DARK XRAY real-WAN node validation gate.

This gate uses the configured Central node registry and the same pinned HTTPS/TLS
client as production. It never changes customer configuration, Xray config or
firewall rules. It may update normal node health metadata (last_seen/error) while
probing. Traffic/security endpoints are read without importing usage into Central.

For a real outage/recovery rehearsal, run with --watch-seconds and one or more
--expect-outage NODE_ID values, then interrupt and restore those node networks
outside this process. PASS requires observing down -> recovered and final health.
"""
from __future__ import annotations

import argparse
import json
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


def atomic_report(path:Path,doc:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    data=(json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode()
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        try:
            if tmp.exists():tmp.unlink()
        except OSError:pass


def inspect_node(registry:NodeRegistry,node:dict,timeout:float)->dict:
    node_id=str(node['id']);started=time.monotonic()
    out={'id':node_id,'name':str(node.get('name') or node_id),'ok':False,
         'failover_enabled':bool(node.get('failover_enabled')),
         'data_address':str(node.get('data_address') or ''),
         'priority':int(node.get('priority') or 100)}
    try:
        probe=registry.probe(node_id,timeout=timeout)
        traffic,traffic_ms=registry._request(node_id,'/node/api/mirrors/traffic',timeout=timeout)
        security,security_ms=registry._request(node_id,'/node/api/mirrors/security',timeout=timeout)
        remote=registry.remote_inbounds(node_id)
        if not isinstance(traffic,dict) or not isinstance(traffic.get('items'),list):
            raise PolicyError('Invalid mirror traffic endpoint')
        if not isinstance(security,dict) or type(security.get('sourceVerified')) is not bool or not isinstance(security.get('items'),list):
            raise PolicyError('Invalid mirror security endpoint')
        out.update(ok=True,latency_ms=int(probe['latency_ms']),
                   traffic_latency_ms=int(traffic_ms),security_latency_ms=int(security_ms),
                   source_verified=bool(security['sourceVerified']),
                   mirrored_traffic_clients=len(traffic['items']),
                   mirrored_security_clients=len(security['items']),
                   remote_inbounds=len(remote.get('items',[])),
                   core_state=str(probe.get('health',{}).get('core',{}).get('state','')),
                   elapsed_ms=max(1,int((time.monotonic()-started)*1000)))
    except Exception as ex:
        out.update(error=(str(ex) if isinstance(ex,PolicyError) else type(ex).__name__+': '+str(ex))[:500],
                   elapsed_ms=max(1,int((time.monotonic()-started)*1000)))
    return out


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--min-nodes',type=int,default=2)
    ap.add_argument('--timeout',type=float,default=8.0)
    ap.add_argument('--watch-seconds',type=float,default=0.0)
    ap.add_argument('--interval',type=float,default=5.0)
    ap.add_argument('--expect-outage',action='append',default=[],metavar='NODE_ID')
    ap.add_argument('--report',type=Path)
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    if a.min_nodes<1:raise SystemExit('--min-nodes must be >= 1')
    if not .2<=a.timeout<=30:raise SystemExit('--timeout must be between 0.2 and 30 seconds')
    if a.watch_seconds<0 or a.interval<=0:raise SystemExit('watch/interval values are invalid')
    if a.expect_outage and a.watch_seconds<=0:raise SystemExit('--expect-outage requires --watch-seconds')

    cfg=Config.load(a.config)
    db_path=a.data/'dark.sqlite3';secret=a.data/'secret.key'
    if not db_path.is_file() or db_path.is_symlink():raise SystemExit('DARK database is missing or unsafe')
    if not secret.is_file() or secret.is_symlink():raise SystemExit('DARK secret.key is missing or unsafe')

    store=Store(db_path);registry=None
    started=time.time()
    try:
        auth=Auth(store,secret);registry=NodeRegistry(store,auth.cipher)
        nodes=[n for n in registry.list() if n.get('enabled')]
        ids={str(n['id']) for n in nodes}
        unknown=sorted(set(a.expect_outage)-ids)
        transitions={node_id:{'saw_down':False,'saw_recovered':False,'down_at':0,'recovered_at':0}
                     for node_id in a.expect_outage}
        rounds=[]

        def one_round()->list[dict]:
            rows=[inspect_node(registry,n,a.timeout) for n in nodes]
            now=time.time()
            for row in rows:
                state=transitions.get(row['id'])
                if state is None:continue
                if not row['ok'] and not state['saw_down']:
                    state['saw_down']=True;state['down_at']=now
                elif row['ok'] and state['saw_down'] and not state['saw_recovered']:
                    state['saw_recovered']=True;state['recovered_at']=now
            rounds.append({'at':now,'nodes':rows})
            return rows

        final=one_round()
        if a.watch_seconds>0:
            deadline=time.monotonic()+a.watch_seconds
            while time.monotonic()<deadline:
                time.sleep(min(a.interval,max(0,deadline-time.monotonic())))
                final=one_round()

        enabled_count=len(nodes);healthy=sum(bool(x['ok']) for x in final)
        failover_ready=sum(bool(x['ok'] and x['failover_enabled'] and x['data_address']) for x in final)
        recovery_ok=all(v['saw_down'] and v['saw_recovered'] for v in transitions.values()) and not unknown
        passed=(enabled_count>=a.min_nodes and healthy==enabled_count and failover_ready>=min(a.min_nodes,enabled_count)
                and (recovery_ok if a.expect_outage else True))
        report={'version':(ROOT/'VERSION').read_text(encoding='utf-8').strip(),
                'passed':passed,'started_at':started,'finished_at':time.time(),
                'configured_enabled_nodes':enabled_count,'minimum_nodes':a.min_nodes,
                'healthy_nodes':healthy,'failover_ready_nodes':failover_ready,
                'expected_outages':a.expect_outage,'unknown_expected_nodes':unknown,
                'recovery':transitions,'final':final,
                'round_count':len(rounds),'real_wan_requests':True,
                'network_loss_injected_by_gate':False,
                'changes_made':'node health metadata only; no customer/Xray/firewall mutation'}
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

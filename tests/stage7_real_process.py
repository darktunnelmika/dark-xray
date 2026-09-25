#!/usr/bin/env python3
"""Disposable process entrypoints for Stage 7.4 real multi-node acceptance."""
from __future__ import annotations

import argparse
import json
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def run_node(args):
    import node_agent

    control=Path(args.warp_control)
    def fixture_scan(binary,assets,outbounds,*,attempts=2,timeout=5.0):
        try:mode=json.loads(control.read_text()).get('mode','healthy')
        except Exception:mode='healthy'
        rows=[]
        for outbound in outbounds:
            tag=str(outbound.get('tag',''))
            if mode=='degraded':
                rows.append({'tag':tag,'ok':True,'latenciesMs':[1800.0,1950.0][:attempts],
                             'lossPercent':35.0,'attempts':attempts,'successes':attempts,
                             'failures':0,'error':'','source':'stage7-real-fixture',
                             'probeUrl':'https://fixture.invalid/204','productionTrafficMutation':False})
            else:
                rows.append({'tag':tag,'ok':True,'latenciesMs':[52.0,58.0,55.0][:attempts],
                             'lossPercent':0.0,'attempts':attempts,'successes':attempts,
                             'failures':0,'error':'','source':'stage7-real-fixture',
                             'probeUrl':'https://fixture.invalid/204','productionTrafficMutation':False})
        return rows
    node_agent.scan_warp_outbounds=fixture_scan
    sys.argv=['node_agent.py','--config',args.config,'--data',args.data,'--token-file',args.token_file,
              '--node-id-file',args.node_id_file,'--host','0.0.0.0','--port',str(args.port)]
    node_agent.main()


def run_hub(args):
    import nodes
    import server as server_module

    ca=str(Path(args.ca).resolve())
    real_create=ssl.create_default_context
    nodes.ssl.create_default_context=lambda: real_create(cafile=ca)
    allowed=set(json.loads(Path(args.hosts).read_text()))

    def fixture_resolve(raw):
        if not isinstance(raw,str): raise nodes.PolicyError('Invalid node URL')
        p=urlsplit(raw.strip())
        if p.scheme!='https' or not p.hostname or p.path not in ('','/') or p.query or p.fragment:
            raise nodes.PolicyError('Invalid fixture node URL')
        port=p.port or 443
        host=p.hostname.lower()
        if host not in allowed: raise nodes.PolicyError('Fixture node host is not allowlisted')
        return f'https://{host}:{port}',host,port,('127.0.0.1',)
    nodes.resolve_origin=fixture_resolve

    def fixture_scan(binary,assets,outbounds,*,attempts=3,timeout=5.0):
        return [{'tag':str(o.get('tag','')),'ok':True,'latenciesMs':[45.0,50.0,48.0][:attempts],
                 'lossPercent':0.0,'attempts':attempts,'successes':attempts,'failures':0,'error':'',
                 'source':'stage7-hub-fixture','probeUrl':'https://fixture.invalid/204',
                 'productionTrafficMutation':False} for o in outbounds]
    server_module.scan_warp_outbounds=fixture_scan

    sys.argv=['server.py','--config',args.config,'--data',args.data,'serve',
              '--host','0.0.0.0','--port',str(args.port)]
    server_module.main()


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='mode',required=True)
    node=sub.add_parser('node')
    node.add_argument('--config',required=True);node.add_argument('--data',required=True)
    node.add_argument('--token-file',required=True);node.add_argument('--node-id-file',required=True)
    node.add_argument('--port',type=int,required=True);node.add_argument('--warp-control',required=True)
    hub=sub.add_parser('hub')
    hub.add_argument('--config',required=True);hub.add_argument('--data',required=True)
    hub.add_argument('--port',type=int,required=True);hub.add_argument('--ca',required=True)
    hub.add_argument('--hosts',required=True)
    args=p.parse_args()
    run_node(args) if args.mode=='node' else run_hub(args)


if __name__=='__main__':
    main()

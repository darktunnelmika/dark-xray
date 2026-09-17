#!/usr/bin/env python3
"""Status/clear client for the root-owned DARK IP Guard broker."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dark_policy import PolicyError
from guard_bridge import BrokerClient

DEFAULT_SOCKET='/run/dark-xray-guard/control.sock'


def guard_socket(config:Path)->str:
    value=json.loads(config.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise SystemExit('Panel configuration must be a JSON object')
    path=str(value.get('guard_socket',DEFAULT_SOCKET))
    if path!=DEFAULT_SOCKET:raise SystemExit('Unexpected Guard socket path')
    return path


def main()->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('action',choices=('status','clear'));a=p.parse_args()
    try:
        client=BrokerClient(guard_socket(a.config))
        result=client.status() if a.action=='status' else client.clear()
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (PolicyError,OSError,ValueError,json.JSONDecodeError) as ex:
        print('Guard operation refused: '+str(ex),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only DARK XRAY health check that is safe while the main service is running."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from core import Config


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True)
    a=p.parse_args()

    cfg=Config.load(a.config)
    db_path=a.data/'dark.sqlite3'
    if db_path.is_symlink() or not db_path.is_file():raise SystemExit('DARK database is missing or unsafe')
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        quick=db.execute('PRAGMA quick_check').fetchone()[0]
        if quick!='ok':raise SystemExit('DARK database quick_check failed')
        inbounds=db.execute('SELECT COUNT(*) FROM core_inbounds').fetchone()[0]
        clients=db.execute('SELECT COUNT(*) FROM core_clients').fetchone()[0]
        runtime=db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
    cp=subprocess.run([cfg.xray_binary,'version'],capture_output=True,text=True,timeout=10,check=False)
    first=(cp.stdout or '').splitlines()[0] if cp.stdout else ''
    if cp.returncode or not first.lstrip().startswith('Xray '):raise SystemExit('Invalid Xray version response')
    staged=False
    if runtime:
        try:
            desired=json.loads(runtime[0])
            staged=any([
                int(desired.get('bind_port',cfg.bind_port))!=cfg.bind_port,
                str(desired.get('public_address',cfg.public_address))!=cfg.public_address,
                str(desired.get('panel_path',cfg.panel_path))!=cfg.panel_path,
                int(desired.get('poll_seconds',cfg.poll_seconds))!=cfg.poll_seconds,
                bool(desired.get('core_autostart',cfg.core_autostart))!=cfg.core_autostart,
            ])
        except Exception:raise SystemExit('Saved runtime settings are invalid')
    print('Core:',first[:250])
    print('Database: ok')
    print('Inbounds:',inbounds,'Clients:',clients)
    print('Writes enabled:',cfg.writes_enabled)
    print('Staged runtime changes:', 'yes' if staged else 'no')


if __name__=='__main__':main()

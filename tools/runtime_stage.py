#!/usr/bin/env python3
"""Stage safe DARK runtime settings as the unprivileged service account."""
from __future__ import annotations
import argparse,json,re,sqlite3
from pathlib import Path
from urllib.parse import urlsplit

PATH_RE=re.compile(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*')
RESERVED={'api','assets','sub','node','health'}

def normalize(value:str)->str:
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    if value!='/' and not PATH_RE.fullmatch(value):raise SystemExit('URI path must be / or slash-prefixed alphanumeric/_/- segments')
    if len(value)>200:raise SystemExit('URI path is too long')
    first=value.strip('/').split('/',1)[0].lower() if value!='/' else ''
    if first in RESERVED:raise SystemExit('URI path conflicts with a reserved DARK endpoint')
    return value

def defaults(config:dict)->dict:
    origin=urlsplit(str(config.get('public_origin','http://127.0.0.1:2087')))
    mode='domain_tls' if origin.scheme=='https' else 'ssh'
    return {'access_mode':mode,'bind_port':int(config.get('bind_port',2087)),'public_address':str(config.get('public_address','127.0.0.1')),
            'panel_path':str(config.get('panel_path','/')),'poll_seconds':int(config.get('poll_seconds',5)),
            'core_autostart':bool(config.get('core_autostart',False)),'domain':(origin.hostname or '') if mode=='domain_tls' else '','acme_email':''}

def apply_overrides(value:dict,*,panel_path=None,bind_port=None,public_address=None)->dict:
    value=dict(value);changed={}
    if panel_path is not None:
        value['panel_path']=normalize(panel_path);changed['panel_path']=value['panel_path']
    if bind_port is not None:
        if type(bind_port)is not int or not 1024<=bind_port<=65535:raise SystemExit('Panel port must be between 1024 and 65535')
        value['bind_port']=bind_port;changed['bind_port']=bind_port
    if public_address is not None:
        address=str(public_address).strip()
        if not address or len(address)>253 or any(c in address for c in '/?#@ \r\n\t'):
            raise SystemExit('Public proxy address must be a plain IP or DNS name')
        value['public_address']=address;changed['public_address']=address
    if not changed:raise SystemExit('No runtime field was requested for staging')
    return value,changed


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--panel-path');p.add_argument('--bind-port',type=int);p.add_argument('--public-address')
    a=p.parse_args();config=json.loads(a.config.read_text());value=defaults(config);dbpath=a.data/'dark.sqlite3'
    if dbpath.is_symlink() or not dbpath.is_file():raise SystemExit('DARK database is missing or unsafe')
    with sqlite3.connect(str(dbpath),timeout=30) as db:
        row=db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
        if row:
            saved=json.loads(row[0])
            if not isinstance(saved,dict):raise SystemExit('Saved runtime settings are invalid')
            value.update(saved)
        value,changed=apply_overrides(value,panel_path=a.panel_path,bind_port=a.bind_port,public_address=a.public_address)
        db.execute("INSERT INTO core_sections(name,body) VALUES('runtime',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",(json.dumps(value),))
        db.commit()
    print('Runtime settings staged:',json.dumps(changed,ensure_ascii=False))

if __name__=='__main__':main()

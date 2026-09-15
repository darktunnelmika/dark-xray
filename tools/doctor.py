#!/usr/bin/env python3
"""Read-only deployment checks, safe while the main service is running."""
import argparse,json,os,shutil,socket,sqlite3,ssl,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from core import Config
from guard_bridge import BrokerClient


def _panel_http_status(cfg:Config,path:str)->int:
    origin=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(cfg.public_origin)
    target='127.0.0.1' if cfg.bind_host in {'0.0.0.0','::'} else cfg.bind_host
    sock=socket.create_connection((target,cfg.bind_port),timeout=2.5)
    try:
        if origin.scheme=='https':
            ctx=ssl.create_default_context();sock=ctx.wrap_socket(sock,server_hostname=origin.hostname)
        req=f'GET {path} HTTP/1.1\r\nHost: {origin.netloc}\r\nConnection: close\r\nUser-Agent: darkxray-doctor\r\n\r\n'.encode()
        sock.sendall(req);raw=b''
        while b'\r\n' not in raw and len(raw)<4096:
            chunk=sock.recv(512)
            if not chunk:break
            raw+=chunk
        first=raw.split(b'\r\n',1)[0].decode('ascii','replace').split()
        return int(first[1]) if len(first)>=2 and first[1].isdigit() else 0
    finally:
        try:sock.close()
        except Exception:pass


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--data',type=Path,required=True);a=p.parse_args()
    result={'checks':{},'changes_made':False};checks=result['checks']
    try:
        cfg=Config.load(a.config);checks['configuration']='ok'
        try:
            version=subprocess.run([cfg.xray_binary,'version'],capture_output=True,text=True,timeout=10,check=True).stdout.splitlines()[0]
            checks['core_version']=version
        except Exception as ex:checks['core_error']=str(ex)[:300]
        db=a.data/'dark.sqlite3'
        if db.exists():
            with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as c:checks['database']=c.execute('PRAGMA quick_check').fetchone()[0]
        else:checks['database']='not initialized'
        checks['mfa_key_present']=(a.data/'secret.key').is_file()
        checks['tls_configured']=bool(cfg.tls_certificate and cfg.tls_private_key)
        checks['panel_url']=cfg.public_origin+(cfg.panel_path if cfg.panel_path!='/' else '')+'/'
        try:
            base=(cfg.panel_path if cfg.panel_path!='/' else '')+'/'
            ui_status=_panel_http_status(cfg,base);asset_status=_panel_http_status(cfg,base+'assets/style.css')
            checks['panel_route']={'ui_status':ui_status,'asset_status':asset_status,'ok':ui_status==200 and asset_status==200}
            if cfg.panel_path!='/':checks['root_hidden_status']=_panel_http_status(cfg,'/')
        except Exception as ex:checks['panel_route']={'ok':False,'error':type(ex).__name__+': '+str(ex)[:180]}
        checks['protected_ports']=cfg.protected_ports
        try:checks['guard']=BrokerClient(cfg.guard_socket).status()
        except Exception as ex:checks['guard']={'connected':False,'message':str(ex)}
        checks['nft_binary_found']=shutil.which('nft') is not None
        checks['live_packet_test']='not performed by doctor'
    except Exception as ex:checks['configuration_error']=str(ex)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

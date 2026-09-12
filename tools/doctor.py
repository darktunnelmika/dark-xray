#!/usr/bin/env python3
"""Read-only deployment checks, safe while the main service is running."""
import argparse,json,os,shutil,socket,sqlite3,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from core import Config
from guard_bridge import BrokerClient

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
        checks['protected_ports']=cfg.protected_ports
        try:checks['guard']=BrokerClient(cfg.guard_socket).status()
        except Exception as ex:checks['guard']={'connected':False,'message':str(ex)}
        checks['nft_binary_found']=shutil.which('nft') is not None
        checks['live_packet_test']='not performed by doctor'
    except Exception as ex:checks['configuration_error']=str(ex)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

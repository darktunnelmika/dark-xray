#!/usr/bin/env python3
"""Root explicitly approves data ports; per-client IP policies then sync from DARK."""
import argparse,json,os,pwd,shutil,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from core import Config
from guardd import BrokerConfig

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ports',required=True,help='Explicit comma-separated Xray listener ports, e.g. 2020,443')
    p.add_argument('--verified-direct-sources',action='store_true',help='Confirm Xray log IP equals packet source on THIS host, not a tunnel/proxy peer')
    p.add_argument('--exempt',action='append',default=[])
    a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root approval is required; the web panel cannot approve its own firewall ports')
    if not a.verified_direct_sources:raise SystemExit('Do not enable on an opaque tunnel. Explicit --verified-direct-sources is required.')
    nft=shutil.which('nft')
    if not nft:raise SystemExit('Install nftables first: apt-get install nftables (no rule changes are made by this helper until approval).')
    path=Path('/etc/dark-xray/config.json');cfg=Config.load(path);user=pwd.getpwnam('darkxray')
    try:ports=[int(x.strip()) for x in a.ports.split(',')]
    except ValueError:raise SystemExit('Invalid numeric data ports')
    value={'allowed_uid':user.pw_uid,'allowed_ports':ports,'protected_ports':cfg.protected_ports,
           'direct_source_verified':True,'max_ban_seconds':86400,'exempt_ips':a.exempt,'nft_binary':nft,
           'socket_path':'/run/dark-xray-guard/control.sock'}
    BrokerConfig.from_dict(value)
    print('Installing a separate DARK-only nftables table. No SSH/panel rules and no host ruleset flush.')
    print('Restarting the main DARK service to load direct-source approval WILL interrupt its Xray connections.')
    guardpath=path.with_name('guard.json')
    def write(p,body,mode,gid):
        with tempfile.NamedTemporaryFile(dir=p.parent,delete=False) as f:
            temp=Path(f.name);f.write(json.dumps(body,indent=2).encode());f.flush();os.fsync(f.fileno())
        os.chown(temp,0,gid);os.chmod(temp,mode);os.replace(temp,p)
    write(guardpath,value,0o600,0)
    config=json.loads(path.read_text());config['direct_source_verified']=True
    write(path,config,0o640,user.pw_gid)
    subprocess.run(['systemctl','enable','--now','dark-xray-guard.service'],check=True)
    subprocess.run(['systemctl','restart','dark-xray-guard.service'],check=True)
    subprocess.run(['systemctl','restart','dark-xray.service'],check=True)
    print('Broker configured. In the DARK IP Guard page select enforcement and verify state=applied.')
    print('This command acceptance does not replace a two-source packet test. Broker restart clears only DARK temporary bans.')
if __name__=='__main__':main()

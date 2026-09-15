#!/usr/bin/env python3
"""Obtain/renew a panel TLS certificate using installed Certbot HTTP-01 on port 80.

No reverse proxy and no other panel is required. Existing web servers are never
stopped to free a port. Certificate renewal restarts DARK, including its Xray
child; the guide explicitly documents that interruption.
"""
import argparse,json,os,pwd,re,shutil,sqlite3,ssl,subprocess,sys,tempfile
from pathlib import Path

CONF=Path('/etc/dark-xray');SOURCE=CONF/'tls-source.json'

def write(path,raw,mode=0o640):
    group=pwd.getpwnam('darkxray').pw_gid
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name);f.write(raw);f.flush();os.fsync(f.fileno())
    os.chmod(tmp,mode);os.chown(tmp,0,group);os.replace(tmp,path)

def copy_pair(lineage):
    cert,key=lineage/'fullchain.pem',lineage/'privkey.pem'
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    dest=CONF/'tls';dest.mkdir(mode=0o750,exist_ok=True);os.chmod(dest,0o750);os.chown(dest,0,pwd.getpwnam('darkxray').pw_gid)
    write(dest/'cert.pem',cert.read_bytes());write(dest/'key.pem',key.read_bytes())
    return str(dest/'cert.pem'),str(dest/'key.pem')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain');p.add_argument('--email');p.add_argument('--port',type=int,default=2087)
    p.add_argument('--agree-tos',action='store_true');p.add_argument('--renew',action='store_true')
    a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root is required for certificate provisioning, not normal panel operation')
    if a.renew:
        state=json.loads(SOURCE.read_text());copy_pair(Path(state['lineage']))
        subprocess.run(['systemctl','restart','dark-xray.service'],check=True);return
    if not a.domain or not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}',a.domain):
        raise SystemExit('Use a valid ASCII domain without protocol, wildcard, path or port')
    if not a.email or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',a.email) or not a.agree_tos:
        raise SystemExit('--email and explicit --agree-tos are required for Certbot')
    if not 1024<=a.port<=65535:raise SystemExit('Use a panel port from 1024 to 65535; data port 443 stays available')
    certbot=shutil.which('certbot')
    if not certbot:raise SystemExit('Install certbot first. This command does not install packages silently.')
    config=json.loads((CONF/'config.json').read_text())
    blocked=set(config.get('protected_ports',[]))-{config.get('bind_port',2087)}
    if a.port in blocked|{22,config['xray_api_port']}:raise SystemExit('Panel/core/SSH collision')
    if subprocess.run(['systemctl','cat','certbot.timer'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        raise SystemExit('Packaged certbot.timer is required; configure it before certificate provisioning')
    with sqlite3.connect('file:/var/lib/dark-xray/dark.sqlite3?mode=ro',uri=True) as db:
        if any(json.loads(r[0])['port']==a.port for r in db.execute('SELECT body FROM core_inbounds')):
            raise SystemExit('Panel port is already assigned to a data inbound')
    certname='dark-xray-'+a.domain.lower();lineage=Path('/etc/letsencrypt/live')/certname
    subprocess.run([certbot,'certonly','--non-interactive','--standalone','--preferred-challenges','http',
                    '--http-01-port','80','--agree-tos','--email',a.email,'--cert-name',certname,'-d',a.domain],check=True)
    cert,key=copy_pair(lineage)
    config.update(public_origin=f'https://{a.domain}:{a.port}',bind_host='0.0.0.0',bind_port=a.port,
                  secure_cookie=True,tls_certificate=cert,tls_private_key=key)
    config['protected_ports']=sorted(set(config.get('protected_ports',[])+[22,a.port,config['xray_api_port']]))
    guard=CONF/'guard.json'
    if guard.exists():
        g=json.loads(guard.read_text());g['protected_ports']=config['protected_ports']
        g['allowed_ports']=[x for x in g['allowed_ports'] if x not in g['protected_ports']]
        if not g['allowed_ports']:
            raise SystemExit('TLS obtained, but config not activated: guard would have no approved data ports. Review guard.json.')
        write(guard,json.dumps(g,indent=2).encode(),0o600)
    write(CONF/'config.json',json.dumps(config,indent=2).encode())
    write(SOURCE,json.dumps({'lineage':str(lineage),'domain':a.domain},indent=2).encode(),0o600)
    hooks=Path('/etc/letsencrypt/renewal-hooks/deploy');hooks.mkdir(parents=True,exist_ok=True)
    hook=hooks/'dark-xray-panel'
    content=f'''#!/bin/sh
set -eu
if [ "${{RENEWED_LINEAGE:-}}" = "{lineage}" ]; then
 /opt/dark-xray/.venv/bin/python /opt/dark-xray/tools/domain.py --renew
fi
'''
    write(hook,content.encode(),0o750)
    # Certbot's packaged timer, where present, supplies scheduling; no hidden cron.
    subprocess.run(['systemctl','enable','--now','certbot.timer'],check=True)
    if guard.exists():subprocess.run(['systemctl','restart','dark-xray-guard.service'],check=True)
    subprocess.run(['systemctl','restart','dark-xray.service'],check=True)
    panel_path=str(config.get('panel_path','/'));print('Panel TLS enabled:',config['public_origin']+(panel_path if panel_path!='/' else '')+'/')
    print('Renewal hook installed. Renewal restarts DARK and can interrupt Xray sessions.')
if __name__=='__main__':main()

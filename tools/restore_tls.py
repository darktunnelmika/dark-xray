#!/usr/bin/env python3
"""Root-only, rollback-safe TLS frontend for imported subscription URLs.

Dedicated nginx instance: never changes panel origin/TLS, global nginx sites,
customer URLs, credentials, quota, mappings or group membership. Standalone
Certbot uses free port 80 only during issuance/renewal. Shared legacy ports are
supported across Restore domains in this instance, not across unrelated apps.
"""
from __future__ import annotations
import argparse, errno, fcntl, ipaddress, json, os, re, shutil, socket, sqlite3, ssl, subprocess, tempfile, time
from pathlib import Path
from urllib.parse import urlsplit
from domain import _prepare_pair

DOMAIN_RE=re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
BASE=Path('/etc/dark-xray-restore')
MAIN=BASE/'nginx.conf'
UNIT=Path('/etc/systemd/system/dark-xray-restore.service')
HOOK=Path('/etc/letsencrypt/renewal-hooks/deploy/65-dark-xray-restore')
SERVICE='dark-xray-restore.service'
MARKER='# DARK XRAY RESTORE MANAGED V1\n'
UNIT_TEXT='''[Unit]
Description=DARK XRAY Restore subscription TLS frontend
After=network-online.target dark-xray.service
Wants=network-online.target
[Service]
Type=forking
PIDFile=/run/dark-xray-restore.pid
ExecStartPre=/usr/sbin/nginx -t -c /etc/dark-xray-restore/nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/dark-xray-restore/nginx.conf
ExecReload=/usr/sbin/nginx -t -c /etc/dark-xray-restore/nginx.conf
ExecReload=/usr/sbin/nginx -s reload -c /etc/dark-xray-restore/nginx.conf
ExecStop=/usr/sbin/nginx -s quit -c /etc/dark-xray-restore/nginx.conf
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
MAIN_TEXT=MARKER+'''user www-data;
worker_processes 2;
pid /run/dark-xray-restore.pid;
error_log /dev/null crit;
events { worker_connections 1024; }
http {
    access_log off;
    server_tokens off;
    ssl_protocols TLSv1.2 TLSv1.3;
    include /etc/dark-xray-restore/sites/*.conf;
}
'''
HOOK_TEXT='''#!/bin/sh
set -eu
if systemctl is-active --quiet dark-xray-restore.service; then
    /usr/sbin/nginx -t -c /etc/dark-xray-restore/nginx.conf
    systemctl reload dark-xray-restore.service
fi
'''

def normalize_domain(value):
    value=value.strip().lower().rstrip('.')
    if not DOMAIN_RE.fullmatch(value):raise ValueError('Invalid Restore domain')
    return value


def plan(config, data, domain):
    domain=normalize_domain(domain)
    cfg=json.loads(Path(config).read_text())
    origin=urlsplit(cfg['public_origin'])
    if origin.scheme not in ('http','https') or not origin.hostname or origin.username or origin.password:
        raise ValueError('Invalid panel origin')
    if origin.hostname.lower()==domain:raise ValueError('Restore domain must not replace the panel domain')
    panel_port=int(cfg.get('bind_port') or origin.port or (443 if origin.scheme=='https' else 80))
    bind=str(cfg.get('bind_host') or '127.0.0.1')
    if bind=='0.0.0.0':bind='127.0.0.1'
    if bind=='::':bind='::1'
    ipaddress.ip_address(bind)
    db_path=Path(data)/'dark.sqlite3'
    if not db_path.is_file():raise ValueError('DARK database not found')
    with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT legacy_url FROM restore_subscriptions WHERE legacy_host=?',(domain,)).fetchall()
        dr=db.execute('SELECT acme_email FROM restore_domains WHERE domain=?',(domain,)).fetchone()
    if not rows:raise ValueError('Import subscriptions for this domain before activating it')
    endpoints={}
    for row in rows:
        u=urlsplit(row[0]);port=u.port or (443 if u.scheme=='https' else 80)
        if u.scheme not in ('http','https') or (u.hostname or '').lower().rstrip('.')!=domain or u.username or u.password:
            raise ValueError('Invalid imported subscription endpoint')
        if not 1<=port<=65535 or port==panel_port:raise ValueError('Legacy port conflicts with the panel or is invalid')
        if port in endpoints and endpoints[port]!=u.scheme:raise ValueError('HTTP and HTTPS cannot share the same legacy port')
        endpoints[port]=u.scheme
    if any(p==80 for p in endpoints):raise ValueError('Legacy port 80 needs a webroot ACME setup; refusing to block standalone renewal')
    if len(endpoints)>32:raise ValueError('Too many original subscription ports')
    host='['+bind+']' if ':' in bind else bind
    tls_name=origin.hostname.lower()
    if not DOMAIN_RE.fullmatch(tls_name):raise ValueError('Panel must use a valid DNS name for verified upstream TLS')
    return {'domain':domain,'endpoints':[{'port':p,'scheme':endpoints[p]} for p in sorted(endpoints)],
        'upstream':origin.scheme+'://'+host+':'+str(panel_port),'upstream_host':tls_name,
        'upstream_authority':origin.netloc,'bind':bind,'panel_port':panel_port,
        'public_address':str(cfg.get('public_address') or ''),'count':len(rows),
        'email':str(dr[0] or '') if dr else ''}


def render_site(p):
    domain=normalize_domain(p['domain']);tls_name=normalize_domain(p['upstream_host'])
    blocks=[]
    for ep in p['endpoints']:
        port=int(ep['port']);secure=ep['scheme']=='https'
        if not 1<=port<=65535:raise ValueError('Invalid port')
        lines=['server {',f'    listen 0.0.0.0:{port}'+(' ssl;' if secure else ';'),
            f'    listen [::]:{port}'+(' ssl;' if secure else ';'),f'    server_name {domain};',
            f'    if ($host != {domain}) {{ return 421; }}']
        if secure:lines += [f'    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;',
            f'    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;']
        lines += ['    location = /.well-known/dark-restore-ready {',
            '        add_header X-Dark-Restore proxy-v1 always;', '        return 204;', '    }',
            '    location / {','        limit_except GET { deny all; }',
            '        proxy_pass '+p['upstream']+';',
            '        proxy_http_version 1.1;', '        proxy_set_header Host $http_host;',
            '        proxy_set_header Connection "";', '        proxy_set_header Authorization "";',
            '        proxy_set_header Cookie "";', '        proxy_set_header X-Forwarded-For $remote_addr;',
            '        proxy_set_header X-Forwarded-Proto $scheme;', '        proxy_pass_request_body off;',
            '        proxy_set_header Content-Length "";', '        proxy_cache off;',
            '        proxy_connect_timeout 5s;', '        proxy_read_timeout 30s;',
            '        proxy_buffering off;']
        if p['upstream'].startswith('https:'):
            lines += ['        proxy_ssl_server_name on;',f'        proxy_ssl_name {tls_name};',
                '        proxy_ssl_verify on;', '        proxy_ssl_verify_depth 5;',
                '        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;']
        lines += ['    }','}'];blocks.append('\n'.join(lines))
    return MARKER+'\n'.join(blocks)+'\n'


def write_atomic(path, text, mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name);f.write(text.encode());f.flush();os.fsync(f.fileno())
    try:os.chmod(tmp,mode);os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def run(*args):subprocess.run(list(args),check=True,timeout=180)


def active():return subprocess.run(['systemctl','is-active','--quiet',SERVICE],check=False).returncode==0


def free_port(port):
    for family,addr in ((socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')):
        with socket.socket(family,socket.SOCK_STREAM) as s:
            try:
                if family==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((addr,port))
            except OSError as ex:
                if family==socket.AF_INET6 and ex.errno in (errno.EAFNOSUPPORT,errno.EADDRNOTAVAIL):continue
                return False
    return True


def request_status(address,port,host,scheme,path='/health',authority=None):
    # Pin address while validating TLS against the intended DNS name.
    with socket.create_connection((address,port),timeout=5) as tcp:
        sock=ssl.create_default_context().wrap_socket(tcp,server_hostname=host) if scheme=='https' else tcp
        with sock:
            sock.sendall(('GET '+path+' HTTP/1.1\r\nHost: '+(authority or host)+'\r\nConnection: close\r\n\r\n').encode('ascii'))
            raw=b''
            while b'\r\n\r\n' not in raw and len(raw)<16384:
                part=sock.recv(4096)
                if not part:break
                raw+=part
    return int(raw.split(b' ',2)[1]),raw


def activate(p,data):
    if os.geteuid()!=0:raise ValueError('Run restore-tls as root')
    if not Path('/usr/sbin/nginx').is_file() or not shutil.which('certbot'):
        raise ValueError('Install prerequisites: apt-get install -y nginx certbot')
    addresses={x[4][0] for x in socket.getaddrinfo(p['domain'],None,type=socket.SOCK_STREAM)}
    local=json.loads(subprocess.check_output(['ip','-j','address','show'],text=True))
    ours={a['local'] for i in local for a in i.get('addr_info',[]) if a.get('local')}
    try:ours.add(str(ipaddress.ip_address(p['public_address'])))
    except ValueError:pass
    if not addresses or not all(ipaddress.ip_address(x).is_global for x in addresses) or not addresses<=ours:
        raise ValueError('Every A/AAAA record must point to this HUB before activation')
    upstream=urlsplit(p['upstream'])
    if request_status(p['bind'],p['panel_port'],p['upstream_host'],upstream.scheme,authority=p['upstream_authority'])[0]!=200:
        raise ValueError('Panel upstream health check failed')
    was_active=active();owned_ports=set()
    if was_active and MAIN.exists() and MAIN.read_text().startswith(MARKER):
        for site in (BASE/'sites').glob('*.conf'):
            raw=site.read_text()
            if raw.startswith(MARKER):owned_ports.update(map(int,re.findall(r'listen 0\.0\.0\.0:(\d+)',raw)))
    for ep in p['endpoints']:
        if not free_port(ep['port']) and ep['port'] not in owned_ports:
            raise ValueError('Legacy port '+str(ep['port'])+' is occupied by another service')
    lineage=Path('/etc/letsencrypt/live')/p['domain']
    if any(ep['scheme']=='https' for ep in p['endpoints']):
        try:_prepare_pair(lineage,p['domain']);valid=True
        except (OSError,ValueError,ssl.SSLError):valid=False
        if not valid:
            if not free_port(80):raise ValueError('HTTP-01 needs free port 80; no existing service was stopped')
            args=['certbot','certonly','--standalone','--non-interactive','--agree-tos','--keep-until-expiring',
                '--cert-name',p['domain'],'-d',p['domain']]
            args += ['--email',p['email']] if p['email'] else ['--register-unsafely-without-email']
            run(*args);_prepare_pair(lineage,p['domain'])
    site=BASE/'sites'/(p['domain']+'.conf')
    for path in (MAIN,site):
        if path.exists() and not path.read_text().startswith(MARKER):raise ValueError('Refusing to replace unmanaged configuration')
    if UNIT.exists() and UNIT.read_text()!=UNIT_TEXT:raise ValueError('Refusing to replace a modified Restore service')
    saved={path:path.read_text() if path.exists() else None for path in (MAIN,site,UNIT,HOOK)}
    try:
        write_atomic(MAIN,MAIN_TEXT);write_atomic(site,render_site(p));write_atomic(UNIT,UNIT_TEXT)
        write_atomic(HOOK,HOOK_TEXT,0o755)
        run('/usr/sbin/nginx','-t','-c',str(MAIN));run('systemctl','daemon-reload')
        run('systemctl','reload' if was_active else 'start',SERVICE)
        for ep in p['endpoints']:
            status,raw=request_status('127.0.0.1',ep['port'],p['domain'],ep['scheme'],'/.well-known/dark-restore-ready')
            if status!=204 or b'proxy-v1' not in raw:raise ValueError('Restore listener verification failed')
        run('systemctl','enable',SERVICE)
        if Path('/lib/systemd/system/certbot.timer').exists() or Path('/usr/lib/systemd/system/certbot.timer').exists():
            run('systemctl','enable','--now','certbot.timer')
    except Exception:
        if not was_active:subprocess.run(['systemctl','stop',SERVICE],check=False)
        for path,raw in saved.items():
            if raw is None:path.unlink(missing_ok=True)
            else:write_atomic(path,raw,0o755 if path==HOOK else 0o644)
        subprocess.run(['systemctl','daemon-reload'],check=False)
        if was_active:subprocess.run(['systemctl','reload',SERVICE],check=False)
        raise
    with sqlite3.connect(Path(data)/'dark.sqlite3') as db:
        db.execute('UPDATE restore_domains SET dns_status=?,ssl_status=?,cert_path=?,key_path=?,last_checked=?,updated_at=? WHERE domain=?',
            ('ok','ready',str(lineage/'fullchain.pem'),str(lineage/'privkey.pem'),time.time(),time.time(),p['domain']))
    return {'activated':True,'domain':p['domain'],'ports':[e['port'] for e in p['endpoints']],
        'subscriptions':p['count'],'service':SERVICE,'customer_records_changed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain');parser.add_argument('--config',type=Path,default=Path('/etc/dark-xray/config.json'))
    parser.add_argument('--data',type=Path,default=Path('/var/lib/dark-xray'));parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    p=plan(args.config.resolve(),args.data.resolve(),args.domain)
    if args.dry_run:print(json.dumps({k:v for k,v in p.items() if k!='email'}));return
    if os.geteuid()!=0:parser.error('Root is required for activation')
    with open('/run/dark-xray-restore-setup.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);result=activate(p,args.data)
    print(json.dumps(result))

if __name__=='__main__':main()

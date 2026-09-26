#!/usr/bin/env python3
"""Local DARK XRAY Node Manager.

This is a root-only recovery/maintenance surface for the lightweight Node Agent.
The Hub remains authoritative for user/inbound state. Pairing reset is allowed
only while the Node has no managed state, so a local menu cannot silently detach
an in-service Node.
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

APP=Path('/opt/dark-xray-node')
CONF=Path('/etc/dark-xray-node')
DATA=Path('/var/lib/dark-xray-node')
CONFIG=CONF/'config.json'
GUARD=CONF/'guard.json'
TOKEN=DATA/'token'
NODE_ID=DATA/'node-id'
PAIR=DATA/'pair.json'
PAIR_CONSUMED=DATA/'pair-consumed'
PROFILE=DATA/'node-profile.json'
DB=DATA/'node.sqlite3'
SOURCE=DATA/'installed-source.json'
REPO='https://github.com/darktunnelmika/dark-xray.git'
SERVICE='dark-xray-node.service'
GUARD_SERVICE='dark-xray-node-guard.service'
C='\033[96m';G='\033[92m';Y='\033[93m';R='\033[91m';D='\033[2m';N='\033[0m'


def root():
    if os.geteuid()!=0:raise SystemExit('Run darknode as root')


def run(args,*,check=False,timeout=90,capture=True):
    cp=subprocess.run([str(x) for x in args],text=True,check=False,timeout=timeout,
                      capture_output=capture)
    if check and cp.returncode:
        raise RuntimeError((cp.stderr or cp.stdout or 'command failed')[-1200:])
    return cp


def load(path:Path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        if default is not None:return default
        raise


def account():
    p=pwd.getpwnam('darkxray');return p.pw_uid,p.pw_gid


def atomic_text(path:Path,text:str,mode:int,uid:int,gid:int):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            os.fchmod(out.fileno(),mode);os.fchown(out.fileno(),uid,gid)
            out.write(text);out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def service_active(name=SERVICE):
    return run(['systemctl','is-active','--quiet',name]).returncode==0


def source_info():
    doc=load(SOURCE,{})
    return str(doc.get('version') or 'unknown'),str(doc.get('commit') or '')


def cfg():
    doc=load(CONFIG)
    if not isinstance(doc,dict):raise RuntimeError('Node config is invalid')
    return doc


def pair_status():
    if PAIR.is_file() and not PAIR.is_symlink():return 'available'
    if PAIR_CONSUMED.exists():return 'consumed'
    return 'missing'


def db_scalar(sql:str,default=0):
    if not DB.is_file():return default
    con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=4)
    try:
        row=con.execute(sql).fetchone()
        return row[0] if row else default
    except sqlite3.OperationalError:
        return default
    finally:con.close()


def fresh_state():
    evidence={
        'inbounds':int(db_scalar('SELECT COUNT(*) FROM core_inbounds')),
        'clients':int(db_scalar('SELECT COUNT(*) FROM core_clients')),
        'runtime_inbounds':int(db_scalar('SELECT COUNT(*) FROM node_runtime_inbounds')),
        'runtime_clients':int(db_scalar('SELECT COUNT(*) FROM node_runtime_clients')),
        'applied_revision':int(db_scalar('SELECT COALESCE(MAX(applied_revision),0) FROM node_runtime_state')),
        'commands':int(db_scalar('SELECT COUNT(*) FROM node_runtime_commands')),
    }
    return not any(evidence.values()),evidence


def require_fresh():
    ok,evidence=fresh_state()
    if not ok:
        values=', '.join(f'{k}={v}' for k,v in evidence.items() if v)
        raise RuntimeError('Pairing reset refused: Node has managed state ('+values+'). Remove assignments from the Hub first.')
    return evidence


def encode_pair(doc:dict):
    raw=json.dumps(doc,separators=(',',':'),ensure_ascii=True).encode()
    return 'DXN1.'+base64.urlsafe_b64encode(raw).decode().rstrip('=')


def profile_name(old:dict|None=None):
    if old and isinstance(old.get('name'),str) and old['name'].strip():return old['name'].strip()
    p=load(PROFILE,{})
    if isinstance(p.get('name'),str) and p['name'].strip():return p['name'].strip()
    node=NODE_ID.read_text().strip()
    m=re.fullmatch(r'node-(.+)-[0-9a-f]{6}',node)
    return (m.group(1) if m else node)[:128]


def _write_profile(name:str,priority:int=100,failover=True):
    _,gid=account()
    atomic_text(PROFILE,json.dumps({'name':name,'priority':priority,'failoverEnabled':bool(failover)},indent=2)+'\n',
                0o640,0,gid)


def _issue_pair_stopped(name:str|None=None):
    require_fresh()
    uid,gid=account()
    conf=cfg()
    old=load(PAIR,{}) if PAIR.is_file() and not PAIR.is_symlink() else {}
    node_id=NODE_ID.read_text(encoding='utf-8').strip()
    name=(name or profile_name(old)).strip()
    if not 1<=len(name)<=128:raise RuntimeError('Node display name must be 1-128 characters')
    p=load(PROFILE,{})
    priority=int(old.get('priority',p.get('priority',100)))
    failover=bool(old.get('failoverEnabled',p.get('failoverEnabled',True)))
    token='dkn_'+secrets.token_urlsafe(48)
    payload={'schema':1,'nodeId':node_id,'name':name,'origin':conf['public_origin'],'token':token,
             'dataAddress':conf['public_address'],'priority':priority,'failoverEnabled':failover}
    code=encode_pair(payload)
    atomic_text(TOKEN,token+'\n',0o600,uid,gid)
    atomic_text(PAIR,json.dumps({**payload,'pairCode':code,'sensitive':True,'displayedOnce':True},indent=2)+'\n',
                0o600,uid,gid)
    PAIR_CONSUMED.unlink(missing_ok=True)
    _write_profile(name,priority,failover)
    return code


def issue_pair(name:str|None=None):
    root();require_fresh()
    run(['systemctl','stop',SERVICE],check=True,timeout=30)
    try:code=_issue_pair_stopped(name)
    finally:run(['systemctl','start',SERVICE],check=True,timeout=30)
    return code


def invalidate_pair():
    root();require_fresh();uid,gid=account()
    run(['systemctl','stop',SERVICE],check=True,timeout=30)
    try:
        token='dkn_'+secrets.token_urlsafe(48)
        atomic_text(TOKEN,token+'\n',0o600,uid,gid)
        PAIR.unlink(missing_ok=True)
        atomic_text(PAIR_CONSUMED,str(time.time())+'\n',0o600,uid,gid)
    finally:run(['systemctl','start',SERVICE],check=True,timeout=30)


def local_health():
    conf=cfg();u=urlsplit(conf['public_origin']);port=int(conf.get('bind_port') or u.port or 443)
    token=TOKEN.read_text(encoding='utf-8').strip()
    ctx=ssl._create_unverified_context()
    con=http.client.HTTPSConnection('127.0.0.1',port,context=ctx,timeout=4)
    try:
        con.request('GET','/node/api/health',headers={'Host':u.netloc,'Authorization':'Bearer '+token})
        res=con.getresponse();raw=res.read(1024*1024)
        if res.status!=200:raise RuntimeError('local health HTTP '+str(res.status))
        return json.loads(raw)
    finally:con.close()


def listening(port:int):
    cp=run(['ss','-lnt']);needle=':'+str(port)
    return any(needle in line for line in cp.stdout.splitlines())


def certificate_info():
    conf=cfg();path=Path(conf.get('tls_certificate') or '')
    if not path.is_file():return {'ok':False,'error':'certificate missing'}
    try:
        doc=ssl._ssl._test_decode_cert(str(path))
        expiry=doc.get('notAfter','');seconds=ssl.cert_time_to_seconds(expiry)-time.time()
        return {'ok':seconds>0,'notAfter':expiry,'days':int(seconds//86400)}
    except Exception as ex:return {'ok':False,'error':type(ex).__name__}


def summary():
    conf=cfg();u=urlsplit(conf['public_origin']);version,commit=source_info()
    health=None;error=''
    try:health=local_health()
    except Exception as ex:error=type(ex).__name__+': '+str(ex)
    fresh,evidence=fresh_state()
    return {'version':version,'commit':commit,'origin':conf.get('public_origin',''),
            'address':conf.get('public_address',''),'port':int(conf.get('bind_port') or u.port or 443),
            'agent':service_active(),'guard':service_active(GUARD_SERVICE),'pair':pair_status(),
            'fresh':fresh,'evidence':evidence,'health':health,'health_error':error,'cert':certificate_info()}


def print_status():
    s=summary()
    print(f'{C}DARK XRAY · NODE STATUS{N}')
    print('Version :',s['version'],s['commit'][:12] if s['commit'] else '')
    print('Origin  :',s['origin'])
    print('Address :',s['address'])
    print('Port    :',s['port'],'LISTEN' if listening(s['port']) else 'NOT LISTENING')
    print('Agent   :',f'{G}ONLINE{N}' if s['agent'] else f'{R}OFFLINE{N}')
    print('Guard   :',f'{G}ONLINE{N}' if s['guard'] else f'{R}OFFLINE{N}')
    print('Pairing :',s['pair'])
    print('Fresh   :','yes' if s['fresh'] else 'no')
    if s['health']:
        h=s['health'];core=h.get('core',{})
        print('Xray    :',core.get('state') or ('running' if core.get('running') else 'stopped'))
        print('Install :',h.get('installation_id',''))
    else:print('Health  :',f'{R}{s["health_error"]}{N}')
    cert=s['cert']
    print('TLS     :',f'{cert.get("days","?")} days' if cert.get('ok') else f'{R}{cert.get("error","expired")}{N}')


def show_pair():
    state=pair_status()
    print('Pair state:',state)
    if state=='available':
        doc=load(PAIR)
        print('Node :',doc.get('name'),doc.get('nodeId'))
        print('Origin:',doc.get('origin'))
        print('\nPAIR CODE (sensitive):\n'+str(doc.get('pairCode') or ''))
    elif state=='consumed':
        print('Pair Code was consumed/invalidated. Use "New Pair Code" only on a fresh unassigned Node.')


def open_firewall(port:int):
    if shutil.which('ufw') and run(['ufw','status']).stdout.startswith('Status: active'):
        run(['ufw','allow',f'{port}/tcp'],check=True)
        run(['ufw','reload'],check=True)
    if shutil.which('firewall-cmd') and service_active('firewalld.service'):
        run(['firewall-cmd','--permanent','--add-port',f'{port}/tcp'],check=True)
        run(['firewall-cmd','--reload'],check=True)


def change_port(port:int):
    root();require_fresh()
    if not 1024<=port<=65535 or port in {10085}:raise RuntimeError('Use a free Node control port between 1024 and 65535')
    conf=cfg();old=int(conf.get('bind_port') or urlsplit(conf['public_origin']).port or 443)
    if port==old:return _issue_pair_stopped()
    run(['systemctl','stop',SERVICE],check=True,timeout=30)
    run(['systemctl','stop',GUARD_SERVICE],check=False,timeout=30)
    try:
        u=urlsplit(conf['public_origin']);host=u.hostname or ''
        if ':' in host and not host.startswith('['):host='['+host+']'
        conf['bind_port']=port
        conf['public_origin']='https://'+host+('' if port==443 else ':'+str(port))
        protected=[int(x) for x in conf.get('protected_ports',[]) if int(x)!=old]
        conf['protected_ports']=sorted(set(protected+[22,10085,port]))
        _,gid=account()
        atomic_text(CONFIG,json.dumps(conf,indent=2)+'\n',0o640,0,gid)
        guard=load(GUARD,{})
        gp=[int(x) for x in guard.get('protected_ports',[]) if int(x)!=old]
        guard['protected_ports']=sorted(set(gp+[22,10085,port]))
        atomic_text(GUARD,json.dumps(guard,indent=2)+'\n',0o600,0,0)
        code=_issue_pair_stopped()
        open_firewall(port)
    finally:
        run(['systemctl','start',GUARD_SERVICE],check=True,timeout=30)
        run(['systemctl','start',SERVICE],check=True,timeout=30)
    return code


def set_domain(domain:str,email:str):
    root();require_fresh()
    domain=domain.strip().lower();email=email.strip()
    if not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}',domain):
        raise RuntimeError('Invalid DNS hostname')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):raise RuntimeError('Invalid ACME email')
    conf=cfg();address=str(conf.get('public_address') or '')
    answers={x[4][0] for x in socket.getaddrinfo(domain,None,socket.AF_INET,socket.SOCK_STREAM)}
    if address and address not in answers:raise RuntimeError('DNS does not point to this Node IPv4: '+address)
    if not shutil.which('certbot'):raise RuntimeError('certbot is not installed')
    open_firewall(80)
    cert_name='dark-xray-node-'+domain
    cp=run(['certbot','certonly','--standalone','--non-interactive','--agree-tos','--preferred-challenges','http',
            '--email',email,'--cert-name',cert_name,'-d',domain],timeout=180)
    if cp.returncode:raise RuntimeError((cp.stderr or cp.stdout or 'Certbot failed')[-1200:])
    live=Path('/etc/letsencrypt/live')/cert_name
    if not (live/'fullchain.pem').is_file() or not (live/'privkey.pem').is_file():raise RuntimeError('Certificate files missing')
    run(['systemctl','stop',SERVICE],check=True,timeout=30)
    try:
        _,gid=account();tls=CONF/'tls';tls.mkdir(mode=0o750,exist_ok=True);os.chmod(tls,0o750);os.chown(tls,0,gid)
        shutil.copy2(live/'fullchain.pem',tls/'cert.pem');shutil.copy2(live/'privkey.pem',tls/'key.pem')
        for p in (tls/'cert.pem',tls/'key.pem'):
            os.chmod(p,0o640);os.chown(p,0,gid)
        u=urlsplit(conf['public_origin']);port=int(conf.get('bind_port') or u.port or 443)
        conf['public_origin']='https://'+domain+('' if port==443 else ':'+str(port))
        atomic_text(CONFIG,json.dumps(conf,indent=2)+'\n',0o640,0,gid)
        hook_dir=Path('/etc/letsencrypt/renewal-hooks/deploy');hook_dir.mkdir(parents=True,exist_ok=True)
        for old in hook_dir.glob('dark-xray-node-*'):
            if old.is_file() and old.name!='dark-xray-node-'+domain:old.unlink()
        hook=hook_dir/('dark-xray-node-'+domain)
        script=f'''#!/usr/bin/env bash
set -Eeuo pipefail
install -o root -g darkxray -m 0640 "{live}/fullchain.pem" /etc/dark-xray-node/tls/cert.pem
install -o root -g darkxray -m 0640 "{live}/privkey.pem" /etc/dark-xray-node/tls/key.pem
systemctl try-restart dark-xray-node.service
'''
        atomic_text(hook,script,0o750,0,0)
        code=_issue_pair_stopped()
    finally:run(['systemctl','start',SERVICE],check=True,timeout=30)
    return code


def diagnose():
    s=summary();conf=cfg();u=urlsplit(conf['public_origin'])
    checks=[]
    def add(name,ok,detail=''):checks.append((name,bool(ok),str(detail)))
    add('Agent service',s['agent'])
    add('Guard service',s['guard'])
    add('Control port listening',listening(s['port']),s['port'])
    add('Local authenticated health',bool(s['health']),s['health_error'])
    try:
        ips={x[4][0] for x in socket.getaddrinfo(u.hostname,None,socket.AF_INET,socket.SOCK_STREAM)}
        add('DNS resolves',True,','.join(sorted(ips)))
        add('DNS contains public address',s['address'] in ips,s['address'])
    except Exception as ex:add('DNS resolves',False,type(ex).__name__)
    add('TLS certificate',s['cert'].get('ok'),s['cert'])
    add('Runtime permissions',os.access(APP,os.X_OK) and (CONF/'tls').is_dir())
    print(f'{C}DARK NODE DIAGNOSTICS{N}')
    for name,ok,detail in checks:print(f'[{G+"PASS"+N if ok else R+"FAIL"+N}] {name} {D}{detail}{N}')
    return all(x[1] for x in checks)


def repair():
    root();uid,gid=account()
    os.chmod(APP,0o755)
    os.chmod(CONF,0o750);os.chown(CONF,0,gid)
    tls=CONF/'tls'
    if tls.exists():os.chmod(tls,0o750);os.chown(tls,0,gid)
    os.chmod(DATA,0o700);os.chown(DATA,uid,gid)
    for p in (TOKEN,NODE_ID,PAIR):
        if p.exists() and not p.is_symlink():os.chmod(p,0o600);os.chown(p,uid,gid)
    if PROFILE.exists() and not PROFILE.is_symlink():os.chmod(PROFILE,0o640);os.chown(PROFILE,0,gid)
    if CONFIG.exists():os.chmod(CONFIG,0o640);os.chown(CONFIG,0,gid)
    if GUARD.exists():os.chmod(GUARD,0o600);os.chown(GUARD,0,0)
    run(['systemctl','daemon-reload'],check=True)
    run(['systemctl','restart',GUARD_SERVICE],check=True,timeout=30)
    run(['systemctl','restart',SERVICE],check=True,timeout=30)
    time.sleep(1)
    return diagnose()


def update_latest():
    root()
    cp=run(['git','ls-remote',REPO,'refs/heads/main'],check=True,timeout=30)
    sha=cp.stdout.split()[0].strip().lower()
    if not re.fullmatch(r'[0-9a-f]{40}',sha):raise RuntimeError('Cannot resolve main commit')
    current=source_info()[1]
    if sha==current:
        print('Node is already on latest main:',sha);return
    print('Updating Node to',sha)
    cp=run([APP/'.venv/bin/python',APP/'tools/update_node.py','--ref',sha],capture=False,timeout=300)
    if cp.returncode:raise RuntimeError('Node updater failed')


def backup():
    root();outdir=DATA/'backups';outdir.mkdir(mode=0o700,exist_ok=True);os.chmod(outdir,0o700)
    out=outdir/('node-settings-'+time.strftime('%Y%m%d-%H%M%S')+'.tar.gz')
    with tempfile.TemporaryDirectory(prefix='darknode-backup.') as td:
        stage=Path(td)
        for src,name in [(CONFIG,'config.json'),(GUARD,'guard.json'),(TOKEN,'token'),(NODE_ID,'node-id'),
                         (PAIR,'pair.json'),(PAIR_CONSUMED,'pair-consumed'),(PROFILE,'node-profile.json'),
                         (SOURCE,'installed-source.json')]:
            if src.is_file() and not src.is_symlink():shutil.copy2(src,stage/name)
        tls=CONF/'tls'
        if tls.is_dir() and not tls.is_symlink():
            shutil.copytree(tls,stage/'tls',symlinks=False)
        if DB.is_file() and not DB.is_symlink():
            src=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=10)
            dst=sqlite3.connect(stage/'node.sqlite3')
            try:src.backup(dst)
            finally:dst.close();src.close()
        with tarfile.open(out,'w:gz') as tar:
            for p in sorted(stage.rglob('*')):
                tar.add(p,arcname=p.relative_to(stage),recursive=False)
    os.chmod(out,0o600)
    print('Sensitive root-only backup:',out)


def logs(lines=150):
    run(['journalctl','-u',SERVICE,'-n',str(max(1,min(int(lines),2000))),'--no-pager'],capture=False)


def pause():
    try:input('\nPress Enter to continue...')
    except EOFError:pass


def pair_menu():
    while True:
        os.system('clear')
        print(f'{C}DARK XRAY · PAIR CODE MANAGER{N}\n')
        fresh,evidence=fresh_state()
        print('State:',pair_status(),'| Fresh:',fresh)
        if not fresh:print(f'{Y}Managed state: {evidence}{N}')
        print('\n1) Show Pair Code / status')
        print('2) Invalidate old code + issue NEW Pair Code')
        print('3) Invalidate current Pair Code only')
        print('0) Back')
        ch=input('\nSelect: ').strip()
        try:
            if ch=='1':show_pair();pause()
            elif ch=='2':
                if input('Type RESET to invalidate the old Hub credential: ').strip()!='RESET':continue
                name=input('Node display name (Enter = keep current): ').strip() or None
                code=issue_pair(name)
                print(f'\n{G}NEW PAIR CODE{N}\n{code}');pause()
            elif ch=='3':
                if input('Type INVALIDATE: ').strip()!='INVALIDATE':continue
                invalidate_pair();print(f'{G}Pair Code invalidated.{N}');pause()
            elif ch=='0':return
        except Exception as ex:print(f'{R}{type(ex).__name__}: {ex}{N}');pause()


def connection_menu():
    while True:
        os.system('clear');conf=cfg()
        print(f'{C}DARK XRAY · CONNECTION SETTINGS{N}\n')
        print('Origin :',conf.get('public_origin'))
        print('Address:',conf.get('public_address'))
        print('Port   :',conf.get('bind_port'))
        print('\n1) Change control port + issue new Pair Code')
        print('2) Change HTTPS domain + obtain SSL + issue new Pair Code')
        print('0) Back')
        ch=input('\nSelect: ').strip()
        try:
            if ch=='1':
                port=int(input('New port (example 8443): ').strip())
                if input('Type CHANGE to detach any old Hub credential: ').strip()!='CHANGE':continue
                code=change_port(port);print(f'\n{G}NEW PAIR CODE{N}\n{code}');pause()
            elif ch=='2':
                domain=input('New DNS hostname: ').strip();email=input('ACME email: ').strip()
                if input('Type CHANGE to continue: ').strip()!='CHANGE':continue
                code=set_domain(domain,email);print(f'\n{G}NEW PAIR CODE{N}\n{code}');pause()
            elif ch=='0':return
        except Exception as ex:print(f'{R}{type(ex).__name__}: {ex}{N}');pause()


def menu():
    root()
    while True:
        os.system('clear')
        s=summary()
        print(f'{C}╔══════════════════════════════════════╗')
        print('║       DARK XRAY · NODE MANAGER      ║')
        print(f'╚══════════════════════════════════════╝{N}')
        print(f"{s['address']} · :{s['port']} · Agent {'ONLINE' if s['agent'] else 'OFFLINE'} · Pair {s['pair']}")
        print('\n1) Node Status')
        print('2) Pair Code Manager')
        print('3) Connection Settings')
        print('4) Health & Diagnostics')
        print('5) Restart Node Agent')
        print('6) Live/Recent Logs')
        print('7) Update Node from GitHub main')
        print('8) Repair Node')
        print('9) Backup Node settings')
        print('0) Exit')
        ch=input('\nSelect: ').strip()
        try:
            if ch=='1':print_status();pause()
            elif ch=='2':pair_menu()
            elif ch=='3':connection_menu()
            elif ch=='4':diagnose();pause()
            elif ch=='5':run(['systemctl','restart',SERVICE],check=True);print(f'{G}Restarted.{N}');pause()
            elif ch=='6':logs(150);pause()
            elif ch=='7':
                if input('Type UPDATE: ').strip()=='UPDATE':update_latest()
                pause()
            elif ch=='8':
                if input('Type REPAIR: ').strip()=='REPAIR':repair()
                pause()
            elif ch=='9':
                if input('Backup contains private keys/tokens. Type BACKUP: ').strip()=='BACKUP':backup()
                pause()
            elif ch=='0':return
        except KeyboardInterrupt:return
        except Exception as ex:print(f'{R}{type(ex).__name__}: {ex}{N}');pause()


def main():
    p=argparse.ArgumentParser(prog='darknode')
    sub=p.add_subparsers(dest='cmd')
    sub.add_parser('menu');sub.add_parser('status');sub.add_parser('pair-info')
    pn=sub.add_parser('pair-new');pn.add_argument('--name')
    sub.add_parser('pair-invalidate')
    pp=sub.add_parser('port');pp.add_argument('port',type=int)
    pd=sub.add_parser('domain');pd.add_argument('domain');pd.add_argument('--email',required=True)
    sub.add_parser('diagnose');sub.add_parser('repair');sub.add_parser('update');sub.add_parser('backup')
    pl=sub.add_parser('logs');pl.add_argument('lines',nargs='?',type=int,default=150)
    sub.add_parser('restart');sub.add_parser('config')
    a=p.parse_args();cmd=a.cmd or 'menu'
    try:
        if cmd=='menu':menu()
        elif cmd=='status':print_status()
        elif cmd=='pair-info':root();show_pair()
        elif cmd=='pair-new':print(issue_pair(a.name))
        elif cmd=='pair-invalidate':invalidate_pair();print('Pair Code invalidated')
        elif cmd=='port':print(change_port(a.port))
        elif cmd=='domain':print(set_domain(a.domain,a.email))
        elif cmd=='diagnose':raise SystemExit(0 if diagnose() else 2)
        elif cmd=='repair':raise SystemExit(0 if repair() else 2)
        elif cmd=='update':update_latest()
        elif cmd=='backup':backup()
        elif cmd=='logs':logs(a.lines)
        elif cmd=='restart':root();run(['systemctl','restart',SERVICE],check=True)
        elif cmd=='config':root();print(CONFIG.read_text())
    except RuntimeError as ex:raise SystemExit('ERROR: '+str(ex))


if __name__=='__main__':main()

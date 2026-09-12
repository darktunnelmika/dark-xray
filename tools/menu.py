#!/usr/bin/env python3
"""DARK XRAY cyber terminal control center."""
from __future__ import annotations
import json, os, shutil, socket, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
INSTALLED=ROOT==Path('/opt/dark-xray')
COMMAND=Path('/usr/local/bin/darkxray') if INSTALLED else ROOT/'darkxray'
CONFIG=Path(os.environ.get('DARK_CONFIG',str(Path('/etc/dark-xray/config.json') if INSTALLED else ROOT/'config.json')))
DATA=Path(os.environ.get('DARK_DATA',str(Path('/var/lib/dark-xray') if INSTALLED else ROOT/'data')))
VERSION=ROOT/'VERSION'
TTY=sys.stdout.isatty(); COLOR=TTY and not os.environ.get('NO_COLOR')
R='\033[0m' if COLOR else ''; B='\033[1m' if COLOR else ''; D='\033[2m' if COLOR else ''
CY='\033[38;5;51m' if COLOR else ''; BL='\033[38;5;39m' if COLOR else ''; PU='\033[38;5;141m' if COLOR else ''
GR='\033[38;5;46m' if COLOR else ''; YE='\033[38;5;226m' if COLOR else ''; RE='\033[38;5;196m' if COLOR else ''; GY='\033[38;5;245m' if COLOR else ''

def clear():
    if TTY: print('\033[2J\033[H',end='')
def run(args,capture=False,check=False):
    try:
        return subprocess.run([str(x) for x in args],text=True,check=check,
            stdout=subprocess.PIPE if capture else None,stderr=subprocess.STDOUT if capture else None)
    except (FileNotFoundError,subprocess.CalledProcessError) as e:
        print(f'{RE}Command failed:{R} {args[0]}')
        return e
def exists(name): return shutil.which(name) is not None
def service(name,mode='is-active'):
    if not exists('systemctl'): return 'n/a'
    cp=run(['systemctl',mode,name],capture=True)
    if not hasattr(cp,'returncode'): return 'unknown'
    out=((cp.stdout or '').strip().splitlines() or ['unknown'])[0]
    if out in {'active','inactive','failed','activating','deactivating','enabled','disabled','static','masked'}: return out
    return 'n/a'
def badge(s):
    return f'{GR}● ONLINE{R}' if s=='active' else f'{RE}● {s.upper()}{R}' if s in {'inactive','failed','dead'} else f'{YE}● {s.upper()}{R}'
def cfg():
    try:return json.loads(CONFIG.read_text())
    except Exception:return {}
def ver():
    try:return VERSION.read_text().strip()
    except Exception:return 'unknown'
def human(n):
    n=float(n)
    for u in ('B','KB','MB','GB','TB'):
        if abs(n)<1024:return f'{n:.1f}{u}'
        n/=1024
    return f'{n:.1f}PB'
def metrics():
    try:
        import psutil
        vm=psutil.virtual_memory(); ds=psutil.disk_usage('/')
        return f'{psutil.cpu_percent(interval=.12):.0f}%',f'{human(vm.used)}/{human(vm.total)} ({vm.percent:.0f}%)',f'{human(ds.used)}/{human(ds.total)} ({ds.percent:.0f}%)'
    except Exception:return 'n/a','n/a','n/a'
def endpoint(c):
    return str(c.get('public_origin') or f"http://{c.get('bind_host','127.0.0.1')}:{c.get('bind_port',2087)}")
def header(title='CONTROL CENTER',sub=''):
    clear(); w=76
    print(f'{CY}╔'+('═'*w)+f'╗{R}')
    print(f'{CY}║{R}{B}{"D A R K   X R A Y".center(w)}{R}{CY}║{R}')
    print(f'{CY}║{R}{PU}{"CYBER CONTROL CENTER".center(w)}{R}{CY}║{R}')
    print(f'{CY}╠'+('═'*w)+f'╣{R}')
    print(f'{CY}║{R} {B}{title:<74}{R}{CY}║{R}')
    if sub: print(f'{CY}║{R} {GY}{sub[:74]:<74}{R}{CY}║{R}')
    print(f'{CY}╚'+('═'*w)+f'╝{R}')
def pause():
    try:input(f'\n{GY}Press Enter to return...{R}')
    except (EOFError,KeyboardInterrupt):pass
def ask(prompt,default=''):
    try:
        x=input(f'{prompt}{f" [{default}]" if default else ""}: ').strip(); return x or default
    except (EOFError,KeyboardInterrupt):return ''
def confirm(text,token='YES'):
    try:return input(f'{YE}{text}{R}\nType {token}: ').strip()==token
    except (EOFError,KeyboardInterrupt):return False
def need_root():
    if os.geteuid()==0:return True
    print(f'{RE}Root is required for this action.{R}');pause();return False
def atomic_config(value):
    if not need_root():return False
    CONFIG.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.dark-config-',dir=CONFIG.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(name,0o640)
        if CONFIG.exists():st=CONFIG.stat();os.chown(name,st.st_uid,st.st_gid)
        os.replace(name,CONFIG);return True
    finally:
        if os.path.exists(name):os.unlink(name)
def inbound_ports():
    db=DATA/'dark.sqlite3'
    if not db.exists():return set()
    out=set()
    try:
        with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as con:
            for (body,) in con.execute('SELECT body FROM core_inbounds'):
                try:out.add(int(json.loads(body).get('port')))
                except Exception:pass
    except Exception:pass
    return out
def port_busy(port):
    if not exists('ss'):return False
    cp=run(['ss','-ltnH'],capture=True); needle=f':{port}'
    return bool(cp and any(line.split()[3].endswith(needle) for line in (cp.stdout or '').splitlines() if len(line.split())>=4))

def dashboard():
    c=cfg(); cpu,ram,disk=metrics(); p=service('dark-xray.service'); g=service('dark-xray-guard.service')
    header('LIVE STATUS',endpoint(c))
    print(f'''\n  Panel            {badge(p)}
  IP Guard         {badge(g)}
  Autostart        {service('dark-xray.service','is-enabled')}
  Version          {ver()}
  CPU              {cpu}
  RAM              {ram}
  Disk             {disk}
  Host             {socket.gethostname()}
  Panel port       {c.get('bind_port','?')}
  Xray API         {c.get('xray_api_port','?')}
  Xray binary      {c.get('xray_binary','not configured')}
  Writes           {'enabled' if c.get('writes_enabled',True) else 'disabled'}
''')
    pause()

def service_menu():
    while True:
        header('SERVICE CONTROL',f"Panel {service('dark-xray.service')} • Guard {service('dark-xray-guard.service')}")
        print('''\n  1) Status details
  2) Start DARK XRAY
  3) Stop DARK XRAY
  4) Restart DARK XRAY + Xray
  5) Enable autostart
  6) Disable autostart
  7) Show systemd unit
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if not need_root():continue
        if x=='1':run(['systemctl','status','dark-xray.service','--no-pager']);pause()
        elif x=='2':run(['systemctl','start','dark-xray.service']);pause()
        elif x in {'3','4'}:
            if confirm('Active proxy sessions will be interrupted.'):
                run(['systemctl','stop' if x=='3' else 'restart','dark-xray.service']);pause()
        elif x=='5':run(['systemctl','enable','--now','dark-xray.service']);pause()
        elif x=='6':
            if confirm('Disable automatic startup?'):run(['systemctl','disable','dark-xray.service']);pause()
        elif x=='7':run(['systemctl','cat','dark-xray.service','--no-pager']);pause()

def settings_menu():
    while True:
        c=cfg();header('PANEL SETTINGS',endpoint(c))
        print('''\n  1) Safe configuration summary
  2) Change panel port
  3) Change public proxy address
  4) Reset owner password
  5) Show access URL / SSH tunnel
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x=='1':
            hidden={'tls_private_key','privateKey','password','token','api_key'}
            safe={k:('*** hidden ***' if any(h.lower() in k.lower() for h in hidden) else v) for k,v in c.items()}
            print(json.dumps(safe,indent=2,ensure_ascii=False));pause()
        elif x=='2':
            if not need_root():continue
            val=ask('New panel port',str(c.get('bind_port',2087)))
            if not val.isdigit() or not 1024<=int(val)<=65535:print(f'{RE}Invalid nonprivileged port.{R}');pause();continue
            new=int(val); old=int(c.get('bind_port',2087)); reserved={int(c.get('xray_api_port',10085)),22}|inbound_ports()
            if new in reserved:print(f'{RE}Port conflicts with SSH/Xray API/data inbound.{R}');pause();continue
            if new!=old and port_busy(new):print(f'{RE}Port is already listening.{R}');pause();continue
            if not confirm(f'Change panel port {old} → {new} and restart?'):continue
            c['bind_port']=new
            c['protected_ports']=sorted((set(map(int,c.get('protected_ports',[])))-{old})|{new,22,int(c.get('xray_api_port',10085))})
            origin=str(c.get('public_origin',''))
            if origin.startswith('http://127.0.0.1:'):c['public_origin']=f'http://127.0.0.1:{new}'
            elif origin.startswith('https://'):
                host=origin.split('://',1)[1].split(':',1)[0];c['public_origin']=f'https://{host}:{new}'
            if atomic_config(c):run(['systemctl','restart','dark-xray.service']);print(f'{GR}Port updated.{R}')
            pause()
        elif x=='3':
            if not need_root():continue
            v=ask('Public proxy IP/DNS',str(c.get('public_address','')))
            if not v or any(ch in v for ch in '/?#@ \r\n'):print(f'{RE}Invalid address.{R}');pause();continue
            if confirm(f'Set public proxy address to {v}?'):
                c['public_address']=v
                if atomic_config(c):run(['systemctl','restart','dark-xray.service'])
            pause()
        elif x=='4':
            user=ask('Owner username','dark')
            if user and confirm('Reset owner password and revoke current sessions?'):run([COMMAND,'reset-password','--username',user]);pause()
        elif x=='5':
            port=c.get('bind_port',2087);print('Panel URL:',endpoint(c));print(f'SSH tunnel: ssh -L {port}:127.0.0.1:{port} root@SERVER -p SSH_PORT');pause()

def tls_menu():
    while True:
        c=cfg();header('DOMAIN / TLS',endpoint(c))
        print('''\n  1) Set domain + issue Let's Encrypt TLS
  2) Renew certificate now
  3) Certificate details
  4) Certbot timer status
  5) DNS check
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x=='1':
            if not need_root():continue
            domain=ask('Domain');email=ask('ACME email');port=ask('HTTPS panel port',str(c.get('bind_port',2087)))
            if domain and email and port and confirm('Port 80 must reach this server. Issue and activate TLS?'):
                run([COMMAND,'domain','--domain',domain,'--email',email,'--port',port,'--agree-tos']);pause()
        elif x=='2':
            if need_root() and confirm('Renewal restarts DARK and active sessions may drop.'):run([COMMAND,'domain','--renew']);pause()
        elif x=='3':
            cert=c.get('tls_certificate')
            if cert and Path(cert).exists() and exists('openssl'):run(['openssl','x509','-in',cert,'-noout','-subject','-issuer','-dates','-ext','subjectAltName'])
            else:print('No active certificate.')
            pause()
        elif x=='4':run(['systemctl','status','certbot.timer','--no-pager']);pause()
        elif x=='5':
            d=ask('Domain');run(['getent','ahostsv4',d]) if d else None;pause()

def guard_menu():
    while True:
        header('IP GUARD',f"worker: {service('dark-xray-guard.service')}")
        print('''\n  1) Status
  2) Configure / enable
  3) Restart worker
  4) Stop worker
  5) Guard logs
  6) Safety notes
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x=='1':run(['systemctl','status','dark-xray-guard.service','--no-pager']);pause()
        elif x=='2':
            if not need_root():continue
            ports=ask('Xray DATA ports, comma separated');ex=ask('Optional exempt IP')
            print(f'{YE}Enable only if Xray logs contain the real client packet source on this host.{R}')
            if ports and confirm('I verified direct source IPs. Enable enforcement?'):
                a=[COMMAND,'guard-enable','--ports',ports,'--verified-direct-sources'];a += ['--exempt',ex] if ex else []
                run(a);pause()
        elif x=='3':
            if need_root():run(['systemctl','restart','dark-xray-guard.service']);pause()
        elif x=='4':
            if need_root() and confirm('Stop worker and clear DARK temporary bans?'):run(['systemctl','stop','dark-xray-guard.service']);pause()
        elif x=='5':run(['journalctl','-u','dark-xray-guard.service','-n','120','--no-pager']);pause()
        elif x=='6':print('\n  • Do not enable behind opaque tunnels.\n  • SSH/panel/API ports are protected.\n  • Validate with two source networks before production.\n');pause()

def backup_menu():
    while True:
        header('BACKUP / RECOVERY','Encrypted application backups')
        print('''\n  1) Create encrypted backup
  2) Restore into NEW directory
  3) Show backup directory
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x=='1':
            default=str(DATA/'backups'/f"dark-{time.strftime('%Y%m%d-%H%M%S')}.darkbackup")
            path=ask('Backup file',default);Path(path).parent.mkdir(parents=True,exist_ok=True);run([COMMAND,'backup','--output',path]);pause()
        elif x=='2':
            arc=ask('Backup archive');dst=ask('NEW empty restore directory','/var/lib/dark-xray-restore')
            if arc and confirm('Restore does not overwrite live data. Continue?'):run([COMMAND,'restore','--archive',arc,'--destination',dst]);pause()
        elif x=='3':print(DATA/'backups');pause()

def logs_menu():
    while True:
        header('LOG CENTER','systemd journal + DARK diagnostics')
        print('''\n  1) Panel logs (last 150)
  2) Follow panel logs
  3) Guard logs (last 150)
  4) Follow guard logs
  5) Doctor
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x in {'1','2','3','4'}:
            unit='dark-xray.service' if x in {'1','2'} else 'dark-xray-guard.service';follow=x in {'2','4'}
            args=['journalctl','-u',unit,'-n','40' if follow else '150','-f'] if follow else ['journalctl','-u',unit,'-n','150','--no-pager']
            try:run(args)
            except KeyboardInterrupt:pass
            pause()
        elif x=='5':run([COMMAND,'doctor']);pause()

def system_menu():
    while True:
        header('SYSTEM / NETWORK','No automatic UFW/SSH changes')
        print('''\n  1) Full diagnostics
  2) Enable BBR (fq + bbr)
  3) Listening TCP ports
  4) DARK nftables table
  5) Disk / memory / uptime
  6) Restart networking-independent DARK services
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x=='1':run([COMMAND,'doctor']);pause()
        elif x=='2':
            if need_root() and confirm('Apply persistent fq + BBR sysctl?'):
                Path('/etc/sysctl.d/99-dark-xray-bbr.conf').write_text('net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n')
                run(['sysctl','--system']);pause()
        elif x=='3':run(['ss','-lntp']);pause()
        elif x=='4':run(['nft','list','table','inet','dark_xray']) if exists('nft') else print('nft not installed');pause()
        elif x=='5':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='6':
            if need_root() and confirm('Restart DARK panel and Guard?'):
                run(['systemctl','restart','dark-xray.service']);
                if service('dark-xray-guard.service')=='active':run(['systemctl','restart','dark-xray-guard.service'])
                pause()

def update_menu():
    header('UPDATE DARK XRAY','Safe source update; /etc and /var/lib are preserved')
    print('Current version:',ver())
    print('\nUpdate creates a source rollback archive before replacing application files.')
    if need_root() and confirm('Download latest main branch and update DARK XRAY?'):
        run([COMMAND,'update']);pause()

def repair_menu():
    while True:
        header('REPAIR / MAINTENANCE','Conservative recovery tools')
        print('''\n  1) Doctor
  2) Reload systemd + restart panel
  3) Reinstall systemd unit files from /opt
  4) Show application paths
  5) Uninstall application (PRESERVE data/config)
  0) Back''')
        x=ask('DARK')
        if x=='0':return
        if x=='1':run([COMMAND,'doctor']);pause()
        elif x=='2':
            if need_root() and confirm('Restart panel?'):run(['systemctl','daemon-reload']);run(['systemctl','restart','dark-xray.service']);pause()
        elif x=='3':
            if need_root():
                for n in ('dark-xray.service','dark-xray-guard.service'):
                    shutil.copy2(ROOT/'deploy'/n,Path('/etc/systemd/system')/n)
                run(['systemctl','daemon-reload']);print(f'{GR}Units restored.{R}');pause()
        elif x=='4':print('App:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA);pause()
        elif x=='5':
            if not need_root():continue
            if confirm('Remove application and services, but KEEP /etc/dark-xray and /var/lib/dark-xray?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for p in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):
                    Path(p).unlink(missing_ok=True)
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data preserved.');raise SystemExit(0)

def main():
    while True:
        c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');cpu,ram,_=metrics()
        header('MAIN MENU',f"Panel {p} • Guard {g} • CPU {cpu} • RAM {ram} • {endpoint(c)}")
        print(f'''\n  {CY}01){R} Live Status            {CY}07){R} Log Center
  {CY}02){R} Service Control        {CY}08){R} System / Network
  {CY}03){R} Panel Settings         {CY}09){R} Update DARK XRAY
  {CY}04){R} Domain / TLS            {CY}10){R} Doctor / Diagnostics
  {CY}05){R} IP Guard                {CY}11){R} Repair / Uninstall
  {CY}06){R} Backup / Recovery       {CY}00){R} Exit
''')
        x=ask('DARK').lstrip('0') or '0'
        if x=='0':return
        actions={'1':dashboard,'2':service_menu,'3':settings_menu,'4':tls_menu,'5':guard_menu,'6':backup_menu,'7':logs_menu,'8':system_menu,'9':update_menu,'10':lambda:(run([COMMAND,'doctor']),pause()),'11':repair_menu}
        fn=actions.get(x)
        if fn:fn()
        else:print(f'{RE}Invalid option.{R}');time.sleep(.6)
if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nBye.')

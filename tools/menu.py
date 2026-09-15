#!/usr/bin/env python3
"""DARK VPN / DARK XRAY cyber terminal control center."""
from __future__ import annotations
import json, os, secrets, shutil, socket, sqlite3, subprocess, sys, tempfile, time
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
    if TTY:print('\033[2J\033[H',end='')

def run(args,capture=False,check=False):
    try:
        return subprocess.run([str(x) for x in args],text=True,check=check,
            stdout=subprocess.PIPE if capture else None,stderr=subprocess.STDOUT if capture else None)
    except (FileNotFoundError,subprocess.CalledProcessError) as e:
        print(f'{RE}Command failed:{R} {args[0]}');return e

def run_action(args,success):
    cp=run(args)
    if getattr(cp,'returncode',1)==0:
        print(f'{GR}{success}{R}')
        return True
    print(f'{RE}Action failed. DARK did not assume the requested change succeeded.{R}')
    return False

def exists(name):return shutil.which(name) is not None

def service(name,mode='is-active'):
    if not exists('systemctl'):return 'n/a'
    cp=run(['systemctl',mode,name],capture=True)
    if not hasattr(cp,'returncode'):return 'unknown'
    out=((cp.stdout or '').strip().splitlines() or ['unknown'])[0]
    return out if out in {'active','inactive','failed','activating','deactivating','enabled','disabled','static','masked'} else 'n/a'

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
        vm=psutil.virtual_memory();ds=psutil.disk_usage('/')
        return f'{psutil.cpu_percent(interval=.12):.0f}%',f'{human(vm.used)}/{human(vm.total)} ({vm.percent:.0f}%)',f'{human(ds.used)}/{human(ds.total)} ({ds.percent:.0f}%)'
    except Exception:return 'n/a','n/a','n/a'

def endpoint(c):
    origin=str(c.get('public_origin') or f"http://{c.get('bind_host','127.0.0.1')}:{c.get('bind_port',2087)}").rstrip('/')
    path=str(c.get('panel_path','/') or '/').strip()
    if path!='/' and path.endswith('/'):path=path.rstrip('/')
    return origin+(path if path!='/' else '')+'/'

def valid_panel_path(value):
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    if len(value)>200 or not (value=='/' or bool(__import__('re').fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',value))):return False
    first=value.strip('/').split('/',1)[0].lower() if value!='/' else ''
    return first not in {'api','assets','sub','node','health'}

def header(title='CONTROL CENTER',sub=''):
    clear();w=76
    print(f'{CY}╔'+('═'*w)+f'╗{R}')
    print(f'{CY}║{R}{B}{"D A R K   V P N".center(w)}{R}{CY}║{R}')
    print(f'{CY}║{R}{PU}{"DARK XRAY  •  CYBER CONTROL CENTER".center(w)}{R}{CY}║{R}')
    print(f'{CY}╠'+('═'*w)+f'╣{R}')
    print(f'{CY}║{R} {B}{title[:74]:<74}{R}{CY}║{R}')
    if sub:print(f'{CY}║{R} {GY}{sub[:74]:<74}{R}{CY}║{R}')
    print(f'{CY}╚'+('═'*w)+f'╝{R}')

def title_row(text):print(f'\n  {PU}┌─ {text}{R}')
def item(n,text,hint=''):
    suffix=f' {D}· {hint}{R}' if hint else ''
    print(f'  {CY}[{n:>2}]{R} {text}{suffix}')
def back():print(f'  {GY}[ 0]{R} Back')

def pause():
    try:input(f'\n{GY}Press Enter to return...{R}')
    except (EOFError,KeyboardInterrupt):pass

def ask(prompt,default=''):
    try:
        x=input(f'{prompt}{f" [{default}]" if default else ""}: ').strip();return x or default
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

def db_path():return DATA/'dark.sqlite3'

def owner_usernames():
    path=db_path()
    if not path.is_file():return []
    try:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            return [r[0] for r in db.execute("SELECT id FROM api_admins WHERE role='owner' ORDER BY id")]
    except Exception:return []

def owner_profiles_without_login():
    path=db_path()
    if not path.is_file():return []
    try:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_profiles'").fetchone():return []
            logins={r[0] for r in db.execute("SELECT id FROM api_admins WHERE role='owner'")}
            return [r[0] for r in db.execute('SELECT id FROM owner_profiles ORDER BY id') if r[0] not in logins]
    except Exception:return []


def choose_owner():
    owners=owner_usernames()
    if not owners:
        print(f'{RE}No login Owner exists. Use Create owner login first.{R}')
        return ''
    if len(owners)==1:
        print(f'{GY}Selected login Owner:{R} {owners[0]}')
        return owners[0]
    print(f'{GY}Login Owners:{R}')
    for i,name in enumerate(owners,1):print(f'  [{i}] {name}')
    raw=ask('Select owner','1')
    if raw.isdigit() and 1<=int(raw)<=len(owners):return owners[int(raw)-1]
    print(f'{RE}Invalid Owner selection. Type the number shown in the list.{R}')
    return ''


def inbound_ports():
    path=db_path();out=set()
    if not path.exists():return out
    try:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as con:
            for (body,) in con.execute('SELECT body FROM core_inbounds'):
                try:out.add(int(json.loads(body).get('port')))
                except Exception:pass
    except Exception:pass
    return out

def port_busy(port):
    if not exists('ss'):return False
    cp=run(['ss','-ltnH'],capture=True);needle=f':{port}'
    return bool(cp and any(line.split()[3].endswith(needle) for line in (cp.stdout or '').splitlines() if len(line.split())>=4))

def safe_config_summary():
    c=cfg();hidden={'tls_private_key','privatekey','password','token','api_key'}
    safe={k:('*** hidden ***' if any(h in k.lower() for h in hidden) else v) for k,v in c.items()}
    print(json.dumps(safe,indent=2,ensure_ascii=False))

def show_access():
    c=cfg();port=c.get('bind_port',2087)
    print(f'\n  Panel URL : {endpoint(c)}')
    print(f'  SSH tunnel: ssh -L {port}:127.0.0.1:{port} root@SERVER -p SSH_PORT')

def xray_version():
    c=cfg();binary=str(c.get('xray_binary',''))
    if binary and Path(binary).is_file():run([binary,'version'])
    else:print(f'{YE}Xray binary is not configured or missing.{R}')

def repair_source_permissions():
    if not need_root():return
    if ROOT!=Path('/opt/dark-xray') or ROOT.is_symlink():print(f'{RE}Permission repair is only available for /opt/dark-xray.{R}');return
    os.chmod(ROOT,0o755)
    for name in ('backend','web','tools','deploy'):
        base=ROOT/name
        if not base.is_dir() or base.is_symlink():continue
        os.chmod(base,0o755)
        for p in base.rglob('*'):
            if p.is_symlink():continue
            if p.is_dir():os.chmod(p,0o755)
            elif p.is_file():os.chmod(p,0o644)
    if (ROOT/'darkxray').is_file():os.chmod(ROOT/'darkxray',0o755)
    print(f'{GR}Application source permissions normalized.{R}')


def dashboard():
    c=cfg();cpu,ram,disk=metrics();p=service('dark-xray.service');g=service('dark-xray-guard.service')
    header('DASHBOARD / QUICK STATUS',endpoint(c))
    print(f'''\n  Panel service     {badge(p)}
  IP Guard          {badge(g)}
  Autostart         {service('dark-xray.service','is-enabled')}
  Version           {ver()}
  CPU               {cpu}
  RAM               {ram}
  Disk              {disk}
  Host              {socket.gethostname()}
  Panel port        {c.get('bind_port','?')}
  Xray API          {c.get('xray_api_port','?')}
  Writes            {'enabled' if c.get('writes_enabled',True) else 'disabled'}
''')
    show_access();pause()


def account_menu():
    while True:
        owners=owner_usernames();profiles=owner_profiles_without_login()
        summary='Login Owners: '+(', '.join(owners) if owners else 'none')
        if profiles:summary+=' | Profiles without login: '+', '.join(profiles)
        header('ACCOUNT & ACCESS',summary)
        title_row('OWNER LOGIN ACCOUNTS')
        item(1,'Account security status','sessions · API keys · TOTP')
        item(2,'Create owner login','separate from owner/reseller profile')
        item(3,'Change owner username','select existing login owner')
        item(4,'Change owner password','select existing login owner')
        item(5,'Revoke all owner sessions','force logout browsers')
        title_row('SECURITY RECOVERY')
        item(6,'Revoke all owner API keys','robot/API tokens')
        item(7,'Reset / disable owner TOTP','emergency recovery')
        item(8,'Show Web Account & Security URL')
        back();x=ask('DARK')
        if x=='0':return
        if x=='2':
            print(f'{GY}This creates a real panel LOGIN Owner. An owner profile alone cannot sign in.{R}')
            if profiles:
                print(f'{GY}Profiles without login:{R}')
                for i,name in enumerate(profiles,1):print(f'  [{i}] {name}')
                print('  [0] Use a new username')
                raw=ask('Select profile','1')
                if raw=='0':user=ask('New owner login username')
                elif raw.isdigit() and 1<=int(raw)<=len(profiles):user=profiles[int(raw)-1]
                else:print(f'{RE}Invalid profile selection.{R}');pause();continue
            else:user=ask('New owner login username')
            if user and confirm(f'Create a full Owner login named {user}?','CREATE'):
                run_action([COMMAND,'account','--username',user,'--action','create-owner'],f'Owner login {user} created; password write verified.');pause()
            continue
        user=choose_owner() if x in {'1','3','4','5','6','7'} else ''
        if x=='1' and user:run([COMMAND,'account','--username',user,'--action','status']);pause()
        elif x=='3' and user:
            new=ask('New owner username')
            if new and confirm(f'Rename owner {user} → {new}? Active sessions will be revoked.','RENAME'):
                run([COMMAND,'account','--username',user,'--action','rename','--new-username',new]);pause()
        elif x=='4' and user:
            if confirm(f'Change password for login Owner {user}? Active sessions will be revoked.'):
                run_action([COMMAND,'reset-password','--username',user],f'Password changed for {user}; password write verified and sessions revoked.');pause()
        elif x=='5' and user:
            if confirm(f'Force logout every active session for {user}?','LOGOUT'):
                run([COMMAND,'account','--username',user,'--action','revoke-sessions']);pause()
        elif x=='6' and user:
            if confirm(f'Revoke every active API key owned by {user}?','REVOKE'):
                run([COMMAND,'account','--username',user,'--action','revoke-api-keys']);pause()
        elif x=='7' and user:
            print(f'{YE}This removes TOTP enrollment. Re-enroll 2FA from the Web panel afterwards.{R}')
            if confirm(f'Disable TOTP for {user} and revoke browser sessions?','DISABLE2FA'):
                run([COMMAND,'account','--username',user,'--action','disable-totp']);pause()
        elif x=='8':show_access();print('  Open the panel, then Account & Security.');pause()


def panel_network_menu():
    while True:
        c=cfg();header('PANEL & NETWORK',endpoint(c))
        title_row('PANEL ACCESS')
        item(1,'Safe configuration summary')
        item(2,'Change panel port',str(c.get('bind_port',2087)))
        item(3,'Change public proxy address',str(c.get('public_address','')))
        item(4,'Change panel URI path',str(c.get('panel_path','/')))
        item(11,'Generate random panel URI path','random 96-bit path token')
        item(5,'Show panel URL / SSH tunnel')
        title_row('WEB SETTINGS')
        item(6,'Preview staged Web Settings')
        item(7,'Apply staged Web Settings','root boundary')
        title_row('NETWORK')
        item(8,'Listening TCP ports')
        item(9,'System resources / uptime')
        item(10,'Enable BBR','fq + bbr')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':safe_config_summary();pause()
        elif x=='2':
            val=ask('New panel port',str(c.get('bind_port',2087)))
            if not val.isdigit() or not 1024<=int(val)<=65535:print(f'{RE}Invalid nonprivileged port.{R}');pause();continue
            new=int(val);old=int(c.get('bind_port',2087));reserved={int(c.get('xray_api_port',10085)),22}|inbound_ports()
            if new in reserved:print(f'{RE}Port conflicts with SSH/Xray API/data inbound.{R}');pause();continue
            if new!=old and port_busy(new):print(f'{RE}Port is already listening.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--bind-port',str(new)],f'Panel port staged: {old} → {new}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}')
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):
                        print(f'{GR}Panel URL: {endpoint(cfg())}{R}')
            pause()
        elif x=='3':
            v=ask('Public proxy IP/DNS',str(c.get('public_address','')))
            if not v or any(ch in v for ch in '/?#@ \r\n'):print(f'{RE}Invalid address.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--public-address',v],f'Public proxy address staged: {v}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}')
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='4':
            v=ask('Panel URI path (example /dark-admin)',str(c.get('panel_path','/'))).strip() or '/'
            if v!='/' and v.endswith('/'):v=v.rstrip('/')
            if not valid_panel_path(v):print(f'{RE}Invalid URI path. Use / or /letters-numbers_-/segments.{R}');pause();continue
            if confirm(f'Stage panel URI path {c.get("panel_path","/")} → {v}?'):
                run_action([COMMAND,'stage-runtime','--panel-path',v],f'URI path staged: {v}')
                print(f'{PU}Review ALL pending runtime changes before apply:{R}')
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):
                        print(f'{GR}New panel URL: {endpoint(cfg())}{R}')
                pause()
        elif x=='11':
            v='/dark-'+secrets.token_hex(12)
            if run_action([COMMAND,'stage-runtime','--panel-path',v],f'Random URI path staged: {v}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}')
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):
                        print(f'{GR}Panel URL: {endpoint(cfg())}{R}')
            pause()
        elif x=='5':show_access();pause()
        elif x=='6':run([COMMAND,'settings-apply','--dry-run']);pause()
        elif x=='7':
            if need_root() and confirm('Apply staged Web Settings? Listener changes may restart the panel.'):
                run([COMMAND,'settings-apply']);pause()
        elif x=='8':run(['ss','-lntp']);pause()
        elif x=='9':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='10':
            if need_root() and confirm('Apply persistent fq + BBR sysctl?'):
                Path('/etc/sysctl.d/99-dark-xray-bbr.conf').write_text('net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n')
                run(['sysctl','--system']);pause()


def domain_tls_menu():
    while True:
        c=cfg();header('DOMAIN & TLS',endpoint(c))
        item(1,'Set / change domain + issue Let\'s Encrypt TLS')
        item(2,'Renew certificate now')
        item(3,'Certificate details')
        item(4,'Certbot timer status')
        item(5,'DNS lookup / verification')
        item(6,'Show current access URL')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            if not need_root():continue
            domain=ask('Domain');email=ask('ACME email');port=ask('HTTPS panel port',str(c.get('bind_port',2087)))
            if domain and email and port and confirm('Port 80 must reach this server. Issue and activate TLS?'):
                run([COMMAND,'domain','--domain',domain,'--email',email,'--port',port,'--agree-tos']);pause()
        elif x=='2':
            if need_root() and confirm('Renewal may restart DARK and disconnect the panel briefly.'):run([COMMAND,'domain','--renew'])
            pause()
        elif x=='3':
            cert=c.get('tls_certificate')
            if cert and Path(cert).exists() and exists('openssl'):run(['openssl','x509','-in',cert,'-noout','-subject','-issuer','-dates','-ext','subjectAltName'])
            else:print('No active certificate.')
            pause()
        elif x=='4':run(['systemctl','status','certbot.timer','--no-pager']);pause()
        elif x=='5':
            d=ask('Domain');run(['getent','ahostsv4',d]) if d else None;pause()
        elif x=='6':show_access();pause()


def xray_services_menu():
    while True:
        p=service('dark-xray.service');header('XRAY & SERVICES',f'Panel {p} • Autostart {service("dark-xray.service","is-enabled")}')
        item(1,'Detailed DARK service status')
        item(2,'Start DARK service')
        item(3,'Stop DARK service','active panel sessions disconnect')
        item(4,'Restart DARK + managed Xray')
        item(5,'Enable autostart')
        item(6,'Disable autostart')
        item(7,'Xray binary / version')
        item(8,'Doctor / core diagnostics')
        item(9,'Show systemd unit')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':run(['systemctl','status','dark-xray.service','--no-pager','-l']);pause()
        elif x=='2':
            if need_root():run(['systemctl','start','dark-xray.service']);pause()
        elif x=='3':
            if need_root() and confirm('Stop DARK panel and managed Xray?','STOP'):run(['systemctl','stop','dark-xray.service']);pause()
        elif x=='4':
            if need_root() and confirm('Restart DARK panel and managed Xray?'):run(['systemctl','restart','dark-xray.service']);pause()
        elif x=='5':
            if need_root():run(['systemctl','enable','--now','dark-xray.service']);pause()
        elif x=='6':
            if need_root() and confirm('Disable automatic startup?'):run(['systemctl','disable','dark-xray.service']);pause()
        elif x=='7':xray_version();pause()
        elif x=='8':run([COMMAND,'doctor']);pause()
        elif x=='9':run(['systemctl','cat','dark-xray.service','--no-pager']);pause()


def security_menu():
    while True:
        header('SECURITY & IP GUARD',f'Guard {service("dark-xray-guard.service")}')
        item(1,'IP Guard status')
        item(2,'Configure / enable IP Guard')
        item(3,'Restart Guard worker')
        item(4,'Stop Guard worker / clear temporary bans')
        item(5,'Guard logs')
        item(6,'Show DARK nftables table')
        item(7,'IP Guard safety notes')
        item(8,'Account & Access','username · password · sessions · 2FA')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':run(['systemctl','status','dark-xray-guard.service','--no-pager']);pause()
        elif x=='2':
            if not need_root():continue
            ports=ask('Xray DATA ports, comma separated');ex=ask('Optional exempt IP')
            print(f'{YE}Enable only if Xray logs contain the real client packet source on this host.{R}')
            if ports and confirm('I verified direct source IPs. Enable enforcement?'):
                a=[COMMAND,'guard-enable','--ports',ports,'--verified-direct-sources'];a += ['--exempt',ex] if ex else [];run(a)
            pause()
        elif x=='3':
            if need_root():run(['systemctl','restart','dark-xray-guard.service']);pause()
        elif x=='4':
            if need_root() and confirm('Stop worker and clear DARK temporary bans?'):run(['systemctl','stop','dark-xray-guard.service']);pause()
        elif x=='5':run(['journalctl','-u','dark-xray-guard.service','-n','150','--no-pager']);pause()
        elif x=='6':run(['nft','list','table','inet','dark_xray']) if exists('nft') else print('nft not installed');pause()
        elif x=='7':print('\n  • Never enforce behind opaque/tunnel source addresses.\n  • SSH/panel/API ports remain protected.\n  • Verify direct client source IPs before enforcement.\n  • Shared NAT IP bans can affect multiple clients.\n');pause()
        elif x=='8':account_menu()


def backup_menu():
    while True:
        header('BACKUP & RECOVERY','Encrypted application backup · isolated restore')
        item(1,'Create encrypted full backup')
        item(2,'Restore into NEW isolated directory')
        item(3,'Show backup directory')
        item(4,'List recent source rollback snapshots')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            default=str(DATA/'backups'/f"dark-{time.strftime('%Y%m%d-%H%M%S')}.darkbackup")
            path=ask('Backup file',default);Path(path).parent.mkdir(parents=True,exist_ok=True);run([COMMAND,'backup','--output',path]);pause()
        elif x=='2':
            arc=ask('Backup archive');dst=ask('NEW empty restore directory','/var/lib/dark-xray-restore')
            if arc and confirm('Restore does not overwrite live data. Continue?'):run([COMMAND,'restore','--archive',arc,'--destination',dst]);pause()
        elif x=='3':print(DATA/'backups');pause()
        elif x=='4':
            path=DATA/'backups';rows=sorted(path.glob('pre-update-source-*.tar.gz'),reverse=True)[:15] if path.exists() else []
            print('\n'.join(str(p) for p in rows) if rows else 'No source rollback snapshots.');pause()


def logs_diagnostics_menu():
    while True:
        header('LOGS & DIAGNOSTICS','Live logs · ports · health · resources')
        item(1,'Panel logs','last 150')
        item(2,'Follow panel logs','Ctrl+C to stop')
        item(3,'Guard logs','last 150')
        item(4,'Follow Guard logs','Ctrl+C to stop')
        item(5,'Doctor / full diagnostics')
        item(6,'Listening TCP ports')
        item(7,'Disk / memory / uptime')
        item(8,'Application paths')
        back();x=ask('DARK')
        if x=='0':return
        if x in {'1','2','3','4'}:
            unit='dark-xray.service' if x in {'1','2'} else 'dark-xray-guard.service';follow=x in {'2','4'}
            args=['journalctl','-u',unit,'-n','40','-f'] if follow else ['journalctl','-u',unit,'-n','150','--no-pager']
            try:run(args)
            except KeyboardInterrupt:pass
            pause()
        elif x=='5':run([COMMAND,'doctor']);pause()
        elif x=='6':run(['ss','-lntp']);pause()
        elif x=='7':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='8':print('App:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA,'\nCommand:',COMMAND);pause()


def update_repair_menu():
    while True:
        header('UPDATE & REPAIR',f'Installed version: {ver()}')
        item(1,'Safe update DARK VPN / XRAY','automatic source rollback')
        item(2,'Doctor')
        item(3,'Repair application source permissions','safe 755/644 normalization')
        item(4,'Reload systemd + restart panel')
        item(5,'Reinstall systemd unit files from /opt')
        item(6,'Show installed version / paths')
        item(7,'Uninstall application','PRESERVE config + data')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            print('\nSafe Update preserves /etc and /var/lib and snapshots current source first.')
            if need_root() and confirm('Download latest main branch and update DARK?'):run([COMMAND,'update'])
            pause()
        elif x=='2':run([COMMAND,'doctor']);pause()
        elif x=='3':repair_source_permissions();pause()
        elif x=='4':
            if need_root() and confirm('Reload systemd and restart DARK panel?'):run(['systemctl','daemon-reload']);run(['systemctl','restart','dark-xray.service'])
            pause()
        elif x=='5':
            if need_root():
                for n in ('dark-xray.service','dark-xray-guard.service'):
                    shutil.copy2(ROOT/'deploy'/n,Path('/etc/systemd/system')/n);os.chmod(Path('/etc/systemd/system')/n,0o644)
                run(['systemctl','daemon-reload']);print(f'{GR}Units restored.{R}')
            pause()
        elif x=='6':print('Version:',ver(),'\nApp:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA);pause()
        elif x=='7':
            if not need_root():continue
            if confirm('Remove application/services but KEEP /etc/dark-xray and /var/lib/dark-xray?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for p in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):Path(p).unlink(missing_ok=True)
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data preserved.');raise SystemExit(0)


def main():
    while True:
        c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');cpu,ram,_=metrics()
        header('MAIN CONTROL MATRIX',f'Panel {p} • Guard {g} • CPU {cpu} • RAM {ram} • {endpoint(c)}')
        title_row('QUICK CONTROL')
        item(1,'Dashboard & Quick Status','health · URL · resources')
        item(2,'Account & Access','username · password · sessions · TOTP · API keys')
        item(3,'Panel & Network','port · proxy address · staged settings · BBR')
        item(4,'Domain & TLS','domain · certificate · DNS')
        item(5,'Xray & Services','start · stop · restart · core version')
        item(6,'Security & IP Guard','nftables · limits · safety')
        item(7,'Backup & Recovery','encrypted backup · isolated restore')
        item(8,'Logs & Diagnostics','journal · doctor · ports')
        item(9,'Update & Repair','safe update · permission repair · uninstall')
        print(f'\n  {GY}[ 0]{R} Exit')
        x=ask('DARK').lstrip('0') or '0'
        if x=='0':return
        actions={'1':dashboard,'2':account_menu,'3':panel_network_menu,'4':domain_tls_menu,'5':xray_services_menu,
                 '6':security_menu,'7':backup_menu,'8':logs_diagnostics_menu,'9':update_repair_menu}
        fn=actions.get(x)
        if fn:fn()
        else:print(f'{RE}Invalid option.{R}');time.sleep(.6)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nBye.')

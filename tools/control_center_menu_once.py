#!/usr/bin/env python3
from __future__ import annotations
import re
from pathlib import Path

p=Path('tools/menu.py');s=p.read_text(encoding='utf-8')

def replace_func(name,next_name,new):
    global s
    pat=rf'^def {re.escape(name)}\(.*?(?=^def {re.escape(next_name)}\()'
    replacement=new.rstrip()+'\n\n'
    s2,n=re.subn(pat,lambda _m:replacement,s,count=1,flags=re.M|re.S)
    if n!=1:raise SystemExit(f'{name} boundary mismatch')
    s=s2

def must(old,new,label):
    global s
    if s.count(old)!=1:raise SystemExit(label+' anchor mismatch')
    s=s.replace(old,new,1)

if 'from urllib.parse import urlsplit\n' not in s:
    must('from pathlib import Path\n','from pathlib import Path\nfrom urllib.parse import urlsplit\n','urlsplit import')

if 'def access_mode(c):' not in s:
    anchor='def inbound_ports():\n'
    helpers=r"""def access_mode(c):
    try:return 'Domain + TLS' if urlsplit(str(c.get('public_origin',''))).scheme=='https' else 'SSH / loopback'
    except Exception:return 'unknown'


def protected_ports(c):
    out={int(c.get('xray_api_port',10085))}
    for value in c.get('protected_ports',[]):
        try:
            port=int(value)
            if 1<=port<=65535:out.add(port)
        except Exception:pass
    return out


def runtime_stage_state(c):
    path=db_path()
    if not path.is_file():return 'unknown'
    try:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            row=db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
        if not row:return 'none'
        desired=json.loads(row[0])
        if not isinstance(desired,dict):return 'invalid'
        origin=urlsplit(str(c.get('public_origin','http://127.0.0.1:2087')))
        actual={'access_mode':'domain_tls' if origin.scheme=='https' else 'ssh','bind_port':int(c.get('bind_port',2087)),
                'public_address':str(c.get('public_address','')),'panel_path':str(c.get('panel_path','/')),
                'poll_seconds':int(c.get('poll_seconds',5)),'core_autostart':bool(c.get('core_autostart',False)),
                'domain':(origin.hostname or '') if origin.scheme=='https' else ''}
        changed=[k for k,v in actual.items() if k in desired and desired.get(k)!=v]
        return 'none' if not changed else f'{len(changed)} pending'
    except Exception:return 'unknown'


def safe_enable_bbr():
    if not need_root():return False
    if not exists('sysctl'):
        print(f'{RE}sysctl is not available.{R}');return False
    avail=run(['sysctl','-n','net.ipv4.tcp_available_congestion_control'],capture=True)
    if getattr(avail,'returncode',1)!=0 or 'bbr' not in ((getattr(avail,'stdout','') or '').split()):
        print(f'{RE}Kernel does not advertise BBR. No persistent sysctl file was changed.{R}');return False
    def current(key):
        cp=run(['sysctl','-n',key],capture=True)
        return (getattr(cp,'stdout','') or '').strip() if getattr(cp,'returncode',1)==0 else ''
    old_qdisc=current('net.core.default_qdisc');old_cc=current('net.ipv4.tcp_congestion_control')
    path=Path('/etc/sysctl.d/99-dark-xray-bbr.conf')
    if path.is_symlink():print(f'{RE}Refusing to replace a sysctl symlink.{R}');return False
    previous=path.read_bytes() if path.is_file() else None
    fd,name=tempfile.mkstemp(prefix='.dark-bbr-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write('net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n');f.flush();os.fsync(f.fileno())
        os.chmod(name,0o644);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    cp=run(['sysctl','-p',path],capture=True)
    if getattr(cp,'returncode',1)!=0:
        if previous is None:path.unlink(missing_ok=True)
        else:path.write_bytes(previous)
        if old_qdisc:run(['sysctl','-w',f'net.core.default_qdisc={old_qdisc}'],capture=True)
        if old_cc:run(['sysctl','-w',f'net.ipv4.tcp_congestion_control={old_cc}'],capture=True)
        print(f'{RE}BBR activation failed; previous sysctl file/runtime values were restored.{R}')
        if getattr(cp,'stdout',''):print(cp.stdout.strip())
        return False
    print(f'{GR}BBR enabled with fq; persistent sysctl activation verified.{R}');return True


"""
    if anchor not in s:raise SystemExit('helper anchor mismatch')
    s=s.replace(anchor,helpers+anchor,1)

replace_func('dashboard','account_menu',r"""def dashboard():
    c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');staged=runtime_stage_state(c)
    header('DASHBOARD / QUICK STATUS',endpoint(c))
    print(f'\n  Panel service     {badge(p)}\n'
          f'  IP Guard          {badge(g)}\n'
          f'  Autostart         {service("dark-xray.service","is-enabled")}\n'
          f'  Version           {ver()}\n'
          f'  Access mode       {access_mode(c)}\n'
          f'  Staged settings   {staged}\n'
          f'  Login Owners      {len(owner_usernames())}\n'
          f'  Host              {socket.gethostname()}\n'
          f'  Panel port        {c.get("bind_port","?")}\n'
          f'  Xray API          {c.get("xray_api_port","?")}\n'
          f'  Writes            {"enabled" if c.get("writes_enabled",True) else "disabled"}\n')
    if staged not in {'none','unknown'}:print(f'  {YE}Review staged settings before applying them.{R}')
    show_access();pause()
""")

replace_func('panel_network_menu','domain_tls_menu',r"""def panel_network_menu():
    while True:
        c=cfg();header('PANEL & NETWORK',f'{access_mode(c)} • staged {runtime_stage_state(c)}')
        title_row('PANEL ACCESS')
        item(1,'Safe configuration summary');item(2,'Change panel port',str(c.get('bind_port',2087)));item(3,'Change public address / DNS',str(c.get('public_address','')))
        item(4,'Change panel URI path',str(c.get('panel_path','/')));item(5,'Generate random panel URI path','random 96-bit path token');item(6,'Show panel URL / SSH tunnel')
        title_row('STAGED SETTINGS');item(7,'Preview staged settings');item(8,'Apply ALL staged settings','root boundary · may restart panel')
        title_row('HOST NETWORK');item(9,'Listening TCP ports');item(10,'System resources / uptime');item(11,'Enable BBR safely','verify kernel support · rollback on failure')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':safe_config_summary();pause()
        elif x=='2':
            val=ask('New panel port',str(c.get('bind_port',2087)))
            if not val.isdigit() or not 1024<=int(val)<=65535:print(f'{RE}Invalid nonprivileged port.{R}');pause();continue
            new=int(val);old=int(c.get('bind_port',2087));reserved=(protected_ports(c)-{old})|inbound_ports()
            if new==old:print(f'{GY}Panel is already using port {old}.{R}');pause();continue
            if new in reserved:print(f'{RE}Port conflicts with a protected management/core/data port.{R}');pause();continue
            if port_busy(new):print(f'{RE}Port is already listening.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--bind-port',str(new)],f'Panel port staged: {old} → {new}'):
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.','APPLY'):run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='3':
            v=ask('Public IP/DNS',str(c.get('public_address','')))
            if not v or any(ch in v for ch in '/?#@ \r\n\t'):print(f'{RE}Invalid address.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--public-address',v],f'Public address staged: {v}'):
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.','APPLY'):run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='4':
            v=ask('Panel URI path (example /dark-admin)',str(c.get('panel_path','/'))).strip() or '/'
            if v!='/' and v.endswith('/'):v=v.rstrip('/')
            if not valid_panel_path(v):print(f'{RE}Invalid URI path.{R}');pause();continue
            if v==str(c.get('panel_path','/')):print(f'{GY}Panel already uses that URI path.{R}');pause();continue
            if confirm(f'Stage panel URI path {c.get("panel_path","/")} → {v}?') and run_action([COMMAND,'stage-runtime','--panel-path',v],f'URI path staged: {v}'):
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.','APPLY'):run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='5':
            v='/dark-'+secrets.token_hex(12)
            if run_action([COMMAND,'stage-runtime','--panel-path',v],f'Random URI path staged: {v}'):
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.','APPLY'):run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='6':show_access();pause()
        elif x=='7':run([COMMAND,'settings-apply','--dry-run']);pause()
        elif x=='8':
            if need_root() and confirm('Apply ALL staged Web Settings? Listener/TLS changes may restart the panel.','APPLY'):run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='9':run(['ss','-lntp']) if exists('ss') else print('ss is not installed');pause()
        elif x=='10':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='11':
            if confirm('Enable persistent fq + BBR after verifying kernel support?','BBR'):safe_enable_bbr()
            pause()
""")

replace_func('domain_tls_menu','xray_services_menu',r"""def domain_tls_menu():
    while True:
        c=cfg();header('DOMAIN & TLS',f'{access_mode(c)} • {endpoint(c)}')
        item(1,'Set / change domain + issue Let\'s Encrypt TLS');item(2,'Renew certificate now');item(3,'Certificate details');item(4,'Certbot timer status');item(5,'DNS A / AAAA lookup');item(6,'Show current access URL');item(7,'Run VPS readiness check')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            if not need_root():continue
            domain=ask('Domain');email=ask('ACME email');port=ask('HTTPS panel port',str(c.get('bind_port',2087)))
            if domain and email and port and confirm('Port 80 must reach this server. Issue and activate TLS?','TLS'):run_action([COMMAND,'domain','--domain',domain,'--email',email,'--port',port,'--agree-tos'],'TLS certificate issued and activated.');pause()
        elif x=='2':
            if need_root() and confirm('Renewal may restart DARK briefly.','RENEW'):run_action([COMMAND,'domain','--renew'],'TLS renewal completed.')
            pause()
        elif x=='3':
            cert=c.get('tls_certificate')
            if cert and Path(cert).exists() and exists('openssl'):run(['openssl','x509','-in',cert,'-noout','-subject','-issuer','-dates','-ext','subjectAltName'])
            else:print('No active certificate or openssl is unavailable.')
            pause()
        elif x=='4':run(['systemctl','status','certbot.timer','--no-pager']) if exists('systemctl') else print('systemctl unavailable');pause()
        elif x=='5':
            d=ask('Domain')
            if d:run(['getent','ahosts',d]) if exists('getent') else print('getent is not installed')
            pause()
        elif x=='6':show_access();pause()
        elif x=='7':run([COMMAND,'vps-verify']);pause()
""")

replace_func('xray_services_menu','security_menu',r"""def xray_services_menu():
    while True:
        p=service('dark-xray.service');header('XRAY & SERVICES',f'Panel {p} • Autostart {service("dark-xray.service","is-enabled")}')
        item(1,'Detailed DARK service status');item(2,'Start DARK service');item(3,'Stop DARK service','active sessions disconnect');item(4,'Restart DARK + managed Xray');item(5,'Enable autostart + start now');item(6,'Disable autostart')
        item(7,'Xray binary / version');item(8,'Lock-free live health check');item(9,'Doctor / core diagnostics');item(10,'VPS readiness validation');item(11,'Production data-plane gate','isolated real Xray lab');item(12,'Show systemd unit')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':run(['systemctl','status','dark-xray.service','--no-pager','-l']);pause()
        elif x=='2':
            if need_root():run_action(['systemctl','start','dark-xray.service'],'DARK service started.');pause()
        elif x=='3':
            if need_root() and confirm('Stop DARK panel and managed Xray?','STOP'):run_action(['systemctl','stop','dark-xray.service'],'DARK service stopped.');pause()
        elif x=='4':
            if need_root() and confirm('Restart DARK panel and managed Xray?','RESTART'):run_action(['systemctl','restart','dark-xray.service'],'DARK service restarted.');pause()
        elif x=='5':
            if need_root():run_action(['systemctl','enable','--now','dark-xray.service'],'DARK autostart enabled and service started.');pause()
        elif x=='6':
            if need_root() and confirm('Disable automatic startup?','DISABLE'):run_action(['systemctl','disable','dark-xray.service'],'DARK autostart disabled.');pause()
        elif x=='7':xray_version();pause()
        elif x=='8':run([COMMAND,'check']);pause()
        elif x=='9':run([COMMAND,'doctor']);pause()
        elif x=='10':run([COMMAND,'vps-verify']);pause()
        elif x=='11':
            if confirm('Run isolated production gate using the installed Xray binary?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='12':run(['systemctl','cat','dark-xray.service','--no-pager']);pause()
""")

replace_func('security_menu','backup_menu',r"""def security_menu():
    while True:
        header('SECURITY & IP GUARD',f'Guard {service("dark-xray-guard.service")}')
        item(1,'Broker / IP Guard status');item(2,'Configure / enable IP Guard');item(3,'Clear current DARK temporary bans');item(4,'Restart Guard worker','clears old bans');item(5,'Stop Guard worker','nft timeouts can remain');item(6,'Guard logs');item(7,'Show DARK nftables table','inet dark_xray_ip');item(8,'IP Guard safety notes');item(9,'Account & Access')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            cp=run([COMMAND,'guard-control','status'])
            if getattr(cp,'returncode',1)!=0:run(['systemctl','status','dark-xray-guard.service','--no-pager','-l'])
            pause()
        elif x=='2':
            if not need_root():continue
            ports=ask('Xray DATA ports, comma separated');ex=ask('Optional exempt IP/CIDR');print(f'{YE}Enable only if Xray sees the real packet source.{R}')
            if ports and confirm('I verified direct source IPs. Enable enforcement?','ENFORCE'):
                a=[COMMAND,'guard-enable','--ports',ports,'--verified-direct-sources'];a += ['--exempt',ex] if ex else [];run_action(a,'IP Guard configuration installed.')
            pause()
        elif x=='3':
            if confirm('Clear every current DARK temporary IP ban?','CLEAR'):run_action([COMMAND,'guard-control','clear'],'Current DARK temporary bans cleared.')
            pause()
        elif x=='4':
            if need_root() and confirm('Restart Guard and rebuild its table? Existing bans are cleared.','RESTART'):run_action(['systemctl','restart','dark-xray-guard.service'],'Guard restarted.')
            pause()
        elif x=='5':
            print(f'{YE}Stopping the worker does NOT immediately flush existing nft timeout elements.{R}')
            if need_root() and confirm('Stop Guard worker?','STOP'):run_action(['systemctl','stop','dark-xray-guard.service'],'Guard worker stopped.')
            pause()
        elif x=='6':run(['journalctl','-u','dark-xray-guard.service','-n','150','--no-pager']);pause()
        elif x=='7':run(['nft','list','table','inet','dark_xray_ip']) if exists('nft') else print('nft not installed');pause()
        elif x=='8':print('\n  • Verify direct client source IPs before enforce.\n  • SSH/panel/API ports remain protected.\n  • Shared NAT bans can affect multiple clients.\n  • Stop-worker alone does not flush current timeout elements.\n');pause()
        elif x=='9':account_menu()
""")

replace_func('backup_menu','logs_diagnostics_menu',r"""def backup_menu():
    while True:
        header('BACKUP & RECOVERY','Encrypted control-plane backup · live data is never overwritten')
        item(1,'Create encrypted control-plane backup');item(2,'Verify encrypted backup');item(3,'Restore into NEW isolated directory');item(4,'List encrypted backup files');item(5,'List recent source rollback snapshots')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            path=ask('Backup file',str(DATA/'backups'/f"dark-{time.strftime('%Y%m%d-%H%M%S')}.darkbackup"))
            if path:run_action([COMMAND,'backup','--output',path],'Encrypted backup created.')
            pause()
        elif x=='2':
            arc=ask('Backup archive')
            if arc:run_action([COMMAND,'backup-verify','--archive',arc],'Backup verification passed.')
            pause()
        elif x=='3':
            arc=ask('Backup archive');dst=ask('NEW empty restore directory',str(DATA.parent/'dark-xray-restore'))
            if arc and dst and confirm('Restore is isolated and never overwrites live data. Continue?','RESTORE'):run_action([COMMAND,'restore','--archive',arc,'--destination',dst],'Backup restored into isolated destination.')
            pause()
        elif x=='4':
            path=DATA/'backups';rows=sorted(path.glob('*.darkbackup'),key=lambda f:f.stat().st_mtime,reverse=True)[:20] if path.exists() else []
            for f in rows:
                st=f.stat();print(f"  {time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(st.st_mtime))}  {human(st.st_size):>9}  {f}")
            if not rows:print('No encrypted DARK backups found.')
            pause()
        elif x=='5':
            path=DATA/'backups';rows=sorted(path.glob('pre-update-source-*.tar.gz'),reverse=True)[:20] if path.exists() else []
            print('\n'.join(str(f) for f in rows) if rows else 'No source rollback snapshots.');pause()
""")

replace_func('logs_diagnostics_menu','update_repair_menu',r"""def logs_diagnostics_menu():
    while True:
        header('LOGS & DIAGNOSTICS','Logs · health · production validation · host resources')
        item(1,'Panel logs');item(2,'Follow panel logs');item(3,'Recent panel errors only');item(4,'Guard logs');item(5,'Follow Guard logs');item(6,'Lock-free live health check');item(7,'Doctor / full diagnostics');item(8,'VPS readiness validation');item(9,'Production data-plane gate');item(10,'Listening TCP ports');item(11,'Disk / memory / uptime');item(12,'Application paths')
        back();x=ask('DARK')
        if x=='0':return
        if x in {'1','2','4','5'}:
            unit='dark-xray.service' if x in {'1','2'} else 'dark-xray-guard.service';follow=x in {'2','5'};args=['journalctl','-u',unit,'-n','40','-f'] if follow else ['journalctl','-u',unit,'-n','150','--no-pager']
            try:run(args)
            except KeyboardInterrupt:pass
            pause()
        elif x=='3':run(['journalctl','-u','dark-xray.service','-p','err','-n','100','--no-pager']);pause()
        elif x=='6':run([COMMAND,'check']);pause()
        elif x=='7':run([COMMAND,'doctor']);pause()
        elif x=='8':run([COMMAND,'vps-verify']);pause()
        elif x=='9':
            if confirm('Run isolated production gate?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='10':run(['ss','-lntp']) if exists('ss') else print('ss is not installed');pause()
        elif x=='11':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='12':print('App:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA,'\nCommand:',COMMAND);pause()
""")

replace_func('update_repair_menu','main',r"""def update_repair_menu():
    while True:
        header('UPDATE & REPAIR',f'Installed version: {ver()}')
        item(1,'Safe update to exact Ref / Tag / Commit','recommended');item(2,'Safe update to latest main','moving target');item(3,'Doctor');item(4,'VPS readiness validation');item(5,'Production data-plane gate');item(6,'Repair source permissions');item(7,'Reload systemd + restart panel');item(8,'Reinstall systemd units from /opt');item(9,'Show version / paths');item(10,'Uninstall application','PRESERVE config + data')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            ref=ask('Git Ref / Tag / Commit (example v0.9.0-rc3)')
            if ref and confirm(f'Rollback-safe update to {ref}?','UPDATE'):run_action([COMMAND,'update','--ref',ref],f'Safe update to {ref} completed.')
            pause()
        elif x=='2':
            print(f'{YE}main is a moving development target. Prefer an exact release Tag.{R}')
            if need_root() and confirm('Update to latest main?','MAIN'):run_action([COMMAND,'update','--ref','main'],'Safe update to main completed.')
            pause()
        elif x=='3':run([COMMAND,'doctor']);pause()
        elif x=='4':run([COMMAND,'vps-verify']);pause()
        elif x=='5':
            if confirm('Run isolated production gate?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='6':repair_source_permissions();pause()
        elif x=='7':
            if need_root() and confirm('Reload systemd and restart DARK?','RESTART'):run(['systemctl','daemon-reload']);run_action(['systemctl','restart','dark-xray.service'],'DARK restarted.');pause()
        elif x=='8':
            if not INSTALLED:print(f'{RE}Unit reinstall is only available from /opt/dark-xray.{R}');pause();continue
            if need_root() and confirm('Replace unit files from installed source?','UNITS'):
                for n in ('dark-xray.service','dark-xray-guard.service'):shutil.copy2(ROOT/'deploy'/n,Path('/etc/systemd/system')/n);os.chmod(Path('/etc/systemd/system')/n,0o644)
                run(['systemctl','daemon-reload']);print(f'{GR}Units restored.{R}')
            pause()
        elif x=='9':print('Version:',ver(),'\nApp:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA);pause()
        elif x=='10':
            if not INSTALLED:print(f'{RE}Uninstall refused outside /opt/dark-xray.{R}');pause();continue
            if need_root() and confirm('Remove app/services but KEEP config/data?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for f in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):Path(f).unlink(missing_ok=True)
                hook=Path('/etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel')
                if hook.exists() or hook.is_symlink():hook.unlink()
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data preserved.');raise SystemExit(0)
""")

replace_func('main','__main__',r"""def main():
    while True:
        c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');staged=runtime_stage_state(c)
        header('MAIN CONTROL MATRIX',f'v{ver()} • Panel {p} • Guard {g} • Staged {staged}')
        item(1,'Dashboard & Quick Status','service · URL · version · staged changes');item(2,'Account & Access','login owners · password · sessions · TOTP · API keys');item(3,'Panel & Network','port · address · URI path · BBR');item(4,'Domain & TLS','certificate · DNS · readiness');item(5,'Xray & Services','service · core · health gates');item(6,'Security & IP Guard','broker · bans · nftables');item(7,'Backup & Recovery','backup · verify · isolated restore');item(8,'Logs & Diagnostics','journal · doctor · production gates');item(9,'Update & Repair','exact ref · rollback-safe repair · uninstall')
        print(f'\n  {GY}[ 0]{R} Exit');x=ask('DARK').lstrip('0') or '0'
        if x=='0':return
        actions={'1':dashboard,'2':account_menu,'3':panel_network_menu,'4':domain_tls_menu,'5':xray_services_menu,'6':security_menu,'7':backup_menu,'8':logs_diagnostics_menu,'9':update_repair_menu}
        fn=actions.get(x)
        if fn:fn()
        else:print(f'{RE}Invalid option.{R}');time.sleep(.6)
""")

p.write_text(s,encoding='utf-8');print('menu boundary patch prepared')

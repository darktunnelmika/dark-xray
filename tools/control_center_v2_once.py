#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path


def replace_func(text,name,next_name,new):
    pat=rf'^def {re.escape(name)}\(.*?(?=^def {re.escape(next_name)}\()'
    out,n=re.subn(pat,new.rstrip()+'\n\n',text,count=1,flags=re.M|re.S)
    if n!=1:raise SystemExit(f'function anchor mismatch: {name}')
    return out

menu=Path('tools/menu.py');s=menu.read_text(encoding='utf-8')
if 'from urllib.parse import urlsplit' not in s:
    s=s.replace('from pathlib import Path\n','from pathlib import Path\nfrom urllib.parse import urlsplit\n',1)

anchor='''def inbound_ports():\n'''
helpers=r'''def access_mode(c):
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
        actual={
            'access_mode':'domain_tls' if origin.scheme=='https' else 'ssh',
            'bind_port':int(c.get('bind_port',2087)),
            'public_address':str(c.get('public_address','')),
            'panel_path':str(c.get('panel_path','/')),
            'poll_seconds':int(c.get('poll_seconds',5)),
            'core_autostart':bool(c.get('core_autostart',False)),
            'domain':(origin.hostname or '') if origin.scheme=='https' else '',
        }
        changed=[key for key,value in actual.items() if key in desired and desired.get(key)!=value]
        return 'none' if not changed else f'{len(changed)} pending'
    except Exception:return 'unknown'


def safe_enable_bbr():
    if not need_root():return False
    if not exists('sysctl'):
        print(f'{RE}sysctl is not available.{R}');return False
    avail=run(['sysctl','-n','net.ipv4.tcp_available_congestion_control'],capture=True)
    available=(getattr(avail,'stdout','') or '').split()
    if getattr(avail,'returncode',1)!=0 or 'bbr' not in available:
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


'''
if helpers.splitlines()[0] not in s:
    if anchor not in s:raise SystemExit('inbound helper anchor missing')
    s=s.replace(anchor,helpers+anchor,1)

s=replace_func(s,'dashboard','account_menu',r'''def dashboard():
    c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');owners=owner_usernames()
    staged=runtime_stage_state(c)
    header('DASHBOARD / QUICK STATUS',endpoint(c))
    print(f'''\n  Panel service     {badge(p)}
  IP Guard          {badge(g)}
  Autostart         {service('dark-xray.service','is-enabled')}
  Version           {ver()}
  Access mode       {access_mode(c)}
  Staged settings   {staged}
  Login Owners      {len(owners)}
  Host              {socket.gethostname()}
  Panel port        {c.get('bind_port','?')}
  Xray API          {c.get('xray_api_port','?')}
  Writes            {'enabled' if c.get('writes_enabled',True) else 'disabled'}
''')
    if staged not in {'none','unknown'}:print(f'  {YE}Review staged settings before applying them.{R}')
    show_access();pause()
''')

s=replace_func(s,'panel_network_menu','domain_tls_menu',r'''def panel_network_menu():
    while True:
        c=cfg();header('PANEL & NETWORK',f'{access_mode(c)} • staged {runtime_stage_state(c)}')
        title_row('PANEL ACCESS')
        item(1,'Safe configuration summary')
        item(2,'Change panel port',str(c.get('bind_port',2087)))
        item(3,'Change public address / DNS',str(c.get('public_address','')))
        item(4,'Change panel URI path',str(c.get('panel_path','/')))
        item(5,'Generate random panel URI path','random 96-bit path token')
        item(6,'Show panel URL / SSH tunnel')
        title_row('STAGED WEB SETTINGS')
        item(7,'Preview staged settings')
        item(8,'Apply ALL staged settings','root boundary · may restart panel')
        title_row('HOST NETWORK')
        item(9,'Listening TCP ports')
        item(10,'System resources / uptime')
        item(11,'Enable BBR safely','verify kernel support + rollback on failure')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':safe_config_summary();pause()
        elif x=='2':
            val=ask('New panel port',str(c.get('bind_port',2087)))
            if not val.isdigit() or not 1024<=int(val)<=65535:print(f'{RE}Invalid nonprivileged port.{R}');pause();continue
            new=int(val);old=int(c.get('bind_port',2087));reserved=(protected_ports(c)-{old})|inbound_ports()
            if new!=old and new in reserved:print(f'{RE}Port conflicts with a protected management/core/data port.{R}');pause();continue
            if new!=old and port_busy(new):print(f'{RE}Port is already listening.{R}');pause();continue
            if new==old:print(f'{GY}Panel is already using port {old}.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--bind-port',str(new)],f'Panel port staged: {old} → {new}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}');run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):print(f'{GR}Panel URL: {endpoint(cfg())}{R}')
            pause()
        elif x=='3':
            v=ask('Public IP/DNS',str(c.get('public_address','')))
            if not v or any(ch in v for ch in '/?#@ \r\n\t'):print(f'{RE}Invalid address.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--public-address',v],f'Public address staged: {v}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}');run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='4':
            v=ask('Panel URI path (example /dark-admin)',str(c.get('panel_path','/'))).strip() or '/'
            if v!='/' and v.endswith('/'):v=v.rstrip('/')
            if not valid_panel_path(v):print(f'{RE}Invalid URI path. Use / or /letters-numbers_-/segments.{R}');pause();continue
            if v==str(c.get('panel_path','/')):print(f'{GY}Panel already uses that URI path.{R}');pause();continue
            if confirm(f'Stage panel URI path {c.get("panel_path","/")} → {v}?'):
                if run_action([COMMAND,'stage-runtime','--panel-path',v],f'URI path staged: {v}'):
                    print(f'{PU}Review ALL pending runtime changes before apply:{R}');run([COMMAND,'settings-apply','--dry-run'])
                    if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                        if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):print(f'{GR}New panel URL: {endpoint(cfg())}{R}')
                pause()
        elif x=='5':
            v='/dark-'+secrets.token_hex(12)
            if run_action([COMMAND,'stage-runtime','--panel-path',v],f'Random URI path staged: {v}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}');run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):print(f'{GR}Panel URL: {endpoint(cfg())}{R}')
            pause()
        elif x=='6':show_access();pause()
        elif x=='7':run([COMMAND,'settings-apply','--dry-run']);pause()
        elif x=='8':
            if need_root() and confirm('Apply ALL staged Web Settings? Listener/TLS changes may restart the panel.','APPLY'):
                run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
        elif x=='9':run(['ss','-lntp']) if exists('ss') else print('ss is not installed');pause()
        elif x=='10':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='11':
            if confirm('Enable persistent fq + BBR after verifying kernel support?','BBR'):safe_enable_bbr()
            pause()
''')

s=replace_func(s,'domain_tls_menu','xray_services_menu',r'''def domain_tls_menu():
    while True:
        c=cfg();header('DOMAIN & TLS',f'{access_mode(c)} • {endpoint(c)}')
        item(1,'Set / change domain + issue Let\'s Encrypt TLS')
        item(2,'Renew certificate now')
        item(3,'Certificate details')
        item(4,'Certbot timer status')
        item(5,'DNS A / AAAA lookup')
        item(6,'Show current access URL')
        item(7,'Run VPS readiness check','TLS · route · service · DB')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            if not need_root():continue
            domain=ask('Domain');email=ask('ACME email');port=ask('HTTPS panel port',str(c.get('bind_port',2087)))
            if domain and email and port and confirm('Port 80 must reach this server. Issue and activate TLS?','TLS'):
                run_action([COMMAND,'domain','--domain',domain,'--email',email,'--port',port,'--agree-tos'],'TLS certificate issued and activated.');pause()
        elif x=='2':
            if need_root() and confirm('Renewal may restart DARK and disconnect the panel briefly.','RENEW'):run_action([COMMAND,'domain','--renew'],'TLS renewal hook completed.')
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
''')

s=replace_func(s,'xray_services_menu','security_menu',r'''def xray_services_menu():
    while True:
        p=service('dark-xray.service');header('XRAY & SERVICES',f'Panel {p} • Autostart {service("dark-xray.service","is-enabled")}')
        title_row('SERVICE CONTROL')
        item(1,'Detailed DARK service status')
        item(2,'Start DARK service')
        item(3,'Stop DARK service','active panel sessions disconnect')
        item(4,'Restart DARK + managed Xray')
        item(5,'Enable autostart + start now')
        item(6,'Disable autostart','does not stop the current service')
        title_row('CORE & VALIDATION')
        item(7,'Xray binary / version')
        item(8,'Lock-free live health check')
        item(9,'Doctor / core diagnostics')
        item(10,'VPS readiness validation')
        item(11,'Production data-plane gate','isolated real Xray lab')
        item(12,'Show systemd unit')
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
            if need_root() and confirm('Disable automatic startup after the next boot?','DISABLE'):run_action(['systemctl','disable','dark-xray.service'],'DARK autostart disabled.');pause()
        elif x=='7':xray_version();pause()
        elif x=='8':run([COMMAND,'check']);pause()
        elif x=='9':run([COMMAND,'doctor']);pause()
        elif x=='10':run([COMMAND,'vps-verify']);pause()
        elif x=='11':
            if confirm('Run isolated production gate using the installed Xray binary?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='12':run(['systemctl','cat','dark-xray.service','--no-pager']);pause()
''')

s=replace_func(s,'security_menu','backup_menu',r'''def security_menu():
    while True:
        header('SECURITY & IP GUARD',f'Guard {service("dark-xray-guard.service")}')
        title_row('IP GUARD')
        item(1,'Broker / IP Guard status','approved + protected ports · active leases')
        item(2,'Configure / enable IP Guard')
        item(3,'Clear current DARK temporary bans','broker-authenticated clear')
        item(4,'Restart Guard worker','rebuilds owned table and clears old bans')
        item(5,'Stop Guard worker','existing nft timeouts can remain active')
        item(6,'Guard logs')
        item(7,'Show DARK nftables table','inet dark_xray_ip')
        item(8,'IP Guard safety notes')
        title_row('ACCOUNT SECURITY')
        item(9,'Account & Access','username · password · sessions · 2FA')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            cp=run([COMMAND,'guard-control','status'])
            if getattr(cp,'returncode',1)!=0:run(['systemctl','status','dark-xray-guard.service','--no-pager','-l'])
            pause()
        elif x=='2':
            if not need_root():continue
            ports=ask('Xray DATA ports, comma separated');ex=ask('Optional exempt IP/CIDR')
            print(f'{YE}Enable only if Xray observes the real client packet source on this host.{R}')
            if ports and confirm('I verified direct source IPs. Enable enforcement?','ENFORCE'):
                a=[COMMAND,'guard-enable','--ports',ports,'--verified-direct-sources'];a += ['--exempt',ex] if ex else [];run_action(a,'IP Guard configuration installed and worker enabled.')
            pause()
        elif x=='3':
            if confirm('Clear every current DARK temporary IP ban?','CLEAR'):run_action([COMMAND,'guard-control','clear'],'Current DARK temporary bans cleared.')
            pause()
        elif x=='4':
            if need_root() and confirm('Restart Guard and rebuild its owned nft table? Existing temporary bans are cleared.','RESTART'):
                run_action(['systemctl','restart','dark-xray-guard.service'],'Guard worker restarted and DARK table rebuilt.')
            pause()
        elif x=='5':
            print(f'{YE}Stopping the worker does NOT promise immediate unban; native nft timeouts may remain until expiry.{R}')
            if need_root() and confirm('Stop the Guard worker?','STOP'):run_action(['systemctl','stop','dark-xray-guard.service'],'Guard worker stopped.')
            pause()
        elif x=='6':run(['journalctl','-u','dark-xray-guard.service','-n','150','--no-pager']);pause()
        elif x=='7':run(['nft','list','table','inet','dark_xray_ip']) if exists('nft') else print('nft not installed');pause()
        elif x=='8':print('\n  • Never enforce behind opaque/tunnel source addresses.\n  • SSH/panel/API ports remain protected.\n  • Verify direct client source IPs before enforcement.\n  • Shared NAT IP bans can affect multiple clients.\n  • Stop-worker alone does not flush active nft timeout elements.\n');pause()
        elif x=='9':account_menu()
''')

s=replace_func(s,'backup_menu','logs_diagnostics_menu',r'''def backup_menu():
    while True:
        header('BACKUP & RECOVERY','Encrypted control-plane backup · live data is never overwritten')
        item(1,'Create encrypted control-plane backup','DB · secret.key · config · panel TLS when configured')
        item(2,'Verify encrypted backup','decrypt + isolated restore rehearsal')
        item(3,'Restore into NEW isolated directory','never overwrites live data')
        item(4,'List encrypted backup files')
        item(5,'List recent source rollback snapshots')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            default=str(DATA/'backups'/f"dark-{time.strftime('%Y%m%d-%H%M%S')}.darkbackup")
            path=ask('Backup file',default)
            if path:run_action([COMMAND,'backup','--output',path],'Encrypted backup created and manifest verified.')
            pause()
        elif x=='2':
            arc=ask('Backup archive')
            if arc:run_action([COMMAND,'backup-verify','--archive',arc],'Encrypted backup verification passed.')
            pause()
        elif x=='3':
            arc=ask('Backup archive');dst=ask('NEW empty restore directory',str(DATA.parent/'dark-xray-restore'))
            if arc and dst and confirm('Restore is isolated and does not overwrite live data. Continue?','RESTORE'):
                run_action([COMMAND,'restore','--archive',arc,'--destination',dst],'Backup restored into the isolated destination.')
            pause()
        elif x=='4':
            path=DATA/'backups';rows=sorted(path.glob('*.darkbackup'),key=lambda p:p.stat().st_mtime,reverse=True)[:20] if path.exists() else []
            if rows:
                for p in rows:
                    st=p.stat();print(f"  {time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(st.st_mtime))}  {human(st.st_size):>9}  {p}")
            else:print('No encrypted DARK backups found.')
            pause()
        elif x=='5':
            path=DATA/'backups';rows=sorted(path.glob('pre-update-source-*.tar.gz'),reverse=True)[:20] if path.exists() else []
            print('\n'.join(str(p) for p in rows) if rows else 'No source rollback snapshots.');pause()
''')

s=replace_func(s,'logs_diagnostics_menu','update_repair_menu',r'''def logs_diagnostics_menu():
    while True:
        header('LOGS & DIAGNOSTICS','Logs · health · production validation · host resources')
        title_row('LOGS')
        item(1,'Panel logs','last 150')
        item(2,'Follow panel logs','Ctrl+C to stop')
        item(3,'Recent panel errors only')
        item(4,'Guard logs','last 150')
        item(5,'Follow Guard logs','Ctrl+C to stop')
        title_row('VALIDATION')
        item(6,'Lock-free live health check')
        item(7,'Doctor / full diagnostics')
        item(8,'VPS readiness validation')
        item(9,'Production data-plane gate','isolated real Xray lab')
        title_row('HOST')
        item(10,'Listening TCP ports')
        item(11,'Disk / memory / uptime')
        item(12,'Application paths')
        back();x=ask('DARK')
        if x=='0':return
        if x in {'1','2','4','5'}:
            unit='dark-xray.service' if x in {'1','2'} else 'dark-xray-guard.service';follow=x in {'2','5'}
            args=['journalctl','-u',unit,'-n','40','-f'] if follow else ['journalctl','-u',unit,'-n','150','--no-pager']
            try:run(args)
            except KeyboardInterrupt:pass
            pause()
        elif x=='3':run(['journalctl','-u','dark-xray.service','-p','err','-n','100','--no-pager']);pause()
        elif x=='6':run([COMMAND,'check']);pause()
        elif x=='7':run([COMMAND,'doctor']);pause()
        elif x=='8':run([COMMAND,'vps-verify']);pause()
        elif x=='9':
            if confirm('Run isolated production gate using the installed Xray binary?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='10':run(['ss','-lntp']) if exists('ss') else print('ss is not installed');pause()
        elif x=='11':run(['uptime']);run(['free','-h']);run(['df','-h','/']);pause()
        elif x=='12':print('App:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA,'\nCommand:',COMMAND);pause()
''')

s=replace_func(s,'update_repair_menu','main',r'''def update_repair_menu():
    while True:
        header('UPDATE & REPAIR',f'Installed version: {ver()}')
        title_row('SAFE UPDATE')
        item(1,'Safe update to exact Ref / Tag / Commit','recommended for RC/stable testing')
        item(2,'Safe update to latest main','advanced / moving target')
        title_row('VERIFY / REPAIR')
        item(3,'Doctor')
        item(4,'VPS readiness validation')
        item(5,'Production data-plane gate')
        item(6,'Repair application source permissions','safe 755/644 normalization')
        item(7,'Reload systemd + restart panel')
        item(8,'Reinstall systemd unit files from /opt')
        item(9,'Show installed version / paths')
        title_row('DESTRUCTIVE')
        item(10,'Uninstall application','PRESERVE config + data')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            print('\nSafe Update preserves /etc and /var/lib and snapshots source + SQLite before activation.')
            ref=ask('Git Ref / Tag / Commit (example v0.9.0-rc3)')
            if ref and confirm(f'Rollback-safe update to exact ref {ref}?','UPDATE'):
                run_action([COMMAND,'update','--ref',ref],f'Safe update to {ref} completed.')
            pause()
        elif x=='2':
            print(f'{YE}main is a moving development target. Prefer an exact release Tag for VPS testing.{R}')
            if need_root() and confirm('Update to latest main with rollback protection?','MAIN'):run_action([COMMAND,'update','--ref','main'],'Safe update to latest main completed.')
            pause()
        elif x=='3':run([COMMAND,'doctor']);pause()
        elif x=='4':run([COMMAND,'vps-verify']);pause()
        elif x=='5':
            if confirm('Run isolated production gate using the installed Xray binary?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='6':repair_source_permissions();pause()
        elif x=='7':
            if need_root() and confirm('Reload systemd and restart DARK panel?','RESTART'):
                run(['systemctl','daemon-reload']);run_action(['systemctl','restart','dark-xray.service'],'DARK service restarted after daemon-reload.')
            pause()
        elif x=='8':
            if not INSTALLED:print(f'{RE}Unit reinstall is available only from /opt/dark-xray.{R}');pause();continue
            if need_root() and confirm('Replace DARK unit files from the installed /opt source?','UNITS'):
                for n in ('dark-xray.service','dark-xray-guard.service'):
                    shutil.copy2(ROOT/'deploy'/n,Path('/etc/systemd/system')/n);os.chmod(Path('/etc/systemd/system')/n,0o644)
                run(['systemctl','daemon-reload']);print(f'{GR}Units restored and daemon reloaded.{R}')
            pause()
        elif x=='9':print('Version:',ver(),'\nApp:',ROOT,'\nConfig:',CONFIG,'\nData:',DATA);pause()
        elif x=='10':
            if not INSTALLED:print(f'{RE}Uninstall is refused outside the installed /opt/dark-xray tree.{R}');pause();continue
            if not need_root():continue
            if confirm('Remove application/services but KEEP /etc/dark-xray and /var/lib/dark-xray?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for p in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):
                    Path(p).unlink(missing_ok=True)
                hook=Path('/etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel')
                if hook.exists() or hook.is_symlink():hook.unlink()
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data/certificates preserved; DARK renewal hook removed.');raise SystemExit(0)
''')

s=replace_func(s,'main','__main__',r'''def main():
    while True:
        c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');staged=runtime_stage_state(c)
        header('MAIN CONTROL MATRIX',f'v{ver()} • Panel {p} • Guard {g} • Staged {staged}')
        title_row('CONTROL CENTER')
        item(1,'Dashboard & Quick Status','service · URL · version · staged changes')
        item(2,'Account & Access','login owners · password · sessions · TOTP · API keys')
        item(3,'Panel & Network','port · public address · URI path · staged settings · BBR')
        item(4,'Domain & TLS','domain · certificate · DNS · readiness')
        item(5,'Xray & Services','service control · core · health gates')
        item(6,'Security & IP Guard','broker · bans · nftables · account recovery')
        item(7,'Backup & Recovery','encrypted backup · verify · isolated restore')
        item(8,'Logs & Diagnostics','journal · doctor · VPS/production gates')
        item(9,'Update & Repair','exact ref update · rollback-safe repair · uninstall')
        print(f'\n  {GY}[ 0]{R} Exit')
        x=ask('DARK').lstrip('0') or '0'
        if x=='0':return
        actions={'1':dashboard,'2':account_menu,'3':panel_network_menu,'4':domain_tls_menu,'5':xray_services_menu,
                 '6':security_menu,'7':backup_menu,'8':logs_diagnostics_menu,'9':update_repair_menu}
        fn=actions.get(x)
        if fn:fn()
        else:print(f'{RE}Invalid option.{R}');time.sleep(.6)
''')
menu.write_text(s,encoding='utf-8')

# Installed wrappers: Guard status/clear and backup verification run as the service
# account so the root broker authenticates the expected Unix peer UID.
for file in (Path('tools/provision.py'),Path('tools/update.py')):
    t=file.read_text(encoding='utf-8')
    old='init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|doctor)'
    new='init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|backup-verify|guard-control|doctor)'
    if old not in t:raise SystemExit(f'wrapper anchor missing: {file}')
    t=t.replace(old,new,1)
    file.write_text(t,encoding='utf-8')

# Candidate validation must include newly required CLI primitives.
up=Path('tools/update.py');u=up.read_text(encoding='utf-8')
old="src/'tools/doctor.py',src/'deploy/dark-xray.service'"
new="src/'tools/doctor.py',src/'tools/backup_cli.py',src/'tools/guard-control.py',src/'deploy/dark-xray.service'"
if old not in u:raise SystemExit('update required-files anchor missing')
u=u.replace(old,new,1)
old2="src/'tools/settings_apply.py',src/'tools/doctor.py'])"
new2="src/'tools/settings_apply.py',src/'tools/doctor.py',src/'tools/backup_cli.py',src/'tools/guard-control.py'])"
if old2 not in u:raise SystemExit('update pycompile anchor missing')
u=u.replace(old2,new2,1);up.write_text(u,encoding='utf-8')

# Persistent regression coverage.
control_test=Path('tests/test_control_center_v2.py')
control_test.write_text(r'''import importlib.util,sqlite3,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dark_menu_control_v2',ROOT/'tools/menu.py')
assert SPEC and SPEC.loader
MENU=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MENU)

def test_protected_ports_uses_configured_ssh_and_core_ports():
    assert MENU.protected_ports({'xray_api_port':10085,'protected_ports':[22,2222,2087]})=={22,2222,2087,10085}

def test_runtime_stage_state_reports_pending(tmp_path,monkeypatch):
    monkeypatch.setattr(MENU,'DATA',tmp_path)
    db=tmp_path/'dark.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE core_sections(name TEXT PRIMARY KEY,body TEXT NOT NULL)')
        con.execute('INSERT INTO core_sections VALUES(?,?)',('runtime',json.dumps({'bind_port':9090,'panel_path':'/'})))
    c={'public_origin':'http://127.0.0.1:2087','bind_port':2087,'panel_path':'/','public_address':'127.0.0.1','xray_api_port':10085}
    assert MENU.runtime_stage_state(c)=='1 pending'

def test_bbr_refuses_unsupported_kernel_before_file_write(monkeypatch):
    monkeypatch.setattr(MENU,'need_root',lambda:True);monkeypatch.setattr(MENU,'exists',lambda name:True)
    class CP:
        returncode=0;stdout='reno cubic\n'
    monkeypatch.setattr(MENU,'run',lambda *a,**k:CP())
    assert MENU.safe_enable_bbr() is False

def test_control_center_routes_real_safety_commands():
    src=(ROOT/'tools/menu.py').read_text()
    assert "'nft','list','table','inet','dark_xray_ip'" in src
    assert "'guard-control','clear'" in src
    assert "'backup-verify'" in src and "'restore'" in src
    assert "'production-gate'" in src and "'vps-verify'" in src
    assert "'update','--ref',ref" in src
    assert 'CPU {cpu}' not in src and 'RAM {ram}' not in src
''',encoding='utf-8')

backup_test=Path('tests/test_backup_cli.py')
backup_test.write_text(r'''import importlib.util,sqlite3,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dark_backup_cli',ROOT/'tools/backup_cli.py')
assert SPEC and SPEC.loader
CLI=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CLI)

def make_install(tmp_path):
    data=tmp_path/'data';data.mkdir();(data/'secret.key').write_bytes(b'x'*32)
    db=data/'dark.sqlite3'
    with sqlite3.connect(db) as con:
        for table in ('clients','owners','api_admins','core_clients'):con.execute(f'CREATE TABLE {table}(id TEXT)')
    config=tmp_path/'config.json';config.write_text('{}')
    return data,config

def test_backup_verify_restore_cli_roundtrip(tmp_path,monkeypatch):
    data,config=make_install(tmp_path);archive=tmp_path/'test.darkbackup';password='correct horse battery staple'
    monkeypatch.setattr(CLI,'new_secret',lambda:password)
    monkeypatch.setattr(sys,'argv',['backup_cli.py','backup','--config',str(config),'--data',str(data),'--output',str(archive)])
    assert CLI.main()==0 and archive.is_file()
    monkeypatch.setattr(CLI,'secret',lambda prompt:password)
    monkeypatch.setattr(sys,'argv',['backup_cli.py','verify','--archive',str(archive)])
    assert CLI.main()==0
    dest=tmp_path/'restore'
    monkeypatch.setattr(sys,'argv',['backup_cli.py','restore','--archive',str(archive),'--destination',str(dest)])
    assert CLI.main()==0 and (dest/'data/dark.sqlite3').is_file()
''',encoding='utf-8')

run=Path('tests/run-tests.sh');r=run.read_text(encoding='utf-8')
needle="python -m pytest tests/test_menu_owner_selection.py -q --junitxml=qa/junit/menu-owner-selection.xml\n"
if needle not in r:raise SystemExit('run-tests menu anchor missing')
add=needle+"python -m pytest tests/test_control_center_v2.py -q --junitxml=qa/junit/control-center-v2.xml\npython -m pytest tests/test_backup_cli.py -q --junitxml=qa/junit/backup-cli.xml\n"
r=r.replace(needle,add,1);run.write_text(r,encoding='utf-8')

# RC3 identifies this larger terminal-control audit distinctly from the RC2
# safe-update compatibility hotfix.
Path('VERSION').write_text('0.9.0-rc3\n',encoding='utf-8')
for name in ('README.md','README.en.md','README.fa.md','STATUS.fa.md','TESTING-RC.fa.md'):
    p=Path(name);t=p.read_text(encoding='utf-8').replace('0.9.0-rc2','0.9.0-rc3')
    t=t.replace('0.9.0 RC1','0.9.0 RC3')
    p.write_text(t,encoding='utf-8')
p=Path('PUBLISH-STATUS.json');value=json.loads(p.read_text());value['current_version']='0.9.0-rc3';value['control_center_v2']=True;p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
ch=Path('CHANGELOG.fa.md');t=ch.read_text(encoding='utf-8')
entry='''## 0.9.0-rc3 — Control Center V2 audit\n\n- منوی ترمینال از نظر Backup/Restore، Guard، Update/Ref، Validation gates و عملیات systemd audit و بازطراحی شد.\n- Backup/Restore CLI واقعی با passphrase تعاملی، verify و isolated restore اضافه شد.\n- Guard status/clear فقط از Broker احرازشده انجام می‌شود؛ نام جدول nft صحیح `dark_xray_ip` است و Stop دیگر به‌اشتباه ادعای clear ban ندارد.\n- Update منویی از Tag/Commit/Ref دقیق پشتیبانی می‌کند و `check`، `vps-verify` و `production-gate` داخل Control Center در دسترس‌اند.\n- BBR قبل از persistence، پشتیبانی kernel را بررسی می‌کند و در خطای activation فایل/runtime قبلی را restore می‌کند.\n- Main Control Matrix از CPU/RAM شلوغ پاک شد؛ منابع سیستم در Diagnostics باقی مانده‌اند.\n\n'''
if '## 0.9.0-rc3 — Control Center V2 audit' not in t:t=t.replace('# تغییرات DARK XRAY\n\n','# تغییرات DARK XRAY\n\n'+entry,1)
ch.write_text(t,encoding='utf-8')
print('Control Center V2 patch prepared')

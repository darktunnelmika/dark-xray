#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path

p=Path('tools/menu.py');s=p.read_text(encoding='utf-8')

def must(old,new,label):
    global s
    if s.count(old)!=1:raise SystemExit(label+' anchor mismatch')
    s=s.replace(old,new,1)

if 'from urllib.parse import urlsplit\n' not in s:
    must('from pathlib import Path\n','from pathlib import Path\nfrom urllib.parse import urlsplit\n','urlsplit import')

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
        actual={'access_mode':'domain_tls' if origin.scheme=='https' else 'ssh',
                'bind_port':int(c.get('bind_port',2087)),'public_address':str(c.get('public_address','')),
                'panel_path':str(c.get('panel_path','/')),'poll_seconds':int(c.get('poll_seconds',5)),
                'core_autostart':bool(c.get('core_autostart',False)),
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
if 'def access_mode(c):' not in s:
    if anchor not in s:raise SystemExit('helper anchor mismatch')
    s=s.replace(anchor,helpers+anchor,1)

old_dashboard="""def dashboard():
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
"""
new_dashboard="""def dashboard():
    c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');staged=runtime_stage_state(c)
    header('DASHBOARD / QUICK STATUS',endpoint(c))
    print(f'\\n  Panel service     {badge(p)}\\n'
          f'  IP Guard          {badge(g)}\\n'
          f'  Autostart         {service("dark-xray.service","is-enabled")}\\n'
          f'  Version           {ver()}\\n'
          f'  Access mode       {access_mode(c)}\\n'
          f'  Staged settings   {staged}\\n'
          f'  Login Owners      {len(owner_usernames())}\\n'
          f'  Host              {socket.gethostname()}\\n'
          f'  Panel port        {c.get("bind_port","?")}\\n'
          f'  Xray API          {c.get("xray_api_port","?")}\\n'
          f'  Writes            {"enabled" if c.get("writes_enabled",True) else "disabled"}\\n')
    if staged not in {'none','unknown'}:print(f'  {YE}Review staged settings before applying them.{R}')
    show_access();pause()
"""
must(old_dashboard,new_dashboard,'dashboard')

must("reserved={int(c.get('xray_api_port',10085)),22}|inbound_ports()","reserved=(protected_ports(c)-{old})|inbound_ports()",'protected panel ports')
must("item(10,'Enable BBR','fq + bbr')","item(10,'Enable BBR safely','verify kernel support · rollback on failure')",'BBR label')
old_bbr="""        elif x=='10':
            if need_root() and confirm('Apply persistent fq + BBR sysctl?'):
                Path('/etc/sysctl.d/99-dark-xray-bbr.conf').write_text('net.core.default_qdisc=fq\\nnet.ipv4.tcp_congestion_control=bbr\\n')
                run(['sysctl','--system']);pause()
"""
new_bbr="""        elif x=='10':
            if confirm('Enable persistent fq + BBR after verifying kernel support?','BBR'):safe_enable_bbr()
            pause()
"""
must(old_bbr,new_bbr,'BBR action')
must("item(5,'DNS lookup / verification')","item(5,'DNS A / AAAA lookup')",'DNS label')
must("d=ask('Domain');run(['getent','ahostsv4',d]) if d else None;pause()","d=ask('Domain');run(['getent','ahosts',d]) if d and exists('getent') else (print('getent is not installed') if d else None);pause()",'DNS action')

old_xray="""        item(7,'Xray binary / version')
        item(8,'Doctor / core diagnostics')
        item(9,'Show systemd unit')
"""
new_xray="""        item(7,'Xray binary / version')
        item(8,'Lock-free live health check')
        item(9,'Doctor / core diagnostics')
        item(10,'VPS readiness validation')
        item(11,'Production data-plane gate','isolated real Xray lab')
        item(12,'Show systemd unit')
"""
must(old_xray,new_xray,'Xray validation menu')
old_xray_actions="""        elif x=='7':xray_version();pause()
        elif x=='8':run([COMMAND,'doctor']);pause()
        elif x=='9':run(['systemctl','cat','dark-xray.service','--no-pager']);pause()
"""
new_xray_actions="""        elif x=='7':xray_version();pause()
        elif x=='8':run([COMMAND,'check']);pause()
        elif x=='9':run([COMMAND,'doctor']);pause()
        elif x=='10':run([COMMAND,'vps-verify']);pause()
        elif x=='11':
            if confirm('Run isolated production gate using the installed Xray binary?','GATE'):run([COMMAND,'production-gate'])
            pause()
        elif x=='12':run(['systemctl','cat','dark-xray.service','--no-pager']);pause()
"""
must(old_xray_actions,new_xray_actions,'Xray validation actions')

start=s.index('def security_menu():');end=s.index('\ndef backup_menu():',start)
new_security="""def security_menu():
    while True:
        header('SECURITY & IP GUARD',f'Guard {service("dark-xray-guard.service")}')
        item(1,'Broker / IP Guard status','approved + protected ports · active leases')
        item(2,'Configure / enable IP Guard')
        item(3,'Clear current DARK temporary bans','broker-authenticated clear')
        item(4,'Restart Guard worker','rebuilds owned table and clears old bans')
        item(5,'Stop Guard worker','existing nft timeouts can remain active')
        item(6,'Guard logs')
        item(7,'Show DARK nftables table','inet dark_xray_ip')
        item(8,'IP Guard safety notes')
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
        elif x=='8':print('\\n  • Never enforce behind opaque/tunnel source addresses.\\n  • SSH/panel/API ports remain protected.\\n  • Verify direct client source IPs before enforcement.\\n  • Shared NAT IP bans can affect multiple clients.\\n  • Stop-worker alone does not flush active nft timeout elements.\\n');pause()
        elif x=='9':account_menu()
"""
s=s[:start]+new_security+s[end+1:]

start=s.index('def backup_menu():');end=s.index('\ndef logs_diagnostics_menu():',start)
new_backup="""def backup_menu():
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
            default=str(DATA/'backups'/f"dark-{time.strftime('%Y%m%d-%H%M%S')}.darkbackup");path=ask('Backup file',default)
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
                for f in rows:
                    st=f.stat();print(f"  {time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(st.st_mtime))}  {human(st.st_size):>9}  {f}")
            else:print('No encrypted DARK backups found.')
            pause()
        elif x=='5':
            path=DATA/'backups';rows=sorted(path.glob('pre-update-source-*.tar.gz'),reverse=True)[:20] if path.exists() else []
            print('\\n'.join(str(f) for f in rows) if rows else 'No source rollback snapshots.');pause()
"""
s=s[:start]+new_backup+s[end+1:]

start=s.index('def logs_diagnostics_menu():');end=s.index('\ndef update_repair_menu():',start)
new_logs="""def logs_diagnostics_menu():
    while True:
        header('LOGS & DIAGNOSTICS','Logs · health · production validation · host resources')
        item(1,'Panel logs','last 150');item(2,'Follow panel logs','Ctrl+C to stop');item(3,'Recent panel errors only')
        item(4,'Guard logs','last 150');item(5,'Follow Guard logs','Ctrl+C to stop')
        item(6,'Lock-free live health check');item(7,'Doctor / full diagnostics');item(8,'VPS readiness validation')
        item(9,'Production data-plane gate','isolated real Xray lab');item(10,'Listening TCP ports');item(11,'Disk / memory / uptime');item(12,'Application paths')
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
        elif x=='12':print('App:',ROOT,'\\nConfig:',CONFIG,'\\nData:',DATA,'\\nCommand:',COMMAND);pause()
"""
s=s[:start]+new_logs+s[end+1:]

start=s.index('def update_repair_menu():');end=s.index('\ndef main():',start)
new_update="""def update_repair_menu():
    while True:
        header('UPDATE & REPAIR',f'Installed version: {ver()}')
        item(1,'Safe update to exact Ref / Tag / Commit','recommended for RC/stable testing')
        item(2,'Safe update to latest main','advanced / moving target')
        item(3,'Doctor');item(4,'VPS readiness validation');item(5,'Production data-plane gate')
        item(6,'Repair application source permissions','safe 755/644 normalization')
        item(7,'Reload systemd + restart panel');item(8,'Reinstall systemd unit files from /opt');item(9,'Show installed version / paths')
        item(10,'Uninstall application','PRESERVE config + data')
        back();x=ask('DARK')
        if x=='0':return
        if x=='1':
            print('\\nSafe Update preserves /etc and /var/lib and snapshots source + SQLite before activation.')
            ref=ask('Git Ref / Tag / Commit (example v0.9.0-rc3)')
            if ref and confirm(f'Rollback-safe update to exact ref {ref}?','UPDATE'):run_action([COMMAND,'update','--ref',ref],f'Safe update to {ref} completed.')
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
        elif x=='9':print('Version:',ver(),'\\nApp:',ROOT,'\\nConfig:',CONFIG,'\\nData:',DATA);pause()
        elif x=='10':
            if not INSTALLED:print(f'{RE}Uninstall is refused outside the installed /opt/dark-xray tree.{R}');pause();continue
            if not need_root():continue
            if confirm('Remove application/services but KEEP /etc/dark-xray and /var/lib/dark-xray?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for f in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):Path(f).unlink(missing_ok=True)
                hook=Path('/etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel')
                if hook.exists() or hook.is_symlink():hook.unlink()
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data/certificates preserved; DARK renewal hook removed.');raise SystemExit(0)
"""
s=s[:start]+new_update+s[end+1:]

must("c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');cpu,ram,_=metrics()","c=cfg();p=service('dark-xray.service');g=service('dark-xray-guard.service');staged=runtime_stage_state(c)",'main metrics')
must("header('MAIN CONTROL MATRIX',f'Panel {p} • Guard {g} • CPU {cpu} • RAM {ram} • {endpoint(c)}')","header('MAIN CONTROL MATRIX',f'v{ver()} • Panel {p} • Guard {g} • Staged {staged}')",'main header')
must("item(1,'Dashboard & Quick Status','health · URL · resources')","item(1,'Dashboard & Quick Status','service · URL · version · staged changes')",'dashboard hint')
must("item(7,'Backup & Recovery','encrypted backup · isolated restore')","item(7,'Backup & Recovery','encrypted backup · verify · isolated restore')",'backup hint')
must("item(8,'Logs & Diagnostics','journal · doctor · ports')","item(8,'Logs & Diagnostics','journal · doctor · VPS/production gates')",'diagnostic hint')
must("item(9,'Update & Repair','safe update · permission repair · uninstall')","item(9,'Update & Repair','exact ref update · rollback-safe repair · uninstall')",'update hint')

p.write_text(s,encoding='utf-8');print('menu patch prepared')

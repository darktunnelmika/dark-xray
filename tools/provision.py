#!/usr/bin/env python3
"""Fresh independent Linux/systemd install. No other panel is read or modified."""
import argparse, hashlib, json, os, pwd, re, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APP=Path('/opt/dark-xray');CONF=Path('/etc/dark-xray');DATA=Path('/var/lib/dark-xray')

def run(args):subprocess.run(list(map(str,args)),check=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core-archive',type=Path)
    p.add_argument('--core-sha256')
    p.add_argument('--core-version',default='v26.3.27')
    p.add_argument('--username',default='dark')
    p.add_argument('--port',type=int,default=2087)
    p.add_argument('--ssh-port',type=int,action='append',default=[])
    p.add_argument('--public-address',default='127.0.0.1',help='Proxy data IP/DNS; the panel itself remains loopback until TLS setup')
    p.add_argument('--install-os-packages',action='store_true',help='Explicitly permit apt installation of python3-venv and ca-certificates')
    a=p.parse_args()
    if os.geteuid()!=0 or sys.platform!='linux':raise SystemExit('Linux root is required only for provisioning')
    if sys.version_info<(3,11):raise SystemExit('Python 3.11+ required')
    if not Path('/run/systemd/system').exists():raise SystemExit('A systemd host is required; use install.sh for local development')
    if not re.fullmatch('[A-Za-z0-9_.@+-]{1,128}',a.username):raise SystemExit('Invalid username')
    if not 1024<=a.port<=65535 or any(not 1<=x<=65535 for x in a.ssh_port):raise SystemExit('Invalid port')
    if bool(a.core_archive)!=bool(a.core_sha256):raise SystemExit('Offline core requires both --core-archive and --core-sha256')
    if not re.fullmatch(r'v\d+\.\d+\.\d+',a.core_version):raise SystemExit('Invalid core version')
    if any(c in a.public_address for c in '/?#@ \r\n') or not a.public_address:raise SystemExit('Use a plain public IP or DNS name')
    if APP.exists() or CONF.exists() or DATA.exists():
        raise SystemExit('Existing DARK paths found. Nothing overwritten. Back up before an explicit upgrade; this is the fresh installer.')
    if Path('/usr/local/bin/darkxray').exists():raise SystemExit('An existing darkxray command was found; nothing overwritten')
    if a.port in [22,10085,*a.ssh_port]:raise SystemExit('Panel port overlaps SSH or the core API')
    if a.install_os_packages:
        run(['apt-get','update']);run(['apt-get','install','-y','python3-venv','ca-certificates'])
    try:account=pwd.getpwnam('darkxray')
    except KeyError:
        run(['useradd','--system','--home-dir',str(DATA),'--shell','/usr/sbin/nologin','darkxray'])
        account=pwd.getpwnam('darkxray')
    if account.pw_uid==0:raise SystemExit('Service account must not be root')
    # Copy source only. Developer databases, test reports, virtualenv and keys do not ship to the VPS.
    APP.mkdir(mode=0o755,parents=True);os.chmod(APP,0o755)
    for name in ['backend','web','tools','deploy']:
        shutil.copytree(ROOT/name,APP/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['darkxray','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md']:
        shutil.copy2(ROOT/name,APP/name)
    run([sys.executable,'-m','venv',str(APP/'.venv')])
    py=APP/'.venv/bin/python'
    run([py,'-m','pip','install','-r',APP/'requirements.txt'])
    core=Path('/usr/local/lib/dark-xray')/a.core_version
    if core.parent.is_symlink():raise SystemExit('Core parent symlink refused')
    core.parent.mkdir(parents=True,exist_ok=True,mode=0o755);os.chmod(core.parent,0o755)
    if a.core_archive:
        run([py,APP/'tools/import-core.py','--archive',a.core_archive.resolve(),'--sha256',a.core_sha256,'--destination',core])
    else:
        run([py,APP/'tools/fetch-core.py','--version',a.core_version,'--destination',core])
    run([core/'xray','version'])
    CONF.mkdir(mode=0o750);os.chmod(CONF,0o750);os.chown(CONF,0,account.pw_gid)
    DATA.mkdir(mode=0o700);os.chown(DATA,account.pw_uid,account.pw_gid)
    cfg=json.loads((ROOT/'config.example.json').read_text())
    cfg.update(public_origin=f'http://127.0.0.1:{a.port}',public_address=a.public_address,
               bind_port=a.port,xray_binary=str(core/'xray'),xray_assets=str(core),core_autostart=True,
               protected_ports=sorted(set([22,a.port,cfg['xray_api_port']]+a.ssh_port)))
    path=CONF/'config.json';path.write_text(json.dumps(cfg,indent=2));os.chmod(path,0o640);os.chown(path,0,account.pw_gid)
    for path in APP.rglob('*'):
        if path.is_symlink():continue
        os.chmod(path,0o755 if path.is_dir() else 0o644)
    for name in ['darkxray','.venv/bin/python','.venv/bin/pip']:
        path=APP/name
        if path.exists() and not path.is_symlink():os.chmod(path,0o755)
    # venv executables need to remain executable; source is root-owned and cannot
    # be overwritten by a compromised web account.
    for path in (APP/'.venv/bin').iterdir():
        if not path.is_symlink():os.chmod(path,0o755)
    run(['runuser','-u','darkxray','--',py,APP/'backend/server.py','--config',CONF/'config.json','--data',DATA,'init','--username',a.username])
    for name in ['dark-xray.service','dark-xray-guard.service']:
        shutil.copy2(APP/'deploy'/name,Path('/etc/systemd/system')/name)
    wrapper=Path('/usr/local/bin/darkxray')
    if wrapper.exists():raise SystemExit('Refusing to overwrite an existing /usr/local/bin/darkxray')
    wrapper.write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
export DARK_CONFIG=/etc/dark-xray/config.json DARK_DATA=/var/lib/dark-xray
# Keep SQLite WAL/lock/key files owned by the service account, even from root's menu.
case "${1:-menu}" in
  init|reset-password|check|serve|backup|doctor)
    if [[ $EUID -eq 0 ]]; then
      exec runuser -u darkxray -- env DARK_CONFIG="$DARK_CONFIG" DARK_DATA="$DARK_DATA" /opt/dark-xray/darkxray "$@"
    fi ;;
esac
exec /opt/dark-xray/darkxray "$@"
''')
    os.chmod(wrapper,0o755)
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','dark-xray.service'])
    print('\nDARK XRAY installed independently. Default web access is loopback only.')
    print(f'From your own computer: ssh -L {a.port}:127.0.0.1:{a.port} root@YOUR_SERVER -p YOUR_SSH_PORT')
    print(f'Open http://127.0.0.1:{a.port} after forwarding. No firewall was enabled.')
    print('Next: darkxray doctor; for TLS: darkxray domain --help; for reviewed IP enforcement: darkxray guard-enable --help')

if __name__=='__main__':main()

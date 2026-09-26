#!/usr/bin/env python3
"""Provision only the DARK XRAY Node Agent runtime on a clean Linux VPS."""
from __future__ import annotations
import argparse,base64,hashlib,json,os,pwd,re,secrets,shutil,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APP=Path('/opt/dark-xray-node');CONF=Path('/etc/dark-xray-node');DATA=Path('/var/lib/dark-xray-node')
SERVICE=Path('/etc/systemd/system/dark-xray-node.service')
GUARD_SERVICE=Path('/etc/systemd/system/dark-xray-node-guard.service')
UPDATE_SERVICE=Path('/etc/systemd/system/dark-xray-node-update.service')
WRAPPER=Path('/usr/local/bin/darknode')

def run(args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)

def core_is_usable(core:Path,version:str)->bool:
    xray=core/'xray'
    if not xray.is_file():return False
    try:r=subprocess.run([str(xray),'version'],capture_output=True,text=True,timeout=10,check=False)
    except Exception:return False
    return r.returncode==0 and version.lstrip('v') in (r.stdout or '')

def safe_name(value:str)->str:
    value=re.sub(r'[^A-Za-z0-9_.-]+','-',str(value or '').strip()).strip('-_.')
    return value[:80] or 'node'

def b64url(raw:bytes)->str:return base64.urlsafe_b64encode(raw).decode().rstrip('=')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain',required=True)
    p.add_argument('--port',type=int,default=8443)
    p.add_argument('--data-address',default='')
    p.add_argument('--name',default='')
    p.add_argument('--node-id',default='')
    p.add_argument('--ssh-port',type=int,action='append',default=[])
    p.add_argument('--core-version',default='v26.3.27')
    p.add_argument('--source-commit',default='')
    p.add_argument('--source-ref',default='node-install')
    p.add_argument('--cert',type=Path,required=True);p.add_argument('--key',type=Path,required=True)
    p.add_argument('--core-archive',type=Path);p.add_argument('--core-sha256')
    p.add_argument('--verified-direct-sources',action='store_true')
    p.add_argument('--exempt',action='append',default=[])
    a=p.parse_args()
    if os.geteuid()!=0 or sys.platform!='linux':raise SystemExit('Linux root is required')
    if sys.version_info<(3,10):raise SystemExit('Python 3.10+ required')
    if not Path('/run/systemd/system').exists():raise SystemExit('systemd host required')
    domain=str(a.domain).strip().lower()
    if not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}',domain):
        raise SystemExit('A valid DNS hostname is required for Node TLS')
    if not 1024<=a.port<=65535 or a.port in {22,10085,*a.ssh_port}:raise SystemExit('Invalid/conflicting Node Agent port')
    if not re.fullmatch(r'v\d+\.\d+\.\d+',a.core_version):raise SystemExit('Invalid Xray version')
    if a.source_commit and not re.fullmatch(r'[0-9a-f]{40}',str(a.source_commit).lower()):
        raise SystemExit('source-commit must be an immutable 40-character lowercase/uppercase SHA')
    if (not isinstance(a.source_ref,str) or not a.source_ref or len(a.source_ref)>128
            or '\r' in a.source_ref or '\n' in a.source_ref):
        raise SystemExit('Invalid source-ref')
    if bool(a.core_archive)!=bool(a.core_sha256):raise SystemExit('Offline core requires archive + sha256')
    if any(x.exists() for x in (APP,CONF,DATA,SERVICE,GUARD_SERVICE,UPDATE_SERVICE,WRAPPER)):
        raise SystemExit('Existing DARK Node/Panel artifacts found; clean or migrate explicitly before provisioning')
    if Path('/opt/dark-xray').exists() or Path('/etc/dark-xray').exists():
        raise SystemExit('A full DARK XRAY panel exists on this host; agent-only install is intentionally separate')
    for src in (a.cert,a.key):
        if src.is_symlink():
            src=src.resolve()
        if not src.is_file() or src.stat().st_size>1024*1024:raise SystemExit('TLS file missing/unsafe')
    try:account=pwd.getpwnam('darkxray')
    except KeyError:
        run(['useradd','--system','--home-dir',str(DATA),'--shell','/usr/sbin/nologin','darkxray'])
        account=pwd.getpwnam('darkxray')
    if account.pw_uid==0:raise SystemExit('Service account must not be root')

    APP.mkdir(parents=True,mode=0o755);os.chmod(APP,0o755);(APP/'backend').mkdir();(APP/'tools').mkdir();(APP/'deploy').mkdir()
    needed_backend=['node_agent.py','node_runtime.py','node_recovery_protocol.py','core.py','dark_policy.py','guard_bridge.py','guardd.py','reality_scan.py','node_updated.py','update_bridge.py']
    for name in needed_backend:shutil.copy2(ROOT/'backend'/name,APP/'backend'/name)
    for name in ['fetch-core.py','import-core.py','update_node.py','node_manager.py']:shutil.copy2(ROOT/'tools'/name,APP/'tools'/name)
    shutil.copy2(ROOT/'deploy'/'dark-xray-node.service',APP/'deploy'/'dark-xray-node.service')
    shutil.copy2(ROOT/'deploy'/'dark-xray-node-guard.service',APP/'deploy'/'dark-xray-node-guard.service')
    shutil.copy2(ROOT/'deploy'/'dark-xray-node-update.service',APP/'deploy'/'dark-xray-node-update.service')
    for name in ['requirements-node.txt','VERSION','LICENSE','THIRD-PARTY-NOTICES.md']:
        shutil.copy2(ROOT/name,APP/name)
    run([sys.executable,'-m','venv',APP/'.venv'])
    py=APP/'.venv/bin/python'
    run([py,'-m','pip','install','-q','--disable-pip-version-check','-r',APP/'requirements-node.txt'])

    core=Path('/usr/local/lib/dark-xray')/a.core_version
    core.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    if core.exists() and core_is_usable(core,a.core_version):
        pass
    else:
        if core.exists():shutil.rmtree(core)
        if a.core_archive:
            run([py,APP/'tools/import-core.py','--archive',a.core_archive.resolve(),'--sha256',a.core_sha256,'--destination',core])
        else:
            run([py,APP/'tools/fetch-core.py','--version',a.core_version,'--destination',core])
    run([core/'xray','version'])

    CONF.mkdir(mode=0o750);os.chmod(CONF,0o750);os.chown(CONF,0,account.pw_gid)
    DATA.mkdir(mode=0o700);os.chmod(DATA,0o700);os.chown(DATA,account.pw_uid,account.pw_gid)
    tls=CONF/'tls';tls.mkdir(mode=0o750);os.chmod(tls,0o750);os.chown(tls,0,account.pw_gid)
    cert=tls/'cert.pem';key=tls/'key.pem'
    cert.write_bytes(a.cert.resolve().read_bytes());key.write_bytes(a.key.resolve().read_bytes())
    os.chmod(cert,0o640);os.chmod(key,0o640);os.chown(cert,0,account.pw_gid);os.chown(key,0,account.pw_gid)

    token='dkn_'+secrets.token_urlsafe(48)
    token_path=DATA/'token';token_path.write_text(token+'\n',encoding='utf-8')
    os.chmod(token_path,0o600);os.chown(token_path,account.pw_uid,account.pw_gid)
    name=safe_name(a.name or os.uname().nodename)
    node_id=safe_name(a.node_id or ('node-'+name+'-'+secrets.token_hex(3)))
    node_id_path=DATA/'node-id';node_id_path.write_text(node_id+'\n',encoding='utf-8')
    os.chmod(node_id_path,0o600);os.chown(node_id_path,account.pw_uid,account.pw_gid)
    data_address=(a.data_address or domain).strip()
    protected=sorted(set([22,10085,a.port,*a.ssh_port]))
    cfg={'public_origin':f'https://{domain}'+(f':{a.port}' if a.port!=443 else ''),
         'panel_path':'/','xray_binary':str(core/'xray'),'xray_assets':str(core),'xray_api_port':10085,
         'public_address':data_address,'writes_enabled':True,'secure_cookie':True,'poll_seconds':5,
         'core_autostart':True,'ip_window_seconds':120,'direct_source_verified':bool(a.verified_direct_sources),
         'protected_ports':protected,'test_engine':False,'bind_host':'0.0.0.0','bind_port':a.port,
         'tls_certificate':str(cert),'tls_private_key':str(key),'guard_socket':'/run/dark-xray-guard/control.sock',
         'ip_ban_seconds':1800,'ip_exempt_ips':[]}
    cfg_path=CONF/'config.json';cfg_path.write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
    os.chmod(cfg_path,0o640);os.chown(cfg_path,0,account.pw_gid)
    nft=shutil.which('nft')
    if not nft:raise SystemExit('nftables is required for the Node Guard runtime')
    guard={'allowed_uid':account.pw_uid,'allowed_ports':[],'protected_ports':protected,
           'direct_source_verified':bool(a.verified_direct_sources),'max_ban_seconds':86400,
           'exempt_ips':a.exempt,'nft_binary':nft,'socket_path':'/run/dark-xray-guard/control.sock',
           'allow_runtime_port_updates':True}
    # Validate with the same root-broker parser before writing the privileged contract.
    sys.path.insert(0,str(APP/'backend'))
    from guardd import BrokerConfig
    BrokerConfig.from_dict(guard)
    guard_path=CONF/'guard.json';guard_path.write_text(json.dumps(guard,indent=2)+'\n',encoding='utf-8')
    os.chmod(guard_path,0o600);os.chown(guard_path,0,0)

    for path in APP.rglob('*'):
        if path.is_symlink():continue
        os.chmod(path,0o755 if path.is_dir() else 0o644)
    for path in (APP/'.venv/bin').iterdir():
        if not path.is_symlink():os.chmod(path,0o755)

    shutil.copy2(APP/'deploy'/'dark-xray-node.service',SERVICE)
    shutil.copy2(APP/'deploy'/'dark-xray-node-guard.service',GUARD_SERVICE)
    shutil.copy2(APP/'deploy'/'dark-xray-node-update.service',UPDATE_SERVICE)
    os.chmod(SERVICE,0o644);os.chmod(GUARD_SERVICE,0o644);os.chmod(UPDATE_SERVICE,0o644)
    WRAPPER.write_text("""#!/usr/bin/env bash
set -Eeuo pipefail
PY=/opt/dark-xray-node/.venv/bin/python
MANAGER=/opt/dark-xray-node/tools/node_manager.py
[[ -x "$PY" && -f "$MANAGER" ]] || { echo "DARK Node Manager is missing; run the Node updater/repair." >&2; exit 1; }
exec "$PY" "$MANAGER" "$@"
""",encoding='utf-8');os.chmod(WRAPPER,0o755)

    source_commit=str(a.source_commit or '').lower()
    if not source_commit:
        source_cp=subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True,text=True,check=False)
        source_commit=source_cp.stdout.strip().lower() if source_cp.returncode==0 else ''
    source={'commit':source_commit,'version':(APP/'VERSION').read_text().strip(),
            'ref':a.source_ref,'installed_at':time.time(),'role':'node-agent'}
    source_path=DATA/'installed-source.json';source_path.write_text(json.dumps(source,indent=2)+'\n')
    os.chmod(source_path,0o640);os.chown(source_path,0,account.pw_gid)
    profile_path=DATA/'node-profile.json'
    profile_path.write_text(json.dumps({'name':name,'priority':100,'failoverEnabled':True},indent=2)+'\n',encoding='utf-8')
    os.chmod(profile_path,0o640);os.chown(profile_path,0,account.pw_gid)

    pair={'schema':1,'nodeId':node_id,'name':name,'origin':cfg['public_origin'],'token':token,
          'dataAddress':data_address,'priority':100,'failoverEnabled':True}
    pair_code='DXN1.'+b64url(json.dumps(pair,separators=(',',':')).encode())
    pair_doc={**pair,'pairCode':pair_code,'sensitive':True,'displayedOnce':True}
    pair_path=DATA/'pair.json';pair_path.write_text(json.dumps(pair_doc,indent=2)+'\n')
    os.chmod(pair_path,0o600);os.chown(pair_path,account.pw_uid,account.pw_gid)

    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','dark-xray-node-guard.service']);run(['systemctl','enable','--now','dark-xray-node-update.service']);run(['systemctl','enable','--now','dark-xray-node.service'])
    print(json.dumps({'installed':True,'agent_only':True,'service':'dark-xray-node.service',
                      'origin':cfg['public_origin'],'nodeId':node_id,'pairCode':pair_code,'directSourceVerified':bool(a.verified_direct_sources)},indent=2))
if __name__=='__main__':main()

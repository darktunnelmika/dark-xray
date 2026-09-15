#!/usr/bin/env python3
from pathlib import Path


def once(path, old, new):
    p=Path(path); s=p.read_text()
    if old not in s:
        raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    p.write_text(s.replace(old,new,1))

# Reject URI paths that collide with DARK root-level endpoints.
old="""        if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):
            raise ValueError('panel_path must be / or slash-prefixed alphanumeric/_/- segments')
        if len(panel_path)>200:raise ValueError('panel_path is too long')
        self.panel_path=panel_path"""
new="""        if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):
            raise ValueError('panel_path must be / or slash-prefixed alphanumeric/_/- segments')
        if len(panel_path)>200:raise ValueError('panel_path is too long')
        first_segment=panel_path.strip('/').split('/',1)[0].lower() if panel_path!='/' else ''
        if first_segment in {'api','assets','sub','node','health'}:
            raise ValueError('panel_path conflicts with a reserved DARK endpoint')
        self.panel_path=panel_path"""
once('backend/core.py',old,new)

old="""            if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):raise CoreError('Invalid panel URI path')
            if len(panel_path)>200:raise CoreError('Panel URI path is too long')
            value['panel_path']=panel_path"""
new="""            if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):raise CoreError('Invalid panel URI path')
            if len(panel_path)>200:raise CoreError('Panel URI path is too long')
            first_segment=panel_path.strip('/').split('/',1)[0].lower() if panel_path!='/' else ''
            if first_segment in {'api','assets','sub','node','health'}:raise CoreError('Panel URI path conflicts with a reserved DARK endpoint')
            value['panel_path']=panel_path"""
once('backend/core.py',old,new)

old="""    if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):
        raise ValueError('Invalid panel URI path')
    if len(panel_path)>200:raise ValueError('Panel URI path is too long')
    v['panel_path']=panel_path"""
new="""    if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):
        raise ValueError('Invalid panel URI path')
    if len(panel_path)>200:raise ValueError('Panel URI path is too long')
    first_segment=panel_path.strip('/').split('/',1)[0].lower() if panel_path!='/' else ''
    if first_segment in {'api','assets','sub','node','health'}:raise ValueError('Panel URI path conflicts with a reserved DARK endpoint')
    v['panel_path']=panel_path"""
once('tools/settings_apply.py',old,new)

old="""PATH_RE=re.compile(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*')

def normalize(value:str)->str:
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    if value!='/' and not PATH_RE.fullmatch(value):raise SystemExit('URI path must be / or slash-prefixed alphanumeric/_/- segments')
    if len(value)>200:raise SystemExit('URI path is too long')
    return value"""
new="""PATH_RE=re.compile(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*')
RESERVED={'api','assets','sub','node','health'}

def normalize(value:str)->str:
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    if value!='/' and not PATH_RE.fullmatch(value):raise SystemExit('URI path must be / or slash-prefixed alphanumeric/_/- segments')
    if len(value)>200:raise SystemExit('URI path is too long')
    first=value.strip('/').split('/',1)[0].lower() if value!='/' else ''
    if first in RESERVED:raise SystemExit('URI path conflicts with a reserved DARK endpoint')
    return value"""
once('tools/runtime_stage.py',old,new)

old="""    if a.panel_path!='/' and (len(a.panel_path)>200 or not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',a.panel_path)):
        raise SystemExit('Invalid panel URI path')"""
new="""    if a.panel_path!='/' and (len(a.panel_path)>200 or not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',a.panel_path)):
        raise SystemExit('Invalid panel URI path')
    first_segment=a.panel_path.strip('/').split('/',1)[0].lower() if a.panel_path!='/' else ''
    if first_segment in {'api','assets','sub','node','health'}:raise SystemExit('Panel URI path conflicts with a reserved DARK endpoint')"""
once('tools/provision.py',old,new)

old='valid_panel_path(){ [[ "$1" == / || ( ${#1} -le 200 && "$1" =~ ^/([A-Za-z0-9_-]{1,64})(/[A-Za-z0-9_-]{1,64})*$ ) ]]; }'
new='valid_panel_path(){ local v="$1" first; [[ "$v" == / ]] && return 0; [[ ${#v} -le 200 && "$v" =~ ^/([A-Za-z0-9_-]{1,64})(/[A-Za-z0-9_-]{1,64})*$ ]] || return 1; first="${v#/}"; first="${first%%/*}"; first="${first,,}"; [[ "$first" != api && "$first" != assets && "$first" != sub && "$first" != node && "$first" != health ]]; }'
once('install-online.sh',old,new)
once('install-online.sh',
     '  valid_user dark || fail "valid_user rejected dark"; valid_domain panel.example.com || fail "valid_domain rejected panel.example.com"',
     '  valid_user dark || fail "valid_user rejected dark"; valid_domain panel.example.com || fail "valid_domain rejected panel.example.com"\n  valid_panel_path /dark-admin || fail "valid_panel_path rejected /dark-admin"; ! valid_panel_path /sub || fail "valid_panel_path accepted reserved /sub"')

# Owner recovery verifies the resulting hash before commit/success.
once('backend/owner_recovery.py',
     'from policy_auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, password_hash',
     'from policy_auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, password_hash, verify_password')
old="""        db.execute('INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)',(username,'owner',hashed,'{}'))
        if _table_exists(db,'owners'):db.execute('INSERT OR IGNORE INTO owners(id) VALUES(?)',(username,))"""
new="""        db.execute('INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)',(username,'owner',hashed,'{}'))
        written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(username,)).fetchone()
        if not written or not verify_password(password,written['password_hash']):raise PolicyError('Owner password verification failed; transaction rolled back')
        if _table_exists(db,'owners'):db.execute('INSERT OR IGNORE INTO owners(id) VALUES(?)',(username,))"""
once('backend/owner_recovery.py',old,new)
once('backend/owner_recovery.py',
     "return {'username':username,'created':True,'role':'owner','profile_attached':True}",
     "return {'username':username,'created':True,'role':'owner','profile_attached':True,'password_verified':True}")
old="""        db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,username))
        _revoke_sessions_tx(db,username);db.execute('COMMIT')
        return {'username':username,'sessions_revoked':True,'totp_preserved':True}"""
new="""        db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,username))
        written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(username,)).fetchone()
        if not written or not verify_password(password,written['password_hash']):raise PolicyError('Owner password verification failed; transaction rolled back')
        _revoke_sessions_tx(db,username);db.execute('COMMIT')
        return {'username':username,'sessions_revoked':True,'totp_preserved':True,'password_verified':True}"""
once('backend/owner_recovery.py',old,new)

# Terminal: only print success after exit 0; owner profile picker; random URI path.
once('tools/menu.py',
     'import json, os, shutil, socket, sqlite3, subprocess, sys, tempfile, time',
     'import json, os, secrets, shutil, socket, sqlite3, subprocess, sys, tempfile, time')
old="""def run(args,capture=False,check=False):
    try:
        return subprocess.run([str(x) for x in args],text=True,check=check,
            stdout=subprocess.PIPE if capture else None,stderr=subprocess.STDOUT if capture else None)
    except (FileNotFoundError,subprocess.CalledProcessError) as e:
        print(f'{RE}Command failed:{R} {args[0]}');return e
"""
new=old+"""
def run_action(args,success):
    cp=run(args)
    if getattr(cp,'returncode',1)==0:
        print(f'{GR}{success}{R}')
        return True
    print(f'{RE}Action failed. DARK did not assume the requested change succeeded.{R}')
    return False
"""
once('tools/menu.py',old,new)
old="""def valid_panel_path(value):
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    return len(value)<=200 and (value=='/' or bool(__import__('re').fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',value)))"""
new="""def valid_panel_path(value):
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    if len(value)>200 or not (value=='/' or bool(__import__('re').fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',value))):return False
    first=value.strip('/').split('/',1)[0].lower() if value!='/' else ''
    return first not in {'api','assets','sub','node','health'}"""
once('tools/menu.py',old,new)
old="""        if x=='2':
            print(f'{GY}This creates a real panel LOGIN Owner. An owner profile alone cannot sign in.{R}')
            user=ask('New owner login username')
            if user and confirm(f'Create a full Owner login named {user}?','CREATE'):
                run([COMMAND,'account','--username',user,'--action','create-owner']);pause()
            continue"""
new="""        if x=='2':
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
            continue"""
once('tools/menu.py',old,new)
once('tools/menu.py',
     "                run([COMMAND,'reset-password','--username',user]);pause()",
     "                run_action([COMMAND,'reset-password','--username',user],f'Password changed for {user}; password write verified and sessions revoked.');pause()")
once('tools/menu.py',
     "        item(4,'Change panel URI path',str(c.get('panel_path','/')))\n        item(5,'Show panel URL / SSH tunnel')",
     "        item(4,'Change panel URI path',str(c.get('panel_path','/')))\n        item(11,'Generate random panel URI path','secure 96-bit path token')\n        item(5,'Show panel URL / SSH tunnel')")
once('tools/menu.py',
     "        elif x=='5':show_access();pause()",
     """        elif x=='11':
            v='/dark-'+secrets.token_hex(12)
            if run_action([COMMAND,'stage-panel-path','--panel-path',v],f'Random URI path staged: {v}'):
                print(f'{YE}Apply staged settings to activate the new URL.{R}')
                if need_root() and confirm('Apply random URI path now?'):
                    run_action([COMMAND,'settings-apply'],'Random URI path applied; panel service restarted.')
                    print(f'{GR}Panel URL: {endpoint(cfg())}{R}')
            pause()
        elif x=='5':show_access();pause()""")

# Web Settings random URI path helper.
old="""${field(L('Panel URI path','مسیر URI پنل'),'panel_path',r.panel_path||'/','text',L('Example: /dark-admin — / keeps the panel at root.','مثال: /dark-admin — / یعنی مسیر ریشه.'),'dir=\"ltr\" maxlength=\"200\" required')}"""
new="""${field(L('Panel URI path','مسیر URI پنل'),'panel_path',r.panel_path||'/','text',L('Example: /dark-admin. Reserved: /api, /assets, /sub, /node, /health.','مثال: /dark-admin. مسیرهای /api، /assets، /sub، /node و /health رزرو هستند.'),'dir=\"ltr\" maxlength=\"200\" required')}<button type=\"button\" class=\"btn\" data-sv2-action=\"random-path\">${ico('refresh')}${L('Generate random path','ساخت مسیر تصادفی')}</button>"""
once('web/settings-v2.js',old,new)
once('web/settings-v2.js',
     "if(act==='account'){await go('account');return;}if(act==='core-validate')",
     "if(act==='account'){await go('account');return;}if(act==='random-path'){const bytes=new Uint8Array(12);crypto.getRandomValues(bytes);const token=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');const form=el.closest('form');if(form?.elements?.panel_path)form.elements.panel_path.value='/dark-'+token;return;}if(act==='core-validate')")

# Backup metadata must respect the panel prefix.
once('backend/server.py',
     "'database_download':'/api/backup','full_backup_command':'sudo darkxray backup --output /root/dark-full.darkbackup',",
     "'database_download':(config.panel_path if config.panel_path!='/' else '')+'/api/backup','full_backup_command':'sudo darkxray backup --output /root/dark-full.darkbackup',")

# Doctor: probe the configured UI route and static asset locally.
p=Path('tools/doctor.py');s=p.read_text()
s=s.replace('import argparse,json,os,shutil,socket,sqlite3,subprocess,sys', 'import argparse,json,os,shutil,socket,sqlite3,ssl,subprocess,sys')
anchor='from guard_bridge import BrokerClient\n'
helper=r'''

def _panel_http_status(cfg:Config,path:str)->int:
    origin=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(cfg.public_origin)
    target='127.0.0.1' if cfg.bind_host in {'0.0.0.0','::'} else cfg.bind_host
    sock=socket.create_connection((target,cfg.bind_port),timeout=2.5)
    try:
        if origin.scheme=='https':
            ctx=ssl.create_default_context();sock=ctx.wrap_socket(sock,server_hostname=origin.hostname)
        req=f'GET {path} HTTP/1.1\r\nHost: {origin.netloc}\r\nConnection: close\r\nUser-Agent: darkxray-doctor\r\n\r\n'.encode()
        sock.sendall(req);raw=b''
        while b'\r\n' not in raw and len(raw)<4096:
            chunk=sock.recv(512)
            if not chunk:break
            raw+=chunk
        first=raw.split(b'\r\n',1)[0].decode('ascii','replace').split()
        return int(first[1]) if len(first)>=2 and first[1].isdigit() else 0
    finally:
        try:sock.close()
        except Exception:pass

'''
if helper.strip() not in s:
    if anchor not in s:raise SystemExit('doctor import anchor missing')
    s=s.replace(anchor,anchor+helper,1)
old="""        checks['tls_configured']=bool(cfg.tls_certificate and cfg.tls_private_key)
        checks['protected_ports']=cfg.protected_ports"""
new="""        checks['tls_configured']=bool(cfg.tls_certificate and cfg.tls_private_key)
        checks['panel_url']=cfg.public_origin+(cfg.panel_path if cfg.panel_path!='/' else '')+'/'
        try:
            base=(cfg.panel_path if cfg.panel_path!='/' else '')+'/'
            ui_status=_panel_http_status(cfg,base);asset_status=_panel_http_status(cfg,base+'assets/style.css')
            checks['panel_route']={'ui_status':ui_status,'asset_status':asset_status,'ok':ui_status==200 and asset_status==200}
            if cfg.panel_path!='/':checks['root_hidden_status']=_panel_http_status(cfg,'/')
        except Exception as ex:checks['panel_route']={'ok':False,'error':type(ex).__name__+': '+str(ex)[:180]}
        checks['protected_ports']=cfg.protected_ports"""
if old not in s:raise SystemExit('doctor checks anchor missing')
p.write_text(s.replace(old,new,1))

# Regression tests.
p=Path('tests/test_settings_v2.py');s=p.read_text()
marker='def test_domain_mode_requires_domain_and_email(env):'
test=r'''

def test_reserved_panel_uri_paths_are_rejected(env,tmp_path):
    _,_,c=env
    runtime=c.get('/api/settings/runtime').json()['value']
    for path in ('/api','/assets','/sub','/node','/health','/sub/private'):
        candidate=dict(runtime,panel_path=path)
        assert c.put('/api/settings/runtime',json={'value':candidate}).status_code==422
        with pytest.raises(ValueError):
            Config(xray_binary=str(tmp_path/'xray'),xray_assets=str(tmp_path),panel_path=path,test_engine=True)


'''
if 'test_reserved_panel_uri_paths_are_rejected' not in s:
    if marker not in s:raise SystemExit('settings test marker missing')
    s=s.replace(marker,test+marker,1)
p.write_text(s)

p=Path('tests/test_owner_recovery.py');s=p.read_text()
s=s.replace("assert result=={'username':'dark','sessions_revoked':True,'totp_preserved':True}","assert result=={'username':'dark','sessions_revoked':True,'totp_preserved':True,'password_verified':True}")
s=s.replace("assert result['created'] is True and result['role']=='owner'","assert result['created'] is True and result['role']=='owner' and result['password_verified'] is True")
p.write_text(s)

# CI smoke coverage.
p=Path('.github/workflows/ci.yml');s=p.read_text()
s=s.replace('tools/menu.py tools/update.py tools/provision.py tools/settings_apply.py tools/runtime_stage.py \\\n', 'tools/menu.py tools/update.py tools/provision.py tools/settings_apply.py tools/runtime_stage.py tools/doctor.py \\\n')
needle="          grep -q 'Panel URI path' web/settings-v2.js\n"
if needle in s and "Generate random path" not in s:
    s=s.replace(needle,needle+"          grep -q 'Generate random path' web/settings-v2.js\n          grep -q 'panel_route' tools/doctor.py\n",1)
p.write_text(s)

print('stabilization patch applied')

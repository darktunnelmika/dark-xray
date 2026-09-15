#!/usr/bin/env python3
from pathlib import Path
import re


def once(path, old, new):
    p=Path(path); s=p.read_text()
    if new in s:
        return
    if old not in s:
        raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    p.write_text(s.replace(old,new,1))


def regex_once(path, pattern, replacement):
    p=Path(path); s=p.read_text()
    out,n=re.subn(pattern,replacement,s,count=1,flags=re.S)
    if n!=1:
        raise SystemExit(f'regex anchor missing/ambiguous in {path}: {pattern[:100]!r} count={n}')
    p.write_text(out)

# ---------------------------------------------------------------------------
# Core Config + staged runtime schema
# ---------------------------------------------------------------------------
once('backend/core.py',
"    public_origin: str = 'http://127.0.0.1:2087'\n    xray_binary: str = '/usr/local/lib/dark-xray/xray'",
"    public_origin: str = 'http://127.0.0.1:2087'\n    panel_path: str = '/'\n    xray_binary: str = '/usr/local/lib/dark-xray/xray'")

once('backend/core.py',
"        self.public_origin=self.public_origin.rstrip('/')\n        if p.scheme=='https' and not self.secure_cookie: raise ValueError('HTTPS requires secure_cookie=true')",
"        self.public_origin=self.public_origin.rstrip('/')\n        panel_path=str(self.panel_path or '/').strip()\n        if panel_path!='/' and panel_path.endswith('/'):panel_path=panel_path.rstrip('/')\n        if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):\n            raise ValueError('panel_path must be / or slash-prefixed alphanumeric/_/- segments')\n        if len(panel_path)>200:raise ValueError('panel_path is too long')\n        self.panel_path=panel_path\n        if p.scheme=='https' and not self.secure_cookie: raise ValueError('HTTPS requires secure_cookie=true')")

once('backend/core.py',
"                  'runtime':{'access_mode':access_mode,'bind_port':self.config.bind_port,'public_address':self.config.public_address,\n                             'poll_seconds':self.config.poll_seconds,'core_autostart':self.config.core_autostart,",
"                  'runtime':{'access_mode':access_mode,'bind_port':self.config.bind_port,'public_address':self.config.public_address,\n                             'panel_path':self.config.panel_path,'poll_seconds':self.config.poll_seconds,'core_autostart':self.config.core_autostart,")

once('backend/core.py',
"            allowed={'access_mode','bind_port','public_address','poll_seconds','core_autostart','domain','acme_email'}",
"            allowed={'access_mode','bind_port','public_address','panel_path','poll_seconds','core_autostart','domain','acme_email'}")

once('backend/core.py',
"            if type(value['core_autostart']) is not bool:raise CoreError('core_autostart must be boolean')\n            addr=value['public_address']",
"            if type(value['core_autostart']) is not bool:raise CoreError('core_autostart must be boolean')\n            panel_path=str(value.get('panel_path','')).strip()\n            if panel_path!='/' and panel_path.endswith('/'):panel_path=panel_path.rstrip('/')\n            if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):raise CoreError('Invalid panel URI path')\n            if len(panel_path)>200:raise CoreError('Panel URI path is too long')\n            value['panel_path']=panel_path\n            addr=value['public_address']")

# ---------------------------------------------------------------------------
# Server: prefix panel UI/API/assets under panel_path. Health, subscription and
# node-agent data-plane endpoints deliberately stay stable/unprefixed.
# ---------------------------------------------------------------------------
once('backend/server.py',
"from fastapi.responses import JSONResponse,FileResponse,Response",
"from fastapi.responses import JSONResponse,FileResponse,Response,RedirectResponse")

once('backend/server.py',
"    public=urlsplit(config.public_origin)\n\n    @app.middleware('http')\n    async def security(request:Request,call_next):\n        if request.headers.get('host','').lower()!=public.netloc.lower():",
"    public=urlsplit(config.public_origin);panel_path=config.panel_path\n\n    @app.middleware('http')\n    async def security(request:Request,call_next):\n        raw_path=request.scope.get('path','/') or '/'\n        stable_public=(raw_path=='/health' or raw_path.startswith('/sub/') or raw_path.startswith('/node/api/'))\n        if panel_path!='/' and not stable_public:\n            if raw_path==panel_path:\n                target=panel_path+'/'\n                if request.url.query:target+='?'+request.url.query\n                return RedirectResponse(target,status_code=307)\n            if not raw_path.startswith(panel_path+'/'):\n                return JSONResponse({'detail':'Not Found'},404)\n            request.scope['root_path']=panel_path\n            request.scope['path']=raw_path[len(panel_path):] or '/'\n        if request.headers.get('host','').lower()!=public.netloc.lower():")

once('backend/server.py',
"response.set_cookie(COOKIE,token,max_age=session_minutes*60,httponly=True,secure=config.secure_cookie,samesite='strict',path='/')",
"response.set_cookie(COOKIE,token,max_age=session_minutes*60,httponly=True,secure=config.secure_cookie,samesite='strict',path=panel_path)")

once('backend/server.py',
"'poll_seconds':config.poll_seconds,'engine_version':engine.version,'independent':True,'test_engine':config.test_engine,",
"'poll_seconds':config.poll_seconds,'engine_version':engine.version,'independent':True,'test_engine':config.test_engine,'panel_path':panel_path,")

once('backend/server.py',
"response=JSONResponse({'revoked':True});response.delete_cookie(COOKIE,path='/');return response",
"response=JSONResponse({'revoked':True});response.delete_cookie(COOKIE,path=panel_path);return response")

once('backend/server.py',
"        actual={'access_mode':mode,'bind_host':config.bind_host,'bind_port':config.bind_port,'public_address':config.public_address,\n                'public_origin':config.public_origin,'poll_seconds':config.poll_seconds,'core_autostart':config.core_autostart,",
"        actual={'access_mode':mode,'bind_host':config.bind_host,'bind_port':config.bind_port,'public_address':config.public_address,\n                'public_origin':config.public_origin,'panel_path':config.panel_path,'panel_url':config.public_origin+(config.panel_path if config.panel_path!='/' else '')+'/',\n                'poll_seconds':config.poll_seconds,'core_autostart':config.core_autostart,")

once('backend/server.py',
"        compare=('access_mode','bind_port','public_address','poll_seconds','core_autostart','domain')",
"        compare=('access_mode','bind_port','public_address','panel_path','poll_seconds','core_autostart','domain')")

# ---------------------------------------------------------------------------
# Settings apply + staging helper
# ---------------------------------------------------------------------------
once('tools/settings_apply.py',
"RUNTIME_KEYS = {'access_mode','bind_port','public_address','poll_seconds','core_autostart','domain','acme_email'}",
"RUNTIME_KEYS = {'access_mode','bind_port','public_address','panel_path','poll_seconds','core_autostart','domain','acme_email'}")

once('tools/settings_apply.py',
"def _runtime_row(db_path: Path) -> dict:\n    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:\n        row = db.execute(\"SELECT body FROM core_sections WHERE name='runtime'\").fetchone()\n    if not row:\n        raise SystemExit('No staged runtime settings found. Save Network / Domain settings in the panel first.')\n    value = json.loads(row[0])\n    if not isinstance(value, dict) or set(value) - RUNTIME_KEYS:\n        raise SystemExit('Staged runtime settings have an invalid shape')\n    return value",
"def _runtime_row(db_path: Path, current: dict) -> dict:\n    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:\n        row = db.execute(\"SELECT body FROM core_sections WHERE name='runtime'\").fetchone()\n    if not row:\n        raise SystemExit('No staged runtime settings found. Save Network / Domain settings in the panel first.')\n    value = json.loads(row[0])\n    if not isinstance(value, dict) or set(value) - RUNTIME_KEYS:\n        raise SystemExit('Staged runtime settings have an invalid shape')\n    # Backward-compatible hydration for installations that saved Settings V2 before\n    # panel_path existed. The next save persists the full shape.\n    value.setdefault('panel_path',str(current.get('panel_path','/')))\n    return value")

once('tools/settings_apply.py',
"    if type(v.get('core_autostart')) is not bool:\n        raise ValueError('core_autostart must be boolean')\n    address = v.get('public_address','')",
"    if type(v.get('core_autostart')) is not bool:\n        raise ValueError('core_autostart must be boolean')\n    panel_path=str(v.get('panel_path','/')).strip()\n    if panel_path!='/' and panel_path.endswith('/'):panel_path=panel_path.rstrip('/')\n    if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):\n        raise ValueError('Invalid panel URI path')\n    if len(panel_path)>200:raise ValueError('Panel URI path is too long')\n    v['panel_path']=panel_path\n    address = v.get('public_address','')")

once('tools/settings_apply.py',
"        'public_address':str(current.get('public_address','')),\n        'poll_seconds':int(current.get('poll_seconds',5)),",
"        'public_address':str(current.get('public_address','')),\n        'panel_path':str(current.get('panel_path','/')),\n        'poll_seconds':int(current.get('poll_seconds',5)),")

once('tools/settings_apply.py',
"    current.update(public_address=desired['public_address'],poll_seconds=desired['poll_seconds'],core_autostart=desired['core_autostart'])",
"    current.update(public_address=desired['public_address'],panel_path=desired['panel_path'],poll_seconds=desired['poll_seconds'],core_autostart=desired['core_autostart'])")

once('tools/settings_apply.py',
"    desired=validate_desired(_runtime_row(db_path),current,_inbound_ports(db_path))",
"    desired=validate_desired(_runtime_row(db_path,current),current,_inbound_ports(db_path))")

# New service-user staging helper used by the terminal menu.
Path('tools/runtime_stage.py').write_text(r'''#!/usr/bin/env python3
"""Stage safe DARK runtime settings as the unprivileged service account."""
from __future__ import annotations
import argparse,json,re,sqlite3
from pathlib import Path
from urllib.parse import urlsplit

PATH_RE=re.compile(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*')

def normalize(value:str)->str:
    value=str(value or '/').strip()
    if value!='/' and value.endswith('/'):value=value.rstrip('/')
    if value!='/' and not PATH_RE.fullmatch(value):raise SystemExit('URI path must be / or slash-prefixed alphanumeric/_/- segments')
    if len(value)>200:raise SystemExit('URI path is too long')
    return value

def defaults(config:dict)->dict:
    origin=urlsplit(str(config.get('public_origin','http://127.0.0.1:2087')))
    mode='domain_tls' if origin.scheme=='https' else 'ssh'
    return {'access_mode':mode,'bind_port':int(config.get('bind_port',2087)),'public_address':str(config.get('public_address','127.0.0.1')),
            'panel_path':str(config.get('panel_path','/')),'poll_seconds':int(config.get('poll_seconds',5)),
            'core_autostart':bool(config.get('core_autostart',False)),'domain':(origin.hostname or '') if mode=='domain_tls' else '','acme_email':''}

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--panel-path',required=True)
    a=p.parse_args();config=json.loads(a.config.read_text());value=defaults(config);dbpath=a.data/'dark.sqlite3'
    if dbpath.is_symlink() or not dbpath.is_file():raise SystemExit('DARK database is missing or unsafe')
    with sqlite3.connect(str(dbpath),timeout=30) as db:
        row=db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
        if row:
            saved=json.loads(row[0])
            if not isinstance(saved,dict):raise SystemExit('Saved runtime settings are invalid')
            value.update(saved)
        value['panel_path']=normalize(a.panel_path)
        db.execute("INSERT INTO core_sections(name,body) VALUES('runtime',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",(json.dumps(value),))
        db.commit()
    print('Panel URI path staged:',value['panel_path'])

if __name__=='__main__':main()
''')

# ---------------------------------------------------------------------------
# Owner recovery: real owner-login creation + clearer profile/login semantics.
# ---------------------------------------------------------------------------
once('backend/owner_recovery.py',
"def reset_owner_password(db_path: Path, username: str, password: str) -> dict:",
"def create_owner_account(db_path: Path, username: str, password: str) -> dict:\n    \"\"\"Create a real login owner, optionally attaching an existing owner profile.\"\"\"\n    if not NAME_RE.fullmatch(username):raise PolicyError('Invalid owner username')\n    hashed=password_hash(password)\n    db=_connect(db_path)\n    try:\n        db.execute('BEGIN IMMEDIATE')\n        if db.execute('SELECT 1 FROM api_admins WHERE id=?',(username,)).fetchone():raise PolicyError('A login account with this username already exists')\n        db.execute('INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES(?,?,?,?,0)',(username,'owner',hashed,'{}'))\n        if _table_exists(db,'owners'):db.execute('INSERT OR IGNORE INTO owners(id) VALUES(?)',(username,))\n        if _table_exists(db,'owner_profiles'):\n            row=db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(username,)).fetchone()\n            if not row:\n                allowed=[]\n                if _table_exists(db,'core_inbounds'):allowed=[r[0] for r in db.execute('SELECT id FROM core_inbounds ORDER BY id')]\n                db.execute('INSERT INTO owner_profiles(id,name,allowed) VALUES(?,?,?)',(username,username,json.dumps(allowed)))\n        db.execute('COMMIT')\n        return {'username':username,'created':True,'role':'owner','profile_attached':True}\n    except BaseException:\n        try:db.execute('ROLLBACK')\n        except sqlite3.Error:pass\n        raise\n    finally:db.close()\n\n\ndef reset_owner_password(db_path: Path, username: str, password: str) -> dict:")

once('backend/owner_recovery.py',
"    p.add_argument('--action',choices=('password','rename','revoke-sessions','revoke-api-keys','disable-totp','status'),default='password')",
"    p.add_argument('--action',choices=('create-owner','password','rename','revoke-sessions','revoke-api-keys','disable-totp','status'),default='password')")

once('backend/owner_recovery.py',
"        if args.action=='password':result=reset_owner_password(db,args.username,_read_password())\n        elif args.action=='rename':",
"        if args.action=='create-owner':result=create_owner_account(db,args.username,_read_password())\n        elif args.action=='password':result=reset_owner_password(db,args.username,_read_password())\n        elif args.action=='rename':")

# ---------------------------------------------------------------------------
# Terminal UX: strict owner selector, profile/login distinction, URI path staging.
# ---------------------------------------------------------------------------
once('tools/menu.py',
"def endpoint(c):\n    return str(c.get('public_origin') or f\"http://{c.get('bind_host','127.0.0.1')}:{c.get('bind_port',2087)}\")",
"def endpoint(c):\n    origin=str(c.get('public_origin') or f\"http://{c.get('bind_host','127.0.0.1')}:{c.get('bind_port',2087)}\").rstrip('/')\n    path=str(c.get('panel_path','/') or '/').strip()\n    if path!='/' and path.endswith('/'):path=path.rstrip('/')\n    return origin+(path if path!='/' else '')+'/'\n\ndef valid_panel_path(value):\n    value=str(value or '/').strip()\n    if value!='/' and value.endswith('/'):value=value.rstrip('/')\n    return len(value)<=200 and (value=='/' or bool(__import__('re').fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',value)))")

regex_once('tools/menu.py',
r"def choose_owner\(\):\n.*?\n\ndef inbound_ports\(\):",
'''def owner_profiles_without_login():
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


def inbound_ports():''')

regex_once('tools/menu.py',
r"def account_menu\(\):\n.*?\n\ndef panel_network_menu\(\):",
'''def account_menu():
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
            user=ask('New owner login username')
            if user and confirm(f'Create a full Owner login named {user}?','CREATE'):
                run([COMMAND,'account','--username',user,'--action','create-owner']);pause()
            continue
        user=choose_owner() if x in {'1','3','4','5','6','7'} else ''
        if x=='1' and user:run([COMMAND,'account','--username',user,'--action','status']);pause()
        elif x=='3' and user:
            new=ask('New owner username')
            if new and confirm(f'Rename owner {user} → {new}? Active sessions will be revoked.','RENAME'):
                run([COMMAND,'account','--username',user,'--action','rename','--new-username',new]);pause()
        elif x=='4' and user:
            if confirm(f'Change password for login Owner {user}? Active sessions will be revoked.'):
                run([COMMAND,'reset-password','--username',user]);pause()
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


def panel_network_menu():''')

# Replace panel/network function entirely for stable numbering + URI path flow.
regex_once('tools/menu.py',
r"def panel_network_menu\(\):\n.*?\n\ndef tls_menu\(\):",
'''def panel_network_menu():
    while True:
        c=cfg();header('PANEL & NETWORK',endpoint(c))
        title_row('PANEL ACCESS')
        item(1,'Safe configuration summary')
        item(2,'Change panel port',str(c.get('bind_port',2087)))
        item(3,'Change public proxy address',str(c.get('public_address','')))
        item(4,'Change panel URI path',str(c.get('panel_path','/')))
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
            if not need_root():continue
            val=ask('New panel port',str(c.get('bind_port',2087)))
            if not val.isdigit() or not 1024<=int(val)<=65535:print(f'{RE}Invalid nonprivileged port.{R}');pause();continue
            new=int(val);old=int(c.get('bind_port',2087));reserved={int(c.get('xray_api_port',10085)),22}|inbound_ports()
            if new in reserved:print(f'{RE}Port conflicts with SSH/Xray API/data inbound.{R}');pause();continue
            if new!=old and port_busy(new):print(f'{RE}Port is already listening.{R}');pause();continue
            if not confirm(f'Change panel port {old} → {new} and restart?'):continue
            c['bind_port']=new;c['protected_ports']=sorted((set(map(int,c.get('protected_ports',[])))-{old})|{new,22,int(c.get('xray_api_port',10085))})
            origin=str(c.get('public_origin',''))
            if origin.startswith('http://127.0.0.1:'):c['public_origin']=f'http://127.0.0.1:{new}'
            elif origin.startswith('https://'):
                host=origin.split('://',1)[1].split(':',1)[0];c['public_origin']=f'https://{host}:{new}'
            if atomic_config(c):run(['systemctl','restart','dark-xray.service']);print(f'{GR}Port updated.{R}')
            pause()
        elif x=='3':
            if not need_root():continue
            v=ask('Public proxy IP/DNS',str(c.get('public_address','')))
            if not v or any(ch in v for ch in '/?#@ \\r\\n'):print(f'{RE}Invalid address.{R}');pause();continue
            if confirm(f'Set public proxy address to {v}?'):
                c['public_address']=v
                if atomic_config(c):run(['systemctl','restart','dark-xray.service'])
            pause()
        elif x=='4':
            v=ask('Panel URI path (example /dark-admin)',str(c.get('panel_path','/'))).strip() or '/'
            if v!='/' and v.endswith('/'):v=v.rstrip('/')
            if not valid_panel_path(v):print(f'{RE}Invalid URI path. Use / or /letters-numbers_-/segments.{R}');pause();continue
            if confirm(f'Stage panel URI path {c.get("panel_path","/")} → {v}?'):
                run([COMMAND,'stage-panel-path','--panel-path',v])
                print(f'{YE}URI path is staged. Apply it now to restart the panel on the new path.{R}')
                if need_root() and confirm('Apply staged URI path now?'):
                    run([COMMAND,'settings-apply'])
                    print(f'{GR}New panel URL: {endpoint(cfg())}{R}')
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
                Path('/etc/sysctl.d/99-dark-xray-bbr.conf').write_text('net.core.default_qdisc=fq\\nnet.ipv4.tcp_congestion_control=bbr\\n')
                run(['sysctl','--system']);pause()


def tls_menu():''')

# ---------------------------------------------------------------------------
# Web UI prefix-awareness
# ---------------------------------------------------------------------------
p=Path('web/index.html');s=p.read_text();s=s.replace('href="/assets/','href="assets/').replace('src="/assets/','src="assets/');p.write_text(s)

once('web/live.js',
"const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));",
"const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));\nconst PANEL_BASE=location.pathname==='/'?'':location.pathname.replace(/\\/+$/,'');\nconst appUrl=path=>PANEL_BASE+(String(path).startsWith('/')?String(path):'/'+String(path));")

once('web/live.js',"res=await fetch(path,{method,headers,credentials:'same-origin'","res=await fetch(appUrl(path),{method,headers,credentials:'same-origin'")

once('web/ops-v2.js','href="/api/backup"','href="${appUrl(\'/api/backup\')}"')

# Settings V2 network form + save
once('web/settings-v2.js',
"${field(L('Panel port','پورت پنل'),'bind_port',r.bind_port||2087,'number',L('Root apply is required after staging.','بعد از ذخیره نیاز به اعمال با روت دارد.'),'min=\"1024\" max=\"65535\" required')}",
"${field(L('Panel port','پورت پنل'),'bind_port',r.bind_port||2087,'number',L('Root apply is required after staging.','بعد از ذخیره نیاز به اعمال با روت دارد.'),'min=\"1024\" max=\"65535\" required')}${field(L('Panel URI path','مسیر URI پنل'),'panel_path',r.panel_path||'/','text',L('Example: /dark-admin — / keeps the panel at root.','مثال: /dark-admin — / یعنی مسیر ریشه.'),'dir=\"ltr\" maxlength=\"200\" required')}")

once('web/settings-v2.js',
"old.public_address=formValue(form,'public_address').trim();old.bind_port=n(formValue(form,'bind_port'));old.poll_seconds=n(formValue(form,'poll_seconds'));",
"old.public_address=formValue(form,'public_address').trim();old.bind_port=n(formValue(form,'bind_port'));old.panel_path=formValue(form,'panel_path').trim()||'/';old.poll_seconds=n(formValue(form,'poll_seconds'));")

# ---------------------------------------------------------------------------
# Config/install/provision/wrappers
# ---------------------------------------------------------------------------
once('config.example.json','  "public_origin": "http://127.0.0.1:2087",','  "public_origin": "http://127.0.0.1:2087",\n  "panel_path": "/",')

once('tools/provision.py',
"    p.add_argument('--username',default='dark')\n    p.add_argument('--port',type=int,default=2087)",
"    p.add_argument('--username',default='dark')\n    p.add_argument('--port',type=int,default=2087)\n    p.add_argument('--panel-path',default='/')")

once('tools/provision.py',
"    if not 1024<=a.port<=65535 or any(not 1<=x<=65535 for x in a.ssh_port):raise SystemExit('Invalid port')",
"    if not 1024<=a.port<=65535 or any(not 1<=x<=65535 for x in a.ssh_port):raise SystemExit('Invalid port')\n    if a.panel_path!='/' and a.panel_path.endswith('/'):a.panel_path=a.panel_path.rstrip('/')\n    if a.panel_path!='/' and (len(a.panel_path)>200 or not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',a.panel_path)):\n        raise SystemExit('Invalid panel URI path')")

once('tools/provision.py',
"    cfg.update(public_origin=f'http://127.0.0.1:{a.port}',public_address=a.public_address,",
"    cfg.update(public_origin=f'http://127.0.0.1:{a.port}',panel_path=a.panel_path,public_address=a.public_address,")

once('tools/provision.py',
"  init|reset-password|check|serve|backup|doctor)",
"  init|reset-password|account|stage-panel-path|check|serve|backup|doctor)")

once('tools/provision.py',
"    print(f'Open http://127.0.0.1:{a.port} after forwarding. No firewall was enabled.')",
"    suffix=(a.panel_path if a.panel_path!='/' else '')+'/'\n    print(f'Open http://127.0.0.1:{a.port}{suffix} after forwarding. No firewall was enabled.')")

# Updater-generated wrapper must run account/staging as the service user.
once('tools/update.py',
"  init|reset-password|check|serve|backup|doctor)",
"  init|reset-password|account|stage-panel-path|check|serve|backup|doctor)")

# Inner launcher stage command.
once('darkxray',
" settings-apply) shift; exec \"$PY\" \"$ROOT/tools/settings_apply.py\" --config \"$CONFIG\" --data \"$DATA\" \"$@\" ;;",
" settings-apply) shift; exec \"$PY\" \"$ROOT/tools/settings_apply.py\" --config \"$CONFIG\" --data \"$DATA\" \"$@\" ;;\n stage-panel-path) shift; exec \"$PY\" \"$ROOT/tools/runtime_stage.py\" --config \"$CONFIG\" --data \"$DATA\" \"$@\" ;;")

# Fresh online installer asks for URI path and passes it through.
once('install-online.sh',
"valid_domain(){ [[ \"$1\" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\\.)+[A-Za-z]{2,63}$ ]]; }",
"valid_domain(){ [[ \"$1\" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\\.)+[A-Za-z]{2,63}$ ]]; }\nvalid_panel_path(){ [[ \"$1\" == / || ( ${#1} -le 200 && \"$1\" =~ ^/([A-Za-z0-9_-]{1,64})(/[A-Za-z0-9_-]{1,64})*$ ) ]]; }")

once('install-online.sh',
"OWNER=\"$(ask 'Owner username' 'dark')\"; valid_user \"$OWNER\" || fail \"Invalid owner username\"\nPANEL_PORT=\"$(ask 'Panel port' '2087')\"; valid_port \"$PANEL_PORT\" || fail \"Invalid panel port\"",
"OWNER=\"$(ask 'Owner username' 'dark')\"; valid_user \"$OWNER\" || fail \"Invalid owner username\"\nURI_PATH=\"$(ask 'Panel URI path' '/')\"; [[ \"$URI_PATH\" != / ]] && URI_PATH=\"${URI_PATH%/}\"; valid_panel_path \"$URI_PATH\" || fail \"Invalid panel URI path\"\nPANEL_PORT=\"$(ask 'Panel port' '2087')\"; valid_port \"$PANEL_PORT\" || fail \"Invalid panel port\"")

once('install-online.sh',
"printf \"  Owner        : %s\\n  Panel port   : %s\\n  Proxy address: %s\\n  Xray core    : %s\\n\" \"$OWNER\" \"$PANEL_PORT\" \"$PUBLIC_ADDRESS\" \"$CORE_VERSION\"",
"printf \"  Owner        : %s\\n  Panel port   : %s\\n  URI path     : %s\\n  Proxy address: %s\\n  Xray core    : %s\\n\" \"$OWNER\" \"$PANEL_PORT\" \"$URI_PATH\" \"$PUBLIC_ADDRESS\" \"$CORE_VERSION\"")

once('install-online.sh',
"bash setup.sh --public-address \"$PUBLIC_ADDRESS\" --ssh-port \"$DETECTED_SSH\" --username \"$OWNER\" --port \"$PANEL_PORT\" --core-version \"$CORE_VERSION\"",
"bash setup.sh --public-address \"$PUBLIC_ADDRESS\" --ssh-port \"$DETECTED_SSH\" --username \"$OWNER\" --port \"$PANEL_PORT\" --panel-path \"$URI_PATH\" --core-version \"$CORE_VERSION\"")

once('install-online.sh',
"  printf \"${C_GREEN}║${C_RESET} Panel: https://%s:%s\\n\" \"$DOMAIN\" \"$PANEL_PORT\"",
"  SUFFIX=\"$([[ \"$URI_PATH\" == / ]] && echo / || echo \"$URI_PATH/\")\"\n  printf \"${C_GREEN}║${C_RESET} Panel: https://%s:%s%s\\n\" \"$DOMAIN\" \"$PANEL_PORT\" \"$SUFFIX\"")

once('install-online.sh',
"  printf \"${C_GREEN}║${C_RESET} Open: http://127.0.0.1:%s\\n\" \"$PANEL_PORT\"",
"  SUFFIX=\"$([[ \"$URI_PATH\" == / ]] && echo / || echo \"$URI_PATH/\")\"\n  printf \"${C_GREEN}║${C_RESET} Open: http://127.0.0.1:%s%s\\n\" \"$PANEL_PORT\" \"$SUFFIX\"")

# Domain tool output displays full panel path while public_origin remains an Origin.
once('tools/domain.py',
"    print('Panel TLS enabled:',config['public_origin'])",
"    panel_path=str(config.get('panel_path','/'));print('Panel TLS enabled:',config['public_origin']+(panel_path if panel_path!='/' else '')+'/')")

# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------
p=Path('tests/test_settings_v2.py');s=p.read_text()
s=s.replace("assert runtime['access_mode']=='ssh' and runtime['bind_port']==2087","assert runtime['access_mode']=='ssh' and runtime['bind_port']==2087 and runtime['panel_path']=='/'")
s=s.replace("runtime.update(bind_port=2443,public_address='edge.example.test',poll_seconds=9,core_autostart=True)","runtime.update(bind_port=2443,public_address='edge.example.test',panel_path='/dark-admin',poll_seconds=9,core_autostart=True)")
s=s.replace("assert status['pending']['bind_port']['to']==2443","assert status['pending']['bind_port']['to']==2443\n    assert status['pending']['panel_path']['to']=='/dark-admin'")
s=s.replace("desired={'access_mode':'ssh','bind_port':2443,'public_address':'edge.example.com','poll_seconds':10,'core_autostart':True,'domain':'','acme_email':''}","desired={'access_mode':'ssh','bind_port':2443,'public_address':'edge.example.com','panel_path':'/dark-admin','poll_seconds':10,'core_autostart':True,'domain':'','acme_email':''}")
if 'test_panel_uri_path_scopes_ui_api_and_cookie' not in s:
    s += r'''


def test_panel_uri_path_scopes_ui_api_and_cookie(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),public_address='vpn.example.test',panel_path='/dark-admin',test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','Test!OnlyPassword123');manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin,follow_redirects=False) as c:
        assert c.get('/').status_code==404
        r=c.get('/dark-admin');assert r.status_code==307 and r.headers['location']=='/dark-admin/'
        assert c.get('/dark-admin/').status_code==200
        assert c.get('/dark-admin/assets/style.css').status_code==200
        assert c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'}).status_code==404
        r=c.post('/dark-admin/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});assert r.status_code==200
        assert 'Path=/dark-admin' in r.headers.get('set-cookie','')
        assert c.get('/health').status_code==200
    store.close()
'''
p.write_text(s)

p=Path('tests/test_owner_recovery.py');s=p.read_text()
s=s.replace('from owner_recovery import reset_owner_password','from owner_recovery import create_owner_account, reset_owner_password')
if 'test_create_owner_login_from_existing_profile' not in s:
    s += r'''


def test_create_owner_login_from_existing_profile(tmp_path):
    data,store,engine,manager,auth=make_runtime(tmp_path)
    auth.bootstrap('dark','OwnerPass8')
    manager.owner_put(OWNER if 'OWNER' in globals() else __import__('dark_policy').Actor('dark','owner',{}),'Mika',name='Mika',allowed=[])
    result=create_owner_account(data/'dark.sqlite3','Mika','MikaPass88')
    assert result['created'] is True and result['role']=='owner'
    with store.lock:
        row=store.db.execute("SELECT role,password_hash FROM api_admins WHERE id='Mika'").fetchone()
    assert row['role']=='owner' and verify_password('MikaPass88',row['password_hash'])
    close_runtime(store,engine,manager)
'''
p.write_text(s)

print('owner login UX + panel URI path patch applied')

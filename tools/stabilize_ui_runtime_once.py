#!/usr/bin/env python3
from pathlib import Path


def once(path, old, new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:140]!r}')
    p.write_text(s.replace(old,new,1))

# Runtime staging now supports panel port and public proxy address as well as URI path.
p=Path('tools/runtime_stage.py')
s=p.read_text()
old="""def main():
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
"""
new="""def apply_overrides(value:dict,*,panel_path=None,bind_port=None,public_address=None)->dict:
    value=dict(value);changed={}
    if panel_path is not None:
        value['panel_path']=normalize(panel_path);changed['panel_path']=value['panel_path']
    if bind_port is not None:
        if type(bind_port)is not int or not 1024<=bind_port<=65535:raise SystemExit('Panel port must be between 1024 and 65535')
        value['bind_port']=bind_port;changed['bind_port']=bind_port
    if public_address is not None:
        address=str(public_address).strip()
        if not address or len(address)>253 or any(c in address for c in '/?#@ \\r\\n\\t'):
            raise SystemExit('Public proxy address must be a plain IP or DNS name')
        value['public_address']=address;changed['public_address']=address
    if not changed:raise SystemExit('No runtime field was requested for staging')
    return value,changed


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--panel-path');p.add_argument('--bind-port',type=int);p.add_argument('--public-address')
    a=p.parse_args();config=json.loads(a.config.read_text());value=defaults(config);dbpath=a.data/'dark.sqlite3'
    if dbpath.is_symlink() or not dbpath.is_file():raise SystemExit('DARK database is missing or unsafe')
    with sqlite3.connect(str(dbpath),timeout=30) as db:
        row=db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
        if row:
            saved=json.loads(row[0])
            if not isinstance(saved,dict):raise SystemExit('Saved runtime settings are invalid')
            value.update(saved)
        value,changed=apply_overrides(value,panel_path=a.panel_path,bind_port=a.bind_port,public_address=a.public_address)
        db.execute("INSERT INTO core_sections(name,body) VALUES('runtime',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",(json.dumps(value),))
        db.commit()
    print('Runtime settings staged:',json.dumps(changed,ensure_ascii=False))
"""
if old not in s:raise SystemExit('runtime_stage main anchor missing')
p.write_text(s.replace(old,new,1))

# Launcher and generated wrappers expose a generic stage-runtime alias; old alias remains compatible.
once('darkxray',
     ' stage-panel-path) shift; exec "$PY" "$ROOT/tools/runtime_stage.py" --config "$CONFIG" --data "$DATA" "$@" ;;',
     ' stage-runtime|stage-panel-path) shift; exec "$PY" "$ROOT/tools/runtime_stage.py" --config "$CONFIG" --data "$DATA" "$@" ;;')
for path in ('tools/update.py','tools/provision.py'):
    once(path,'init|reset-password|account|stage-panel-path|check|serve|backup|doctor)',
              'init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|doctor)')

# Terminal: port/address/path all use stage -> preview -> explicit root apply.
p=Path('tools/menu.py');s=p.read_text()
old="""        elif x=='2':
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
"""
new="""        elif x=='2':
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
            if not v or any(ch in v for ch in '/?#@ \\r\\n'):print(f'{RE}Invalid address.{R}');pause();continue
            if run_action([COMMAND,'stage-runtime','--public-address',v],f'Public proxy address staged: {v}'):
                print(f'{PU}Review ALL pending runtime changes before apply:{R}')
                run([COMMAND,'settings-apply','--dry-run'])
                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):
                    run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.')
            pause()
"""
if old not in s:raise SystemExit('menu port/address anchor missing')
s=s.replace(old,new,1)
s=s.replace("run([COMMAND,'stage-panel-path','--panel-path',v])","run_action([COMMAND,'stage-runtime','--panel-path',v],f'URI path staged: {v}')",1)
s=s.replace("                print(f'{YE}URI path is staged. Apply it now to restart the panel on the new path.{R}')\n                if need_root() and confirm('Apply staged URI path now?'):\n                    run([COMMAND,'settings-apply'])\n                    print(f'{GR}New panel URL: {endpoint(cfg())}{R}')",
"                print(f'{PU}Review ALL pending runtime changes before apply:{R}')\n                run([COMMAND,'settings-apply','--dry-run'])\n                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):\n                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):\n                        print(f'{GR}New panel URL: {endpoint(cfg())}{R}')",1)
s=s.replace("item(11,'Generate random panel URI path','secure 96-bit path token')","item(11,'Generate random panel URI path','random 96-bit path token')",1)
s=s.replace("run_action([COMMAND,'stage-panel-path','--panel-path',v],f'Random URI path staged: {v}')","run_action([COMMAND,'stage-runtime','--panel-path',v],f'Random URI path staged: {v}')",1)
s=s.replace("                print(f'{YE}Apply staged settings to activate the new URL.{R}')\n                if need_root() and confirm('Apply random URI path now?'):\n                    run_action([COMMAND,'settings-apply'],'Random URI path applied; panel service restarted.')\n                    print(f'{GR}Panel URL: {endpoint(cfg())}{R}')",
"                print(f'{PU}Review ALL pending runtime changes before apply:{R}')\n                run([COMMAND,'settings-apply','--dry-run'])\n                if need_root() and confirm('Apply ALL staged settings shown above? Panel may restart.'):\n                    if run_action([COMMAND,'settings-apply'],'Staged runtime settings applied.'):\n                        print(f'{GR}Panel URL: {endpoint(cfg())}{R}')",1)
p.write_text(s)

# Prefix-safe account backup link and dynamic version labels.
p=Path('web/live.js');s=p.read_text()
old='href="/api/backup"'
if old not in s:raise SystemExit('account backup href anchor missing')
s=s.replace(old,'href="${appUrl(\'/api/backup\')}"',1)
s=s.replace('<span class="code-caption">v0.7 · STANDALONE</span>',"<span class=\"code-caption\">${e((state.me?.version||'0.7').replace('-standalone-lab',''))} · STANDALONE</span>",1)
s=s.replace('<span class="mono">0.6.0 STANDALONE</span>',"<span class=\"mono\">${e(state.me?.version||'DARK')} </span>",1)
p.write_text(s)

# Align installer branding with terminal while keeping project identity explicit.
once('install-online.sh','"D A R K   X R A Y"','"D A R K   V P N"')
once('install-online.sh','"100-STEP CYBER INSTALLER"','"DARK XRAY  •  100-STEP CYBER INSTALLER"')

# Runtime staging regression coverage goes into the already-run Settings V2 suite.
p=Path('tests/test_settings_v2.py');s=p.read_text()
if 'test_runtime_stage_supports_port_address_and_path' not in s:
    s += r'''


def test_runtime_stage_supports_port_address_and_path(tmp_path):
    path=Path(__file__).resolve().parents[1]/'tools'/'runtime_stage.py'
    spec=importlib.util.spec_from_file_location('runtime_stage',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    base={'access_mode':'ssh','bind_port':2087,'public_address':'1.2.3.4','panel_path':'/','poll_seconds':5,'core_autostart':False,'domain':'','acme_email':''}
    value,changed=mod.apply_overrides(base,panel_path='/dark-next',bind_port=2443,public_address='edge.example.com')
    assert value['panel_path']=='/dark-next' and value['bind_port']==2443 and value['public_address']=='edge.example.com'
    assert set(changed)=={'panel_path','bind_port','public_address'}
    with pytest.raises(SystemExit):mod.apply_overrides(base,panel_path='/sub')
    with pytest.raises(SystemExit):mod.apply_overrides(base,bind_port=80)
    with pytest.raises(SystemExit):mod.apply_overrides(base,public_address='https://bad.example')
'''
p.write_text(s)
print('UI/runtime stabilization patch applied')

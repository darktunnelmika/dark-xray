#!/usr/bin/env python3
from pathlib import Path
import json

ROOT=Path('.')

# 1) Safe-update legacy Doctor compatibility.
p=ROOT/'tools/update.py'
s=p.read_text(encoding='utf-8')
old="import argparse,json,os,re,shutil,sqlite3,subprocess,sys,tarfile,tempfile,time\n"
new="import argparse,json,os,re,shutil,socket,sqlite3,ssl,subprocess,sys,tarfile,tempfile,time\nfrom urllib.parse import urlsplit\n"
if s.count(old)!=1: raise SystemExit('update import anchor mismatch')
s=s.replace(old,new,1)
anchor="\ndef _doctor_once()->tuple[bool,str]:\n"
compat=r'''
def _compat_panel_route()->dict:
    """Strict local UI/asset probe for pre-panel_route legacy Doctor versions."""
    raw=json.loads((CONF/'config.json').read_text(encoding='utf-8'))
    origin=urlsplit(str(raw.get('public_origin') or ''))
    if origin.scheme not in {'http','https'} or not origin.hostname:
        raise RuntimeError('legacy config has no usable public_origin')
    bind_host=str(raw.get('bind_host') or '127.0.0.1')
    target='127.0.0.1' if bind_host in {'0.0.0.0','::'} else bind_host
    port_raw=raw.get('bind_port')
    bind_port=int(port_raw if port_raw is not None else (origin.port or (443 if origin.scheme=='https' else 80)))
    if not 1 <= bind_port <= 65535: raise RuntimeError('legacy bind_port is invalid')
    panel_path=str(raw.get('panel_path') or '/')
    if not panel_path.startswith('/'): raise RuntimeError('legacy panel_path is invalid')
    base='' if panel_path=='/' else panel_path.rstrip('/')
    host=origin.netloc
    def status(path:str)->int:
        sock=socket.create_connection((target,bind_port),timeout=2.5)
        try:
            if origin.scheme=='https':
                ctx=ssl.create_default_context();sock=ctx.wrap_socket(sock,server_hostname=origin.hostname)
            req=f'GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: darkxray-update-compat\r\n\r\n'.encode()
            sock.sendall(req);raw_head=b''
            while b'\r\n' not in raw_head and len(raw_head)<4096:
                chunk=sock.recv(512)
                if not chunk:break
                raw_head+=chunk
            first=raw_head.split(b'\r\n',1)[0].decode('ascii','replace').split()
            return int(first[1]) if len(first)>=2 and first[1].isdigit() else 0
        finally:
            try:sock.close()
            except Exception:pass
    ui=status(base+'/');asset=status(base+'/assets/style.css')
    return {'ui_status':ui,'asset_status':asset,'ok':ui==200 and asset==200,'probe':'legacy-compat'}

'''
if s.count(anchor)!=1: raise SystemExit('doctor anchor mismatch')
s=s.replace(anchor,'\n'+compat+anchor,1)
old_block="""    route=checks.get('panel_route') if isinstance(checks.get('panel_route'),dict) else {}\n    ok=checks.get('configuration')=='ok' and checks.get('database')=='ok' and route.get('ok') is True\n    detail='config='+str(checks.get('configuration'))+', db='+str(checks.get('database'))+', panel='+str(route)\n"""
new_block="""    route=checks.get('panel_route') if isinstance(checks.get('panel_route'),dict) else {}\n    if not route and checks.get('configuration')=='ok' and checks.get('database')=='ok':\n        try:route=_compat_panel_route()\n        except Exception as ex:route={'ok':False,'probe':'legacy-compat','error':type(ex).__name__+': '+str(ex)[:180]}\n    ok=checks.get('configuration')=='ok' and checks.get('database')=='ok' and route.get('ok') is True\n    detail='config='+str(checks.get('configuration'))+', db='+str(checks.get('database'))+', panel='+str(route)\n"""
if s.count(old_block)!=1: raise SystemExit('doctor route block mismatch')
s=s.replace(old_block,new_block,1)
p.write_text(s,encoding='utf-8')

# 2) Regression for the exact legacy Doctor shape seen on real VPS.
p=ROOT/'tests/test_update_transaction.py'
t=p.read_text(encoding='utf-8')
marker='def test_legacy_doctor_without_panel_route_uses_strict_compat_probe'
if marker not in t:
    t += r'''

def test_legacy_doctor_without_panel_route_uses_strict_compat_probe(monkeypatch):
    state={'checks':{'configuration':'ok','database':'ok'}}
    class CP:
        returncode=0
        @property
        def stdout(self):return json.dumps(state)
    monkeypatch.setattr(UPDATE.subprocess,'run',lambda *a,**k:CP())
    monkeypatch.setattr(UPDATE,'_compat_panel_route',lambda:{'ok':True,'ui_status':200,'asset_status':200,'probe':'legacy-compat'})
    ok,detail=UPDATE._doctor_once()
    assert ok is True and 'legacy-compat' in detail


def test_legacy_doctor_compat_probe_remains_fail_closed(monkeypatch):
    state={'checks':{'configuration':'ok','database':'ok'}}
    class CP:
        returncode=0
        @property
        def stdout(self):return json.dumps(state)
    monkeypatch.setattr(UPDATE.subprocess,'run',lambda *a,**k:CP())
    monkeypatch.setattr(UPDATE,'_compat_panel_route',lambda:{'ok':False,'ui_status':200,'asset_status':404,'probe':'legacy-compat'})
    assert UPDATE._doctor_once()[0] is False
'''
p.write_text(t,encoding='utf-8')

# 3) RC2 metadata/docs. Keep RC1 changelog history immutable.
(ROOT/'VERSION').write_text('0.9.0-rc2\n',encoding='utf-8')
for name in ('README.md','README.en.md','README.fa.md','STATUS.fa.md','TESTING-RC.fa.md'):
    path=ROOT/name
    text=path.read_text(encoding='utf-8').replace('0.9.0-rc1','0.9.0-rc2').replace('v0.9.0-rc1','v0.9.0-rc2')
    path.write_text(text,encoding='utf-8')

path=ROOT/'PUBLISH-STATUS.json'
data=json.loads(path.read_text(encoding='utf-8'))
data['current_version']='0.9.0-rc2'
data.setdefault('current_main_features',{})['legacy_doctor_safe_update_compat']=True
data['checksum_note']='RC2 source checksums will be regenerated after the compatibility hotfix is finalized; release asset checksums are generated with the prerelease.'
path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

path=ROOT/'CHANGELOG.fa.md'
text=path.read_text(encoding='utf-8')
entry='''## 0.9.0-rc2 — Safe Update compatibility hotfix\n\n- RC1 روی نصب‌های قدیمی که Doctor آن‌ها هنوز `panel_route` نداشت، ممکن بود با `panel={}` به‌اشتباه Update را unhealthy تشخیص دهد.\n- updater در این حالت فقط به یک probe محلی سخت‌گیرانه fallback می‌کند: UI و `assets/style.css` هر دو باید HTTP 200 بدهند.\n- اگر Doctor جدید `panel_route` دارد ولی آن را fail اعلام کند، هیچ fallbackی انجام نمی‌شود و Update همچنان fail-closed می‌ماند.\n- regression برای شکل دقیق legacy Doctor اضافه شد.\n\n'''
if '## 0.9.0-rc2' not in text:
    text=text.replace('# تغییرات DARK XRAY\n\n','# تغییرات DARK XRAY\n\n'+entry,1)
path.write_text(text,encoding='utf-8')
print('RC2 legacy-update compatibility patch prepared')

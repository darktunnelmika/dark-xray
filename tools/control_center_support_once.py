#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

# Installed wrappers: backup/status run as the service account; isolated restore
# remains root-capable because its destination is intentionally outside live data.
for file in (Path('tools/provision.py'),Path('tools/update.py')):
    s=file.read_text(encoding='utf-8')
    old='init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|doctor)'
    new='init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|backup-verify|guard-control|doctor)'
    if s.count(old)!=1:raise SystemExit(f'wrapper anchor mismatch: {file}')
    file.write_text(s.replace(old,new,1),encoding='utf-8')

# Candidate source validation must include the new required control primitives.
p=Path('tools/update.py');s=p.read_text(encoding='utf-8')
old="src/'tools/doctor.py',src/'deploy/dark-xray.service'"
new="src/'tools/doctor.py',src/'tools/backup_cli.py',src/'tools/guard-control.py',src/'deploy/dark-xray.service'"
if s.count(old)!=1:raise SystemExit('update required-file anchor mismatch')
s=s.replace(old,new,1)
old="src/'tools/settings_apply.py',src/'tools/doctor.py'])"
new="src/'tools/settings_apply.py',src/'tools/doctor.py',src/'tools/backup_cli.py',src/'tools/guard-control.py'])"
if s.count(old)!=1:raise SystemExit('update pycompile anchor mismatch')
s=s.replace(old,new,1);p.write_text(s,encoding='utf-8')

Path('tests/test_control_center_v2.py').write_text('''import importlib.util,sqlite3,json\nfrom pathlib import Path\n\nROOT=Path(__file__).resolve().parents[1]\nSPEC=importlib.util.spec_from_file_location("dark_menu_control_v2",ROOT/"tools/menu.py")\nassert SPEC and SPEC.loader\nMENU=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MENU)\n\ndef test_protected_ports_uses_configured_ssh_and_core_ports():\n    assert MENU.protected_ports({"xray_api_port":10085,"protected_ports":[22,2222,2087]})=={22,2222,2087,10085}\n\ndef test_runtime_stage_state_reports_pending(tmp_path,monkeypatch):\n    monkeypatch.setattr(MENU,"DATA",tmp_path)\n    db=tmp_path/"dark.sqlite3"\n    with sqlite3.connect(db) as con:\n        con.execute("CREATE TABLE core_sections(name TEXT PRIMARY KEY,body TEXT NOT NULL)")\n        con.execute("INSERT INTO core_sections VALUES(?,?)",("runtime",json.dumps({"bind_port":9090,"panel_path":"/"})))\n    c={"public_origin":"http://127.0.0.1:2087","bind_port":2087,"panel_path":"/","public_address":"127.0.0.1","xray_api_port":10085}\n    assert MENU.runtime_stage_state(c)=="1 pending"\n\ndef test_bbr_refuses_unsupported_kernel_before_file_write(monkeypatch):\n    monkeypatch.setattr(MENU,"need_root",lambda:True);monkeypatch.setattr(MENU,"exists",lambda name:True)\n    class CP:\n        returncode=0;stdout="reno cubic\\n"\n    monkeypatch.setattr(MENU,"run",lambda *a,**k:CP())\n    assert MENU.safe_enable_bbr() is False\n\ndef test_control_center_routes_real_safety_commands():\n    src=(ROOT/"tools/menu.py").read_text()\n    assert "'nft','list','table','inet','dark_xray_ip'" in src\n    assert "'guard-control','clear'" in src\n    assert "'backup-verify'" in src and "'restore'" in src\n    assert "'production-gate'" in src and "'vps-verify'" in src\n    assert "'update','--ref',ref" in src\n    assert "CPU {cpu}" not in src and "RAM {ram}" not in src\n''',encoding='utf-8')

Path('tests/test_backup_cli.py').write_text('''import importlib.util,sqlite3,sys\nfrom pathlib import Path\n\nROOT=Path(__file__).resolve().parents[1]\nSPEC=importlib.util.spec_from_file_location("dark_backup_cli",ROOT/"tools/backup_cli.py")\nassert SPEC and SPEC.loader\nCLI=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CLI)\n\ndef make_install(tmp_path):\n    data=tmp_path/"data";data.mkdir();(data/"secret.key").write_bytes(b"x"*32)\n    db=data/"dark.sqlite3"\n    with sqlite3.connect(db) as con:\n        for table in ("clients","owners","api_admins","core_clients"):con.execute(f"CREATE TABLE {table}(id TEXT)")\n    config=tmp_path/"config.json";config.write_text("{}")\n    return data,config\n\ndef test_backup_verify_restore_cli_roundtrip(tmp_path,monkeypatch):\n    data,config=make_install(tmp_path);archive=tmp_path/"test.darkbackup";password="correct horse battery staple"\n    monkeypatch.setattr(CLI,"new_secret",lambda:password)\n    monkeypatch.setattr(sys,"argv",["backup_cli.py","backup","--config",str(config),"--data",str(data),"--output",str(archive)])\n    assert CLI.main()==0 and archive.is_file()\n    monkeypatch.setattr(CLI,"secret",lambda prompt:password)\n    monkeypatch.setattr(sys,"argv",["backup_cli.py","verify","--archive",str(archive)])\n    assert CLI.main()==0\n    dest=tmp_path/"restore"\n    monkeypatch.setattr(sys,"argv",["backup_cli.py","restore","--archive",str(archive),"--destination",str(dest)])\n    assert CLI.main()==0 and (dest/"data/dark.sqlite3").is_file()\n''',encoding='utf-8')

p=Path('tests/run-tests.sh');s=p.read_text(encoding='utf-8')
needle='python -m pytest tests/test_menu_owner_selection.py -q --junitxml=qa/junit/menu-owner-selection.xml\n'
if s.count(needle)!=1:raise SystemExit('run-tests anchor mismatch')
s=s.replace(needle,needle+'python -m pytest tests/test_control_center_v2.py -q --junitxml=qa/junit/control-center-v2.xml\npython -m pytest tests/test_backup_cli.py -q --junitxml=qa/junit/backup-cli.xml\n',1);p.write_text(s,encoding='utf-8')

Path('VERSION').write_text('0.9.0-rc3\n',encoding='utf-8')
for name in ('README.md','README.en.md','README.fa.md','STATUS.fa.md','TESTING-RC.fa.md'):
    p=Path(name);s=p.read_text(encoding='utf-8').replace('0.9.0-rc2','0.9.0-rc3').replace('0.9.0 RC1','0.9.0 RC3')
    p.write_text(s,encoding='utf-8')
p=Path('PUBLISH-STATUS.json');value=json.loads(p.read_text());value['current_version']='0.9.0-rc3';value['control_center_v2']=True;p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
p=Path('CHANGELOG.fa.md');s=p.read_text(encoding='utf-8')
entry='''## 0.9.0-rc3 — Control Center V2 audit\n\n- منوی ترمینال از نظر Backup/Restore، Guard، Update/Ref، Validation gates و عملیات systemd audit و بازطراحی شد.\n- Backup/Restore CLI واقعی با passphrase تعاملی، verify و isolated restore اضافه شد.\n- Guard status/clear فقط از Broker احرازشده انجام می‌شود؛ نام جدول nft صحیح `dark_xray_ip` است و Stop دیگر به‌اشتباه ادعای clear ban ندارد.\n- Update منویی از Tag/Commit/Ref دقیق پشتیبانی می‌کند و `check`، `vps-verify` و `production-gate` داخل Control Center در دسترس‌اند.\n- BBR قبل از persistence، پشتیبانی kernel را بررسی می‌کند و در خطای activation فایل/runtime قبلی را restore می‌کند.\n- Main Control Matrix از CPU/RAM شلوغ پاک شد؛ منابع سیستم در Diagnostics باقی مانده‌اند.\n\n'''
if '## 0.9.0-rc3 — Control Center V2 audit' not in s:s=s.replace('# تغییرات DARK XRAY\n\n','# تغییرات DARK XRAY\n\n'+entry,1)
p.write_text(s,encoding='utf-8')
print('support patch prepared')

#!/usr/bin/env python3
from pathlib import Path

menu=Path('tools/menu.py')
s=menu.read_text(encoding='utf-8')
old="""def choose_owner():
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
"""
new="""def choose_owner(action='manage'):
    owners=owner_usernames()
    if not owners:
        print(f'{RE}No login Owner exists. Use Create owner login first.{R}')
        return ''
    print(f'{GY}Login Owners — choose account to {action}:{R}')
    for i,name in enumerate(owners,1):print(f'  [{i}] {name}')
    profiles=owner_profiles_without_login()
    if profiles:
        print(f'{YE}Profiles without login/password (not selectable):{R} '+', '.join(profiles))
    raw=ask('Select login Owner','1')
    if raw.isdigit() and 1<=int(raw)<=len(owners):
        selected=owners[int(raw)-1]
        print(f'{GY}Selected login Owner:{R} {selected}')
        return selected
    print(f'{RE}Invalid Owner selection. Type the number shown in the list.{R}')
    return ''
"""
if s.count(old)!=1:
    raise SystemExit('choose_owner anchor mismatch')
s=s.replace(old,new,1)
old2="""        user=choose_owner() if x in {'1','3','4','5','6','7'} else ''
"""
new2="""        owner_actions={
            '1':'view security status for',
            '3':'rename',
            '4':'change password for',
            '5':'revoke sessions for',
            '6':'revoke API keys for',
            '7':'reset TOTP for',
        }
        user=choose_owner(owner_actions.get(x,'manage')) if x in owner_actions else ''
"""
if s.count(old2)!=1:
    raise SystemExit('account_menu selector anchor mismatch')
s=s.replace(old2,new2,1)
menu.write_text(s,encoding='utf-8')

test=Path('tests/test_menu_owner_selection.py')
test.write_text("""import importlib.util\nfrom pathlib import Path\n\nROOT=Path(__file__).resolve().parents[1]\nSPEC=importlib.util.spec_from_file_location('dark_menu_owner_select',ROOT/'tools/menu.py')\nassert SPEC and SPEC.loader\nMENU=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MENU)\n\ndef test_single_login_owner_is_still_listed_and_selected_explicitly(monkeypatch,capsys):\n    monkeypatch.setattr(MENU,'owner_usernames',lambda:['dark'])\n    monkeypatch.setattr(MENU,'owner_profiles_without_login',lambda:[])\n    calls=[]\n    def fake_ask(prompt,default=''):\n        calls.append((prompt,default));return '1'\n    monkeypatch.setattr(MENU,'ask',fake_ask)\n    assert MENU.choose_owner('change password for')=='dark'\n    out=capsys.readouterr().out\n    assert 'Login Owners' in out and '[1] dark' in out\n    assert calls==[('Select login Owner','1')]\n\ndef test_multiple_login_owners_can_choose_password_target(monkeypatch,capsys):\n    monkeypatch.setattr(MENU,'owner_usernames',lambda:['dark','mika'])\n    monkeypatch.setattr(MENU,'owner_profiles_without_login',lambda:['profile-only'])\n    monkeypatch.setattr(MENU,'ask',lambda prompt,default='':'2')\n    assert MENU.choose_owner('change password for')=='mika'\n    out=capsys.readouterr().out\n    assert '[1] dark' in out and '[2] mika' in out\n    assert 'Profiles without login/password' in out and 'profile-only' in out\n\ndef test_invalid_login_owner_selection_is_rejected(monkeypatch):\n    monkeypatch.setattr(MENU,'owner_usernames',lambda:['dark'])\n    monkeypatch.setattr(MENU,'owner_profiles_without_login',lambda:[])\n    monkeypatch.setattr(MENU,'ask',lambda prompt,default='':'9')\n    assert MENU.choose_owner('change password for')==''\n""",encoding='utf-8')

runner=Path('tests/run-tests.sh')
r=runner.read_text(encoding='utf-8')
needle="python -m pytest tests/test_owner_recovery.py -q --junitxml=qa/junit/owner-recovery.xml\n"
add=needle+"python -m pytest tests/test_menu_owner_selection.py -q --junitxml=qa/junit/menu-owner-selection.xml\n"
if 'test_menu_owner_selection.py' not in r:
    if r.count(needle)!=1:raise SystemExit('run-tests owner-recovery anchor mismatch')
    r=r.replace(needle,add,1)
runner.write_text(r,encoding='utf-8')
print('Owner login selector UX patch prepared')

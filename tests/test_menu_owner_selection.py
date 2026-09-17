import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dark_menu_owner_select',ROOT/'tools/menu.py')
assert SPEC and SPEC.loader
MENU=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MENU)

def test_single_login_owner_is_still_listed_and_selected_explicitly(monkeypatch,capsys):
    monkeypatch.setattr(MENU,'owner_usernames',lambda:['dark'])
    monkeypatch.setattr(MENU,'owner_profiles_without_login',lambda:[])
    calls=[]
    def fake_ask(prompt,default=''):
        calls.append((prompt,default));return '1'
    monkeypatch.setattr(MENU,'ask',fake_ask)
    assert MENU.choose_owner('change password for')=='dark'
    out=capsys.readouterr().out
    assert 'Login Owners' in out and '[1] dark' in out
    assert calls==[('Select login Owner','1')]

def test_multiple_login_owners_can_choose_password_target(monkeypatch,capsys):
    monkeypatch.setattr(MENU,'owner_usernames',lambda:['dark','mika'])
    monkeypatch.setattr(MENU,'owner_profiles_without_login',lambda:['profile-only'])
    monkeypatch.setattr(MENU,'ask',lambda prompt,default='':'2')
    assert MENU.choose_owner('change password for')=='mika'
    out=capsys.readouterr().out
    assert '[1] dark' in out and '[2] mika' in out
    assert 'Profiles without login/password' in out and 'profile-only' in out

def test_invalid_login_owner_selection_is_rejected(monkeypatch):
    monkeypatch.setattr(MENU,'owner_usernames',lambda:['dark'])
    monkeypatch.setattr(MENU,'owner_profiles_without_login',lambda:[])
    monkeypatch.setattr(MENU,'ask',lambda prompt,default='':'9')
    assert MENU.choose_owner('change password for')==''

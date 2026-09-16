import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    path=ROOT/'tools'/name
    spec=importlib.util.spec_from_file_location(name.replace('.py',''),path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def runtime_base():
    return {'access_mode':'ssh','bind_port':2087,'public_address':'1.2.3.4','panel_path':'/','poll_seconds':5,'core_autostart':False,'domain':'','acme_email':''}


def current_base():
    return {'public_origin':'http://127.0.0.1:2087','bind_port':2087,'public_address':'1.2.3.4','panel_path':'/','poll_seconds':5,
            'core_autostart':False,'xray_api_port':10085,'tls_certificate':'','tls_private_key':''}


def test_runtime_stage_rejects_custom_subscription_overlap():
    mod=load('runtime_stage.py');base=runtime_base()
    for panel,sub in [('/portal','/portal/sub'),('/portal/sub','/portal'),('/portal','/portal')]:
        with pytest.raises(SystemExit):
            mod.apply_overrides(base,panel_path=panel,subscription_path=sub)
    value,changed=mod.apply_overrides(base,panel_path='/admin',subscription_path='/portal/sub')
    assert value['panel_path']=='/admin' and changed=={'panel_path':'/admin'}


def test_settings_apply_rejects_custom_subscription_overlap():
    mod=load('settings_apply.py');desired=runtime_base();desired['panel_path']='/portal'
    with pytest.raises(ValueError):
        mod.validate_desired(desired,current_base(),set(),'/portal/sub')
    desired['panel_path']='/admin'
    assert mod.validate_desired(desired,current_base(),set(),'/portal/sub')['panel_path']=='/admin'


def test_settings_apply_reads_custom_subscription_path(tmp_path):
    mod=load('settings_apply.py');db=tmp_path/'dark.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE core_sections(name TEXT PRIMARY KEY, body TEXT NOT NULL)')
        conn.execute('INSERT INTO core_sections(name,body) VALUES(?,?)',('subscription',json.dumps({'path':'/custom-sub'})))
    assert mod._subscription_path(db)=='/custom-sub'


def test_settings_apply_fails_closed_on_invalid_saved_subscription(tmp_path):
    mod=load('settings_apply.py');db=tmp_path/'dark.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE core_sections(name TEXT PRIMARY KEY, body TEXT NOT NULL)')
        conn.execute('INSERT INTO core_sections(name,body) VALUES(?,?)',('subscription','{"path":"/"}'))
    with pytest.raises(SystemExit):mod._subscription_path(db)

import importlib.util
import json
import socket
import subprocess
from pathlib import Path

import pytest


def load_module():
    path=Path(__file__).resolve().parents[1]/'tools'/'settings_apply.py'
    spec=importlib.util.spec_from_file_location('settings_apply_runtime_tests',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def test_new_panel_port_busy_preflight():
    mod=load_module()
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.bind(('127.0.0.1',0));sock.listen(1)
    port=sock.getsockname()[1]
    try:assert mod._port_available(port) is False
    finally:sock.close()


def test_candidate_build_updates_management_protection(monkeypatch,tmp_path):
    mod=load_module();monkeypatch.setattr(mod,'GUARD_PATH',tmp_path/'missing-guard.json')
    current={
        'public_origin':'http://127.0.0.1:2087','panel_path':'/','bind_host':'127.0.0.1','bind_port':2087,
        'public_address':'1.2.3.4','poll_seconds':5,'core_autostart':False,'secure_cookie':False,
        'tls_certificate':'','tls_private_key':'','xray_api_port':10085,'protected_ports':[22,2087,10085],
    }
    desired={'access_mode':'ssh','bind_port':2443,'public_address':'edge.example.com','panel_path':'/dark-admin',
             'poll_seconds':9,'core_autostart':True,'domain':'','acme_email':''}
    plan=mod.build_plan(current,desired)
    candidate,guard=mod._candidate_from_desired(current,desired,plan)
    assert guard is None
    assert candidate['public_origin']=='http://127.0.0.1:2443'
    assert candidate['panel_path']=='/dark-admin'
    assert candidate['public_address']=='edge.example.com'
    assert 2443 in candidate['protected_ports'] and 2087 not in candidate['protected_ports']


def test_activation_failure_restores_previous_config(monkeypatch,tmp_path):
    mod=load_module();config_path=tmp_path/'config.json';mod.GUARD_PATH=tmp_path/'guard.json'
    previous={'public_origin':'http://127.0.0.1:2087','bind_port':2087,'protected_ports':[22,2087,10085]}
    candidate={'public_origin':'http://127.0.0.1:2443','bind_port':2443,'protected_ports':[22,2443,10085]}
    config_path.write_text(json.dumps(previous))
    def atomic(path,value,mode=None):Path(path).write_text(json.dumps(value))
    calls=[]
    def restart(name):
        calls.append(name)
        if len(calls)==1:raise subprocess.CalledProcessError(1,['systemctl','restart',name])
    monkeypatch.setattr(mod,'_atomic_json',atomic)
    monkeypatch.setattr(mod,'_service_active',lambda name:False)
    monkeypatch.setattr(mod,'_restart_checked',restart)
    with pytest.raises(SystemExit,match='previous DARK configuration was restored'):
        mod._activate_candidate(config_path,previous,candidate,None)
    assert json.loads(config_path.read_text())==previous
    assert calls==['dark-xray.service','dark-xray.service']



def test_tls_renewal_metadata_cleanup_removes_only_files(tmp_path):
    mod=load_module();source=tmp_path/'tls-source.json';hook=tmp_path/'dark-xray-panel'
    source.write_text('{}');hook.write_text('#!/bin/sh\n')
    mod._deactivate_tls_renewal(source,hook)
    assert not source.exists() and not hook.exists()


def test_tls_renewal_cleanup_refuses_directory_shape(tmp_path):
    mod=load_module();source=tmp_path/'source-dir';hook=tmp_path/'hook';source.mkdir()
    try:mod._validate_tls_cleanup_targets(source,hook);assert False
    except SystemExit as exc:assert 'expected file path is a directory' in str(exc)

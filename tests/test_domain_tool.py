import importlib.util
import json
import socket
from pathlib import Path


def load_module():
    path=Path(__file__).resolve().parents[1]/'tools'/'domain.py'
    spec=importlib.util.spec_from_file_location('dark_domain_tests',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def test_domain_new_port_busy_preflight():
    mod=load_module();sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.bind(('127.0.0.1',0));sock.listen(1)
    port=sock.getsockname()[1]
    try:assert mod._port_available(port) is False
    finally:sock.close()


def test_tls_activation_config_replaces_old_panel_protection():
    mod=load_module()
    old={'public_origin':'http://127.0.0.1:2087','bind_host':'127.0.0.1','bind_port':2087,'secure_cookie':False,
         'tls_certificate':'','tls_private_key':'','xray_api_port':10085,'protected_ports':[22,2087,10085,2020],
         'panel_path':'/dark-admin'}
    new=mod._activation_config(old,'panel.example.com',2443,'/cert.pem','/key.pem')
    assert new['public_origin']=='https://panel.example.com:2443'
    assert new['bind_host']=='0.0.0.0' and new['secure_cookie'] is True
    assert 2443 in new['protected_ports'] and 2087 not in new['protected_ports']
    assert 2020 in new['protected_ports'] and new['panel_path']=='/dark-admin'


def test_guard_activation_filters_new_management_ports():
    mod=load_module()
    candidate={'protected_ports':[22,2443,10085]}
    guard={'protected_ports':[22,2087,10085],'allowed_ports':[2020,2443,3030]}
    result=mod._activation_guard(guard,candidate)
    assert result['protected_ports']==[22,2443,10085]
    assert result['allowed_ports']==[2020,3030]



def test_renewal_source_must_match_active_https_config(tmp_path):
    mod=load_module();old=mod.TLS_DIR;mod.TLS_DIR=tmp_path/'tls'
    try:
        state={'domain':'panel.example.com','lineage':'/x'}
        good={'public_origin':'https://panel.example.com:2087','tls_certificate':str(mod.TLS_DIR/'cert.pem'),'tls_private_key':str(mod.TLS_DIR/'key.pem')}
        assert mod._renewal_source_active(state,good) is True
        assert mod._renewal_source_active(state,good|{'public_origin':'http://127.0.0.1:2087'}) is False
        assert mod._renewal_source_active(state,good|{'public_origin':'https://old.example.com:2087'}) is False
    finally:mod.TLS_DIR=old

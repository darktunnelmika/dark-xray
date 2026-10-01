import importlib.util,os,sys
from pathlib import Path
from fastapi.testclient import TestClient

def load(tmp_path,monkeypatch):
 from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
 from cryptography.hazmat.primitives import serialization
 key=Ed25519PrivateKey.generate();kp=tmp_path/'signing.key';kp.write_bytes(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()))
 monkeypatch.setenv('DARK_LICENSE_DB',str(tmp_path/'licenses.sqlite3'));monkeypatch.setenv('DARK_LICENSE_SIGNING_KEY',str(kp));monkeypatch.setenv('DARK_LICENSE_ADMIN_TOKEN','admin-secret-fixture')
 spec=importlib.util.spec_from_file_location('license_authority_fixture',Path(__file__).parents[1]/'standalone/license-server/app.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m,TestClient(m.app)
def test_create_bind_check_reject_second_install_and_revoke(tmp_path,monkeypatch):
 m,c=load(tmp_path,monkeypatch);h={'X-Dark-License-Admin':'admin-secret-fixture'};created=c.post('/admin/licenses',headers=h,json={'telegram_id':778899,'days':30});assert created.status_code==200;key=created.json()['license_key'];lid=created.json()['license_id'];body={'license_key':key,'installation_id':'a'*32,'panel_origin':'https://panel.example'}
 a=c.post('/v1/activate',json=body);assert a.status_code==200 and a.json()['payload']['telegram_id']==778899
 assert c.post('/v1/check',json=body).status_code==200
 assert c.post('/v1/activate',json={**body,'installation_id':'b'*32}).status_code==409
 assert c.post('/admin/licenses/'+lid+'/revoke',headers=h).status_code==200
 revoked=c.post('/v1/check',json=body);assert revoked.status_code==200 and revoked.json()['payload']['revoked'] is True
def test_admin_is_not_public(tmp_path,monkeypatch):
 m,c=load(tmp_path,monkeypatch);assert c.post('/admin/licenses',json={'telegram_id':1,'days':30}).status_code==403


def test_replace_revokes_old_and_issues_unbound_key(tmp_path,monkeypatch):
    m,c=load(tmp_path,monkeypatch);h={'X-Dark-License-Admin':'admin-secret-fixture'}
    old=c.post('/admin/licenses',headers=h,json={'telegram_id':42,'days':30}).json()
    body={'license_key':old['license_key'],'installation_id':'c'*32,'panel_origin':'https://old.example'}
    assert c.post('/v1/activate',json=body).status_code==200
    new=c.post('/admin/licenses/'+old['license_id']+'/replace',headers=h);assert new.status_code==200
    assert c.post('/v1/check',json=body).json()['payload']['revoked'] is True
    fresh=new.json();assert fresh['replaced_license_id']==old['license_id']
    assert c.post('/v1/activate',json={'license_key':fresh['license_key'],'installation_id':'d'*32,'panel_origin':'https://new.example'}).status_code==200
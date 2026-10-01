import base64,json,time
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from licensing import LicenseClient,CHECK_SECONDS,DEFAULT_GRACE_SECONDS
from dark_policy import PolicyError

def pair(tmp_path):
 k=Ed25519PrivateKey.generate();pub=base64.urlsafe_b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode().rstrip('=');cfg=tmp_path/'license.json';cfg.write_text(json.dumps({'server_url':'https://license.example.test','public_key':pub,'enforce':True}));return k,LicenseClient(tmp_path/'data',cfg)
def envelope(k,client,**changes):
 now=time.time();p={'license_id':'lic_test','telegram_id':12345,'installation_id':client.installation_id,'issued_at':now,'expires_at':now+30*86400,'lease_until':now+5*86400,'revoked':False};p.update(changes);raw=json.dumps(p,sort_keys=True,separators=(',',':')).encode();sig=base64.urlsafe_b64encode(k.sign(raw)).decode().rstrip('=');return {'payload':p,'signature':sig}
def test_unconfigured_is_non_enforcing_and_identity_is_stable(tmp_path):
 c=LicenseClient(tmp_path/'data',tmp_path/'missing.json');assert c.status()['status']=='unconfigured';assert c.status()['writes_allowed'] is True;assert LicenseClient(tmp_path/'data',tmp_path/'missing.json').installation_id==c.installation_id
def test_signed_active_license_and_five_day_lease(tmp_path):
 k,c=pair(tmp_path);env=envelope(k,c);c._save({'envelope':env,'license_key':'DL1-'+'x'*32,'last_success':time.time(),'next_check':time.time()+CHECK_SECONDS,'last_error':''});s=c.status();assert s['status']=='active' and s['writes_allowed'];assert 4.9*86400 < s['lease_until']-time.time() <= 5*86400
def test_signature_and_installation_binding_are_fail_closed(tmp_path):
 k,c=pair(tmp_path);bad=envelope(k,c,installation_id='0'*32);c._save({'envelope':bad,'license_key':'x'*32});assert c.status()['status']=='inactive';assert c.status()['writes_allowed'] is False
 other=Ed25519PrivateKey.generate();bad=envelope(other,c);c._save({'envelope':bad,'license_key':'x'*32});assert c.status()['status']=='inactive'
def test_grace_then_offline_expired(tmp_path):
 k,c=pair(tmp_path);now=time.time();c._save({'envelope':envelope(k,c,lease_until=now-60,expires_at=now+86400),'license_key':'x'*32});assert c.status()['status']=='grace';c._save({'envelope':envelope(k,c,lease_until=now-DEFAULT_GRACE_SECONDS-60,expires_at=now+86400),'license_key':'x'*32});assert c.status()['status']=='offline_expired' and not c.status()['writes_allowed']
def test_expired_and_revoked_are_read_only(tmp_path):
 k,c=pair(tmp_path);now=time.time();
 for changes,status in [({'expires_at':now-1},'expired'),({'revoked':True},'revoked')]:
  c._save({'envelope':envelope(k,c,**changes),'license_key':'x'*32});s=c.status();assert s['status']==status and not s['writes_allowed']
def test_https_and_public_key_required(tmp_path):
 c=LicenseClient(tmp_path/'data',tmp_path/'missing.json')
 with pytest.raises(PolicyError):c.save_config({'server_url':'http://license.example.test','public_key':'bad','enforce':True})
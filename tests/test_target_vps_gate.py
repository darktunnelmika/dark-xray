import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


def load_gate():
    path=ROOT/'tools/target-vps-gate.py'
    spec=importlib.util.spec_from_file_location('dark_target_vps_gate',path)
    mod=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_atomic_target_report_is_private(tmp_path):
    gate=load_gate();path=tmp_path/'qa'/'target.json'
    gate.atomic_report(path,{'target_vps_gate_passed':True})
    assert json.loads(path.read_text())['target_vps_gate_passed'] is True
    assert os.stat(path).st_mode & 0o777 == 0o600
    link=tmp_path/'link.json';link.symlink_to(path)
    try:gate.atomic_report(link,{'x':1})
    except RuntimeError:pass
    else:raise AssertionError('symlink report path must be refused')


def test_reboot_evidence_requires_new_boot_same_source_and_same_config():
    gate=load_gate()
    pre={'boot':{'boot_id':'boot-a'},'source':{'commit':'a'*40,'version':'0.9.0-rc7'},'config_sha256':'cfg'}
    ok=gate.reboot_evidence(pre,{'boot_id':'boot-b'},{'commit':'a'*40,'version':'0.9.0-rc7'},'cfg')
    assert ok['ok'] is True and ok['boot_changed'] is True and ok['same_source'] is True and ok['same_config'] is True
    assert gate.reboot_evidence(pre,{'boot_id':'boot-a'},{'commit':'a'*40,'version':'0.9.0-rc7'},'cfg')['ok'] is False
    assert gate.reboot_evidence(pre,{'boot_id':'boot-b'},{'commit':'b'*40,'version':'0.9.0-rc7'},'cfg')['ok'] is False
    assert gate.reboot_evidence(pre,{'boot_id':'boot-b'},{'commit':'a'*40,'version':'0.9.0-rc7'},'changed')['ok'] is False


def test_source_expectation_is_exact_commit(tmp_path):
    gate=load_gate();data=tmp_path/'data';data.mkdir()
    (data/'installed-source.json').write_text(json.dumps({'commit':'1'*40,'version':'0.9.0-rc7','ref':'main'}))
    good=gate.source_evidence(data,'1'*40);bad=gate.source_evidence(data,'2'*40)
    assert good['ok'] is True and good['present'] is True
    assert bad['ok'] is False


def _renewal_fixture(gate,tmp_path,monkeypatch,server=None):
    live=tmp_path/'letsencrypt'/'live';renewal=tmp_path/'letsencrypt'/'renewal'
    lineage=live/'dark-xray-panel.example.test';lineage.mkdir(parents=True);renewal.mkdir(parents=True)
    conf=renewal/(lineage.name+'.conf')
    conf.write_text('[renewalparams]\nserver = '+(server or gate.LE_PRODUCTION_DIRECTORY)+'\n')
    gate.LE_LIVE=live;gate.LE_RENEWAL=renewal
    gate.TLS_SOURCE=tmp_path/'tls-source.json';gate.TLS_HOOK=tmp_path/'dark-xray-panel'
    gate.TLS_SOURCE.write_text(json.dumps({'lineage':str(lineage),'domain':'panel.example.test'}))
    gate.TLS_HOOK.write_text('#!/bin/sh\nexit 0\n');gate.TLS_HOOK.chmod(0o750)
    cert=tmp_path/'cert.pem';key=tmp_path/'key.pem';cert.write_bytes(b'cert-a');key.write_bytes(b'key-a')
    cfg=SimpleNamespace(public_origin='https://panel.example.test:2087',
        tls_certificate=str(cert),tls_private_key=str(key))
    monkeypatch.setattr(gate.Config,'load',lambda path:cfg)
    monkeypatch.setattr(gate,'_systemctl_check',lambda *a:True)
    monkeypatch.setattr(gate.shutil,'which',lambda name:'/usr/bin/certbot')
    return cfg,cert,key,lineage


def test_renewal_rehearsal_is_explicit_real_dry_run_and_keeps_active_pair(tmp_path,monkeypatch):
    gate=load_gate();cfg,cert,key,lineage=_renewal_fixture(gate,tmp_path,monkeypatch)
    calls=[]
    def child(args,timeout):
        calls.append((args,timeout));return SimpleNamespace(returncode=0,stdout='ok',stderr='')
    monkeypatch.setattr(gate,'_child',child)
    result=gate.renewal_evidence(tmp_path/'config.json',rehearse=True,require_letsencrypt=True,timeout=90)
    assert result['ok'] is True and result['renewal_rehearsed'] is True
    assert result['letsencrypt_production'] is True
    args,timeout=calls[0]
    assert args==['/usr/bin/certbot','renew','--dry-run','--cert-name',lineage.name,
                  '--no-random-sleep-on-renew','--no-directory-hooks']
    assert timeout==90 and result['rehearsal']['active_pair_unchanged'] is True


def test_renewal_rehearsal_rejects_wrong_acme_server(tmp_path,monkeypatch):
    gate=load_gate();_renewal_fixture(gate,tmp_path,monkeypatch,server='https://acme.invalid/directory')
    monkeypatch.setattr(gate,'_child',lambda *a,**k:SimpleNamespace(returncode=0,stdout='',stderr=''))
    result=gate.renewal_evidence(tmp_path/'config.json',rehearse=False,require_letsencrypt=True)
    assert result['ok'] is False and result['letsencrypt_production'] is False


def test_renewal_dry_run_cannot_change_active_pair_silently(tmp_path,monkeypatch):
    gate=load_gate();cfg,cert,key,_=_renewal_fixture(gate,tmp_path,monkeypatch)
    def child(args,timeout):
        cert.write_bytes(b'changed-by-bad-dry-run')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(gate,'_child',child)
    result=gate.renewal_evidence(tmp_path/'config.json',rehearse=True,require_letsencrypt=True)
    assert result['ok'] is False and result['renewal_rehearsed'] is False
    assert result['rehearsal']['active_pair_unchanged'] is False


def test_tls_renewal_gate_keeps_external_actions_opt_in():
    gate=load_gate()
    source=(ROOT/'tools/target-vps-gate.py').read_text(encoding='utf-8')
    assert "--rehearse-renewal" in source and "--require-letsencrypt" in source
    assert "'certificate_issuance_performed':False" in source
    assert "'reboot_injected_by_gate':False" in source
    assert "'node_outage_injected_by_gate':False" in source


def test_darkxray_exposes_target_vps_gate():
    source=(ROOT/'darkxray').read_text(encoding='utf-8')
    assert 'target-vps-gate)' in source
    assert 'tools/target-vps-gate.py' in source


def test_fresh_installer_persists_source_identity_and_uses_neutral_branding():
    source=(ROOT/'install-online.sh').read_text(encoding='utf-8')
    assert 'record_installed_source' in source
    assert '/var/lib/dark-xray/installed-source.json' in source
    assert 'SOURCE_VERSION=' in source
    assert 'No Sanayi runtime' not in source

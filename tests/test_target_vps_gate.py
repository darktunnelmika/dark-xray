import importlib.util
import json
import os
from pathlib import Path

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


def test_tls_renewal_evidence_does_not_overclaim_rehearsal():
    gate=load_gate()
    source=(ROOT/'tools/target-vps-gate.py').read_text(encoding='utf-8')
    assert "'renewal_rehearsed':False" in source
    assert "'certificate_renewal_rehearsed':False" in source
    assert "'reboot_injected_by_gate':False" in source
    assert "'node_outage_injected_by_gate':False" in source


def test_darkxray_exposes_target_vps_gate():
    source=(ROOT/'darkxray').read_text(encoding='utf-8')
    assert 'target-vps-gate)' in source
    assert 'tools/target-vps-gate.py' in source

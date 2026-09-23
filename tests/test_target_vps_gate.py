import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_passive_tls_evidence_does_not_overclaim_rehearsal():
    gate=load_gate()
    source=(ROOT/'tools/target-vps-gate.py').read_text(encoding='utf-8')
    assert "'renewal_rehearsed':False" in source
    assert '--run-deploy-hooks' in source
    assert 'acme-staging-v02.api.letsencrypt.org' in source
    assert "'production_certificate_replaced':False" in source
    assert "'reboot_injected_by_gate':False" in source
    assert "'node_outage_injected_by_gate':False" in source


def test_public_renewal_rehearsal_is_opt_in_staging_and_observes_restart(tmp_path,monkeypatch):
    gate=load_gate();live=tmp_path/'live';lineage=live/'dark-xray-panel.example.test';lineage.mkdir(parents=True)
    source=tmp_path/'tls-source.json'
    source.write_text(json.dumps({'domain':'panel.example.test','lineage':str(lineage)}))
    monkeypatch.setattr(gate,'TLS_SOURCE',source);monkeypatch.setattr(gate,'LE_LIVE',live)
    monkeypatch.setattr(gate.Config,'load',lambda p:type('Cfg',(),{'public_origin':'https://panel.example.test:2087'})())
    monkeypatch.setattr(gate.shutil,'which',lambda name:'/usr/bin/certbot')
    pids=iter([101,202]);monkeypatch.setattr(gate,'_service_pid',lambda:next(pids))
    monkeypatch.setattr(gate,'https_evidence',lambda p:{'ok':True})
    calls=[]
    def child(args,timeout):
        calls.append((args,timeout));return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(gate,'_child',child)
    result=gate.public_renewal_rehearsal(tmp_path/'config',True,True,120)
    assert result['passed'] is True and result['renewal_rehearsed'] is True
    args=calls[0][0]
    assert '--dry-run' in args and '--run-deploy-hooks' in args and gate.LE_STAGING in args
    assert result['production_certificate_replaced'] is False


def test_load_acceptance_requires_three_retained_exact_workloads(tmp_path,monkeypatch):
    gate=load_gate();tool=ROOT/'tools/load-scale-gate.py'
    monkeypatch.setattr(gate,'ROOT',ROOT)
    calls=[]
    def child(args,timeout):
        calls.append(list(map(str,args)))
        report=Path(args[args.index('--report')+1]);report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps({
            'passed':True,'phase':'completed','clients_requested':1000,'concurrency':12,'errors':[],
            'patch_requests':{'finished':100,'accepted':100,'max_seconds':1.25},
        }))
        return subprocess.CompletedProcess(args,0,'{}','')
    monkeypatch.setattr(gate,'_child',child)
    result=gate.load_acceptance(tmp_path,True,300)
    assert result['passed'] is True and result['runs_completed']==3
    assert len(calls)==3 and len({x[x.index('--report')+1] for x in calls})==3
    assert all('--clients' in x and x[x.index('--clients')+1]=='1000' for x in calls)
    assert all('--concurrency' in x and x[x.index('--concurrency')+1]=='12' for x in calls)
    assert (Path(result['evidence_directory'])/'summary.json').is_file()
    assert tool.is_file()


def test_stage4_profile_refuses_partial_invocation(monkeypatch):
    gate=load_gate();monkeypatch.setattr(gate.os,'geteuid',lambda:0)
    monkeypatch.setattr(sys,'argv',['target-vps-gate','--config','/fixture','--data','/fixture','--stage4'])
    with pytest.raises(SystemExit) as done:gate.main()
    assert 'Stage 4 profile requires' in str(done.value)


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

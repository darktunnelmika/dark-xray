import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


def load_gate():
    path=ROOT/'tools/production-gate.py'
    spec=importlib.util.spec_from_file_location('dark_production_gate',path)
    mod=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_gate_requires_readiness_and_real_data_plane():
    gate=load_gate()
    ok=gate.combine({'ready':True},{'passed':True},started_at=10,finished_at=12)
    assert ok['production_gate_passed'] is True
    assert ok['installed_state_modified'] is False
    assert ok['installed_customer_database_modified'] is False
    assert ok['installed_firewall_modified'] is False
    assert gate.combine({'ready':False},{'passed':True},started_at=1,finished_at=2)['production_gate_passed'] is False
    assert gate.combine({'ready':True},{'passed':False},started_at=1,finished_at=2)['production_gate_passed'] is False
    assert gate.combine({'ready':True},{'passed':False,'skipped':True},started_at=1,finished_at=2)['production_gate_passed'] is False


def test_atomic_report_is_private_and_complete(tmp_path):
    gate=load_gate();path=tmp_path/'qa'/'production-gate.json'
    gate.atomic_report(path,{'production_gate_passed':True,'value':7})
    assert json.loads(path.read_text())['value']==7
    assert os.stat(path).st_mode & 0o777 == 0o600
    link=tmp_path/'report-link.json';link.symlink_to(path)
    try:gate.atomic_report(link,{'value':8})
    except RuntimeError:pass
    else:raise AssertionError('symlink report path must be refused')


def test_data_plane_uses_binary_from_installed_config_only(tmp_path,monkeypatch):
    gate=load_gate();binary=tmp_path/'real-xray';binary.write_text('placeholder')
    monkeypatch.setattr(gate.Config,'load',lambda config:SimpleNamespace(xray_binary=str(binary)))
    seen={}
    def child(args,timeout):
        seen['args']=[str(x) for x in args];seen['timeout']=timeout
        report=Path(seen['args'][seen['args'].index('--report')+1])
        report.write_text(json.dumps({'passed':True,'binary_version':'Xray test fixture contract'}))
        return subprocess.CompletedProcess(seen['args'],0,'','')
    monkeypatch.setattr(gate,'_child',child)
    result=gate.data_plane_phase(tmp_path/'config.json',9)
    assert result['passed'] is True and result['exit_code']==0
    assert seen['args'][seen['args'].index('--binary')+1]==str(binary)
    assert seen['timeout']==9


def test_data_plane_is_skipped_if_installed_config_cannot_load(tmp_path,monkeypatch):
    gate=load_gate()
    def fail(_):raise ValueError('bad config')
    monkeypatch.setattr(gate.Config,'load',fail)
    result=gate.data_plane_phase(tmp_path/'config.json',5)
    assert result['passed'] is False and result['skipped'] is True
    assert 'configuration unavailable' in result['reason']


def test_real_smoke_report_uses_repository_version():
    source=(ROOT/'tools/smoke-real.py').read_text(encoding='utf-8')
    assert "report={'version':VERSION" in source
    assert 'DARK-XRAY-REAL-E2E-{VERSION}' in source
    assert "'version':'0.6.0'" not in source

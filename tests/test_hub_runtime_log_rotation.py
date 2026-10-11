"""Disposable installer regressions. Never alters production systemd or logs."""
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('hub_runtime_logrotate',ROOT/'tools/install_hub_logrotate.py')
assert SPEC and SPEC.loader
mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_hub_rotation_policy_is_bounded_and_preserves_xray_runtime():
    assert 'size 100M' in mod.POLICY and 'rotate 5' in mod.POLICY
    assert 'compress' in mod.POLICY and 'copytruncate' in mod.POLICY
    assert 'dark.sqlite3' not in mod.POLICY
    service=(ROOT/'deploy/dark-xray-runtime-rotate.service').read_text()
    timer=(ROOT/'deploy/dark-xray-runtime-rotate.timer').read_text()
    assert 'ExecStart=/usr/sbin/logrotate /etc/logrotate.d/dark-xray-runtime' in service
    assert 'OnCalendar=*-*-* *:0/15:00' in timer
    assert 'NoNewPrivileges=true' in service
    for text in (service,timer):
        assert 'systemctl restart' not in text and 'dark_core' not in text


def test_install_writes_only_root_owned_policy_and_units_in_isolated_paths(monkeypatch,tmp_path):
    monkeypatch.setattr(mod.os,'geteuid',lambda:0)
    conf=tmp_path/'config'/'dark-xray-runtime'
    unit_dir=tmp_path/'units'
    conf.parent.mkdir();unit_dir.mkdir()
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(tuple(args))
        assert kwargs['check']
    result=mod.install(ROOT,conf,unit_dir,runner=fake_run,logrotate_bin='/usr/sbin/logrotate')
    assert result is True
    assert conf.read_text()==mod.POLICY
    for name in mod.UNITS:
        assert (unit_dir/name).read_bytes()==(ROOT/'deploy'/name).read_bytes()
    assert calls==[('systemctl','daemon-reload'),('systemctl','enable','--now','dark-xray-runtime-rotate.timer')]


def test_symlinked_config_is_rejected_without_touching_target(monkeypatch,tmp_path):
    monkeypatch.setattr(mod.os,'geteuid',lambda:0)
    target=tmp_path/'untouched';target.write_text('original')
    conf=tmp_path/'runtime';conf.symlink_to(target)
    unit_dir=tmp_path/'units';unit_dir.mkdir()
    with pytest.raises(RuntimeError,match='Unsafe rotation target'):
        mod.install(ROOT,conf,unit_dir,runner=lambda *a,**k:None,logrotate_bin='/usr/sbin/logrotate')
    assert target.read_text()=='original'


def test_existing_runtime_calls_installer_after_verified_source_copy():
    updater=(ROOT/'tools/update.py').read_text()
    fresh=(ROOT/'tools/provision.py').read_text()
    assert "rotation=APP/'tools/install_hub_logrotate.py'" in updater
    assert "run([sys.executable,APP/'tools/install_hub_logrotate.py'])" in fresh
    assert "'logrotate'" in fresh

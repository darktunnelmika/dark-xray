"""Node updater permissions and pre-stop service-account boundary regression."""
import importlib.util
import os
import pwd
import stat
import subprocess
import sys
import tempfile
import types
import venv
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('node_update_perms',ROOT/'tools/update_node.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)


def test_normalize_only_staged_runtime_preserves_interpreter_and_secrets(tmp_path):
    root=tmp_path/'venv';root.mkdir(mode=0o700)
    bindir=root/'bin';bindir.mkdir(mode=0o700)
    script=bindir/'tool';script.write_text('#!/bin/sh\n');script.chmod(0o700)
    lib=root/'lib';lib.mkdir(mode=0o700)
    module=lib/'mod.py';module.write_text('x=1');module.chmod(0o600)
    outside=tmp_path/'private';outside.write_text('not public');outside.chmod(0o600)
    (bindir/'outside').symlink_to(outside)
    u.normalize_runtime_permissions(root)
    assert stat.S_IMODE(root.stat().st_mode)==0o755
    assert stat.S_IMODE(lib.stat().st_mode)==0o755
    assert stat.S_IMODE(module.stat().st_mode)==0o644
    assert stat.S_IMODE(script.stat().st_mode)==0o755
    assert stat.S_IMODE(outside.stat().st_mode)==0o600


def test_normalize_refuses_symlink_root(tmp_path):
    root=tmp_path/'link';root.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(RuntimeError,match='Unsafe staged'):u.normalize_runtime_permissions(root)


def test_service_probe_drops_identity_and_supplementary_groups(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(u.pwd,'getpwnam',lambda name:types.SimpleNamespace(pw_uid=1234,pw_gid=2345))
    monkeypatch.setattr(u.subprocess,'run',lambda args,**kw:(calls.append((args,kw)) or subprocess.CompletedProcess(args,0,'','')))
    u.service_import_probe(tmp_path/'python',tmp_path/'backend')
    _,kw=calls[0]
    assert kw['user']==1234 and kw['group']==2345 and kw['extra_groups']==[]
    assert kw['umask']==0o077 and kw['env']['PYTHONNOUSERSITE']=='1'
    assert 'PYTHONPATH' not in kw['env']


def test_service_probe_rejects_failed_import(monkeypatch,tmp_path):
    monkeypatch.setattr(u.pwd,'getpwnam',lambda name:types.SimpleNamespace(pw_uid=1234,pw_gid=2345))
    monkeypatch.setattr(u.subprocess,'run',lambda args,**kw:subprocess.CompletedProcess(args,1,'','ImportError'))
    with pytest.raises(RuntimeError,match='service-account import failed'):
        u.service_import_probe(tmp_path/'python',tmp_path)


def test_service_probe_rejects_execution_denied(monkeypatch,tmp_path):
    monkeypatch.setattr(u.pwd,'getpwnam',lambda name:types.SimpleNamespace(pw_uid=1234,pw_gid=2345))
    def denied(*a,**kw):raise PermissionError('denied')
    monkeypatch.setattr(u.subprocess,'run',denied)
    with pytest.raises(RuntimeError,match='service-account preflight failed'):
        u.service_import_probe(tmp_path/'python',tmp_path)


def test_umask_is_explicit_in_subprocess_builder(monkeypatch):
    calls=[]
    monkeypatch.setattr(u.subprocess,'run',lambda a,**kw:(calls.append(kw) or subprocess.CompletedProcess(a,0,'','')))
    u.run(['true'],umask=0o022)
    assert calls[0]['umask']==0o022


def fake_source(root):
    (root/'backend').mkdir(parents=True)
    for name in u.BACKEND:(root/'backend'/name).write_text('')
    (root/'VERSION').write_text('0.10.0-rc17')


def test_failed_preflight_never_stops_or_mutates_active_node(monkeypatch,tmp_path):
    app=tmp_path/'app';app.mkdir();src=tmp_path/'src';fake_source(src)
    env=tmp_path/'candidate';env.mkdir();(env/'bin').mkdir()
    (env/'bin/python').write_text('not an executable');(app/'live-sentinel').write_text('unchanged')
    calls=[];monkeypatch.setattr(u,'APP',app)
    monkeypatch.setattr(u,'run',lambda *a,**kw:calls.append(a))
    def fail(*a):raise RuntimeError('fixture import failure')
    monkeypatch.setattr(u,'service_import_probe',fail)
    with pytest.raises(RuntimeError,match='fixture import failure'):
        u.activate_candidate(src,env,'a'*40,'0.10.0-rc17','a'*40,tmp_path/'transaction')
    assert calls==[]
    assert (app/'live-sentinel').read_text()=='unchanged'
    assert not list(app.glob('.node-preflight-*'))


@pytest.mark.skipif(os.geteuid()!=0,reason='requires identity switching; also run by root systemd CI')
def test_real_unprivileged_import_of_umask077_candidate(monkeypatch):
    account=pwd.getpwnam('nobody')
    monkeypatch.setattr(u.pwd,'getpwnam',lambda name:account)
    with tempfile.TemporaryDirectory(prefix='dark-permission-fixture-') as td:
        root=Path(td);root.chmod(0o755)
        app=root/'app';app.mkdir(mode=0o755);monkeypatch.setattr(u,'APP',app)
        private=root/'private';private.mkdir(mode=0o700)
        src=private/'src';fake_source(src)
        env=private/'venv'
        old=os.umask(0o077)
        try:venv.EnvBuilder(with_pip=False).create(env)
        finally:os.umask(old)
        with u.service_preflight(src,env) as staged:
            assert staged.exists()
            u.service_import_probe(staged/'bin/python',staged.parent/'backend')
        assert stat.S_IMODE(private.stat().st_mode)==0o700
        assert not list(app.glob('.node-preflight-*'))

"""Local process lifecycle tests use fake_xray.py, NOT real Xray."""
import json,socket,os
from pathlib import Path
import pytest
from core import Config,CoreEngine,CoreError
from dark_policy import Store

@pytest.fixture
def engine(tmp_path):
    s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
    b=Path(__file__).parent/'fixtures'/'fake_xray.py'
    b.chmod(0o755)
    db=Store(tmp_path/'db');e=CoreEngine(Config(xray_binary=str(b.resolve()),xray_assets=str(tmp_path),xray_api_port=port,test_engine=True),db,tmp_path/'run')
    try:yield e
    finally:e.close();db.close()

def test_testdouble_version_identified(engine):assert 'TEST-DOUBLE' in engine.version_info()

def test_process_start_stop_is_local(engine):
    r=engine.command('start');assert r['running']
    pid=r['pid'];assert pid!=os.getpid()
    assert engine.command('stop')['running'] is False

def test_apply_does_not_restart_identical_generation(engine):
    engine.command('start');pid=engine.process.pid
    engine.flush();assert engine.process.pid==pid

def test_edited_config_restarts_owned_process(engine):
    engine.command('start');pid=engine.process.pid
    engine.save_section('dns',{'servers':['8.8.8.8']});engine.flush()
    assert engine.running and engine.process.pid!=pid
    assert not engine.runtime_state()['dirty']

def test_invalid_config_preserves_current_process(engine):
    engine.command('start');pid=engine.process.pid
    engine.save_section('outbounds',[{'tag':'direct','protocol':'invalid-test-protocol','settings':{}}])
    with pytest.raises(CoreError):engine.command('apply')
    assert engine.running and engine.process.pid==pid
    assert engine.runtime_state()['dirty']

def test_failed_new_process_rolls_back_previous(engine):
    engine.command('start');old=engine.applied_hash
    engine.save_section('outbounds',[{'tag':'direct','protocol':'freedom','settings':{'failTestStartup':True}}])
    with pytest.raises(CoreError):engine.command('apply')
    assert engine.running and engine.applied_hash==old
    assert engine.runtime_state()['dirty']
    assert 'restored' in engine.last_error

def test_missing_binary_does_not_start_another_panel(engine):
    engine.config.xray_binary='/missing/xray-core'
    with pytest.raises(CoreError):engine.command('start')
    assert not engine.running

def test_api_collision_does_not_attach_to_foreign_process(engine):
    sock=socket.socket();sock.bind(('127.0.0.1',engine.config.xray_api_port));sock.listen()
    try:
        with pytest.raises(CoreError,match='occupied'):engine.command('start')
        assert not engine.running
    finally:sock.close()

def test_stats_are_queried_directly_using_core_cli(engine,monkeypatch):
    import subprocess
    engine.command('start');calls=[];real=subprocess.run
    def run(args,**kw):
        if args[1:3]==['api','statsquery']:
            calls.append(args)
            return subprocess.CompletedProcess(args,0,'{"stat": [{"name":"user>>>test>>>traffic>>>uplink","value":"23"}]}','')
        return real(args,**kw)
    engine.create({'email':'test','id':'e02b3ba0-a9b8-4d0c-8bd1-93c6eae6afda','enable':True},[])
    monkeypatch.setattr(subprocess,'run',run)
    engine.collect_stats(force=True)
    engine.collect_stats(force=True)
    assert engine.clients()[0]['traffic']['up']==23
    assert '-reset=false' in calls[0]
    engine.reset('test');engine.collect_stats(force=True)
    assert engine.clients()[0]['traffic']['up']==0

def test_explicit_restart_restarts_identical_owned_config(engine):
    engine.command('start');pid=engine.process.pid;generation=engine.applied_hash
    engine.command('restart')
    assert engine.running and engine.process.pid!=pid and engine.applied_hash==generation

def test_restart_failure_restores_previous_owned_config(engine):
    engine.command('start');generation=engine.applied_hash
    engine.save_section('outbounds',[{'tag':'direct','protocol':'freedom','settings':{'failTestStartup':True}}])
    with pytest.raises(CoreError):engine.command('restart')
    assert engine.running and engine.applied_hash==generation


def test_unexpected_owned_core_exit_is_auto_recovered(engine):
    engine.command('start');old_pid=engine.process.pid
    engine.process.kill();engine.process.wait(timeout=3)
    engine.last_start=0
    engine.flush()
    assert engine.running and engine.process.pid!=old_pid
    state=engine.runtime_state()
    assert state['automatic_recoveries']==1
    assert state['last_exit_code'] is not None
    assert state['desired_running'] is True


def test_persisted_core_counters_continue_across_owned_restart(engine,monkeypatch):
    import subprocess
    raw={'value':100};real=subprocess.run
    def run(args,**kw):
        if len(args)>=3 and args[1:3]==['api','statsquery']:
            body='{"stat":[{"name":"user>>>test>>>traffic>>>uplink","value":"%s"}]}'%raw['value']
            return subprocess.CompletedProcess(args,0,body,'')
        return real(args,**kw)
    monkeypatch.setattr(subprocess,'run',run)
    engine.create({'email':'test','id':'e02b3ba0-a9b8-4d0c-8bd1-93c6eae6afda','enable':True},[])
    engine.command('start')
    engine.collect_stats(force=True)
    assert engine.clients()[0]['traffic']['up']==100
    engine.command('restart')
    raw['value']=20
    engine.collect_stats(force=True)
    assert engine.clients()[0]['traffic']['up']==120

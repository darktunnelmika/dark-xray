"""Node control lifecycle: isolated SQLite/FastAPI and fake Xray, not host reboot proof."""
from __future__ import annotations

import contextlib
import copy
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import Config, CoreEngine
from dark_policy import Store
from node_agent import AgentToken, make_agent_app
from test_node_hub_recovery import NODE, ORIGIN, TOKEN, ROOT, free_port, payload, post_state


@contextlib.contextmanager
def rebooted_agent(root: Path, *, background=False, autostart=True):
    """Recreate process-owned objects from the same disk state, with no Hub calls."""
    root.mkdir(parents=True, exist_ok=True)
    fake=root/'fake-xray'
    shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);fake.chmod(0o755)
    cfg=Config(public_origin=ORIGIN,public_address='node.example.test',secure_cookie=True,
               xray_binary=str(fake),xray_assets=str(root),xray_api_port=free_port(),
               core_autostart=autostart,test_engine=True)
    store=Store(root/'node.sqlite3');engine=CoreEngine(cfg,store,root/'runtime')
    token_path=root/'token'
    if not token_path.exists():token_path.write_text(TOKEN+'\n');token_path.chmod(0o600)
    app=make_agent_app(engine,store,AgentToken(token_path),NODE,background=background)
    try:
        with TestClient(app,base_url=ORIGIN,raise_server_exceptions=False) as client:
            client.headers['Authorization']='Bearer '+TOKEN
            yield store,engine,app.state.runtime,client,app.state.loop
    finally:
        engine.close();store.close()


def control(client,action):
    response=client.post('/node/api/core/'+action,json={})
    assert response.status_code==200,response.text
    return response.json()


def test_manual_stop_survives_agent_recreation_and_hub_outage(tmp_path):
    root=tmp_path/'node'
    with rebooted_agent(root) as (_,engine,runtime,client,_):
        body=payload(engine);post_state(client,body)
        control(client,'stop');assert not engine.running
    with rebooted_agent(root,background=True) as (_,engine,runtime,client,loop):
        assert not engine.running,'manual Stop was lost on Agent restart'
        loop.tick();assert not engine.running and not engine.wants_running
        assert runtime.status()['appliedRevision']==1


def test_hub_desired_stop_survives_agent_recreation(tmp_path):
    root=tmp_path/'node'
    with rebooted_agent(root) as (_,engine,runtime,client,_):
        body=payload(engine);body['desiredRunning']=False
        post_state(client,body);assert not engine.running
    with rebooted_agent(root,background=True) as (_,engine,_,_,loop):
        assert not engine.running,'desiredRunning=False was ignored on restart'
        loop.tick();assert not engine.running


def test_new_config_revision_cannot_undo_explicit_stop(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,loop):
        body=payload(engine);post_state(client,body)
        control(client,'stop')
        update=copy.deepcopy(body);update['sections']['dns']={'servers':['8.8.8.8']}
        post_state(client,update,2)
        assert not engine.running,'ordinary Hub config sync undid explicit Stop'
        assert engine.section('dns')==update['sections']['dns']
        loop.tick();assert not engine.running
        control(client,'start');assert engine.running


def test_start_and_restart_resume_then_survive_agent_recreation(tmp_path):
    root=tmp_path/'node'
    with rebooted_agent(root) as (_,engine,runtime,client,_):
        body=payload(engine);post_state(client,body)
        control(client,'stop');control(client,'start');assert engine.running
        pid=engine.process.pid
        control(client,'restart');assert engine.running and engine.process.pid!=pid
    with rebooted_agent(root,background=True) as (_,engine,_,_,_):
        assert engine.running


def test_repeated_state_after_stop_does_not_restart_xray(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,loop):
        body=payload(engine);post_state(client,body)
        control(client,'stop')
        result=post_state(client,body)
        assert result['changed'] is False and not engine.running
        loop.tick();assert not engine.running


def test_guard_recovery_cannot_restart_manually_stopped_xray(tmp_path,monkeypatch):
    import node_runtime as module
    from test_node_guard_recovery import RestartableGuard
    guard=RestartableGuard();monkeypatch.setattr(module,'BrokerClient',lambda *a,**k:guard)
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,loop):
        body=payload(engine);post_state(client,body)
        control(client,'stop');guard.ports=[]
        loop.tick()
        assert guard.ports==[body['assignments'][0]['inbound']['port']]
        assert not engine.running


def test_validate_does_not_clear_stop(tmp_path):
    root=tmp_path/'node'
    with rebooted_agent(root) as (_,engine,runtime,client,_):
        post_state(client,payload(engine));control(client,'stop');control(client,'validate')
        assert not engine.running
    with rebooted_agent(root,background=True) as (_,engine,_,_,_):assert not engine.running


@pytest.mark.parametrize('action',['start','restart'])
def test_failed_resume_keeps_previous_stop_across_agent_recreation(tmp_path,monkeypatch,action):
    from core import CoreError
    root=tmp_path/'node'
    with rebooted_agent(root) as (_,engine,runtime,client,_):
        post_state(client,payload(engine));control(client,'stop')
        def fail():raise CoreError('Injected spawn failure',status=503)
        monkeypatch.setattr(engine,'_spawn',fail)
        response=client.post('/node/api/core/'+action,json={})
        assert response.status_code==503,response.text
        assert runtime.control_status()['manual_stop'] and not engine.wants_running
    with rebooted_agent(root,background=True) as (_,engine,runtime,_,_):
        assert not engine.running and runtime.control_status()['manual_stop']


def test_stop_intent_survives_failed_final_snapshot_and_is_retried(tmp_path,monkeypatch):
    from core import CoreError
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,loop):
        post_state(client,payload(engine))
        collect=engine.collect_stats
        def fail(*a,**k):raise CoreError('Injected final counter failure',status=503)
        monkeypatch.setattr(engine,'collect_stats',fail)
        response=client.post('/node/api/core/stop',json={})
        assert response.status_code==503,response.text
        assert runtime.control_status()['manual_stop'] and not engine.wants_running
        # Keep the old process until its cumulative counters can be saved safely.
        assert engine.running
        monkeypatch.setattr(engine,'collect_stats',collect)
        loop.tick()
        assert not engine.running and not engine.wants_running


def test_unauthorized_or_readonly_stop_cannot_change_control_intent(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,_):
        post_state(client,payload(engine));before=runtime.control_status()
        response=client.post('/node/api/core/stop',json={},headers={'Authorization':''})
        assert response.status_code==401
        assert runtime.control_status()==before and engine.running
        engine.config.writes_enabled=False
        response=client.post('/node/api/core/stop',json={})
        assert response.status_code==409,response.text
        assert runtime.control_status()==before and engine.running
        engine.config.writes_enabled=True


def test_crashed_running_child_recovers_but_manual_stop_does_not(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,loop):
        post_state(client,payload(engine));engine.process.kill();engine.process.wait(timeout=3)
        engine.last_start=0
        loop.tick();assert engine.running, {'core':engine.runtime_state(),'maintenance_error':loop.last_error}
        control(client,'stop');engine.last_start=0
        loop.tick();assert not engine.running


def test_control_is_visible_to_authenticated_hub(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,engine,runtime,client,_):
        post_state(client,payload(engine));result=control(client,'stop')
        assert result['run_control']['manual_stop'] is True
        result=client.get('/node/api/v1/state').json()
        assert result['run_control']['persisted'] is True
        assert result['run_control']['effective_running'] is False


def test_agent_health_exposes_guard_state_without_claiming_enforcement(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_,engine,_,client,_):
        response=client.get('/node/api/health')
        assert response.status_code==200,response.text
        doc=response.json();guard=doc['guard']
        assert doc['capabilities']['guard_status']==1
        assert guard['mode']=='observe'
        assert guard['requested_mode']=='observe'
        assert guard['applied'] is False
        assert guard['state']=='observing'
        assert guard['source_verified'] is False
        assert doc['direct_source_verified'] is False

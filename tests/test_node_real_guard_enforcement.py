"""Stage 6: real Node Guard packet enforcement in an isolated net namespace.

This test exercises the production chain:
Hub-shaped desired state -> NodeRuntime -> real Xray access log -> DARK Guard
policy -> authenticated Unix broker -> real nftables -> packet drop.
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import pytest

from core import Config, CoreEngine
from dark_policy import Store
from guard_bridge import BrokerClient
from node_runtime import NodeRuntime
from node_security_lab import SOURCES, check_namespace
from test_node_hub_recovery import NODE, CLIENT_UUID, envelope, payload, free_port
from test_node_real_data_plane import real_binary, target_server, transfer
from test_node_real_security import bound_client

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_namespace():
    check_namespace()


def wait_socket(socket_path:Path, broker, timeout:float=5.0)->None:
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if broker.poll() is not None:
            raise AssertionError('Guard broker exited before socket creation')
        if socket_path.exists():return
        time.sleep(.05)
    raise AssertionError('Guard broker socket did not become ready')


def exercise_agent(root:Path,binary:Path,socket_path:Path)->dict:
    data_port=free_port()
    cfg=Config(xray_binary=str(binary),xray_assets=str(binary.parent),
               xray_api_port=free_port(),core_autostart=False,test_engine=False,
               direct_source_verified=True,guard_socket=str(socket_path))
    store=Store(root/'node.sqlite3');engine=CoreEngine(cfg,store,root/'runtime')
    try:
        runtime=NodeRuntime(store,engine,NODE)
        body=payload(engine)
        body['assignments'][0]['inbound']['port']=data_port
        body['assignments'][0]['inbound']['listen']='127.0.0.1'
        body['security']['clients'][0].update(limitIp=1,limitHwid=0,
                                              globalIpBlocked=False,globalDeviceBlocked=False)
        body['sections']['ipguard']={'mode':'enforce','window_seconds':120,
                                     'ban_seconds':30,'exempt_ips':[]}
        applied=runtime.apply(envelope(body))
        assert applied['changed'] is True and engine.running

        status=BrokerClient(str(socket_path)).status()
        assert status['allowed_ports']==[data_port] and status['active_address_port_leases']==0
        engine.sync_ip_guard()
        guard=engine.ip_status()
        assert guard['state']=='applied' and guard['applied'] is True and guard['source_verified'] is True

        mirror=runtime.mirror_for_source('alice')
        assert mirror
        with store.lock:
            limit_ip=store.db.execute('SELECT limit_ip FROM clients WHERE id=?',(mirror,)).fetchone()[0]
        assert limit_ip==1
        uri=f'vless://{CLIENT_UUID}@127.0.0.1:{data_port}?encryption=none&security=none&type=tcp'
        with target_server() as target:
            with bound_client(root/'source-one',binary,uri,SOURCES[0]) as first:
                transfer(first.port,target,allowed=True);engine.read_ip_log()
                ips={x['ip']:x for x in engine.ips(mirror)}
                assert ips[SOURCES[0]]['granted']==1
                with bound_client(root/'source-two',binary,uri,SOURCES[1]) as second:
                    transfer(second.port,target,allowed=True);engine.read_ip_log()
                    with store.lock:
                        ban=store.db.execute(
                            "SELECT ip,state FROM bans WHERE client_id=? AND state='applied'",(mirror,)).fetchone()
                    assert ban and ban['ip']==SOURCES[1]
                    status=BrokerClient(str(socket_path)).status()
                    assert status['active_address_port_leases']>=1
                    transfer(second.port,target,allowed=False)
                    transfer(first.port,target,allowed=True)
                    released=BrokerClient(str(socket_path)).release(SOURCES[1])
                    assert released['released_elements']>=1
                    transfer(second.port,target,allowed=True)
        with store.lock:
            events=[r[0] for r in store.db.execute(
                "SELECT kind FROM events WHERE client_id=? ORDER BY id",(mirror,))]
        assert 'violation' in events
        return {'passed':True,'data_port':data_port,'mirror':mirror,
                'packet_drop_verified':True,'unix_peer_uid_verified':True}
    finally:
        engine.close();store.close()


def test_node_enforce_observation_reaches_real_nftables_drop(tmp_path, real_binary):
    if os.geteuid()!=0:pytest.fail('Stage 6 packet acceptance requires root inside the isolated namespace')
    nft=shutil.which('nft')
    if not nft:pytest.fail('nft is required; no firewall mock fallback')
    account=pwd.getpwnam('nobody');uid,gid=account.pw_uid,account.pw_gid
    assert uid>0

    socket_dir=Path('/run/dark-xray-guard')
    assert socket_dir.is_dir() and not socket_dir.is_symlink()
    os.chown(socket_dir,0,gid);os.chmod(socket_dir,0o750)
    socket_path=socket_dir/'control.sock'
    guard_cfg=tmp_path/'guard.json'
    guard_cfg.write_text(json.dumps({
        'allowed_uid':uid,'allowed_ports':[],'protected_ports':[22,9443,10085],
        'direct_source_verified':True,'max_ban_seconds':120,'exempt_ips':[],
        'nft_binary':nft,'socket_path':'/run/dark-xray-guard/control.sock','allow_runtime_port_updates':True,
    },indent=2)+'\n');guard_cfg.chmod(0o600)
    worker_root=Path(tempfile.mkdtemp(prefix='dark-stage6-agent.',dir='/tmp'))
    os.chown(worker_root,uid,gid);os.chmod(worker_root,0o700)
    result_path=worker_root/'result.json'

    guard_log=(tmp_path/'guard.log').open('wb')
    def broker_ids():
        os.setgroups([gid]);os.setgid(gid)
    broker=subprocess.Popen([sys.executable,str(ROOT/'backend/guardd.py'),'--config',str(guard_cfg)],
                            stdout=guard_log,stderr=subprocess.STDOUT,preexec_fn=broker_ids)
    try:
        wait_socket(socket_path,broker)
        pid=os.fork()
        if pid==0:
            code=1
            try:
                os.setgroups([]);os.setgid(gid);os.setuid(uid)
                os.environ['HOME']=str(worker_root)
                result=exercise_agent(worker_root,real_binary.path,socket_path);code=0
            except BaseException:
                result={'passed':False,'traceback':traceback.format_exc()}
            try:result_path.write_text(json.dumps(result,indent=2))
            finally:os._exit(code)
        _,status=os.waitpid(pid,0)
        result=json.loads(result_path.read_text()) if result_path.exists() else {'passed':False,'traceback':'worker wrote no result'}
        assert os.waitstatus_to_exitcode(status)==0,result.get('traceback',result)
        assert result['passed'] is True and result['packet_drop_verified'] is True
        assert result['unix_peer_uid_verified'] is True
    finally:
        if broker.poll() is None:
            broker.terminate()
            try:broker.wait(timeout=3)
            except subprocess.TimeoutExpired:broker.kill();broker.wait(timeout=3)
        guard_log.close()
        shutil.rmtree(worker_root,ignore_errors=True)


"""Opt-in real Xray / verified Agent HTTPS lease acceptance, disposable loopback only."""
from __future__ import annotations
import contextlib
import socket
import socketserver
import struct
import threading

from node_runtime import LeaseGuard
from node_agent import EngineLoop
from test_node_real_data_plane import (
    real_binary, real_fleet, tls_material, transfer, usage, stable_identity, EMAIL, exact,
)


def grant(f, node):
    result=f.reg.sync_traffic(node.id)
    return f.http.app.state.renew_node_lease(node.id,result)


def arm(f):
    clocks=[]
    for node in f.agents:
        clock=[100.0];clocks.append(clock)
        node.runtime.hub_lease.clock=lambda c=clock:c[0]
        assert grant(f,node)['valid']
    return clocks


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            while data:=self.request.recv(4096):self.request.sendall(data)
        except OSError:pass


@contextlib.contextmanager
def existing_connections(f):
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=True
        daemon_threads=True
    server=Server(('127.0.0.1',0),Echo)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    sockets=[]
    try:
        for client in f.clients:
            s=socket.create_connection(('127.0.0.1',client.port),timeout=3)
            sockets.append(s);s.sendall(b'\x05\x01\x00');assert exact(s,2)==b'\x05\x00'
            s.sendall(b'\x05\x01\x00\x01'+socket.inet_aton('127.0.0.1')+struct.pack('!H',server.server_address[1]))
            header=exact(s,4);assert header[1]==0 and header[3] in (1,4)
            exact(s,6 if header[3]==1 else 18)
            s.sendall(b'BEFORE-EXPIRY');assert exact(s,13)==b'BEFORE-EXPIRY'
        yield sockets
    finally:
        for s in sockets:s.close()
        server.shutdown();server.server_close();thread.join(3)


def test_real_offline_checkpoints_close_existing_and_new_connections(real_fleet):
    f=real_fleet;identity=stable_identity(f);clocks=arm(f)
    before=usage(f)
    received=[transfer(client.port,f.target) for client in f.clients]
    for node in f.agents:
        node.engine.last_stats=0
        EngineLoop(node.engine,5,node.runtime).tick()
        mirror=node.runtime.mirror_for_source(EMAIL)
        local=node.store.db.execute('SELECT up+down FROM core_clients WHERE email=?',(mirror,)).fetchone()[0]
        assert local>0,'offline checkpoint missing'
    assert usage(f)==before,'Node-local checkpoint unexpectedly contacted Hub'
    with existing_connections(f) as sockets:
        for clock,node in zip(clocks,f.agents):
            clock[0]=161;LeaseGuard(node.runtime).tick();assert not node.engine.running
            health,_=f.reg._request(node.id,'/node/api/health')
            assert health['hub_lease']['state']=='expired'
        for s in sockets:
            try:s.sendall(b'AFTER');assert s.recv(64)==b'','existing session survived lease expiry'
            except (ConnectionResetError,BrokenPipeError):pass
        for client in f.clients:transfer(client.port,f.target,allowed=False)
    for node in f.agents:
        assert grant(f,node)['valid'] and node.engine.running
    after=usage(f);assert after[0]>=sum(received) and after[0]==after[2]
    assert after[0]==sum(up+down for _,up,down in after[1])
    for node in f.agents:grant(f,node)
    assert usage(f)==after,'replayed counters billed twice after recovery'
    for client in f.clients:transfer(client.port,f.target)
    assert stable_identity(f)==identity
    f.evidence.update(accounting_lease=True,offline_checkpoint=True,old_and_new_sessions_closed=True,
                      recovered_with_identical_clients=True,charged_bytes=after[0])


def test_real_global_quota_is_applied_before_lease_recovery(real_fleet):
    f=real_fleet
    f.api('/api/clients/'+EMAIL,{'client':{'totalGB':1024}},'PATCH')
    for node in f.agents:f.api('/api/nodes/'+node.id+'/sync',{})
    clocks=arm(f)
    received=transfer(f.clients[0].port,f.target)
    for clock,node in zip(clocks,f.agents):
        node.engine.last_stats=0;EngineLoop(node.engine,5,node.runtime).tick()
        clock[0]=161;LeaseGuard(node.runtime).tick()
    for node in f.agents:grant(f,node)
    total,rows,ledger=usage(f)
    assert total>=received>1024 and total==ledger==sum(up+down for _,up,down in rows)
    for node,client in zip(f.agents,f.clients):
        mirror=node.runtime.mirror_for_source(EMAIL)
        assert not node.engine.client_snapshot(mirror)['enable']
        transfer(client.port,f.target,allowed=False)
    f.evidence.update(global_quota_before_resume=True,charged_bytes=total)


def test_real_pending_start_cannot_deadlock_accounting_recovery(real_fleet):
    f=real_fleet;clocks=arm(f);node=f.agents[0]
    f.api('/api/nodes/'+node.id+'/core/stop',{})
    clocks[0][0]=161;LeaseGuard(node.runtime).tick()
    result=f.api('/api/nodes/'+node.id+'/core/start',{})
    assert result['queued'] and not node.engine.running
    grant(f,node)
    result=f.reg.deliver_pending_control(node.id)
    assert not result['queued'] and node.engine.running
    transfer(f.clients[0].port,f.target)

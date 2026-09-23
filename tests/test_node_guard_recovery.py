"""Pre-update Node regressions. Isolated SQLite/fake Xray; no production files."""
import time

import pytest
from fastapi.testclient import TestClient

import node_runtime as runtime_module
from node_agent import AgentToken,make_agent_app
from test_node_hub_recovery import agent,payload,post_state,NODE,ORIGIN,TOKEN


class RestartableGuard:
    """Only the root broker transport is replaced in these recovery tests."""
    def __init__(self):
        self.ports=[]
        self.calls=[]
        self.available=True
    def status(self):
        if not self.available:
            from dark_policy import PolicyError
            raise PolicyError('Guard temporarily unavailable')
        return {'runtime_port_updates':True,'allowed_ports':self.ports[:],
                'direct_source_verified':True,'protected_ports':[22,9443,10085]}
    def set_ports(self,ports):
        self.ports=list(ports);self.calls.append(self.ports[:])
        return {'ok':True,'allowed_ports':self.ports[:]}


def test_invalid_json_without_credentials_is_rejected_before_json_parser(tmp_path):
    with agent(tmp_path/'node') as (_,_,_,client):
        response=client.post('/node/api/v1/state/apply',content=b'{broken-json',
                            headers={'Authorization':'','Content-Type':'application/json'})
        assert response.status_code==401,response.text
        assert response.headers.get('cache-control')=='no-store'


def test_streamed_body_without_content_length_is_actually_bounded(tmp_path):
    with agent(tmp_path/'node') as (_,_,_,client):
        chunks=(b' '*(1024*1024) for _ in range(9))
        response=client.post('/node/api/v1/state/apply',content=chunks,
                            headers={'Content-Type':'application/json'})
        assert response.status_code==413,response.text[:200]
        assert response.headers.get('cache-control')=='no-store'


def test_duplicate_revision_restores_ports_after_guard_restart_without_xray_restart(tmp_path,monkeypatch):
    guard=RestartableGuard()
    monkeypatch.setattr(runtime_module,'BrokerClient',lambda *a,**k:guard)
    with agent(tmp_path/'node') as (_,engine,runtime,client):
        body=payload(engine)
        post_state(client,body)
        expected=[body['assignments'][0]['inbound']['port']]
        assert guard.ports==expected
        pid=engine.process.pid
        guard.ports=[]  # Broker restart clears its dynamically approved ports.
        response=post_state(client,body)
        assert response['changed'] is False
        assert guard.ports==expected
        assert engine.process.pid==pid


def test_guard_recovers_locally_without_a_new_hub_request(tmp_path,monkeypatch):
    guard=RestartableGuard()
    monkeypatch.setattr(runtime_module,'BrokerClient',lambda *a,**k:guard)
    with agent(tmp_path/'node') as (store,engine,_,client):
        body=payload(engine);post_state(client,body)
        expected=[body['assignments'][0]['inbound']['port']]
        engine.config.poll_seconds=1
        background_app=make_agent_app(engine,store,AgentToken(tmp_path/'node/token'),NODE,background=True)
        with TestClient(background_app,base_url=ORIGIN):
            guard.ports=[]
            deadline=time.monotonic()+2.5
            while guard.ports!=expected and time.monotonic()<deadline:time.sleep(.05)
            assert guard.ports==expected


def run_guard(tmp_path,chunks,extra_headers=(),authorized=True):
    """Exercise raw ASGI receive chunks (TestClient can combine generator chunks)."""
    import asyncio
    from node_agent import AgentRequestBoundary
    token_path=tmp_path/'request-token';token_path.write_text(TOKEN+'\n');token_path.chmod(0o600)
    scope={'type':'http','asgi':{'version':'3.0'},'method':'POST','scheme':'https',
           'path':'/node/api/v1/state/apply','query_string':b'',
           'headers':[(b'host',b'node.example.test:9443'),*extra_headers]}
    if authorized:scope['headers'].append((b'authorization',('Bearer '+TOKEN).encode()))
    evidence={'reads':0,'called':False,'body':b'','messages':[]}
    events=list(chunks)
    async def receive():
        evidence['reads']+=1
        item=events.pop(0)
        if item=='slow':
            await asyncio.sleep(.1)
            return {'type':'http.request','body':b'','more_body':False}
        return item
    async def send(message):evidence['messages'].append(message)
    async def handler(scope,receive,send):
        evidence['called']=True
        message=await receive();evidence['body']=message.get('body',b'')
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'OK'})
    guard=AgentRequestBoundary(handler,AgentToken(token_path),'node.example.test:9443')
    guard.MAX_BODY=8;guard.READ_TIMEOUT=.02
    asyncio.run(guard(scope,receive,send))
    starts=[m for m in evidence['messages'] if m['type']=='http.response.start']
    evidence['status']=starts[0]['status'] if starts else None
    return evidence


def chunk(raw,more=False):return {'type':'http.request','body':raw,'more_body':more}


@pytest.mark.parametrize('events,headers,status,reads',[
    ([chunk(b'abcd',True),chunk(b'efgh')],[],200,2),
    ([chunk(b'abcd',True),chunk(b'efghi',True),chunk(b'never-read')],[],413,2),
    ([chunk(b'abcd')],[(b'content-length',b'2')],400,1),
    ([chunk(b'abcd')],[(b'content-length',b'4')],200,1),
    ([chunk(b'abcd')],[(b'content-length',b'9')],413,0),
    ([chunk(b'abcd')],[(b'content-length',b'-1')],400,0),
    ([chunk(b'abcd')],[(b'content-length',b'4'),(b'content-length',b'4')],400,0),
    ([chunk(b'abcd')],[(b'content-encoding',b'gzip')],415,0),
    (['slow'],[],408,1),
    ([{'type':'http.disconnect'}],[],None,1),
])
def test_asgi_guard_counts_bytes_before_command_handler(tmp_path,events,headers,status,reads):
    result=run_guard(tmp_path,events,headers)
    assert result['status']==status
    assert result['reads']==reads
    assert result['called']==(status==200)
    if status==200:assert result['body'] in {b'abcdefgh',b'abcd'}


def test_unauthorized_request_does_not_read_a_single_body_chunk(tmp_path):
    result=run_guard(tmp_path,[chunk(b'never-read')],authorized=False)
    assert result['status']==401 and result['reads']==0 and not result['called']


def test_repeated_guard_checks_do_not_clear_or_rewrite_live_allowlist(tmp_path,monkeypatch):
    guard=RestartableGuard();monkeypatch.setattr(runtime_module,'BrokerClient',lambda *a,**k:guard)
    with agent(tmp_path/'node') as (_,engine,runtime,client):
        body=payload(engine);post_state(client,body)
        count=len(guard.calls);pid=engine.process.pid
        for _ in range(3):runtime.reconcile_guard()
        assert len(guard.calls)==count
        assert engine.process.pid==pid and runtime.status()['appliedRevision']==1


def test_no_approved_revision_cannot_update_guard_allowlist(tmp_path,monkeypatch):
    guard=RestartableGuard();monkeypatch.setattr(runtime_module,'BrokerClient',lambda *a,**k:guard)
    with agent(tmp_path/'node') as (_,_,runtime,_):runtime.reconcile_guard()
    assert guard.calls==[]


def test_guard_recovery_keeps_root_protected_port_rejection(tmp_path,monkeypatch):
    from dark_policy import PolicyError
    guard=RestartableGuard();monkeypatch.setattr(runtime_module,'BrokerClient',lambda *a,**k:guard)
    with agent(tmp_path/'node') as (store,engine,runtime,client):
        body=payload(engine);post_state(client,body);pid=engine.process.pid
        guard.ports=[]
        def reject(_):raise PolicyError('Root refuses protected management ports')
        monkeypatch.setattr(guard,'set_ports',reject)
        with pytest.raises(PolicyError,match='protected'):runtime.reconcile_guard()
        assert guard.ports==[] and engine.process.pid==pid
        assert runtime.status()['appliedRevision']==1


def test_maintenance_error_is_visible_and_success_clears_it(tmp_path,monkeypatch):
    from node_agent import EngineLoop
    from dark_policy import PolicyError
    with agent(tmp_path/'node') as (_,engine,runtime,_):
        loop=EngineLoop(engine,1,runtime)
        collections=[]
        monkeypatch.setattr(engine,'flush',lambda:collections.append('traffic'))
        def fail():raise PolicyError('Guard unavailable for approved data ports')
        monkeypatch.setattr(runtime,'reconcile_guard',fail)
        loop.tick()
        assert 'PolicyError' in loop.last_error and loop.last_success==0
        assert collections==['traffic']
        monkeypatch.setattr(runtime,'reconcile_guard',lambda:None)
        loop.tick()
        assert loop.last_error=='' and loop.last_success>0


def test_agent_startup_restores_guard_before_background_tick(tmp_path,monkeypatch):
    guard=RestartableGuard();monkeypatch.setattr(runtime_module,'BrokerClient',lambda *a,**k:guard)
    with agent(tmp_path/'node') as (store,engine,runtime,client):
        body=payload(engine);post_state(client,body)
        guard.ports=[]
        app=make_agent_app(engine,store,AgentToken(tmp_path/'node/token'),NODE,background=True)
        with TestClient(app,base_url=ORIGIN):
            assert guard.ports==[body['assignments'][0]['inbound']['port']]
            assert app.state.loop.last_success==0  # The first timed tick has not run.

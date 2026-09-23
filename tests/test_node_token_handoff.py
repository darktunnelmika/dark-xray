"""Credential/idle safety with actual files and Agent API, not physical power-loss proof."""
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from node_agent import AgentToken
from test_node_replacement_prepare import candidate
from test_node_hub_recovery import TOKEN,payload,post_state

NEW='dkn_'+'B'*60
OTHER='dkn_'+'C'*60


def authorized(token=TOKEN):
    return Request({'type':'http','method':'POST','path':'/node/api/v1/token/rotate',
                    'headers':[(b'authorization',('Bearer '+token).encode())]})


def token_file(tmp_path):
    path=tmp_path/'token';path.write_text(TOKEN+'\n');path.chmod(0o600)
    return AgentToken(path)


def test_buffered_old_authenticated_rotation_cannot_replace_new_credential(tmp_path):
    token=token_file(tmp_path);stale=authorized()
    token.require(stale)
    token.rotate(NEW,request=stale)
    with pytest.raises(HTTPException) as exc:token.rotate(OTHER,request=stale)
    assert exc.value.status_code==401
    assert token.token==NEW and AgentToken(token.path).token==NEW


def test_concurrent_different_rotations_compare_original_bearer_under_lock(tmp_path):
    token=token_file(tmp_path)
    def rotate(value):
        try:token.rotate(value,request=authorized());return 200
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(rotate,[NEW,OTHER]))
    assert sorted(results)==[200,401]
    assert token.token in (NEW,OTHER) and AgentToken(token.path).token==token.token
    assert token.path.stat().st_mode&0o777==0o600


@pytest.mark.parametrize('stage',['file-fsync','replace','directory-fsync'])
def test_rotation_io_failure_never_splits_runtime_from_token_file(tmp_path,monkeypatch,stage):
    token=token_file(tmp_path);fsync=os.fsync;calls=[0]
    def sync(fd):
        calls[0]+=1
        if calls[0]==(2 if stage=='directory-fsync' else 1):raise OSError('Injected fsync failure')
        return fsync(fd)
    with monkeypatch.context() as patch:
        if stage=='replace':patch.setattr(os,'replace',lambda *a:(_ for _ in ()).throw(OSError('Injected replace failure')))
        else:patch.setattr(os,'fsync',sync)
        with pytest.raises(OSError):token.rotate(NEW,request=authorized())
    expected=NEW if stage=='directory-fsync' else TOKEN
    assert token.token==AgentToken(token.path).token==expected
    assert list(tmp_path.glob('.token.rotate.*'))==[]


def test_old_crashed_temp_file_and_pair_marker_symlink_do_not_block_rotation(tmp_path):
    token=token_file(tmp_path)
    old=tmp_path/('.token.rotate.'+str(os.getpid()));old.write_text('old interrupted write')
    outside=tmp_path/'untouched';outside.write_text('keep')
    (tmp_path/'pair-consumed').symlink_to(outside)
    token.rotate(NEW,request=authorized())
    assert outside.read_text()=='keep' and AgentToken(token.path).token==NEW
    assert old.read_text()=='old interrupted write'


@pytest.mark.parametrize('mode',['anonymous','readonly','wrong-installation'])
def test_rotation_endpoint_security_before_token_change(tmp_path,mode):
    with candidate(tmp_path/'node') as (engine,runtime,client,token):
        headers={}
        if mode=='anonymous':headers['Authorization']=''
        if mode=='readonly':engine.config.writes_enabled=False
        if mode=='wrong-installation':headers['X-Dark-Expected-Installation-Id']='a'*32
        response=client.post('/node/api/v1/token/rotate',json={'token':NEW},headers=headers)
        assert response.status_code in (401,409)
        assert token.token==TOKEN and AgentToken(token.path).token==TOKEN


@pytest.mark.parametrize('mode',['applied','unassigned-inbound','ordered-command'])
def test_idle_endpoint_rechecks_freshness_not_browser_claims(tmp_path,mode):
    with candidate(tmp_path/'node') as (engine,runtime,client,_):
        body=payload(engine);body['nodeId']='new-turkey'
        if mode=='applied':post_state(client,body)
        elif mode=='unassigned-inbound':engine.save_inbound(body['assignments'][0]['inbound'])
        else:
            runtime.ordered_command({'nodeId':'new-turkey','revision':1,'commandId':'f'*32,'action':'stop'})
        before=runtime.control_status();running=engine.running
        response=client.post('/node/api/v1/replacement/idle',json={})
        assert response.status_code==409,response.text
        assert runtime.control_status()==before and engine.running==running


def test_idle_empty_candidate_is_durable_and_idempotent(tmp_path):
    root=tmp_path/'node'
    with candidate(root,background=True) as (engine,runtime,client,_):
        assert engine.running
        response=client.post('/node/api/v1/replacement/idle',json={})
        assert response.status_code==200,response.text
        assert not engine.running and runtime.control_status()['manual_stop']
        assert not runtime.command_status()['persisted'] and not runtime.status()['appliedRevision']
        assert client.post('/node/api/v1/replacement/idle',json={}).status_code==200
    with candidate(root,background=True) as (engine,runtime,_,_):
        assert not engine.running and runtime.control_status()['manual_stop']


@pytest.mark.parametrize('mode',['nonempty-body','anonymous','readonly','wrong-installation'])
def test_idle_boundary_never_changes_run_intent(tmp_path,mode):
    with candidate(tmp_path/'node') as (engine,runtime,client,_):
        before=runtime.control_status();body={};headers={}
        if mode=='nonempty-body':body={'fresh':True}
        elif mode=='anonymous':headers['Authorization']=''
        elif mode=='readonly':engine.config.writes_enabled=False
        else:headers['X-Dark-Expected-Installation-Id']='a'*32
        response=client.post('/node/api/v1/replacement/idle',json=body,headers=headers)
        assert response.status_code in (401,409,422),response.text
        assert runtime.control_status()==before

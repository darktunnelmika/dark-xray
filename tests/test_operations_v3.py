import json
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from test_standalone import env
from server import make_app
from operations_overview import snapshot, safe_error


def test_overview_is_read_only_no_probe_or_enforcement(env, monkeypatch):
    store, engine, manager, auth, c = env
    reg = c.app.state.nodes
    monkeypatch.setattr(reg, '_request', lambda *a, **k: pytest.fail('No remote request allowed'))
    monkeypatch.setattr(engine, 'apply', lambda *a, **k: pytest.fail('No Apply allowed'))
    monkeypatch.setattr(manager, 'tick', lambda *a, **k: pytest.fail('No reconciliation allowed'))
    before = store.db.total_changes
    response = c.get('/api/operations/overview')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['read_only'] and value['network_probes'] is False
    assert value['hub']['status'] == 'stopped'
    assert value['nodes'] == []
    assert value['warp'][0]['status'] == 'not_configured'
    assert value['warp'][0]['connectivity'] == 'not_tested'
    assert value['summary']['warp_attention'] == 0
    assert store.db.total_changes == before


def test_overview_is_owner_only_and_requires_login(env):
    store, engine, manager, auth, c = env
    assert c.put('/api/owners/opsrep', json={'name':'Ops Rep','allowed':[]}).status_code == 200
    assert c.post('/api/admins',json={'username':'opsrep','password':'OpsRepPass88','role':'reseller'}).status_code == 200
    token, p = auth.login('opsrep','OpsRepPass88','','127.0.0.9',3600,'ops-v3')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as rep:
        assert rep.get('/api/operations/overview').status_code == 401
        rep.cookies.set('dark_session',token)
        assert rep.get('/api/operations/overview').status_code == 403


def node(now, **kwargs):
    base = {'id':'fixture','name':'Fixture Node','enabled':True,'online':True,'last_seen':now-5,
        'health':{'core':{'state':'running','dirty':False}},
        'desired_state':{'applied_revision':4,'revision':4,'pending':False,'applied_at':now-20}}
    return base | kwargs


@pytest.mark.parametrize('change,status', [({},'applied'),({'last_seen':None},'unknown'),
    ({'last_seen':1},'offline'),({'last_seen':time.time()-90},'stale'),
    ({'online':False},'offline'),({'control':{'pending':True}},'pending'),
    ({'desired_state':{}},'pending'),({'health':{'core':{'state':'stopped'}}},'stopped'),
    ({'last_error':'connection failed'},'error'),({'assignments':[{'last_error':'sync failed'}]},'error')])
def test_node_acknowledgement_is_not_confused_with_freshness(env, change, status):
    store, engine, *_ = env
    now = time.time()
    reg = SimpleNamespace(list=Mock(return_value=[node(now,**change),node(now,id='disabled',enabled=False)]))
    result = snapshot(store,engine,reg,now=now)
    reg.list.assert_called_once()
    assert len(result['nodes']) == 1 and result['nodes'][0]['status'] == status
    assert [x['server'] for x in result['warp']] == ['hub','node:fixture']
    assert all(x['status'] == 'not_configured' for x in result['warp'])


@pytest.mark.parametrize('enabled,configured,observed,runtime,error,state',[
    (False,True,True,'online','','disabled'),(True,False,True,'online','','unconfigured'),
    (True,True,True,'online','','online'),(True,True,False,'online','','stale'),
    (True,True,True,'stopped','','stopped'),(True,True,True,'error','error details','error')])
def test_bot_runtime_and_observation_are_required(env, enabled, configured, observed, runtime, error, state):
    store, engine, *_ = env
    now = time.time()
    with store.transaction() as db:
        db.execute('INSERT INTO telegram_bots(owner,enabled,token_enc,admin_telegram_id,updated_at,last_seen,last_error) VALUES(?,?,?,?,?,?,?)',
                   ('dark',int(enabled),'not-a-real-secret' if configured else '',1,now,now-5 if observed else now-190,error))
    worker = SimpleNamespace(status=Mock(return_value={'runtime_state':runtime}))
    result = snapshot(store,engine,SimpleNamespace(list=lambda:[]),worker,now=now)
    assert result['telegram'][0]['state'] == state
    assert 'not-a-real-secret' not in json.dumps(result)


def test_warp_saved_state_never_claims_connectivity(env, monkeypatch):
    store, engine, *_ = env
    now = time.time()
    monkeypatch.setattr(engine,'runtime_state',lambda:{'state':'running','dirty':False,'last_apply':now})
    with store.transaction() as db:
        db.execute('INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,?)',
            ('hub',json.dumps({'settings':{'secretKey':'sensitive-private-key','peers':[{'endpoint':'162.159.192.1:2408'}]}}),'sensitive-device',now))
    result = snapshot(store,engine,SimpleNamespace(list=lambda:[]),now=now)
    assert result['warp'][0]['status'] == 'applied'
    assert result['warp'][0]['connectivity'] == 'not_tested'
    assert result['warp'][0]['endpoint'] == '162.159.192.1:2408'
    assert 'sensitive' not in json.dumps(result)


def test_bad_profile_visible_as_error(env):
    store, engine, *_ = env
    with store.transaction() as db:
        db.execute("INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES('hub','not-json','fixture',1)")
    result = snapshot(store,engine,SimpleNamespace(list=lambda:[]))
    assert result['warp'][0]['status'] == 'error'
    assert result['summary']['warp_attention'] == 1


@pytest.mark.parametrize('error',['https://api.telegram.org/bot123456789:abcDEF_ABCdef12345678901234/getMe',
    'token=never-display password:1234 secretKey="private"','Authorization: Bearer SECRET'])
def test_error_messages_redact_credential_bearing_text(error):
    sanitized = safe_error(error)
    assert all(x not in sanitized for x in ['abcDEF','never-display','1234','private','SECRET'])
    assert 'redact' in sanitized.lower()

"""Owner-only local operation discovery; real SQLite/API, never a remote probe."""
import json
import uuid

import pytest
import nodes as node_module
from test_hub_node_control_live import hub
from test_node_hub_recovery import NODE, TOKEN
from test_node_replacement_prepare import code, candidate, prepare
from test_node_replacement_resolution import resolve
from test_node_replacement_activation import review, consent
from test_node_installations import http_transport, seed_account

URL=f'/api/nodes/{NODE}/replacement/current'


def test_empty_discovery_and_missing_node(hub,monkeypatch):
    reg,owner,_=hub
    monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('GET contacted target'))
    result=owner.get(URL)
    assert result.status_code==200,result.text
    assert result.json()['attempt'] is None and not result.json()['network_verified']
    assert owner.get('/api/nodes/missing/replacement/current').status_code==400


def test_lost_prepare_reply_discovery_survives_registry_recreation_and_never_exposes_tokens(hub,monkeypatch):
    from node_replacement import NodeReplacement
    from nodes import NodeRegistry
    reg,owner,_=hub
    pending=owner.app.state.replacements.begin(NODE,code())
    rebuilt=NodeRegistry(reg.store,reg.cipher);controller=NodeReplacement(rebuilt)
    monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('GET contacted target'))
    changes=reg.store.db.total_changes
    for result in (owner.get(URL).json(),controller.current_status(NODE)):
        assert result['attempt']['attempt_id']==pending['attempt_id']
        assert result['attempt']['phase']=='pending'
        text=json.dumps(result)
        assert TOKEN not in text and 'candidate_enc' not in text and 'bootstrap_enc' not in text
        assert not result['has_activation'] and result['activation_resume'] is None
    assert changes==reg.store.db.total_changes


@pytest.mark.parametrize('mode',['anonymous','reseller','key','readonly'])
def test_discovery_access_contract(hub,mode):
    reg,owner,engine=hub
    if mode=='anonymous':owner.cookies.clear()
    if mode=='reseller':
        with reg.store.transaction() as db:db.execute("UPDATE api_admins SET role='reseller' WHERE id='dark'")
    if mode=='key':
        response=owner.post('/api/keys',json={'name':'test','days':1,'permissions':{'clients.read':'all'}})
        assert response.status_code==200,response.text
        owner.cookies.clear();owner.headers['Authorization']='Bearer '+response.json()['key']
    if mode=='readonly':engine.config.writes_enabled=False
    response=owner.get(URL)
    assert response.status_code==({'anonymous':401,'reseller':403,'key':403,'readonly':200}[mode]),response.text


def test_current_committed_receipt_beats_later_cancelled_history(hub):
    reg,owner,_=hub;binding=reg.installations.capture(NODE)['binding_id']
    first,second=uuid.uuid4().hex,uuid.uuid4().hex
    with reg.store.transaction() as db:
        for attempt,phase,bid in ((first,'committed',binding),(second,'cancelled','')):
            doc={'node_id':NODE,'attempt_id':attempt,'phase':phase,'committed_binding_id':bid}
            db.execute('INSERT INTO remote_node_replacement_history VALUES(?,?,?)',(attempt,NODE,json.dumps(doc)))
    found=owner.get(URL).json()
    assert found['attempt']['attempt_id']==first and found['attempt']['binding_current']
    pending=owner.app.state.replacements.begin(NODE,code())
    assert owner.get(URL).json()['attempt']['attempt_id']==pending['attempt_id']


def test_cancelled_history_is_available_when_no_current_committed_receipt(hub):
    reg,owner,_=hub;attempt=uuid.uuid4().hex
    with reg.store.transaction() as db:
        db.execute('INSERT INTO remote_node_replacement_history VALUES(?,?,?)',
                   (attempt,NODE,json.dumps({'node_id':NODE,'attempt_id':attempt,'phase':'cancelled'})))
    found=owner.get(URL).json()
    assert found['attempt']['phase']=='cancelled' and not found['attempt']['binding_current']


def test_pending_start_hash_is_recovered_only_for_the_current_binding(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub;seed_account(reg,owner,engine)
    with candidate(tmp_path/'target') as (_,_,client,token):
        http_transport(reg,client,monkeypatch)
        receipt=resolve(owner,prepare(owner));checked=review(owner,receipt)
        controller=owner.app.state.replacement_activation
        controller._begin(NODE,receipt['attempt_id'],checked['review_hash'])
        monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('GET contacted target'))
        changes=reg.store.db.total_changes
        found=owner.get(URL).json()
        assert found['has_activation'] and found['has_deployment']
        assert found['activation_resume']=={'binding_id':receipt['committed_binding_id'],
            'phase':'starting','review_hash':checked['review_hash']}
        assert reg.store.db.total_changes==changes
        assert TOKEN not in json.dumps(found) and token.token not in json.dumps(found)
        with reg.store.transaction() as db:
            db.execute("UPDATE remote_node_replacement_activations SET phase='paused'")
        assert owner.get(URL).json()['activation_resume'] is None
        with reg.store.transaction() as db:
            db.execute("UPDATE remote_node_replacement_activations SET phase='starting',binding_id=?",(uuid.uuid4().hex,))
        assert owner.get(URL).json()['activation_resume'] is None


def test_current_alias_is_not_accepted_as_a_mutation_attempt(hub,monkeypatch):
    reg,owner,_=hub
    from test_node_installations import sql_rows
    monkeypatch.setattr(node_module,'node_https_request',lambda *a,**k:pytest.fail('Invalid attempt contacted target'))
    tables=('remote_nodes','remote_node_replacements','remote_node_replacement_history')
    before={t:sql_rows(reg,t) for t in tables}
    response=owner.post(URL+'/retry',json={})
    assert response.status_code==400,response.text
    # The rejected explicit POST may be audited; no target or replacement state changes.
    assert {t:sql_rows(reg,t) for t in tables}==before

"""Same request on parent and candidate: lose the reply after Agent accepts rotation."""
import json
import pytest
from dark_policy import PolicyError
from test_hub_node_control_live import hub
from test_node_replacement_prepare import candidate, code
from test_node_installations import http_transport
from test_node_hub_recovery import TOKEN


def test_accepted_rotation_lost_reply_retains_recoverable_pair(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        def lost(response):
            if response.request.method=='POST' and ('rotate' in response.request.url.path):
                raise PolicyError('Injected loss AFTER rotation')
        http_transport(reg,client,monkeypatch,response_hook=lost)
        response=owner.post('/api/nodes/pair',json={'code':code()})
        print(json.dumps({'response_status':response.status_code, 'credential_changed':token.token!=TOKEN,
                          'registered':reg.store.db.execute("SELECT 1 FROM remote_nodes WHERE id='new-turkey'").fetchone() is not None,
                          'journal_table_exists':reg.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='remote_node_pairings'").fetchone() is not None}))
        assert token.token!=TOKEN, 'The failure injection must happen after the credential changes'
        assert response.status_code==200 and response.json().get('paired') is False, response.text
        saved=response.json()
        journal=reg.store.db.execute('SELECT candidate_enc FROM remote_node_pairings WHERE attempt_id=?',
                                     (saved['attempt_id'],)).fetchone()
        assert journal and reg.cipher.decrypt(journal['candidate_enc'].encode()).decode()==token.token
        assert reg.store.db.execute('SELECT 1 FROM remote_nodes WHERE id=?',('new-turkey',)).fetchone() is None

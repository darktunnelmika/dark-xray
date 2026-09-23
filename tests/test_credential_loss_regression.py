"""Portable parent/candidate reproductions through the original PATCH endpoint."""
import json

from dark_policy import PolicyError
from test_hub_node_control_live import hub
from test_node_control_lifecycle import rebooted_agent
from test_node_installations import http_transport
from test_node_hub_recovery import NODE, TOKEN, ORIGIN

CANDIDATE='dkn_'+'P'*60


def edit(inbounds=None):
    return {'name':'Node','origin':ORIGIN,'dataAddress':'node.example.test','priority':100,
        'enabled':True,'failoverEnabled':True,'inboundIds':inbounds or [],'token':CANDIDATE}


def test_invalid_inbound_cannot_rotate_agent_before_edit_rejection(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        seen=http_transport(reg,client,monkeypatch);reg.probe(NODE);seen.clear()
        response=owner.patch('/api/nodes/'+NODE,json=edit([999]))
        assert response.status_code==400,response.text
        assert not any(m=='POST' for m,_,_ in seen),'Invalid edit already changed the Agent token'
        assert reg.get(NODE,secret=True)['token']==TOKEN


def test_accepted_registered_rotation_lost_reply_keeps_a_recovery_credential(hub,tmp_path,monkeypatch):
    reg,owner,_=hub;reg.set_inbound_assignment(NODE,1,False)
    with rebooted_agent(tmp_path/'agent') as (_,_,_,client,_):
        def dropped(response):
            if response.request.url.path.endswith('/token/rotate'):
                raise PolicyError('Injected loss after registered Agent token replacement')
        http_transport(reg,client,monkeypatch,response_hook=dropped);reg.probe(NODE)
        response=owner.patch('/api/nodes/'+NODE,json=edit())
        # It really reached the target, not merely a simulated network failure.
        assert client.get('/node/api/health',headers={'Authorization':'Bearer '+CANDIDATE}).status_code==200
        with reg.store.lock:
            exists=reg.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_credentials'").fetchone()
            assert exists,'Agent changed token, but Hub has no durable handoff journal'
            row=reg.store.db.execute("SELECT candidate_enc,phase FROM remote_node_credentials WHERE node_id=?",(NODE,)).fetchone()
        assert row and row['phase'] in ('pending','rotating')
        assert reg.cipher.decrypt(row['candidate_enc'].encode()).decode()==CANDIDATE
        assert response.status_code==200 and response.json()['pending'] is True

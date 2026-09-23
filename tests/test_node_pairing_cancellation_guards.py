"""Publication guards: real Hub/Agent handlers and SQLite, fixture Xray/transport."""
import json

import pytest
import nodes as nodes_module
from nodes import NodeHTTPError
from test_hub_node_control_live import hub
from test_node_installations import http_transport, sql_rows
from test_node_pairing import PATH, pending
from test_node_pairing_cancellation import CONSENT, cancel, interrupted, assert_cancelled
from test_node_replacement_prepare import candidate
from test_node_hub_recovery import TOKEN


def test_wrong_key_cannot_poison_saved_cancellation_credentials(hub,tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    reg,owner,_=hub;service=owner.app.state.pairing
    with candidate(tmp_path/'agent') as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch);before=pending(reg);accepted=token.token
        seen=http_transport(reg,client,monkeypatch)
        with monkeypatch.context() as wrong:
            wrong.setattr(service,'cipher',Fernet(Fernet.generate_key()))
            response=owner.post(PATH+'/'+saved['attempt_id']+'/cancel',json=CONSENT)
        assert response.status_code==400,response.text
        assert pending(reg)==before and seen==[] and token.token==accepted
        assert_cancelled(cancel(owner,saved),True)


@pytest.mark.parametrize('credential_field',['bootstrap_enc','candidate_enc'])
def test_cancel_requires_verified_rejection_of_each_earlier_credential(hub,tmp_path,monkeypatch,credential_field):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch);before=pending(reg)
        old=reg.cipher.decrypt(before[credential_field].encode()).decode()
        seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request;rotated=False
        def transport(origin,credential,path,*args,**kwargs):
            nonlocal rotated
            if rotated and path=='/node/api/health' and credential==old:
                return real(origin,token.token,path,*args,**kwargs)
            result=real(origin,credential,path,*args,**kwargs)
            if path.endswith('/replacement/rotate-token'):rotated=True
            return result
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        result=cancel(owner,saved)
        assert result['phase']=='cancelling' and result['cancelled'] is False
        assert result['last_error']=='pairing_cancellation_old_credential_accepted'
        row=pending(reg);disposal=reg.cipher.decrypt(row['disposal_enc'].encode()).decode()
        assert disposal==token.token and row['candidate_enc']==before['candidate_enc']
        assert not sql_rows(reg,'remote_node_pair_cancellations')
        assert all(secret not in json.dumps(result) for secret in (TOKEN,old,disposal))
        monkeypatch.setattr(nodes_module,'node_https_request',real);seen.clear()
        assert_cancelled(cancel(owner,saved),True)
        assert token.token==disposal and all(method=='GET' for method,_,_ in seen)


@pytest.mark.parametrize('failure',['timeout','503'])
def test_old_credential_check_transport_error_is_not_revocation(hub,tmp_path,monkeypatch,failure):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch)
        old=reg.cipher.decrypt(pending(reg)['candidate_enc'].encode()).decode()
        seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request;rotated=False
        def transport(origin,credential,path,*args,**kwargs):
            nonlocal rotated
            if rotated and path=='/node/api/health' and credential==old:
                if failure=='503':raise NodeHTTPError(503)
                raise OSError('timeout '+credential)
            result=real(origin,credential,path,*args,**kwargs)
            if path.endswith('/replacement/rotate-token'):rotated=True
            return result
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        result=cancel(owner,saved)
        assert result['phase']=='cancelling' and not result['cancelled']
        assert result['last_error']=='pairing_cancellation_contact_or_verification_failed'
        row=pending(reg);disposal=reg.cipher.decrypt(row['disposal_enc'].encode()).decode()
        assert disposal==token.token and disposal not in json.dumps(result) and old not in json.dumps(result)
        monkeypatch.setattr(nodes_module,'node_https_request',real);seen.clear()
        assert_cancelled(cancel(owner,saved),True)
        assert token.token==disposal and all(method=='GET' for method,_,_ in seen)


def test_cancellation_rechecks_idle_after_credential_rejections(hub,tmp_path,monkeypatch):
    reg,owner,_=hub
    with candidate(tmp_path/'agent') as (_,_,client,token):
        saved=interrupted(reg,owner,client,monkeypatch)
        real_seen=http_transport(reg,client,monkeypatch);real=nodes_module.node_https_request;revoked=[]
        def transport(origin,credential,path,*args,**kwargs):
            try:result=real(origin,credential,path,*args,**kwargs)
            except NodeHTTPError as exc:
                row=pending(reg)
                if exc.status==401 and row and row['disposal_enc']:
                    disposal=reg.cipher.decrypt(row['disposal_enc'].encode()).decode()
                    if token.token==disposal and credential in (TOKEN,reg.cipher.decrypt(row['candidate_enc'].encode()).decode()):
                        revoked.append(credential)
                raise
            if path=='/node/api/health' and len(set(revoked))==2:
                doc,latency=result;doc['run_control']['manual_stop']=False
            return result
        monkeypatch.setattr(nodes_module,'node_https_request',transport)
        result=cancel(owner,saved)
        assert result['phase']=='cancelling' and not result['cancelled']
        assert result['last_error']=='pairing_target_not_durably_idle'
        assert len(set(revoked))==2 and pending(reg)
        disposal=token.token;monkeypatch.setattr(nodes_module,'node_https_request',real);real_seen.clear()
        assert_cancelled(cancel(owner,saved),True)
        assert token.token==disposal and all(method=='GET' for method,_,_ in real_seen)

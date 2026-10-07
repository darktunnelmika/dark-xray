import json
import time
import pytest
from fastapi.testclient import TestClient
from test_representatives_v2 import env, create_inbound, rep_body, OWNER
from dark_policy import PolicyError


def setup(env,credit=500):
    store,engine,manager,auth,c=env
    inbound=create_inbound(c)
    r=c.put('/api/resellers/seller',json=rep_body(inbound,unlimited_credit=credit,max_client_ips=5,max_client_hwid=0))
    assert r.status_code==200,r.text
    return inbound

def balance(env):
    return env[0].owner_stats(OWNER,'seller')['unlimited_credit_remaining']

def test_panel_quote_create_renew_delete(env):
    inbound=setup(env);c=env[-1]
    payload={'email':'s_months','limitIp':5,'expiryTime':int(time.time()*1000)+60*86400000,'totalGB':0}
    q=c.post('/api/unlimited-credit/quote',json={'owner':'seller','client':payload})
    assert q.status_code==200 and q.json()['units']==10 and q.json()['after']==490
    assert balance(env)==500
    r=c.post('/api/clients',json={'owner':'seller','client':payload,'inboundIds':[inbound]})
    assert r.status_code==202,r.text
    assert balance(env)==490
    payload['expiryTime']+=30*86400000;payload.pop('email')
    for _ in range(2):
        r=c.patch('/api/clients/s_months',json={'client':payload})
        assert r.status_code==202,r.text
        assert balance(env)==485
    assert c.post('/api/clients/s_months/action',json={'action':'delete'}).status_code==202
    assert balance(env)==485

def test_creation_failure_restores_credit_and_desired(env):
    inbound=setup(env);store,_,manager,_,_=env
    store.db.execute("CREATE TRIGGER fail_desired BEFORE INSERT ON managed_clients BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(PolicyError):
        manager.create(OWNER,'seller',{'email':'s_fail','limitIp':5,'expiryTime':int(time.time()*1000)+60*86400000},[inbound])
    assert balance(env)==500
    assert store.db.execute("SELECT COUNT(*) FROM clients WHERE owner='seller'").fetchone()[0]==0

def test_bot_product_no_debit_and_fulfillment_retry_once(env,monkeypatch):
    inbound=setup(env);store,engine,manager,_,c=env
    commerce=c.app.state.telegram_runtime.commerce
    product=commerce.create_simple_plan('seller',{'name':'Five users two months','plan_type':'unlimited',
        'price_minor':100,'duration_days':60,'ip_limit':5,'inbound_ids':[inbound],'published':True})
    assert product['prices'][0]['unlimited_units']==10
    assert balance(env)==500
    order=commerce.create_order('seller',987001,'testbuyer',product['id'],product['prices'][0]['id'])
    store.db.execute("UPDATE commerce_orders SET status='paid' WHERE id=?",(order['id'],))
    result=commerce.provision_order(order['id'],manager);identity=result['client_id']
    assert result['activation_pending'] is True
    assert balance(env)==490
    assert commerce.provision_order(order['id'],manager)['client_id']==identity
    assert balance(env)==490
    # Simulate a crash between durable client staging and attaching the order.
    store.db.execute("UPDATE commerce_orders SET client_id='',status='paid' WHERE id=?",(order['id'],))
    assert commerce.provision_order(order['id'],manager)['client_id']==identity
    assert balance(env)==490
    store.record_usage('credit-first-connection',identity,10,20)
    store.db.execute("CREATE TRIGGER fail_activation BEFORE UPDATE ON commerce_orders WHEN NEW.status='provisioned' BEGIN SELECT RAISE(ABORT,'test activation marker'); END")
    assert commerce.activate_first_connections(manager)==[]
    assert balance(env)==490
    store.db.execute('DROP TRIGGER fail_activation')
    later=time.time()+5
    monkeypatch.setattr(time,'time',lambda:later)
    assert commerce.activate_first_connections(manager)[0]['client_id']==identity
    assert balance(env)==490
    assert commerce.activate_first_connections(manager)==[]

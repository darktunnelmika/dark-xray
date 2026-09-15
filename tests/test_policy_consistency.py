import json
from test_standalone import env,create,IB


def test_owner_ip_ceiling_cannot_strand_existing_clients(env):
    store,engine,m,auth,c=env
    create(c,extra={'limitIp':5})
    r=c.put('/api/owners/dark',json={'name':'DARK','allowed':[1],'max_client_ips':2})
    assert r.status_code==400,r.text
    assert 'Reduce existing client IP limits' in r.text
    # Once the client is made compliant, the owner ceiling can be lowered.
    assert c.patch('/api/clients/dark-test',json={'client':{'limitIp':2}}).status_code==202
    r=c.put('/api/owners/dark',json={'name':'DARK','allowed':[1],'max_client_ips':2})
    assert r.status_code==200,r.text


def test_non_customer_inbound_cannot_receive_managed_client(env):
    store,engine,m,auth,c=env
    ib=dict(IB);ib.update(protocol='dokodemo-door',tag='infra-door',port=19001,settings={'address':'127.0.0.1','port':80,'network':'tcp'})
    r=c.post('/api/inbounds',json=ib);assert r.status_code==200,r.text
    ids=[x['id'] for x in c.get('/api/inbounds').json()]
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'bad-infra-client'},'inboundIds':[ids[-1]]})
    assert r.status_code==400,r.text
    assert 'credential-bearing' in r.text


def test_client_delete_cleans_periodic_cycle(env):
    store,engine,m,auth,c=env
    create(c,extra={'reset':30})
    with store.lock:assert store.db.execute('SELECT 1 FROM client_cycles WHERE email=?',('dark-test',)).fetchone()
    assert c.post('/api/clients/dark-test/action',json={'action':'delete'}).status_code==202
    with store.lock:assert store.db.execute('SELECT 1 FROM client_cycles WHERE email=?',('dark-test',)).fetchone() is None

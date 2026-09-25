import json
from fastapi.testclient import TestClient

from server import make_app
from test_standalone import env

def product_payload():
    return {
        'id':'turbo','name':'DARK TURBO','description':'Fast plan',
        'kind':'volume','active':True,'visible':True,
    }

def price_payload():
    return {
        'id':'turbo-30','label':'30 GB / 30 Days','price_minor':3000000,'currency':'IRR',
        'duration_days':30,'volume_bytes':30*1024**3,'unlimited_units':0,
        'device_limit':2,'inbound_ids':[],'active':True,
    }

def test_bot_settings_encrypt_token_and_never_return_it(env):
    store,_,_,_,c=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    r=c.put('/api/telegram/settings',json={
        'enabled':True,'bot_token':token,'admin_telegram_id':123456789})
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['configured'] is True and doc['enabled'] is True
    assert 'bot_token' not in doc and 'token' not in json.dumps(doc)
    with store.lock:
        saved=store.db.execute("SELECT token_enc FROM telegram_bots WHERE owner='dark'").fetchone()[0]
    assert saved and token not in saved

def test_product_price_and_order_amount_are_server_authoritative(env):
    store,_,_,_,c=env
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    r=c.put('/api/commerce/products/turbo/prices',json=price_payload())
    assert r.status_code==200,r.text
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30',
        'buyer_telegram_id':555001,'buyer_username':'buyer'})
    assert order.status_code==201,order.text
    assert order.json()['amount_minor']==3000000
    with store.lock:
        row=store.db.execute("SELECT amount_minor,currency FROM commerce_orders WHERE id=?",(order.json()['id'],)).fetchone()
    assert tuple(row)==(3000000,'IRR')

def test_manual_gateway_purchase_flow(env):
    _,_,_,_,c=env
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload()).status_code==200
    gateway=c.put('/api/commerce/gateways',json={
        'id':'card','label':'Manual Card','kind':'manual','enabled':True,
        'instructions':'Send receipt to support','plugin':'','secret':None})
    assert gateway.status_code==200,gateway.text
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30',
        'buyer_telegram_id':555002,'buyer_username':''}).json()
    pay=c.post(f"/api/commerce/orders/{order['id']}/pay",json={'gateway_id':'card'})
    assert pay.status_code==200,pay.text
    assert pay.json()['mode']=='manual' and pay.json()['status']=='awaiting_payment'
    confirm=c.post(f"/api/commerce/orders/{order['id']}/confirm-payment",json={'reference':'receipt-001'})
    assert confirm.status_code==200,confirm.text
    assert confirm.json()=={'id':order['id'],'status':'paid','provisioned':False}

def test_representative_commerce_scope_is_isolated(env):
    store,engine,manager,auth,c=env
    assert c.put('/api/owners/seller',json={
        'name':'Seller','allowed':[],'volume_credit_bytes':10_000_000,
        'unlimited_credit':2,'max_clients':10}).status_code==200
    assert c.post('/api/admins',json={
        'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    token,p=auth.login('seller','SellerPass88','','127.0.0.2',3600,'commerce-test')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token)
        seller.headers['X-Dark-CSRF']=p.csrf
        assert seller.put('/api/commerce/products',json={
            'id':'seller-plan','name':'Seller Plan','description':'','kind':'volume',
            'active':True,'visible':True}).status_code==200
        own=seller.get('/api/commerce/products')
        assert own.status_code==200 and [x['id'] for x in own.json()]==['seller-plan']
        denied=seller.get('/api/commerce/products',params={'owner_id':'dark'})
        assert denied.status_code==403
    owner_products=c.get('/api/commerce/products').json()
    assert all(x['id']!='seller-plan' for x in owner_products)

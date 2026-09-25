import json
from fastapi.testclient import TestClient

from server import make_app
from telegram_runtime import BotWorker
from test_standalone import env
from test_representatives_v2 import create_inbound

def product_payload(product_id='turbo'):
    return {
        'id':product_id,'name':'DARK TURBO','description':'Fast plan',
        'kind':'volume','active':True,'visible':True,
    }

def price_payload(inbound_id,price_id='turbo-30',**overrides):
    body={
        'id':price_id,'label':'30 GB / 30 Days','price_minor':3000000,'currency':'IRT',
        'duration_days':30,'volume_bytes':30*1024**3,'unlimited_units':0,
        'device_limit':2,'inbound_ids':[inbound_id],'active':True,
    }
    body.update(overrides)
    return body

def manual_gateway():
    return {
        'id':'card','label':'Manual Card','kind':'manual','enabled':True,
        'instructions':'Send receipt to support','plugin':'','secret':None,
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
    status=c.get('/api/telegram/status')
    assert status.status_code==200 and status.json()['runtime_state']=='stopped'
    with store.lock:
        saved=store.db.execute("SELECT token_enc FROM telegram_bots WHERE owner='dark'").fetchone()[0]
    assert saved and token not in saved

def test_product_price_and_order_amount_are_server_authoritative(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    r=c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound_id))
    assert r.status_code==200,r.text
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30',
        'buyer_telegram_id':555001,'buyer_username':'buyer'})
    assert order.status_code==201,order.text
    assert order.json()['amount_minor']==3000000
    with store.lock:
        row=store.db.execute("SELECT amount_minor,currency FROM commerce_orders WHERE id=?",(order.json()['id'],)).fetchone()
    assert tuple(row)==(3000000,'IRT')

def test_manual_gateway_confirm_provisions_real_client(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound_id)).status_code==200
    assert c.put('/api/commerce/gateways',json=manual_gateway()).status_code==200
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30',
        'buyer_telegram_id':555002,'buyer_username':''}).json()
    pay=c.post(f"/api/commerce/orders/{order['id']}/pay",json={'gateway_id':'card'})
    assert pay.status_code==200,pay.text
    assert pay.json()['mode']=='manual' and pay.json()['status']=='awaiting_payment'
    confirm=c.post(f"/api/commerce/orders/{order['id']}/confirm-payment",json={'reference':'receipt-001'})
    assert confirm.status_code==200,confirm.text
    result=confirm.json()
    assert result['status']=='provisioned' and result['provisioned'] is True
    assert result['client_id'].startswith('tg555002_')
    detail=c.get('/api/clients/'+result['client_id'])
    assert detail.status_code==200,detail.text
    assert detail.json()['client']['tgId']==555002
    assert detail.json()['client']['totalGB']==30*1024**3
    assert detail.json()['inboundIds']==[inbound_id]
    with store.lock:
        row=store.db.execute("SELECT status,client_id,fulfillment_error FROM commerce_orders WHERE id=?",(order['id'],)).fetchone()
    assert tuple(row)==('provisioned',result['client_id'],'')

def test_representative_commerce_scope_is_isolated(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/owners/seller',json={
        'name':'Seller','allowed':[inbound_id],'volume_credit_bytes':100*1024**3,
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
        assert seller.put('/api/commerce/products/seller-plan/prices',json=price_payload(
            inbound_id,price_id='seller-30')).status_code==200
        own=seller.get('/api/commerce/products')
        assert own.status_code==200 and [x['id'] for x in own.json()]==['seller-plan']
        denied=seller.get('/api/commerce/products',params={'owner_id':'dark'})
        assert denied.status_code==403
    owner_products=c.get('/api/commerce/products').json()
    assert all(x['id']!='seller-plan' for x in owner_products)

def test_main_bot_menu_has_representative_factory_but_reseller_bot_does_not(env):
    _,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':777001}).status_code==200
    runtime=c.app.state.telegram_runtime
    owner_worker=BotWorker(runtime,'dark',token,'mark-owner')
    try:
        owner_text=' '.join(x['text'] for row in owner_worker.main_keyboard(True)['keyboard'] for x in row)
        assert '➕ ساخت نماینده' in owner_text and '🤝 نمایندگان' in owner_text
    finally:
        owner_worker.api.close()
    assert c.put('/api/owners/seller',json={
        'name':'Seller','allowed':[inbound_id],'volume_credit_bytes':50*1024**3,
        'unlimited_credit':1,'max_clients':10}).status_code==200
    assert c.post('/api/admins',json={'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    with manager.store.transaction() as db:
        db.execute("INSERT INTO telegram_bots(owner,enabled,token_enc,admin_telegram_id,updated_at) VALUES(?,?,?,?,?)",
                   ('seller',0,auth.cipher.encrypt(token.encode()).decode(),777002,1.0))
    seller_worker=BotWorker(runtime,'seller',token,'mark-seller')
    try:
        seller_text=' '.join(x['text'] for row in seller_worker.main_keyboard(True)['keyboard'] for x in row)
        assert '➕ ساخت نماینده' not in seller_text and '🤝 نمایندگان' not in seller_text
        assert '👥 مدیریت کاربران' in seller_text
    finally:
        seller_worker.api.close()

def test_owner_bot_can_create_representative_account(env):
    store,_,_,auth,c=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':888001}).status_code==200
    worker=BotWorker(c.app.state.telegram_runtime,'dark',token,'mark-owner')
    sent=[]
    worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((chat_id,text,reply_markup))
    try:
        worker.create_representative_from_text(888001,888001,'botrep | StrongPass88 | 500 | 4')
    finally:
        worker.api.close()
    with store.lock:
        admin=store.db.execute("SELECT role,disabled FROM api_admins WHERE id='botrep'").fetchone()
        owner=store.db.execute("SELECT volume_credit_bytes,unlimited_credit FROM owners WHERE id='botrep'").fetchone()
        profile=store.db.execute("SELECT prefix,allowed FROM owner_profiles WHERE id='botrep'").fetchone()
    assert tuple(admin)==('reseller',0)
    assert tuple(owner)==(500*1024**3,4)
    assert profile['prefix']=='botrep_' and json.loads(profile['allowed'])==[]
    assert any('نماینده ساخته شد' in text for _,text,_ in sent)
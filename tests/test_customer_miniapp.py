import json

from test_standalone import env
from test_representatives_v2 import create_inbound
from test_telegram_commerce import manual_gateway
from test_telegram_ops import BOT_TOKEN,mini_init


def h(uid):
    return {'X-Telegram-Init-Data':mini_init(BOT_TOKEN,uid)}


def bot(c):
    r=c.put('/api/telegram/settings',json={'enabled':False,'bot_token':BOT_TOKEN,'admin_telegram_id':700001})
    assert r.status_code==200,r.text


def plan(c,inbound,price=200000):
    r=c.post('/api/commerce/simple-plans',json={
        'name':'CUSTOMER V5','plan_type':'volume','price_minor':price,'duration_months':1,
        'volume_gb':30,'ip_limit':2,'inbound_ids':[inbound],'activation_mode':'immediate','published':True})
    assert r.status_code==201,r.text
    return r.json()


def credit(center,uid,amount,ref):
    with center.store.transaction() as db:
        center._credit_tx(db,'dark',uid,amount,'topup',ref,'customer-v5-test')


def test_customer_signed_bootstrap_wallet_purchase_and_service_scope(env):
    _,_,_,_,c=env
    inbound=create_inbound(c);bot(c);p=plan(c,inbound,220000)
    center=c.app.state.telegram_runtime.customer
    credit(center,880010,500000,'seed-buy')

    boot=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=h(880010))
    assert boot.status_code==200,boot.text
    assert boot.json()['identity']['telegram_id']==880010
    assert len(boot.json()['products'])==1

    order=c.post('/api/telegram-customer/orders',params={'owner':'dark'},headers=h(880010),
                 json={'product_id':p['id'],'price_id':p['price_id']})
    assert order.status_code==201,order.text
    oid=order.json()['order']['id']
    paid=c.post(f'/api/telegram-customer/orders/{oid}/pay',params={'owner':'dark'},headers=h(880010),
                json={'method':'wallet','gateway_id':'crypto'})
    assert paid.status_code==200,paid.text
    cid=paid.json()['client_id']

    after=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=h(880010)).json()
    assert after['wallet']['balance_minor']==280000
    assert any(x['id']==cid for x in after['services'])

    svc=c.get('/api/telegram-customer/services/'+cid,params={'owner':'dark'},headers=h(880010))
    assert svc.status_code==200 and svc.json()['delivery']['subscription_url'].startswith('http')
    denied=c.get('/api/telegram-customer/services/'+cid,params={'owner':'dark'},headers=h(880011))
    assert denied.status_code!=200


def test_customer_crypto_checkout_is_order_owner_scoped(env):
    _,_,_,_,c=env
    inbound=create_inbound(c);bot(c);p=plan(c,inbound,330000)
    setup=c.put('/api/telegram/operations/crypto',json={
        'id':'crypto','label':'Crypto Pay','enabled':True,
        'checkout_url_template':'https://pay.example/checkout?amount={amount}&order={order_id}&payment={payment_id}',
        'webhook_secret':'customer-v5-secret-123456','instructions':'Pay online'})
    assert setup.status_code==200,setup.text
    order=c.post('/api/telegram-customer/orders',params={'owner':'dark'},headers=h(880020),
                 json={'product_id':p['id'],'price_id':p['price_id']}).json()['order']
    pay=c.post(f"/api/telegram-customer/orders/{order['id']}/pay",params={'owner':'dark'},headers=h(880020),
               json={'method':'crypto','gateway_id':'crypto'})
    assert pay.status_code==200 and pay.json()['checkout_url'].startswith('https://pay.example/')
    stolen=c.post(f"/api/telegram-customer/orders/{order['id']}/pay",params={'owner':'dark'},headers=h(880021),
                  json={'method':'crypto','gateway_id':'crypto'})
    assert stolen.status_code!=200


def test_customer_renewal_topup_and_support_chat(env):
    _,_,_,_,c=env
    inbound=create_inbound(c);bot(c);p=plan(c,inbound,100000)
    assert c.put('/api/commerce/gateways',json=manual_gateway()).status_code==200
    center=c.app.state.telegram_runtime.customer
    credit(center,880030,500000,'seed-renew')

    order=c.post('/api/telegram-customer/orders',params={'owner':'dark'},headers=h(880030),
                 json={'product_id':p['id'],'price_id':p['price_id']}).json()['order']
    paid=c.post(f"/api/telegram-customer/orders/{order['id']}/pay",params={'owner':'dark'},headers=h(880030),
                json={'method':'wallet','gateway_id':'crypto'}).json()
    cid=paid['client_id']
    before=c.get('/api/telegram-customer/services/'+cid,params={'owner':'dark'},headers=h(880030)).json()['expiry_ms']
    ren=c.post('/api/telegram-customer/renewals',params={'owner':'dark'},headers=h(880030),
               json={'client_id':cid,'price_id':p['price_id']})
    assert ren.status_code==201,ren.text
    rpay=c.post(f"/api/telegram-customer/renewals/{ren.json()['id']}/pay",params={'owner':'dark'},headers=h(880030),json={})
    assert rpay.status_code==200 and rpay.json()['status']=='renewed'
    after=c.get('/api/telegram-customer/services/'+cid,params={'owner':'dark'},headers=h(880030)).json()['expiry_ms']
    assert after>before

    top=c.post('/api/telegram-customer/topups',params={'owner':'dark'},headers=h(880030),json={'amount_minor':250000})
    assert top.status_code==201,top.text
    assert top.json()['payment']['receipt_channel']=='telegram_bot'
    assert 'secret' not in json.dumps(top.json()).lower()

    t=c.post('/api/telegram-customer/tickets',params={'owner':'dark'},headers=h(880030),
             json={'subject':'Need help','message':'Please check service'})
    assert t.status_code==201,t.text
    row=t.json()['ticket']['row_id']
    assert c.get(f'/api/telegram-customer/tickets/{row}',params={'owner':'dark'},headers=h(880030)).status_code==200
    assert c.get(f'/api/telegram-customer/tickets/{row}',params={'owner':'dark'},headers=h(880031)).status_code!=200


def test_customer_representative_purchase_returns_one_time_credentials(env):
    _,_,_,_,c=env
    inbound=create_inbound(c);bot(c)
    rp=c.put('/api/representative-marketplace/plans/customer-v5',json={
        'name':'REP V5','description':'Fixed','price_minor':120000,'currency':'IRT','duration_days':30,
        'volume_credit_bytes':50*1024**3,'unlimited_credit':2,'max_clients':20,'prefix':'rep_',
        'max_client_ips':2,'max_client_hwid':1,'allowed_inbounds':[inbound],
        'bot_allowed':True,'renewal_enabled':True,'active':True,'visible':True})
    assert rp.status_code==200,rp.text
    center=c.app.state.telegram_runtime.customer
    credit(center,880060,300000,'seed-rep')
    boot=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=h(880060)).json()
    row=boot['representative']['plans'][0]['row_id']
    o=c.post('/api/telegram-customer/representative/orders',params={'owner':'dark'},headers=h(880060),
             json={'plan_row':row,'kind':'purchase'})
    paid=c.post(f"/api/telegram-customer/representative/orders/{o.json()['id']}/pay",
                params={'owner':'dark'},headers=h(880060),json={})
    assert paid.status_code==200 and paid.json()['password']
    oid=o.json()['id']
    ack=c.post(f'/api/telegram-customer/representative/orders/{oid}/ack',params={'owner':'dark'},headers=h(880060),json={})
    assert ack.status_code==200
    assert 'password' not in c.app.state.telegram_runtime.marketplace.result(oid,'dark')


def test_representative_customer_miniapp_is_owner_scoped(env):
    from fastapi.testclient import TestClient
    from server import make_app
    _,engine,manager,auth,c=env
    inbound=create_inbound(c)
    assert c.put('/api/owners/mini-rep',json={
        'name':'Mini Rep','allowed':[inbound],'volume_credit_bytes':100*1024**3,
        'unlimited_credit':2,'max_clients':20}).status_code==200
    assert c.post('/api/admins',json={
        'username':'mini-rep','password':'MiniRepPass88','role':'reseller'}).status_code==200
    token,p=auth.login('mini-rep','MiniRepPass88','','127.0.0.44',3600,'customer-mini-rep')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        assert seller.put('/api/telegram/settings',json={
            'enabled':False,'bot_token':BOT_TOKEN,'admin_telegram_id':770001}).status_code==200
        created=seller.post('/api/commerce/simple-plans',json={
            'name':'REP CUSTOMER PLAN','plan_type':'volume','price_minor':90000,'duration_months':1,
            'volume_gb':20,'ip_limit':1,'inbound_ids':[inbound],'published':True})
        assert created.status_code==201,created.text
        boot=seller.get('/api/telegram-customer/bootstrap',params={'owner':'mini-rep'},headers=h(881000))
        assert boot.status_code==200,boot.text
        doc=boot.json()
        assert doc['identity']['telegram_id']==881000
        assert [x['id'] for x in doc['products']]==[created.json()['id']]
        assert doc['representative']['available'] is False
        assert doc['wallet']['balance_minor']==0

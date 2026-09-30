import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from telegram_runtime import BotWorker
from test_standalone import env
from test_representatives_v2 import create_inbound
from test_telegram_commerce import product_payload,price_payload,manual_gateway


BOT='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'


def signed(user_id:int,token:str=BOT,auth_date:int|None=None):
    values={
        'auth_date':str(int(auth_date if auth_date is not None else time.time())),
        'query_id':'customer-v5',
        'user':json.dumps({'id':int(user_id),'first_name':'Customer','username':'user'+str(user_id)},separators=(',',':')),
    }
    check='\n'.join(f'{k}={values[k]}' for k in sorted(values))
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    values['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(values)


def hdr(uid:int):
    return {'X-Telegram-Init-Data':signed(uid)}


def configure(c,admin=900001):
    r=c.put('/api/telegram/settings',json={'enabled':False,'bot_token':BOT,'admin_telegram_id':admin})
    assert r.status_code==200,r.text


def seed_product(c,inbound_id,*,pid='v5',price_id='v5-30',price=200000,volume=30*1024**3):
    assert c.put('/api/commerce/products',json=product_payload(pid,name='V5 PLAN',add_volume_enabled=True)).status_code==200
    body=price_payload(inbound_id,price_id=price_id,price_minor=price,volume_bytes=volume,
                       activation_mode='immediate',delivery_mode='subscription')
    assert c.put(f'/api/commerce/products/{pid}/prices',json=body).status_code==200
    return pid,price_id


def credit(center,owner,uid,amount,ref):
    with center.store.transaction() as db:
        center._credit_tx(db,owner,uid,amount,'topup',ref,'customer-v5-seed')


def test_customer_miniapp_accepts_signed_customer_and_is_telegram_frameable(env):
    _,_,_,_,c=env
    create_inbound(c);configure(c)
    page=c.get('/assets/telegram-customer.html')
    assert page.status_code==200
    assert 'telegram-web-app.js' in page.text and 'vendor-qr.js' in page.text
    csp=page.headers.get('content-security-policy','')
    assert 'https://telegram.org' in csp and 'frame-ancestors https://telegram.org https://*.telegram.org' in csp
    assert 'x-frame-options' not in page.headers

    r=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=hdr(700101))
    assert r.status_code==200,r.text
    assert r.json()['user']['telegram_id']==700101
    bad=dict(hdr(700101));bad['X-Telegram-Init-Data']=bad['X-Telegram-Init-Data'][:-1]+'0'
    assert c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=bad).status_code==403


def test_customer_bot_menu_exposes_signed_webapp_button(env):
    _,_,_,_,c=env
    configure(c,900011)
    worker=BotWorker(c.app.state.telegram_runtime,'dark',BOT,'customer-v5-menu')
    try:
        kb=worker.main_keyboard(False)['keyboard']
        button=next(x for row in kb for x in row if x['text']=='📱 پنل من')
        assert button['web_app']['url'].endswith('/assets/telegram-customer.html?owner=dark')
    finally:worker.api.close()


def test_customer_purchase_wallet_service_and_cross_user_isolation(env):
    store,_,_,_,c=env
    inbound=create_inbound(c);configure(c)
    pid,price=seed_product(c,inbound)
    center=c.app.state.telegram_runtime.customer
    credit(center,'dark',710001,500000,'seed-wallet')

    order=c.post('/api/telegram-customer/orders',params={'owner':'dark'},headers=hdr(710001),
                 json={'product_id':pid,'price_id':price})
    assert order.status_code==201,order.text
    paid=c.post(f"/api/telegram-customer/orders/{order.json()['id']}/wallet",
                params={'owner':'dark'},headers=hdr(710001),json={})
    assert paid.status_code==200,paid.text
    assert paid.json()['provisioned'] is True

    boot=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=hdr(710001)).json()
    assert len(boot['services'])==1
    svc=boot['services'][0]
    detail=c.get(f"/api/telegram-customer/services/{svc['row_id']}",params={'owner':'dark'},headers=hdr(710001))
    assert detail.status_code==200 and detail.json()['delivery']['subscription_url']

    denied=c.get(f"/api/telegram-customer/services/{svc['row_id']}",params={'owner':'dark'},headers=hdr(710002))
    assert denied.status_code==400
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM clients WHERE owner='dark'").fetchone()[0]==1


def test_customer_renewal_and_volume_addon_keep_same_client_and_preserve_expiry(env):
    store,_,manager,_,c=env
    inbound=create_inbound(c);configure(c)
    pid,price=seed_product(c,inbound,price=100000,volume=20*1024**3)
    center=c.app.state.telegram_runtime.customer
    credit(center,'dark',720001,1000000,'seed-renew-addon')

    order=c.post('/api/telegram-customer/orders',params={'owner':'dark'},headers=hdr(720001),
                 json={'product_id':pid,'price_id':price}).json()
    paid=c.post(f"/api/telegram-customer/orders/{order['id']}/wallet",params={'owner':'dark'},headers=hdr(720001),json={}).json()
    client=paid['client_id']
    boot=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=hdr(720001)).json()
    row=boot['services'][0]['row_id']
    actor=c.app.state.telegram_commerce.actor_for('dark')
    before=manager.detail(actor,client,credentials=True)
    expiry_before=int(before['client']['expiryTime']);quota_before=int(before['client']['totalGB'])
    with store.transaction() as db:db.execute("UPDATE clients SET used_bytes=? WHERE id=?",(5*1024**3,client))

    addon=c.post(f'/api/telegram-customer/services/{row}/volume',params={'owner':'dark'},headers=hdr(720001),
                 json={'price_id':price})
    assert addon.status_code==201,addon.text
    added=c.post(f"/api/telegram-customer/volume-addons/{addon.json()['id']}/wallet",
                 params={'owner':'dark'},headers=hdr(720001),json={})
    assert added.status_code==200,added.text
    after_add=manager.detail(actor,client,credentials=True)
    assert int(after_add['client']['totalGB'])==quota_before+20*1024**3
    assert int(after_add['client']['expiryTime'])==expiry_before
    assert int(after_add['used_bytes'])==5*1024**3

    renewal=c.post(f'/api/telegram-customer/services/{row}/renew',params={'owner':'dark'},headers=hdr(720001),
                   json={'price_id':price})
    assert renewal.status_code==201,renewal.text
    renewed=c.post(f"/api/telegram-customer/renewals/{renewal.json()['id']}/wallet",
                   params={'owner':'dark'},headers=hdr(720001),json={})
    assert renewed.status_code==200,renewed.text
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM clients WHERE owner='dark'").fetchone()[0]==1


def test_customer_direct_manual_gateway_and_wallet_topup(env):
    _,_,_,_,c=env
    inbound=create_inbound(c);configure(c)
    pid,price=seed_product(c,inbound)
    assert c.put('/api/commerce/gateways',json=manual_gateway()).status_code==200

    order=c.post('/api/telegram-customer/orders',params={'owner':'dark'},headers=hdr(730001),
                 json={'product_id':pid,'price_id':price}).json()
    pay=c.post(f"/api/telegram-customer/orders/{order['id']}/gateway",params={'owner':'dark'},headers=hdr(730001),
               json={'gateway_id':'card'})
    assert pay.status_code==200,pay.text
    assert pay.json()['mode']=='manual' and pay.json()['receipt_in_bot'] is True
    assert pay.json()['card_number']=='6037991234567890'

    top=c.post('/api/telegram-customer/wallet/topups',params={'owner':'dark'},headers=hdr(730001),
               json={'amount_minor':150000})
    assert top.status_code==201,top.text
    assert top.json()['payment']['receipt_in_bot'] is True


def test_customer_support_chat_is_scoped(env):
    _,_,_,_,c=env
    configure(c)
    new=c.post('/api/telegram-customer/support',params={'owner':'dark'},headers=hdr(740001),
               json={'subject':'Need help','message':'Hello support'})
    assert new.status_code==201,new.text
    row=new.json()['ticket']['row_id']
    detail=c.get(f'/api/telegram-customer/support/{row}',params={'owner':'dark'},headers=hdr(740001))
    assert detail.status_code==200 and detail.json()['messages'][-1]['text']=='Hello support'
    assert c.get(f'/api/telegram-customer/support/{row}',params={'owner':'dark'},headers=hdr(740002)).status_code==400
    rep=c.post(f'/api/telegram-customer/support/{row}/reply',params={'owner':'dark'},headers=hdr(740001),
               json={'text':'More details'})
    assert rep.status_code==200
    assert c.post(f'/api/telegram-customer/support/{row}/close',params={'owner':'dark'},headers=hdr(740001),json={}).json()['status']=='closed'


def test_referral_qualifies_after_admin_confirmed_manual_purchase(env):
    _,_,_,_,c=env
    inbound=create_inbound(c);configure(c)
    pid,price=seed_product(c,inbound,price=120000)
    center=c.app.state.telegram_runtime.customer
    center.set_referral_reward('dark',25000)
    ref=center.ensure_referral_profile('dark',750001)
    assert center.register_referral('dark',750002,ref['code']) is True
    assert c.put('/api/commerce/gateways',json=manual_gateway()).status_code==200

    order=c.post('/api/commerce/orders',json={'product_id':pid,'price_id':price,
        'buyer_telegram_id':750002,'buyer_username':'buyer'}).json()
    assert c.post(f"/api/commerce/orders/{order['id']}/pay",json={'gateway_id':'card'}).status_code==200
    confirmed=c.post(f"/api/commerce/orders/{order['id']}/confirm-payment",json={'reference':'manual-v5'})
    assert confirmed.status_code==200,confirmed.text
    stats=center.referral_stats('dark',750001)
    assert stats['qualified']==1 and stats['earned']==25000


def test_customer_representative_marketplace_wallet_and_one_time_credentials(env):
    store,_,_,_,c=env
    inbound=create_inbound(c);configure(c)
    assert c.put('/api/representative-marketplace/plans/v5rep',json={
        'name':'REP V5','description':'Customer V5 rep plan','price_minor':100000,'currency':'IRT',
        'duration_days':30,'volume_credit_bytes':50*1024**3,'unlimited_credit':2,'max_clients':10,
        'prefix':'v5_','max_client_ips':2,'max_client_hwid':1,'allowed_inbounds':[inbound],
        'bot_allowed':True,'renewal_enabled':True,'active':True,'visible':True}).status_code==200
    center=c.app.state.telegram_runtime.customer
    credit(center,'dark',760001,200000,'seed-rep-v5')
    boot=c.get('/api/telegram-customer/bootstrap',params={'owner':'dark'},headers=hdr(760001)).json()
    plan=boot['representative']['plans'][0]
    order=c.post('/api/telegram-customer/representative/orders',params={'owner':'dark'},headers=hdr(760001),
                 json={'plan_row':plan['row_id'],'kind':'purchase'})
    assert order.status_code==201,order.text
    paid=c.post(f"/api/telegram-customer/representative/orders/{order.json()['id']}/wallet",
                params={'owner':'dark'},headers=hdr(760001),json={})
    assert paid.status_code==200,paid.text
    doc=paid.json();assert doc['username'] and doc['password'] and doc['credentials_pending'] is True
    saved=c.post(f"/api/telegram-customer/representative/orders/{order.json()['id']}/credentials-saved",
                 params={'owner':'dark'},headers=hdr(760001),json={})
    assert saved.status_code==200
    with store.lock:
        row=store.db.execute("SELECT password_enc FROM representative_market_orders WHERE id=?",(order.json()['id'],)).fetchone()
    assert row['password_enc']==''

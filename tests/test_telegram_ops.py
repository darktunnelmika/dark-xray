import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from server import make_app
from test_standalone import env
from test_representatives_v2 import create_inbound,inbound_payload


BOT_TOKEN='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'


def mini_init(token:str,user_id:int,auth_date:int|None=None)->str:
    values={
        'auth_date':str(int(auth_date if auth_date is not None else time.time())),
        'query_id':'AAE-test-query',
        'user':json.dumps({'id':int(user_id),'first_name':'Admin','username':'darkadmin'},separators=(',',':')),
    }
    check='\n'.join(f'{k}={values[k]}' for k in sorted(values))
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    values['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(values)


def configure_bot(c,admin_id=700001,token=BOT_TOKEN):
    r=c.put('/api/telegram/settings',json={'enabled':False,'bot_token':token,'admin_telegram_id':admin_id})
    assert r.status_code==200,r.text


def simple_plan(c,inbound_id,name='OPS PLAN',price=250000):
    r=c.post('/api/commerce/simple-plans',json={
        'name':name,'plan_type':'volume','price_minor':price,'duration_days':30,
        'volume_gb':30,'ip_limit':1,'inbound_ids':[inbound_id],'published':True,
    })
    assert r.status_code==201,r.text
    return r.json()


def test_mini_app_validates_signed_init_data_and_csp(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c);configure_bot(c,700001)
    page=c.get('/assets/telegram-miniapp.html')
    assert page.status_code==200
    assert 'https://telegram.org/js/telegram-web-app.js' in page.text
    csp=page.headers.get('content-security-policy','')
    assert 'https://telegram.org' in csp and 'frame-ancestors https://telegram.org https://*.telegram.org' in csp
    assert 'x-frame-options' not in page.headers

    init=mini_init(BOT_TOKEN,700001)
    r=c.get('/api/telegram-miniapp/bootstrap',params={'owner':'dark'},
            headers={'X-Telegram-Init-Data':init})
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['auth']['user']['id']==700001
    assert [x['id'] for x in doc['inbounds']]==[inbound_id]
    assert doc['dashboard']['owner']=='dark'

    wrong=c.get('/api/telegram-miniapp/bootstrap',params={'owner':'dark'},
                headers={'X-Telegram-Init-Data':mini_init(BOT_TOKEN,700002)})
    assert wrong.status_code==403
    stale=c.get('/api/telegram-miniapp/bootstrap',params={'owner':'dark'},
                headers={'X-Telegram-Init-Data':mini_init(BOT_TOKEN,700001,int(time.time())-3600)})
    assert stale.status_code==403


def test_support_center_triage_reply_quick_reply_and_close(env):
    store,_,_,_,c=env
    configure_bot(c,700011)
    center=c.app.state.telegram_runtime.customer
    ticket=center.create_ticket('dark',880001,'buyer','Connection problem')
    center.add_ticket_message('dark',ticket['id'],'customer',880001,text='Please check my service')

    rows=c.get('/api/telegram/operations/support').json()['tickets']
    row=next(x for x in rows if x['id']==ticket['id'])
    assert row['priority']=='normal' and row['message_count']==1

    meta=c.put(f"/api/telegram/operations/support/{row['row_id']}/meta",
               json={'priority':'urgent','assigned_to':'support-a'})
    assert meta.status_code==200,meta.text
    assert meta.json()['priority']=='urgent' and meta.json()['assigned_to']=='support-a'

    qr=c.put('/api/telegram/operations/support/quick-replies/checking',
             json={'id':'checking','title':'Checking','body':'در حال بررسی سرویس شما هستیم.','active':True})
    assert qr.status_code==200,qr.text
    assert qr.json()['active'] is True

    reply=c.post(f"/api/telegram/operations/support/{row['row_id']}/reply",
                 json={'text':'در حال بررسی سرویس شما هستیم.'})
    assert reply.status_code==200,reply.text
    assert reply.json()['ticket']['status']=='answered'
    detail=c.get(f"/api/telegram/operations/support/{row['row_id']}").json()
    assert detail['messages'][-1]['sender_type']=='admin'

    closed=c.post(f"/api/telegram/operations/support/{row['row_id']}/close",json={})
    assert closed.status_code==200 and closed.json()['status']=='closed'
    with store.lock:
        saved=store.db.execute("SELECT priority,assigned_to,status FROM customer_support_tickets WHERE id=?",(ticket['id'],)).fetchone()
    assert tuple(saved)==('urgent','support-a','closed')


def test_hosted_crypto_checkout_and_signed_webhook_are_idempotent(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c);configure_bot(c,700021)
    plan=simple_plan(c,inbound_id,price=450000)
    secret='ops-webhook-secret-123456789'
    setup=c.put('/api/telegram/operations/crypto',json={
        'id':'crypto','label':'Crypto Hosted','enabled':True,
        'checkout_url_template':'https://pay.example/checkout?amount={amount}&currency={currency}&order={order_id}&payment={payment_id}',
        'webhook_secret':secret,'instructions':'Choose any supported crypto at checkout',
    })
    assert setup.status_code==200,setup.text
    assert setup.json()['configured'] is True and setup.json()['enabled'] is True
    assert secret not in json.dumps(setup.json())

    order=c.post('/api/commerce/orders',json={
        'product_id':plan['id'],'price_id':plan['price_id'],
        'buyer_telegram_id':880021,'buyer_username':'cryptobuyer'})
    assert order.status_code==201,order.text
    oid=order.json()['id']
    pay=c.post(f'/api/commerce/orders/{oid}/pay',json={'gateway_id':'crypto'})
    assert pay.status_code==200,pay.text
    payment=pay.json()
    assert payment['mode']=='plugin' and payment['checkout_url'].startswith('https://pay.example/checkout?')
    assert 'amount=450000' in payment['checkout_url'] and oid in payment['checkout_url']

    bad_doc={'event_id':'evt_bad','order_id':oid,'status':'paid','amount_minor':450000,'currency':'IRT','reference':'chain-bad'}
    bad_raw=json.dumps(bad_doc,separators=(',',':')).encode()
    bad=c.post('/api/telegram/crypto/webhook/dark/crypto',content=bad_raw,
               headers={'content-type':'application/json','X-Dark-Signature':'0'*64})
    assert bad.status_code==400

    doc={'event_id':'evt_paid_1','order_id':oid,'status':'paid','amount_minor':450000,'currency':'IRT','reference':'chain-abc'}
    raw=json.dumps(doc,separators=(',',':')).encode()
    sig=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    good=c.post('/api/telegram/crypto/webhook/dark/crypto',content=raw,
                headers={'content-type':'application/json','X-Dark-Signature':sig})
    assert good.status_code==200,good.text
    assert good.json()['duplicate'] is False

    duplicate=c.post('/api/telegram/crypto/webhook/dark/crypto',content=raw,
                     headers={'content-type':'application/json','X-Dark-Signature':sig})
    assert duplicate.status_code==200 and duplicate.json()['duplicate'] is True

    with store.lock:
        row=store.db.execute("SELECT status,client_id FROM commerce_orders WHERE id=?",(oid,)).fetchone()
        events=store.db.execute("SELECT COUNT(*) FROM commerce_gateway_events WHERE owner='dark' AND event_id='evt_paid_1'").fetchone()[0]
    assert row['status'] in ('provisioned','provisioned_waiting_activation') and row['client_id']
    assert events==1
    dash=c.get('/api/telegram/operations/dashboard').json()
    assert dash['revenue_today']>=450000 and dash['paid_today']>=1


def test_representative_mini_app_only_lists_allowed_inbounds(env):
    _,engine,manager,auth,c=env
    inbound_a=create_inbound(c)
    second=inbound_payload(25102);second['tag']='rep-v2-b';second['remark']='REP V2 B'
    r=c.post('/api/inbounds',json=second);assert r.status_code==200,r.text
    inbound_b=r.json()['id']
    assert c.put('/api/owners/mini-seller',json={
        'name':'Mini Seller','allowed':[inbound_a],'volume_credit_bytes':100*1024**3,
        'unlimited_credit':2,'max_clients':10}).status_code==200
    assert c.post('/api/admins',json={
        'username':'mini-seller','password':'MiniSeller88','role':'reseller'}).status_code==200
    token,p=auth.login('mini-seller','MiniSeller88','','127.0.0.31',3600,'mini-app-scope')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        assert seller.put('/api/telegram/settings',json={
            'enabled':False,'bot_token':BOT_TOKEN,'admin_telegram_id':700031}).status_code==200
        init=mini_init(BOT_TOKEN,700031)
        r=seller.get('/api/telegram-miniapp/bootstrap',params={'owner':'mini-seller'},
                     headers={'X-Telegram-Init-Data':init})
        assert r.status_code==200,r.text
        assert [x['id'] for x in r.json()['inbounds']]==[inbound_a]
        assert inbound_b not in [x['id'] for x in r.json()['inbounds']]
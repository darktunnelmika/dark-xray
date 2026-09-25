import json
from fastapi.testclient import TestClient

from server import make_app
from telegram_runtime import BotWorker
from telegram_forum import FORUM_REQUEST_ID,TOPICS
from test_standalone import env
from test_representatives_v2 import create_inbound

def product_payload(product_id='turbo',**overrides):
    body={
        'id':product_id,'name':'DARK TURBO','description':'Fast plan','category':'VIP',
        'kind':'volume','sale_limit_per_user':0,'renewal_enabled':True,'add_volume_enabled':True,
        'active':True,'visible':True,
    }
    body.update(overrides)
    return body

def price_payload(inbound_id,price_id='turbo-30',**overrides):
    body={
        'id':price_id,'label':'30 GB / 30 Days','price_minor':3000000,'currency':'IRT',
        'duration_days':30,'volume_bytes':30*1024**3,'unlimited_units':0,
        'ip_limit':2,'hwid_limit':0,'device_limit':None,'inbound_ids':[inbound_id],
        'activation_mode':'immediate','delivery_mode':'subscription','primary_inbound_id':inbound_id,
        'show_qr':True,'show_portal':True,'active':True,
    }
    body.update(overrides)
    return body

def manual_gateway():
    return {
        'id':'card','label':'Manual Card','kind':'manual','enabled':True,
        'card_number':'6037991234567890','card_holder':'DARK VPN','bank_name':'Melli',
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
    assert pay.json()['card_number']=='6037991234567890'
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
    assert detail.json()['client']['limitIp']==2
    assert result['delivery']['subscription_url'].startswith('http')
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
        assert '👥 کاربران' in seller_text
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

class FakeTelegramAPI:
    def __init__(self):
        self.calls=[];self.topic=100
    def call(self,method,payload=None):
        payload=payload or {};self.calls.append((method,payload))
        if method=='getChat':return {'id':payload['chat_id'],'is_forum':True,'title':'DARK Reports'}
        if method=='getChatMember':return {'status':'administrator','can_manage_topics':True}
        if method=='createForumTopic':
            self.topic+=1;return {'message_thread_id':self.topic,'name':payload['name']}
        if method=='sendMessage':return {'message_id':999}
        raise AssertionError(method)


def test_forum_setup_requests_forum_and_creates_report_topics(env):
    store,_,_,_,c=env
    forum=c.app.state.telegram_runtime.forum
    kb=forum.request_keyboard()
    req=kb['keyboard'][0][0]['request_chat']
    assert req['request_id']==FORUM_REQUEST_ID and req['chat_is_forum'] is True
    assert req['bot_administrator_rights']['can_manage_topics'] is True
    assert 'bot_is_member' not in req
    api=FakeTelegramAPI()
    result=forum.setup(api,'dark',{'request_id':FORUM_REQUEST_ID,'chat_id':-100123},42)
    assert result['configured'] is True and len(result['topics'])==len(TOPICS)
    assert {x['kind'] for x in result['topics']}==set(TOPICS)
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM telegram_forum_topics WHERE owner='dark'").fetchone()[0]==len(TOPICS)


def test_product_v2_sale_limit_and_snapshot_policy(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload(sale_limit_per_user=1)).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,activation_mode='first_connection',delivery_mode='both',ip_limit=3,hwid_limit=2)).status_code==200
    first=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30','buyer_telegram_id':90001,'buyer_username':''})
    assert first.status_code==201,first.text
    second=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30','buyer_telegram_id':90001,'buyer_username':''})
    assert second.status_code==400 and 'limit' in second.text.lower()
    with store.lock:
        row=store.db.execute("SELECT activation_mode,delivery_mode,ip_limit,hwid_limit,primary_inbound_id FROM commerce_orders WHERE id=?",
                             (first.json()['id'],)).fetchone()
    assert tuple(row)==('first_connection','both',3,2,inbound_id)


def test_first_connection_activation_is_real_and_starts_expiry_after_activity(env):
    store,_,manager,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,activation_mode='first_connection',delivery_mode='config')).status_code==200
    assert c.put('/api/commerce/gateways',json=manual_gateway()).status_code==200
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30','buyer_telegram_id':90002,'buyer_username':''}).json()
    assert c.post(f"/api/commerce/orders/{order['id']}/pay",json={'gateway_id':'card'}).status_code==200
    result=c.post(f"/api/commerce/orders/{order['id']}/confirm-payment",json={'reference':'receipt-first'}).json()
    assert result['status']=='provisioned_waiting_activation' and result['activation_pending'] is True
    assert result['delivery']['main_config']
    detail=c.get('/api/clients/'+result['client_id']).json()
    assert detail['client']['expiryTime']==0
    store.record_usage('first-connect-activity-0001',result['client_id'],10,20)
    activated=c.app.state.telegram_commerce.activate_first_connections(manager)
    assert activated and activated[0]['client_id']==result['client_id']
    detail=c.get('/api/clients/'+result['client_id']).json()
    assert detail['client']['expiryTime']>int(__import__('time').time()*1000)
    with store.lock:
        row=store.db.execute("SELECT status,activation_started_at,activation_expires_at FROM commerce_orders WHERE id=?",
                             (order['id'],)).fetchone()
    assert row['status']=='provisioned' and row['activation_started_at']>0 and row['activation_expires_at']>row['activation_started_at']


def test_unlimited_price_consumes_exactly_one_unlimited_credit(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload(kind='unlimited')).status_code==200
    bad_zero=c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,volume_bytes=0,unlimited_units=0))
    assert bad_zero.status_code==400 and 'exactly one' in bad_zero.text.lower()
    bad_two=c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,volume_bytes=0,unlimited_units=2))
    assert bad_two.status_code==400 and 'exactly one' in bad_two.text.lower()
    good=c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,volume_bytes=0,unlimited_units=1))
    assert good.status_code==200,good.text


def test_forum_audit_router_scopes_and_routes_events(env):
    store,_,manager,_,c=env
    forum=c.app.state.telegram_runtime.forum
    api=FakeTelegramAPI()
    forum.setup(api,'dark',{'request_id':FORUM_REQUEST_ID,'chat_id':-100999},42)
    actor=c.app.state.auth.current(c.cookies.get('dark_session'),None).actor
    manager.audit(actor,'dark','commerce.payment_confirm','order-1','paid')
    manager.audit(actor,'dark','backup.full','dark','backup made')
    manager.audit(actor,'dark','client.create','customer-1','created')
    sent=forum.poll_audits(api,'dark','owner')
    assert sent==3
    routed=[payload for method,payload in api.calls if method=='sendMessage' and payload.get('message_thread_id')]
    with store.lock:
        topics={r['kind']:r['thread_id'] for r in store.db.execute(
            "SELECT kind,thread_id FROM telegram_forum_topics WHERE owner='dark'")}
    assert any(x['message_thread_id']==topics['payments'] and 'commerce.payment_confirm' in x['text'] for x in routed)
    assert any(x['message_thread_id']==topics['backups'] and 'backup.full' in x['text'] for x in routed)
    assert any(x['message_thread_id']==topics['services'] and 'client.create' in x['text'] for x in routed)


def test_daily_forum_summary_uses_completed_day_and_topic(env):
    import time as _time
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound_id)).status_code==200
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30','buyer_telegram_id':90100,'buyer_username':''}).json()
    forum=c.app.state.telegram_runtime.forum
    api=FakeTelegramAPI()
    forum.setup(api,'dark',{'request_id':FORUM_REQUEST_ID,'chat_id':-100777},42)
    with store.transaction() as db:
        db.execute("UPDATE commerce_orders SET created_at=?,updated_at=? WHERE id=?",
                   (_time.time()-86400,_time.time()-86400,order['id']))
        db.execute("UPDATE telegram_forums SET last_daily_key='2000-01-01' WHERE owner='dark'")
    assert forum.maybe_daily_summary(api,'dark','owner','UTC') is True
    with store.lock:
        daily=store.db.execute(
            "SELECT thread_id FROM telegram_forum_topics WHERE owner='dark' AND kind='daily'").fetchone()['thread_id']
    messages=[p for m,p in api.calls if m=='sendMessage' and p.get('message_thread_id')==daily]
    assert messages and 'سفارش‌ها: 1' in messages[-1]['text']
    assert '3000000 IRT' in messages[-1]['text'].replace(',','')

def test_manual_payment_wizard_is_managed_inside_admin_bot(env):
    store,_,_,_,c=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    admin_id=990001
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':admin_id}).status_code==200
    worker=BotWorker(c.app.state.telegram_runtime,'dark',token,'manual-payment-test')
    sent=[]
    worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((chat_id,text,reply_markup))
    try:
        menu=' '.join(x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row)
        assert '💳 پرداخت دستی' in menu and '💳 درگاه‌ها' not in menu
        worker.admin_gateways(admin_id)
        assert sent[-1][2]['inline_keyboard'][0][0]['callback_data']=='paycfg'
        worker.start_payment_setup(admin_id,admin_id)
        assert worker.sessions[admin_id]=='pay_card'
        worker.handle_payment_setup_text(admin_id,admin_id,'6037 9912 3456 7890')
        worker.handle_payment_setup_text(admin_id,admin_id,'DARK VPN')
        worker.handle_payment_setup_text(admin_id,admin_id,'Melli')
        worker.handle_payment_setup_text(admin_id,admin_id,'بعد از پرداخت رسید را ارسال کنید')
        row=worker.manual_gateway()
        assert row is not None and row['enabled'] is True
        assert row['card_number']=='6037991234567890'
        assert row['card_holder']=='DARK VPN' and row['bank_name']=='Melli'
        assert 'رسید' in row['instructions']
        assert admin_id not in worker.sessions and admin_id not in worker.session_data
        worker.toggle_manual_payment(admin_id,admin_id)
        assert worker.manual_gateway()['enabled'] is False
        worker.toggle_manual_payment(admin_id,admin_id)
        assert worker.manual_gateway()['enabled'] is True
    finally:
        worker.api.close()
    with store.lock:
        audit=store.db.execute("""SELECT action FROM live_audit
          WHERE owner='dark' AND action='commerce.manual_payment_bot_save'
          ORDER BY id DESC LIMIT 1""").fetchone()
    assert audit is not None


def test_telegram_status_exposes_forum_connection_state(env):
    _,_,_,_,c=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':990002}).status_code==200
    before=c.get('/api/telegram/status').json()
    assert before['forum']['configured'] is False
    forum=c.app.state.telegram_runtime.forum
    api=FakeTelegramAPI()
    forum.setup(api,'dark',{'request_id':FORUM_REQUEST_ID,'chat_id':-100456},42)
    after=c.get('/api/telegram/status').json()
    assert after['forum']['configured'] is True
    assert len(after['forum']['topics'])==len(TOPICS)

def test_recovered_forum_requires_and_completes_rebind(env):
    store,_,_,_,c=env
    forum=c.app.state.telegram_runtime.forum
    api=FakeTelegramAPI()
    forum.setup(api,'dark',{'request_id':FORUM_REQUEST_ID,'chat_id':-100777},42)
    with store.transaction() as db:
        db.execute("UPDATE telegram_forums SET rebind_required=1,rebind_reason='restored-backup' WHERE owner='dark'")
    before=forum.status('dark')
    assert before['preserved'] is True and before['configured'] is False
    assert before['rebind_required'] is True and len(before['topics'])==len(TOPICS)
    rebound=forum.rebind_existing(api,'dark',42)
    assert rebound['configured'] is True and rebound['rebind_required'] is False
    assert rebound['chat_id']==-100777 and len(rebound['topics'])==len(TOPICS)


def test_admin_v2_keyboard_exposes_daily_management_centers(env):
    _,_,_,_,c=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    admin_id=991100
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':admin_id}).status_code==200
    worker=BotWorker(c.app.state.telegram_runtime,'dark',token,'admin-v2-test')
    try:
        text=' '.join(x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row)
        for label in ('🏠 داشبورد','👥 کاربران','📦 سرویس‌ها','🧾 سفارش‌ها','💳 پرداخت دستی',
                      '📊 گزارش‌ها','💾 بکاپ','⚙️ تنظیمات ربات','🤝 نمایندگان'):
            assert label in text
    finally:
        worker.api.close()


def test_admin_v2_centers_render_without_external_side_effects(env):
    _,_,_,_,c=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    admin_id=991101
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':admin_id}).status_code==200
    worker=BotWorker(c.app.state.telegram_runtime,'dark',token,'admin-v2-centers')
    sent=[]
    worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((chat_id,text,reply_markup))
    try:
        worker.admin_dashboard(admin_id)
        worker.admin_services(admin_id)
        worker.admin_orders(admin_id)
        worker.admin_reports(admin_id)
        worker.admin_backup(admin_id)
        worker.admin_settings(admin_id)
    finally:
        worker.api.close()
    text='\n'.join(x[1] for x in sent)
    assert 'DARK BOT ADMIN V2' in text
    assert 'سرویس‌ها' in text
    assert 'مرکز گزارش DARK' in text
    assert 'DARK Full Backup' in text
    assert 'تنظیمات DARK BOT' in text
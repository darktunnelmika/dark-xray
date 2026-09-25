import json
from fastapi.testclient import TestClient

from server import make_app
from telegram_runtime import BotWorker
from telegram_forum import FORUM_REQUEST_ID,TOPICS
from test_standalone import env,create
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
    assert 'DARK BOT ADMIN V3' in text
    assert 'سرویس‌ها' in text
    assert 'مرکز گزارش DARK' in text
    assert 'DARK Full Backup' in text
    assert 'تنظیمات DARK BOT' in text

def _bot_worker(c,admin_id=992001):
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    assert c.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':admin_id}).status_code==200
    worker=BotWorker(c.app.state.telegram_runtime,'dark',token,'v3-test')
    sent=[]
    worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((chat_id,text,reply_markup))
    return worker,sent


def test_store_manager_v3_builds_product_and_price_with_inbound_picker(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    worker,sent=_bot_worker(c,992101)
    try:
        worker.start_product_create(992101,992101)
        worker.handle_store_text(992101,992101,'DARK GAMER')
        worker.handle_store_text(992101,992101,'Gaming')
        worker.store_choose_type(992101,992101,'volume')
        worker.handle_store_text(992101,992101,'Low latency gaming plan')
        worker.handle_store_text(992101,992101,'2')
        with store.lock:
            product=dict(store.db.execute("SELECT rowid AS row_id,* FROM commerce_products WHERE owner='dark'").fetchone())
        assert product['name']=='DARK GAMER' and product['category']=='Gaming' and product['sale_limit_per_user']==2
        worker.start_price_create(992101,992101,int(product['row_id']))
        worker.handle_store_text(992101,992101,'50GB / 30 Days')
        worker.handle_store_text(992101,992101,'250000')
        worker.handle_store_text(992101,992101,'30')
        worker.handle_store_text(992101,992101,'50')
        worker.handle_store_text(992101,992101,'2')
        worker.handle_store_text(992101,992101,'1')
        worker.price_choose_activation(992101,992101,'first_connection')
        worker.price_choose_delivery(992101,992101,'subscription')
        worker.price_toggle_inbound(992101,992101,inbound_id)
        worker.price_inbounds_done(992101,992101)
        with store.lock:
            price=dict(store.db.execute("SELECT * FROM commerce_prices WHERE owner='dark' AND product_id=?",(product['id'],)).fetchone())
        assert price['label']=='50GB / 30 Days'
        assert price['price_minor']==250000 and price['currency']=='IRT'
        assert price['volume_bytes']==50*1024**3 and price['duration_days']==30
        assert price['ip_limit']==2 and price['hwid_limit']==1
        assert price['activation_mode']=='first_connection' and price['delivery_mode']=='subscription'
        assert json.loads(price['inbound_ids'])==[inbound_id]
        assert price['primary_inbound_id']==inbound_id
    finally:
        worker.api.close()


def test_store_manager_v3_clone_is_safe_and_price_toggle_works(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound_id)).status_code==200
    worker,_=_bot_worker(c,992102)
    try:
        with store.lock:
            prow=int(store.db.execute("SELECT rowid FROM commerce_products WHERE owner='dark' AND id='turbo'").fetchone()[0])
            price_row=int(store.db.execute("SELECT rowid FROM commerce_prices WHERE owner='dark' AND id='turbo-30'").fetchone()[0])
        worker.clone_product(992102,prow)
        with store.lock:
            products=[dict(r) for r in store.db.execute("SELECT * FROM commerce_products WHERE owner='dark' ORDER BY created_at")]
            clones=[r for r in products if r['id']!='turbo']
        assert len(clones)==1 and clones[0]['active']==0 and clones[0]['visible']==0
        worker.toggle_price(992102,price_row)
        with store.lock:
            assert store.db.execute("SELECT active FROM commerce_prices WHERE rowid=?",(price_row,)).fetchone()[0]==0
    finally:
        worker.api.close()


def test_service_manager_v3_renew_volume_limits_and_inbounds_use_manager_core(env):
    store,_,manager,_,c=env
    first=create_inbound(c)
    expiry=int((__import__('time').time()+86400)*1000)
    create(c,email='svc-v3',extra={'totalGB':20*1024**3,'expiryTime':expiry,'limitIp':1,'limitHwid':0})
    from test_representatives_v2 import inbound_payload
    second_body=inbound_payload();second_body['port']+=1;second_body['remark']='Second';second_body['tag']='second'
    r=c.post('/api/inbounds',json=second_body);assert r.status_code==200,r.text
    second=r.json()['id']
    worker,_=_bot_worker(c,992103)
    try:
        with store.lock:
            row_id=int(store.db.execute("SELECT rowid FROM clients WHERE id='svc-v3'").fetchone()[0])
        before=manager.detail(worker.actor(),'svc-v3',credentials=True)['client']
        worker.renew_client(992103,row_id,30)
        after=manager.detail(worker.actor(),'svc-v3',credentials=True)['client']
        assert int(after['expiryTime'])>=int(before['expiryTime'])+30*86400*1000-1000

        worker.start_service_input(992103,992103,'volume',row_id,'volume')
        worker.handle_service_text(992103,992103,'10')
        after=manager.detail(worker.actor(),'svc-v3',credentials=True)['client']
        assert after['totalGB']==30*1024**3

        worker.start_service_input(992103,992103,'ip',row_id,'ip')
        worker.handle_service_text(992103,992103,'3')
        worker.start_service_input(992103,992103,'hwid',row_id,'hwid')
        worker.handle_service_text(992103,992103,'2')
        after=manager.detail(worker.actor(),'svc-v3',credentials=True)['client']
        assert after['limitIp']==3 and after['limitHwid']==2

        worker.start_client_inbounds(992103,992103,row_id)
        worker.toggle_client_inbound(992103,992103,second)
        worker.finish_client_inbounds(992103,992103)
        after=manager.detail(worker.actor(),'svc-v3',credentials=True)
        assert set(after['inboundIds'])=={first,second}
    finally:
        worker.api.close()


def test_service_manager_v3_refuses_volume_add_to_unlimited(env):
    store,_,manager,_,c=env
    create_inbound(c);create(c,email='unlimited-v3',extra={'totalGB':0})
    worker,sent=_bot_worker(c,992104)
    try:
        with store.lock:
            row_id=int(store.db.execute("SELECT rowid FROM clients WHERE id='unlimited-v3'").fetchone()[0])
        worker.start_service_input(992104,992104,'volume',row_id,'volume')
        worker.handle_service_text(992104,992104,'10')
        assert manager.detail(worker.actor(),'unlimited-v3',credentials=True)['client']['totalGB']==0
        assert any('نامحدود' in x[1] for x in sent)
    finally:
        worker.api.close()


def test_admin_v3_keyboard_exposes_store_manager(env):
    _,_,_,_,c=env
    worker,_=_bot_worker(c,992105)
    try:
        text=' '.join(x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row)
        assert '🛠 مدیریت فروشگاه' in text
        worker.admin_dashboard(992105)
    finally:
        worker.api.close()


def test_store_manager_v3_archives_product_with_order_history(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound_id)).status_code==200
    order=c.post('/api/commerce/orders',json={
        'product_id':'turbo','price_id':'turbo-30','buyer_telegram_id':999991,'buyer_username':'archive'}).json()
    worker,sent=_bot_worker(c,992106)
    try:
        with store.lock:
            row_id=int(store.db.execute("SELECT rowid FROM commerce_products WHERE owner='dark' AND id='turbo'").fetchone()[0])
        worker.preview_product(992106,row_id)
        assert any('PREVIEW' in x[1] and '30 GB' in x[1] for x in sent)
        worker.delete_or_archive_product(992106,row_id)
        with store.lock:
            p=store.db.execute("SELECT active,visible FROM commerce_products WHERE owner='dark' AND id='turbo'").fetchone()
            pr=store.db.execute("SELECT active FROM commerce_prices WHERE owner='dark' AND id='turbo-30'").fetchone()
        assert tuple(p)==(0,0) and pr[0]==0
        assert c.get('/api/commerce/orders').json()[0]['id']==order['id']
    finally:
        worker.api.close()

def _credit_wallet(center,owner,telegram_id,amount,reference):
    with center.store.transaction() as db:
        center._credit_tx(db,owner,telegram_id,amount,'topup',reference,'test credit')


def test_customer_v2_menu_is_exactly_six_simple_sections(env):
    _,_,_,_,c=env
    worker,_=_bot_worker(c,993001)
    try:
        labels=[x['text'] for row in worker.main_keyboard(False)['keyboard'] for x in row]
        assert labels==[
            '🛍 خرید اشتراک','🔄 تمدید سرویس',
            '💰 کیف پول + شارژ','📦 سرویس‌های من',
            '👥 زیرمجموعه‌گیری','🎫 پشتیبانی',
        ]
    finally:
        worker.api.close()


def test_wallet_topup_approval_is_idempotent(env):
    _,_,_,_,c=env
    center=c.app.state.telegram_runtime.customer
    top=center.create_topup('dark',700001,'wallet-user',250000)
    center.record_topup_receipt('dark',700001,'telegram:photo:test')
    first=center.approve_topup('dark',top['row_id'])
    second=center.approve_topup('dark',top['row_id'])
    assert first['status']=='approved' and second['status']=='approved'
    assert center.wallet('dark',700001)['balance_minor']==250000
    rows=center.ledger('dark',700001,20)
    assert len([x for x in rows if x['kind']=='topup'])==1


def test_wallet_purchase_provisions_once_and_debits_once(env):
    store,_,manager,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    body=price_payload(inbound_id,price_id='wallet-plan',price_minor=120000,volume_bytes=15*1024**3)
    assert c.put('/api/commerce/products/turbo/prices',json=body).status_code==200
    center=c.app.state.telegram_runtime.customer
    _credit_wallet(center,'dark',700002,500000,'seed-purchase')
    order=c.app.state.telegram_commerce.create_order('dark',700002,'buyer','turbo','wallet-plan')
    first=center.pay_purchase('dark',order['id'])
    second=center.pay_purchase('dark',order['id'])
    assert first['provisioned'] is True and second['provisioned'] is True
    assert first['client_id']==second['client_id']
    assert center.wallet('dark',700002)['balance_minor']==380000
    purchases=[x for x in center.ledger('dark',700002,20) if x['kind']=='purchase']
    assert len(purchases)==1 and purchases[0]['delta_minor']==-120000
    detail=manager.detail(center.commerce.actor_for('dark'),first['client_id'],credentials=True)
    assert detail['client']['tgId']==700002
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM clients WHERE id=?",(first['client_id'],)).fetchone()[0]==1


def test_referral_rewards_only_after_first_successful_purchase(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,price_id='ref-plan',price_minor=100000,volume_bytes=5*1024**3)).status_code==200
    center=c.app.state.telegram_runtime.customer
    center.set_referral_reward('dark',40000)
    ref=center.ensure_referral_profile('dark',710001)
    assert center.register_referral('dark',710002,ref['code']) is True
    _credit_wallet(center,'dark',710002,300000,'seed-ref')
    order=center.commerce.create_order('dark',710002,'child','turbo','ref-plan')
    center.pay_purchase('dark',order['id'])
    assert center.wallet('dark',710001)['balance_minor']==40000
    stats=center.referral_stats('dark',710001)
    assert stats['invited']==1 and stats['qualified']==1 and stats['earned']==40000
    center.pay_purchase('dark',order['id'])
    assert center.wallet('dark',710001)['balance_minor']==40000


def test_wallet_renewal_updates_real_service_and_is_idempotent(env):
    _,_,manager,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    price=price_payload(inbound_id,price_id='renew-wallet',price_minor=90000,
                        duration_days=20,volume_bytes=8*1024**3,ip_limit=3,hwid_limit=1)
    assert c.put('/api/commerce/products/turbo/prices',json=price).status_code==200
    center=c.app.state.telegram_runtime.customer
    _credit_wallet(center,'dark',720001,400000,'seed-renew')
    order=center.commerce.create_order('dark',720001,'renewuser','turbo','renew-wallet')
    bought=center.pay_purchase('dark',order['id'])
    client_id=bought['client_id']
    before=manager.detail(center.commerce.actor_for('dark'),client_id,credentials=True)['client']
    renewal=center.create_renewal_order('dark',720001,'renewuser',client_id,'renew-wallet')
    result=center.pay_renewal('dark',renewal['id'])
    again=center.pay_renewal('dark',renewal['id'])
    assert result['status']=='renewed' and again['status']=='renewed'
    after=manager.detail(center.commerce.actor_for('dark'),client_id,credentials=True)['client']
    assert int(after['expiryTime'])>=int(before['expiryTime'])+20*86400*1000-1000
    assert after['totalGB']==8*1024**3 and after['limitIp']==3 and after['limitHwid']==1
    assert center.wallet('dark',720001)['balance_minor']==220000
    renewals=[x for x in center.ledger('dark',720001,20) if x['kind']=='renewal']
    assert len(renewals)==1 and renewals[0]['delta_minor']==-90000


def test_customer_support_ticket_roundtrip(env):
    _,_,_,_,c=env
    worker,sent=_bot_worker(c,993010)
    worker.api.call=lambda method,payload=None: {}
    customer=730001
    try:
        assert worker.handle_customer_callback('supnew',customer,customer,{'id':customer,'username':'supporter'})
        worker.handle_customer_text(customer,customer,'مشکل اتصال','supporter')
        worker.handle_customer_text(customer,customer,'کانفیگ من متصل نمی‌شود','supporter')
        tickets=worker.runtime.customer.tickets_for_customer('dark',customer)
        assert len(tickets)==1 and tickets[0]['status']=='open'
        msgs=worker.runtime.customer.ticket_messages('dark',tickets[0]['id'])
        assert len(msgs)==1 and msgs[0]['sender_type']=='customer'
        row_id=tickets[0]['row_id']
        assert worker.handle_customer_callback('asupreply:'+str(row_id),993010,993010,{'id':993010})
        worker.handle_customer_text(993010,993010,'بررسی شد؛ دوباره تست کنید','admin')
        msgs=worker.runtime.customer.ticket_messages('dark',tickets[0]['id'])
        assert [m['sender_type'] for m in msgs]==['customer','admin']
        assert any(chat==customer and 'پاسخ پشتیبانی' in text for chat,text,_ in sent)
        assert worker.handle_customer_callback('asupclose:'+str(row_id),993010,993010,{'id':993010})
        assert worker.runtime.customer.ticket(tickets[0]['id'],'dark')['status']=='closed'
    finally:
        worker.api.close()


def test_start_payload_registers_referral(env):
    _,_,_,_,c=env
    worker,sent=_bot_worker(c,993020)
    ref=worker.runtime.customer.ensure_referral_profile('dark',740001)
    worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((chat_id,text,reply_markup))
    try:
        worker.handle({'message':{'chat':{'id':740002,'type':'private'},
                                  'from':{'id':740002,'username':'child'},
                                  'text':'/start ref_'+ref['code']}})
        profile=worker.runtime.customer.ensure_referral_profile('dark',740002)
        assert profile['referrer_telegram_id']==740001
    finally:
        worker.api.close()


def test_customer_v2_wallet_referral_and_support_survive_full_backup(env,tmp_path):
    import dataclasses
    import sqlite3
    from pathlib import Path
    from backup import create_backup,restore_backup
    store,engine,_,_,c=env
    center=c.app.state.telegram_runtime.customer
    _credit_wallet(center,'dark',750001,345000,'backup-wallet')
    ref=center.ensure_referral_profile('dark',750001)
    center.ensure_referral_profile('dark',750002)
    assert center.register_referral('dark',750002,ref['code'])
    ticket=center.create_ticket('dark',750001,'backupuser','Backup support')
    center.add_ticket_message('dark',ticket['id'],'customer',750001,text='keep this message')
    data=Path(store.path).parent
    config=data/'config.json'
    config.write_text(json.dumps(dataclasses.asdict(engine.config)),encoding='utf-8')
    archive=tmp_path/'customer-v2.darkbackup'
    create_backup(data,config,archive,'Customer-Backup-Passphrase-123!')
    dest=tmp_path/'customer-v2-restored'
    result=restore_backup(archive,dest,'Customer-Backup-Passphrase-123!')
    assert result['restored'] is True
    with sqlite3.connect(dest/'data/dark.sqlite3') as db:
        assert db.execute("SELECT balance_minor FROM customer_wallets WHERE owner='dark' AND telegram_id=750001").fetchone()[0]==345000
        row=db.execute("SELECT referrer_telegram_id FROM customer_referrals WHERE owner='dark' AND telegram_id=750002").fetchone()
        assert row[0]==750001
        assert db.execute("SELECT subject FROM customer_support_tickets WHERE id=?",(ticket['id'],)).fetchone()[0]=='Backup support'
        assert db.execute("SELECT text FROM customer_support_messages WHERE ticket_id=?",(ticket['id'],)).fetchone()[0]=='keep this message'


def test_customer_wallet_purchase_refuses_insufficient_balance_without_debit(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,price_id='expensive-wallet',price_minor=500000)).status_code==200
    center=c.app.state.telegram_runtime.customer
    _credit_wallet(center,'dark',760001,100000,'seed-low')
    order=center.commerce.create_order('dark',760001,'low','turbo','expensive-wallet')
    import pytest
    from dark_policy import PolicyError
    with pytest.raises(PolicyError,match='Insufficient wallet balance'):
        center.pay_purchase('dark',order['id'])
    assert center.wallet('dark',760001)['balance_minor']==100000
    assert center.commerce.order(order['id'],'dark')['status']=='pending'
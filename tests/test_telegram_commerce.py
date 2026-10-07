import json
import time
from fastapi.testclient import TestClient

from server import make_app
from telegram_runtime import BotWorker
from telegram_forum import FORUM_REQUEST_ID,TOPICS
from test_standalone import env,create
from test_representatives_v2 import create_inbound,inbound_payload

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
        assert '🤝 نمایندگان' in owner_text and '🛒 فروش' in owner_text and '⚙️ مدیریت' in owner_text
        assert '💾 بکاپ' not in owner_text
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
        assert '➕ ساخت نماینده' not in seller_text and '🤝 نمایندگان' not in seller_text and '💾 بکاپ' not in seller_text
        assert '👥 کاربران' in seller_text and '🛒 فروش' in seller_text and '⚙️ مدیریت' in seller_text
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
    def portal_data():
        page=c.get(detail['subscription_url']+'?portal=1')
        assert page.status_code==200,page.text
        return json.loads(page.text.split('window.__DARK_SUB__=',1)[1].split(';</script>',1)[0])
    pending=portal_data()
    assert pending['expiry']==0 and pending['activation_pending'] is True
    assert pending['duration_days']==30
    store.record_usage('first-connect-activity-0001',result['client_id'],10,20)
    activated=c.app.state.telegram_commerce.activate_first_connections(manager)
    assert activated and activated[0]['client_id']==result['client_id']
    detail=c.get('/api/clients/'+result['client_id']).json()
    assert detail['client']['expiryTime']>int(__import__('time').time()*1000)
    active=portal_data()
    assert active['activation_pending'] is False
    assert active['expiry']==detail['client']['expiryTime']//1000
    with store.lock:
        row=store.db.execute("SELECT status,activation_started_at,activation_expires_at FROM commerce_orders WHERE id=?",
                             (order['id'],)).fetchone()
    assert row['status']=='provisioned' and row['activation_started_at']>0 and row['activation_expires_at']>row['activation_started_at']


def test_unlimited_price_units_are_server_calculated(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload(kind='unlimited')).status_code==200
    for supplied in (0,1,2,999):
        result=c.put('/api/commerce/products/turbo/prices',json=price_payload(
            inbound_id,volume_bytes=0,unlimited_units=supplied,ip_limit=5,duration_days=60))
        assert result.status_code==200,result.text
        assert result.json()['prices'][0]['unlimited_units']==10
        assert result.json()['prices'][0]['credit_units']==10


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
    assert any(x['message_thread_id']==topics['payments'] and 'پرداخت سفارش تأیید شد' in x['text'] for x in routed)
    assert any(x['message_thread_id']==topics['backups'] and 'بکاپ کامل ساخته شد' in x['text'] for x in routed)
    assert any(x['message_thread_id']==topics['services'] and 'سرویس جدید ساخته شد' in x['text'] for x in routed)


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
    assert 'فروش قطعی: 0 تومان' in messages[-1]['text']
    assert 'مبلغ سفارش‌های پرداخت‌نشدهٔ این روز: 3,000,000 تومان' in messages[-1]['text']

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
        assert '🛒 فروش' in menu and '💳 پرداخت دستی' not in menu and '💳 درگاه‌ها' not in menu
        worker.admin_sales_menu(admin_id)
        sales_callbacks=[b.get('callback_data') for row in sent[-1][2]['inline_keyboard'] for b in row]
        assert 'ops_payments' in sales_callbacks
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
        labels=[x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row]
        for label in ('🏠 داشبورد','👥 کاربران','🛒 فروش','🎫 پشتیبانی','🤝 نمایندگان','📊 گزارش‌ها','📣 اعلان‌ها','⚙️ مدیریت'):
            assert label in labels
        for hidden in ('📦 سرویس‌ها','🧾 سفارش‌ها','💳 پرداخت دستی','📈 رشد و فروش','📱 Mini App','💾 بکاپ','⚙️ تنظیمات ربات'):
            assert hidden not in labels
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
    assert 'DARK CONTROL / BOT V6' in text
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
    worker.api.send_photo_bytes=lambda chat_id,data,caption='',reply_markup=None: sent.append((chat_id,caption,reply_markup))
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
    worker,sent=_bot_worker(c,992105)
    try:
        text=' '.join(x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row)
        assert '🛒 فروش' in text and '🛠 مدیریت فروشگاه' not in text
        worker.admin_sales_menu(992105)
        callbacks=[b.get('callback_data') for row in sent[-1][2]['inline_keyboard'] for b in row]
        assert 'sthome' in callbacks
        worker.admin_dashboard(992105)
    finally:
        worker.api.close()


def test_bot_v6_action_center_and_service_alert_dedupe(env):
    store,_,_,_,c=env
    create_inbound(c)
    now_ms=int(time.time()*1000)
    create(c,email='notify-v6',extra={
        'tgId':994001,'totalGB':10*1024**3,
        'expiryTime':now_ms+48*3600*1000,
    })
    worker,sent=_bot_worker(c,992107)
    runtime=c.app.state.telegram_runtime
    previous=runtime.workers.get('dark')
    runtime.workers['dark']=worker
    try:
        worker.admin_dashboard(992107)
        assert any('DARK CONTROL / BOT V6' in text and 'ACTION CENTER' in text for _,text,_ in sent)
        runtime.notify_service_health()
        alerts=[x for x in sent if x[0]==994001 and 'DARK SERVICE ALERT' in x[1]]
        assert len(alerts)==1 and '۳ روز' in alerts[0][1]
        runtime.notify_service_health()
        alerts=[x for x in sent if x[0]==994001 and 'DARK SERVICE ALERT' in x[1]]
        assert len(alerts)==1
        with store.lock:
            count=store.db.execute("""SELECT COUNT(*) FROM telegram_customer_notifications
              WHERE owner='dark' AND telegram_id=994001 AND client_id='notify-v6'""").fetchone()[0]
        assert count==1
    finally:
        if previous is None:runtime.workers.pop('dark',None)
        else:runtime.workers['dark']=previous
        worker.api.close()


def test_bot_v6_broadcast_queue_is_owner_scoped_and_idempotent(env):
    store,_,_,_,c=env
    create_inbound(c)
    create(c,email='broadcast-v6',extra={'tgId':994101,'totalGB':5*1024**3})
    worker,sent=_bot_worker(c,992108)
    runtime=c.app.state.telegram_runtime
    previous=runtime.workers.get('dark')
    runtime.workers['dark']=worker
    try:
        worker.start_broadcast(992108,992108,'all')
        assert worker.sessions[992108]=='broadcast_text'
        worker.handle_broadcast_text(992108,992108,'Maintenance notice V6')
        assert worker.sessions[992108]=='broadcast_review'
        worker.queue_broadcast(992108,992108)
        with store.lock:
            job=store.db.execute("SELECT id,status,total FROM telegram_broadcasts WHERE owner='dark' ORDER BY created_at DESC LIMIT 1").fetchone()
        assert job and job['status']=='queued' and job['total']==1
        runtime.process_broadcasts()
        notices=[x for x in sent if x[0]==994101 and 'DARK NOTICE' in x[1]]
        assert len(notices)==1 and 'Maintenance notice V6' in notices[0][1]
        runtime.process_broadcasts()
        notices=[x for x in sent if x[0]==994101 and 'DARK NOTICE' in x[1]]
        assert len(notices)==1
        with store.lock:
            done=store.db.execute("SELECT status,sent,failed FROM telegram_broadcasts WHERE id=?",(job['id'],)).fetchone()
        assert tuple(done)==('completed',1,0)
    finally:
        if previous is None:runtime.workers.pop('dark',None)
        else:runtime.workers['dark']=previous
        worker.api.close()


def test_bot_v6_owner_representative_control_center(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/owners/repcontrol',json={
        'name':'Rep Control','allowed':[inbound_id],'volume_credit_bytes':120*1024**3,
        'unlimited_credit':3,'max_clients':25}).status_code==200
    assert c.post('/api/admins',json={
        'username':'repcontrol','password':'RepControlPass88','role':'reseller'}).status_code==200
    worker,sent=_bot_worker(c,992109)
    try:
        worker.representatives(992109)
        assert any(any(btn.get('callback_data')=='repctl:repcontrol' for row in (markup or {}).get('inline_keyboard',[]) for btn in row)
                   for _,_,markup in sent)
        worker.representative_detail(992109,'repcontrol')
        assert any('REP CONTROL' in text and 'Rep Control' in text and '120.0 GiB' in text for _,text,_ in sent)
    finally:
        worker.api.close()


def test_bot_v6_customer_crm_filters_360_and_direct_message(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    now_ms=int(time.time()*1000)
    create(c,email='crm-v6',extra={
        'tgId':995001,'totalGB':10*1024**3,
        'expiryTime':now_ms+48*3600*1000,
    })
    center=c.app.state.telegram_runtime.customer
    with center.store.transaction() as db:
        center._credit_tx(db,'dark',995001,200000,'topup','crm-wallet','crm test')
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound_id)).status_code==200
    c.app.state.telegram_commerce.create_order('dark',995001,'crmuser','turbo','turbo-30')
    ticket=center.create_ticket('dark',995001,'crmuser','CRM support')
    center.add_ticket_message('dark',ticket['id'],'customer',995001,text='Need help')
    worker,sent=_bot_worker(c,992110)
    try:
        worker.admin_clients(992110,'expiring')
        assert any('DARK CUSTOMER CRM / EXPIRING' in text for _,text,_ in sent)
        with store.lock:
            row_id=int(store.db.execute("SELECT rowid FROM clients WHERE owner='dark' AND id='crm-v6'").fetchone()[0])
        worker.client_detail(992110,row_id)
        assert any('CUSTOMER 360' in text and 'crm-v6' in text and 'Orders: 1' in text and 'تیکت باز: 1' in text for _,text,_ in sent)
        worker.customer_orders_admin(992110,995001)
        assert any('CUSTOMER ORDERS' in text for _,text,_ in sent)
        worker.customer_tickets_admin(992110,995001)
        assert any('CUSTOMER SUPPORT' in text for _,text,_ in sent)
        worker.start_direct_message(992110,992110,row_id)
        worker.handle_crm_text(992110,992110,'Hello from DARK CRM')
        worker.send_direct_message(992110,992110)
        direct=[x for x in sent if x[0]==995001 and 'DARK MESSAGE' in x[1]]
        assert len(direct)==1 and 'Hello from DARK CRM' in direct[0][1]
        with store.lock:
            audit=store.db.execute("""SELECT 1 FROM live_audit WHERE action='telegram.crm_message'
              AND target='crm-v6' ORDER BY id DESC LIMIT 1""").fetchone()
        assert audit
    finally:
        worker.api.close()


def test_bot_v6_lifecycle_purchase_first_connect_and_renewal(env):
    store,_,manager,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,price_id='life-v6',price_minor=100000,duration_days=10,
        volume_bytes=6*1024**3,activation_mode='first_connection')).status_code==200
    center=c.app.state.telegram_runtime.customer
    _credit_wallet(center,'dark',996001,500000,'seed-life')
    order=center.commerce.create_order('dark',996001,'lifeuser','turbo','life-v6')
    bought=center.pay_purchase('dark',order['id'])
    assert bought['activation_pending'] is True
    worker,sent=_bot_worker(c,992111)
    runtime=c.app.state.telegram_runtime
    previous=runtime.workers.get('dark')
    runtime.workers['dark']=worker
    try:
        worker.send_delivery(996001,bought)
        ready=[x for x in sent if x[0]==996001 and 'DARK SERVICE READY' in x[1]]
        assert len(ready)==1 and 'منتظر اولین اتصال' in ready[0][1]
        buttons=[b for row in (ready[0][2] or {}).get('inline_keyboard',[]) for b in row]
        assert any(str(b.get('callback_data','')).startswith('usvclink:') for b in buttons)
        client_id=bought['client_id']
        store.record_usage('life-v6-usage-1',client_id,100,200)
        activated=center.commerce.activate_first_connections(manager)
        assert activated and activated[0]['client_id']==client_id
        runtime.notify_activations(activated)
        active=[x for x in sent if x[0]==996001 and 'DARK SERVICE ACTIVATED' in x[1]]
        assert len(active)==1 and 'اولین اتصال تأیید شد' in active[0][1]
        buttons=[b for row in (active[0][2] or {}).get('inline_keyboard',[]) for b in row]
        assert any(str(b.get('callback_data','')).startswith('usvcrenew:') for b in buttons)
        renewal=center.create_renewal_order('dark',996001,'lifeuser',client_id,'life-v6')
        assert worker.handle_customer_callback('urpay:'+renewal['id'],996001,996001,{'id':996001,'username':'lifeuser'})
        done=[x for x in sent if x[0]==996001 and 'DARK RENEW COMPLETE' in x[1]]
        assert len(done)==1 and client_id in done[0][1]
    finally:
        if previous is None:runtime.workers.pop('dark',None)
        else:runtime.workers['dark']=previous
        worker.api.close()


def test_bot_v6_support_center_quick_reply_and_customer_360(env):
    store,_,_,_,c=env
    create_inbound(c)
    create(c,email='support-v6',extra={'tgId':997001,'totalGB':4*1024**3})
    center=c.app.state.telegram_runtime.customer
    ticket=center.create_ticket('dark',997001,'supportv6','Connection issue')
    center.add_ticket_message('dark',ticket['id'],'customer',997001,text='Please check my connection')
    worker,sent=_bot_worker(c,992112)
    try:
        worker.admin_support(992112)
        assert any('DARK SUPPORT CENTER' in text for _,text,_ in sent)
        row_id=int(ticket['row_id'])
        worker.admin_support_detail(992112,row_id)
        detail=[x for x in sent if 'SUPPORT TICKET' in x[1]][-1]
        buttons=[b for row in (detail[2] or {}).get('inline_keyboard',[]) for b in row]
        assert any(str(b.get('callback_data','')).startswith('asupquick:') for b in buttons)
        assert any(str(b.get('callback_data','')).startswith('asupcrm:') for b in buttons)
        worker.admin_support_quick(992112,992112,row_id,'checking')
        customer_msgs=[x for x in sent if x[0]==997001 and 'DARK SUPPORT' in x[1]]
        assert customer_msgs and 'در حال بررسی' in customer_msgs[-1][1]
        assert center.ticket(ticket['id'],'dark')['status']=='answered'
        worker.admin_support_customer(992112,997001)
        assert any('CUSTOMER 360' in text and 'support-v6' in text for _,text,_ in sent)
        worker.admin_support_quick(992112,992112,row_id,'resolved')
        assert center.ticket(ticket['id'],'dark')['status']=='closed'
        with store.lock:
            audit=store.db.execute("""SELECT 1 FROM live_audit WHERE action='telegram.support_quick'
              AND target=? ORDER BY id DESC LIMIT 1""",(ticket['id'],)).fetchone()
        assert audit
    finally:
        worker.api.close()


def test_bot_v6_growth_center_retention_campaign_requires_explicit_queue(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,price_id='growth-plan',price_minor=150000,volume_bytes=5*1024**3)).status_code==200
    pending=c.app.state.telegram_commerce.create_order('dark',998001,'growthpending','turbo','growth-plan')
    with store.transaction() as db:
        db.execute("UPDATE commerce_orders SET created_at=? WHERE id=?",(time.time()-4000,pending['id']))
    now_ms=int(time.time()*1000)
    create(c,email='growth-expired',extra={
        'tgId':998002,'totalGB':5*1024**3,'expiryTime':now_ms-24*3600*1000,
    })
    worker,sent=_bot_worker(c,992113)
    try:
        worker.admin_growth_center(992113)
        center=[x for x in sent if 'DARK GROWTH CENTER' in x[1]][-1]
        assert 'پرداخت‌نشده >۳۰m: 1' in center[1]
        assert 'منقضی ۷ روز اخیر: 1' in center[1]
        assert worker._broadcast_targets('pending')==[998001]
        assert worker._broadcast_targets('expired')==[998002]
        worker.growth_campaign_preview(992113,992113,'pending')
        assert worker.sessions[992113]=='broadcast_review'
        with store.lock:
            before=store.db.execute("SELECT COUNT(*) FROM telegram_broadcasts WHERE owner='dark'").fetchone()[0]
        assert before==0
        worker.queue_broadcast(992113,992113)
        with store.lock:
            job=store.db.execute("""SELECT id,segment,total,status FROM telegram_broadcasts
              WHERE owner='dark' ORDER BY created_at DESC LIMIT 1""").fetchone()
            recipients=[r[0] for r in store.db.execute(
                "SELECT telegram_id FROM telegram_broadcast_recipients WHERE broadcast_id=?",(job['id'],))]
        assert tuple(job)[1:]==('pending',1,'queued')
        assert recipients==[998001]
    finally:
        worker.api.close()


def test_bot_v6_service_doctor_and_smart_ticket_dedup(env):
    store,_,_,_,c=env
    create_inbound(c)
    now_ms=int(time.time()*1000)
    create(c,email='doctor-v6',extra={
        'tgId':998101,'totalGB':5*1024**3,'expiryTime':now_ms+5*86400*1000,
    })
    create(c,email='doctor-expired-v6',extra={
        'tgId':998102,'totalGB':5*1024**3,'expiryTime':now_ms-3600*1000,
    })
    worker,sent=_bot_worker(c,992114)
    try:
        with store.lock:
            row_id=int(store.db.execute("SELECT rowid FROM clients WHERE owner='dark' AND id='doctor-v6'").fetchone()[0])
            expired_row=int(store.db.execute("SELECT rowid FROM clients WHERE owner='dark' AND id='doctor-expired-v6'").fetchone()[0])
        worker.customer_service_doctor(998101,998101,row_id)
        doctor=[x for x in sent if x[0]==998101 and 'DARK SERVICE DOCTOR' in x[1]][-1]
        assert '● READY' in doctor[1] and 'doctor-v6' in doctor[1]
        buttons=[b for row in (doctor[2] or {}).get('inline_keyboard',[]) for b in row]
        assert any(str(b.get('callback_data','')).startswith('usvchelp:') for b in buttons)

        worker.customer_service_help(998101,998101,row_id,'doctoruser')
        tickets=worker.runtime.customer.tickets_for_customer('dark',998101)
        assert len(tickets)==1 and tickets[0]['subject'].startswith('Service Doctor')
        messages=worker.runtime.customer.ticket_messages('dark',tickets[0]['id'])
        assert len(messages)==1 and 'Service Doctor Snapshot' in messages[0]['text']
        worker.customer_service_help(998101,998101,row_id,'doctoruser')
        assert len(worker.runtime.customer.tickets_for_customer('dark',998101))==1
        assert any(x[0]==998101 and 'تیکت باز وجود دارد' in x[1] for x in sent)

        worker.customer_service_doctor(998102,998102,expired_row)
        expired=[x for x in sent if x[0]==998102 and 'DARK SERVICE DOCTOR' in x[1]][-1]
        assert 'ACTION REQUIRED · EXPIRED' in expired[1]

        denied=False
        try:
            worker.customer_service_doctor(998102,998102,row_id)
        except Exception:
            denied=True
        assert denied
    finally:
        worker.api.close()


def test_bot_v6_notification_preferences_gate_marketing_and_service_alerts(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(
        inbound_id,price_id='prefs-plan',price_minor=100000,volume_bytes=5*1024**3)).status_code==200
    order=c.app.state.telegram_commerce.create_order('dark',998201,'prefsuser','turbo','prefs-plan')
    with store.transaction() as db:
        db.execute("UPDATE commerce_orders SET created_at=? WHERE id=?",(time.time()-4000,order['id']))
    now_ms=int(time.time()*1000)
    create(c,email='prefs-service',extra={
        'tgId':998201,'totalGB':5*1024**3,'expiryTime':now_ms+48*3600*1000,
    })
    worker,sent=_bot_worker(c,992115)
    runtime=c.app.state.telegram_runtime
    previous=runtime.workers.get('dark')
    runtime.workers['dark']=worker
    try:
        defaults=runtime.notification_preferences('dark',998201)
        assert defaults['marketing_enabled'] is True and defaults['service_alerts_enabled'] is True

        worker.customer_notification_preferences(998201,998201)
        assert any(chat==998201 and 'DARK NOTIFICATION CONTROL' in text for chat,text,_ in sent)

        worker.growth_campaign_preview(992115,992115,'pending')
        assert worker.sessions[992115]=='broadcast_review'
        assert worker.session_data[992115]['kind']=='marketing'
        worker.queue_broadcast(992115,992115)
        with store.lock:
            job=store.db.execute("""SELECT id,kind,total,status FROM telegram_broadcasts
              WHERE owner='dark' ORDER BY created_at DESC LIMIT 1""").fetchone()
        assert tuple(job)[1:]==('marketing',1,'queued')

        runtime.set_notification_preference('dark',998201,'marketing_enabled',False)
        runtime.process_broadcasts()
        notices=[x for x in sent if x[0]==998201 and 'DARK NOTICE' in x[1]]
        assert notices==[]
        with store.lock:
            done=store.db.execute("SELECT status,sent,failed,skipped FROM telegram_broadcasts WHERE id=?",(job['id'],)).fetchone()
        assert tuple(done)==('completed',0,0,1)
        assert worker._broadcast_targets('pending',marketing=True)==[]
        assert 998201 in worker._broadcast_targets('all')

        runtime.set_notification_preference('dark',998201,'service_alerts_enabled',False)
        runtime.notify_service_health()
        alerts=[x for x in sent if x[0]==998201 and 'DARK SERVICE ALERT' in x[1]]
        assert alerts==[]
        runtime.set_notification_preference('dark',998201,'service_alerts_enabled',True)
        runtime.notify_service_health()
        alerts=[x for x in sent if x[0]==998201 and 'DARK SERVICE ALERT' in x[1]]
        assert len(alerts)==1

        worker.toggle_customer_notification(998201,998201,'marketing')
        p=runtime.notification_preferences('dark',998201)
        assert p['marketing_enabled'] is True
        with store.lock:
            audit=store.db.execute("""SELECT 1 FROM live_audit WHERE action='telegram.notification_preference'
              AND target='998201' ORDER BY id DESC LIMIT 1""").fetchone()
        assert audit
    finally:
        if previous is None:runtime.workers.pop('dark',None)
        else:runtime.workers['dark']=previous
        worker.api.close()


def test_bot_v6_health_and_surface_repair_reapply_safe_telegram_contract(env):
    store,_,_,_,c=env
    worker,sent=_bot_worker(c,992116)
    calls=[]
    def fake_call(method,payload=None):
        calls.append((method,payload or {}))
        if method=='getMe':return {'id':4242,'username':'dark_health_bot'}
        if method=='getWebhookInfo':return {'url':'https://legacy.example/webhook'}
        if method=='getChatMenuButton':return {'type':'web_app','text':'فروشگاه'}
        return True
    worker.api.call=fake_call
    try:
        worker.bot_health(992116)
        health=[x for x in sent if 'DARK BOT HEALTH' in x[1]][-1]
        assert '@dark_health_bot' in health[1]
        assert 'Webhook: ○ webhook set' in health[1]
        assert 'Menu: ● فروشگاه' in health[1]
        assert 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' not in health[1]

        worker.repair_telegram_surface(992116,992116)
        methods=[x[0] for x in calls]
        assert 'deleteWebhook' in methods
        assert 'setMyCommands' in methods
        assert 'setChatMenuButton' in methods
        menu=[p for m,p in calls if m=='setChatMenuButton'][-1]
        assert menu['menu_button']['text']=='فروشگاه'
        assert menu['menu_button']['type']=='web_app'
        with store.lock:
            row=store.db.execute("SELECT bot_username,last_error,last_seen FROM telegram_bots WHERE owner='dark'").fetchone()
            audit=store.db.execute("""SELECT 1 FROM live_audit WHERE action='telegram.bot_surface_repair'
              ORDER BY id DESC LIMIT 1""").fetchone()
        assert row['bot_username']=='dark_health_bot' and row['last_error']=='' and row['last_seen']>0
        assert audit
        assert any('Telegram Surface Repair انجام شد' in text for _,text,_ in sent)
    finally:
        worker.api.close()


def test_bot_v6_final_closeout_navigation_owner_rep_customer(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    customer_id=998301
    create(c,email='closeout-v6',extra={
        'tgId':customer_id,'totalGB':5*1024**3,
        'expiryTime':int(time.time()*1000)+7*86400*1000,
    })
    center=c.app.state.telegram_runtime.customer
    ticket=center.create_ticket('dark',customer_id,'closeout','Closeout support')
    center.add_ticket_message('dark',ticket['id'],'customer',customer_id,text='navigation check')
    worker,sent=_bot_worker(c,992117)

    def callbacks(markup):
        return [str(b.get('callback_data') or '') for row in (markup or {}).get('inline_keyboard',[]) for b in row]

    try:
        worker.admin_store(992117)
        worker.admin_growth_center(992117)
        worker.admin_settings(992117)
        worker.admin_support(992117)
        admin_screens=[x for x in sent if any(k in x[1] for k in (
            'DARK STORE / V6','DARK GROWTH CENTER','تنظیمات DARK BOT','DARK SUPPORT CENTER'))]
        assert len(admin_screens)>=4
        for _,_,markup in admin_screens[-4:]:
            assert 'ahome' in callbacks(markup)

        with store.lock:
            row_id=int(store.db.execute("SELECT rowid FROM clients WHERE owner='dark' AND id='closeout-v6'").fetchone()[0])
        worker.customer_service_detail(customer_id,customer_id,row_id)
        worker.customer_service_doctor(customer_id,customer_id,row_id)
        worker.customer_notification_preferences(customer_id,customer_id)
        worker.customer_ticket_detail(customer_id,customer_id,int(ticket['row_id']))
        customer_screens=[x for x in sent if x[0]==customer_id and any(k in x[1] for k in (
            'DARK SERVICE\n','DARK SERVICE DOCTOR','DARK NOTIFICATION CONTROL','Closeout support'))]
        assert len(customer_screens)>=4
        for _,_,markup in customer_screens[-4:]:
            assert 'uhome' in callbacks(markup)

        owner_labels=[x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row]
        assert '🛒 فروش' in owner_labels and '📊 گزارش‌ها' in owner_labels and '⚙️ مدیریت' in owner_labels
        assert '📈 رشد و فروش' not in owner_labels
        assert '◈ DARK Mini App' not in owner_labels
    finally:
        worker.api.close()

    assert c.put('/api/owners/closeoutrep',json={
        'name':'Closeout Rep','allowed':[inbound_id],'volume_credit_bytes':50*1024**3,
        'unlimited_credit':2,'max_clients':20}).status_code==200
    assert c.post('/api/admins',json={
        'username':'closeoutrep','password':'CloseoutRepPass88','role':'reseller'}).status_code==200
    rep_token,principal=auth.login('closeoutrep','CloseoutRepPass88','','127.0.0.9',3600,'closeout-rep')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',rep_token);seller.headers['X-Dark-CSRF']=principal.csrf
        bot_token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        assert seller.put('/api/telegram/settings',json={
            'enabled':False,'bot_token':bot_token,'admin_telegram_id':998399}).status_code==200
        rep=BotWorker(seller.app.state.telegram_runtime,'closeoutrep',bot_token,'closeout-rep-bot')
        rep_sent=[]
        rep.api.send=lambda chat_id,text,reply_markup=None: rep_sent.append((chat_id,text,reply_markup))
        try:
            labels=[x['text'] for row in rep.main_keyboard(True)['keyboard'] for x in row]
            assert '➕ ساخت نماینده' not in labels and '🤝 نمایندگان' not in labels and '💾 بکاپ' not in labels
            assert '🛒 فروش' in labels and '📊 گزارش‌ها' in labels and '⚙️ مدیریت' in labels
            rep.admin_store(998399)
            assert 'ahome' in callbacks(rep_sent[-1][2])
        finally:
            rep.api.close()


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


def test_owner_customer_menu_adds_representative_marketplace(env):
    _,_,_,_,c=env
    worker,_=_bot_worker(c,993001)
    try:
        labels=[x['text'] for row in worker.main_keyboard(False)['keyboard'] for x in row]
        assert labels==[
            '⚡ خرید سرویس','📦 سرویس‌های من',
            '💳 کیف پول','🎫 پشتیبانی','☰ بیشتر',
        ]
        sent=[]
        worker.api.send=lambda chat_id,text,reply_markup=None:sent.append((text,reply_markup))
        worker.customer_more_menu(993001,993001)
        more=[b.get('callback_data') for row in sent[-1][1]['inline_keyboard'] for b in row]
        assert 'rmmarket' in more and 'more_renew' in more
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


def test_representative_panel_has_independent_bot_customer_wallet_store_and_support(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/owners/sellerbot',json={
        'name':'Seller Bot','allowed':[inbound_id],'volume_credit_bytes':200*1024**3,
        'unlimited_credit':5,'max_clients':50}).status_code==200
    assert c.post('/api/admins',json={
        'username':'sellerbot','password':'SellerBotPass88','role':'reseller'}).status_code==200
    token,p=auth.login('sellerbot','SellerBotPass88','','127.0.0.7',3600,'seller-bot-test')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        bot_token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        r=seller.put('/api/telegram/settings',json={
            'enabled':False,'bot_token':bot_token,'admin_telegram_id':880001})
        assert r.status_code==200,r.text
        status=seller.get('/api/telegram/status').json()
        assert status['owner']=='sellerbot' and status['configured'] is True
        assert seller.put('/api/commerce/products',json={
            'id':'seller-plan','name':'Seller Plan','description':'Rep scoped',
            'category':'Seller','kind':'volume','sale_limit_per_user':0,
            'renewal_enabled':True,'add_volume_enabled':True,
            'active':True,'visible':True}).status_code==200
        center=seller.app.state.telegram_runtime.customer
        _credit_wallet(center,'sellerbot',880002,150000,'seller-wallet')
        assert center.wallet('sellerbot',880002)['balance_minor']==150000
        assert center.wallet('dark',880002)['balance_minor']==0
        ticket=center.create_ticket('sellerbot',880002,'buyer','Seller support')
        center.add_ticket_message('sellerbot',ticket['id'],'customer',880002,text='help')
        assert len(center.tickets_for_customer('sellerbot',880002))==1
        assert len(center.tickets_for_customer('dark',880002))==0
        worker=BotWorker(seller.app.state.telegram_runtime,'sellerbot',bot_token,'seller-v2')
        try:
            assert worker.owner_role()=='reseller'
            admin_labels=[x['text'] for row in worker.main_keyboard(True)['keyboard'] for x in row]
            assert '➕ ساخت نماینده' not in admin_labels
            customer_labels=[x['text'] for row in worker.main_keyboard(False)['keyboard'] for x in row]
            assert customer_labels==[
                '⚡ خرید سرویس','📦 سرویس‌های من',
                '💳 کیف پول','🎫 پشتیبانی','☰ بیشتر',
            ]
        finally:
            worker.api.close()
    assert c.get('/api/telegram/status').json()['owner']=='dark'
    assert all(x['id']!='seller-plan' for x in c.get('/api/commerce/products').json())

def test_simple_store_v4_api_generates_ids_and_safe_defaults(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    r=c.post('/api/commerce/simple-plans',json={
        'name':'Turbo 100','plan_type':'volume','price_minor':450000,
        'duration_days':180,'volume_gb':100,'ip_limit':3,'inbound_ids':[inbound_id],
        'published':True,
    })
    assert r.status_code==201,r.text
    doc=r.json()
    assert doc['id'].startswith('p_') and doc['price_id'].startswith('v_')
    assert doc['kind']=='volume' and doc['active'] is True and doc['visible'] is True
    assert len(doc['prices'])==1
    price=doc['prices'][0]
    assert price['id']==doc['price_id'] and price['duration_days']==180
    assert price['volume_bytes']==100*1024**3 and price['unlimited_units']==0
    assert price['ip_limit']==3 and price['hwid_limit']==0
    assert price['activation_mode']=='first_connection'
    assert price['delivery_mode']=='subscription'
    assert price['primary_inbound_id']==inbound_id
    assert price['show_qr'] is True and price['show_portal'] is True
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM commerce_products WHERE owner='dark' AND id=?",(doc['id'],)).fetchone()[0]==1


def test_simple_store_v4_defaults_to_draft_without_publish(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    r=c.post('/api/commerce/simple-plans',json={
        'name':'Draft Unlimited','plan_type':'unlimited','price_minor':700000,
        'duration_days':30,'volume_gb':0,'ip_limit':1,'inbound_ids':[inbound_id],
    })
    assert r.status_code==201,r.text
    doc=r.json()
    assert doc['active'] is False and doc['visible'] is False and doc['published'] is False
    assert doc['prices'][0]['active'] is False
    assert doc['prices'][0]['volume_bytes']==0 and doc['prices'][0]['unlimited_units']==1


def test_representative_bot_simple_plan_wizard_uses_only_allowed_inbounds(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/owners/simpleseller',json={
        'name':'Simple Seller','allowed':[inbound_id],'volume_credit_bytes':500*1024**3,
        'unlimited_credit':5,'max_clients':50}).status_code==200
    assert c.post('/api/admins',json={
        'username':'simpleseller','password':'SimpleSeller88','role':'reseller'}).status_code==200
    token,p=auth.login('simpleseller','SimpleSeller88','','127.0.0.22',3600,'simple-store-v4')
    bot_token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        assert seller.put('/api/telegram/settings',json={
            'enabled':False,'bot_token':bot_token,'admin_telegram_id':991001}).status_code==200
        worker=BotWorker(seller.app.state.telegram_runtime,'simpleseller',bot_token,'simple-v4')
        sent=[];worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((text,reply_markup))
        try:
            worker.start_simple_plan_create(991001,991001)
            worker.handle_store_text(991001,991001,'Rep Turbo')
            worker.simple_plan_choose_category(991001,991001,'turbo')
            worker.simple_plan_choose_type(991001,991001,'volume')
            worker.handle_store_text(991001,991001,'350000')
            worker.simple_plan_choose_months(991001,991001,3)
            worker.simple_plan_choose_volume(991001,991001,50)
            worker.simple_plan_choose_ip(991001,991001,2)
            worker.simple_plan_toggle_inbound(991001,991001,inbound_id)
            worker.simple_plan_review(991001,991001)
            assert worker.sessions[991001]=='store_simple_review'
            worker.publish_simple_plan(991001,991001)
        finally:
            worker.api.close()
    with store.lock:
        product=store.db.execute("SELECT id,kind,active,visible FROM commerce_products WHERE owner='simpleseller'").fetchone()
        price=store.db.execute("SELECT duration_days,volume_bytes,ip_limit,hwid_limit,inbound_ids,activation_mode,delivery_mode FROM commerce_prices WHERE owner='simpleseller'").fetchone()
    assert product and product['id'].startswith('p_') and tuple(product)[1:]==('volume',1,1)
    assert price['duration_days']==90 and price['volume_bytes']==50*1024**3
    assert price['ip_limit']==2 and price['hwid_limit']==0
    assert json.loads(price['inbound_ids'])==[inbound_id]
    assert price['activation_mode']=='first_connection' and price['delivery_mode']=='subscription'
    assert any('پیش‌نمایش نهایی' in text for text,_ in sent)
    assert any('پلن فروش منتشر شد' in text for text,_ in sent)


def test_simple_store_v6_accepts_manual_days_volume_and_ip(env):
    store,engine,manager,auth,c=env;inbound_id=create_inbound(c)
    r=c.post('/api/commerce/simple-plans',json={
        'name':'MANUAL V6','plan_type':'volume','price_minor':123456,'duration_days':47,
        'volume_gb':73,'ip_limit':9,'inbound_ids':[inbound_id],'published':True})
    assert r.status_code==201,r.text
    doc=r.json();price=doc['prices'][0]
    assert price['duration_days']==47
    assert price['volume_bytes']==73*1024**3
    assert price['ip_limit']==9

def test_simple_store_v6_rejects_manual_values_outside_safe_ranges(env):
    store,engine,manager,auth,c=env;inbound_id=create_inbound(c)
    base={'name':'BAD V6','plan_type':'volume','price_minor':1,'duration_days':30,'volume_gb':10,'ip_limit':1,'inbound_ids':[inbound_id]}
    for key,value in [('duration_days',0),('duration_days',3651),('volume_gb',1000001),('ip_limit',-1),('ip_limit',1001)]:
        body=dict(base);body[key]=value
        assert c.post('/api/commerce/simple-plans',json=body).status_code in (400,422)

def test_customer_receipt_goes_directly_to_admin_pv_not_forum(env):
    store,_,_,_,client=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    admin_id=993001
    user_id=993002
    assert client.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':admin_id}).status_code==200
    center=client.app.state.telegram_runtime.customer
    center.ensure_referral_profile('dark',user_id)
    top=center.create_topup('dark',user_id,'receipt-user',250000)
    worker=BotWorker(client.app.state.telegram_runtime,'dark',token,'receipt-pv-test')
    calls=[];fallback=[]
    worker.api.call=lambda method,payload=None: calls.append((method,payload or {})) or {'message_id':1}
    worker.api.send=lambda chat_id,text,reply_markup=None: fallback.append((chat_id,text,reply_markup))
    forum_calls=[]
    worker.runtime.forum.report_media=lambda *args,**kwargs: forum_calls.append((args,kwargs)) or True
    try:
        handled=worker.handle_customer_media(user_id,user_id,{'photo':[{'file_id':'small'},{'file_id':'receipt-photo'}]})
    finally:
        worker.api.close()
    assert handled is True
    media=[payload for method,payload in calls if method=='sendPhoto']
    assert len(media)==1
    assert media[0]['chat_id']==admin_id
    assert media[0]['photo']=='receipt-photo'
    assert 'رسید شارژ کیف پول' in media[0]['caption']
    assert media[0]['reply_markup']['inline_keyboard'][0][0]['callback_data'].startswith('utopok:')
    assert forum_calls==[]
    with store.lock:
        row=store.db.execute('SELECT status,receipt_ref FROM customer_topups WHERE id=?',(top['id'],)).fetchone()
    assert row['status']=='review' and row['receipt_ref']=='telegram:photo:receipt-photo'


def test_bot_settings_exposes_owner_scoped_customer_miniapp_setup(env):
    store,_,_,auth,client=env
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    owner_admin=994001
    assert client.put('/api/telegram/settings',json={
        'enabled':False,'bot_token':token,'admin_telegram_id':owner_admin}).status_code==200
    owner=BotWorker(client.app.state.telegram_runtime,'dark',token,'miniapp-owner')
    owner_sent=[]
    owner.api.send=lambda chat_id,text,reply_markup=None: owner_sent.append((chat_id,text,reply_markup))
    try:
        owner.admin_settings(owner_admin)
        settings_markup=owner_sent[-1][2]
        assert settings_markup['inline_keyboard'][0][0]['callback_data']=='miniappsetup'
        owner.mini_app_setup(owner_admin)
        text=owner_sent[-1][1];markup=owner_sent[-1][2]
        assert 'فعال‌سازی Customer Mini App' in text
        assert 'owner=dark' in text
        assert 'URL مخصوص این ربات' in text and 'BotFather' in text
        assert 'owner=dark' in markup['inline_keyboard'][0][0]['web_app']['url']
    finally:
        owner.api.close()

    assert client.put('/api/owners/seller',json={
        'name':'Seller','allowed':[],'volume_credit_bytes':50*1024**3,
        'unlimited_credit':1,'max_clients':10}).status_code==200
    assert client.post('/api/admins',json={
        'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    with store.transaction() as db:
        db.execute("INSERT INTO telegram_bots(owner,enabled,token_enc,admin_telegram_id,updated_at) VALUES(?,?,?,?,?)",
                   ('seller',0,auth.cipher.encrypt(token.encode()).decode(),994002,1.0))
    seller=BotWorker(client.app.state.telegram_runtime,'seller',token,'miniapp-seller')
    seller_sent=[]
    seller.api.send=lambda chat_id,text,reply_markup=None: seller_sent.append((chat_id,text,reply_markup))
    try:
        seller.admin_settings(994002)
        assert seller_sent[-1][2]['inline_keyboard'][0][0]['callback_data']=='miniappsetup'
        seller.mini_app_setup(994002)
        text=seller_sent[-1][1];url=seller_sent[-1][2]['inline_keyboard'][0][0]['web_app']['url']
        assert 'Owner: seller' in text
        assert 'owner=seller' in text and 'owner=seller' in url
        assert 'owner=dark' not in url
    finally:
        seller.api.close()


def test_simple_plan_supports_explicit_unlimited_ip_zero(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    r=c.post('/api/commerce/simple-plans',json={
        'name':'Volume Unlimited IP','plan_type':'volume','price_minor':150000,
        'duration_days':30,'volume_gb':25,'ip_limit':0,'inbound_ids':[inbound_id],
        'published':True,
    })
    assert r.status_code==201,r.text
    doc=r.json();price=doc['prices'][0]
    assert price['ip_limit']==0 and price['device_limit']==0
    runtime=c.app.state.telegram_runtime
    order=runtime.commerce.create_order('dark',991001,'buyer',doc['id'],price['id'])
    with store.lock:
        row=store.db.execute("SELECT ip_limit FROM commerce_orders WHERE id=?",(order['id'],)).fetchone()
    assert row['ip_limit']==0


def test_guided_checkout_can_narrow_multi_server_plan_to_selected_inbound(env):
    store,_,_,_,c=env
    first=create_inbound(c)
    second_payload=inbound_payload(25102);second_payload['tag']='rep-v2-second';second_payload['remark']='REP V2 SECOND'
    second_response=c.post('/api/inbounds',json=second_payload)
    assert second_response.status_code==200,second_response.text
    second=second_response.json()['id']
    r=c.post('/api/commerce/simple-plans',json={
        'name':'Pick Server','plan_type':'volume','price_minor':200000,
        'duration_days':60,'volume_gb':40,'ip_limit':2,'inbound_ids':[first,second],
        'published':True,
    })
    assert r.status_code==201,r.text
    doc=r.json();price=doc['prices'][0]
    runtime=c.app.state.telegram_runtime
    order=runtime.commerce.create_order('dark',991002,'buyer',doc['id'],price['id'],selected_inbound_id=second)
    with store.lock:
        row=store.db.execute("SELECT inbound_ids,primary_inbound_id FROM commerce_orders WHERE id=?",(order['id'],)).fetchone()
    assert json.loads(row['inbound_ids'])==[second]
    assert row['primary_inbound_id']==second


def test_representative_bot_can_delete_unused_store_plan(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/owners/sellerdelete',json={
        'name':'Seller Delete','allowed':[inbound_id],'volume_credit_bytes':100*1024**3,
        'unlimited_credit':1,'max_clients':20
    }).status_code==200
    assert c.post('/api/admins',json={
        'username':'sellerdelete','password':'SellerDelete88','role':'reseller'}).status_code==200
    token,p=auth.login('sellerdelete','SellerDelete88','','127.0.0.8',3600,'delete-plan-test')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        plan=seller.post('/api/commerce/simple-plans',json={
            'name':'Disposable Plan','plan_type':'volume','price_minor':120000,
            'duration_days':30,'volume_gb':20,'ip_limit':0,'inbound_ids':[inbound_id],
            'published':True,
        })
        assert plan.status_code==201,plan.text
        doc=plan.json()
        worker=BotWorker(seller.app.state.telegram_runtime,'sellerdelete',
                         '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789','delete-plan')
        sent=[];worker.api.send=lambda chat_id,text,reply_markup=None:sent.append((text,reply_markup))
        try:
            worker.delete_or_archive_product(700001,int(doc['row_id']))
        finally:
            worker.api.close()
        assert seller.get('/api/commerce/products').json()==[]
        assert any('کامل حذف شد' in text for text,_ in sent)
        with store.lock:
            assert store.db.execute("SELECT COUNT(*) FROM commerce_products WHERE owner='sellerdelete'").fetchone()[0]==0

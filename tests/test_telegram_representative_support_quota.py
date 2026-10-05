import pytest
from fastapi.testclient import TestClient
from dark_policy import PolicyError
from server import make_app
from telegram_runtime import BotWorker
from test_standalone import env
from test_representatives_v2 import create_inbound
from test_telegram_commerce import product_payload, price_payload
from test_telegram_ops import configure_bot, mini_init, BOT_TOKEN

@pytest.fixture
def representative(env):
    store,engine,manager,auth,c=env
    inbound=create_inbound(c)
    assert c.put('/api/owners/seller',json={'name':'Seller','allowed':[inbound],
        'volume_credit_bytes':100*1024**3,'unlimited_credit':0,'max_clients':10}).status_code==200
    assert c.post('/api/admins',json={'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    token,p=auth.login('seller','SellerPass88','','127.0.0.2',3600,'quota-support-test')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        yield store,c,seller,inbound

def unlimited_spec(inbound):
    return {'name':'Unlimited','plan_type':'unlimited','price_minor':100000,
            'duration_days':30,'ip_limit':1,'inbound_ids':[inbound],'published':True}

def test_unlimited_catalog_requires_assigned_credit_on_all_api_paths(representative):
    store,owner,seller,inbound=representative
    runtime=seller.app.state.telegram_runtime
    assert seller.post('/api/commerce/simple-plans',json=unlimited_spec(inbound)).status_code==400
    assert seller.put('/api/commerce/products',json=product_payload(kind='unlimited')).status_code==400
    assert seller.put('/api/commerce/products',json=product_payload()).status_code==200
    price=price_payload(inbound,volume_bytes=0,unlimited_units=1)
    assert seller.put('/api/commerce/products/turbo/prices',json=price).status_code==400
    assert owner.post('/api/commerce/simple-plans',json=unlimited_spec(inbound)).status_code==201
    with store.transaction() as db:db.execute("UPDATE owners SET unlimited_credit=1 WHERE id='seller'")
    plan=seller.post('/api/commerce/simple-plans',json=unlimited_spec(inbound))
    assert plan.status_code==201,plan.text
    p=plan.json();price_id=p['prices'][0]['id']
    order=runtime.commerce.create_order('seller',123,'buyer',p['id'],price_id)
    runtime.customer.wallet('seller',123)
    with store.transaction() as db:
        db.execute("UPDATE owners SET unlimited_credit=0 WHERE id='seller'")
        db.execute("UPDATE customer_wallets SET balance_minor=200000 WHERE owner='seller' AND telegram_id=123")
    assert runtime.commerce.product_rows('seller',public=True)==[]
    assert any(x['id']==p['id'] for x in runtime.commerce.product_rows('seller'))
    with pytest.raises(PolicyError,match='سهمیه'):runtime.commerce.create_order('seller',123,'buyer',p['id'],price_id)
    with pytest.raises(PolicyError,match='سهمیه'):runtime.customer.pay_purchase('seller',order['id'])
    with pytest.raises(PolicyError,match='سهمیه'):runtime.commerce.start_payment('seller',order['id'],'card')
    assert runtime.customer.wallet('seller',123)['balance_minor']==200000
    assert runtime.customer.ledger('seller',123)==[]
    assert runtime.commerce.order(order['id'],'seller')['status']=='pending'

def test_bot_unlimited_wizards_and_legacy_write_cannot_bypass_quota(representative):
    store,owner,seller,inbound=representative
    configure_bot(seller,700001)
    runtime=seller.app.state.telegram_runtime
    worker=BotWorker(runtime,'seller',BOT_TOKEN,'quota-test');sent=[]
    worker.api.send=lambda chat,text,reply_markup=None:sent.append((text,reply_markup))
    try:
        assert all(x['callback_data']!='ststype:unlimited' for x in worker.plan_type_buttons('ststype'))
        worker.sessions[700001]='store_simple_type_wait';worker.session_data[700001]={}
        with pytest.raises(PolicyError,match='سهمیه'):worker.simple_plan_choose_type(700001,700001,'unlimited')
        assert worker.sessions[700001]=='store_simple_type_wait'
        worker.sessions[700001]='store_product_type_wait'
        with pytest.raises(PolicyError,match='سهمیه'):worker.store_choose_type(700001,700001,'unlimited')
        worker.sessions[700001]='store_product_limit'
        worker.session_data[700001]={'name':'Bad','category':'VIP','kind':'unlimited'}
        with pytest.raises(PolicyError,match='سهمیه'):worker.handle_store_text(700001,700001,'0')
        worker.session_data[700001]={'volume_bytes':0}
        with pytest.raises(PolicyError,match='سهمیه'):worker.save_price_wizard(700001,700001)
        assert runtime.commerce.product_rows('seller')==[]
    finally:worker.api.close()

@pytest.mark.parametrize('value', ['@Seller_support','t.me/Seller_support','https://telegram.me/Seller_support'])
def test_support_contact_is_per_representative_and_customer_visible(representative,value):
    store,owner,seller,inbound=representative
    configure_bot(seller,700001)
    r=seller.put('/api/telegram/operations/support-contact',json={'support_url':value})
    assert r.status_code==200,r.text
    assert r.json()['support_url']=='https://t.me/Seller_support'
    assert owner.get('/api/telegram/operations/support').json()['support_url']==''
    headers={'X-Telegram-Init-Data':mini_init(BOT_TOKEN,123)}
    customer=seller.get('/api/telegram-customer/bootstrap',params={'owner':'seller'},headers=headers)
    assert customer.status_code==200,customer.text
    assert customer.json()['support_url']=='https://t.me/Seller_support'
    assert seller.put('/api/telegram-miniapp/support-contact',params={'owner':'seller'},headers=headers,
        json={'support_url':'@Hacker'}).status_code in (400,403)
    admin_headers={'X-Telegram-Init-Data':mini_init(BOT_TOKEN,700001)}
    assert seller.put('/api/telegram-miniapp/support-contact',params={'owner':'seller'},headers=admin_headers,
        json={'support_url':'@New_support'}).status_code==200
    assert seller.put('/api/telegram/operations/support-contact',json={'support_url':'-'}).json()['support_url']==''

@pytest.mark.parametrize('value', ['javascript:alert(1)','https://evil.test/name','https://t.me/name?start=x',
                                 'https://t.me/name/other','https://evil@t.me/name','https://t.me:443/name','@bad\"name'])
def test_support_contact_rejects_unsafe_urls(env,value):
    runtime=env[-1].app.state.telegram_runtime
    with pytest.raises(PolicyError):runtime.customer.set_support_contact('dark',value)
    assert runtime.customer.settings('dark')['support_url']==''

def test_bot_empty_support_can_set_contact_and_rejects_nonadmin(representative):
    store,owner,seller,inbound=representative;configure_bot(seller,700001)
    runtime=seller.app.state.telegram_runtime
    worker=BotWorker(runtime,'seller',BOT_TOKEN,'support-test');sent=[]
    worker.api.send=lambda chat,text,reply_markup=None:sent.append((text,reply_markup))
    try:
        worker.admin_support(700001)
        assert any(b.get('callback_data')=='supportcontact' for row in sent[-1][1]['inline_keyboard'] for b in row)
        assert worker.handle_customer_callback('supportcontact',123,123,{}) is False
        assert worker.handle_customer_callback('supportcontact',700001,700001,{}) is True
        worker.handle_customer_text(700001,700001,'@Seller_support','seller')
        worker.customer_support_menu(123,123)
        assert sent[-1][1]['inline_keyboard'][0][0]['url']=='https://t.me/Seller_support'
        worker.sessions[123]='customer_admin_support_contact'
        with pytest.raises(PolicyError):worker.handle_customer_text(123,123,'@Hacker','buyer')
        assert runtime.customer.settings('seller')['support_url']=='https://t.me/Seller_support'
        ticket=runtime.customer.create_ticket('seller',123,'buyer','Problem')
        runtime.customer.add_ticket_message('seller',ticket['id'],'customer',123,text='Help')
        with pytest.raises(PolicyError):runtime.customer.ticket(ticket['id'],'dark')
        runtime.customer.add_ticket_message('seller',ticket['id'],'admin',700001,text='Reply')
        runtime.customer.close_ticket('seller',ticket['id'])
        assert runtime.customer.ticket(ticket['id'],'seller')['status']=='closed'
    finally:worker.api.close()

def test_stale_unlimited_renewal_cannot_debit_wallet(representative):
    store,owner,seller,inbound=representative;runtime=seller.app.state.telegram_runtime
    with store.transaction() as db:db.execute("UPDATE owners SET unlimited_credit=1 WHERE id='seller'")
    plan=runtime.commerce.create_simple_plan('seller',unlimited_spec(inbound))
    order=runtime.commerce.create_order('seller',123,'buyer',plan['id'],plan['prices'][0]['id'])
    runtime.customer.wallet('seller',123)
    with store.transaction() as db:
        db.execute("UPDATE owners SET unlimited_credit=0 WHERE id='seller'")
        db.execute("UPDATE commerce_orders SET order_type='renewal',target_client_id='legacy-service' WHERE id=?",(order['id'],))
        db.execute("UPDATE customer_wallets SET balance_minor=200000 WHERE owner='seller' AND telegram_id=123")
    with pytest.raises(PolicyError,match='سهمیه'):runtime.customer.pay_renewal('seller',order['id'])
    assert runtime.customer.wallet('seller',123)['balance_minor']==200000
    assert runtime.customer.ledger('seller',123)==[]
    worker=BotWorker(runtime,'seller',BOT_TOKEN,'legacy-toggle');worker.api.send=lambda *args:None
    with store.transaction() as db:
        db.execute("UPDATE commerce_products SET active=0,visible=0 WHERE owner='seller'")
        db.execute("UPDATE commerce_prices SET active=0 WHERE owner='seller'")
    product=runtime.commerce.product_rows('seller')[0]
    try:
        with pytest.raises(PolicyError,match='سهمیه'):worker.toggle_product(123,product['row_id'],'active')
        with pytest.raises(PolicyError,match='سهمیه'):worker.toggle_price(123,product['prices'][0]['row_id'])
        with pytest.raises(PolicyError,match='سهمیه'):worker.clone_product(123,product['row_id'])
    finally:worker.api.close()

def test_support_contact_migrates_existing_settings_without_losing_values(env):
    from telegram_customer import CustomerCenter
    store,_,manager,_,c=env;runtime=c.app.state.telegram_runtime
    with store.lock:
        store.db.execute('DROP TABLE telegram_customer_settings')
        store.db.execute("CREATE TABLE telegram_customer_settings(owner TEXT PRIMARY KEY,currency TEXT NOT NULL DEFAULT 'IRT',referral_reward_minor INTEGER NOT NULL DEFAULT 0,support_enabled INTEGER NOT NULL DEFAULT 1,updated_at REAL NOT NULL)")
        store.db.execute("INSERT INTO telegram_customer_settings VALUES('dark','IRT',50000,1,123)")
    customer=CustomerCenter(store,runtime.commerce,manager)
    settings=customer.settings('dark')
    assert settings['support_url']=='' and settings['referral_reward_minor']==50000
    assert customer.set_support_contact('dark','@Owner_support')['support_url']=='https://t.me/Owner_support'

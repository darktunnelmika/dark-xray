import time

import pytest
from fastapi.testclient import TestClient

from dark_policy import PermissionDenied,PolicyError
from server import make_app
from telegram_runtime import BotWorker
from test_standalone import env
from test_representatives_v2 import create_inbound


def plan_payload(inbound_id,**overrides):
    body={
        'name':'DARK REP STARTER','description':'Owner fixed representative plan',
        'price_minor':200000,'currency':'IRT','duration_days':30,
        'volume_credit_bytes':50*1024**3,'unlimited_credit':3,'max_clients':20,
        'prefix':'gold_','max_client_ips':2,'max_client_hwid':1,
        'allowed_inbounds':[inbound_id],'bot_allowed':True,'renewal_enabled':True,
        'active':True,'visible':True,
    }
    body.update(overrides);return body


def credit_wallet(center,owner,telegram_id,amount,reference):
    with center.store.transaction() as db:
        center._credit_tx(db,owner,telegram_id,amount,'topup',reference,'marketplace test credit')


def test_plan_management_is_owner_only(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    r=c.put('/api/representative-marketplace/plans/starter',json=plan_payload(inbound_id))
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['allowed_inbounds']==[inbound_id]
    assert doc['volume_credit_bytes']==50*1024**3
    assert c.get('/api/representative-marketplace/plans').json()[0]['id']=='starter'

    assert c.put('/api/owners/sellerx',json={
        'name':'Seller X','allowed':[inbound_id],'volume_credit_bytes':100*1024**3,
        'unlimited_credit':2,'max_clients':10}).status_code==200
    assert c.post('/api/admins',json={
        'username':'sellerx','password':'SellerXPass88','role':'reseller'}).status_code==200
    token,p=auth.login('sellerx','SellerXPass88','','127.0.0.8',3600,'rep-plan-deny')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        assert seller.get('/api/representative-marketplace/plans').status_code==403
        denied=seller.put('/api/representative-marketplace/plans/hack',json=plan_payload(inbound_id))
        assert denied.status_code==403
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM representative_plans WHERE id='hack'").fetchone()[0]==0


def test_purchase_uses_snapshot_not_later_owner_edits(env):
    store,_,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/representative-marketplace/plans/starter',json=plan_payload(inbound_id)).status_code==200
    market=c.app.state.telegram_runtime.marketplace
    center=c.app.state.telegram_runtime.customer
    credit_wallet(center,'dark',810001,500000,'rep-buy-seed')
    plan=market.plan_rows('dark',public=True)[0]
    order=market.create_order('dark',810001,'repbuyer',plan['row_id'],'purchase')

    changed=plan_payload(inbound_id,price_minor=900000,volume_credit_bytes=5*1024**3,
                         unlimited_credit=0,max_clients=2,max_client_ips=1,max_client_hwid=0)
    assert c.put('/api/representative-marketplace/plans/starter',json=changed).status_code==200

    result=market.pay_order('dark',order['id'],810001)
    assert result['status']=='provisioned'
    assert result['username']==result['representative_id'] and result['password']
    rep_id=result['representative_id'];password=result['password']
    profile=manager.profile(rep_id)
    with store.lock:
        owner=store.db.execute("""SELECT volume_credit_bytes,unlimited_credit,max_clients,manual,account_disabled
          FROM owners WHERE id=?""",(rep_id,)).fetchone()
    assert owner['volume_credit_bytes']==50*1024**3
    assert owner['unlimited_credit']==3 and owner['max_clients']==20
    assert owner['manual']==0 and owner['account_disabled']==0
    assert profile['allowed']==[inbound_id]
    assert profile['max_client_ips']==2 and profile['max_client_hwid']==1
    assert profile['prefix'].startswith('gold_'+rep_id)
    assert center.wallet('dark',810001)['balance_minor']==300000
    token,p=auth.login(rep_id,password,'','127.0.0.9',3600,'market-login')
    assert token and p.actor.role=='reseller'

    market.mark_credentials_delivered('dark',order['id'])
    assert 'password' not in market.result(order['id'],'dark')


def test_failed_provision_refunds_wallet_and_cleans_partial_account(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/representative-marketplace/plans/starter',json=plan_payload(
        inbound_id,price_minor=120000)).status_code==200
    market=c.app.state.telegram_runtime.marketplace;center=c.app.state.telegram_runtime.customer
    credit_wallet(center,'dark',815001,200000,'rep-fail-seed')
    plan=market.plan_rows('dark',public=True)[0]
    order=market.create_order('dark',815001,'brokenbuyer',plan['row_id'],'purchase')
    assert c.delete('/api/inbounds/'+str(inbound_id)).status_code==200

    with pytest.raises(PolicyError):
        market.pay_order('dark',order['id'],815001)
    assert center.wallet('dark',815001)['balance_minor']==200000
    failed=market.order(order['id'],'dark')
    assert failed['status']=='failed_refunded'
    rep_id=failed['representative_id']
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM api_admins WHERE id=?",(rep_id,)).fetchone()[0]==0
        assert store.db.execute("SELECT COUNT(*) FROM owner_profiles WHERE id=?",(rep_id,)).fetchone()[0]==0


def test_expiry_suspends_login_and_bot_without_disabling_existing_clients_then_renewal_reactivates(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/representative-marketplace/plans/starter',json=plan_payload(
        inbound_id,price_minor=100000,volume_credit_bytes=100*1024**3)).status_code==200
    market=c.app.state.telegram_runtime.marketplace;center=c.app.state.telegram_runtime.customer
    credit_wallet(center,'dark',820001,400000,'rep-exp-seed')
    plan=market.plan_rows('dark',public=True)[0]
    order=market.create_order('dark',820001,'expirebuyer',plan['row_id'],'purchase')
    result=market.pay_order('dark',order['id'],820001)
    rep_id=result['representative_id'];password=result['password']

    owner_actor=center.commerce.actor_for('dark')
    client_id=manager.profile(rep_id)['prefix']+'client'
    manager.create(owner_actor,rep_id,{'email':client_id,'totalGB':10*1024**3,
                                      'limitIp':1,'limitHwid':1,'enable':True},[inbound_id])

    token,p=auth.login(rep_id,password,'','127.0.0.10',3600,'rep-bot-enable')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        r=seller.put('/api/telegram/settings',json={
            'enabled':True,'bot_token':'123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            'admin_telegram_id':820099})
        assert r.status_code==200,r.text

    with store.transaction() as db:
        db.execute("UPDATE representative_subscriptions SET expires_at=? WHERE representative_id=?",
                   (time.time()-5,rep_id))
    assert market.sweep_expired(force=True)==1
    with store.lock:
        admin=store.db.execute("SELECT disabled FROM api_admins WHERE id=?",(rep_id,)).fetchone()
        own=store.db.execute("SELECT manual,account_disabled FROM owners WHERE id=?",(rep_id,)).fetchone()
        bot=store.db.execute("SELECT enabled FROM telegram_bots WHERE owner=?",(rep_id,)).fetchone()
    assert admin['disabled']==1 and bot['enabled']==0
    assert tuple(own)==(0,0)
    detail=manager.detail(owner_actor,client_id,credentials=False)
    assert 'owner_account_disabled' not in detail['block_reasons']
    assert 'owner_manual' not in detail['block_reasons']
    with pytest.raises(PermissionDenied):
        auth.login(rep_id,password,'','127.0.0.11',3600,'expired-login')

    renewal=market.create_order('dark',820001,'expirebuyer',plan['row_id'],'renewal')
    renewed=market.pay_order('dark',renewal['id'],820001)
    assert renewed['status']=='renewed'
    sub=market.subscription('dark',820001)
    assert sub['status']=='active' and sub['expires_at']>time.time()
    with store.lock:
        assert store.db.execute("SELECT disabled FROM api_admins WHERE id=?",(rep_id,)).fetchone()[0]==0
        assert store.db.execute("SELECT enabled FROM telegram_bots WHERE owner=?",(rep_id,)).fetchone()[0]==1
    token,_=auth.login(rep_id,password,'','127.0.0.12',3600,'renewed-login')
    assert token


def test_plan_can_forbid_independent_representative_bot(env):
    _,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/representative-marketplace/plans/nobot',json=plan_payload(
        inbound_id,price_minor=50000,bot_allowed=False)).status_code==200
    market=c.app.state.telegram_runtime.marketplace;center=c.app.state.telegram_runtime.customer
    credit_wallet(center,'dark',830001,100000,'rep-nobot-seed')
    plan=market.plan_rows('dark',public=True)[0]
    order=market.create_order('dark',830001,'nobotbuyer',plan['row_id'],'purchase')
    result=market.pay_order('dark',order['id'],830001)
    rep_id=result['representative_id'];password=result['password']

    token,p=auth.login(rep_id,password,'','127.0.0.13',3600,'nobot-login')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        r=seller.put('/api/telegram/settings',json={
            'enabled':True,'bot_token':'123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            'admin_telegram_id':830099})
        assert r.status_code!=200
        assert 'does not allow' in r.text


def test_customer_bot_marketplace_is_only_on_primary_owner_bot(env):
    store,engine,manager,auth,c=env
    inbound_id=create_inbound(c)
    owner_worker=BotWorker(c.app.state.telegram_runtime,'dark',
                           '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789','owner-menu')
    try:
        labels=[x['text'] for row in owner_worker.main_keyboard(False)['keyboard'] for x in row]
        assert '🏪 خرید پنل نمایندگی' in labels
    finally:owner_worker.api.close()

    assert c.put('/api/owners/repmenu',json={
        'name':'Rep Menu','allowed':[inbound_id],'volume_credit_bytes':10*1024**3,
        'unlimited_credit':1,'max_clients':5}).status_code==200
    assert c.post('/api/admins',json={
        'username':'repmenu','password':'RepMenuPass88','role':'reseller'}).status_code==200
    token,p=auth.login('repmenu','RepMenuPass88','','127.0.0.14',3600,'rep-menu')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as seller:
        seller.cookies.set('dark_session',token);seller.headers['X-Dark-CSRF']=p.csrf
        seller.put('/api/telegram/settings',json={
            'enabled':False,'bot_token':'123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            'admin_telegram_id':830088})
        repworker=BotWorker(seller.app.state.telegram_runtime,'repmenu',
                            '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789','rep-menu')
        try:
            labels=[x['text'] for row in repworker.main_keyboard(False)['keyboard'] for x in row]
            assert '🏪 خرید پنل نمایندگی' not in labels
        finally:repworker.api.close()


def test_marketplace_plan_order_subscription_and_wallet_survive_full_backup(env,tmp_path):
    import dataclasses,json,sqlite3
    from pathlib import Path
    from backup import create_backup,restore_backup
    store,engine,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/representative-marketplace/plans/backup-plan',json=plan_payload(
        inbound_id,price_minor=70000)).status_code==200
    market=c.app.state.telegram_runtime.marketplace
    center=c.app.state.telegram_runtime.customer
    credit_wallet(center,'dark',840001,150000,'rep-backup-seed')
    plan=market.plan_rows('dark',public=True)[0]
    order=market.create_order('dark',840001,'backuprep',plan['row_id'],'purchase')
    result=market.pay_order('dark',order['id'],840001)
    rep_id=result['representative_id']

    data=Path(store.path).parent
    config=data/'config.json'
    config.write_text(json.dumps(dataclasses.asdict(engine.config)),encoding='utf-8')
    archive=tmp_path/'rep-market.darkbackup'
    create_backup(data,config,archive,'Rep-Market-Backup-123!')
    dest=tmp_path/'rep-market-restored'
    out=restore_backup(archive,dest,'Rep-Market-Backup-123!')
    assert out['restored'] is True
    with sqlite3.connect(dest/'data/dark.sqlite3') as db:
        assert db.execute("SELECT name FROM representative_plans WHERE owner='dark' AND id='backup-plan'").fetchone()[0]=='DARK REP STARTER'
        row=db.execute("SELECT status,representative_id FROM representative_market_orders WHERE id=?",(order['id'],)).fetchone()
        assert row==( 'provisioned',rep_id )
        sub=db.execute("SELECT status,representative_id FROM representative_subscriptions WHERE owner='dark' AND buyer_telegram_id=840001").fetchone()
        assert sub==('active',rep_id)
        assert db.execute("SELECT balance_minor FROM customer_wallets WHERE owner='dark' AND telegram_id=840001").fetchone()[0]==80000
def test_bot_purchase_delivery_includes_panel_url(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/representative-marketplace/plans/starter',json=plan_payload(
        inbound_id,price_minor=50000)).status_code==200
    market=c.app.state.telegram_runtime.marketplace
    center=c.app.state.telegram_runtime.customer
    credit_wallet(center,'dark',850001,100000,'rep-url-seed')
    plan=market.plan_rows('dark',public=True)[0]
    order=market.create_order('dark',850001,'urlbuyer',plan['row_id'],'purchase')
    worker=BotWorker(c.app.state.telegram_runtime,'dark',
                     '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789','rep-url')
    sent=[]
    worker.api.send=lambda chat_id,text,reply_markup=None: sent.append((chat_id,text,reply_markup))
    try:
        worker.customer_representative_pay(850001,850001,order['id'])
        text='\n'.join(x[1] for x in sent)
        assert 'Panel: '+worker.runtime.manager.engine.config.public_origin.rstrip('/') in text
        assert 'Username:' in text and 'Password:' in text
    finally:
        worker.api.close()
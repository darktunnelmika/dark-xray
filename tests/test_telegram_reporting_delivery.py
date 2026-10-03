from datetime import datetime,timezone
import struct

import httpx
import pytest

from dark_policy import PolicyError
from telegram_reporting import sales_summary
from telegram_runtime import TelegramAPI
from telegram_forum import FORUM_REQUEST_ID
from test_standalone import env
from test_telegram_commerce import _bot_worker,FakeTelegramAPI,product_payload,price_payload
from test_representatives_v2 import create_inbound


def seed_order(db,key,amount,created,paid=None,owner='dark',currency='IRT',updated=None,status=None):
    db.execute("""INSERT INTO commerce_orders(id,owner,buyer_telegram_id,product_id,price_id,amount_minor,
      currency,status,created_at,updated_at) VALUES(?,?,880001,'report-plan','report-price',?,?,?,?,?)""",
      (key,owner,amount,currency,status or ('provisioned' if paid is not None else 'pending'),created,updated or created))
    if paid is not None:
        db.execute("""INSERT INTO commerce_payments(id,order_id,owner,gateway_id,amount_minor,currency,status,
          created_at,updated_at) VALUES(?,?,?,'wallet',?,?,'paid',?,?)""",('pay-'+key,key,owner,amount,currency,paid,paid))


def test_reports_use_confirmed_payment_time_owner_and_currency_without_double_counting(env,monkeypatch):
    store,engine,_,_,c=env
    now=datetime(2026,10,3,12,tzinfo=timezone.utc).timestamp()
    import telegram_ops
    monkeypatch.setattr(telegram_ops.time,'time',lambda:now)
    day=datetime(2026,10,2,tzinfo=timezone.utc).timestamp()
    with store.transaction() as db:
        # Activation today must not rebook yesterday's sale.
        seed_order(db,'yesterday',100000,day+100,day+200,updated=now-100)
        seed_order(db,'pending',700000,day+300)
        seed_order(db,'other-owner',900000,day+400,day+450,owner='other')
        seed_order(db,'today',200000,day+500,now-200)
        seed_order(db,'rial',5000000,day+600,day+700,currency='IRR')
        # A second paid attempt is still one sale.
        db.execute("INSERT INTO commerce_payments SELECT 'pay-duplicate',order_id,owner,gateway_id,amount_minor,currency,status,external_ref,created_at,updated_at+1 FROM commerce_payments WHERE id='pay-yesterday'")
        seed_order(db,'mismatch',333333,day+800,day+900)
        db.execute("UPDATE commerce_payments SET amount_minor=1 WHERE id='pay-mismatch'")
        db.execute("""INSERT INTO customer_wallet_ledger(id,owner,telegram_id,delta_minor,currency,kind,reference,created_at)
          VALUES('topup','dark',880001,1000000,'IRT','topup','topup-ref',?)""",(day+1000,))
    before=store.db.total_changes
    yesterday=sales_summary(store.db,'dark',day,day+86400)
    assert yesterday=={'count':2,'amounts':{'IRR':5000000,'IRT':100000}}
    dashboard=c.app.state.telegram_runtime.ops.dashboard('dark')
    assert dashboard['paid_today']==1 and dashboard['revenue_today']==200000
    assert dashboard['revenue_7d']==300000
    assert dashboard['revenue_7d_by_currency']=={'IRR':5000000,'IRT':300000}
    report=c.app.state.telegram_runtime.forum.daily_summary_text('dark',datetime(2026,10,2).date(),'UTC')
    assert 'فروش قطعی: 5,000,000 ریال · 100,000 تومان' in report
    assert 'شارژ کیف پول این روز: 1,000,000 تومان' in report
    assert 'مبلغ سفارش‌های پرداخت‌نشدهٔ این روز: 700,000 تومان' in report
    assert '900,000' not in report and 'Actor:' not in report and 'provisioned' not in report
    assert store.db.total_changes==before


def test_representative_sales_are_separate_from_wallet_topups_and_refunds(env):
    store,_,_,_,c=env
    with store.transaction() as db:
        for key,status,amount in [('rep-ok','provisioned',400000),('rep-refund','failed_refunded',900000)]:
            db.execute("""INSERT INTO representative_market_orders(id,owner,buyer_telegram_id,plan_id,kind,amount_minor,
              currency,status,snapshot,created_at,updated_at) VALUES(?,'dark',880002,'rep','purchase',?,'IRT',?,'{}',1000,3000)""",(key,amount,status))
            db.execute("""INSERT INTO customer_wallet_ledger(id,owner,telegram_id,delta_minor,currency,kind,reference,created_at)
              VALUES(?,'dark',880002,?,'IRT','rep_purchase',?,2000)""",('debit-'+key,-amount,'representative:'+key))
        db.execute("""INSERT INTO customer_wallet_ledger(id,owner,telegram_id,delta_minor,currency,kind,reference,created_at)
          VALUES('refund','dark',880002,900000,'IRT','rep_refund','refund:rep-refund',2500)""")
    assert sales_summary(store.db,'dark',1500,2500)=={'count':1,'amounts':{'IRT':400000}}
    assert sales_summary(store.db,'dark',2500,3500)=={'count':0,'amounts':{}}


def test_daily_reporting_and_dashboard_use_configured_timezone(env,monkeypatch):
    store,engine,_,_,c=env
    now=datetime(2026,10,3,22,tzinfo=timezone.utc).timestamp()
    import telegram_ops
    monkeypatch.setattr(telegram_ops.time,'time',lambda:now)
    original=engine.section
    monkeypatch.setattr(engine,'section',lambda key: {'timezone':'Asia/Tehran'} if key=='panel' else original(key))
    with store.transaction() as db:
        seed_order(db,'tehran-today',100000,now-600,now-500)
        seed_order(db,'tehran-yesterday',200000,now-6000,now-5900)
    dashboard=c.app.state.telegram_runtime.ops.dashboard('dark')
    assert dashboard['revenue_today']==100000
    report=c.app.state.telegram_runtime.forum.daily_summary_text('dark',datetime(2026,10,3).date(),'Asia/Tehran')
    assert 'فروش قطعی: 200,000 تومان' in report and 'Asia/Tehran' in report


def test_reconfirming_paid_order_preserves_first_payment_timestamp(env,monkeypatch):
    store,_,manager,_,c=env;runtime=c.app.state.telegram_runtime
    inbound=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound)).status_code==200
    order=runtime.commerce.create_order('dark',880009,'buyer','turbo','turbo-30')
    # A provisioning error leaves a valid paid order that can be retried later.
    with store.transaction() as db:
        db.execute("UPDATE commerce_orders SET status='paid' WHERE id=?",(order['id'],))
        db.execute("""INSERT INTO commerce_payments(id,order_id,owner,gateway_id,amount_minor,currency,status,
          created_at,updated_at) VALUES('retry-pay',?,'dark','card',3000000,'IRT','paid',1000,1200)""",(order['id'],))
    runtime.commerce.confirm_payment('dark',order['id'],'retry',manager)
    assert store.db.execute("SELECT updated_at FROM commerce_payments WHERE id='retry-pay'").fetchone()[0]==1200


def purchased_service(c,store,qr=True,mode='subscription'):
    inbound=create_inbound(c)
    assert c.put('/api/commerce/products',json=product_payload()).status_code==200
    assert c.put('/api/commerce/products/turbo/prices',json=price_payload(inbound,price_minor=100000,
        volume_bytes=10*1024**3,ip_limit=0,activation_mode='first_connection',show_qr=qr,delivery_mode=mode)).status_code==200
    runtime=c.app.state.telegram_runtime
    with store.transaction() as db:runtime.customer._credit_tx(db,'dark',880003,200000,'topup','seed','test')
    order=runtime.commerce.create_order('dark',880003,'qr-buyer','turbo','turbo-30')
    result=runtime.customer.pay_purchase('dark',order['id'])
    return result


@pytest.mark.parametrize('qr,mode',[(True,'subscription'),(False,'subscription'),(True,'config'),(True,'both'),(True,'portal')])
def test_delivery_and_get_connection_honor_flags_and_include_real_service_details(env,qr,mode,tmp_path):
    store,_,_,_,c=env
    result=purchased_service(c,store,qr,mode);worker,sent=_bot_worker(c)
    photos=[]
    worker.api.send_photo_bytes=lambda chat,data,caption='',reply_markup=None: photos.append((chat,data,caption,reply_markup))
    try:
        worker.send_delivery(880003,result)
        rid=store.db.execute('SELECT rowid FROM clients WHERE id=?',(result['client_id'],)).fetchone()[0]
        worker.customer_connection(880003,880003,rid)
        with pytest.raises(PolicyError):worker.customer_connection(880004,880004,rid)
    finally:worker.api.close()
    assert len(photos)==(2 if qr else 0)
    text='\n'.join(x[1] for x in sent)+'\n'+'\n'.join(x[2] for x in photos)
    assert '100,000 تومان' in text and 'کاربر نامحدود' in text and 'مدت: 30 روز' in text
    assert 'منتظر اولین اتصال' in text and 'DARK TURBO' in text
    assert 'اطلاعات اتصال' in text and 'حجم باقی‌مانده' in text
    payload=result['delivery'].get('subscription_url') or result['delivery'].get('main_config') or result['delivery'].get('portal_url')
    if len(payload)<=256:
        buttons=[b for x in photos for row in (x[3] or {}).get('inline_keyboard',[]) for b in row]
        buttons+=[b for x in sent for row in (x[2] or {}).get('inline_keyboard',[]) for b in row]
        assert any(b.get('copy_text',{}).get('text')==payload for b in buttons)
    for _,data,caption,_ in photos:
        assert data.startswith(b'\x89PNG\r\n\x1a\n')
        width,height=struct.unpack('>II',data[16:24]);assert width==height and width>=300
        assert len(caption.encode('utf-16-le'))//2<=1024


def test_qr_upload_failure_delivers_intact_link_instead_of_losing_purchase(env):
    store,_,_,_,c=env;result=purchased_service(c,store);worker,sent=_bot_worker(c)
    def failure(*args,**kwargs):raise httpx.RemoteProtocolError('disconnected')
    worker.api.send_photo_bytes=failure
    try:worker.send_delivery(880003,result)
    finally:worker.api.close()
    assert any(result['delivery']['subscription_url'] in x[1] and 'ارسال QR کامل نشد' in x[1] for x in sent)


def test_http_retry_is_read_only_and_upload_uses_native_multipart():
    api=TelegramAPI('test');calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:raise httpx.RemoteProtocolError('disconnected')
        return httpx.Response(200,json={'ok':True,'result':[]})
    api.client.close();api.client=httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert api.call('getUpdates',{'offset':7})==[] and len(calls)==2
        calls.clear()
        with pytest.raises(httpx.RemoteProtocolError):api.send(880003,'test')
        assert len(calls)==1
        calls.append(None)
        api.send_photo_bytes(880003,b'png-data','caption',{'inline_keyboard':[]})
        request=calls[-1]
        assert request.url.path.endswith('/sendPhoto')
        assert 'multipart/form-data' in request.headers['content-type']
        assert b'png-data' in request.content and b'caption' in request.content and b'reply_markup' in request.content
    finally:api.close()


def test_transient_disconnect_is_quiet_and_persistent_outage_has_one_recovery(env,monkeypatch):
    _,_,_,_,c=env;worker,_=_bot_worker(c);reports=[];now=[100.0]
    import telegram_runtime
    monkeypatch.setattr(telegram_runtime.time,'monotonic',lambda:now[0])
    worker.runtime.forum.report=lambda *args,**kwargs: reports.append(args[3]) or True
    try:
        worker.network_failed();now[0]=110;worker.network_failed();assert reports==[]
        worker.network_recovered();assert reports==[]
        now[0]=200;worker.network_failed();now[0]=261;worker.network_failed()
        now[0]=280;worker.network_failed();assert len(reports)==1
        worker.network_recovered();assert len(reports)==2 and 'برقرار شد' in reports[-1]
    finally:worker.api.close()


def test_forum_audit_is_readable_and_resumes_after_partial_send(env):
    store,_,manager,_,c=env;forum=c.app.state.telegram_runtime.forum;api=FakeTelegramAPI()
    forum.setup(api,'dark',{'request_id':FORUM_REQUEST_ID,'chat_id':-100888},42)
    actor=c.app.state.telegram_runtime.commerce.actor_for('dark')
    manager.audit(actor,'dark','client.create','service-1','CoreEngine synchronization requested')
    manager.audit(actor,'dark','auth.login','dark','token=never-send-this')
    original=api.call;delivered=[]
    def flaky(method,payload=None):
        if method=='sendMessage':
            if delivered:raise httpx.RemoteProtocolError('disconnected')
            delivered.append(payload['text'])
        return original(method,payload)
    api.call=flaky
    with pytest.raises(httpx.RemoteProtocolError):forum.poll_audits(api,'dark','owner')
    cursor=store.db.execute("SELECT last_audit_id FROM telegram_forums WHERE owner='dark'").fetchone()[0]
    assert cursor>0 and len(delivered)==1
    api.call=original;api.calls=[]
    assert forum.poll_audits(api,'dark','owner')==1
    text=api.calls[-1][1]['text']
    assert 'ورود به پنل' in text and 'Actor:' not in text and 'never-send-this' not in text
    assert 'CoreEngine' not in delivered[0] and 'سرویس جدید' in delivered[0]


def test_representative_creation_is_guided_and_only_creates_after_confirmation(env):
    store,_,_,_,c=env;worker,sent=_bot_worker(c)
    worker.sessions[992001]='rep_new_id';worker.session_data[992001]={}
    try:
        for text in ('newrep','خودکار','۵۰۰','۲۰'):worker.handle_representative_step(992001,992001,text)
        assert worker.sessions[992001]=='rep_new_confirm'
        assert store.db.execute("SELECT 1 FROM api_admins WHERE id='newrep'").fetchone() is None
        worker.handle_representative_step(992001,992001,'تأیید')
        assert store.db.execute("SELECT role FROM api_admins WHERE id='newrep'").fetchone()[0]=='reseller'
        assert 992001 not in worker.session_data
        assert any('رمز ورود نماینده:' in x[1] for x in sent)
    finally:worker.api.close()

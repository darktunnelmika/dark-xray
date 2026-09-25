from __future__ import annotations
import json
import secrets
import time
from typing import Any

from dark_policy import PolicyError

CURRENCY='IRT'

def _id(prefix:str)->str:
    return prefix+'_'+secrets.token_hex(12)

class CustomerCenter:
    def __init__(self,store,commerce,manager):
        self.store=store;self.commerce=commerce;self.manager=manager
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS customer_wallets(
              owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'IRT',
              balance_minor INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,
              PRIMARY KEY(owner,telegram_id));
            CREATE TABLE IF NOT EXISTS customer_wallet_ledger(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,
              delta_minor INTEGER NOT NULL,currency TEXT NOT NULL,kind TEXT NOT NULL,
              reference TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS customer_wallet_ledger_ref
              ON customer_wallet_ledger(owner,kind,reference);
            CREATE TABLE IF NOT EXISTS customer_topups(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,
              username TEXT NOT NULL DEFAULT '',amount_minor INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'IRT',
              status TEXT NOT NULL,receipt_ref TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS customer_topups_owner
              ON customer_topups(owner,telegram_id,created_at);
            CREATE TABLE IF NOT EXISTS customer_referrals(
              owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,code TEXT NOT NULL,
              referrer_telegram_id INTEGER NOT NULL DEFAULT 0,referred_at REAL NOT NULL DEFAULT 0,
              qualified_at REAL NOT NULL DEFAULT 0,reward_minor INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(owner,telegram_id),UNIQUE(owner,code));
            CREATE TABLE IF NOT EXISTS customer_support_tickets(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,telegram_id INTEGER NOT NULL,
              username TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',
              created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS customer_support_owner
              ON customer_support_tickets(owner,status,updated_at);
            CREATE TABLE IF NOT EXISTS customer_support_messages(
              id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL,owner TEXT NOT NULL,
              sender_type TEXT NOT NULL,sender_telegram_id INTEGER NOT NULL,
              text TEXT NOT NULL DEFAULT '',file_kind TEXT NOT NULL DEFAULT '',
              file_id TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS customer_support_messages_ticket
              ON customer_support_messages(ticket_id,created_at);
            CREATE TABLE IF NOT EXISTS telegram_customer_settings(
              owner TEXT PRIMARY KEY,currency TEXT NOT NULL DEFAULT 'IRT',
              referral_reward_minor INTEGER NOT NULL DEFAULT 0,
              support_enabled INTEGER NOT NULL DEFAULT 1,updated_at REAL NOT NULL);
            """)
            columns={r[1] for r in store.db.execute('PRAGMA table_info(commerce_orders)')}
            if 'order_type' not in columns:
                store.db.execute("ALTER TABLE commerce_orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'purchase'")
            if 'target_client_id' not in columns:
                store.db.execute("ALTER TABLE commerce_orders ADD COLUMN target_client_id TEXT NOT NULL DEFAULT ''")
            if 'renewal_target_expiry' not in columns:
                store.db.execute("ALTER TABLE commerce_orders ADD COLUMN renewal_target_expiry INTEGER NOT NULL DEFAULT 0")

    def settings(self,owner:str)->dict[str,Any]:
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM telegram_customer_settings WHERE owner=?',(owner,)).fetchone()
            if not row:
                now=time.time()
                db.execute("INSERT INTO telegram_customer_settings(owner,currency,referral_reward_minor,support_enabled,updated_at) VALUES(?,?,?,?,?)",
                           (owner,CURRENCY,0,1,now))
                row=db.execute('SELECT * FROM telegram_customer_settings WHERE owner=?',(owner,)).fetchone()
        out=dict(row);out['support_enabled']=bool(out['support_enabled']);return out

    def set_referral_reward(self,owner:str,amount_minor:int)->dict[str,Any]:
        amount=int(amount_minor)
        if not 0<=amount<=10**12:raise PolicyError('Referral reward is outside the allowed range')
        self.settings(owner)
        with self.store.transaction() as db:
            db.execute('UPDATE telegram_customer_settings SET referral_reward_minor=?,updated_at=? WHERE owner=?',
                       (amount,time.time(),owner))
        return self.settings(owner)

    def wallet(self,owner:str,telegram_id:int)->dict[str,Any]:
        tid=int(telegram_id);now=time.time()
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM customer_wallets WHERE owner=? AND telegram_id=?',(owner,tid)).fetchone()
            if not row:
                db.execute('INSERT INTO customer_wallets(owner,telegram_id,currency,balance_minor,updated_at) VALUES(?,?,?,?,?)',
                           (owner,tid,CURRENCY,0,now))
                row=db.execute('SELECT * FROM customer_wallets WHERE owner=? AND telegram_id=?',(owner,tid)).fetchone()
        return dict(row)

    def ledger(self,owner:str,telegram_id:int,limit:int=20)->list[dict[str,Any]]:
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute("""SELECT * FROM customer_wallet_ledger
              WHERE owner=? AND telegram_id=? ORDER BY created_at DESC LIMIT ?""",
              (owner,int(telegram_id),max(1,min(int(limit),100))))]

    def _credit_tx(self,db,owner:str,telegram_id:int,amount:int,kind:str,reference:str,detail:str='')->bool:
        amount=int(amount)
        if amount<=0:raise PolicyError('Wallet credit must be positive')
        if db.execute('SELECT 1 FROM customer_wallet_ledger WHERE owner=? AND kind=? AND reference=?',
                      (owner,kind,reference)).fetchone():return False
        now=time.time();tid=int(telegram_id)
        db.execute("""INSERT INTO customer_wallets(owner,telegram_id,currency,balance_minor,updated_at)
          VALUES(?,?,?,?,?) ON CONFLICT(owner,telegram_id) DO UPDATE SET
          balance_minor=customer_wallets.balance_minor+excluded.balance_minor,updated_at=excluded.updated_at""",
          (owner,tid,CURRENCY,amount,now))
        db.execute("""INSERT INTO customer_wallet_ledger(id,owner,telegram_id,delta_minor,currency,kind,reference,detail,created_at)
          VALUES(?,?,?,?,?,?,?,?,?)""",(_id('wlt'),owner,tid,amount,CURRENCY,kind,reference,detail[:1000],now))
        return True

    def _debit_tx(self,db,owner:str,telegram_id:int,amount:int,kind:str,reference:str,detail:str='')->bool:
        amount=int(amount)
        if amount<0:raise PolicyError('Wallet debit is invalid')
        if db.execute('SELECT 1 FROM customer_wallet_ledger WHERE owner=? AND kind=? AND reference=?',
                      (owner,kind,reference)).fetchone():return False
        tid=int(telegram_id);now=time.time()
        row=db.execute('SELECT balance_minor,currency FROM customer_wallets WHERE owner=? AND telegram_id=?',
                       (owner,tid)).fetchone()
        balance=int(row['balance_minor']) if row else 0
        if balance<amount:raise PolicyError('Insufficient wallet balance')
        if not row:
            db.execute('INSERT INTO customer_wallets(owner,telegram_id,currency,balance_minor,updated_at) VALUES(?,?,?,?,?)',
                       (owner,tid,CURRENCY,0,now))
        db.execute('UPDATE customer_wallets SET balance_minor=balance_minor-?,updated_at=? WHERE owner=? AND telegram_id=?',
                   (amount,now,owner,tid))
        db.execute("""INSERT INTO customer_wallet_ledger(id,owner,telegram_id,delta_minor,currency,kind,reference,detail,created_at)
          VALUES(?,?,?,?,?,?,?,?,?)""",(_id('wlt'),owner,tid,-amount,CURRENCY,kind,reference,detail[:1000],now))
        return True

    def create_topup(self,owner:str,telegram_id:int,username:str,amount_minor:int)->dict[str,Any]:
        amount=int(amount_minor)
        if not 1000<=amount<=10**12:raise PolicyError('Top-up amount is outside the allowed range')
        now=time.time();topup_id=_id('top')
        with self.store.transaction() as db:
            db.execute("""INSERT INTO customer_topups(id,owner,telegram_id,username,amount_minor,currency,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?)""",(topup_id,owner,int(telegram_id),str(username or '')[:128],amount,CURRENCY,
                                            'awaiting_receipt',now,now))
        return self.topup(topup_id,owner)

    def topup(self,topup_id:str,owner:str)->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute('SELECT rowid AS row_id,* FROM customer_topups WHERE id=? AND owner=?',
                                      (topup_id,owner)).fetchone()
        if not row:raise PolicyError('Wallet top-up not found')
        return dict(row)

    def latest_waiting_topup(self,owner:str,telegram_id:int)->dict[str,Any]|None:
        with self.store.lock:
            row=self.store.db.execute("""SELECT rowid AS row_id,* FROM customer_topups
              WHERE owner=? AND telegram_id=? AND status='awaiting_receipt'
              ORDER BY created_at DESC LIMIT 1""",(owner,int(telegram_id))).fetchone()
        return dict(row) if row else None

    def record_topup_receipt(self,owner:str,telegram_id:int,reference:str)->dict[str,Any]:
        topup=self.latest_waiting_topup(owner,telegram_id)
        if not topup:raise PolicyError('No wallet top-up is waiting for a receipt')
        with self.store.transaction() as db:
            db.execute("UPDATE customer_topups SET status='review',receipt_ref=?,updated_at=? WHERE id=?",
                       (str(reference)[:1024],time.time(),topup['id']))
        return self.topup(topup['id'],owner)

    def topup_by_rowid(self,owner:str,row_id:int)->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute('SELECT rowid AS row_id,* FROM customer_topups WHERE rowid=? AND owner=?',
                                      (int(row_id),owner)).fetchone()
        if not row:raise PolicyError('Wallet top-up not found')
        return dict(row)

    def approve_topup(self,owner:str,row_id:int)->dict[str,Any]:
        now=time.time()
        with self.store.transaction() as db:
            row=db.execute('SELECT rowid AS row_id,* FROM customer_topups WHERE rowid=? AND owner=?',
                           (int(row_id),owner)).fetchone()
            if not row:raise PolicyError('Wallet top-up not found')
            if row['status']=='approved':
                return dict(row)
            if row['status']!='review':raise PolicyError('Top-up is not awaiting approval')
            self._credit_tx(db,owner,int(row['telegram_id']),int(row['amount_minor']),'topup',row['id'],'manual receipt approved')
            db.execute("UPDATE customer_topups SET status='approved',updated_at=? WHERE id=?",(now,row['id']))
            row=db.execute('SELECT rowid AS row_id,* FROM customer_topups WHERE id=?',(row['id'],)).fetchone()
        return dict(row)

    def reject_topup(self,owner:str,row_id:int)->dict[str,Any]:
        row=self.topup_by_rowid(owner,row_id)
        if row['status'] not in ('review','awaiting_receipt'):raise PolicyError('Top-up cannot be rejected')
        with self.store.transaction() as db:
            db.execute("UPDATE customer_topups SET status='rejected',updated_at=? WHERE id=?",(time.time(),row['id']))
        return self.topup(row['id'],owner)

    def pay_purchase(self,owner:str,order_id:str)->dict[str,Any]:
        order=self.commerce.order(order_id,owner)
        if order.get('order_type','purchase')!='purchase':raise PolicyError('Order is not a purchase')
        if str(order['currency']).upper()!=CURRENCY:raise PolicyError('Wallet currently supports IRT products only')
        reference='order:'+order_id
        if order['status'] in ('provisioned','provisioned_waiting_activation'):
            result=self.commerce.provision_order(order_id,self.manager)
            if result.get('provisioned'):self.qualify_referral(owner,int(order['buyer_telegram_id']))
            return {'id':order_id,'status':order['status'],'wallet_paid':True}|result
        with self.store.transaction() as db:
            current=db.execute('SELECT * FROM commerce_orders WHERE id=? AND owner=?',(order_id,owner)).fetchone()
            if not current:raise PolicyError('Order not found')
            if current['status'] not in ('pending','awaiting_payment','payment_rejected','paid'):
                raise PolicyError('Order cannot be paid from current state')
            self._debit_tx(db,owner,int(current['buyer_telegram_id']),int(current['amount_minor']),'purchase',reference,order_id)
            now=time.time()
            payment=db.execute("SELECT id FROM commerce_payments WHERE order_id=? AND owner=? AND gateway_id='wallet' ORDER BY created_at DESC LIMIT 1",
                               (order_id,owner)).fetchone()
            if not payment:
                db.execute("""INSERT INTO commerce_payments(id,order_id,owner,gateway_id,amount_minor,currency,status,external_ref,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",(_id('pay'),order_id,owner,'wallet',int(current['amount_minor']),CURRENCY,
                                                 'paid',reference,now,now))
            db.execute("UPDATE commerce_orders SET status='paid',gateway_id='wallet',payment_ref=?,updated_at=? WHERE id=?",
                       (reference,now,order_id))
        result=self.commerce.provision_order(order_id,self.manager)
        if result.get('provisioned'):self.qualify_referral(owner,int(order['buyer_telegram_id']))
        return {'id':order_id,'status':self.commerce.order(order_id,owner)['status'],'wallet_paid':True}|result

    def latest_service_order(self,owner:str,telegram_id:int,client_id:str)->dict[str,Any]|None:
        with self.store.lock:
            row=self.store.db.execute("""SELECT * FROM commerce_orders WHERE owner=? AND buyer_telegram_id=?
              AND client_id=? AND status IN ('provisioned','provisioned_waiting_activation')
              ORDER BY updated_at DESC LIMIT 1""",(owner,int(telegram_id),client_id)).fetchone()
        return dict(row) if row else None

    def renewal_prices(self,owner:str,telegram_id:int,client_id:str)->dict[str,Any]:
        origin=self.latest_service_order(owner,telegram_id,client_id)
        if not origin:raise PolicyError('Service has no DARK purchase history')
        with self.store.lock:
            product=self.store.db.execute('SELECT * FROM commerce_products WHERE owner=? AND id=?',(owner,origin['product_id'])).fetchone()
            prices=[dict(r) for r in self.store.db.execute("""SELECT rowid AS row_id,* FROM commerce_prices
              WHERE owner=? AND product_id=? AND active=1 ORDER BY price_minor,id""",(owner,origin['product_id']))]
        if not product or not product['renewal_enabled']:raise PolicyError('Renewal is disabled for this product')
        return {'origin':origin,'product':dict(product),'prices':prices}

    def create_renewal_order(self,owner:str,telegram_id:int,username:str,client_id:str,price_id:str)->dict[str,Any]:
        data=self.renewal_prices(owner,telegram_id,client_id)
        price=next((x for x in data['prices'] if x['id']==price_id),None)
        if not price:raise PolicyError('Renewal price is not available')
        actor=self.commerce.actor_for(owner)
        detail=self.manager.detail(actor,client_id,credentials=True);client=detail.get('client') or {}
        if int(client.get('tgId') or 0)!=int(telegram_id):raise PolicyError('Service does not belong to this Telegram user')
        with self.store.lock:
            waiting=self.store.db.execute("""SELECT 1 FROM commerce_orders WHERE owner=? AND client_id=?
              AND status='provisioned_waiting_activation' LIMIT 1""",(owner,client_id)).fetchone()
        if waiting:raise PolicyError('Service is waiting for first connection activation')
        current_expiry=int(client.get('expiryTime') or 0)
        target_expiry=max(int(time.time()*1000),current_expiry)+max(1,int(price['duration_days']))*86400*1000
        now=time.time();order_id=_id('ord');inbounds=json.loads(price['inbound_ids'])
        with self.store.transaction() as db:
            db.execute("""INSERT INTO commerce_orders(id,owner,buyer_telegram_id,buyer_username,product_id,price_id,
              amount_minor,currency,status,created_at,updated_at,volume_bytes,duration_days,ip_limit,hwid_limit,
              inbound_ids,activation_mode,delivery_mode,primary_inbound_id,show_qr,show_portal,order_type,target_client_id,
              renewal_target_expiry)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (order_id,owner,int(telegram_id),str(username or '')[:128],data['product']['id'],price_id,
               int(price['price_minor']),price['currency'],'pending',now,now,int(price['volume_bytes']),
               int(price['duration_days']),int(price['ip_limit']),int(price['hwid_limit']),json.dumps(inbounds),
               'immediate',price['delivery_mode'],int(price['primary_inbound_id'] or 0),int(price['show_qr']),
               int(price['show_portal']),'renewal',client_id,target_expiry))
        return self.commerce.order(order_id,owner)

    def pay_renewal(self,owner:str,order_id:str)->dict[str,Any]:
        order=self.commerce.order(order_id,owner)
        if order.get('order_type')!='renewal':raise PolicyError('Order is not a renewal')
        if str(order['currency']).upper()!=CURRENCY:raise PolicyError('Wallet currently supports IRT products only')
        target=str(order.get('target_client_id') or '')
        if not target:raise PolicyError('Renewal target is missing')
        reference='renewal:'+order_id
        with self.store.transaction() as db:
            current=db.execute('SELECT * FROM commerce_orders WHERE id=? AND owner=?',(order_id,owner)).fetchone()
            if not current:raise PolicyError('Renewal order not found')
            if current['status']=='renewed':return {'id':order_id,'status':'renewed','client_id':target,'wallet_paid':True}
            if current['status'] not in ('pending','paid'):raise PolicyError('Renewal cannot be paid from current state')
            self._debit_tx(db,owner,int(current['buyer_telegram_id']),int(current['amount_minor']),'renewal',reference,order_id)
            now=time.time()
            if not db.execute("SELECT 1 FROM commerce_payments WHERE order_id=? AND owner=? AND gateway_id='wallet'",(order_id,owner)).fetchone():
                db.execute("""INSERT INTO commerce_payments(id,order_id,owner,gateway_id,amount_minor,currency,status,external_ref,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",(_id('pay'),order_id,owner,'wallet',int(current['amount_minor']),CURRENCY,
                                                 'paid',reference,now,now))
            db.execute("UPDATE commerce_orders SET status='paid',gateway_id='wallet',payment_ref=?,updated_at=? WHERE id=?",
                       (reference,now,order_id))
        try:
            actor=self.commerce.actor_for(owner)
            detail=self.manager.detail(actor,target,credentials=True)
            client=detail.get('client') or {}
            if int(client.get('tgId') or 0)!=int(order['buyer_telegram_id']):raise PolicyError('Service does not belong to this Telegram user')
            with self.store.lock:
                waiting=self.store.db.execute("""SELECT 1 FROM commerce_orders WHERE owner=? AND client_id=?
                  AND status='provisioned_waiting_activation' LIMIT 1""",(owner,target)).fetchone()
            if waiting:raise PolicyError('Service is waiting for first connection activation')
            target_expiry=int(order.get('renewal_target_expiry') or 0)
            if target_expiry<=0:
                current_expiry=int(client.get('expiryTime') or 0)
                target_expiry=max(int(time.time()*1000),current_expiry)+max(1,int(order['duration_days']))*86400*1000
            patch={'expiryTime':target_expiry,'totalGB':int(order['volume_bytes']),
                   'limitIp':int(order['ip_limit']),'limitHwid':int(order['hwid_limit'])}
            ids=[int(x) for x in json.loads(order['inbound_ids'])]
            self.manager.update(actor,target,patch,ids=ids)
            self.manager.action(actor,target,'reset')
            with self.store.transaction() as db:
                db.execute("UPDATE commerce_orders SET status='renewed',client_id=?,fulfillment_error='',updated_at=? WHERE id=?",
                           (target,time.time(),order_id))
            return {'id':order_id,'status':'renewed','client_id':target,'wallet_paid':True,
                    'client':self.manager.detail(actor,target,credentials=True)}
        except Exception as ex:
            with self.store.transaction() as db:
                db.execute("UPDATE commerce_orders SET fulfillment_error=?,updated_at=? WHERE id=?",
                           (str(ex)[:1000],time.time(),order_id))
            raise

    def ensure_referral_profile(self,owner:str,telegram_id:int)->dict[str,Any]:
        tid=int(telegram_id)
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM customer_referrals WHERE owner=? AND telegram_id=?',(owner,tid)).fetchone()
            if not row:
                while True:
                    code=secrets.token_urlsafe(6).replace('-','').replace('_','')[:10]
                    if not db.execute('SELECT 1 FROM customer_referrals WHERE owner=? AND code=?',(owner,code)).fetchone():break
                db.execute("""INSERT INTO customer_referrals(owner,telegram_id,code,referrer_telegram_id,referred_at,qualified_at,reward_minor)
                  VALUES(?,?,?,?,?,?,?)""",(owner,tid,code,0,0,0,0))
                row=db.execute('SELECT * FROM customer_referrals WHERE owner=? AND telegram_id=?',(owner,tid)).fetchone()
        return dict(row)

    def register_referral(self,owner:str,telegram_id:int,code:str)->bool:
        tid=int(telegram_id);self.ensure_referral_profile(owner,tid)
        with self.store.transaction() as db:
            me=db.execute('SELECT * FROM customer_referrals WHERE owner=? AND telegram_id=?',(owner,tid)).fetchone()
            if int(me['referrer_telegram_id'] or 0):return False
            ref=db.execute('SELECT * FROM customer_referrals WHERE owner=? AND code=?',(owner,str(code))).fetchone()
            if not ref or int(ref['telegram_id'])==tid:return False
            db.execute('UPDATE customer_referrals SET referrer_telegram_id=?,referred_at=? WHERE owner=? AND telegram_id=?',
                       (int(ref['telegram_id']),time.time(),owner,tid))
        return True

    def referral_stats(self,owner:str,telegram_id:int)->dict[str,Any]:
        me=self.ensure_referral_profile(owner,telegram_id)
        with self.store.lock:
            invited=int(self.store.db.execute('SELECT COUNT(*) FROM customer_referrals WHERE owner=? AND referrer_telegram_id=?',
                                              (owner,int(telegram_id))).fetchone()[0])
            qualified=int(self.store.db.execute("""SELECT COUNT(*) FROM customer_referrals
              WHERE owner=? AND referrer_telegram_id=? AND qualified_at>0""",(owner,int(telegram_id))).fetchone()[0])
            earned=int(self.store.db.execute("""SELECT COALESCE(SUM(reward_minor),0) FROM customer_referrals
              WHERE owner=? AND referrer_telegram_id=?""",(owner,int(telegram_id))).fetchone()[0])
        return {'code':me['code'],'invited':invited,'qualified':qualified,'earned':earned,
                'reward_minor':int(self.settings(owner)['referral_reward_minor'])}

    def qualify_referral(self,owner:str,telegram_id:int)->bool:
        reward=int(self.settings(owner)['referral_reward_minor'])
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM customer_referrals WHERE owner=? AND telegram_id=?',(owner,int(telegram_id))).fetchone()
            if not row or not int(row['referrer_telegram_id'] or 0) or float(row['qualified_at'] or 0)>0:return False
            referrer=int(row['referrer_telegram_id']);now=time.time()
            if reward>0:self._credit_tx(db,owner,referrer,reward,'referral','ref:'+str(int(telegram_id)),'first successful purchase')
            db.execute('UPDATE customer_referrals SET qualified_at=?,reward_minor=? WHERE owner=? AND telegram_id=?',
                       (now,reward,owner,int(telegram_id)))
        return True

    def create_ticket(self,owner:str,telegram_id:int,username:str,subject:str)->dict[str,Any]:
        title=str(subject or '').strip()
        if not 1<=len(title)<=128:raise PolicyError('Ticket subject is invalid')
        ticket_id=_id('tkt');now=time.time()
        with self.store.transaction() as db:
            db.execute("""INSERT INTO customer_support_tickets(id,owner,telegram_id,username,subject,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?)""",(ticket_id,owner,int(telegram_id),str(username or '')[:128],title,'open',now,now))
        return self.ticket(ticket_id,owner)

    def ticket(self,ticket_id:str,owner:str)->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute('SELECT rowid AS row_id,* FROM customer_support_tickets WHERE id=? AND owner=?',
                                      (ticket_id,owner)).fetchone()
        if not row:raise PolicyError('Support ticket not found')
        return dict(row)

    def ticket_by_rowid(self,owner:str,row_id:int)->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute('SELECT rowid AS row_id,* FROM customer_support_tickets WHERE rowid=? AND owner=?',
                                      (int(row_id),owner)).fetchone()
        if not row:raise PolicyError('Support ticket not found')
        return dict(row)

    def tickets_for_customer(self,owner:str,telegram_id:int,limit:int=20)->list[dict[str,Any]]:
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute("""SELECT rowid AS row_id,* FROM customer_support_tickets
              WHERE owner=? AND telegram_id=? ORDER BY updated_at DESC LIMIT ?""",
              (owner,int(telegram_id),max(1,min(int(limit),100))))]

    def open_tickets(self,owner:str,limit:int=30)->list[dict[str,Any]]:
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute("""SELECT rowid AS row_id,* FROM customer_support_tickets
              WHERE owner=? AND status<>'closed' ORDER BY updated_at DESC LIMIT ?""",
              (owner,max(1,min(int(limit),100))))]

    def add_ticket_message(self,owner:str,ticket_id:str,sender_type:str,sender_telegram_id:int,
                           text:str='',file_kind:str='',file_id:str='')->dict[str,Any]:
        if sender_type not in ('customer','admin'):raise PolicyError('Invalid support sender')
        ticket=self.ticket(ticket_id,owner)
        body=str(text or '').strip()[:4000];kind=str(file_kind or '')[:32];fid=str(file_id or '')[:512]
        if not body and not fid:raise PolicyError('Support message is empty')
        now=time.time();msg_id=_id('msg')
        status='answered' if sender_type=='admin' else 'open'
        with self.store.transaction() as db:
            db.execute("""INSERT INTO customer_support_messages(id,ticket_id,owner,sender_type,sender_telegram_id,text,file_kind,file_id,created_at)
              VALUES(?,?,?,?,?,?,?,?,?)""",(msg_id,ticket_id,owner,sender_type,int(sender_telegram_id),body,kind,fid,now))
            db.execute('UPDATE customer_support_tickets SET status=?,updated_at=? WHERE id=?',(status,now,ticket_id))
        return {'id':msg_id,'ticket':ticket,'text':body,'file_kind':kind,'file_id':fid,'sender_type':sender_type}

    def ticket_messages(self,owner:str,ticket_id:str,limit:int=30)->list[dict[str,Any]]:
        self.ticket(ticket_id,owner)
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute("""SELECT * FROM customer_support_messages
              WHERE owner=? AND ticket_id=? ORDER BY created_at DESC LIMIT ?""",
              (owner,ticket_id,max(1,min(int(limit),100))))][::-1]

    def close_ticket(self,owner:str,ticket_id:str)->dict[str,Any]:
        self.ticket(ticket_id,owner)
        with self.store.transaction() as db:
            db.execute("UPDATE customer_support_tickets SET status='closed',updated_at=? WHERE id=?",
                       (time.time(),ticket_id))
        return self.ticket(ticket_id,owner)
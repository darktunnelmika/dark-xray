from __future__ import annotations
from datetime import datetime,time as dt_time
from telegram_presentation import timezone
from telegram_reporting import SALES_CTE,ORDERS_CTE,sales_summary
import hashlib
import hmac
import json
import sqlite3
import string
import time
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, urlsplit

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from dark_policy import NAME_RE, PolicyError
from telegram_commerce import SimplePlanBody


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PaymentAction(Model):
    action: Literal['approve','reject']


class SupportContact(Model):
    support_url: str = Field(default='',max_length=500)

class SupportReply(Model):
    text: str = Field(min_length=1, max_length=4000)


class SupportMeta(Model):
    priority: Literal['low','normal','high','urgent'] = 'normal'
    assigned_to: str = Field(default='', max_length=128)


class QuickReplyBody(Model):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=2000)
    active: bool = True


class CryptoGatewayBody(Model):
    id: str = Field(default='crypto', min_length=1, max_length=64)
    label: str = Field(default='Crypto Gateway', min_length=1, max_length=128)
    enabled: bool = False
    checkout_url_template: str = Field(default='', max_length=2000)
    webhook_secret: str | None = Field(default=None, min_length=16, max_length=512)
    instructions: str = Field(default='', max_length=4000)


class TelegramOperations:
    SUCCESS_ORDER_STATES=('paid','provisioned','provisioned_waiting_activation','renewed')
    REVIEW_PAYMENT_STATES=('review',)
    MINIAPP_MAX_AGE=900

    def __init__(self,runtime):
        self.runtime=runtime
        self.store=runtime.store
        self.commerce=runtime.commerce
        self.customer=runtime.customer
        self.manager=runtime.manager
        with self.store.lock:
            self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS commerce_gateway_events(
              owner TEXT NOT NULL,gateway_id TEXT NOT NULL,event_id TEXT NOT NULL,
              status TEXT NOT NULL,order_id TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,updated_at REAL NOT NULL,
              PRIMARY KEY(owner,gateway_id,event_id));
            CREATE TABLE IF NOT EXISTS customer_support_quick_replies(
              owner TEXT NOT NULL,id TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,updated_at REAL NOT NULL,
              PRIMARY KEY(owner,id));
            """)
            self._column('customer_support_tickets','priority',"TEXT NOT NULL DEFAULT 'normal'")
            self._column('customer_support_tickets','assigned_to',"TEXT NOT NULL DEFAULT ''")

    def _column(self,table:str,name:str,spec:str):
        columns={r[1] for r in self.store.db.execute(f'PRAGMA table_info({table})')}
        if name not in columns:self.store.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {spec}')

    def _panel_prefix(self)->str:
        p=str(self.manager.engine.config.panel_path or '/')
        return '' if p=='/' else p

    def public_url(self,path:str)->str:
        return self.manager.engine.config.public_origin.rstrip('/')+self._panel_prefix()+path

    def mini_app_url(self,owner:str)->str:
        return self.public_url('/assets/telegram-miniapp.html')+'?owner='+quote(str(owner),safe='')

    def customer_mini_app_url(self,owner:str)->str:
        url=self.public_url('/assets/telegram-customer.html')+'?owner='+quote(str(owner),safe='')
        try:
            row=self.commerce.bot_row(owner) or {}
            username=str(row.get('bot_username') or '').strip().lstrip('@')
        except Exception:
            username=''
        if username:url+='&bot='+quote(username,safe='')
        return url

    def webhook_url(self,owner:str,gateway_id:str)->str:
        return self.public_url('/api/telegram/crypto/webhook/'+quote(str(owner),safe='')+'/'+quote(str(gateway_id),safe=''))

    def _bot_secret(self,owner:str)->tuple[dict[str,Any],str]:
        row=self.commerce.bot_row(owner,secret=True)
        if not row or not row.get('bot_token'):raise PolicyError('Telegram bot token is not configured')
        return row,str(row['bot_token'])

    def verify_webapp_user(self,owner:str,init_data:str,admin_only:bool=False)->dict[str,Any]:
        raw=str(init_data or '')
        if not raw or len(raw)>16384:raise PolicyError('Telegram Mini App authorization is missing')
        try:pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
        except ValueError as ex:raise PolicyError('Telegram Mini App authorization is malformed') from ex
        values={}
        for key,value in pairs:
            if key in values:raise PolicyError('Duplicate Telegram Mini App field')
            values[key]=value
        supplied=values.pop('hash',None)
        if not supplied or len(supplied)!=64:raise PolicyError('Telegram Mini App hash is invalid')
        row,token=self._bot_secret(owner)
        check='\n'.join(f'{key}={values[key]}' for key in sorted(values))
        secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,supplied.lower()):raise PolicyError('Telegram Mini App signature is invalid')
        try:auth_date=int(values.get('auth_date','0'))
        except ValueError:raise PolicyError('Telegram Mini App auth_date is invalid')
        now=int(time.time())
        if auth_date>now+60 or now-auth_date>self.MINIAPP_MAX_AGE:raise PolicyError('Telegram Mini App authorization expired')
        try:user=json.loads(values.get('user','{}'))
        except (TypeError,ValueError):raise PolicyError('Telegram Mini App user is invalid')
        if not isinstance(user,dict) or type(user.get('id')) is not int:raise PolicyError('Telegram Mini App user is invalid')
        if int(user['id'])<=0:raise PolicyError('Telegram Mini App user is invalid')
        if admin_only and int(user['id'])!=int(row['admin_telegram_id']):
            raise PolicyError('Telegram Mini App admin does not match this bot')
        return {'owner':owner,'user':user,'auth_date':auth_date}

    def verify_mini_app(self,owner:str,init_data:str)->dict[str,Any]:
        return self.verify_webapp_user(owner,init_data,admin_only=True)

    @staticmethod
    def _midnight(now:float)->float:
        local=time.localtime(now)
        return time.mktime((local.tm_year,local.tm_mon,local.tm_mday,0,0,0,local.tm_wday,local.tm_yday,local.tm_isdst))

    def dashboard(self,owner:str)->dict[str,Any]:
        now=time.time();week=now-7*86400;month=now-30*86400
        tz=timezone(self.manager.engine.section('panel').get('timezone','UTC'))
        today=datetime.combine(datetime.fromtimestamp(now,tz).date(),dt_time.min,tzinfo=tz).timestamp()
        with self.store.lock:
            db=self.store.db
            orders_today=int(db.execute(ORDERS_CTE+"SELECT COUNT(*) FROM orders WHERE owner=? AND created_at>=? AND created_at<?",(owner,today,now+0.001)).fetchone()[0])
            day_sales=sales_summary(db,owner,today,now+0.001)
            paid_today=day_sales['count'];revenue_today=day_sales['amounts'].get('IRT',0)
            orders_week=int(db.execute(ORDERS_CTE+"SELECT COUNT(*) FROM orders WHERE owner=? AND created_at>=? AND created_at<?",(owner,week,now+0.001)).fetchone()[0])
            week_sales=sales_summary(db,owner,week,now+0.001)
            paid_week=week_sales['count'];revenue_week=week_sales['amounts'].get('IRT',0)
            converted_week=int(db.execute(SALES_CTE+"""SELECT COUNT(*) FROM sales s JOIN (
              SELECT id,owner,created_at FROM commerce_orders UNION ALL
              SELECT id,owner,created_at FROM representative_market_orders
              ) o ON o.id=s.id AND o.owner=s.owner
              WHERE s.owner=? AND o.created_at>=? AND o.created_at<? AND s.paid_at<?""",
              (owner,week,now+0.001,now+0.001)).fetchone()[0])
            pending_payments=int(db.execute("SELECT COUNT(*) FROM commerce_payments WHERE owner=? AND status='review'",(owner,)).fetchone()[0])
            pending_topups=int(db.execute("SELECT COUNT(*) FROM customer_topups WHERE owner=? AND status='review'",(owner,)).fetchone()[0])
            wallet_liability=int(db.execute("SELECT COALESCE(SUM(balance_minor),0) FROM customer_wallets WHERE owner=?",(owner,)).fetchone()[0])
            products=int(db.execute("SELECT COUNT(*) FROM commerce_products WHERE owner=? AND active=1 AND visible=1",(owner,)).fetchone()[0])
            customers=int(db.execute(SALES_CTE+"SELECT COUNT(DISTINCT buyer_telegram_id) FROM sales WHERE owner=?",(owner,)).fetchone()[0])
            support_open=int(db.execute("SELECT COUNT(*) FROM customer_support_tickets WHERE owner=? AND status<>'closed'",(owner,)).fetchone()[0])
            support_urgent=int(db.execute("SELECT COUNT(*) FROM customer_support_tickets WHERE owner=? AND status<>'closed' AND priority='urgent'",(owner,)).fetchone()[0])
            top=[dict(r) for r in db.execute(SALES_CTE+"""SELECT p.id,p.name,COUNT(o.id) sales,COALESCE(SUM(o.amount_minor),0) revenue_minor
              FROM commerce_products p LEFT JOIN sales o ON o.owner=p.owner AND o.product_id=p.id AND o.kind='service'
                AND o.paid_at>=? AND o.paid_at<? AND o.currency='IRT'
              WHERE p.owner=? GROUP BY p.id,p.name ORDER BY revenue_minor DESC,sales DESC,p.name LIMIT 5""",
              (month,now+0.001,owner))]
            role=self.commerce.actor_for(owner).role
            reps=int(db.execute("SELECT COUNT(*) FROM api_admins WHERE role='reseller' AND disabled=0").fetchone()[0]) if role=='owner' else 0
        return {'unlimited_plan_allowed':self.commerce.unlimited_plan_allowed(owner),'owner':owner,'role':role,'orders_today':orders_today,'paid_today':paid_today,'revenue_today':revenue_today,
                'orders_7d':orders_week,'paid_7d':paid_week,'revenue_7d':revenue_week,
                'conversion_7d':round((converted_week/orders_week*100.0) if orders_week else 0.0,1),
                'pending_payments':pending_payments+pending_topups,'wallet_liability':wallet_liability,
                'published_plans':products,'customers':customers,'support_open':support_open,
                'support_urgent':support_urgent,'representatives':reps,'top_products':top,'currency':'IRT',
                'revenue_today_by_currency':day_sales['amounts'],'revenue_7d_by_currency':week_sales['amounts'],
                'mini_app_url':self.mini_app_url(owner)}

    def inbound_catalog(self,owner:str)->list[dict[str,Any]]:
        actor=self.commerce.actor_for(owner)
        allowed=None
        if actor.role=='reseller':
            profile=self.manager.profile(owner);allowed={int(x) for x in (profile.get('allowed') or [])}
        out=[]
        with self.store.lock:
            rows=list(self.store.db.execute("SELECT id,body FROM core_inbounds ORDER BY id"))
        for row in rows:
            inbound_id=int(row['id'])
            if allowed is not None and inbound_id not in allowed:continue
            try:body=json.loads(row['body'])
            except Exception:body={}
            out.append({'id':inbound_id,'name':str(body.get('remark') or body.get('tag') or ('Inbound '+str(inbound_id))),
                        'port':int(body.get('port') or 0),'protocol':str(body.get('protocol') or '')})
        return out

    def payment_rows(self,owner:str,limit:int=100)->list[dict[str,Any]]:
        limit=max(1,min(int(limit),200));rows=[]
        with self.store.lock:
            db=self.store.db
            for r in db.execute("""SELECT p.rowid AS row_id,p.id,p.order_id,p.gateway_id,p.amount_minor,p.currency,p.status,
              p.external_ref,p.created_at,p.updated_at,o.buyer_telegram_id,o.buyer_username,o.product_id,o.client_id
              FROM commerce_payments p JOIN commerce_orders o ON o.id=p.order_id
              WHERE p.owner=? ORDER BY p.updated_at DESC LIMIT ?""",(owner,limit)):
                x=dict(r);x['kind']='order';x['reviewable']=x['status']=='review';rows.append(x)
            for r in db.execute("""SELECT rowid AS row_id,id,telegram_id AS buyer_telegram_id,username AS buyer_username,
              amount_minor,currency,status,receipt_ref AS external_ref,created_at,updated_at
              FROM customer_topups WHERE owner=? ORDER BY updated_at DESC LIMIT ?""",(owner,limit)):
                x=dict(r);x['kind']='topup';x['gateway_id']='card';x['order_id']='';x['product_id']='wallet'
                x['client_id']='';x['reviewable']=x['status']=='review';rows.append(x)
        rows.sort(key=lambda x:float(x.get('updated_at') or 0),reverse=True)
        return rows[:limit]

    def _worker(self,owner:str):
        with self.runtime.lock:return self.runtime.workers.get(owner)

    def _notify(self,owner:str,chat_id:int,text:str):
        worker=self._worker(owner)
        if not worker:return
        try:worker.api.send(int(chat_id),text)
        except Exception:pass

    def payment_action(self,owner:str,kind:str,row_id:int,action:str)->dict[str,Any]:
        if kind not in ('order','topup') or action not in ('approve','reject'):raise PolicyError('Invalid payment action')
        if kind=='order':
            if action=='approve':
                result=self.commerce.approve_payment(owner,row_id,self.manager)
                order=self.commerce.order(result['id'],owner)
                worker=self._worker(owner)
                if worker and result.get('provisioned'):
                    try:worker.send_delivery(int(order['buyer_telegram_id']),result)
                    except Exception:pass
                elif worker:
                    self._notify(owner,int(order['buyer_telegram_id']),'پرداخت شما تأیید شد و سفارش در حال تکمیل است.')
                return {'kind':kind,'action':action,**result}
            result=self.commerce.reject_payment(owner,row_id)
            self._notify(owner,int(result['buyer_telegram_id']),'رسید پرداخت تأیید نشد. می‌توانید رسید جدید ارسال کنید.')
            return {'kind':kind,'action':action,**result}
        if action=='approve':
            result=self.customer.approve_topup(owner,row_id)
            self._notify(owner,int(result['telegram_id']),'✅ شارژ کیف پول شما تأیید شد.')
            return {'kind':kind,'action':action,'topup':result}
        result=self.customer.reject_topup(owner,row_id)
        self._notify(owner,int(result['telegram_id']),'❌ رسید شارژ کیف پول تأیید نشد.')
        return {'kind':kind,'action':action,'topup':result}

    def support_rows(self,owner:str,limit:int=100)->list[dict[str,Any]]:
        limit=max(1,min(int(limit),200))
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("""SELECT rowid AS row_id,* FROM customer_support_tickets
              WHERE owner=? ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
              updated_at DESC LIMIT ?""",(owner,limit))]
            for row in rows:
                meta=self.store.db.execute("""SELECT COUNT(*) c,MAX(created_at) last_at,
                  (SELECT sender_type FROM customer_support_messages m2 WHERE m2.ticket_id=? AND m2.owner=? ORDER BY created_at DESC LIMIT 1) last_sender
                  FROM customer_support_messages WHERE ticket_id=? AND owner=?""",
                  (row['id'],owner,row['id'],owner)).fetchone()
                row['message_count']=int(meta['c'] or 0);row['last_message_at']=float(meta['last_at'] or 0)
                row['last_sender']=str(meta['last_sender'] or '')
        return rows

    def support_detail(self,owner:str,row_id:int)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id)
        return {'ticket':ticket,'messages':self.customer.ticket_messages(owner,ticket['id'],100)}

    def support_reply(self,owner:str,row_id:int,text:str)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id)
        bot=self.commerce.bot_row(owner) or {}
        msg=self.customer.add_ticket_message(owner,ticket['id'],'admin',int(bot.get('admin_telegram_id') or 0),text=text)
        self._notify(owner,int(ticket['telegram_id']),'🎫 پاسخ پشتیبانی\n'+str(text)[:3500])
        return {'ticket':self.customer.ticket(ticket['id'],owner),'message':msg}

    def support_close(self,owner:str,row_id:int)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id);result=self.customer.close_ticket(owner,ticket['id'])
        self._notify(owner,int(ticket['telegram_id']),'✅ تیکت پشتیبانی شما بسته شد.')
        return result

    def support_meta(self,owner:str,row_id:int,priority:str,assigned_to:str)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id)
        if priority not in ('low','normal','high','urgent'):raise PolicyError('Invalid support priority')
        with self.store.transaction() as db:
            db.execute("UPDATE customer_support_tickets SET priority=?,assigned_to=?,updated_at=? WHERE id=? AND owner=?",
                       (priority,str(assigned_to or '')[:128],time.time(),ticket['id'],owner))
        return self.customer.ticket(ticket['id'],owner)

    def quick_replies(self,owner:str)->list[dict[str,Any]]:
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("""SELECT * FROM customer_support_quick_replies
              WHERE owner=? ORDER BY active DESC,title,id""",(owner,))]
        for row in rows:row['active']=bool(row['active'])
        return rows

    def save_quick_reply(self,owner:str,body:QuickReplyBody)->dict[str,Any]:
        if not NAME_RE.fullmatch(body.id):raise PolicyError('Invalid quick reply ID')
        now=time.time()
        with self.store.transaction() as db:
            old=db.execute("SELECT created_at FROM customer_support_quick_replies WHERE owner=? AND id=?",(owner,body.id)).fetchone()
            created=float(old['created_at']) if old else now
            db.execute("""INSERT INTO customer_support_quick_replies(owner,id,title,body,active,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET title=excluded.title,body=excluded.body,
              active=excluded.active,updated_at=excluded.updated_at""",
              (owner,body.id,body.title,body.body,int(body.active),created,now))
        return next(x for x in self.quick_replies(owner) if x['id']==body.id)

    def delete_quick_reply(self,owner:str,reply_id:str)->dict[str,Any]:
        with self.store.transaction() as db:
            changed=db.execute("DELETE FROM customer_support_quick_replies WHERE owner=? AND id=?",(owner,reply_id)).rowcount
        if not changed:raise PolicyError('Quick reply not found')
        return {'deleted':True,'id':reply_id}

    @staticmethod
    def _validate_checkout_template(template:str)->str:
        value=str(template or '').strip()
        if not value:raise PolicyError('Crypto checkout URL template is required')
        allowed={'amount','currency','order_id','payment_id'}
        try:
            fields={name for _,name,_,_ in string.Formatter().parse(value) if name}
        except ValueError as ex:raise PolicyError('Invalid crypto checkout URL template') from ex
        if not fields<=allowed:raise PolicyError('Unsupported crypto checkout URL placeholder')
        sample=value.format(amount='1000',currency='IRT',order_id='ord_test',payment_id='pay_test')
        parsed=urlsplit(sample)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
            raise PolicyError('Crypto checkout URL must be a public HTTPS URL')
        host=(parsed.hostname or '').lower()
        if host in ('localhost','127.0.0.1','::1') or host.endswith('.local'):
            raise PolicyError('Crypto checkout URL must not target a local host')
        return value

    def crypto_gateway(self,owner:str,gateway_id:str='crypto')->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute("SELECT * FROM commerce_gateways WHERE owner=? AND id=? AND kind='plugin'",(owner,gateway_id)).fetchone()
        if not row:return {'id':gateway_id,'label':'Crypto Gateway','enabled':False,'configured':False,
                           'checkout_url_template':'','webhook_url':self.webhook_url(owner,gateway_id),'instructions':''}
        out=dict(row);cfg={}
        if out.get('secret_enc'):
            try:cfg=json.loads(self.commerce._open(out['secret_enc']))
            except Exception:cfg={}
        return {'id':out['id'],'label':out['label'],'enabled':bool(out['enabled']),
                'configured':bool(cfg.get('checkout_url_template') and cfg.get('webhook_secret')),
                'checkout_url_template':str(cfg.get('checkout_url_template') or ''),
                'webhook_url':self.webhook_url(owner,out['id']),'instructions':out.get('instructions') or ''}

    def save_crypto_gateway(self,owner:str,body:CryptoGatewayBody)->dict[str,Any]:
        if not NAME_RE.fullmatch(body.id):raise PolicyError('Invalid crypto gateway ID')
        template=self._validate_checkout_template(body.checkout_url_template)
        with self.store.lock:
            old=self.store.db.execute("SELECT secret_enc FROM commerce_gateways WHERE owner=? AND id=?",(owner,body.id)).fetchone()
        old_cfg={}
        if old and old['secret_enc']:
            try:old_cfg=json.loads(self.commerce._open(old['secret_enc']))
            except Exception:old_cfg={}
        secret=str(body.webhook_secret or old_cfg.get('webhook_secret') or '')
        if len(secret)<16:raise PolicyError('Crypto webhook secret must be at least 16 characters')
        cfg={'checkout_url_template':template,'webhook_secret':secret}
        now=time.time()
        with self.store.transaction() as db:
            db.execute("""INSERT INTO commerce_gateways(id,owner,label,kind,enabled,instructions,plugin,secret_enc,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET label=excluded.label,kind='plugin',
              enabled=excluded.enabled,instructions=excluded.instructions,plugin=excluded.plugin,
              secret_enc=excluded.secret_enc,updated_at=excluded.updated_at""",
              (body.id,owner,body.label,'plugin',int(body.enabled),body.instructions,'crypto-hosted',
               self.commerce._seal(json.dumps(cfg,separators=(',',':'))),now))
        return self.crypto_gateway(owner,body.id)

    def _crypto_secret(self,owner:str,gateway_id:str)->tuple[dict[str,Any],str]:
        with self.store.lock:
            row=self.store.db.execute("SELECT * FROM commerce_gateways WHERE owner=? AND id=? AND kind='plugin' AND enabled=1",
                                      (owner,gateway_id)).fetchone()
        if not row:raise PolicyError('Crypto gateway not found or disabled')
        try:cfg=json.loads(self.commerce._open(row['secret_enc']))
        except Exception as ex:raise PolicyError('Crypto gateway configuration is invalid') from ex
        secret=str(cfg.get('webhook_secret') or '')
        if len(secret)<16:raise PolicyError('Crypto gateway webhook secret is not configured')
        return dict(row),secret

    def crypto_webhook(self,owner:str,gateway_id:str,raw:bytes,signature:str)->dict[str,Any]:
        _row,secret=self._crypto_secret(owner,gateway_id)
        supplied=str(signature or '').strip()
        if supplied.startswith('sha256='):supplied=supplied[7:]
        expected=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
        if len(supplied)!=64 or not hmac.compare_digest(supplied.lower(),expected):
            raise PolicyError('Crypto webhook signature is invalid')
        try:doc=json.loads(raw)
        except (TypeError,ValueError) as ex:raise PolicyError('Crypto webhook JSON is invalid') from ex
        if not isinstance(doc,dict):raise PolicyError('Crypto webhook payload must be an object')
        event_id=str(doc.get('event_id') or '')[:160];order_id=str(doc.get('order_id') or '')[:160]
        status=str(doc.get('status') or '').lower();reference=str(doc.get('reference') or event_id)[:512]
        if not event_id or not order_id or status not in ('paid','failed','expired'):
            raise PolicyError('Crypto webhook fields are invalid')
        try:amount=int(doc.get('amount_minor'));currency=str(doc.get('currency') or '').upper()
        except (TypeError,ValueError):raise PolicyError('Crypto webhook amount is invalid')
        order=self.commerce.order(order_id,owner)
        if order.get('gateway_id')!=gateway_id:raise PolicyError('Crypto webhook gateway does not match order')
        if amount!=int(order['amount_minor']) or currency!=str(order['currency']).upper():
            raise PolicyError('Crypto webhook amount or currency does not match order')
        now=time.time()
        try:
            with self.store.transaction() as db:
                db.execute("""INSERT INTO commerce_gateway_events(owner,gateway_id,event_id,status,order_id,detail,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?)""",(owner,gateway_id,event_id,'processing',order_id,status,now,now))
        except sqlite3.IntegrityError:
            return {'ok':True,'duplicate':True,'event_id':event_id}
        try:
            if status=='paid':
                result=self.commerce.confirm_payment(owner,order_id,reference,self.manager)
                worker=self._worker(owner)
                if worker and result.get('provisioned'):
                    try:worker.send_delivery(int(order['buyer_telegram_id']),result)
                    except Exception:pass
            else:
                terminal='payment_rejected' if status=='failed' else 'cancelled'
                with self.store.transaction() as db:
                    db.execute("""UPDATE commerce_payments SET status=?,external_ref=?,updated_at=?
                      WHERE id=(SELECT id FROM commerce_payments WHERE order_id=? AND owner=? ORDER BY created_at DESC LIMIT 1)
                      AND status<>'paid'""",(status,reference,now,order_id,owner))
                    db.execute("""UPDATE commerce_orders SET status=?,payment_ref=?,updated_at=?
                      WHERE id=? AND owner=? AND status IN ('pending','awaiting_payment','payment_review')""",
                      (terminal,reference,now,order_id,owner))
                self._notify(owner,int(order['buyer_telegram_id']),
                             '❌ پرداخت ناموفق بود.' if status=='failed' else '⌛ مهلت پرداخت این سفارش تمام شد.')
            with self.store.transaction() as db:
                db.execute("UPDATE commerce_gateway_events SET status='processed',detail=?,updated_at=? WHERE owner=? AND gateway_id=? AND event_id=?",
                           (status,time.time(),owner,gateway_id,event_id))
            return {'ok':True,'duplicate':False,'event_id':event_id,'status':status}
        except Exception:
            with self.store.transaction() as db:
                db.execute("DELETE FROM commerce_gateway_events WHERE owner=? AND gateway_id=? AND event_id=?",
                           (owner,gateway_id,event_id))
            raise

    def mini_bootstrap(self,owner:str,init_data:str)->dict[str,Any]:
        auth=self.verify_mini_app(owner,init_data)
        return {'auth':auth,'dashboard':self.dashboard(owner),'plans':self.commerce.product_rows(owner),
                'inbounds':self.inbound_catalog(owner),'payments':self.payment_rows(owner,80),
                'support':self.support_rows(owner,80),'quick_replies':self.quick_replies(owner),
                'support_url':self.runtime.customer.settings(owner)['support_url'],
                'crypto':self.crypto_gateway(owner)}


def install_telegram_ops(app,runtime,current,writable,audit):
    ops=TelegramOperations(runtime);runtime.ops=ops;app.state.telegram_ops=ops

    def panel_owner(p):
        return runtime.commerce.owner_for(p)

    def mini_owner(owner:str,init_data:str):
        ops.verify_mini_app(owner,init_data);return owner

    @app.get('/api/telegram/operations/dashboard')
    def operations_dashboard(p=Depends(current)):
        return ops.dashboard(panel_owner(p))

    @app.get('/api/telegram/operations/payments')
    def operations_payments(p=Depends(current)):
        return ops.payment_rows(panel_owner(p))

    @app.post('/api/telegram/operations/payments/{kind}/{row_id}/{action}')
    def operations_payment_action(kind:str,row_id:int,action:str,p=Depends(current)):
        writable();owner=panel_owner(p);result=ops.payment_action(owner,kind,row_id,action)
        audit(p.actor,owner,'telegram.payment_center',str(row_id),kind+':'+action);return result

    @app.get('/api/telegram/operations/support')
    def operations_support(p=Depends(current)):
        owner=panel_owner(p)
        return {'tickets':ops.support_rows(owner),'quick_replies':ops.quick_replies(owner),
                'support_url':runtime.customer.settings(owner)['support_url']}

    @app.put('/api/telegram/operations/support-contact')
    def operations_support_contact(body:SupportContact,p=Depends(current)):
        writable();owner=panel_owner(p)
        result=runtime.customer.set_support_contact(owner,body.support_url)
        audit(p.actor,owner,'telegram.support_contact',owner,'panel');return result

    @app.put('/api/telegram-miniapp/support-contact')
    def mini_support_contact(owner:str,body:SupportContact,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();mini_owner(owner,x_telegram_init_data)
        return runtime.customer.set_support_contact(owner,body.support_url)

    @app.get('/api/telegram/operations/support/{row_id}')
    def operations_support_detail(row_id:int,p=Depends(current)):
        return ops.support_detail(panel_owner(p),row_id)

    @app.post('/api/telegram/operations/support/{row_id}/reply')
    def operations_support_reply(row_id:int,body:SupportReply,p=Depends(current)):
        writable();owner=panel_owner(p);result=ops.support_reply(owner,row_id,body.text)
        audit(p.actor,owner,'telegram.support_reply',str(row_id),'panel');return result

    @app.post('/api/telegram/operations/support/{row_id}/close')
    def operations_support_close(row_id:int,p=Depends(current)):
        writable();owner=panel_owner(p);result=ops.support_close(owner,row_id)
        audit(p.actor,owner,'telegram.support_close',str(row_id),'panel');return result

    @app.put('/api/telegram/operations/support/{row_id}/meta')
    def operations_support_meta(row_id:int,body:SupportMeta,p=Depends(current)):
        writable();owner=panel_owner(p);result=ops.support_meta(owner,row_id,body.priority,body.assigned_to)
        audit(p.actor,owner,'telegram.support_meta',str(row_id),body.priority+';'+body.assigned_to);return result

    @app.put('/api/telegram/operations/support/quick-replies/{reply_id}')
    def operations_quick_reply_put(reply_id:str,body:QuickReplyBody,p=Depends(current)):
        writable();owner=panel_owner(p)
        if body.id!=reply_id:raise HTTPException(400,'Quick reply ID mismatch')
        result=ops.save_quick_reply(owner,body);audit(p.actor,owner,'telegram.quick_reply_save',reply_id,body.title);return result

    @app.delete('/api/telegram/operations/support/quick-replies/{reply_id}')
    def operations_quick_reply_delete(reply_id:str,p=Depends(current)):
        writable();owner=panel_owner(p);result=ops.delete_quick_reply(owner,reply_id)
        audit(p.actor,owner,'telegram.quick_reply_delete',reply_id,'');return result

    @app.get('/api/telegram/operations/crypto')
    def operations_crypto(p=Depends(current)):
        return ops.crypto_gateway(panel_owner(p))

    @app.put('/api/telegram/operations/crypto')
    def operations_crypto_put(body:CryptoGatewayBody,p=Depends(current)):
        writable();owner=panel_owner(p);result=ops.save_crypto_gateway(owner,body)
        audit(p.actor,owner,'telegram.crypto_gateway_save',body.id,'enabled='+str(body.enabled));return result

    @app.post('/api/telegram/crypto/webhook/{owner}/{gateway_id}')
    async def telegram_crypto_webhook(owner:str,gateway_id:str,request:Request,
                                      x_dark_signature:str=Header(default='',alias='X-Dark-Signature')):
        raw=await request.body()
        try:return ops.crypto_webhook(owner,gateway_id,raw,x_dark_signature)
        except PolicyError as ex:raise HTTPException(400,str(ex))

    @app.get('/api/telegram-miniapp/bootstrap')
    def mini_bootstrap(owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        try:return ops.mini_bootstrap(owner,x_telegram_init_data)
        except PolicyError as ex:raise HTTPException(403,str(ex))

    @app.post('/api/telegram-miniapp/simple-plans',status_code=201)
    def mini_simple_plan(body:SimplePlanBody,owner:str,
                         x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();mini_owner(owner,x_telegram_init_data)
        result=runtime.commerce.create_simple_plan(owner,body.model_dump())
        return result

    @app.post('/api/telegram-miniapp/payments/{kind}/{row_id}/{action}')
    def mini_payment_action(kind:str,row_id:int,action:str,owner:str,
                            x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();mini_owner(owner,x_telegram_init_data);return ops.payment_action(owner,kind,row_id,action)

    @app.get('/api/telegram-miniapp/support/{row_id}')
    def mini_support_detail(row_id:int,owner:str,
                            x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        mini_owner(owner,x_telegram_init_data);return ops.support_detail(owner,row_id)

    @app.post('/api/telegram-miniapp/support/{row_id}/reply')
    def mini_support_reply(row_id:int,body:SupportReply,owner:str,
                           x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();mini_owner(owner,x_telegram_init_data);return ops.support_reply(owner,row_id,body.text)

    @app.post('/api/telegram-miniapp/support/{row_id}/close')
    def mini_support_close(row_id:int,owner:str,
                           x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();mini_owner(owner,x_telegram_init_data);return ops.support_close(owner,row_id)

    @app.put('/api/telegram-miniapp/support/{row_id}/meta')
    def mini_support_meta(row_id:int,body:SupportMeta,owner:str,
                          x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();mini_owner(owner,x_telegram_init_data);return ops.support_meta(owner,row_id,body.priority,body.assigned_to)

    return ops

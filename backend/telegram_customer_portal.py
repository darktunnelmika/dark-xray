from __future__ import annotations
import json
import time
from typing import Any, Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from dark_policy import Actor, PolicyError


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class PurchaseBody(Model):
    product_id:str=Field(min_length=1,max_length=64)
    price_id:str=Field(min_length=1,max_length=64)


class GatewayPayBody(Model):
    gateway_id:str=Field(min_length=1,max_length=64)


class TopupBody(Model):
    amount_minor:StrictInt=Field(ge=1000,le=10**12)


class RenewBody(Model):
    price_id:str=Field(min_length=1,max_length=64)


class TicketCreateBody(Model):
    subject:str=Field(min_length=1,max_length=128)
    message:str=Field(min_length=1,max_length=4000)


class TicketReplyBody(Model):
    text:str=Field(min_length=1,max_length=4000)


class RepresentativeOrderBody(Model):
    plan_row:StrictInt=Field(ge=1)
    kind:Literal['purchase','renewal']='purchase'


class CustomerPortal:
    ORDER_PHASES={
        'pending':('created','سفارش ساخته شد'),
        'awaiting_payment':('awaiting_payment','در انتظار پرداخت'),
        'payment_review':('review','در انتظار تأیید پرداخت'),
        'payment_rejected':('rejected','پرداخت رد شد'),
        'paid':('provisioning','پرداخت تأیید شد؛ در حال ساخت سرویس'),
        'provisioned_waiting_activation':('active','سرویس آماده است؛ زمان از اولین اتصال شروع می‌شود'),
        'provisioned':('active','سرویس فعال است'),
        'renewed':('active','تمدید انجام شد'),
        'volume_added':('active','حجم اضافه شد'),
        'cancelled':('cancelled','سفارش لغو/منقضی شد'),
    }

    def __init__(self,runtime):
        self.runtime=runtime;self.store=runtime.store;self.commerce=runtime.commerce
        self.customer=runtime.customer;self.manager=runtime.manager;self.ops=runtime.ops;self.market=runtime.marketplace

    def verify(self,owner:str,init_data:str)->dict[str,Any]:
        auth=self.ops.verify_init_data(owner,init_data,admin_only=False)
        user=auth['user'];uid=int(user['id'])
        return {'owner':owner,'user':user,'telegram_id':uid,'username':str(user.get('username') or '')[:128],
                'admin':bool(auth.get('admin')),'auth_date':auth['auth_date']}

    @staticmethod
    def _phase(status:str)->dict[str,str]:
        phase,label=CustomerPortal.ORDER_PHASES.get(str(status),('unknown',str(status)))
        return {'phase':phase,'label':label}

    def _audit(self,owner:str,uid:int,action:str,target:str,detail:str=''):
        self.manager.audit(Actor('telegram:'+str(uid),'customer',{}),owner,action,target,detail)

    def _product_rows(self,owner:str)->list[dict[str,Any]]:
        out=[]
        for p in self.commerce.product_rows(owner,public=True):
            prices=[]
            for x in p.get('prices') or []:
                if not x.get('active'):continue
                prices.append({'id':x['id'],'row_id':x['row_id'],'label':x['label'],
                               'price_minor':int(x['price_minor']),'currency':x['currency'],
                               'duration_days':int(x['duration_days']),'volume_bytes':int(x['volume_bytes']),
                               'unlimited':int(x['volume_bytes'])==0,'ip_limit':int(x['ip_limit'])})
            if prices:
                out.append({'id':p['id'],'row_id':p['row_id'],'name':p['name'],'description':p.get('description') or '',
                            'category':p.get('category') or 'General','kind':p['kind'],'prices':prices})
        return out

    def _owned_order(self,owner:str,uid:int,order_id:str)->dict[str,Any]:
        row=self.commerce.order(order_id,owner)
        if int(row['buyer_telegram_id'])!=int(uid):raise PolicyError('Order does not belong to this Telegram account')
        return row

    def order_rows(self,owner:str,uid:int,limit:int=40)->list[dict[str,Any]]:
        limit=max(1,min(int(limit),100))
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("""SELECT o.*,p.name product_name,cp.label price_label
              FROM commerce_orders o LEFT JOIN commerce_products p ON p.owner=o.owner AND p.id=o.product_id
              LEFT JOIN commerce_prices cp ON cp.owner=o.owner AND cp.id=o.price_id
              WHERE o.owner=? AND o.buyer_telegram_id=? ORDER BY o.created_at DESC LIMIT ?""",
              (owner,int(uid),limit))]
        out=[]
        for r in rows:
            x={k:r.get(k) for k in ('id','product_id','price_id','product_name','price_label','amount_minor','currency',
                                     'status','gateway_id','client_id','fulfillment_error','created_at','updated_at',
                                     'order_type','target_client_id')}
            x.update(self._phase(str(r['status'])));out.append(x)
        return out

    def payment_methods(self,owner:str,uid:int)->list[dict[str,Any]]:
        wallet=self.customer.wallet(owner,uid)
        out=[{'id':'wallet','label':'کیف پول','kind':'wallet','balance_minor':int(wallet['balance_minor']),
              'currency':wallet['currency'],'enabled':True}]
        for g in self.commerce.gateway_rows(owner,enabled_only=True):
            if g['kind']=='manual':
                out.append({'id':g['id'],'label':g['label'],'kind':'manual','enabled':True})
            elif g['kind']=='plugin' and g.get('plugin')=='crypto-hosted':
                c=self.ops.crypto_gateway(owner,g['id'])
                if c.get('enabled') and c.get('configured'):
                    out.append({'id':g['id'],'label':g['label'],'kind':'crypto','enabled':True})
        return out

    def purchase(self,owner:str,uid:int,username:str,product_id:str,price_id:str)->dict[str,Any]:
        public={p['id']:p for p in self._product_rows(owner)}
        p=public.get(product_id)
        if not p or price_id not in {x['id'] for x in p['prices']}:raise PolicyError('Plan is not available for purchase')
        order=self.commerce.create_order(owner,uid,username,product_id,price_id)
        self._audit(owner,uid,'customer.purchase_create',order['id'],'product='+product_id+'; price='+price_id)
        return self._owned_order(owner,uid,order['id'])|self._phase(order['status'])

    def pay_wallet(self,owner:str,uid:int,order_id:str)->dict[str,Any]:
        order=self._owned_order(owner,uid,order_id)
        if str(order.get('order_type') or 'purchase')!='purchase':raise PolicyError('Wallet purchase endpoint only accepts new purchases')
        result=self.customer.pay_purchase(owner,order_id)
        self._audit(owner,uid,'customer.purchase_wallet',order_id,'status='+str(result.get('status') or ''))
        return result|self._phase(str(result.get('status') or self.commerce.order(order_id,owner)['status']))

    def pay_gateway(self,owner:str,uid:int,order_id:str,gateway_id:str)->dict[str,Any]:
        order=self._owned_order(owner,uid,order_id)
        if str(order.get('order_type') or 'purchase')!='purchase':
            raise PolicyError('Direct gateway payment is not enabled for renewals')
        if gateway_id=='wallet':return self.pay_wallet(owner,uid,order_id)
        allowed={x['id'] for x in self.payment_methods(owner,uid) if x['id']!='wallet'}
        if gateway_id not in allowed:raise PolicyError('Payment method is unavailable')
        result=self.commerce.start_payment(owner,order_id,gateway_id)
        self._audit(owner,uid,'customer.purchase_gateway',order_id,'gateway='+gateway_id)
        return result|{'order_id':order_id,'receipt_in_bot':result.get('mode')=='manual'}

    def _service_row(self,owner:str,uid:int,row_id:int)->tuple[str,dict[str,Any]]:
        with self.store.lock:
            row=self.store.db.execute("SELECT rowid,id FROM clients WHERE rowid=? AND owner=?",(int(row_id),owner)).fetchone()
        if not row:raise PolicyError('Service not found')
        detail=self.manager.detail(self.commerce.actor_for(owner),str(row['id']),credentials=True)
        if int((detail.get('client') or {}).get('tgId') or 0)!=int(uid):
            raise PolicyError('Service does not belong to this Telegram account')
        return str(row['id']),detail

    def _service_summary(self,owner:str,uid:int,email:str,detail:dict[str,Any],row_id:int)->dict[str,Any]:
        c=detail.get('client') or {};quota=int(c.get('totalGB') or 0);used=int(detail.get('used_bytes') or 0)
        expiry=int(c.get('expiryTime') or 0);now_ms=int(time.time()*1000)
        origin=self.customer.latest_service_order(owner,uid,email);product_name='DARK Service';renewal=False;add_volume=False
        activation_pending=False
        if origin:
            activation_pending=str(origin.get('status'))=='provisioned_waiting_activation'
            with self.store.lock:
                p=self.store.db.execute("SELECT name,renewal_enabled,add_volume_enabled FROM commerce_products WHERE owner=? AND id=?",
                                        (owner,origin['product_id'])).fetchone()
            if p:
                product_name=str(p['name']);renewal=bool(p['renewal_enabled']);add_volume=bool(p['add_volume_enabled'])
        blocked=bool(detail.get('block_reasons'));expired=bool(expiry and expiry<=now_ms)
        status='blocked' if blocked else 'expired' if expired else 'waiting_activation' if activation_pending else 'active'
        return {'row_id':int(row_id),'id':email,'product_name':product_name,'quota_bytes':quota,'used_bytes':used,
                'remaining_bytes':None if quota==0 else max(0,quota-used),'unlimited':quota==0,'expiry_time':expiry,
                'status':status,'renewal_enabled':renewal,'add_volume_enabled':bool(add_volume and quota>0),
                'presence_state':detail.get('presence_state') or 'offline',
                'last_seen_at':float(detail.get('last_seen_at') or 0),'block_reasons':detail.get('block_reasons') or []}

    def services(self,owner:str,uid:int)->list[dict[str,Any]]:
        actor=self.commerce.actor_for(owner);rows=self.manager.list(actor);out=[]
        with self.store.lock:
            rowids={str(r['id']):int(r['rowid']) for r in self.store.db.execute("SELECT rowid,id FROM clients WHERE owner=?",(owner,))}
        for detail in rows:
            if int((detail.get('client') or {}).get('tgId') or 0)!=int(uid):continue
            email=str(detail['email']);out.append(self._service_summary(owner,uid,email,detail,rowids.get(email,0)))
        return out

    def service_detail(self,owner:str,uid:int,row_id:int)->dict[str,Any]:
        email,detail=self._service_row(owner,uid,row_id)
        summary=self._service_summary(owner,uid,email,detail,row_id)
        origin=self.customer.latest_service_order(owner,uid,email);delivery={}
        if origin:
            try:delivery=self.commerce.delivery_payload(origin['id'],self.manager)
            except Exception:delivery={}
        if not any(delivery.get(k) for k in ('subscription_url','main_config','portal_url')):
            sub=str(detail.get('subscription_url') or '')
            if sub:delivery={'mode':'subscription','subscription_url':sub,'portal_url':sub,'show_qr':True,'show_portal':True}
        summary['delivery']=delivery
        if summary['renewal_enabled']:
            try:
                rp=self.customer.renewal_prices(owner,uid,email)
                summary['renewal_prices']=[{'id':p['id'],'row_id':p['row_id'],'label':p['label'],
                                            'price_minor':int(p['price_minor']),'currency':p['currency'],
                                            'duration_days':int(p['duration_days']),'volume_bytes':int(p['volume_bytes'])}
                                           for p in rp['prices'] if p.get('active')]
            except PolicyError:summary['renewal_prices']=[]
        else:summary['renewal_prices']=[]
        if summary['add_volume_enabled']:
            try:
                vp=self.customer.volume_addon_prices(owner,uid,email)
                summary['volume_prices']=[{'id':p['id'],'row_id':p['row_id'],'label':p['label'],
                                           'price_minor':int(p['price_minor']),'currency':p['currency'],
                                           'volume_bytes':int(p['volume_bytes'])}
                                          for p in vp['prices'] if p.get('active') and int(p.get('volume_bytes') or 0)>0]
            except PolicyError:summary['volume_prices']=[]
        else:summary['volume_prices']=[]
        return summary

    def create_renewal(self,owner:str,uid:int,username:str,row_id:int,price_id:str)->dict[str,Any]:
        email,_=self._service_row(owner,uid,row_id)
        order=self.customer.create_renewal_order(owner,uid,username,email,price_id)
        self._audit(owner,uid,'customer.renewal_create',order['id'],'client='+email)
        return order|self._phase(order['status'])

    def pay_renewal_wallet(self,owner:str,uid:int,order_id:str)->dict[str,Any]:
        order=self._owned_order(owner,uid,order_id)
        if order.get('order_type')!='renewal':raise PolicyError('Order is not a renewal')
        result=self.customer.pay_renewal(owner,order_id)
        self._audit(owner,uid,'customer.renewal_wallet',order_id,'client='+str(result.get('client_id') or ''))
        return result|self._phase(str(result.get('status') or 'renewed'))

    def create_volume_addon(self,owner:str,uid:int,username:str,row_id:int,price_id:str)->dict[str,Any]:
        email,_=self._service_row(owner,uid,row_id)
        order=self.customer.create_volume_addon_order(owner,uid,username,email,price_id)
        self._audit(owner,uid,'customer.volume_addon_create',order['id'],'client='+email)
        return order|self._phase(order['status'])

    def pay_volume_addon_wallet(self,owner:str,uid:int,order_id:str)->dict[str,Any]:
        order=self._owned_order(owner,uid,order_id)
        if order.get('order_type')!='volume_addon':raise PolicyError('Order is not a volume add-on')
        result=self.customer.pay_volume_addon(owner,order_id)
        self._audit(owner,uid,'customer.volume_addon_wallet',order_id,'client='+str(result.get('client_id') or ''))
        return result|self._phase(str(result.get('status') or 'volume_added'))

    def wallet_bundle(self,owner:str,uid:int)->dict[str,Any]:
        wallet=self.customer.wallet(owner,uid)
        with self.store.lock:
            topups=[dict(r) for r in self.store.db.execute("""SELECT rowid AS row_id,id,amount_minor,currency,status,
              receipt_ref,created_at,updated_at FROM customer_topups WHERE owner=? AND telegram_id=?
              ORDER BY created_at DESC LIMIT 20""",(owner,int(uid)))]
        return {'wallet':wallet,'ledger':self.customer.ledger(owner,uid,40),'topups':topups}

    def create_topup(self,owner:str,uid:int,username:str,amount:int)->dict[str,Any]:
        gateway=next((g for g in self.commerce.gateway_rows(owner,enabled_only=True) if g['kind']=='manual'),None)
        if not gateway:raise PolicyError('Manual wallet top-up is not enabled')
        top=self.customer.create_topup(owner,uid,username,amount)
        self._audit(owner,uid,'customer.wallet_topup_create',top['id'],'amount='+str(amount))
        return {'topup':top,'payment':{'kind':'manual','label':gateway['label'],'card_number':gateway.get('card_number') or '',
                'card_holder':gateway.get('card_holder') or '','bank_name':gateway.get('bank_name') or '',
                'instructions':gateway.get('instructions') or '','receipt_in_bot':True}}

    def support_rows(self,owner:str,uid:int)->list[dict[str,Any]]:
        return self.customer.tickets_for_customer(owner,uid,30)

    def _owned_ticket(self,owner:str,uid:int,row_id:int)->dict[str,Any]:
        t=self.customer.ticket_by_rowid(owner,row_id)
        if int(t['telegram_id'])!=int(uid):raise PolicyError('Ticket does not belong to this Telegram account')
        return t

    def support_detail(self,owner:str,uid:int,row_id:int)->dict[str,Any]:
        t=self._owned_ticket(owner,uid,row_id)
        return {'ticket':t,'messages':self.customer.ticket_messages(owner,t['id'],100)}

    def support_create(self,owner:str,uid:int,username:str,subject:str,message:str)->dict[str,Any]:
        if not self.customer.settings(owner).get('support_enabled'):raise PolicyError('Support is disabled')
        t=self.customer.create_ticket(owner,uid,username,subject)
        msg=self.customer.add_ticket_message(owner,t['id'],'customer',uid,text=message)
        self._audit(owner,uid,'customer.support_create',t['id'],'')
        worker=self.ops._worker(owner)
        if worker:worker.notify_admin('🎫 تیکت جدید مشتری\n'+t['subject']+'\nکاربر: '+str(uid))
        return {'ticket':self.customer.ticket(t['id'],owner),'message':msg}

    def support_reply(self,owner:str,uid:int,row_id:int,text:str)->dict[str,Any]:
        t=self._owned_ticket(owner,uid,row_id)
        if t['status']=='closed':raise PolicyError('Ticket is closed')
        msg=self.customer.add_ticket_message(owner,t['id'],'customer',uid,text=text)
        self._audit(owner,uid,'customer.support_reply',t['id'],'')
        worker=self.ops._worker(owner)
        if worker:worker.notify_admin('🎫 پاسخ جدید مشتری\n'+t['subject']+'\nکاربر: '+str(uid))
        return {'ticket':self.customer.ticket(t['id'],owner),'message':msg}

    def support_close(self,owner:str,uid:int,row_id:int)->dict[str,Any]:
        t=self._owned_ticket(owner,uid,row_id);result=self.customer.close_ticket(owner,t['id'])
        self._audit(owner,uid,'customer.support_close',t['id'],'');return result

    def referral(self,owner:str,uid:int)->dict[str,Any]:
        stats=self.customer.referral_stats(owner,uid);bot=self.commerce.bot_get(owner).get('bot_username') or ''
        stats['invite_url']='https://t.me/'+str(bot)+'?start=ref_'+stats['code'] if bot else ''
        return stats

    def representative(self,owner:str,uid:int)->dict[str,Any]:
        if not self.market.is_primary_owner(owner):return {'available':False}
        sub=self.market.subscription(owner,uid)
        plans=self.market.plan_rows(owner,public=True,renewal=bool(sub))
        safe=[]
        for p in plans:
            safe.append({'row_id':p['row_id'],'id':p['id'],'name':p['name'],'description':p.get('description') or '',
                         'price_minor':int(p['price_minor']),'currency':p['currency'],'duration_days':int(p['duration_days']),
                         'volume_credit_bytes':int(p['volume_credit_bytes']),'unlimited_credit':int(p['unlimited_credit']),
                         'max_clients':int(p['max_clients']),'bot_allowed':bool(p['bot_allowed']),
                         'renewal_enabled':bool(p['renewal_enabled'])})
        return {'available':True,'subscription':sub,'plans':safe}

    def representative_order(self,owner:str,uid:int,username:str,plan_row:int,kind:str)->dict[str,Any]:
        if not self.market.is_primary_owner(owner):raise PolicyError('Representative marketplace is unavailable')
        order=self.market.create_order(owner,uid,username,plan_row,kind)
        self._audit(owner,uid,'customer.representative_order',order['id'],'kind='+kind)
        return order

    def representative_pay(self,owner:str,uid:int,order_id:str)->dict[str,Any]:
        order=self.market.order(order_id,owner)
        if int(order['buyer_telegram_id'])!=int(uid):raise PolicyError('Representative order does not belong to this Telegram account')
        result=self.market.pay_order(owner,order_id,uid)
        self._audit(owner,uid,'customer.representative_pay',order_id,'status='+str(result.get('status') or ''))
        if result.get('password'):
            result['panel_url']=self.manager.engine.config.public_origin.rstrip('/')
            result['credentials_pending']=True
        return result

    def representative_delivered(self,owner:str,uid:int,order_id:str)->dict[str,Any]:
        order=self.market.order(order_id,owner)
        if int(order['buyer_telegram_id'])!=int(uid):raise PolicyError('Representative order does not belong to this Telegram account')
        self.market.mark_credentials_delivered(owner,order_id)
        self._audit(owner,uid,'customer.representative_credentials_saved',order_id,'')
        return {'saved':True}

    def bootstrap(self,owner:str,auth:dict[str,Any])->dict[str,Any]:
        uid=int(auth['telegram_id']);self.customer.ensure_referral_profile(owner,uid)
        return {'user':{'telegram_id':uid,'username':auth['username'],'first_name':auth['user'].get('first_name') or ''},
                'owner':owner,'bot_username':self.commerce.bot_get(owner).get('bot_username') or '',
                'shop':self._product_rows(owner),'services':self.services(owner,uid),'orders':self.order_rows(owner,uid),
                'wallet':self.wallet_bundle(owner,uid),'support':self.support_rows(owner,uid),
                'referral':self.referral(owner,uid),'payment_methods':self.payment_methods(owner,uid),
                'representative':self.representative(owner,uid)}


def install_customer_portal(app,runtime,writable):
    portal=CustomerPortal(runtime);runtime.customer_portal=portal;app.state.telegram_customer_portal=portal

    def auth(owner:str,init_data:str):
        try:return portal.verify(owner,init_data)
        except PolicyError as ex:raise HTTPException(403,str(ex))

    def header_auth(owner:str,x_telegram_init_data:str):
        return auth(owner,x_telegram_init_data)

    @app.get('/api/telegram-customer/bootstrap')
    def customer_bootstrap(owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        a=header_auth(owner,x_telegram_init_data);return portal.bootstrap(owner,a)

    @app.post('/api/telegram-customer/orders',status_code=201)
    def customer_order(body:PurchaseBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.purchase(owner,a['telegram_id'],a['username'],body.product_id,body.price_id)

    @app.post('/api/telegram-customer/orders/{order_id}/wallet')
    def customer_order_wallet(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data);return portal.pay_wallet(owner,a['telegram_id'],order_id)

    @app.post('/api/telegram-customer/orders/{order_id}/gateway')
    def customer_order_gateway(order_id:str,body:GatewayPayBody,owner:str,
                               x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.pay_gateway(owner,a['telegram_id'],order_id,body.gateway_id)

    @app.get('/api/telegram-customer/orders/{order_id}')
    def customer_order_status(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        a=header_auth(owner,x_telegram_init_data);row=portal._owned_order(owner,a['telegram_id'],order_id)
        out={k:row.get(k) for k in ('id','status','amount_minor','currency','gateway_id','client_id','fulfillment_error','order_type')}
        out.update(portal._phase(row['status']))
        if row.get('client_id'):
            try:out['delivery']=portal.commerce.delivery_payload(order_id,portal.manager)
            except Exception:out['delivery']={}
        return out

    @app.get('/api/telegram-customer/services/{row_id}')
    def customer_service(row_id:int,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        a=header_auth(owner,x_telegram_init_data);return portal.service_detail(owner,a['telegram_id'],row_id)

    @app.post('/api/telegram-customer/services/{row_id}/renew',status_code=201)
    def customer_renew(row_id:int,body:RenewBody,owner:str,
                       x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.create_renewal(owner,a['telegram_id'],a['username'],row_id,body.price_id)

    @app.post('/api/telegram-customer/renewals/{order_id}/wallet')
    def customer_renew_pay(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.pay_renewal_wallet(owner,a['telegram_id'],order_id)

    @app.post('/api/telegram-customer/services/{row_id}/volume',status_code=201)
    def customer_volume_addon(row_id:int,body:RenewBody,owner:str,
                              x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.create_volume_addon(owner,a['telegram_id'],a['username'],row_id,body.price_id)

    @app.post('/api/telegram-customer/volume-addons/{order_id}/wallet')
    def customer_volume_addon_pay(order_id:str,owner:str,
                                  x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.pay_volume_addon_wallet(owner,a['telegram_id'],order_id)

    @app.post('/api/telegram-customer/wallet/topups',status_code=201)
    def customer_topup(body:TopupBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.create_topup(owner,a['telegram_id'],a['username'],body.amount_minor)

    @app.post('/api/telegram-customer/support',status_code=201)
    def customer_support_create(body:TicketCreateBody,owner:str,
                                x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.support_create(owner,a['telegram_id'],a['username'],body.subject,body.message)

    @app.get('/api/telegram-customer/support/{row_id}')
    def customer_support_detail(row_id:int,owner:str,
                                x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        a=header_auth(owner,x_telegram_init_data);return portal.support_detail(owner,a['telegram_id'],row_id)

    @app.post('/api/telegram-customer/support/{row_id}/reply')
    def customer_support_reply(row_id:int,body:TicketReplyBody,owner:str,
                               x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.support_reply(owner,a['telegram_id'],row_id,body.text)

    @app.post('/api/telegram-customer/support/{row_id}/close')
    def customer_support_close(row_id:int,owner:str,
                               x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.support_close(owner,a['telegram_id'],row_id)

    @app.post('/api/telegram-customer/representative/orders',status_code=201)
    def customer_rep_order(body:RepresentativeOrderBody,owner:str,
                           x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.representative_order(owner,a['telegram_id'],a['username'],body.plan_row,body.kind)

    @app.post('/api/telegram-customer/representative/orders/{order_id}/wallet')
    def customer_rep_pay(order_id:str,owner:str,
                         x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.representative_pay(owner,a['telegram_id'],order_id)

    @app.post('/api/telegram-customer/representative/orders/{order_id}/credentials-saved')
    def customer_rep_saved(order_id:str,owner:str,
                           x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();a=header_auth(owner,x_telegram_init_data)
        return portal.representative_delivered(owner,a['telegram_id'],order_id)

    return portal

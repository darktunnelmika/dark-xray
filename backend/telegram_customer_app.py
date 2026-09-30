from __future__ import annotations
import json
import time
from typing import Any, Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from dark_policy import PolicyError


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class PurchaseBody(Model):
    product_id:str=Field(min_length=1,max_length=64)
    price_id:str=Field(min_length=1,max_length=64)


class PayBody(Model):
    method:Literal['wallet','crypto']='wallet'
    gateway_id:str=Field(default='crypto',min_length=1,max_length=64)


class RenewalBody(Model):
    client_id:str=Field(min_length=1,max_length=128)
    price_id:str=Field(min_length=1,max_length=64)


class TopupBody(Model):
    amount_minor:StrictInt=Field(ge=1000,le=10**12)


class TicketBody(Model):
    subject:str=Field(min_length=1,max_length=128)
    message:str=Field(min_length=1,max_length=4000)


class TicketReplyBody(Model):
    text:str=Field(min_length=1,max_length=4000)


class RepresentativeOrderBody(Model):
    plan_row:StrictInt=Field(ge=1)
    kind:Literal['purchase','renewal']='purchase'


class CustomerMiniApp:
    def __init__(self,runtime):
        self.runtime=runtime
        self.store=runtime.store
        self.commerce=runtime.commerce
        self.customer=runtime.customer
        self.market=runtime.marketplace
        self.manager=runtime.manager

    def identity(self,owner:str,init_data:str)->dict[str,Any]:
        ops=getattr(self.runtime,'ops',None)
        if not ops:raise PolicyError('Telegram operations runtime is unavailable')
        auth=ops.verify_webapp_user(owner,init_data,admin_only=False)
        user=auth['user']
        self.customer.ensure_referral_profile(owner,int(user['id']))
        return {'owner':owner,'telegram_id':int(user['id']),
                'username':str(user.get('username') or '')[:128],
                'first_name':str(user.get('first_name') or '')[:128],
                'last_name':str(user.get('last_name') or '')[:128]}

    def _owned_order(self,owner:str,telegram_id:int,order_id:str)->dict[str,Any]:
        order=self.commerce.order(order_id,owner)
        if int(order['buyer_telegram_id'])!=int(telegram_id):
            raise PolicyError('Order does not belong to this Telegram account')
        return order

    def orders(self,owner:str,telegram_id:int,limit:int=50)->list[dict[str,Any]]:
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("""SELECT * FROM commerce_orders
              WHERE owner=? AND buyer_telegram_id=? ORDER BY created_at DESC LIMIT ?""",
              (owner,int(telegram_id),max(1,min(int(limit),100))))]
            product_names={r['id']:r['name'] for r in self.store.db.execute(
                "SELECT id,name FROM commerce_products WHERE owner=?",(owner,))}
        for row in rows:
            row['product_name']=product_names.get(row['product_id'],row['product_id'])
            row['inbound_ids']=json.loads(row.get('inbound_ids') or '[]')
        return rows

    def _service_detail(self,owner:str,telegram_id:int,client_id:str)->dict[str,Any]:
        actor=self.commerce.actor_for(owner)
        detail=self.manager.detail(actor,client_id,credentials=True)
        client=detail.get('client') or {}
        if int(client.get('tgId') or 0)!=int(telegram_id):
            raise PolicyError('Service does not belong to this Telegram account')
        return detail

    def services(self,owner:str,telegram_id:int)->list[dict[str,Any]]:
        actor=self.commerce.actor_for(owner)
        rows=[x for x in self.manager.list(actor)
              if int((x.get('client') or {}).get('tgId') or 0)==int(telegram_id)]
        out=[]
        with self.store.lock:
            for row in rows:
                client=row.get('client') or {};client_id=str(row.get('email') or client.get('email') or '')
                dbrow=self.store.db.execute("SELECT rowid FROM clients WHERE owner=? AND id=?",(owner,client_id)).fetchone()
                origin=self.customer.latest_service_order(owner,telegram_id,client_id)
                product=None
                if origin:
                    product=self.store.db.execute("SELECT name,renewal_enabled FROM commerce_products WHERE owner=? AND id=?",
                                                  (owner,origin['product_id'])).fetchone()
                total=int(client.get('totalGB') or 0);used=int(row.get('used_bytes') or 0)
                out.append({'row_id':int(dbrow['rowid']) if dbrow else 0,'id':client_id,
                            'name':str(product['name']) if product else 'DARK Service',
                            'total_bytes':total,'used_bytes':used,
                            'remaining_bytes':0 if total==0 else max(0,total-used),'unlimited':total==0,
                            'expiry_ms':int(client.get('expiryTime') or 0),'enabled':bool(client.get('enable',True)),
                            'blocked':bool(row.get('block_reasons')),'block_reasons':row.get('block_reasons') or [],
                            'renewal_enabled':bool(product['renewal_enabled']) if product else False})
        return out

    def service(self,owner:str,telegram_id:int,client_id:str)->dict[str,Any]:
        detail=self._service_detail(owner,telegram_id,client_id);client=detail.get('client') or {}
        origin=self.customer.latest_service_order(owner,telegram_id,client_id)
        delivery={}
        product_name='DARK Service';renewal=[]
        if origin:
            with self.store.lock:
                p=self.store.db.execute("SELECT name FROM commerce_products WHERE owner=? AND id=?",
                                        (owner,origin['product_id'])).fetchone()
            if p:product_name=str(p['name'])
            try:delivery=self.commerce.delivery_payload(origin['id'],self.manager)
            except Exception:delivery={}
            try:renewal=self.customer.renewal_prices(owner,telegram_id,client_id)['prices']
            except PolicyError:renewal=[]
        total=int(client.get('totalGB') or 0);used=int(detail.get('used_bytes') or 0)
        return {'id':client_id,'name':product_name,'total_bytes':total,'used_bytes':used,
                'remaining_bytes':0 if total==0 else max(0,total-used),'unlimited':total==0,
                'expiry_ms':int(client.get('expiryTime') or 0),'enabled':bool(client.get('enable',True)),
                'blocked':bool(detail.get('block_reasons')),'block_reasons':detail.get('block_reasons') or [],
                'delivery':delivery,'renewal_prices':renewal}

    def payment_methods(self,owner:str,telegram_id:int)->dict[str,Any]:
        wallet=self.customer.wallet(owner,telegram_id)
        ops=self.runtime.ops;crypto=ops.crypto_gateway(owner)
        manual=next((g for g in self.commerce.gateway_rows(owner,enabled_only=True)
                     if g.get('kind')=='manual'),None)
        return {'wallet':wallet,'crypto':{'enabled':bool(crypto.get('enabled') and crypto.get('configured')),
                                         'id':crypto.get('id','crypto'),'label':crypto.get('label','Crypto Gateway')},
                'manual_topup':bool(manual)}

    def bot_url(self,owner:str)->str:
        row=self.commerce.bot_row(owner) or {};username=str(row.get('bot_username') or '')
        return 'https://t.me/'+username if username else ''

    def bootstrap(self,owner:str,identity:dict[str,Any])->dict[str,Any]:
        tid=int(identity['telegram_id']);wallet=self.customer.wallet(owner,tid)
        stats=self.customer.referral_stats(owner,tid);bot=self.commerce.bot_row(owner) or {}
        bot_username=str(bot.get('bot_username') or '')
        referral_url=('https://t.me/'+bot_username+'?start=ref_'+stats['code']) if bot_username else ''
        products=self.commerce.product_rows(owner,public=True)
        products=[p for p in products if any(x.get('active') for x in p.get('prices') or [])]
        tickets=self.customer.tickets_for_customer(owner,tid,30)
        role=self.commerce.actor_for(owner).role
        rep={'available':False,'subscription':None,'plans':[]}
        if role=='owner':
            rep['available']=True;rep['subscription']=self.market.subscription(owner,tid)
            rep['plans']=self.market.plan_rows(owner,public=True,renewal=bool(rep['subscription']))
        return {'identity':identity,'wallet':wallet,'ledger':self.customer.ledger(owner,tid,30),
                'products':products,'orders':self.orders(owner,tid,60),'services':self.services(owner,tid),
                'payments':self.payment_methods(owner,tid),'tickets':tickets,
                'referral':stats|{'url':referral_url},'representative':rep,'bot_url':self.bot_url(owner)}

    def create_purchase(self,owner:str,identity:dict[str,Any],product_id:str,price_id:str)->dict[str,Any]:
        result=self.commerce.create_order(owner,int(identity['telegram_id']),identity['username'],product_id,price_id)
        order=self._owned_order(owner,identity['telegram_id'],result['id'])
        return {'order':order,'payments':self.payment_methods(owner,identity['telegram_id'])}

    def pay_purchase(self,owner:str,identity:dict[str,Any],order_id:str,method:str,gateway_id:str)->dict[str,Any]:
        order=self._owned_order(owner,identity['telegram_id'],order_id)
        if order.get('order_type','purchase')!='purchase':raise PolicyError('Order is not a purchase')
        if method=='wallet':
            return self.customer.pay_purchase(owner,order_id)
        if method!='crypto':raise PolicyError('Unsupported payment method')
        crypto=self.runtime.ops.crypto_gateway(owner,gateway_id)
        if not crypto.get('enabled') or not crypto.get('configured'):raise PolicyError('Crypto payment is not available')
        return self.commerce.start_payment(owner,order_id,gateway_id)

    def order_detail(self,owner:str,telegram_id:int,order_id:str)->dict[str,Any]:
        order=self._owned_order(owner,telegram_id,order_id)
        out=dict(order);out['inbound_ids']=json.loads(out.get('inbound_ids') or '[]')
        with self.store.lock:
            p=self.store.db.execute("SELECT name FROM commerce_products WHERE owner=? AND id=?",
                                    (owner,order['product_id'])).fetchone()
        out['product_name']=str(p['name']) if p else order['product_id']
        if order.get('client_id'):
            try:out['delivery']=self.commerce.delivery_payload(order_id,self.manager)
            except Exception:out['delivery']={}
        else:out['delivery']={}
        return out

    def create_renewal(self,owner:str,identity:dict[str,Any],client_id:str,price_id:str)->dict[str,Any]:
        self._service_detail(owner,identity['telegram_id'],client_id)
        return self.customer.create_renewal_order(owner,int(identity['telegram_id']),identity['username'],client_id,price_id)

    def pay_renewal(self,owner:str,identity:dict[str,Any],order_id:str)->dict[str,Any]:
        order=self._owned_order(owner,identity['telegram_id'],order_id)
        if order.get('order_type')!='renewal':raise PolicyError('Order is not a renewal')
        return self.customer.pay_renewal(owner,order_id)

    def start_topup(self,owner:str,identity:dict[str,Any],amount_minor:int)->dict[str,Any]:
        gateway=next((g for g in self.commerce.gateway_rows(owner,enabled_only=True)
                      if g.get('kind')=='manual'),None)
        if not gateway:raise PolicyError('Manual wallet top-up is not available')
        topup=self.customer.create_topup(owner,int(identity['telegram_id']),identity['username'],amount_minor)
        return {'topup':topup,'payment':{'card_number':gateway.get('card_number') or '',
                                         'card_holder':gateway.get('card_holder') or '',
                                         'bank_name':gateway.get('bank_name') or '',
                                         'instructions':gateway.get('instructions') or '',
                                         'receipt_channel':'telegram_bot','bot_url':self.bot_url(owner)}}

    def ticket(self,owner:str,telegram_id:int,row_id:int)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id)
        if int(ticket['telegram_id'])!=int(telegram_id):raise PolicyError('Ticket does not belong to this Telegram account')
        return {'ticket':ticket,'messages':self.customer.ticket_messages(owner,ticket['id'],100)}

    def create_ticket(self,owner:str,identity:dict[str,Any],subject:str,message:str)->dict[str,Any]:
        ticket=self.customer.create_ticket(owner,int(identity['telegram_id']),identity['username'],subject)
        msg=self.customer.add_ticket_message(owner,ticket['id'],'customer',int(identity['telegram_id']),text=message)
        worker=self.runtime.workers.get(owner)
        if worker:
            try:worker.notify_admin(f"🎫 تیکت جدید Mini App\n{ticket['subject']}\nکاربر: {identity['telegram_id']}")
            except Exception:pass
        return {'ticket':self.customer.ticket(ticket['id'],owner),'message':msg}

    def reply_ticket(self,owner:str,identity:dict[str,Any],row_id:int,text:str)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id)
        if int(ticket['telegram_id'])!=int(identity['telegram_id']):raise PolicyError('Ticket does not belong to this Telegram account')
        msg=self.customer.add_ticket_message(owner,ticket['id'],'customer',int(identity['telegram_id']),text=text)
        worker=self.runtime.workers.get(owner)
        if worker:
            try:worker.notify_admin(f"🎫 پاسخ مشتری Mini App\n{ticket['subject']}\nکاربر: {identity['telegram_id']}")
            except Exception:pass
        return {'ticket':self.customer.ticket(ticket['id'],owner),'message':msg}

    def close_ticket(self,owner:str,telegram_id:int,row_id:int)->dict[str,Any]:
        ticket=self.customer.ticket_by_rowid(owner,row_id)
        if int(ticket['telegram_id'])!=int(telegram_id):raise PolicyError('Ticket does not belong to this Telegram account')
        return self.customer.close_ticket(owner,ticket['id'])

    def create_rep_order(self,owner:str,identity:dict[str,Any],plan_row:int,kind:str)->dict[str,Any]:
        if self.commerce.actor_for(owner).role!='owner':raise PolicyError('Representative marketplace is unavailable')
        return self.market.create_order(owner,int(identity['telegram_id']),identity['username'],plan_row,kind)

    def pay_rep_order(self,owner:str,identity:dict[str,Any],order_id:str)->dict[str,Any]:
        order=self.market.order(order_id,owner)
        if int(order['buyer_telegram_id'])!=int(identity['telegram_id']):raise PolicyError('Representative order does not belong to this Telegram account')
        return self.market.pay_order(owner,order_id,int(identity['telegram_id']))

    def ack_rep_credentials(self,owner:str,telegram_id:int,order_id:str)->dict[str,Any]:
        order=self.market.order(order_id,owner)
        if int(order['buyer_telegram_id'])!=int(telegram_id):raise PolicyError('Representative order does not belong to this Telegram account')
        self.market.mark_credentials_delivered(owner,order_id)
        return {'acknowledged':True,'order_id':order_id}


def install_customer_miniapp(app,runtime,writable):
    api=CustomerMiniApp(runtime);runtime.customer_app=api;app.state.telegram_customer_app=api

    def auth(owner:str,init_data:str):
        try:return api.identity(owner,init_data)
        except PolicyError as ex:raise HTTPException(403,str(ex))

    @app.get('/api/telegram-customer/bootstrap')
    def bootstrap(owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        identity=auth(owner,x_telegram_init_data);return api.bootstrap(owner,identity)

    @app.post('/api/telegram-customer/orders',status_code=201)
    def create_order(body:PurchaseBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data)
        return api.create_purchase(owner,identity,body.product_id,body.price_id)

    @app.get('/api/telegram-customer/orders/{order_id}')
    def order_detail(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        identity=auth(owner,x_telegram_init_data);return api.order_detail(owner,identity['telegram_id'],order_id)

    @app.post('/api/telegram-customer/orders/{order_id}/pay')
    def pay_order(order_id:str,body:PayBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data)
        return api.pay_purchase(owner,identity,order_id,body.method,body.gateway_id)

    @app.get('/api/telegram-customer/services/{client_id}')
    def service(client_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        identity=auth(owner,x_telegram_init_data);return api.service(owner,identity['telegram_id'],client_id)

    @app.post('/api/telegram-customer/renewals',status_code=201)
    def renewal(body:RenewalBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data)
        return api.create_renewal(owner,identity,body.client_id,body.price_id)

    @app.post('/api/telegram-customer/renewals/{order_id}/pay')
    def renewal_pay(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.pay_renewal(owner,identity,order_id)

    @app.post('/api/telegram-customer/topups',status_code=201)
    def topup(body:TopupBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.start_topup(owner,identity,body.amount_minor)

    @app.get('/api/telegram-customer/tickets/{row_id}')
    def ticket(row_id:int,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        identity=auth(owner,x_telegram_init_data);return api.ticket(owner,identity['telegram_id'],row_id)

    @app.post('/api/telegram-customer/tickets',status_code=201)
    def ticket_create(body:TicketBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.create_ticket(owner,identity,body.subject,body.message)

    @app.post('/api/telegram-customer/tickets/{row_id}/reply')
    def ticket_reply(row_id:int,body:TicketReplyBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.reply_ticket(owner,identity,row_id,body.text)

    @app.post('/api/telegram-customer/tickets/{row_id}/close')
    def ticket_close(row_id:int,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.close_ticket(owner,identity['telegram_id'],row_id)

    @app.post('/api/telegram-customer/representative/orders',status_code=201)
    def rep_order(body:RepresentativeOrderBody,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.create_rep_order(owner,identity,body.plan_row,body.kind)

    @app.post('/api/telegram-customer/representative/orders/{order_id}/pay')
    def rep_pay(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.pay_rep_order(owner,identity,order_id)

    @app.post('/api/telegram-customer/representative/orders/{order_id}/ack')
    def rep_ack(order_id:str,owner:str,x_telegram_init_data:str=Header(default='',alias='X-Telegram-Init-Data')):
        writable();identity=auth(owner,x_telegram_init_data);return api.ack_rep_credentials(owner,identity['telegram_id'],order_id)

    return api

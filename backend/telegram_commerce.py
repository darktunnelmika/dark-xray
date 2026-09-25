from __future__ import annotations
import json
import secrets
import time
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from auth import DEFAULTS
from dark_policy import Actor, NAME_RE, MAX_INT, PolicyError

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class BotSettingsBody(Model):
    enabled: bool = False
    bot_token: str | None = Field(default=None, min_length=20, max_length=256)
    admin_telegram_id: StrictInt = Field(ge=1, le=9_223_372_036_854_775_807)

class ProductBody(Model):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default='', max_length=2000)
    kind: Literal['volume','unlimited','multi_location','gaming'] = 'volume'
    active: bool = True
    visible: bool = True

class PriceBody(Model):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    price_minor: StrictInt = Field(ge=0, le=MAX_INT)
    currency: str = Field(default='IRT', min_length=3, max_length=8)
    duration_days: StrictInt = Field(ge=1, le=3650)
    volume_bytes: StrictInt = Field(default=0, ge=0, le=MAX_INT)
    unlimited_units: StrictInt = Field(default=0, ge=0, le=1_000_000)
    device_limit: StrictInt = Field(default=1, ge=0, le=1000)
    inbound_ids: list[StrictInt] = Field(default_factory=list, min_length=1, max_length=256)
    active: bool = True

class GatewayBody(Model):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    kind: Literal['manual','plugin'] = 'manual'
    enabled: bool = True
    instructions: str = Field(default='', max_length=4000)
    plugin: str = Field(default='', max_length=128)
    secret: str | None = Field(default=None, max_length=4096)

class OrderBody(Model):
    product_id: str = Field(min_length=1, max_length=64)
    price_id: str = Field(min_length=1, max_length=64)
    buyer_telegram_id: StrictInt = Field(ge=1, le=9_223_372_036_854_775_807)
    buyer_username: str = Field(default='', max_length=128)

class PayBody(Model):
    gateway_id: str = Field(min_length=1, max_length=64)

class PaymentConfirmBody(Model):
    reference: str = Field(min_length=1, max_length=256)

def _id(prefix: str) -> str:
    return prefix + '_' + secrets.token_hex(12)

class TelegramCommerce:
    def __init__(self, store, cipher):
        self.store = store
        self.cipher = cipher
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS telegram_bots(
              owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
              token_enc TEXT NOT NULL DEFAULT '', admin_telegram_id INTEGER NOT NULL,
              updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS commerce_products(
              id TEXT NOT NULL, owner TEXT NOT NULL, name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, visible INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL, updated_at REAL NOT NULL,
              PRIMARY KEY(owner,id));
            CREATE TABLE IF NOT EXISTS commerce_prices(
              id TEXT NOT NULL, owner TEXT NOT NULL, product_id TEXT NOT NULL,
              label TEXT NOT NULL, price_minor INTEGER NOT NULL, currency TEXT NOT NULL,
              duration_days INTEGER NOT NULL, volume_bytes INTEGER NOT NULL DEFAULT 0,
              unlimited_units INTEGER NOT NULL DEFAULT 0, device_limit INTEGER NOT NULL DEFAULT 1,
              inbound_ids TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL, updated_at REAL NOT NULL,
              PRIMARY KEY(owner,id));
            CREATE INDEX IF NOT EXISTS commerce_prices_product ON commerce_prices(owner,product_id);
            CREATE TABLE IF NOT EXISTS commerce_gateways(
              id TEXT NOT NULL, owner TEXT NOT NULL, label TEXT NOT NULL, kind TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1, instructions TEXT NOT NULL DEFAULT '',
              plugin TEXT NOT NULL DEFAULT '', secret_enc TEXT NOT NULL DEFAULT '',
              updated_at REAL NOT NULL, PRIMARY KEY(owner,id));
            CREATE TABLE IF NOT EXISTS commerce_orders(
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, buyer_telegram_id INTEGER NOT NULL,
              buyer_username TEXT NOT NULL DEFAULT '', product_id TEXT NOT NULL, price_id TEXT NOT NULL,
              amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
              gateway_id TEXT NOT NULL DEFAULT '', payment_ref TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS commerce_orders_owner ON commerce_orders(owner,created_at);
            CREATE TABLE IF NOT EXISTS commerce_payments(
              id TEXT PRIMARY KEY, order_id TEXT NOT NULL, owner TEXT NOT NULL,
              gateway_id TEXT NOT NULL, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
              status TEXT NOT NULL, external_ref TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            """)
            self._column('telegram_bots','update_offset','INTEGER NOT NULL DEFAULT 0')
            self._column('telegram_bots','bot_username',"TEXT NOT NULL DEFAULT ''")
            self._column('telegram_bots','last_error',"TEXT NOT NULL DEFAULT ''")
            self._column('telegram_bots','last_seen','REAL NOT NULL DEFAULT 0')
            self._column('commerce_orders','client_id',"TEXT NOT NULL DEFAULT ''")
            self._column('commerce_orders','fulfillment_error',"TEXT NOT NULL DEFAULT ''")

    def _column(self, table: str, name: str, spec: str) -> None:
        columns={r[1] for r in self.store.db.execute(f'PRAGMA table_info({table})')}
        if name not in columns:self.store.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {spec}')

    def _seal(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode() if value else ''

    def _open(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode() if value else ''

    def owner_for(self, principal, requested: str | None = None) -> str:
        if principal.actor.role == 'owner':
            return requested or principal.actor.id
        if requested and requested != principal.actor.id:
            raise HTTPException(403,'Representative may access only its own commerce scope')
        return principal.actor.id

    def actor_for(self, owner: str) -> Actor:
        with self.store.lock:
            row=self.store.db.execute('SELECT role FROM api_admins WHERE id=? AND disabled=0',(owner,)).fetchone()
        if not row:raise PolicyError('Commerce owner account is unavailable or disabled')
        role=str(row['role'])
        if role=='owner':return Actor(owner,'owner',{})
        if role=='reseller':return Actor(owner,'reseller',dict(DEFAULTS['reseller']))
        raise PolicyError('Commerce owner role is unsupported')

    def bot_row(self, owner: str, *, secret: bool=False) -> dict[str,Any] | None:
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM telegram_bots WHERE owner=?',(owner,)).fetchone()
        if not row:return None
        out=dict(row)
        if secret:out['bot_token']=self._open(out.pop('token_enc'))
        else:out['configured']=bool(out.pop('token_enc'))
        out['enabled']=bool(out['enabled'])
        return out

    def bot_get(self, owner: str) -> dict[str,Any]:
        row=self.bot_row(owner)
        if not row:return {'owner':owner,'enabled':False,'configured':False,'admin_telegram_id':None,
                           'bot_username':'','last_error':'','last_seen':0}
        return {k:row[k] for k in ('owner','enabled','configured','admin_telegram_id','bot_username','last_error','last_seen','updated_at')}

    def product_rows(self, owner: str, public: bool=False) -> list[dict[str,Any]]:
        where="owner=?"+(" AND active=1 AND visible=1" if public else "")
        with self.store.lock:
            products=[dict(r) for r in self.store.db.execute(
                f"SELECT rowid AS row_id,* FROM commerce_products WHERE {where} ORDER BY created_at,id",(owner,))]
            prices=[dict(r) for r in self.store.db.execute(
                "SELECT rowid AS row_id,* FROM commerce_prices WHERE owner=? ORDER BY product_id,price_minor,id",(owner,))]
        by={}
        for p in prices:
            p['inbound_ids']=json.loads(p.pop('inbound_ids'));p['active']=bool(p['active'])
            by.setdefault(p['product_id'],[]).append(p)
        for x in products:
            x['active']=bool(x['active']);x['visible']=bool(x['visible']);x['prices']=by.get(x['id'],[])
        return products

    def gateway_rows(self, owner: str, *, enabled_only: bool=False) -> list[dict[str,Any]]:
        sql="SELECT rowid AS row_id,id,owner,label,kind,enabled,instructions,plugin,updated_at,secret_enc FROM commerce_gateways WHERE owner=?"
        if enabled_only:sql+=" AND enabled=1"
        sql+=" ORDER BY id"
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute(sql,(owner,))]
        for r in rows:r['enabled']=bool(r['enabled']);r['configured']=bool(r.pop('secret_enc'))
        return rows

    def create_order(self, owner: str, buyer_telegram_id: int, buyer_username: str,
                     product_id: str, price_id: str) -> dict[str,Any]:
        now=time.time();order_id=_id('ord')
        with self.store.transaction() as db:
            price=db.execute("""SELECT cp.*,p.active product_active,p.visible product_visible FROM commerce_prices cp
              JOIN commerce_products p ON p.owner=cp.owner AND p.id=cp.product_id
              WHERE cp.owner=? AND cp.product_id=? AND cp.id=?""",(owner,product_id,price_id)).fetchone()
            if not price:raise PolicyError('Product price not found')
            if not price['active'] or not price['product_active'] or not price['product_visible']:
                raise PolicyError('Product is not available')
            db.execute("""INSERT INTO commerce_orders(id,owner,buyer_telegram_id,buyer_username,product_id,price_id,
              amount_minor,currency,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (order_id,owner,int(buyer_telegram_id),buyer_username[:128],product_id,price_id,
               int(price['price_minor']),price['currency'],'pending',now,now))
        return {'id':order_id,'status':'pending','amount_minor':int(price['price_minor']),'currency':price['currency']}

    def start_payment(self, owner: str, order_id: str, gateway_id: str) -> dict[str,Any]:
        now=time.time()
        with self.store.transaction() as db:
            order=db.execute("SELECT * FROM commerce_orders WHERE id=? AND owner=?",(order_id,owner)).fetchone()
            if not order:raise PolicyError('Order not found')
            if order['status'] not in ('pending','awaiting_payment','payment_rejected'):
                raise PolicyError('Order cannot enter payment from current state')
            gw=db.execute("SELECT * FROM commerce_gateways WHERE owner=? AND id=? AND enabled=1",(owner,gateway_id)).fetchone()
            if not gw:raise PolicyError('Payment gateway not found or disabled')
            payment_id=_id('pay')
            db.execute("UPDATE commerce_orders SET status='awaiting_payment',gateway_id=?,payment_ref='',updated_at=? WHERE id=?",
                       (gateway_id,now,order_id))
            db.execute("""INSERT INTO commerce_payments(id,order_id,owner,gateway_id,amount_minor,currency,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?)""",(payment_id,order_id,owner,gateway_id,order['amount_minor'],order['currency'],'pending',now,now))
        return {'payment_id':payment_id,'status':'awaiting_payment','mode':gw['kind'],
                'instructions':gw['instructions'],'plugin':gw['plugin']}

    def latest_waiting_order(self, owner: str, buyer_telegram_id: int) -> dict[str,Any] | None:
        with self.store.lock:
            row=self.store.db.execute("""SELECT * FROM commerce_orders WHERE owner=? AND buyer_telegram_id=?
              AND status='awaiting_payment' ORDER BY created_at DESC LIMIT 1""",(owner,int(buyer_telegram_id))).fetchone()
        return dict(row) if row else None

    def record_receipt(self, owner: str, buyer_telegram_id: int, reference: str) -> dict[str,Any]:
        order=self.latest_waiting_order(owner,buyer_telegram_id)
        if not order:raise PolicyError('No order is waiting for a payment receipt')
        now=time.time()
        with self.store.transaction() as db:
            pay=db.execute("""SELECT rowid AS row_id,* FROM commerce_payments WHERE order_id=? AND owner=?
              ORDER BY created_at DESC LIMIT 1""",(order['id'],owner)).fetchone()
            if not pay:raise PolicyError('Payment record not found')
            db.execute("UPDATE commerce_payments SET status='review',external_ref=?,updated_at=? WHERE id=?",
                       (reference[:1024],now,pay['id']))
            db.execute("UPDATE commerce_orders SET status='payment_review',payment_ref=?,updated_at=? WHERE id=?",
                       (reference[:1024],now,order['id']))
        return {'order':order,'payment':dict(pay)|{'external_ref':reference,'status':'review'}}

    def payment_by_rowid(self, owner: str, row_id: int) -> dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute("SELECT rowid AS row_id,* FROM commerce_payments WHERE rowid=? AND owner=?",(row_id,owner)).fetchone()
        if not row:raise PolicyError('Payment not found')
        return dict(row)

    def reject_payment(self, owner: str, row_id: int) -> dict[str,Any]:
        pay=self.payment_by_rowid(owner,row_id);now=time.time()
        with self.store.transaction() as db:
            db.execute("UPDATE commerce_payments SET status='rejected',updated_at=? WHERE id=?",(now,pay['id']))
            db.execute("UPDATE commerce_orders SET status='payment_rejected',updated_at=? WHERE id=?",(now,pay['order_id']))
        return {'order_id':pay['order_id'],'buyer_telegram_id':self.order(pay['order_id'],owner)['buyer_telegram_id']}

    def order(self, order_id: str, owner: str | None=None) -> dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute("SELECT * FROM commerce_orders WHERE id=?"+(" AND owner=?" if owner else ""),
                                      (order_id,owner) if owner else (order_id,)).fetchone()
        if not row:raise PolicyError('Order not found')
        return dict(row)

    def provision_order(self, order_id: str, manager) -> dict[str,Any]:
        order=self.order(order_id)
        if order['client_id']:
            actor=self.actor_for(order['owner'])
            return {'provisioned':True,'client_id':order['client_id'],
                    'client':manager.detail(actor,order['client_id'],credentials=True)}
        with self.store.lock:
            price=self.store.db.execute("SELECT * FROM commerce_prices WHERE owner=? AND id=?",
                                        (order['owner'],order['price_id'])).fetchone()
        if not price:raise PolicyError('Order price definition is missing')
        actor=self.actor_for(order['owner'])
        profile=manager.profile(order['owner'])
        prefix=str(profile.get('prefix') or '')
        identity=(prefix+'tg'+str(order['buyer_telegram_id'])+'_'+order['id'][-8:]).lower()
        inbound_ids=[int(x) for x in json.loads(price['inbound_ids'])]
        client={'email':identity,'totalGB':int(price['volume_bytes']),'expiryTime':int((time.time()+int(price['duration_days'])*86400)*1000),
                'limitIp':int(price['device_limit']),'limitHwid':0,'tgId':int(order['buyer_telegram_id']),
                'enable':True,'comment':'DARK BOT order '+order['id']}
        try:
            result=manager.create(actor,order['owner'],client,inbound_ids)
        except Exception as ex:
            with self.store.transaction() as db:
                db.execute("UPDATE commerce_orders SET fulfillment_error=?,updated_at=? WHERE id=?",
                           (str(ex)[:1000],time.time(),order_id))
            raise
        with self.store.transaction() as db:
            db.execute("UPDATE commerce_orders SET status='provisioned',client_id=?,fulfillment_error='',updated_at=? WHERE id=?",
                       (identity,time.time(),order_id))
        return {'provisioned':True,'client_id':identity,'client':result}

    def confirm_payment(self, owner: str, order_id: str, reference: str, manager) -> dict[str,Any]:
        order=self.order(order_id,owner)
        if order['status'] not in ('awaiting_payment','payment_review','paid'):
            raise PolicyError('Order is not awaiting payment confirmation')
        now=time.time()
        with self.store.transaction() as db:
            db.execute("UPDATE commerce_orders SET status='paid',payment_ref=?,updated_at=? WHERE id=?",
                       (reference[:256],now,order_id))
            db.execute("""UPDATE commerce_payments SET status='paid',external_ref=?,updated_at=?
              WHERE id=(SELECT id FROM commerce_payments WHERE order_id=? ORDER BY created_at DESC LIMIT 1)""",
              (reference[:1024],now,order_id))
        result={'id':order_id,'status':'paid','provisioned':False}
        try:result.update(self.provision_order(order_id,manager));result['status']='provisioned'
        except Exception as ex:result['fulfillment_error']=str(ex)
        return result

    def approve_payment(self, owner: str, row_id: int, manager) -> dict[str,Any]:
        pay=self.payment_by_rowid(owner,row_id)
        return self.confirm_payment(owner,pay['order_id'],pay.get('external_ref') or pay['id'],manager)

def install_telegram_commerce(app, store, auth, current, writable, audit, manager):
    commerce=TelegramCommerce(store,auth.cipher)
    from telegram_runtime import TelegramBotRuntime
    runtime=TelegramBotRuntime(commerce,manager,auth,audit)
    app.state.telegram_commerce=commerce
    app.state.telegram_runtime=runtime

    @app.get('/api/telegram/settings')
    def telegram_settings(owner_id:str|None=None,p=Depends(current)):
        return commerce.bot_get(commerce.owner_for(p,owner_id))

    @app.put('/api/telegram/settings')
    def telegram_settings_put(body:BotSettingsBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p);now=time.time()
        with store.transaction() as db:
            old=db.execute("SELECT token_enc FROM telegram_bots WHERE owner=?",(oid,)).fetchone()
            token=commerce._seal(body.bot_token) if body.bot_token else (old['token_enc'] if old else '')
            if body.enabled and not token:raise PolicyError('Bot token is required before enabling Telegram bot')
            reset=1 if body.bot_token else 0
            db.execute("""INSERT INTO telegram_bots(owner,enabled,token_enc,admin_telegram_id,updated_at,update_offset,last_error)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET enabled=excluded.enabled,
              token_enc=excluded.token_enc,admin_telegram_id=excluded.admin_telegram_id,updated_at=excluded.updated_at,
              update_offset=CASE WHEN ?=1 THEN 0 ELSE telegram_bots.update_offset END,
              last_error=CASE WHEN ?=1 THEN '' ELSE telegram_bots.last_error END""",
              (oid,int(body.enabled),token,int(body.admin_telegram_id),now,0,'',reset,reset))
        runtime.wake()
        audit(p.actor,oid,'telegram.settings',oid,'enabled='+str(body.enabled))
        return commerce.bot_get(oid)

    @app.get('/api/telegram/status')
    def telegram_status(p=Depends(current)):
        oid=commerce.owner_for(p)
        return runtime.status(oid)|commerce.bot_get(oid)

    @app.post('/api/telegram/test')
    def telegram_test(p=Depends(current)):
        oid=commerce.owner_for(p)
        result=runtime.test_bot(oid)
        audit(p.actor,oid,'telegram.test',oid,str(result.get('username','')))
        return result

    @app.get('/api/commerce/products')
    def products(owner_id:str|None=None,p=Depends(current)):
        return commerce.product_rows(commerce.owner_for(p,owner_id))

    @app.put('/api/commerce/products')
    def product_put(body:ProductBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p)
        if not NAME_RE.fullmatch(body.id):raise HTTPException(400,'Invalid product ID')
        now=time.time()
        with store.transaction() as db:
            db.execute("""INSERT INTO commerce_products(id,owner,name,description,kind,active,visible,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET name=excluded.name,
              description=excluded.description,kind=excluded.kind,active=excluded.active,
              visible=excluded.visible,updated_at=excluded.updated_at""",
              (body.id,oid,body.name,body.description,body.kind,int(body.active),int(body.visible),now,now))
        audit(p.actor,oid,'commerce.product_save',body.id)
        return next(x for x in commerce.product_rows(oid) if x['id']==body.id)

    @app.put('/api/commerce/products/{product_id}/prices')
    def price_put(product_id:str,body:PriceBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p)
        if not NAME_RE.fullmatch(product_id) or not NAME_RE.fullmatch(body.id):raise HTTPException(400,'Invalid product/price ID')
        with store.lock:
            exists=store.db.execute("SELECT 1 FROM commerce_products WHERE owner=? AND id=?",(oid,product_id)).fetchone()
        if not exists:raise HTTPException(404,'Product not found')
        if body.volume_bytes==0 and body.unlimited_units==0:raise HTTPException(400,'Price must allocate volume or unlimited service')
        now=time.time()
        with store.transaction() as db:
            db.execute("""INSERT INTO commerce_prices(id,owner,product_id,label,price_minor,currency,duration_days,
              volume_bytes,unlimited_units,device_limit,inbound_ids,active,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET
              product_id=excluded.product_id,label=excluded.label,price_minor=excluded.price_minor,currency=excluded.currency,
              duration_days=excluded.duration_days,volume_bytes=excluded.volume_bytes,unlimited_units=excluded.unlimited_units,
              device_limit=excluded.device_limit,inbound_ids=excluded.inbound_ids,active=excluded.active,updated_at=excluded.updated_at""",
              (body.id,oid,product_id,body.label,int(body.price_minor),body.currency.upper(),int(body.duration_days),
               int(body.volume_bytes),int(body.unlimited_units),int(body.device_limit),json.dumps(body.inbound_ids),
               int(body.active),now,now))
        audit(p.actor,oid,'commerce.price_save',body.id,f'product={product_id}; amount={body.price_minor} {body.currency}')
        return next(x for x in commerce.product_rows(oid) if x['id']==product_id)

    @app.get('/api/commerce/gateways')
    def gateways(p=Depends(current)):
        return commerce.gateway_rows(commerce.owner_for(p))

    @app.put('/api/commerce/gateways')
    def gateway_put(body:GatewayBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p);now=time.time()
        if not NAME_RE.fullmatch(body.id):raise HTTPException(400,'Invalid gateway ID')
        with store.transaction() as db:
            old=db.execute("SELECT secret_enc FROM commerce_gateways WHERE owner=? AND id=?",(oid,body.id)).fetchone()
            secret=commerce._seal(body.secret) if body.secret is not None else (old['secret_enc'] if old else '')
            db.execute("""INSERT INTO commerce_gateways(id,owner,label,kind,enabled,instructions,plugin,secret_enc,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET label=excluded.label,kind=excluded.kind,
              enabled=excluded.enabled,instructions=excluded.instructions,plugin=excluded.plugin,secret_enc=excluded.secret_enc,updated_at=excluded.updated_at""",
              (body.id,oid,body.label,body.kind,int(body.enabled),body.instructions,body.plugin,secret,now))
        audit(p.actor,oid,'commerce.gateway_save',body.id,body.kind)
        return {'saved':True,'id':body.id}

    @app.post('/api/commerce/orders',status_code=201)
    def order_create(body:OrderBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p)
        result=commerce.create_order(oid,body.buyer_telegram_id,body.buyer_username,body.product_id,body.price_id)
        audit(p.actor,oid,'commerce.order_create',result['id'],f'price={body.price_id}')
        return result

    @app.get('/api/commerce/orders')
    def orders(p=Depends(current)):
        oid=commerce.owner_for(p)
        with store.lock:return [dict(r) for r in store.db.execute(
            "SELECT * FROM commerce_orders WHERE owner=? ORDER BY created_at DESC LIMIT 500",(oid,))]

    @app.post('/api/commerce/orders/{order_id}/pay')
    def order_pay(order_id:str,body:PayBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p)
        result=commerce.start_payment(oid,order_id,body.gateway_id)
        audit(p.actor,oid,'commerce.payment_start',order_id,body.gateway_id)
        return result

    @app.post('/api/commerce/orders/{order_id}/confirm-payment')
    def payment_confirm(order_id:str,body:PaymentConfirmBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p)
        result=commerce.confirm_payment(oid,order_id,body.reference,manager)
        audit(p.actor,oid,'commerce.payment_confirm',order_id,body.reference[:120])
        return result

    @app.post('/api/commerce/orders/{order_id}/provision')
    def order_provision(order_id:str,p=Depends(current)):
        writable();oid=commerce.owner_for(p);commerce.order(order_id,oid)
        result=commerce.provision_order(order_id,manager)
        audit(p.actor,oid,'commerce.order_provision',order_id,result.get('client_id',''))
        return result
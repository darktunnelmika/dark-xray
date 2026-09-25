from __future__ import annotations
import json
import secrets
import time
from typing import Any, Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from dark_policy import NAME_RE, MAX_INT, PolicyError

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
    currency: str = Field(default='IRR', min_length=3, max_length=8)
    duration_days: StrictInt = Field(ge=1, le=3650)
    volume_bytes: StrictInt = Field(default=0, ge=0, le=MAX_INT)
    unlimited_units: StrictInt = Field(default=0, ge=0, le=1_000_000)
    device_limit: StrictInt = Field(default=0, ge=0, le=1000)
    inbound_ids: list[StrictInt] = Field(default_factory=list, max_length=256)
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
              unlimited_units INTEGER NOT NULL DEFAULT 0, device_limit INTEGER NOT NULL DEFAULT 0,
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

    def _seal(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode() if value else ''

    def owner_for(self, principal, requested: str | None = None) -> str:
        if principal.actor.role == 'owner':
            return requested or principal.actor.id
        if requested and requested != principal.actor.id:
            raise HTTPException(403,'Representative may access only its own commerce scope')
        return principal.actor.id

    def bot_get(self, owner: str) -> dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute("SELECT owner,enabled,admin_telegram_id,updated_at,token_enc FROM telegram_bots WHERE owner=?",(owner,)).fetchone()
        if not row:return {'owner':owner,'enabled':False,'configured':False,'admin_telegram_id':None}
        return {'owner':owner,'enabled':bool(row['enabled']),'configured':bool(row['token_enc']),
                'admin_telegram_id':int(row['admin_telegram_id']),'updated_at':row['updated_at']}

    def product_rows(self, owner: str, public: bool=False) -> list[dict[str,Any]]:
        where="owner=?"+(" AND active=1 AND visible=1" if public else "")
        with self.store.lock:
            products=[dict(r) for r in self.store.db.execute(f"SELECT * FROM commerce_products WHERE {where} ORDER BY created_at,id",(owner,))]
            prices=[dict(r) for r in self.store.db.execute("SELECT * FROM commerce_prices WHERE owner=? ORDER BY product_id,price_minor,id",(owner,))]
        by={}
        for p in prices:
            p['inbound_ids']=json.loads(p.pop('inbound_ids'));p['active']=bool(p['active'])
            by.setdefault(p['product_id'],[]).append(p)
        for x in products:
            x['active']=bool(x['active']);x['visible']=bool(x['visible']);x['prices']=by.get(x['id'],[])
        return products

def install_telegram_commerce(app, store, auth, current, writable, audit):
    commerce=TelegramCommerce(store,auth.cipher)
    app.state.telegram_commerce=commerce

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
            db.execute("""INSERT INTO telegram_bots(owner,enabled,token_enc,admin_telegram_id,updated_at)
              VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET enabled=excluded.enabled,
              token_enc=excluded.token_enc,admin_telegram_id=excluded.admin_telegram_id,updated_at=excluded.updated_at""",
              (oid,int(body.enabled),token,int(body.admin_telegram_id),now))
        audit(p.actor,oid,'telegram.settings',oid,'enabled='+str(body.enabled))
        return commerce.bot_get(oid)

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
        oid=commerce.owner_for(p)
        with store.lock:rows=[dict(r) for r in store.db.execute("SELECT id,owner,label,kind,enabled,instructions,plugin,updated_at,secret_enc FROM commerce_gateways WHERE owner=? ORDER BY id",(oid,))]
        for r in rows:r['enabled']=bool(r['enabled']);r['configured']=bool(r.pop('secret_enc'))
        return rows

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
        writable();oid=commerce.owner_for(p);now=time.time();order_id=_id('ord')
        with store.transaction() as db:
            price=db.execute("""SELECT cp.*,p.active product_active,p.visible product_visible FROM commerce_prices cp
              JOIN commerce_products p ON p.owner=cp.owner AND p.id=cp.product_id
              WHERE cp.owner=? AND cp.product_id=? AND cp.id=?""",(oid,body.product_id,body.price_id)).fetchone()
            if not price:raise HTTPException(404,'Product price not found')
            if not price['active'] or not price['product_active'] or not price['product_visible']:raise HTTPException(409,'Product is not available')
            db.execute("""INSERT INTO commerce_orders(id,owner,buyer_telegram_id,buyer_username,product_id,price_id,
              amount_minor,currency,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (order_id,oid,int(body.buyer_telegram_id),body.buyer_username,body.product_id,body.price_id,
               int(price['price_minor']),price['currency'],'pending',now,now))
        audit(p.actor,oid,'commerce.order_create',order_id,f'price={body.price_id}')
        return {'id':order_id,'status':'pending','amount_minor':int(price['price_minor']),'currency':price['currency']}

    @app.get('/api/commerce/orders')
    def orders(p=Depends(current)):
        oid=commerce.owner_for(p)
        with store.lock:return [dict(r) for r in store.db.execute("SELECT * FROM commerce_orders WHERE owner=? ORDER BY created_at DESC LIMIT 500",(oid,))]

    @app.post('/api/commerce/orders/{order_id}/pay')
    def order_pay(order_id:str,body:PayBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p);now=time.time()
        with store.transaction() as db:
            order=db.execute("SELECT * FROM commerce_orders WHERE id=? AND owner=?",(order_id,oid)).fetchone()
            if not order:raise HTTPException(404,'Order not found')
            if order['status'] not in ('pending','awaiting_payment'):raise HTTPException(409,'Order cannot enter payment from current state')
            gw=db.execute("SELECT * FROM commerce_gateways WHERE owner=? AND id=? AND enabled=1",(oid,body.gateway_id)).fetchone()
            if not gw:raise HTTPException(404,'Payment gateway not found or disabled')
            payment_id=_id('pay')
            db.execute("UPDATE commerce_orders SET status='awaiting_payment',gateway_id=?,updated_at=? WHERE id=?",(body.gateway_id,now,order_id))
            db.execute("""INSERT INTO commerce_payments(id,order_id,owner,gateway_id,amount_minor,currency,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?)""",(payment_id,order_id,oid,body.gateway_id,order['amount_minor'],order['currency'],'pending',now,now))
        audit(p.actor,oid,'commerce.payment_start',order_id,body.gateway_id)
        if gw['kind']=='manual':
            return {'payment_id':payment_id,'status':'awaiting_payment','mode':'manual','instructions':gw['instructions']}
        return {'payment_id':payment_id,'status':'awaiting_payment','mode':'plugin','plugin':gw['plugin'],'checkout_ready':False}

    @app.post('/api/commerce/orders/{order_id}/confirm-payment')
    def payment_confirm(order_id:str,body:PaymentConfirmBody,p=Depends(current)):
        writable();oid=commerce.owner_for(p);now=time.time()
        with store.transaction() as db:
            order=db.execute("SELECT * FROM commerce_orders WHERE id=? AND owner=?",(order_id,oid)).fetchone()
            if not order:raise HTTPException(404,'Order not found')
            if order['status']!='awaiting_payment':raise HTTPException(409,'Order is not awaiting payment')
            db.execute("UPDATE commerce_orders SET status='paid',payment_ref=?,updated_at=? WHERE id=?",(body.reference,now,order_id))
            db.execute("""UPDATE commerce_payments SET status='paid',external_ref=?,updated_at=?
              WHERE id=(SELECT id FROM commerce_payments WHERE order_id=? ORDER BY created_at DESC LIMIT 1)""",
              (body.reference,now,order_id))
        audit(p.actor,oid,'commerce.payment_confirm',order_id,body.reference[:120])
        return {'id':order_id,'status':'paid','provisioned':False}

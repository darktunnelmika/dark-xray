from __future__ import annotations
import json
import secrets
import time
from typing import Any

from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field

from auth import DEFAULTS
from dark_policy import Actor,NAME_RE,PolicyError

CURRENCY='IRT'

def _id(prefix:str)->str:
    return prefix+'_'+secrets.token_hex(12)

class RepresentativePlanBody(BaseModel):
    name:str=Field(min_length=1,max_length=128)
    description:str=Field(default='',max_length=2000)
    price_minor:int=Field(ge=0,le=10**12)
    currency:str=Field(default='IRT',min_length=3,max_length=8)
    duration_days:int=Field(ge=1,le=3650)
    volume_credit_bytes:int=Field(ge=0)
    unlimited_credit:int=Field(ge=0,le=1_000_000)
    max_clients:int=Field(ge=0,le=1_000_000)
    prefix:str=Field(default='rep_',max_length=32)
    max_client_ips:int=Field(default=0,ge=0,le=1000)
    max_client_hwid:int=Field(default=0,ge=0,le=1000)
    allowed_inbounds:list[int]=Field(default_factory=list,max_length=256)
    bot_allowed:bool=True
    renewal_enabled:bool=True
    active:bool=True
    visible:bool=True

class RepresentativeMarketplace:
    def __init__(self,store,manager,auth,customer,commerce):
        self.store=store;self.manager=manager;self.auth=auth;self.customer=customer;self.commerce=commerce
        self.last_sweep=0.0
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS representative_plans(
              id TEXT NOT NULL,owner TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
              price_minor INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'IRT',duration_days INTEGER NOT NULL,
              volume_credit_bytes INTEGER NOT NULL DEFAULT 0,unlimited_credit INTEGER NOT NULL DEFAULT 0,
              max_clients INTEGER NOT NULL DEFAULT 0,prefix TEXT NOT NULL DEFAULT 'rep_',
              max_client_ips INTEGER NOT NULL DEFAULT 0,max_client_hwid INTEGER NOT NULL DEFAULT 0,
              allowed_inbounds TEXT NOT NULL DEFAULT '[]',bot_allowed INTEGER NOT NULL DEFAULT 1,
              renewal_enabled INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1,
              visible INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,updated_at REAL NOT NULL,
              PRIMARY KEY(owner,id));
            CREATE INDEX IF NOT EXISTS representative_plans_owner
              ON representative_plans(owner,active,visible);
            CREATE TABLE IF NOT EXISTS representative_market_orders(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,buyer_telegram_id INTEGER NOT NULL,
              buyer_username TEXT NOT NULL DEFAULT '',plan_id TEXT NOT NULL,kind TEXT NOT NULL,
              amount_minor INTEGER NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,
              snapshot TEXT NOT NULL,representative_id TEXT NOT NULL DEFAULT '',
              password_enc TEXT NOT NULL DEFAULT '',credentials_delivered_at REAL NOT NULL DEFAULT 0,
              fulfillment_error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS representative_market_orders_owner
              ON representative_market_orders(owner,buyer_telegram_id,created_at);
            CREATE TABLE IF NOT EXISTS representative_subscriptions(
              owner TEXT NOT NULL,buyer_telegram_id INTEGER NOT NULL,representative_id TEXT NOT NULL,
              plan_id TEXT NOT NULL,status TEXT NOT NULL,started_at REAL NOT NULL,expires_at REAL NOT NULL,
              suspended_at REAL NOT NULL DEFAULT 0,bot_allowed INTEGER NOT NULL DEFAULT 1,
              bot_was_enabled INTEGER NOT NULL DEFAULT 0,last_order_id TEXT NOT NULL DEFAULT '',
              updated_at REAL NOT NULL,PRIMARY KEY(owner,buyer_telegram_id),UNIQUE(representative_id));
            """)

    def is_primary_owner(self,owner:str)->bool:
        with self.store.lock:
            row=self.store.db.execute("SELECT role,disabled FROM api_admins WHERE id=?",(owner,)).fetchone()
        return bool(row and row['role']=='owner' and not row['disabled'])

    def require_primary_owner(self,owner:str):
        if not self.is_primary_owner(owner):raise PolicyError('Representative marketplace exists only on the primary Owner panel')

    def _validate_inbounds(self,ids:list[int]):
        if not ids or len(ids)>256 or any(type(x)is not int or x<1 for x in ids):
            raise PolicyError('Representative plan requires 1..256 valid Inbound IDs')
        if len(set(ids))!=len(ids):raise PolicyError('Duplicate Inbound IDs')
        known={int(x['id']) for x in self.manager.engine.inbounds()}
        if not set(ids)<=known:raise PolicyError('Representative plan contains an unknown Inbound')

    def _clean_prefix(self,prefix:str)->str:
        value=str(prefix or '').strip().lower()
        if len(value)>32 or any(not(ch.isalnum() or ch in '_.@+-') for ch in value):
            raise PolicyError('Representative plan prefix is invalid')
        return value

    def plan_rows(self,owner:str,public:bool=False,renewal:bool=False)->list[dict[str,Any]]:
        where="owner=?"
        if public:where+=" AND active=1 AND visible=1"
        if renewal:where+=" AND renewal_enabled=1"
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute(
                f"SELECT rowid AS row_id,* FROM representative_plans WHERE {where} ORDER BY price_minor,id",(owner,))]
        for r in rows:
            r['allowed_inbounds']=json.loads(r['allowed_inbounds'])
            for key in ('bot_allowed','renewal_enabled','active','visible'):r[key]=bool(r[key])
        return rows

    def plan_by_rowid(self,owner:str,row_id:int,public:bool=False)->dict[str,Any]:
        sql="SELECT rowid AS row_id,* FROM representative_plans WHERE owner=? AND rowid=?"
        if public:sql+=" AND active=1 AND visible=1"
        with self.store.lock:row=self.store.db.execute(sql,(owner,int(row_id))).fetchone()
        if not row:raise PolicyError('Representative plan not found')
        out=dict(row);out['allowed_inbounds']=json.loads(out['allowed_inbounds'])
        for key in ('bot_allowed','renewal_enabled','active','visible'):out[key]=bool(out[key])
        return out

    def save_plan(self,owner:str,plan_id:str,body:dict[str,Any])->dict[str,Any]:
        self.require_primary_owner(owner)
        if not NAME_RE.fullmatch(plan_id):raise PolicyError('Invalid representative plan ID')
        ids=[int(x) for x in body.get('allowed_inbounds') or []];self._validate_inbounds(ids)
        prefix=self._clean_prefix(body.get('prefix','rep_'));currency=str(body.get('currency') or CURRENCY).upper()
        if currency!='IRT':raise PolicyError('Representative marketplace currently supports IRT wallet pricing only')
        now=time.time()
        with self.store.transaction() as db:
            old=db.execute("SELECT created_at FROM representative_plans WHERE owner=? AND id=?",(owner,plan_id)).fetchone()
            created=float(old['created_at']) if old else now
            db.execute("""INSERT INTO representative_plans(id,owner,name,description,price_minor,currency,duration_days,
              volume_credit_bytes,unlimited_credit,max_clients,prefix,max_client_ips,max_client_hwid,allowed_inbounds,
              bot_allowed,renewal_enabled,active,visible,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(owner,id) DO UPDATE SET name=excluded.name,description=excluded.description,
              price_minor=excluded.price_minor,currency=excluded.currency,duration_days=excluded.duration_days,
              volume_credit_bytes=excluded.volume_credit_bytes,unlimited_credit=excluded.unlimited_credit,
              max_clients=excluded.max_clients,prefix=excluded.prefix,max_client_ips=excluded.max_client_ips,
              max_client_hwid=excluded.max_client_hwid,allowed_inbounds=excluded.allowed_inbounds,
              bot_allowed=excluded.bot_allowed,renewal_enabled=excluded.renewal_enabled,
              active=excluded.active,visible=excluded.visible,updated_at=excluded.updated_at""",
              (plan_id,owner,str(body['name'])[:128],str(body.get('description') or '')[:2000],
               int(body['price_minor']),currency,int(body['duration_days']),int(body['volume_credit_bytes']),
               int(body['unlimited_credit']),int(body['max_clients']),prefix,int(body['max_client_ips']),
               int(body['max_client_hwid']),json.dumps(sorted(set(ids))),int(bool(body.get('bot_allowed',True))),
               int(bool(body.get('renewal_enabled',True))),int(bool(body.get('active',True))),
               int(bool(body.get('visible',True))),created,now))
        return next(x for x in self.plan_rows(owner) if x['id']==plan_id)

    def delete_or_archive_plan(self,owner:str,plan_id:str)->dict[str,Any]:
        self.require_primary_owner(owner)
        with self.store.transaction() as db:
            plan=db.execute("SELECT * FROM representative_plans WHERE owner=? AND id=?",(owner,plan_id)).fetchone()
            if not plan:raise PolicyError('Representative plan not found')
            used=int(db.execute("SELECT COUNT(*) FROM representative_market_orders WHERE owner=? AND plan_id=?",
                                (owner,plan_id)).fetchone()[0])
            if used:
                db.execute("UPDATE representative_plans SET active=0,visible=0,updated_at=? WHERE owner=? AND id=?",
                           (time.time(),owner,plan_id));mode='archived'
            else:
                db.execute("DELETE FROM representative_plans WHERE owner=? AND id=?",(owner,plan_id));mode='deleted'
        return {'id':plan_id,'mode':mode}

    @staticmethod
    def _snapshot(plan:dict[str,Any])->dict[str,Any]:
        keys=('id','name','description','price_minor','currency','duration_days','volume_credit_bytes',
              'unlimited_credit','max_clients','prefix','max_client_ips','max_client_hwid','allowed_inbounds',
              'bot_allowed','renewal_enabled')
        return {k:plan[k] for k in keys}

    def subscription(self,owner:str,telegram_id:int)->dict[str,Any]|None:
        with self.store.lock:
            row=self.store.db.execute("""SELECT * FROM representative_subscriptions
              WHERE owner=? AND buyer_telegram_id=?""",(owner,int(telegram_id))).fetchone()
        return dict(row) if row else None

    def create_order(self,owner:str,telegram_id:int,username:str,plan_row:int,kind:str)->dict[str,Any]:
        self.require_primary_owner(owner)
        plan=self.plan_by_rowid(owner,plan_row,public=True)
        if kind not in ('purchase','renewal'):raise PolicyError('Invalid representative order kind')
        sub=self.subscription(owner,telegram_id)
        if kind=='purchase' and sub:raise PolicyError('This Telegram account already owns a representative panel')
        if kind=='renewal':
            if not sub:raise PolicyError('No representative panel exists to renew')
            if not plan['renewal_enabled']:raise PolicyError('This plan is not available for renewal')
        now=time.time();order_id=_id('rpo');rep_id='';password_enc=''
        if kind=='purchase':
            base='rep'+str(int(telegram_id))
            with self.store.lock:
                existing={str(r[0]) for r in self.store.db.execute("SELECT id FROM api_admins")}
            rep_id=base
            while rep_id in existing:rep_id=base+'_'+secrets.token_hex(2)
            password=secrets.token_urlsafe(15)
            password_enc=self.auth.cipher.encrypt(password.encode()).decode()
        else:rep_id=str(sub['representative_id'])
        snapshot=self._snapshot(plan)
        with self.store.transaction() as db:
            db.execute("""INSERT INTO representative_market_orders(id,owner,buyer_telegram_id,buyer_username,plan_id,
              kind,amount_minor,currency,status,snapshot,representative_id,password_enc,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (order_id,owner,int(telegram_id),str(username or '')[:128],plan['id'],kind,int(plan['price_minor']),
               plan['currency'],'pending',json.dumps(snapshot),rep_id,password_enc,now,now))
        return self.order(order_id,owner)

    def order(self,order_id:str,owner:str)->dict[str,Any]:
        with self.store.lock:
            row=self.store.db.execute("SELECT * FROM representative_market_orders WHERE id=? AND owner=?",
                                      (order_id,owner)).fetchone()
        if not row:raise PolicyError('Representative order not found')
        out=dict(row);out['snapshot']=json.loads(out['snapshot']);return out

    def _password(self,order:dict[str,Any])->str:
        value=str(order.get('password_enc') or '')
        return self.auth.cipher.decrypt(value.encode()).decode() if value else ''

    def _representative_prefix(self,snapshot:dict[str,Any],rep_id:str)->str:
        base=self._clean_prefix(snapshot.get('prefix','rep_'))
        suffix=rep_id+'_'
        if len(base)+len(suffix)>64:base=base[:max(0,64-len(suffix))]
        return base+suffix

    def _apply_policy(self,owner:str,rep_id:str,snapshot:dict[str,Any]):
        actor=self.commerce.actor_for(owner)
        self.manager.owner_put(actor,rep_id,name=str(snapshot['name']),allowed=[int(x) for x in snapshot['allowed_inbounds']],
            volume_credit_bytes=int(snapshot['volume_credit_bytes']),unlimited_credit=int(snapshot['unlimited_credit']),
            max_clients=int(snapshot['max_clients']),manual=False,prefix=self._representative_prefix(snapshot,rep_id),
            max_client_ips=int(snapshot['max_client_ips']),max_client_hwid=int(snapshot['max_client_hwid']))

    def _cleanup_failed_new_rep(self,rep_id:str):
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM clients WHERE owner=?",(rep_id,)).fetchone():
                db.execute("UPDATE api_admins SET disabled=1 WHERE id=? AND role='reseller'",(rep_id,));return
            if db.execute("SELECT 1 FROM representative_subscriptions WHERE representative_id=?",(rep_id,)).fetchone():return
            role=db.execute("SELECT role FROM api_admins WHERE id=?",(rep_id,)).fetchone()
            if role and role['role']!='reseller':return
            db.execute("DELETE FROM live_sessions WHERE admin_id=?",(rep_id,))
            db.execute("DELETE FROM robot_keys WHERE admin_id=?",(rep_id,))
            db.execute("DELETE FROM mfa WHERE admin_id=?",(rep_id,))
            db.execute("DELETE FROM telegram_bots WHERE owner=?",(rep_id,))
            db.execute("DELETE FROM api_admins WHERE id=? AND role='reseller'",(rep_id,))
            db.execute("DELETE FROM owner_profiles WHERE id=?",(rep_id,))
            db.execute("DELETE FROM owners WHERE id=?",(rep_id,))

    def pay_order(self,owner:str,order_id:str,telegram_id:int)->dict[str,Any]:
        order=self.order(order_id,owner)
        if int(order['buyer_telegram_id'])!=int(telegram_id):raise PolicyError('Representative order does not belong to this Telegram account')
        if order['status'] in ('provisioned','renewed'):
            return self.result(order_id,owner)
        if order['status'] not in ('pending','paid','failed_refunded'):raise PolicyError('Representative order cannot be paid')
        if order['status']=='failed_refunded':raise PolicyError('This failed order was refunded; create a new order')
        kind='rep_purchase' if order['kind']=='purchase' else 'rep_renewal'
        reference='representative:'+order_id
        with self.store.transaction() as db:
            self.customer._debit_tx(db,owner,int(telegram_id),int(order['amount_minor']),kind,reference,order_id)
            db.execute("UPDATE representative_market_orders SET status='paid',updated_at=?,fulfillment_error='' WHERE id=?",
                       (time.time(),order_id))
        try:
            return self._provision(owner,order_id)
        except Exception as ex:
            with self.store.transaction() as db:
                self.customer._credit_tx(db,owner,int(telegram_id),int(order['amount_minor']),'rep_refund',
                                         'refund:'+order_id,'automatic marketplace refund')
                db.execute("UPDATE representative_market_orders SET status='failed_refunded',fulfillment_error=?,updated_at=? WHERE id=?",
                           (str(ex)[:1000],time.time(),order_id))
            if order['kind']=='purchase':self._cleanup_failed_new_rep(str(order['representative_id']))
            raise

    def _provision(self,owner:str,order_id:str)->dict[str,Any]:
        order=self.order(order_id,owner);snapshot=order['snapshot'];rep_id=str(order['representative_id'])
        now=time.time()
        if order['kind']=='purchase':
            self._apply_policy(owner,rep_id,snapshot)
            with self.store.lock:account=self.store.db.execute("SELECT role FROM api_admins WHERE id=?",(rep_id,)).fetchone()
            if account and account['role']!='reseller':raise PolicyError('Generated representative ID conflicts with another role')
            if not account:
                password=self._password(order)
                if not password:raise PolicyError('Representative credential is unavailable')
                self.auth.admin_create(self.commerce.actor_for(owner),rep_id,password,'reseller',DEFAULTS['reseller'])
            expires=now+int(snapshot['duration_days'])*86400
            with self.store.transaction() as db:
                db.execute("UPDATE api_admins SET disabled=0 WHERE id=?",(rep_id,))
                db.execute("""INSERT INTO representative_subscriptions(owner,buyer_telegram_id,representative_id,plan_id,status,
                  started_at,expires_at,suspended_at,bot_allowed,bot_was_enabled,last_order_id,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (owner,int(order['buyer_telegram_id']),rep_id,order['plan_id'],'active',now,expires,0,
                   int(bool(snapshot['bot_allowed'])),0,order_id,now))
                db.execute("UPDATE representative_market_orders SET status='provisioned',updated_at=? WHERE id=?",(now,order_id))
        else:
            sub=self.subscription(owner,int(order['buyer_telegram_id']))
            if not sub or str(sub['representative_id'])!=rep_id:raise PolicyError('Representative subscription is unavailable')
            self._apply_policy(owner,rep_id,snapshot)
            expires=max(now,float(sub['expires_at']))+int(snapshot['duration_days'])*86400
            with self.store.transaction() as db:
                db.execute("UPDATE api_admins SET disabled=0 WHERE id=?",(rep_id,))
                if bool(snapshot['bot_allowed']) and int(sub.get('bot_was_enabled') or 0):
                    db.execute("UPDATE telegram_bots SET enabled=1 WHERE owner=? AND token_enc<>''",(rep_id,))
                elif not bool(snapshot['bot_allowed']):
                    db.execute("UPDATE telegram_bots SET enabled=0 WHERE owner=?",(rep_id,))
                db.execute("""UPDATE representative_subscriptions SET plan_id=?,status='active',expires_at=?,suspended_at=0,
                  bot_allowed=?,bot_was_enabled=0,last_order_id=?,updated_at=? WHERE owner=? AND buyer_telegram_id=?""",
                  (order['plan_id'],expires,int(bool(snapshot['bot_allowed'])),order_id,now,owner,int(order['buyer_telegram_id'])))
                db.execute("UPDATE representative_market_orders SET status='renewed',updated_at=? WHERE id=?",(now,order_id))
        self.sweep_expired(force=True)
        return self.result(order_id,owner)

    def result(self,order_id:str,owner:str)->dict[str,Any]:
        order=self.order(order_id,owner)
        sub=self.subscription(owner,int(order['buyer_telegram_id']))
        out={'id':order_id,'status':order['status'],'representative_id':order['representative_id'],
             'subscription':sub,'snapshot':order['snapshot']}
        if order['kind']=='purchase' and not float(order.get('credentials_delivered_at') or 0):
            out['username']=order['representative_id'];out['password']=self._password(order)
        return out

    def mark_credentials_delivered(self,owner:str,order_id:str):
        with self.store.transaction() as db:
            db.execute("""UPDATE representative_market_orders SET credentials_delivered_at=?,password_enc='',updated_at=?
              WHERE id=? AND owner=?""",(time.time(),time.time(),order_id,owner))

    def bot_allowed(self,rep_id:str)->bool:
        with self.store.lock:
            row=self.store.db.execute("SELECT status,bot_allowed FROM representative_subscriptions WHERE representative_id=?",
                                      (rep_id,)).fetchone()
        if not row:return True
        return row['status']=='active' and bool(row['bot_allowed'])

    def sweep_expired(self,force:bool=False)->int:
        now=time.time()
        if not force and now-self.last_sweep<30:return 0
        self.last_sweep=now
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("""SELECT * FROM representative_subscriptions
              WHERE status='active' AND expires_at<=?""",(now,))]
        count=0
        for sub in rows:
            rep_id=str(sub['representative_id'])
            with self.store.transaction() as db:
                bot=db.execute("SELECT enabled FROM telegram_bots WHERE owner=?",(rep_id,)).fetchone()
                was_enabled=1 if bot and bot['enabled'] else 0
                db.execute("UPDATE api_admins SET disabled=1 WHERE id=? AND role='reseller'",(rep_id,))
                db.execute("DELETE FROM live_sessions WHERE admin_id=?",(rep_id,))
                db.execute("UPDATE telegram_bots SET enabled=0 WHERE owner=?",(rep_id,))
                db.execute("""UPDATE representative_subscriptions SET status='suspended',suspended_at=?,
                  bot_was_enabled=?,updated_at=? WHERE owner=? AND buyer_telegram_id=? AND status='active'""",
                  (now,was_enabled,now,sub['owner'],int(sub['buyer_telegram_id'])))
            count+=1
        return count

def install_representative_marketplace(app,market,current,writable,audit):
    @app.get('/api/representative-marketplace/plans')
    def plans(p=Depends(current)):
        if p.actor.role!='owner':raise HTTPException(403,'Only the primary Owner manages representative plans')
        return market.plan_rows(p.actor.id)

    @app.put('/api/representative-marketplace/plans/{plan_id}')
    def plan_put(plan_id:str,body:RepresentativePlanBody,p=Depends(current)):
        writable()
        if p.actor.role!='owner':raise HTTPException(403,'Only the primary Owner manages representative plans')
        result=market.save_plan(p.actor.id,plan_id,body.model_dump())
        audit(p.actor,p.actor.id,'representative.plan_save',plan_id,'owner-defined marketplace plan')
        return result

    @app.delete('/api/representative-marketplace/plans/{plan_id}')
    def plan_delete(plan_id:str,p=Depends(current)):
        writable()
        if p.actor.role!='owner':raise HTTPException(403,'Only the primary Owner manages representative plans')
        result=market.delete_or_archive_plan(p.actor.id,plan_id)
        audit(p.actor,p.actor.id,'representative.plan_delete',plan_id,result['mode'])
        return result

    @app.get('/api/representative-marketplace/subscriptions')
    def subscriptions(p=Depends(current)):
        if p.actor.role!='owner':raise HTTPException(403,'Only the primary Owner may view marketplace subscriptions')
        with market.store.lock:
            return [dict(r) for r in market.store.db.execute(
                "SELECT * FROM representative_subscriptions WHERE owner=? ORDER BY updated_at DESC",(p.actor.id,))]
"""Telegram bot, shop, pricing, orders and safe fulfillment for DARK XRAY."""
from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit,urlunsplit
from urllib.request import Request,urlopen

from dark_policy import Actor, MAX_INT, NAME_RE, PermissionDenied, PolicyError

BOT_TOKEN_RE = re.compile(r"^[1-9][0-9]{4,15}:[A-Za-z0-9_-]{20,128}$")
CURRENCY_RE = re.compile(r"^[A-Z0-9_]{2,12}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")


class BotCommerce:
    """Commerce stays separate from representative resource-credit accounting."""

    def __init__(self, store, manager, auth):
        self.store, self.manager, self.auth = store, manager, auth
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS telegram_bots(
              owner_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
              token_enc TEXT NOT NULL, admin_telegram_id INTEGER NOT NULL,
              public_id TEXT UNIQUE NOT NULL, webhook_secret TEXT NOT NULL,
              bot_username TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS shop_products(
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 100,
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS shop_products_owner
              ON shop_products(owner_id,sort_order,id);
            CREATE TABLE IF NOT EXISTS shop_plans(
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, product_id TEXT NOT NULL,
              label TEXT NOT NULL, service_type TEXT NOT NULL, quota_bytes INTEGER NOT NULL,
              duration_days INTEGER NOT NULL, inbound_ids TEXT NOT NULL,
              limit_ip INTEGER NOT NULL DEFAULT 1, limit_hwid INTEGER NOT NULL DEFAULT 0,
              price_amount INTEGER NOT NULL, currency TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 100,
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS shop_plans_owner
              ON shop_plans(owner_id,product_id,sort_order,id);
            CREATE TABLE IF NOT EXISTS shop_payment_methods(
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
              provider TEXT NOT NULL, name TEXT NOT NULL,
              instructions TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 100,
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS shop_payment_owner
              ON shop_payment_methods(owner_id,sort_order,id);
            CREATE TABLE IF NOT EXISTS shop_orders(
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
              telegram_user_id INTEGER NOT NULL,
              product_id TEXT NOT NULL, plan_id TEXT NOT NULL,
              product_name TEXT NOT NULL, plan_label TEXT NOT NULL,
              service_type TEXT NOT NULL, quota_bytes INTEGER NOT NULL,
              duration_days INTEGER NOT NULL, inbound_ids TEXT NOT NULL,
              limit_ip INTEGER NOT NULL, limit_hwid INTEGER NOT NULL,
              amount INTEGER NOT NULL, currency TEXT NOT NULL,
              payment_method_id TEXT NOT NULL, status TEXT NOT NULL,
              client_email TEXT NOT NULL DEFAULT '',
              subscription_url TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL, paid_at REAL NOT NULL DEFAULT 0,
              fulfilled_at REAL NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS shop_orders_owner
              ON shop_orders(owner_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS shop_orders_customer
              ON shop_orders(owner_id,telegram_user_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS shop_payment_events(
              event_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
              order_id TEXT NOT NULL, kind TEXT NOT NULL,
              reference TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS shop_payment_events_order
              ON shop_payment_events(order_id,at);
            """)

    @staticmethod
    def _panel_owner(actor: Actor) -> str:
        if actor.role not in ("owner", "reseller"):
            raise PermissionDenied("Owner or representative access required")
        return actor.id

    def _actor_for_owner(self, owner_id: str) -> Actor:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT role,permissions,disabled FROM api_admins WHERE id=?", (owner_id,)
            ).fetchone()
        if not row or row["role"] not in ("owner", "reseller") or row["disabled"]:
            raise PermissionDenied("Bot owner account is unavailable or disabled")
        try:
            permissions = json.loads(row["permissions"] or "{}")
        except (TypeError, ValueError):
            permissions = {}
        return Actor(owner_id, str(row["role"]), permissions)

    def _account_role(self, owner_id: str) -> str:
        return self._actor_for_owner(owner_id).role

    def bot_settings(self, actor: Actor) -> dict:
        owner_id = self._panel_owner(actor)
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT * FROM telegram_bots WHERE owner_id=?", (owner_id,)
            ).fetchone()
        if not row:
            return {
                "configured": False, "owner_id": owner_id, "enabled": False,
                "admin_telegram_id": 0, "bot_username": "", "last_error": "",
                "webhook_path": "", "token_hint": ""
            }
        token = self.auth.cipher.decrypt(row["token_enc"].encode()).decode()
        return {
            "configured": True, "owner_id": owner_id, "enabled": bool(row["enabled"]),
            "admin_telegram_id": row["admin_telegram_id"],
            "bot_username": row["bot_username"], "last_error": row["last_error"],
            "connected": bool(row["bot_username"] and not row["last_error"] and row["enabled"]),
            "webhook_path": "/telegram/" + row["public_id"],
            "token_hint": "…" + token[-6:], "updated_at": row["updated_at"]
        }

    def save_bot(self, actor: Actor, token: str | None,
                 admin_telegram_id: int, enabled: bool) -> dict:
        owner_id = self._panel_owner(actor)
        if type(admin_telegram_id) is not int or not 1 <= admin_telegram_id <= MAX_INT:
            raise PolicyError("Telegram admin ID must be a positive numeric ID")
        with self.store.lock:
            old = self.store.db.execute(
                "SELECT * FROM telegram_bots WHERE owner_id=?", (owner_id,)
            ).fetchone()
        raw = (token or "").strip()
        if not raw and not old:
            raise PolicyError("Bot token is required for first activation")
        if raw and not BOT_TOKEN_RE.fullmatch(raw):
            raise PolicyError("Invalid Telegram bot token format")
        token_enc = old["token_enc"] if not raw else self.auth.cipher.encrypt(raw.encode()).decode()
        now = time.time()
        public_id = old["public_id"] if old else secrets.token_urlsafe(12)
        webhook_secret = old["webhook_secret"] if old else secrets.token_urlsafe(32)
        created_at = old["created_at"] if old else now
        bot_username = (old["bot_username"] if old else "") if not raw else ""
        last_error = (old["last_error"] if old else "") if not raw else ""
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO telegram_bots(
                   owner_id,enabled,token_enc,admin_telegram_id,public_id,webhook_secret,
                   bot_username,last_error,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                   enabled=excluded.enabled,token_enc=excluded.token_enc,
                   admin_telegram_id=excluded.admin_telegram_id,
                   updated_at=excluded.updated_at""",
                (owner_id, int(enabled), token_enc, admin_telegram_id, public_id,
                 webhook_secret, bot_username, last_error, created_at, now)
            )
        self.manager.audit(
            actor, owner_id, "telegram_bot.save", owner_id,
            f"enabled={bool(enabled)}; admin_telegram_id={admin_telegram_id}"
        )
        return self.bot_settings(actor)

    @staticmethod
    def _telegram_call(token: str, method: str, payload: dict | None = None) -> Any:
        url = "https://api.telegram.org/bot" + token + "/" + method
        data = json.dumps(payload or {}).encode()
        request = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read(1024 * 1024)
        except HTTPError as ex:
            raise PolicyError("Telegram API HTTP " + str(ex.code)) from None
        except URLError:
            raise PolicyError("Telegram API is unreachable") from None
        except Exception:
            raise PolicyError("Telegram API connection failed") from None
        try:
            doc = json.loads(raw)
        except (TypeError, ValueError):
            raise PolicyError("Telegram API returned invalid JSON") from None
        if not isinstance(doc, dict) or not doc.get("ok"):
            description = str(doc.get("description") or "Telegram API rejected the request")
            raise PolicyError(description[:200])
        return doc.get("result")

    def connect_bot(self, actor: Actor, public_origin: str) -> dict:
        owner_id = self._panel_owner(actor)
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT * FROM telegram_bots WHERE owner_id=?", (owner_id,)
            ).fetchone()
        if not row:
            raise PolicyError("Telegram bot is not configured")
        if not row["enabled"]:
            result = self.bot_settings(actor)
            result["connected"] = False
            return result
        parsed = urlsplit(public_origin)
        if parsed.scheme != "https" or not parsed.netloc:
            error = "Telegram webhook requires an HTTPS public panel origin"
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE telegram_bots SET bot_username='',last_error=?,updated_at=? WHERE owner_id=?",
                    (error, time.time(), owner_id)
                )
            result = self.bot_settings(actor)
            result["connected"] = False
            return result
        token = self.auth.cipher.decrypt(row["token_enc"].encode()).decode()
        webhook_url = urlunsplit(
            (parsed.scheme, parsed.netloc, "/telegram/" + row["public_id"], "", "")
        )
        try:
            me = self._telegram_call(token, "getMe")
            if not isinstance(me, dict) or not me.get("username"):
                raise PolicyError("Telegram bot identity is unavailable")
            self._telegram_call(token, "setWebhook", {
                "url": webhook_url,
                "secret_token": row["webhook_secret"],
                "allowed_updates": ["message"],
                "drop_pending_updates": False
            })
        except PolicyError as ex:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE telegram_bots SET bot_username='',last_error=?,updated_at=? WHERE owner_id=?",
                    (str(ex)[:200], time.time(), owner_id)
                )
            self.manager.audit(
                actor, owner_id, "telegram_bot.connect_failed", owner_id, str(ex)[:200]
            )
            result = self.bot_settings(actor)
            result["connected"] = False
            return result
        with self.store.transaction() as db:
            db.execute(
                "UPDATE telegram_bots SET bot_username=?,last_error='',updated_at=? WHERE owner_id=?",
                (str(me["username"])[:128], time.time(), owner_id)
            )
        self.manager.audit(
            actor, owner_id, "telegram_bot.connected", owner_id, "webhook registered"
        )
        result = self.bot_settings(actor)
        result["connected"] = True
        result["webhook_url"] = webhook_url
        return result

    def _product(self, owner_id: str, product_id: str):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT * FROM shop_products WHERE id=? AND owner_id=?",
                (product_id, owner_id)
            ).fetchone()
        if not row:
            raise PolicyError("Product not found")
        return row

    @staticmethod
    def _plan_public(row: dict) -> dict:
        out = dict(row)
        out["active"] = bool(out["active"])
        out["inbound_ids"] = json.loads(out["inbound_ids"])
        return out

    def products(self, actor: Actor, *, active_only: bool = False) -> list[dict]:
        owner_id = self._panel_owner(actor)
        condition = "owner_id=?" + (" AND active=1" if active_only else "")
        with self.store.lock:
            products = [dict(r) for r in self.store.db.execute(
                f"SELECT * FROM shop_products WHERE {condition} ORDER BY sort_order,id",
                (owner_id,)
            )]
            plans = [dict(r) for r in self.store.db.execute(
                "SELECT * FROM shop_plans WHERE owner_id=? ORDER BY sort_order,id",
                (owner_id,)
            )]
        for product in products:
            product["active"] = bool(product["active"])
            product["plans"] = [
                self._plan_public(plan) for plan in plans
                if plan["product_id"] == product["id"]
                and (not active_only or plan["active"])
            ]
        return products

    def save_product(self, actor: Actor, product_id: str | None, name: str,
                     description: str, active: bool, sort_order: int) -> dict:
        owner_id = self._panel_owner(actor)
        name, description = name.strip(), description.strip()
        if not 1 <= len(name) <= 128 or len(description) > 2000:
            raise PolicyError("Invalid product name or description")
        if type(sort_order) is not int or not 0 <= sort_order <= 100000:
            raise PolicyError("Invalid product sort order")
        product_id = product_id or secrets.token_urlsafe(10)
        if not ID_RE.fullmatch(product_id):
            raise PolicyError("Invalid product ID")
        now = time.time()
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT created_at FROM shop_products WHERE id=? AND owner_id=?",
                (product_id, owner_id)
            ).fetchone()
            if not old and db.execute(
                "SELECT 1 FROM shop_products WHERE id=?", (product_id,)
            ).fetchone():
                raise PolicyError("Product ID belongs to another owner")
            db.execute(
                """INSERT INTO shop_products VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name,description=excluded.description,
                   active=excluded.active,sort_order=excluded.sort_order,
                   updated_at=excluded.updated_at""",
                (product_id, owner_id, name, description, int(active), sort_order,
                 old["created_at"] if old else now, now)
            )
        self.manager.audit(actor, owner_id, "shop.product.save", product_id,
                           f"active={bool(active)}")
        return next(x for x in self.products(actor) if x["id"] == product_id)

    def save_plan(self, actor: Actor, plan_id: str | None, product_id: str,
                  label: str, service_type: str, quota_bytes: int,
                  duration_days: int, inbound_ids: list[int],
                  limit_ip: int, limit_hwid: int, price_amount: int,
                  currency: str, active: bool, sort_order: int) -> dict:
        owner_id = self._panel_owner(actor)
        self._product(owner_id, product_id)
        label = label.strip()
        if not 1 <= len(label) <= 128:
            raise PolicyError("Invalid plan label")
        if service_type not in ("limited", "unlimited"):
            raise PolicyError("Invalid service type")
        if type(quota_bytes) is not int or not 0 <= quota_bytes <= MAX_INT:
            raise PolicyError("Invalid plan quota")
        if service_type == "limited" and quota_bytes <= 0:
            raise PolicyError("Limited plan requires positive quota")
        if service_type == "unlimited" and quota_bytes != 0:
            raise PolicyError("Unlimited plan quota must be zero")
        if type(duration_days) is not int or not 1 <= duration_days <= 3650:
            raise PolicyError("Invalid plan duration")
        if not isinstance(inbound_ids, list) or not inbound_ids:
            raise PolicyError("Plan requires at least one inbound")
        if any(type(x) is not int or x < 1 for x in inbound_ids) or                 len(set(inbound_ids)) != len(inbound_ids):
            raise PolicyError("Invalid plan inbound IDs")
        self.manager.check_inbounds(actor, owner_id, inbound_ids)
        profile = self.manager.profile(owner_id)
        for value, label_name, ceiling in (
            (limit_ip, "IP", int(profile.get("max_client_ips") or 0)),
            (limit_hwid, "HWID", int(profile.get("max_client_hwid") or 0)),
        ):
            if type(value) is not int or not 0 <= value <= 1000:
                raise PolicyError("Invalid " + label_name + " limit")
            if actor.role == "reseller" and ceiling and (value == 0 or value > ceiling):
                raise PolicyError(f"Plan {label_name} limit exceeds representative policy")
        if type(price_amount) is not int or not 0 <= price_amount <= MAX_INT:
            raise PolicyError("Invalid price")
        currency = currency.strip().upper()
        if not CURRENCY_RE.fullmatch(currency):
            raise PolicyError("Invalid currency")
        if type(sort_order) is not int or not 0 <= sort_order <= 100000:
            raise PolicyError("Invalid plan sort order")
        plan_id = plan_id or secrets.token_urlsafe(10)
        if not ID_RE.fullmatch(plan_id):
            raise PolicyError("Invalid plan ID")
        now = time.time()
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT created_at FROM shop_plans WHERE id=? AND owner_id=?",
                (plan_id, owner_id)
            ).fetchone()
            if not old and db.execute(
                "SELECT 1 FROM shop_plans WHERE id=?", (plan_id,)
            ).fetchone():
                raise PolicyError("Plan ID belongs to another owner")
            db.execute(
                """INSERT INTO shop_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                   product_id=excluded.product_id,label=excluded.label,
                   service_type=excluded.service_type,quota_bytes=excluded.quota_bytes,
                   duration_days=excluded.duration_days,inbound_ids=excluded.inbound_ids,
                   limit_ip=excluded.limit_ip,limit_hwid=excluded.limit_hwid,
                   price_amount=excluded.price_amount,currency=excluded.currency,
                   active=excluded.active,sort_order=excluded.sort_order,
                   updated_at=excluded.updated_at""",
                (plan_id, owner_id, product_id, label, service_type, quota_bytes,
                 duration_days, json.dumps(inbound_ids), limit_ip, limit_hwid,
                 price_amount, currency, int(active), sort_order,
                 old["created_at"] if old else now, now)
            )
        self.manager.audit(
            actor, owner_id, "shop.plan.save", plan_id,
            f"type={service_type}; amount={price_amount}; currency={currency}"
        )
        return next(
            plan for product in self.products(actor) for plan in product["plans"]
            if plan["id"] == plan_id
        )

    def payment_methods(self, actor: Actor, *, enabled_only: bool = False) -> list[dict]:
        owner_id = self._panel_owner(actor)
        condition = "owner_id=?" + (" AND enabled=1" if enabled_only else "")
        with self.store.lock:
            rows = [dict(r) for r in self.store.db.execute(
                f"SELECT * FROM shop_payment_methods WHERE {condition} ORDER BY sort_order,id",
                (owner_id,)
            )]
        for row in rows:
            row["enabled"] = bool(row["enabled"])
            row["ready"] = row["kind"] == "manual"
        return rows

    def save_payment_method(self, actor: Actor, method_id: str | None, kind: str,
                            provider: str, name: str, instructions: str,
                            enabled: bool, sort_order: int) -> dict:
        owner_id = self._panel_owner(actor)
        kind, provider = kind.strip().lower(), provider.strip().lower()
        if kind not in ("manual", "gateway"):
            raise PolicyError("Payment kind must be manual or gateway")
        if kind == "gateway" and provider in ("", "manual"):
            raise PolicyError("Gateway payment requires a provider")
        if not 1 <= len(name.strip()) <= 128 or len(instructions) > 4000:
            raise PolicyError("Invalid payment method")
        if type(sort_order) is not int or not 0 <= sort_order <= 100000:
            raise PolicyError("Invalid payment sort order")
        method_id = method_id or secrets.token_urlsafe(10)
        if not ID_RE.fullmatch(method_id):
            raise PolicyError("Invalid payment method ID")
        now = time.time()
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT created_at FROM shop_payment_methods WHERE id=? AND owner_id=?",
                (method_id, owner_id)
            ).fetchone()
            if not old and db.execute(
                "SELECT 1 FROM shop_payment_methods WHERE id=?", (method_id,)
            ).fetchone():
                raise PolicyError("Payment method ID belongs to another owner")
            db.execute(
                """INSERT INTO shop_payment_methods VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                   kind=excluded.kind,provider=excluded.provider,
                   name=excluded.name,instructions=excluded.instructions,
                   enabled=excluded.enabled,sort_order=excluded.sort_order,
                   updated_at=excluded.updated_at""",
                (method_id, owner_id, kind, provider or kind, name.strip(),
                 instructions.strip(), int(enabled), sort_order,
                 old["created_at"] if old else now, now)
            )
        self.manager.audit(
            actor, owner_id, "shop.payment_method.save", method_id,
            f"kind={kind}; provider={provider or kind}; enabled={bool(enabled)}"
        )
        return next(x for x in self.payment_methods(actor) if x["id"] == method_id)

    def _bot_by_public_id(self, public_id: str):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT * FROM telegram_bots WHERE public_id=?", (public_id,)
            ).fetchone()
        if not row or not row["enabled"]:
            raise PermissionDenied("Telegram bot is disabled or unknown")
        return row

    def verify_webhook(self, public_id: str, secret: str):
        row = self._bot_by_public_id(public_id)
        if not hmac.compare_digest(str(row["webhook_secret"]), str(secret or "")):
            raise PermissionDenied("Invalid Telegram webhook secret")
        return row

    def public_catalog(self, owner_id: str) -> list[dict]:
        return self.products(self._actor_for_owner(owner_id), active_only=True)

    def create_order(self, owner_id: str, telegram_user_id: int,
                     plan_id: str, payment_method_id: str) -> dict:
        if type(telegram_user_id) is not int or not 1 <= telegram_user_id <= MAX_INT:
            raise PolicyError("Invalid Telegram user ID")
        actor = self._actor_for_owner(owner_id)
        catalog = self.products(actor, active_only=True)
        plan = next(
            (x for product in catalog for x in product["plans"] if x["id"] == plan_id),
            None
        )
        if not plan:
            raise PolicyError("Plan is unavailable")
        product = next(x for x in catalog if x["id"] == plan["product_id"])
        method = next(
            (x for x in self.payment_methods(actor, enabled_only=True)
             if x["id"] == payment_method_id),
            None
        )
        if not method:
            raise PolicyError("Payment method is unavailable")
        if method["kind"] == "gateway" and not method["ready"]:
            raise PolicyError("Selected gateway adapter is not configured yet")
        order_id, now = secrets.token_urlsafe(12), time.time()
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO shop_orders(
                   id,owner_id,telegram_user_id,product_id,plan_id,product_name,
                   plan_label,service_type,quota_bytes,duration_days,inbound_ids,
                   limit_ip,limit_hwid,amount,currency,payment_method_id,status,
                   client_email,subscription_url,error,created_at,paid_at,fulfilled_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order_id, owner_id, telegram_user_id, product["id"], plan["id"],
                 product["name"], plan["label"], plan["service_type"],
                 plan["quota_bytes"], plan["duration_days"],
                 json.dumps(plan["inbound_ids"]), plan["limit_ip"], plan["limit_hwid"],
                 plan["price_amount"], plan["currency"], method["id"],
                 "pending_payment", "", "", "", now, 0, 0)
            )
        return self.order(owner_id, order_id)

    def order(self, owner_id: str, order_id: str) -> dict:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT * FROM shop_orders WHERE id=? AND owner_id=?",
                (order_id, owner_id)
            ).fetchone()
        if not row:
            raise PolicyError("Order not found")
        out = dict(row)
        out["inbound_ids"] = json.loads(out["inbound_ids"])
        return out

    def orders(self, actor: Actor, limit: int = 200) -> list[dict]:
        owner_id = self._panel_owner(actor)
        limit = max(1, min(int(limit), 500))
        with self.store.lock:
            rows = [dict(r) for r in self.store.db.execute(
                """SELECT * FROM shop_orders WHERE owner_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (owner_id, limit)
            )]
        for row in rows:
            row["inbound_ids"] = json.loads(row["inbound_ids"])
        return rows

    def customer_orders(self, owner_id: str, telegram_user_id: int,
                        limit: int = 10) -> list[dict]:
        with self.store.lock:
            rows = [dict(r) for r in self.store.db.execute(
                """SELECT * FROM shop_orders
                   WHERE owner_id=? AND telegram_user_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (owner_id, telegram_user_id, max(1, min(limit, 30)))
            )]
        for row in rows:
            row["inbound_ids"] = json.loads(row["inbound_ids"])
        return rows

    def confirm_manual(self, actor: Actor, order_id: str,
                       event_id: str, reference: str = "") -> dict:
        owner_id = self._panel_owner(actor)
        if not 12 <= len(event_id) <= 128:
            raise PolicyError("Payment event ID is required")
        order = self.order(owner_id, order_id)
        with self.store.lock:
            method = self.store.db.execute(
                """SELECT kind FROM shop_payment_methods
                   WHERE id=? AND owner_id=?""",
                (order["payment_method_id"], owner_id)
            ).fetchone()
        if not method or method["kind"] != "manual":
            raise PolicyError("Order is not using manual payment")
        now = time.time()
        duplicate = False
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT order_id FROM shop_payment_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing:
                if existing["order_id"] != order_id:
                    raise PolicyError("Payment event belongs to another order")
                duplicate = True
            else:
                current = db.execute(
                    "SELECT status FROM shop_orders WHERE id=? AND owner_id=?",
                    (order_id, owner_id)
                ).fetchone()
                if not current:
                    raise PolicyError("Order not found")
                if current["status"] not in (
                    "pending_payment", "paid", "fulfillment_failed"
                ):
                    raise PolicyError("Order cannot be paid in its current state")
                db.execute(
                    "INSERT INTO shop_payment_events VALUES(?,?,?,?,?,?)",
                    (event_id, owner_id, order_id, "manual_confirm",
                     reference[:256], now)
                )
                db.execute(
                    """UPDATE shop_orders SET status='paid',
                       paid_at=CASE WHEN paid_at=0 THEN ? ELSE paid_at END,error=''
                       WHERE id=? AND owner_id=?""",
                    (now, order_id, owner_id)
                )
        if duplicate:
            return self.order(owner_id, order_id)
        self.manager.audit(
            actor, owner_id, "shop.payment.confirm", order_id, "manual event=" + event_id
        )
        return self.fulfill(actor, order_id)

    def _order_email(self, owner_id: str, telegram_user_id: int, order_id: str) -> str:
        prefix = str(self.manager.profile(owner_id).get("prefix") or "")
        clean = re.sub(r"[^A-Za-z0-9]", "", order_id)[:8].lower()
        value = (prefix + f"tg{telegram_user_id}_{clean}")[:128].lower()
        if not NAME_RE.fullmatch(value):
            raise PolicyError(
                "Representative prefix cannot generate a valid client identity"
            )
        return value

    def fulfill(self, actor: Actor, order_id: str) -> dict:
        owner_id = self._panel_owner(actor)
        order = self.order(owner_id, order_id)
        if order["status"] == "fulfilled":
            return order
        if order["status"] not in ("paid", "fulfillment_failed"):
            raise PolicyError("Only paid orders can be fulfilled")
        email = order["client_email"] or self._order_email(
            owner_id, order["telegram_user_id"], order_id
        )
        now = time.time()
        with self.store.transaction() as db:
            db.execute(
                """UPDATE shop_orders SET status='fulfilling',
                   client_email=?,error='' WHERE id=? AND owner_id=?""",
                (email, order_id, owner_id)
            )
        client = {
            "email": email, "totalGB": order["quota_bytes"],
            "expiryTime": int((now + order["duration_days"] * 86400) * 1000),
            "limitIp": order["limit_ip"], "limitHwid": order["limit_hwid"],
            "tgId": order["telegram_user_id"], "enable": True,
            "comment": "DARK BOT ORDER " + order_id
        }
        try:
            result = self.manager.create(actor, owner_id, client, order["inbound_ids"])
            subscription = str(result.get("subscription_url") or "")
        except Exception as ex:
            with self.store.transaction() as db:
                db.execute(
                    """UPDATE shop_orders SET status='fulfillment_failed',error=?
                       WHERE id=? AND owner_id=?""",
                    (str(ex)[:500], order_id, owner_id)
                )
            self.manager.audit(
                actor, owner_id, "shop.fulfillment.failed", order_id, str(ex)[:300]
            )
            return self.order(owner_id, order_id)
        with self.store.transaction() as db:
            db.execute(
                """UPDATE shop_orders SET status='fulfilled',
                   subscription_url=?,error='',fulfilled_at=?
                   WHERE id=? AND owner_id=?""",
                (subscription, now, order_id, owner_id)
            )
        self.manager.audit(
            actor, owner_id, "shop.fulfillment.success", order_id, email
        )
        return self.order(owner_id, order_id)

    def telegram_reply(self, public_id: str, secret: str,
                       update: dict[str, Any]) -> dict:
        bot = self.verify_webhook(public_id, secret)
        owner_id = str(bot["owner_id"])
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return {"ok": True}
        sender, chat = message.get("from") or {}, message.get("chat") or {}
        try:
            user_id, chat_id = int(sender.get("id")), int(chat.get("id"))
        except (TypeError, ValueError):
            raise PolicyError("Invalid Telegram update identity")
        text = str(message.get("text") or "").strip()
        is_admin = user_id == int(bot["admin_telegram_id"])
        role = self._account_role(owner_id)
        keyboard = [["🛒 فروشگاه", "📦 سفارش‌های من"]]
        if is_admin:
            keyboard.append(["🛠 مدیریت"])
            if role == "owner":
                keyboard.append(["👥 نمایندگان"])
        reply_markup = {"keyboard": keyboard, "resize_keyboard": True}

        def reply(body: str) -> dict:
            return {
                "method": "sendMessage", "chat_id": chat_id, "text": body[:3900],
                "reply_markup": reply_markup, "disable_web_page_preview": True
            }

        if text in ("/start", "start", ""):
            suffix = "\nدسترسی مدیریت برای این Telegram ID فعال است." if is_admin else ""
            return reply("DARK XRAY\nفروش و مدیریت سرویس از همین ربات انجام می‌شود." + suffix)
        if text in ("🛒 فروشگاه", "/store"):
            catalog = self.public_catalog(owner_id)
            if not catalog:
                return reply("فعلاً محصول فعالی در فروشگاه وجود ندارد.")
            lines = ["🛒 فروشگاه"]
            for product in catalog:
                lines.append("\n" + product["name"])
                for plan in product["plans"]:
                    quota = (
                        "نامحدود" if plan["service_type"] == "unlimited"
                        else str(round(plan["quota_bytes"] / 1024**3, 2)) + " GB"
                    )
                    lines.append(
                        f"• {plan['label']} — {quota} / {plan['duration_days']} روز — "
                        f"{plan['price_amount']} {plan['currency']}\n  /buy {plan['id']}"
                    )
            return reply("\n".join(lines))
        if text.startswith("/buy "):
            plan_id = text.split(None, 1)[1].strip()
            methods = self.payment_methods(
                self._actor_for_owner(owner_id), enabled_only=True
            )
            ready = [m for m in methods if m["ready"]]
            if not ready:
                return reply("روش پرداخت آماده‌ای برای فروشگاه فعال نشده است.")
            order = self.create_order(owner_id, user_id, plan_id, ready[0]["id"])
            method = ready[0]
            return reply(
                f"سفارش {order['id']} ساخته شد.\n"
                f"مبلغ: {order['amount']} {order['currency']}\n"
                "وضعیت: در انتظار پرداخت\n\n"
                f"{method['instructions']}\n\n"
                "پس از تأیید پرداخت توسط ادمین، سرویس خودکار ساخته می‌شود."
            )
        if text in ("📦 سفارش‌های من", "/orders"):
            rows = self.customer_orders(owner_id, user_id)
            if not rows:
                return reply("هنوز سفارشی ندارید.")
            lines = ["📦 سفارش‌های من"]
            for row in rows:
                lines.append(
                    f"• {row['plan_label']} — {row['status']} — {row['id']}"
                )
                if row["status"] == "fulfilled" and row["subscription_url"]:
                    lines.append(row["subscription_url"])
            return reply("\n".join(lines))
        if text in ("🛠 مدیریت", "/admin"):
            if not is_admin:
                return reply("این بخش فقط برای ادمین عددی تنظیم‌شدهٔ ربات است.")
            actor = self._actor_for_owner(owner_id)
            with self.store.lock:
                clients = self.store.db.execute(
                    "SELECT COUNT(*) FROM clients WHERE owner=?", (owner_id,)
                ).fetchone()[0]
            pending = sum(
                1 for x in self.orders(actor, 500)
                if x["status"] in ("pending_payment", "paid", "fulfillment_failed")
            )
            return reply(
                f"🛠 مدیریت DARK\nکاربران: {clients}\n"
                f"سفارش‌های نیازمند بررسی: {pending}\n"
                "برای عملیات کامل از پنل وب استفاده کنید."
            )
        if text in ("👥 نمایندگان", "/representatives"):
            if not is_admin or role != "owner":
                return reply("این گزینه فقط در ربات مالک اصلی در دسترس است.")
            with self.store.lock:
                reps = [dict(r) for r in self.store.db.execute(
                    """SELECT p.id,p.name FROM owner_profiles p
                       JOIN api_admins a ON a.id=p.id
                       WHERE a.role='reseller' ORDER BY p.id"""
                )]
            body = "\n".join(
                "• " + r["name"] + " — " + r["id"] for r in reps
            ) if reps else "هنوز نماینده‌ای ساخته نشده است."
            return reply("👥 نمایندگان\n" + body)
        return reply("از منوی ربات استفاده کنید یا /store و /orders را بزنید.")


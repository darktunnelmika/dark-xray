import json

import pytest

from bot_commerce import BotCommerce
from dark_policy import Actor,PolicyError
from test_representatives_v2 import OWNER,create_inbound,env,rep_body


BOT_TOKEN="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_1234567890"


def _seller(env,*,volume_gib=5,unlimited=2):
    store,_,_,auth,c=env
    inbound_id=create_inbound(c)
    gib=1024**3
    body=rep_body(
        inbound_id,volume_credit_bytes=volume_gib*gib,
        unlimited_credit=unlimited,max_clients=20,prefix="s_",
        max_client_ips=2,max_client_hwid=2,
    )
    assert c.put("/api/resellers/seller",json=body).status_code==200
    _,principal=auth.login("seller","SellerPass88","","127.0.0.2")
    return inbound_id,principal.actor


def _catalog(commerce,actor,inbound_id,*,quota_gib=3,service_type="limited"):
    gib=1024**3
    product=commerce.save_product(actor,None,"DARK TURBO","Fast plan",True,10)
    quota=0 if service_type=="unlimited" else quota_gib*gib
    plan=commerce.save_plan(
        actor,None,product["id"],"30 Days",service_type,quota,30,[inbound_id],
        1,1,300_000,"IRT",True,10,
    )
    method=commerce.save_payment_method(
        actor,None,"manual","manual","Card to card",
        "Send the receipt to support.",True,10,
    )
    return product,plan,method


def test_bot_settings_encrypt_token_and_do_not_change_core_schema(env):
    store,_,_,auth,c=env
    response=c.put("/api/bot/settings",json={
        "token":BOT_TOKEN,"admin_telegram_id":99887766,"enabled":True,
    })
    assert response.status_code==200,response.text
    doc=response.json()
    assert doc["configured"] is True and doc["enabled"] is True
    assert doc["admin_telegram_id"]==99887766
    assert "token" not in doc and BOT_TOKEN not in response.text
    with store.lock:
        row=store.db.execute(
            "SELECT token_enc FROM telegram_bots WHERE owner_id='dark'"
        ).fetchone()
        version=store.db.execute("PRAGMA user_version").fetchone()[0]
    assert row and row["token_enc"]!=BOT_TOKEN
    assert auth.cipher.decrypt(row["token_enc"].encode()).decode()==BOT_TOKEN
    assert version==3


def test_representative_sale_snapshots_price_and_fulfills_through_existing_credit(env):
    store,_,_,_,c=env
    inbound_id,actor=_seller(env,volume_gib=5,unlimited=1)
    commerce=c.app.state.commerce
    _,plan,method=_catalog(commerce,actor,inbound_id,quota_gib=3)
    order=commerce.create_order("seller",445566,plan["id"],method["id"])
    assert order["status"]=="pending_payment"
    assert order["amount"]==300_000 and order["currency"]=="IRT"
    paid=commerce.confirm_manual(actor,order["id"],"manual-payment-0001","receipt-42")
    assert paid["status"]=="fulfilled"
    assert paid["client_email"].startswith("s_tg445566_")
    assert paid["subscription_url"]
    with store.lock:
        client=store.db.execute(
            "SELECT quota_bytes FROM clients WHERE id=?",(paid["client_email"],)
        ).fetchone()
        owner=store.db.execute(
            "SELECT volume_credit_bytes FROM owners WHERE id='seller'"
        ).fetchone()
        events=store.db.execute(
            "SELECT COUNT(*) FROM shop_payment_events WHERE event_id='manual-payment-0001'"
        ).fetchone()[0]
    assert client["quota_bytes"]==3*1024**3
    assert owner["volume_credit_bytes"]==5*1024**3
    rep=next(x for x in c.get("/api/resellers").json() if x["id"]=="seller")
    assert rep["volume_credit_remaining_bytes"]==2*1024**3
    again=commerce.confirm_manual(actor,order["id"],"manual-payment-0001","receipt-42")
    assert again["status"]=="fulfilled" and events==1


def test_paid_order_with_insufficient_credit_is_explicitly_retryable(env):
    store,_,_,_,c=env
    inbound_id,actor=_seller(env,volume_gib=1,unlimited=0)
    commerce=c.app.state.commerce
    _,plan,method=_catalog(commerce,actor,inbound_id,quota_gib=2)
    order=commerce.create_order("seller",112233,plan["id"],method["id"])
    result=commerce.confirm_manual(actor,order["id"],"manual-payment-0002")
    assert result["status"]=="fulfillment_failed"
    assert "volume credit" in result["error"].lower()
    with store.lock:
        assert store.db.execute(
            "SELECT COUNT(*) FROM clients WHERE owner='seller'"
        ).fetchone()[0]==0
    assert store.adjust_resource_credit(
        OWNER,"seller",2*1024**3,0,"bot-credit-retry-0001"
    ) is True
    retried=commerce.fulfill(actor,order["id"])
    assert retried["status"]=="fulfilled"


def test_telegram_admin_menu_is_numeric_id_scoped_and_rep_has_no_rep_factory(env):
    store,_,_,_,c=env
    _,actor=_seller(env)
    commerce=c.app.state.commerce
    settings=commerce.save_bot(actor,BOT_TOKEN,777001,True)
    with store.lock:
        cfg=store.db.execute(
            "SELECT public_id,webhook_secret FROM telegram_bots WHERE owner_id='seller'"
        ).fetchone()
    customer=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":777002},"chat":{"id":777002},"text":"/start"}},
    )
    admin=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":777001},"chat":{"id":777001},"text":"/start"}},
    )
    assert "🛠 مدیریت" not in json.dumps(customer,ensure_ascii=False)
    assert "🛠 مدیریت" in json.dumps(admin,ensure_ascii=False)
    assert "👥 نمایندگان" not in json.dumps(admin,ensure_ascii=False)
    assert settings["token_hint"].endswith(BOT_TOKEN[-6:])


def test_gateway_method_is_modeled_but_cannot_fake_success_without_adapter(env):
    _,_,_,_,c=env
    inbound_id,actor=_seller(env)
    commerce=c.app.state.commerce
    product=commerce.save_product(actor,None,"Gateway test","",True,10)
    plan=commerce.save_plan(
        actor,None,product["id"],"10 GB","limited",10*1024**3,30,[inbound_id],
        1,1,100_000,"IRT",True,10,
    )
    gateway=commerce.save_payment_method(
        actor,None,"gateway","custom","Online gateway","Redirect",True,10,
    )
    assert gateway["ready"] is False
    with pytest.raises(PolicyError,match="adapter"):
        commerce.create_order("seller",12345,plan["id"],gateway["id"])


def test_primary_bot_can_create_scoped_representative_without_user_supplied_password(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    commerce=c.app.state.commerce
    commerce.save_bot(OWNER,BOT_TOKEN,991100,True)
    with store.lock:
        cfg=store.db.execute(
            "SELECT public_id,webhook_secret FROM telegram_bots WHERE owner_id='dark'"
        ).fetchone()
    denied=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":991101},"chat":{"id":991101},
                    "text":f"/newrep agentbot 10 2 {inbound_id}"}},
    )
    assert "فقط" in denied["text"]
    response=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":991100},"chat":{"id":991100},
                    "text":f"/newrep agentbot 10 2 {inbound_id}"}},
    )
    assert "✅" in response["text"] and "Temporary password:" in response["text"]
    with store.lock:
        account=store.db.execute(
            "SELECT role,disabled FROM api_admins WHERE id='agentbot'"
        ).fetchone()
        profile=store.db.execute(
            "SELECT allowed,prefix FROM owner_profiles WHERE id='agentbot'"
        ).fetchone()
        owner=store.db.execute(
            "SELECT volume_credit_bytes,unlimited_credit FROM owners WHERE id='agentbot'"
        ).fetchone()
        audit=[dict(x) for x in store.db.execute(
            "SELECT action,detail FROM live_audit WHERE target='agentbot'"
        )]
    assert account["role"]=="reseller" and account["disabled"]==0
    assert json.loads(profile["allowed"])==[inbound_id]
    assert profile["prefix"]=="agentbot_"
    assert owner["volume_credit_bytes"]==10*1024**3
    assert owner["unlimited_credit"]==2
    password=response["text"].split("Temporary password: ",1)[1].splitlines()[0]
    assert password and all(password not in row["detail"] for row in audit)


def test_representative_bot_management_is_numeric_admin_only_and_owner_scoped(env):
    store,_,manager,_,c=env
    inbound_id,actor=_seller(env,volume_gib=20,unlimited=2)
    other=rep_body(
        inbound_id,volume_credit_bytes=20*1024**3,unlimited_credit=2,
        max_clients=20,prefix="o_",max_client_ips=2,max_client_hwid=2,
    )
    assert c.put("/api/resellers/other",json=other).status_code==200
    manager.create(actor,"seller",{"email":"s_alpha","totalGB":1024**3},[inbound_id])
    manager.create(OWNER,"other",{"email":"o_alpha","totalGB":1024**3},[inbound_id])
    commerce=c.app.state.commerce
    commerce.save_bot(actor,BOT_TOKEN,880001,True)
    with store.lock:
        cfg=store.db.execute(
            "SELECT public_id,webhook_secret FROM telegram_bots WHERE owner_id='seller'"
        ).fetchone()
    denied=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":880002},"chat":{"id":880002},
                    "text":"/disable s_alpha"}},
    )
    assert "فقط برای ادمین" in denied["text"]
    assert manager.detail(actor,"s_alpha")["client"]["enable"] is True
    own=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":880001},"chat":{"id":880001},
                    "text":"/disable s_alpha"}},
    )
    assert "غیرفعال" in own["text"]
    assert manager.detail(actor,"s_alpha")["client"]["enable"] is False
    cross=commerce.telegram_reply(
        cfg["public_id"],cfg["webhook_secret"],
        {"message":{"from":{"id":880001},"chat":{"id":880001},
                    "text":"/user o_alpha"}},
    )
    assert "قابل دسترسی نیست" in cross["text"]

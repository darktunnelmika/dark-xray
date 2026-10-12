"""P1 representative credit response / login continuity regressions.

Disposable TestClient databases; never touches production accounts or traffic.
"""
import sqlite3
from pathlib import Path

from test_representatives_v2 import env, create_inbound, rep_body


def test_credit_summary_never_scans_immutable_usage_ledger(env, monkeypatch):
    store, engine, manager, auth, client = env
    inbound = create_inbound(client)
    gib = 1024 ** 3
    response = client.put(
        "/api/resellers/seller",
        json=rep_body(inbound, volume_credit_bytes=2*gib,
                      unlimited_credit=3, max_clients=20),
    )
    assert response.status_code == 200, response.text
    # An ordinary historical read must still be exact and available.
    before = client.get("/api/resellers")
    assert before.status_code == 200
    assert before.json()[0]["traffic_totals_available"] is True
    assert before.json()[0]["used_bytes"] == 0

    def no_ledger_read(*args, **kwargs):
        raise AssertionError("Historical ledger was scanned by the quick credit API")

    monkeypatch.setattr(store, "_usage", no_ledger_read)
    quick = client.get("/api/resellers/quick")
    assert quick.status_code == 200, quick.text
    summary = quick.json()[0]
    assert summary["id"] == "seller"
    assert summary["volume_credit_bytes"] == 2*gib
    assert summary["volume_credit_remaining_bytes"] == 2*gib
    assert summary["unlimited_credit_remaining"] == 3
    assert summary["used_bytes"] is None
    assert summary["lifetime_used_bytes"] is None
    assert summary["traffic_totals_available"] is False

    quick_owners = client.get("/api/owners/quick")
    assert quick_owners.status_code == 200, quick_owners.text
    owners = {r["id"]:r for r in quick_owners.json()}
    assert "dark" in owners and "seller" in owners
    assert owners["seller"]["volume_credit_remaining_bytes"] == 2*gib

    tx = {"volume_bytes":gib,"unlimited_units":2,"event_id":"representative-fastpath-test-0001"}
    one = client.post("/api/resellers/seller/credits",json=tx)
    two = client.post("/api/resellers/seller/credits",json=tx)
    assert one.status_code == two.status_code == 200, (one.text,two.text)
    assert one.json()["recorded"] is True
    assert two.json()["recorded"] is False
    rep = one.json()["representative"]
    assert rep["volume_credit_bytes"] == 3*gib
    assert rep["unlimited_credit"] == 5
    assert rep["used_bytes"] is None
    with store.lock:
        assert store.db.execute(
            "SELECT COUNT(*) FROM resource_credit_ledger WHERE event_id=?",
            (tx["event_id"],)
        ).fetchone()[0] == 1


def test_credit_only_edit_preserves_reseller_login_session_but_disabling_revokes(env):
    store, engine, manager, auth, client = env
    inbound = create_inbound(client)
    gib = 1024**3
    create = client.put("/api/resellers/seller",json=rep_body(
        inbound,volume_credit_bytes=4*gib,unlimited_credit=4))
    assert create.status_code == 200,create.text
    # Auth.login inserts a real interactive session in the disposable fixture.
    auth.login("seller","SellerPass88","","127.0.0.2")
    with store.lock:
        sessions = store.db.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE admin_id='seller'"
        ).fetchone()[0]
    assert sessions==1
    edit = client.put("/api/resellers/seller",json=rep_body(
        inbound,password=None,volume_credit_bytes=5*gib,unlimited_credit=5))
    assert edit.status_code == 200,edit.text
    with store.lock:
        assert store.db.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE admin_id='seller'"
        ).fetchone()[0] == sessions
    assert edit.json()["volume_credit_bytes"] == 5*gib
    assert edit.json()["unlimited_credit"] == 5

    disabled = client.put("/api/resellers/seller",json=rep_body(
        inbound,password=None,enabled=False,volume_credit_bytes=5*gib,
        unlimited_credit=5))
    assert disabled.status_code == 200, disabled.text
    with store.lock:
        assert store.db.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE admin_id='seller'"
        ).fetchone()[0] == 0


def test_exhausted_volume_credit_is_not_confused_with_unlimited_units(env):
    store, engine, manager, auth, client = env
    inbound = create_inbound(client)
    gib = 1024**3
    rep = client.put("/api/resellers/seller",json=rep_body(
        inbound,volume_credit_bytes=2*gib,unlimited_credit=200,
        max_clients=0,prefix=""))
    assert rep.status_code == 200,rep.text
    def new_user(name,size):
        return client.post("/api/clients",json={
            "owner":"seller",
            "client":{"email":name,"totalGB":size,"limitIp":1,"limitHwid":1},
            "inboundIds":[inbound],
        })
    assert new_user("two_gb",2*gib).status_code == 202
    blocked=new_user("one_more_gb",gib)
    assert blocked.status_code == 400
    assert "volume credit" in blocked.text.lower()
    # User-month unlimited balance must not silently become volumetric credit.
    pos=client.get("/api/resellers/quick").json()[0]
    assert pos["volume_credit_remaining_bytes"] == 0
    assert pos["unlimited_credit_remaining"] == 200
    add=client.post("/api/resellers/seller/credits",json={
        "volume_bytes":gib,"unlimited_units":0,
        "event_id":"volume-replenish-unique-00001",
    })
    assert add.status_code == 200,add.text
    assert new_user("one_more_gb",gib).status_code == 202
    with store.lock:
        credit=store.db.execute(
            "SELECT volume_credit_bytes,unlimited_credit FROM owners WHERE id='seller'"
        ).fetchone()
        assert tuple(credit) == (3*gib,200)
        assert store.db.execute(
            "SELECT COUNT(*) FROM clients WHERE owner='seller'"
        ).fetchone()[0]==2

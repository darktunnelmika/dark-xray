"""Transient Node control failures must not flap healthy inbounds/routes.

This is only UI/control-plane hysteresis. Expired Hub leases, stopped Xray,
security/identity violations and explicit disabled/maintenance remain blockers.
"""
import time

import pytest

import nodes as nodes_mod
from auth import Auth
from dark_policy import Store
from nodes import NodeRegistry


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        nodes_mod.socket, "getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    store = Store(tmp_path / "dark.sqlite3")
    auth = Auth(store, tmp_path / "secret.key")
    registry = NodeRegistry(store, auth.cipher)
    registry.put("n1", "Test Netherlands", "https://node.example", "dkn_" + ("A" * 60), True)
    registry._request_ok("n1", 17)
    yield registry, store
    store.close()


def test_transient_timeouts_do_not_flicker_online_or_fake_recovery(nodes):
    registry, store = nodes
    original = registry.list()[0]
    assert original["online"] is True and original["telemetry_state"] == "fresh"
    previous_recovery = original["recovery_count"]
    previous_offline = original["last_offline_at"]

    for count in (1, 2):
        registry._request_failed("n1", "Node connection failed: Timeout")
        state = registry.list()[0]
        assert state["failure_count"] == count
        assert state["last_error"]
        assert state["telemetry_state"] == "fresh"
        assert state["online"] is True
        assert state["operational_health"]["state"] == "warning"
        assert state["last_offline_at"] == previous_offline

    registry._request_ok("n1", 18)
    restored = registry.list()[0]
    assert restored["failure_count"] == 0 and restored["last_error"] == ""
    assert restored["recovery_count"] == previous_recovery
    assert restored["online"] is True


def test_three_consecutive_failures_are_offline_then_recovered(nodes):
    registry, store = nodes
    for count in (1, 2, 3):
        registry._request_failed("n1", "Node connection failed: Timeout")
    down = registry.list()[0]
    assert down["failure_count"] == 3
    assert down["telemetry_state"] == "offline" and down["online"] is False
    assert down["last_offline_at"] > 0
    registry._request_ok("n1", 12)
    up = registry.list()[0]
    assert up["online"] is True and up["telemetry_state"] == "fresh"
    assert up["recovery_count"] == down["recovery_count"] + 1


@pytest.mark.parametrize("message", [
    "Node response installation identity mismatch",
    "Node credential cannot be decrypted",
    "Node HTTP 401: Token invalid",
    "Node HTTP 403: Denied",
    "Node connection failed: SSLCertVerificationError",
])
def test_identity_auth_and_tls_fail_closed_immediately(nodes, message):
    registry, _store = nodes
    registry._request_failed("n1", message)
    result = registry.list()[0]
    assert result["failure_count"] == 1
    assert result["online"] is False
    assert result["telemetry_state"] == "offline"
    assert result["last_offline_at"] > 0


def test_three_probe_windows_then_stale_but_not_deleted(nodes):
    registry, store = nodes
    now = time.time()
    with store.transaction() as db:
        db.execute("UPDATE remote_nodes SET last_seen=? WHERE id='n1'", (now - 40,))
    row = registry.list()[0]
    assert row["telemetry_state"] == "fresh" and row["online"] is True
    with store.transaction() as db:
        db.execute("UPDATE remote_nodes SET last_seen=? WHERE id='n1'", (now - 50,))
    row = registry.list()[0]
    assert row["telemetry_state"] == "stale" and row["online"] is True
    with store.transaction() as db:
        db.execute("UPDATE remote_nodes SET last_seen=? WHERE id='n1'", (now - 185,))
    row = registry.list()[0]
    assert row["telemetry_state"] == "offline" and row["online"] is False
    assert bool(registry.get("n1")["enabled"]) is True


def test_deployment_readiness_is_not_revoked_by_first_two_timeouts(nodes):
    registry, _store = nodes
    node = registry.list()[0]
    node.update(data_address="203.0.113.9", failover_enabled=True)
    healthy = {
        "local_inbound_id": 17, "remote_inbound_id": 7,
        "last_error": "", "node_id": "n1"
    }
    # Existing persisted assignment state is not coupled to telemetry freshness.
    assert NodeRegistry._assignment_state(node, healthy)["deployment_state"] == "deployed"
    registry._request_failed("n1", "Node connection failed: Timeout")
    node = registry.list()[0]
    node.update(data_address="203.0.113.9", failover_enabled=True)
    assert NodeRegistry._assignment_state(node, healthy)["deployment_state"] == "deployed"
    assert NodeRegistry._assignment_state(node, healthy)["failover_reason"] == "ready"
    registry._request_failed("n1", "Node connection failed: Timeout")
    node = registry.list()[0]
    node.update(data_address="203.0.113.9", failover_enabled=True)
    assert NodeRegistry._assignment_state(node, healthy)["failover_reason"] == "ready"
    registry._request_failed("n1", "Node connection failed: Timeout")
    node = registry.list()[0]
    node.update(data_address="203.0.113.9", failover_enabled=True)
    assert NodeRegistry._assignment_state(node, healthy)["deployment_state"] == "deployed"
    assert NodeRegistry._assignment_state(node, healthy)["failover_reason"] == "node_offline"

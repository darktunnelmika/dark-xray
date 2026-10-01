"""V5 regressions: truthful results, selected origin, bounded read-only scans."""
import json
import pytest
from test_traffic_matrix_api import env, _seed_warp, _registered_warp
from warp_paths import warp_endpoint_candidates, warp_scan_results
from nodes import NodeRegistry


def result(endpoint, **extra):
    return {"endpoint": endpoint, "success": True, "warpVerified": True,
            "delayMs": 35.0, "lossPercent": 0, "jitterMs": 1,
            "egress": {"country": "DE", "colo": "FRA", "warp": "on"}, **extra}


@pytest.mark.parametrize("changes,status", [({}, "healthy"), ({"lossPercent": 50}, "degraded"),
    ({"delayMs": 501}, "degraded"), ({"jitterMs": 101}, "degraded"),
    ({"jitterMs": None}, "degraded"), ({"success": False}, "failed"),
    ({"warpVerified": False}, "failed"), ({"delayMs": float("nan")}, "failed"),
    ({"delayMs": True}, "failed"), ({"delayMs": -1}, "failed")])
def test_health_is_measured_not_assumed(changes, status):
    row = warp_scan_results(["162.159.192.1:2408"], [result("162.159.192.1:2408", **changes)])[0]
    assert row["status"] == status
    assert row["selected"] is False
    assert row["latencyKind"] == "http-through-warp"
    if status == "failed":
        assert row["country"] == "" and row["colo"] == ""


def test_missing_results_stay_visible_and_unknown_locations_are_not_fabricated():
    endpoints = warp_endpoint_candidates()
    rows = warp_scan_results(endpoints, [result(endpoints[0])])
    assert len(rows) == len(set(endpoints)) > 1
    assert sum(row["ready"] for row in rows) == 1
    assert all(row["status"] == "failed" and row["country"] == "" for row in rows[1:])
    assert all(row["error"] == "No result returned for endpoint" for row in rows[1:])


def test_server_listing_is_read_only_and_has_real_apply_states(env, monkeypatch):
    store, eng, client, _iid = env
    for runtime, expected in [({"state": "running", "dirty": False}, "applied"),
                               ({"state": "running", "dirty": True}, "pending"),
                               ({"state": "running", "last_error": "rejected"}, "error")]:
        monkeypatch.setattr(eng, "runtime_state", lambda: runtime)
        response = client.get("/api/xray-settings/servers")
        assert response.status_code == 200
        assert response.json()["items"][0]["applyState"]["status"] == expected
        assert response.json()["productionTrafficMutation"] is False
    assert store.db.execute("SELECT COUNT(*) FROM warp_profiles").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM traffic_matrix").fetchone()[0] == 0


def test_scan_batches_and_partial_failures_cannot_mutate_active_profile(env, monkeypatch):
    import server
    store, eng, client, _iid = env
    _seed_warp(store)
    before = eng.warp_profile("hub")
    monkeypatch.setattr(eng, "_binary", lambda: "/bin/true")
    calls = []
    def probe(binary, assets, outbounds, **kwargs):
        calls.append(outbounds)
        assert len(outbounds) <= 4
        if len(calls) == 2:
            raise server.OutboundProbeError("fixture timeout")
        # Simulate the historical single-item/incomplete response.
        item = outbounds[0]
        return [result(item["settings"]["peers"][0]["endpoint"], tag=item["tag"])]
    monkeypatch.setattr(server, "probe_outbounds", probe)
    monkeypatch.setattr(eng, "apply", lambda **kw: pytest.fail("scan must not apply"))
    response = client.post("/api/traffic-matrix/warp/scan", json={"server": "hub"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(calls) > 1
    assert body["resultCount"] == len(warp_endpoint_candidates("162.159.192.1:2408"))
    assert body["productionTrafficMutation"] is False
    assert sum(x["ready"] for x in body["items"]) == len(calls)-1
    assert any(x["error"] == "fixture timeout" for x in body["items"])
    assert eng.warp_profile("hub") == before


def test_node_scan_uses_selected_node_and_probe_only_pending_profile(env, monkeypatch):
    import server
    store, eng, client, _iid = env
    profile = _registered_warp()["outbound"]
    scope = "node:fixture-node"
    with store.transaction() as db:
        db.execute("INSERT INTO warp_pending_profiles(scope,outbound_json,device_id,created_at,updated_at) VALUES(?,?,?,1,1)",
                   (scope, json.dumps(profile), "fixture"))
    monkeypatch.setattr(NodeRegistry, "list", lambda self: [{"id": "fixture-node", "name": "Fixture Node", "online": True}])
    calls = []
    def remote(self, node_id, tag, endpoints, **kw):
        calls.append((node_id, endpoints, kw))
        assert node_id == "fixture-node"
        assert kw["outbound"] == profile
        return {"items": [result(endpoint) for endpoint in endpoints]}
    monkeypatch.setattr(NodeRegistry, "warp_endpoint_probe", remote)
    monkeypatch.setattr(server, "probe_outbounds", lambda *a, **kw: pytest.fail("Node scan must never run on Hub"))
    body = client.post("/api/traffic-matrix/warp/scan", json={"server": scope}).json()
    assert body["server"]["id"] == scope
    assert body["pendingRegistration"] is True and body["selected"] == ""
    assert len(body["items"]) > 4 and max(len(call[1]) for call in calls) <= 4
    assert eng.warp_profile(scope) is None


def test_rejected_selected_route_restores_old_profile(env, monkeypatch):
    import server
    store, eng, client, _iid = env
    _seed_warp(store)
    before = eng.warp_profile("hub")
    monkeypatch.setattr(eng, "_binary", lambda: "/bin/true")
    monkeypatch.setattr(server, "probe_outbounds", lambda *a, **kw: [result("162.159.192.5:2408")])
    def rejected(**kwargs):
        raise RuntimeError("fixture apply rejected")
    monkeypatch.setattr(eng, "apply", rejected)
    response = client.post("/api/traffic-matrix/warp/endpoint", json={"server": "hub", "endpoint": "162.159.192.5:2408"})
    assert response.status_code == 409
    assert eng.warp_profile("hub") == before


def test_node_pending_ack_is_not_reported_as_applied(env, monkeypatch):
    _store, _eng, client, _iid = env
    node = {"id": "fixture-node", "name": "Fixture", "online": True,
            "health": {"core": {"state": "running", "dirty": False}},
            "desired_state": {"revision": 3, "applied_revision": 2, "pending": True}}
    monkeypatch.setattr(NodeRegistry, "list", lambda self: [node])
    body = client.get("/api/xray-settings/servers").json()
    assert body["items"][1]["applyState"]["status"] == "pending"
    node["desired_state"].update(applied_revision=3, pending=False)
    assert client.get("/api/xray-settings/servers").json()["items"][1]["applyState"]["status"] == "applied"

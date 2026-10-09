"""Grant renewal should not resend unchanged full Node configs every 5 seconds.

All lease safety decisions remain strict: the Hub stats and policy checks
still run, and a pending/error/mismatched desired state is synced and
acknowledged before granting a lease.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dark_policy import PolicyError
from node_lease_sync import renew_accounting_lease


class Rows:
    def fetchall(self):
        return []


def setup_lease(*,pending=False,failed_apply=False):
    digest = "a" * 64
    current = {
        "revision": 9, "hash": digest,
        "applied_revision": 8 if pending else 9,
        "applied_hash": "b" * 64 if pending else digest,
        "pending": bool(pending), "last_error": ""
    }
    apply_calls = []
    lease_posts = []

    def apply(node_id,state,**_kw):
        apply_calls.append(node_id)
        if not failed_apply:
            current.update(applied_revision=9, applied_hash=digest, pending=False)

    def request(node_id,path,method,body,timeout):
        lease_posts.append((node_id,path,method,body,timeout))
        return {"lease":{"valid":True,"remaining_seconds":60}}, 12

    nodes = SimpleNamespace(
        installations=SimpleNamespace(operation=lambda node: contextlib.nullcontext()),
        _allowed_traffic_clients=lambda node: set(),
        sync_desired_state=apply,
        desired_state=lambda node,**kw:dict(current),
        commands=SimpleNamespace(status=lambda node: {"revision": 4}),
        _request=request
    )
    manager=SimpleNamespace(
        store=SimpleNamespace(lock=contextlib.nullcontext(),
                              db=SimpleNamespace(execute=lambda *args:Rows())),
        last_error="",tick=Mock(),
    )
    engine=SimpleNamespace(
        collect_stats=Mock(),stats_error="",
        config=SimpleNamespace(writes_enabled=True),
    )
    desired=lambda node:{"revision":9,"hash":digest}
    bundles=Mock(return_value=[])
    return nodes,manager,engine,desired,bundles,apply_calls,lease_posts


def test_identical_durable_node_state_skips_second_full_apply():
    nodes,manager,engine,desired,bundles,apply_calls,posts=setup_lease()
    result=renew_accounting_lease(nodes,manager,engine,"n",
        {"accounting_lease":"z"*64},desired,bundles)
    assert result["valid"] is True
    assert apply_calls == [] and bundles.call_count==0
    engine.collect_stats.assert_called_once_with(force=True,strict=True)
    manager.tick.assert_called_once_with(suppress=False)
    assert len(posts)==1 and posts[0][2]=="POST"
    assert posts[0][3]["revision"]==9 and posts[0][3]["hash"]=="a"*64


def test_changed_node_state_must_be_applied_and_acknowledged_before_lease():
    nodes,manager,engine,desired,bundles,apply_calls,posts=setup_lease(pending=True)
    result=renew_accounting_lease(nodes,manager,engine,"n",
        {"accounting_lease":"z"*64},desired,bundles)
    assert result["valid"] is True
    assert apply_calls==["n"] and bundles.call_count==1
    assert len(posts)==1


def test_unacknowledged_node_does_not_get_any_lease():
    nodes,manager,engine,desired,bundles,apply_calls,posts=setup_lease(pending=True,failed_apply=True)
    with pytest.raises(PolicyError,match="not acknowledged"):
        renew_accounting_lease(nodes,manager,engine,"n",
            {"accounting_lease":"z"*64},desired,bundles)
    assert apply_calls==["n"] and not posts


def test_unhealthy_hub_does_not_use_fast_path():
    nodes,manager,engine,desired,bundles,apply_calls,posts=setup_lease()
    engine.stats_error="accounting broken"
    with pytest.raises(PolicyError,match="not healthy"):
        renew_accounting_lease(nodes,manager,engine,"n",
            {"accounting_lease":"z"*64},desired,bundles)
    assert not posts

"""Regression checks for monitoring hysteresis and fail-closed Node watchdog.

Only fake engines and disposable SQLite stores; never touches running services.
"""
import threading
from unittest.mock import Mock

from auth import Auth
from dark_policy import Store
from node_runtime import LeaseGuard
from nodes import NodeRegistry


class FakeLease:
    def __init__(self, valid=True):
        self.required = True
        self.valid = valid

    @property
    def allowed(self):
        return self.valid


class BusyLock:
    def acquire(self, timeout=None):
        return False


class FakeEngine:
    def __init__(self, lock=None):
        self.lock = lock if lock is not None else threading.RLock()
        self.running = True


class FakeRuntime:
    def __init__(self, valid=True, lock=None, stop_succeeds=True):
        self.hub_lease = FakeLease(valid)
        self.engine = FakeEngine(lock)
        self.pause_calls = 0
        self.stop_succeeds = stop_succeeds

    def pause_expired_lease(self):
        self.pause_calls += 1
        if self.stop_succeeds:
            self.engine.running = False


def guard(runtime):
    result = LeaseGuard(runtime)
    result.watchdog = Mock(enabled=True, seconds=30)
    return result


def test_valid_lease_feeds_watchdog_even_during_last_thirty_seconds():
    runtime = FakeRuntime(valid=True, lock=BusyLock())
    safety = guard(runtime)
    safety.tick()
    safety.watchdog.notify.assert_called_once()
    safety.watchdog.trigger.assert_not_called()
    assert runtime.engine.running


def test_expired_lease_with_busy_engine_triggers_cgroup_fence():
    runtime = FakeRuntime(valid=False, lock=BusyLock())
    safety = guard(runtime)
    safety.tick()
    safety.watchdog.notify.assert_not_called()
    safety.watchdog.trigger.assert_called_once()
    assert safety.last_error == 'lease_expired_engine_busy'


def test_expired_lease_with_stop_confirmed_keeps_agent_alive():
    runtime = FakeRuntime(valid=False)
    safety = guard(runtime)
    safety.tick()
    assert runtime.pause_calls == 1
    assert not runtime.engine.running
    safety.watchdog.notify.assert_called_once()
    safety.watchdog.trigger.assert_not_called()


def test_expired_lease_with_still_running_xray_triggers_fence():
    runtime = FakeRuntime(valid=False, stop_succeeds=False)
    safety = guard(runtime)
    safety.tick()
    safety.watchdog.trigger.assert_called_once()
    safety.watchdog.notify.assert_not_called()


def test_transient_node_request_failure_does_not_flip_live_or_change_assignment(tmp_path, monkeypatch):
    import nodes as nodes_module
    monkeypatch.setattr(nodes_module.socket, 'getaddrinfo',
                        lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))])
    store = Store(tmp_path / 'node-stability.sqlite3')
    registry = NodeRegistry(store, Auth(store, tmp_path / 'key').cipher)
    try:
        registry.put('stable', 'Stable', 'https://stable.example',
                     'dkn_' + 'A' * 60, True)
        registry._request_ok('stable', 5)
        before = registry.list()[0]
        assert before['online'] and before['telemetry_state'] == 'fresh'
        assignments = before['assignments']
        for count in (1, 2):
            registry._request_failed('stable', 'transient timeout')
            state = registry.list()[0]
            assert state['failure_count'] == count
            assert state['online'] and state['telemetry_state'] == 'fresh'
            assert state['assignments'] == assignments
        registry._request_failed('stable', 'third consecutive timeout')
        offline = registry.list()[0]
        assert offline['failure_count'] == 3
        assert not offline['online'] and offline['telemetry_state'] == 'stale'
        assert offline['assignments'] == assignments
        registry._request_ok('stable', 5)
        recovered = registry.list()[0]
        assert recovered['online'] and recovered['failure_count'] == 0
    finally:
        registry.close()
        store.close()

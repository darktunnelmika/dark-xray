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
            registry._request_failed('stable', 'Node connection failed: TimeoutError')
            state = registry.list()[0]
            assert state['failure_count'] == count
            assert state['online'] and state['telemetry_state'] == 'fresh'
            assert state['last_offline_at'] == 0
            assert state['recovery_count'] == 0
            assert state['assignments'] == assignments
        registry._request_failed('stable', 'Node connection failed: TimeoutError')
        offline = registry.list()[0]
        assert offline['failure_count'] == 3 and offline['last_offline_at'] > 0
        assert not offline['online'] and offline['telemetry_state'] == 'stale'
        assert offline['assignments'] == assignments
        registry._request_ok('stable', 5)
        recovered = registry.list()[0]
        assert recovered['online'] and recovered['failure_count'] == 0
        assert recovered['recovery_count'] == 1 and recovered['last_recovered_at'] > 0
    finally:
        registry.close()
        store.close()


def test_security_error_is_immediate_and_not_cleared_by_a_timeout(tmp_path, monkeypatch):
    import nodes as nodes_module
    monkeypatch.setattr(nodes_module.socket, 'getaddrinfo',
                        lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))])
    store = Store(tmp_path / 'security.sqlite3')
    registry = NodeRegistry(store, Auth(store, tmp_path / 'key').cipher)
    try:
        registry.put('secure', 'Secure', 'https://secure.example', 'dkn_'+'A'*60, True)
        registry.put('healthy', 'Healthy', 'https://healthy.example', 'dkn_'+'B'*60, True)
        registry._request_ok('healthy', 5)
        for error in ('Node response installation identity mismatch',
                      'Node connection failed: SSLCertVerificationError',
                      'Node HTTP 401', 'Node HTTP 403', 'Node returned invalid JSON'):
            registry._request_ok('secure', 5)
            registry._request_failed('secure', error)
            states = {row['id']: row for row in registry.list()}
            state = states['secure']
            assert state['failure_count'] == 1 and not state['online']
            assert state['last_offline_at'] > 0 and states['healthy']['online']
            candidate = dict(state, enabled=True, failover_enabled=True,
                             data_address='edge.example', maintenance=False)
            assignment = {'remote_inbound_id': 7, 'local_inbound_id': 1, 'last_error': ''}
            clean = dict(candidate, last_error='', failure_count=0)
            assert registry._assignment_state(clean, assignment)['failover_ready']
            assert not registry._assignment_state(candidate, assignment)['failover_ready']
            registry._request_failed('secure', 'Node connection failed: TimeoutError')
            state = next(row for row in registry.list() if row['id'] == 'secure')
            assert state['last_error'] == error and not state['online']
            assert state['failure_count'] == 2
        registry._request_ok('secure', 5)
        assert all(row['online'] for row in registry.list())
    finally:
        registry.close()
        store.close()

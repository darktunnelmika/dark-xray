"""Independent OS watchdog budget; disposable sockets and SQLite only."""
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dark_policy import Store
from node_runtime import HubLease, SystemdWatchdog, LeaseGuard


@pytest.mark.parametrize('remaining,expected', [(59.0, 30000000), (25.0, 24000000), (12.0, 11000000), (11.0, 10000000)])
def test_watchdog_budget_never_outlives_valid_lease(tmp_path, monkeypatch, remaining, expected):
    path = str(tmp_path / 'notify.sock')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(path)
        receiver.settimeout(.2)
        monkeypatch.setenv('NOTIFY_SOCKET', path)
        monkeypatch.setenv('WATCHDOG_USEC', '30000000')
        monkeypatch.delenv('WATCHDOG_PID', raising=False)
        watchdog = SystemdWatchdog()
        lease = SimpleNamespace(required=True, lock=threading.RLock(), deadline=100+remaining, clock=lambda: 100)
        assert watchdog.notify(lease=lease)
        fields = dict(line.split('=', 1) for line in receiver.recv(200).decode().splitlines())
        assert fields['WATCHDOG'] == '1'
        assert int(fields['WATCHDOG_USEC']) == expected
        assert expected / 1000000 < remaining


@pytest.mark.parametrize('remaining', [10.0, 9.99, 4.0, 1.006, 1.0, .5, 0.0, -1.0])
def test_no_unbounded_keepalive_near_or_after_expiry(monkeypatch, remaining):
    watchdog = SystemdWatchdog()
    watchdog.enabled = True
    watchdog.seconds = 30
    watchdog._send = Mock()
    lease = SimpleNamespace(required=True, lock=threading.RLock(), deadline=100+remaining, clock=lambda: 100)
    assert watchdog.notify(lease=lease) is False
    watchdog._send.assert_not_called()


def test_guard_cannot_replace_bounded_timer_with_unbounded_ping():
    lease = SimpleNamespace(required=True, allowed=True)
    engine = SimpleNamespace(running=True, lock=threading.RLock())
    runtime = SimpleNamespace(engine=engine, hub_lease=lease)
    guard = LeaseGuard(runtime)
    guard.watchdog.notify = Mock(return_value=False)
    guard.tick()
    assert guard.watchdog.notify.call_count == 2
    for call in guard.watchdog.notify.call_args_list:
        assert call.kwargs['lease'] is lease


def test_repeated_grants_do_not_rewrite_durable_enforcement_latch(tmp_path):
    store = Store(tmp_path / 'db')
    try:
        now = [100.0]
        lease = HubLease(store, 'node', required=True, clock=lambda: now[0])
        statements = []
        store.db.set_trace_callback(statements.append)
        state = {'appliedRevision': 1, 'appliedHash': 'a'*64, 'lastError': ''}
        for _ in range(5):
            challenge = lease.challenge()
            result = lease.renew({'challenge': challenge, 'revision': 1, 'hash': 'a'*64, 'controlRevision': 0}, state, 0)
            assert result['valid']
            now[0] += 1
        assert not any('UPDATE NODE_HUB_LEASE' in sql.upper() for sql in statements)
        restarted = HubLease(store, 'node', clock=lambda: now[0])
        assert restarted.required and not restarted.allowed
    finally:
        store.close()

def test_guard_poll_frequency_outpaces_contracted_watchdog():
    clock = [100.0]
    lease = SimpleNamespace(required=True, lock=threading.RLock(),
                            deadline=160.0, clock=lambda: clock[0])
    engine = SimpleNamespace(running=True, lock=threading.RLock())
    guard = LeaseGuard(SimpleNamespace(engine=engine, hub_lease=lease))
    for remaining in (60, 5, 2.5, 1.5, 1.1):
        clock[0] = 160.0 - remaining
        delay = guard._poll_delay()
        assert 0.02 <= delay <= 1.0
        if remaining <= 5:
            assert delay < remaining - 1.0
    engine.running = False
    assert guard._poll_delay() == 1.0


def test_old_notification_cannot_override_new_grant_timer():
    clock = [100.0]
    lease = SimpleNamespace(required=True, lock=threading.RLock(),
                            deadline=120.0, clock=lambda: clock[0])
    watchdog = SystemdWatchdog()
    watchdog.enabled = True
    watchdog.seconds = 30.0
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    messages = []

    def fake_send(payload):
        if not messages and not entered.is_set():
            entered.set()
            assert release.wait(2)
        messages.append(payload)
        return True

    watchdog._send = fake_send
    first = threading.Thread(target=lambda: watchdog.notify(lease=lease))
    def renew():
        with lease.lock:
            lease.deadline = 160.0
        watchdog.notify(lease=lease)
        second_done.set()
    second = threading.Thread(target=renew)
    first.start()
    assert entered.wait(1)
    second.start()
    try:
        assert not second_done.wait(.05)
    finally:
        release.set()
        first.join(2)
        second.join(2)
    assert second_done.is_set()
    assert messages == [b'WATCHDOG_USEC=19000000\nWATCHDOG=1',
                        b'WATCHDOG_USEC=30000000\nWATCHDOG=1']

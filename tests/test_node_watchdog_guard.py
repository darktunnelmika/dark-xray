"""LeaseGuard fail-closed boundary: stopped Xray must not kill the Agent."""
from types import SimpleNamespace

from node_runtime import LeaseGuard


class Lock:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.releases = 0

    def acquire(self, timeout=None):
        return self.acquired

    def release(self):
        self.releases += 1


class Watchdog:
    def __init__(self):
        self.notifies = 0
        self.triggers = 0

    def notify(self, *, lease=None):
        self.notifies += 1
        return True

    def trigger(self):
        self.triggers += 1


def guard(*, running, acquired=True, pause_error=None):
    lease = SimpleNamespace(required=True, allowed=False)
    engine = SimpleNamespace(running=running, lock=Lock(acquired))

    def pause_expired_lease():
        if pause_error is not None:
            raise pause_error

    runtime = SimpleNamespace(
        hub_lease=lease,
        engine=engine,
        pause_expired_lease=pause_expired_lease,
    )
    value = LeaseGuard(runtime)
    value.watchdog = Watchdog()
    return value, engine


def test_expired_lease_busy_engine_does_not_kill_agent_when_core_stopped():
    value, engine = guard(running=False, acquired=False)
    value.tick()
    assert value.watchdog.triggers == 0
    assert value.watchdog.notifies == 1
    assert value.last_error == 'lease_expired_engine_busy_core_stopped'
    assert engine.lock.releases == 0


def test_expired_lease_busy_engine_still_fences_running_core():
    value, engine = guard(running=True, acquired=False)
    value.tick()
    assert value.watchdog.triggers == 1
    assert value.watchdog.notifies == 0
    assert value.last_error == 'lease_expired_engine_busy_core_running'
    assert engine.lock.releases == 0


def test_expiry_handler_failure_does_not_restart_loop_when_core_is_stopped():
    value, engine = guard(
        running=False,
        acquired=True,
        pause_error=RuntimeError('injected expiry cleanup failure'),
    )
    value.tick()
    assert value.watchdog.triggers == 0
    assert value.last_error.startswith('RuntimeError:')
    assert engine.lock.releases == 1


def test_expiry_handler_failure_still_fences_if_core_is_running():
    value, engine = guard(
        running=True,
        acquired=True,
        pause_error=RuntimeError('injected expiry cleanup failure'),
    )
    value.tick()
    assert value.watchdog.triggers == 1
    assert value.last_error.startswith('RuntimeError:')
    assert engine.lock.releases == 1

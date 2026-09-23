"""Linux loopback port regressions: real TCP sockets and fake Xray, not WAN.

Force the server side to close first. This deterministically leaves TIME_WAIT
on the API port, unlike the short readiness probe's scheduler-dependent close.
No host sysctls, firewall rules or unrelated services are changed.
"""
from __future__ import annotations

import errno
import socket
import sys

import pytest

from core import CoreError
from test_supervisor import engine
from test_node_control_lifecycle import rebooted_agent, control
from test_node_hub_recovery import payload, post_state

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux TCP bind semantics')


def leave_time_wait(port: int) -> None:
    address = ('127.0.0.1', port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(2)
        listener.bind(address)
        listener.listen(1)
        with socket.create_connection(address, timeout=2) as peer:
            accepted, _ = listener.accept()
            accepted.close()  # Active closer: the server-side port enters TIME_WAIT.
            assert peer.recv(1) == b''
    # Verify the actual kernel condition rather than replacing bind() with a mock.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        with pytest.raises(OSError) as error:
            probe.bind(address)
        assert error.value.errno == errno.EADDRINUSE
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(address, timeout=2)


def test_start_accepts_closed_connection_time_wait_not_a_live_listener(engine):
    leave_time_wait(engine.config.xray_api_port)
    result = engine.command('start')
    assert result['running'] and not result['dirty'], result
    assert engine.automatic_recoveries == 0


def test_crash_recovery_with_time_wait_recovers_once_and_preserves_counters(engine):
    engine.create({'email': 'retained', 'enable': True}, [])
    with engine.store.transaction() as db:
        db.execute('UPDATE core_clients SET up=123,down=456 WHERE email=?', ('retained',))
    engine.command('start')
    previous_pid = engine.process.pid
    engine.process.kill()
    engine.process.wait(timeout=3)
    leave_time_wait(engine.config.xray_api_port)
    engine.last_start = 0
    engine.flush()
    assert engine.running, engine.runtime_state()
    assert engine.process.pid != previous_pid
    assert engine.automatic_recoveries == 1
    assert engine.clients()[0]['traffic'] == {'up': 123, 'down': 456}
    recovered_pid = engine.process.pid
    engine.flush()
    assert engine.process.pid == recovered_pid and engine.automatic_recoveries == 1


def test_agent_tick_recovers_time_wait_but_respects_durable_manual_stop(tmp_path):
    with rebooted_agent(tmp_path/'node') as (_, engine, runtime, client, loop):
        post_state(client, payload(engine))
        engine.process.kill()
        engine.process.wait(timeout=3)
        leave_time_wait(engine.config.xray_api_port)
        engine.last_start = 0
        loop.tick()
        assert engine.running, {'core': engine.runtime_state(), 'loop_error': loop.last_error}
        assert runtime.control_status()['effective_running']
        control(client, 'stop')
        leave_time_wait(engine.config.xray_api_port)
        engine.last_start = 0
        loop.tick()
        assert not engine.running and not engine.wants_running
        assert runtime.control_status()['manual_stop']


@pytest.mark.parametrize('host', ['127.0.0.1', '0.0.0.0'])
@pytest.mark.parametrize('reuse', [False, True])
def test_real_foreign_listener_still_refused_and_left_untouched(engine, host, reuse):
    active = engine.runtime/'active.json'
    active.write_text('UNCHANGED')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, engine.config.xray_api_port))
        listener.listen(1)
        listener.settimeout(2)
        with pytest.raises(CoreError, match='occupied') as error:
            engine.command('start')
        assert error.value.status == 409
        assert not engine.running and engine.process is None
        assert active.read_text() == 'UNCHANGED'
        # The supervisor must neither close nor attach to this unrelated listener.
        with socket.create_connection(('127.0.0.1', engine.config.xray_api_port), timeout=2) as peer:
            accepted, _ = listener.accept()
            with accepted:
                peer.sendall(b'still-owned-by-test')
                assert accepted.recv(64) == b'still-owned-by-test'

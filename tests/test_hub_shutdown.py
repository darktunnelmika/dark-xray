"""Bounded HTTP stream drain regression, no production state or services."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from server import HUB_HTTP_DRAIN_SECONDS, _hub_uvicorn_options

ROOT = Path(__file__).resolve().parents[1]


def test_production_http_shutdown_budget_preserves_other_server_controls():
    config = SimpleNamespace(tls_certificate='', tls_private_key='')
    opts = _hub_uvicorn_options(config, '127.0.0.1', 12345)
    assert opts['host'] == '127.0.0.1' and opts['port'] == 12345
    assert opts['proxy_headers'] is False
    assert opts['workers'] == 1 and opts['ws'] == 'none'
    assert opts['access_log'] is False
    assert opts['ssl_certfile'] is None and opts['ssl_keyfile'] is None
    assert HUB_HTTP_DRAIN_SECONDS == 10.0
    assert opts['timeout_graceful_shutdown'] == HUB_HTTP_DRAIN_SECONDS < 60.0

    config = SimpleNamespace(tls_certificate='/tmp/fixture-cert',
                             tls_private_key='/tmp/fixture-key')
    opts = _hub_uvicorn_options(config, '127.0.0.1', 12346)
    assert opts['ssl_certfile'] == config.tls_certificate
    assert opts['ssl_keyfile'] == config.tls_private_key


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux SIGTERM smoke only')
def test_held_http_stream_does_not_block_uvicorn_lifespan_teardown(tmp_path):
    """A live, disposable Uvicorn child must finish its lifespan after SIGTERM."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    marker = tmp_path/'lifespan-closed'
    fixture = ROOT/'tests/fixtures/hub_held_stream.py'
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT/'backend')
    proc = subprocess.Popen([sys.executable, str(fixture), str(port), str(marker)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    conn = None
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError('Disposable Uvicorn exited before request')
            try:
                conn = socket.create_connection(('127.0.0.1', port), timeout=.3)
                break
            except OSError:
                time.sleep(.05)
        assert conn is not None, 'Disposable Uvicorn never listened'
        conn.settimeout(3)
        conn.sendall(b'GET /hold HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n')
        reply = conn.recv(2048)
        assert b'200 OK' in reply, reply[:200]
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=9)
        elapsed = time.monotonic() - started
        # Uvicorn 0.48 deliberately re-raises the captured SIGTERM AFTER
        # the ASGI lifespan has closed; this is a clean systemd stop, not SIGKILL.
        assert proc.returncode in (0, -signal.SIGTERM), proc.returncode
        assert elapsed < 8.0, elapsed
        assert marker.read_text() == 'lifespan-closed\n'
    finally:
        if conn is not None:
            conn.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)

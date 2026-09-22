"""Harness checks; none of these is a real-Xray acceptance claim."""
import contextlib

import httpx
import pytest
from fastapi import FastAPI

import node_automatic_lab as lab
from test_node_automatic_limits import eventually, serving, listener_socket


def test_http_serving_starts_and_stops_real_lifespan():
    events = []
    @contextlib.asynccontextmanager
    async def lifecycle(app):
        events.append('start')
        yield
        events.append('stop')
    app = FastAPI(lifespan=lifecycle)
    @app.get('/test')
    def endpoint():
        return {'observed':True}
    sock = listener_socket(); port = sock.getsockname()[1]
    with serving(app, sock):
        with httpx.Client(trust_env=False, timeout=2) as http:
            result = http.get(f'http://127.0.0.1:{port}/test')
        assert result.status_code == 200 and result.json() == {'observed':True}
        assert events == ['start']
    assert events == ['start', 'stop']


def test_wait_timeout_is_not_success():
    with pytest.raises(AssertionError, match='Automatic convergence timed out'):
        eventually(lambda: False, 'never-ready', .01)


def test_wait_does_not_swallow_programming_errors():
    def bad():
        raise ValueError('broken predicate')
    with pytest.raises(ValueError, match='broken predicate'):
        eventually(bad, 'bad predicate')


def test_bootstrap_refuses_host_before_any_command(monkeypatch):
    def reject():
        raise RuntimeError('Refusing host')
    monkeypatch.setattr(lab, 'check_namespace', reject)
    monkeypatch.setattr(lab.subprocess, 'run', lambda *a, **k: pytest.fail('command ran before namespace check'))
    with pytest.raises(RuntimeError, match='Refusing host'):
        lab.main()


def test_bootstrap_requires_root_before_any_command(monkeypatch):
    monkeypatch.setattr(lab, 'check_namespace', lambda: None)
    monkeypatch.setattr(lab.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(lab.subprocess, 'run', lambda *a, **k: pytest.fail('non-root command'))
    with pytest.raises(RuntimeError, match='requires root'):
        lab.main()


def test_bootstrap_preserves_failure_and_runs_only_fixed_suite(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, 'check_namespace', lambda: calls.append('checked'))
    monkeypatch.setattr(lab.os, 'geteuid', lambda: 0)
    def run(args, **kwargs):
        calls.append(args)
        return type('Result', (), {'returncode':7})()
    monkeypatch.setattr(lab.subprocess, 'run', run)
    assert lab.main() == 7
    assert calls[0] == 'checked'
    assert calls[1] == ['ip', 'link', 'set', 'lo', 'up']
    assert calls[2:4] == [['ip','address','add',ip+'/32','dev','lo'] for ip in lab.SOURCES]
    assert calls[4][1:4] == ['-m', 'pytest', 'tests/test_node_automatic_limits.py']
    assert len(calls) == 5

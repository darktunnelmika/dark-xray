"""Test the CI-only HTTPS client without installing services or disabling TLS.

Use a disposable, explicitly trusted certificate and a real loopback TLS socket.
Only the fixed fixture destination is redirected to an ephemeral local port.
"""
import contextlib
import importlib.util
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def tls_contexts(tmp_path_factory):
    root = tmp_path_factory.mktemp('ci-client-tls')
    cert, key = root/'cert.pem', root/'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(key), '-out', str(cert), '-days', '1',
        '-subj', '/CN=node.example.test', '-addext', 'subjectAltName=DNS:node.example.test'],
        check=True, capture_output=True, timeout=15)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, key)
    trusted = ssl.create_default_context(cafile=str(cert))
    return server, trusted


@contextlib.contextmanager
def connect_fixture(monkeypatch, tls_contexts, timeout, *, trusted=True):
    spec = importlib.util.spec_from_file_location('ci_node_update_client', ROOT/'tests/node-update-systemd-smoke.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    server_context, client_context = tls_contexts
    listener = socket.socket();listener.bind(('127.0.0.1', 0));listener.listen(1);listener.settimeout(5)
    release = threading.Event();observations = {'connect': [], 'sni': [], 'raw': []};errors = []
    server_context.set_servername_callback(lambda _sock, name, _ctx: observations['sni'].append(name))
    def serve():
        try:
            with listener.accept()[0] as raw:
                raw.settimeout(3)
                try:
                    with server_context.wrap_socket(raw, server_side=True):
                        release.wait(5)
                except ssl.SSLError:
                    if trusted:raise
        except Exception as exc:errors.append(exc)
    thread = threading.Thread(target=serve);thread.start()
    real_connect = socket.create_connection
    def redirected(address, timeout):
        assert address == ('127.0.0.1', 9443)
        observations['connect'].append(timeout)
        raw = real_connect(listener.getsockname(), timeout=timeout)
        observations['raw'].append(raw)
        return raw
    monkeypatch.setattr(module.socket, 'create_connection', redirected)
    client = module.LocalTLS('node.example.test', 9443, timeout=timeout,
        context=client_context if trusted else ssl.create_default_context())
    try:yield client, observations
    finally:
        client.close();release.set();thread.join(6);listener.close()
        assert not thread.is_alive() and not errors, errors


@pytest.mark.parametrize('timeout', [0.125, 15.0, None])
def test_ci_tls_preserves_configured_io_timeout(monkeypatch, tls_contexts, timeout):
    with connect_fixture(monkeypatch, tls_contexts, timeout) as (client, observed):
        client.connect()
        assert isinstance(client.sock, ssl.SSLSocket)
        assert observed['connect'] == [3] and observed['sni'] == ['node.example.test']
        assert client.sock.gettimeout() == timeout


def test_ci_tls_read_is_bounded_by_configured_timeout(monkeypatch, tls_contexts):
    with connect_fixture(monkeypatch, tls_contexts, 0.05) as (client, _):
        client.connect()
        with pytest.raises(TimeoutError):client.sock.recv(1)


def test_ci_tls_untrusted_certificate_is_rejected_and_raw_socket_closed(monkeypatch, tls_contexts):
    with connect_fixture(monkeypatch, tls_contexts, 15.0, trusted=False) as (client, observed):
        with pytest.raises(ssl.SSLCertVerificationError):client.connect()
        assert all(raw.fileno() == -1 for raw in observed['raw'])
        assert client.sock is None

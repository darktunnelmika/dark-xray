"""Read-only Dashboard telemetry regression tests."""
import socket

import psutil

from core import host_socket_counts
from core import Config, CoreEngine
from dark_policy import Store


def test_sockstat_uses_ipv4_ipv6_kernel_aggregates_without_socket_enumeration(tmp_path, monkeypatch):
    v4 = tmp_path / 'sockstat'
    v6 = tmp_path / 'sockstat6'
    v4.write_text('sockets: used 40\nTCP: inuse 11 orphan 0 tw 20 alloc 19 mem 2\nUDP: inuse 3 mem 1\n', encoding='ascii')
    v6.write_text('TCP6: inuse 5\nUDP6: inuse 2\n', encoding='ascii')

    def forbid_enumeration(**_kwargs):
        raise AssertionError('High-cardinality socket enumeration was triggered')

    monkeypatch.setattr(psutil, 'net_connections', forbid_enumeration)
    result = host_socket_counts((v4, v6))
    assert result == {
        'open': 21, 'tcp': 16, 'udp': 5,
        'available': True, 'source': 'linux-sockstat'
    }


def test_zero_kernel_counts_are_valid_measurements(tmp_path, monkeypatch):
    v4 = tmp_path / 'sockstat'
    v4.write_text('TCP: inuse 0\nUDP: inuse 0\n', encoding='ascii')
    monkeypatch.setattr(psutil, 'net_connections',
                        lambda **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected fallback')))
    result = host_socket_counts((v4,))
    assert result['available'] is True
    assert result['open'] == 0
    assert result['source'] == 'linux-sockstat'


def test_socket_fallback_and_unavailable_are_distinct(tmp_path, monkeypatch):
    missing = tmp_path / 'missing'
    class Row:
        def __init__(self, kind):
            self.type = kind

    monkeypatch.setattr(psutil, 'net_connections',
                        lambda **_kwargs: [Row(socket.SOCK_STREAM), Row(socket.SOCK_DGRAM)])
    result = host_socket_counts((missing,))
    assert result['source'] == 'psutil-enumeration'
    assert (result['open'], result['tcp'], result['udp']) == (2, 1, 1)

    def denied(**_kwargs):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, 'net_connections', denied)
    result = host_socket_counts((missing,))
    assert result == {
        'open': 0, 'tcp': 0, 'udp': 0,
        'available': False, 'source': 'unavailable'
    }


def test_fast_system_snapshot_skips_full_config_hashing(tmp_path, monkeypatch):
    store = Store(tmp_path / 'dark.sqlite3')
    engine = CoreEngine(
        Config(public_origin='http://127.0.0.1:2087',
               xray_binary=str(tmp_path / 'missing-xray'),
               xray_assets=str(tmp_path), test_engine=True),
        store, tmp_path / 'runtime'
    )
    try:
        def forbid_rebuild():
            raise AssertionError('Full Xray config rebuilt during fast Dashboard telemetry')
        monkeypatch.setattr(engine, 'build_config', forbid_rebuild)
        doc = engine.system(live_only=True)
        assert {'cpu', 'cpuInfo', 'mem', 'disk', 'swap', 'netIO',
                'netTraffic', 'connections', 'panel', 'xray', 'runtime'} <= set(doc)
        assert doc['runtime']['telemetry_only'] is True
        assert doc['runtime']['dirty'] is None
        assert doc['runtime']['state'] in ('running', 'stopped')
        assert doc['connections']['open'] == doc['connections']['tcp'] + doc['connections']['udp']
    finally:
        engine.close()
        store.close()

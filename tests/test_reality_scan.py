import socket
import pytest

from reality_scan import RealityScanError, parse_target, scan_target, search_targets


def test_parse_target_defaults_to_443():
    t=parse_target('Example.COM')
    assert t.host=='example.com' and t.port==443 and t.display=='example.com:443'


def test_parse_target_custom_port_and_ipv6():
    assert parse_target('example.com:8443').port==8443
    t=parse_target('[2606:4700:4700::1111]:443')
    assert t.host=='2606:4700:4700::1111' and t.display=='[2606:4700:4700::1111]:443'


def test_parse_target_rejects_url_and_cidr():
    for value in ('https://example.com','10.0.0.0/24','example.com/path','user@example.com:443'):
        with pytest.raises(RealityScanError):
            parse_target(value)


def test_scan_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(RealityScanError,match='public IP'):
        scan_target('localhost.example:443')


def test_search_sorts_recommended_then_latency(monkeypatch):
    import reality_scan
    rows={
      'a.example:443':{'target':'a.example:443','ok':True,'recommended':False,'latencyMs':10},
      'b.example:443':{'target':'b.example:443','ok':True,'recommended':True,'latencyMs':50},
      'c.example:443':{'target':'c.example:443','ok':True,'recommended':True,'latencyMs':20},
    }
    monkeypatch.setattr(reality_scan,'scan_target',lambda value,timeout=4.0:rows[value])
    result=search_targets(list(rows))
    assert [x['target'] for x in result]==['c.example:443','b.example:443','a.example:443']

import socket
import pytest

from reality_scan import DEFAULT_REALITY_TARGETS, RealityScanError, parse_target, reality_target_policy, scan_target, search_targets
from core import CoreEngine,CoreError


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


def test_pinned_xray_blocks_known_microsoft_reality_target():
    policy=reality_target_policy('www.microsoft.com:443')
    assert policy['compatible'] is False
    assert policy['severity']=='blocked'
    assert policy['suggestedTarget']=='www.bing.com:443'
    assert DEFAULT_REALITY_TARGETS[0]=='www.bing.com:443'
    assert all('microsoft.com' not in x for x in DEFAULT_REALITY_TARGETS)


def test_core_transport_validation_rejects_known_bad_reality_target():
    import base64
    key=base64.urlsafe_b64encode(b'K'*32).decode().rstrip('=')
    base={
      'protocol':'vless',
      'settings':{'decryption':'none'},
      'streamSettings':{
        'network':'grpc','security':'reality',
        'grpcSettings':{'serviceName':'dark-test'},
        'realitySettings':{
          'privateKey':key,'target':'www.microsoft.com:443',
          'serverNames':['www.microsoft.com'],'shortIds':['0123456789abcdef']
        }
      }
    }
    with pytest.raises(CoreError,match='Known incompatible REALITY target'):
        CoreEngine._validate_transport(base)
    base['streamSettings']['realitySettings']['target']='www.bing.com:443'
    base['streamSettings']['realitySettings']['serverNames']=['www.bing.com']
    CoreEngine._validate_transport(base)

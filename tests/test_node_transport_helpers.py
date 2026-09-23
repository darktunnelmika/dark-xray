"""Offline tests of the acceptance harness; not real-network acceptance."""
import base64
import json
from urllib.parse import urlencode

import pytest
import test_node_real_transports as transports
from test_node_real_transports import decode_link, outbound_from_link, CASES


def uri(**overrides):
    q={'type':'ws','security':'tls','sni':'node1.example.test','path':'/custom?q=a+b',
       'host':'node1.example.test','alpn':'http/1.1'}
    q.update(overrides)
    return 'vless://11111111-1111-4111-8111-111111111111@127.0.0.1:24567?'+urlencode(q)+'#test'


def test_ca_verification_and_nondefault_ws_path_come_from_link(tmp_path):
    d=outbound_from_link(uri(),tmp_path/'ca.pem')
    assert d['streamSettings']['wsSettings']=={'path':'/custom?q=a+b','headers':{'Host':'node1.example.test'}}
    tls=d['streamSettings']['tlsSettings']
    assert tls['allowInsecure'] is False and tls['disableSystemRoot'] is True
    assert tls['serverName']=='node1.example.test' and tls['alpn']==['http/1.1']
    assert tls['certificates']==[{'certificateFile':str(tmp_path/'ca.pem'),'usage':'verify'}]
    assert 'privateKey' not in str(d)


@pytest.mark.parametrize('address',['example.com','192.168.0.1','169.254.169.254','[::1]'])
def test_reader_cannot_follow_nonfixture_targets(address):
    with pytest.raises(AssertionError):decode_link(uri().replace('127.0.0.1',address))


@pytest.mark.parametrize('port',['0','443','65536'])
def test_reader_refuses_invalid_fixture_port(port):
    with pytest.raises((AssertionError,ValueError)):decode_link(uri().replace(':24567',':'+port))


@pytest.mark.parametrize('flag',['1','true'])
def test_insecure_uri_is_not_repaired_silently(flag):
    with pytest.raises(AssertionError):outbound_from_link(uri(allowInsecure=flag),'ca.pem')


def test_duplicate_parameters_fail_instead_of_selecting_a_value():
    with pytest.raises(AssertionError):decode_link(uri().replace('#test','&sni=other#test'))


def test_reality_uses_exported_password_shortid_and_flow_only():
    d=outbound_from_link(uri(type='tcp',security='reality',pbk='A'*43,sid='a1b2',fp='chrome',
        flow='xtls-rprx-vision',spx='/own-spider'), 'ca.pem')
    assert d['streamSettings']['realitySettings']=={'serverName':'node1.example.test',
        'password':'A'*43,'shortId':'a1b2','fingerprint':'chrome','spiderX':'/own-spider'}
    assert d['settings']['vnext'][0]['users'][0]['flow']=='xtls-rprx-vision'
    assert 'privateKey' not in str(d)


@pytest.mark.parametrize('network', ['grpc','httpupgrade','xhttp','kcp','raw'])
def test_exported_transport_options(network):
    d=outbound_from_link(uri(type=network,serviceName='grpc-custom',mode='packet-up'),'ca.pem')
    stream=d['streamSettings']
    assert stream['network']==network
    if network=='grpc':assert stream['grpcSettings']=={'serviceName':'grpc-custom'}
    if network=='xhttp':assert stream['xhttpSettings']['mode']=='packet-up'
    if network=='kcp':assert stream['kcpSettings']=={}


def test_vmess_base64_is_decoded_without_server_side_settings():
    raw={'add':'127.0.0.1','port':'24567','id':'fixture-id','aid':'0','scy':'auto',
         'net':'grpc','path':'own-service','tls':'tls','sni':'node1.example.test','alpn':'h2'}
    link='vmess://'+base64.b64encode(json.dumps(raw).encode()).decode()
    d=outbound_from_link(link,'ca.pem')
    assert d['protocol']=='vmess'
    assert d['streamSettings']['grpcSettings']['serviceName']=='own-service'
    assert d['settings']['vnext'][0]['users']==[{'id':'fixture-id','alterId':0,'security':'auto'}]


def test_shadowsocks_password_and_cipher_come_from_export():
    user=base64.urlsafe_b64encode(b'aes-128-gcm:password:with:colons').decode().rstrip('=')
    d=outbound_from_link('ss://'+user+'@127.0.0.1:24567#test','ca.pem')
    assert d['settings']['servers']==[{'address':'127.0.0.1','port':24567,
                                     'method':'aes-128-gcm','password':'password:with:colons'}]


def test_trojan_escaped_password_is_preserved():
    d=outbound_from_link(uri().replace('vless://11111111-1111-4111-8111-111111111111',
                                    'trojan://some%3Ap%40ss'),'ca.pem')
    assert d['settings']['servers'][0]['password']=='some:p@ss'


def test_matrix_covers_explicit_distinct_cases_without_claiming_every_combination():
    assert len(CASES)==16 and len({x.label for x in CASES})==16
    assert {x.protocol for x in CASES}=={'vless','vmess','trojan','shadowsocks'}
    assert {x.network for x in CASES}=={'tcp','raw','ws','grpc','httpupgrade','xhttp','kcp'}


# These use actual local TLS sockets, not a REALITY/Xray substitute.
from test_node_real_transports import reality_target, tls_material


@pytest.mark.parametrize('pending_count',[1,3])
def test_stalled_tls_clients_do_not_serialize_target_accept(tls_material,pending_count):
    import contextlib
    import socket
    import ssl
    ca,materials=tls_material
    with reality_target(materials[0]) as address, contextlib.ExitStack() as stack:
        host,port=address.split(':')
        for _ in range(pending_count):
            stack.enter_context(socket.create_connection((host,int(port)),timeout=1))
        context=ssl.create_default_context(cafile=str(ca));context.set_alpn_protocols(['h2'])
        with socket.create_connection((host,int(port)),timeout=1) as good:
            with context.wrap_socket(good,server_hostname=materials[0][0]) as stream:
                assert stream.version()=='TLSv1.3' and stream.selected_alpn_protocol()=='h2'
        assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED


@pytest.mark.parametrize('fault',['untrusted-ca','wrong-name'])
def test_target_rejects_bad_verification_then_remains_usable(tls_material,fault):
    import socket
    import ssl
    ca,materials=tls_material
    with reality_target(materials[0]) as address:
        host,port=address.split(':')
        bad=ssl.create_default_context() if fault=='untrusted-ca' else ssl.create_default_context(cafile=str(ca))
        with socket.create_connection((host,int(port)),timeout=1) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                bad.wrap_socket(raw,server_hostname='wrong.example.test' if fault=='wrong-name' else materials[0][0])
        good=ssl.create_default_context(cafile=str(ca))
        with socket.create_connection((host,int(port)),timeout=1) as raw:
            with good.wrap_socket(raw,server_hostname=materials[0][0]) as conn:
                assert conn.version()=='TLSv1.3'


def test_target_shutdown_closes_a_stalled_handshake(tls_material):
    import socket
    import time
    _,materials=tls_material
    raw=None;started=time.monotonic()
    try:
        with reality_target(materials[0]) as address:
            host,port=address.split(':')
            raw=socket.create_connection((host,int(port)),timeout=1)
        assert time.monotonic()-started<2
        # A socket accepted just as shutdown begins must also be closed.
        raw.settimeout(1)
        try: assert raw.recv(1)==b''
        except ConnectionResetError: pass
    finally:
        if raw is not None: raw.close()


def test_unique_fixture_ports_do_not_reuse_an_unbound_candidate(monkeypatch):
    values=iter([25001,25001,25002])
    monkeypatch.setattr(transports,'free_port',lambda:next(values))
    used=set()
    assert transports.unique_free_port(used)==25001
    assert transports.unique_free_port(used)==25002
    assert used=={25001,25002}

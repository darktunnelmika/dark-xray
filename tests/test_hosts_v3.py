import json
from urllib.parse import urlsplit,parse_qs
from test_settings_v2 import env,IB


def setup_client(c,email='host-v3-user',ib=None):
    r=c.post('/api/inbounds',json=ib or IB);assert r.status_code==200,r.text
    iid=r.json()['id']
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':email},'inboundIds':[iid]});assert r.status_code==202,r.text
    return iid,r.json()['subscription_url']


def host(iid,**kw):
    out={'inboundId':iid,'address':'edge.example.test','port':443,'remark':'EDGE','security':'tls','sni':'tls.example.test','overrideSniFromAddress':False,'keepSniBlank':False,'host':'cdn.example.test','path':'/edge','alpn':'h2,http/1.1','fingerprint':'chrome','allowInsecure':True,'finalMask':'{"tcpPadding":true}','mihomoIpVersion':'ipv4-prefer','excludeFromSubTypes':[],'enable':True}
    out.update(kw);return out


def test_host_v3_raw_link_consumes_security_tls_controls_and_finalmask(env):
    store,engine,c=env;iid,url=setup_client(c)
    h=host(iid);r=c.put('/api/settings/hosts',json={'value':[h]});assert r.status_code==200,r.text
    r=c.get(url+'?format=raw');assert r.status_code==200,r.text
    p=urlsplit(r.text.strip());q={k:v[-1] for k,v in parse_qs(p.query).items()}
    assert p.hostname=='edge.example.test' and p.port==443
    assert q['security']=='tls' and q['sni']=='tls.example.test'
    assert q['allowInsecure']=='1' and q['alpn']=='h2,http/1.1'
    assert json.loads(q['fm'])=={'tcpPadding':True}


def test_host_v3_clash_consumes_mihomo_ip_alpn_and_skip_verify(env):
    store,engine,c=env;iid,url=setup_client(c)
    assert c.put('/api/settings/hosts',json={'value':[host(iid)]}).status_code==200
    r=c.get(url+'?format=clash');assert r.status_code==200,r.text
    assert '"skip-cert-verify": true' in r.text
    assert '"ip-version": "ipv4-prefer"' in r.text
    assert '"alpn":' in r.text and '"h2"' in r.text and '"http/1.1"' in r.text


def test_host_v3_format_exclusion_has_no_direct_fallback(env):
    store,engine,c=env;iid,url=setup_client(c)
    assert c.put('/api/settings/hosts',json={'value':[host(iid,excludeFromSubTypes=['clash'])]}).status_code==200
    assert c.get(url+'?format=raw').status_code==200
    r=c.get(url+'?format=clash');assert r.status_code==503


def test_host_v3_sni_address_and_blank_modes(env):
    store,engine,c=env;iid,url=setup_client(c)
    h=host(iid,overrideSniFromAddress=True,sni='ignored.example')
    assert c.put('/api/settings/hosts',json={'value':[h]}).status_code==200
    q=parse_qs(urlsplit(c.get(url+'?format=raw').text.strip()).query);assert q['sni'][-1]=='edge.example.test'
    h=host(iid,keepSniBlank=True,sni='ignored.example',overrideSniFromAddress=False)
    assert c.put('/api/settings/hosts',json={'value':[h]}).status_code==200
    q=parse_qs(urlsplit(c.get(url+'?format=raw').text.strip()).query);assert 'sni' not in q


def test_host_v3_force_none_drops_reality_only_params(env):
    store,engine,c=env
    keys=c.post('/api/keys/x25519',json={}).json()
    ib=dict(IB);ib['port']=19051;ib['tag']='reality-host-test';ib['streamSettings']={'network':'tcp','security':'reality','realitySettings':{'privateKey':keys['privateKey'],'target':'example.com:443','serverNames':['example.com'],'shortIds':[keys['shortId']]}}
    iid,url=setup_client(c,'reality-host-user',ib)
    assert c.put('/api/settings/hosts',json={'value':[host(iid,security='none',allowInsecure=False,alpn='',finalMask='')]}).status_code==200
    q=parse_qs(urlsplit(c.get(url+'?format=raw').text.strip()).query)
    assert q['security'][-1]=='none'
    for key in ('pbk','sid','spx','sni','fp','allowInsecure'):assert key not in q


def test_host_v3_validation_rejects_fake_or_conflicting_controls(env):
    store,engine,c=env;iid,_=setup_client(c)
    cases=[host(iid,security='reality'),host(iid,overrideSniFromAddress=True,keepSniBlank=True),host(iid,finalMask='[]'),host(iid,mihomoIpVersion='magic'),host(iid,excludeFromSubTypes=['clash','clash'])]
    for value in cases:
        r=c.put('/api/settings/hosts',json={'value':[value]});assert r.status_code==422,(value,r.text)

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


def test_local_tunnel_adds_direct_sibling_without_replacing_inbound_route(env):
    store,engine,c=env;iid,url=setup_client(c,'local-tunnel-user')
    tunnel=host(iid,runtime='local',endpointType='tunnel',
                address='iran.example.test',port=20001,remark='TUNNEL',
                security='same',sni='',host='',path='',alpn='',fingerprint='',
                allowInsecure=False,finalMask='',mihomoIpVersion='')
    dep=c.put(f'/api/inbounds/{iid}/deployments',json={'local':True,'nodeIds':[],'tunnelPorts':{'local':20001}})
    assert dep.status_code==200,dep.text
    assert c.put('/api/settings/hosts',json={'value':[tunnel]}).status_code==200
    out=engine.links('local-tunnel-user',runtime_ready={'local':{iid}})
    assert [(x['endpointType'],x['runtime']) for x in out['links']]==[
        ('direct','local'),('tunnel','local')]
    direct=urlsplit(out['links'][0]['uri'])
    tunnel_link=urlsplit(out['links'][1]['uri'])
    assert direct.hostname=='vpn.example.test' and direct.port==IB['port']
    assert tunnel_link.hostname=='iran.example.test' and tunnel_link.port==20001


def test_disabled_explicit_local_direct_suppresses_implicit_fallback(env):
    store,engine,c=env;iid,url=setup_client(c,'local-tunnel-disabled-direct')
    direct=host(iid,runtime='local',endpointType='direct',
                address='direct.example.test',port=IB['port'],remark='DIRECT',enable=False)
    tunnel=host(iid,runtime='local',endpointType='tunnel',
                address='iran.example.test',port=20001,remark='TUNNEL',
                security='same',sni='',host='',path='',alpn='',fingerprint='',
                allowInsecure=False,finalMask='',mihomoIpVersion='')
    dep=c.put(f'/api/inbounds/{iid}/deployments',json={'local':True,'nodeIds':[],'tunnelPorts':{'local':20001}})
    assert dep.status_code==200,dep.text
    assert c.put('/api/settings/hosts',json={'value':[direct,tunnel]}).status_code==200
    out=engine.links('local-tunnel-disabled-direct',runtime_ready={'local':{iid}})
    assert [(x['endpointType'],x['runtime']) for x in out['links']]==[('tunnel','local')]


def test_one_tunnel_host_exports_multiple_addresses_on_shared_port(env):
    store,engine,c=env;iid,url=setup_client(c,'multi-address-tunnel-user')
    dep=c.put(f'/api/inbounds/{iid}/deployments',json={'local':True,'nodeIds':[],'tunnelPorts':{'local':20001}})
    assert dep.status_code==200,dep.text
    tunnel=host(iid,runtime='local',endpointType='tunnel',
                address='iran-a.example.test',
                addresses=['iran-a.example.test','iran-b.example.test','iran-a.example.test','iran-c.example.test'],
                port=20001,remark='TURKEY TUNNEL',
                security='same',sni='',host='',path='',alpn='',fingerprint='',
                allowInsecure=False,finalMask='',mihomoIpVersion='')
    r=c.put('/api/settings/hosts',json={'value':[tunnel]});assert r.status_code==200,r.text
    saved=engine.section('hosts')
    assert len(saved)==1
    assert saved[0]['address']=='iran-a.example.test'
    assert saved[0]['addresses']==['iran-a.example.test','iran-b.example.test','iran-c.example.test']

    out=engine.links('multi-address-tunnel-user',runtime_ready={'local':{iid}})
    tunnels=[x for x in out['links'] if x['endpointType']=='tunnel']
    assert len(tunnels)==3
    assert [urlsplit(x['uri']).hostname for x in tunnels]==[
        'iran-a.example.test','iran-b.example.test','iran-c.example.test']
    assert {urlsplit(x['uri']).port for x in tunnels}=={20001}
    assert len({x['remark'] for x in tunnels})==3
    assert [x['remark'].split(' | ')[0] for x in tunnels]==[
        'TURKEY TUNNEL · 1','TURKEY TUNNEL · 2','TURKEY TUNNEL · 3']


def test_host_multi_address_validation_rejects_empty_or_unsafe_values(env):
    store,engine,c=env;iid,_=setup_client(c,'multi-address-validation')
    bad_empty=host(iid,addresses=[])
    bad_path=host(iid,addresses=['ok.example.test','bad/path'])
    for value in (bad_empty,bad_path):
        r=c.put('/api/settings/hosts',json={'value':[value]})
        assert r.status_code==422,r.text


def test_tunnel_port_contract_waits_for_matching_host_and_allows_multiple_hosts(env):
    store,engine,c=env;iid,url=setup_client(c,'tunnel-contract-user')
    values=[
        host(iid,runtime='local',endpointType='tunnel',address='iran-a.example.test',port=20001,remark='IRAN A',
             security='same',sni='',host='',path='',alpn='',fingerprint='',allowInsecure=False,finalMask='',mihomoIpVersion=''),
        host(iid,runtime='local',endpointType='tunnel',address='iran-b.example.test',port=20001,remark='IRAN B',
             security='same',sni='',host='',path='',alpn='',fingerprint='',allowInsecure=False,finalMask='',mihomoIpVersion=''),
        host(iid,runtime='local',endpointType='tunnel',address='wrong.example.test',port=20002,remark='WRONG PORT',
             security='same',sni='',host='',path='',alpn='',fingerprint='',allowInsecure=False,finalMask='',mihomoIpVersion=''),
    ]
    assert c.put('/api/settings/hosts',json={'value':values}).status_code==200

    waiting=engine.links('tunnel-contract-user',runtime_ready={'local':{iid}})
    assert [(x['endpointType'],x['runtime']) for x in waiting['links']]==[('direct','local')]
    assert any('waiting for Tunnel Port' in w for w in waiting['warnings'])

    dep=c.put(f'/api/inbounds/{iid}/deployments',json={'local':True,'nodeIds':[],'tunnelPorts':{'local':20001}})
    assert dep.status_code==200,dep.text
    route=dep.json()['tunnelRoutes']['local']
    assert route['state']=='active' and route['matchingHosts']==2 and route['configuredHosts']==3

    active=engine.links('tunnel-contract-user',runtime_ready={'local':{iid}})
    assert [(x['endpointType'],x['runtime']) for x in active['links']]==[
        ('direct','local'),('tunnel','local'),('tunnel','local')]
    hosts=[urlsplit(x['uri']).hostname for x in active['links']]
    assert hosts==['vpn.example.test','iran-a.example.test','iran-b.example.test']
    assert any('port mismatch' in w for w in active['warnings'])
    assert all('wrong.example.test' not in x['uri'] for x in active['links'])


def test_tunnel_port_requires_same_deployment_target(env):
    store,engine,c=env;iid,url=setup_client(c,'tunnel-target-user')
    r=c.put(f'/api/inbounds/{iid}/deployments',json={
        'local':False,'nodeIds':[],'tunnelPorts':{'local':20001}})
    assert r.status_code==400 and 'deployment target' in r.text.lower()
    r=c.put(f'/api/inbounds/{iid}/deployments',json={
        'local':True,'nodeIds':[],'tunnelPorts':{'node:missing':20001}})
    assert r.status_code==400 and 'deployment target' in r.text.lower()


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


def test_host_v3_endpoint_type_is_persisted_and_exported(env):
    store,engine,c=env;iid,url=setup_client(c,'host-pair-user')
    inbound=engine.inbound(iid);inbound.pop('id',None);inbound.pop('applied',None)
    meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
    meta['tunnelPorts']={'node:pair-node':20001};inbound['panelMeta']=meta
    engine.save_inbound(inbound,iid)
    values=[
        host(iid,runtime='local',endpointType='direct',address='direct.example.test',port=443,remark='DIRECT'),
        host(iid,runtime='node:pair-node',endpointType='tunnel',address='iran.example.test',port=20001,remark='TUNNEL'),
    ]
    assert c.put('/api/settings/hosts',json={'value':values}).status_code==200
    out=engine.links('host-pair-user',runtime_ready={'local':{iid},'node:pair-node':{iid}})
    assert [(x['endpointType'],x['runtime']) for x in out['links']]==[('direct','local'),('tunnel','node:pair-node')]
    assert ':20001' in out['links'][1]['uri']
    bad=host(iid,endpointType='invalid')
    assert c.put('/api/settings/hosts',json={'value':[bad]}).status_code==422

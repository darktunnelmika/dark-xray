import json

from test_standalone import env


def put(c,section,value):
    r=c.put('/api/settings/'+section,json={'value':value})
    assert r.status_code==200,r.text
    return r.json()


def test_traffic_engine_dependency_graph_and_prefix_balancers(env):
    _,_,_,_,c=env
    outbounds=[
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'block','protocol':'blackhole','settings':{}},
        {'tag':'proxy-a','protocol':'freedom','settings':{},'streamSettings':{'sockopt':{'dialerProxy':'direct'}}},
        {'tag':'proxy-b','protocol':'freedom','settings':{}},
    ]
    put(c,'outbounds',outbounds)
    routing={
        'domainStrategy':'AsIs',
        'rules':[
            {'type':'field','domain':['domain:example.com'],'outboundTag':'proxy-a','ruleTag':'EXAMPLE'},
            {'type':'field','network':'udp','balancerTag':'pool'}
        ],
        'balancers':[{'tag':'pool','selector':['proxy-'],'strategy':{'type':'random'},'fallbackTag':'direct'}]
    }
    put(c,'routing',routing)
    put(c,'observatory',{'subjectSelector':['proxy-'],'probeURL':'https://www.gstatic.com/generate_204','probeInterval':'30s','enableConcurrency':True})

    r=c.get('/api/traffic-engine');assert r.status_code==200,r.text
    doc=r.json();rows={x['tag']:x for x in doc['outbounds']}
    assert doc['routing']['default_outbound']=='direct'
    assert rows['direct']['default'] is True
    assert rows['direct']['dialed_by']==['proxy-a']
    assert rows['direct']['fallback_refs']==['pool']
    assert rows['proxy-a']['dials_via']=='direct'
    assert rows['proxy-a']['rule_refs']==[1]
    assert rows['proxy-a']['balancer_refs']==['pool']
    bal=doc['balancers'][0]
    assert bal['selectors']==['proxy-']
    assert bal['candidates']==['proxy-a','proxy-b']
    assert bal['observed_candidates']==['proxy-a','proxy-b']
    assert doc['warnings']==[]

    # Removing the fallback/dial target is refused even when no direct rule names it.
    reduced=[x for x in outbounds if x['tag']!='direct']
    bad=c.put('/api/settings/outbounds',json={'value':reduced})
    assert bad.status_code==422,bad.text
    assert 'outbound being removed' in bad.text.lower() or 'chained outbound' in bad.text.lower()


def test_outbound_removal_cannot_empty_balancer_prefix(env):
    _,_,_,_,c=env
    outbounds=[
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'edge-only','protocol':'freedom','settings':{}},
    ]
    put(c,'outbounds',outbounds)
    put(c,'routing',{'domainStrategy':'AsIs','rules':[],
                     'balancers':[{'tag':'edge-pool','selector':['edge-'],'strategy':{'type':'random'}}]})
    bad=c.put('/api/settings/outbounds',json={'value':[outbounds[0]]})
    assert bad.status_code==422,bad.text
    assert 'no matching outbound' in bad.text.lower()


def test_routing_v4_validates_and_normalizes_extended_rule_fields(env):
    _,_,_,_,c=env
    rule={
        'type':'field','ruleTag':'FULL-CONTEXT',
        'domain':['domain:example.com'],'ip':['1.1.1.0/24'],
        'port':'443,8443-8444','sourceIP':['203.0.113.0/24'],'sourcePort':'10000-20000',
        'localIP':['192.0.2.1'],'localPort':'19443','network':'tcp',
        'user':['regexp:^dark-'],'inboundTag':['dark-test'],'protocol':'http,tls',
        'process':['curl'],'vlessRoute':'1,14','attrs':{':method':'GET'},
        'outboundTag':'direct'
    }
    put(c,'routing',{'domainStrategy':'IPOnDemand','rules':[rule]})
    saved=c.get('/api/settings/routing').json()['value']['rules'][0]
    assert saved['protocol']==['http','tls']
    assert saved['sourceIP']==['203.0.113.0/24']
    assert saved['attrs']=={':method':'GET'}

    bad=json.loads(json.dumps(saved));bad['sourcePort']='70000'
    r=c.put('/api/settings/routing',json={'value':{'domainStrategy':'AsIs','rules':[bad]}})
    assert r.status_code==422 and 'sourcePort' in r.text


def test_route_preview_matches_literal_rule_and_chain_without_sending_traffic(env):
    _,_,_,_,c=env
    put(c,'outbounds',[
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'block','protocol':'blackhole','settings':{}},
        {'tag':'proxy-main','protocol':'freedom','settings':{},'streamSettings':{'sockopt':{'dialerProxy':'direct'}}},
        {'tag':'proxy-backup','protocol':'freedom','settings':{}},
    ])
    put(c,'routing',{
        'domainStrategy':'AsIs',
        'rules':[
            {'type':'field','ruleTag':'API-BLOCK','domain':['full:api.example.com'],'port':'443','outboundTag':'block'},
            {'type':'field','ruleTag':'USER-PROXY','user':['dark-user'],'inboundTag':['dark-test'],
             'sourceIP':['203.0.113.0/24'],'sourcePort':'12000-13000','protocol':['tls'],'outboundTag':'proxy-main'},
            {'type':'field','ruleTag':'EDGE-BAL','domain':['domain:edge.test'],'balancerTag':'pool'},
        ],
        'balancers':[{'tag':'pool','selector':['proxy-'],'strategy':{'type':'roundRobin'}}]
    })

    r=c.post('/api/traffic-engine/preview',json={'domain':'api.example.com','port':443,'network':'tcp'})
    assert r.status_code==200,r.text
    doc=r.json();assert doc['result']=='matched' and doc['rule_index']==1
    assert doc['selected_outbound']=='block' and doc['chain']==['block']
    assert doc['live_core_verified'] is False

    r=c.post('/api/traffic-engine/preview',json={
        'domain':'other.example','port':443,'network':'tcp','protocol':'tls','user':'dark-user',
        'inbound_tag':'dark-test','source_ip':'203.0.113.8','source_port':12500
    })
    doc=r.json();assert doc['rule_index']==2 and doc['selected_outbound']=='proxy-main'
    assert doc['chain']==['proxy-main','direct']

    r=c.post('/api/traffic-engine/preview',json={'domain':'www.edge.test','port':443,'network':'tcp'})
    doc=r.json();assert doc['target_type']=='balancer' and doc['target']=='pool'
    assert doc['candidates']==['proxy-main','proxy-backup']
    assert doc['selected_outbound']=='' and 'runtime' in doc['reason'].lower()


def test_route_preview_reports_geodata_as_indeterminate_not_fake_match(env):
    _,_,_,_,c=env
    put(c,'routing',{'domainStrategy':'AsIs','rules':[
        {'type':'field','ruleTag':'GOOGLE-GEO','domain':['geosite:google'],'outboundTag':'block'},
        {'type':'field','network':'tcp,udp','outboundTag':'direct'}
    ]})
    r=c.post('/api/traffic-engine/preview',json={'domain':'google.com','port':443,'network':'tcp'})
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['result']=='indeterminate'
    assert doc['rule_index']==1
    assert doc['live_core_verified'] is False
    assert 'geodata' in doc['reason'].lower() or 'dns' in doc['reason'].lower()


def test_route_preview_uses_first_outbound_when_no_rule_matches(env):
    _,_,_,_,c=env
    put(c,'outbounds',[
        {'tag':'custom-default','protocol':'freedom','settings':{}},
        {'tag':'direct','protocol':'freedom','settings':{}},
    ])
    put(c,'routing',{'domainStrategy':'AsIs','rules':[
        {'type':'field','domain':['full:only.example'],'outboundTag':'direct'}
    ]})
    doc=c.post('/api/traffic-engine/preview',json={'domain':'other.example','port':443,'network':'tcp'}).json()
    assert doc['result']=='default'
    assert doc['selected_outbound']=='custom-default'

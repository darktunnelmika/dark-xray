import node_agent

from test_node_control_lifecycle import rebooted_agent


def warp_outbound():
    return {'tag':'warp','protocol':'wireguard','settings':{
        'secretKey':'secret','address':['172.16.0.2/32'],
        'peers':[{'publicKey':'peer','endpoint':'162.159.192.1:2408',
                  'allowedIPs':['0.0.0.0/0','::/0']}]},'streamSettings':{'sockopt':{}}}


def test_node_agent_exposes_real_server_outbound_probe(tmp_path,monkeypatch):
    with rebooted_agent(tmp_path/'node') as (_store,engine,_runtime,client,_loop):
        engine.save_section('outbounds',[
            {'tag':'direct','protocol':'freedom','settings':{}},warp_outbound()])
        def fake(_binary,_assets,outbounds,*,tags=None,attempts=1,timeout=5.0,trace=False):
            return [{'tag':tag,'testable':True,'success':True,'delayMs':12.0,'lossPercent':0.0,
                     'jitterMs':0.0,'error':'','productionTrafficMutation':False}
                    for tag in (tags or [])]
        monkeypatch.setattr(node_agent,'probe_outbounds',fake)
        r=client.post('/node/api/v1/outbounds/probe',json={'tags':['warp'],'attempts':1,'timeoutSeconds':5})
        assert r.status_code==200,r.text
        doc=r.json()
        assert doc['service']=='DARK XRAY NODE'
        assert doc['items'][0]['tag']=='warp' and doc['items'][0]['delayMs']==12.0
        assert doc['productionTrafficMutation'] is False


def test_node_agent_warp_endpoint_probe_uses_local_warp_profile(tmp_path,monkeypatch):
    with rebooted_agent(tmp_path/'node') as (_store,engine,_runtime,client,_loop):
        engine.save_section('outbounds',[
            {'tag':'direct','protocol':'freedom','settings':{}},warp_outbound()])
        def fake(_binary,_assets,outbounds,*,tags=None,attempts=1,timeout=5.0,trace=False):
            rows=[]
            for out in outbounds:
                if tags and out['tag'] not in tags:continue
                endpoint=out['settings']['peers'][0]['endpoint']
                rows.append({'tag':out['tag'],'testable':True,'success':True,'delayMs':21.0,
                             'lossPercent':0.0,'jitterMs':2.0,'error':'','warpVerified':True,
                             'egress':{'ip':'104.28.1.1','country':'FR','colo':'CDG','warp':'on'},
                             'productionTrafficMutation':False})
            return rows
        monkeypatch.setattr(node_agent,'probe_outbounds',fake)
        r=client.post('/node/api/v1/warp/endpoints/probe',json={
            'tag':'warp','endpoints':['162.159.192.200:1701'],'attempts':2,'timeoutSeconds':4})
        assert r.status_code==200,r.text
        doc=r.json()
        assert doc['items'][0]['endpoint']=='162.159.192.200:1701'
        assert doc['items'][0]['warpVerified'] is True
        assert doc['productionTrafficMutation'] is False

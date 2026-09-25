import copy
import pytest

import smart_warp_probe as probe


def wireguard(tag='warp-us'):
    return {
        'tag':tag,
        'protocol':'wireguard',
        'settings':{
            'secretKey':'secret-do-not-return',
            'address':['172.16.0.2/32'],
            'peers':[{'publicKey':'public-peer','endpoint':'1.1.1.1:2408'}],
        },
    }


def test_non_wireguard_candidate_is_rejected():
    with pytest.raises(probe.SmartWarpProbeError,match='WireGuard'):
        probe._validate_candidate({'tag':'direct','protocol':'freedom','settings':{}})


def test_chained_wireguard_is_rejected():
    out=wireguard()
    out['streamSettings']={'sockopt':{'dialerProxy':'proxy'}}
    with pytest.raises(probe.SmartWarpProbeError,match='chained'):
        probe._validate_candidate(out)


def test_probe_config_is_loopback_and_routes_only_probe_inbound():
    source=wireguard()
    tag,candidate=probe._validate_candidate(source)
    cfg=probe._probe_config(candidate,32123)
    assert tag=='warp-us'
    assert cfg['inbounds'][0]['listen']=='127.0.0.1'
    assert cfg['inbounds'][0]['port']==32123
    assert cfg['routing']['rules']==[{
        'type':'field','inboundTag':['dark-warp-probe-in'],'outboundTag':'dark-warp-probe-out'
    }]
    assert source['tag']=='warp-us'
    assert candidate['tag']=='dark-warp-probe-out'


def test_scan_returns_sanitized_latency_and_always_stops(monkeypatch):
    stopped=[]
    class FakeProcess:
        def poll(self): return None
    monkeypatch.setattr(probe,'_free_loopback_port',lambda:32123)
    monkeypatch.setattr(probe,'_validate_xray',lambda *a,**k:None)
    monkeypatch.setattr(probe.subprocess,'Popen',lambda *a,**k:FakeProcess())
    monkeypatch.setattr(probe,'_wait_port',lambda *a,**k:None)
    values=iter([20.0,probe.SmartWarpProbeError('TimeoutError'),30.0])
    def fake_https(*a,**k):
        value=next(values)
        if isinstance(value,Exception):raise value
        return value
    monkeypatch.setattr(probe,'_https_probe',fake_https)
    monkeypatch.setattr(probe,'_stop',lambda p:stopped.append(p))

    out=wireguard()
    before=copy.deepcopy(out)
    result=probe.scan_warp_outbound('/xray','/assets',out,attempts=3,timeout=3)

    assert out==before
    assert result['tag']=='warp-us'
    assert result['ok'] is True
    assert result['latenciesMs']==[20.0,30.0]
    assert result['lossPercent']==pytest.approx(33.33)
    assert result['productionTrafficMutation'] is False
    assert 'secretKey' not in result
    assert 'secret-do-not-return' not in str(result)
    assert len(stopped)==1


def test_multi_scan_keeps_other_candidates_when_one_is_invalid(monkeypatch):
    monkeypatch.setattr(probe,'scan_warp_outbound',
        lambda binary,assets,outbound,**kwargs:
            (_ for _ in ()).throw(probe.SmartWarpProbeError('bad path'))
            if outbound.get('tag')=='bad'
            else {'tag':outbound['tag'],'ok':True,'latenciesMs':[12.0],
                  'lossPercent':0.0,'attempts':1,'successes':1,'failures':0,
                  'error':'','source':'isolated-temporary-xray-http-proxy',
                  'probeUrl':probe.PROBE_URL,'productionTrafficMutation':False})
    rows=probe.scan_warp_outbounds('/xray','/assets',[wireguard('bad'),wireguard('good')],attempts=1)
    assert rows[0]['ok'] is False and rows[0]['lossPercent']==100.0
    assert rows[1]['ok'] is True and rows[1]['tag']=='good'


def test_scan_caps_candidate_count():
    with pytest.raises(probe.SmartWarpProbeError,match='at most 8'):
        probe.scan_warp_outbounds('/xray','/assets',[wireguard(str(i)) for i in range(9)])
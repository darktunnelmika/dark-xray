import outbound_probe as probe


class FakeProcess:
    def __init__(self):
        self._returncode = None
    def poll(self):
        return self._returncode
    def terminate(self):
        self._returncode = 0
    def wait(self, timeout=None):
        return self._returncode
    def kill(self):
        self._returncode = -9


def test_probe_outbounds_shapes_batch_results_without_secrets(monkeypatch):
    outbounds = [
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'block','protocol':'blackhole','settings':{}},
        {'tag':'warp','protocol':'wireguard','settings':{
            'secretKey':'never-return-me','address':['172.16.0.2/32'],
            'peers':[{'publicKey':'peer','endpoint':'162.159.192.5:2408'}]}},
    ]
    monkeypatch.setattr(probe, '_validate_xray', lambda *a,**k: None)
    monkeypatch.setattr(probe, '_wait_port', lambda *a,**k: None)
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *a,**k: FakeProcess())
    monkeypatch.setattr(probe, '_request', lambda *a,**k: (42.4,204,''))
    rows = probe.probe_outbounds('/xray','/assets',outbounds,attempts=2)
    by = {x['tag']:x for x in rows}
    assert by['direct']['testable'] is False
    assert by['block']['testable'] is False
    assert by['warp']['success'] is True
    assert by['warp']['delayMs'] == 42.4
    assert by['warp']['lossPercent'] == 0
    assert 'never-return-me' not in str(rows)


def test_trace_probe_verifies_warp(monkeypatch):
    out = [{'tag':'warp','protocol':'wireguard','settings':{
        'secretKey':'secret','address':['172.16.0.2/32'],
        'peers':[{'publicKey':'peer','endpoint':'162.159.192.5:2408'}]}}]
    monkeypatch.setattr(probe, '_validate_xray', lambda *a,**k: None)
    monkeypatch.setattr(probe, '_wait_port', lambda *a,**k: None)
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *a,**k: FakeProcess())
    monkeypatch.setattr(probe, '_request', lambda *a,**k: (55.0,200,'ip=104.28.1.1\nloc=DE\ncolo=FRA\nwarp=on\n'))
    row = probe.probe_outbounds('/xray','/assets',out,trace=True)[0]
    assert row['success'] is True
    assert row['warpVerified'] is True
    assert row['egress']['country'] == 'DE'
    assert row['egress']['colo'] == 'FRA'

import json
from types import SimpleNamespace

import pytest
import updated


def test_update_ref_validation_is_narrow():
    assert updated.valid_ref('main')=='main'
    assert updated.valid_ref('v0.9.0-rc7')=='v0.9.0-rc7'
    assert updated.valid_ref('a'*40)=='a'*40
    for bad in ('../main','-main','main@{1}','a\\b','', 'x'*129):
        with pytest.raises(updated.UpdateError):
            updated.valid_ref(bad)


def test_update_channels_resolve_only_semver_tags(monkeypatch):
    output='\n'.join([
        'a'*40+' refs/tags/v0.8.2',
        'b'*40+' refs/tags/v0.9.0-rc1',
        'c'*40+' refs/tags/v0.9.0-rc7',
        'd'*40+' refs/tags/not-a-release',
        'e'*40+' refs/tags/v1.0.0',
    ])
    monkeypatch.setattr(updated,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=output,stderr=''))
    assert updated.resolve_channel('stable','')=='v1.0.0'
    assert updated.resolve_channel('rc','')=='v0.9.0-rc7'
    assert updated.resolve_channel('main','')=='main'


def test_controller_check_requires_green_ci_and_local_preflight(tmp_path,monkeypatch):
    app=tmp_path/'app';data=tmp_path/'data';app.mkdir();data.mkdir()
    (app/'VERSION').write_text('0.9.0-rc7\n')
    monkeypatch.setattr(updated,'APP',app);monkeypatch.setattr(updated,'DATA',data)
    monkeypatch.setattr(updated,'STATE',data/'update-state.json');monkeypatch.setattr(updated,'LOG',data/'update.log')
    monkeypatch.setattr(updated,'resolve_channel',lambda channel,ref:'main')
    monkeypatch.setattr(updated,'inspect_ref',lambda ref:{'ref':'main','commit':'1'*40,'version':'0.9.0-rc8','notes':'notes'})
    monkeypatch.setattr(updated,'local_preflight',lambda:{'database':True,'panel_service':True,'disk':True,'git':True,'ready':True})
    monkeypatch.setattr(updated,'ci_status',lambda commit:{'state':'failure','verified':False})
    c=updated.UpdateController(1234)
    result=c.check('main','')
    assert result['state']=='blocked'
    assert result['candidate']['ready'] is False
    assert any('CI' in x for x in result['warnings'])
    monkeypatch.setattr(updated,'ci_status',lambda commit:{'state':'success','verified':True,'run_id':42})
    result=c.check('main','')
    assert result['state']=='ready'
    assert result['candidate']['ready'] is True
    assert result['candidate']['commit']=='1'*40


def test_start_needs_prechecked_immutable_candidate(tmp_path,monkeypatch):
    app=tmp_path/'app';data=tmp_path/'data';app.mkdir();data.mkdir()
    (app/'VERSION').write_text('0.9.0-rc7\n')
    monkeypatch.setattr(updated,'APP',app);monkeypatch.setattr(updated,'DATA',data)
    monkeypatch.setattr(updated,'STATE',data/'update-state.json');monkeypatch.setattr(updated,'LOG',data/'update.log')
    c=updated.UpdateController(1234)
    with pytest.raises(updated.UpdateError,match='Check first'):
        c.start('2'*40)
    c.candidate={'commit':'2'*40,'version':'0.9.0-rc8','ready':True}
    class FakeThread:
        def __init__(self,*a,**k):self.started=False
        def is_alive(self):return False
        def start(self):self.started=True
    monkeypatch.setattr(updated.threading,'Thread',FakeThread)
    result=c.start('2'*40)
    assert result['state']=='queued'
    assert result['candidate']['commit']=='2'*40
    with pytest.raises(updated.UpdateError):
        c.start('not-a-sha')

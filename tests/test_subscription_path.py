import pytest
from test_settings_v2 import env,IB


def _client(c,email='sub-path-user'):
    assert c.post('/api/inbounds',json=IB).status_code==200
    ids=[x['id'] for x in c.get('/api/inbounds').json()]
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':email},'inboundIds':ids});assert r.status_code==202,r.text
    return r


def test_custom_subscription_path_changes_generated_url_and_route(env):
    store,engine,c=env
    sub=c.get('/api/settings/subscription').json()['value'];sub['path']='/dark-feed'
    r=c.put('/api/settings/subscription',json={'value':sub});assert r.status_code==200,r.text
    created=_client(c);url=created.json()['subscription_url']
    assert '/dark-feed/' in url and '/sub/' not in url
    out=c.get(url);assert out.status_code==200,out.text
    legacy=url.replace('/dark-feed/','/sub/')
    assert c.get(legacy).status_code==404


def test_subscription_path_reserved_and_panel_overlap_rejected(env):
    store,engine,c=env
    sub=c.get('/api/settings/subscription').json()['value']
    for path in ('/','/api','/assets/private','/node/x','/health/feed'):
        bad=dict(sub,path=path);assert c.put('/api/settings/subscription',json={'value':bad}).status_code==422
    runtime=c.get('/api/settings/runtime').json()['value'];runtime['panel_path']='/dark-admin'
    assert c.put('/api/settings/runtime',json={'value':runtime}).status_code==200
    bad=dict(sub,path='/dark-admin/feed')
    assert c.put('/api/settings/subscription',json={'value':bad}).status_code==422


def test_panel_path_cannot_be_staged_over_custom_subscription_path(env):
    store,engine,c=env
    sub=c.get('/api/settings/subscription').json()['value'];sub['path']='/feed-zone'
    assert c.put('/api/settings/subscription',json={'value':sub}).status_code==200
    runtime=c.get('/api/settings/runtime').json()['value'];runtime['panel_path']='/feed-zone/admin'
    r=c.put('/api/settings/runtime',json={'value':runtime});assert r.status_code==422 and 'overlaps' in r.text

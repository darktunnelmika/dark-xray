import base64,time
from test_standalone import env,create


def test_bulk_quota_reduction_cannot_turn_limited_client_unlimited(env):
    store,engine,m,auth,c=env
    create(c,extra={'totalGB':1024})
    r=c.post('/api/clients/bulk-adjust',json={'emails':['dark-test'],'add_bytes':-2048})
    assert r.status_code==200,r.text
    assert r.json()['changed']==0
    assert '0 means unlimited' in r.json()['items'][0]['error']
    d=c.get('/api/clients/dark-test').json()
    assert d['client']['totalGB']==1024


def test_bulk_negative_expiry_never_becomes_no_expiry(env):
    store,engine,m,auth,c=env
    create(c,extra={'expiryTime':int((time.time()+86400)*1000)})
    r=c.post('/api/clients/bulk-adjust',json={'emails':['dark-test'],'add_days':-36500})
    assert r.status_code==200,r.text
    assert r.json()['changed']==1
    d=c.get('/api/clients/dark-test').json()
    assert d['client']['expiryTime']==1000
    assert 'expired' in d['block_reasons']


def test_unicode_subscription_title_uses_safe_header_encoding(env):
    store,engine,m,auth,c=env
    d=create(c);url=d['subscription_url']
    s=c.get('/api/settings/subscription').json()['value']
    s['profile_title']='دارک وی پی ان'
    assert c.put('/api/settings/subscription',json={'value':s}).status_code==200
    r=c.get(url)
    assert r.status_code==200,r.text
    header=r.headers['profile-title']
    assert header.startswith('base64:')
    assert base64.b64decode(header[7:]).decode('utf-8')=='دارک وی پی ان'


def test_subscription_header_urls_reject_control_characters(env):
    store,engine,m,auth,c=env
    s=c.get('/api/settings/subscription').json()['value']
    s['support_url']='https://support.example.test\nX-Evil: yes'
    r=c.put('/api/settings/subscription',json={'value':s})
    assert r.status_code==422

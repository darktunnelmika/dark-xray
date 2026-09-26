import base64,json
from test_standalone import env,IB

def _ib(c):
    r=c.post('/api/inbounds',json=IB);assert r.status_code==200,r.text
    return r.json()['id']

def test_restore_import_isolated_and_old_url_takeover(env,monkeypatch):
    store,engine,_,_,c=env;inbound=_ib(c)
    restore=c.app.state.dark_restore
    monkeypatch.setattr(restore,'_scan',lambda url:{'status':'verified','error':'','upload':3_000,'download':7_000,'total':50_000,'expire':2_000_000_000})
    r=c.post('/api/dark-restore/import',json={'urls':['https://legacy.example/sub/abc'],'inboundIds':[inbound],'nodeIds':[],'scan':True})
    assert r.status_code==200,r.text
    row=c.get('/api/dark-restore').json()['items'][0]
    assert row['effective_used']==10_000 and row['legacy_total']==50_000
    assert c.get('/api/clients').json()==[]
    assert c.get('/api/unmanaged').json()==[]
    with store.lock:
        core=store.db.execute('SELECT body FROM core_clients WHERE email=?',(row['core_email'],)).fetchone()
    first=json.loads(core['body'])['id']
    r=c.post('/api/dark-restore/import',json={'urls':['https://legacy.example/sub/abc'],'inboundIds':[inbound],'nodeIds':[],'scan':True})
    assert r.status_code==200
    with store.lock:
        core=store.db.execute('SELECT body FROM core_clients WHERE email=?',(row['core_email'],)).fetchone()
    assert json.loads(core['body'])['id']==first
    old=c.get('/sub/abc',headers={'host':'legacy.example','accept':'text/plain'})
    assert old.status_code==200 and b'vless://' in base64.b64decode(old.content)
    assert old.headers['subscription-userinfo']=='upload=3000; download=7000; total=50000; expire=2000000000'
    item=c.get('/api/dark-restore').json()['items'][0]
    assert item['last_seen']>0

def test_restore_scan_rejects_private_destination(env,monkeypatch):
    _,_,_,_,c=env;restore=c.app.state.dark_restore
    monkeypatch.setattr('dark_restore.socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',0))])
    try:restore._validate_fetch_url('https://private.example/sub/x')
    except Exception as ex:assert 'non-public' in str(ex)
    else:raise AssertionError('private destination was accepted')

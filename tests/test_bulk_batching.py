from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app

OWNER=Actor('dark','owner',{})
PASSWORD='Test!OnlyPassword123'


def make_env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    cfg=Config(xray_binary=str(tmp_path/'missing'),xray_assets=str(tmp_path),public_address='vpn.test',test_engine=True)
    engine=CoreEngine(cfg,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark',PASSWORD);manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
    client=TestClient(make_app(manager,auth,background=False),base_url=cfg.public_origin)
    client.__enter__();login=client.post('/api/auth/login',json={'username':'dark','password':PASSWORD})
    client.headers['X-Dark-CSRF']=login.json()['csrf']
    return store,engine,manager,client


def inbound(tag,port):
    return {'remark':tag.upper(),'listen':'127.0.0.1','port':port,'protocol':'vless','enable':True,'tag':tag,
            'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}}


def test_bulk_paths_reconcile_once_per_request(tmp_path,monkeypatch):
    store,engine,manager,c=make_env(tmp_path)
    try:
        i1=c.post('/api/inbounds',json=inbound('batch-a',19701)).json()['id']
        i2=c.post('/api/inbounds',json=inbound('batch-b',19702)).json()['id']
        calls=0;real_tick=manager.tick
        def counted(*args,**kwargs):
            nonlocal calls;calls+=1;return real_tick(*args,**kwargs)
        monkeypatch.setattr(manager,'tick',counted)

        r=c.post('/api/clients/bulk-create',json={'owner':'dark','prefix':'batch-','postfix':'','first':1,'quantity':5,
            'inboundIds':[i1],'client':{'totalGB':1024*1024,'limitIp':1}})
        assert r.status_code==200,r.text
        assert r.json()['created']==5 and calls==1
        assert all(x['result']['state']=='applied' for x in r.json()['items'] if 'result' in x)

        # Bulk edit endpoints must never fall back to one Manager.update()/commit per item.
        def forbidden_update(*args,**kwargs):raise AssertionError('bulk endpoint used per-client Manager.update')
        monkeypatch.setattr(manager,'update',forbidden_update)
        calls=0
        r=c.post('/api/clients/bulk-adjust',json={'emails':[f'batch-{i}' for i in range(1,6)],'add_bytes':1024,'group':'LOAD'})
        assert r.status_code==200,r.text
        assert r.json()['changed']==5 and calls==1

        calls=0
        r=c.post('/api/clients/bulk-inbounds',json={'emails':[f'batch-{i}' for i in range(1,6)],'inboundIds':[i2],'mode':'attach'})
        assert r.status_code==200,r.text
        assert r.json()['changed']==5 and calls==1
        assert all(set(c.get(f'/api/clients/batch-{i}').json()['inboundIds'])=={i1,i2} for i in range(1,6))
    finally:
        c.__exit__(None,None,None);manager.close();engine.close();store.close()


def test_bulk_create_mixed_duplicate_does_not_poison_other_items(tmp_path,monkeypatch):
    store,engine,manager,c=make_env(tmp_path)
    try:
        i1=c.post('/api/inbounds',json=inbound('batch-c',19703)).json()['id']
        assert c.post('/api/clients',json={'owner':'dark','client':{'email':'mix-2'},'inboundIds':[i1]}).status_code==202
        calls=0;real_tick=manager.tick
        def counted(*args,**kwargs):
            nonlocal calls;calls+=1;return real_tick(*args,**kwargs)
        monkeypatch.setattr(manager,'tick',counted)
        r=c.post('/api/clients/bulk-create',json={'owner':'dark','prefix':'mix-','postfix':'','first':1,'quantity':3,
            'inboundIds':[i1],'client':{'limitIp':1}})
        assert r.status_code==200,r.text
        body=r.json();assert body['created']==2 and calls==1
        assert [x['email'] for x in body['items'] if 'error' in x]==['mix-2']
        assert c.get('/api/clients/mix-1').status_code==200
        assert c.get('/api/clients/mix-3').status_code==200
    finally:
        c.__exit__(None,None,None);manager.close();engine.close();store.close()


def test_bulk_adjust_uses_one_core_batch_transaction(tmp_path,monkeypatch):
    store,engine,manager,c=make_env(tmp_path)
    try:
        i1=c.post('/api/inbounds',json=inbound('batch-fast',19704)).json()['id']
        r=c.post('/api/clients/bulk-create',json={'owner':'dark','prefix':'fast-','postfix':'','first':1,'quantity':25,
            'inboundIds':[i1],'client':{'totalGB':1024*1024,'limitIp':1}})
        assert r.status_code==200 and r.json()['created']==25
        calls=[];real=engine.upsert_many;policy_calls=[];real_policy=store.edit_clients_many
        def counted(items):
            calls.append(len(items));return real(items)
        def counted_policy(actor,items):
            policy_calls.append(len(items));return real_policy(actor,items)
        monkeypatch.setattr(engine,'upsert_many',counted)
        monkeypatch.setattr(store,'edit_clients_many',counted_policy)
        r=c.post('/api/clients/bulk-adjust',json={'emails':[f'fast-{i}' for i in range(1,26)],
            'add_days':1,'group':'FAST'})
        assert r.status_code==200,r.text
        assert r.json()['changed']==25
        assert calls==[25] and policy_calls==[25]
        assert all(x.get('result',{}).get('client',{}).get('group')=='FAST' for x in r.json()['items'])
    finally:
        c.__exit__(None,None,None);manager.close();engine.close();store.close()

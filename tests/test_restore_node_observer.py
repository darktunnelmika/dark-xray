from test_restore_groups import env,prepare,add,row


def node(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    monkeypatch.setattr('nodes.socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    result=c.post('/api/nodes',json={'id':'n1','name':'Node 1','origin':'https://node.example.com',
        'token':'dkn_'+'O'*60,'enabled':True,'inboundIds':[inbound]})
    assert result.status_code==200,result.text
    add(c,inbound)
    return store,c,inbound,row(c),c.app.state.nodes


def test_unchanged_restore_sample_advances_watermark(env,monkeypatch):
    store,c,inbound,record,registry=node(env,monkeypatch)
    email=record['core_email']
    for at,up,down in [(1000,10,20),(1200,10,20),(1100,2,3)]:
        registry.apply_traffic_snapshot('n1',[{'sourceEmail':email,'up':up,'down':down}],captured_at=at)
    assert row(c)['dark_used']==30
    usage=store.db.execute("SELECT updated_at FROM restore_usage WHERE scope='node:n1'").fetchone()
    assert usage[0]==1200


def test_mixed_native_and_restore_snapshots_keep_native_ledger_intact(env,monkeypatch):
    store,c,inbound,record,registry=node(env,monkeypatch)
    created=c.post('/api/clients',json={'owner':'dark','client':{'email':'native-user'},'inboundIds':[inbound]})
    assert created.status_code==202,created.text
    restored=record['core_email']
    def snapshot(at,native_up,restore_up):
        return registry.apply_traffic_snapshot('n1',[
            {'sourceEmail':'native-user','up':native_up,'down':0},
            {'sourceEmail':restored,'up':restore_up,'down':0}],captured_at=at)
    first=snapshot(1000,1000,100)
    assert first['charged_bytes']==0 and row(c)['dark_used']==100
    second=snapshot(1010,1050,140)
    assert second['charged_bytes']==50 and row(c)['dark_used']==140
    ledger=store.db.execute("SELECT client_id,SUM(up_bytes+down_bytes) FROM traffic_ledger WHERE event_id LIKE 'node:%' GROUP BY client_id").fetchall()
    assert [tuple(x) for x in ledger]==[('native-user',50)]
    assert store.db.execute("SELECT used_bytes FROM clients WHERE id='native-user'").fetchone()[0]==50


def test_restore_lease_accepts_both_committed_ledgers_but_not_unknown(env,monkeypatch):
    import pytest
    from dark_policy import PolicyError
    from node_lease_sync import require_accounted_snapshot
    store,c,inbound,record,registry=node(env,monkeypatch)
    response=c.post('/api/clients',json={'owner':'dark','client':{'email':'native-user'},'inboundIds':[inbound]})
    assert response.status_code==202,response.text
    items=[{'sourceEmail':'native-user','up':10,'down':0},
           {'sourceEmail':record['core_email'],'up':20,'down':0}]
    accepted=registry.apply_traffic_snapshot('n1',items,captured_at=1000)
    assert accepted['ignored_clients']==1 and accepted['restore_usage']['clients']==1
    require_accounted_snapshot(registry,c.app.state.manager,'n1',accepted)
    rejected=registry.apply_traffic_snapshot('n1',items+[{'sourceEmail':'unknown','up':30,'down':0}],captured_at=1010)
    with pytest.raises(PolicyError,match='unmanaged'):
        require_accounted_snapshot(registry,c.app.state.manager,'n1',rejected)
    with store.transaction() as db:
        db.execute('UPDATE restore_subscriptions SET core_email=? WHERE id=?',('native-user',record['id']))
    with pytest.raises(PolicyError,match='ambiguous'):
        require_accounted_snapshot(registry,c.app.state.manager,'n1',accepted)

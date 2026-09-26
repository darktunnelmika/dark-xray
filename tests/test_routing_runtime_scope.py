import json

from core import Config, CoreEngine
from dark_policy import Store


def test_routing_rules_are_filtered_by_runtime_scope(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    cfg=Config(xray_binary='/bin/true',xray_assets=str(tmp_path),public_address='127.0.0.1',
               test_engine=True,core_autostart=False)
    engine=CoreEngine(cfg,store,tmp_path/'runtime')
    engine.save_section('outbounds',[
        {'tag':'direct','protocol':'freedom','settings':{}},
        {'tag':'warp','protocol':'wireguard','settings':{
            'secretKey':'secret','address':['172.16.0.2/32'],
            'peers':[{'publicKey':'peer','endpoint':'162.159.192.1:2408'}]}},
    ])
    routing={'domainStrategy':'AsIs','rules':[
        {'type':'field','ruleTag':'hub-only','domain':['domain:hub.test'],'outboundTag':'direct'},
        {'type':'field','ruleTag':'node-only','domain':['domain:node.test'],'outboundTag':'warp'},
        {'type':'field','ruleTag':'everywhere','domain':['domain:all.test'],'outboundTag':'direct'},
    ]}
    engine.save_section('routing',routing)
    with store.transaction() as db:
        db.executemany('INSERT INTO routing_rule_scopes(rule_tag,scope,updated_at) VALUES(?,?,1)',[
            ('hub-only','hub'),('node-only','node:n1'),('everywhere','all')])
    hub=engine.routing_for_scope('hub')
    n1=engine.routing_for_scope('node:n1')
    n2=engine.routing_for_scope('node:n2')
    assert [x['ruleTag'] for x in hub['rules']]==['hub-only','everywhere']
    assert [x['ruleTag'] for x in n1['rules']]==['node-only','everywhere']
    assert [x['ruleTag'] for x in n2['rules']]==['everywhere']
    assert [x['ruleTag'] for x in engine.build_config()['routing']['rules']]==['hub-only','everywhere']
    store.close()


def test_routing_scope_table_survives_engine_reopen(tmp_path):
    db=tmp_path/'dark.sqlite3'
    store=Store(db);cfg=Config(xray_binary='/bin/true',xray_assets=str(tmp_path),public_address='127.0.0.1',test_engine=True)
    CoreEngine(cfg,store,tmp_path/'runtime-a')
    with store.transaction() as tx:
        tx.execute('INSERT INTO routing_rule_scopes(rule_tag,scope,updated_at) VALUES(?,?,1)',('r1','node:n9'))
    store.close()
    reopened=Store(db);engine=CoreEngine(cfg,reopened,tmp_path/'runtime-b')
    with reopened.lock:
        row=reopened.db.execute('SELECT scope FROM routing_rule_scopes WHERE rule_tag=?',('r1',)).fetchone()
    assert row['scope']=='node:n9'
    reopened.close()

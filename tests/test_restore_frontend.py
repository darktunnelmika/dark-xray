import importlib.util,json,sqlite3,sys,time
from pathlib import Path
import pytest
from test_standalone import env,IB
from dark_policy import PolicyError
import restore_frontend
from restore_scan import parse_userinfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import restore_tls


def imported(env):
    store,engine,_,_,client=env
    inbound=client.post('/api/inbounds',json=IB).json()['id']
    # HEAD delivery needs an eligible fixture, not unknown values defaulted to zero.
    client.app.state.dark_restore._scan=lambda url:parse_userinfo('upload=0;download=0;total=0;expire=0')
    out=client.post('/api/dark-restore/import',json={'urls':['https://legacy.example:2096/sub/original?token=kept'],
        'inboundIds':[inbound],'scan':True,'groupName':'Representative A'})
    assert out.status_code==200,out.text
    return client.get('/api/dark-restore').json()['items'][0]


def test_head_preserves_identity_group_quota_and_migration_timestamp(env):
    row=imported(env);store,_,_,_,c=env
    with store.lock:before=tuple(store.db.execute('SELECT * FROM restore_subscriptions').fetchone())
    r=c.head('/sub/original?token=kept',headers={'host':'legacy.example:2096'})
    assert r.status_code==200,r.text
    assert not r.content and int(r.headers['content-length'])>0
    assert r.headers['cache-control']=='no-store'
    with store.lock:
        assert tuple(store.db.execute('SELECT * FROM restore_subscriptions').fetchone())==before
        assert store.db.execute("SELECT COUNT(*) FROM restore_events WHERE event='subscription.update'").fetchone()[0]==0
    r=c.get('/sub/original?token=kept',headers={'host':'legacy.example:2096'})
    assert r.status_code==200
    with store.lock:assert store.db.execute('SELECT first_seen FROM restore_subscriptions').fetchone()[0]>0


def test_head_still_rejects_expired_and_unknown_subscriptions(env):
    row=imported(env);store,_,_,_,c=env
    with store.transaction() as db:db.execute('UPDATE restore_subscriptions SET legacy_expire=?',(int(time.time())-100,))
    assert c.head('/sub/original?token=kept',headers={'host':'legacy.example:2096'}).status_code==403
    assert c.head('/sub/unknown',headers={'host':'legacy.example:2096'}).status_code==400
    with store.lock:assert store.db.execute('SELECT first_seen FROM restore_subscriptions').fetchone()[0]==0


def test_domain_checks_original_listener_not_just_certificate_files(env,monkeypatch):
    row=imported(env);_,_,_,_,c=env;restore=c.app.state.dark_restore;calls=[]
    monkeypatch.setattr(restore,'_public_addresses',lambda host:['93.184.216.34'])
    monkeypatch.setattr(restore_frontend,'probe',lambda *args:(calls.append(args) or False))
    out=restore.check_domain('legacy.example')
    assert out['original_ports']==[2096] and not out['ready'] and out['requires_root_apply']
    assert calls==[('93.184.216.34','legacy.example',2096,'https')]
    monkeypatch.setattr(restore_frontend,'probe',lambda *args:True)
    out=restore.check_domain('legacy.example')
    assert out['ready'] and out['ssl_status']=='ready' and not out['requires_root_apply']


def test_dns_mismatch_never_probes_or_claims_activation(env,monkeypatch):
    imported(env);restore=env[-1].app.state.dark_restore
    monkeypatch.setattr(restore,'_public_addresses',lambda host:['93.184.216.34'] if host=='legacy.example' else ['1.1.1.1'])
    monkeypatch.setattr(restore_frontend,'probe',lambda *args:pytest.fail('Must not probe wrong destination'))
    out=restore.check_domain('legacy.example')
    assert out['dns_status']=='mismatch' and not out['ready']


def make_plan(tmp_path,urls=None):
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'public_origin':'https://panel.example:2087',
        'bind_host':'0.0.0.0','bind_port':2087,'public_address':'93.184.216.34'}))
    with sqlite3.connect(tmp_path/'dark.sqlite3') as db:
        db.execute('CREATE TABLE restore_subscriptions(legacy_url TEXT,legacy_host TEXT)')
        db.execute('CREATE TABLE restore_domains(domain TEXT,acme_email TEXT)')
        db.executemany('INSERT INTO restore_subscriptions VALUES(?,?)',[(u,'legacy.example') for u in
            (urls or ['https://legacy.example:2096/sub/secret-token','https://legacy.example:2096/sub/other-token'])])
    return restore_tls.plan(cfg,tmp_path,'legacy.example')


def test_plan_preserves_2096_and_never_serializes_customer_tokens(tmp_path):
    p=make_plan(tmp_path)
    assert p['endpoints']==[{'scheme':'https','port':2096}] and p['count']==2
    assert 'secret-token' not in json.dumps(p)
    assert p['upstream']=='https://127.0.0.1:2087'
    text=restore_tls.render_site(p)
    assert 'listen 0.0.0.0:2096 ssl;' in text
    assert 'listen 0.0.0.0:443' not in text
    assert 'proxy_set_header Host $http_host;' in text
    assert 'proxy_pass https://127.0.0.1:2087;' in text
    assert 'proxy_ssl_verify on;' in text and 'proxy_ssl_name panel.example;' in text
    assert 'if ($host != legacy.example)' in text
    assert 'secret-token' not in text and 'return 301' not in text
    assert 'limit_except GET { deny all; }' in text
    assert 'access_log off;' in restore_tls.MAIN_TEXT


@pytest.mark.parametrize('urls',[
    ['https://legacy.example:2087/sub/x'],
    ['https://legacy.example:2096/sub/x','http://legacy.example:2096/sub/y'],
    ['https://legacy.example:99999/sub/x'],
    ['http://legacy.example/sub/x'],
])
def test_unsafe_or_conflicting_ports_refused(tmp_path,urls):
    with pytest.raises(ValueError):make_plan(tmp_path,urls)


@pytest.mark.parametrize('value',['../evil','example.com; touch /tmp/x','example.com\nlisten 22','127.0.0.1','example.com/path'])
def test_domain_cannot_inject_paths_or_nginx_config(value):
    with pytest.raises(ValueError):restore_tls.normalize_domain(value)


def test_cli_dispatch_is_real_and_renewal_only_reloads_frontend():
    cli=(ROOT/'darkxray').read_text()
    assert 'restore-tls) shift;' in cli and 'tools/restore_tls.py' in cli
    assert 'systemctl reload dark-xray-restore.service' in restore_tls.HOOK_TEXT
    assert 'restart dark-xray.service' not in restore_tls.HOOK_TEXT
    assert 'sites-enabled' not in restore_tls.MAIN_TEXT

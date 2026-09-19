import time
from pathlib import Path

from test_settings_v2 import env, IB


def seed_security_client(c,email='security-v4'):
    r=c.post('/api/inbounds',json=IB);assert r.status_code==200,r.text
    iid=r.json()['id']
    r=c.post('/api/clients',json={'owner':'dark','client':{
        'email':email,'limitIp':1,'limitHwid':1
    },'inboundIds':[iid]})
    assert r.status_code==202,r.text
    return iid


def test_security_center_aggregates_local_and_node_ip_device_state(env):
    store,_,c=env
    seed_security_client(c)
    now=time.time()
    with store.transaction() as db:
        db.execute("INSERT INTO observations(client_id,ip,node,first_seen,last_seen,granted) VALUES(?,?,?,?,?,1)",
                   ('security-v4','8.8.8.8','local',now-10,now))
        db.execute("INSERT INTO core_devices(email,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?)",
                   ('security-v4','a'*64,'ios','local-phone',now-10,now))
        db.execute("INSERT INTO remote_node_ips(node_id,client_id,ip,first_seen,last_seen,verified) VALUES(?,?,?,?,?,1)",
                   ('n1','security-v4','8.8.8.8',now-10,now))
        db.execute("INSERT INTO remote_node_ips(node_id,client_id,ip,first_seen,last_seen,verified) VALUES(?,?,?,?,?,1)",
                   ('n1','security-v4','1.1.1.1',now-10,now))
        db.execute("INSERT INTO remote_node_devices(node_id,client_id,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
                   ('n1','security-v4','a'*64,'ios','same-phone',now-10,now))
        db.execute("INSERT INTO remote_node_devices(node_id,client_id,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
                   ('n1','security-v4','b'*64,'android','node-phone',now-10,now))
        db.execute("UPDATE clients SET global_ip_block=1 WHERE id='security-v4'")
        db.execute("INSERT INTO events(kind,owner,client_id,ip,node,detail,at) VALUES(?,?,?,?,?,?,?)",
                   ('violation','dark','security-v4','1.1.1.1','n1','distinct recent source IP quota exceeded',now))
        db.execute("INSERT INTO bans(jail,ip,client_id,node,expires_at,state) VALUES(?,?,?,?,?,'applied')",
                   ('dark-native-scope','9.9.9.9','security-v4','local',now+600))

    r=c.get('/api/security-center');assert r.status_code==200,r.text
    doc=r.json();row=next(x for x in doc['clients'] if x['email']=='security-v4')
    assert row['local_ip_count']==1 and row['remote_ip_count']==2 and row['global_ip_count']==2
    assert row['local_device_count']==1 and row['remote_device_count']==2 and row['global_device_count']==2
    assert row['global_ip_block'] is True
    assert doc['summary']['ip_blocked']==1 and doc['summary']['active_local_bans']==1
    assert doc['summary']['recent_violations']>=1
    assert doc['architecture']['local_enforcer']=='root-owned DARK nftables broker'
    assert doc['architecture']['global_remote_firewall_ban'] is False
    assert doc['architecture']['fail2ban'] is False
    assert doc['bans'][0]['ip']=='9.9.9.9'


def test_ip_status_distinguishes_local_firewall_from_global_account_policy(env):
    _,_,c=env
    r=c.get('/api/ip-status');assert r.status_code==200,r.text
    doc=r.json()
    assert doc['source']=='DARK Native IP Guard'
    assert doc['global_multi_node_limit'] is False
    assert doc['global_account_policy'] is True
    assert 'nftables' in doc['engine']['enforcement']
    assert 'remote' in doc['global_policy_note'].lower()


def test_fail2ban_runtime_adapter_is_absent():
    root=Path(__file__).resolve().parents[1]
    policy=(root/'backend'/'dark_policy.py').read_text(encoding='utf-8')
    core=(root/'backend'/'core.py').read_text(encoding='utf-8')
    assert 'Fail2BanExecutor' not in policy
    assert 'render_fail2ban' not in policy
    assert 'fail2ban-client' not in policy
    assert 'render_fail2ban' not in core


def test_security_center_frontend_uses_native_guard_and_hwid_controls():
    root=Path(__file__).resolve().parents[1]
    ui=(root/'web'/'ipguard-v4.js').read_text(encoding='utf-8')
    clients=(root/'web'/'clients-v4.js').read_text(encoding='utf-8')
    live=(root/'web'/'live.js').read_text(encoding='utf-8')
    assert '/api/security-center' in ui
    assert 'Native nftables Guard' in ui
    assert 'global_ip_count' in ui and 'global_device_count' in ui
    assert "name="limitHwid"" not in clients  # helper emits the attribute dynamically
    assert "'limitHwid',c.limitHwid??0" in clients
    assert "limitHwid:Number(f.get('limitHwid')||0)" in clients
    assert 'کارگر Fail2ban' not in live

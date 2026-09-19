import dataclasses
import json
import sqlite3
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from dark_policy import (Actor, ClientPolicy, Fail2BanExecutor, Guard, MAX_INT, PermissionDenied,
    Policy, PolicyError, Store, jail_for, load_policy, normalize_ip, parse_access_line, render_fail2ban)

OWNER=Actor('root','owner')
RESELLER=Actor('arda',permissions={f'clients.{a}':'own' for a in ['read','create','edit','delete','reset']}|{'owners.read':'own'})
READONLY=Actor('support','readonly',{'clients.read':'all','owners.read':'all'})

def policy(**extra):
    raw={'schema':1,'clients':{'a':{'owner':'arda','limit_ip':1,'ports':[443,8443]},'b':{'owner':'dark','limit_ip':2,'ports':[443]}},
         'source_mode':'direct','original_ip_verified':True,'enforce':False,'window_seconds':120,'ban_seconds':1800}
    raw.update(extra);return Policy.from_dict(raw)

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'test.db')
    s.register_owner(OWNER,'arda',volume_credit_bytes=1000,unlimited_credit=2,max_clients=2)
    s.register_owner(OWNER,'dark')
    with s.transaction() as db:
        db.execute("INSERT INTO api_admins(id,role,password_hash,permissions,disabled) VALUES('arda','reseller','x','{}',0)")
    yield s;s.close()

@pytest.mark.parametrize('raw,canonical',[('8.8.8.8','8.8.8.8'),('[2001:4860:4860:0:0:0:0:8888]','2001:4860:4860::8888'),('::ffff:8.8.8.8','8.8.8.8')])
def test_ip_canonical(raw,canonical):assert normalize_ip(raw)==canonical

@pytest.mark.parametrize('raw',['8.8.8.999','8.8.8.8; reboot','[::1]:80','1.1.1.1:443','fe80::1%eth0','08.8.8.8','--help'])
def test_invalid_ip(raw):
    with pytest.raises(PolicyError):normalize_ip(raw)

def test_one_ip_many_connections(store):
    g=Guard(policy(),store)
    for t in [1000,1001,1002]:assert g.observe('a','8.8.8.8',t)['decision']=='allow'
    assert g.observe('a','1.1.1.1',1003)=={'decision':'violation','applied':False,'reason':'monitor_mode','jail':jail_for((443,8443))}
    assert g.observe('a','1.1.1.1',1004)['decision']=='violation'
    assert store.db.execute('select count(*) from events').fetchone()[0]==1

def test_ipv4mapped_same_slot(store):
    g=Guard(policy(),store);g.observe('a','8.8.8.8',1000)
    assert g.observe('a','::ffff:8.8.8.8',1001)['decision']=='allow'

def test_expired_lease_allows_new_ip(store):
    g=Guard(policy(),store);g.observe('a','8.8.8.8',1000)
    assert g.observe('a','1.1.1.1',1121)['decision']=='allow'

def test_two_nodes_share_lease(store):
    g=Guard(policy(node_id='tr'),store);h=Guard(policy(node_id='de'),store)
    g.observe('a','8.8.8.8',1000)
    assert h.observe('a','1.1.1.1',1001)['decision']=='violation'
    assert h.observe('a','8.8.8.8',1002)['decision']=='allow'

def test_unlimited(store):
    p=policy(clients={'a':{'owner':'arda','limit_ip':0,'ports':[443]}})
    for ip in ['8.8.8.8','1.1.1.1','9.9.9.9']:assert Guard(p,store).observe('a',ip)['decision']=='allow'

def test_shared_nat_same_address_not_device_limit(store):
    g=Guard(policy(),store)
    assert g.observe('a','8.8.8.8',1000)['decision']=='allow'
    assert g.observe('b','8.8.8.8',1001)['decision']=='allow'

@pytest.mark.parametrize('mode',['opaque','trusted-proxy'])
def test_opaque_fail_safe(store,mode):
    p=policy(source_mode=mode,enforce=True)
    assert Guard(p,store).observe('a','8.8.8.8')['decision']=='observe_only'
    with pytest.raises(PolicyError):p.assert_enforcement_safe()

def test_unverified_fail_safe(store):
    p=policy(enforce=True,original_ip_verified=False)
    with pytest.raises(PolicyError):p.assert_enforcement_safe()
    assert Guard(p,store).observe('a','8.8.8.8')['applied'] is False

@pytest.mark.parametrize('ip',['127.0.0.1','::1','10.0.0.1','fe80::1','224.0.0.1','0.0.0.0'])
def test_special_sources_exempt(store,ip):assert Guard(policy(),store).observe('a',ip)['decision']=='exempt'

def test_explicit_exempt_peer(store):
    g=Guard(policy(trusted_peers=['8.8.8.8'],exempt_ips=['1.1.1.0/24']),store)
    assert g.observe('a','8.8.8.8')['reason']=='trusted_peer'
    assert g.observe('a','1.1.1.1')['reason']=='explicit_exemption'

@pytest.mark.parametrize('port',[22,2053,2096,0,65536])
def test_bad_data_port_refused(port):
    with pytest.raises(PolicyError):policy(clients={'a':{'owner':'arda','ports':[port],'limit_ip':1}})

@pytest.mark.parametrize('flag',['enforce','original_ip_verified','allow_private_sources'])
def test_policy_booleans_strict(flag):
    with pytest.raises(PolicyError):policy(**{flag:'false'})

def test_unknown_client(store):assert Guard(policy(),store).observe('nobody','8.8.8.8')['decision']=='ignored'

class FakeExecutor:
    def __init__(self,fail=False):self.calls=[];self.fail=fail
    def ban(self,jail,ip):
        if self.fail:raise PolicyError('unavailable')
        self.calls.append(('ban',jail,ip))
    def unban(self,jail,ip):self.calls.append(('unban',jail,ip))

def test_adapter_receives_only_extra_ip(store):
    e=FakeExecutor();g=Guard(policy(enforce=True),store,e)
    g.observe('a','8.8.8.8',1000)
    r=g.observe('a','1.1.1.1',1001)
    assert r['applied'] is True and r['until']==2801
    assert e.calls==[('ban',jail_for((443,8443)),'1.1.1.1')]
    assert g.observe('a','1.1.1.1',1002)['decision']=='banned'
    assert len(e.calls)==1

def test_adapter_failure_not_claimed_applied(store):
    g=Guard(policy(enforce=True),store,FakeExecutor(True));g.observe('a','8.8.8.8',1000)
    assert g.observe('a','1.1.1.1',1001)['decision']=='enforcement_failed'
    assert store.db.execute('select count(*) from bans').fetchone()[0]==0

def test_unban_owner_only(store):
    e=FakeExecutor();g=Guard(policy(enforce=True),store,e)
    with pytest.raises(PermissionDenied):g.unban(RESELLER,jail_for((443,8443)),'1.1.1.1')
    g.unban(OWNER,jail_for((443,8443)),'1.1.1.1')
    assert len(e.calls)==1

def test_executor_no_shell_and_whitelist():
    calls=[]
    def runner(args,**kwargs):
        calls.append((args,kwargs));return subprocess.CompletedProcess(args,0,'1800\n' if 'bantime' in args else 'OK','')
    e=Fail2BanExecutor(policy(enforce=True),executable='/usr/bin/true',runner=runner)
    e.check();e.ban(jail_for((443,8443)),'8.8.8.8')
    assert calls[-1][0][-3:]==[jail_for((443,8443)),'banip','8.8.8.8']
    assert all(not kw.get('shell') for _,kw in calls)
    with pytest.raises(PolicyError):e.ban('sshd','1.1.1.1')
    with pytest.raises(PolicyError):e.ban(jail_for((443,8443)),'1.1.1.1; reboot')

def test_config_dedicated_ports():
    files=render_fail2ban(policy(enforce=True));text=files['jail.d/dark-xray-policy.local']
    assert 'port="443,8443"' in text and 'protocol="tcp,udp"' in text
    assert '[sshd]' not in text and 'action = ' in text and 'port="22' not in text

def test_policy_file_ownership(tmp_path):
    p=tmp_path/'p.json';p.write_text(json.dumps({'schema':1,'clients':{},'enforce':True,'source_mode':'direct','original_ip_verified':True}));p.chmod(0o666)
    with pytest.raises(PolicyError):load_policy(p,apply=True)

@pytest.mark.parametrize('line,ip',[
 ('2026/09/12 10:00:00 from 8.8.8.8:12345 accepted tcp:example.com:443 [a -> b] email: a','8.8.8.8'),
 ('2026/09/12 10:00:00 tcp:[2001:4860:4860::8888]:23456 accepted tcp:example.com:443 email: a','2001:4860:4860::8888')])
def test_log_parser_source_only(line,ip):
    observation=parse_access_line(line,1000);assert observation.email=='a' and observation.ip==ip and observation.timestamp==1000

@pytest.mark.parametrize('line',[
 'from 8.8.8.8:1 rejected tcp:example.com:443 email: a',
 'from 8.8.8.8:1 accepted tcp:example.com:443',
 'malformed accepted email: a',
 'from 8.8.8.8:65536 accepted tcp:example.com:443 email: a'])
def test_invalid_log_ignored(line):assert parse_access_line(line) is None

def test_owner_usage_survives_reset_delete(store):
    store.register_client(OWNER,'a','arda');store.record_usage('evt','a',13,10)
    assert store.owner_stats(OWNER,'arda')['used_bytes']==23
    store.reset_client_usage(RESELLER,'a')
    assert store.owner_stats(OWNER,'arda')['used_bytes']==23
    store.delete_client(RESELLER,'a')
    assert store.owner_stats(OWNER,'arda')['used_bytes']==23
    assert store.owner_stats(OWNER,'arda')['client_count']==0

def test_usage_idempotency_and_conflict(store):
    store.register_client(OWNER,'a','arda');assert store.record_usage('one','a',7,9)
    assert not store.record_usage('one','a',7,9)
    with pytest.raises(PolicyError):store.record_usage('one','a',8,9)
    assert store.owner_stats(OWNER,'arda')['used_bytes']==16

def test_cross_owner_no_read_or_edit(store):
    store.register_client(OWNER,'a','arda');store.register_client(OWNER,'b','dark')
    assert [u['id'] for u in store.list_clients(RESELLER)]==['a']
    with pytest.raises(PermissionDenied):store.edit_client(RESELLER,'b',limit_ip=9)
    with pytest.raises(PermissionDenied):store.owner_stats(RESELLER,'dark')
    with pytest.raises(PermissionDenied):store.delete_client(READONLY,'a')

def test_manual_flags_survive_topup_and_resets(store):
    store.register_client(OWNER,'a','arda',quota_bytes=10);store.edit_client(OWNER,'a',manual=True,expires_at=1)
    store.record_usage('evt','a',10,0);store.register_owner(OWNER,'arda',quota_bytes=10,manual=True)
    assert set(store.client_reasons('a'))=={'client_manual','expired','client_quota','owner_manual'}
    store.register_owner(OWNER,'arda',quota_bytes=100)
    assert 'owner_manual' in store.client_reasons('a')
    store.reset_client_usage(OWNER,'a');store.reset_owner_period(OWNER,'arda')
    assert set(store.client_reasons('a'))=={'client_manual','expired','owner_manual'}
    assert store.owner_stats(OWNER,'arda')['lifetime_used_bytes']==10

def test_exhausted_representative_unlimited_credit_cannot_create(store):
    store.register_owner(OWNER,'arda',volume_credit_bytes=0,unlimited_credit=1)
    store.register_client(RESELLER,'a','arda')
    store.record_usage('evt','a',MAX_INT//4,0)
    assert 'owner_quota' not in store.client_reasons('a')
    with pytest.raises(PolicyError,match='unlimited credit'):store.register_client(RESELLER,'b','arda')

def test_atomic_client_quota_under_concurrency(store):
    def add(n):
        try:return store.register_client(RESELLER,'c'+str(n),'arda')
        except PolicyError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(add,range(20)))
    assert sum(results)==2 and store.owner_stats(OWNER,'arda')['client_count']==2

def test_resource_credit_adjustment_and_idempotency(store):
    assert store.adjust_resource_credit(OWNER,'arda',500,1,'resource-topup-0001')
    assert not store.adjust_resource_credit(OWNER,'arda',500,1,'resource-topup-0001')
    stats=store.owner_stats(OWNER,'arda')
    assert stats['volume_credit_bytes']==1500 and stats['unlimited_credit']==3
    store.register_client(RESELLER,'a','arda',quota_bytes=1200)
    stats=store.owner_stats(OWNER,'arda')
    assert stats['volume_credit_remaining_bytes']==300
    with pytest.raises(PolicyError,match='volume credit'):
        store.register_client(RESELLER,'b','arda',quota_bytes=400)
    store.delete_client(RESELLER,'a')
    assert store.owner_stats(OWNER,'arda')['volume_credit_remaining_bytes']==1500


def test_resource_credit_permissions(store):
    with pytest.raises(PermissionDenied):
        store.adjust_resource_credit(RESELLER,'arda',1,0,'reseller-credit-0001')


def test_legacy_paid_client_creation_is_rejected(store):
    with pytest.raises(PolicyError,match='Monetary reseller credit is retired'):
        store.register_client(RESELLER,'a','arda',price=100,order_id='sale')
    assert store.owner_stats(OWNER,'arda')['client_count']==0


def test_overflow_no_partial_mutation(store):
    store.register_client(OWNER,'a','arda');store.record_usage('one','a',MAX_INT,0)
    with pytest.raises(PolicyError):store.record_usage('two','a',1,0)
    assert store.owner_stats(OWNER,'arda')['used_bytes']==MAX_INT

def test_backup_persistence(store,tmp_path):
    store.register_client(OWNER,'a','arda');store.record_usage('one','a',30,10);store.backup(tmp_path/'backup.db')
    other=Store(tmp_path/'backup.db')
    try:assert other.owner_stats(OWNER,'arda')['used_bytes']==40
    finally:other.close()
    with pytest.raises(PolicyError):store.backup(tmp_path/'backup.db')

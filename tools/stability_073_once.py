#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    p.write_text(s.replace(old,new,1))

# ---------------------------------------------------------------------------
# 1) Bulk adjustments: never map a limited quota/expiry to zero because zero
# means unlimited/no-expiry in DARK semantics.
# ---------------------------------------------------------------------------
replace_once('backend/server.py',
"""                if body.add_bytes:
                    current=int(c.get('totalGB',0))
                    if current==0:raise PolicyError('Unlimited quota is unchanged by add-bytes; set a quota explicitly per client')
                    patch['totalGB']=max(0,min((1<<63)-1,current+body.add_bytes))
                if body.add_days:
                    current=int(c.get('expiryTime',0));base=current if current>now_ms else now_ms
                    patch['expiryTime']=max(0,base+body.add_days*86400000)
""",
"""                if body.add_bytes:
                    current=int(c.get('totalGB',0))
                    if current==0:raise PolicyError('Unlimited quota is unchanged by add-bytes; set a quota explicitly per client')
                    adjusted=current+body.add_bytes
                    if adjusted<=0:raise PolicyError('Bulk quota adjustment would become 0, but 0 means unlimited; choose a smaller reduction')
                    patch['totalGB']=min((1<<63)-1,adjusted)
                if body.add_days:
                    current=int(c.get('expiryTime',0));base=current if current>now_ms else now_ms
                    # expiryTime=0 means NO EXPIRY. A negative bulk adjustment must
                    # never accidentally turn an expiring client into unlimited.
                    patch['expiryTime']=max(1,base+body.add_days*86400000)
""")

# ---------------------------------------------------------------------------
# 2) Subscription headers: Starlette/HTTP headers are latin-1. Preserve ASCII
# titles as-is; encode Unicode titles explicitly and reject control chars in
# operator-controlled header metadata.
# ---------------------------------------------------------------------------
replace_once('backend/core.py',
"""            if not isinstance(value['profile_title'],str) or not 1<=len(value['profile_title'])<=120:raise CoreError('Invalid subscription profile title')
            if not isinstance(value['announce'],str) or len(value['announce'])>2000:raise CoreError('Invalid subscription announcement')
""",
"""            if not isinstance(value['profile_title'],str) or not 1<=len(value['profile_title'])<=120:raise CoreError('Invalid subscription profile title')
            if any(ord(ch)<32 or ord(ch)==127 for ch in value['profile_title']):raise CoreError('Subscription profile title contains control characters')
            if not isinstance(value['announce'],str) or len(value['announce'])>2000:raise CoreError('Invalid subscription announcement')
            if any(ch in value['announce'] for ch in ('\\r','\\n')) and len(value['announce'].splitlines())>100:raise CoreError('Subscription announcement has too many lines')
""")
replace_once('backend/core.py',
"""                if val:
                    u=urlsplit(val)
                    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Subscription URL must be http/https without credentials')
                    try:val.encode('ascii')
                    except UnicodeEncodeError:raise CoreError('Subscription URL must be ASCII/punycode')
""",
"""                if val:
                    if any(ord(ch)<32 or ord(ch)==127 for ch in val):raise CoreError('Subscription URL contains control characters')
                    u=urlsplit(val)
                    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Subscription URL must be http/https without credentials')
                    try:val.encode('ascii')
                    except UnicodeEncodeError:raise CoreError('Subscription URL must be ASCII/punycode')
""")
replace_once('backend/core.py',
"""    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:
        if fmt not in ('raw','base64','json','clash'):raise CoreError('Unsupported subscription format',status=400)
""",
"""    @staticmethod
    def _header_text(value:str)->str:
        value=' '.join(str(value or '').splitlines()).strip()
        if any(ord(ch)<32 or ord(ch)==127 for ch in value):raise CoreError('Unsafe subscription header value')
        try:value.encode('ascii');return value
        except UnicodeEncodeError:return 'base64:'+base64.b64encode(value.encode('utf-8')).decode('ascii')

    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:
        if fmt not in ('raw','base64','json','clash'):raise CoreError('Unsupported subscription format',status=400)
""")
replace_once('backend/core.py',
"""        c=json.loads(r['body']);headers={'Content-Type':content_type,'profile-update-interval':str(settings.get('profile_update_interval_hours',6)),
            'profile-title':settings.get('profile_title','DARK XRAY'),
            'subscription-userinfo':f\"upload={r['up']}; download={r['down']}; total={c.get('totalGB',0)}; expire={max(0,c.get('expiryTime',0)//1000)}\"}
""",
"""        c=json.loads(r['body']);headers={'Content-Type':content_type,'profile-update-interval':str(settings.get('profile_update_interval_hours',6)),
            'profile-title':self._header_text(settings.get('profile_title','DARK XRAY')),
            'subscription-userinfo':f\"upload={r['up']}; download={r['down']}; total={c.get('totalGB',0)}; expire={max(0,c.get('expiryTime',0)//1000)}\"}
""")

# ---------------------------------------------------------------------------
# 3) Core lifecycle observability + auto recovery accounting.
# ---------------------------------------------------------------------------
replace_once('backend/core.py',
"""        self.version='';self.last_error='';self.stats_error='';self.applied_hash=''
        self.wants_running=config.core_autostart;self.last_samples:dict[str,tuple[int,int]]={}
        self.last_stats=0.;self.last_apply=0.;self.last_start=0.
""",
"""        self.version='';self.last_error='';self.stats_error='';self.applied_hash=''
        self.wants_running=config.core_autostart;self.last_samples:dict[str,tuple[int,int]]={}
        self.last_stats=0.;self.last_apply=0.;self.last_start=0.
        self.last_exit_code=None;self.last_exit_at=0.;self.automatic_recoveries=0;self._observed_exit_pid=None
""")
replace_once('backend/core.py',
"""    @property
    def running(self):return bool(self.process is not None and self.process.poll() is None)
""",
"""    @property
    def running(self):
        p=self.process
        if p is None:return False
        code=p.poll()
        if code is None:return True
        if self._observed_exit_pid!=p.pid:
            self._observed_exit_pid=p.pid;self.last_exit_code=code;self.last_exit_at=time.time()
        return False
""")
replace_once('backend/core.py',
"""    def flush(self):
        self.read_ip_log()
        if not self.wants_running:return
        if not self.running and time.time()-self.last_start<5:return
        try:self.apply(start=True)
        except Exception as e:self.last_error=str(e)[:1500]
""",
"""    def flush(self):
        self.read_ip_log()
        if not self.wants_running:return
        crashed=self.process is not None and not self.running
        if not self.running and time.time()-self.last_start<5:return
        try:
            self.apply(start=True)
            if crashed and self.running:self.automatic_recoveries+=1
        except Exception as e:self.last_error=str(e)[:1500]
""")
replace_once('backend/core.py',
"""        return {'running':self.running,'pid':self.process.pid if self.running else None,'core_binary_present':Path(self.config.xray_binary).is_file(),
                'version':self.version,'dirty':dirty,'state':'running' if self.running else 'stopped','last_error':self.last_error,
                'statistics_error':self.stats_error,'applied_hash':self.applied_hash,'last_apply':self.last_apply,
                'independent':True,'restart_disconnects_existing_sessions':True}
""",
"""        running=self.running
        return {'running':running,'pid':self.process.pid if running else None,'core_binary_present':Path(self.config.xray_binary).is_file(),
                'version':self.version,'dirty':dirty,'state':'running' if running else 'stopped','last_error':self.last_error,
                'statistics_error':self.stats_error,'applied_hash':self.applied_hash,'last_apply':self.last_apply,
                'desired_running':bool(self.wants_running),'automatic_recoveries':self.automatic_recoveries,
                'last_exit_code':self.last_exit_code,'last_exit_at':self.last_exit_at,
                'independent':True,'restart_disconnects_existing_sessions':True}
""")

# Best-effort final traffic snapshot retries before stopping the owned child.
replace_once('backend/core.py',
"""    def close(self):
        with self.lock:
            try:self.collect_stats(force=True)
            finally:self._stop_child()
""",
"""    def close(self):
        with self.lock:
            if self.running:
                last=None
                for _ in range(3):
                    try:self.collect_stats(force=True,strict=True);last=None;break
                    except CoreError as ex:last=ex;time.sleep(.1)
                if last:self.stats_error=('Final traffic snapshot failed before core shutdown: '+str(last))[:500]
            self._stop_child()
""")

# ---------------------------------------------------------------------------
# 4) Client UI: manual disable is Disabled, quota/expiry/owner blockers remain Blocked.
# ---------------------------------------------------------------------------
replace_once('web/clients-v2.js',
"""function statusOf(r){if(r.block_reasons?.length)return 'blocked';if(r.client?.enable===false||r.observed_enable===false)return 'disabled';return 'active';}
""",
"""function statusOf(r){const reasons=r.block_reasons||[];if(reasons.includes('client_manual'))return 'disabled';if(reasons.length)return 'blocked';if(r.client?.enable===false||r.observed_enable===false)return 'disabled';return 'active';}
""")

# Xray V2 runtime card: expose recovery state without clutter.
replace_once('web/xray-v2.js',
"""<div class=\"xv2-kv\"><div><span>${L('State','وضعیت')}</span><b>${xesc(r.state)}</b></div><div><span>PID</span><b class=\"mono\">${xesc(r.pid||'—')}</b></div><div><span>${L('Version','نسخه')}</span><b class=\"mono\">${xesc(r.version||'—')}</b></div><div><span>${L('Config dirty','تغییرات اعمال‌نشده')}</span><b>${r.dirty?L('Yes','بله'):L('No','خیر')}</b></div></div>""",
"""<div class=\"xv2-kv\"><div><span>${L('State','وضعیت')}</span><b>${xesc(r.state)}</b></div><div><span>PID</span><b class=\"mono\">${xesc(r.pid||'—')}</b></div><div><span>${L('Version','نسخه')}</span><b class=\"mono\">${xesc(r.version||'—')}</b></div><div><span>${L('Config dirty','تغییرات اعمال‌نشده')}</span><b>${r.dirty?L('Yes','بله'):L('No','خیر')}</b></div><div><span>${L('Desired state','وضعیت مطلوب')}</span><b>${r.desired_running?L('Running','روشن'):L('Stopped','خاموش')}</b></div><div><span>${L('Auto recoveries','بازیابی خودکار')}</span><b>${xesc(r.automatic_recoveries||0)}</b></div></div>""")

# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
Path('tests/test_accounting_stability.py').write_text(r'''import base64,time
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
    assert d['client']['expiryTime']==1
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
''')

# Supervisor regression additions.
p=Path('tests/test_supervisor.py');s=p.read_text()
s += r'''

def test_unexpected_owned_core_exit_is_auto_recovered(engine):
    engine.command('start');old_pid=engine.process.pid
    engine.process.kill();engine.process.wait(timeout=3)
    engine.last_start=0
    engine.flush()
    assert engine.running and engine.process.pid!=old_pid
    state=engine.runtime_state()
    assert state['automatic_recoveries']==1
    assert state['last_exit_code'] is not None
    assert state['desired_running'] is True


def test_persisted_core_counters_continue_across_owned_restart(engine):
    engine.create({'email':'test','id':'e02b3ba0-a9b8-4d0c-8bd1-93c6eae6afda','enable':True},[])
    engine.command('start')
    (engine.runtime/'stats-fixture.json').write_text('{"stat":[{"name":"user>>>test>>>traffic>>>uplink","value":"100"}]}')
    engine.collect_stats(force=True)
    assert engine.clients()[0]['traffic']['up']==100
    engine.command('restart')
    (engine.runtime/'stats-fixture.json').write_text('{"stat":[{"name":"user>>>test>>>traffic>>>uplink","value":"20"}]}')
    engine.collect_stats(force=True)
    assert engine.clients()[0]['traffic']['up']==120
'''
p.write_text(s)

# Ensure suite runs the new file.
replace_once('tests/run-tests.sh',
"""python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml
""",
"""python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml
python -m pytest tests/test_accounting_stability.py -q --junitxml=qa/junit/accounting-stability.xml
""")

Path('VERSION').write_text('0.7.3-standalone-lab\n')
print('0.7.3 stability patch applied')

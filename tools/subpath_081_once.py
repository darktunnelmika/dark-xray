#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:160]!r}')
    p.write_text(s.replace(old,new,1))

# ---------------------------------------------------------------------------
# Core subscription settings: configurable public path with panel overlap guard.
# ---------------------------------------------------------------------------
once('backend/core.py',
"""                  'subscription':{'enabled':True,'default_format':'base64','auto_detect':True,'profile_update_interval_hours':6,
                                  'remark_template':'{remark} | {email}','support_url':'','profile_title':'DARK XRAY',
                                  'profile_url':'','announce':''},
""",
"""                  'subscription':{'enabled':True,'default_format':'base64','auto_detect':True,'profile_update_interval_hours':6,
                                  'remark_template':'{remark} | {email}','support_url':'','profile_title':'DARK XRAY',
                                  'profile_url':'','announce':'','path':'/sub'},
""")
once('backend/core.py',
"""            first_segment=panel_path.strip('/').split('/',1)[0].lower() if panel_path!='/' else ''
            if first_segment in {'api','assets','sub','node','health'}:raise CoreError('Panel URI path conflicts with a reserved DARK endpoint')
            value['panel_path']=panel_path
""",
"""            first_segment=panel_path.strip('/').split('/',1)[0].lower() if panel_path!='/' else ''
            if first_segment in {'api','assets','sub','node','health'}:raise CoreError('Panel URI path conflicts with a reserved DARK endpoint')
            sub_path=str(self.section('subscription').get('path','/sub'))
            if panel_path!='/' and (panel_path==sub_path or panel_path.startswith(sub_path+'/') or sub_path.startswith(panel_path+'/')):
                raise CoreError('Panel URI path overlaps the subscription path')
            value['panel_path']=panel_path
""")
once('backend/core.py',
"""        if name=='subscription':
            allowed={'enabled','default_format','auto_detect','profile_update_interval_hours','remark_template','support_url','profile_title','profile_url','announce'}
""",
"""        if name=='subscription':
            allowed={'enabled','default_format','auto_detect','profile_update_interval_hours','remark_template','support_url','profile_title','profile_url','announce','path'}
""")
once('backend/core.py',
"""            if type(value['enabled']) is not bool or type(value['auto_detect']) is not bool:raise CoreError('Invalid subscription toggles')
            if value['default_format'] not in ('raw','base64','json','clash'):raise CoreError('Unsupported default subscription format')
""",
"""            if type(value['enabled']) is not bool or type(value['auto_detect']) is not bool:raise CoreError('Invalid subscription toggles')
            if value['default_format'] not in ('raw','base64','json','clash'):raise CoreError('Unsupported default subscription format')
            sub_path=str(value.get('path','/sub')).strip()
            if sub_path!='/' and sub_path.endswith('/'):sub_path=sub_path.rstrip('/')
            if sub_path=='/' or len(sub_path)>200 or not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',sub_path):raise CoreError('Invalid subscription URI path')
            first=sub_path.strip('/').split('/',1)[0].lower()
            if first in {'api','assets','node','health'}:raise CoreError('Subscription path conflicts with a reserved DARK endpoint')
            panel_path=str(self.section('runtime').get('panel_path',self.config.panel_path))
            if panel_path!='/' and (panel_path==sub_path or panel_path.startswith(sub_path+'/') or sub_path.startswith(panel_path+'/')):raise CoreError('Subscription path overlaps the panel URI path')
            value['path']=sub_path
""")

# ---------------------------------------------------------------------------
# Manager link generation follows live subscription settings.
# ---------------------------------------------------------------------------
once('backend/manager.py',
"""                'data_plane_state':'running' if self.engine.running and not self.engine.runtime_state()['dirty'] else 'staged',
                'subscription_url':self.engine.config.public_origin+'/sub/'+meta['public_token']
                     if credentials and actor.can('clients','credentials',row['owner']) else None}
""",
"""                'data_plane_state':'running' if self.engine.running and not self.engine.runtime_state()['dirty'] else 'staged',
                'subscription_url':self.engine.config.public_origin+self.engine.section('subscription').get('path','/sub')+'/'+meta['public_token']
                     if credentials and actor.can('clients','credentials',row['owner']) else None}
""")

# ---------------------------------------------------------------------------
# Server middleware maps configured public subscription path to the fixed
# internal route. Legacy /sub becomes inaccessible when a custom path is active.
# ---------------------------------------------------------------------------
once('backend/server.py',
"""        raw_path=request.scope.get('path','/') or '/'
        stable_public=(raw_path=='/health' or raw_path.startswith('/sub/') or raw_path.startswith('/node/api/'))
        if panel_path!='/' and not stable_public:
""",
"""        raw_path=request.scope.get('path','/') or '/'
        try:subscription_path=str(engine.section('subscription').get('path','/sub'))
        except Exception:subscription_path='/sub'
        subscription_request=raw_path.startswith(subscription_path+'/')
        if subscription_path!='/sub' and raw_path.startswith('/sub/'):
            return JSONResponse({'detail':'Not Found'},404)
        stable_public=(raw_path=='/health' or subscription_request or raw_path.startswith('/node/api/'))
        if subscription_request and subscription_path!='/sub':
            request.scope['path']='/sub'+raw_path[len(subscription_path):]
        if panel_path!='/' and not stable_public:
""")

# ---------------------------------------------------------------------------
# Settings V2: field + random generator + save.
# ---------------------------------------------------------------------------
once('web/settings-v2.js',
"""${field(L('Profile update interval (hours)','بازه آپدیت پروفایل (ساعت)'),'profile_update_interval_hours',s.profile_update_interval_hours||6,'number','', 'min=\"1\" max=\"168\" required')}<div class=\"sv2-span-2\">${toggle(L('Auto detect Clash / Mihomo','تشخیص خودکار Clash / Mihomo'),'auto_detect',s.auto_detect!==false,L('Known Clash/Mihomo user agents receive Clash output automatically.','User-Agentهای شناخته‌شده Clash/Mihomo خروجی Clash می‌گیرند.'))}</div>""",
"""${field(L('Profile update interval (hours)','بازه آپدیت پروفایل (ساعت)'),'profile_update_interval_hours',s.profile_update_interval_hours||6,'number','', 'min=\"1\" max=\"168\" required')}${field(L('Subscription URI path','مسیر URI اشتراک'),'path',s.path||'/sub','text',L('Changing this invalidates previously distributed subscription URLs.','تغییر این مسیر لینک‌های اشتراک قبلی را نامعتبر می‌کند.'),'dir=\"ltr\" maxlength=\"200\" required')}<button type=\"button\" class=\"btn\" data-sv2-action=\"random-sub-path\">${ico('refresh')}${L('Generate random subscription path','ساخت مسیر تصادفی اشتراک')}</button><div class=\"sv2-span-2\">${toggle(L('Auto detect Clash / Mihomo','تشخیص خودکار Clash / Mihomo'),'auto_detect',s.auto_detect!==false,L('Known Clash/Mihomo user agents receive Clash output automatically.','User-Agentهای شناخته‌شده Clash/Mihomo خروجی Clash می‌گیرند.'))}</div>""")
once('web/settings-v2.js',
"""async function saveSubscription(form){const s={...(SV.data.subscription||{})};s.enabled=formValue(form,'enabled')==='true';s.default_format=formValue(form,'default_format');s.auto_detect=formValue(form,'auto_detect')==='true';s.profile_update_interval_hours=n(formValue(form,'profile_update_interval_hours'),6);s.remark_template=formValue(form,'remark_template');""",
"""async function saveSubscription(form){const s={...(SV.data.subscription||{})};s.enabled=formValue(form,'enabled')==='true';s.default_format=formValue(form,'default_format');s.auto_detect=formValue(form,'auto_detect')==='true';s.profile_update_interval_hours=n(formValue(form,'profile_update_interval_hours'),6);s.path=formValue(form,'path').trim()||'/sub';s.remark_template=formValue(form,'remark_template');""")
once('web/settings-v2.js',
"""if(act==='random-path'){const bytes=new Uint8Array(12);crypto.getRandomValues(bytes);const token=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');const form=el.closest('form');if(form?.elements?.panel_path)form.elements.panel_path.value='/dark-'+token;return;}if(act==='core-validate')""",
"""if(act==='random-path'){const bytes=new Uint8Array(12);crypto.getRandomValues(bytes);const token=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');const form=el.closest('form');if(form?.elements?.panel_path)form.elements.panel_path.value='/dark-'+token;return;}if(act==='random-sub-path'){const bytes=new Uint8Array(12);crypto.getRandomValues(bytes);const token=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');const form=el.closest('form');if(form?.elements?.path)form.elements.path.value='/feed-'+token;return;}if(act==='core-validate')""")

# ---------------------------------------------------------------------------
# Tests: custom path works, old route is hidden, conflicts fail closed.
# ---------------------------------------------------------------------------
Path('tests/test_subscription_path.py').write_text(r'''import pytest
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
''')

once('tests/run-tests.sh',
"""python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml
""",
"""python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml
python -m pytest tests/test_subscription_path.py -q --junitxml=qa/junit/subscription-path.xml
""")
Path('VERSION').write_text('0.8.1-standalone-lab\n')
print('0.8.1 subscription path patch applied')

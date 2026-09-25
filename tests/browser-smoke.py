#!/usr/bin/env python3
"""Strict DARK XRAY browser QA against real local HTTP + SQLite.

The browser and session are real. Xray itself is intentionally absent here; the
real-core/data-plane gate remains tools/smoke-real.py on a VPS.
"""
import json,re,socket,sys,tempfile,threading,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dark_policy import Store,Actor
from auth import Auth
from core import Config,CoreEngine
from manager import Manager
from server import make_app
import uvicorn
from playwright.sync_api import sync_playwright

OUT=ROOT/'qa';OUT.mkdir(exist_ok=True)
VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
report={'version':VERSION,'browser':'Playwright Chromium','backend':'real local HTTP + SQLite','real_xray':False,
        'checks':[],'pages':[],'status':'not_run'}


def mark(text):
    report['checks'].append(text)


def assert_language_surface(page,name):
    page.wait_for_function("()=>window.DarkI18nAudit&&typeof window.DarkI18nAudit.collectLeaks==='function'",timeout=10000)
    audit=page.evaluate("()=>({language:window.DarkI18nAudit.language,leaks:window.DarkI18nAudit.collectLeaks(document.body)})")
    assert not audit['leaks'],f"{name} contains language leaks in {audit['language']} mode: {audit['leaks'][:12]}"


def visit(page,name):
    button=page.locator(f'.nav-btn[data-page="{name}"]')
    button.wait_for(state='visible',timeout=10000)
    button.click()
    page.wait_for_function("p=>document.querySelector('.nav-btn.active')?.dataset.page===p",arg=name,timeout=10000)
    page.wait_for_function("()=>{const c=document.getElementById('content');return c&&c.getAttribute('aria-busy')!=='true'&&c.textContent.trim().length>0}",timeout=10000)
    report['pages'].append(name)

def open_guided(page,locator,stage):
    errors_before=page.locator('.toast.error').count()
    locator.click()
    page.wait_for_function("""n=>{
      const editor=document.querySelector('.xv3-editor');
      const errors=document.querySelectorAll('.toast.error');
      return !!editor || errors.length>n;
    }""",arg=errors_before,timeout=10000)
    editor=page.locator('.xv3-editor')
    if editor.count()==0:
        msg=page.locator('.toast.error').last.inner_text() if page.locator('.toast.error').count()>errors_before else 'no editor and no error toast'
        raise RuntimeError(stage+': '+msg)
    editor.wait_for(state='visible',timeout=10000)
    mark(stage+' opened')


with tempfile.TemporaryDirectory(prefix='dark-browser-082-') as d:
    tmp=Path(d);sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    origin=f'http://127.0.0.1:{port}'
    store=Store(tmp/'dark.sqlite3')
    engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='example.test',
                             xray_binary=str(tmp/'missing-core'),xray_assets=str(tmp),test_engine=True),store,tmp/'runtime')
    manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
    auth.bootstrap('qa-owner','Temporary-QA-password-082')
    manager.owner_put(Actor('qa-owner','owner',{}),'qa-owner',name='DARK QA',allowed=[])
    server=uvicorn.Server(uvicorn.Config(make_app(manager,auth,background=False),host='127.0.0.1',port=port,
                                         log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(150):
        if server.started:break
        time.sleep(.03)
    browser=None
    try:
        if not server.started:raise RuntimeError('local QA server did not start')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1440,'height':1000})
            page=context.new_page();errors=[];inbound_responses=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.on('response',lambda r:inbound_responses.append(r) if r.request.method=='POST' and r.url.rstrip('/').endswith('/api/inbounds') else None)
            page.goto(origin,wait_until='networkidle',timeout=20000)
            page.locator('#login-form [name=username]').fill('qa-owner')
            page.locator('#login-form [name=password]').fill('Temporary-QA-password-082')
            page.locator('#login-form button[type=submit]').click()
            page.wait_for_selector('.nav-btn[data-page="inbounds"]',timeout=10000)
            assert page.locator('html').get_attribute('dir')=='ltr'
            assert page.locator('body').evaluate("b=>b.classList.contains('skin-cyber-classic')")
            page.locator('.ov4-commandbar').wait_for(state='visible',timeout=10000)
            skin=page.evaluate("""()=>({
                green:getComputedStyle(document.body).getPropertyValue('--classic-green').trim(),
                activeBorder:getComputedStyle(document.querySelector('.nav-btn.active')).borderTopColor,
                overviewBg:getComputedStyle(document.querySelector('.ov4-card')).backgroundImage,
                overviewBorder:getComputedStyle(document.querySelector('.ov4-card')).borderTopColor,
                overviewRadius:getComputedStyle(document.querySelector('.ov4-card')).borderRadius,
                overviewAccent:getComputedStyle(document.querySelector('.ov4-spark polyline')).stroke,
                actionBg:getComputedStyle(document.querySelector('.ov4-action')).backgroundColor
            })""")
            assert skin['green']=='#19ff86',skin
            assert skin['overviewRadius'] in ('2px','2px 2px 2px 2px'),skin
            assert skin['overviewAccent']=='rgb(25, 255, 134)',skin
            assert '25, 255, 134' in skin['overviewBorder'],skin
            assert skin['actionBg'] in ('rgb(0, 16, 8)','rgba(0, 16, 8, 1)'),skin
            mark('real browser login, Cyber Classic shell and theme-inherited Overview V4')

            assert page.locator('.ov4-resource').count()==4
            assert page.locator('.ov4-traffic-card').count()==1
            assert page.locator('.ov4-connections').count()==1
            assert page.locator('.ov4-telemetry-strip').count()==1
            assert page.locator('.ov4-summary-item').count()==6
            page.locator('.up-dashboard-compact').wait_for(state='visible',timeout=10000)

            resource_boxes=[page.locator('.ov4-resource').nth(i).bounding_box() for i in range(4)]
            assert all(resource_boxes),resource_boxes
            assert max(abs(resource_boxes[i]['y']-resource_boxes[0]['y']) for i in range(1,4))<=3,resource_boxes
            traffic_box=page.locator('.ov4-traffic-card').bounding_box()
            connection_box=page.locator('.ov4-connections').bounding_box()
            assert traffic_box and connection_box,(traffic_box,connection_box)
            assert abs(traffic_box['y']-connection_box['y'])<=3,(traffic_box,connection_box)
            node_box=page.locator('.ov4-management-grid > .ov4-node-card').bounding_box()
            update_box=page.locator('.ov4-management-grid > .up-dashboard-shell').bounding_box()
            assert node_box and update_box,(node_box,update_box)
            assert abs(node_box['y']-update_box['y'])<=4,(node_box,update_box)
            assert page.locator('.ov4-resource-value').all_inner_texts()

            sidebar_box=page.locator('.sidebar').bounding_box()
            language_box=page.locator('.cyber-lang-switch').bounding_box()
            assert sidebar_box and language_box,(sidebar_box,language_box)
            assert language_box['x']>=sidebar_box['x']-1 and language_box['x']+language_box['width']<=sidebar_box['x']+sidebar_box['width']+1,(sidebar_box,language_box)

            report['dashboard_layout']={
                'resource_cards':4,
                'traffic_width':round(traffic_box['width'],1),
                'connections_width':round(connection_box['width'],1),
                'node_update_y_delta':round(abs(node_box['y']-update_box['y']),1)
            }
            mark('Overview V4 renders four resource cards, traffic, connections and system telemetry')
            mark('Overview V4 keeps Node Fleet and compact Update Center aligned')
            mark('desktop language switch stays inside the sidebar instead of covering dashboard content')
            page.screenshot(path=str(OUT/'browser-dashboard.png'),full_page=True)

            assert page.evaluate("localStorage.getItem('dark_lang')")=='en'
            assert page.evaluate("document.documentElement.lang")=='en'
            assert page.evaluate("document.documentElement.dir")=='ltr'
            mark('English is the default browser language and LTR normalization is active')

            # Every owner workspace must render without leaving a busy/blank content surface.
            pages=['dashboard','inbounds','clients','resellers','ipguard','finance','sync',
                   'hosts','outbounds','routing','nodes','xray','settings','account']
            for name in pages:
                visit(page,name)
                assert_language_surface(page,name)
            mark('all primary owner workspaces render in English without Persian leakage')
            assert page.locator('.nav-btn[data-page="roles"]').count()==0
            assert page.locator('.nav-btn[data-page="logs"]').count()==0
            assert page.locator('.nav-btn[data-page="audit"]').count()==0
            mark('legacy Access Control workspace is absent; representative management is unified')
            mark('Logs and Audit are absent from primary navigation')

            # Current Inbounds V3 editor -> real API -> real SQLite.
            visit(page,'inbounds')
            page.locator('[data-v3-action="new"]').click()
            form=page.locator('#iv3-editor');form.wait_for(state='visible',timeout=10000)
            assert form.locator('input[name="deployLocal"]').count()==1 and form.locator('input[name="deployLocal"]').is_checked()
            assert form.locator('input[name="deployNode"]').count()==0
            assert_language_surface(page,'inbounds editor / English')
            form.locator('[name=remark]').fill('DARK Browser QA / VLESS')
            form.locator('[name=port]').fill('19443')
            validity=form.evaluate("f=>({valid:f.checkValidity(),invalid:[...f.querySelectorAll(':invalid')].map(x=>({name:x.name,type:x.type,value:x.value,message:x.validationMessage}))})")
            if not validity['valid']:raise RuntimeError('Inbound native form invalid: '+json.dumps(validity['invalid'],ensure_ascii=False))
            try:
                built=page.evaluate("()=>DarkInboundV3.buildBody(document.querySelector('#iv3-editor'),null)")
            except Exception as ex:
                raise RuntimeError('Inbound buildBody failed before submit: '+str(ex)) from ex
            report['inbound_payload']={'protocol':built.get('protocol'),'port':built.get('port'),'network':built.get('streamSettings',{}).get('network'),'security':built.get('streamSettings',{}).get('security')}
            before_errors=len(errors);before_responses=len(inbound_responses)
            form.locator('button[type=submit]').click()
            deadline=time.monotonic()+10
            while time.monotonic()<deadline and form.count() and not inbound_responses[before_responses:]:
                if len(errors)>before_errors:break
                page.wait_for_timeout(100)
            if len(errors)>before_errors:raise RuntimeError('Inbound submit JS error: '+errors[-1])
            new_responses=inbound_responses[before_responses:]
            if not new_responses:
                raise RuntimeError('Inbound submit produced no POST; form_valid='+str(validity['valid'])+' payload='+json.dumps(report['inbound_payload']))
            save_response=new_responses[-1]
            if not save_response.ok:
                try:detail=save_response.text()
                except Exception:detail='response body unavailable'
                raise RuntimeError(f'Inbound save HTTP {save_response.status}: {detail[:1000]}')
            form.wait_for(state='detached',timeout=10000)
            saved=[i for i in engine.inbounds() if i['port']==19443 and i.get('protocol')=='vless']
            assert saved and saved[0]['remark']=='DARK Browser QA / VLESS'
            page.locator('#iv3-search').fill('DARK Browser QA')
            page.locator('#iv3-search').focus()
            before=page.locator('#iv3-search').input_value()
            page.evaluate('refresh()')
            assert page.locator('#iv3-search').input_value()==before
            assert page.evaluate("document.activeElement?.id")=='iv3-search'
            mark('Inbounds V3 save and active-editor refresh stability')
            page.screenshot(path=str(OUT/'browser-inbounds.png'),full_page=True)

            inbound_id=saved[0]['id']
            visit(page,'hosts')
            page.locator('[data-act="hv2new"]').click()
            page.locator('.hv3-editor').wait_for(state='visible',timeout=10000)
            page.locator('#dialog-form [name="inboundId"]').select_option(str(inbound_id))
            assert page.locator('#dialog-form [name="runtime"] option[value="local"]').count()==1
            page.locator('#dialog-form [name="runtime"]').select_option('local')
            page.locator('#dialog-form [name="address"]').fill('public-browser.example.test')
            page.locator('#dialog-form [name="port"]').fill('20443')
            page.locator('#dialog-form [name="remark"]').fill('BROWSER PUBLIC ENDPOINT')
            assert page.locator('#dialog-form input[name="mode"][value="direct"]').is_checked()
            assert page.locator('[data-hv3-preview]').inner_text().find('public-browser.example.test:20443')>=0
            page.locator('#submit-dialog').click()
            page.locator('.hv3-editor').wait_for(state='detached',timeout=10000)
            hosts=page.evaluate("()=>api('/api/settings/hosts').then(x=>x.value)")
            endpoint=next((x for x in hosts if x.get('remark')=='BROWSER PUBLIC ENDPOINT'),None)
            assert endpoint and endpoint['inboundId']==inbound_id
            assert endpoint['address']=='public-browser.example.test' and endpoint['port']==20443
            assert endpoint['security']=='same' and endpoint['host']=='' and endpoint['path']=='' and endpoint['runtime']=='local'
            mark('Public Endpoints V3 distinguishes Xray listener from customer-facing address and previews delivery impact')
            page.screenshot(path=str(OUT/'browser-public-endpoints-v3.png'),full_page=True)

            visit(page,'clients')
            page.locator('.clients-v4').wait_for(state='visible',timeout=10000)
            rows=page.locator('.cv4-row')
            if rows.count():
                first=rows.first
                assert first.locator('.cv4-row-actions button').count()<=2
                text=first.inner_text()
                assert 'HWID' not in text and 'More actions' not in text
            page.locator('[data-act="new"]').click()
            page.locator('.cv4-editor').wait_for(state='visible',timeout=10000)
            assert page.locator('#dialog-form [name="email"]').count()==1
            assert page.locator('#dialog-form [name="limitIp"]').count()==1
            assert page.locator('#dialog-form [name="limitHwid"]').count()==1
            assert page.locator('#dialog-form [name="tgId"]').count()==0
            assert page.locator('#dialog-form [name="id"]').count()==0
            assert page.locator('#dialog-form details.cv4-advanced').get_attribute('open') is None
            mark('Clients V4 command deck and basic-first create editor')
            page.screenshot(path=str(OUT/'browser-clients-v4.png'),full_page=True)
            page.locator('[data-act="close"]').first.click()

            delivery_client=page.evaluate("""({inboundId})=>api('/api/clients','POST',{
                owner:state.me.id,client:{email:'browser-delivery',totalGB:0,limitIp:1},
                inboundIds:[inboundId]
            })""",{'inboundId':inbound_id})
            assert delivery_client.get('email')=='browser-delivery',delivery_client
            page.evaluate("refresh()")
            page.locator('[data-act="cv4delivery"][data-id="browser-delivery"]').click()
            page.locator('.cv4-delivery').wait_for(state='visible',timeout=10000)
            assert page.locator('[data-act="cv4dformat"][data-format="clash"]').count()==1
            assert page.locator('[data-act="cv4dformat"][data-format="json"]').count()==1
            assert page.locator('.cv4d-config.primary').count()>=1
            assert 'public-browser.example.test:20443' in page.locator('.cv4d-config.primary').first.locator('code').inner_text()
            assert page.locator('.cv4d-empty').count()==1
            assert page.locator('#cv4d-qr svg').count()==1
            page.locator('[data-act="cv4dformat"][data-format="clash"]').click()
            assert 'format=clash' in page.locator('#cv4d-sub-url').inner_text()
            mark('Clients V5 Delivery Center consumes the Guided Public Endpoint and exposes subscription formats plus failover readiness')
            page.screenshot(path=str(OUT/'browser-client-delivery-v5.png'),full_page=True)
            page.locator('[data-act="close"]').first.click()

            visit(page,'outbounds')
            page.locator('.te4').wait_for(state='visible',timeout=10000)
            assert page.locator('.te4-summary').count()==1
            assert page.locator('.te4-graph').count()==1
            open_guided(page,page.locator('[data-act="te4outnew"]'),'Traffic Engine V4 new outbound editor')
            assert page.locator('#dialog-form [name="settings"]').count()==0
            assert page.locator('#dialog-form [name="stream"]').count()==0
            assert page.locator('#dialog-form [name="protocol"]').count()==1
            page.locator('#dialog-form [name="tag"]').fill('browser-proxy')
            page.locator('#dialog-form [name="protocol"]').select_option('vless')
            page.locator('#dialog-form [name="address"]').fill('edge.example.test')
            page.locator('#dialog-form [name="port"]').fill('443')
            page.locator('#dialog-form [name="id"]').fill('33333333-3333-4333-8333-333333333333')
            page.locator('#dialog-form [name="network"]').select_option('grpc')
            page.locator('#dialog-form [name="serviceName"]').fill('browser-grpc')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            outbounds=page.evaluate("()=>api('/api/settings/outbounds').then(x=>x.value)")
            browser_out=next((x for x in outbounds if x.get('tag')=='browser-proxy'),None)
            assert browser_out and browser_out['protocol']=='vless',browser_out
            assert browser_out['settings']['address']=='edge.example.test'
            assert browser_out['streamSettings']['grpcSettings']['serviceName']=='browser-grpc'

            proxy_card=page.locator('.te4-out').filter(has_text='browser-proxy')
            open_guided(page,proxy_card.locator('[data-act="te4outclone"]'),'Traffic Engine V4 clone outbound editor')
            page.locator('#dialog-form [name="tag"]').fill('browser-proxy-backup')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            page.once('dialog',lambda d:d.accept())
            page.locator('.te4-out').filter(has_text='browser-proxy').first.locator('[data-act="te4default"]').click()
            page.wait_for_function("()=>document.querySelector('.te4-out.default h3')?.textContent==='browser-proxy'",timeout=10000)
            outbounds=page.evaluate("()=>api('/api/settings/outbounds').then(x=>x.value)")
            assert outbounds[0]['tag']=='browser-proxy',outbounds
            mark('Traffic Engine V4 creates guided outbounds, clones them and explicitly controls Xray default egress')

            visit(page,'xray')
            page.locator('[data-act="xv2tab"][data-tab="dns"]').click()
            page.locator('[data-act="xv2dnsedit"]').click()
            page.locator('.xv3-editor').wait_for(state='visible',timeout=10000)
            assert page.locator('#dialog-form [name="dnsQueryStrategy"]').count()==1
            assert page.locator('#dialog-form [name="dnsServers"]').count()==1
            page.locator('#dialog-form [name="dnsQueryStrategy"]').select_option('UseIPv4')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            dns=page.evaluate("()=>api('/api/settings/dns').then(x=>x.value)")
            assert dns.get('queryStrategy')=='UseIPv4',dns
            mark('DNS Guided V3 exposes safe structured controls instead of raw JSON')

            visit(page,'routing')
            page.locator('.te5-routing-bar').wait_for(state='visible',timeout=10000)
            assert page.locator('.te5-advanced-tools').count()==1
            assert page.locator('.te4-preview').is_hidden()
            open_guided(page,page.locator('[data-act="te4rulenew"]'),'Traffic Engine V4 routing rule editor')
            assert page.locator('#dialog-form [name="targetType"]').count()==1
            assert page.locator('#dialog-form [name="ruleSourceIP"]').count()==1
            assert page.locator('#dialog-form [name="ruleUser"]').count()==1
            assert page.locator('#dialog-form [name="ruleAttrs"]').count()==1
            assert page.locator('#dialog-form [name="targetOutbound"] option[value="browser-proxy"]').count()==1
            page.locator('#dialog-form [name="ruleTag"]').fill('BROWSER-DIRECT')
            page.locator('#dialog-form [name="domain"]').fill('domain:browser.example')
            page.locator('#dialog-form [name="targetOutbound"]').select_option('browser-proxy')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            routing=page.evaluate("()=>api('/api/settings/routing').then(x=>x.value)")
            assert any(r.get('ruleTag')=='BROWSER-DIRECT' and r.get('outboundTag')=='browser-proxy' and 'domain:browser.example' in r.get('domain',[]) for r in routing.get('rules',[])),routing

            page.wait_for_function("()=>state.te4?.data?.routing?.rules?.some(r=>r.ruleTag==='BROWSER-DIRECT')",timeout=10000)
            page.locator('.te5-advanced-tools').evaluate("x=>x.open=true")
            page.locator('.te4-preview').wait_for(state='visible',timeout=10000)
            page.locator('#te4-preview-form [name="domain"]').fill('api.browser.example')
            page.locator('#te4-preview-form [name="port"]').fill('443')
            preview_errors=page.locator('.toast.error').count()
            preview_seq=page.evaluate("()=>state.te4?.preview_seq||0")
            page.locator('#te4-preview-form [data-act="te4preview"]').click()
            page.wait_for_function("""x=>(state.te4?.preview_seq||0)>x.seq||document.querySelectorAll('.toast.error').length>x.errors""",arg={'seq':preview_seq,'errors':preview_errors},timeout=10000)
            if page.evaluate("()=>state.te4?.preview_seq||0")<=preview_seq:
                raise RuntimeError('Traffic Engine direct preview: '+page.locator('.toast.error').last.inner_text())
            preview_state=page.evaluate("()=>state.te4.preview")
            assert preview_state['result']=='matched' and preview_state['selected_outbound']=='browser-proxy',preview_state
            preview_text=page.locator('#te4-preview-result').inner_text()
            assert 'RULE #1' in preview_text and 'OUT browser-proxy' in preview_text,preview_text
            mark('Traffic Engine V4 previews literal routing decisions without sending traffic')

            open_guided(page,page.locator('[data-act="te4routesettings"]'),'Traffic Engine V4 routing settings editor')
            assert page.locator('#dialog-form [name="routeDomainStrategy"] option[value="IPIfNonMatch"]').count()==1
            page.locator('#dialog-form [name="routeDomainStrategy"]').select_option('IPIfNonMatch')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            routing=page.evaluate("()=>api('/api/settings/routing').then(x=>x.value)")
            assert routing.get('domainStrategy')=='IPIfNonMatch',routing
            assert any(r.get('outboundTag')=='browser-proxy' for r in routing.get('rules',[])),routing
            mark('Routing Guided V4 changes domain strategy without overwriting rules')

            open_guided(page,page.locator('[data-act="te4balnew"]'),'Traffic Engine V4 balancer editor')
            assert page.locator('#dialog-form [name="balStrategy"] option[value="leastPing"]').count()==1
            assert page.locator('#dialog-form [name="balStrategy"] option[value="leastLoad"]').count()==0
            page.locator('#dialog-form [name="balTag"]').fill('browser-bal')
            page.locator('#dialog-form input[name="selector"][value="browser-proxy"]').check()
            page.locator('#dialog-form [name="balStrategy"]').select_option('leastPing')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            routing=page.evaluate("()=>api('/api/settings/routing').then(x=>x.value)")
            bal=next((b for b in routing.get('balancers',[]) if b.get('tag')=='browser-bal'),None)
            assert bal and bal['strategy']['type']=='leastPing' and 'browser-proxy' in bal['selector'],bal
            observatory=page.evaluate("()=>api('/api/settings/observatory').then(x=>x.value)")
            assert 'browser-proxy' in observatory.get('subjectSelector',[]),observatory
            bal_card=page.locator('.te4-bal').filter(has_text='browser-bal')
            bal_text=bal_card.inner_text()
            assert 'browser-proxy' in bal_text and 'browser-proxy-backup' in bal_text,bal_text
            mark('Traffic Engine V4 makes Xray prefix-selector expansion visible for leastPing balancers')

            open_guided(page,page.locator('[data-act="te4rulenew"]'),'Traffic Engine V4 balancer-target rule editor')
            page.locator('#dialog-form [name="ruleTag"]').fill('BROWSER-BAL')
            page.locator('#dialog-form [name="domain"]').fill('domain:balance.example')
            page.locator('#dialog-form [name="targetType"]').select_option('balancer')
            page.locator('#dialog-form [name="targetBalancer"]').select_option('browser-bal')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            page.wait_for_function("()=>state.te4?.data?.routing?.rules?.some(r=>r.ruleTag==='BROWSER-BAL')&&state.te4.preview===null",timeout=10000)
            page.locator('.te5-advanced-tools').evaluate("x=>x.open=true")
            page.locator('.te4-preview').wait_for(state='visible',timeout=10000)
            page.locator('#te4-preview-form [name="domain"]').fill('www.balance.example')
            preview_errors=page.locator('.toast.error').count()
            preview_seq=page.evaluate("()=>state.te4?.preview_seq||0")
            page.locator('#te4-preview-form [data-act="te4preview"]').click()
            page.wait_for_function("""x=>(state.te4?.preview_seq||0)>x.seq||document.querySelectorAll('.toast.error').length>x.errors""",arg={'seq':preview_seq,'errors':preview_errors},timeout=10000)
            if page.evaluate("()=>state.te4?.preview_seq||0")<=preview_seq:
                raise RuntimeError('Traffic Engine balancer preview: '+page.locator('.toast.error').last.inner_text())
            preview_state=page.evaluate("()=>state.te4.preview")
            assert preview_state['target_type']=='balancer' and preview_state['target']=='browser-bal',preview_state
            bal_preview=page.locator('#te4-preview-result').inner_text()
            assert 'browser-proxy' in bal_preview and 'browser-proxy-backup' in bal_preview,bal_preview

            page.locator('.te5-advanced-tools').evaluate("x=>x.open=true")
            open_guided(page,page.locator('[data-act="te4obsedit"]'),'Traffic Engine V4 Observatory editor')
            assert page.locator('#dialog-form input[name="obsSelector"][value="browser-proxy"]').is_checked()
            page.locator('#dialog-form [name="obsInterval"]').fill('45s')
            page.locator('#submit-dialog').click()
            page.locator('.xv3-editor').wait_for(state='detached',timeout=10000)
            observatory=page.evaluate("()=>api('/api/settings/observatory').then(x=>x.value)")
            assert observatory.get('probeInterval')=='45s',observatory
            assert 'browser-proxy' in observatory.get('subjectSelector',[]),observatory
            mark('Traffic Engine V4 keeps Observatory configuration visible without fabricating live health')
            page.screenshot(path=str(OUT/'browser-traffic-engine-v4.png'),full_page=True)

            now=time.time()
            with store.transaction() as db:
                token_enc=auth.cipher.encrypt(('dkn_'+('R'*60)).encode()).decode()
                db.execute("""INSERT INTO remote_nodes(
                    id,name,origin,token_enc,enabled,created_at,updated_at,last_seen,last_latency_ms,last_error,last_health,
                    data_address,priority,failover_enabled)
                    VALUES(?,?,?,?,1,?,?,?,?,?,'{}',?,?,1)""",
                    ('browser-node','BROWSER NODE','https://browser-node-control.example.test',token_enc,
                     now,now,now,11,'','browser-node.example.test',5))
                db.execute("""INSERT INTO remote_node_inbounds(
                    node_id,local_inbound_id,remote_inbound_id,updated_at,last_sync,last_error)
                    VALUES(?,?,?,?,?,'')""",('browser-node',inbound_id,77,now,now))

            visit(page,'nodes')
            page.locator('.nv5-fleet-head').wait_for(state='visible',timeout=10000)
            page.locator('.nv5-advanced > summary').click()
            page.locator('.nv4-orchestrator').wait_for(state='visible',timeout=10000)
            orch=page.locator('.nv4-inbound').filter(has_text='DARK Browser QA / VLESS')
            assert orch.count()==1
            orch_text=orch.inner_text()
            assert 'public-browser.example.test:20443' in orch_text
            assert 'browser-node.example.test:19443' in orch_text
            assert 'IN SUBSCRIPTION' in orch_text
            assert '#77' in orch_text
            mark('Nodes V4 Orchestrator separates primary Public Endpoint port from source-inbound failover port')
            page.screenshot(path=str(OUT/'browser-nodes-v4-orchestrator.png'),full_page=True)

            page.locator('[data-act="nv2new"]').click()
            page.locator('#dialog-form [name="code"]').wait_for(state='visible',timeout=10000)
            assert page.locator('#dialog-form [name="code"]').get_attribute('placeholder').startswith('DXN1.')
            assert 'no second web panel' in page.locator('#dialog-form').inner_text().lower()
            mark('Nodes V5 Add Node uses one-paste lightweight Agent Pair Code')
            page.locator('[data-act="close"]').first.click()

            visit(page,'inbounds')
            page.locator('[data-v3-action="edit"][data-id="'+str(inbound_id)+'"]').click()
            page.locator('#iv3-editor').wait_for(state='visible',timeout=10000)
            node_target=page.locator('#iv3-editor input[name="deployNode"][value="browser-node"]')
            assert node_target.count()==1 and node_target.is_checked()
            mark('Inbound editor exposes paired Nodes as first-class deployment targets')
            page.evaluate("closeDialog()")

            visit(page,'finance')
            finance_text=page.locator('#content').inner_text()
            assert 'Owner scope' not in finance_text and 'محدوده مالک' not in finance_text
            assert 'Representative' in finance_text or 'نماینده' in finance_text
            mark('Finance workspace is representative-centric and has no owner-scope selector')
            page.screenshot(path=str(OUT/'browser-finance.png'),full_page=True)

            hwid_client=page.evaluate("""({inboundId})=>api('/api/clients','POST',{
                owner:state.me.id,client:{email:'browser-hwid-policy',totalGB:0,limitIp:1,limitHwid:1},
                inboundIds:[inboundId]
            })""",{'inboundId':inbound_id})
            hwid_access=page.evaluate("""async url=>{
                const r=await fetch(url,{headers:{'x-hwid':'browser-device-policy-001','x-device-os':'browser'}});
                return {status:r.status,text:await r.text()}
            }""",hwid_client['subscription_url'])
            assert hwid_access['status']==200,hwid_access

            visit(page,'settings')
            page.locator('[data-sv2-action="tab"][data-tab="subscription"]').click()
            page.locator('.sv3-subscription').wait_for(state='visible',timeout=10000)
            sub_text=page.locator('.sv3-subscription').inner_text()
            assert 'global local + node traffic' in sub_text.lower()
            assert 'x-hwid' in sub_text.lower()
            assert page.locator('.sv3-substat').count()==4
            stats_text=' '.join(page.locator('.sv3-substats').inner_text().split())
            assert '1' in stats_text
            page.locator('[data-sv2-segment="default_format"] [data-value="raw"]').click()
            page.locator('[name="profile_update_interval_hours"]').fill('9')
            page.locator('[name="profile_title"]').fill('DARK Browser Policy')
            page.locator('[name="remark_template"]').fill('{protocol} :: {remark}')
            page.locator('[name="announce"]').fill('Browser JSON only')
            assert 'VLESS :: TURKEY FAST' in page.locator('[data-sub-remark-preview]').inner_text()
            page.locator('.sv3-subscription button[type="submit"]').click()
            page.locator('.sv3-subscription').wait_for(state='visible',timeout=10000)

            sub_page=context.new_page()
            sub_page.goto(delivery_client['subscription_url'],wait_until='networkidle',timeout=20000)
            sub_page.locator('#dark-sub-app').wait_for(state='visible',timeout=10000)
            assert sub_page.locator('#service-status').inner_text().strip()=='ACTIVE'
            assert sub_page.locator('#profile-title').inner_text().strip()=='DARK Browser Policy'
            assert sub_page.locator('#qr-code svg').count()==1
            assert sub_page.locator('#format-tabs button').count()==4
            assert sub_page.locator('#subscription-url').inner_text().strip().endswith('?format=raw')
            assert sub_page.evaluate("document.documentElement.lang")=='en'
            sub_page.screenshot(path=str(OUT/'browser-subscription-portal.png'),full_page=True)
            sub_page.close()
            mark('Cyber Subscription Portal renders status, QR, formats and English-first responsive UI')

            raw_sub=page.evaluate("""async url=>{
                const r=await fetch(url);return {
                  status:r.status,text:await r.text(),title:r.headers.get('profile-title'),
                  interval:r.headers.get('profile-update-interval'),info:r.headers.get('subscription-userinfo')
                }
            }""",delivery_client['subscription_url'])
            assert raw_sub['status']==200 and raw_sub['text'].startswith('vless://'),raw_sub
            assert raw_sub['title']=='DARK Browser Policy'
            assert raw_sub['interval']=='9'
            assert 'upload=' in raw_sub['info'] and 'download=' in raw_sub['info']
            assert 'VLESS%20%3A%3A%20BROWSER%20PUBLIC%20ENDPOINT' in raw_sub['text']

            json_sub=page.evaluate("""async url=>{
                const r=await fetch(url+'?format=json');return {status:r.status,doc:await r.json()}
            }""",delivery_client['subscription_url'])
            assert json_sub['status']==200 and json_sub['doc']['announce']=='Browser JSON only',json_sub
            assert json_sub['doc']['title']=='DARK Browser Policy'
            mark('Subscription Policy V3 drives real format, headers, naming, announcement and HWID status')
            page.screenshot(path=str(OUT/'browser-subscription-policy-v3.png'),full_page=True)

            sec_now=time.time()
            with store.transaction() as db:
                db.execute("INSERT INTO observations(client_id,ip,node,first_seen,last_seen,granted) VALUES(?,?,?,?,?,1)",
                           ('browser-hwid-policy','8.8.8.8','local',sec_now-5,sec_now))
                db.execute("INSERT INTO remote_node_ips(node_id,client_id,ip,first_seen,last_seen,verified) VALUES(?,?,?,?,?,1)",
                           ('browser-node','browser-hwid-policy','1.1.1.1',sec_now-5,sec_now))
                db.execute("INSERT INTO remote_node_devices(node_id,client_id,digest,device_os,model,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
                           ('browser-node','browser-hwid-policy','c'*64,'android','remote-browser',sec_now-5,sec_now))
                db.execute("INSERT INTO events(kind,owner,client_id,ip,node,detail,at) VALUES(?,?,?,?,?,?,?)",
                           ('violation','qa-owner','browser-hwid-policy','1.1.1.1','browser-node','browser security-center QA',sec_now))

            visit(page,'ipguard')
            page.locator('.ip4').wait_for(state='visible',timeout=10000)
            assert page.locator('.ip4-arch-card').count()==4
            sec_text=page.locator('.ip4').inner_text()
            assert 'nftables' in sec_text.lower()
            assert 'fail2ban' not in sec_text.lower()
            sec_card=page.locator('.ip4-client').filter(has_text='browser-hwid-policy')
            assert sec_card.count()==1
            assert sec_card.locator('.ip4-count b').nth(0).inner_text().strip()=='2'
            assert sec_card.locator('.ip4-count b').nth(1).inner_text().strip()=='2'
            sec_card.locator('[data-act="ip4inspect"]').click()
            page.locator('.ip4-dialog').wait_for(state='visible',timeout=10000)
            detail_text=page.locator('.ip4-dialog').inner_text()
            assert 'LOCAL' in detail_text and 'NODE browser-node' in detail_text
            page.locator('[data-act="close"]').first.click()
            mark('Security Center V4 aggregates Local + Node IP/HWID and exposes Native nftables architecture')
            page.screenshot(path=str(OUT/'browser-security-center-v4.png'),full_page=True)

            with store.transaction() as db:
                body=json.loads(db.execute("SELECT body FROM core_clients WHERE email='browser-hwid-policy'").fetchone()[0])
                body['enable']=False
                db.execute("UPDATE core_clients SET body=? WHERE email='browser-hwid-policy'",(json.dumps(body),))
                db.execute("UPDATE managed_clients SET state='applied',op='none',error='',external_disabled=1,expected_enable=1 WHERE email='browser-hwid-policy'")
            page.evaluate("refresh()")
            page.wait_for_timeout(100)

            visit(page,'sync')
            page.locator('.sy4').wait_for(state='visible',timeout=10000)
            assert page.locator('.sy4-control-card').count()>=3,'Sync V4 control cards missing'
            runtime_text=page.locator('.sy4-control-card').nth(1).inner_text()
            assert 'STOPPED / STAGED' in runtime_text,runtime_text
            drift=page.locator('.sy4-item').filter(has_text='browser-hwid-policy')
            assert drift.count()==1,'external-disabled client missing from issues view'
            assert 'EXTERNAL DISABLE' in drift.inner_text(),drift.inner_text()
            drift.locator('[data-act="sy4control"]').click()
            page.wait_for_function("()=>state.sync?.items?.find(x=>x.email==='browser-hwid-policy')?.reason_code==='clean'",timeout=10000)
            external_flag=page.evaluate("()=>api('/api/clients/browser-hwid-policy').then(x=>x.client.enable)")
            assert external_flag is True,'external control was not restored'
            page.locator('[data-act="sy4filter"][data-view="all"]').click()
            page.wait_for_function("()=>state.sv4?.view==='all'&&document.querySelector('[data-act=sy4filter][data-view=all]')?.classList.contains('active')",timeout=10000)
            clean=page.locator('.sy4-item').filter(has_text='browser-hwid-policy')
            clean.wait_for(state='visible',timeout=10000)
            assert 'IN SYNC' in clean.inner_text(),clean.inner_text()

            with store.transaction() as db:
                db.execute("DELETE FROM core_clients WHERE email='browser-delivery'")
                db.execute("UPDATE managed_clients SET state='missing',op='none',error='CoreEngine client missing; automatic recreation refused',retry_at=0 WHERE email='browser-delivery'")
            page.evaluate("refresh()")
            page.wait_for_function("()=>state.sync?.items?.find(x=>x.email==='browser-delivery')?.reason_code==='runtime_missing'",timeout=10000)
            page.locator('[data-act="sy4filter"][data-view="issues"]').click()
            page.wait_for_function("()=>state.sv4?.view==='issues'&&document.querySelector('[data-act=sy4filter][data-view=issues]')?.classList.contains('active')",timeout=10000)
            missing=page.locator('.sy4-item').filter(has_text='browser-delivery')
            missing.wait_for(state='visible',timeout=10000)
            assert 'MISSING IN RUNTIME' in missing.inner_text(),missing.inner_text()
            missing.locator('[data-act="sy4restore"]').click()
            page.locator('#dialog-form [name="confirmation"]').wait_for(state='visible',timeout=10000)
            page.locator('#dialog-form [name="confirmation"]').fill('browser-delivery')
            page.locator('#submit-dialog').click()
            page.locator('.dialog').wait_for(state='detached',timeout=10000)
            page.wait_for_function("()=>state.sync?.items?.find(x=>x.email==='browser-delivery')?.reason_code==='clean'",timeout=10000)
            restored=page.evaluate("()=>api('/api/clients/browser-delivery')")
            assert restored['state']=='applied',restored
            assert page.locator('.sy4-item').filter(has_text='browser-delivery').count()==0,'restored client remained in issues filter'
            mark('Sync Runtime V4 restores external control and explicitly recovers a missing runtime client')
            page.screenshot(path=str(OUT/'browser-sync-runtime-v4.png'),full_page=True)

            visit(page,'settings')
            page.locator('[data-sv2-action="tab"][data-tab="operations"]').click()
            page.locator('.sv2-operations').wait_for(state='visible',timeout=10000)
            assert page.locator('.sv2-terminal').count()==1
            ops_text=page.locator('.sv2-operations').inner_text()
            assert ('Runtime logs' in ops_text or 'لاگ‌های Runtime' in ops_text)
            assert ('Operation report' in ops_text or 'گزارش عملیات' in ops_text)
            mark('Settings Operations contains runtime Logs and Audit report')
            page.screenshot(path=str(OUT/'browser-settings.png'),full_page=True)
            visit(page,'dashboard')
            assert page.locator('.nav-btn[data-page="update"]').count()==0
            page.locator('.up-dashboard-shell').wait_for(state='visible',timeout=10000)
            page.locator('.update-center').wait_for(state='visible',timeout=10000)
            assert page.locator('.up-unavailable').count()==1
            mark('first-page Update Center unavailable state is graceful when broker is absent in browser lab')

            # Language toggle must survive reload/session and flip geometry.
            switch=page.locator('.cyber-lang-switch');switch.wait_for(state='visible',timeout=10000)
            switch.click();page.wait_for_load_state('networkidle')
            page.wait_for_function("()=>document.documentElement.dir==='rtl'&&document.documentElement.lang==='fa'",timeout=10000)
            page.wait_for_selector('.nav-btn[data-page="inbounds"]',timeout=10000)
            mark('Persian/RTL switch survives reload with authenticated session')

            for name in pages:
                visit(page,name)
                assert_language_surface(page,name+' / Persian')
            mark('all primary owner workspaces render in Persian without English UI leakage')

            visit(page,'inbounds')
            page.locator('[data-v3-action="new"]').click()
            page.locator('#iv3-editor').wait_for(state='visible',timeout=10000)
            assert_language_surface(page,'inbounds editor / Persian')
            page.evaluate("closeDialog()")

            visit(page,'account')
            page.locator('[data-act="password"]').click()
            page.wait_for_function("()=>document.querySelector('#overlay')?.classList.contains('show')",timeout=10000)
            assert_language_surface(page,'account password dialog / Persian')
            page.evaluate("closeDialog()")

            page.set_viewport_size({'width':390,'height':844})
            page.evaluate("go('inbounds')")
            page.wait_for_function("()=>document.querySelector('.nav-btn.active')?.dataset.page==='inbounds'",timeout=10000)
            page.wait_for_timeout(150)
            fa_text=page.locator('#content').inner_text()
            assert re.search(r'[\u0600-\u06FF]',fa_text),'Persian page did not render Persian UI text'
            for stale in ('Sanaei','Sanayi','Mirza','سنایی','میرزا','New Inbound','Search name, tag, port','Core online'):
                assert stale not in fa_text,(stale,fa_text[:1500])
            mark('Persian workspace is localized and free of legacy product names')
            width=page.evaluate('document.documentElement.scrollWidth')
            inner=page.evaluate('window.innerWidth')
            assert width<=inner+2,(width,inner)
            page.screenshot(path=str(OUT/'browser-mobile-fa.png'),full_page=True)
            mark('390px mobile Persian page has no document horizontal overflow')

            if errors:raise RuntimeError('JavaScript page errors: '+json.dumps(errors,ensure_ascii=False))
            mark('no uncaught JavaScript runtime errors')
            report['status']='passed'
    except Exception as exc:
        report.update(status='failed',error=f'{type(exc).__name__}: {exc}'[:3000])
    finally:
        if browser:
            try:browser.close()
            except Exception:pass
        server.should_exit=True;thread.join(timeout=8)
        try:manager.close()
        except Exception:pass
        try:engine.close()
        except Exception:pass
        store.close()

(OUT/'browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if report['status']=='passed' else 1)

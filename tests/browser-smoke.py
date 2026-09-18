#!/usr/bin/env python3
"""Strict DARK XRAY browser QA against real local HTTP + SQLite.

The browser and session are real. Xray itself is intentionally absent here; the
real-core/data-plane gate remains tools/smoke-real.py on a VPS.
"""
import json,socket,sys,tempfile,threading,time
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


def visit(page,name):
    button=page.locator(f'.nav-btn[data-page="{name}"]')
    button.wait_for(state='visible',timeout=10000)
    button.click()
    page.wait_for_function("p=>document.querySelector('.nav-btn.active')?.dataset.page===p",arg=name,timeout=10000)
    page.wait_for_function("()=>{const c=document.getElementById('content');return c&&c.getAttribute('aria-busy')!=='true'&&c.textContent.trim().length>0}",timeout=10000)
    report['pages'].append(name)


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

            # Every owner workspace must render without leaving a busy/blank content surface.
            pages=['dashboard','inbounds','clients','resellers','ipguard','finance','audit','sync',
                   'hosts','outbounds','routing','nodes','xray','settings','account']
            for name in pages:visit(page,name)
            mark('all primary owner workspaces render through real navigation')
            assert page.locator('.nav-btn[data-page="roles"]').count()==0
            mark('legacy Access Control workspace is absent; representative management is unified')

            # Current Inbounds V3 editor -> real API -> real SQLite.
            visit(page,'inbounds')
            page.locator('[data-v3-action="new"]').click()
            form=page.locator('#iv3-editor');form.wait_for(state='visible',timeout=10000)
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
            assert page.locator('#dialog-form [name="tgId"]').count()==0
            assert page.locator('#dialog-form [name="id"]').count()==0
            assert page.locator('#dialog-form details.cv4-advanced').get_attribute('open') is None
            mark('Clients V4 command deck and basic-first create editor')
            page.screenshot(path=str(OUT/'browser-clients-v4.png'),full_page=True)
            page.locator('[data-act="close"]').first.click()

            visit(page,'nodes')
            page.locator('[data-act="nv2new"]').click()
            page.locator('#dialog-form .nv2-picker').wait_for(state='visible',timeout=10000)
            assert page.locator('#dialog-form [name="inboundIds"]').count()>=1
            assert page.locator('#dialog-form').get_by_text('Clone local inbound to node').count()==0
            mark('Nodes V3 Add Node exposes assigned-inbound picker instead of manual clone flow')
            page.screenshot(path=str(OUT/'browser-nodes-v3.png'),full_page=True)
            page.locator('[data-act="close"]').first.click()

            visit(page,'finance');page.screenshot(path=str(OUT/'browser-finance.png'),full_page=True)
            visit(page,'settings');page.screenshot(path=str(OUT/'browser-settings.png'),full_page=True)
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

            page.set_viewport_size({'width':390,'height':844})
            page.evaluate("go('inbounds')")
            page.wait_for_function("()=>document.querySelector('.nav-btn.active')?.dataset.page==='inbounds'",timeout=10000)
            page.wait_for_timeout(150)
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

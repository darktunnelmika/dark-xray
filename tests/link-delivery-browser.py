#!/usr/bin/env python3
import json,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from server import make_app
import uvicorn
from playwright.sync_api import sync_playwright

OUT=ROOT/'qa';OUT.mkdir(exist_ok=True)

def visit(page,name):
    b=page.locator(f'.nav-btn[data-page="{name}"]')
    b.wait_for(state='visible',timeout=10000);b.click()
    page.wait_for_function("p=>document.querySelector('.nav-btn.active')?.dataset.page===p",arg=name,timeout=10000)
    page.wait_for_function("()=>document.querySelector('#content')?.getAttribute('aria-busy')!=='true'",timeout=10000)

with tempfile.TemporaryDirectory(prefix='dark-link-center.') as d:
    tmp=Path(d);sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    origin=f'http://127.0.0.1:{port}'
    store=Store(tmp/'dark.sqlite3')
    engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='delivery.test',
                             xray_binary=str(tmp/'missing-core'),xray_assets=str(tmp),test_engine=True),store,tmp/'runtime')
    manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
    auth.bootstrap('qa-owner','Temporary-QA-Link-Password')
    manager.owner_put(Actor('qa-owner','owner',{}),'qa-owner',name='DARK QA',allowed=[])
    server=uvicorn.Server(uvicorn.Config(make_app(manager,auth,background=False),host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(150):
        if server.started:break
        time.sleep(.03)
    if not server.started:raise RuntimeError('QA server did not start')
    browser=None
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            ctx=browser.new_context(viewport={'width':1360,'height':980})
            page=ctx.new_page();errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            page.goto(origin,wait_until='networkidle',timeout=20000)
            page.locator('#login-form [name=username]').fill('qa-owner')
            page.locator('#login-form [name=password]').fill('Temporary-QA-Link-Password')
            page.locator('#login-form button[type=submit]').click()
            page.wait_for_selector('.nav-btn[data-page="clients"]',timeout=10000)
            inbound=page.evaluate("""()=>api('/api/inbounds','POST',{
              remark:'QR DELIVERY',listen:'127.0.0.1',port:19455,protocol:'vless',enable:true,tag:'qr-delivery',
              settings:{decryption:'none'},streamSettings:{network:'tcp',security:'none'},sniffing:{}
            })""")
            assert inbound.get('id'),inbound
            client=page.evaluate("""id=>api('/api/clients','POST',{
              owner:state.me.id,client:{email:'qr-delivery-user',totalGB:10737418240,limitIp:1,limitHwid:0},
              inboundIds:[id]
            })""",inbound['id'])
            assert client.get('subscription_url'),client
            page.evaluate("refresh()");visit(page,'clients')
            page.locator('[data-act="cv4delivery"][data-id="qr-delivery-user"]').click()
            page.locator('.cv4d-shell').wait_for(state='visible',timeout=10000)
            page.locator('#cv4d-qr svg').wait_for(state='visible',timeout=10000)
            qr=page.locator('#cv4d-qr');svg=qr.locator('svg');box=qr.bounding_box();assert box
            assert qr.get_attribute('data-ready')=='true'
            assert float(box['width'])>=235 and float(box['height'])>=235,box
            assert svg.get_attribute('width')=='240' and svg.get_attribute('height')=='240'
            assert page.locator('.cv4d-sub.selected').count()==1
            selected=page.locator('#cv4d-selected-payload').inner_text()
            assert '/sub/' in selected,selected
            assert page.locator('.cv4d-qr-error').count()==0
            assert page.locator('[data-act="cv4dcopyselected"]').count()==1
            assert page.locator('[data-act="cv4ddownloadpng"]').count()==1
            assert page.locator('[data-act="cv4dopenportal"]').count()>=1

            pick=page.locator('.cv4d-config.primary [data-act="cv4dqr"]').first
            pick.click();page.wait_for_timeout(100)
            assert page.locator('.cv4d-config.primary.selected').count()==1
            assert page.locator('#cv4d-selected-payload').inner_text().startswith('vless://')
            assert qr.get_attribute('data-ready')=='true'

            page.locator('.cv4d-sub [data-act="cv4dqr"]').click()
            page.locator('[data-act="cv4dformat"][data-format="clash"]').click()
            page.wait_for_timeout(100)
            assert 'format=clash' in page.locator('#cv4d-selected-payload').inner_text()
            assert qr.get_attribute('data-ready')=='true'
            assert not errors,errors
            page.screenshot(path=str(OUT/'link-delivery-center-desktop.png'),full_page=True)

            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
            mobile=page.locator('#cv4d-qr-stage').bounding_box();assert mobile
            assert mobile['width']<=350 and mobile['height']<=350,mobile
            overflow=page.evaluate("document.documentElement.scrollWidth-window.innerWidth")
            assert overflow<=1,overflow
            page.screenshot(path=str(OUT/'link-delivery-center-mobile.png'),full_page=True)
            (OUT/'link-delivery-center.json').write_text(json.dumps({
              'passed':True,'desktop_qr':{'width':box['width'],'height':box['height']},
              'mobile_qr':{'width':mobile['width'],'height':mobile['height']},
              'overflow_px':overflow,'page_errors':errors
            },indent=2),encoding='utf-8')
            print('LINK_DELIVERY_BROWSER=PASS')
            ctx.close();browser.close()
    finally:
        if browser:
            try:browser.close()
            except Exception:pass
        server.should_exit=True;thread.join(timeout=3);manager.stop.set();store.close()

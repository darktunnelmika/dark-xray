#!/usr/bin/env python3
"""Real HTTP/auth/SQLite browser acceptance; isolated data, no production routes."""
from __future__ import annotations
import argparse,json,secrets,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import uvicorn
import server as server_module
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from playwright.sync_api import sync_playwright

def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,default=ROOT,help='Read-only source of the web assets to serve')
    parser.add_argument('--report',type=Path,default=ROOT/'qa/traffic-matrix-access.json')
    args=parser.parse_args()
    assert (args.app_root/'web/traffic-matrix-access.js').is_file()
    server_module.ROOT=args.app_root.resolve()
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dark-warp-access-test-') as tmpdir:
        tmp=Path(tmpdir)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        store=Store(tmp/'dark.sqlite3')
        engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='example.test',
            xray_binary=str(tmp/'missing-xray'),xray_assets=str(tmp),test_engine=True,
            core_autostart=False),store,tmp/'runtime')
        manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
        password='QA-'+secrets.token_urlsafe(24)
        auth.bootstrap('dark',password)
        inbound=engine.save_inbound({'remark':'Dark Vpn','protocol':'vless','listen':'0.0.0.0','port':8569,
            'enable':True,'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},
            'sniffing':{'enabled':True,'destOverride':['http','tls']}})
        manager.owner_put(Actor('dark','owner',{}),'dark',name='Owner',allowed=[inbound['id']])
        app=server_module.make_app(manager,auth,background=False)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(150):
            if server.started:break
            time.sleep(.02)
        assert server.started,'test server failed to start'
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'])
                try:
                    for width in (1440,390,320):
                        for lang in ('en','fa'):
                            context=browser.new_context(viewport={'width':width,'height':1000})
                            context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(lang)+');')
                            page=context.new_page();errors=[];policy_writes=[]
                            page.on('pageerror',lambda err:errors.append(str(err)))
                            page.on('request',lambda req:policy_writes.append(req.url) if '/api/traffic-matrix' in req.url and req.method!='GET' else None)
                            page.goto(origin,wait_until='networkidle')
                            page.locator('#login-form [name=username]').fill('dark')
                            page.locator('#login-form [name=password]').fill(password)
                            page.locator('#login-form button[type=submit]').click()
                            page.locator('.nav-btn[data-page="inbounds"]').wait_for(state='attached')
                            page.wait_for_function('()=>typeof DarkTrafficMatrixAccess!=="undefined"&&DarkTrafficMatrixAccess.ready')
                            me=page.evaluate('()=>api("/api/me").then(x=>({id:x.id,role:x.role}))')
                            assert me=={'id':'dark','role':'owner'},me
                            page.wait_for_selector('.ov4-commandbar')
                            if width<=760:page.locator('.mobile-menu').click()
                            nav=page.locator('.nav-btn[data-page="trafficmatrix"]')
                            assert nav.count()==1 and nav.is_visible()
                            nav.click()
                            card=page.locator('.tm-access-card');card.wait_for(state='visible')
                            button=card.locator('.tm-access-open')
                            box=button.bounding_box();assert box and box['height']>=44 and box['width']>=100,box
                            assert box['x']>=0 and box['x']+box['width']<=width+1,box
                            assert card.locator('b').inner_text()=='Dark Vpn'
                            button.click()
                            page.locator('.traffic-matrix-dialog .tm-server').wait_for(state='visible')
                            assert page.locator('.traffic-matrix-dialog [data-act="tmwarpcreate"]').count()==1
                            page.locator('.traffic-matrix-dialog .dialog-head [data-act="close"]').click()
                            if width<=760:page.locator('.mobile-menu').click()
                            page.locator('.nav-btn[data-page="inbounds"]').click()
                            banner=page.locator('.tm-access-banner');banner.wait_for(state='visible')
                            banner.locator('[data-page="trafficmatrix"]').click()
                            page.locator('.tm-access-card').wait_for(state='visible')
                            assert not errors,errors
                            assert not policy_writes,policy_writes
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                            check={'width':width,'language':lang,'role':me['role'],'menu_visible':True,
                                'matrix_open':True,'banner_works':True,'policy_writes':0,'errors':errors}
                            checks.append(check);print(json.dumps(check),flush=True)
                            if width==390 and lang=='en':
                                args.report.parent.mkdir(parents=True,exist_ok=True)
                                page.screenshot(path=str(args.report.with_suffix('.png')),full_page=True)
                            context.close()
                finally:browser.close()
        finally:
            server.should_exit=True;thread.join(timeout=10)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'passed':True,'real_http_login':True,'isolated_database':True,
        'production_changed':False,'checks':checks},indent=2)+'\n')
    print('TRAFFIC_MATRIX_ACCESS_BROWSER_PASS',len(checks))
if __name__=='__main__':run()

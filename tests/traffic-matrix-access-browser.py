#!/usr/bin/env python3
"""Real HTTP/auth/SQLite acceptance of grouped Xray settings; no production writes."""
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
    parser.add_argument('--app-root',type=Path,default=ROOT,help='Read-only source of web assets')
    parser.add_argument('--report',type=Path,default=ROOT/'qa/traffic-matrix-access.json')
    args=parser.parse_args()
    assert (args.app_root/'web/traffic-matrix-access.js').is_file()
    server_module.ROOT=args.app_root.resolve()
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dark-xray-settings-test-') as tmpdir:
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
            'panelMeta':{'deployLocal':True,'tunnelPorts':{'local':1185}},
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
                    for width in (1440,1024,390,320):
                        for lang in ('en','fa'):
                            context=browser.new_context(viewport={'width':width,'height':1000})
                            context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(lang)+');')
                            page=context.new_page();errors=[];writes=[];failures=[]
                            page.on('pageerror',lambda err:errors.append(str(err)))
                            page.on('request',lambda req:writes.append(req.url) if '/api/' in req.url and
                                req.method not in ('GET','HEAD') and '/api/auth/' not in req.url else None)
                            page.on('response',lambda res:failures.append([res.status,res.url]) if
                                '/api/' in res.url and res.status>=400 and '/api/me' not in res.url else None)
                            page.goto(origin,wait_until='networkidle')
                            page.locator('#login-form [name=username]').fill('dark')
                            page.locator('#login-form [name=password]').fill(password)
                            page.locator('#login-form button[type=submit]').click()
                            page.locator('.nav-btn[data-page="inbounds"]').wait_for(state='attached')
                            page.wait_for_function('()=>typeof DarkTrafficMatrixAccess!=="undefined"&&DarkTrafficMatrixAccess.grouped')
                            me=page.evaluate('()=>api("/api/me").then(x=>({id:x.id,role:x.role}))')
                            assert me=={'id':'dark','role':'owner'},me
                            page.wait_for_selector('.ov4-commandbar')
                            for child in ('outbounds','routing','trafficmatrix'):
                                assert page.locator('.sidebar .nav-btn[data-page="'+child+'"]').count()==0
                            assert page.locator('.sidebar .nav-btn[data-page="xray"]').count()==1
                            if width<=760:page.locator('.mobile-menu').click()
                            page.locator('.sidebar .nav-btn[data-page="xray"]').click()
                            page.locator('[data-xray-section="xray"]').wait_for(state='visible')
                            page.locator('[data-act="xv2tab"][data-tab="dns"]').click()
                            page.locator('[data-act="xv2dnsedit"]').wait_for(state='visible')
                            for child in ('outbounds','routing','trafficmatrix'):
                                page.locator('.xray-settings-nav [data-page="'+child+'"]').click()
                                page.locator('[data-xray-section="'+child+'"]').wait_for(state='visible')
                                assert page.locator('.sidebar .nav-btn.active').get_attribute('data-page')=='xray'
                                assert page.locator('.xray-settings-nav [aria-current="page"]').get_attribute('data-page')==child
                                assert page.locator('.te4-tabs:visible').count()==0
                                for button in page.locator('.xray-settings-nav button').all():
                                    box=button.bounding_box();assert box and box['height']>=44,box
                                    assert box['x']>=0 and box['x']+box['width']<=width+1,box
                            card=page.locator('.tm-access-card');card.wait_for(state='visible')
                            button=card.locator('.tm-access-open')
                            box=button.bounding_box();assert box and box['height']>=44 and box['width']>=100,box
                            assert box['x']>=0 and box['x']+box['width']<=width+1,box
                            assert card.locator('b').inner_text()=='Dark Vpn'
                            button.click()
                            matrix=page.locator('.traffic-matrix-dialog')
                            matrix.locator('.tm-server').wait_for(state='visible')
                            assert matrix.locator('[data-act="tmwarpcreate"]').count()==1
                            assert matrix.locator('[data-act="tmprobe"][data-path="direct"]').count()==1
                            assert matrix.locator('[data-act="tmprobe"][data-path="tunnel"]').count()==1
                            assert matrix.locator('[data-act="tmprobeall"]').count()==1
                            assert matrix.locator('[data-act="tmapplyboth"]').count()==1
                            assert matrix.locator('[data-matrix-policy] option[value="adblock"]').count()==2
                            matrix.locator('.dialog-head [data-act="close"]').click()
                            # Refresh the active child without losing parent navigation.
                            page.locator('.topbar [data-act="refresh"]').click()
                            page.locator('[data-xray-section="trafficmatrix"]').wait_for(state='visible')
                            assert page.locator('.sidebar .nav-btn.active').get_attribute('data-page')=='xray'
                            if width==390 and lang=='en':
                                args.report.parent.mkdir(parents=True,exist_ok=True)
                                page.screenshot(path=str(args.report.with_suffix('.png')),full_page=True)
                            if width<=760:page.locator('.mobile-menu').click()
                            page.locator('.sidebar .nav-btn[data-page="inbounds"]').click()
                            page.locator('.inbounds-v3').wait_for(state='visible')
                            assert page.locator('.tm-access-banner').count()==0
                            assert not errors,errors
                            assert not failures,failures
                            assert not writes,writes
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                            check={'width':width,'language':lang,'role':me['role'],'single_xray_menu':True,
                                'sections_opened':['core_dns','outbounds','routing','warp_adblock'],
                                'matrix_open':True,'direct_tunnel_ping_controls':True,'writes':0,'errors':errors}
                            checks.append(check);print(json.dumps(check),flush=True)
                            context.close()
                finally:browser.close()
        finally:
            server.should_exit=True;thread.join(timeout=10)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'passed':True,'real_http_login':True,'isolated_database':True,
        'production_changed':False,'checks':checks},indent=2)+'\n')
    print('XRAY_SETTINGS_NAVIGATION_BROWSER_PASS',len(checks))
if __name__=='__main__':run()

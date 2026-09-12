#!/usr/bin/env python3
"""Real browser + local HTTP/SQLite, with no proxy core and no fake data source."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from dark_policy import Store,Actor
from auth import Auth
from core import Config,CoreEngine
from manager import Manager
from server import make_app
import uvicorn
from playwright.sync_api import sync_playwright
out=ROOT/'qa';out.mkdir(exist_ok=True)
report={'browser':'Chromium','backend':'real local HTTP + SQLite','real_xray':False,'checks':[], 'status':'not_run'}
with tempfile.TemporaryDirectory(prefix='dark-browser-06-') as d:
    tmp=Path(d);s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
    origin=f'http://127.0.0.1:{port}';store=Store(tmp/'dark.sqlite3');engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='example.test',xray_binary=str(tmp/'missing-core'),xray_assets=str(tmp)),store,tmp/'runtime')
    manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key');auth.bootstrap('qa-owner','Temporary-QA-password-06');manager.owner_put(Actor('qa-owner','owner',{}),'qa-owner',name='DARK QA',allowed=[])
    server=uvicorn.Server(uvicorn.Config(make_app(manager,auth,background=False),host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.03)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            page.goto(origin,wait_until='networkidle',timeout=15000)
            page.locator('#login-form [name=username]').fill('qa-owner');page.locator('#login-form [name=password]').fill('Temporary-QA-password-06')
            page.locator('#login-form button[type=submit]').click();page.wait_for_selector('[data-page=inbounds]',timeout=10000)
            report['checks'].append('actual login through browser with server session')
            page.screenshot(path=str(out/'Dashboard-v0.6.png'),full_page=True)
            page.locator('[data-page=inbounds]').click();page.locator('[data-act=inboundnew]').click()
            page.locator('#dialog-form [name=remark]').fill('DARK QA / VLESS');page.locator('#dialog-form [name=port]').fill('19443')
            page.locator('#submit-dialog').click();page.wait_for_selector('#overlay',state='hidden')
            assert engine.inbounds()[0]['port']==19443
            report['checks'].append('native inbound created into own DB')
            page.screenshot(path=str(out/'Inbounds-v0.6.png'),full_page=True)
            page.locator('[data-page=hosts]').click();page.locator('[data-act=hostnew]').click()
            page.locator('#dialog-form [name=address]').fill('entry.example.test');page.locator('#submit-dialog').click();page.wait_for_selector('#overlay',state='hidden')
            assert engine.section('hosts')[0]['address']=='entry.example.test'
            report['checks'].append('native host created and saved via HTTP')
            page.locator('[data-page=outbounds]').click();page.locator('[data-act=outboundnew]').click()
            page.locator('#dialog-form [name=tag]').fill('dark-remote');page.locator('#dialog-form [name=address]').fill('relay.example.test')
            page.locator('#dialog-form [name=credential]').fill('12345678-1234-4234-9234-123456789012');page.locator('#dialog-form [name=network]').select_option('grpc')
            page.locator('#dialog-form [name=path]').fill('dark-service');page.locator('#submit-dialog').click();page.wait_for_selector('#overlay',state='hidden')
            assert engine.section('outbounds')[-1]['streamSettings']['grpcSettings']['serviceName']=='dark-service'
            report['checks'].append('gRPC outbound form end-to-end mapping')
            page.locator('[data-page=routing]').click();page.locator('[data-act=routenew]').click()
            page.locator('#dialog-form [name=domain]').fill('example.test');page.locator('#dialog-form [name=destination]').select_option('dark-remote')
            page.screenshot(path=str(out/'Routing-Editor-v0.6.png'),full_page=True)
            page.locator('#submit-dialog').click();page.wait_for_selector('#overlay',state='hidden')
            assert engine.section('routing')['rules'][-1]['outboundTag']=='dark-remote'
            report['checks'].append('native routing rule stored, correct outbound')
            page.locator('[data-page=ipguard]').click();page.screenshot(path=str(out/'IPGuard-v0.6.png'),full_page=True)
            page.locator('[data-act=ipsettings]').click();page.locator('#dialog-form [name=window_seconds]').fill('180')
            page.locator('#submit-dialog').click();page.wait_for_selector('#overlay',state='hidden')
            assert engine.section('ipguard')['window_seconds']==180
            report['checks'].append('IP policy saved without manual export')
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'Mobile-v0.6.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
            report['checks'].append('mobile page has no document horizontal overflow')
            if errors:raise RuntimeError('JavaScript page errors: '+json.dumps(errors))
            report['checks'].append('no JavaScript runtime errors')
            report['status']='passed';browser.close()
    except Exception as exc:
        report.update(status='blocked' if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc) else 'failed',error=str(exc)[:2000])
    finally:
        server.should_exit=True;thread.join(timeout=8);store.close()
(out/'browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if report['status'] in ('passed','blocked') else 1)

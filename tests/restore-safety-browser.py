#!/usr/bin/env python3
"""Real HTTP/auth/SQLite browser acceptance; no production data or runtime writes.

Legacy metadata is a named deterministic fixture; the test core is absent. Real
Xray enforcement is verified separately by test_restore_real_limits.py.
"""
import argparse,json,secrets,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import uvicorn
import server as server_module
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from restore_scan import parse_userinfo
from playwright.sync_api import sync_playwright


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,default=ROOT)
    parser.add_argument('--report',type=Path,default=ROOT/'qa/restore-safety-browser.json')
    args=parser.parse_args();server_module.ROOT=args.app_root.resolve();checks=[]
    with tempfile.TemporaryDirectory(prefix='dark-restore-safety-browser-') as td:
        tmp=Path(td)
        for port in range(18000,19000):
            with socket.socket() as sock:
                try:sock.bind(('127.0.0.1',port));break
                except OSError:continue
        else:raise RuntimeError('No browser-safe test port')
        origin=f'http://127.0.0.1:{port}';store=Store(tmp/'dark.sqlite3')
        engine=CoreEngine(Config(public_origin=origin,public_address='example.test',bind_port=port,
            xray_binary=str(tmp/'absent-core'),xray_assets=str(tmp),test_engine=True,core_autostart=False),store,tmp/'runtime')
        manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key');password='QA-'+secrets.token_urlsafe(24)
        auth.bootstrap('dark',password)
        ib=engine.save_inbound({'remark':'Safety inbound','protocol':'vless','port':19443,'listen':'127.0.0.1',
            'enable':True,'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}})
        manager.owner_put(Actor('dark','owner',{}),'dark',name='Owner',allowed=[ib['id']])
        app=server_module.make_app(manager,auth,background=False);restore=app.state.dark_restore
        def scan(url):
            if '/partial-' in url:return parse_userinfo('total=0;expire=0')
            if '/expired-' in url:return parse_userinfo('upload=0;download=0;total=1000;expire=1')
            if '/exhausted-' in url:return parse_userinfo('upload=1000;download=0;total=1000;expire=0')
            return parse_userinfo('upload=0;download=0;total=100000;expire=0')
        restore._scan=scan
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(150):
            if server.started:break
            time.sleep(.02)
        assert server.started
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
                try:
                    for width in (1440,390,320):
                        for lang in ('en','fa'):
                            suffix=f'{width}-{lang}'
                            urls=[f'https://old.example/eligible-{suffix}-{i}' for i in range(50)]
                            urls += [f'https://old.example/{kind}-{suffix}' for kind in ('partial','expired','exhausted')]
                            imported=restore.import_urls(urls,[ib['id']],[],True,group_name='Safety '+suffix)
                            gid=imported['group']['id'];initial=restore.rows(gid)
                            item=next(x for x in initial if '/partial-' in x['legacy_path'])
                            identity=engine.client_detail(item['core_email'])['client']
                            context=browser.new_context(viewport={'width':width,'height':1000})
                            context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(lang)+');')
                            page=context.new_page();errors=[];writes=[]
                            page.on('pageerror',lambda ex:errors.append(str(ex)))
                            page.on('request',lambda req:writes.append(req.url) if req.method=='PUT' and '/api/dark-restore/' in req.url else None)
                            page.goto(origin,wait_until='networkidle')
                            page.locator('#login-form [name=username]').fill('dark');page.locator('#login-form [name=password]').fill(password)
                            page.locator('#login-form button[type=submit]').click();page.locator('.ov4-commandbar').wait_for(state='visible')
                            if width<=760:page.locator('.mobile-menu').click()
                            page.locator('.sidebar [data-page="darkrestore"]').click();page.locator('.drs-panel').wait_for(state='visible')
                            page.locator('[data-dr-filter-group]').select_option(gid)
                            page.wait_for_function('gid=>DarkRestoreGroups.state.group===gid',arg=gid)
                            page.locator('[data-dr-search]').fill('partial-'+suffix)
                            page.wait_for_function('()=>document.querySelectorAll(".dr-user").length===1&&document.querySelector(".drs-user-status")')
                            assert page.locator('.drs-user-status .tag').inner_text()==('Needs source review' if lang=='en' else 'نیازمند بررسی مبدا')
                            assert not writes
                            page.locator('[data-act=drsreview]').click();form=page.locator('#dialog-form')
                            assert form.locator('[name=upload]').input_value()==''
                            assert form.locator('[name=total]').input_value()==''
                            page.locator('.dialog-head [data-act=close]').click();assert not writes
                            page.locator('[data-act=drsreview]').click();form=page.locator('#dialog-form')
                            for key,value in [('upload','0'),('download','0'),('total','50000')]:form.locator('[name='+key+']').fill(value)
                            form.locator('[name=noExpiry]').check();form.locator('[name=note]').fill('Checked against the isolated source fixture')
                            form.locator('[type=submit]').click();page.wait_for_timeout(100);assert not writes
                            form.locator('[name=confirmed]').check()
                            with page.expect_response(lambda r:'/metadata' in r.url and r.request.method=='PUT') as response:form.locator('[type=submit]').click()
                            assert response.value.status==200,response.value.text()
                            if response.value.json().get('applied') is False:
                                page.locator('.dialog .notice.warning').wait_for(state='visible');page.locator('.dialog-head [data-act=close]').click()
                            page.wait_for_function('id=>DarkRestoreGroups.state.data?.items.find(x=>x.id===id)?.service_status==="eligible"',arg=item['id'])
                            page.locator('[data-act=drssuspend]').click();form=page.locator('#dialog-form');form.locator('[name=confirmed]').check()
                            with page.expect_response(lambda r:'/suspension' in r.url and r.request.method=='PUT') as suspended:form.locator('[type=submit]').click()
                            assert suspended.value.status==200
                            if suspended.value.json().get('applied') is False:
                                page.locator('.dialog .notice.warning').wait_for(state='visible');page.locator('.dialog-head [data-act=close]').click()
                            page.wait_for_function('id=>DarkRestoreGroups.state.data?.items.find(x=>x.id===id)?.service_status==="suspended"',arg=item['id'])
                            fresh=next(x for x in restore.rows(gid) if x['id']==item['id'])
                            assert fresh['dark_used']==0 and fresh['first_seen']==0 and fresh['public_token']==item['public_token']
                            current=engine.client_detail(item['core_email'])['client']
                            for key in ('id','password','email'):assert current[key]==identity[key]
                            assert store.db.execute('SELECT COUNT(*) FROM managed_clients').fetchone()[0]==0
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                            assert not errors,errors
                            if width==390 and lang=='en':
                                args.report.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(args.report.with_suffix('.png')))
                            check={'width':width,'language':lang,'group_clients':53,'review_requires_confirmation':True,
                                   'cancel_readonly':True,'suspension_saved':True,'identity_usage_delivery_preserved':True,'errors':errors}
                            checks.append(check);print(json.dumps(check),flush=True);context.close()
                finally:browser.close()
        finally:
            server.should_exit=True;thread.join(10);store.close()
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps({'passed':True,'checks':checks},indent=2)+'\n')
    print('RESTORE_SAFETY_BROWSER_PASS',len(checks))
if __name__=='__main__':run()

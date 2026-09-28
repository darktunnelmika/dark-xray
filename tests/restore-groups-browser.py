#!/usr/bin/env python3
"""Real HTTP/login/SQLite; isolated Restore imports with deterministic legacy metadata.

No production DB, credentials or routes are used. Xray and the external legacy
provider are absent; the fixture supplies only their input metadata/counters.
"""
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
    parser.add_argument('--app-root',type=Path,default=ROOT)
    parser.add_argument('--report',type=Path,default=ROOT/'qa/restore-groups-browser.json')
    args=parser.parse_args();server_module.ROOT=args.app_root.resolve();checks=[]
    with tempfile.TemporaryDirectory(prefix='dark-restore-browser-') as tmpdir:
        tmp=Path(tmpdir)
        for port in range(18000,19000):
            with socket.socket() as sock:
                try:sock.bind(('127.0.0.1',port));break
                except OSError:continue
        else:raise RuntimeError('No test port available')
        origin=f'http://127.0.0.1:{port}';store=Store(tmp/'dark.sqlite3')
        config=Config(public_origin=origin,bind_port=port,public_address='example.test',
            xray_binary=str(tmp/'missing-core'),xray_assets=str(tmp),test_engine=True,core_autostart=False)
        engine=CoreEngine(config,store,tmp/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
        password='Test-'+secrets.token_urlsafe(24);auth.bootstrap('dark',password)
        inbound=engine.save_inbound({'remark':'Restore Browser Inbound','protocol':'vless','port':19443,
            'listen':'127.0.0.1','enable':True,'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}})
        manager.owner_put(Actor('dark','owner',{}),'dark',name='Owner',allowed=[inbound['id']])
        app=server_module.make_app(manager,auth,background=False)
        app.state.dark_restore._scan=lambda url:{'status':'verified','error':'','upload':1024*1024,'download':2*1024*1024,'total':20*1024*1024,'expire':2000000000}
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
                        for language in ('en','fa'):
                            suffix=str(width)+'-'+language
                            context=browser.new_context(viewport={'width':width,'height':1000})
                            context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(language)+');')
                            page=context.new_page();errors=[]
                            page.on('pageerror',lambda ex:errors.append(str(ex)))
                            page.goto(origin,wait_until='networkidle')
                            page.locator('#login-form [name=username]').fill('dark')
                            page.locator('#login-form [name=password]').fill(password)
                            page.locator('#login-form button[type=submit]').click()
                            page.locator('.ov4-commandbar').wait_for(state='visible')
                            if width<=760:page.locator('.mobile-menu').click()
                            page.locator('.sidebar [data-page="darkrestore"]').click()
                            page.locator('.dark-restore-v2').wait_for(state='visible')
                            def import_one(name,key):
                                page.locator('[data-act="drimport"]').click()
                                form=page.locator('#dialog-form')
                                form.locator('[name=groupChoice]').select_option('__new__')
                                form.locator('[name=groupName]').fill(name)
                                form.locator('[name=urls]').fill('https://legacy.example/sub/'+key)
                                form.locator('[name=drInbound]').check()
                                with page.expect_response(lambda r:'/api/dark-restore/import' in r.url and r.request.method=='POST') as response:
                                    form.locator('[type=submit]').click()
                                result=response.value.json();assert response.value.status==200,result
                                page.wait_for_function('gid=>DarkRestoreGroups.state.data?.items?.some(x=>x.group_id===gid)',arg=result['group']['id'])
                                # Runtime deliberately absent in this fixture; import truthfully
                                # reports saved records and a pending runtime apply.
                                if result.get('applied') is False:
                                    page.locator('.dialog .notice.warning').wait_for(state='visible')
                                    page.locator('.dialog-head [data-act="close"]').click()
                                return result
                            first=import_one('Rep A '+suffix,'a-'+suffix)
                            gid_a=first['group']['id'];rid=first['items'][0]['id']
                            page.locator('.dr-user').wait_for(state='visible')
                            assert page.locator('[data-dr-dark-usage]').inner_text()==page.evaluate('bytes(0)')
                            original=app.state.dark_restore.rows(gid_a)[0]
                            with store.transaction() as db:
                                db.execute('UPDATE core_clients SET up=1024,down=2048 WHERE email=?',(original['core_email'],))
                            page.locator('.topbar [data-act=refresh]').click()
                            page.wait_for_function('()=>DarkRestoreGroups.state.data.items.some(x=>x.dark_used===3072)')
                            assert page.locator('[data-dr-dark-usage]').inner_text()==page.evaluate('bytes(3072)')
                            second=import_one('Rep B '+suffix,'b-'+suffix);gid_b=second['group']['id']
                            assert page.locator('.dr-user').count()==1
                            page.locator('[data-dr-filter-group]').select_option(gid_a)
                            page.wait_for_function('id=>DarkRestoreGroups.state.group===id',arg=gid_a)
                            page.locator('[data-dr-select="'+rid+'"]').wait_for(state='visible')
                            page.locator('[data-act=drselectall]').click()
                            page.locator('[data-act=drmove]').click()
                            page.locator('#dialog-form [name=groupChoice]').select_option(gid_b)
                            page.locator('#submit-dialog').click()
                            page.wait_for_function('id=>DarkRestoreGroups.state.group===id&&DarkRestoreGroups.state.data?.items.filter(x=>x.group_id===id).length===2',arg=gid_b)
                            current=next(x for x in app.state.dark_restore.rows() if x['id']==rid)
                            assert current['public_token']==original['public_token'] and current['core_email']==original['core_email']
                            assert current['dark_used']==3072 and current['legacy_used']==3*1024*1024
                            assert page.locator('.dr-user').count()==2
                            assert page.locator('.dr-result-head strong').inner_text()==page.evaluate('bytes(3072)')
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                            assert store.db.execute('SELECT count(*) FROM managed_clients').fetchone()[0]==0
                            assert not errors,errors
                            checks.append({'width':width,'language':language,'group_import':True,'bulk_assignment':True,'dark_only_usage':True,'native_clients_unchanged':True,'errors':errors})
                            print(json.dumps(checks[-1]),flush=True)
                            if width==390 and language=='fa':
                                args.report.parent.mkdir(parents=True,exist_ok=True)
                                page.screenshot(path=str(args.report.with_suffix('.png')),full_page=True)
                            context.close()
                finally:browser.close()
        finally:server.should_exit=True;thread.join(timeout=10)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'passed':True,'isolated_data':True,'real_login':True,'external_scan_fixture':True,'checks':checks},indent=2)+'\n')
    print('RESTORE_GROUPS_BROWSER_PASS',len(checks))
if __name__=='__main__':run()

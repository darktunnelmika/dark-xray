#!/usr/bin/env python3
"""Real HTTP + SQLite + browser; isolated Node inventory and legacy metadata fixtures."""
from __future__ import annotations
import argparse, copy, json, secrets, socket, sys, tempfile, threading, time
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
    parser.add_argument('--report',type=Path,default=ROOT/'qa/restore-targets-browser.json')
    args=parser.parse_args();server_module.ROOT=args.app_root.resolve();checks=[]
    with tempfile.TemporaryDirectory(prefix='restore-targets-browser-') as directory:
        tmp=Path(directory)
        for port in range(18000,19000):
            with socket.socket() as sock:
                try:sock.bind(('127.0.0.1',port));break
                except OSError:continue
        else:raise RuntimeError('No safe test port')
        origin=f'http://127.0.0.1:{port}'
        store=Store(tmp/'dark.sqlite3')
        engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='hub.example.test',
            xray_binary=str(tmp/'missing-core'),xray_assets=str(tmp),test_engine=True,core_autostart=False),store,tmp/'runtime')
        manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
        password='QA-'+secrets.token_urlsafe(24);auth.bootstrap('dark',password)
        iid=engine.save_inbound({'remark':'Whole Inbound','protocol':'vless','port':19443,'listen':'127.0.0.1',
            'enable':True,'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}})['id']
        manager.owner_put(Actor('dark','owner',{}),'dark',name='Owner',allowed=[iid])
        app=server_module.make_app(manager,auth,background=False);restore=app.state.dark_restore
        inventory=[{'id':n,'name':name,'data_address':n+'.example.test','enabled':True,'online':True,'last_error':'',
            'assignments':[{'local_inbound_id':iid,'remote_inbound_id':1,'deployed':True,'last_error':''}]}
            for n,name in [('am','Armenia'),('pl','Poland'),('uk','United Kingdom')]]
        app.state.nodes.list=lambda:copy.deepcopy(inventory)
        restore._scan=lambda url:{'status':'verified','error':'','upload':3000,'download':7000,'total':1000000,'expire':2000000000}
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
                    for width in (1440,1024,390,320):
                        for language in ('en','fa'):
                            suffix=str(width)+'-'+language
                            result=restore.import_urls(['https://legacy.example/sub/'+suffix+'-'+str(i) for i in range(55)],
                                [iid],[],True,group_name='Representative '+suffix,node_mode='selected')
                            gid=result['group']['id'];records=restore.rows(gid)
                            identities={r['id']:(r['public_token'],r['core_email'],engine.client_detail(r['core_email'])['client']) for r in records}
                            context=browser.new_context(viewport={'width':width,'height':1000})
                            context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(language)+');')
                            page=context.new_page();errors=[];writes=[]
                            page.on('pageerror',lambda ex:errors.append(str(ex)))
                            page.on('request',lambda req:writes.append(req.url) if req.method=='PUT' and '/mapping' in req.url else None)
                            page.goto(origin,wait_until='networkidle')
                            page.locator('#login-form [name=username]').fill('dark');page.locator('#login-form [name=password]').fill(password)
                            page.locator('#login-form button[type=submit]').click();page.locator('.ov4-commandbar').wait_for(state='visible')
                            if width<=760:page.locator('.mobile-menu').click()
                            page.locator('.sidebar [data-page="darkrestore"]').click()
                            page.locator('.dark-restore-v2').wait_for(state='visible')
                            page.locator('[data-dr-filter-group]').select_option(gid)
                            summary=page.locator('.drm-group-summary');summary.wait_for(state='visible')
                            assert page.locator('.dr-user').count()==50
                            edit=summary.locator('[data-act="drgroupmapping"]');box=edit.bounding_box()
                            assert box and box['height']>=44 and box['x']>=0 and box['x']+box['width']<=width+1,box
                            edit.click();form=page.locator('#dialog-form')
                            form.locator('[name=nodeMode]').wait_for(state='visible')
                            assert form.locator('[name=nodeMode]').input_value()=='selected'
                            assert form.locator('[name=drInbound]').is_checked()
                            assert form.locator('[name=includeLocal]').is_checked()
                            form.locator('[name=nodeMode]').select_option('all')
                            assert form.locator('.drm-target').count()==4
                            form.locator('[type=submit]').click()
                            form.locator('[name=confirmTargets]').wait_for(state='visible')
                            assert not writes
                            assert all(r['node_mode']=='selected' for r in restore.rows(gid))
                            form.locator('[name=confirmTargets]').check()
                            with page.expect_response(lambda r:r.request.method=='PUT' and '/groups/'+gid+'/mapping' in r.url) as response:
                                form.locator('[type=submit]').click()
                            saved=response.value.json();assert response.value.status==200 and saved['updated']==55,saved
                            page.locator('.drm-dialog').wait_for(state='detached')
                            assert len(writes)==1
                            assert all(r['node_mode']=='all' for r in restore.rows(gid))
                            for r in restore.rows(gid):
                                assert (r['public_token'],r['core_email'],engine.client_detail(r['core_email'])['client'])==identities[r['id']]
                                assert r['dark_used']==0
                            body,_=restore.subscription(records[0]['public_token'],'json',record_access=False)
                            assert len(json.loads(body)['links'])==4
                            # Reopening must show saved destinations and all three Nodes.
                            page.locator('.drm-group-summary [data-act="drgroupmapping"]').click()
                            form.locator('[name=nodeMode]').wait_for(state='visible')
                            assert form.locator('[name=nodeMode]').input_value()=='all'
                            form.locator('[name=nodeMode]').select_option('selected')
                            form.locator('[name=includeLocal]').uncheck()
                            form.locator('[name=drNode][value=am]').check()
                            form.locator('[type=submit]').click();form.locator('[name=confirmTargets]').wait_for(state='visible')
                            assert form.locator('.drm-target').count()==1
                            # Cancelling a preview must not apply the Node-only choice.
                            page.locator('.dialog-head [data-act=close]').click()
                            assert all(r['node_mode']=='all' for r in restore.rows(gid))
                            page.locator('[data-act=drimport]').click()
                            form.locator('[name=nodeMode]').wait_for(state='visible')
                            assert form.locator('[name=nodeMode]').input_value()=='all'
                            if width==390 and language=='en':
                                args.report.parent.mkdir(parents=True,exist_ok=True)
                                page.screenshot(path=str(args.report.with_suffix('.png')),full_page=True)
                            page.locator('.dialog-head [data-act=close]').click()
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                            assert not errors,errors
                            checks.append({'width':width,'language':language,'clients_updated':55,'visible_page':50,
                                'preview_readonly':True,'cancel_readonly':True,'saved_mode_reopened':True,'node_directs':3,
                                'credentials_and_usage_preserved':True,'errors':errors})
                            print(json.dumps(checks[-1]),flush=True);context.close()
                finally:browser.close()
        finally:
            server.should_exit=True;thread.join(timeout=10)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'passed':True,'isolated_fixture':True,'checks':checks},indent=2)+'\n')
    print('RESTORE_TARGETS_BROWSER_PASS',len(checks))
if __name__=='__main__':run()

#!/usr/bin/env python3
import socket,sys,tempfile,threading,time,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend')]
import uvicorn,server as api_server
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from update_bridge import UpdateBrokerClient
from playwright.sync_api import sync_playwright
report={'status':'running','checks':[]};checks=report['checks'];out=ROOT/'qa';out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dark-repv2-browser-') as td:
 tmp=Path(td);sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();origin=f'http://127.0.0.1:{port}'
 api_server.UpdateBrokerClient=lambda **kw:UpdateBrokerClient(path=str(tmp/'absent-update.sock'),**kw)
 store=Store(tmp/'dark.sqlite3');engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='fixture.test',xray_binary=str(tmp/'missing'),xray_assets=str(tmp),test_engine=True,core_autostart=False),store,tmp/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
 auth.bootstrap('qa-owner','Temporary-RepV2-Password123');manager.owner_put(Actor('qa-owner','owner',{}),'qa-owner',name='Rep V2 QA',allowed=[])
 inbound=engine.save_inbound({'remark':'Amsterdam','protocol':'vless','listen':'127.0.0.1','port':19443,'enable':True,'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'panelMeta':{'deployLocal':True},'sniffing':{'enabled':True,'destOverride':['http','tls']}})
 server=uvicorn.Server(uvicorn.Config(api_server.make_app(manager,auth,background=False),host='127.0.0.1',port=port,log_level='error',access_log=False));t=threading.Thread(target=server.run,daemon=True);t.start()
 for _ in range(200):
  if server.started:break
  time.sleep(.025)
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':844});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(origin,wait_until='networkidle');page.locator('#login-form [name=username]').fill('qa-owner');page.locator('#login-form [name=password]').fill('Temporary-RepV2-Password123');page.locator('#login-form button[type=submit]').click();page.wait_for_selector('.ov4-commandbar');page.evaluate("go('repplans')")
   page.locator('[data-act="repplannew"]').wait_for();assert page.locator('.repv2-stats').count()==1;assert 'Order operations' in page.locator('body').inner_text();checks.append('owner operations dashboard visible')
   page.locator('[data-act="repplannew"]').click();page.locator('#dialog-form [name=name]').fill('Browser Representative V2');page.locator('#dialog-form [name=price]').fill('890000');page.locator('#dialog-form [name=days]').select_option('90');page.locator('#dialog-form button[type=submit]').click()
   page.locator('#dialog-form [name=volume]').fill('750');page.locator('#dialog-form [name=unlimited]').fill('3');page.locator('#dialog-form [name=max_clients]').fill('125');page.locator('#dialog-form button[type=submit]').click()
   page.locator('#dialog-form input[name=repInbound]').check();page.locator('#dialog-form button[type=submit]').click();review=page.locator('#dialog-form').inner_text();assert 'Browser Representative V2' in review and '90' in review and '750' in review and '125' in review;assert store.db.execute('select count(*) from representative_plans').fetchone()[0]==0;checks.append('review precedes plan mutation')
   page.locator('#dialog-form button[type=submit]').click();page.wait_for_function("()=>document.querySelector('#overlay').style.display==='none'");row=store.db.execute('select duration_days,volume_credit_bytes,unlimited_credit,max_clients from representative_plans').fetchone();assert tuple(row)==(90,750*1024**3,3,125);checks.append('publish persists exact plan snapshot')
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');checks.append('390px no horizontal overflow');assert not errors,errors;browser.close();report['status']='passed'
 finally:
  server.should_exit=True;t.join(timeout=5);store.close();(out/'representative-marketplace-v2-browser.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
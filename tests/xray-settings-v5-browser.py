#!/usr/bin/env python3
"""Real HTTP/browser/DB flow. WARP network and runtime apply are explicit fixtures.
Never touches installed services, tunnel listeners or customer data.
"""
import json,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tests')]
import uvicorn
import server as api_server
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from update_bridge import UpdateBrokerClient
from playwright.sync_api import sync_playwright
from test_traffic_matrix_api import IB,_registered_warp
report={'status':'running','backend':'isolated real HTTP + SQLite','warp_network':'fixture','runtime_apply':'fixture','checks':[]}
checks=report['checks'];out=ROOT/'qa';out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dark-xray-v5-browser-') as td:
 tmp=Path(td);sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
 origin=f'http://127.0.0.1:{port}';store=Store(tmp/'dark.sqlite3')
 api_server.UpdateBrokerClient=lambda **kw:UpdateBrokerClient(path=str(tmp/'absent-update.sock'),**kw)
 cfg=Config(public_origin=origin,bind_port=port,public_address='fixture.example',xray_binary=str(tmp/'missing'),xray_assets=str(tmp),test_engine=True)
 engine=CoreEngine(cfg,store,tmp/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
 auth.bootstrap('qa-owner','Temporary-V5-Password123');manager.owner_put(Actor('qa-owner','owner',{}),'qa-owner',name='V5 QA',allowed=[])
 iid=engine.save_inbound(IB)['id'];calls=[];applies=[]
 original_state=engine.runtime_state
 engine.runtime_state=lambda:{**original_state(),'state':'running','dirty':False,'last_error':''}
 engine._binary=lambda:'/bin/true'
 def apply(**kw):
  applies.append(kw);return {'state':'running','dirty':False,'last_error':''}
 engine.apply=apply
 api_server.register_cloudflare_warp=lambda **kw:_registered_warp()
 def probe(binary,assets,outbounds,**kw):
  calls.append([x['settings'].get('peers',[{}])[0].get('endpoint','') for x in outbounds])
  rows=[]
  for i,tag in enumerate(kw.get('tags') or []):
   rows.append({'tag':tag,'success':i!=3,'testable':True,'warpVerified':i!=3,
                'delayMs':35+i if i!=3 else None,'lossPercent':50 if i==2 else 0,'jitterMs':1,
                'egress':{'country':'DE','colo':'FRA','warp':'on','ip':'198.51.100.20'},'error':'fixture failure' if i==3 else ''})
  return rows
 api_server.probe_outbounds=probe
 web=uvicorn.Server(uvicorn.Config(api_server.make_app(manager,auth,background=False),host='127.0.0.1',port=port,log_level='error',access_log=False))
 t=threading.Thread(target=web.run,daemon=True);t.start()
 for _ in range(200):
  if web.started:break
  time.sleep(.025)
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[]
   page.on('pageerror',lambda err:errors.append(str(err)))
   page.goto(origin,wait_until='networkidle');page.locator('#login-form [name=username]').fill('qa-owner');page.locator('#login-form [name=password]').fill('Temporary-V5-Password123');page.locator('#login-form button[type=submit]').click()
   page.wait_for_selector('.nav-btn[data-page="xray"]');page.evaluate("go('trafficmatrix')")
   page.locator('[data-x5-select="server"]').wait_for();assert page.locator('[data-x5-select="server"]').input_value()=='';assert not calls and not applies
   checks.append('page entry: server required, no scan, no route activation')
   page.locator('[data-x5-select="server"]').select_option('hub');page.locator('[data-act="x5warpregister"]').wait_for();page.locator('[data-act="x5warpregister"]').click()
   page.locator('.tm-warp-path').first.wait_for();assert page.locator('.tm-warp-path').count()>4
   assert page.locator('.warp-scan-dialog').get_by_text('Degraded',exact=True).count()>0
   assert not page.locator('[data-warp-auto-best]').is_checked();assert not page.locator('[data-warp-best-preview]').is_visible()
   assert store.db.execute('SELECT COUNT(*) FROM warp_profiles').fetchone()[0]==0
   checks.append('registration and multi-result scan remain pending; Auto Best off')
   manual=page.locator('.tm-warp-path [data-act="tmwarpuse"]').nth(1);chosen=manual.get_attribute('data-endpoint');manual.click()
   page.locator('#dialog-form [name="confirmed"]').wait_for();assert not applies
   assert store.db.execute('SELECT COUNT(*) FROM warp_profiles').fetchone()[0]==0
   checks.append('manual selection opens preview without applying')
   page.locator('#dialog-form [name="confirmed"]').check();page.locator('#dialog-form button[type="submit"]').click()
   page.locator('[data-act="x5warpscan"]').wait_for();assert engine.warp_profile('hub')['settings']['peers'][0]['endpoint']==chosen
   assert len(applies)>=1;warp_apply_count=len(applies);checks.append('confirmed Apply stores exactly selected per-server endpoint')
   page.locator('[data-act="x5warpscan"]').click();page.locator('[data-warp-auto-best]').wait_for();page.locator('[data-warp-auto-best]').check()
   assert page.locator('[data-warp-best-preview]').is_visible();assert len(applies)==warp_apply_count
   page.locator('[data-warp-best-preview]').click();page.locator('#dialog-form [name="confirmed"]').wait_for();assert len(applies)==warp_apply_count
   page.evaluate('closeDialog()');checks.append('Auto Best toggle only reveals preview; cancellation changes nothing')
   page.evaluate("go('routing')");page.locator('[data-x5-select="inbound"]').wait_for();page.locator('[data-x5-select="inbound"]').select_option(str(iid));page.locator('[data-x5-select="path"]').wait_for();page.locator('[data-x5-select="path"]').select_option('direct')
   page.locator('[data-x5-route]').wait_for();page.locator('[data-x5-route]').select_option('warp_ai');page.locator('[data-x5-adblock]').check();page.locator('[data-act="x5preview"]').click()
   page.locator('#dialog-form [name="confirmed"]').wait_for();assert store.db.execute('SELECT COUNT(*) FROM traffic_matrix').fetchone()[0]==0
   page.locator('#dialog-form [name="confirmed"]').check();page.locator('#dialog-form button[type="submit"]').click();page.wait_for_function("()=>document.querySelector('#overlay').style.display==='none'");page.locator('[data-x5-route]').wait_for()
   assert store.db.execute("SELECT policy FROM traffic_matrix WHERE scope='hub' AND access_path='direct'").fetchone()[0]=='warp_ai_adblock'
   assert store.db.execute("SELECT COUNT(*) FROM traffic_matrix WHERE access_path='tunnel'").fetchone()[0]==0
   checks.append('routing + Ad-block use explicit context and confirmed Apply; tunnel policy untouched')
   page.screenshot(path=str(out/'browser-xray-v5-desktop.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
   page.screenshot(path=str(out/'browser-xray-v5-mobile.png'),full_page=True);checks.append('390px mobile layout has no horizontal overflow')
   page.route('**/api/xray-settings/servers',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"fixture status unavailable"}'))
   page.evaluate('renderPage()');page.locator('[role="alert"]').wait_for();assert 'fixture status unavailable' in page.locator('[role="alert"]').inner_text()
   checks.append('failed status fetch shows actionable error, never blank page')
   assert not errors,errors
   browser.close();report['status']='passed';report['apply_calls']=len(applies);report['probe_batches']=len(calls)
 finally:
  web.should_exit=True;t.join(timeout=5);store.close();(out/'browser-xray-v5-results.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
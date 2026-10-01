#!/usr/bin/env python3
import base64,socket,sys,tempfile,threading,time,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend')]
import uvicorn,server as api_server
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from update_bridge import UpdateBrokerClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from playwright.sync_api import sync_playwright
report={'status':'running','checks':[]};checks=report['checks'];out=ROOT/'qa';out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dark-license-v1-browser-') as td:
 tmp=Path(td);sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();origin=f'http://127.0.0.1:{port}'
 api_server.UpdateBrokerClient=lambda **kw:UpdateBrokerClient(path=str(tmp/'absent-update.sock'),**kw)
 store=Store(tmp/'dark.sqlite3');engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='fixture.test',xray_binary=str(tmp/'missing'),xray_assets=str(tmp),test_engine=True,core_autostart=False),store,tmp/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
 auth.bootstrap('qa-owner','Temporary-LicenseV1-Password123');manager.owner_put(Actor('qa-owner','owner',{}),'qa-owner',name='License QA',allowed=[])
 server=uvicorn.Server(uvicorn.Config(api_server.make_app(manager,auth,background=False),host='127.0.0.1',port=port,log_level='error',access_log=False));t=threading.Thread(target=server.run,daemon=True);t.start()
 for _ in range(200):
  if server.started:break
  time.sleep(.025)
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':844});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(origin,wait_until='networkidle');page.locator('#login-form [name=username]').fill('qa-owner');page.locator('#login-form [name=password]').fill('Temporary-LicenseV1-Password123');page.locator('#login-form button[type=submit]').click();page.wait_for_selector('.ov4-commandbar');page.evaluate("DarkSettingsV2.open('license')")
   page.locator('[data-sv2-form="license-config"]').wait_for();body=page.locator('.sv2-content').inner_text();assert 'UNCONFIGURED' in body and 'Installation ID' in body;checks.append('existing installation starts unconfigured and writable')
   pub=base64.urlsafe_b64encode(Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode().rstrip('=')
   form=page.locator('[data-sv2-form="license-config"]');form.locator('[name=server_url]').fill('https://license.example.test');form.locator('[name=public_key]').fill(pub);form.locator('button[type=submit]').click();page.wait_for_timeout(300)
   assert page.locator('[data-sv2-form="license-config"] [name=server_url]').input_value()=='https://license.example.test';checks.append('HTTPS authority and pinned public key persist')
   assert page.locator('[data-sv2-form="license-activate"] [name=key]').count()==1;assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');checks.append('activation UI visible and 390px has no horizontal overflow');assert not errors,errors;browser.close();report['status']='passed'
 finally:
  server.should_exit=True;t.join(timeout=5);store.close();(out/'licensing-v1-browser.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
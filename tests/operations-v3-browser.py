#!/usr/bin/env python3
"""Actual HTTP + SQLite + full shipped browser shell; isolated fixture telemetry."""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend')]
import uvicorn
import server as api_server
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from update_bridge import UpdateBrokerClient
from playwright.sync_api import sync_playwright,expect

out=ROOT/'qa';out.mkdir(exist_ok=True)
report={'status':'running','telemetry':'isolated fixtures, not Production probes','cases':[],'writes':[]}
with tempfile.TemporaryDirectory(prefix='dark-operations-v3-') as td:
    tmp=Path(td)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    api_server.UpdateBrokerClient=lambda **kw:UpdateBrokerClient(path=str(tmp/'absent-update.sock'),**kw)
    store=Store(tmp/'dark.sqlite3')
    engine=CoreEngine(Config(public_origin=origin,bind_port=port,public_address='fixture.test',test_engine=True,
        xray_binary=str(tmp/'missing'),xray_assets=str(tmp),core_autostart=False),store,tmp/'runtime')
    manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
    auth.bootstrap('dark','Temporary-OpsV3-Password123')
    manager.owner_put(Actor('dark','owner',{}),'dark',name='QA',allowed=[])
    app=api_server.make_app(manager,auth,background=False)
    now=time.time()
    with store.transaction() as db:
        db.execute('INSERT INTO telegram_bots(owner,enabled,token_enc,admin_telegram_id,updated_at,last_seen,last_error) VALUES(?,?,?,?,?,?,?)',
            ('dark',0,'encrypted-fixture-not-used',1,now,now,'https://api.telegram.org/bot123456789:FAKE_SECRET_123456789012345/getMe'))
        db.execute('INSERT INTO warp_profiles(scope,outbound_json,device_id,updated_at) VALUES(?,?,?,?)',
            ('hub',json.dumps({'settings':{'secretKey':'NEVER_DISPLAY_SECRET','peers':[{'endpoint':'162.159.192.1:2408'}]}}),'fixture',now))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.025)
    assert server.started
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
            for width in (1440,1024,390,320):
                for lang in ('en','fa'):
                    context=browser.new_context(viewport={'width':width,'height':1000})
                    context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(lang)+');')
                    page=context.new_page();errors=[];requests=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('request',lambda req:requests.append((req.method,req.url)))
                    page.goto(origin,wait_until='networkidle')
                    page.locator('#login-form [name=username]').fill('dark')
                    page.locator('#login-form [name=password]').fill('Temporary-OpsV3-Password123')
                    page.locator('#login-form button[type=submit]').click()
                    panel=page.locator('[data-operations-v3]');panel.wait_for()
                    expect(panel.locator('.ov3-service')).to_have_count(4)
                    # This is the active Overview V4, not a shadowed legacy dashboard.
                    assert page.locator('.ov4 [data-operations-v3]').count()==1
                    expect(panel.locator('.ov3-service').first).to_contain_text('متوقف' if lang=='fa' else 'Stopped')
                    panel.locator('summary').click()
                    expect(panel).to_contain_text('162.159.192.1:2408')
                    expect(panel).to_contain_text('اتصال آزمایش نشده' if lang=='fa' else 'Connectivity not tested')
                    expect(panel).to_contain_text('غیرفعال' if lang=='fa' else 'Disabled')
                    assert 'FAKE_SECRET' not in panel.inner_text()
                    response=page.evaluate("()=>api('/api/operations/overview')")
                    assert response['network_probes'] is False and response['read_only'] is True
                    assert 'NEVER_DISPLAY_SECRET' not in json.dumps(response)
                    if not page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'):
                        page.screenshot(path=str(out/'operations-v3-overflow.png'),full_page=True)
                        print('OVERFLOW',width,lang,page.evaluate('()=>[...document.querySelectorAll("body *")].filter(x=>{const r=x.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+1||r.left< -1)}).slice(0,18).map(x=>({tag:x.tagName,cls:x.className,rect:{x:x.getBoundingClientRect().x,width:x.getBoundingClientRect().width},text:x.innerText?.slice(0,100)}))'))
                        raise AssertionError('horizontal overflow')
                    if width in (1440,390):page.screenshot(path=str(out/f'operations-v3-{width}-{lang}.png'),full_page=True)
                    # Failure must erase former good data; retry uses real endpoint afterwards.
                    page.route('**/api/operations/overview',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"fixture unavailable"}'))
                    panel.locator('[data-act=ov3refresh]').click()
                    expect(panel.locator('.notice')).to_be_visible()
                    expect(panel.locator('.ov3-service')).to_have_count(0)
                    page.unroute('**/api/operations/overview')
                    panel.locator('[data-act=ov3refresh]').click()
                    expect(panel.locator('.ov3-service')).to_have_count(4)
                    # A stale browser cache cannot pretend current health.
                    page.evaluate("()=>{state.ov2.overview.generated_at-=120;return renderPage();}")
                    expect(panel.locator('.notice')).to_be_visible()
                    writes=[x for x in requests if x[0] not in ('GET','HEAD','OPTIONS') and '/api/auth/' not in x[1]]
                    assert not writes,writes
                    assert not errors,errors
                    report['cases'].append({'width':width,'language':lang,'active_dashboard':True,'error_retry_stale':True,'writes':0,'overflow':False})
                    context.close()
            browser.close()
        report['status']='passed'
    finally:
        server.should_exit=True;thread.join(timeout=8);store.close()
        (out/'operations-v3-browser.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))

"""Recovery UI: real Chromium and Hub HTTP by default; fake Xray and Agent transport.

DARK_BROWSER_TEST_BRIDGE=1 is an explicit local DOM/API harness when managed
Chromium disallows loopback HTTP. CI never sets it; it requires the full panel.
Neither mode is a provider-WAN or live-VPS disaster recovery test.
"""
import json
import os
import socket
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from server import make_app
from test_hub_node_control_live import hub
from test_node_control_lifecycle import rebooted_agent
from test_node_recovery import setup_stale, NODE, TOKEN, URL
from test_node_installations import http_transport, sql_rows

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATIONS = ['acknowledgeServiceInterruption', 'acknowledgeBackupMayBeStale']


@pytest.fixture(scope='module')
def recovery_browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('DARK_TEST_CHROMIUM'),
                                     args=['--no-sandbox'])
        yield browser
        browser.close()


@pytest.fixture
def recovery_env(hub, tmp_path, monkeypatch, recovery_browser):
    reg, owner, engine = hub
    # Hold the listener for Uvicorn: avoid a free-port close/rebind race in QA.
    listener = socket.socket();listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    engine.config.public_origin = origin;engine.config.secure_cookie = False
    engine.config.panel_path = '/control'
    with rebooted_agent(tmp_path/'node') as (_, target, runtime, agent, _):
        seen = setup_stale(reg, owner, engine, target, runtime, agent, monkeypatch)
        app = make_app(owner.app.state.manager, owner.app.state.auth, background=False)
        reg = app.state.nodes
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False, ws='none'))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start();deadline = time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
        assert server.started, 'Local fixture did not start'
        context = recovery_browser.new_context(viewport={'width':1365,'height':950})
        page = context.new_page();errors = [];requests = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.on('request', lambda req: requests.append((req.method, req.url, req.post_data_json))
                if '/recovery/' in req.url else None)
        env = dict(page=page, reg=reg, owner=owner, engine=engine, target=target, runtime=runtime,
                   agent=agent, seen=seen, errors=errors, requests=requests, origin=origin, app=app,
                   bridge=os.environ.get('DARK_BROWSER_TEST_BRIDGE')=='1')
        try:
            if env['bridge']:
                with TestClient(app, base_url=origin, raise_server_exceptions=False) as local:
                    login = local.post('/control/api/auth/login', json={'username':'dark','password':'Test!OnlyPassword123'})
                    assert login.status_code==200, login.text
                    local.headers['X-Dark-CSRF'] = login.json()['csrf'];env['local'] = local
                    def request(path, method, body):
                        requests.append((method, origin+'/control'+path, body))
                        response = local.request(method, '/control'+path, **({'json':body} if body is not None else {}))
                        if env.get('lose')==path:return {'status':599,'body':{}}
                        doc = response.json()
                        if env.get('override_path')==path:doc = env['override'](doc)
                        return {'status':response.status_code,'body':doc}
                    page.expose_function('recoveryTestRequest', request)
                    yield env
            else:
                yield env
        finally:
            context.close();server.should_exit = True;thread.join(timeout=8);listener.close()
            assert not thread.is_alive(), 'Local fixture failed to stop'


def enter(env, lang='en'):
    p = env['page']
    if env['bridge']:
        me = env['local'].get('/control/api/me').json()
        p.set_content('<html><head></head><body class="skin-cyber-classic"><div id="content"></div></body></html>')
        for name in ('style.css','cyber-classic.css','node-replacement.css'):p.add_style_tag(path=str(ROOT/'web'/name))
        p.evaluate("""([me,lang,node])=>{
          Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:k=>k==='dark_lang'?lang:null}});
          Object.defineProperty(window,'sessionStorage',{configurable:true,value:{}});
          window.state={me,page:'nodes'};window.isOwner=()=>state.me?.role==='owner';
          window.e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          window.enc=encodeURIComponent;window.closeDialog=()=>{};
          window.enginePage=async()=>'<div class="nv2-actions"><button data-act="nv2edit" data-id="'+e(node)+'">Manage</button></div>';
          window.runAction=async()=>{};
          window.api=async(path,method='GET',body)=>{
            const r=await recoveryTestRequest(path,method,body??null);
            if(r.status===401)state.me=null;if(r.status>=400)throw Error(typeof r.body?.detail==='string'?r.body.detail:'API failed');return r.body;
          };
          document.body.addEventListener('click',ev=>{const el=ev.target.closest('[data-act]');if(el)void runAction(el.dataset.act,el);});
        }""", [me,lang,NODE])
        for name in ('node-replacement.js','node-recovery.js'):p.add_script_tag(path=str(ROOT/'web'/name))
        p.evaluate("async()=>{document.querySelector('#content').innerHTML=await enginePage()}")
    else:
        p.add_init_script(f"localStorage.setItem('dark_lang',{json.dumps(lang)})")
        p.goto(env['origin']+'/control/')
        p.locator('#login-form [name=username]').fill('dark')
        p.locator('#login-form [name=password]').fill('Test!OnlyPassword123')
        p.locator('#login-form button[type=submit]').click()
        p.locator('.nav-btn[data-page=nodes]').click()
    p.locator('.nv2-actions [data-act=nrec-open]').click()
    expect(p.locator('.nrec-body')).to_have_attribute('aria-busy','false')
    return p


def act(p, name, phase=None):
    b = p.locator(f'.nrec-dialog [data-nrec={name}]');expect(b).to_be_enabled();b.click()
    expect(p.locator('.nrec-body')).to_have_attribute('aria-busy','false')
    if phase:expect(p.locator(f'[data-nrec-phase={phase}]')).to_be_visible()


def agree(p):
    for key in CONFIRMATIONS:p.locator(f'.nrec-dialog [name={key}]').check()


def posts(env):return [r for r in env['requests'] if r[0]=='POST']


def override(env, suffix, transform):
    if env['bridge']:
        env['override_path'] = URL+suffix;env['override'] = transform
    else:
        def reply(route):
            response = route.fetch()
            route.fulfill(response=response, json=transform(response.json()))
        env['page'].route('**/recovery'+suffix, reply)


@pytest.mark.parametrize('lang',['en','fa'])
def test_browser_recovery_is_explicit_preserves_data_and_never_applies_or_starts(recovery_env, lang):
    env = recovery_env;p = enter(env,lang);reg = env['reg'];runtime = env['runtime']
    assert not posts(env)
    before = {t:sql_rows(reg,t) for t in ('core_clients','clients','owners','traffic_ledger','remote_node_client_usage')}
    original_pid = env['target'].process.pid;env['seen'].clear()
    act(p,'refresh','not_started');assert not env['seen'] and not posts(env)
    act(p,'review','not_started')
    expect(p.locator('[data-nrec-needed=true]')).to_be_visible()
    assert p.locator('.nrec-review td').all_text_contents()==['2','12','1','9']
    assert env['target'].process.pid==original_pid
    assert all(method=='GET' for method,_,_ in env['seen'])
    expect(p.locator('[data-nrec=stop]')).to_be_disabled()
    p.locator('[name=acknowledgeServiceInterruption]').check()
    expect(p.locator('[data-nrec=stop]')).to_be_disabled()
    assert p.locator('dialog [name=acknowledgeBackupMayBeStale]').is_checked() is False
    if lang=='fa':p.set_viewport_size({'width':390,'height':844})
    assert p.locator('.nrec-dialog').evaluate('(x)=>x.scrollWidth<=x.clientWidth+1')
    (ROOT/'qa').mkdir(exist_ok=True)
    p.screenshot(path=str(ROOT/'qa'/f'browser-recovery-review-{lang}.png'), full_page=True)
    p.locator('[name=acknowledgeBackupMayBeStale]').check()
    act(p,'stop','recovered_stopped')
    assert not env['target'].running and not reg.get(NODE)['enabled']
    assert runtime.status()['appliedRevision']==9
    assert reg.desired_state(NODE)['pending'] and reg.desired_state(NODE)['revision']==10
    assert {t:sql_rows(reg,t) for t in before}==before
    assert [path for method,path,_ in env['seen'] if method=='POST']==['/node/api/v1/recovery/stop']
    assert p.locator('[data-nrec=stop]').count()==0 and p.locator('[data-nrec=retry]').count()==0
    assert p.locator('[data-nrec-receipt]').is_visible()
    # A past receipt must not turn into a new Stop after a separate explicit resume.
    reg.set_enabled(NODE,True);reg.sync_desired_state(NODE,reg.desired_state(NODE));reg.remote_core(NODE,'start')
    pid = env['target'].process.pid;env['seen'].clear();count = len(posts(env))
    p.keyboard.press('Escape');p.locator('.nv2-actions [data-act=nrec-open]').click()
    expect(p.locator('[data-nrec-phase=recovered_stopped]')).to_be_visible()
    assert len(posts(env))==count and not env['seen'] and env['target'].process.pid==pid
    act(p,'review','recovered_stopped')
    expect(p.locator('[data-nrec-needed=false]')).to_be_visible()
    assert p.locator('[data-nrec=stop]').count()==0 and env['target'].process.pid==pid
    assert not env['errors'],env['errors']


def test_browser_pending_recovery_reloads_and_retries_the_same_stop(recovery_env,monkeypatch):
    env = recovery_env;p = enter(env);act(p,'review');agree(p)
    def lose(response):
        if response.request.url.path=='/node/api/v1/recovery/stop':raise OSError('Injected lost acknowledgement')
    http_transport(env['reg'],env['agent'],monkeypatch,response_hook=lose)
    act(p,'stop','pending');receipt = env['runtime'].command_status()
    assert receipt['revision']==13 and not env['target'].running
    count = len(posts(env))
    if env['bridge']:
        p.keyboard.press('Escape');enter(env)
    else:
        p.reload();p.locator('.nav-btn[data-page=nodes]').click()
        p.locator('.nv2-actions [data-act=nrec-open]').click()
    expect(p.locator('[data-nrec-phase=pending]')).to_be_visible()
    assert len(posts(env))==count
    expect(p.locator('[data-nrec=retry]')).to_be_disabled()
    http_transport(env['reg'],env['agent'],monkeypatch)
    agree(p);act(p,'retry','recovered_stopped')
    assert env['runtime'].command_status()==receipt and not env['errors']


def test_browser_lost_hub_reply_requires_get_not_automatic_resend(recovery_env):
    env = recovery_env;p = enter(env);act(p,'review');agree(p)
    if env['bridge']:env['lose'] = URL+'/stop'
    else:
        def lose(route):
            response = route.fetch();assert response.ok;route.abort('failed')
        p.route('**/recovery/stop',lose)
    act(p,'stop','unavailable')
    assert env['app'].state.node_recovery.status(NODE)['recovery_completed']
    count = len(posts(env));assert p.locator('[data-nrec=stop]').count()==0
    env['seen'].clear();act(p,'refresh','recovered_stopped')
    assert len(posts(env))==count and not env['seen'] and not env['errors']


def test_browser_refresh_clears_review_and_both_confirmations(recovery_env):
    env = recovery_env;p = enter(env);act(p,'review');agree(p)
    expect(p.locator('[data-nrec=stop]')).to_be_enabled()
    act(p,'refresh');assert p.locator('[data-nrec=stop]').count()==0
    act(p,'review');expect(p.locator('[data-nrec=stop]')).to_be_disabled()
    assert p.locator('.nrec-dialog input:checked').count()==0
    assert env['target'].running and not env['errors']


def test_browser_stale_confirmation_rejected_after_new_agent_command(recovery_env):
    env = recovery_env;p = enter(env);act(p,'review');agree(p)
    env['runtime'].ordered_command({'nodeId':NODE,'revision':20,'commandId':uuid.uuid4().hex,'action':'restart'})
    pid = env['target'].process.pid
    act(p,'stop','unavailable');assert env['target'].process.pid==pid
    assert env['reg'].get(NODE)['enabled'] and env['reg'].commands.status(NODE)['revision']==2
    act(p,'refresh');act(p,'review')
    assert p.locator('.nrec-review td').all_text_contents()==['2','20','1','9']
    expect(p.locator('[data-nrec=stop]')).to_be_disabled();assert not env['errors']


def test_browser_readonly_can_compare_but_not_repair(recovery_env):
    env = recovery_env;env['engine'].config.writes_enabled=False;p = enter(env)
    act(p,'review');expect(p.locator('[data-nrec-needed=true]')).to_be_visible()
    assert p.locator('.nrec-dialog input').count()==0
    assert p.locator('[data-nrec=stop]').count()==0 and env['target'].running
    assert all(r[1].endswith('/review') for r in posts(env)) and not env['errors']


def test_browser_escapes_diagnostic_text_and_has_no_credential_storage(recovery_env,monkeypatch):
    env = recovery_env;p = enter(env);act(p,'review');agree(p)
    original = env['app'].state.node_recovery._exchange
    def lose(binding,path,body=None):
        if path.endswith('/stop'):raise OSError('Target unavailable')
        return original(binding,path,body)
    monkeypatch.setattr(env['app'].state.node_recovery,'_exchange',lose)
    act(p,'stop','pending')
    injected = '<img src=x onerror="window.recoveryPwned=1">'
    with env['reg'].store.transaction() as db:db.execute('UPDATE remote_node_recoveries SET last_error=?',(injected,))
    act(p,'refresh','pending')
    assert p.locator('.nrec-dialog img').count()==0 and p.evaluate('window.recoveryPwned') is None
    assert injected in p.locator('.nrec-dialog').inner_text()
    assert TOKEN not in p.locator('.nrec-dialog').inner_text()
    storage = p.evaluate('JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])')
    receipt = env['app'].state.node_recovery._row(NODE)
    assert TOKEN not in storage and receipt['review_hash'] not in storage and receipt['attempt_id'] not in storage
    assert not env['errors']


@pytest.mark.parametrize('malformation',['node','phase','truthy','review_identity','unsafe_number'])
def test_browser_malformed_responses_fail_closed(recovery_env,malformation):
    env = recovery_env;p = enter(env)
    def corrupt(doc):
        doc = dict(doc)
        if malformation=='node':doc['node_id']='different-node'
        elif malformation=='phase':doc['phase']='success-but-unknown'
        elif malformation=='truthy':doc['recovery_completed']='false'
        elif malformation=='review_identity':doc['review_hash']='not-a-hash'
        else:doc['agent_command_revision']=2**60
        return doc
    reviewing = malformation in ('review_identity','unsafe_number')
    override(env,'/review' if reviewing else '/current',corrupt)
    act(p,'review' if reviewing else 'refresh','unavailable')
    assert p.locator('[data-nrec=stop]').count()==0 and p.locator('[data-nrec=retry]').count()==0
    assert env['target'].running and not env['errors']


def test_browser_duplicate_click_is_one_mutation_and_role_change_drops_late_view(recovery_env):
    env = recovery_env;p = enter(env);act(p,'review');agree(p)
    p.evaluate("()=>{const b=document.querySelector('[data-nrec=stop]');b.click();b.click();}")
    expect(p.locator('[data-nrec-phase=recovered_stopped]')).to_be_visible()
    assert len([r for r in posts(env) if r[1].endswith('/stop')])==1
    # Hold a read response to test a disappearing owner session without more mutation.
    p.evaluate("""()=>{const prior=api;api=async(...args)=>{const value=await prior(...args);window.readReady=true;await new Promise(r=>window.releaseRecoveryRead=r);return value;};}""")
    p.locator('[data-nrec=refresh]').click();p.wait_for_function('window.readReady===true')
    p.evaluate('()=>{state.me=null;window.releaseRecoveryRead();}')
    expect(p.locator('.nrec-dialog')).to_have_count(0);assert not env['errors']


def test_browser_manage_entry_and_replacement_entry_coexist(recovery_env):
    env = recovery_env;p = enter(env);assert p.locator('.nv2-actions [data-act=nr-open]').count()==1
    p.keyboard.press('Escape')
    if not env['bridge']:
        p.locator('.nv2-actions [data-act=nv2edit]').click()
        expect(p.locator('.nv5-manage-actions [data-act=nrec-open]')).to_be_visible()
        assert p.locator('.nv5-manage-actions [data-act=nr-open]').count()==1
        p.locator('.nv5-manage-actions [data-act=nrec-open]').click()
        expect(p.locator('[data-nrec-phase=not_started]')).to_be_visible()
        p.keyboard.press('Escape')
    # The entry wrapper must not expose an owner action to a reseller view.
    p.evaluate("()=>{state.me={id:'rep',role:'reseller',writes_enabled:true};}")
    p.evaluate("()=>DarkNodeRecovery.open('node-recovery-1')")
    assert p.locator('.nrec-dialog').count()==0 and not env['errors']


def test_browser_missing_snapshot_explains_rebuild_instead_of_offering_stop(recovery_env):
    env = recovery_env;p = enter(env)
    with env['reg'].store.transaction() as db:db.execute('DELETE FROM remote_node_desired_state WHERE node_id=?',(NODE,))
    env['seen'].clear();pid = env['target'].process.pid
    act(p,'review','unavailable')
    assert 'No saved configuration exists' in p.locator('[role=alert]').inner_text()
    assert p.locator('[data-nrec=stop]').count()==0 and not env['seen']
    assert env['target'].process.pid==pid and not env['errors']

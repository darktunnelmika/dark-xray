"""Ordinary enrollment UI: full-panel Chromium HTTP in CI, no provider WAN.

DARK_BROWSER_TEST_BRIDGE=1 explicitly selects a local DOM/API harness when
managed Chromium blocks loopback. It does not disable that policy. CI requires
HTTP mode and never sets the bridge flag. Agent socket/TLS and Xray are fixtures.
"""
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from server import make_app
from test_hub_node_control_live import hub
from test_node_replacement_prepare import candidate, code
from test_node_installations import http_transport, seed_account, sql_rows
from test_node_pairing import NEW, pending
from test_node_hub_recovery import TOKEN

ROOT = Path(__file__).resolve().parents[1]
PAIR = '/api/nodes/pair'
PENDING = '/api/nodes/pairings'
TABLES = ('clients', 'managed_clients', 'core_clients', 'traffic_ledger', 'owners', 'core_inbounds')


@pytest.fixture(scope='module')
def pairing_browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('DARK_TEST_CHROMIUM'),
                                     args=['--no-sandbox'])
        yield browser
        browser.close()


@pytest.fixture
def pairing_env(hub, tmp_path, monkeypatch, pairing_browser):
    reg, owner, engine = hub
    seed_account(reg, owner, engine)
    listener = socket.socket();listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    engine.config.public_origin = origin;engine.config.secure_cookie = False
    engine.config.panel_path = '/control'
    app = make_app(owner.app.state.manager, owner.app.state.auth, background=False)
    reg = app.state.nodes
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False, ws='none'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start();deadline = time.monotonic()+5
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
    assert server.started, 'HTTP fixture did not start'
    context = pairing_browser.new_context(viewport={'width':1365,'height':950})
    try:
        with candidate(tmp_path/'agent', background=True) as (target, runtime, agent, token):
            env = dict(reg=reg, owner=owner, app=app, engine=engine, target=target, runtime=runtime,
                       agent=agent, token=token, origin=origin, page=context.new_page(), requests=[], errors=[],
                       bridge=os.environ.get('DARK_BROWSER_TEST_BRIDGE')=='1', drop_rotation=False)
            if os.environ.get('DARK_REQUIRE_PAIR_HTTP') == '1':
                assert not env['bridge'], 'CI must exercise full-panel HTTP, not a DOM bridge'
            def after(response):
                if env['drop_rotation'] and response.request.url.path.endswith('/replacement/rotate-token'):
                    raise OSError('Injected lost rotation acknowledgement')
            env['seen'] = http_transport(reg, agent, monkeypatch, response_hook=after)
            env['page'].on('pageerror', lambda exc: env['errors'].append(str(exc)))
            def track(req):
                if '/api/nodes/pair' in req.url:
                    env['requests'].append((req.method, req.url, req.post_data_json))
            env['page'].on('request', track)
            if env['bridge']:
                with TestClient(app, base_url=origin, raise_server_exceptions=False) as local:
                    login = local.post('/control/api/auth/login', json={'username':'dark','password':'Test!OnlyPassword123'})
                    assert login.status_code==200, login.text
                    local.headers['X-Dark-CSRF'] = login.json()['csrf'];env['local'] = local
                    def request(path, method, body):
                        env['requests'].append((method, origin+'/control'+path, body))
                        response = local.request(method, '/control'+path, **({'json':body} if body is not None else {}))
                        if env.get('lose_path')==path:return {'status':599,'body':{'detail':'Injected lost HTTP response'}}
                        doc = response.json()
                        if env.get('override_path')==path:doc = env['override'](doc)
                        return {'status':response.status_code,'body':doc}
                    env['page'].expose_function('pairingTestRequest', request)
                    yield env
            else:
                yield env
    finally:
        context.close();server.should_exit = True;thread.join(timeout=8);listener.close()
        assert not thread.is_alive(), 'HTTP fixture failed to stop'


def enter(env, lang='en'):
    p = env['page']
    if env['bridge']:
        p.set_content('<html><head></head><body class="skin-cyber-classic"><div id="content"></div>'
                      '<div id="overlay" style="display:none"></div><div id="toasts"></div></body></html>')
        for name in ('style.css','live.css','nodes-v2.css','cyber-classic.css'):
            p.add_style_tag(path=str(ROOT/'web'/name))
        me = env['local'].get('/control/api/me').json()
        # Real dialog helper and real pairNodeDialog are evaluated, not copied.
        # This intentionally omits the full shell; only CI is full HTTP evidence.
        p.evaluate("""([me,lang])=>{
          window.localStorage={getItem:k=>k==='dark_lang'?lang:null};
          window.state={me,page:'nodes',inbounds:[]};window.isOwner=()=>state.me?.role==='owner';
          window.L=(en,fa)=>lang==='fa'?fa:en;window.enc=encodeURIComponent;
          window.e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          window.$=s=>document.querySelector(s);window.icon=()=>'';
          window.closeDialog=()=>{document.querySelector('#overlay').replaceChildren();document.querySelector('#overlay').style.display='none';};
          window.toast=(s)=>{const e=document.createElement('div');e.textContent=s;document.querySelector('#toasts').append(e);};
          window.renderPage=async()=>{};
          window.api=async(path,method='GET',body)=>{const r=await pairingTestRequest(path,method,body??null);if(r.status>=400)throw Error(r.body?.detail||'API failed');return r.body;};
          document.documentElement.lang=lang;document.documentElement.dir=lang==='fa'?'rtl':'ltr';
          document.body.addEventListener('click',ev=>{if(ev.target.closest('[data-act=close]'))closeDialog();});
          document.querySelector('#content').innerHTML='<button data-act="nv2new">Add Node</button>';
        }""", [me, lang])
        live = (ROOT/'web/live.js').read_text()
        dialog = live[live.index('function dialog('):live.index('\n',live.index('function dialog('))]
        source = (ROOT/'web/nodes-v2.js').read_text()
        pair = source[source.index('async function pairNodeDialog'):source.index('async function createToken')]
        p.add_script_tag(content=dialog+'\n'+pair+'\ndocument.querySelector("[data-act=nv2new]").onclick=()=>pairNodeDialog().catch(e=>toast(e.message));')
    else:
        p.add_init_script(f"localStorage.setItem('dark_lang',{json.dumps(lang)})")
        p.goto(env['origin']+'/control/')
        p.locator('#login-form [name=username]').fill('dark')
        p.locator('#login-form [name=password]').fill('Test!OnlyPassword123')
        p.locator('#login-form button[type=submit]').click()
        p.locator('.nav-btn[data-page=nodes]').click()
    return p


def open_pair(env):
    p=env['page'];p.locator('[data-act=nv2new]').click()
    expect(p.locator('#dialog-form textarea[name=code]')).to_be_visible()
    return p


def close_pair(p):
    p.locator('#dialog-form .dialog-head [data-act=close]').click()
    expect(p.locator('#dialog-form')).to_have_count(0)


def submit(p, error=False):
    p.locator('#submit-dialog').click()
    if error:
        expect(p.locator('#form-error')).not_to_be_empty()
        expect(p.locator('#submit-dialog')).to_be_enabled()
    else:
        expect(p.locator('#dialog-form')).to_have_count(0)


def mutations(env):
    return [r for r in env['requests'] if r[0]=='POST' and '/api/nodes/pair' in r[1]]


def relaunch_nodes(env, lang='en'):
    # Closing/reopening the bridge recreates DOM only. CI really reloads HTTP.
    if env['bridge']:return enter(env, lang)
    p=env['page'];p.reload();p.locator('.nav-btn[data-page=nodes]').click();return p


@pytest.mark.parametrize('lang',['en','fa'])
def test_browser_lost_rotation_resumes_from_saved_list_without_new_secret(pairing_env, lang):
    env=pairing_env;p=enter(env,lang);reg=env['reg']
    before={t:sql_rows(reg,t) for t in TABLES};pid=env['target'].process.pid
    open_pair(env);assert mutations(env)==[] and env['seen']==[]
    env['drop_rotation']=True
    p.locator('[name=code]').fill(code());submit(p,error=True)
    row=pending(reg);assert row and env['token'].token!=TOKEN
    assert reg.store.db.execute('SELECT 1 FROM remote_nodes WHERE id=?',(NEW,)).fetchone() is None
    assert 'Node paired' not in p.locator('#toasts').inner_text() and 'نود متصل شد' not in p.locator('#toasts').inner_text()
    secret=reg.cipher.decrypt(row['candidate_enc'].encode()).decode()
    close_pair(p);relaunch_nodes(env,lang);env['seen'].clear();count=len(mutations(env));open_pair(env)
    assert not env['seen'] and len(mutations(env))==count
    expect(p.locator('[name=code]')).to_have_value('')
    expect(p.locator('[name=resume]')).to_have_value('')
    assert not p.locator('[name=confirmRetry]').is_checked()
    p.locator('[name=resume]').select_option(row['attempt_id']);submit(p,error=True)
    assert len(mutations(env))==count
    if lang=='fa':p.set_viewport_size({'width':390,'height':844})
    assert p.locator('.dialog').evaluate('(x)=>x.scrollWidth<=x.clientWidth+1')
    (ROOT/'qa').mkdir(exist_ok=True)
    p.screenshot(path=str(ROOT/'qa'/f'browser-pairing-pending-{lang}.png'),full_page=True)
    env['drop_rotation']=False
    p.locator('[name=confirmRetry]').check();submit(p)
    assert pending(reg) is None and reg.get(NEW,secret=True)['token']==secret==env['token'].token
    assert reg.get(NEW)['inboundIds']==[] and not next(n for n in reg.list() if n['id']==NEW)['failover_ready']
    assert env['target'].process.pid==pid and not env['runtime'].status()['appliedRevision']
    assert all(m=='GET' for m,_,_ in env['seen'])
    assert before=={t:sql_rows(reg,t) for t in TABLES}
    assert mutations(env)[-1][2]=={'confirmRetry':True}
    if not env['bridge']:
        storage=p.evaluate('JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])')
        assert TOKEN not in storage and secret not in storage and 'DXN1.' not in storage and row['attempt_id'] not in storage
    assert not env['errors'],env['errors']


@pytest.mark.parametrize('pending_reply',[True,False])
def test_browser_lost_first_hub_reply_does_not_repeat_pair_on_reload(pairing_env,pending_reply):
    env=pairing_env;p=enter(env);open_pair(env);env['drop_rotation']=pending_reply
    if env['bridge']:env['lose_path']=PAIR
    else:
        def drop(route):route.fetch();route.abort()
        p.route('**/api/nodes/pair',drop)
    p.locator('[name=code]').fill(code());submit(p,error=True)
    assert len(mutations(env))==1
    assert bool(pending(env['reg']))==pending_reply
    close_pair(p)
    if env['bridge']:env.pop('lose_path',None)
    else:p.unroute('**/api/nodes/pair')
    relaunch_nodes(env);env['seen'].clear();open_pair(env)
    assert len(mutations(env))==1 and not env['seen']
    assert p.locator('[name=resume]').count()==int(pending_reply)
    if not pending_reply:assert env['reg'].get(NEW)['id']==NEW
    assert not env['errors'],env['errors']


def test_browser_double_click_registers_once_and_readonly_has_no_submit(pairing_env):
    env=pairing_env;p=enter(env);open_pair(env)
    p.locator('[name=code]').fill(code())
    p.evaluate("()=>{const b=document.querySelector('#submit-dialog');b.click();b.click();}")
    expect(p.locator('#dialog-form')).to_have_count(0)
    assert len(mutations(env))==1 and env['reg'].store.db.execute('SELECT COUNT(*) FROM remote_node_pair_history').fetchone()[0]==1
    env['engine'].config.writes_enabled=False
    if not env['bridge']:p.evaluate('()=>{state.me.writes_enabled=false}')
    else:enter(env)
    count=len(mutations(env));open_pair(env)
    assert p.locator('#submit-dialog').count()==0 and len(mutations(env))==count
    assert not env['errors'],env['errors']


def test_browser_saved_selection_rejects_code_conflict_and_escapes_name(pairing_env):
    env=pairing_env;saved=env['app'].state.pairing.begin(code())
    env['reg'].store.db.execute('UPDATE remote_node_pairings SET name=?',('<img src=x onerror="window.pairXss=1">',))
    p=enter(env);open_pair(env)
    assert p.locator('.nv5-pair img').count()==0 and p.evaluate('window.pairXss||0')==0
    p.locator('[name=resume]').select_option(saved['attempt_id']);p.locator('[name=confirmRetry]').check()
    p.locator('[name=code]').fill(code());submit(p,error=True)
    assert mutations(env)==[] and env['seen']==[]
    close_pair(p);open_pair(env)
    assert p.locator('[name=confirmRetry]').is_checked() is False
    assert p.locator('[name=resume]').input_value()=='' and not env['errors']


@pytest.mark.parametrize('kind',['duplicate','unknown-phase','wrong-type'])
def test_browser_invalid_saved_response_exposes_no_actionable_form(pairing_env,kind):
    env=pairing_env;env['app'].state.pairing.begin(code());p=enter(env)
    def corrupt(doc):
        if kind=='duplicate':doc['items']*=2
        elif kind=='unknown-phase':doc['items'][0]['phase']='arbitrary'
        else:doc['items'][0]['paired']='false'
        return doc
    if env['bridge']:env['override_path']=PENDING;env['override']=corrupt
    else:
        def reply(route):
            response=route.fetch();route.fulfill(response=response,json=corrupt(response.json()))
        p.route('**/api/nodes/pairings',reply)
    p.locator('[data-act=nv2new]').click()
    expect(p.locator('#toasts')).to_contain_text('Invalid saved pairing status.')
    assert p.locator('#dialog-form').count()==0 and mutations(env)==[] and env['seen']==[]
    assert not env['errors'],env['errors']


def test_browser_late_completion_cannot_close_new_dialog(pairing_env):
    env=pairing_env;p=enter(env);open_pair(env)
    p.evaluate("""()=>{const original=api;api=async(...args)=>{const result=await original(...args);if(args[0]==='/api/nodes/pair'){document.body.dataset.pairReply='ready';await new Promise(r=>window.releasePairReply=r);}return result;};}""")
    p.locator('[name=code]').fill(code());p.locator('#submit-dialog').click()
    expect(p.locator('body')).to_have_attribute('data-pair-reply','ready')
    close_pair(p);open_pair(env)
    p.evaluate('()=>window.releasePairReply()')
    expect(p.locator('#dialog-form textarea[name=code]')).to_be_visible()
    assert p.locator('[name=code]').input_value()=='' and len(mutations(env))==1
    assert env['reg'].get(NEW)['id']==NEW and not env['errors']

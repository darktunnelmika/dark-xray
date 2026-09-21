"""Real Chromium -> HTTP Hub -> real Agent API/SQLite; fake Xray, no WAN proof.

Run in the browser CI job (Chromium is required). No production paths or servers.
DARK_TEST_CHROMIUM optionally points to a local installed browser for local QA.
"""
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect
from server import make_app
from fastapi.testclient import TestClient
from test_hub_node_control_live import hub
from test_node_replacement_prepare import candidate, code, NODE, TOKEN
from test_node_installations import http_transport, seed_account, sql_rows

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def browser_env(hub,tmp_path,monkeypatch):
    reg,owner,engine=hub
    seed_account(reg,owner,engine)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    origin=f'http://127.0.0.1:{port}'
    engine.config.public_origin=origin;engine.config.secure_cookie=False;engine.config.panel_path='/control'
    app=make_app(owner.app.state.manager,owner.app.state.auth,background=False)
    reg=app.state.nodes
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False,ws='none'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+5
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
    assert server.started,'Local fixture did not start'
    try:
        with candidate(tmp_path/'target') as (target,runtime,agent,token),sync_playwright() as pw:
            seen=http_transport(reg,agent,monkeypatch)
            executable=os.environ.get('DARK_TEST_CHROMIUM')
            browser=pw.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox'])
            try:
                context=browser.new_context(viewport={'width':1365,'height':1000})
                page=context.new_page();errors=[];posts=[]
                page.on('pageerror',lambda ex:errors.append(str(ex)))
                page.on('request',lambda req:posts.append(req.url) if req.method=='POST' and '/replacement/' in req.url else None)
                env={'page':page,'reg':reg,'target':target,'runtime':runtime,'token':token,'engine':engine,
                     'agent':agent,'seen':seen,'errors':errors,'posts':posts,'origin':origin,'app':app,
                     'bridge':os.environ.get('DARK_BROWSER_TEST_BRIDGE')=='1'}
                if env['bridge']:
                    # Explicit local test transport: no browser HTTP navigation.
                    # CI does NOT set this and uses the real HTTP Hub above.
                    with TestClient(app,base_url=origin,raise_server_exceptions=False) as local:
                        login=local.post('/control/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
                        assert login.status_code==200,login.text
                        local.headers['X-Dark-CSRF']=login.json()['csrf']
                        env['local']=local
                        def bridge_request(path,method,body):
                            if method=='POST':posts.append(origin+'/control'+path)
                            response=local.request(method,'/control'+path,json=body) if body is not None else local.request(method,'/control'+path)
                            if env.get('lose_prepare') and path.endswith('/prepare'):
                                return {'status':599,'body':{'detail':'Injected lost response'}}
                            return {'status':response.status_code,'body':response.json()}
                        page.expose_function('replacementTestRequest',bridge_request)
                        yield env
                else:
                    yield env
            finally:browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=8)
        assert not thread.is_alive(),'Local fixture did not stop'


def enter(env,lang='en'):
    page=env['page']
    if env['bridge']:
        bridge_page(env,lang)
        page.locator('.nv2-actions [data-act=nr-open]').click()
        expect(page.locator('[data-nr-phase=none]')).to_be_visible()
        return page
    page.add_init_script(f"localStorage.setItem('dark_lang',{json.dumps(lang)})")
    page.goto(env['origin']+'/control/')
    page.locator('#login-form [name=username]').fill('dark')
    page.locator('#login-form [name=password]').fill('Test!OnlyPassword123')
    page.locator('#login-form button[type=submit]').click()
    page.locator('.nav-btn[data-page=nodes]').click()
    page.locator('.nv2-actions [data-act=nr-open]').click()
    expect(page.locator('[data-nr-phase=none]')).to_be_visible()
    return page


def do(page,action,phase=None):
    b=page.locator(f'.nr-dialog [data-nr={action}]')
    expect(b).to_be_enabled()
    b.click()
    expect(page.locator('.nr-body')).to_have_attribute('aria-busy','false')
    if phase:expect(page.locator(f'[data-nr-phase={phase}]')).to_be_visible()


def agree(page,keys):
    for key in keys:page.locator(f'.nr-dialog input[name={key}]').check()


def prepare_ui(page):
    page.locator('#nr-code').fill(code());do(page,'prepare','prepared')


def commit_ui(page):
    expect(page.locator('[data-nr=commit]')).to_be_disabled()
    agree(page,['acceptUnconfirmedOldServer','acceptUnreportedTraffic']);do(page,'commit','committed')


def ready_ui(page):
    prepare_ui(page);commit_ui(page);do(page,'stage','staged');do(page,'review','staged')
    expect(page.locator('[data-nr=start]')).to_be_disabled()


def start_ui(page,phase='activated'):
    agree(page,['confirmStart','acceptEndpointResponsibility','acceptUnconfirmedOldServer','acceptUnreportedTraffic'])
    do(page,'start',phase)


@pytest.mark.parametrize('lang',['en','fa'])
def test_browser_full_replacement_flow_with_explicit_consents_and_preserved_usage(browser_env,lang):
    env=browser_env;page=enter(env,lang)
    assert not env['posts']
    before=sql_rows(env['reg'],'traffic_ledger');users=sql_rows(env['reg'],'core_clients')
    do(page,'refresh','none');assert not env['posts']
    prepare_ui(page)
    assert env['reg'].get(NODE)['enabled'] and not env['target'].running
    commit_ui(page);assert not env['reg'].get(NODE)['enabled']
    do(page,'stage','staged');assert not env['target'].running
    do(page,'review','staged')
    assert page.locator('.nr-plan').is_visible()
    assert 'turkey.example.test' in page.locator('.nr-plan').inner_text()
    assert page.locator('.nr-consents input:checked').count()==0
    agree(page,['confirmStart','acceptEndpointResponsibility','acceptUnconfirmedOldServer'])
    expect(page.locator('[data-nr=start]')).to_be_disabled()
    if lang=='fa':page.set_viewport_size({'width':390,'height':844})
    assert page.locator('.nr-dialog').evaluate('(x)=>x.scrollWidth<=x.clientWidth+1')
    out=ROOT/'qa';out.mkdir(exist_ok=True)
    page.screenshot(path=str(out/f'browser-replacement-review-{lang}.png'),full_page=True)
    page.locator('[name=acceptUnreportedTraffic]').check();do(page,'start','activated')
    assert env['target'].running and env['reg'].get(NODE)['enabled']
    assert sql_rows(env['reg'],'traffic_ledger')==before and sql_rows(env['reg'],'core_clients')==users
    assert page.locator('[data-nr=start]').count()==0
    assert not env['errors'],env['errors']
    storage=page.evaluate('JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])')
    assert TOKEN not in storage and 'DXN1.' not in storage and env['token'].token not in storage
    count=len(env['posts']);page.keyboard.press('Escape')
    expect(page.locator('.nr-dialog')).to_have_count(0)
    page.locator('.nv2-actions [data-act=nr-open]').click()
    expect(page.locator('[data-nr-phase=activated]')).to_be_visible()
    assert len(env['posts'])==count


def test_browser_resumes_lost_start_reply_after_reload_without_new_command(browser_env,monkeypatch):
    env=browser_env;page=enter(env);ready_ui(page)
    def lose(response):
        if response.request.url.path=='/node/api/v1/control/activate':raise OSError('Injected lost Start reply')
    http_transport(env['reg'],env['agent'],monkeypatch,response_hook=lose)
    start_ui(page,'starting')
    assert env['target'].running and not env['reg'].get(NODE)['enabled']
    pid=env['target'].process.pid;receipt=env['runtime'].command_status()
    count=len(env['posts'])
    if env['bridge']:bridge_page(env,'en')
    else:
        page.reload();page.locator('.nav-btn[data-page=nodes]').click()
    page.locator('.nv2-actions [data-act=nr-open]').click()
    expect(page.locator('[data-nr-phase=starting]')).to_be_visible()
    assert len(env['posts'])==count
    expect(page.locator('[data-nr=start]')).to_be_disabled()
    http_transport(env['reg'],env['agent'],monkeypatch)
    start_ui(page)
    assert env['target'].process.pid==pid and env['runtime'].command_status()==receipt
    assert not env['errors'],env['errors']


def test_browser_disposal_is_explicit_and_keeps_source_unchanged(browser_env):
    env=browser_env;page=enter(env);before=env['reg'].get(NODE,secret=True)
    prepare_ui(page);page.locator('.nr-dialog details summary').click()
    expect(page.locator('[data-nr=cancel]')).to_be_disabled()
    agree(page,['discardCandidate']);do(page,'cancel','cancelled')
    assert env['reg'].get(NODE,secret=True)==before
    assert not env['target'].running
    assert page.locator('#nr-code').input_value()==''
    assert not env['errors']


def test_browser_ambiguous_prepare_reply_requires_read_recovery_not_repost(browser_env):
    env=browser_env;page=enter(env)
    pattern='**/replacement/prepare'
    def lose(route):
        response=route.fetch()
        assert response.ok
        route.abort('failed')
    if env['bridge']:env['lose_prepare']=True
    else:page.route(pattern,lose)
    page.locator('#nr-code').fill(code());do(page,'prepare')
    expect(page.locator('.nr-dialog [role=alert]')).to_be_visible()
    assert page.locator('[data-nr=prepare]').count()==0
    assert len(env['posts'])==1
    if env['bridge']:env['lose_prepare']=False
    else:page.unroute(pattern,lose)
    do(page,'refresh','prepared')
    assert len(env['posts'])==1 and page.locator('#nr-code').count()==0
    assert not env['errors']


def test_browser_readonly_mode_cannot_offer_mutations(browser_env):
    env=browser_env;env['engine'].config.writes_enabled=False
    page=enter(env)
    assert page.locator('.nr-dialog [data-consents]').count()==0
    assert page.locator('[data-nr=prepare]').count()==0
    do(page,'refresh','none');assert not env['posts']


def test_browser_remote_text_is_escaped_and_refresh_clears_old_consents(browser_env):
    env=browser_env;page=enter(env);prepare_ui(page)
    with env['reg'].store.transaction() as db:
        db.execute("UPDATE remote_node_replacements SET last_error=?",('<img src=x onerror="window.nrPwned=1">',))
    do(page,'refresh','prepared')
    assert page.locator('.nr-dialog img').count()==0 and page.evaluate('window.nrPwned') is None
    with env['reg'].store.transaction() as db:db.execute("UPDATE remote_node_replacements SET last_error=''")
    do(page,'refresh','prepared');agree(page,['acceptUnconfirmedOldServer','acceptUnreportedTraffic'])
    expect(page.locator('[data-nr=commit]')).to_be_enabled()
    do(page,'refresh','prepared');expect(page.locator('[data-nr=commit]')).to_be_disabled()
    assert len(env['posts'])==1 and not env['errors']


def bridge_page(env,lang):
    """Render the exact workspace assets against the real API without sockets.

    The managed local browser disallows loopback HTTP. This explicit harness
    tests DOM/consent logic, not full shell routing; full-shell HTTP runs in CI.
    """
    page=env['page'];me=env['local'].get('/control/api/me').json()
    page.set_content('<html><head></head><body class="skin-cyber-classic"><div id="content"></div></body></html>')
    for css in ('style.css','cyber-classic.css','node-replacement.css'):
        page.add_style_tag(path=str(ROOT/'web'/css))
    page.evaluate("""([me,lang,node])=>{
        // Fake only language storage on about:blank, which has no origin storage.
        Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:k=>k==='dark_lang'?lang:null}});
        Object.defineProperty(window,'sessionStorage',{configurable:true,value:{}});
        window.state={me,page:'nodes'};window.isOwner=()=>state.me?.role==='owner';
        window.e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        window.enc=encodeURIComponent;window.closeDialog=()=>{};
        window.enginePage=async()=>'<div class="nv2-actions"><button data-act="nv2edit" data-id="'+e(node)+'">Manage</button></div>';
        window.runAction=async()=>{};
        window.api=async(path,method='GET',body)=>{
            const r=await replacementTestRequest(path,method,body??null);
            if(r.status===401)state.me=null;
            if(r.status>=400)throw Error('API request failed');return r.body;
        };
        document.body.addEventListener('click',ev=>{const el=ev.target.closest('[data-act]');if(el)void runAction(el.dataset.act,el);});
    }""",[me,lang,NODE])
    page.add_script_tag(path=str(ROOT/'web/node-replacement.js'))
    page.evaluate("async()=>{document.querySelector('#content').innerHTML=await enginePage()}")


def test_browser_pending_start_can_be_paused_and_requires_a_new_review(browser_env,monkeypatch):
    env=browser_env;page=enter(env);ready_ui(page)
    def lose(response):
        if response.request.url.path=='/node/api/v1/control/activate':raise OSError('Injected lost Start reply')
    http_transport(env['reg'],env['agent'],monkeypatch,response_hook=lose)
    start_ui(page,'starting');assert env['target'].running
    http_transport(env['reg'],env['agent'],monkeypatch)
    expect(page.locator('[data-nr=pause]')).to_be_disabled()
    agree(page,['confirmStop']);do(page,'pause','paused')
    assert not env['target'].running and not env['reg'].get(NODE)['enabled']
    assert page.locator('[data-nr=start]').count()==0
    do(page,'review','paused')
    expect(page.locator('[data-nr=start]')).to_be_disabled()
    start_ui(page,'activated')
    assert env['target'].running and not env['errors']


def test_browser_stale_review_never_starts_target_and_clears_consent(browser_env):
    env=browser_env;page=enter(env);ready_ui(page)
    env['engine'].save_section('dns',{'servers':['8.8.8.8']})
    agree(page,['confirmStart','acceptEndpointResponsibility','acceptUnconfirmedOldServer','acceptUnreportedTraffic'])
    do(page,'start')
    expect(page.locator('.nr-dialog [role=alert]')).to_be_visible()
    assert not env['target'].running and not env['reg'].get(NODE)['enabled']
    assert page.locator('[data-nr=start]').count()==0
    do(page,'refresh','staging')
    assert not env['errors']

"""Manage Node credential flow, real Chromium; bridge mode is explicitly not HTTP."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from node_pairing import NodePairing
import nodes as node_module
from test_hub_node_control_live import hub
from test_node_pairing_browser import pairing_browser, pairing_env, enter, submit, close_pair
from test_node_pairing import NEW
from test_node_replacement_prepare import code

ROOT=Path(__file__).resolve().parents[1]
TOKEN='dkn_'+'BrowserCredentialFixture'*3


def setup(env,monkeypatch,lang='en'):
    reg=env['reg'];pairing=NodePairing(reg);saved=pairing.begin(code());assert pairing.retry(saved['attempt_id'])['paired']
    original=node_module.node_https_request;env['token_posts']=0;env['drop_token']=False
    def transport(*a,**k):
        result=original(*a,**k)
        if a[2].endswith('/token/rotate'):
            env['token_posts']+=1
            if env['drop_token']:raise OSError('Injected lost registered rotation response')
        return result
    monkeypatch.setattr(node_module,'node_https_request',transport)
    p=enter(env,lang)
    if env['bridge']:
        p.evaluate('nodes=>{state.nv2={nodes};state.inbounds=[];window.controlBanner=()=>"";window.button=()=>"";}',reg.list())
        live=(ROOT/'web/live.js').read_text();start=live.index('function field(')
        field=live[start:live.index('\n',start)]
        source=(ROOT/'web/nodes-v2.js').read_text()
        funcs=source[source.index('function inboundPicker'):source.index('async function pairNodeDialog')]
        p.add_script_tag(content=field+'\n'+funcs+'\ndocument.querySelector("#content").innerHTML=\'<button id="credential-manage">Manage Node</button>\';document.querySelector("#credential-manage").onclick=()=>nodeDialog('+json.dumps(NEW)+').catch(e=>toast(e.message));')
    return p


def open_manage(env):
    p=env['page']
    p.locator('#credential-manage' if env['bridge'] else '[data-act=nv2edit][data-id="'+NEW+'"]').first.click()
    expect(p.locator('#dialog-form [name=token]')).to_be_visible()
    return p


@pytest.mark.parametrize('lang',['en','fa'])
def test_browser_registered_token_lost_reply_resumes_without_second_rotation(pairing_env,monkeypatch,lang):
    env=pairing_env;p=setup(env,monkeypatch,lang);open_manage(env);pid=env['target'].process.pid
    env['drop_token']=True;p.locator('[name=token]').fill(TOKEN);submit(p,error=True)
    expect(p.locator('[name=token]')).to_have_value('');assert env['token_posts']==1
    status=env['app'].state.node_credentials.status(NEW);assert status['pending']
    close_pair(p);env['drop_token']=False;open_manage(env)
    expect(p.locator('[name=retryCredential]')).not_to_be_checked()
    if lang=='fa':
        p.set_viewport_size({'width':430,'height':950});(ROOT/'qa').mkdir(exist_ok=True)
        p.screenshot(path=str(ROOT/'qa/browser-credentials-pending-fa.png'),full_page=True)
    p.locator('[name=retryCredential]').check();submit(p)
    assert env['app'].state.node_credentials.status(NEW)['rotated']
    assert env['token_posts']==1 and env['target'].process.pid==pid and not env['errors']


def test_browser_mixed_settings_rejected_before_rotation(pairing_env,monkeypatch):
    env=pairing_env;p=setup(env,monkeypatch);open_manage(env)
    p.locator('[name=name]').fill('Do not apply');p.locator('[name=token]').fill(TOKEN);submit(p,error=True)
    assert env['token_posts']==0 and env['app'].state.node_credentials.status(NEW)['phase']=='not_started'
    assert env['reg'].get(NEW)['name']!='Do not apply' and not env['errors']


def test_browser_pending_retry_needs_new_consent_after_reopen(pairing_env,monkeypatch):
    env=pairing_env;p=setup(env,monkeypatch);open_manage(env)
    env['drop_token']=True;p.locator('[name=token]').fill(TOKEN);submit(p,error=True);close_pair(p)
    open_manage(env);submit(p,error=True);assert env['token_posts']==1
    p.locator('[name=retryCredential]').check();close_pair(p);open_manage(env)
    expect(p.locator('[name=retryCredential]')).not_to_be_checked()
    assert env['token_posts']==1 and not env['errors']

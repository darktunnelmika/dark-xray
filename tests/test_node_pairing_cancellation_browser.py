"""Cancellation browser cases reuse the real full-panel HTTP pairing fixture."""
import pytest
from playwright.sync_api import expect
from test_hub_node_control_live import hub
from test_node_pairing_browser import (
    ROOT, PAIR, PENDING, TABLES, pairing_browser, pairing_env, enter, open_pair,
    close_pair, submit, mutations, relaunch_nodes,
)
from test_node_pairing import pending
from test_node_replacement_prepare import code
from test_node_hub_recovery import TOKEN
from test_node_installations import sql_rows


def select_cancellation(p, attempt, *, consent=True):
    p.locator('[name=resume]').select_option(attempt)
    p.locator('[name=pairAction]').select_option('cancel')
    if consent:
        p.locator('[name=confirmCancel]').check()
        p.locator('[name=acknowledgeCredentialReset]').check()


@pytest.mark.parametrize('lang',['en','fa'])
def test_browser_cancellation_recovers_lost_disposal_and_keeps_registered_data(pairing_env,lang):
    env=pairing_env;p=enter(env,lang);reg=env['reg']
    before={t:sql_rows(reg,t) for t in TABLES};open_pair(env)
    env['drop_rotation']=True
    p.locator('[name=code]').fill(code());submit(p,error=True)
    saved=pending(reg);original_candidate=env['token'].token
    expect(p.locator('[name=code]')).to_have_value('')
    close_pair(p);open_pair(env);select_cancellation(p,saved['attempt_id'],consent=False)
    count=len(mutations(env));submit(p,error=True);assert len(mutations(env))==count
    p.locator('[name=confirmCancel]').check();p.locator('[name=acknowledgeCredentialReset]').check()
    submit(p,error=True);row=pending(reg)
    assert row['resolution']=='cancelling' and row['disposal_enc']
    disposal=reg.cipher.decrypt(row['disposal_enc'].encode()).decode()
    assert env['token'].token==disposal!=original_candidate
    assert not p.locator('[name=confirmCancel]').is_checked()
    assert not p.locator('[name=acknowledgeCredentialReset]').is_checked()
    close_pair(p);relaunch_nodes(env,lang);env['seen'].clear();count=len(mutations(env));open_pair(env)
    assert env['seen']==[] and len(mutations(env))==count
    expect(p.locator('[name=resume]')).to_have_value('')
    p.locator('[name=resume]').select_option(saved['attempt_id']);p.locator('[name=confirmRetry]').check()
    submit(p,error=True);assert len(mutations(env))==count
    select_cancellation(p,saved['attempt_id'])
    if lang=='fa':p.set_viewport_size({'width':390,'height':844})
    assert p.locator('.dialog').evaluate('(x)=>x.scrollWidth<=x.clientWidth+1')
    p.screenshot(path=str(ROOT/'qa'/f'browser-pairing-cancel-{lang}.png'),full_page=True)
    env['drop_rotation']=False;submit(p)
    assert pending(reg) is None and env['token'].token==disposal
    assert not env['target'].running and env['runtime'].control_status()['manual_stop']
    assert before=={t:sql_rows(reg,t) for t in TABLES}
    assert not any(path.endswith('/replacement/rotate-token') for _,path,_ in env['seen'])
    assert mutations(env)[-1][2]=={'confirmCancel':True,'acknowledgeCredentialReset':True}
    assert not env['errors'],env['errors']


def test_browser_local_withdrawal_no_remote_call_and_changed_selection_clears_consent(pairing_env):
    env=pairing_env;reg=env['reg'];saved=env['app'].state.pairing.begin(code());p=enter(env);open_pair(env)
    select_cancellation(p,saved['attempt_id'])
    p.locator('[name=pairAction]').select_option('resume')
    assert not p.locator('[name=confirmCancel]').is_checked()
    assert not p.locator('[name=acknowledgeCredentialReset]').is_checked()
    select_cancellation(p,saved['attempt_id']);p.locator('[name=resume]').select_option('')
    assert not p.locator('[name=confirmCancel]').is_checked()
    select_cancellation(p,saved['attempt_id']);submit(p)
    assert pending(reg) is None and env['seen']==[] and env['token'].token==TOKEN
    assert env['target'].running  # local withdrawal makes no remote idle claim
    assert len(mutations(env))==1 and not env['errors']


def test_browser_lost_cancellation_response_is_read_only_on_reopen(pairing_env):
    env=pairing_env;reg=env['reg'];saved=env['app'].state.pairing.begin(code());p=enter(env);open_pair(env)
    path=PENDING+'/'+saved['attempt_id']+'/cancel';select_cancellation(p,saved['attempt_id'])
    if env['bridge']:env['lose_path']=path
    else:
        def drop(route):route.fetch();route.abort()
        p.route('**'+path,drop)
    submit(p,error=True)
    assert pending(reg) is None and env['seen']==[] and len(mutations(env))==1
    close_pair(p)
    if env['bridge']:env.pop('lose_path',None)
    else:p.unroute('**'+path)
    relaunch_nodes(env);open_pair(env)
    assert p.locator('[name=resume]').count()==0 and len(mutations(env))==1 and env['seen']==[]
    assert not env['errors']


def test_browser_late_cancel_receipt_cannot_close_a_new_dialog(pairing_env):
    env=pairing_env;saved=env['app'].state.pairing.begin(code());p=enter(env);open_pair(env)
    p.evaluate("""()=>{const original=api;api=async(...args)=>{const result=await original(...args);if(args[0].endsWith('/cancel')){document.body.dataset.cancelReply='ready';await new Promise(r=>window.releaseCancelReply=r);}return result;};}""")
    select_cancellation(p,saved['attempt_id']);p.locator('#submit-dialog').click()
    expect(p.locator('body')).to_have_attribute('data-cancel-reply','ready')
    close_pair(p);open_pair(env);p.evaluate('()=>window.releaseCancelReply()')
    expect(p.locator('#dialog-form textarea[name=code]')).to_be_visible()
    assert len(mutations(env))==1 and pending(env['reg']) is None and not env['errors']

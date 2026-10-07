"""Full panel HTTP/Chromium for independent port selection; remote delivery is a fixture ACK.
Real encrypted data plane is covered separately by test_node_real_transports.
"""
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect
from server import make_app
from test_node_relay import relay_env, env
from test_node_pairing_browser import pairing_browser


@pytest.mark.parametrize('lang', ['en', 'fa'])
def test_owner_port_swap_workspace_and_inbound_checkbox(relay_env, pairing_browser, monkeypatch, lang):
    state, _, _ = relay_env
    store, engine, manager, auth, _ = state
    listener = socket.socket(); listener.bind(('127.0.0.1', 0))
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    engine.config.public_origin = origin; engine.config.secure_cookie = False
    engine.config.panel_path = '/control'
    app = make_app(manager, auth, background=False)
    def acknowledged(node_id, desired):
        app.state.nodes.mark_desired_state(node_id, desired['revision'], desired['hash'])
        return {'desired_state_applied': True, 'queued': False}
    monkeypatch.setattr(app.state.nodes, 'sync_desired_state', acknowledged)
    monkeypatch.setattr(app.state.nodes, 'traffic_matrix_probe', lambda *a, **k: {'listenerReady': True})
    monkeypatch.setattr(app.state.nodes, 'outbound_probe', lambda *a, **k: {'probe': {
        'success': True, 'delayMs': 31, 'egress': {'ip': '203.0.113.6'}}})
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False, ws='none'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True); thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline: time.sleep(.02)
    assert server.started
    context = pairing_browser.new_context(viewport={'width': 430 if lang == 'fa' else 1365, 'height': 950})
    page = context.new_page(); errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.on('dialog', lambda d: d.accept())
    try:
        page.add_init_script("localStorage.setItem('dark_lang','" + lang + "')")
        page.goto(origin + '/control/')
        page.locator('#login-form [name=username]').fill('dark')
        page.locator('#login-form [name=password]').fill('Test!OnlyPassword123')
        page.locator('#login-form button[type=submit]').click()
        if lang == 'fa': page.locator('.mobile-menu').click()
        page.locator('.nav-btn[data-page=swap]').click()
        expect(page.locator('h1')).to_have_text('DARK SWAP')
        page.locator('[data-act=swapnew]').click()
        expect(page.locator('[name=swapSource]')).to_be_visible()
        page.locator('[name=swapSource]').select_option('nl')
        page.locator('[name=swapInbound]').select_option('1')
        page.locator('[name=swapDestination]').select_option('de')
        page.locator('[name=swapSourcePort]').fill('26801')
        page.locator('[name=swapExitPort]').fill('26802')
        page.locator('[name=swapEntryAddress]').fill('iran.example.test')
        page.locator('#submit-dialog').click()
        enable = page.locator('[data-act=swapenable]')
        expect(enable).to_be_visible()
        row = app.state.node_port_swaps.rows()[0]
        rid = row['id']
        assert not row['enabled'] and row['phase'] == 'disabled'
        enable.click()
        expect(page.locator('[data-swap-phase]')).to_have_text('فعال' if lang == 'fa' else 'Enabled')
        assert app.state.node_port_swaps.get(rid)['enabled'] == 1
        expect(page.locator('[data-act=swapedit]')).to_be_disabled()
        page.locator('[data-act=swapdiagnostics]').click()
        expect(page.locator('.swap-probe')).to_contain_text('203.0.113.6')
        page.locator('[data-act=swapdisable]').click()
        expect(page.locator('[data-swap-phase]')).to_have_text('خاموش' if lang == 'fa' else 'Disabled')
        assert app.state.node_port_swaps.get(rid)['phase'] == 'disabled'
        page.locator('[data-act=swapedit]').click()
        page.locator('[name=swapName]').fill('Edited route')
        page.locator('#submit-dialog').click()
        expect(page.locator('.swap-card')).to_contain_text('Edited route')
        expect(page.locator('.swap-probe')).not_to_contain_text('203.0.113.6')
        # The destination card in the real inbound editor applies state immediately.
        if lang == 'fa': page.locator('.mobile-menu').click()
        page.locator('.nav-btn[data-page=inbounds]').click()
        page.locator('[data-v3-action=edit][data-id="1"]').first.click()
        card = page.locator('.iv3-deploy-card[data-runtime="node:de"]')
        expect(card.locator('[data-act=swapinboundconfigure]')).to_be_visible()
        toggle = card.locator('input[name=swapEnable]')
        toggle_label = card.locator('.iv3-swap-route .iv3-tunnel-toggle')
        expect(toggle_label.locator('i')).to_be_visible()
        toggle_label.click()
        expect(toggle).to_be_checked()
        expect(card.locator('[data-swap-inline-phase]')).to_have_text('فعال' if lang == 'fa' else 'Enabled')
        card.locator('.iv3-deploy-target').click()
        expect(card.locator('input[name=deployNode]')).to_be_checked()
        expect(toggle).to_be_enabled()
        toggle.focus()
        page.keyboard.press('Space')
        expect(toggle).not_to_be_checked()
        expect(card.locator('[data-swap-inline-phase]')).to_have_text('خاموش' if lang == 'fa' else 'Disabled')
        page.locator('.iv3-icon[data-v3-action=close]').click()
        if lang == 'fa': page.locator('.mobile-menu').click()
        page.locator('.nav-btn[data-page=swap]').click()
        page.locator('[data-act=swapdelete]').click()
        expect(page.locator('.swap-card')).to_have_count(0)
        assert not app.state.node_port_swaps.rows()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
    finally:
        context.close(); server.should_exit = True; thread.join(8); listener.close()
        assert not thread.is_alive()

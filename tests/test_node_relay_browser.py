"""Full panel HTTP/Chromium for exit selection; remote delivery is a fixture ACK.
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
def test_owner_saves_disabled_exit_then_enables_and_disables(relay_env, pairing_browser, monkeypatch, lang):
    state, _, _ = relay_env
    store, engine, manager, auth, _ = state
    listener = socket.socket(); listener.bind(('127.0.0.1', 0))
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    engine.config.public_origin = origin; engine.config.secure_cookie = False
    engine.config.panel_path = '/control'
    app = make_app(manager, auth, background=False)
    monkeypatch.setattr(app.state.nodes, 'sync_desired_state', lambda *a: {'desired_state_applied': True, 'queued': False})
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
        page.locator('.nav-btn[data-page=nodes]').click()
        page.locator('[data-act=nv2edit][data-id=nl]').first.click()
        page.locator('[data-act=nv2exit][data-id=nl]').click()
        expect(page.locator('[name=sourceInbound]')).to_be_visible()
        page.locator('[name=sourceInbound]').select_option('1')
        page.locator('[name=exit]').select_option('de|2')
        page.locator('#submit-dialog').click()
        enable = page.locator('[data-act=nv2exittoggle][data-enabled=true]')
        expect(enable).to_be_visible()
        row = app.state.node_relays.get('nl', 1)
        assert not row['enabled'] and row['phase'] == 'disabled'
        enable.click()
        expect(page.locator('#dialog-form')).to_contain_text('فعال' if lang == 'fa' else 'Enabled')
        assert app.state.node_relays.get('nl', 1)['enabled'] == 1
        page.locator('[data-act=nv2exittoggle][data-enabled=false]').click()
        expect(page.locator('#dialog-form')).to_contain_text('خاموش' if lang == 'fa' else 'Disabled')
        assert app.state.node_relays.get('nl', 1)['phase'] == 'disabled'
        assert not errors
    finally:
        context.close(); server.should_exit = True; thread.join(8); listener.close()
        assert not thread.is_alive()

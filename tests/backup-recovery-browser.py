#!/usr/bin/env python3
"""Real local HTTP + Chromium + encrypted archive, never Production services."""
import dataclasses,json,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend')]
import uvicorn
import server as api_server
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store,Actor
from manager import Manager
from backup import restore_backup
from update_bridge import UpdateBrokerClient
from playwright.sync_api import sync_playwright

OUT=ROOT/'qa';OUT.mkdir(exist_ok=True)
report={'status':'running','environment':'isolated fixture, not Production','cases':[]}
password='Backup-Browser-Fixture-123!';archive_password='Temporary-Backup-Passphrase-321!'
with tempfile.TemporaryDirectory(prefix='dark-backup-browser-') as td:
    root=Path(td);data=root/'data';data.mkdir()
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    origin=f'http://127.0.0.1:{port}'
    config_path=root/'config.json'
    config_path.write_text(json.dumps(dataclasses.asdict(Config(public_origin=origin,bind_port=port,
        public_address='fixture.example',xray_binary=str(root/'missing-core'),xray_assets=str(root),
        test_engine=True,core_autostart=False))))
    config_path.chmod(0o600)
    config=Config.load(config_path);store=Store(data/'dark.sqlite3')
    engine=CoreEngine(config,store,root/'runtime');manager=Manager(store,engine);auth=Auth(store,data/'secret.key')
    auth.bootstrap('dark',password);manager.owner_put(Actor('dark','owner',{}),'dark',name='Backup QA',allowed=[])
    api_server.UpdateBrokerClient=lambda **kw:UpdateBrokerClient(path=str(root/'absent-broker.sock'),**kw)
    app=api_server.make_app(manager,auth,background=False)
    with store.transaction() as db:
        db.execute('INSERT INTO restore_domains VALUES(?,?,?,?,?,?,?,?,?)',
            ('legacy.example','','ok','ready','/old/cert.pem','/old/key.pem',50,10,50))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.025)
    assert server.started
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            try:
                for width in (1440,1024,390,320):
                    for lang in ('en','fa'):
                        context=browser.new_context(viewport={'width':width,'height':1000},accept_downloads=True)
                        context.add_init_script('localStorage.setItem("dark_lang",'+json.dumps(lang)+');')
                        page=context.new_page();errors=[];mutations=[]
                        page.on('pageerror',lambda ex:errors.append(str(ex)))
                        page.on('request',lambda req:mutations.append(req.url.split(origin)[-1]) if req.method not in ('GET','HEAD')
                            and '/api/' in req.url and '/api/auth/' not in req.url else None)
                        page.goto(origin,wait_until='networkidle')
                        page.locator('#login-form [name=username]').fill('dark')
                        page.locator('#login-form [name=password]').fill(password)
                        page.locator('#login-form button[type=submit]').click()
                        page.wait_for_selector('.ov4-commandbar')
                        page.locator('.ov4-backup-card [data-act=backupfull]').click()
                        page.locator('#dialog-form [name=backup_passphrase]').wait_for()
                        assert mutations==[],mutations
                        page.evaluate('closeDialog()');page.evaluate("go('backup')")
                        page.wait_for_selector('.ov2-backup [data-act=backupfull]')
                        body=page.locator('.ov2-backup').inner_text()
                        assert ('not encrypted' if lang=='en' else 'رمزگذاری نشده') in body
                        assert ('not a full' if lang=='en' else 'به‌تنهایی') in body
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,lang)
                        page.locator('.ov2-backup [data-act=backupfull]').click()
                        form=page.locator('#dialog-form')
                        form.locator('[name=backup_passphrase]').fill(archive_password)
                        form.locator('[name=backup_repeat]').fill('Different-Password-123!')
                        form.locator('button[type=submit]').click();page.wait_for_timeout(80)
                        assert mutations==[],mutations
                        form.locator('[name=backup_repeat]').fill(archive_password)
                        with page.expect_download() as info:form.locator('button[type=submit]').click()
                        archive=root/f'{width}-{lang}.darkbackup';info.value.save_as(archive)
                        assert archive.is_file() and not archive.read_bytes().startswith(b'PK')
                        restored=root/f'restored-{width}-{lang}'
                        result=restore_backup(archive,restored,archive_password)
                        assert result['dark_restore_recovery']['frontend_rebuild_required'] is True
                        assert not result['live_activation_performed'] and result['sessions_revoked']
                        assert (restored/'data/secret.key').read_bytes()==(data/'secret.key').read_bytes()
                        assert mutations==['/api/backup/full'],mutations
                        page.wait_for_function("()=>document.querySelector('#overlay').style.display==='none'")
                        page.locator('.ov2-backup [data-act=ov2restorehelp]').click()
                        page.locator('#dialog-form .ov2-command').first.wait_for(state='visible')
                        commands=page.locator('#dialog-form .ov2-command').all_text_contents()
                        assert commands==['sudo darkxray backup-verify --archive /root/dark-full.darkbackup',
                            'sudo darkxray restore --archive /root/dark-full.darkbackup --destination /root/dark-restore'],(lang,commands)
                        assert mutations==['/api/backup/full']
                        page.evaluate('closeDialog()')
                        assert not errors,errors
                        if width==390:page.locator('.ov2-backup').screenshot(path=str(OUT/f'backup-recovery-{lang}.png'))
                        report['cases'].append({'width':width,'language':lang,'dashboard_button':True,
                            'encrypted_download_and_restore':True,'mismatch_rejected':True,'overflow':False,
                            'only_write':'requested backup.full','live_services_started':False})
                        context.close()
            finally:browser.close()
        report['status']='passed'
    except Exception as ex:
        report.update(status='failed',error=str(ex));raise
    finally:
        server.should_exit=True;thread.join(timeout=6)
        manager.close();engine.close();store.close()
        (OUT/'backup-recovery-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))

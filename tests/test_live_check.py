import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_live_check_does_not_compete_for_instance_lock(tmp_path):
    data=tmp_path/'data';data.mkdir()
    db=data/'dark.sqlite3'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE core_inbounds(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        c.execute('CREATE TABLE core_clients(email TEXT PRIMARY KEY, body TEXT NOT NULL, inbounds TEXT NOT NULL, up INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0)')
        c.execute('CREATE TABLE core_sections(name TEXT PRIMARY KEY, body TEXT NOT NULL)')
        c.execute("INSERT INTO core_sections(name,body) VALUES('runtime',?)",(json.dumps({'bind_port':2087,'public_address':'vpn.example.test','panel_path':'/','poll_seconds':5,'core_autostart':False}),))
    fake=tmp_path/'xray';shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);os.chmod(fake,0o755)
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'public_origin':'http://127.0.0.1:2087','panel_path':'/','xray_binary':str(fake),'xray_assets':str(tmp_path),'xray_api_port':10085,'public_address':'vpn.example.test','writes_enabled':True,'secure_cookie':False,'poll_seconds':5,'core_autostart':False,'ip_window_seconds':120,'direct_source_verified':False,'protected_ports':[22,2087,10085],'test_engine':True,'bind_host':'127.0.0.1','bind_port':2087,'tls_certificate':'','tls_private_key':'','guard_socket':str(tmp_path/'guard.sock'),'ip_ban_seconds':1800,'ip_exempt_ips':[]}))
    os.chmod(config,0o600)
    lock=(data/'instance.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cp=subprocess.run([sys.executable,str(ROOT/'tools/check.py'),'--config',str(config),'--data',str(data)],capture_output=True,text=True,check=False)
    finally:
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
    assert cp.returncode==0,cp.stderr
    assert 'Database: ok' in cp.stdout and 'Inbounds: 0 Clients: 0' in cp.stdout

#!/usr/bin/env python3
"""Stage 8 integrated Release Candidate migration/backup gate.

Disposable only. Historical sources are extracted from git into temporary
directories; no installed DARK XRAY path or production database is opened.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

from auth import Auth
from backup import create_backup,restore_backup
from core import Config,CoreEngine
from dark_policy import Store
from manager import Manager
from server import make_app

STABLE='c880c3b2f1e9f039e4a18815784ebb501fe58112'
MAIN_BASE='282111a7194c4381316f5edb8af7f6972060b0e1'
STAGE7='e9355bd78aaa882faaf727566b3211328b245d8b'
VERSION='0.9.1-rc1'


def run(args,**kwargs):
    return subprocess.run([str(x) for x in args],cwd=ROOT,check=False,
                          capture_output=True,text=True,**kwargs)


def git(*args):
    cp=run(['git',*args])
    if cp.returncode:
        raise RuntimeError('git '+' '.join(args)+' failed: '+cp.stderr[-500:])
    return cp.stdout.strip()


def require_ancestor(commit,head):
    cp=run(['git','merge-base','--is-ancestor',commit,head])
    if cp.returncode!=0:
        raise AssertionError(f'{commit} is not an ancestor of {head}')


def extract_commit(commit:str,dest:Path):
    cp=subprocess.run(['git','archive','--format=tar',commit],cwd=ROOT,check=False,
                      stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if cp.returncode:
        raise RuntimeError(cp.stderr.decode(errors='replace')[-600:])
    dest.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(cp.stdout),mode='r:') as tf:
        for member in tf.getmembers():
            path=Path(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                raise AssertionError('unsafe historical archive member: '+member.name)
        tf.extractall(dest,filter='data')


def config(root:Path,fake:Path)->Path:
    path=root/'config.json'
    doc={
        'public_origin':'http://127.0.0.1:2087','panel_path':'/',
        'xray_binary':str(fake),'xray_assets':str(root),'xray_api_port':19085,
        'public_address':'127.0.0.1','writes_enabled':True,'secure_cookie':False,
        'poll_seconds':5,'core_autostart':False,'ip_window_seconds':120,
        'direct_source_verified':False,'protected_ports':[22,2087,19085],
        'test_engine':True,'bind_host':'127.0.0.1','bind_port':2087,
        'tls_certificate':'','tls_private_key':'','guard_socket':str(root/'guard.sock'),
        'ip_ban_seconds':1800,'ip_exempt_ips':[],
    }
    path.write_text(json.dumps(doc));path.chmod(0o600);return path


OLD_SEED=r'''
import json,os,sqlite3,sys,time
from pathlib import Path
src=Path(os.environ['DARK_STAGE8_OLD_SOURCE'])
data=Path(os.environ['DARK_STAGE8_OLD_DATA'])
cfg_path=Path(os.environ['DARK_STAGE8_OLD_CONFIG'])
kind=os.environ['DARK_STAGE8_SEED_KIND']
sys.path.insert(0,str(src/'backend'))
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app

data.mkdir(parents=True,exist_ok=True)
store=Store(data/'dark.sqlite3')
cfg=Config.load(cfg_path)
engine=CoreEngine(cfg,store,data/'runtime')
manager=Manager(store,engine)
auth=Auth(store,data/'secret.key')
cred='X'+('a'*20)+'!7Z'
auth.bootstrap('dark',cred)
manager.owner_put(Actor('system','owner',{}),'dark',name='dark',allowed=[])
app=None
try:
    if kind=='stable':
        with store.transaction() as db:
            db.execute("""INSERT OR REPLACE INTO owners(
              id,quota_bytes,volume_credit_bytes,unlimited_credit,max_clients,manual,
              account_disabled,period,credit) VALUES(?,?,?,?,?,?,?,?,?)""",
              ('legacy-reseller',0,987654321,3,9,0,0,0,0))
            db.execute("""INSERT OR REPLACE INTO clients(
              id,owner,limit_ip,quota_bytes,used_bytes,manual,expires_at,
              global_ip_block,global_device_block) VALUES(?,?,?,?,?,?,?,?,?)""",
              ('legacy-client','legacy-reseller',2,123456789,23456789,0,0,0,0))
    elif kind=='main':
        app=make_app(manager,auth,background=False)
        now=time.time()
        with store.transaction() as db:
            db.execute("""INSERT OR REPLACE INTO customer_wallets(
              owner,telegram_id,currency,balance_minor,updated_at) VALUES(?,?,?,?,?)""",
              ('dark',880001,'IRT',345000,now))
            db.execute("""INSERT OR REPLACE INTO telegram_customer_settings(
              owner,currency,referral_reward_minor,support_enabled,updated_at) VALUES(?,?,?,?,?)""",
              ('dark','IRT',12500,1,now))
            db.execute("""INSERT OR REPLACE INTO customer_support_tickets(
              id,owner,telegram_id,username,subject,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              ('stage8-ticket','dark',880001,'stage8','preserve-me','open',now,now))
    else:
        raise RuntimeError('unknown seed kind')
    with store.lock:
        quick=store.db.execute('PRAGMA quick_check').fetchone()[0]
    if quick!='ok':raise RuntimeError('old fixture quick_check failed')
finally:
    runtime=getattr(app.state,'telegram_runtime',None) if app is not None else None
    if runtime:
        try:runtime.close()
        except Exception:pass
    manager.close();engine.close();store.close()
print('OLD_FIXTURE_OK',kind)
'''


def seed_old(source:Path,data:Path,cfg:Path,kind:str):
    env=dict(os.environ)
    env.update({
        'DARK_STAGE8_OLD_SOURCE':str(source),
        'DARK_STAGE8_OLD_DATA':str(data),
        'DARK_STAGE8_OLD_CONFIG':str(cfg),
        'DARK_STAGE8_SEED_KIND':kind,
        'PYTHONPATH':str(source/'backend'),
    })
    cp=subprocess.run([sys.executable,'-c',OLD_SEED],cwd=source,env=env,
                      capture_output=True,text=True,timeout=60)
    if cp.returncode:
        raise AssertionError(f'old fixture {kind} failed: {cp.stdout[-500:]} {cp.stderr[-1200:]}')


def table(store,name):
    with store.lock:
        return bool(store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


def candidate_open(data:Path,cfg_path:Path,kind:str):
    store=Store(data/'dark.sqlite3');cfg=Config.load(cfg_path)
    engine=CoreEngine(cfg,store,data/'runtime');manager=Manager(store,engine)
    auth=Auth(store,data/'secret.key');app=make_app(manager,auth,background=False)
    result={'kind':kind}
    try:
        with store.lock:
            result['quickCheck']=store.db.execute('PRAGMA quick_check').fetchone()[0]
            result['userVersion']=store.db.execute('PRAGMA user_version').fetchone()[0]
            if kind=='stable':
                result['owner']=dict(store.db.execute(
                    "SELECT id,volume_credit_bytes,unlimited_credit,max_clients FROM owners WHERE id='legacy-reseller'").fetchone())
                result['client']=dict(store.db.execute(
                    "SELECT id,owner,limit_ip,quota_bytes,used_bytes FROM clients WHERE id='legacy-client'").fetchone())
            else:
                result['wallet']=dict(store.db.execute(
                    "SELECT owner,telegram_id,currency,balance_minor FROM customer_wallets WHERE owner='dark' AND telegram_id=880001").fetchone())
                result['ticket']=dict(store.db.execute(
                    "SELECT id,owner,telegram_id,subject,status FROM customer_support_tickets WHERE id='stage8-ticket'").fetchone())
                result['customerSettings']=dict(store.db.execute(
                    "SELECT owner,currency,referral_reward_minor,support_enabled FROM telegram_customer_settings WHERE owner='dark'").fetchone())
        result['stage7Tables']=all(table(store,x) for x in (
            'smart_routing_revisions','smart_routing_node_roles','smart_routing_rollouts',
            'smart_routing_rollout_nodes','smart_routing_rollout_events'))
        result['customerTables']=all(table(store,x) for x in (
            'customer_wallets','customer_support_tickets','telegram_customer_settings'))
        assert result['quickCheck']=='ok' and result['userVersion']==3
        if kind=='stable':
            assert result['owner']['volume_credit_bytes']==987654321
            assert result['owner']['unlimited_credit']==3
            assert result['client']['owner']=='legacy-reseller'
            assert result['client']['quota_bytes']==123456789
            assert result['client']['used_bytes']==23456789
        else:
            assert result['wallet']['balance_minor']==345000
            assert result['ticket']['subject']=='preserve-me'
            assert result['customerSettings']['referral_reward_minor']==12500
        assert result['stage7Tables'] and result['customerTables']
        return result,store,engine,manager,app
    except Exception:
        runtime=getattr(app.state,'telegram_runtime',None)
        if runtime:
            try:runtime.close()
            except Exception:pass
        manager.close();engine.close();store.close()
        raise


def close_candidate(store,engine,manager,app):
    runtime=getattr(app.state,'telegram_runtime',None)
    if runtime:
        try:runtime.close()
        except Exception:pass
    manager.close();engine.close();store.close()


def seed_stage7_history(store):
    rid='stage8-preserved-rollout';now=time.time()
    with store.transaction() as db:
        db.execute("""INSERT OR REPLACE INTO smart_routing_rollouts(
          id,revision_id,actor,created_at,state,phase,current_index,observation_seconds,
          control_state,detail,started_at,completed_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (rid,'stage8-revision','dark',now,'completed','complete',4,5,'run',
           'stage8 backup marker',now,now))
        db.execute("""INSERT INTO smart_routing_rollout_events(
          rollout_id,node_id,phase,state,kind,detail,metrics,at)
          VALUES(?,?,?,?,?,?,?,?)""",
          (rid,'node-us','complete','completed','stage8_marker','preserve-event',
           json.dumps({'latencyMs':42.0}),now))
    return rid


def backup_roundtrip(data:Path,cfg:Path,store)->dict:
    rid=seed_stage7_history(store)
    root=data.parent;archive=root/'stage8.darkbackup';dest=root/'restored'
    secret='Stage8-'+('B'*20)+'!9'
    manifest=create_backup(data,cfg,archive,secret)
    verify=restore_backup(archive,dest,secret)
    db_path=dest/'data/dark.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        quick=db.execute('PRAGMA quick_check').fetchone()[0]
        wallet=dict(db.execute(
            "SELECT owner,telegram_id,balance_minor FROM customer_wallets WHERE owner='dark' AND telegram_id=880001").fetchone())
        ticket=dict(db.execute(
            "SELECT id,subject,status FROM customer_support_tickets WHERE id='stage8-ticket'").fetchone())
        rollout=dict(db.execute(
            "SELECT id,state,detail FROM smart_routing_rollouts WHERE id=?",(rid,)).fetchone())
        event=dict(db.execute(
            "SELECT kind,detail,metrics FROM smart_routing_rollout_events WHERE rollout_id=? ORDER BY id DESC LIMIT 1",(rid,)).fetchone())
    assert quick=='ok' and wallet['balance_minor']==345000
    assert ticket['subject']=='preserve-me'
    assert rollout['state']=='completed' and event['kind']=='stage8_marker'
    assert json.loads(event['metrics'])['latencyMs']==42.0
    return {'manifestSchema':manifest.get('schema'),'restore':verify,'quickCheck':quick,
            'wallet':wallet,'ticket':ticket,'rollout':rollout,'event':event}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args();args.report.parent.mkdir(parents=True,exist_ok=True)
    head=git('rev-parse','HEAD')
    report={'schema':1,'stage':'8','version':(ROOT/'VERSION').read_text().strip(),
            'candidateCommit':head,'productionMutation':False,'productionPathsTouched':[],
            'stableBase':STABLE,'productionLineBase':MAIN_BASE,'stage7Base':STAGE7,'passed':False}
    tmp=Path(tempfile.mkdtemp(prefix='dark-stage8-rc.'))
    try:
        assert report['version']==VERSION,report['version']
        for commit in (STABLE,MAIN_BASE,STAGE7):require_ancestor(commit,head)
        report['ancestry']={'stable':True,'main':True,'stage7':True}

        fake=tmp/'fake-xray';shutil.copy2(ROOT/'tests/fixtures/fake_xray.py',fake);fake.chmod(0o755)
        migrations=[]
        main_state=None
        for label,commit,kind in [('stable-v0.9.0',STABLE,'stable'),('production-main-0.9.0',MAIN_BASE,'main')]:
            fixture=tmp/label;source=fixture/'source';data=fixture/'data'
            extract_commit(commit,source);cfg=config(fixture,fake)
            seed_old(source,data,cfg,kind)
            result,store,engine,manager,app=candidate_open(data,cfg,kind)
            result['sourceCommit']=commit;migrations.append(result)
            if kind=='main':
                main_state=(data,cfg,store,engine,manager,app)
            else:
                close_candidate(store,engine,manager,app)
        report['migrations']=migrations

        assert main_state is not None
        data,cfg,store,engine,manager,app=main_state
        report['backupRestore']=backup_roundtrip(data,cfg,store)
        close_candidate(store,engine,manager,app)

        report['passed']=all(x['quickCheck']=='ok' and x['stage7Tables'] and x['customerTables'] for x in migrations)
        report['generatedAt']=time.time()
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True))
        return 0 if report['passed'] else 1
    except Exception as exc:
        report['passed']=False;report['error']=type(exc).__name__+': '+str(exc)[:2000];report['generatedAt']=time.time()
        try:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
        except Exception:pass
        print(json.dumps({'passed':False,'error':report['error']},indent=2))
        raise
    finally:
        shutil.rmtree(tmp,ignore_errors=True)


if __name__=='__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deterministic DARK XRAY application load/SQLite contention gate.

Runs a real local HTTP server backed by a real temporary SQLite database and the
DARK test-engine (no fake HTTP API). It is a capacity regression scenario, not a
production SLA benchmark. PASS means the fixed workload completed correctly with
no HTTP 5xx, lost writes, duplicate finance events or SQLite integrity failure.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app

OWNER=Actor('load-owner','owner',{})
PASSWORD='Temporary-Load-Owner-Password-082'


def port()->int:
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return int(s.getsockname()[1])


def request(client:httpx.Client,method:str,path:str,*,json_body=None,csrf=''):
    headers={'X-Dark-CSRF':csrf} if method not in {'GET','HEAD'} else {}
    r=client.request(method,path,json=json_body,headers=headers)
    if r.status_code>=500:
        raise RuntimeError(f'{method} {path} returned {r.status_code}: {r.text[:400]}')
    return r


def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--clients',type=int,default=1000)
    ap.add_argument('--concurrency',type=int,default=12)
    ap.add_argument('--report',type=Path,default=Path('qa/load-scale.json'))
    a=ap.parse_args()
    if not 500<=a.clients<=5000:raise SystemExit('--clients must be 500..5000')
    if a.clients%500:raise SystemExit('--clients must be a multiple of 500')
    if not 2<=a.concurrency<=32:raise SystemExit('--concurrency must be 2..32')

    report={'scenario':'application-load-sqlite-contention','clients_requested':a.clients,
            'concurrency':a.concurrency,'passed':False,'timings_seconds':{},'counts':{},
            'sqlite':{},'process':{},'errors':[],'production_sla_claimed':False}
    started=time.perf_counter();server=None;worker=None;store=None;manager=None;engine=None
    try:
        with tempfile.TemporaryDirectory(prefix='dark-load-') as tmp_raw:
            tmp=Path(tmp_raw);api_port=port();core_api=port()
            store=Store(tmp/'dark.sqlite3')
            cfg=Config(public_origin=f'http://127.0.0.1:{api_port}',bind_host='127.0.0.1',bind_port=api_port,
                       public_address='load.example.test',xray_binary=str(tmp/'unused-xray'),xray_assets=str(tmp),
                       xray_api_port=core_api,test_engine=True,poll_seconds=3600,core_autostart=False)
            engine=CoreEngine(cfg,store,tmp/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp/'secret.key')
            auth.bootstrap('load-owner',PASSWORD);manager.owner_put(OWNER,'load-owner',name='Load Owner',allowed=[])
            server=uvicorn.Server(uvicorn.Config(make_app(manager,auth,background=False),host='127.0.0.1',port=api_port,
                                                 log_level='error',access_log=False,ws='none'))
            worker=threading.Thread(target=server.run,daemon=True);worker.start()
            for _ in range(100):
                if server.started:break
                time.sleep(.05)
            if not server.started:raise RuntimeError('Local load HTTP server did not start')

            base=f'http://127.0.0.1:{api_port}'
            with httpx.Client(base_url=base,timeout=60,trust_env=False) as client:
                login=client.post('/api/auth/login',json={'username':'load-owner','password':PASSWORD})
                login.raise_for_status();csrf=login.json()['csrf']

                t=time.perf_counter();inbound_ids=[]
                for i in range(4):
                    r=request(client,'POST','/api/inbounds',csrf=csrf,json_body={
                        'remark':f'LOAD {i+1}','listen':'127.0.0.1','port':22000+i,'protocol':'vless','enable':True,
                        'tag':f'load-{i+1}','settings':{'decryption':'none'},
                        'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}})
                    if r.status_code!=200:raise RuntimeError('Inbound create failed: '+r.text[:500])
                    inbound_ids.append(r.json()['id'])
                owner=request(client,'PUT','/api/owners/load-owner',csrf=csrf,json_body={
                    'name':'Load Owner','allowed':inbound_ids,'max_clients':a.clients+100,'quota_bytes':0})
                if owner.status_code!=200:raise RuntimeError('Owner setup failed: '+owner.text[:500])
                report['timings_seconds']['setup']=round(time.perf_counter()-t,3)

                t=time.perf_counter();created=0
                batches=a.clients//500
                for b in range(batches):
                    r=request(client,'POST','/api/clients/bulk-create',csrf=csrf,json_body={
                        'owner':'load-owner','prefix':f'load-{b:02d}-','postfix':'@dark.test','first':1,'quantity':500,
                        'inboundIds':[inbound_ids[0]],'client':{'totalGB':10*1024*1024*1024,'limitIp':0,'limitHwid':0}})
                    if r.status_code!=200:raise RuntimeError('Bulk create failed: '+r.text[:500])
                    body=r.json();created+=body['created']
                    if body['created']!=500 or any('error' in x for x in body['items']):
                        raise RuntimeError(f'Bulk create batch {b} incomplete: {body["created"]}/500')
                report['timings_seconds']['bulk_create']=round(time.perf_counter()-t,3)
                report['counts']['created']=created
                if created!=a.clients:raise RuntimeError(f'Expected {a.clients} created clients, got {created}')

                t=time.perf_counter();rows=request(client,'GET','/api/clients').json()
                report['timings_seconds']['list_all']=round(time.perf_counter()-t,3)
                if len(rows)!=a.clients:raise RuntimeError(f'List returned {len(rows)} clients, expected {a.clients}')
                emails=sorted(x['email'] for x in rows)
                report['counts']['listed']=len(emails)

                # Concurrent read path: details + generated subscription links.
                sample=emails[:200]
                def read_one(item):
                    idx,email=item
                    with httpx.Client(base_url=base,timeout=30,trust_env=False,cookies=client.cookies) as c:
                        path=f'/api/clients/{email}/links' if idx%2 else f'/api/clients/{email}'
                        r=request(c,'GET',path)
                        if r.status_code!=200:return f'{path}:{r.status_code}'
                        return ''
                t=time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
                    read_errors=[x for x in pool.map(read_one,enumerate(sample)) if x]
                report['timings_seconds']['concurrent_reads_200']=round(time.perf_counter()-t,3)
                if read_errors:raise RuntimeError('Concurrent read errors: '+str(read_errors[:5]))

                # Concurrent writes on disjoint clients exercise the Store lock/SQLite transaction path.
                mutate=emails[200:300]
                def patch_one(item):
                    idx,email=item
                    with httpx.Client(base_url=base,timeout=45,trust_env=False,cookies=client.cookies) as c:
                        r=request(c,'PATCH',f'/api/clients/{email}',csrf=csrf,json_body={'client':{'limitHwid':(idx%3)+1}})
                        return '' if r.status_code==202 else f'{email}:{r.status_code}:{r.text[:120]}'
                t=time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
                    write_errors=[x for x in pool.map(patch_one,enumerate(mutate)) if x]
                report['timings_seconds']['concurrent_patches_100']=round(time.perf_counter()-t,3)
                if write_errors:raise RuntimeError('Concurrent patch errors: '+str(write_errors[:5]))

                # Bulk adjustment and multi-inbound attachment at the API maximum batch size.
                bulk=emails[:500]
                request(client,'POST','/api/groups',csrf=csrf,json_body={'owner':'load-owner','name':'LOAD','color':'#123456'})
                t=time.perf_counter();adj=request(client,'POST','/api/clients/bulk-adjust',csrf=csrf,json_body={
                    'emails':bulk,'add_days':1,'group':'LOAD'})
                report['timings_seconds']['bulk_adjust_500']=round(time.perf_counter()-t,3)
                if adj.status_code!=200 or adj.json()['changed']!=500:
                    raise RuntimeError('Bulk adjust did not change 500 clients: '+adj.text[:500])
                t=time.perf_counter();attach=request(client,'POST','/api/clients/bulk-inbounds',csrf=csrf,json_body={
                    'emails':bulk,'inboundIds':[inbound_ids[1]],'mode':'attach'})
                report['timings_seconds']['bulk_attach_500']=round(time.perf_counter()-t,3)
                if attach.status_code!=200 or attach.json()['changed']!=500:
                    raise RuntimeError('Bulk inbound attach did not change 500 clients: '+attach.text[:500])

                # Contended finance writes plus idempotent concurrent retries.
                events=[f'load-credit-{i:04d}-event' for i in range(64)]
                def credit(event):
                    with httpx.Client(base_url=base,timeout=30,trust_env=False,cookies=client.cookies) as c:
                        r=request(c,'POST','/api/owners/load-owner/credit',csrf=csrf,json_body={'amount':1,'event_id':event})
                        return (r.status_code,r.json().get('recorded'))
                t=time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
                    first=list(pool.map(credit,events))
                    second=list(pool.map(credit,events))
                report['timings_seconds']['finance_64_plus_retries']=round(time.perf_counter()-t,3)
                if any(code!=200 or recorded is not True for code,recorded in first):raise RuntimeError('Initial finance events failed')
                if any(code!=200 or recorded is not False for code,recorded in second):raise RuntimeError('Finance retries were not idempotent')

            with store.lock:
                quick=store.db.execute('PRAGMA quick_check').fetchone()[0]
                journal=store.db.execute('PRAGMA journal_mode').fetchone()[0]
                client_count=store.db.execute('SELECT COUNT(*) FROM core_clients').fetchone()[0]
                ledger_count=store.db.execute("SELECT COUNT(*) FROM money_ledger WHERE event_id LIKE 'load-credit-%'").fetchone()[0]
                credit=store.db.execute("SELECT credit FROM owners WHERE id='load-owner'").fetchone()['credit']
                attached=store.db.execute("SELECT COUNT(*) FROM core_clients WHERE inbounds LIKE '%2%'").fetchone()[0]
            report['sqlite']={'quick_check':quick,'journal_mode':journal,'database_bytes':(tmp/'dark.sqlite3').stat().st_size}
            report['counts'].update({'database_clients':client_count,'finance_events':ledger_count,'owner_credit':credit,
                                     'bulk_attached_at_least':attached})
            if quick!='ok':raise RuntimeError('SQLite quick_check failed: '+str(quick))
            if client_count!=a.clients:raise RuntimeError('Client count changed during contention scenario')
            if ledger_count!=64 or credit!=64:raise RuntimeError(f'Finance contention lost/duplicated writes: ledger={ledger_count} credit={credit}')
            for email in mutate[:20]:
                detail=engine.client_detail(email)
                if int(detail['client'].get('limitHwid',0))<1:raise RuntimeError('Concurrent client patch was lost: '+email)
            report['process']={'rss_bytes':psutil.Process().memory_info().rss,'cpu_count':psutil.cpu_count()}
            report['timings_seconds']['total']=round(time.perf_counter()-started,3)
            report['passed']=True
    except Exception as ex:
        report['errors'].append(type(ex).__name__+': '+str(ex)[:1500])
        report['timings_seconds']['total']=round(time.perf_counter()-started,3)
    finally:
        if server is not None:server.should_exit=True
        if worker is not None:worker.join(timeout=15)
        if manager is not None:
            try:manager.close()
            except Exception:pass
        if engine is not None:
            try:engine.close()
            except Exception:pass
        if store is not None:
            try:store.close()
            except Exception:pass
        a.report.parent.mkdir(parents=True,exist_ok=True)
        a.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Stage 7.5 disposable Smart Routing soak and stability acceptance.

Runs repeated staged rollouts against one real Hub process and four real TLS Node
Agent processes on loopback. No production paths, services, or databases are used.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from stage7_real_multinode_acceptance import (  # noqa:E402
    FAKE,NODES,PASSWORD,Process,api,config,free_port,login,make_ca,make_cert,
    review_and_safety,start_rollout,wait_https,wait_rollout,write_json,
)
RUNNER=ROOT/'tests/stage7_real_process.py'


def proc_metrics(pid:int)->dict:
    p=psutil.Process(pid)
    return {'pid':pid,'rss':p.memory_info().rss,'threads':p.num_threads()}


class SoakLab:
    def __init__(self,root:Path):
        self.root=root;self.processes={};self.hub=None;self.client=None;self.hub_restarts=0
        self.ca_key,self.ca_cert,self.ca=make_ca(root)
        self.fake=root/'fake-xray';shutil.copy2(FAKE,self.fake);self.fake.chmod(0o755)
        self.node_meta={}
        for idx,(node_id,region,host) in enumerate(NODES,1):
            nroot=root/node_id;nroot.mkdir()
            cert,key=make_cert(nroot,self.ca_key,self.ca_cert,node_id,dns=host,ip='127.0.0.1')
            port=free_port();api_port=free_port();token='dkn_'+(chr(64+idx)*60)
            data=nroot/'data';data.mkdir()
            token_file=data/'token';token_file.write_text(token+'\n');token_file.chmod(0o600)
            node_id_file=data/'node-id';node_id_file.write_text(node_id+'\n');node_id_file.chmod(0o600)
            control=nroot/'warp.json';write_json(control,{'mode':'healthy'})
            cfg=nroot/'config.json';write_json(cfg,config(
                f'https://{host}:{port}',host,port,cert,key,self.fake,api_port,nroot/'guard.sock'))
            self.node_meta[node_id]={'id':node_id,'region':region,'host':host,'port':port,'token':token,
                'root':nroot,'config':cfg,'data':data,'token_file':token_file,'node_id_file':node_id_file,
                'control':control,'origin':f'https://{host}:{port}'}

        hroot=root/'hub';hroot.mkdir();self.hroot=hroot;self.hdata=hroot/'data';self.hdata.mkdir()
        hcert,hkey=make_cert(hroot,self.ca_key,self.ca_cert,'hub',ip='127.0.0.1')
        self.hport=free_port();hapi=free_port();self.hcfg=hroot/'config.json'
        write_json(self.hcfg,config(f'https://127.0.0.1:{self.hport}','127.0.0.1',self.hport,
                                    hcert,hkey,self.fake,hapi,hroot/'guard.sock'))
        self.hosts=hroot/'hosts.json';write_json(self.hosts,[x[2] for x in NODES])

    @property
    def origin(self):return f'https://127.0.0.1:{self.hport}'

    def start_node(self,node_id:str):
        m=self.node_meta[node_id]
        p=Process([sys.executable,RUNNER,'node','--config',m['config'],'--data',m['data'],
                   '--token-file',m['token_file'],'--node-id-file',m['node_id_file'],
                   '--port',m['port'],'--warp-control',m['control']],m['root']/'agent.log')
        self.processes[node_id]=p
        r=wait_https(f"https://127.0.0.1:{m['port']}/node/api/health",self.ca,
                     {'Authorization':'Bearer '+m['token'],'Host':m['host']+':'+str(m['port'])})
        assert r.status_code==200 and r.json()['service']=='DARK XRAY NODE',r.text
        return p

    def start_hub(self):
        self.hub=Process([sys.executable,RUNNER,'hub','--config',self.hcfg,'--data',self.hdata,
                          '--port',self.hport,'--ca',self.ca,'--hosts',self.hosts],self.hroot/'hub.log')
        wait_https(self.origin+'/health',self.ca)
        return self.hub

    def reconnect(self):
        if self.client is not None:
            try:self.client.close()
            except Exception:pass
        self.client=login(self.origin,self.ca)
        return self.client

    def restart_hub(self):
        old=self.hub.pid
        if self.client is not None:
            try:self.client.close()
            except Exception:pass
            self.client=None
        self.hub.stop();self.start_hub();self.hub_restarts+=1;self.reconnect()
        assert self.hub.pid!=old
        return old,self.hub.pid

    def initialize(self):
        for node_id,_,_ in NODES:self.start_node(node_id)
        init=subprocess.run([sys.executable,ROOT/'backend/server.py','--config',self.hcfg,'--data',self.hdata,'init',
                             '--username','dark','--password-stdin'],cwd=ROOT,input=PASSWORD+'\n'+PASSWORD+'\n',
                            text=True,capture_output=True,timeout=20)
        assert init.returncode==0,init.stdout+init.stderr
        self.start_hub();self.reconnect()
        outbounds=[
            {'tag':'direct','protocol':'freedom','settings':{}},
            {'tag':'block','protocol':'blackhole','settings':{}},
            {'tag':'warp-us','protocol':'wireguard','settings':{'secretKey':'fixture-us',
                'address':['172.16.10.2/32'],'peers':[{'publicKey':'peer-us','endpoint':'162.159.192.1:2408'}]}},
            {'tag':'warp-de','protocol':'wireguard','settings':{'secretKey':'fixture-de',
                'address':['172.16.20.2/32'],'peers':[{'publicKey':'peer-de','endpoint':'162.159.192.2:2408'}]}},
        ]
        api(self.client,'PUT','/api/settings/outbounds',json={'value':outbounds})
        for node_id,region,_ in NODES:
            m=self.node_meta[node_id]
            api(self.client,'POST','/api/nodes',json={'id':node_id,'name':region,'origin':m['origin'],
                'token':m['token'],'enabled':True,'dataAddress':'','priority':100,'failoverEnabled':True,'inboundIds':[]})
            api(self.client,'POST',f'/api/nodes/{node_id}/probe')
            api(self.client,'POST',f'/api/nodes/{node_id}/sync')
            health=api(self.client,'POST',f'/api/nodes/{node_id}/probe')
            assert health['health']['core']['state']=='running',health

    def set_warp(self,node_id,mode):
        write_json(self.node_meta[node_id]['control'],{'mode':mode})

    def baseline(self):
        return api(self.client,'GET','/api/settings/routing')['value']

    def review(self):
        return review_and_safety(self.client)

    def wait_terminal(self,rid,timeout=35):
        return wait_rollout(self.client,rid,lambda d:d['state']!='running',timeout=timeout)

    def current_metrics(self):
        data={'hub':proc_metrics(self.hub.pid)}
        for node_id,p in self.processes.items():data[node_id]=proc_metrics(p.pid)
        return data

    def recover_node(self,node_id):
        self.start_node(node_id)
        api(self.client,'POST',f'/api/nodes/{node_id}/probe')
        api(self.client,'POST',f'/api/nodes/{node_id}/sync')
        health=api(self.client,'POST',f'/api/nodes/{node_id}/probe')
        assert health['health']['core']['state']=='running',health

    def close(self):
        if self.client is not None:
            try:self.client.close()
            except Exception:pass
        if self.hub is not None:
            try:self.hub.stop()
            except Exception:pass
        for p in list(self.processes.values()):
            try:p.stop()
            except Exception:pass


def sums(metrics):
    return {'rss':sum(x['rss'] for x in metrics.values()),
            'threads':sum(x['threads'] for x in metrics.values())}


def db_consistency(db_path:Path,cycles:int)->dict:
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        quick=db.execute('PRAGMA quick_check').fetchone()[0]
        total=db.execute('SELECT COUNT(*) FROM smart_routing_rollouts').fetchone()[0]
        running=db.execute("SELECT COUNT(*) FROM smart_routing_rollouts WHERE state='running'").fetchone()[0]
        transient=db.execute("""SELECT COUNT(*) FROM smart_routing_rollout_nodes
          WHERE state IN ('applying','verifying','healthy','rolling_back')""").fetchone()[0]
        duplicate_nodes=db.execute("""SELECT COUNT(*) FROM (
          SELECT rollout_id,node_id,COUNT(*) c FROM smart_routing_rollout_nodes
          GROUP BY rollout_id,node_id HAVING c>1)""").fetchone()[0]
        bad_control=db.execute("""SELECT COUNT(*) FROM smart_routing_rollouts
          WHERE state!='running' AND control_state!='run'""").fetchone()[0]
        event_count=db.execute('SELECT COUNT(*) FROM smart_routing_rollout_events').fetchone()[0]
        state_counts={r['state']:r['c'] for r in db.execute(
            'SELECT state,COUNT(*) c FROM smart_routing_rollouts GROUP BY state')}
        start_events=db.execute("SELECT COUNT(*) FROM smart_routing_rollout_events WHERE kind='started'").fetchone()[0]
    return {'quickCheck':quick,'rollouts':total,'running':running,'transientNodeStates':transient,
            'duplicateNodeRows':duplicate_nodes,'terminalBadControl':bad_control,'events':event_count,
            'stateCounts':state_counts,'startEvents':start_events,
            'consistent':quick=='ok' and total==cycles and running==0 and transient==0 and
                         duplicate_nodes==0 and bad_control==0 and start_events==cycles}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--cycles',type=int,default=50)
    ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args()
    if not 5<=args.cycles<=100:raise SystemExit('cycles must be 5..100')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='dark-stage75.'))
    evidence={'schema':1,'stage':'7.5','name':'Smart Routing soak and stability',
              'productionMutation':False,'productionPathsTouched':[],'cyclesRequested':args.cycles,
              'passed':False,'cycleResults':[],'failuresInjected':0,'rollbacksExpected':0,
              'rollbacksImmediateSuccess':0,'eventualNodeRecoveries':0}
    lab=None
    try:
        lab=SoakLab(root);lab.initialize()
        initial=lab.current_metrics();evidence['initialMetrics']=initial;initial_sum=sums(initial)
        peak_rss=initial_sum['rss'];peak_threads=initial_sum['threads']
        type_counts={}
        for i in range(args.cycles):
            mode=['hub_restart','pause_resume','abort','warp_degrade','node_flap'][i%5]
            type_counts[mode]=type_counts.get(mode,0)+1
            result={'cycle':i+1,'mode':mode,'passed':False}
            lab.set_warp('node-us','healthy');lab.set_warp('node-de','healthy')
            baseline=lab.baseline();revision=lab.review()
            if mode=='hub_restart':
                rid=start_rollout(lab.client,revision,1)
                wait_rollout(lab.client,rid,lambda d:any(x['state']=='verifying' for x in d['items']))
                api(lab.client,'POST',f'/api/smart-routing/rollout/{rid}/pause',
                    json={'confirmation':'PAUSE STAGED ROLLOUT'})
                wait_rollout(lab.client,rid,lambda d:d['controlState']=='paused')
                old,new=lab.restart_hub()
                persisted=api(lab.client,'GET',f'/api/smart-routing/rollout/{rid}')
                assert persisted['state']=='running' and persisted['controlState']=='paused',persisted
                api(lab.client,'POST',f'/api/smart-routing/rollout/{rid}/resume',
                    json={'confirmation':'RESUME STAGED ROLLOUT'})
                done=lab.wait_terminal(rid,40);assert done['state']=='completed',done
                result.update(state=done['state'],oldHubPid=old,newHubPid=new)

            elif mode=='pause_resume':
                rid=start_rollout(lab.client,revision,1)
                wait_rollout(lab.client,rid,lambda d:any(x['state']=='verifying' for x in d['items']))
                api(lab.client,'POST',f'/api/smart-routing/rollout/{rid}/pause',
                    json={'confirmation':'PAUSE STAGED ROLLOUT'})
                paused=wait_rollout(lab.client,rid,lambda d:d['controlState']=='paused')
                progress=paused['progressPercent'];time.sleep(.3)
                still=api(lab.client,'GET',f'/api/smart-routing/rollout/{rid}')
                assert still['controlState']=='paused' and still['progressPercent']==progress
                api(lab.client,'POST',f'/api/smart-routing/rollout/{rid}/resume',
                    json={'confirmation':'RESUME STAGED ROLLOUT'})
                done=lab.wait_terminal(rid,35);assert done['state']=='completed',done
                result.update(state=done['state'])

            elif mode=='abort':
                evidence['failuresInjected']+=1;evidence['rollbacksExpected']+=1
                rid=start_rollout(lab.client,revision,1)
                target='node-us' if (i//5)%2==0 else 'node-de'
                wait_rollout(lab.client,rid,lambda d:any(
                    x['node_id']==target and x['state']=='verifying' for x in d['items']),timeout=20)
                api(lab.client,'POST',f'/api/smart-routing/rollout/{rid}/abort',
                    json={'confirmation':'ABORT STAGED ROLLOUT'})
                done=lab.wait_terminal(rid,20);assert done['state']=='aborted',done
                assert lab.baseline()==baseline
                assert any(x['kind']=='rollback_complete' for x in done['timeline'])
                evidence['rollbacksImmediateSuccess']+=1
                result.update(state=done['state'],abortTarget=target)

            elif mode=='warp_degrade':
                evidence['failuresInjected']+=1;evidence['rollbacksExpected']+=1
                lab.set_warp('node-us','degraded')
                rid=start_rollout(lab.client,revision,1)
                done=lab.wait_terminal(rid,20);assert done['state']=='rolled_back',done
                assert lab.baseline()==baseline
                assert any(x['kind']=='warp_probe' and x['metrics'].get('passed') is False for x in done['timeline'])
                evidence['rollbacksImmediateSuccess']+=1
                lab.set_warp('node-us','healthy')
                result.update(state=done['state'])

            else:
                evidence['failuresInjected']+=1;evidence['rollbacksExpected']+=1
                target='node-us' if (i//5)%2==0 else 'node-de'
                rid=start_rollout(lab.client,revision,1)
                wait_rollout(lab.client,rid,lambda d:any(
                    x['node_id']==target and x['state']=='verifying' for x in d['items']),timeout=20)
                old=lab.processes[target].pid;lab.processes[target].stop()
                done=lab.wait_terminal(rid,20);assert done['state']=='failed',done
                assert lab.baseline()==baseline
                lab.recover_node(target);new=lab.processes[target].pid
                assert new!=old;evidence['eventualNodeRecoveries']+=1
                result.update(state=done['state'],flapTarget=target,oldPid=old,newPid=new)

            result['passed']=True
            evidence['cycleResults'].append(result)
            now=lab.current_metrics();total=sums(now)
            peak_rss=max(peak_rss,total['rss']);peak_threads=max(peak_threads,total['threads'])
            if (i+1)%10==0:
                print('SOAK_PROGRESS',i+1,args.cycles,json.dumps({'rss':total['rss'],'threads':total['threads']}),
                      flush=True)

        time.sleep(.5)
        final=lab.current_metrics();final_sum=sums(final)
        evidence['finalMetrics']=final;evidence['typeCounts']=type_counts
        evidence['metricsSummary']={
            'initialRss':initial_sum['rss'],'finalRss':final_sum['rss'],'rssDelta':final_sum['rss']-initial_sum['rss'],
            'peakRss':peak_rss,'initialThreads':initial_sum['threads'],'finalThreads':final_sum['threads'],
            'threadDelta':final_sum['threads']-initial_sum['threads'],'peakThreads':peak_threads,
            'hubRestarts':lab.hub_restarts,
        }
        db_path=lab.hdata/'dark.sqlite3'
        evidence['database']=db_consistency(db_path,args.cycles)
        evidence['rollbackImmediateSuccessRate']=(
            evidence['rollbacksImmediateSuccess']/evidence['rollbacksExpected'] if evidence['rollbacksExpected'] else 1.0)
        evidence['rollbackEventualRecoveryRate']=(
            (evidence['rollbacksImmediateSuccess']+evidence['eventualNodeRecoveries'])/evidence['rollbacksExpected']
            if evidence['rollbacksExpected'] else 1.0)
        # Bounded-growth gates: detect obvious thread/process/state leaks while allowing Python/SQLite caches.
        evidence['stabilityGates']={
            'rssGrowthWithin128MiB':final_sum['rss']-initial_sum['rss'] <= 128*1024*1024,
            'threadGrowthWithin8':final_sum['threads']-initial_sum['threads'] <= 8,
            'sqliteConsistent':evidence['database']['consistent'],
            'allCyclesPassed':len(evidence['cycleResults'])==args.cycles and all(x['passed'] for x in evidence['cycleResults']),
            'noStuckRollout':evidence['database']['running']==0,
            'noTransientNodeState':evidence['database']['transientNodeStates']==0,
            'eventualRecovery100Percent':evidence['rollbackEventualRecoveryRate']==1.0,
        }
        evidence['passed']=all(evidence['stabilityGates'].values())
        evidence['generatedAt']=time.time()
        args.report.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
        print(json.dumps({k:evidence[k] for k in ('stage','passed','cyclesRequested','typeCounts','metricsSummary',
                                                   'database','rollbackImmediateSuccessRate',
                                                   'rollbackEventualRecoveryRate','stabilityGates')},
                         indent=2,sort_keys=True))
        return 0 if evidence['passed'] else 1
    except Exception as exc:
        evidence['passed']=False;evidence['generatedAt']=time.time()
        evidence['error']=type(exc).__name__+': '+str(exc)[:2000]
        tails={}
        for log in root.rglob('*.log'):
            try:tails[str(log.relative_to(root))]=log.read_text(errors='replace')[-2500:]
            except Exception:pass
        evidence['diagnosticLogTails']=tails
        try:args.report.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
        except Exception:pass
        print(json.dumps({'passed':False,'error':evidence['error']},indent=2))
        raise
    finally:
        if lab is not None:lab.close()
        shutil.rmtree(root,ignore_errors=True)


if __name__=='__main__':
    raise SystemExit(main())

"""Minimal DARK XRAY Node Agent.

This runtime intentionally exposes no panel UI, owner login, reseller/finance
surface, or local management API. The Hub is authoritative and sends a
versioned desired state over authenticated HTTPS.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import argparse
import contextlib
import hmac
import json
import os
import threading
import time

from fastapi import Depends,FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse

from core import Config,CoreEngine,CoreError
from dark_policy import Store,PolicyError
from node_runtime import NodeRuntime

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip()


class AgentToken:
    def __init__(self,path:Path):
        self.path=Path(path)
        if self.path.is_symlink() or not self.path.is_file():raise PolicyError('Node token file is missing or unsafe')
        if self.path.stat().st_size>4096:raise PolicyError('Node token file is too large')
        token=self.path.read_text(encoding='utf-8').strip()
        if not token.startswith('dkn_') or not 40<=len(token)<=256:raise PolicyError('Invalid node token')
        self.token=token
        import hashlib
        self.scope=hashlib.sha256(token.encode()).hexdigest()[:24]

    def require(self,request:Request)->str:
        header=request.headers.get('authorization','')
        if not header.startswith('Bearer '):raise HTTPException(401,'DARK node token required')
        value=header[7:]
        if not hmac.compare_digest(value,self.token):raise HTTPException(401,'Invalid DARK node token')
        return self.scope

    def rotate(self,value:str):
        if not isinstance(value,str) or not value.startswith('dkn_') or not 40<=len(value)<=256:
            raise PolicyError('Invalid replacement node token')
        temp=self.path.with_name('.token.rotate.'+str(os.getpid()))
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as out:
                out.write(value+'\n');out.flush();os.fsync(out.fileno())
            os.replace(temp,self.path)
        finally:
            try:temp.unlink(missing_ok=True)
            except OSError:pass
        self.token=value


class EngineLoop:
    def __init__(self,engine:CoreEngine,interval:float):
        self.engine=engine;self.interval=max(1.0,min(60.0,float(interval)));self.stop=threading.Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        def run():
            while not self.stop.wait(self.interval):
                try:self.engine.flush()
                except Exception:pass
        self.thread=threading.Thread(target=run,name='dark-node-engine',daemon=True);self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=6)
        self.thread=None


def make_agent_app(engine:CoreEngine,store:Store,token:AgentToken,*,background:bool=True)->FastAPI:
    runtime=NodeRuntime(store,engine,token.scope);loop=EngineLoop(engine,engine.config.poll_seconds)
    public=urlsplit(engine.config.public_origin)
    @contextlib.asynccontextmanager
    async def lifespan(app):
        if background:
            if engine.config.core_autostart:
                try:engine.command('start')
                except Exception:pass
            loop.start()
        yield
        loop.close();engine.close()

    app=FastAPI(title='DARK XRAY NODE',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.engine=engine;app.state.store=store;app.state.runtime=runtime

    @app.middleware('http')
    async def security(request:Request,call_next):
        if request.headers.get('host','').lower()!=public.netloc.lower():
            return JSONResponse({'detail':'Unexpected Host'},400)
        if not request.scope.get('path','/').startswith('/node/api/'):
            return JSONResponse({'detail':'Not Found'},404)
        if request.method not in ('GET','HEAD','OPTIONS'):
            declared=request.headers.get('content-length')
            if declared and (not declared.isdigit() or int(declared)>8*1024*1024):
                return JSONResponse({'detail':'Request too large'},413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'none'; frame-ancestors 'none'"
        response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    auth=token.require

    @app.get('/node/api/health')
    def health(_scope:str=Depends(auth)):
        system=engine.system();state=runtime.status();core=engine.runtime_state()
        with store.lock:
            assigned=store.db.execute('SELECT COUNT(*) FROM node_runtime_inbounds WHERE scope=?',(token.scope,)).fetchone()[0]
            clients=store.db.execute('SELECT COUNT(*) FROM node_runtime_clients WHERE scope=?',(token.scope,)).fetchone()[0]
        source={}
        source_path=engine.runtime.parent/'installed-source.json'
        try:
            if source_path.is_file() and not source_path.is_symlink() and source_path.stat().st_size<65536:
                raw=json.loads(source_path.read_text(encoding='utf-8'))
                if isinstance(raw,dict):source={k:raw.get(k) for k in ('commit','version','ref','role')}
        except (OSError,ValueError):source={}
        return {'service':'DARK XRAY NODE','agent_only':True,'version':VERSION,'installed_source':source,
                'core':{'state':core['state'],'version':core['version'],'dirty':core['dirty'],'last_error':core['last_error']},
                'system':{'cpu':system['cpu'],'memory_percent':100*system['mem']['current']/max(1,system['mem']['total']),
                          'disk_percent':100*system['disk']['current']/max(1,system['disk']['total']),'uptime':system['uptime']},
                'inbounds':int(assigned),'managed_clients':int(clients),'writes_enabled':engine.config.writes_enabled,
                'desired_state':state,'direct_source_verified':bool(engine.config.direct_source_verified)}

    @app.get('/node/api/v1/state')
    def state(_scope:str=Depends(auth)):
        return {'service':'DARK XRAY NODE',**runtime.status(),'core':engine.runtime_state()}

    @app.post('/node/api/v1/state/apply')
    def apply_state(body:dict,_scope:str=Depends(auth)):
        try:result=runtime.apply(body)
        except (PolicyError,CoreError,ValueError) as ex:raise HTTPException(422,str(ex))
        return {'service':'DARK XRAY NODE',**result}

    @app.get('/node/api/inbounds')
    def inbounds(_scope:str=Depends(auth)):
        keys={'id','remark','protocol','port','listen','enable','tag'};out=[]
        for row in engine.inbounds():
            item={k:v for k,v in row.items() if k in keys}
            stream=row.get('streamSettings',{}) if isinstance(row.get('streamSettings'),dict) else {}
            item['network']=stream.get('network','tcp');item['security']=stream.get('security','none')
            out.append(item)
        return out

    @app.get('/node/api/mirrors/traffic')
    def traffic(_scope:str=Depends(auth)):
        rows=engine.clients();items=[]
        for row in rows:
            source=runtime.source_for_mirror(str(row.get('email','')))
            if not source:continue
            up,down=CoreEngine.counters(row);items.append({'sourceEmail':source,'up':up,'down':down})
        return {'items':items,'capturedAt':time.time()}

    @app.post('/node/api/mirrors/traffic/reset')
    def traffic_reset(body:dict,_scope:str=Depends(auth)):
        source=str(body.get('sourceEmail') or '');reset_id=str(body.get('resetId') or '')
        if not source or not 8<=len(reset_id)<=128:raise HTTPException(400,'Invalid traffic reset request')
        try:cached=runtime.reset_result(reset_id,source)
        except PolicyError as ex:raise HTTPException(409,str(ex))
        if cached:return cached
        mirror=runtime.mirror_for_source(source)
        if not mirror:raise HTTPException(404,'Mirrored client is not present')
        result=engine.reset(mirror)
        if not result:raise HTTPException(404,'Mirrored traffic state is missing')
        up,down=CoreEngine.counters(result)
        try:return runtime.remember_reset(reset_id,source,up,down)
        except PolicyError as ex:raise HTTPException(409,str(ex))

    @app.get('/node/api/mirrors/security')
    def security_state(_scope:str=Depends(auth)):
        engine.read_ip_log();items=[]
        with store.lock:
            mappings=[dict(r) for r in store.db.execute(
                'SELECT source_email,mirror_email FROM node_runtime_clients WHERE scope=? ORDER BY source_email',(token.scope,))]
            for mapping in mappings:
                ips=[{'ip':r['ip'],'firstSeen':float(r['first_seen']),'lastSeen':float(r['last_seen'])}
                     for r in store.db.execute('SELECT ip,first_seen,last_seen FROM observations WHERE client_id=? ORDER BY last_seen DESC',
                                               (mapping['mirror_email'],))]
                devices=[{'digest':r['digest'],'deviceOs':r['device_os'],'model':r['model'],
                          'firstSeen':float(r['first_seen']),'lastSeen':float(r['last_seen'])}
                         for r in store.db.execute('SELECT digest,device_os,model,first_seen,last_seen FROM core_devices WHERE email=? ORDER BY last_seen DESC',
                                                   (mapping['mirror_email'],))]
                items.append({'sourceEmail':mapping['source_email'],'ips':ips,'devices':devices})
        return {'sourceVerified':bool(engine.config.direct_source_verified and not engine.ip_error),
                'items':items,'capturedAt':time.time(),'guard':engine.ip_status()}

    @app.post('/node/api/mirrors/security/clear')
    def security_clear(body:dict,_scope:str=Depends(auth)):
        source=str(body.get('sourceEmail') or '');kind=str(body.get('kind') or '')
        if kind not in {'ips','devices','all'}:raise HTTPException(400,'Invalid security clear kind')
        mirror=runtime.mirror_for_source(source)
        if not mirror:raise HTTPException(404,'Mirrored client is not present')
        cleared_ips=cleared_devices=0
        with store.transaction() as db:
            if kind in {'ips','all'}:
                cur=db.execute('DELETE FROM observations WHERE client_id=?',(mirror,));cleared_ips=max(0,cur.rowcount)
            if kind in {'devices','all'}:
                cur=db.execute('DELETE FROM core_devices WHERE email=?',(mirror,));cleared_devices=max(0,cur.rowcount)
        return {'sourceEmail':source,'kind':kind,'ips':cleared_ips,'devices':cleared_devices}

    @app.post('/node/api/v1/token/rotate')
    def rotate_token(body:dict,_scope:str=Depends(auth)):
        value=body.get('token') if isinstance(body,dict) else None
        try:token.rotate(value)
        except PolicyError as ex:raise HTTPException(422,str(ex))
        return {'service':'DARK XRAY NODE','rotated':True}

    @app.post('/node/api/core/{action}')
    def core_action(action:str,_scope:str=Depends(auth)):
        if action not in {'validate','restart','start','stop'}:raise HTTPException(404,'Unknown node core action')
        try:return {'engine':engine.command(action),'node_agent':True}
        except CoreError as ex:raise HTTPException(getattr(ex,'status',422),str(ex))

    @app.get('/node/api/logs/{kind}')
    def logs(kind:str,limit:int=300,_scope:str=Depends(auth)):
        if kind not in {'process','error','access'}:raise HTTPException(404,'Unknown log kind')
        limit=max(1,min(1000,int(limit)))
        path=engine.runtime/(kind+'.log')
        if path.is_symlink():raise HTTPException(409,'Unsafe log path')
        try:
            if not path.is_file():return {'kind':kind,'lines':[]}
            text=path.read_text(encoding='utf-8',errors='replace')[-512*1024:]
        except OSError as ex:raise HTTPException(503,type(ex).__name__)
        return {'kind':kind,'lines':[x[-2000:] for x in text.splitlines()[-limit:]]}

    return app


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path('/etc/dark-xray-node/config.json'))
    p.add_argument('--data',type=Path,default=Path('/var/lib/dark-xray-node'))
    p.add_argument('--token-file',type=Path,default=Path('/var/lib/dark-xray-node/token'))
    p.add_argument('--host',default=None);p.add_argument('--port',type=int,default=None)
    a=p.parse_args()
    a.data.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    lock=(a.data/'instance.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another DARK Node Agent is already using this data directory')
    store=Store(a.data/'node.sqlite3');config=Config.load(a.config)
    engine=CoreEngine(config,store,a.data/'runtime');token=AgentToken(a.token_file)
    host=a.host or config.bind_host;port=a.port or config.bind_port
    if host in ('127.0.0.1','::1') or not config.tls_certificate or not config.tls_private_key:
        raise SystemExit('Node Agent requires a public HTTPS listener with a real certificate/key')
    try:
        import uvicorn
        uvicorn.run(make_agent_app(engine,store,token),host=host,port=port,proxy_headers=False,access_log=False,
                    workers=1,ws='none',ssl_certfile=config.tls_certificate,ssl_keyfile=config.tls_private_key)
    except (PolicyError,CoreError,ValueError) as ex:raise SystemExit(str(ex))
    finally:
        try:engine.close()
        except Exception:pass
        store.close();lock.close()


if __name__=='__main__':main()

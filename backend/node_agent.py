"""Minimal DARK XRAY Node Agent.

This runtime intentionally exposes no panel UI, owner login, reseller/finance
surface, or local management API. The Hub is authoritative and sends a
versioned desired state over authenticated HTTPS.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import argparse
import asyncio
import contextlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
import time

from fastapi import Depends,FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse

from core import Config,CoreEngine,CoreError
from dark_policy import Store,PolicyError
from node_runtime import NodeRuntime
from update_bridge import UpdateBrokerClient,UpdateBrokerError

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip()


class AgentToken:
    def __init__(self,path:Path):
        self.lock=threading.RLock()
        self.path=Path(path)
        if self.path.is_symlink() or not self.path.is_file():raise PolicyError('Node token file is missing or unsafe')
        if self.path.stat().st_size>4096:raise PolicyError('Node token file is too large')
        token=self.path.read_text(encoding='utf-8').strip()
        if not token.startswith('dkn_') or not 40<=len(token)<=256:raise PolicyError('Invalid node token')
        self.token=token

    def require(self,request:Request)->None:
        # Authentication proves possession only; it must not derive or change
        # the stable identity used by runtime tables and traffic counters.
        header=request.headers.get('authorization','')
        if not header.startswith('Bearer '):raise HTTPException(401,'DARK node token required')
        value=header[7:]
        try:valid=hmac.compare_digest(value,self.token)
        except TypeError:valid=False
        if not valid:raise HTTPException(401,'Invalid DARK node token')

    def rotate(self,value:str,*,request:Request|None=None):
        if not isinstance(value,str) or not value.startswith('dkn_') or not 40<=len(value)<=256 or not value.isascii():
            raise PolicyError('Invalid replacement node token')
        # Reauthenticate under the rotation lock, not only before body parsing.
        # An already buffered request with the old token cannot overwrite a
        # completed rotation. Hubs recover a lost response using the new token.
        with self.lock:
            if request is not None:self.require(request)
            fd,name=tempfile.mkstemp(prefix='.token.rotate.',dir=self.path.parent)
            temp=Path(name)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as out:
                    out.write(value+'\n');out.flush();os.fsync(out.fileno())
                os.replace(temp,self.path)
                # Replacement has happened even if directory fsync fails. Keep
                # disk and in-memory authentication aligned; do not report success.
                self.token=value
                parent=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(parent)
                finally:os.close(parent)
            finally:temp.unlink(missing_ok=True)
            pair=self.path.parent/'pair.json'
            try:
                if pair.is_file() and not pair.is_symlink():pair.unlink()
                marker=self.path.parent/'pair-consumed'
                fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as out:
                    out.write(str(time.time())+'\n');out.flush();os.fsync(out.fileno())
            except OSError:
                # The bootstrap bearer is invalid already. Artifact cleanup is
                # best effort, never a reason to roll the credential back.
                pass


def node_identity(path:Path)->str:
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>512:raise PolicyError('Node identity file is missing or unsafe')
    value=path.read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.@+-]{1,128}',value):raise PolicyError('Invalid stable Node identity')
    return value


class AgentRequestBoundary:
    """Authenticate before parsing; bound actual bytes, not only Content-Length.

    This is pure ASGI middleware: the entire body is bounded before the router
    sees any bytes. Chunked requests, disconnects and read timeouts therefore
    cannot bypass the limit or result in a partially processed command.
    """
    MAX_BODY = 8 * 1024 * 1024
    READ_TIMEOUT = 30.0
    RESPONSE_HEADERS = {
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
        'referrer-policy': 'no-referrer',
        'x-frame-options': 'DENY',
        'content-security-policy': "default-src 'none'; frame-ancestors 'none'",
        'strict-transport-security': 'max-age=31536000',
    }

    def __init__(self, app, token: AgentToken, authority: str, node_id: str = '', installation_id: str = ''):
        self.app, self.token, self.authority = app, token, authority.lower()
        self.node_id, self.installation_id = node_id, installation_id

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        authenticated = False

        async def secured_send(message):
            if message['type'] == 'http.response.start':
                owned = {key.encode() for key in self.RESPONSE_HEADERS}
                headers = [(key, value) for key, value in message.get('headers', [])
                           if key.lower() not in owned]
                headers.extend((key.encode(), value.encode())
                               for key, value in self.RESPONSE_HEADERS.items())
                if authenticated and self.installation_id:
                    headers = [(k,v) for k,v in headers if k.lower() not in
                               {b'x-dark-node-id',b'x-dark-installation-id'}]
                    headers.extend([(b'x-dark-node-id',self.node_id.encode()),
                                    (b'x-dark-installation-id',self.installation_id.encode())])
                message = {**message, 'headers': headers}
            await send(message)

        async def reject(status, detail):
            response = JSONResponse({'detail': detail}, status_code=status)
            await response(scope, receive, secured_send)

        request = Request(scope)
        if request.headers.get('host', '').lower() != self.authority:
            await reject(400, 'Unexpected Host')
            return
        if not scope.get('path', '/').startswith('/node/api/'):
            await reject(404, 'Not Found')
            return
        try:
            self.token.require(request)
        except HTTPException as exc:
            await reject(exc.status_code, exc.detail)
            return

        authenticated = True
        # A delayed mutation for another installation must be rejected BEFORE
        # reading its body or touching Xray. Old Hubs without these optional
        # headers remain compatible; newly pinned Hubs always supply both.
        for name,expected in (('x-dark-expected-node-id',self.node_id),
                              ('x-dark-expected-installation-id',self.installation_id)):
            values=request.headers.getlist(name)
            if values and (len(values)!=1 or not expected or values[0]!=expected):
                await reject(409, 'Node installation identity mismatch')
                return

        # Keep duplicate headers visible instead of silently selecting one.
        lengths = request.headers.getlist('content-length')
        declared = None
        if lengths:
            if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,12}', lengths[0]):
                await reject(400, 'Invalid Content-Length')
                return
            declared = int(lengths[0])
            if declared > self.MAX_BODY:
                await reject(413, 'Request too large')
                return
        if request.headers.get('content-encoding', 'identity').lower() != 'identity':
            await reject(415, 'Compressed request bodies are not supported')
            return

        body = bytearray()
        try:
            # A total deadline prevents an endless trickle of small chunks.
            async with asyncio.timeout(self.READ_TIMEOUT):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    if message['type'] != 'http.request':
                        await reject(400, 'Invalid request body')
                        return
                    chunk = message.get('body', b'')
                    if len(body) + len(chunk) > self.MAX_BODY:
                        await reject(413, 'Request too large')
                        return
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            await reject(408, 'Request body timed out')
            return
        if declared is not None and len(body) != declared:
            await reject(400, 'Content-Length does not match request body')
            return

        buffered = bytes(body)
        del body
        delivered = False

        async def replay():
            nonlocal delivered, buffered
            if not delivered:
                delivered = True
                chunk, buffered = buffered, b''
                return {'type': 'http.request', 'body': chunk, 'more_body': False}
            return await receive()

        await self.app(scope, replay, secured_send)

class EngineLoop:
    def __init__(self,engine:CoreEngine,interval:float,runtime:NodeRuntime|None=None):
        self.runtime=runtime;self.last_error='';self.last_success=0.0
        self.engine=engine;self.interval=max(1.0,min(60.0,float(interval)));self.stop=threading.Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        def run():
            while not self.stop.wait(self.interval):
                self.tick()
        self.thread=threading.Thread(target=run,name='dark-node-engine',daemon=True);self.thread.start()
    def tick(self):
        try:
            # The root broker's dynamic allowlist is volatile. Restore only
            # locally persisted, validated Xray data ports after its restart.
            try:
                if self.runtime is not None:
                    self.runtime.reconcile_control()
                    self.runtime.reconcile_guard()
            finally:
                # Guard outages must not also stop cumulative traffic collection.
                self.engine.flush()
            self.last_error='';self.last_success=time.time()
        except Exception as exc:
            self.last_error=type(exc).__name__+': '+str(exc)[:400]
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=6)
        self.thread=None


def make_agent_app(engine:CoreEngine,store:Store,token:AgentToken,node_id:str,*,background:bool=True)->FastAPI:
    if not isinstance(node_id,str) or not re.fullmatch(r'[A-Za-z0-9_.@+-]{1,128}',node_id):
        raise PolicyError('Invalid stable Node identity')
    runtime=NodeRuntime(store,engine,node_id);loop=EngineLoop(engine,engine.config.poll_seconds,runtime)
    public=urlsplit(engine.config.public_origin)
    @contextlib.asynccontextmanager
    async def lifespan(app):
        if background:
            try:
                wanted=runtime.reconcile_control()
                runtime.reconcile_guard()
                if wanted:engine.command('start')
            except Exception as exc:
                loop.last_error=type(exc).__name__+': '+str(exc)[:400]
            loop.start()
        yield
        loop.close();engine.close()

    app=FastAPI(title='DARK XRAY NODE',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.engine=engine;app.state.store=store;app.state.runtime=runtime;app.state.loop=loop

    app.add_middleware(AgentRequestBoundary,token=token,authority=public.netloc,node_id=node_id,installation_id=runtime.installation_id)

    def auth(request:Request)->str:
        token.require(request)
        return node_id

    updater=UpdateBrokerClient('/run/dark-xray-node-update/control.sock',timeout=12)

    @app.get('/node/api/health')
    def health(_scope:str=Depends(auth)):
        system=engine.system();state=runtime.status();core=engine.runtime_state()
        with store.lock:
            assigned=store.db.execute('SELECT COUNT(*) FROM node_runtime_inbounds WHERE scope=?',(_scope,)).fetchone()[0]
            clients=store.db.execute('SELECT COUNT(*) FROM node_runtime_clients WHERE scope=?',(_scope,)).fetchone()[0]
        source={}
        source_path=engine.runtime.parent/'installed-source.json'
        try:
            if source_path.is_file() and not source_path.is_symlink() and source_path.stat().st_size<65536:
                raw=json.loads(source_path.read_text(encoding='utf-8'))
                if isinstance(raw,dict):source={k:raw.get(k) for k in ('commit','version','ref','role')}
        except (OSError,ValueError):source={}
        return {'service':'DARK XRAY NODE','agent_only':True,'version':VERSION,'node_id':node_id,'installed_source':source,
                'core':{'state':core['state'],'version':core['version'],'dirty':core['dirty'],'last_error':core['last_error']},
                'system':{'cpu':system['cpu'],'memory_percent':100*system['mem']['current']/max(1,system['mem']['total']),
                          'disk_percent':100*system['disk']['current']/max(1,system['disk']['total']),'uptime':system['uptime']},
                'inbounds':int(assigned),'managed_clients':int(clients),'writes_enabled':engine.config.writes_enabled,
                'installation_id':runtime.installation_id,'capabilities':{'ordered_control':1,'installation_identity':1,'replacement_prepare':1},'control_receipt':runtime.command_status(),
                'desired_state':state,'run_control':runtime.control_status(),'maintenance':{'last_error':loop.last_error,'last_success':loop.last_success},'direct_source_verified':bool(engine.config.direct_source_verified)}

    @app.get('/node/api/v1/state')
    def state(_scope:str=Depends(auth)):
        return {'service':'DARK XRAY NODE',**runtime.status(),'core':engine.runtime_state(),
                'run_control':runtime.control_status(),'control_receipt':runtime.command_status()}

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
        with engine.lock:
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
                'SELECT source_email,mirror_email FROM node_runtime_clients WHERE scope=? ORDER BY source_email',(_scope,))]
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

    @app.get('/node/api/v1/update/status')
    def update_status(_scope:str=Depends(auth)):
        try:return {'service':'DARK XRAY NODE','update':updater.status()}
        except UpdateBrokerError as ex:raise HTTPException(503,str(ex))

    @app.post('/node/api/v1/update/check')
    def update_check(body:dict,_scope:str=Depends(auth)):
        commit=str(body.get('commit') or '').lower() if isinstance(body,dict) else ''
        try:return {'service':'DARK XRAY NODE','update':updater.check('exact',commit)}
        except UpdateBrokerError as ex:raise HTTPException(422,str(ex))

    @app.post('/node/api/v1/update/start')
    def update_start(body:dict,_scope:str=Depends(auth)):
        commit=str(body.get('commit') or '').lower() if isinstance(body,dict) else ''
        try:return {'service':'DARK XRAY NODE','update':updater.start(commit)}
        except UpdateBrokerError as ex:raise HTTPException(422,str(ex))

    def require_fresh_candidate():
        # Caller holds engine.lock. A previous authenticated Health is not an
        # authorization to stop/rotate a candidate that has since gained users.
        engine._write()
        with store.lock:
            inbounds=store.db.execute('SELECT COUNT(*) FROM core_inbounds').fetchone()[0]
            clients=store.db.execute('SELECT COUNT(*) FROM core_clients').fetchone()[0]
        if (inbounds or clients or runtime.status()['appliedRevision'] or runtime.command_status()['persisted']):
            raise HTTPException(409,'Replacement candidate is not a fresh unassigned installation')

    @app.post('/node/api/v1/replacement/rotate-token')
    def prepare_credential(body:dict,request:Request,_scope:str=Depends(auth)):
        if set(body)!={'token'}:raise HTTPException(422,'Invalid replacement credential envelope')
        with engine.lock:
            token.require(request)
            try:
                require_fresh_candidate()
                token.rotate(body['token'],request=request)
            except CoreError as ex:raise HTTPException(getattr(ex,'status',422),str(ex))
            except PolicyError as ex:raise HTTPException(422,str(ex))
        return {'service':'DARK XRAY NODE','rotated':True,'node_id':node_id,
                'installation_id':runtime.installation_id}

    @app.post('/node/api/v1/replacement/idle')
    def prepare_idle(body:dict,request:Request,_scope:str=Depends(auth)):
        if body:raise HTTPException(422,'Replacement idle accepts an empty object only')
        with engine.lock:
            token.require(request)
            try:
                require_fresh_candidate()
                runtime.command('stop')
            except CoreError as ex:raise HTTPException(getattr(ex,'status',422),str(ex))
        return {'service':'DARK XRAY NODE','idle':True,'node_id':node_id,
                'installation_id':runtime.installation_id}

    @app.post('/node/api/v1/token/rotate')
    def rotate_token(body:dict,request:Request,_scope:str=Depends(auth)):
        value=body.get('token') if isinstance(body,dict) else None
        try:
            engine._write()
            token.rotate(value,request=request)
        except CoreError as ex:raise HTTPException(getattr(ex,'status',422),str(ex))
        except PolicyError as ex:raise HTTPException(422,str(ex))
        return {'service':'DARK XRAY NODE','rotated':True}

    @app.post('/node/api/v1/control')
    def ordered_control(body:dict,_scope:str=Depends(auth)):
        try:return runtime.ordered_command(body)
        except CoreError as ex:raise HTTPException(getattr(ex,'status',422),str(ex))
        except PolicyError as ex:raise HTTPException(422,str(ex))

    @app.post('/node/api/core/{action}')
    def core_action(action:str,_scope:str=Depends(auth)):
        if action not in {'validate','restart','start','stop'}:raise HTTPException(404,'Unknown node core action')
        try:return {'engine':runtime.command(action),'node_agent':True,'run_control':runtime.control_status()}
        except CoreError as ex:raise HTTPException(getattr(ex,'status',422),str(ex))

    @app.get('/node/api/logs/{kind}')
    def logs(kind:str,limit:int=300,_scope:str=Depends(auth)):
        if kind not in {'process','error','access'}:raise HTTPException(404,'Unknown log kind')
        limit=max(1,min(1000,int(limit)))
        path=engine.runtime/(kind+'.log')
        if path.is_symlink():raise HTTPException(409,'Unsafe log path')
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:return {'kind':kind,'lines':[]}
        except OSError as ex:raise HTTPException(503,type(ex).__name__)
        try:
            with os.fdopen(fd,'rb') as log:
                info=os.fstat(log.fileno())
                if not stat.S_ISREG(info.st_mode):raise HTTPException(409,'Unsafe log file')
                start=max(0,info.st_size-512*1024)
                log.seek(start)
                text=log.read(512*1024).decode('utf-8','replace')
        except OSError as ex:raise HTTPException(503,type(ex).__name__)
        return {'kind':kind,'lines':[x[-2000:] for x in text.splitlines()[-limit:]],'truncated':start>0}

    return app


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path('/etc/dark-xray-node/config.json'))
    p.add_argument('--data',type=Path,default=Path('/var/lib/dark-xray-node'))
    p.add_argument('--token-file',type=Path,default=Path('/var/lib/dark-xray-node/token'))
    p.add_argument('--node-id-file',type=Path,default=Path('/var/lib/dark-xray-node/node-id'))
    p.add_argument('--host',default=None);p.add_argument('--port',type=int,default=None)
    a=p.parse_args()
    a.data.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    lock=(a.data/'instance.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another DARK Node Agent is already using this data directory')
    store=Store(a.data/'node.sqlite3');config=Config.load(a.config)
    engine=CoreEngine(config,store,a.data/'runtime');token=AgentToken(a.token_file);node_id=node_identity(a.node_id_file)
    host=a.host or config.bind_host;port=a.port or config.bind_port
    if host in ('127.0.0.1','::1') or not config.tls_certificate or not config.tls_private_key:
        raise SystemExit('Node Agent requires a public HTTPS listener with a real certificate/key')
    try:
        import uvicorn
        uvicorn.run(make_agent_app(engine,store,token,node_id),host=host,port=port,proxy_headers=False,access_log=False,
                    workers=1,ws='none',ssl_certfile=config.tls_certificate,ssl_keyfile=config.tls_private_key)
    except (PolicyError,CoreError,ValueError) as ex:raise SystemExit(str(ex))
    finally:
        try:engine.close()
        except Exception:pass
        store.close();lock.close()


if __name__=='__main__':main()

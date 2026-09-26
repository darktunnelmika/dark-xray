[Reading 299 lines from start (total: 299 lines, 0 remaining)]

from __future__ import annotations
import base64, json, ipaddress, re, secrets, socket, ssl, time, uuid
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

from fastapi import Depends, HTTPException, Request as FastAPIRequest
from fastapi.responses import Response
from pydantic import BaseModel, Field

from dark_policy import PolicyError

_HOST_RE=re.compile(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$')

class RestoreImportBody(BaseModel):
    urls:list[str]=Field(min_length=1,max_length=2000)
    inboundIds:list[int]=Field(default_factory=list,max_length=256)
    nodeIds:list[str]=Field(default_factory=list,max_length=256)
    scan:bool=True

class RestoreMappingBody(BaseModel):
    inboundIds:list[int]=Field(min_length=1,max_length=256)
    nodeIds:list[str]=Field(default_factory=list,max_length=256)

class RestoreDomainBody(BaseModel):
    domain:str=Field(min_length=3,max_length=253)
    acme_email:str=Field(default='',max_length=254)

class _SafeRedirect(HTTPRedirectHandler):
    def __init__(self,validator):
        super().__init__();self.validator=validator
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        self.validator(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class DarkRestore:
    def __init__(self,store,engine,nodes):
        self.store,self.engine,self.nodes=store,engine,nodes
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS restore_subscriptions(
              id TEXT PRIMARY KEY, public_token TEXT UNIQUE NOT NULL,
              legacy_url TEXT NOT NULL, legacy_host TEXT NOT NULL,
              legacy_path TEXT NOT NULL, legacy_query TEXT NOT NULL DEFAULT '',
              core_email TEXT UNIQUE NOT NULL,
              inbound_ids TEXT NOT NULL DEFAULT '[]', node_ids TEXT NOT NULL DEFAULT '[]',
              legacy_upload INTEGER NOT NULL DEFAULT 0, legacy_download INTEGER NOT NULL DEFAULT 0,
              legacy_total INTEGER NOT NULL DEFAULT 0, legacy_expire INTEGER NOT NULL DEFAULT 0,
              scan_status TEXT NOT NULL DEFAULT 'pending', scan_error TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1, first_seen REAL NOT NULL DEFAULT 0,
              last_seen REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS restore_legacy_route
              ON restore_subscriptions(legacy_host,legacy_path,legacy_query);
            CREATE TABLE IF NOT EXISTS restore_domains(
              domain TEXT PRIMARY KEY, acme_email TEXT NOT NULL DEFAULT '',
              dns_status TEXT NOT NULL DEFAULT 'unchecked',
              ssl_status TEXT NOT NULL DEFAULT 'unchecked',
              cert_path TEXT NOT NULL DEFAULT '', key_path TEXT NOT NULL DEFAULT '',
              last_checked REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS restore_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, restore_id TEXT NOT NULL,
              event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
            """)

    @staticmethod
    def _safe_url(value:str):
        try:u=urlsplit(value.strip())
        except Exception:raise PolicyError('Invalid subscription URL')
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise PolicyError('Subscription URL must be http/https without embedded credentials')
        host=(u.hostname or '').rstrip('.').lower()
        if not _HOST_RE.fullmatch(host):raise PolicyError('Subscription host must be a public DNS name')
        path=u.path or '/'
        if len(path)>1500 or len(u.query)>1500:raise PolicyError('Subscription URL is too long')
        return u,host,path,u.query

    @staticmethod
    def _public_addresses(host:str)->list[str]:
        try:infos=socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)
        except OSError as ex:raise PolicyError('Subscription host DNS lookup failed') from ex
        out=[]
        for item in infos:
            ip=item[4][0]
            try:obj=ipaddress.ip_address(ip)
            except ValueError:continue
            if not obj.is_global:raise PolicyError('Subscription host resolves to a non-public address')
            out.append(ip)
        if not out:raise PolicyError('Subscription host has no public address')
        return sorted(set(out))

    def _validate_fetch_url(self,url:str):
        _,host,_,_=self._safe_url(url);self._public_addresses(host)

    def _scan(self,url:str)->dict[str,Any]:
        self._validate_fetch_url(url)
        req=Request(url,headers={'User-Agent':'DARK-XRAY-Restore/1.0','Accept':'*/*'},method='GET')
        try:
            ctx=ssl.create_default_context()
            opener=build_opener(HTTPSHandler(context=ctx),_SafeRedirect(self._validate_fetch_url))
            with opener.open(req,timeout=10) as r:
                header=r.headers.get('subscription-userinfo','')
        except Exception as ex:
            return {'status':'partial','error':str(ex)[:300],'upload':0,'download':0,'total':0,'expire':0}
        vals={}
        for part in header.split(';'):
            if '=' not in part:continue
            k,v=part.split('=',1);k=k.strip().lower();v=v.strip()
            if k in {'upload','download','total','expire'}:
                try:vals[k]=max(0,int(v))
                except Exception:pass
        if not vals:return {'status':'no_usage_data','error':'subscription-userinfo header is missing','upload':0,'download':0,'total':0,'expire':0}
        return {'status':'verified' if {'total','expire'}<=set(vals) else 'partial','error':'',
                'upload':vals.get('upload',0),'download':vals.get('download',0),
                'total':vals.get('total',0),'expire':vals.get('expire',0)}

    def _validate_targets(self,inbounds:list[int],nodes:list[str]):
        if not inbounds or len(set(inbounds))!=len(inbounds) or any(type(x)is not int or x<1 for x in inbounds):
            raise PolicyError('Select at least one valid Inbound')
        known={int(x['id']) for x in self.engine.inbounds()}
        if not set(inbounds)<=known:raise PolicyError('Restore mapping contains an unknown Inbound')
        if nodes:
            known_nodes={str(x.get('id')) for x in self.nodes.list()}
            if not set(nodes)<=known_nodes:raise PolicyError('Restore mapping contains an unknown Node')

    def _client_body(self,core_email:str,inbounds:list[int],total:int,expire:int)->dict:
        protos={self.engine.inbound(i)['protocol'] for i in inbounds}
        uid=str(uuid.uuid4());password=secrets.token_urlsafe(24)
        body={'email':core_email,'id':uid,'password':password,'enable':True,
              'totalGB':int(total or 0),'expiryTime':int(expire or 0)*1000,
              'limitIp':0,'limitHwid':0,'flow':'','security':'auto','encryption':'none'}
        if protos=={'shadowsocks'}:body['password']=password
        return body

    def import_urls(self,urls:list[str],inbounds:list[int],nodes:list[str],scan:bool)->dict:
        self._validate_targets(inbounds,nodes)
        created=updated=0;items=[];now=time.time()
        for raw in urls:
            u,host,path,query=self._safe_url(raw)
            legacy=u.geturl();probe=self._scan(legacy) if scan else {'status':'pending','error':'','upload':0,'download':0,'total':0,'expire':0}
            with self.store.lock:
                old=self.store.db.execute('SELECT * FROM restore_subscriptions WHERE legacy_host=? AND legacy_path=? AND legacy_query=?',(host,path,query)).fetchone()
            if old:
                rid=str(old['id']);core_email=str(old['core_email']);updated+=1
                with self.store.transaction() as db:
                    db.execute("""UPDATE restore_subscriptions SET legacy_url=?,inbound_ids=?,node_ids=?,
                      legacy_upload=?,legacy_download=?,legacy_total=?,legacy_expire=?,scan_status=?,scan_error=?,enabled=1,updated_at=?
                      WHERE id=?""",(legacy,json.dumps(inbounds),json.dumps(nodes),probe['upload'],probe['download'],probe['total'],probe['expire'],probe['status'],probe['error'],now,rid))
                with self.store.lock:core_row=self.store.db.execute('SELECT body FROM core_clients WHERE email=?',(core_email,)).fetchone()
                if core_row:
                    body=json.loads(core_row['body']);body['totalGB']=int(probe['total']);body['expiryTime']=int(probe['expire'])*1000;body['enable']=True
                    with self.store.transaction() as db:
                        db.execute('UPDATE core_clients SET body=?,inbounds=? WHERE email=?',(json.dumps(body),json.dumps(inbounds),core_email))
                else:
                    self.engine.create(self._client_body(core_email,inbounds,probe['total'],probe['expire']),inbounds)
            else:
                rid='rst_'+secrets.token_hex(12);token=secrets.token_urlsafe(24);core_email='restore_'+secrets.token_hex(10)+'@dark.restore'
                self.engine.create(self._client_body(core_email,inbounds,probe['total'],probe['expire']),inbounds)
                with self.store.transaction() as db:
                    db.execute("""INSERT INTO restore_subscriptions(id,public_token,legacy_url,legacy_host,legacy_path,legacy_query,core_email,
                      inbound_ids,node_ids,legacy_upload,legacy_download,legacy_total,legacy_expire,scan_status,scan_error,enabled,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (rid,token,legacy,host,path,query,core_email,json.dumps(inbounds),json.dumps(nodes),probe['upload'],probe['download'],
                       probe['total'],probe['expire'],probe['status'],probe['error'],1,now,now))
                created+=1
            self.ensure_domain(host)
            items.append({'id':rid,'host':host,'path':path,'scan_status':probe['status']})
        try:self.engine.apply(start=self.engine.running)
        except Exception:pass
        return {'created':created,'updated':updated,'items':items}

    def ensure_domain(self,domain:str,acme_email:str=''):
        now=time.time()
        with self.store.transaction() as db:
            db.execute("""INSERT INTO restore_domains(domain,acme_email,created_at,updated_at) VALUES(?,?,?,?)
              ON CONFLICT(domain) DO UPDATE SET acme_email=CASE WHEN excluded.acme_email<>'' THEN excluded.acme_email ELSE restore_domains.acme_email END,
              updated_at=excluded.updated_at""",(domain,acme_email,now,now))

    def rows(self)->list[dict]:
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute('SELECT * FROM restore_subscriptions ORDER BY created_at DESC')]
        for r in rows:
            r['inbound_ids']=json.loads(r['inbound_ids']);r['node_ids']=json.loads(r['node_ids']);r['enabled']=bool(r['enabled'])
            with self.store.lock:
                c=self.store.db.execute('SELECT up,down FROM core_clients WHERE email=?',(r['core_email'],)).fetchone()
            r['dark_used']=int(c['up']+c['down']) if c else 0
            r['effective_used']=int(r['legacy_upload'])+int(r['legacy_download'])+r['dark_used']
            r['remaining']=max(0,int(r['legacy_total'])-r['effective_used']) if int(r['legacy_total']) else 0
        return rows

    def domains(self)->list[dict]:
        with self.store.lock:rows=[dict(r) for r in self.store.db.execute('SELECT * FROM restore_domains ORDER BY domain')]
        return rows

    def check_domain(self,domain:str)->dict:
        domain=domain.lower().rstrip('.')
        self.ensure_domain(domain)
        try:ips=self._public_addresses(domain);dns='ok'
        except Exception as ex:ips=[];dns='error';err=str(ex)
        cert=f'/etc/letsencrypt/live/{domain}/fullchain.pem';key=f'/etc/letsencrypt/live/{domain}/privkey.pem'
        import os
        ssl_status='ready' if os.path.isfile(cert) and os.path.isfile(key) else 'missing'
        now=time.time()
        with self.store.transaction() as db:
            db.execute('UPDATE restore_domains SET dns_status=?,ssl_status=?,cert_path=?,key_path=?,last_checked=?,updated_at=? WHERE domain=?',
                       (dns,ssl_status,cert if ssl_status=='ready' else '',key if ssl_status=='ready' else '',now,now,domain))
        return {'domain':domain,'dns_status':dns,'addresses':ips,'ssl_status':ssl_status,
                'error':locals().get('err',''),'requires_root_apply':ssl_status!='ready',
                'apply_command':f"sudo darkxray restore-tls {domain}" if ssl_status!='ready' else ''}

    def match_request(self,host_header:str,path:str,query:str)->dict|None:
        host=(host_header or '').split(':',1)[0].lower().rstrip('.')
        with self.store.lock:
            row=self.store.db.execute("""SELECT id,public_token FROM restore_subscriptions
              WHERE legacy_host=? AND legacy_path=? AND legacy_query=? AND enabled=1""",(host,path,query)).fetchone()
            if not row and query:
                row=self.store.db.execute("""SELECT id,public_token FROM restore_subscriptions
                  WHERE legacy_host=? AND legacy_path=? AND legacy_query='' AND enabled=1""",(host,path)).fetchone()
        return dict(row) if row else None

    def _runtime_ready(self,inbound_ids:list[int],node_ids:list[str])->dict[str,set[int]]:
        ids={int(x) for x in inbound_ids};selected={str(x) for x in node_ids};ready={'local':set()}
        for inbound_id in ids:
            inbound=self.engine.inbound(inbound_id);meta=inbound.get('panelMeta',{}) if isinstance(inbound.get('panelMeta'),dict) else {}
            if meta.get('deployLocal',True) is not False:ready['local'].add(inbound_id)
        for node in self.nodes.list():
            node_id=str(node.get('id'))
            if node_id not in selected or not node.get('enabled') or not node.get('online') or node.get('last_error'):continue
            for a in node.get('assignments',[]):
                inbound_id=int(a.get('local_inbound_id') or 0)
                if inbound_id in ids and a.get('deployed') and not a.get('last_error'):
                    ready.setdefault('node:'+node_id,set()).add(inbound_id)
        return ready

    def subscription(self,token:str,fmt:str)->tuple[bytes,dict]:
        with self.store.lock:r=self.store.db.execute('SELECT * FROM restore_subscriptions WHERE public_token=? AND enabled=1',(token,)).fetchone()
        if not r:raise HTTPException(404,'Restore subscription not found')
        now=time.time()
        if int(r['legacy_expire']) and int(r['legacy_expire'])<=int(now):raise HTTPException(403,'Subscription expired')
        inbound_ids=json.loads(r['inbound_ids']);node_ids=json.loads(r['node_ids'])
        body,headers=self.engine.subscription(str(r['core_email']),fmt,runtime_ready=self._runtime_ready(inbound_ids,node_ids))
        with self.store.lock:c=self.store.db.execute('SELECT up,down FROM core_clients WHERE email=?',(r['core_email'],)).fetchone()
        dark_up=int(c['up']) if c else 0;dark_down=int(c['down']) if c else 0
        headers['subscription-userinfo']=f"upload={int(r['legacy_upload'])+dark_up}; download={int(r['legacy_download'])+dark_down}; total={int(r['legacy_total'])}; expire={int(r['legacy_expire'])}"
        with self.store.transaction() as db:
            db.execute('UPDATE restore_subscriptions SET first_seen=CASE WHEN first_seen=0 THEN ? ELSE first_seen END,last_seen=?,updated_at=? WHERE id=?',(now,now,now,r['id']))
            db.execute('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',(r['id'],'subscription.update',fmt,now))
        return body,headers

def install_dark_restore(app,restore,current,owner,writable,audit):
    @app.get('/api/dark-restore')
    def list_restore(p=Depends(owner)):
        return {'items':restore.rows(),'domains':restore.domains()}

    @app.post('/api/dark-restore/import')
    def import_restore(body:RestoreImportBody,p=Depends(owner)):
        writable();result=restore.import_urls(body.urls,body.inboundIds,body.nodeIds,body.scan)
        audit(p.actor,p.actor.id,'dark_restore.import',str(len(body.urls)),'isolated restore users')
        return result

    @app.put('/api/dark-restore/{restore_id}/mapping')
    def mapping(restore_id:str,body:RestoreMappingBody,p=Depends(owner)):
        writable();restore._validate_targets(body.inboundIds,body.nodeIds)
        with restore.store.lock:r=restore.store.db.execute('SELECT * FROM restore_subscriptions WHERE id=?',(restore_id,)).fetchone()
        if not r:raise HTTPException(404,'Restore subscription not found')
        with restore.store.transaction() as db:
            db.execute('UPDATE restore_subscriptions SET inbound_ids=?,node_ids=?,updated_at=? WHERE id=?',
                       (json.dumps(body.inboundIds),json.dumps(body.nodeIds),time.time(),restore_id))
            db.execute('UPDATE core_clients SET inbounds=? WHERE email=?',(json.dumps(body.inboundIds),r['core_email']))
        restore.engine.apply(start=restore.engine.running)
        audit(p.actor,p.actor.id,'dark_restore.mapping',restore_id,'targets changed')
        return {'updated':True}

    @app.delete('/api/dark-restore/{restore_id}')
    def delete_restore(restore_id:str,p=Depends(owner)):
        writable()
        with restore.store.lock:r=restore.store.db.execute('SELECT core_email FROM restore_subscriptions WHERE id=?',(restore_id,)).fetchone()
        if not r:raise HTTPException(404,'Restore subscription not found')
        restore.engine.delete(r['core_email'])
        with restore.store.transaction() as db:db.execute('DELETE FROM restore_subscriptions WHERE id=?',(restore_id,))
        restore.engine.apply(start=restore.engine.running)
        audit(p.actor,p.actor.id,'dark_restore.delete',restore_id,'restore user deleted')
        return {'deleted':True}

    @app.put('/api/dark-restore/domains/{domain}')
    def save_domain(domain:str,body:RestoreDomainBody,p=Depends(owner)):
        writable()
        if body.domain.lower()!=domain.lower():raise HTTPException(400,'Domain mismatch')
        restore.ensure_domain(domain.lower(),body.acme_email)
        return restore.check_domain(domain)

    @app.post('/api/dark-restore/domains/{domain}/check')
    def check_domain(domain:str,p=Depends(owner)):
        return restore.check_domain(domain)

    @app.get('/restore/sub/{token}')
    def restore_sub(token:str,request:FastAPIRequest):
        ua=request.headers.get('user-agent','').lower();fmt=request.query_params.get('format','')
        if fmt not in ('raw','base64','json','clash'):fmt='clash' if ('clash' in ua or 'mihomo' in ua) else 'base64'
        body,headers=restore.subscription(token,fmt)
        return Response(content=body,headers=headers,media_type=None)

[executed on device: ubuntu (cf6412b3-aea9-4ff4-bf30-dfe3a0179e70)]
from __future__ import annotations
import base64,hashlib,json,os,secrets,time,threading
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel,Field
from fastapi import Depends,HTTPException
from dark_policy import PolicyError

CHECK_SECONDS=5*86400
DEFAULT_GRACE_SECONDS=2*86400

class LicenseActivateBody(BaseModel):
    key:str=Field(min_length=16,max_length=256)
class LicenseConfigBody(BaseModel):
    server_url:str=Field(min_length=8,max_length=512)
    public_key:str=Field(min_length=40,max_length=128)
    enforce:bool=False

class LicenseClient:
    def __init__(self,data_dir:Path,config_path:Path|None=None):
        self.data_dir=Path(data_dir);self.root=self.data_dir/'license';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.config_path=config_path or self.root/'config.json';self.identity_path=self.root/'installation-id';self.state_path=self.root/'state.json'
        self.installation_id=self._identity()
    def _identity(self):
        if self.identity_path.exists():
            value=self.identity_path.read_text().strip()
            if len(value)==32 and all(c in '0123456789abcdef' for c in value):return value
            raise PolicyError('Invalid persisted license installation identity')
        value=secrets.token_hex(16);self.identity_path.write_text(value+'\n');os.chmod(self.identity_path,0o600);return value
    def config(self):
        if not self.config_path.exists():return {'configured':False,'enforce':False}
        if self.config_path.is_symlink():raise PolicyError('License config symlinks refused')
        raw=json.loads(self.config_path.read_text());url=str(raw.get('server_url') or '').rstrip('/');key=str(raw.get('public_key') or '');enforce=raw.get('enforce',False)
        p=urlsplit(url)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:raise PolicyError('License server must be a credential-free HTTPS origin')
        if type(enforce) is not bool:raise PolicyError('License enforce must be boolean')
        self._public_key(key)
        return {'configured':True,'server_url':url,'public_key':key,'enforce':enforce}
    def save_config(self,body:dict):
        url=str(body.get('server_url') or '').rstrip('/');p=urlsplit(url)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:raise PolicyError('License server must be a credential-free HTTPS origin')
        key=str(body.get('public_key') or '');self._public_key(key)
        self.config_path.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
        tmp=self.config_path.with_suffix('.tmp');tmp.write_text(json.dumps({'server_url':url,'public_key':key,'enforce':bool(body.get('enforce',False))},indent=2)+'\n');os.chmod(tmp,0o600);os.replace(tmp,self.config_path)
        return self.status()
    @staticmethod
    def _public_key(value):
        try:raw=base64.urlsafe_b64decode(value+'='*((4-len(value)%4)%4));return Ed25519PublicKey.from_public_bytes(raw)
        except Exception as ex:raise PolicyError('Invalid Ed25519 license public key') from ex
    def _state(self):
        if not self.state_path.exists():return {}
        try:return json.loads(self.state_path.read_text())
        except (ValueError,OSError):return {}
    def _save(self,value):
        tmp=self.state_path.with_suffix('.tmp');tmp.write_text(json.dumps(value,separators=(',',':')));os.chmod(tmp,0o600);os.replace(tmp,self.state_path)
    def _verify(self,envelope,public_key):
        if not isinstance(envelope,dict) or set(envelope)!={'payload','signature'}:raise PolicyError('Invalid license envelope')
        payload=envelope['payload'];raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
        try:sig=base64.urlsafe_b64decode(str(envelope['signature'])+'='*((4-len(str(envelope['signature']))%4)%4));self._public_key(public_key).verify(sig,raw)
        except Exception as ex:raise PolicyError('License signature verification failed') from ex
        if payload.get('installation_id')!=self.installation_id:raise PolicyError('License installation binding mismatch')
        return payload
    def activate(self,key,panel_origin=''):
        cfg=self.config();
        if not cfg.get('configured'):raise PolicyError('Configure license server first')
        try:r=httpx.post(cfg['server_url']+'/v1/activate',json={'license_key':key,'installation_id':self.installation_id,'panel_origin':panel_origin},timeout=10.0);r.raise_for_status();env=r.json()
        except Exception as ex:raise PolicyError('License server activation failed') from ex
        payload=self._verify(env,cfg['public_key']);now=time.time();state={'envelope':env,'license_key':key,'last_success':now,'next_check':now+CHECK_SECONDS,'last_error':''};self._save(state);return self.status()
    def refresh(self,panel_origin='',force=False):
        cfg=self.config();state=self._state();now=time.time()
        if not cfg.get('configured') or not state.get('license_key'):return self.status()
        if not force and now<float(state.get('next_check') or 0):return self.status()
        try:r=httpx.post(cfg['server_url']+'/v1/check',json={'license_key':state['license_key'],'installation_id':self.installation_id,'panel_origin':panel_origin},timeout=10.0);r.raise_for_status();env=r.json();self._verify(env,cfg['public_key']);state.update(envelope=env,last_success=now,next_check=now+CHECK_SECONDS,last_error='')
        except Exception as ex:state.update(next_check=now+3600,last_error=str(ex)[:240])
        self._save(state);return self.status()
    def start(self,panel_origin=''):
        if getattr(self,'_thread',None) and self._thread.is_alive():return
        self._stop=threading.Event()
        def loop():
            while not self._stop.wait(3600):
                try:self.refresh(panel_origin)
                except Exception:pass
        self._thread=threading.Thread(target=loop,name='dark-license-check',daemon=True);self._thread.start()
    def close(self):
        stop=getattr(self,'_stop',None)
        if stop:stop.set()
        thread=getattr(self,'_thread',None)
        if thread and thread.is_alive():thread.join(timeout=2)

    def status(self):
        cfg=self.config();state=self._state();now=time.time();payload={}
        if state.get('envelope') and cfg.get('configured'):
            try:payload=self._verify(state['envelope'],cfg['public_key'])
            except PolicyError:payload={}
        expires=float(payload.get('expires_at') or 0);lease=float(payload.get('lease_until') or 0);last=float(state.get('last_success') or 0);grace=lease+DEFAULT_GRACE_SECONDS if lease else 0
        if not cfg.get('configured'):status='unconfigured'
        elif not payload:status='inactive'
        elif payload.get('revoked'):status='revoked'
        elif expires and now>=expires:status='expired'
        elif grace and now>=grace:status='offline_expired'
        elif lease and now>=lease:status='grace'
        else:status='active'
        return {'status':status,'configured':bool(cfg.get('configured')),'server_url':str(cfg.get('server_url') or ''),'public_key':str(cfg.get('public_key') or ''),'enforce':bool(cfg.get('enforce')),'installation_id':self.installation_id,'license_id':str(payload.get('license_id') or ''),'telegram_id':payload.get('telegram_id'),'issued_at':payload.get('issued_at'),'expires_at':expires or None,'lease_until':lease or None,'next_check':state.get('next_check'),'last_success':last or None,'last_error':str(state.get('last_error') or ''),'writes_allowed':not cfg.get('enforce') or status in ('active','grace')}

def install_licensing(app,client,current,owner,writable,audit,panel_origin):
    @app.get('/api/license/status')
    def status(p=Depends(current)):return client.status()
    @app.put('/api/license/config')
    def config(body:LicenseConfigBody,p=Depends(owner)):
        writable();result=client.save_config(body.model_dump());audit(p.actor,p.actor.id,'license.config','hub','license server configuration updated');return result
    @app.post('/api/license/activate')
    def activate(body:LicenseActivateBody,p=Depends(owner)):
        writable();result=client.activate(body.key,panel_origin);audit(p.actor,p.actor.id,'license.activate','hub','license activated');return result
    @app.post('/api/license/check')
    def check(p=Depends(owner)):return client.refresh(panel_origin,force=True)
from __future__ import annotations
import base64,hashlib,json,os,secrets,sqlite3,time
from pathlib import Path
from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel,Field
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DB=Path(os.environ.get('DARK_LICENSE_DB','/var/lib/dark-license/licenses.sqlite3'));KEY=Path(os.environ.get('DARK_LICENSE_SIGNING_KEY','/etc/dark-license/signing.key'));ADMIN=os.environ.get('DARK_LICENSE_ADMIN_TOKEN','')
DB.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(DB,check_same_thread=False,isolation_level=None);db.row_factory=sqlite3.Row
db.executescript('''CREATE TABLE IF NOT EXISTS licenses(id TEXT PRIMARY KEY,key_hash TEXT UNIQUE NOT NULL,telegram_id INTEGER NOT NULL,expires_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,installation_id TEXT NOT NULL DEFAULT '',panel_origin TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);''')
def signer():
 raw=KEY.read_bytes();return Ed25519PrivateKey.from_private_bytes(raw)
def env(row):
 now=time.time();payload={'license_id':row['id'],'telegram_id':row['telegram_id'],'installation_id':row['installation_id'],'issued_at':now,'expires_at':row['expires_at'],'lease_until':min(row['expires_at'],now+5*86400),'revoked':bool(row['revoked'])};raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode();sig=base64.urlsafe_b64encode(signer().sign(raw)).decode().rstrip('=');return {'payload':payload,'signature':sig}
def kh(k):return hashlib.sha256(k.encode()).hexdigest()
class Check(BaseModel):license_key:str=Field(min_length=16,max_length=256);installation_id:str=Field(pattern=r'^[0-9a-f]{32}$');panel_origin:str=Field(default='',max_length=512)
class Create(BaseModel):telegram_id:int=Field(gt=0);days:int=Field(default=30,ge=1,le=3650)
app=FastAPI(title='DARK License Authority',docs_url=None,redoc_url=None,openapi_url=None)
def admin(x_dark_license_admin:str=Header(default='')):
 if not ADMIN or not secrets.compare_digest(x_dark_license_admin,ADMIN):raise HTTPException(403,'Admin token required')
@app.get('/health')
def health():return {'service':'DARK License Authority','status':'ok'}
@app.post('/admin/licenses')
def create(body:Create,_=__import__('fastapi').Depends(admin)):
 key='DL1-'+secrets.token_urlsafe(32);lid='lic_'+secrets.token_hex(12);now=time.time();db.execute('INSERT INTO licenses(id,key_hash,telegram_id,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?)',(lid,kh(key),body.telegram_id,now+body.days*86400,now,now));return {'license_id':lid,'license_key':key,'expires_at':now+body.days*86400}
@app.post('/admin/licenses/{lid}/replace')
def replace(lid:str,_=__import__('fastapi').Depends(admin)):
 row=db.execute('SELECT * FROM licenses WHERE id=?',(lid,)).fetchone()
 if not row:raise HTTPException(404,'License not found')
 now=time.time();db.execute('UPDATE licenses SET revoked=1,updated_at=? WHERE id=?',(now,lid))
 key='DL1-'+secrets.token_urlsafe(32);new_id='lic_'+secrets.token_hex(12)
 db.execute('INSERT INTO licenses(id,key_hash,telegram_id,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?)',(new_id,kh(key),row['telegram_id'],max(now+86400,row['expires_at']),now,now))
 return {'license_id':new_id,'license_key':key,'expires_at':max(now+86400,row['expires_at']),'replaced_license_id':lid}
@app.post('/admin/licenses/{lid}/revoke')
def revoke(lid:str,_=__import__('fastapi').Depends(admin)):
 cur=db.execute('UPDATE licenses SET revoked=1,updated_at=? WHERE id=?',(time.time(),lid));
 if not cur.rowcount:raise HTTPException(404,'License not found')
 return {'revoked':True}
@app.post('/v1/activate')
def activate(body:Check):
 row=db.execute('SELECT * FROM licenses WHERE key_hash=?',(kh(body.license_key),)).fetchone()
 if not row or row['revoked'] or time.time()>=row['expires_at']:raise HTTPException(403,'License unavailable')
 if row['installation_id'] and row['installation_id']!=body.installation_id:raise HTTPException(409,'License is bound to another installation')
 db.execute('UPDATE licenses SET installation_id=?,panel_origin=?,updated_at=? WHERE id=?',(body.installation_id,body.panel_origin,time.time(),row['id']));row=db.execute('SELECT * FROM licenses WHERE id=?',(row['id'],)).fetchone();return env(row)
@app.post('/v1/check')
def check(body:Check):
 row=db.execute('SELECT * FROM licenses WHERE key_hash=?',(kh(body.license_key),)).fetchone()
 if not row or row['installation_id']!=body.installation_id:raise HTTPException(403,'License binding rejected')
 return env(row)
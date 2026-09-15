#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:150]!r}')
    p.write_text(s.replace(old,new,1))

# ---------------------------------------------------------------------------
# Auth storage: migrate live sessions with non-secret public IDs and metadata.
# ---------------------------------------------------------------------------
once('backend/auth.py',
"""            CREATE TABLE IF NOT EXISTS live_sessions(digest TEXT PRIMARY KEY,admin_id TEXT NOT NULL,
              csrf TEXT NOT NULL,expires_at REAL NOT NULL);""",
"""            CREATE TABLE IF NOT EXISTS live_sessions(digest TEXT PRIMARY KEY,admin_id TEXT NOT NULL,
              csrf TEXT NOT NULL,expires_at REAL NOT NULL,public_id TEXT,
              created_at REAL NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT '',user_agent TEXT NOT NULL DEFAULT '');""")
once('backend/auth.py',
"""            CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT PRIMARY KEY,start REAL NOT NULL,count INTEGER NOT NULL);
            ''')
""",
"""            CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT PRIMARY KEY,start REAL NOT NULL,count INTEGER NOT NULL);
            ''')
            cols={r[1] for r in store.db.execute('PRAGMA table_info(live_sessions)')}
            for name,ddl in (
                ('public_id','TEXT'),('created_at','REAL NOT NULL DEFAULT 0'),
                ('source',"TEXT NOT NULL DEFAULT ''"),('user_agent',"TEXT NOT NULL DEFAULT ''")):
                if name not in cols:store.db.execute(f'ALTER TABLE live_sessions ADD COLUMN {name} {ddl}')
            store.db.execute("UPDATE live_sessions SET public_id=substr(digest,1,24) WHERE public_id IS NULL OR public_id='' ")
            store.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS live_sessions_public_id ON live_sessions(public_id)')
""")

old="""    def login(self,username: str,password: str,otp: str,source: str,session_seconds: int=8*3600)->tuple[str,Principal]:
        if type(session_seconds) is not int or not 3600<=session_seconds<=525600*60:raise PolicyError('Invalid session lifetime')
        now=time.time();bucket=digest(source)"""
new="""    def login(self,username: str,password: str,otp: str,source: str,session_seconds: int=8*3600,user_agent: str='')->tuple[str,Principal]:
        if type(session_seconds) is not int or not 3600<=session_seconds<=525600*60:raise PolicyError('Invalid session lifetime')
        source=str(source or 'unknown').strip()[:128] or 'unknown'
        user_agent=' '.join(str(user_agent or '').split())[:300]
        now=time.time();bucket=digest(source)"""
once('backend/auth.py',old,new)
old="""            token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32)
            db.execute('DELETE FROM live_sessions WHERE expires_at<=?',(now,))
            db.execute('INSERT INTO live_sessions VALUES(?,?,?,?)',(digest(token),username,csrf,now+session_seconds))
            db.execute('DELETE FROM auth_attempts WHERE bucket=?',(bucket,))
        return token,Principal(Actor(row['id'],row['role'],json.loads(row['permissions'])),digest(token),csrf)
"""
new="""            token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32);session_digest=digest(token);public_id=session_digest[:24]
            db.execute('DELETE FROM live_sessions WHERE expires_at<=?',(now,))
            db.execute('''INSERT INTO live_sessions(digest,admin_id,csrf,expires_at,public_id,created_at,source,user_agent)
                          VALUES(?,?,?,?,?,?,?,?)''',(session_digest,username,csrf,now+session_seconds,public_id,now,source,user_agent))
            db.execute('DELETE FROM auth_attempts WHERE bucket=?',(bucket,))
        return token,Principal(Actor(row['id'],row['role'],json.loads(row['permissions'])),session_digest,csrf)
"""
once('backend/auth.py',old,new)

# Add session management methods before admin_create.
anchor="""    def admin_create(self,actor: Actor,username: str,password: str,role: str,permissions: dict|None):
"""
methods="""    def sessions(self,p:Principal)->list[dict]:
        if p.key_id or not p.session_id:raise PermissionDenied('Interactive session required')
        now=time.time()
        with self.store.lock:
            rows=self.store.db.execute('''SELECT digest,public_id,created_at,source,user_agent,expires_at
                FROM live_sessions WHERE admin_id=? AND expires_at>? ORDER BY created_at DESC,expires_at DESC''',(p.actor.id,now)).fetchall()
        return [{'id':r['public_id'] or r['digest'][:24],'current':r['digest']==p.session_id,
                 'created_at':float(r['created_at'] or 0),'expires_at':float(r['expires_at']),
                 'source':str(r['source'] or 'unknown')[:128],'user_agent':str(r['user_agent'] or '')[:300]} for r in rows]

    def revoke_session(self,p:Principal,public_id:str)->dict:
        if p.key_id or not p.session_id:raise PermissionDenied('Interactive session required')
        if not re.fullmatch(r'[0-9a-f]{24}',str(public_id or '')):raise PolicyError('Invalid session ID')
        with self.store.transaction() as db:
            row=db.execute('SELECT digest FROM live_sessions WHERE public_id=? AND admin_id=?',(public_id,p.actor.id)).fetchone()
            if not row:raise PolicyError('Session not found')
            current=row['digest']==p.session_id
            db.execute('DELETE FROM live_sessions WHERE digest=?',(row['digest'],))
        return {'revoked':True,'current':current,'id':public_id}

    def revoke_other_sessions(self,p:Principal)->dict:
        if p.key_id or not p.session_id:raise PermissionDenied('Interactive session required')
        with self.store.transaction() as db:
            cur=db.execute('DELETE FROM live_sessions WHERE admin_id=? AND digest<>?',(p.actor.id,p.session_id))
        return {'revoked_others':max(0,cur.rowcount)}

"""
if anchor not in Path('backend/auth.py').read_text():raise SystemExit('auth method insertion anchor missing')
once('backend/auth.py',anchor,methods+anchor)

# Verify web/admin password writes too.
old="""            if hashed:db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,username))
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(username,))
"""
new="""            if hashed:
                db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,username))
                written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(username,)).fetchone()
                if not written or not verify_password(password,written['password_hash']):raise PolicyError('Admin password verification failed; transaction rolled back')
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(username,))
"""
once('backend/auth.py',old,new)
old="""        with self.store.transaction() as db:
            db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,p.actor.id))
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(p.actor.id,))
"""
new="""        with self.store.transaction() as db:
            db.execute('UPDATE api_admins SET password_hash=? WHERE id=?',(hashed,p.actor.id))
            written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(p.actor.id,)).fetchone()
            if not written or not verify_password(new,written['password_hash']):raise PolicyError('Password verification failed; transaction rolled back')
            db.execute('DELETE FROM live_sessions WHERE admin_id=?',(p.actor.id,))
"""
once('backend/auth.py',old,new)

# ---------------------------------------------------------------------------
# Server endpoints and user-agent capture.
# ---------------------------------------------------------------------------
old="""    def owner(p:Principal=Depends(current))->Principal:
        if p.actor.role!='owner' or p.key_id:raise HTTPException(403,'Interactive owner access required')
        return p
"""
new="""    def interactive(p:Principal=Depends(current))->Principal:
        if p.key_id:raise HTTPException(403,'Interactive session required')
        return p
    def owner(p:Principal=Depends(current))->Principal:
        if p.actor.role!='owner' or p.key_id:raise HTTPException(403,'Interactive owner access required')
        return p
"""
once('backend/server.py',old,new)
old="""        token,p=auth.login(body.username,body.password,body.otp,request.client.host if request.client else 'unknown',session_minutes*60)
"""
new="""        token,p=auth.login(body.username,body.password,body.otp,request.client.host if request.client else 'unknown',session_minutes*60,request.headers.get('user-agent',''))
"""
once('backend/server.py',old,new)
anchor="""    @app.post('/api/auth/password')
    def password(body:Password,p:Principal=Depends(current)):
"""
endpoints="""    @app.get('/api/auth/sessions')
    def sessions(p:Principal=Depends(interactive)):
        return auth.sessions(p)
    @app.delete('/api/auth/sessions/{session_id}')
    def revoke_session(session_id:str,p:Principal=Depends(interactive)):
        result=auth.revoke_session(p,session_id);manager.audit(p.actor,p.actor.id,'auth.session.revoke',session_id)
        response=JSONResponse(result)
        if result['current']:response.delete_cookie(COOKIE,path=panel_path)
        return response
    @app.post('/api/auth/sessions/revoke-others')
    def revoke_other_sessions(p:Principal=Depends(interactive)):
        result=auth.revoke_other_sessions(p);manager.audit(p.actor,p.actor.id,'auth.sessions.revoke_others',p.actor.id,str(result['revoked_others']))
        return result

"""
once('backend/server.py',anchor,endpoints+anchor)

# ---------------------------------------------------------------------------
# Account UI: active sessions list and revoke controls.
# ---------------------------------------------------------------------------
p=Path('web/live.js');s=p.read_text()
old="""async function accountPage(){let keys=can('api.manage')?await api('/api/keys'):[];return heading('حساب و امنیت','رمز، نشست، ورود دومرحله‌ای و کلید مستقل برای اتصال نرم‌افزارها.')+`<div class=\"account-grid\"><article class=\"panel\"><h2>ورود دومرحله‌ای TOTP</h2><p>وضعیت: ${state.me.totp_enabled?'فعال':'غیرفعال'}؛ تنظیمات این بخش برای ورود DARK است، ورود دوم دیگری وجود ندارد.</p>${button(state.me.totp_enabled?'غیرفعال‌سازی با تأیید':'راه‌اندازی TOTP',state.me.totp_enabled?'mfadisable':'mfasetup','shield')}</article><article class=\"panel\"><h2>رمز و نشست‌ها</h2><p>با تغییر رمز، تمام نشست‌های این حساب باطل می‌شوند.</p>${button('تغییر رمز','password','lock')}</article>${can('api.manage')?`<article class=\"panel\"><h2>کلید API</h2>"""
new="""async function accountPage(){let keys=can('api.manage')?await api('/api/keys'):[],sessions=await api('/api/auth/sessions');return heading('حساب و امنیت','رمز، نشست، ورود دومرحله‌ای و کلید مستقل برای اتصال نرم‌افزارها.')+`<div class=\"account-grid\"><article class=\"panel\"><h2>ورود دومرحله‌ای TOTP</h2><p>وضعیت: ${state.me.totp_enabled?'فعال':'غیرفعال'}؛ تنظیمات این بخش برای ورود DARK است، ورود دوم دیگری وجود ندارد.</p>${button(state.me.totp_enabled?'غیرفعال‌سازی با تأیید':'راه‌اندازی TOTP',state.me.totp_enabled?'mfadisable':'mfasetup','shield')}</article><article class=\"panel\"><h2>رمز حساب</h2><p>با تغییر رمز، تمام نشست‌های این حساب باطل می‌شوند.</p>${button('تغییر رمز','password','lock')}</article><article class=\"panel\"><h2>نشست‌های فعال</h2><p>${sessions.length} نشست فعال برای این حساب ثبت شده است.</p>${sessions.length>1?button('خروج از همه نشست‌های دیگر','sessionsothers','power'):''}${sessions.map(s=>`<div class=\"connection-strip\"><span><b>${s.current?'این نشست':'نشست دیگر'}</b> · ${e(s.source||'unknown')}<br><small>${s.created_at?date(s.created_at):'زمان ساخت نامشخص'} → ${date(s.expires_at)} · ${e(s.user_agent||'User-Agent نامشخص')}</small></span>${s.current?'<span class=\"tag green\">CURRENT</span>':button('ابطال','sessiondelete','trash',`data-id=\"${e(s.id)}\"`)}</div>`).join('')}</article>${can('api.manage')?`<article class=\"panel\"><h2>کلید API</h2>"""
if old not in s:raise SystemExit('accountPage anchor missing')
s=s.replace(old,new,1)
old="""case'keydelete':if(confirm('این کلید باطل شود؟')){await api('/api/keys/'+enc(id),'DELETE');await renderPage();}return;
"""
new="""case'sessiondelete':if(confirm('این نشست فعال باطل شود؟')){await api('/api/auth/sessions/'+enc(id),'DELETE');toast('نشست باطل شد.');await renderPage();}return;
case'sessionsothers':if(confirm('از تمام نشست‌های دیگر این حساب خارج شود؟')){let r=await api('/api/auth/sessions/revoke-others','POST',{});toast(`${r.revoked_others} نشست دیگر باطل شد.`);await renderPage();}return;
case'keydelete':if(confirm('این کلید باطل شود؟')){await api('/api/keys/'+enc(id),'DELETE');await renderPage();}return;
"""
if old not in s:raise SystemExit('runAction session anchor missing')
p.write_text(s.replace(old,new,1))

# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
p=Path('tests/test_sessions.py')
p.write_text(r'''from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Store
from manager import Manager
from server import make_app


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3');config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key');auth.bootstrap('dark','OwnerPass88')
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as c:
        r=c.post('/api/auth/login',headers={'User-Agent':'DARK Browser A'},json={'username':'dark','password':'OwnerPass88'});assert r.status_code==200
        c.headers['X-Dark-CSRF']=r.json()['csrf'];yield store,engine,manager,auth,c
    store.close()


def test_session_inventory_and_individual_revoke(env):
    store,engine,manager,auth,c=env
    token,p=auth.login('dark','OwnerPass88','','10.0.0.2',3600,'DARK CLI B')
    rows=c.get('/api/auth/sessions').json();assert len(rows)==2 and sum(bool(x['current']) for x in rows)==1
    other=next(x for x in rows if not x['current']);assert other['source']=='10.0.0.2' and other['user_agent']=='DARK CLI B'
    r=c.delete('/api/auth/sessions/'+other['id']);assert r.status_code==200 and not r.json()['current']
    rows=c.get('/api/auth/sessions').json();assert len(rows)==1 and rows[0]['current']


def test_revoke_other_sessions_keeps_current(env):
    store,engine,manager,auth,c=env
    auth.login('dark','OwnerPass88','','10.0.0.2',3600,'Agent B');auth.login('dark','OwnerPass88','','10.0.0.3',3600,'Agent C')
    r=c.post('/api/auth/sessions/revoke-others',json={});assert r.status_code==200 and r.json()['revoked_others']==2
    rows=c.get('/api/auth/sessions').json();assert len(rows)==1 and rows[0]['current']
    assert c.get('/api/me').status_code==200


def test_session_metadata_migration_from_legacy_table(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    with store.transaction() as db:
        db.execute('CREATE TABLE live_sessions(digest TEXT PRIMARY KEY,admin_id TEXT NOT NULL,csrf TEXT NOT NULL,expires_at REAL NOT NULL)')
    auth=Auth(store,tmp_path/'secret.key')
    cols={r[1] for r in store.db.execute('PRAGMA table_info(live_sessions)')}
    assert {'public_id','created_at','source','user_agent'} <= cols
    store.close()
''')

# Run in isolated suite.
p=Path('tests/run-tests.sh');s=p.read_text()
needle="python -m pytest tests/test_owner_recovery.py -q --junitxml=qa/junit/owner-recovery.xml\n"
if needle not in s:raise SystemExit('run-tests anchor missing')
s=s.replace(needle,needle+"python -m pytest tests/test_sessions.py -q --junitxml=qa/junit/sessions.xml\n",1);p.write_text(s)
print('session management patch applied')

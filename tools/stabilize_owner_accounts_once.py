#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:150]!r}')
    p.write_text(s.replace(old,new,1))

# Web/API owner creation must attach the same ownership/profile records as terminal recovery.
old="""        with self.store.transaction() as db:
            if role=='reseller' and not db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(username,)).fetchone():
                raise PolicyError('Create a matching reseller profile first')
            db.execute('INSERT INTO api_admins VALUES(?,?,?,?,0)',(username,role,hashed,json.dumps(perms)))
"""
new="""        with self.store.transaction() as db:
            if role=='reseller' and not db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(username,)).fetchone():
                raise PolicyError('Create a matching reseller profile first')
            db.execute('INSERT INTO api_admins VALUES(?,?,?,?,0)',(username,role,hashed,json.dumps(perms)))
            if role=='owner':
                db.execute('INSERT OR IGNORE INTO owners(id) VALUES(?)',(username,))
                if not db.execute('SELECT 1 FROM owner_profiles WHERE id=?',(username,)).fetchone():
                    allowed=[r[0] for r in db.execute('SELECT id FROM core_inbounds ORDER BY id')]
                    db.execute('INSERT INTO owner_profiles(id,name,allowed) VALUES(?,?,?)',(username,username,json.dumps(allowed)))
            written=db.execute('SELECT password_hash FROM api_admins WHERE id=?',(username,)).fetchone()
            if not written or not verify_password(password,written['password_hash']):raise PolicyError('Admin password verification failed; transaction rolled back')
"""
once('backend/auth.py',old,new)

# Clarify the Roles UI so Owner login and reseller profile semantics are explicit.
p=Path('web/live.js');s=p.read_text()
old='حساب نماینده را با همان شناسهٔ پروفایل بساز. مجوز «همه» عمداً دسترسی بین نماینده‌ها می‌دهد؛ پیش‌فرضِ امن «خود» است.'
new='نماینده به پروفایل همنام نیاز دارد. ساخت Owner از این بخش، Owner Profile متناظر را خودکار ایجاد یا متصل می‌کند. مجوز «همه» عمداً دسترسی بین نماینده‌ها می‌دهد؛ پیش‌فرض امن «خود» است.'
if old not in s:raise SystemExit('roles notice anchor missing')
p.write_text(s.replace(old,new,1))

# Regression: Owner created through the web Admin API is a complete login+profile owner.
p=Path('tests/test_standalone.py');s=p.read_text()
marker='def test_reseller_scope_and_shared_inbound(env):'
test=r'''

def test_web_owner_creation_attaches_owner_profile(env):
    store,engine,m,auth,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    r=c.post('/api/admins',json={'username':'Mika','password':'MikaPass88','role':'owner','permissions':{}})
    assert r.status_code==200,r.text
    with store.lock:
        admin=store.db.execute("SELECT role,password_hash FROM api_admins WHERE id='Mika'").fetchone()
        owner=store.db.execute("SELECT id FROM owners WHERE id='Mika'").fetchone()
        profile=store.db.execute("SELECT id,allowed FROM owner_profiles WHERE id='Mika'").fetchone()
    assert admin['role']=='owner' and owner['id']=='Mika' and profile['id']=='Mika'
    assert json.loads(profile['allowed'])==[1]
    token,p=auth.login('Mika','MikaPass88','','127.0.0.9')
    with TestClient(make_app(m,auth,background=False),base_url=engine.config.public_origin) as other:
        other.cookies.set('dark_session',token);other.headers['X-Dark-CSRF']=p.csrf
        me=other.get('/api/me');assert me.status_code==200 and me.json()['role']=='owner'
        ids={x['id'] for x in other.get('/api/owners').json()}
        assert 'Mika' in ids


'''
if 'test_web_owner_creation_attaches_owner_profile' not in s:
    if marker not in s:raise SystemExit('standalone marker missing')
    s=s.replace(marker,test+marker,1)
p.write_text(s)
print('owner account consistency patch applied')

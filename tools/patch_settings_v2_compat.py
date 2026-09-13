#!/usr/bin/env python3
"""Compatibility and session-policy follow-up for the Settings V2 migration."""
from pathlib import Path


def once(s, old, new):
    if new in s:
        return s
    if old not in s:
        raise RuntimeError('compat anchor not found: '+old[:120])
    return s.replace(old,new,1)

p=Path('backend/core.py');s=p.read_text()
old="""        with self.store.lock:r=self.store.db.execute('SELECT body FROM core_sections WHERE name=?',(name,)).fetchone()
        return json.loads(r[0]) if r else copy.deepcopy(defaults[name])
"""
new="""        with self.store.lock:r=self.store.db.execute('SELECT body FROM core_sections WHERE name=?',(name,)).fetchone()
        if not r:return copy.deepcopy(defaults[name])
        saved=json.loads(r[0]);base=copy.deepcopy(defaults[name])
        if isinstance(base,dict) and isinstance(saved,dict):
            base.update(saved);return base
        return saved
"""
s=once(s,old,new);p.write_text(s)

p=Path('backend/auth.py');s=p.read_text()
s=once(s,"    def login(self,username: str,password: str,otp: str,source: str)->tuple[str,Principal]:\n        now=time.time();bucket=digest(source)\n","    def login(self,username: str,password: str,otp: str,source: str,session_seconds: int=8*3600)->tuple[str,Principal]:\n        if type(session_seconds) is not int or not 3600<=session_seconds<=525600*60:raise PolicyError('Invalid session lifetime')\n        now=time.time();bucket=digest(source)\n")
s=once(s,"            db.execute('INSERT INTO live_sessions VALUES(?,?,?,?)',(digest(token),username,csrf,now+8*3600))\n","            db.execute('INSERT INTO live_sessions VALUES(?,?,?,?)',(digest(token),username,csrf,now+session_seconds))\n")
p.write_text(s)

p=Path('backend/server.py');s=p.read_text()
old="""        token,p=auth.login(body.username,body.password,body.otp,request.client.host if request.client else 'unknown')
        response=JSONResponse({'id':p.actor.id,'role':p.actor.role,'csrf':p.csrf,'permissions':p.actor.permissions})
        session_minutes=int(engine.section('panel').get('session_max_age_minutes',480))
"""
new="""        session_minutes=int(engine.section('panel').get('session_max_age_minutes',480))
        token,p=auth.login(body.username,body.password,body.otp,request.client.host if request.client else 'unknown',session_minutes*60)
        response=JSONResponse({'id':p.actor.id,'role':p.actor.role,'csrf':p.csrf,'permissions':p.actor.permissions})
"""
s=once(s,old,new);p.write_text(s)

# Test database expiry and legacy-section hydration explicitly.
p=Path('tests/test_settings_v2.py');s=p.read_text()
if 'test_legacy_panel_section_is_hydrated' not in s:
    s += r'''

def test_legacy_panel_section_is_hydrated(env):
    store,engine,c=env
    with store.transaction() as db:
        db.execute("INSERT INTO core_sections(name,body) VALUES('panel',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",(json.dumps({'title':'OLD DARK','support_url':''}),))
    value=c.get('/api/settings/panel').json()['value']
    assert value['title']=='OLD DARK'
    assert value['language']=='en'
    assert value['session_max_age_minutes']==480


def test_database_session_expiry_matches_setting(env):
    store,_,c=env
    panel=c.get('/api/settings/panel').json()['value'];panel['session_max_age_minutes']=60
    assert c.put('/api/settings/panel',json={'value':panel}).status_code==200
    c.post('/api/auth/logout',json={})
    before=time.time()
    r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});assert r.status_code==200
    with store.lock:
        expiry=store.db.execute('SELECT MAX(expires_at) FROM live_sessions').fetchone()[0]
    assert 3590 <= expiry-before <= 3610
'''
p.write_text(s)

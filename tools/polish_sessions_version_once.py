#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:160]!r}')
    p.write_text(s.replace(old,new,1))

# Single version source: backend reads the repository VERSION file.
once('backend/server.py',
     "VERSION='0.7.0-standalone-lab'\nROOT=Path(__file__).resolve().parents[1]",
     "ROOT=Path(__file__).resolve().parents[1]\nVERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip()")
Path('VERSION').write_text('0.7.1-standalone-lab\n')

# English-first translations for the new active-session surface.
p=Path('web/i18n-en.js');s=p.read_text()
anchor=" 'تأیید نشده':'Not verified'\n}));"
replacement=""" 'تأیید نشده':'Not verified',
 'رمز حساب':'Account Password',
 'نشست‌های فعال':'Active Sessions',
 'خروج از همه نشست‌های دیگر':'Sign out all other sessions',
 'این نشست':'Current session',
 'نشست دیگر':'Other session',
 'زمان ساخت نامشخص':'Creation time unknown',
 'User-Agent نامشخص':'Unknown User-Agent',
 'ابطال':'Revoke',
 'نشست باطل شد.':'Session revoked.'
}));"""
if anchor not in s:raise SystemExit('i18n exact-map anchor missing')
s=s.replace(anchor,replacement,1)
phrases_anchor="const phrases=[\n"
extra="""const phrases=[
 ['رمز، نشست، ورود دومرحله‌ای و کلید مستقل برای اتصال نرم‌افزارها.','Password, sessions, two-factor authentication, and independent API keys.'],
 ['با تغییر رمز، تمام نشست‌های این حساب باطل می‌شوند.','Changing the password revokes all sessions for this account.'],
 ['نشست فعال برای این حساب ثبت شده است.','active sessions are registered for this account.'],
 ['این نشست فعال باطل شود؟','Revoke this active session?'],
 ['از تمام نشست‌های دیگر این حساب خارج شود؟','Sign out all other sessions for this account?'],
 ['نشست دیگر باطل شد.','other sessions were revoked.'],
"""
if phrases_anchor not in s:raise SystemExit('i18n phrases anchor missing')
s=s.replace(phrases_anchor,extra,1)
p.write_text(s)

# Ensure the regular CI guards the single-source version and session API/UI.
p=Path('.github/workflows/ci.yml');s=p.read_text()
needle="          grep -q 'session_seconds' backend/auth.py\n"
addition="""          grep -q '/api/auth/sessions' backend/server.py
          grep -q 'Active Sessions' web/i18n-en.js
          grep -q \"VERSION=(ROOT/'VERSION').read_text\" backend/server.py
"""
if needle not in s:raise SystemExit('CI session anchor missing')
if "grep -q '/api/auth/sessions' backend/server.py" not in s:s=s.replace(needle,addition+needle,1)
p.write_text(s)

# Tiny regression that API version equals VERSION file.
p=Path('tests/test_standalone.py');s=p.read_text()
marker='def test_web_owner_creation_attaches_owner_profile(env):'
test=r'''

def test_api_version_uses_version_file(env):
    store,engine,m,auth,c=env
    expected=(Path(__file__).resolve().parents[1]/'VERSION').read_text().strip()
    assert c.get('/health').json()['version']==expected
    assert c.get('/api/me').json()['version']==expected


'''
if 'test_api_version_uses_version_file' not in s:
    if marker not in s:raise SystemExit('version regression anchor missing')
    s=s.replace(marker,test+marker,1)
p.write_text(s)
print('session i18n and version polish applied')
